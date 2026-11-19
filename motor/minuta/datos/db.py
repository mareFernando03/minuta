"""SQLite: la fuente de verdad del motor. WAL para que la API lea mientras la cola escribe.

Una conexión por hilo (`conexion()`); cada escritura es una transacción corta. Las migraciones son una lista
ordenada: la versión aplicada vive en `PRAGMA user_version`, y una migración nueva se agrega al final, nunca se
edita una vieja.
"""
import difflib
import json
import re
import unicodedata
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

from minuta import config

MIGRACIONES = [
    """
    CREATE TABLE grabaciones (
        id TEXT PRIMARY KEY,                -- nombre de la carpeta: 20261009-172910
        carpeta TEXT NOT NULL,
        inicio TEXT NOT NULL,
        fin TEXT,
        duracion_s REAL,
        corte_s REAL,                       -- dónde se soltó el micrófono: lo posterior no es la reunión
        apps TEXT NOT NULL DEFAULT '[]',
        titulos_ventana TEXT NOT NULL DEFAULT '{}',
        estado TEXT NOT NULL,               -- grabando | grabada | procesando | archivada | revisar | descartada | error
        error TEXT,
        reunion_id TEXT,
        audio_hasta TEXT,                   -- fecha desde la que se puede borrar el audio
        creado TEXT NOT NULL,
        actualizado TEXT NOT NULL
    );
    CREATE TABLE reuniones (
        id TEXT PRIMARY KEY,                -- local-<uuid> (Minuta) o el uuid de Granola (importadas)
        fuente TEXT NOT NULL,               -- minuta | granola
        grabacion_id TEXT REFERENCES grabaciones(id),
        titulo TEXT NOT NULL,
        carpeta TEXT,                       -- Seguimiento Northwind, Contoso Fabrikam, Orbit, Globex, Acme, Facultad
        inicio TEXT NOT NULL,
        fin TEXT,
        origen TEXT,                        -- calendario northwind_outlook | ventana | slack | granola | manual
        participantes TEXT NOT NULL DEFAULT '[]',
        motor TEXT,                         -- assemblyai | groq | small | large-v3-turbo | granola
        resumen TEXT,
        correcciones TEXT NOT NULL DEFAULT '[]',
        exportado_en TEXT,
        creado TEXT NOT NULL,
        actualizado TEXT NOT NULL
    );
    CREATE INDEX reuniones_inicio ON reuniones(inicio DESC);
    CREATE TABLE segmentos (
        id INTEGER PRIMARY KEY,
        reunion_id TEXT NOT NULL REFERENCES reuniones(id) ON DELETE CASCADE,
        orden INTEGER NOT NULL,
        canal TEXT NOT NULL,                -- mic | sistema | <nombre de hablante de Granola>
        inicio REAL,                        -- segundos desde el inicio de la grabación; NULL en lo importado
        fin REAL,
        texto TEXT NOT NULL
    );
    CREATE INDEX segmentos_reunion ON segmentos(reunion_id, orden);
    CREATE VIRTUAL TABLE segmentos_fts USING fts5(texto, content='segmentos', content_rowid='id',
                                                 tokenize='unicode61 remove_diacritics 2');
    CREATE TRIGGER segmentos_ai AFTER INSERT ON segmentos BEGIN
        INSERT INTO segmentos_fts(rowid, texto) VALUES (new.id, new.texto);
    END;
    CREATE TRIGGER segmentos_ad AFTER DELETE ON segmentos BEGIN
        INSERT INTO segmentos_fts(segmentos_fts, rowid, texto) VALUES ('delete', old.id, old.texto);
    END;
    CREATE TRIGGER segmentos_au AFTER UPDATE ON segmentos BEGIN
        INSERT INTO segmentos_fts(segmentos_fts, rowid, texto) VALUES ('delete', old.id, old.texto);
        INSERT INTO segmentos_fts(rowid, texto) VALUES (new.id, new.texto);
    END;
    CREATE TABLE trabajos (
        id INTEGER PRIMARY KEY,
        tipo TEXT NOT NULL,                 -- transcribir | repasar
        grabacion_id TEXT NOT NULL REFERENCES grabaciones(id),
        estado TEXT NOT NULL,               -- pendiente | corriendo | hecho | error
        intentos INTEGER NOT NULL DEFAULT 0,
        proximo_intento TEXT,
        progreso TEXT,
        error TEXT,
        creado TEXT NOT NULL,
        actualizado TEXT NOT NULL
    );
    CREATE INDEX trabajos_pendientes ON trabajos(estado, proximo_intento);
    CREATE TABLE uso_motores (
        id INTEGER PRIMARY KEY,
        fecha TEXT NOT NULL,
        motor TEXT NOT NULL,                -- assemblyai | groq | haiku | local
        segundos_audio REAL NOT NULL DEFAULT 0,
        costo_usd REAL NOT NULL DEFAULT 0,
        grabacion_id TEXT
    );
    CREATE TABLE ajustes (
        clave TEXT PRIMARY KEY,
        valor TEXT NOT NULL
    );
    """,
    # 2: parámetros de un trabajo (por ejemplo, reprocesar con un motor elegido a mano)
    "ALTER TABLE trabajos ADD COLUMN parametros TEXT NOT NULL DEFAULT '{}'",
    # 3: nombre que el usuario le puso a cada canal de una reunión ({"sistema": "Pedro Ríos"})
    "ALTER TABLE reuniones ADD COLUMN hablantes TEXT NOT NULL DEFAULT '{}'",
    # 4: el usuario pidió, durante la grabación, que esta reunión se transcriba solo en la PC
    "ALTER TABLE grabaciones ADD COLUMN solo_local INTEGER NOT NULL DEFAULT 0",
    # 5: momentos marcados durante la grabación (atajo global o botón). `t` en segundos desde el inicio del audio.
    """
    CREATE TABLE marcas (
        id INTEGER PRIMARY KEY,
        grabacion_id TEXT NOT NULL,
        reunion_id TEXT,
        t REAL NOT NULL,
        etiqueta TEXT,                      -- accion | decision | pregunta | NULL (solo «importante»)
        nota TEXT,
        creado TEXT NOT NULL
    );
    CREATE INDEX marcas_reunion ON marcas(reunion_id, t);
    CREATE INDEX marcas_grabacion ON marcas(grabacion_id, t)
    """,
    # 6: avance real de un trabajo (0 a 1) y cuándo empezó a correr, para estimar cuánto falta
    "ALTER TABLE trabajos ADD COLUMN avance REAL; ALTER TABLE trabajos ADD COLUMN empezado TEXT",
    # 7: avance con el que arrancó la corrida: lo retomado de una corrida anterior no cuenta para la velocidad
    "ALTER TABLE trabajos ADD COLUMN avance_inicial REAL",
    # 8: palabras de cada turno con tiempo y probabilidad, 1 a 1 con el texto (transcripcion/palabras.py)
    "ALTER TABLE segmentos ADD COLUMN palabras TEXT",
    # 9: la reunión tiene correcciones o borrados a mano: el repaso con el modelo grande ya no la pisa
    "ALTER TABLE reuniones ADD COLUMN editada_en TEXT",
    # 10: quién habla dentro de «Ellos» (diarizacion/). `voces` son las personas con nombre y su huella (promedio de
    # las huellas confirmadas); `voces_reunion`, cada voz que la diarización encontró en una reunión.
    """
    ALTER TABLE segmentos ADD COLUMN voz TEXT;
    CREATE TABLE voces (
        id INTEGER PRIMARY KEY,
        nombre TEXT NOT NULL,
        huella BLOB NOT NULL,
        n INTEGER NOT NULL DEFAULT 1,
        creado TEXT NOT NULL,
        actualizado TEXT NOT NULL
    );
    CREATE UNIQUE INDEX voces_nombre ON voces(nombre COLLATE NOCASE);
    CREATE TABLE voces_reunion (
        reunion_id TEXT NOT NULL,
        etiqueta TEXT NOT NULL,             -- V1, V2... en esa reunión
        huella BLOB,
        segundos REAL,
        voz_id INTEGER,                     -- la persona, si se sabe
        origen TEXT,                        -- manual (la puso el usuario) | auto (la asignó Minuta)
        sugerida_id INTEGER,                -- «¿Es Pedro?»
        similitud REAL,
        rechazadas TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY (reunion_id, etiqueta)
    )
    """,
    # 11: borrar una reunión dejaba sus voces (huellas de terceros) en la base
    "DELETE FROM voces_reunion WHERE reunion_id NOT IN (SELECT id FROM reuniones)",
    # 12: inteligencia (inteligencia/). `resumen_minuta` es el resumen propio, con citas al minuto (el `resumen`
    # importado de Granola no se toca); `acciones`, los compromisos que salen de él, que el usuario acepta o descarta;
    # `ediciones_pendientes`, las correcciones que Claude propone por MCP y el usuario aplica o rechaza en la app.
    """
    ALTER TABLE reuniones ADD COLUMN resumen_minuta TEXT;
    CREATE TABLE acciones (
        id INTEGER PRIMARY KEY,
        reunion_id TEXT NOT NULL,
        texto TEXT NOT NULL,
        responsable TEXT,
        t REAL,
        estado TEXT NOT NULL DEFAULT 'propuesta',   -- propuesta | aceptada | descartada
        creado TEXT NOT NULL,
        actualizado TEXT NOT NULL
    );
    CREATE INDEX acciones_reunion ON acciones(reunion_id);
    CREATE TABLE ediciones_pendientes (
        id INTEGER PRIMARY KEY,
        reunion_id TEXT NOT NULL,
        segmento_id INTEGER NOT NULL,
        original TEXT NOT NULL,             -- el texto del turno cuando se propuso: si cambió, no se aplica a ciegas
        propuesto TEXT NOT NULL,
        motivo TEXT,
        creado TEXT NOT NULL
    );
    CREATE INDEX ediciones_reunion ON ediciones_pendientes(reunion_id)
    """,
    # 13 a 15: reservadas. Quedan vacías para no correr la numeración de las siguientes.
    "SELECT 1",
    "SELECT 1",
    "SELECT 1",
    # 16: grabaciones que empezó el usuario a mano (reuniones presenciales): el título y la carpeta que dio al empezar.
    # `apps` lleva "manual" en esas grabaciones.
    "ALTER TABLE grabaciones ADD COLUMN titulo_manual TEXT; ALTER TABLE grabaciones ADD COLUMN carpeta_manual TEXT",
    # 17: los últimos dictados (dictado.py), solo para «Copiar el último» si el pegado cayó en otra ventana. El audio
    # no se guarda; el texto no sale por el MCP.
    """
    CREATE TABLE dictados (id INTEGER PRIMARY KEY, en TEXT NOT NULL, texto TEXT NOT NULL, segundos REAL NOT NULL,
                           motor TEXT NOT NULL, ms INTEGER NOT NULL)
    """,
]

