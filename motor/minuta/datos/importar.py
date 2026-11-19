"""Importa lo que existía antes de Minuta, para que la biblioteca arranque con toda la historia.

- `importar_granola(carpeta)`: el respaldo de Granola (`Documents\\granola-archivo`, un JSON por reunión). Las
  transcripciones vienen como turnos «Etiqueta: texto» sin tiempos; «Microphone» es el usuario, «System audio» los
  demás y «System audio (Nombre)» trae el nombre del hablante, que se conserva.
- `importar_grabador(carpeta)`: las grabaciones del grabador de scripts (`%LOCALAPPDATA%\\grabador-reuniones`),
  con sus segmentos con tiempos. Se copian a la carpeta de Minuta para no depender de la vieja.

Las dos son idempotentes: volver a correrlas actualiza, no duplica.
"""
import html
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from minuta import config
from minuta.datos import db
from minuta.transcripcion.local import fusionar_turnos

TURNO = re.compile(r"^(Microphone|System audio(?: \(([^)\n]*)\)?)?|Speaker [A-Z]|Me|Them):\s?(.*)$")


def _participantes(meeting_raw):
    m = re.search(r"<known_participants>(.*?)</known_participants>", meeting_raw or "", re.S)
    if not m:
        return []
    gente = []
    for parte in html.unescape(m.group(1)).split(">,"):
        mail = re.search(r"<([^<>@\s]+@[^<>\s]+)", parte + ">")
        nombre = re.sub(r"\s*<.*$", "", parte.strip()).replace(" (note creator)", "")
        nombre = re.sub(r"\s+from\s+\S+$", "", nombre).strip()
        if mail or nombre:
            gente.append({"mail": mail.group(1) if mail else "", "nombre": nombre or (mail.group(1) if mail else "")})
    return gente


def _resumen(meeting_raw):
    m = re.search(r"<summary>(.*?)</summary>", meeting_raw or "", re.S)
    return m.group(1).strip() if m and m.group(1).strip() else None


def _turnos_granola(transcript):
    turnos = []
    for bloque in (transcript or "").split("\n\n"):
        m = TURNO.match(bloque.strip())
        if not m:
            if turnos and bloque.strip():
                turnos[-1]["texto"] += " " + bloque.strip()
            continue
        etiqueta, nombre, texto = m.group(1), m.group(2), m.group(3).strip()
        if etiqueta in ("Microphone", "Me"):
            canal = "mic"
        elif etiqueta.startswith("System audio") or etiqueta == "Them":
            canal = nombre.strip() if nombre and nombre.strip() and not nombre.lower().startswith("room") else "sistema"
        else:
            canal = etiqueta
        if not texto:
            continue
        if turnos and turnos[-1]["canal"] == canal:
            turnos[-1]["texto"] += " " + texto
        else:
            turnos.append({"canal": canal, "texto": texto})
    return turnos


def _fecha(date_granola):
    return datetime.strptime(date_granola.rsplit(" GMT", 1)[0], "%b %d, %Y %I:%M %p").isoformat(timespec="seconds")


def importar_granola(carpeta):
    carpeta = Path(carpeta)
    hechas = 0
    for ruta in sorted(carpeta.glob("*.json")):
        if ruta.name == "indice.json":
            continue
        d = json.loads(ruta.read_text(encoding="utf-8"))
        if not d.get("transcript"):
            continue
        db.guardar_reunion({"id": d["id"], "fuente": "granola" if not d["id"].startswith("local-") else "minuta",
                            "titulo": d.get("title") or "Sin título", "carpeta": (d.get("folders") or [None])[0],
                            "inicio": _fecha(d["date"]), "origen": "granola", "motor": "granola",
                            "participantes": _participantes(d.get("meeting_raw")), "resumen": _resumen(d.get("meeting_raw"))},
                           _turnos_granola(d["transcript"]))
        hechas += 1
    return hechas


def importar_grabador(carpeta_viejo):
    """Grabaciones del grabador de scripts con `meta.json`. Las archivadas pasan como reuniones con sus segmentos."""
    hechas = 0
    for meta_ruta in sorted(Path(carpeta_viejo).glob("*/meta.json")):
        m = json.loads(meta_ruta.read_text(encoding="utf-8"))
        if "inicio" not in m or not m.get("archivo_id"):
            continue
        gid = meta_ruta.parent.name
        destino = config.GRABACIONES / gid
        if not destino.exists():
            shutil.copytree(meta_ruta.parent, destino)
        pasada = 2 if any(destino.glob("seg-2-*.json")) else 1
        cargar = lambda p: [s for f in sorted(destino.glob(f"seg-{pasada}-{p}-*.json"))
                            for s in json.loads(f.read_text(encoding="utf-8"))]
        turnos = fusionar_turnos(cargar("mic"), cargar("sistema"), m.get("corte_s"))
        if db.grabacion(gid) is None:
            db.crear_grabacion(gid, destino, m["inicio"], m.get("apps") or [])
        db.actualizar_grabacion(gid, estado="archivada", fin=m.get("fin"), duracion_s=m.get("duracion_s"),
                                corte_s=m.get("corte_s"), titulos_ventana=m.get("titulos_ventana") or {},
                                reunion_id=m["archivo_id"])
        db.guardar_reunion({"id": m["archivo_id"], "fuente": "minuta", "grabacion_id": gid, "titulo": m.get("titulo") or gid,
                            "carpeta": (m.get("folders") or [None])[0], "inicio": m["inicio"], "fin": m.get("fin"),
                            "origen": m.get("origen"), "participantes": m.get("participantes") or [],
                            "motor": m.get("modelo"), "correcciones": m.get("correcciones_2") or m.get("correcciones_1") or []},
                           turnos)
        hechas += 1
    return hechas
