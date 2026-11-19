"""Vigía: graba cuando una app de reunión toma el micrófono y deja la grabación en la cola.

Qué app tiene el micrófono tomado sale de las sesiones de audio de captura de WASAPI: una sesión `Active` en un
micrófono es una app que lo está escuchando ahora. Hasta el 22/10/2026 se leía de CapabilityAccessManager en el
registro (LastUsedTimeStop en 0), pero Windows (26H2, cerca de la KB5127648) dejó de escribirlo: probado con el
micrófono abierto, el registro no cambia, y desde ese día no se grabó ninguna reunión sola. El registro queda como
respaldo, por si vuelve; sus valores viejos tienen Stop distinto de 0 y no dan falsos positivos.
Las reuniones (Teams web y Meet) pasan por Chrome o Edge; las llamadas con compañeros, por Slack.
Mientras graba, mira cada 30 s los títulos de las ventanas de esas apps: sirven para identificar la reunión cuando
no está en el calendario (el 🔊 de Slack marca dónde está el huddle).

Corre como un hilo del motor. Expone `estado()` para la pantalla «Ahora» y acepta pausar, «no es una reunión» y
«cortar acá».

También se graba a mano (`empezar_manual`), para las reuniones presenciales: ninguna app toma el micrófono, así que
esa grabación no se corta por soltarlo. Termina con «Cortar», por silencio, por suspensión o por pausa. Medido el
13/10/2026 sobre el micrófono en la oficina: el silencio más largo de una reunión fue de 1,1 min, lejos de los 10.

Además del micrófono suelto, la grabación termina por:
- silencio: 10 min sin audio en las dos pistas avisa «¿Sigue la reunión?» y, si en 2 min no vuelve el audio ni el
  usuario dice que sigue, corta donde empezó el silencio (la pestaña de Meet que quedó abierta);
- suspensión: si entre dos revisiones pasó más de un minuto, la PC estuvo dormida y se corta en la última revisión.
  En Windows `time.monotonic` sigue contando durante la suspensión: por eso se mira el salto y no la diferencia
  entre relojes.
Y avisa (sin cortar) cuando el micrófono no capta nada mientras el sistema suena, cuando deja de mandar audio,
cuando cambia de dispositivo o cuando no se escucha a los demás por la salida grabada. Una grabación sin tu voz es la
falla que más se repite en estas apps.
"""
import ctypes
import ctypes.wintypes as wt
import logging
import shutil
import threading
import time
import winreg
from datetime import datetime, timedelta

from minuta import config
from minuta.captura.grabador import Grabacion
from minuta.datos import db

APPS = ("chrome.exe", "msedge.exe", "slack.exe")
MANUAL = "manual"  # en `apps` de una grabación empezada a mano
SONDEO_S = 3
SOLTADO_S = 90       # el mic suelto por menos que esto no corta la reunión (salir y volver a entrar)
MINIMO_S = 180       # menos que esto no es una reunión
TITULOS_CADA_S = 30
MINIMO_CORTADA_S = 30  # «cortar acá» conserva tramos más cortos: el usuario sabe lo que corta
MUDO_RMS = 1e-4        # silencio digital: mic silenciado en Windows, por hardware, o el dispositivo equivocado
MUDO_S = 60
SISTEMA_MUDO_S = 120   # sin nadie del otro lado: la reunión suena por una salida que no se graba (22/10/2026)
SISTEMA_SUENA_RMS = 0.003
SIN_SENAL_S = 10       # sin audio del mic: el dispositivo dejó de mandar (se desconectó, se cayó el driver)
REINTENTO_S = 30
SILENCIO_SISTEMA_RMS = 0.001
SILENCIO_MIC_RMS = 0.008  # por debajo de la voz: ruido de fondo de la habitación
SILENCIO_S = 600
SILENCIO_GRACIA_S = 120
SUSPENSION_S = 60
ALERTA_EFIMERA_S = 30
CLAVE_MIC = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone\NonPackaged"

log = logging.getLogger("minuta.vigia")


