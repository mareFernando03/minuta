import numpy as np
import pytest
from fastapi.testclient import TestClient

from minuta import dictado as dictado_mod
from minuta.api.servidor import crear_app
from minuta.captura.grabador import PistaMemoria
from minuta.datos import db

TOKEN = "t" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SR = 16000


def pcm(segundos, amplitud=0.1):
    t = np.arange(int(segundos * SR)) / SR
    return (np.sin(2 * np.pi * 220 * t) * amplitud * 32767).astype("<i2").tobytes()


class MicFalso:
    """Hace de micrófono propio: lo que se le dé a `hablar` llega al oyente."""
    abiertos = []

    def __init__(self, oyente):
        self.oyente, self.cerrado = oyente, False
        MicFalso.abiertos.append(self)

    def hablar(self, segundos):
        self.oyente(pcm(segundos), 0.07)

    def cerrar(self):
        self.cerrado = True


class PistaDeReunion:
    def __init__(self):
        self.oyentes = []


class VigiaFalso:
    def __init__(self, grab=None):
        self.grab = grab


class GrabFalsa:
    def __init__(self):
        self.mic = PistaDeReunion()


@pytest.fixture
def eventos():
    return []


@pytest.fixture
def dictado(eventos):
    MicFalso.abiertos = []
    db.guardar_ajuste("dictado", True)
    pedidos = []

    def transcribir(audio, motor):
        pedidos.append((len(audio) / SR, motor))
        return "Hola Pedro, mirá lo de Contoso", motor, len(audio) / SR

    d = dictado_mod.Dictado(vigia=VigiaFalso(), evento=lambda tipo, datos: eventos.append((tipo, datos)),
                            abrir_mic=MicFalso, transcribir=transcribir, calentar=lambda: None)
    d.pedidos = pedidos
    return d


def test_apagado_por_defecto():
    assert db.ajuste("dictado") is False
    d = dictado_mod.Dictado(abrir_mic=MicFalso)
    with pytest.raises(dictado_mod.Apagado):
        d.empezar()


def test_escucha_transcribe_y_guarda(dictado, eventos):
    dictado.empezar()
    MicFalso.abiertos[-1].hablar(2)
    r = dictado.terminar()
    assert r["texto"] == "Hola Pedro, mirá lo de Contoso "  # el espacio final deja seguir dictando
    assert r["motor"] == "groq"
    assert dictado.pedidos == [(2.0, "groq")]
    assert MicFalso.abiertos[-1].cerrado
    assert [d["texto"] for d in db.dictados()] == [r["texto"]]
    assert db.uso_por_motor()["groq"]["segundos"] == 2.0
    estados = [d["estado"] for t, d in eventos if t == "dictado"]
    assert estados[0] == "escuchando" and estados[-2:] == ["transcribiendo", "listo"]


def test_un_toque_no_transcribe(dictado, eventos):
    dictado.empezar()
    MicFalso.abiertos[-1].hablar(0.1)
    assert dictado.terminar()["texto"] == ""
    assert dictado.pedidos == [] and db.dictados() == []


def test_dos_a_la_vez_no(dictado):
    dictado.empezar()
    with pytest.raises(dictado_mod.Ocupado):
        dictado.empezar()
    dictado.cancelar()
    dictado.empezar()  # cancelado, se puede volver a empezar
    dictado.cancelar()


def test_cancelar_no_transcribe_ni_guarda(dictado):
    dictado.empezar()
    MicFalso.abiertos[-1].hablar(2)
    dictado.cancelar()
    assert dictado.terminar() == {"texto": "", "motor": None, "ms": 0}
    assert dictado.pedidos == [] and db.dictados() == []


def test_con_reunion_grabando_escucha_la_pista_sin_abrir_otro_mic(dictado):
    grab = GrabFalsa()
    dictado.vigia = VigiaFalso(grab)
    dictado.empezar()
    assert MicFalso.abiertos == [] and len(grab.mic.oyentes) == 1
    grab.mic.oyentes[0](pcm(1.5), 0.05)
    assert dictado.terminar()["texto"]
    assert grab.mic.oyentes == []  # la reunión sigue grabando sin el dictado colgado


def test_no_escucha_mas_de_tres_minutos():
    e = dictado_mod.Escucha()
    for _ in range(4):
        e(pcm(60, 0.01), 0.01)
    assert len(e.audio()) == dictado_mod.MAX_S * SR


def test_solo_guarda_los_ultimos(dictado, monkeypatch):
    monkeypatch.setattr(db, "DICTADOS_GUARDADOS", 3)
    for _ in range(5):
        db.guardar_dictado("x ", 1.0, "groq", 300)
    assert len(db.dictados(10)) == 3


def test_la_pista_de_memoria_avisa_a_los_oyentes_y_no_escribe():
    llegaron = []
    p = PistaMemoria(None, "dictado", lambda m: None)
    p.oyentes.append(lambda pcm_, nivel: llegaron.append(len(pcm_)))
    p.ch, p.sr = 1, SR
    import av
    p.resampler = av.AudioResampler(format="s16", layout="mono", rate=SR)
    p._cb(pcm(0.064), 1024, None, 0)
    assert sum(llegaron) > 0 and p.escritas == 0 and p.wav is None


