"""El vigía con un grabador y relojes simulados: alertas, fin por silencio y suspensión, y «cortar acá»."""
import pytest

from minuta.captura import vigia as v
from minuta.datos import db


class Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class PistaFalsa:
    def __init__(self, reloj, dispositivo):
        self.reloj, self.dispositivo = reloj, dispositivo
        self.pico_fijo, self.nivel, self.vivo = 0.05, 0.05, True

    @property
    def ultimo_audio(self):  # mientras el dispositivo manda audio, el último llegó recién
        return self.reloj() if self.vivo else 0.0

    def leer_pico(self):
        return self.pico_fijo


class GrabadorFalso:
    creados = []

    def __init__(self, carpeta, log, al_cambiar):
        self.reloj = Reloj.actual
        self.t0, self.al_cambiar, self.reaperturas = self.reloj(), al_cambiar, 0
        self.mic, self.sistema = PistaFalsa(self.reloj, "Micrófono"), PistaFalsa(self.reloj, "Loopback")
        self.salida = "Parlantes"
        GrabadorFalso.creados.append(self)

    def reabrir(self):
        self.reaperturas += 1

    def cerrar(self, hasta=None):
        self.cerrado_hasta = hasta
        return round((self.reloj() if hasta is None else hasta) - self.t0, 1)


