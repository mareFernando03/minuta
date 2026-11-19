"""Graba una reunión en dos pistas alineadas: micrófono y audio del sistema (loopback WASAPI).

Cada pista sale en mono de 16 kHz, en bloques WAV de 5 minutos (`mic-000.wav`, `sistema-000.wav`...), así una
suspensión o un corte solo pierde el bloque en curso. Las dos pistas comparten el reloj de inicio: cuando un
dispositivo no entrega audio (el loopback no manda nada mientras no suena nada) el hueco se rellena con silencio.

El audio de los demás se toma del loopback de la salida por defecto, pero la app de la reunión puede sonar por otra:
el 22/10/2026 Meet sonó por los auriculares con la salida por defecto en los altavoces, y la pista quedó en ceros. Por
eso cada otra salida tiene una sonda que solo mide nivel, y si la reunión suena por una de ellas mientras la grabada
calla, la grabación pasa a esa salida (`SeguidorDeSalida`).
"""
import json
import threading
import time
import wave
from pathlib import Path

import av
import numpy as np
import pyaudiowpatch as pyaudio

SR = 16000
BLOQUE_S = 300
REVISAR_SALIDA_S = 2
MAX_HUECO_S = 120  # un hueco mayor es una suspensión: el vigía corta antes, no se escriben horas de ceros
SUENA_RMS = 0.003  # el mismo umbral con el que el vigía decide que los demás suenan
CAMBIO_S = 6       # tiempo sonando por otra salida antes de pasarse: una notificación no alcanza
SUFIJO_LOOPBACK = " [Loopback]"
# PortAudio (Pa_Initialize/Pa_Terminate) no es seguro entre hilos, y el dictado abre su propio PyAudio: todo lo que lo
# toca, de cualquier grabación o dictado, va bajo este lock.
PORTAUDIO = threading.RLock()