def test_groq_caido_cae_a_local(monkeypatch):
    from minuta.transcripcion import local, nube

    def caido(audio, timeout=15):
        raise nube.SinNube("red: sin conexión")

    class Seg:
        text = " desde local "

    class Modelo:
        def transcribe(self, audio, **kw):
            return [Seg()], None

    monkeypatch.setattr(nube, "groq_dictado", caido)
    monkeypatch.setattr(local, "modelo", lambda nombre: Modelo())
    assert dictado_mod.transcribir(np.zeros(SR, dtype=np.float32), "groq") == ("desde local", "local", 1.0)


def test_api(dictado):
    c = TestClient(crear_app(TOKEN, dictado=dictado))
    assert c.post("/api/dictado/empezar").status_code == 401
    assert c.post("/api/dictado/empezar", headers=AUTH).status_code == 200
    assert c.post("/api/dictado/empezar", headers=AUTH).status_code == 409
    MicFalso.abiertos[-1].hablar(1)
    r = c.post("/api/dictado/terminar", headers=AUTH).json()
    assert r["texto"].startswith("Hola Pedro")
    assert c.get("/api/dictados", headers=AUTH).json()[0]["texto"] == r["texto"]
    db.guardar_ajuste("dictado", False)
    assert c.post("/api/dictado/empezar", headers=AUTH).status_code == 409


def test_api_sin_captura():
    c = TestClient(crear_app(TOKEN))
    assert c.post("/api/dictado/empezar", headers=AUTH).status_code == 503


def test_ajustes_del_dictado():
    c = TestClient(crear_app(TOKEN))
    a = c.get("/api/ajustes", headers=AUTH).json()
    assert a["dictado"] is False and a["dictado_motor"] == "groq"
    assert c.put("/api/ajustes", headers=AUTH, json={"dictado": True, "dictado_motor": "local"}).status_code == 200
    assert db.ajuste("dictado") is True and db.ajuste("dictado_motor") == "local"
    assert c.put("/api/ajustes", headers=AUTH, json={"dictado_motor": "assemblyai"}).status_code == 422
    assert c.put("/api/ajustes", headers=AUTH, json={"dictado": "sí"}).status_code == 422


def esperar(condicion, segundos=2.0):
    import time
    limite = time.monotonic() + segundos
    while not condicion() and time.monotonic() < limite:
        time.sleep(0.02)
    return condicion()


def test_si_la_reunion_se_corta_sigue_con_el_mic_propio(dictado):
    grab = GrabFalsa()
    dictado.vigia = VigiaFalso(grab)
    dictado.empezar()
    grab.mic.oyentes[0](pcm(1), 0.05)
    dictado.vigia.grab = None  # el vigía cerró la grabación en medio del dictado
    assert esperar(lambda: len(MicFalso.abiertos) == 1)
    assert grab.mic.oyentes == []
    MicFalso.abiertos[0].hablar(1)
    dictado.terminar()
    assert dictado.pedidos == [(2.0, "groq")]  # lo dicho antes y después del corte
    assert MicFalso.abiertos[0].cerrado


def test_en_una_reunion_solo_en_esta_pc_el_dictado_no_sale(dictado):
    grab = GrabFalsa()
    dictado.vigia = VigiaFalso(grab)
    dictado.vigia.solo_local = True
    dictado.empezar()
    grab.mic.oyentes[0](pcm(1), 0.05)
    dictado.terminar()
    assert dictado.pedidos == [(1.0, "local")]


def test_sin_terminar_vence_y_cierra_el_mic(dictado, eventos, monkeypatch):
    monkeypatch.setattr(dictado_mod, "VENCE_S", 0.2)
    dictado.empezar()
    assert esperar(lambda: dictado.escucha is None)
    assert MicFalso.abiertos[-1].cerrado
    assert ("dictado", {"estado": "cancelado"}) in eventos


def test_a_los_tres_minutos_avisa_una_vez(dictado, eventos, monkeypatch):
    monkeypatch.setattr(dictado_mod, "MAX_S", 1)
    dictado.empezar()
    MicFalso.abiertos[-1].hablar(1.5)
    assert esperar(lambda: any(d.get("estado") == "error" for _, d in eventos))
    import time
    time.sleep(0.3)
    assert sum(d.get("estado") == "error" for _, d in eventos) == 1
    dictado.cancelar()


def test_si_falla_el_oyente_la_reunion_igual_escribe(tmp_path):
    import av
    from minuta.captura.grabador import Pista
    p = Pista(None, "mic", tmp_path, __import__("time").monotonic(), lambda m: None)
    p.ch, p.sr = 1, SR
    p.resampler = av.AudioResampler(format="s16", layout="mono", rate=SR)

    def roto(pcm_, nivel):
        raise RuntimeError("roto")
    p.oyentes.append(roto)
    p._cb(pcm(0.064), 1024, None, 0)
    assert p.escritas > 0
    p.cerrar(__import__("time").monotonic())


def test_calentar_carga_el_vad_una_sola_vez(monkeypatch):
    from minuta.transcripcion import nube
    llamadas = []
    monkeypatch.setattr(nube, "solo_voz", lambda audio, max_s: llamadas.append(len(audio)) or [])
    monkeypatch.setattr(dictado_mod, "_calentado", __import__("threading").Event())
    dictado_mod.calentar()
    dictado_mod.calentar()
    assert llamadas == [SR]
