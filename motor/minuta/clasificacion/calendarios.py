"""Le pone título, participantes y carpeta a una grabación, cruzando su horario con los calendarios.

Los calendarios se leen de las URL iCal privadas guardadas en el Administrador de credenciales (`secretos`) y se
cachean (se refrescan cada 6 h; si la red falla, sirve la copia anterior). Si una grabación no coincide con ningún
evento, se vuelve a bajar una vez la copia que tenga más de 10 min: una invitación mandada el mismo día puede no
estar en la copia de la mañana. Las reuniones recurrentes vienen como regla y se expanden con
`recurring_ical_events`. Sin evento que coincida, se usa el título de ventana de Chrome si muestra una reunión.

Las carpetas son las de Granola (`folders` en granola-archivo), para que los corpus las sigan reconociendo.
"""
import os
import re
import threading
import time
import urllib.request
from datetime import datetime, timedelta

import icalendar
import recurring_ical_events

from minuta import config, secretos

REFRESCO_S = 6 * 3600
FORZAR_DESPUES_S = 600  # sin evento que coincida, se rebaja la copia si tiene más que esto
# Tus propios correos (los tuyos en las invitaciones), separados por coma: MINUTA_MIS_CORREOS=yo@empresa.com,yo@otra.com
YO = tuple(m.strip().lower() for m in os.environ.get("MINUTA_MIS_CORREOS", "").split(",") if m.strip())
SOLAPE_MINIMO = 0.3  # fracción de la grabación que tiene que cubrir el evento

# Orden = prioridad. Se evalúan contra título + calendario de origen + dominios de los participantes.
# Son un ejemplo con empresas ficticias: adaptalas a tus carpetas.
REGLAS = [
    ("Contoso Fabrikam", r"contoso|fabrikam|initech|northwind_outlook"),
    ("Globex", r"globex|hooli|plugins?|ledger|ordersapp|@globex\.example"),
    ("Orbit", r"orbit|cpx|colegio|txt"),
    ("Seguimiento Northwind", r"seguimiento|northwind|ventas|@northwind\.example"),
    ("Facultad", r"facultad|c[aá]tedra|clase\b|proyecto final"),
    ("Acme", r"acme(?!\.example)|all hands|workshop|lab session"),
]
# Teams web pone su sección en el título ("(1) Chat | Fulano | Microsoft Teams"): chat, actividad o calendario no
# son una reunión en curso. La excepción es una llamada desde el chat, que Teams muestra en la vista compacta
# ("Chat | Vista compacta de la reunión | Fulano, Mengano | Microsoft Teams"). Meet usa "Meet - X" o "Meet: X" (28/10).
VISTA_COMPACTA = r"(\(\d+\) )?Chat \| Vista compacta de la reunión \| "
VENTANA_REUNION = re.compile(
    rf"^(Meet( -|:) .+|{VISTA_COMPACTA}.+\| Microsoft Teams"
    r"|(?!(\(\d+\) )?(Chat|Actividad|Calendario|Activity|Calendar|Archivos|Files|Teams y canales)\b)"
    r".+\| Microsoft Teams|Reunión.+Teams.*|.+ - Zoom)", re.I)


def carpeta_para(texto):
    t = texto.lower()
    return next((nombre for nombre, patron in REGLAS if re.search(patron, t)), None)


def _ics(nombre, url, forzar=False):
    config.CACHE_CALENDARIOS.mkdir(parents=True, exist_ok=True)
    ruta = config.CACHE_CALENDARIOS / f"{nombre}.ics"
    edad = time.time() - ruta.stat().st_mtime if ruta.exists() else None
    vencido = edad is None or edad > (FORZAR_DESPUES_S if forzar else REFRESCO_S)
    # Sin red se sirve la copia vieja, y no se reintenta en cada vuelta de la agenda (cada 30 s): cada intento puede
    # bloquear hasta 30 s.
    espera = REINTENTO_S if nombre in _fallo else 0  # la espera es entre fallos de red, no tras una bajada buena
    if vencido and (not ruta.exists() or time.time() - _intento.get(nombre, 0) > espera):
        _intento[nombre] = time.time()
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                ruta.write_bytes(r.read())
            _fallo.discard(nombre)
        except OSError:
            _fallo.add(nombre)
    if not ruta.exists():
        return None
    # La agenda del brief pregunta cada 30 s: se guarda la consulta ya armada (armarla recorre todos los eventos, unos
    # 180 ms con 3000) hasta que cambie el archivo.
    clave = (ruta, ruta.stat().st_mtime)
    with _candado:
        if _parseados.get(nombre, (None,))[0] != clave:
            _parseados[nombre] = (clave, recurring_ical_events.of(icalendar.Calendar.from_ical(ruta.read_bytes())))
        return _parseados[nombre][1]


REINTENTO_S = 900
_parseados, _intento, _fallo = {}, {}, set()
_candado = threading.Lock()


def _a_local(dt):
    if not isinstance(dt, datetime):  # evento de día completo
        return None
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


def _participantes(ev):
    gente = ev.get("ATTENDEE") or []
    if not isinstance(gente, list):
        gente = [gente]
    salida = []
    for a in gente:
        mail = str(a).removeprefix("mailto:").removeprefix("MAILTO:")
        nombre = a.params.get("CN", mail)
        if nombre == mail:  # Google manda el correo como nombre: nombre.apellido@ alcanza para leerlo
            nombre = " ".join(p.capitalize() for p in re.split(r"[._]", mail.split("@")[0]) if p)
        salida.append({"mail": mail, "nombre": nombre, "estado": a.params.get("PARTSTAT", "")})
    return salida