class Pista:
    """Una fuente de audio escrita en bloques WAV, alineada contra el reloj de la grabación."""

    def __init__(self, pa, nombre, carpeta, t0, log):
        self.pa, self.nombre, self.carpeta, self.t0, self.log = pa, nombre, carpeta, t0, log
        self.lock = threading.Lock()
        self.escritas = 0  # muestras a 16 kHz desde t0
        self.nivel, self.ultimo_audio = 0.0, 0.0  # RMS del último bloque, para los medidores de la app
        self.pico = 0.0  # RMS máximo desde la última lectura: el vigía lo usa para las alertas y el silencio
        self.pico_seguimiento = 0.0  # lo mismo para el seguimiento de la salida: si leyeran uno solo, se lo robarían
        self.bloque, self.wav, self.stream, self.dispositivo = -1, None, None, None
        self.resampler = None
        self.oyentes = []  # funciones (pcm, nivel) que reciben el audio mono de 16 kHz además del WAV: el dictado

    def abrir(self, dev):
        self.cerrar_stream()
        ch, sr = int(dev["maxInputChannels"]), int(dev["defaultSampleRate"])
        self.resampler = av.AudioResampler(format="s16", layout="mono", rate=SR)
        self.ch, self.sr = ch, sr
        self.stream = self.pa.open(format=pyaudio.paInt16, channels=ch, rate=sr, input=True,
                                   input_device_index=dev["index"], frames_per_buffer=1024,
                                   stream_callback=self._cb)
        self.dispositivo = dev["name"]
        self.log(f"{self.nombre}: abierto {dev['name']} ({ch} canales, {sr} Hz)")

    def _cb(self, datos, n, info, estado):
        try:
            x = np.frombuffer(datos, dtype=np.int16).reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(x, format="s16", layout="stereo" if self.ch == 2 else "mono")
            frame.sample_rate = self.sr
            mono = b"".join(f.to_ndarray().tobytes() for f in self.resampler.resample(frame))
            if mono:
                m = np.frombuffer(mono, dtype=np.int16).astype(np.float32) / 32768
                self.nivel, self.ultimo_audio = float(np.sqrt(np.mean(m * m))), time.monotonic()
                self.pico = max(self.pico, self.nivel)
                self.pico_seguimiento = max(self.pico_seguimiento, self.nivel)
            self.escribir(mono, fin_en=time.monotonic())
            # Después del WAV y cada uno por su cuenta: si falla el dictado, la reunión no pierde ese tramo.
            for oyente in list(self.oyentes) if mono else ():
                try:
                    oyente(mono, self.nivel)
                except Exception as e:
                    self.log(f"{self.nombre}: error en un oyente: {e!r}")
        except Exception as e:  # el callback no puede lanzar: PortAudio cortaría el stream en silencio
            self.log(f"{self.nombre}: error en callback: {e!r}")
        return (None, pyaudio.paContinue)

    def escribir(self, pcm, fin_en):
        """Escribe `pcm` (int16 mono 16 kHz) terminando en `fin_en`, rellenando antes el hueco si lo hay."""
        with self.lock:
            n = len(pcm) // 2
            hueco = int((fin_en - self.t0) * SR) - n - self.escritas
            if hueco > MAX_HUECO_S * SR:
                self.log(f"{self.nombre}: hueco de {hueco // SR} s (¿suspensión?): se rellenan solo {MAX_HUECO_S} s")
                hueco = MAX_HUECO_S * SR
            if hueco > SR // 10:
                self._volcar(b"\x00\x00" * hueco)
            self._volcar(pcm)

    def rellenar_hasta(self, t):
        with self.lock:
            falta = int((t - self.t0) * SR) - self.escritas
            if falta > 0:
                self._volcar(b"\x00\x00" * falta)

    def _volcar(self, pcm):
        while pcm:
            bloque = self.escritas // (BLOQUE_S * SR)
            if bloque != self.bloque:
                if self.wav:
                    self.wav.close()
                self.wav = wave.open(str(self.carpeta / f"{self.nombre}-{bloque:03d}.wav"), "wb")
                self.wav.setnchannels(1); self.wav.setsampwidth(2); self.wav.setframerate(SR)
                self.bloque = bloque
            cabe = ((bloque + 1) * BLOQUE_S * SR - self.escritas) * 2
            self.wav.writeframes(pcm[:cabe])
            self.escritas += len(pcm[:cabe]) // 2
            pcm = pcm[cabe:]

    def leer_pico(self):
        """El nivel más alto desde la lectura anterior. Leer solo el último buffer sería una muestra al azar."""
        pico, self.pico = self.pico, 0.0
        return pico

    def leer_pico_seguimiento(self):
        pico, self.pico_seguimiento = self.pico_seguimiento, 0.0
        return pico

    def cerrar_stream(self):
        if self.stream:
            try:
                self.stream.close()
            except Exception as e:
                self.log(f"{self.nombre}: error al cerrar: {e!r}")
            self.stream = None

    def cerrar(self, t):
        self.cerrar_stream()
        self.rellenar_hasta(t)
        with self.lock:
            if self.wav:
                self.wav.close()
                self.wav = None


class PistaMemoria(Pista):
    """Una pista que no escribe nada: el audio solo llega a sus oyentes. La usa el dictado sin reunión en curso."""

    def __init__(self, pa, nombre, log):
        super().__init__(pa, nombre, None, time.monotonic(), log)

    def escribir(self, pcm, fin_en):
        pass

    def cerrar(self, t=None):
        self.cerrar_stream()


class SeguidorDeSalida:
    """Decide cuándo la reunión suena por otra salida que la grabada. Recibe en cada revisión el pico de la pista del
    sistema y el de cada sonda; devuelve la salida a la que hay que pasar, o None. Con audio en las dos no se cambia:
    puede ser la reunión en una y la música en la otra."""

    def __init__(self):
        self.reiniciar()

    def reiniciar(self):
        self.candidata, self.desde = None, None

    def revisar(self, grabada, sondas, ahora):
        sonando = sorted((p, n) for n, p in sondas.items() if p >= SUENA_RMS)
        if grabada >= SUENA_RMS or not sonando:
            self.reiniciar()
            return None
        mejor = sonando[-1][1]
        if mejor != self.candidata:
            self.candidata, self.desde = mejor, ahora
            return None
        if ahora - self.desde >= CAMBIO_S:
            self.reiniciar()
            return mejor
        return None


