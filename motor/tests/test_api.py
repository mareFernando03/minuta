import json
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from minuta import config, secretos
from minuta.api.servidor import crear_app
from minuta.datos import db

TOKEN = "t" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def cliente(tmp_path):
    db.guardar_ajuste("exportar_a", str(tmp_path / "export"))
    db.guardar_reunion({"id": "local-1", "fuente": "minuta", "titulo": "Huddle", "carpeta": "Seguimiento Northwind",
                        "inicio": "2026-10-09T17:29:10", "motor": "assemblyai"},
                       [{"canal": "sistema", "inicio": 41.0, "fin": 44.0, "texto": "La base garantiza la unicidad."},
                        {"canal": "mic", "inicio": 48.0, "fin": 55.0, "texto": "Tal cual."}])
    return TestClient(crear_app(TOKEN))


def test_sin_token_o_con_token_falso_no_entra(cliente):
    assert cliente.get("/api/reuniones").status_code == 401
    assert cliente.get("/api/reuniones", headers={"Authorization": "Bearer otro"}).status_code == 401
    assert cliente.get("/api/salud").status_code == 200  # el único abierto: lo usa el supervisor


def test_otra_pagina_del_navegador_no_puede_usarla(cliente):
    r = cliente.delete("/api/reuniones/local-1", headers={**AUTH, "Origin": "https://pagina-cualquiera.com"})
    assert r.status_code == 403 and db.reunion("local-1")
    assert cliente.get("/api/reuniones", headers={**AUTH, "Origin": "http://tauri.localhost"}).status_code == 200


def test_lista_detalle_y_busqueda(cliente):
    assert [r["id"] for r in cliente.get("/api/reuniones", headers=AUTH).json()] == ["local-1"]
    r = cliente.get("/api/reuniones/local-1", headers=AUTH).json()
    assert [s["canal"] for s in r["segmentos"]] == ["sistema", "mic"] and r["audio"] is False
    res = cliente.get("/api/buscar", params={"q": "unicidad"}, headers=AUTH).json()
    assert res[0]["segundo"] == 41.0 and "[[unicidad]]" in res[0]["fragmento"]
    # Ctrl+K busca mientras se escribe: con `prefijo`, lo empezado ya encuentra; sin él, no.
    assert cliente.get("/api/buscar", params={"q": "unici"}, headers=AUTH).json() == []
    assert cliente.get("/api/buscar", params={"q": "unici", "prefijo": "true"}, headers=AUTH).json()[0]["segundo"] == 41.0
    assert cliente.get("/api/reuniones/no-existe", headers=AUTH).status_code == 404


def test_editar_titulo_hablantes_y_rechazar_carpeta_inventada(cliente):
    r = cliente.patch("/api/reuniones/local-1", json={"titulo": "Huddle con Ríos", "hablantes": {"sistema": "Pedro Ríos"}},
                      headers=AUTH).json()
    assert r["titulo"] == "Huddle con Ríos" and r["hablantes"] == {"sistema": "Pedro Ríos"}
    assert cliente.patch("/api/reuniones/local-1", json={"carpeta": "Inventada"}, headers=AUTH).status_code == 422


def test_markdown_usa_los_nombres_de_los_hablantes(cliente):
    cliente.patch("/api/reuniones/local-1", json={"hablantes": {"sistema": "Pedro Ríos"}}, headers=AUTH)
    md = cliente.get("/api/reuniones/local-1/markdown", headers=AUTH).text
    assert md.startswith("# Huddle") and "**Pedro Ríos** (00:41): La base garantiza" in md and "**Vos** (00:48)" in md


def test_los_secretos_se_escriben_pero_nunca_se_leen(cliente):
    r = cliente.put("/api/secretos", json={"groq": "gsk_secreto", "calendarios": {"acme": "https://x/y.ics"}}, headers=AUTH)
    assert r.json() == {"assemblyai": False, "groq": True, "calendarios": ["acme"]}
    assert "gsk_secreto" not in json.dumps(cliente.get("/api/ajustes", headers=AUTH).json())
    assert "gsk_secreto" not in json.dumps(cliente.get("/api/motores", headers=AUTH).json())
    assert secretos.leer("groq") == "gsk_secreto"
    assert cliente.put("/api/secretos", json={"calendarios": {"x": "http://inseguro"}}, headers=AUTH).status_code == 422