ETIQUETAS = {"accion": "Acción", "decision": "Decisión", "pregunta": "Pregunta"}
CARPETA_SIN_IDENTIFICAR = "sin identificar"

AJUSTES_POR_DEFECTO = {
    # Qué motores puede usar cada carpeta, en orden. "local" no sale de la notebook.
    "privacidad": {
        "Contoso Fabrikam": ["assemblyai", "groq", "local"],
        "Seguimiento Northwind": ["assemblyai", "groq", "local"],
        "Orbit": ["assemblyai", "groq", "local"],
        "Globex": ["assemblyai", "groq", "local"],
        "*": ["groq", "local"],
        "sin identificar": ["local"],
    },
    "audio_dias": 7,
    "exportar_a": str(config.EXPORTAR_A_POR_DEFECTO),
    "pausado": False,
    # Notificaciones nativas de Windows: al empezar a grabar, alertas, reunión lista, errores.
    "notificaciones": True,
    # Quién habla dentro de «Ellos» (diarizacion/): se separan las voces en esta PC después de transcribir.
    "diarizar": True,
    # Asignar nombres solo cuando la voz se parece mucho a una persona conocida. Apagado hasta medir con reuniones de
    # varias personas (scripts/medir_diarizacion.py): mientras tanto Minuta solo sugiere «¿Es Pedro?».
    "diarizacion_auto": False,
    "diarizacion_umbral": 1.2,
    # Resumen con Claude (inteligencia/resumen.py) de las reuniones que pueden salir de la PC según su carpeta.
    "resumir": True,
    # Plantilla del resumen por carpeta ({"Orbit": "## Decisiones\n..."}). Sin plantilla, Claude elige las secciones.
    "plantillas": {},
    # Dictado con Ctrl+Win (dictado.py). Apagado hasta que el usuario lo encienda: con "groq" el audio sale a Groq.
    "dictado": False,
    "dictado_motor": "groq",  # "groq" (sin red o sin clave cae a "local") o "local"
}

_local = threading.local()


def ahora():
    return datetime.now().isoformat(timespec="seconds")


