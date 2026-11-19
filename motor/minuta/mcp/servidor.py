"""Servidor MCP «reuniones»: Claude Code consulta la biblioteca de Minuta.

Las herramientas imitan las del MCP de Granola (list_meetings, get_meetings, get_meeting_transcript), así lo que ya
usaba ese MCP solo cambia el nombre del servidor. Suma `search`, que Granola no tiene: búsqueda de texto completo en
lo que se dijo.

Lee la base en modo solo lectura (no bloquea al motor). Lo que el MCP devuelve va a Claude, así que no muestra las
reuniones cuyo texto no puede salir de la PC (sin identificar, «solo en esta PC» o de una carpeta local): la misma
regla que el audio a la nube (db.texto_puede_salir). La única escritura es `propose_edit`, con una conexión
aparte: deja una corrección propuesta que el usuario aplica o rechaza en la app; nunca toca la transcripción.
También expone las acciones aceptadas (`list_action_items`, para llevarlas a donde se sigan las tareas) y prompts:
cuatro fijos y las recetas propias de `DATOS/recetas/*.md`. No necesita que la app esté abierta. Se registra con:  claude mcp add reuniones -s user -- <python del motor> -m minuta.mcp
"""
import json
import sqlite3
from datetime import date, timedelta

from mcp.server.mcpserver import MCPServer  # mcp 2.x: FastMCP pasó a llamarse MCPServer
from mcp.server.mcpserver.prompts import Prompt

from minuta import config
from minuta.datos import db  # solo funciones puras (intercalar_marcas): la conexión del MCP es propia y de lectura

mcp = MCPServer("reuniones", instructions="Reuniones grabadas por Minuta y su historia importada de Granola.")
NOMBRES = {"mic": "Usuario", "sistema": "Otros"}