def test_la_privacidad_de_lo_no_identificado_no_se_puede_abrir_a_la_nube(cliente):
    reglas = dict(db.ajuste("privacidad"), **{"sin identificar": ["groq"]})
    assert cliente.put("/api/ajustes", json={"privacidad": reglas}, headers=AUTH).status_code == 422
    reglas = dict(db.ajuste("privacidad"), **{"Acme": ["local"]})
    assert cliente.put("/api/ajustes", json={"privacidad": reglas}, headers=AUTH).json()["privacidad"]["Acme"] == ["local"]


def test_audio_mezcla_las_pistas_y_valida_la_ruta(cliente):
    gid = "20261009-172910"
    carpeta = config.GRABACIONES / gid
    carpeta.mkdir(parents=True)
    for p in ("mic", "sistema"):
        with wave.open(str(carpeta / f"{p}-000.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes((np.ones(1600) * 1000).astype("<i2").tobytes())
    db.crear_grabacion(gid, carpeta, "2026-10-09T17:29:10", [])
    r = cliente.get(f"/api/grabaciones/{gid}/audio", params={"t": TOKEN})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/ogg" and r.content[:4] == b"OggS"
    assert cliente.get("/api/grabaciones/..%2F..%2Fx/audio", params={"t": TOKEN}).status_code == 404


def test_reprocesar_valida_el_motor(cliente):
    gid = "20261009-180200"
    carpeta = config.GRABACIONES / gid
    carpeta.mkdir(parents=True)
    (carpeta / "mic-000.wav").write_bytes(b"x")
    db.crear_grabacion(gid, carpeta, "2026-10-09T18:02:00", [])
    db.actualizar_grabacion(gid, estado="archivada")
    assert cliente.post(f"/api/grabaciones/{gid}/reprocesar", json={"motor": "openai"}, headers=AUTH).status_code == 422
    assert cliente.post(f"/api/grabaciones/{gid}/reprocesar", json={"motor": "groq"}, headers=AUTH).status_code == 200
    assert db.trabajos()[0]["parametros"] == '{"motor": "groq"}'


def test_reprocesar_no_saca_a_la_nube_lo_que_no_sale(cliente):
    gid = "20261009-180300"
    carpeta = config.GRABACIONES / gid
    carpeta.mkdir(parents=True)
    (carpeta / "mic-000.wav").write_bytes(b"x")
    db.crear_grabacion(gid, carpeta, "2026-10-09T18:03:00", [])
    db.actualizar_grabacion(gid, estado="revisar")  # sin identificar
    assert cliente.post(f"/api/grabaciones/{gid}/reprocesar", json={"motor": "groq"}, headers=AUTH).status_code == 409
    assert cliente.post(f"/api/grabaciones/{gid}/reprocesar", json={"motor": "local"}, headers=AUTH).status_code == 200
    db.actualizar_grabacion(gid, estado="archivada", solo_local=1)
    assert cliente.post(f"/api/grabaciones/{gid}/reprocesar", json={"motor": "assemblyai"}, headers=AUTH).status_code == 409


def test_glosario_cuenta_tokens_y_se_guarda(cliente):
    g = cliente.put("/api/glosario", json={"prompt": "Reunión con Lucía Benítez."}, headers=AUTH).json()
    assert g["prompt"] == "Reunión con Lucía Benítez." and g["limite_tokens"] == 223
    assert g["tokens_prompt"] is None or 3 < g["tokens_prompt"] < 20


def test_eventos_rechazan_sin_token(cliente):
    with pytest.raises(Exception):
        with cliente.websocket_connect("/api/eventos") as ws:
            ws.receive_text()


def test_cors_solo_para_la_app(cliente):
    ok = cliente.options("/api/reuniones", headers={"Origin": "http://tauri.localhost", "Access-Control-Request-Method": "GET",
                                                   "Access-Control-Request-Headers": "authorization"})
    assert ok.headers.get("access-control-allow-origin") == "http://tauri.localhost"
    malo = cliente.options("/api/reuniones", headers={"Origin": "https://malo.com", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in malo.headers


def test_calendarios_se_agregan_y_quitan_de_a_uno(cliente):
    cliente.put("/api/secretos", json={"calendarios": {"acme": "https://a/1.ics"}}, headers=AUTH)
    r = cliente.put("/api/secretos", json={"calendarios": {"northwind": "https://b/2.ics"}}, headers=AUTH).json()
    assert r["calendarios"] == ["acme", "northwind"]
    r = cliente.put("/api/secretos", json={"calendarios": {"acme": None}}, headers=AUTH).json()
    assert r["calendarios"] == ["northwind"]


def test_apagar_llama_al_supervisor_y_exige_token():
    llamados = []
    from minuta.api.servidor import crear_app
    from fastapi.testclient import TestClient
    c = TestClient(crear_app("x" * 32, apagar=lambda: llamados.append(1)))
    assert c.post("/api/apagar").status_code == 401
    assert c.post("/api/apagar", headers={"Authorization": "Bearer " + "x" * 32}).json() == {"apagando": True}
    assert llamados == [1]


def test_solo_local_para_la_reunion_en_curso():
    class VigiaFalso:
        def __init__(self):
            self.pedidos = []

        def solo_local_actual(self, valor):
            self.pedidos.append(valor)
            return "20261009-172910"

    v = VigiaFalso()
    c = TestClient(crear_app(TOKEN, vigia=v))
    assert c.post("/api/grabacion-actual/solo-local", headers=AUTH, json={}).json() == {"id": "20261009-172910", "solo_local": True}
    assert c.post("/api/grabacion-actual/solo-local", headers=AUTH, json={"solo_local": False}).status_code == 200
    assert v.pedidos == [True, False]
    assert TestClient(crear_app(TOKEN)).post("/api/grabacion-actual/solo-local", headers=AUTH, json={}).status_code == 409


def test_cortar_y_seguir_la_grabacion_en_curso():
    class VigiaFalso:
        def cortar_actual(self):
            return "g1"

        def seguir_actual(self):
            return "g1"

    c = TestClient(crear_app(TOKEN, vigia=VigiaFalso()))
    assert c.post("/api/grabacion-actual/cortar", headers=AUTH).json() == {"cortada": "g1"}
    assert c.post("/api/grabacion-actual/seguir", headers=AUTH).json() == {"sigue": "g1"}
    sin = TestClient(crear_app(TOKEN))
    assert sin.post("/api/grabacion-actual/cortar", headers=AUTH).status_code == 409
    assert sin.post("/api/grabacion-actual/cortar").status_code == 401


def test_marcas_de_la_grabacion_pasan_a_la_reunion_y_salen_en_el_markdown(cliente):
    db.crear_grabacion("g1", config.GRABACIONES / "g1", "2026-10-09T17:29:10", ["slack.exe"])
    db.actualizar_grabacion("g1", reunion_id="local-1")
    m = db.crear_marca("g1", 45.3, "decision", "Se usa la base para la unicidad")
    assert m["reunion_id"] == "local-1"  # la grabación ya tenía reunión: entra directo
    md = cliente.get("/api/reuniones/local-1/markdown", headers=AUTH).text
    assert md.index("La base garantiza") < md.index("★ [00:45] Decisión: Se usa la base") < md.index("Tal cual")
    r = cliente.patch(f"/api/marcas/{m['id']}", headers=AUTH, json={"etiqueta": "accion", "nota": "Revisar índice"})
    assert r.json()["etiqueta"] == "accion"
    assert cliente.patch(f"/api/marcas/{m['id']}", headers=AUTH, json={"etiqueta": "otra"}).status_code == 422
    assert cliente.get("/api/reuniones/local-1", headers=AUTH).json()["marcas"][0]["nota"] == "Revisar índice"
    assert cliente.delete(f"/api/marcas/{m['id']}", headers=AUTH).json() == {"borrada": m["id"]}
    assert db.marcas(reunion_id="local-1") == []


def test_marcar_sin_grabacion_en_curso_es_409(cliente):
    assert cliente.post("/api/grabacion-actual/marca", headers=AUTH, json={}).status_code == 409


def test_estado_muestra_avance_y_cuanto_falta_y_los_errores(cliente):
    from datetime import datetime, timedelta
    db.crear_grabacion("g2", config.GRABACIONES / "g2", "2026-10-10T10:00:00", ["chrome.exe"])
    db.actualizar_grabacion("g2", estado="grabada", titulos_ventana={"Meet - Daily Orbit - Google Chrome": 4})
    tid = db.encolar("transcribir", "g2")
    db.tomar_trabajo(["transcribir"])
    hace = (datetime.now() - timedelta(seconds=60)).isoformat(timespec="seconds")
    db.conexion().execute("UPDATE trabajos SET empezado = ? WHERE id = ?", (hace, tid))
    db.progreso_trabajo(tid, "transcribiendo en esta PC, parte 1 de 4", 0.05)  # línea de base de esta corrida
    assert cliente.get("/api/estado", headers=AUTH).json()["cola"][0]["restante_s"] is None  # todavía no hay velocidad
    db.progreso_trabajo(tid, "transcribiendo en esta PC, parte 2 de 4", 0.25)
    db.crear_grabacion("g3", config.GRABACIONES / "g3", "2026-10-10T11:00:00", ["slack.exe"])
    db.actualizar_grabacion("g3", estado="error", error="No se pudo usar el servicio de transcripción")
    e = cliente.get("/api/estado", headers=AUTH).json()
    t = e["cola"][0]
    assert t["avance"] == 0.25 and 215 <= t["restante_s"] <= 235  # 20 % en 60 s: el 75 % que falta, ~225 s
    assert t["ventanas"] == ["Meet - Daily Orbit - Google Chrome"] and t["inicio"] == "2026-10-10T10:00:00"
    assert e["errores"] == [{"inicio": "2026-10-10T11:00:00", "titulo": None, "ventanas": [], "apps": ["slack.exe"],
                             "grabacion_id": "g3", "error": "No se pudo usar el servicio de transcripción"}]


def test_una_corrida_retomada_no_cuenta_lo_ya_hecho_para_estimar(cliente):
    from datetime import datetime, timedelta
    db.crear_grabacion("g4", config.GRABACIONES / "g4", "2026-10-10T10:00:00", ["chrome.exe"])
    tid = db.encolar("transcribir", "g4")
    db.tomar_trabajo(["transcribir"])
    db.conexion().execute("UPDATE trabajos SET empezado = ? WHERE id = ?",
                          ((datetime.now() - timedelta(seconds=60)).isoformat(timespec="seconds"), tid))
    db.progreso_trabajo(tid, "parte 5 de 8", 0.5)   # los bloques 1 a 4 ya estaban hechos
    db.progreso_trabajo(tid, "parte 6 de 8", 0.6)   # esta corrida avanzó 10 % en 60 s
    assert 230 <= cliente.get("/api/estado", headers=AUTH).json()["cola"][0]["restante_s"] <= 250  # 40 % más: ~240 s


def test_patch_de_marca_valida_tipos_y_el_contador_de_errores_cuenta_todo(cliente):
    db.crear_grabacion("g1", config.GRABACIONES / "g1", "2026-10-09T17:29:10", [])
    m = db.crear_marca("g1", 10.0)
    assert cliente.patch(f"/api/marcas/{m['id']}", headers=AUTH, json={"t": "abc"}).status_code == 422
    assert cliente.patch(f"/api/marcas/{m['id']}", headers=AUTH, json={"nota": "x" * 2001}).status_code == 422
    assert cliente.patch(f"/api/marcas/{m['id']}", headers=AUTH, json={"otra": 1}).status_code == 422
    for i in range(12):
        db.crear_grabacion(f"e{i}", config.GRABACIONES / f"e{i}", "2026-10-10T10:00:00", [])
        db.actualizar_grabacion(f"e{i}", estado="error", error="x")
    e = cliente.get("/api/estado", headers=AUTH).json()
    assert e["con_error"] == 12 and len(e["errores"]) == 10


def test_corregir_un_turno_sugiere_las_otras_y_el_glosario(cliente):
    db.guardar_reunion({"id": "r9", "fuente": "minuta", "titulo": "Daily", "inicio": "2026-10-10T09:00:00"},
                       [{"canal": "sistema", "inicio": 1.0, "fin": 3.0, "texto": "Lo de Contosó está listo",
                         "palabras": [["Lo", 1.0, 1.2, 0.9], ["de", 1.2, 1.3, 0.9], ["Contosó", 1.3, 2.0, 0.2],
                                      ["está", 2.0, 2.4, 0.9], ["listo", 2.4, 3.0, 0.9]]},
                        {"canal": "mic", "inicio": 4.0, "fin": 5.0, "texto": "Hablé con Contosó ayer"},
                        {"canal": "mic", "inicio": 6.0, "fin": 7.0, "texto": "Contosó-bis no se toca"}])
    sid = db.reunion("r9")["segmentos"][0]["id"]
    r = cliente.patch(f"/api/reuniones/r9/segmentos/{sid}", headers=AUTH, json={"texto": "Lo de Contoso está listo"}).json()
    assert r["segmento"]["palabras"][2] == ["Contoso", 1.3, 2.0, None]
    assert r["sugerencia"] == {"original": "Contosó", "correcto": "Contoso", "otras": 1}  # «Contosó-bis» no cuenta
    assert cliente.post("/api/reuniones/r9/reemplazar", headers=AUTH, json={"original": "Contosó", "correcto": "Contoso"}).json() == {"cambiados": 1}
    assert [s["texto"] for s in db.reunion("r9")["segmentos"]][1:] == ["Hablé con Contoso ayer", "Contosó-bis no se toca"]
    assert db.buscar("Contoso")  # la búsqueda se actualiza sola
    assert cliente.patch(f"/api/reuniones/otra/segmentos/{sid}", headers=AUTH, json={"texto": "x"}).status_code == 404
    assert cliente.patch(f"/api/reuniones/r9/segmentos/{sid}", headers=AUTH, json={"texto": ""}).status_code == 422


def test_agregar_una_deformacion_al_glosario(cliente):
    linea = cliente.post("/api/glosario/deformacion", headers=AUTH, json={"correcto": "Contoso", "deformacion": "Contosó"}).json()["linea"]
    assert linea.startswith("Contoso <- ") and linea.endswith(", Contosó")
    assert cliente.post("/api/glosario/deformacion", headers=AUTH, json={"correcto": "Pedro", "deformacion": "Nicko"}).json()["linea"] == "Pablo Sosa (Pedro) <- Nicko"
    # Repetirla no la duplica; un término nuevo va a su sección al final.
    cliente.post("/api/glosario/deformacion", headers=AUTH, json={"correcto": "Contoso", "deformacion": "contosu"})
    assert cliente.post("/api/glosario/deformacion", headers=AUTH, json={"correcto": "Kubernetes", "deformacion": "cubernetes"}).json()["linea"] == "Kubernetes <- cubernetes"
    terminos = (config.GLOSARIO / "glosario-terminos.txt").read_text(encoding="utf-8")
    assert terminos.count("Contosó") == 1 and "## Agregados desde Minuta\nKubernetes <- cubernetes" in terminos


def test_un_turno_no_puede_quedar_vacio(cliente):
    sid = db.reunion("local-1")["segmentos"][0]["id"]
    assert cliente.patch(f"/api/reuniones/local-1/segmentos/{sid}", headers=AUTH, json={"texto": "   "}).status_code == 422
    assert cliente.patch(f"/api/reuniones/local-1/segmentos/{sid}", headers=AUTH, json={"texto": "x" * 200_001}).status_code == 422


def test_borrar_un_tramo_saca_texto_marcas_y_audio(cliente):
    carpeta = config.GRABACIONES / "g7"
    carpeta.mkdir(parents=True)
    tono = (np.sin(np.arange(16000 * 20) / 16000 * 2 * np.pi * 440) * 8000).astype("<i2")
    for pista in ("mic", "sistema"):
        with wave.open(str(carpeta / f"{pista}-000.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(tono.tobytes())
    (carpeta / "seg-1-mic-000.json").write_text("[]", encoding="utf-8")
    db.crear_grabacion("g7", carpeta, "2026-10-10T10:00:00", [])
    db.guardar_reunion({"id": "r7", "fuente": "minuta", "titulo": "x", "inicio": "2026-10-10T10:00:00", "grabacion_id": "g7"},
                       [{"canal": "mic", "inicio": 1.0, "fin": 4.0, "texto": "Esto queda"},
                        {"canal": "sistema", "inicio": 5.0, "fin": 8.0, "texto": "Esto es privado"},
                        {"canal": "mic", "inicio": 12.0, "fin": 15.0, "texto": "Esto también queda"}])
    db.actualizar_grabacion("g7", reunion_id="r7")
    m = db.crear_marca("g7", 6.0, None, "privada")
    sid = db.reunion("r7")["segmentos"][1]["id"]
    r = cliente.post("/api/reuniones/r7/borrar-tramo", headers=AUTH, json={"segmentos": [sid]}).json()
    assert r == {"borrados": 1, "desde": 5.0, "hasta": 8.0, "audio": True}
    assert [s["texto"] for s in db.reunion("r7")["segmentos"]] == ["Esto queda", "Esto también queda"]
    assert db.marca(m["id"]) is None and not db.buscar("privado") and not list(carpeta.glob("seg-*.json"))
    for pista in ("mic", "sistema"):
        with wave.open(str(carpeta / f"{pista}-000.wav")) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), "<i2")
        assert not audio[int(5.0 * 16000):int(8.0 * 16000)].any()  # el tramo quedó en silencio
        assert audio[int(2.0 * 16000):int(4.0 * 16000)].any() and audio[int(12.0 * 16000):].any()  # el resto no
    assert cliente.post("/api/reuniones/r7/borrar-tramo", headers=AUTH, json={"segmentos": [999]}).status_code == 404


def test_el_reemplazo_es_literal_y_los_turnos_largos_se_pueden_corregir(cliente):
    db.guardar_reunion({"id": "r8", "fuente": "granola", "titulo": "x", "inicio": "2026-10-10T09:00:00"},
                       [{"canal": "Pedro", "inicio": None, "fin": None, "texto": "Contosó " * 1000 + "fin"}])
    sid = db.reunion("r8")["segmentos"][0]["id"]
    texto = "Contoso " + "Contosó " * 999 + "fin"  # más de 5000 caracteres
    assert cliente.patch(f"/api/reuniones/r8/segmentos/{sid}", headers=AUTH, json={"texto": texto}).status_code == 200
    r = cliente.post("/api/reuniones/r8/reemplazar", headers=AUTH, json={"original": "Contosó", "correcto": r"C:\1 y \g<0>"})
    assert r.status_code == 200 and db.reunion("r8")["segmentos"][0]["texto"].count(r"C:\1 y \g<0>") == 999


def test_nombrar_rechazar_y_cambiar_voces_por_la_api(cliente):
    import numpy as np
    h = np.zeros(192, dtype=np.float32); h[0] = 1
    db.guardar_reunion({"id": "rv", "fuente": "minuta", "titulo": "Daily", "inicio": "2026-10-12T09:00:00"},
                       [{"canal": "sistema", "inicio": 1.0, "fin": 2.0, "texto": "arranco yo", "voz": "V1"},
                        {"canal": "sistema", "inicio": 3.0, "fin": 4.0, "texto": "sigo yo", "voz": "V2"}])
    db.guardar_voces_reunion("rv", {"V1": (h, 30.0), "V2": (h * -1, 20.0)})
    v = cliente.post("/api/reuniones/rv/voces/V1/nombre", headers=AUTH, json={"nombre": "Pedro"}).json()["voces"]
    assert v[0]["nombre"] == "Pedro" and v[0]["origen"] == "manual"
    assert cliente.post("/api/reuniones/rv/voces/V9/nombre", headers=AUTH, json={"nombre": "x"}).status_code == 404
    md = cliente.get("/api/reuniones/rv/markdown", headers=AUTH).text
    assert "**Pedro** (00:01): arranco yo" in md and "**Ellos (V2)** (00:03): sigo yo" in md
    ids = [s["id"] for s in db.reunion("rv")["segmentos"]]
    assert cliente.post("/api/reuniones/rv/cambiar-voz", headers=AUTH, json={"segmentos": [ids[1]], "etiqueta": "V1"}).json()["cambiados"] == 1
    assert cliente.post("/api/reuniones/rv/cambiar-voz", headers=AUTH, json={"segmentos": [ids[1]], "etiqueta": "x; DROP"}).status_code == 422
    assert [p["nombre"] for p in cliente.get("/api/voces", headers=AUTH).json()] == ["Pedro"]
    assert cliente.delete("/api/reuniones/rv/voces/V1/nombre", headers=AUTH).json()["voces"][0]["nombre"] is None


def test_resumen_acciones_y_ediciones_por_la_api(cliente):
    db.guardar_resumen("local-1", {"secciones": [{"titulo": "Decisiones", "vinetas": [{"texto": "La base valida", "citas": [41]}]}]},
                       [{"texto": "Agregar el índice único", "responsable": "Vos", "t": 41}])
    md = cliente.get("/api/reuniones/local-1/markdown", headers=AUTH).text
    assert "## Decisiones\n\n- La base valida (00:41)" in md and "- [ ] Agregar el índice único · Vos" in md
    [a] = cliente.get("/api/reuniones/local-1", headers=AUTH).json()["acciones"]
    assert cliente.patch(f"/api/acciones/{a['id']}", headers=AUTH, json={"estado": "aceptada"}).json()["estado"] == "aceptada"
    assert cliente.patch(f"/api/acciones/{a['id']}", headers=AUTH, json={"estado": "hecha"}).status_code == 422
    assert [x["id"] for x in cliente.get("/api/acciones?estado=aceptada", headers=AUTH).json()] == [a["id"]]
    # Una reunión importada (sin grabación) no se resume: trae el resumen de Granola.
    assert cliente.post("/api/reuniones/local-1/resumir", headers=AUTH, json={}).status_code == 409

    sid = db.reunion("local-1")["segmentos"][0]["id"]
    eid = db.proponer_edicion(db.conexion(), "local-1", sid, "La base garantiza la unicidad del CUIT.")
    assert cliente.post(f"/api/ediciones/{eid}/aplicar", headers=AUTH).json()["segmento"]["texto"].endswith("del CUIT.")
    eid = db.proponer_edicion(db.conexion(), "local-1", sid, "Otra cosa.")
    db.editar_segmento(sid, "Cambiado a mano.")
    assert cliente.post(f"/api/ediciones/{eid}/aplicar", headers=AUTH).status_code == 409  # no se aplica a ciegas
    assert db.segmento(sid)["texto"] == "Cambiado a mano." and not db.ediciones_pendientes("local-1")


def test_plantillas_por_carpeta(cliente):
    a = cliente.put("/api/ajustes", headers=AUTH, json={"plantillas": {"Orbit": "## Decisiones\n## Próximos pasos", "Globex": "  "}}).json()
    assert a["plantillas"] == {"Orbit": "## Decisiones\n## Próximos pasos"}
    assert cliente.put("/api/ajustes", headers=AUTH, json={"plantillas": {"Orbit": "x" * 5000}}).status_code == 422


def test_identificar_una_grabacion_para_revisar_la_resume(cliente):
    db.crear_grabacion("g9", "x", "2026-10-09T10:00:00", [])
    db.actualizar_grabacion("g9", estado="revisar", reunion_id="local-1")
    r = cliente.post("/api/grabaciones/g9/asignar", headers=AUTH, json={"titulo": "Huddle", "carpeta": "Orbit"})
    assert r.status_code == 200
    assert [t["tipo"] for t in db.trabajos()] == ["resumir"]


def test_grabar_a_mano_valida_la_carpeta_y_no_toca_la_pausa():
    class VigiaFalso:
        def __init__(self):
            self.pedidos, self.grabando = [], False

        def empezar_manual(self, titulo, carpeta):
            if self.grabando:
                return None
            self.pedidos.append((titulo, carpeta))
            self.grabando = True
            return "20261013-114500"

    vg = VigiaFalso()
    c = TestClient(crear_app(TOKEN, vigia=vg))
    db.guardar_ajuste("pausado", True)
    assert c.post("/api/grabacion-actual/empezar", headers=AUTH, json={"carpeta": "Otra"}).status_code == 422
    r = c.post("/api/grabacion-actual/empezar", headers=AUTH, json={"titulo": "  Charla  ", "carpeta": "Globex"})
    assert r.json() == {"id": "20261013-114500"} and vg.pedidos == [("Charla", "Globex")]
    assert db.ajuste("pausado") is True  # graba igual; al terminar, la detección automática sigue pausada
    assert c.post("/api/grabacion-actual/empezar", headers=AUTH, json={}).status_code == 409