def conexion():
    c = getattr(_local, "c", None)
    if c is None:
        config.DATOS.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(config.BASE_DE_DATOS, timeout=30, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=30000")
        _local.c = c
    return c


def cerrar():
    c = getattr(_local, "c", None)
    if c is not None:
        c.close()
        _local.c = None


@contextmanager
def transaccion():
    c = conexion()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise


def migrar():
    c = conexion()
    version = c.execute("PRAGMA user_version").fetchone()[0]
    for n, sql in enumerate(MIGRACIONES[version:], start=version + 1):
        c.executescript(f"BEGIN; {sql}; PRAGMA user_version = {n}; COMMIT;")
    return len(MIGRACIONES)


# --- ajustes -----------------------------------------------------------------------------------------------

def ajuste(clave):
    fila = conexion().execute("SELECT valor FROM ajustes WHERE clave = ?", (clave,)).fetchone()
    return json.loads(fila["valor"]) if fila else AJUSTES_POR_DEFECTO.get(clave)


def motores_para(reglas, carpeta):
    """Qué motores puede usar una carpeta, en orden. Lo no identificado es siempre local, aunque al ajuste guardado le
    falte esa clave."""
    if carpeta == CARPETA_SIN_IDENTIFICAR:
        return ["local"]
    return reglas.get(carpeta) or reglas.get("*") or ["local"]


def texto_puede_salir(reglas, carpeta, identificada, solo_local):
    """Si el texto de una reunión puede ir a Claude (glosario, resumen, MCP): la misma regla que el audio a la nube.
    Tiene que estar identificada, no marcada «solo en esta PC», y su carpeta tiene que permitir algún motor en la nube."""
    return bool(identificada and not solo_local and any(m != "local" for m in motores_para(reglas, carpeta)))


def reuniones_que_no_salen(c):
    """Ids de las reuniones grabadas por Minuta cuyo texto no puede ir a Claude. Recibe la conexión: el MCP usa la
    suya, de solo lectura. Las importadas de Granola no tienen grabación y ya estaban en la nube."""
    f = c.execute("SELECT valor FROM ajustes WHERE clave = 'privacidad'").fetchone()
    reglas = json.loads(f["valor"]) if f else AJUSTES_POR_DEFECTO["privacidad"]
    return {f["id"] for f in c.execute(
        "SELECT r.id, r.carpeta, g.estado, g.solo_local FROM reuniones r JOIN grabaciones g ON g.id = r.grabacion_id")
        if not texto_puede_salir(reglas, f["carpeta"], f["estado"] == "archivada", f["solo_local"])}


def guardar_ajuste(clave, valor):
    conexion().execute("INSERT INTO ajustes(clave, valor) VALUES (?, ?) ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                       (clave, json.dumps(valor, ensure_ascii=False)))


# --- grabaciones ---------------------------------------------------------------------------------------------

def _fila(r, json_cols=()):
    if r is None:
        return None
    d = dict(r)
    for col in json_cols:
        if d.get(col) is not None:
            d[col] = json.loads(d[col])
    return d


def crear_grabacion(gid, carpeta, inicio, apps):
    t = ahora()
    conexion().execute(
        "INSERT INTO grabaciones(id, carpeta, inicio, apps, estado, creado, actualizado) VALUES (?, ?, ?, ?, 'grabando', ?, ?)",
        (gid, str(carpeta), inicio, json.dumps(apps), t, t))


def actualizar_grabacion(gid, **campos):
    for col in ("apps", "titulos_ventana"):
        if col in campos:
            campos[col] = json.dumps(campos[col], ensure_ascii=False)
    campos["actualizado"] = ahora()
    sets = ", ".join(f"{k} = ?" for k in campos)
    conexion().execute(f"UPDATE grabaciones SET {sets} WHERE id = ?", (*campos.values(), gid))


def grabacion(gid):
    return _fila(conexion().execute("SELECT * FROM grabaciones WHERE id = ?", (gid,)).fetchone(), ("apps", "titulos_ventana"))


def grabaciones(estados=None, limite=200):
    if estados:
        filas = conexion().execute(
            f"SELECT * FROM grabaciones WHERE estado IN ({','.join('?' * len(estados))}) ORDER BY inicio DESC LIMIT ?",
            (*estados, limite)).fetchall()
    else:
        filas = conexion().execute("SELECT * FROM grabaciones ORDER BY inicio DESC LIMIT ?", (limite,)).fetchall()
    return [_fila(f, ("apps", "titulos_ventana")) for f in filas]


# --- reuniones y segmentos -----------------------------------------------------------------------------------

def guardar_reunion(r, segmentos):
    """Inserta o reemplaza una reunión con todos sus segmentos (dicts con canal, inicio, fin, texto)."""
    t = ahora()
    with transaccion() as c:
        previa = c.execute("SELECT creado FROM reuniones WHERE id = ?", (r["id"],)).fetchone()
        c.execute("DELETE FROM segmentos WHERE reunion_id = ?", (r["id"],))
        c.execute("DELETE FROM ediciones_pendientes WHERE reunion_id = ?", (r["id"],))  # apuntaban a los turnos viejos
        c.execute(
            """INSERT INTO reuniones(id, fuente, grabacion_id, titulo, carpeta, inicio, fin, origen, participantes, motor,
                                     resumen, correcciones, creado, actualizado)
               VALUES (:id, :fuente, :grabacion_id, :titulo, :carpeta, :inicio, :fin, :origen, :participantes, :motor,
                       :resumen, :correcciones, :creado, :actualizado)
               ON CONFLICT(id) DO UPDATE SET fuente=excluded.fuente, grabacion_id=excluded.grabacion_id,
                   titulo=excluded.titulo, carpeta=excluded.carpeta, inicio=excluded.inicio, fin=excluded.fin,
                   origen=excluded.origen, participantes=excluded.participantes, motor=excluded.motor,
                   resumen=excluded.resumen, correcciones=excluded.correcciones, actualizado=excluded.actualizado""",
            {"grabacion_id": None, "fin": None, "origen": None, "motor": None, "resumen": None, "carpeta": None, **r,
             "participantes": json.dumps(r.get("participantes") or [], ensure_ascii=False),
             "correcciones": json.dumps(r.get("correcciones") or [], ensure_ascii=False),
             "creado": previa["creado"] if previa else t, "actualizado": t})
        c.executemany("INSERT INTO segmentos(reunion_id, orden, canal, inicio, fin, texto, palabras, voz) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      [(r["id"], i, s["canal"], s.get("inicio"), s.get("fin"), s["texto"],
                        json.dumps(s["palabras"], ensure_ascii=False) if s.get("palabras") else None, s.get("voz"))
                       for i, s in enumerate(segmentos)])


def editar_reunion(rid, **campos):
    permitidos = {"titulo", "carpeta", "participantes", "exportado_en", "resumen", "hablantes"}
    campos = {k: v for k, v in campos.items() if k in permitidos}
    for col in ("participantes", "hablantes"):
        if col in campos:
            campos[col] = json.dumps(campos[col], ensure_ascii=False)
    if not campos:
        return
    campos["actualizado"] = ahora()
    conexion().execute(f"UPDATE reuniones SET {', '.join(f'{k} = ?' for k in campos)} WHERE id = ?", (*campos.values(), rid))


def reunion_basica(rid):
    """La reunión sin turnos ni marcas: para chequeos que no necesitan la transcripción entera."""
    return _fila(conexion().execute("SELECT * FROM reuniones WHERE id = ?", (rid,)).fetchone(),
                 ("participantes", "correcciones", "hablantes"))