@pytest.fixture
def entorno():
    reloj, pared = Reloj(), Reloj()
    pared.t = 1_791_600_000.0
    Reloj.actual = reloj
    GrabadorFalso.creados = []
    eventos, terminadas, activas = [], [], ["slack.exe"]
    w = v.Vigia(al_terminar=terminadas.append, evento=lambda tipo, datos: eventos.append((tipo, datos)),
                detector=lambda: list(activas), ventanas=lambda: [], grabador=GrabadorFalso,
                reloj=reloj, reloj_pared=pared)

    def avanzar(segundos, paso=v.SONDEO_S):
        for _ in range(int(segundos // paso)):
            reloj.t += paso
            pared.t += paso
            w.paso()

    w.paso()  # empieza a grabar
    return w, avanzar, eventos, terminadas, activas, reloj, pared


def tipos(eventos, tipo):
    return [d for t, d in eventos if t == tipo]


def test_mic_mudo_mientras_los_demas_hablan_avisa_una_vez_y_se_resuelve(entorno):
    w, avanzar, eventos, *_ = entorno
    g = GrabadorFalso.creados[0]
    g.mic.pico_fijo = 0.0
    avanzar(v.MUDO_S + v.SONDEO_S)
    assert [a["tipo"] for a in tipos(eventos, "alerta")] == ["mic_mudo"]
    assert w.estado()["alertas"][0]["tipo"] == "mic_mudo"
    avanzar(30)
    assert len(tipos(eventos, "alerta")) == 1  # no repite
    g.mic.pico_fijo = 0.05
    avanzar(v.SONDEO_S)
    assert tipos(eventos, "alerta_fin") == [{"tipo": "mic_mudo"}] and w.estado()["alertas"] == []


def test_mic_en_silencio_sin_que_nadie_hable_no_avisa(entorno):
    w, avanzar, eventos, *_ = entorno
    g = GrabadorFalso.creados[0]
    g.mic.pico_fijo = g.sistema.pico_fijo = 0.0
    avanzar(v.MUDO_S * 2)
    assert "mic_mudo" not in [a["tipo"] for a in tipos(eventos, "alerta")]


def test_sin_audio_de_los_demas_avisa_a_los_dos_minutos_y_se_resuelve(entorno):
    # El caso del 22/10: la reunión sonaba por los auriculares y el loopback grabado quedó en ceros.
    w, avanzar, eventos, *_ = entorno
    g = GrabadorFalso.creados[0]
    g.sistema.pico_fijo = 0.0
    avanzar(v.SISTEMA_MUDO_S - v.SONDEO_S)
    assert "sistema_mudo" not in [a["tipo"] for a in tipos(eventos, "alerta")]
    avanzar(2 * v.SONDEO_S)
    alertas = [a for a in tipos(eventos, "alerta") if a["tipo"] == "sistema_mudo"]
    assert len(alertas) == 1 and "Loopback" in alertas[0]["mensaje"]
    avanzar(60)
    assert len([a for a in tipos(eventos, "alerta") if a["tipo"] == "sistema_mudo"]) == 1  # no repite
    g.sistema.pico_fijo = 0.05
    avanzar(v.SONDEO_S)
    assert {"tipo": "sistema_mudo"} in tipos(eventos, "alerta_fin")


def test_mic_que_deja_de_mandar_audio_avisa_y_se_reabre(entorno):
    w, avanzar, eventos, *_ = entorno
    g = GrabadorFalso.creados[0]
    g.mic.vivo = False
    avanzar(v.SIN_SENAL_S + 2 * v.SONDEO_S)
    assert [a["tipo"] for a in tipos(eventos, "alerta")] == ["sin_senal"] and g.reaperturas == 1
    avanzar(v.REINTENTO_S)
    assert g.reaperturas == 2  # reintenta cada 30 s
    g.mic.vivo = True
    avanzar(v.SONDEO_S)
    assert {"tipo": "sin_senal"} in tipos(eventos, "alerta_fin")


def test_cambio_de_dispositivo_avisa_y_la_alerta_se_va_sola(entorno):
    w, avanzar, eventos, *_ = entorno
    GrabadorFalso.creados[0].al_cambiar("mic", "Auriculares Bluetooth")
    avanzar(v.SONDEO_S)
    assert "Auriculares Bluetooth" in tipos(eventos, "alerta")[0]["mensaje"]
    avanzar(v.ALERTA_EFIMERA_S + v.SONDEO_S)
    assert {"tipo": "dispositivo"} in tipos(eventos, "alerta_fin")


def test_silencio_pregunta_y_si_nadie_contesta_corta_donde_empezo(entorno):
    w, avanzar, eventos, terminadas, activas, reloj, _ = entorno
    avanzar(300)  # 5 minutos de reunión
    g = GrabadorFalso.creados[0]
    g.mic.pico_fijo, g.sistema.pico_fijo = 0.002, 0.0  # ruido de fondo en el mic; el loopback en silencio da cero
    callado_desde = reloj.t - g.t0
    avanzar(v.SILENCIO_S + v.SONDEO_S)
    alerta = tipos(eventos, "alerta")[-1]
    assert alerta["tipo"] == "silencio" and alerta["vence"]
    avanzar(v.SILENCIO_GRACIA_S)
    gid = terminadas[-1]
    grab = db.grabacion(gid)
    assert grab["estado"] == "grabada" and abs(grab["corte_s"] - (callado_desde + v.SONDEO_S)) <= v.SONDEO_S
    assert tipos(eventos, "grabacion_terminada")[-1]["motivo"] == "silencio"


def test_si_el_usuario_dice_que_sigue_no_corta(entorno):
    w, avanzar, eventos, terminadas, *_ = entorno
    g = GrabadorFalso.creados[0]
    g.mic.pico_fijo = g.sistema.pico_fijo = 0.0
    avanzar(v.SILENCIO_S + v.SONDEO_S)
    assert w.seguir_actual()
    assert {"tipo": "silencio"} in tipos(eventos, "alerta_fin")
    avanzar(v.SILENCIO_GRACIA_S + v.SONDEO_S)
    assert terminadas == [] and w.grab is not None


def test_la_pc_dormida_corta_en_la_ultima_revision(entorno):
    w, avanzar, eventos, terminadas, activas, reloj, pared = entorno
    avanzar(400)
    ultimo = reloj.t - GrabadorFalso.creados[0].t0
    reloj.t += 3600  # en Windows el monotónico también avanza durante la suspensión
    pared.t += 3600
    w.paso()
    grab = db.grabacion(terminadas[-1])
    assert grab["estado"] == "grabada" and grab["corte_s"] == round(ultimo, 1)
    assert tipos(eventos, "grabacion_terminada")[-1]["motivo"] == "suspension"


def test_cortar_aca_cierra_y_abre_otra_si_el_mic_sigue_tomado(entorno):
    w, avanzar, eventos, terminadas, *_ = entorno
    avanzar(60)
    primera = w.cortar_actual()
    assert db.grabacion(primera)["estado"] == "grabada"  # 60 s alcanzan para un corte a mano
    avanzar(v.SONDEO_S)
    assert w.grab is not None and w.gid != primera and len(GrabadorFalso.creados) == 2


def test_marcar_guarda_el_segundo_de_la_grabacion(entorno):
    w, avanzar, eventos, *_ = entorno
    avanzar(90)
    m = w.marcar("pregunta", "¿Quién valida?")
    assert m["t"] == 90.0 and m["etiqueta"] == "pregunta" and tipos(eventos, "marca") == [m]
    assert w.estado()["marcas"] == [m]


def test_tras_cortar_por_silencio_no_vuelve_a_grabar_la_pestana_abierta(entorno):
    w, avanzar, eventos, terminadas, activas, *_ = entorno
    avanzar(300)
    g = GrabadorFalso.creados[0]
    g.mic.pico_fijo = g.sistema.pico_fijo = 0.0
    avanzar(v.SILENCIO_S + v.SILENCIO_GRACIA_S + v.SONDEO_S)
    assert len(terminadas) == 1
    avanzar(60)
    assert w.grab is None and len(GrabadorFalso.creados) == 1  # el mic sigue tomado, pero no se regraba
    activas.clear()
    avanzar(v.SONDEO_S)  # la pestaña soltó el micrófono
    activas.append("chrome.exe")
    avanzar(v.SONDEO_S)
    assert w.grab is not None and len(GrabadorFalso.creados) == 2  # una reunión nueva sí se graba


def test_terminó_cortar_ahora_corta_donde_empezo_el_silencio(entorno):
    w, avanzar, eventos, terminadas, activas, reloj, _ = entorno
    avanzar(300)
    g = GrabadorFalso.creados[0]
    g.mic.pico_fijo = g.sistema.pico_fijo = 0.0
    callado_desde = reloj.t - g.t0
    avanzar(v.SILENCIO_S + v.SONDEO_S)
    gid = w.cortar_actual()
    assert abs(db.grabacion(gid)["corte_s"] - callado_desde) <= 2 * v.SONDEO_S
    avanzar(30)
    assert w.grab is None


def test_cambios_de_dispositivo_seguidos_muestran_el_ultimo(entorno):
    w, avanzar, eventos, *_ = entorno
    g = GrabadorFalso.creados[0]
    g.al_cambiar("mic", "Auriculares")
    avanzar(v.SONDEO_S)
    g.al_cambiar("mic", "Webcam")
    avanzar(v.SONDEO_S)
    assert "Webcam" in w.estado()["alertas"][0]["mensaje"]


def test_no_es_una_reunion_borra_tambien_las_marcas(entorno):
    w, avanzar, *_ = entorno
    avanzar(60)
    w.marcar("accion", "algo privado")
    gid = w.descartar_actual()
    assert db.marcas(grabacion_id=gid) == []


def test_tras_la_suspension_la_reunion_sigue_solo_local_y_no_se_rellena_lo_dormido(entorno):
    w, avanzar, eventos, terminadas, activas, reloj, pared = entorno
    avanzar(400)
    w.solo_local_actual(True)
    ultimo = reloj.t
    reloj.t += 3600
    pared.t += 3600
    w.paso()  # corta por suspensión
    assert GrabadorFalso.creados[0].cerrado_hasta == ultimo
    avanzar(v.SONDEO_S)  # Chrome sigue con el mic: empieza otra
    assert w.grab is not None and db.grabacion(w.gid)["solo_local"] == 1 and w.estado()["solo_local"]


def test_si_la_pista_de_los_demas_quedo_cerrada_avisa_y_reintenta(entorno):
    w, avanzar, eventos, *_ = entorno
    g = GrabadorFalso.creados[0]
    g.sistema_abierto = lambda: False
    avanzar(v.SONDEO_S)
    assert "los demás" in tipos(eventos, "alerta")[-1]["mensaje"] and g.reaperturas == 1


# --- grabar a mano ------------------------------------------------------------------------------------------------

@pytest.fixture
def sin_apps():
    """Ninguna app tiene el micrófono (una reunión presencial): el vigía no graba solo."""
    reloj, pared = Reloj(), Reloj()
    pared.t = 1_791_600_000.0
    Reloj.actual = reloj
    GrabadorFalso.creados = []
    eventos, terminadas = [], []
    w = v.Vigia(al_terminar=terminadas.append, evento=lambda tipo, datos: eventos.append((tipo, datos)),
                detector=lambda: [], ventanas=lambda: [], grabador=GrabadorFalso, reloj=reloj, reloj_pared=pared)

    def avanzar(segundos, paso=v.SONDEO_S):
        for _ in range(int(segundos // paso)):
            reloj.t += paso
            pared.t += paso
            w.paso()

    w.paso()
    return w, avanzar, eventos, terminadas


def test_grabar_a_mano_no_se_corta_por_falta_de_app(sin_apps):
    w, avanzar, eventos, terminadas = sin_apps
    assert not w.estado()["grabando"]
    gid = w.empezar_manual("Charla con Pedro", "Globex")
    avanzar(10 * 60)  # mucho más que los 90 s de mic suelto
    e = w.estado()
    assert e["grabando"] and e["manual"] and e["apps"] == [v.MANUAL]
    g = db.grabacion(gid)
    assert g["apps"] == [v.MANUAL] and g["titulo_manual"] == "Charla con Pedro" and g["carpeta_manual"] == "Globex"
    assert terminadas == []


def test_grabar_a_mano_no_pisa_una_grabacion_en_curso(entorno):
    w, *_ = entorno
    assert w.estado()["grabando"]
    assert w.empezar_manual() is None


def test_la_manual_termina_con_cortar_y_no_vuelve_a_empezar(sin_apps):
    w, avanzar, eventos, terminadas = sin_apps
    gid = w.empezar_manual()
    avanzar(60)
    assert w.cortar_actual() == gid and terminadas == [gid]
    avanzar(30)
    assert not w.estado()["grabando"] and not w.manual


def test_la_manual_se_corta_por_silencio_de_la_sala(sin_apps):
    w, avanzar, eventos, terminadas = sin_apps
    gid = w.empezar_manual()
    avanzar(5 * 60)
    g = GrabadorFalso.creados[-1]
    g.mic.pico_fijo = g.sistema.pico_fijo = 0.0
    avanzar(v.SILENCIO_S + v.SILENCIO_GRACIA_S + 6)
    assert terminadas == [gid] and db.grabacion(gid)["estado"] == "grabada"


def test_en_la_manual_el_mic_mudo_avisa_aunque_el_sistema_no_suene(sin_apps):
    w, avanzar, eventos, _ = sin_apps
    w.empezar_manual()
    g = GrabadorFalso.creados[-1]
    g.mic.pico_fijo, g.sistema.pico_fijo = 0.0, 0.0
    avanzar(v.MUDO_S + 6)
    alertas = tipos(eventos, "alerta")
    assert any(a["tipo"] == "mic_mudo" and "mientras" not in a["mensaje"] for a in alertas)


def test_en_la_manual_el_sistema_mudo_no_avisa(sin_apps):
    w, avanzar, eventos, _ = sin_apps
    w.empezar_manual()
    GrabadorFalso.creados[-1].sistema.pico_fijo = 0.0  # presencial: los demás no suenan por el sistema
    avanzar(v.SISTEMA_MUDO_S * 2)
    assert "sistema_mudo" not in [a["tipo"] for a in tipos(eventos, "alerta")]


def test_la_manual_graba_en_pausa_y_la_pausa_sigue_al_terminar(sin_apps):
    w, avanzar, eventos, terminadas = sin_apps
    db.guardar_ajuste("pausado", True)
    gid = w.empezar_manual()
    avanzar(60)
    assert w.estado()["grabando"]  # la pausa de antes no la corta
    w.cortar_actual()
    assert terminadas == [gid] and db.ajuste("pausado") is True


def test_pausar_durante_la_manual_la_corta(sin_apps):
    w, avanzar, eventos, terminadas = sin_apps
    gid = w.empezar_manual()
    avanzar(4 * 60)  # más que el mínimo de una reunión
    db.guardar_ajuste("pausado", True)
    w.pausa_pedida()
    assert terminadas == [gid]


def test_en_pausa_desde_antes_pedir_pausa_igual_corta_la_manual(sin_apps):
    w, avanzar, eventos, terminadas = sin_apps
    db.guardar_ajuste("pausado", True)
    gid = w.empezar_manual()
    avanzar(4 * 60)  # más que el mínimo de una reunión
    w.pausa_pedida()
    assert terminadas == [gid]


def test_el_silencio_de_una_presencial_no_tira_lo_grabado(sin_apps):
    w, avanzar, eventos, terminadas = sin_apps
    gid = w.empezar_manual()
    avanzar(5 * 60)
    g = GrabadorFalso.creados[-1]
    g.mic.pico_fijo = g.sistema.pico_fijo = 0.0
    avanzar(v.SILENCIO_S + v.SILENCIO_GRACIA_S + 6)
    assert terminadas == [gid] and db.grabacion(gid)["corte_s"] is None and g.cerrado_hasta is None


# --- quién tiene el micrófono -------------------------------------------------------------------------------------

def test_detector_filtra_las_apps_sin_repetir_y_el_registro_no_se_suma():
    sesiones = lambda: ["granola.exe", "chrome.exe", "python.exe", "chrome.exe"]
    registro = lambda: ["chrome.exe", "slack.exe"]  # un Stop en 0 viejo: WASAPI respondió, no cuenta
    assert v.apps_con_mic((sesiones, registro)) == ["chrome.exe"]


def test_si_falla_wasapi_queda_el_registro_y_se_avisa(caplog):
    def _por_sesiones():
        raise OSError("COM")

    registro = lambda: ["slack.exe"]
    v._fallas_avisadas.clear()
    assert v.apps_con_mic((_por_sesiones, registro)) == ["slack.exe"]
    assert v.apps_con_mic((_por_sesiones, registro)) == ["slack.exe"]
    assert sum("no se pudo ver" in r.message for r in caplog.records) == 1  # una vez por racha, no cada 3 s
    assert v.deteccion_caida()

    def _por_sesiones():  # WASAPI vuelve: el aviso se va
        return []

    v.apps_con_mic((_por_sesiones, registro))
    assert not v.deteccion_caida()


def test_el_estado_avisa_la_deteccion_caida(sin_apps):
    w = sin_apps[0]
    v._fallas_avisadas.add("_por_sesiones")
    try:
        assert w.estado()["sin_deteccion"] is True
    finally:
        v._fallas_avisadas.clear()
    assert w.estado()["sin_deteccion"] is False


def test_por_defecto_mira_las_sesiones(monkeypatch):
    monkeypatch.setattr(v, "_por_sesiones", lambda: ["msedge.exe"])
    monkeypatch.setattr(v, "_por_registro", lambda: ["slack.exe"])
    assert v.apps_con_mic() == ["msedge.exe"]
