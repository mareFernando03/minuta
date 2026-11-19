"""Corrige nombres propios y términos técnicos de una transcripción contra el glosario, con Claude Haiku.

Whisper no conoce los nombres del proyecto y los deforma ("consumidor" por Contoso, "upsetting" por appsettings).
Claude no reescribe la transcripción: devuelve solo pares {original, correcto} con unas palabras de contexto, y acá
se aplican literalmente. Lo que no aparezca tal cual en el texto se ignora, así que no puede inventar contenido.

Corre con la suscripción de Claude Code (`claude -p`), con un system prompt propio para gastar pocos tokens.
Medido el 9/10/2026: ~US$0,003 equivalentes por cada 6 min de reunión.
"""
import difflib
import json
import re
import subprocess

from minuta import config
from minuta.transcripcion import palabras as pal

MODELO = "haiku"

SISTEMA = """Corregís transcripciones automáticas de reuniones de trabajo en español rioplatense.
El reconocimiento de voz deforma nombres de personas, empresas, sistemas y términos técnicos.
Te doy un glosario con los términos correctos (y, después de "<-", deformaciones ya vistas) y una transcripción.

Devolvé SOLO un array JSON, sin texto alrededor, con los reemplazos necesarios:
[{"original": "...", "correcto": "..."}]

Reglas:
- "original" tiene que aparecer EXACTAMENTE así en la transcripción. Incluí 2 a 5 palabras de contexto para que sea
  inequívoco y no pise usos legítimos de la misma palabra.
- "correcto" es el mismo fragmento con solo el término corregido. No cambies nada más: ni muletillas, ni gramática,
  ni puntuación, ni el sentido.
- Corregí solo cuando el contexto deja claro que se refiere a un término del glosario. Ante la duda, no lo incluyas.
- Si no hay nada que corregir, devolvé []."""


def pedir_reemplazos(texto):
    glosario = config.glosario_terminos()
    entrada = f"<glosario>\n{glosario}\n</glosario>\n\n<transcripcion>\n{texto}\n</transcripcion>"
    r = subprocess.run(
        ["claude", "-p", "--model", MODELO, "--system-prompt", SISTEMA, "--output-format", "json",
         "--no-session-persistence", "--strict-mcp-config", "--disallowedTools", "*"],
        input=entrada, capture_output=True, text=True, encoding="utf-8", timeout=600, cwd=str(config.DATOS),
        creationflags=0x08000000)
    if r.returncode != 0:
        raise RuntimeError(f"claude -p falló: {r.stderr[:300]}")
    salida = json.loads(r.stdout)
    respuesta = salida.get("result", "").strip()
    inicio, fin = respuesta.find("["), respuesta.rfind("]")
    pares = json.loads(respuesta[inicio:fin + 1]) if inicio >= 0 else []
    return pares, salida.get("usage", {}), salida.get("total_cost_usd")


def cambio_de_palabra(o, c):
    """Si el par cambia una sola palabra ("el upsetting" -> "el appsettings"), devuelve (mala, buena)."""
    po, pc = o.split(), c.split()
    ops = [op for op in difflib.SequenceMatcher(None, po, pc, autojunk=False).get_opcodes() if op[0] != "equal"]
    if len(ops) == 1 and ops[0][0] == "replace" and ops[0][2] - ops[0][1] == 1:
        mala = re.sub(r"^\W+|\W+$", "", po[ops[0][1]])
        buena = re.sub(r"^\W+|\W+$", "", " ".join(pc[ops[0][3]:ops[0][4]]))
        if len(mala) >= 3 and mala != buena:
            return mala, buena
    return None


def aplicar(texto, pares):
    """Aplica los pares tal cual. Un cambio de una sola palabra se extiende al resto de la reunión: Claude a veces
    marca una aparición y no otra, y dentro de la misma reunión la deformación nunca es una palabra legítima."""
    aplicados = []
    for p in pares:
        o, c = p.get("original", ""), p.get("correcto", "")
        if o and c and o != c and o in texto:
            texto = texto.replace(o, c)
            aplicados.append((o, c))
            palabra = cambio_de_palabra(o, c)
            if palabra:
                patron = re.compile(rf"(?<![\w-]){re.escape(palabra[0])}(?![\w-])")
                n = len(patron.findall(texto))
                if n:
                    texto = patron.sub(palabra[1], texto)
                    aplicados.append((f"{palabra[0]} (x{n} más)", palabra[1]))
    return texto, aplicados


def corregir(texto):
    pares, uso, costo = pedir_reemplazos(texto)
    nuevo, aplicados = aplicar(texto, pares)
    return nuevo, aplicados, uso, costo


def corregir_turnos(turnos, como_texto):
    """Pide los reemplazos sobre la reunión entera (Claude necesita el contexto) y los aplica turno por turno.
    Devuelve (turnos corregidos, [(original, correcto)], costo en US$ equivalentes)."""
    pares, _, costo = pedir_reemplazos(como_texto(turnos))
    corregidos, todos = [], []
    for t in turnos:
        texto, aplicados = aplicar(t["texto"], pares)
        corregidos.append(dict(t, texto=texto, palabras=pal.alinear(texto, t.get("palabras"))))
        todos += aplicados
    return corregidos, todos, costo or 0.0


# --- Agregar lo que el usuario corrige a mano ----------------------------------------------------------------

SECCION_AGREGADOS = "## Agregados desde Minuta"


def _norma(t):
    import unicodedata
    t = unicodedata.normalize("NFD", t.lower())
    return re.sub(r"[^\w]", "", "".join(c for c in t if unicodedata.category(c) != "Mn"))


def _nombres(linea):
    """Nombres con los que una línea del glosario nombra a su término: el principal, los separados por coma y los
    alias entre paréntesis. «Pablo Sosa (Pedro)» -> {pablososa, pedro}."""
    cabeza = linea.split("<-")[0]
    alias = re.findall(r"\(([^)]*)\)", cabeza)
    base = re.sub(r"\([^)]*\)", "", cabeza)
    return {_norma(p) for t in [base, *alias] for p in t.split(",") if _norma(p)}


def agregar_deformacion(correcto, deformacion):
    """Suma `deformacion` («Contosó») a la línea del término `correcto` («Contoso») en glosario-terminos.txt, o crea
    la línea en una sección propia al final. La próxima corrección con Claude ya la conoce. Devuelve la línea."""
    ruta = config.GLOSARIO / "glosario-terminos.txt"
    lineas = ruta.read_text(encoding="utf-8").splitlines()
    objetivo, deformacion = _norma(correcto), deformacion.strip()
    for i, linea in enumerate(lineas):
        if linea.strip() and not linea.lstrip().startswith("#") and objetivo in _nombres(linea):
            if "<-" in linea:
                vistas = {_norma(d) for d in linea.split("<-", 1)[1].split(",")}
                if _norma(deformacion) not in vistas:
                    lineas[i] = f"{linea.rstrip()}, {deformacion}"
            else:
                lineas[i] = f"{linea.rstrip()} <- {deformacion}"
            break
    else:
        if SECCION_AGREGADOS not in lineas:
            lineas += ["", SECCION_AGREGADOS]
        lineas.append(f"{correcto.strip()} <- {deformacion}")
        i = len(lineas) - 1
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return lineas[i]
