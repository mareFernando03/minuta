import json

from minuta.datos import db, importar

RAW = ("\n  <known_participants>\n  Lucía Benítez (note creator) from Acme &lt;yo@acme.example&gt;, "
       "Cgomez &lt;cgomez@northwind.example&gt;\n  </known_participants>\n  \n  <summary>\n# Resumen\n\n- punto\n</summary>")


def test_importa_una_reunion_de_granola_con_hablantes_y_resumen(tmp_path):
    (tmp_path / "2026-10-02_abc.json").write_text(json.dumps({
        "id": "abc", "title": "Seguimiento", "date": "Oct 2, 2026 9:00 AM GMT-3", "folders": ["Seguimiento Northwind"],
        "meeting_raw": RAW,
        "transcript": "Microphone: Hola.\n\nSystem audio: Buenas.\n\nSystem audio (Pablo Sosa): Arrancamos.\n\nSystem audio (Pablo Sosa): Primero lo de Ventas."
    }), encoding="utf-8")
    (tmp_path / "indice.json").write_text("[]", encoding="utf-8")
    assert importar.importar_granola(tmp_path) == 1
    r = db.reunion("abc")
    assert r["inicio"] == "2026-10-02T09:00:00" and r["carpeta"] == "Seguimiento Northwind" and r["fuente"] == "granola"
    assert [s["canal"] for s in r["segmentos"]] == ["mic", "sistema", "Pablo Sosa"]
    assert r["segmentos"][2]["texto"] == "Arrancamos. Primero lo de Ventas."
    assert {p["mail"] for p in r["participantes"]} == {"yo@acme.example", "cgomez@northwind.example"}
    assert r["participantes"][0]["nombre"] == "Lucía Benítez"
    assert r["resumen"].startswith("# Resumen")
    assert importar.importar_granola(tmp_path) == 1 and len(db.reuniones()) == 1  # idempotente


def test_ignora_las_que_no_tienen_transcripcion(tmp_path):
    (tmp_path / "x.json").write_text(json.dumps({"id": "x", "title": "t", "date": "Oct 2, 2026 9:00 AM GMT-3",
                                                 "transcript": None}), encoding="utf-8")
    assert importar.importar_granola(tmp_path) == 0