def apps_con_mic(fuentes=None):
    """Apps de la lista que tienen el micrófono tomado en este momento, según las sesiones de WASAPI o el registro."""
    activas = []
    for fuente in fuentes or (_por_sesiones, _por_registro):
        nombre = getattr(fuente, "__name__", repr(fuente))
        try:
            encontradas = fuente()
        except Exception as e:
            if nombre not in _fallas_avisadas:  # cada 3 s: se avisa una vez por racha, no se llena el log
                _fallas_avisadas.add(nombre)
                log.warning("no se pudo ver quién usa el micrófono (%s): %r", nombre, e)
            continue
        _fallas_avisadas.discard(nombre)
        for exe in encontradas:  # Chrome abre una sesión por pestaña: la misma app viene repetida
            if exe in APPS and exe not in activas:
                activas.append(exe)
        # La primera fuente que responde manda: el registro es respaldo. Sumado, un Stop en 0 que quedó viejo (una app
        # que murió con el micrófono abierto) daría una reunión eterna y ninguna grabación más.
        break
    return activas


def deteccion_caida():
    """WASAPI viene fallando: la detección automática depende del registro, que Windows ya no escribe."""
    return "_por_sesiones" in _fallas_avisadas


_fallas_avisadas = set()
_com = threading.local()
ACTIVA = 1  # AudioSessionStateActive


def _por_sesiones():
    """Exes con una sesión de audio activa en algún micrófono."""
    import comtypes
    from pycaw.api.audiopolicy import IAudioSessionControl2, IAudioSessionManager2
    from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
    from pycaw.constants import CLSID_MMDeviceEnumerator

    if not getattr(_com, "listo", False):  # COM se inicializa una vez por hilo
        comtypes.CoInitialize()
        _com.listo = True
    enumerador = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_ALL)
    micros = enumerador.EnumAudioEndpoints(1, 1)  # eCapture, DEVICE_STATE_ACTIVE
    exes = []
    for i in range(micros.GetCount()):
        try:  # un micrófono que no responde (virtual, recién desenchufado) no tapa a los demás
            gestor = micros.Item(i).Activate(IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None)
            sesiones = gestor.QueryInterface(IAudioSessionManager2).GetSessionEnumerator()
        except (comtypes.COMError, OSError) as e:
            log.debug("micrófono %d sin sesiones: %r", i, e)
            continue
        for j in range(sesiones.GetCount()):
            s = sesiones.GetSession(j).QueryInterface(IAudioSessionControl2)
            if s.GetState() == ACTIVA:
                exe = _exe_de_pid(s.GetProcessId())
                if exe:
                    exes.append(exe)
    return exes


def _exe_de_pid(pid):
    """Nombre del ejecutable de un proceso, en minúsculas («chrome.exe»), o None si no se puede abrir."""
    if not pid:
        return None
    k = ctypes.windll.kernel32
    hp = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not hp:
        return None
    try:
        ruta, tam = ctypes.create_unicode_buffer(260), wt.DWORD(260)
        if not k.QueryFullProcessImageNameW(hp, 0, ruta, ctypes.byref(tam)):
            return None
        return ruta.value.rsplit("\\", 1)[-1].lower()
    finally:
        k.CloseHandle(hp)


def _por_registro():
    """Lo que anota CapabilityAccessManager (Windows dejó de escribirlo el 22/10/2026; queda de respaldo)."""
    activas = []
    try:
        raiz = winreg.OpenKey(winreg.HKEY_CURRENT_USER, CLAVE_MIC)
    except OSError:
        return activas
    with raiz:
        for i in range(winreg.QueryInfoKey(raiz)[0]):
            sub = winreg.EnumKey(raiz, i)
            exe = sub.rsplit("#", 1)[-1].lower()
            if exe not in APPS:
                continue
            with winreg.OpenKey(raiz, sub) as k:
                try:
                    inicio = winreg.QueryValueEx(k, "LastUsedTimeStart")[0]
                    fin = winreg.QueryValueEx(k, "LastUsedTimeStop")[0]
                except FileNotFoundError:
                    continue
                if inicio and fin == 0:
                    activas.append(exe)
    return activas


def titulos_ventanas():
    """Títulos de las ventanas visibles de las apps de reunión (cada ventana muestra su pestaña activa)."""
    u = ctypes.windll.user32
    titulos = []

    def cb(h, _):
        if not u.IsWindowVisible(h):
            return True
        n = u.GetWindowTextLengthW(h)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, buf, n + 1)
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if _exe_de_pid(pid.value) in APPS:
            titulos.append(buf.value)
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    return titulos