def sin_sufijo(nombre_loopback):
    return nombre_loopback.removesuffix(SUFIJO_LOOPBACK)


class Grabacion:
    """Mic + loopback de la salida por defecto, más una sonda en cada otra salida. Si cambia la salida por defecto
    (HDMI, auriculares) o el micrófono, se reabren las pistas; si la reunión suena por otra salida, la pista del sistema
    pasa a esa. Cada cambio se avisa con `al_cambiar(tipo, nombre)`."""

    def __init__(self, carpeta, log=print, al_cambiar=lambda tipo, nombre: None):
        self.carpeta = Path(carpeta)
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self.log = log
        self._lock_dispositivos = PORTAUDIO
        self._parar = threading.Event()
        with self._lock_dispositivos:
            self.pa = pyaudio.PyAudio()
            self.t0 = time.monotonic()
            self.inicio = time.time()
            self.mic = Pista(self.pa, "mic", self.carpeta, self.t0, log)
            self.sistema = Pista(self.pa, "sistema", self.carpeta, self.t0, log)
            self.cambios_salida, self.cambios_mic = [], []
            self.al_cambiar = al_cambiar
            self.sondas, self.seguidor = {}, SeguidorDeSalida()
            # Las pistas se abren en secuencia: abrir dos streams WASAPI a la vez desde hilos distintos falla con -9999.
            self.entrada = self._dispositivo("defaultInputDevice")["name"]
            self.mic.abrir(self._dispositivo("defaultInputDevice"))
            self.salida = self._dispositivo("defaultOutputDevice")["name"]  # la por defecto, para ver si cambia
            self.sistema.abrir(self._loopback(self.salida))
            self.grabada = self.salida  # la que graba la pista del sistema: puede no ser la por defecto
            self._abrir_sondas()
        self._hilo = threading.Thread(target=self._seguir_dispositivos, daemon=True)
        self._hilo.start()

    def _dispositivo(self, clave):
        wasapi = self.pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        return self.pa.get_device_info_by_index(wasapi[clave])

    def _loopback(self, nombre_salida):
        return next(d for d in self.pa.get_loopback_device_info_generator() if nombre_salida in d["name"])

    def _abrir_sondas(self):
        """Una sonda por cada salida que no es la grabada. Si una no abre, se sigue sin ella: nunca corta la grabación."""
        self.loopbacks = set()
        for d in list(self.pa.get_loopback_device_info_generator()):
            nombre = sin_sufijo(d["name"])
            self.loopbacks.add(nombre)
            if nombre == self.grabada:
                continue
            sonda = PistaMemoria(self.pa, f"sonda {nombre}", self.log)
            try:
                sonda.abrir(d)
                self.sondas[nombre] = sonda
            except Exception as e:
                self.log(f"sonda {nombre}: no se pudo abrir: {e!r}")

    def _cerrar_sondas(self):
        for sonda in self.sondas.values():
            sonda.cerrar_stream()
        self.sondas = {}

    def _seguir_dispositivos(self):
        while not self._parar.wait(REVISAR_SALIDA_S):
            try:
                cambios = []
                with self._lock_dispositivos:
                    # PortAudio no refresca la lista de dispositivos sola: hay que reiniciarlo para ver el default nuevo.
                    pa_nuevo = pyaudio.PyAudio()
                    try:
                        wasapi = pa_nuevo.get_host_api_info_by_type(pyaudio.paWASAPI)
                        salida = pa_nuevo.get_device_info_by_index(wasapi["defaultOutputDevice"])["name"]
                        entrada = pa_nuevo.get_device_info_by_index(wasapi["defaultInputDevice"])["name"]
                        loopbacks = {sin_sufijo(d["name"]) for d in pa_nuevo.get_loopback_device_info_generator()}
                    finally:
                        pa_nuevo.terminate()
                    t = round(time.monotonic() - self.t0, 1)
                    antes = self.grabada
                    if salida != self.salida:
                        self.log(f"cambió la salida: {self.salida} -> {salida}")
                    if entrada != self.entrada:
                        self.log(f"cambió el micrófono: {self.entrada} -> {entrada}")
                        cambios.append(("mic", entrada, None))
                    if salida != self.salida or entrada != self.entrada or loopbacks != self.loopbacks:
                        # Cambió la por defecto, el micrófono o el juego de salidas (un auricular enchufado en plena
                        # reunión). Un cambio que no es de la por defecto no mueve la pista del sistema de donde suena
                        # la reunión, salvo que esa salida ya no exista. Si reabrir falla, nada se actualiza y en 2 s
                        # se vuelve a intentar.
                        if salida != self.salida or self.grabada not in loopbacks:
                            destino = salida
                        else:
                            destino = self.grabada
                        self.reabrir(destino)
                        # La lista que vio `pa_nuevo` manda: si la de `self.pa` difiriera, se reabriría cada 2 s.
                        self.salida, self.entrada, self.loopbacks = salida, entrada, loopbacks
                        if self.grabada != antes:
                            cambios.append(("salida", self.grabada, None))
                    else:
                        picos = {n: s.leer_pico_seguimiento() for n, s in self.sondas.items()}
                        destino = self.seguidor.revisar(self.sistema.leer_pico_seguimiento(), picos, time.monotonic())
                        if destino:
                            self.log(f"la reunión suena por {destino}, no por {self.grabada}: se graba de ahí")
                            try:
                                self.reabrir(destino)
                            except Exception as e:  # puede haber fallado solo el micrófono: el cambio vale igual
                                self.log(f"al pasar a {destino}: {e!r}")
                            if self.grabada != antes:
                                cambios.append(("salida", self.grabada, "suena"))
                    for tipo, nombre, motivo in cambios:
                        cambio = {"t": t, tipo: nombre}
                        if motivo:
                            cambio["motivo"] = motivo
                        (self.cambios_salida if tipo == "salida" else self.cambios_mic).append(cambio)
                for tipo, nombre, _ in cambios:
                    self.al_cambiar(tipo, nombre)
            except Exception as e:
                self.log(f"seguimiento de dispositivos: {e!r}")

    def reabrir(self, salida=None):
        """Reinicia PortAudio y vuelve a abrir las pistas: el sistema en `salida` (por defecto, la que ya grababa) y el
        micrófono en el por defecto, más las sondas. Lo usa el seguimiento de dispositivos y el vigía cuando una pista
        deja de mandar audio. Cada pista se abre por su cuenta: si no hay micrófono, el audio de los demás se sigue
        grabando."""
        with self._lock_dispositivos:
            destino = salida or self.grabada
            self.mic.cerrar_stream()
            self.sistema.cerrar_stream()
            self._cerrar_sondas()
            self.pa.terminate()
            self.pa = self.mic.pa = self.sistema.pa = pyaudio.PyAudio()
            self.seguidor.reiniciar()
            self.sistema.pico_seguimiento = 0.0
            errores = []
            for pista, dispositivo in ((self.sistema, lambda: self._loopback(destino)),
                                       (self.mic, lambda: self._dispositivo("defaultInputDevice"))):
                try:
                    pista.abrir(dispositivo())
                    if pista is self.sistema:
                        self.grabada = destino
                except Exception as e:
                    self.log(f"{pista.nombre}: no se pudo abrir: {e!r}")
                    errores.append(e)
            self._abrir_sondas()
            if errores:
                raise errores[0]

    def sistema_abierto(self):
        return self.sistema.stream is not None

    def cerrar(self, hasta=None):
        """Cierra las pistas. `hasta` (reloj monotónico): no rellenar más allá, p. ej. el tiempo que la PC durmió."""
        self._parar.set()
        self._hilo.join(timeout=10)
        t = time.monotonic() if hasta is None else min(hasta, time.monotonic())
        with self._lock_dispositivos:
            self.mic.cerrar(t)
            self.sistema.cerrar(t)
            self._cerrar_sondas()
            self.pa.terminate()
        dur = round(t - self.t0, 1)
        meta = {"inicio": self.inicio, "duracion_s": dur, "cambios_salida": self.cambios_salida, "cambios_mic": self.cambios_mic,
                "mic": self.mic.dispositivo, "salida_inicial": self.sistema.dispositivo}
        (self.carpeta / "audio.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        return dur
