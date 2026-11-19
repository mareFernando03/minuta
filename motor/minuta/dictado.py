"""Dictado con Ctrl+Win: mientras se mantiene apretado se escucha el micrófono; al soltar se transcribe y la app pega
el texto donde está el cursor (app/src-tauri/src/dictado.rs). Reemplaza a Wispr Flow.

El audio nunca toca el disco. Si hay una reunión grabando, el dictado se cuelga como oyente de la pista del micrófono
(abrir un segundo stream WASAPI mientras graba arriesga el -9999 de grabador.py); si no, abre su propio stream del
micrófono por defecto. Con "groq" el audio sale a Groq (turbo, con el glosario como prompt); sin red o sin clave cae a
Whisper local. El texto queda en `dictados` (los últimos 50) solo para «Copiar el último»: no sale por el MCP.
"""
import logging
import threading
import time

import numpy as np

from minuta.datos import db

log = logging.getLogger("minuta.dictado")

SR = 16000
MAX_S = 180  # más que esto no es un dictado: se deja de juntar audio y se avisa que hay que soltar
MIN_S = 0.3  # menos es un toque de la tecla, no una frase
NIVEL_CADA_S = 0.1
VENCE_S = MAX_S + 30  # nadie pidió terminar (la app perdió el soltar o se cayó): se cancela y se cierra el mic


class Apagado(Exception):
    """El dictado está apagado en Ajustes."""


class Ocupado(Exception):
    """Ya hay un dictado escuchando."""


class Escucha:
    """Junta el PCM mono de 16 kHz que llega del micrófono, hasta MAX_S."""

    def __init__(self):
        self.lock = threading.Lock()
        self.partes, self.muestras, self.nivel = [], 0, 0.0

    def __call__(self, pcm, nivel):
        with self.lock:
            self.nivel = nivel
            if self.muestras < MAX_S * SR:
                self.partes.append(pcm)
                self.muestras += len(pcm) // 2

    def audio(self):
        with self.lock:
            crudo = b"".join(self.partes)
        return np.frombuffer(crudo, dtype=np.int16).astype(np.float32) / 32768


class MicPropio:
    """El micrófono por defecto, para cuando no hay una reunión grabando."""

    def __init__(self, oyente):
        import pyaudiowpatch as pyaudio
        from minuta.captura.grabador import PORTAUDIO, PistaMemoria
        with PORTAUDIO:
            self.pa = pyaudio.PyAudio()
            try:
                wasapi = self.pa.get_host_api_info_by_type(pyaudio.paWASAPI)
                self.pista = PistaMemoria(self.pa, "dictado", log.info)
                self.pista.oyentes.append(oyente)
                self.pista.abrir(self.pa.get_device_info_by_index(wasapi["defaultInputDevice"]))
            except Exception:
                self.pa.terminate()
                raise

    def cerrar(self):
        from minuta.captura.grabador import PORTAUDIO
        with PORTAUDIO:
            self.pista.cerrar()
            self.pa.terminate()


class MicMudo:
    """Para `--simular-reunion`: no abre ningún dispositivo y no escucha nada."""

    def __init__(self, oyente):
        pass

    def cerrar(self):
        pass


def transcribir(audio, motor):
    """(texto, motor usado, segundos de voz)."""
    from minuta.transcripcion import local, nube
    if motor == "groq":
        try:
            texto, voz = nube.groq_dictado(audio)
            return texto, "groq", voz
        except nube.SinNube as e:
            log.info(f"dictado: Groq no disponible ({e}), va local")
    from minuta import config
    segs, _ = local.modelo("small").transcribe(audio, language="es", vad_filter=True, beam_size=1, batch_size=8,
                                               initial_prompt=config.glosario_prompt())
    return " ".join(s.text.strip() for s in segs).strip(), "local", len(audio) / SR


_calentado = threading.Event()


def calentar():
    """Carga el VAD de faster-whisper mientras se habla: en frío, el primer dictado tardaba 1,6 s en vez de 0,7 s."""
    if _calentado.is_set():
        return
    _calentado.set()
    try:
        from minuta.transcripcion import nube
        nube.solo_voz(np.zeros(SR, dtype=np.float32), max_s=1)
    except Exception as e:
        log.warning(f"dictado: no se pudo precalentar el VAD: {e!r}")


def limpiar(texto):
    from minuta.transcripcion import local
    texto = local.limpiar_repeticiones(texto)
    return f"{texto} " if texto else ""  # el espacio final deja seguir dictando sin pegar palabras


