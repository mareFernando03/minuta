"""Palabras por turno: siempre 1 a 1 con el texto, aunque el texto cambie después de transcribir."""
import json
from types import SimpleNamespace as P

from minuta.correccion import glosario
from minuta.datos import db
from minuta.transcripcion import local, nube
from minuta.transcripcion.palabras import alinear


def test_alinear_conserva_tiempos_y_reparte_lo_cambiado():
    crudas = [["no", 1.0, 1.2, 0.9], ["cambiaste", 1.2, 1.7, 0.8], ["el", 1.7, 1.8, 0.95], ["upsetting", 1.8, 2.4, 0.3]]
    r = alinear("No cambiaste el appsettings.", crudas)
    assert [w[0] for w in r] == ["No", "cambiaste", "el", "appsettings."]
    assert r[0][1:] == [1.0, 1.2, 0.9]  # coincide (sin distinguir mayúsculas): conserva tiempo y probabilidad
    assert r[3] == ["appsettings.", 1.8, 2.4, None]  # corregida: su tiempo, sin probabilidad
    r = alinear("No cambiaste el app settings", crudas)
    assert r[3][1:3] == [1.8, 2.1] and r[4][1:3] == [2.1, 2.4]  # una palabra que pasa a ser dos se reparte el tiempo
    assert len(alinear("cambiaste el", crudas)) == 2  # borradas: desaparecen
    assert alinear("hola", None) is None


def test_partir_en_frases_guarda_las_palabras_con_su_probabilidad():
    seg = P(words=[P(word=" Hola", start=0.0, end=0.4, probability=0.91), P(word=" ¿qué", start=0.5, end=0.7, probability=0.4),
                   P(word=" tal?", start=0.7, end=1.0, probability=0.88)])
    [f] = local.partir_en_frases(seg, 300.0)
    assert f["texto"] == "Hola ¿qué tal?" and f["palabras"][1] == ["¿qué", 300.5, 300.7, 0.4]


def test_fusionar_turnos_une_las_palabras():
    a = {"inicio": 0.0, "fin": 1.0, "texto": "Hola", "palabras": [["Hola", 0.0, 1.0, 0.9]]}
    b = {"inicio": 1.5, "fin": 2.0, "texto": "Pedro", "palabras": [["Pedro", 1.5, 2.0, 0.8]]}
    [t] = local.fusionar_turnos([], [a, b])
    assert t["texto"] == "Hola Pedro" and [w[0] for w in t["palabras"]] == ["Hola", "Pedro"]


def test_assemblyai_lleva_la_confianza_de_cada_palabra():
    [s] = nube._frases([("Hola", 0.0, 0.4, 0.97), ("che", 0.5, 0.8, 0.41)])
    assert s["palabras"] == [["Hola", 0.0, 0.4, 0.97], ["che", 0.5, 0.8, 0.41]]


def test_groq_reparte_las_palabras_sueltas_entre_sus_segmentos():
    palabras = [{"word": "Buen", "start": 0.0, "end": 0.3}, {"word": "día", "start": 0.3, "end": 0.6},
                {"word": "Chau", "start": 5.0, "end": 5.4}]
    r = nube._palabras_de_groq({"start": 0.0, "end": 1.0, "text": " Buen día."}, palabras)
    assert r == [["Buen", 0.0, 0.3, None], ["día.", 0.3, 0.6, None]]


def test_la_correccion_del_glosario_mantiene_las_palabras_alineadas(monkeypatch):
    monkeypatch.setattr(glosario, "pedir_reemplazos", lambda texto: ([{"original": "Contosó", "correcto": "Contoso"}], None, 0.0))
    turno = {"canal": "sistema", "inicio": 0.0, "fin": 1.0, "texto": "Lo de Contosó",
             "palabras": [["Lo", 0.0, 0.2, 0.9], ["de", 0.2, 0.3, 0.9], ["Contosó", 0.3, 1.0, 0.2]]}
    [t], aplicados, _ = glosario.corregir_turnos([turno], local.como_texto)
    assert t["texto"] == "Lo de Contoso" and t["palabras"][2] == ["Contoso", 0.3, 1.0, None]


def test_la_base_guarda_y_devuelve_las_palabras_y_el_id_del_turno():
    db.guardar_reunion({"id": "r1", "fuente": "minuta", "titulo": "x", "inicio": "2026-10-10T10:00:00"},
                       [{"canal": "mic", "inicio": 0.0, "fin": 1.0, "texto": "Hola", "palabras": [["Hola", 0.0, 1.0, 0.9]]},
                        {"canal": "sistema", "inicio": 2.0, "fin": 3.0, "texto": "Viejo sin palabras"}])
    s = db.reunion("r1")["segmentos"]
    assert s[0]["palabras"] == [["Hola", 0.0, 1.0, 0.9]] and s[1]["palabras"] is None and s[0]["id"] != s[1]["id"]
    assert json.dumps(s)  # serializable para la API
