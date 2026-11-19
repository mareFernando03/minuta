"""Quién habla dentro de «Ellos»: reparto de turnos por voz y huellas entre reuniones (sin modelos: huellas inventadas)."""
import numpy as np

from minuta import diarizacion
from minuta.datos import db


def huella(*componentes):
    v = np.zeros(192, dtype=np.float32)
    for i, x in enumerate(componentes):
        v[i] = x
    return v / np.linalg.norm(v)


PEDRO, JUAN = huella(1, 0.1), huella(0.1, 1)


def _reunion(rid, segmentos):
    db.guardar_reunion({"id": rid, "fuente": "minuta", "titulo": rid, "inicio": "2026-10-12T09:00:00"}, segmentos)


def test_repartir_parte_un_turno_donde_cambia_la_voz():
    palabras = [["Hola", 0.0, 0.4, 0.9], ["a", 0.4, 0.5, 0.9], ["todos", 0.5, 0.9, 0.9],
                ["Yo", 3.0, 3.2, 0.9], ["arranco", 3.2, 3.6, 0.9], ["hoy", 3.6, 3.9, 0.9]]
    turnos = [{"canal": "sistema", "inicio": 0.0, "fin": 3.9, "texto": "Hola a todos Yo arranco hoy", "palabras": palabras},
              {"canal": "mic", "inicio": 5.0, "fin": 6.0, "texto": "Dale"}]
    tramos = [(0.0, 1.0, 0), (2.9, 4.0, 1)]
    r = diarizacion.repartir(turnos, tramos)
    assert [(s["texto"], s["voz"]) for s in r] == [("Hola a todos", "V1"), ("Yo arranco hoy", "V2"), ("Dale", None)]


def test_un_pedacito_suelto_no_es_otra_voz():
    palabras = [[w, i, i + 0.4, 0.9] for i, w in enumerate("uno dos tres cuatro cinco seis".split())]
    tramos = [(0, 3, 0), (3, 3.5, 1), (3.5, 6, 0)]  # «cuatro» cae en otra voz por error del modelo
    [s] = diarizacion.repartir([{"canal": "sistema", "inicio": 0, "fin": 6, "texto": "x", "palabras": palabras}], tramos)
    assert s["voz"] == "V1" and s["texto"] == "uno dos tres cuatro cinco seis"


def test_sugiere_y_nombrar_propaga_a_otras_reuniones():
    _reunion("r1", [{"canal": "sistema", "inicio": 0.0, "fin": 1.0, "texto": "hola", "voz": "V1"}])
    db.guardar_voces_reunion("r1", {"V1": (PEDRO, 30.0)})
    _reunion("r2", [{"canal": "sistema", "inicio": 0.0, "fin": 1.0, "texto": "chau", "voz": "V1"},
                    {"canal": "sistema", "inicio": 2.0, "fin": 3.0, "texto": "buenas", "voz": "V2"}])
    db.guardar_voces_reunion("r2", {"V1": (huella(1, 0.15), 20.0), "V2": (JUAN, 25.0)})
    assert db.nombrar_voz("r1", "V1", "Pedro Ríos")  # sin auto: en r2 solo se sugiere
    v = {x["etiqueta"]: x for x in db.reunion("r2")["voces"]}
    assert v["V1"]["nombre"] is None and v["V1"]["sugerida"]["nombre"] == "Pedro Ríos" and v["V2"]["sugerida"] is None
    db.rechazar_sugerencia("r2", "V1")
    db.emparejar("r2")
    assert db.reunion("r2")["voces"][0]["sugerida"] is None  # rechazada: no vuelve a sugerirla


def test_con_auto_asigna_solo_si_es_inequivoca():
    db.guardar_voces_reunion("r0", {"V1": (PEDRO, 30.0)})
    db.nombrar_voz("r0", "V1", "Pedro")
    db.guardar_voces_reunion("r3", {"V1": (huella(1, 0.12), 20.0), "V2": (huella(1, 0.2), 20.0)})
    db.emparejar("r3", auto=True)
    v = {x["etiqueta"]: x for x in db.voces_de_reunion("r3")}
    # Las dos se parecen a Pedro: solo la más parecida puede serlo (emparejamiento mutuo), la otra queda sin nombre.
    assert v["V1"]["nombre"] == "Pedro" and v["V1"]["origen"] == "auto" and v["V2"]["nombre"] is None


