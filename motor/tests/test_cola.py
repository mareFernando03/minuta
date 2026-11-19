import json
import wave

import numpy as np
import pytest

from minuta import config
from minuta.datos import db
from minuta.trabajos import cola as cola_mod
from minuta.transcripcion import nube


def _grabacion(gid="20261009-172910", segundos=2):
    carpeta = config.GRABACIONES / gid
    carpeta.mkdir(parents=True)
    # El mic con voz cerca (un tono fuerte): si no, el filtro de lo ajeno descartaría todo lo del usuario.
    voz = (3000 * np.sin(np.arange(16000 * segundos) / 16000 * 2 * np.pi * 220)).astype("<i2")
    for p, audio in (("mic", voz), ("sistema", np.zeros(16000 * segundos, "<i2"))):
        with wave.open(str(carpeta / f"{p}-000.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(audio.tobytes())
    db.crear_grabacion(gid, carpeta, "2026-10-09T17:29:10", ["slack.exe"])
    db.actualizar_grabacion(gid, estado="grabada", fin="2026-10-09T17:40:00", duracion_s=660, corte_s=650.0,
                            titulos_ventana={"northwind-equipo-desarrollo (Canal) - Acme - Slack 🔊": 3})
    db.encolar("transcribir", gid)
    return gid, carpeta


@pytest.fixture
def entorno(monkeypatch, tmp_path):
    llamados, eventos = [], []
    monkeypatch.setattr(cola_mod, "clasificar", lambda *a: {
        "titulo": "Huddle de Slack: northwind-equipo-desarrollo", "folders": ["Seguimiento Northwind"],
        "participantes": [], "origen": "slack"})
    monkeypatch.setattr(cola_mod, "corregir_turnos", lambda turnos, como_texto: (
        [dict(t, texto=t["texto"].replace("upsetting", "appsettings")) for t in turnos],
        [("upsetting", "appsettings")], 0.003))

    def falla(_):
        llamados.append("assemblyai"); raise nube.SinNube("HTTP 500")

    def groq(_):
        llamados.append("groq")
        return [{"inicio": 1.0, "fin": 2.0, "texto": "no cambiaste el upsetting"}], 1.0

    monkeypatch.setitem(nube.MOTORES, "assemblyai", falla)
    monkeypatch.setitem(nube.MOTORES, "groq", groq)
    db.guardar_ajuste("exportar_a", str(tmp_path / "export"))
    c = cola_mod.Cola(evento=lambda tipo, datos: eventos.append((tipo, datos)))
    return c, llamados, eventos, tmp_path / "export"


def test_si_assemblyai_falla_sigue_con_groq_y_archiva(entorno):
    c, llamados, eventos, export = entorno
    gid, _ = _grabacion()
    c.ejecutar(db.tomar_trabajo(["transcribir"]))
    assert llamados == ["assemblyai", "groq", "groq"]  # AssemblyAI falla en la primera pista; Groq hace las dos
    g = db.grabacion(gid)
    assert g["estado"] == "archivada"
    r = db.reunion(g["reunion_id"])
    assert r["motor"] == "groq" and r["carpeta"] == "Seguimiento Northwind"
    assert r["segmentos"][0]["texto"] == "no cambiaste el appsettings"
    assert r["correcciones"] == [{"original": "upsetting", "correcto": "appsettings"}]
    exportados = [p for p in export.glob("*.json") if p.name != "indice.json"]
    assert len(exportados) == 1 and json.loads(exportados[0].read_text(encoding="utf-8"))["id"] == r["id"]
    assert ("reunion_lista", eventos[-1][1]) == eventos[-1] and not eventos[-1][1]["revisar"]
    assert db.uso_por_motor()["groq"]["segundos"] == 2.0
    assert [t["estado"] for t in db.trabajos() if t["tipo"] == "transcribir"] == ["hecho"]
    # Después: separar las voces de «Ellos» y resumir con Claude (la carpeta permite la nube).
    assert sorted(t["tipo"] for t in db.trabajos() if t["estado"] == "pendiente") == ["diarizar", "resumir"]


def test_lo_no_identificado_no_sale_de_la_notebook(entorno, monkeypatch):
    c, llamados, eventos, export = entorno
    monkeypatch.setattr(cola_mod, "clasificar", lambda *a: None)
    monkeypatch.setattr(c, "_local", lambda t, carpeta, tipo, **_: ([{"inicio": 0.0, "fin": 1.0, "texto": "hola"}], []))
    gid, _ = _grabacion()
    c.ejecutar(db.tomar_trabajo(["transcribir"]))
    assert llamados == []  # ningún motor en la nube
    g = db.grabacion(gid)
    assert g["estado"] == "revisar"
    assert db.reunion(g["reunion_id"])["titulo"] == "Grabación de las 17:29"
    assert not export.exists() or not [p for p in export.glob("*.json") if p.name != "indice.json"]
    assert db.trabajos()[0]["tipo"] == "repasar"  # lo local se repasa con turbo después


def test_reprocesar_con_un_motor_de_nube_no_saca_lo_que_no_sale(entorno, monkeypatch):
    """Lo no identificado, o marcado «solo en esta PC», sigue en local aunque se pida otro motor a mano."""
    c, llamados, *_ = entorno
    monkeypatch.setattr(cola_mod, "clasificar", lambda *a: None)
    monkeypatch.setattr(c, "_local", lambda t, carpeta, tipo, **_: ([{"inicio": 0.0, "fin": 1.0, "texto": "hola"}], []))
    gid, _ = _grabacion()
    db.encolar("transcribir", gid, aunque_corra=True, motor="groq")  # el reproceso pide groq a mano
    c.ejecutar(db.tomar_trabajo(["transcribir"]))
    assert llamados == []
    assert db.reunion(db.grabacion(gid)["reunion_id"])["motor"] != "groq"


def test_reprocesar_solo_local_aunque_la_carpeta_permita_la_nube(entorno, monkeypatch):
    c, llamados, *_ = entorno
    monkeypatch.setattr(c, "_local", lambda t, carpeta, tipo, **_: ([{"inicio": 0.0, "fin": 1.0, "texto": "hola"}], []))
    gid, _ = _grabacion()
    db.actualizar_grabacion(gid, solo_local=1)
    db.encolar("transcribir", gid, aunque_corra=True, motor="groq")
    c.ejecutar(db.tomar_trabajo(["transcribir"]))
    assert llamados == []


def test_reprocesar_con_un_motor_permitido_lo_usa(entorno):
    c, llamados, *_ = entorno
    gid, _ = _grabacion()
    db.encolar("transcribir", gid, aunque_corra=True, motor="groq")
    c.ejecutar(db.tomar_trabajo(["transcribir"]))
    assert llamados == ["groq", "groq"]  # sin pasar por AssemblyAI, que va primero en la regla


def test_a_bateria_lo_local_espera_sin_contar_como_error(entorno, monkeypatch):
    c, *_ = entorno
    monkeypatch.setattr(cola_mod, "clasificar", lambda *a: None)
    monkeypatch.setattr(cola_mod.local, "enchufada", lambda: False)
    gid, _ = _grabacion()
    c.ejecutar(db.tomar_trabajo(["transcribir"]))
    t = db.trabajos()[0]
    assert t["estado"] == "pendiente" and t["intentos"] == 0 and "batería" in t["progreso"]


def test_un_error_real_se_reintenta_y_al_quinto_queda_en_error(entorno, monkeypatch):
    c, *_ = entorno

    def rompe(_):
        raise ValueError("audio corrupto")

    monkeypatch.setitem(nube.MOTORES, "groq", rompe)
    db.guardar_ajuste("privacidad", {"*": ["groq"], "Seguimiento Northwind": ["groq"]})
    gid, _ = _grabacion()
    t = db.tomar_trabajo(["transcribir"])
    c.ejecutar(t)
    assert db.trabajos()[0]["estado"] == "pendiente" and "audio corrupto" in db.trabajos()[0]["error"]
    db.conexion().execute("UPDATE trabajos SET intentos = 4, proximo_intento = NULL, estado = 'pendiente'")
    c.ejecutar(db.tomar_trabajo(["transcribir"]))
    assert db.trabajos()[0]["estado"] == "error" and db.grabacion(gid)["estado"] == "error"


def test_reprocesar_con_un_motor_elegido(entorno):
    c, llamados, *_ = entorno
    gid, _ = _grabacion()
    db.conexion().execute("DELETE FROM trabajos")
    db.encolar("transcribir", gid, motor="groq")
    c.ejecutar(db.tomar_trabajo(["transcribir"]))
    assert llamados == ["groq", "groq"]


def test_borrar_audio_vencido_respeta_lo_pendiente(entorno):
    gid, carpeta = _grabacion()
    db.actualizar_grabacion(gid, estado="archivada", audio_hasta="2000-01-01")
    cola_mod.borrar_audio_vencido()
    assert list(carpeta.glob("*.wav"))  # el trabajo de transcribir sigue pendiente
    db.conexion().execute("UPDATE trabajos SET estado = 'hecho'")
    (carpeta / "mezcla.ogg").write_bytes(b"OggS")
    cola_mod.borrar_audio_vencido()
    assert not list(carpeta.glob("*.wav")) and not (carpeta / "mezcla.ogg").exists()


def test_solo_local_pedido_en_la_grabacion_no_usa_la_nube(entorno, monkeypatch):
    c, llamados, *_ = entorno
    monkeypatch.setattr(c, "_local", lambda t, carpeta, tipo, **_: ([{"inicio": 0.0, "fin": 1.0, "texto": "hola"}], []))
    gid, _ = _grabacion()
    db.actualizar_grabacion(gid, solo_local=1)
    c.ejecutar(db.tomar_trabajo(["transcribir"]))
    assert llamados == []  # la carpeta permite AssemblyAI y Groq, pero se pidió solo local
    assert db.reunion(db.grabacion(gid)["reunion_id"])["motor"] == "small"


def test_solo_se_lee_y_transcribe_hasta_el_corte(tmp_path):
    from minuta.transcripcion import local
    for n in (0, 1, 2):
        with wave.open(str(tmp_path / f"mic-{n:03d}.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(np.zeros(16000 * 10, "<i2").tobytes())
    assert [b.name for b in local.bloques_hasta(tmp_path, "mic", 310.0)] == ["mic-000.wav", "mic-001.wav"]
    assert len(local.leer_pista(tmp_path, "mic", 5.0)) == 16000 * 8  # corte + 3 s de margen
    assert len(local.leer_pista(tmp_path, "mic")) == 16000 * 30


def test_el_repaso_no_pisa_una_reunion_corregida_a_mano(entorno, monkeypatch):
    c, *_ = entorno
    monkeypatch.setattr(cola_mod, "clasificar", lambda *a: None)
    monkeypatch.setattr(c, "_local", lambda t, carpeta, tipo, **_: ([{"inicio": 0.0, "fin": 1.0, "texto": "hola contosó"}], []))
    gid, _ = _grabacion()
    c.ejecutar(db.tomar_trabajo(["transcribir"]))
    rid = db.grabacion(gid)["reunion_id"]
    sid = db.reunion(rid)["segmentos"][0]["id"]
    db.editar_segmento(sid, "hola Contoso")
    monkeypatch.setattr(c, "_local", lambda t, carpeta, tipo, **_: ([{"inicio": 0.0, "fin": 1.0, "texto": "hola contosó (turbo)"}], []))
    c.ejecutar(db.tomar_trabajo(["repasar"]))
    assert db.reunion(rid)["segmentos"][0]["texto"] == "hola Contoso"


def test_si_falla_separar_las_voces_la_reunion_sigue_archivada(entorno, monkeypatch):
    c, *_ = entorno
    gid, _ = _grabacion()
    c.ejecutar(db.tomar_trabajo(["transcribir"]))
    monkeypatch.setattr(cola_mod.local, "enchufada", lambda: True)
    monkeypatch.setattr(cola_mod.diarizacion, "en_proceso_aparte", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("modelo roto")))
    db.conexion().execute("UPDATE trabajos SET intentos = 4 WHERE tipo = 'diarizar'")
    c.ejecutar(db.tomar_trabajo(["diarizar"]))
    assert [t["estado"] for t in db.trabajos() if t["tipo"] == "diarizar"] == ["error"]
    assert db.grabacion(gid)["estado"] == "archivada"  # no aparece «Reintentar», que re-transcribiría


def test_una_manual_con_carpeta_queda_identificada_con_su_titulo(entorno, monkeypatch):
    c, llamados, *_ = entorno
    monkeypatch.setattr(cola_mod, "clasificar", lambda *a: None)  # la presencial no estaba agendada
    gid, _ = _grabacion()
    db.actualizar_grabacion(gid, apps=["manual"], titulo_manual="Charla con Pedro", carpeta_manual="Globex")
    c.ejecutar(db.tomar_trabajo(["transcribir"]))
    g = db.grabacion(gid)
    r = db.reunion(g["reunion_id"])
    assert g["estado"] == "archivada" and r["titulo"] == "Charla con Pedro" and r["carpeta"] == "Globex"
    assert r["origen"] == "manual" and llamados  # Globex permite la nube


def test_una_manual_sin_carpeta_va_a_revisar_con_su_titulo(entorno, monkeypatch):
    c, llamados, *_ = entorno
    monkeypatch.setattr(cola_mod, "clasificar", lambda *a: None)
    monkeypatch.setattr(c, "_local", lambda t, carpeta, tipo, **_: ([{"inicio": 0.0, "fin": 1.0, "texto": "hola"}], []))
    gid, _ = _grabacion()
    db.actualizar_grabacion(gid, apps=["manual"], titulo_manual="Charla con Pedro")
    c.ejecutar(db.tomar_trabajo(["transcribir"]))
    g = db.grabacion(gid)
    assert g["estado"] == "revisar" and llamados == []
    assert db.reunion(g["reunion_id"])["titulo"] == "Charla con Pedro"


def test_en_una_manual_agendada_el_calendario_pone_la_gente_y_el_usuario_la_carpeta(monkeypatch):
    monkeypatch.setattr(cola_mod, "clasificar", lambda *a: {
        "titulo": "Panel", "participantes": [{"mail": "pedro@globex.example"}], "folders": ["Orbit"], "origen": "calendario x"})
    from datetime import datetime
    c = cola_mod.clasificar_grabacion({"apps": ["manual"], "carpeta_manual": "Globex"}, datetime.now(), datetime.now())
    assert c["titulo"] == "Panel" and c["folders"] == ["Globex"] and c["participantes"]


def test_el_reproceso_respeta_la_carpeta_que_asigno_el_usuario(entorno, monkeypatch):
    """El calendario de hoy encuentra otro evento: igual manda lo que quedó guardado (aquí, una carpeta local)."""
    c, llamados, *_ = entorno
    monkeypatch.setattr(c, "_local", lambda t, carpeta, tipo, **_: ([{"inicio": 0.0, "fin": 1.0, "texto": "hola"}], []))
    gid, _ = _grabacion()
    c.ejecutar(db.tomar_trabajo(["transcribir"]))
    g = db.grabacion(gid)
    db.editar_reunion(g["reunion_id"], carpeta="Facultad", titulo="Clase")
    db.guardar_ajuste("privacidad", dict(db.ajuste("privacidad"), Facultad=["local"]))
    llamados.clear()
    db.encolar("transcribir", gid)
    c.ejecutar(db.tomar_trabajo(["transcribir"]))
    r = db.reunion(g["reunion_id"])
    assert llamados == [] and r["carpeta"] == "Facultad" and r["titulo"] == "Clase"


def test_en_una_manual_las_ventanas_abiertas_no_la_identifican(monkeypatch):
    from datetime import datetime
    vistos = []
    monkeypatch.setattr(cola_mod, "clasificar", lambda i, f, titulos, apps: vistos.append(titulos))
    cola_mod.clasificar_grabacion({"apps": ["manual"], "titulos_ventana": {"Meet - abc - Google Chrome": 9}},
                                  datetime.now(), datetime.now())
    assert vistos == [{}]


def test_con_otro_microfono_no_se_filtra_lo_ajeno(tmp_path):
    import json as js
    (tmp_path / "audio.json").write_text(js.dumps({"mic": "Auriculares USB", "cambios_mic": []}), encoding="utf-8")
    assert cola_mod.voz_del_mic({"apps": ["chrome.exe"]}, tmp_path) is None
    (tmp_path / "audio.json").write_text(js.dumps({"mic": "Micrófono (Realtek(R) Audio)", "cambios_mic": ["x"]}),
                                         encoding="utf-8")
    assert cola_mod.voz_del_mic({"apps": ["chrome.exe"]}, tmp_path) is None
    assert cola_mod.voz_del_mic({"apps": ["manual"]}, tmp_path) is None
