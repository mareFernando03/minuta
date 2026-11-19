from minuta.datos import db
from minuta.mcp import servidor as s


def _datos():
    db.guardar_reunion({"id": "local-1", "fuente": "minuta", "titulo": "Huddle", "carpeta": "Seguimiento Northwind",
                        "inicio": "2026-10-09T17:29:10", "resumen": "- se probó AssemblyAI"},
                       [{"canal": "sistema", "inicio": 41.0, "texto": "La base garantiza la unicidad."},
                        {"canal": "mic", "inicio": 48.0, "texto": "Tal cual."}])
    db.editar_reunion("local-1", hablantes={"sistema": "Pedro Ríos"})


def test_list_meetings_por_rango_y_carpeta():
    _datos()
    assert 'title="Huddle"' in s.list_meetings("custom", "2026-10-09", "2026-10-09")
    assert "No hay reuniones" in s.list_meetings("custom", "2026-10-10", "2026-10-11")
    assert "No hay reuniones" in s.list_meetings("custom", "2026-10-09", "2026-10-09", folder="Orbit")


def test_get_meetings_y_transcripcion_con_nombres():
    _datos()
    assert "<summary>\n- se probó AssemblyAI" in s.get_meetings(["local-1", "nada"])
    t = s.get_meeting_transcript("local-1")
    assert "Pedro Ríos [00:41]: La base garantiza" in t and "Usuario [00:48]: Tal cual." in t


def test_search_encuentra_con_minuto():
    _datos()
    assert "en 00:41: La base garantiza la [[unicidad]]" in s.search("unicidad")
    assert "Nada encontrado" in s.search("inexistente")


def test_la_transcripcion_lleva_las_marcas_en_su_lugar():
    _datos()
    db.crear_grabacion("g1", "c", "2026-10-09T17:29:10", [])
    db.actualizar_grabacion("g1", reunion_id="local-1")
    db.crear_marca("g1", 44.0, "accion", "Mandar el script")
    t = s.get_meeting_transcript("local-1")
    assert t.index("[00:41]") < t.index("★ [00:44] Acción: Mandar el script") < t.index("[00:48]")


def test_acciones_aceptadas_y_resumen_de_minuta():
    _datos()
    db.guardar_resumen("local-1", {"secciones": [{"titulo": "Decisiones", "vinetas": [{"texto": "Índice único", "citas": [41]}]}]},
                       [{"texto": "Agregar el índice", "responsable": "Pedro", "t": 41}, {"texto": "Otra", "t": 48}])
    a, b = db.acciones(reunion_id="local-1")
    db.cambiar_accion(a["id"], estado="aceptada")
    texto = s.list_action_items("custom", "2026-10-09", "2026-10-09")
    assert "- Agregar el índice · Pedro (Huddle, 2026-10-09, id local-1 en 00:41, aceptada)" in texto and "Otra" not in texto
    assert "Otra" in s.list_action_items("custom", "2026-10-09", "2026-10-09", status="todas")
    assert "## Decisiones\n- Índice único [00:41]" in s.get_meetings(["local-1"])


def test_propose_edit_solo_propone():
    _datos()
    assert "aparece en 2 turnos" not in s.propose_edit("local-1", "unicidad", "unicidad del CUIT", "falta el campo")
    [e] = db.ediciones_pendientes("local-1")
    assert e["propuesto"] == "La base garantiza la unicidad del CUIT." and e["motivo"] == "falta el campo"
    assert db.reunion("local-1")["segmentos"][0]["texto"] == "La base garantiza la unicidad."  # no cambió nada
    assert "no aparece tal cual" in s.propose_edit("local-1", "inexistente", "x")
    assert "sumá palabras de contexto" in s.propose_edit("local-1", "a", "b")


def test_recetas_propias_como_prompts(tmp_path):
    from minuta import config
    (config.DATOS / "recetas").mkdir(parents=True, exist_ok=True)
    (config.DATOS / "recetas" / "retro.md").write_text("# Retro del sprint\nArmá la retro con las dailies.", encoding="utf-8")
    s.cargar_recetas()
    import asyncio
    nombres = {p.name: p.description for p in asyncio.run(s.mcp.list_prompts())}
    assert nombres["receta_retro"] == "Retro del sprint" and "resumen_semanal" in nombres


def test_lo_que_no_sale_de_la_pc_no_aparece_en_el_mcp():
    _datos()
    for gid, estado, solo_local, rid, carpeta in [("g1", "revisar", 0, "r-revisar", None),
                                                   ("g2", "archivada", 1, "r-local", "Orbit"),
                                                   ("g3", "archivada", 0, "r-ok", "Orbit")]:
        db.crear_grabacion(gid, "x", "2026-10-09T10:00:00", [])
        db.actualizar_grabacion(gid, estado=estado, solo_local=solo_local, reunion_id=rid)
        db.guardar_reunion({"id": rid, "fuente": "minuta", "grabacion_id": gid, "titulo": f"Charla {rid}", "carpeta": carpeta,
                            "inicio": "2026-10-09T10:00:00"}, [{"canal": "mic", "inicio": 0, "texto": "algo privado"}])
    lista = s.list_meetings("custom", "2026-10-09", "2026-10-09")
    assert "r-ok" in lista and "r-revisar" not in lista and "r-local" not in lista
    assert "No existe" in s.get_meeting_transcript("r-revisar") and "no existe" in s.get_meetings(["r-local"])
    assert s.search("privado").count("- ") == 1 and "r-ok" in s.search("privado")
    assert "No existe" in s.propose_edit("r-local", "algo", "otro")


def test_propose_edit_cuenta_apariciones():
    _datos()
    db.editar_segmento(db.reunion("local-1")["segmentos"][0]["id"], "Juan dijo el lunes y Juan confirmó el martes.")
    assert "aparece 2 veces" in s.propose_edit("local-1", "Juan", "Joan")