def reunion(rid):
    r = _fila(conexion().execute("SELECT * FROM reuniones WHERE id = ?", (rid,)).fetchone(),
              ("participantes", "correcciones", "hablantes"))
    if r:
        r["segmentos"] = [_fila(s, ("palabras",)) for s in conexion().execute(
            "SELECT id, canal, inicio, fin, texto, palabras, voz FROM segmentos WHERE reunion_id = ? ORDER BY orden", (rid,))]
        r["marcas"] = marcas(reunion_id=rid)
        r["voces"] = voces_de_reunion(rid)
        r["resumen_minuta"] = json.loads(r["resumen_minuta"]) if r.get("resumen_minuta") else None
        r["acciones"] = acciones(reunion_id=rid)
        r["ediciones"] = ediciones_pendientes(rid)
    return r


def reuniones(carpeta=None, limite=100, antes_de=None):
    sql, args = "SELECT id, fuente, titulo, carpeta, inicio, fin, motor, origen FROM reuniones WHERE 1=1", []
    if carpeta:
        sql += " AND carpeta = ?"; args.append(carpeta)
    if antes_de:
        sql += " AND inicio < ?"; args.append(antes_de)
    sql += " ORDER BY inicio DESC LIMIT ?"; args.append(limite)
    return [dict(f) for f in conexion().execute(sql, args)]


# --- edición de turnos ----------------------------------------------------------------------------------------

def segmento(sid):
    return _fila(conexion().execute("SELECT * FROM segmentos WHERE id = ?", (sid,)).fetchone(), ("palabras",))


def editar_segmento(sid, texto):
    """Cambia el texto de un turno y reacomoda sus palabras al texto nuevo (la búsqueda se actualiza por trigger)."""
    from minuta.transcripcion.palabras import alinear
    s = segmento(sid)
    palabras = alinear(texto, s.get("palabras"))
    conexion().execute("UPDATE segmentos SET texto = ?, palabras = ? WHERE id = ?",
                       (texto, json.dumps(palabras, ensure_ascii=False) if palabras else None, sid))
    _marcar_editada(conexion(), s["reunion_id"])
    return segmento(sid)


def _marcar_editada(c, rid):
    t = ahora()
    c.execute("UPDATE reuniones SET actualizado = ?, editada_en = ? WHERE id = ?", (t, t, rid))


def _patron_palabra(palabra):
    return re.compile(rf"(?<![\w-]){re.escape(palabra)}(?![\w-])")


def contar_en_reunion(rid, palabra):
    patron = _patron_palabra(palabra)
    return sum(len(patron.findall(f["texto"])) for f in conexion().execute(
        "SELECT texto FROM segmentos WHERE reunion_id = ?", (rid,)))


def reemplazar_en_reunion(rid, original, correcto):
    """Cambia una palabra en todos los turnos de la reunión (como palabra entera). Devuelve cuántas cambió."""
    from minuta.transcripcion.palabras import alinear
    patron, n = _patron_palabra(original), 0
    with transaccion() as c:
        for f in c.execute("SELECT id, texto, palabras FROM segmentos WHERE reunion_id = ?", (rid,)).fetchall():
            nuevo, k = patron.subn(lambda _m: correcto, f["texto"])  # literal: una barra en `correcto` no es regex
            if k:
                palabras = alinear(nuevo, json.loads(f["palabras"]) if f["palabras"] else None)
                c.execute("UPDATE segmentos SET texto = ?, palabras = ? WHERE id = ?",
                          (nuevo, json.dumps(palabras, ensure_ascii=False) if palabras else None, f["id"]))
                n += k
        if n:
            _marcar_editada(c, rid)
    return n


def borrar_segmentos(rid, ids):
    """Borra turnos de una reunión y las marcas que caían en ese tramo. Devuelve (desde_s, hasta_s, cuántos)."""
    marcadores = ",".join("?" * len(ids))
    filas = conexion().execute(f"SELECT inicio, fin FROM segmentos WHERE reunion_id = ? AND id IN ({marcadores})",
                               (rid, *ids)).fetchall()
    if not filas:
        return None, None, 0
    tiempos = [t for f in filas for t in (f["inicio"], f["fin"]) if t is not None]
    desde, hasta = (min(tiempos), max(tiempos)) if tiempos else (None, None)
    with transaccion() as c:
        c.execute(f"DELETE FROM ediciones_pendientes WHERE reunion_id = ? AND segmento_id IN ({marcadores})", (rid, *ids))
        c.execute(f"DELETE FROM segmentos WHERE reunion_id = ? AND id IN ({marcadores})", (rid, *ids))
        if desde is not None:
            c.execute("DELETE FROM marcas WHERE reunion_id = ? AND t BETWEEN ? AND ?", (rid, desde, hasta))
            # Las acciones que el usuario ya aceptó quedan: son su decisión, quizás ya están en su lista de tareas.
            c.execute("DELETE FROM acciones WHERE reunion_id = ? AND estado != 'aceptada' AND t BETWEEN ? AND ?",
                      (rid, desde, hasta))
        # El resumen se armó con lo que se acaba de borrar: se descarta y la app ofrece hacerlo de nuevo.
        c.execute("UPDATE reuniones SET resumen_minuta = NULL WHERE id = ?", (rid,))
        _marcar_editada(c, rid)
    return desde, hasta, len(filas)


def borrar_reunion(rid):
    with transaccion() as c:
        c.execute("UPDATE grabaciones SET reunion_id = NULL WHERE reunion_id = ?", (rid,))
        c.execute("DELETE FROM marcas WHERE reunion_id = ?", (rid,))
        c.execute("DELETE FROM acciones WHERE reunion_id = ?", (rid,))
        c.execute("DELETE FROM ediciones_pendientes WHERE reunion_id = ?", (rid,))
        personas = [f["voz_id"] for f in c.execute(
            "SELECT DISTINCT voz_id FROM voces_reunion WHERE reunion_id = ? AND origen = 'manual'", (rid,))]
        c.execute("DELETE FROM voces_reunion WHERE reunion_id = ?", (rid,))  # las huellas son datos de terceros
        for pid in personas:
            _recalcular_huella(c, pid)
        c.execute("DELETE FROM reuniones WHERE id = ?", (rid,))


# --- marcas ------------------------------------------------------------------------------------------------------

def crear_marca(gid, t, etiqueta=None, nota=None):
    cur = conexion().execute(
        "INSERT INTO marcas(grabacion_id, reunion_id, t, etiqueta, nota, creado) "
        "VALUES (?, (SELECT reunion_id FROM grabaciones WHERE id = ?), ?, ?, ?, ?)",
        (gid, gid, round(t, 1), etiqueta, nota, ahora()))
    return marca(cur.lastrowid)


def marca(mid):
    return _fila(conexion().execute("SELECT * FROM marcas WHERE id = ?", (mid,)).fetchone())


