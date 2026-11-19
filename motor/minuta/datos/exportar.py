"""Exporta una reunión con el mismo esquema que `granola-proxy/archivar.py`, y regenera `indice.json`.

Los corpus de reuniones (Northwind, GLOBEX, Orbit) leen esos JSON. El destino es el ajuste `exportar_a`: hasta que
se apague Granola (7/11) apunta a `granola-archivo-paralelo`, para no duplicar reuniones en `granola-archivo`.
"""
import json
import time
import uuid
from pathlib import Path

MESES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def fecha_granola(dt):
    """'Oct 9, 2026 3:30 PM GMT-3', el formato que `archivar.py` lee con '%b %d, %Y %I:%M %p'."""
    h = dt.hour % 12 or 12
    return f"{MESES[dt.month - 1]} {dt.day}, {dt.year} {h}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'} GMT-3"


def meeting_raw(participantes):
    def uno(p):
        return f"{p['nombre']} &lt;{p['mail']}&gt;" if p["nombre"] != p["mail"] else f"&lt;{p['mail']}&gt;"
    gente = ", ".join(uno(p) for p in participantes if p.get("estado", "").upper() != "DECLINED")
    return f"\n  <known_participants>\n  {gente}\n  </known_participants>\n  \n  <summary>\n</summary>"


def archivar(destino, inicio, titulo, folders, participantes, transcript, id_previo=None):
    """Escribe o reescribe (pasada 2) la reunión. Devuelve el id, que la pasada 2 reusa para pisar el mismo archivo."""
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    rid = id_previo or f"local-{uuid.uuid4()}"
    reg = {"id": rid, "title": titulo, "date": fecha_granola(inicio), "folders": folders, "url": "",
           "meeting_raw": meeting_raw(participantes), "transcript": transcript,
           "archivado": time.strftime("%Y-%m-%d %H:%M")}
    ruta = destino / f"{inicio:%Y-%m-%d}_{rid}.json"
    ruta.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    regenerar_indice(destino)
    return rid, ruta


def regenerar_indice(destino):
    indice = []
    destino = Path(destino)
    for ruta in sorted(destino.glob("*.json")):
        if ruta.name == "indice.json":
            continue
        r = json.loads(ruta.read_text(encoding="utf-8"))
        indice.append({"archivo": ruta.name, "title": r["title"], "date": r["date"], "folders": r["folders"],
                       "transcript_chars": len(r["transcript"] or "")})
    (destino / "indice.json").write_text(json.dumps(indice, ensure_ascii=False, indent=1), encoding="utf-8")