class Dictado:
    """`evento(tipo, datos)` avisa a la app (la píldora). El micrófono propio y la transcripción se inyectan para las
    pruebas."""

    def __init__(self, vigia=None, evento=lambda tipo, datos: None, abrir_mic=MicPropio, transcribir=transcribir,
                 reloj=time.monotonic, calentar=calentar):
        self.vigia, self.evento, self.abrir_mic, self._transcribir, self.reloj = vigia, evento, abrir_mic, transcribir, reloj
        self._calentar = calentar
        self._lock = threading.Lock()
        self.escucha, self._soltar, self.desde, self._grab = None, None, None, None
        self._solo_local = False
        self._fin_nivel = threading.Event()

    def _colgarse(self, escucha):
        """Engancha la escucha a la reunión que graba, o al mic propio si no hay. Devuelve la grabación o None."""
        grab = getattr(self.vigia, "grab", None)
        pista = getattr(grab, "mic", None)
        if hasattr(pista, "oyentes"):
            pista.oyentes.append(escucha)
            self._soltar = lambda: pista.oyentes.remove(escucha) if escucha in pista.oyentes else None
            # El dictado sale del micrófono de la reunión, con lo que oiga la sala: si la reunión es «solo en esta
            # PC», ese audio tampoco va a la nube.
            self._solo_local = self._solo_local or bool(getattr(self.vigia, "solo_local", False))
            return grab
        self._soltar = self.abrir_mic(escucha).cerrar
        return None

    def empezar(self):
        with self._lock:
            if not db.ajuste("dictado"):
                raise Apagado()
            if self.escucha:
                raise Ocupado()
            escucha = Escucha()
            self._solo_local = False
            self._grab = self._colgarse(escucha)
            self.escucha, self.desde = escucha, self.reloj()
            self._fin_nivel = threading.Event()
        self.evento("dictado", {"estado": "escuchando", "nivel": 0.0})
        threading.Thread(target=self._vigilar, args=(escucha, self._fin_nivel), name="dictado-nivel",
                         daemon=True).start()
        threading.Thread(target=self._calentar, name="dictado-calentar", daemon=True).start()

    def _vigilar(self, escucha, fin):
        """Mientras se dicta: el nivel para la píldora, el aviso de los 3 minutos, el cambio al mic propio si la
        reunión se cerró en el medio, y el vencimiento si nadie termina."""
        avisado = False
        while not fin.wait(NIVEL_CADA_S):
            if self.reloj() - self.desde > VENCE_S:
                log.warning("dictado: venció sin terminar, se descarta")
                self.cancelar()
                return
            with self._lock:
                if self.escucha is escucha and self._grab is not None and getattr(self.vigia, "grab", None) is not self._grab:
                    try:
                        self._soltar()
                        self._grab = None
                        self._soltar = self.abrir_mic(escucha).cerrar  # la reunión se cortó: sigue con el mic propio
                    except Exception as e:
                        log.warning(f"dictado: no se pudo seguir con el mic propio: {e!r}")
                        self._soltar = lambda: None
            if escucha.muestras >= MAX_S * SR:
                if not avisado:
                    self.evento("dictado", {"estado": "error", "mensaje": "Llegaste a 3 minutos: soltá para pegar"})
                    avisado = True
                continue
            self.evento("dictado", {"estado": "escuchando", "nivel": round(escucha.nivel, 4)})

    def _cortar(self):
        """Deja de escuchar y devuelve (lo escuchado o None si no había dictado, si tiene que quedar en esta PC)."""
        with self._lock:
            escucha, soltar, solo_local = self.escucha, self._soltar, self._solo_local
            self.escucha, self._soltar, self._grab = None, None, None
            self._fin_nivel.set()
        if escucha is None:
            return None, False
        try:
            soltar()
        except Exception as e:  # cerrar el mic no puede perder lo dictado
            log.warning(f"dictado: error al soltar el micrófono: {e!r}")
        return escucha, solo_local

    def cancelar(self):
        if self._cortar()[0] is not None:
            self.evento("dictado", {"estado": "cancelado"})

    def terminar(self):
        """{texto, motor, ms}. Texto vacío si fue un toque o no hubo voz."""
        escucha, solo_local = self._cortar()
        if escucha is None:
            return {"texto": "", "motor": None, "ms": 0}
        t0 = self.reloj()
        audio = escucha.audio()
        if len(audio) < MIN_S * SR:
            self.evento("dictado", {"estado": "cancelado"})
            return {"texto": "", "motor": None, "ms": 0}
        self.evento("dictado", {"estado": "transcribiendo"})
        try:
            motor_pedido = "local" if solo_local else db.ajuste("dictado_motor") or "groq"
            texto, motor, voz = self._transcribir(audio, motor_pedido)
        except Exception as e:
            log.exception("dictado: no se pudo transcribir")
            self.evento("dictado", {"estado": "error", "mensaje": f"No se pudo transcribir: {e}"})
            raise
        texto = limpiar(texto)
        ms = int((self.reloj() - t0) * 1000)
        if texto:
            db.guardar_dictado(texto, len(audio) / SR, motor, ms)
            if motor != "local":
                db.registrar_uso(motor, voz)
        self.evento("dictado", {"estado": "listo", "ms": ms, "vacio": not texto})
        return {"texto": texto, "motor": motor, "ms": ms}