def marcas(reunion_id=None, grabacion_id=None):
    col, valor = ("reunion_id", reunion_id) if reunion_id else ("grabacion_id", grabacion_id)
    return [dict(m) for m in conexion().execute(f"SELECT * FROM marcas WHERE {col} = ? ORDER BY t", (valor,))]


def editar_marca(mid, **campos):
    campos = {k: v for k, v in campos.items() if k in ("etiqueta", "nota", "t")}
    if campos:
        conexion().execute(f"UPDATE marcas SET {', '.join(f'{k} = ?' for k in campos)} WHERE id = ?",
                           (*campos.values(), mid))
    return marca(mid)


def borrar_marcas_de(gid):
    conexion().execute("DELETE FROM marcas WHERE grabacion_id = ?", (gid,))


def titulo_reunion(rid):
    f = conexion().execute("SELECT titulo FROM reuniones WHERE id = ?", (rid,)).fetchone()
    return f["titulo"] if f else None


def borrar_marca(mid):
    conexion().execute("DELETE FROM marcas WHERE id = ?", (mid,))


def asignar_marcas(gid, rid):
    """Al guardar la reunión, las marcas de su grabación pasan a ella."""
    conexion().execute("UPDATE marcas SET reunion_id = ? WHERE grabacion_id = ?", (rid, gid))


def intercalar_marcas(lineas_con_tiempo, lista):
    """Mezcla las marcas entre los turnos de una transcripción. `lineas_con_tiempo`: [(inicio | None, texto)]. Cada
    marca va antes del primer turno que empieza después de ella."""
    salida, pendientes = [], sorted(lista, key=lambda m: m["t"])
    for inicio, texto in lineas_con_tiempo:
        while pendientes and inicio is not None and pendientes[0]["t"] <= inicio:
            salida.append(texto_marca(pendientes.pop(0)))
        salida.append(texto)
    return salida + [texto_marca(m) for m in pendientes]


def texto_marca(m):
    minuto = f"{int(m['t'] // 60):02d}:{int(m['t'] % 60):02d}"
    nota = f": {m['nota']}" if m.get("nota") else ""
    return f"★ [{minuto}] {ETIQUETAS.get(m.get('etiqueta'), 'Marca')}{nota}"


def buscar(consulta, limite=50, prefijo=False):
    """Búsqueda de texto completo, sin distinguir tildes. Devuelve el fragmento con la coincidencia marcada.
    `prefijo`: la última palabra también encuentra lo que empieza así (Ctrl+K, mientras se escribe)."""
    terminos = " ".join(f'"{t}"' for t in consulta.replace('"', " ").split() if t)
    if not terminos:
        return []
    if prefijo:
        terminos += "*"
    filas = conexion().execute(
        """SELECT r.id, r.titulo, r.carpeta, r.inicio AS fecha, s.inicio AS segundo, s.canal,
                  snippet(segmentos_fts, 0, '[[', ']]', '…', 14) AS fragmento
           FROM segmentos_fts JOIN segmentos s ON s.id = segmentos_fts.rowid JOIN reuniones r ON r.id = s.reunion_id
           WHERE segmentos_fts MATCH ? ORDER BY rank LIMIT ?""", (terminos, limite)).fetchall()
    return [dict(f) for f in filas]


# --- trabajos ------------------------------------------------------------------------------------------------

def encolar(tipo, gid, aunque_corra=False, **parametros):
    """Un trabajo por tipo y grabación. `aunque_corra`: el que ya corre leyó texto o parámetros viejos, así que solo se
    reusa uno pendiente, y con los parámetros nuevos (el pedido de «Regenerar»)."""
    t = ahora()
    estados = ("pendiente",) if aunque_corra else ("pendiente", "corriendo")
    existe = conexion().execute(
        f"SELECT id FROM trabajos WHERE tipo = ? AND grabacion_id = ? AND estado IN ({','.join('?' * len(estados))})",
        (tipo, gid, *estados)).fetchone()
    if existe:
        if aunque_corra:
            conexion().execute("UPDATE trabajos SET parametros = ? WHERE id = ?",
                               (json.dumps(parametros, ensure_ascii=False), existe["id"]))
        return existe["id"]
    return conexion().execute(
        """INSERT INTO trabajos(tipo, grabacion_id, estado, creado, actualizado, proximo_intento, parametros)
           VALUES (?, ?, 'pendiente', ?, ?, ?, ?)""",
        (tipo, gid, t, t, t, json.dumps(parametros, ensure_ascii=False))).lastrowid


def tomar_trabajo(tipos):
    """Toma el próximo trabajo vencido y lo marca corriendo, de forma atómica. Transcribir va primero: una reunión
    nueva no espera a que termine de separarse en voces (o de repasarse) otra anterior."""
    with transaccion() as c:
        fila = c.execute(
            f"""SELECT * FROM trabajos WHERE estado = 'pendiente' AND tipo IN ({','.join('?' * len(tipos))})
                AND (proximo_intento IS NULL OR proximo_intento <= ?)
                ORDER BY CASE tipo WHEN 'transcribir' THEN 0 ELSE 1 END, id LIMIT 1""", (*tipos, ahora())).fetchone()
        if fila is None:
            return None
        c.execute("UPDATE trabajos SET estado = 'corriendo', intentos = intentos + 1, actualizado = ?, empezado = ?, "
                  "avance = 0, avance_inicial = NULL WHERE id = ?", (ahora(), ahora(), fila["id"]))
        return dict(fila, intentos=fila["intentos"] + 1, parametros=json.loads(fila["parametros"] or "{}"))


def terminar_trabajo(tid, estado, error=None, proximo_intento=None, progreso=None):
    conexion().execute("UPDATE trabajos SET estado = ?, error = ?, proximo_intento = ?, progreso = ?, actualizado = ? WHERE id = ?",
                       (estado, error, proximo_intento, progreso, ahora(), tid))


def progreso_trabajo(tid, progreso, avance=None):
    """`progreso`: qué está haciendo, en palabras. `avance`: fracción hecha (0 a 1), si se sabe."""
    conexion().execute("UPDATE trabajos SET progreso = ?, avance = COALESCE(?, avance), "
                       "avance_inicial = COALESCE(avance_inicial, ?), actualizado = ? WHERE id = ?",
                       (progreso, avance, avance, ahora(), tid))


def trabajos(limite=50):
    return [dict(f) for f in conexion().execute("SELECT * FROM trabajos ORDER BY id DESC LIMIT ?", (limite,))]


def reanudar_trabajos_cortados():
    """Un trabajo que quedó 'corriendo' es de un motor que se cerró a mitad: vuelve a la cola."""
    conexion().execute("UPDATE trabajos SET estado = 'pendiente', actualizado = ? WHERE estado = 'corriendo'", (ahora(),))


# --- uso de motores ------------------------------------------------------------------------------------------