def test_un_nombre_puesto_a_mano_sobrevive_a_rediarizar():
    db.guardar_voces_reunion("r4", {"V1": (JUAN, 30.0), "V2": (PEDRO, 20.0)})
    db.nombrar_voz("r4", "V1", "Juan")
    db.guardar_voces_reunion("r4", {"V1": (PEDRO, 20.0), "V2": (huella(0.12, 1), 30.0)})  # la numeración se invirtió
    v = {x["etiqueta"]: x for x in db.voces_de_reunion("r4")}
    assert v["V2"]["nombre"] == "Juan" and v["V2"]["origen"] == "manual" and v["V1"]["nombre"] is None


def test_cambiar_voz_y_nombres_de_los_turnos():
    _reunion("r5", [{"canal": "mic", "inicio": 0.0, "fin": 1.0, "texto": "a"},
                    {"canal": "sistema", "inicio": 1.0, "fin": 2.0, "texto": "b", "voz": "V1"},
                    {"canal": "sistema", "inicio": 2.0, "fin": 3.0, "texto": "c", "voz": "V1"}])
    db.guardar_voces_reunion("r5", {"V1": (PEDRO, 10.0)})
    ids = [s["id"] for s in db.reunion("r5")["segmentos"]]
    assert db.cambiar_voz("r5", [ids[2]], "V2") == 1
    db.nombrar_voz("r5", "V1", "Pedro")
    r = db.reunion("r5")
    assert db.nombres_de_turnos(r, {"mic": "Vos", "sistema": "Ellos"}) == ["Vos", "Pedro", "Ellos (V2)"]
    assert db.reunion_basica("r5")["editada_en"]  # cambiar la voz es una corrección a mano


def test_las_palabras_fuera_de_todo_tramo_van_con_sus_vecinas():
    palabras = [["mira,", 0.0, 0.3, 0.9], ["esto", 1.0, 1.3, 0.9], ["anda", 1.3, 1.6, 0.9], ["bien", 1.6, 2.0, 0.9]]
    [s] = diarizacion.repartir([{"canal": "sistema", "inicio": 0.0, "fin": 2.0, "texto": "x", "palabras": palabras}], [(0.9, 2.1, 0)])
    assert s["voz"] == "V1" and s["texto"] == "mira, esto anda bien"


def test_diarizar_no_cambia_los_ids_de_los_turnos():
    palabras = [["a", 0.0, 0.4, 0.9], ["b", 0.4, 0.8, 0.9], ["c", 0.8, 1.0, 0.9],
                ["d", 3.0, 3.2, 0.9], ["e", 3.2, 3.6, 0.9], ["f", 3.6, 3.9, 0.9]]
    _reunion("r6", [{"canal": "sistema", "inicio": 0.0, "fin": 3.9, "texto": "a b c d e f", "palabras": palabras},
                    {"canal": "sistema", "inicio": 5.0, "fin": 6.0, "texto": "turno dos corregido a mano"}])
    antes = {s["texto"]: s["id"] for s in db.reunion("r6")["segmentos"]}
    db.aplicar_voces("r6", diarizacion.repartir(db.reunion("r6")["segmentos"], [(0, 1, 0), (2.9, 6.5, 1)]))
    despues = [(s["id"], s["texto"], s["voz"]) for s in db.reunion("r6")["segmentos"]]
    assert despues[0] == (antes["a b c d e f"], "a b c", "V1")  # el primer pedazo conserva el id
    assert despues[1][1:] == ("d e f", "V2") and despues[1][0] not in antes.values()
    assert despues[2] == (antes["turno dos corregido a mano"], "turno dos corregido a mano", "V2")


def test_un_turno_borrado_mientras_se_diarizaba_no_vuelve():
    _reunion("r7", [{"canal": "sistema", "inicio": 0.0, "fin": 1.0, "texto": "uno"},
                    {"canal": "sistema", "inicio": 2.0, "fin": 3.0, "texto": "dos"}])
    r = db.reunion("r7")
    db.borrar_segmentos("r7", [r["segmentos"][1]["id"]])
    db.aplicar_voces("r7", diarizacion.repartir(r["segmentos"], [(0, 3, 0)]))
    assert [s["texto"] for s in db.reunion("r7")["segmentos"]] == ["uno"]


def test_voces_sin_huella_tienen_ficha_y_no_rompen_rediarizar():
    db.guardar_voces_reunion("r8", {"V1": (PEDRO, 30.0), "V2": (None, 2.0)})
    db.nombrar_voz("r8", "V2", "Vale")  # voz corta, sin huella: Vale existe pero no se puede reconocer
    db.guardar_voces_reunion("r8", {"V1": (PEDRO, 30.0), "V2": (None, 2.0)})  # antes: TypeError
    assert {v["etiqueta"] for v in db.voces_de_reunion("r8")} == {"V1", "V2"}
    db.emparejar("r8")  # Vale no tiene huella (n = 0): no se sugiere a nadie
    assert all(v["sugerida"] is None for v in db.voces_de_reunion("r8"))


