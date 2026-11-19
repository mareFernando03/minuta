from minuta.datos import db


def _reunion(rid="local-1", **extra):
    return {"id": rid, "fuente": "minuta", "titulo": "Seguimiento", "carpeta": "Seguimiento Northwind",
            "inicio": "2026-10-09T09:00:00", "motor": "assemblyai", **extra}


def test_migrar_es_idempotente():
    assert db.migrar() == len(db.MIGRACIONES)
    assert db.conexion().execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRACIONES)


def test_guardar_y_leer_reunion_con_segmentos_en_orden():
    db.guardar_reunion(_reunion(participantes=[{"mail": "a@x.com", "nombre": "Ana"}]),
                       [{"canal": "sistema", "inicio": 0.5, "fin": 2.0, "texto": "Hola."},
                        {"canal": "mic", "inicio": 2.5, "fin": 4.0, "texto": "¿Todo bien?"}])
    r = db.reunion("local-1")
    assert r["participantes"][0]["nombre"] == "Ana"
    assert [s["texto"] for s in r["segmentos"]] == ["Hola.", "¿Todo bien?"]


def test_reemplazar_reunion_no_duplica_segmentos_ni_pierde_la_fecha_de_creacion():
    db.guardar_reunion(_reunion(), [{"canal": "mic", "texto": "uno"}])
    creado = db.reunion("local-1")["creado"]
    db.guardar_reunion(_reunion(motor="groq"), [{"canal": "mic", "texto": "dos"}])
    r = db.reunion("local-1")
    assert [s["texto"] for s in r["segmentos"]] == ["dos"] and r["motor"] == "groq" and r["creado"] == creado


def test_busqueda_ignora_tildes_y_marca_el_fragmento():
    db.guardar_reunion(_reunion(), [{"canal": "sistema", "inicio": 41.0, "texto": "La base garantiza la unicidad del nombre de usuario."}])
    res = db.buscar("unicidad")
    assert res and res[0]["segundo"] == 41.0 and "[[unicidad]]" in res[0]["fragmento"]
    assert db.buscar("garantizá")  # con tilde de más encuentra igual


def test_busqueda_no_rompe_con_comillas_ni_operadores():
    db.guardar_reunion(_reunion(), [{"canal": "mic", "texto": "texto normal"}])
    assert isinstance(db.buscar('"normal" OR AND NEAR('), list)
    assert db.buscar("   ") == []


def test_trabajos_se_toman_una_sola_vez_y_se_reanudan_si_quedaron_cortados():
    db.crear_grabacion("g1", "c:/x", "2026-10-09T09:00:00", ["chrome.exe"])
    db.encolar("transcribir", "g1")
    assert db.encolar("transcribir", "g1") == db.encolar("transcribir", "g1")  # no duplica
    t = db.tomar_trabajo(["transcribir"])
    assert t and t["intentos"] == 1
    assert db.tomar_trabajo(["transcribir"]) is None
    db.reanudar_trabajos_cortados()
    assert db.tomar_trabajo(["transcribir"])["intentos"] == 2


def test_ajustes_tienen_valores_por_defecto_y_se_pisan():
    assert db.ajuste("audio_dias") == 7
    db.guardar_ajuste("audio_dias", 14)
    assert db.ajuste("audio_dias") == 14


def test_uso_por_motor_suma():
    db.registrar_uso("assemblyai", 600, 0.035)
    db.registrar_uso("assemblyai", 300, 0.0175)
    u = db.uso_por_motor()["assemblyai"]
    assert u["segundos"] == 900 and round(u["costo"], 4) == 0.0525


def test_transcribir_va_antes_que_diarizar_y_repasar():
    for gid in ("g1", "g2"):
        db.crear_grabacion(gid, f"c/{gid}", "2026-10-12T09:00:00", [])
    db.encolar("diarizar", "g1")
    db.encolar("repasar", "g1")
    db.encolar("transcribir", "g2")
    assert db.tomar_trabajo(["transcribir", "repasar", "diarizar"])["tipo"] == "transcribir"
    assert db.tomar_trabajo(["transcribir", "repasar", "diarizar"])["tipo"] == "diarizar"