def registrar_uso(motor, segundos_audio=0.0, costo_usd=0.0, grabacion_id=None):
    conexion().execute("INSERT INTO uso_motores(fecha, motor, segundos_audio, costo_usd, grabacion_id) VALUES (?, ?, ?, ?, ?)",
                       (ahora(), motor, segundos_audio, costo_usd, grabacion_id))


DICTADOS_GUARDADOS = 50


def guardar_dictado(texto, segundos, motor, ms):
    c = conexion()
    c.execute("INSERT INTO dictados(en, texto, segundos, motor, ms) VALUES (?, ?, ?, ?, ?)",
              (ahora(), texto, round(segundos, 1), motor, ms))
    c.execute("DELETE FROM dictados WHERE id NOT IN (SELECT id FROM dictados ORDER BY id DESC LIMIT ?)",
              (DICTADOS_GUARDADOS,))


def dictados(limite=DICTADOS_GUARDADOS):
    return [dict(f) for f in conexion().execute("SELECT * FROM dictados ORDER BY id DESC LIMIT ?", (limite,))]


def uso_por_motor(desde=None):
    sql = "SELECT motor, SUM(segundos_audio) AS segundos, SUM(costo_usd) AS costo, COUNT(*) AS usos FROM uso_motores"
    args = ()
    if desde:
        sql += " WHERE fecha >= ?"; args = (desde,)
    return {f["motor"]: dict(f) for f in conexion().execute(sql + " GROUP BY motor", args)}


# --- voces (quién habla dentro de «Ellos») ------------------------------------------------------------------------

import numpy as _np

# Umbrales de similitud coseno entre huellas CAM++. Provisorios hasta medir con reuniones de varias personas
# (scripts/medir_diarizacion.py); por eso, salvo que se active `diarizacion_auto`, Minuta solo sugiere.
SIMILITUD_ALTA = 0.70
SIMILITUD_BAJA = 0.55
MARGEN = 0.08


def _huella(blob):
    return _np.frombuffer(blob, dtype=_np.float32) if blob else None


def voces_de_reunion(rid):
    filas = conexion().execute(
        """SELECT vr.etiqueta, vr.segundos, vr.voz_id, vr.origen, vr.sugerida_id, vr.similitud,
                  v.nombre AS nombre, s.nombre AS sugerida_nombre
           FROM voces_reunion vr LEFT JOIN voces v ON v.id = vr.voz_id LEFT JOIN voces s ON s.id = vr.sugerida_id
           WHERE vr.reunion_id = ? ORDER BY vr.etiqueta""", (rid,)).fetchall()
    return [{"etiqueta": f["etiqueta"], "segundos": f["segundos"], "voz_id": f["voz_id"], "nombre": f["nombre"],
             "origen": f["origen"],
             "sugerida": {"id": f["sugerida_id"], "nombre": f["sugerida_nombre"], "similitud": round(f["similitud"], 2)}
             if f["sugerida_id"] else None} for f in filas]


def guardar_voces_reunion(rid, huellas):
    """Reemplaza las voces de la reunión ({etiqueta: (huella o None, segundos)}). Una voz se reconoce entre una
    diarización y la siguiente por su huella (la numeración puede cambiar): conserva el nombre puesto a mano y los
    «No es X». Las voces muy cortas no tienen huella, pero igual llevan fila: si no, sus turnos quedan sin ficha."""
    previas = conexion().execute(
        "SELECT huella, voz_id, origen, rechazadas FROM voces_reunion WHERE reunion_id = ? AND huella IS NOT NULL",
        (rid,)).fetchall()
    tocadas = set()
    with transaccion() as c:
        c.execute("DELETE FROM voces_reunion WHERE reunion_id = ?", (rid,))
        for etiqueta, (h, segundos) in huellas.items():
            voz_id = origen = None
            rechazadas = "[]"
            for p in previas if h is not None else []:
                if float(_np.dot(h, _huella(p["huella"]))) >= 0.75:
                    rechazadas = p["rechazadas"]
                    if p["origen"] == "manual":
                        voz_id, origen = p["voz_id"], "manual"
                        tocadas.add(voz_id)
                    break
            c.execute("INSERT INTO voces_reunion(reunion_id, etiqueta, huella, segundos, voz_id, origen, rechazadas) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (rid, etiqueta, None if h is None else _np.asarray(h, dtype=_np.float32).tobytes(), segundos,
                       voz_id, origen, rechazadas))
        for pid in tocadas | {p["voz_id"] for p in previas if p["origen"] == "manual"}:
            _recalcular_huella(c, pid)


def _recalcular_huella(c, pid):
    """La huella de una persona es el promedio de las voces que el usuario le asignó a mano. Se recalcula entera
    (y no como promedio corrido) para que quitar o cambiar un nombre saque esa voz de la huella."""
    hs = [_huella(f["huella"]) for f in c.execute(
        "SELECT huella FROM voces_reunion WHERE voz_id = ? AND origen = 'manual' AND huella IS NOT NULL", (pid,))]
    m = _np.mean(hs, axis=0) if hs else _np.zeros(192, dtype=_np.float32)
    m = (m / (_np.linalg.norm(m) or 1.0)).astype(_np.float32)
    c.execute("UPDATE voces SET huella = ?, n = ?, actualizado = ? WHERE id = ?", (m.tobytes(), len(hs), ahora(), pid))


def emparejar(rid, auto=False):
    """Para cada voz sin nombre de la reunión, busca la persona más parecida. Con similitud alta, margen sobre la
    segunda y emparejamiento mutuo (esa persona no se parece más a otra voz de la misma reunión) la asigna sola si
    `auto`; si no, o con similitud media, la sugiere. Nunca toca un nombre puesto a mano, no propone a alguien que
    ya tiene voz en la reunión, y una sugerencia que dejó de valer se borra."""
    personas = [(f["id"], _huella(f["huella"])) for f in conexion().execute("SELECT id, huella FROM voces WHERE n > 0")]
    filas = conexion().execute("SELECT etiqueta, huella, voz_id, origen, rechazadas FROM voces_reunion WHERE reunion_id = ?",
                               (rid,)).fetchall()
    if not filas:
        return
    ya_estan = {f["voz_id"] for f in filas if f["origen"] == "manual"}
    sims = {f["etiqueta"]: {pid: float(_np.dot(_huella(f["huella"]), h)) for pid, h in personas} for f in filas if f["huella"]}
    for f in filas:
        if f["origen"] == "manual":
            continue
        rechazadas = set(json.loads(f["rechazadas"])) | ya_estan
        orden = sorted(((s, pid) for pid, s in sims.get(f["etiqueta"], {}).items() if pid not in rechazadas), reverse=True)
        mejor, pid = orden[0] if orden else (-1.0, None)
        segunda = orden[1][0] if len(orden) > 1 else -1.0
        mutua = pid is not None and all(sims[e].get(pid, -1) <= mejor for e in sims if e != f["etiqueta"])
        if auto and mejor >= SIMILITUD_ALTA and mejor - segunda >= MARGEN and mutua:
            conexion().execute("UPDATE voces_reunion SET voz_id = ?, origen = 'auto', sugerida_id = NULL, similitud = ? "
                               "WHERE reunion_id = ? AND etiqueta = ?", (pid, mejor, rid, f["etiqueta"]))
        elif mejor >= SIMILITUD_BAJA and mutua:
            conexion().execute("UPDATE voces_reunion SET sugerida_id = ?, similitud = ? WHERE reunion_id = ? AND etiqueta = ? "
                               "AND voz_id IS NULL", (pid, mejor, rid, f["etiqueta"]))
        else:  # la persona que se sugería ya no es la mejor opción (o ya se nombró en esta reunión)
            conexion().execute("UPDATE voces_reunion SET sugerida_id = NULL, similitud = NULL, "
                               "voz_id = CASE WHEN origen = 'auto' THEN NULL ELSE voz_id END, "
                               "origen = CASE WHEN origen = 'auto' THEN NULL ELSE origen END "
                               "WHERE reunion_id = ? AND etiqueta = ?", (rid, f["etiqueta"]))