class Vigia(threading.Thread):
    """`al_terminar(gid)` se llama cuando una grabación queda lista para transcribir; `evento(tipo, datos)` avisa a la
    app (grabación empezada, terminada, descartada, alertas). El grabador y los relojes se inyectan para las pruebas."""

    def __init__(self, al_terminar, evento=lambda tipo, datos: None, detector=apps_con_mic, ventanas=titulos_ventanas,
                 grabador=Grabacion, reloj=time.monotonic, reloj_pared=time.time):
        super().__init__(name="vigia", daemon=True)
        self.al_terminar, self.evento = al_terminar, evento
        self.detector, self.ventanas, self.grabador = detector, ventanas, grabador
        self.reloj, self.reloj_pared = reloj, reloj_pared
        self._parar = threading.Event()
        self._lock = threading.RLock()
        self.grab, self.gid, self.carpeta, self.apps = None, None, None, []
        self.soltado_desde, self.ultimo_titulo, self.titulos = None, 0.0, {}
        self.inicio_monotonic = None
        self.ultimo_paso = None  # (reloj de pared, monotónico) de la revisión anterior
        self._cambios = []  # cambios de dispositivo avisados desde el hilo del grabador
        # Tras un corte por silencio, la pestaña que quedó abierta sigue con el micrófono tomado: no se vuelve a grabar
        # hasta que lo suelte (si no, cada 12 minutos habría otra grabación de silencio).
        self.esperar_soltado = False
        # Un corte automático (suspensión) de una reunión marcada «solo en esta PC»: la que sigue hereda la marca.
        self._heredar_solo_local = None
        self.solo_local = False
        self.manual, self.titulo_manual = False, None
        # Grabar a mano con Minuta en pausa graba igual (es un pedido explícito) sin levantar la pausa: al terminar,
        # la detección automática sigue pausada. Una pausa pedida durante la manual sí la corta.
        self._pausa_al_empezar = False
        self._reiniciar_audio()

    def _reiniciar_audio(self):
        self.alertas = {}
        self.mudo_desde, self.sistema_sono, self.silencio_desde, self.ultimo_reintento = None, False, None, None
        self.sistema_mudo_desde = None

    # --- ciclo -------------------------------------------------------------------------------------------------

    def run(self):
        self.recuperar_interrumpidas()
        while not self._parar.wait(SONDEO_S):
            try:
                self.paso()
            except Exception:
                log.exception("error en el ciclo del vigía")
        self.detener_grabacion(motivo="cierre")

    def parar(self):
        self._parar.set()

    def paso(self):
        with self._lock:
            ahora, pared = self.reloj(), self.reloj_pared()
            anterior, self.ultimo_paso = self.ultimo_paso, (pared, ahora)
            if self.grab and anterior and pared - anterior[0] > SUSPENSION_S:
                self.detener_grabacion(motivo="suspension", corte_en=anterior[1])
                return
            pausado = db.ajuste("pausado")
            if not pausado:
                self._pausa_al_empezar = False
            activas = [] if pausado else self.detector()
            if not activas:
                self.esperar_soltado = False
            if activas and not self.grab and not self.esperar_soltado:
                self.empezar(activas)
            elif self.grab:
                if pausado and not (self.manual and self._pausa_al_empezar):
                    self.detener_grabacion(motivo="pausa")
                    return
                if activas or self.manual:  # la manual no depende de que una app tenga el micrófono
                    self.soltado_desde = None
                elif self.soltado_desde is None:
                    self.soltado_desde = ahora
                elif ahora - self.soltado_desde > SOLTADO_S:
                    self.detener_grabacion(motivo="fin")
                    return
                if ahora - self.ultimo_titulo > TITULOS_CADA_S:
                    self.ultimo_titulo = ahora
                    for t in self.ventanas():
                        self.titulos[t] = self.titulos.get(t, 0) + 1
                self.revisar_audio(ahora)

    def revisar_audio(self, ahora):
        """Alertas del audio y corte por silencio. Mira el pico de cada pista desde la revisión anterior."""
        mic, sistema = self.grab.mic.leer_pico(), self.grab.sistema.leer_pico()
        while self._cambios:
            tipo, nombre = self._cambios.pop(0)
            que = "el micrófono" if tipo == "mic" else "la salida de audio"
            self.alertar("dispositivo", f"Cambió {que}: ahora se graba con {nombre}.", efimera=True)
        for tipo, a in list(self.alertas.items()):
            if a.get("_expira") and ahora >= a["_expira"]:
                self.resolver(tipo)

        # La pista de los demás quedó cerrada (falló al reabrir): sin esto, «Ellos» quedaría mudo sin aviso.
        if not getattr(self.grab, "sistema_abierto", lambda: True)():
            self.alertar("sin_senal", "No se está grabando el audio de los demás. Minuta intenta reabrirlo.")
            self._reintentar(ahora)
        # El micrófono dejó de mandar audio: se intenta reabrir cada 30 s.
        elif ahora - max(self.grab.mic.ultimo_audio, self.inicio_monotonic) > SIN_SENAL_S:
            self.alertar("sin_senal", "El micrófono dejó de mandar audio. Minuta intenta reabrirlo.")
            self._reintentar(ahora)
        else:
            self.resolver("sin_senal")
            self.ultimo_reintento = None
            self._revisar_mudo(ahora, mic, sistema)
        self._revisar_sistema_mudo(ahora, sistema)

        # Silencio en las dos pistas: se pregunta y, si nadie contesta, se corta donde empezó.
        if sistema < SILENCIO_SISTEMA_RMS and mic < SILENCIO_MIC_RMS:
            self.silencio_desde = self.silencio_desde or ahora
            callado = ahora - self.silencio_desde
            if callado >= SILENCIO_S + SILENCIO_GRACIA_S:
                self._cortar_por_silencio()
            elif callado >= SILENCIO_S:
                corta_en = self.reloj_pared() + (SILENCIO_S + SILENCIO_GRACIA_S - callado)
                self.alertar("silencio", "Hace 10 minutos que no se escucha nada. ¿Sigue la reunión?",
                             vence=datetime.fromtimestamp(corta_en).astimezone().isoformat(timespec="seconds"))
        else:
            self.silencio_desde = None
            self.resolver("silencio")

    def _reintentar(self, ahora):
        if self.ultimo_reintento is None or ahora - self.ultimo_reintento >= REINTENTO_S:
            self.ultimo_reintento = ahora
            try:
                self.grab.reabrir()
            except Exception as e:
                log.warning("no se pudo reabrir el audio: %r", e)

    def _revisar_mudo(self, ahora, mic, sistema):
        """Micrófono en silencio digital mientras los demás hablan. En una presencial los demás no suenan por el
        sistema: el micrófono mudo ya es la falla."""
        if mic < MUDO_RMS:
            self.mudo_desde = self.mudo_desde or ahora
            self.sistema_sono = self.sistema_sono or sistema > SISTEMA_SUENA_RMS
            if ahora - self.mudo_desde >= MUDO_S and (self.sistema_sono or self.manual):
                cuando = "" if self.manual else " mientras los demás hablan"
                self.alertar("mic_mudo", f"El micrófono «{self.grab.mic.dispositivo}» no capta nada{cuando}. "
                                         "Revisá que no esté silenciado en Windows.")
        else:
            self.mudo_desde, self.sistema_sono = None, False
            self.resolver("mic_mudo")

    def _revisar_sistema_mudo(self, ahora, sistema):
        """Dos minutos sin audio de los demás en una reunión de una app: la reunión suena por una salida que no se
        está grabando. El grabador ya pasa solo a la salida que suena; esto cubre la que PortAudio no ve. En una
        presencial los demás no suenan por el sistema, así que no aplica."""
        if self.manual or sistema >= MUDO_RMS:
            self.sistema_mudo_desde = None
            self.resolver("sistema_mudo")
            return
        self.sistema_mudo_desde = self.sistema_mudo_desde or ahora
        if ahora - self.sistema_mudo_desde >= SISTEMA_MUDO_S:
            self.alertar("sistema_mudo", f"No se escucha a los demás por «{self.grab.sistema.dispositivo}». "
                                         "Si la reunión suena por otro lado, elegilo como salida en Windows.")

    def _cortar_por_silencio(self):
        # En una presencial no hay pestaña olvidada: el «silencio» puede ser la sala hablando bajo. Se corta acá, sin
        # tirar lo grabado desde que empezó.
        gid = self.detener_grabacion(motivo="silencio", corte_en=None if self.manual else self.silencio_desde)
        self.esperar_soltado = True
        return gid

    def alertar(self, tipo, mensaje, vence=None, efimera=False):
        if tipo in self.alertas and not efimera:  # una efímera se reemplaza: el último cambio de dispositivo manda
            return
        a = {"tipo": tipo, "mensaje": mensaje, "desde": datetime.now().isoformat(timespec="seconds"), "vence": vence}
        if efimera:
            a["_expira"] = self.reloj() + ALERTA_EFIMERA_S
        self.alertas[tipo] = a
        log.info("alerta %s: %s", tipo, mensaje)
        self.evento("alerta", self._publica(a))

    def resolver(self, tipo):
        if self.alertas.pop(tipo, None):
            self.evento("alerta_fin", {"tipo": tipo})

    @staticmethod
    def _publica(a):
        return {k: v for k, v in a.items() if not k.startswith("_")}

    def _al_cambiar_dispositivo(self, tipo, nombre):
        # Llega desde el hilo del grabador: no se toma el lock (cerrar la grabación espera a ese hilo).
        self._cambios.append((tipo, nombre))

    def recuperar_interrumpidas(self):
        """Grabaciones que quedaron abiertas por un corte (apagado, cuelgue): los bloques ya escritos se aprovechan."""
        for g in db.grabaciones(estados=["grabando"]):
            db.actualizar_grabacion(g["id"], estado="grabada", fin=g["actualizado"],
                                    error="interrumpida: se transcribe lo que alcanzó a grabarse")
            log.info("recuperada grabación interrumpida %s", g["id"])
            self.al_terminar(g["id"])

    # --- grabación -----------------------------------------------------------------------------------------------

    def empezar_manual(self, titulo=None, carpeta=None):
        """Graba sin que ninguna app tenga el micrófono (una reunión presencial). Devuelve el id, o None si ya se está
        grabando. El título y la carpeta, si se dan, identifican la reunión al transcribirla."""
        with self._lock:
            if self.grab:
                return None
            self.empezar([MANUAL], manual=True)
            self.titulo_manual = titulo
            self._pausa_al_empezar = bool(db.ajuste("pausado"))
            if titulo or carpeta:
                db.actualizar_grabacion(self.gid, titulo_manual=titulo or None, carpeta_manual=carpeta or None)
            return self.gid

    def pausa_pedida(self):
        """Pausar durante una grabación a mano la corta, aunque Minuta ya estuviera en pausa al empezarla."""
        with self._lock:
            self._pausa_al_empezar = False
            self.paso()

    def empezar(self, activas, manual=False):
        base = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.gid, n = base, 2
        while db.grabacion(self.gid) or (config.GRABACIONES / self.gid).exists():  # «cortar acá» en el mismo segundo
            self.gid, n = f"{base}-{n}", n + 1
        self.carpeta = config.GRABACIONES / self.gid
        self.apps, self.titulos, self.soltado_desde, self.ultimo_titulo = activas, {}, None, 0.0
        self.manual, self.titulo_manual = manual, None
        self.inicio_monotonic = self.reloj()
        self._reiniciar_audio()
        self._cambios.clear()
        self.grab = self.grabador(self.carpeta, log=log.info, al_cambiar=self._al_cambiar_dispositivo)
        db.crear_grabacion(self.gid, self.carpeta, datetime.now().isoformat(timespec="seconds"), activas)
        heredada = self._heredar_solo_local is not None and self.reloj() - self._heredar_solo_local < 15 * 60
        self._heredar_solo_local = None
        self.solo_local = heredada
        if heredada:
            db.actualizar_grabacion(self.gid, solo_local=1)
        log.info("empieza %s (%s)", self.gid, ", ".join(activas))
        self.evento("grabacion_empezada", {"id": self.gid, "apps": activas})

    def detener_grabacion(self, motivo, descartar=False, corte_en=None):
        """Cierra la grabación en curso. `fin`: el mic se soltó hace SOLTADO_S. `silencio`/`suspension`: se corta en
        `corte_en` (reloj monotónico). `pausa`/`cierre`/`cortada`: se corta ahora. `descartar`: el usuario dijo que no
        es una reunión; se borra todo."""
        with self._lock:
            if not self.grab:
                return None
            gid, carpeta = self.gid, self.carpeta
            if corte_en is None and motivo == "fin":
                corte_en = self.soltado_desde
            corte_s = round(corte_en - self.grab.t0, 1) if corte_en is not None else None
            # Al despertar, el reloj ya incluye el tiempo dormido: no se rellena de ceros hasta ahora.
            total = self.grab.cerrar(hasta=corte_en) if motivo == "suspension" else self.grab.cerrar()
            if motivo == "suspension" and self.solo_local:
                self._heredar_solo_local = self.reloj()
            dur = corte_s if corte_s is not None else total
            self.grab, self.manual = None, False
            self._reiniciar_audio()
            minimo = MINIMO_CORTADA_S if motivo == "cortada" else MINIMO_S
            if descartar or dur < minimo:
                shutil.rmtree(carpeta, ignore_errors=True)
                db.borrar_marcas_de(gid)  # «se borra todo»: también lo que se marcó
                db.actualizar_grabacion(gid, estado="descartada", duracion_s=round(dur),
                                        error="no es una reunión" if descartar else f"duró {dur:.0f} s")
                log.info("descartada %s (%s, %.0f s)", gid, motivo, dur)
                self.evento("grabacion_descartada", {"id": gid, "motivo": "usuario" if descartar else "corta"})
                return gid
            dias = db.ajuste("audio_dias")
            db.actualizar_grabacion(gid, estado="grabada", fin=datetime.now().isoformat(timespec="seconds"),
                                    duracion_s=round(dur), corte_s=corte_s, titulos_ventana=self.titulos,
                                    audio_hasta=(datetime.now() + timedelta(days=dias)).date().isoformat())
            log.info("termina %s: %.0f min (%s)", gid, dur / 60, motivo)
            self.evento("grabacion_terminada", {"id": gid, "minutos": round(dur / 60), "motivo": motivo})
            self.al_terminar(gid)
            return gid

    def descartar_actual(self):
        return self.detener_grabacion(motivo="usuario", descartar=True)

    def cortar_actual(self):
        """Cierra la grabación acá. Si la app sigue con el micrófono tomado, la próxima revisión abre otra: sirve para
        dos reuniones seguidas en la misma pestaña."""
        with self._lock:
            if "silencio" in self.alertas and self.silencio_desde is not None:  # «Terminó, cortar ahora»
                return self._cortar_por_silencio()
            return self.detener_grabacion(motivo="cortada")

    def marcar(self, etiqueta=None, nota=None):
        """Marca este momento de la grabación (atajo global o botón). Devuelve la marca o None si no se graba."""
        with self._lock:
            if not self.grab:
                return None
            m = db.crear_marca(self.gid, self.reloj() - self.grab.t0, etiqueta, nota)
            self.evento("marca", m)
            return m

    def seguir_actual(self):
        """El usuario dijo que la reunión sigue: el conteo de silencio vuelve a empezar."""
        with self._lock:
            if not self.grab:
                return None
            self.silencio_desde = self.reloj()
            self.resolver("silencio")
            return self.gid

    def solo_local_actual(self, valor=True):
        """Esta reunión no sale de la PC, aunque la regla de su carpeta permita la nube."""
        with self._lock:
            if not self.grab:
                return None
            db.actualizar_grabacion(self.gid, solo_local=int(valor))
            self.solo_local = bool(valor)
            return self.gid

    # --- estado para la app --------------------------------------------------------------------------------------

    def estado(self):
        with self._lock:
            pausado = bool(db.ajuste("pausado"))
            if not self.grab:
                # La detección caída tiene que verse: del 22 al 26/10 no se grabó ninguna reunión sola, sin aviso.
                return {"grabando": False, "pausado": pausado, "sin_deteccion": deteccion_caida()}
            ahora = self.reloj()

            def nivel(p):
                return round(p.nivel, 4) if ahora - p.ultimo_audio < 0.6 else 0.0

            vistos = sorted(self.titulos.items(), key=lambda x: -x[1])[:5]
            return {"grabando": True, "pausado": pausado, "id": self.gid, "apps": self.apps, "manual": self.manual,
                    "titulo": self.titulo_manual, "solo_local": self.solo_local,
                    "segundos": round(ahora - self.inicio_monotonic), "soltado": self.soltado_desde is not None,
                    "niveles": {"mic": nivel(self.grab.mic), "sistema": nivel(self.grab.sistema)},
                    "dispositivos": {"mic": self.grab.mic.dispositivo, "salida": self.grab.salida},
                    "ventanas": [t for t, _ in vistos],
                    "alertas": [self._publica(a) for a in self.alertas.values()],
                    "marcas": db.marcas(grabacion_id=self.gid)}