def eventos(inicio, fin, forzar=False):
    """Eventos de todos los calendarios que se superponen con [inicio, fin]. `forzar`: rebajar las copias con más de
    10 min (respeta la espera entre reintentos si la red falló)."""
    hallados = []
    for nombre, url in secretos.calendarios().items():
        consulta = _ics(nombre, url, forzar) if url else None
        if consulta is None:
            continue
        for ev in consulta.between(inicio - timedelta(hours=1), fin + timedelta(hours=1)):
            ini, fn = _a_local(ev.get("DTSTART").dt), _a_local(ev.get("DTEND").dt if ev.get("DTEND") else None)
            if not ini or not fn:
                continue
            gente = _participantes(ev)
            if any(p["mail"].lower() in YO and p["estado"].upper() == "DECLINED" for p in gente):
                continue
            solape = (min(fin, fn) - max(inicio, ini)).total_seconds()
            if solape > 0:
                hallados.append({"titulo": str(ev.get("SUMMARY", "")).strip(), "inicio": ini, "fin": fn,
                                 "calendario": nombre, "participantes": gente, "solape_s": solape})
    return hallados


def _vistas(titulo, titulos_ventana):
    """Cuántas veces se vio el título del evento en una ventana: con dos eventos a la misma hora, desempata.
    Sin signos, porque Teams cambia «Sync: Archivos Altas» por «Sync_ Archivos Altas»."""
    limpio = lambda s: " ".join(re.split(r"[\W_]+", s.casefold())).strip()
    t = limpio(titulo)
    return sum(n for v, n in titulos_ventana.items() if t and t in limpio(v))


def clasificar(inicio, fin, titulos_ventana, apps=()):
    """Devuelve {titulo, participantes, folders, origen} o None si no hay nada que la identifique como reunión."""
    dur = (fin - inicio).total_seconds()
    candidatos = [e for e in eventos(inicio, fin) if e["solape_s"] >= SOLAPE_MINIMO * dur]
    if not candidatos:  # una invitación del mismo día puede no estar en la copia
        candidatos = [e for e in eventos(inicio, fin, forzar=True) if e["solape_s"] >= SOLAPE_MINIMO * dur]
    if candidatos:
        e = max(candidatos, key=lambda e: (_vistas(e["titulo"], titulos_ventana), e["solape_s"]))
        dominios = " ".join(p["mail"] for p in e["participantes"] if p["mail"].lower() not in YO)
        carpeta = carpeta_para(f"{e['titulo']} {e['calendario']} {dominios}")
        return {"titulo": e["titulo"], "participantes": e["participantes"], "folders": [carpeta] if carpeta else [],
                "origen": f"calendario {e['calendario']}"}
    if "slack.exe" in apps:
        # Las llamadas de Slack no están en el calendario: la ventana dice con quién o en qué canal.
        # Títulos como "Pedro Ríos (Mensajes directos) - Acme - Slack [Principal] 🏠🔊": se suman por persona o
        # canal. El 🔊 marca dónde está el huddle; si aparece, manda sobre la ventana más vista.
        slack = {t: n for t, n in titulos_ventana.items() if " - Slack" in t}
        con_huddle = {t: n for t, n in slack.items() if "🔊" in t}
        conteo = {}
        for t, n in (con_huddle or slack).items():
            limpio = re.sub(r"\s*\((Canal|Channel|DM|MD|Mensajes directos|Direct messages)\)"
                            r"| - \d+ elementos? nuevos?| - Slack.*$", "", t).strip(" -")
            conteo[limpio] = conteo.get(limpio, 0) + n
        lugar = max(conteo, key=conteo.get) if conteo else ""
        partes = [p.strip() for p in lugar.split(" - ") if p.strip()]
        titulo = "Huddle de Slack" + (f": {partes[0]}" if partes else "")
        carpeta = carpeta_para(partes[0]) if partes else None  # el workspace (partes[1]) no dice de qué frente es
        return {"titulo": titulo, "participantes": [], "folders": [carpeta] if carpeta else [], "origen": "slack"}
    # Cuántas veces se vio cada pestaña, sin el navegador: la misma reunión en Chrome y en Edge suma junta.
    vistas = {}
    for t in titulos_ventana:
        limpio = t.removesuffix(" - Google Chrome").removesuffix(" - Microsoft Edge")
        if VENTANA_REUNION.match(limpio):
            n = titulos_ventana.get(t, 0) if isinstance(titulos_ventana, dict) else 0
            vistas[limpio] = vistas.get(limpio, 0) + n
    if vistas:
        titulo = max(vistas, key=vistas.get)
        titulo = re.sub(r"^Meet( -|:) |\s*\| Microsoft Teams$", "", titulo)
        titulo = re.sub(f"^{VISTA_COMPACTA}", "Llamada de Teams: ", titulo, flags=re.I)
        # Teams web antepone la sección desde la que se entró a la reunión: «Equipos y canales | Sync Altas».
        titulo = re.sub(r"^(\(\d+\) )?(Equipos y canales|Teams y canales|Teams and channels) \| ", "", titulo)
        carpeta = carpeta_para(titulo)
        return {"titulo": titulo, "participantes": [], "folders": [carpeta] if carpeta else [], "origen": "ventana"}
    return None