def nombrar_voz(rid, etiqueta, nombre, auto=False):
    """El usuario dice quién es esa voz. La huella de la persona se recalcula con esta y se re-emparejan todas las
    reuniones con voces sin nombre, esta incluida (las que se le parezcan pasan a sugerencia, o a nombre si `auto`)."""
    fila = conexion().execute("SELECT voz_id, origen FROM voces_reunion WHERE reunion_id = ? AND etiqueta = ?",
                              (rid, etiqueta)).fetchone()
    if not fila:
        return None
    nombre = nombre.strip()
    persona = conexion().execute("SELECT id FROM voces WHERE nombre = ? COLLATE NOCASE", (nombre,)).fetchone()
    t = ahora()
    with transaccion() as c:
        if persona:
            pid = persona["id"]
        else:
            pid = c.execute("INSERT INTO voces(nombre, huella, n, creado, actualizado) VALUES (?, ?, 0, ?, ?)",
                            (nombre, _np.zeros(192, dtype=_np.float32).tobytes(), t, t)).lastrowid
        c.execute("UPDATE voces_reunion SET voz_id = ?, origen = 'manual', sugerida_id = NULL, similitud = NULL "
                  "WHERE reunion_id = ? AND etiqueta = ?", (pid, rid, etiqueta))
        _recalcular_huella(c, pid)
        if fila["voz_id"] and fila["voz_id"] != pid and fila["origen"] == "manual":
            _recalcular_huella(c, fila["voz_id"])
    _reemparejar(auto)
    return pid


def _reemparejar(auto):
    for f in conexion().execute("SELECT DISTINCT reunion_id FROM voces_reunion WHERE voz_id IS NULL OR origen = 'auto'").fetchall():
        emparejar(f["reunion_id"], auto=auto)


def rechazar_sugerencia(rid, etiqueta):
    f = conexion().execute("SELECT sugerida_id, rechazadas FROM voces_reunion WHERE reunion_id = ? AND etiqueta = ?",
                           (rid, etiqueta)).fetchone()
    if not f or not f["sugerida_id"]:
        return
    rechazadas = sorted(set(json.loads(f["rechazadas"])) | {f["sugerida_id"]})
    conexion().execute("UPDATE voces_reunion SET sugerida_id = NULL, similitud = NULL, rechazadas = ? WHERE reunion_id = ? AND etiqueta = ?",
                       (json.dumps(rechazadas), rid, etiqueta))


def quitar_nombre_voz(rid, etiqueta):
    """Quitar un nombre es también decir «no es esa persona»: queda rechazada para esa voz, así Minuta no lo vuelve
    a poner, y la voz sale de la huella de la persona."""
    with transaccion() as c:
        f = c.execute("SELECT voz_id, origen, rechazadas FROM voces_reunion WHERE reunion_id = ? AND etiqueta = ?",
                      (rid, etiqueta)).fetchone()
        if not f or not f["voz_id"]:
            return
        rechazadas = sorted(set(json.loads(f["rechazadas"])) | {f["voz_id"]})
        c.execute("UPDATE voces_reunion SET voz_id = NULL, origen = NULL, rechazadas = ? WHERE reunion_id = ? AND etiqueta = ?",
                  (json.dumps(rechazadas), rid, etiqueta))
        if f["origen"] == "manual":
            _recalcular_huella(c, f["voz_id"])


def personas():
    return [dict(f) for f in conexion().execute(
        """SELECT v.id, v.nombre, v.n, COUNT(DISTINCT vr.reunion_id) AS reuniones FROM voces v
           LEFT JOIN voces_reunion vr ON vr.voz_id = v.id GROUP BY v.id ORDER BY v.nombre COLLATE NOCASE""")]


def olvidar_persona(pid):
    """Borra la huella de una persona y su nombre en todas las reuniones (los turnos vuelven a «Ellos»). La voz de
    cada reunión queda, sin nombre: es la del audio de esa reunión, no la de la persona."""
    with transaccion() as c:
        n = c.execute("DELETE FROM voces WHERE id = ?", (pid,)).rowcount
        if not n:
            return False
        c.execute("UPDATE voces_reunion SET voz_id = NULL, origen = NULL WHERE voz_id = ?", (pid,))
        c.execute("UPDATE voces_reunion SET sugerida_id = NULL, similitud = NULL WHERE sugerida_id = ?", (pid,))
        for f in c.execute("SELECT reunion_id, etiqueta, rechazadas FROM voces_reunion WHERE rechazadas LIKE ?",
                           (f"%{pid}%",)).fetchall():
            quedan = [x for x in json.loads(f["rechazadas"]) if x != pid]
            c.execute("UPDATE voces_reunion SET rechazadas = ? WHERE reunion_id = ? AND etiqueta = ?",
                      (json.dumps(quedan), f["reunion_id"], f["etiqueta"]))
    return True