def test_quitar_un_nombre_no_vuelve_solo_y_sale_de_la_huella():
    db.guardar_voces_reunion("r9", {"V1": (PEDRO, 30.0)})
    db.nombrar_voz("r9", "V1", "Pedro")
    db.guardar_voces_reunion("r10", {"V1": (huella(1, 0.12), 30.0)})
    db.emparejar("r10", auto=True)
    assert db.voces_de_reunion("r10")[0]["nombre"] == "Pedro"
    db.quitar_nombre_voz("r10", "V1")
    db.guardar_voces_reunion("r11", {"V1": (huella(1, 0.11), 30.0)})
    db.nombrar_voz("r11", "V1", "pedro")  # otra capitalización: misma persona, no se suma dos veces
    assert db.voces_de_reunion("r10")[0]["nombre"] is None
    n = db.conexion().execute("SELECT n FROM voces WHERE nombre = 'Pedro'").fetchone()["n"]
    assert n == 2
    db.quitar_nombre_voz("r11", "V1")
    assert db.conexion().execute("SELECT n FROM voces WHERE nombre = 'Pedro'").fetchone()["n"] == 1


def test_los_no_es_x_sobreviven_a_rediarizar():
    db.guardar_voces_reunion("r12", {"V1": (PEDRO, 30.0)})
    db.nombrar_voz("r12", "V1", "Pedro")
    db.guardar_voces_reunion("r13", {"V1": (huella(1, 0.3), 30.0)})
    db.emparejar("r13")
    assert db.voces_de_reunion("r13")[0]["sugerida"]["nombre"] == "Pedro"
    db.rechazar_sugerencia("r13", "V1")
    db.guardar_voces_reunion("r13", {"V1": (huella(1, 0.31), 30.0)})
    db.emparejar("r13")
    assert db.voces_de_reunion("r13")[0]["sugerida"] is None


def test_una_sugerencia_vencida_se_borra():
    db.guardar_voces_reunion("r14", {"V1": (PEDRO, 30.0)})
    db.nombrar_voz("r14", "V1", "Pedro")
    db.guardar_voces_reunion("r15", {"V1": (huella(1, 0.3), 30.0), "V2": (huella(1, 0.6), 30.0)})
    db.emparejar("r15")
    assert db.voces_de_reunion("r15")[0]["sugerida"]["nombre"] == "Pedro"
    db.nombrar_voz("r15", "V2", "Pedro")  # Pedro ya es V2: a V1 no se la sigue proponiendo
    assert db.voces_de_reunion("r15")[0]["sugerida"] is None


def test_borrar_una_reunion_borra_sus_voces_y_olvidar_una_persona():
    _reunion("r16", [{"canal": "sistema", "inicio": 0.0, "fin": 1.0, "texto": "hola", "voz": "V1"}])
    db.guardar_voces_reunion("r16", {"V1": (JUAN, 30.0)})
    pid = db.nombrar_voz("r16", "V1", "Juan")
    db.borrar_reunion("r16")
    assert not db.conexion().execute("SELECT 1 FROM voces_reunion WHERE reunion_id = 'r16'").fetchone()
    assert db.conexion().execute("SELECT n FROM voces WHERE id = ?", (pid,)).fetchone()["n"] == 0
    _reunion("r17", [{"canal": "sistema", "inicio": 0.0, "fin": 1.0, "texto": "hola", "voz": "V1"}])
    db.guardar_voces_reunion("r17", {"V1": (JUAN, 30.0)})
    pid = db.nombrar_voz("r17", "V1", "Juan")
    assert db.olvidar_persona(pid)
    assert db.voces_de_reunion("r17")[0]["nombre"] is None
    assert not db.olvidar_persona(pid)


def test_diarizar_en_proceso_aparte_ida_y_vuelta(tmp_path, monkeypatch):
    # El hijo lee la pista, diariza y devuelve por JSON. Medio segundo no alcanza para voces: no baja los modelos.
    import wave
    from minuta import config
    monkeypatch.setattr(config, "DATOS", tmp_path / "datos")
    with wave.open(str(tmp_path / "sistema-000.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes(np.zeros(8000, dtype=np.int16).tobytes())
    assert diarizacion.en_proceso_aparte(tmp_path, None, 1.2) == ([], {})
    assert not (tmp_path / "datos" / "modelos").exists()


def test_si_el_proceso_falla_se_sabe_por_que(tmp_path):
    import pytest
    with pytest.raises(RuntimeError, match="código"):
        diarizacion.en_proceso_aparte(tmp_path / "no-existe", "x", 1.2)