def _conexion():
    c = sqlite3.connect(f"file:{config.BASE_DE_DATOS}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _conexion_escritura():
    c = sqlite3.connect(f"file:{config.BASE_DE_DATOS}?mode=rw", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _ocultas(c):
    """Los ids que no salen, como JSON para `NOT IN (SELECT value FROM json_each(?))`: el filtro va en el SQL, así
    los LIMIT siguen contando bien."""
    return json.dumps(sorted(db.reuniones_que_no_salen(c)))


def _rango(time_range, custom_start, custom_end):
    hoy = date.today()
    lunes = hoy - timedelta(days=hoy.weekday())
    if time_range == "this_week":
        return lunes, hoy + timedelta(days=1)
    if time_range == "last_week":
        return lunes - timedelta(days=7), lunes
    if time_range == "custom" and custom_start:
        fin = date.fromisoformat(custom_end) + timedelta(days=1) if custom_end else hoy + timedelta(days=1)
        return date.fromisoformat(custom_start), fin
    return hoy - timedelta(days=30), hoy + timedelta(days=1)


@mcp.tool()
def list_meetings(time_range: str = "last_30_days", custom_start: str | None = None, custom_end: str | None = None,
                  folder: str | None = None) -> str:
    """Lista reuniones por fecha. time_range: this_week | last_week | last_30_days | custom (con custom_start y
    custom_end en AAAA-MM-DD). folder filtra por carpeta (Seguimiento Northwind, Contoso Fabrikam, Orbit, Globex,
    Acme, Facultad)."""
    desde, hasta = _rango(time_range, custom_start, custom_end)
    sql = ("SELECT id, titulo, carpeta, inicio, fin, motor FROM reuniones WHERE inicio >= ? AND inicio < ? "
           "AND id NOT IN (SELECT value FROM json_each(?))")
    args = [desde.isoformat(), hasta.isoformat()]
    if folder:
        sql += " AND carpeta = ?"; args.append(folder)
    with _conexion() as c:
        args.insert(2, _ocultas(c))
        filas = c.execute(sql + " ORDER BY inicio", args).fetchall()
    if not filas:
        return f"No hay reuniones entre {desde} y {hasta - timedelta(days=1)}."
    return "\n".join(f'<meeting id="{f["id"]}" title="{f["titulo"]}" date="{f["inicio"][:16].replace("T", " ")}" '
                     f'folder="{f["carpeta"] or ""}" />' for f in filas)


@mcp.tool()
def get_meetings(meeting_ids: list[str]) -> str:
    """Detalle de hasta 10 reuniones: título, fecha, carpeta, participantes y resumen (si lo hay)."""
    salida = []
    with _conexion() as c:
        ocultas = db.reuniones_que_no_salen(c)
        for rid in meeting_ids[:10]:
            r = None if rid in ocultas else c.execute("SELECT * FROM reuniones WHERE id = ?", (rid,)).fetchone()
            if not r:
                salida.append(f'<meeting id="{rid}">no existe</meeting>')
                continue
            gente = ", ".join(p.get("nombre") or p.get("mail") for p in json.loads(r["participantes"] or "[]"))
            resumen = _resumen_minuta(r["resumen_minuta"]) or r["resumen"] or "(sin resumen: pedí la transcripción)"
            salida.append(f'<meeting id="{r["id"]}" title="{r["titulo"]}" date="{r["inicio"][:16].replace("T", " ")}" '
                          f'folder="{r["carpeta"] or ""}">\n<participants>{gente}</participants>\n'
                          f'<summary>\n{resumen}\n</summary>\n</meeting>')
    return "\n\n".join(salida)


@mcp.tool()
def get_meeting_transcript(meeting_id: str) -> str:
    """Transcripción completa de una reunión, turno por turno, con quién habla y el minuto."""
    with _conexion() as c:
        r = None if meeting_id in db.reuniones_que_no_salen(c) else c.execute(
            "SELECT titulo, inicio, hablantes FROM reuniones WHERE id = ?", (meeting_id,)).fetchone()
        if not r:
            return f"No existe la reunión {meeting_id}."
        nombres = {**NOMBRES, **json.loads(r["hablantes"] or "{}")}
        segs = [dict(s) for s in c.execute("SELECT canal, inicio, texto, voz FROM segmentos WHERE reunion_id = ? ORDER BY orden",
                                           (meeting_id,))]
        marcas = [dict(m) for m in c.execute("SELECT t, etiqueta, nota FROM marcas WHERE reunion_id = ?", (meeting_id,))]
        voces = [{"etiqueta": v["etiqueta"], "nombre": v["nombre"]} for v in c.execute(
            "SELECT vr.etiqueta, v.nombre FROM voces_reunion vr LEFT JOIN voces v ON v.id = vr.voz_id WHERE vr.reunion_id = ?",
            (meeting_id,))]
    turnos = []
    for s, quien in zip(segs, db.nombres_de_turnos({"segmentos": segs, "voces": voces}, nombres)):
        minuto = f" [{int(s['inicio'] // 60):02d}:{int(s['inicio'] % 60):02d}]" if s["inicio"] is not None else ""
        turnos.append((s["inicio"], f"{quien}{minuto}: {s['texto']}"))
    # Las marcas (★) las puso el usuario durante la reunión: señalan lo que le importó.
    return "\n\n".join([f"# {r['titulo']} ({r['inicio'][:16].replace('T', ' ')})", *db.intercalar_marcas(turnos, marcas)])


@mcp.tool()
def search(query: str, limit: int = 20) -> str:
    """Busca palabras en lo que se dijo en todas las reuniones (sin distinguir tildes). Devuelve la reunión, el
    minuto y el fragmento con la coincidencia entre [[ ]]."""
    terminos = " ".join(f'"{t}"' for t in query.replace('"', " ").split() if t)
    if not terminos:
        return "La búsqueda está vacía."
    with _conexion() as c:
        filas = c.execute(
            """SELECT r.id, r.titulo, r.inicio AS fecha, s.inicio AS segundo,
                      snippet(segmentos_fts, 0, '[[', ']]', '…', 14) AS fragmento
               FROM segmentos_fts JOIN segmentos s ON s.id = segmentos_fts.rowid JOIN reuniones r ON r.id = s.reunion_id
               WHERE segmentos_fts MATCH ? AND r.id NOT IN (SELECT value FROM json_each(?))
               ORDER BY rank LIMIT ?""", (terminos, _ocultas(c), min(limit, 50))).fetchall()
    if not filas:
        return f"Nada encontrado para «{query}»."
    return "\n".join(f'- {f["titulo"]} ({f["fecha"][:10]}, id {f["id"]}){_en_minuto(f["segundo"])}: {f["fragmento"]}'
                     for f in filas)


def _resumen_minuta(crudo):
    """El resumen de Minuta en texto: secciones y viñetas, cada una con los minutos que la respaldan."""
    if not crudo:
        return ""
    lineas = []
    for sec in json.loads(crudo).get("secciones", []):
        lineas.append(f"## {sec['titulo']}")
        for v in sec["vinetas"]:
            citas = ", ".join(f"{int(c // 60):02d}:{int(c % 60):02d}" for c in v["citas"])
            lineas.append(f"- {v['texto']}" + (f" [{citas}]" if citas else ""))
    return "\n".join(lineas)


@mcp.tool()
def list_action_items(time_range: str = "last_30_days", custom_start: str | None = None, custom_end: str | None = None,
                      status: str = "aceptada", folder: str | None = None) -> str:
    """Acciones (compromisos) que salieron de las reuniones. status: aceptada (las que el usuario confirmó, por
    defecto) | propuesta | todas. Cada una trae la reunión, el responsable y el minuto en que se dijo."""
    desde, hasta = _rango(time_range, custom_start, custom_end)
    sql = ("SELECT a.texto, a.responsable, a.t, a.estado, r.id, r.titulo, r.carpeta, r.inicio FROM acciones a "
           "JOIN reuniones r ON r.id = a.reunion_id WHERE r.inicio >= ? AND r.inicio < ? AND a.estado != 'descartada' "
           "AND r.id NOT IN (SELECT value FROM json_each(?))")
    args = [desde.isoformat(), hasta.isoformat()]
    if status != "todas":
        sql += " AND a.estado = ?"; args.append(status)
    if folder:
        sql += " AND r.carpeta = ?"; args.append(folder)
    with _conexion() as c:
        args.insert(2, _ocultas(c))
        filas = c.execute(sql + " ORDER BY r.inicio, a.t", args).fetchall()
    if not filas:
        return f"No hay acciones ({status}) entre {desde} y {hasta - timedelta(days=1)}."
    return "\n".join(f'- {f["texto"]}' + (f' · {f["responsable"]}' if f["responsable"] else "")
                     + f' ({f["titulo"]}, {f["inicio"][:10]}, id {f["id"]}{_en_minuto(f["t"])}, {f["estado"]})'
                     for f in filas)


@mcp.tool()
def propose_edit(meeting_id: str, original: str, corrected: str, reason: str | None = None) -> str:
    """Propone corregir un fragmento de la transcripción (un nombre mal escrito, una palabra mal transcripta). No
    cambia nada: el usuario ve la propuesta en la app y la aplica o la rechaza. `original` tiene que aparecer tal cual
    en un solo turno (sumá palabras de contexto si se repite); `corrected` es ese mismo fragmento corregido."""
    if not original or original == corrected:
        return "No hay nada que proponer: el original está vacío o es igual a la corrección."
    if len(corrected) > 2000 or len(reason or "") > 500:
        return "La corrección es demasiado larga: proponé el fragmento, no el turno entero."
    with _conexion() as c:
        if meeting_id in db.reuniones_que_no_salen(c):
            return f"No existe la reunión {meeting_id}."
        turnos = [dict(f) for f in c.execute("SELECT id, texto FROM segmentos WHERE reunion_id = ? AND instr(texto, ?) > 0",
                                             (meeting_id, original))]
    if not turnos:
        return f"«{original}» no aparece tal cual en la reunión {meeting_id}: copiá el fragmento exacto de la transcripción."
    veces = sum(t["texto"].count(original) for t in turnos)
    if veces > 1:
        return f"«{original}» aparece {veces} veces: sumá palabras de contexto para que sea una sola."
    t = turnos[0]
    c = _conexion_escritura()
    try:
        with c:
            db.proponer_edicion(c, meeting_id, t["id"], t["texto"].replace(original, corrected, 1), reason)
    except ValueError as e:
        return str(e)
    finally:
        c.close()
    return "Propuesta guardada. El usuario la ve en la reunión, en la app, y decide si la aplica."


# --- prompts --------------------------------------------------------------------------------------------------

@mcp.prompt(description="Resumen de la semana de una carpeta: decisiones, acciones y temas abiertos.")
def resumen_semanal(folder: str) -> str:
    return (f"Armá el resumen de esta semana de la carpeta «{folder}» con las reuniones de Minuta. Usá list_meetings "
            f"(this_week, folder={folder}), get_meetings para los resúmenes y list_action_items para las acciones. "
            "Agrupá por tema: qué se decidió, qué quedó abierto y quién hace qué. Citá la reunión y el minuto de cada "
            "punto. Tono ejecutivo, sin guion largo.")


@mcp.prompt(description="Preparar una reunión: qué quedó de las anteriores de la misma serie.")
def preparar_reunion(title: str) -> str:
    return (f"Voy a entrar a «{title}». Buscá con list_meetings y search las reuniones anteriores de esa serie o de su "
            "carpeta (las últimas tres), y decime en pocas líneas: qué se decidió, qué acciones siguen abiertas "
            "(list_action_items con status=todas) y qué preguntas quedaron sin respuesta. Con el minuto de cada cosa.")


@mcp.prompt(description="Acciones abiertas de las reuniones, para pasar a la lista de tareas.")
def acciones_abiertas(time_range: str = "this_week") -> str:
    return (f"Listá las acciones aceptadas de las reuniones ({time_range}) con list_action_items. Para cada una, "
            "proponé cómo anotarla como tarea, con responsable y minuto. No escribas nada: proponé y esperá.")


@mcp.prompt(description="Qué se dijo de un tema en todas las reuniones, en orden.")
def que_se_dijo_de(topic: str) -> str:
    return (f"Buscá «{topic}» en las reuniones con search (probá también variantes y nombres relacionados). Armá una "
            "línea de tiempo: fecha, reunión, minuto y qué se dijo, citando lo justo. Al final, el estado actual del tema.")


def _receta(ruta):
    def receta(detalle: str = "") -> str:
        texto = ruta.read_text(encoding="utf-8")  # se lee al pedirla: editar el archivo alcanza, sin reiniciar
        return texto + (f"\n\n{detalle}" if detalle.strip() else "")
    return receta


def cargar_recetas():
    """Cada `DATOS/recetas/<nombre>.md` es un prompt: la primera línea es su descripción."""
    carpeta = config.DATOS / "recetas"
    for ruta in sorted(carpeta.glob("*.md")) if carpeta.exists() else []:
        lineas = [l.strip("# ").strip() for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()]
        mcp.add_prompt(Prompt.from_function(_receta(ruta), name=f"receta_{ruta.stem}",
                                            description=(lineas[0] if lineas else ruta.stem)[:200]))


def _en_minuto(segundo):
    return f" en {int(segundo // 60):02d}:{int(segundo % 60):02d}" if segundo is not None else ""


def main():
    cargar_recetas()
    mcp.run()