def aplicar_voces(rid, segmentos):
    """Guarda el reparto de la diarización sin cambiar los ids de los turnos: la app puede tener abierta una edición,
    un «Deshacer» o un borrado diferido con ellos. Un turno partido conserva su id en el primer pedazo; los otros
    pedazos son turnos nuevos. Un turno que se borró mientras corría la diarización no vuelve."""
    with transaccion() as c:
        existentes = {f["id"] for f in c.execute("SELECT id FROM segmentos WHERE reunion_id = ?", (rid,))}
        orden = 0
        for s in segmentos:
            if s.get("de", s.get("id")) not in existentes:
                continue
            pal = json.dumps(s["palabras"], ensure_ascii=False) if s.get("palabras") else None
            if s.get("id") is not None and s.get("partido"):
                c.execute("UPDATE segmentos SET orden = ?, inicio = ?, fin = ?, texto = ?, palabras = ?, voz = ? WHERE id = ?",
                          (orden, s.get("inicio"), s.get("fin"), s["texto"], pal, s.get("voz"), s["id"]))
            elif s.get("id") is not None:  # sin partir: el texto queda como está (puede tener una corrección a mano)
                c.execute("UPDATE segmentos SET orden = ?, voz = ? WHERE id = ?", (orden, s.get("voz"), s["id"]))
            else:
                c.execute("INSERT INTO segmentos(reunion_id, orden, canal, inicio, fin, texto, palabras, voz) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (rid, orden, s["canal"], s.get("inicio"), s.get("fin"), s["texto"], pal, s.get("voz")))
            orden += 1


def cambiar_voz(rid, ids, etiqueta):
    """«Cambiar hablante»: los turnos elegidos pasan a otra voz de la reunión (o a una nueva)."""
    marcadores = ",".join("?" * len(ids))
    n = conexion().execute(f"UPDATE segmentos SET voz = ? WHERE reunion_id = ? AND canal = 'sistema' AND id IN ({marcadores})",
                           (etiqueta, rid, *ids)).rowcount
    if n and not conexion().execute("SELECT 1 FROM voces_reunion WHERE reunion_id = ? AND etiqueta = ?", (rid, etiqueta)).fetchone():
        conexion().execute("INSERT INTO voces_reunion(reunion_id, etiqueta, segundos) VALUES (?, ?, 0)", (rid, etiqueta))
    if n:
        _marcar_editada(conexion(), rid)
    return n


def nombres_de_turnos(r, base):
    """Nombre de quien habla en cada turno. `base`: los nombres por canal ({"mic": "Vos", "sistema": "Ellos"}).
    Dentro de «Ellos», la persona si se sabe; si hay varias voces sin nombre, «Ellos (V2)»."""
    voces = {v["etiqueta"]: v for v in r.get("voces") or []}
    varias = len(voces) > 1
    salida = []
    for s in r["segmentos"]:
        v = voces.get(s.get("voz")) if s.get("voz") else None
        if v and v["nombre"]:
            salida.append(v["nombre"])
        elif v and varias:
            salida.append(f"{base.get(s['canal'], s['canal'])} ({s['voz']})")
        else:
            salida.append(base.get(s["canal"], s["canal"]))
    return salida


# --- resumen y acciones (inteligencia/resumen.py) -------------------------------------------------------------

def guardar_resumen(rid, resumen, propuestas, editada_en=None):
    """Guarda el resumen de Minuta y sus acciones. Al regenerar, las que el usuario ya aceptó o descartó quedan;
    las propuestas viejas se reemplazan, y una nueva que repite una ya decidida no se vuelve a proponer.
    Devuelve False sin guardar si la reunión se borró o se editó mientras Claude resumía (`editada_en` es el que
    tenía al leerla): un tramo borrado no puede volver por el resumen."""
    t = ahora()
    with transaccion() as c:
        f = c.execute("SELECT editada_en FROM reuniones WHERE id = ?", (rid,)).fetchone()
        if not f or f["editada_en"] != editada_en:
            return False
        c.execute("UPDATE reuniones SET resumen_minuta = ?, actualizado = ? WHERE id = ?",
                  (json.dumps(resumen, ensure_ascii=False), t, rid))
        c.execute("DELETE FROM acciones WHERE reunion_id = ? AND estado = 'propuesta'", (rid,))
        decididas = [_norma_accion(f["texto"]) for f in c.execute("SELECT texto FROM acciones WHERE reunion_id = ?", (rid,))]
        for a in propuestas:
            n = _norma_accion(a["texto"])
            if any(difflib.SequenceMatcher(None, n, d).ratio() >= 0.85 for d in decididas):
                continue
            decididas.append(n)  # tampoco dos casi iguales en la misma respuesta
            c.execute("INSERT INTO acciones(reunion_id, texto, responsable, t, estado, creado, actualizado) "
                      "VALUES (?, ?, ?, ?, 'propuesta', ?, ?)", (rid, a["texto"], a.get("responsable"), a.get("t"), t, t))
    return True


def _norma_accion(texto):
    t = "".join(ch for ch in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^\w\s]", " ", t).split())


def acciones(reunion_id=None, estado=None, desde=None, limite=500):
    sql = ("SELECT a.*, r.titulo, r.carpeta, r.inicio AS fecha FROM acciones a JOIN reuniones r ON r.id = a.reunion_id "
           "WHERE 1=1")
    args = []
    if reunion_id:
        sql += " AND a.reunion_id = ?"; args.append(reunion_id)
    if estado:
        sql += " AND a.estado = ?"; args.append(estado)
    if desde:
        sql += " AND r.inicio >= ?"; args.append(desde)
    sql += " ORDER BY r.inicio DESC, a.t, a.id LIMIT ?"; args.append(limite)
    return [dict(f) for f in conexion().execute(sql, args)]


def accion(aid):
    f = conexion().execute("SELECT * FROM acciones WHERE id = ?", (aid,)).fetchone()
    return dict(f) if f else None


def cambiar_accion(aid, **campos):
    campos = {k: v for k, v in campos.items() if k in ("estado", "texto", "responsable")}
    if not campos:
        return accion(aid)
    campos["actualizado"] = ahora()
    conexion().execute(f"UPDATE acciones SET {', '.join(f'{k} = ?' for k in campos)} WHERE id = ?", (*campos.values(), aid))
    return accion(aid)


# --- ediciones que propone Claude por MCP ---------------------------------------------------------------------

def ediciones_pendientes(rid):
    return [dict(f) for f in conexion().execute(
        """SELECT e.*, s.inicio, s.canal FROM ediciones_pendientes e LEFT JOIN segmentos s ON s.id = e.segmento_id
           WHERE e.reunion_id = ? ORDER BY s.orden, e.id""", (rid,))]


def proponer_edicion(c, rid, sid, propuesto, motivo=None):
    """La escribe el MCP con su propia conexión (`c`): es lo único que escribe, y no toca la transcripción."""
    s = c.execute("SELECT texto FROM segmentos WHERE id = ? AND reunion_id = ?", (sid, rid)).fetchone()
    if not s:
        raise ValueError("no existe ese turno en esa reunión")
    if s["texto"] == propuesto:
        raise ValueError("el texto propuesto es igual al actual")
    c.execute("DELETE FROM ediciones_pendientes WHERE segmento_id = ?", (sid,))  # la última propuesta reemplaza a la anterior
    return c.execute("INSERT INTO ediciones_pendientes(reunion_id, segmento_id, original, propuesto, motivo, creado) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (rid, sid, s["texto"], propuesto, motivo, ahora())).lastrowid


def edicion(eid):
    f = conexion().execute("SELECT * FROM ediciones_pendientes WHERE id = ?", (eid,)).fetchone()
    return dict(f) if f else None


def descartar_edicion(eid):
    conexion().execute("DELETE FROM ediciones_pendientes WHERE id = ?", (eid,))
