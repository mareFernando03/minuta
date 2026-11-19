"""Brief: un minuto antes de una reunión del calendario, qué quedó de la anterior de esa serie.

Se arma con datos que ya existen, sin LLM: la última reunión con el mismo título (o de la misma carpeta si el título
cambia de fecha a fecha), su resumen, las acciones que siguen abiertas y las marcas. Llegar con el contexto cargado
ataca directo los saltos de contexto del día.
"""
import json
import logging
import re
import threading
from datetime import datetime, timedelta

from minuta.clasificacion import calendarios
from minuta.datos import db

log = logging.getLogger("minuta.brief")

ANTICIPO_S = 60
REVISAR_S = 30
HORIZONTE = timedelta(minutes=15)


def _clave(titulo):
    """El título sin fechas ni números: «Daily Orbit 12/10» y «Daily Orbit 13/10» son la misma serie."""
    t = re.sub(r"\d+([/.-]\d+)*", " ", titulo.lower())
    return " ".join(re.sub(r"[^\w\s]", " ", t).split())


def anterior_de(titulo, antes_de, carpeta=None):
    """La última reunión de la misma serie, o de la misma carpeta si no hay de la serie. «Daily» existe en más de un
    frente: con carpeta conocida, la serie es título más carpeta."""
    clave, carpeta = _clave(titulo), carpeta or calendarios.carpeta_para(titulo)
    candidatas = [r for r in db.reuniones(limite=400) if r["inicio"] < antes_de]
    misma = [r for r in candidatas if _clave(r["titulo"]) == clave and (not carpeta or r.get("carpeta") in (carpeta, None))]
    if not misma and carpeta:
        misma = [r for r in candidatas if r.get("carpeta") == carpeta]
    return misma[0] if misma else None


def armar(evento):
    """{evento, anterior} con lo que sirve para entrar a la reunión."""
    inicio = evento["inicio"].isoformat(timespec="minutes") if isinstance(evento["inicio"], datetime) else evento["inicio"]
    dominios = " ".join(p["mail"] for p in evento.get("participantes") or [] if p["mail"].lower() not in calendarios.YO)
    carpeta = calendarios.carpeta_para(f"{evento['titulo']} {evento.get('calendario', '')} {dominios}")
    previa = anterior_de(evento["titulo"], inicio, carpeta)
    anterior = None
    if previa:
        r = db.reunion_basica(previa["id"])  # sin los turnos: acá no hacen falta y pesan (las palabras)
        resumen = json.loads(r["resumen_minuta"]) if r.get("resumen_minuta") else {}
        vinetas = [v["texto"] for s in resumen.get("secciones", []) for v in s["vinetas"]][:6]
        anterior = {
            "id": r["id"], "titulo": r["titulo"], "inicio": r["inicio"],
            "resumen": vinetas or ([r["resumen"][:600]] if r.get("resumen") else []),
            "acciones": [a for a in db.acciones(reunion_id=r["id"]) if a["estado"] in ("propuesta", "aceptada")],
            "marcas": db.marcas(reunion_id=r["id"]),
        }
    return {"evento": {"titulo": evento["titulo"], "inicio": inicio}, "anterior": anterior}


def proximo(ahora=None):
    """El brief de la próxima reunión del calendario en los próximos 15 minutos, o None."""
    ahora = ahora or datetime.now()
    try:
        evs = [e for e in calendarios.eventos(ahora, ahora + HORIZONTE) if e["inicio"] >= ahora]
    except Exception as e:  # sin red o calendario roto: no hay brief, no se corta nada
        log.warning("no se pudieron leer los calendarios: %r", e)
        return None
    if not evs:
        return None
    return armar(min(evs, key=lambda e: e["inicio"]))


class Agenda(threading.Thread):
    """Avisa (evento «brief») un minuto antes de cada reunión del calendario."""

    def __init__(self, evento, reloj=datetime.now):
        super().__init__(name="agenda", daemon=True)
        self.evento, self.reloj = evento, reloj
        self._parar = threading.Event()
        self.avisados = set()

    def parar(self):
        self._parar.set()

    def run(self):
        while not self._parar.wait(REVISAR_S):
            try:
                self.revisar()
            except Exception:
                log.exception("error al revisar la agenda")

    def revisar(self):
        ahora = self.reloj()
        for e in calendarios.eventos(ahora, ahora + timedelta(seconds=ANTICIPO_S + REVISAR_S)):
            faltan = (e["inicio"] - ahora).total_seconds()
            clave = (e["titulo"], e["inicio"])
            if 0 <= faltan <= ANTICIPO_S + REVISAR_S and clave not in self.avisados:
                self.avisados.add(clave)
                self.evento("brief", armar(e))
