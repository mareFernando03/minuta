"""Palabras de un turno: `[[texto, inicio_s, fin_s, probabilidad | None], ...]`, siempre 1 a 1 con `texto.split()`.

Sirven para resaltar lo que suena palabra por palabra, saltar al audio desde una palabra y marcar las dudosas. El
texto de un turno cambia después de transcribir (limpieza de repeticiones, corrección del glosario, edición a mano):
`alinear` reacomoda las palabras al texto nuevo. Las que coinciden conservan sus tiempos y su probabilidad; las
cambiadas o agregadas se reparten el tiempo de lo que reemplazan y quedan con probabilidad None (las tocó una
corrección, no tienen por qué marcarse como dudosas).
"""
import difflib
import re


def _norma(w):
    return re.sub(r"[^\w]", "", w.lower())


def alinear(texto, palabras):
    if not palabras:
        return None
    nuevos = texto.split()
    if not nuevos:
        return []
    viejos = [_norma(p[0]) for p in palabras]
    sm = difflib.SequenceMatcher(a=viejos, b=[_norma(w) for w in nuevos], autojunk=False)
    salida = [None] * len(nuevos)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(j2 - j1):
                o = palabras[i1 + k]
                salida[j1 + k] = [nuevos[j1 + k], o[1], o[2], o[3]]
        elif op in ("replace", "insert"):
            if op == "replace":
                ini, fin = palabras[i1][1], palabras[i2 - 1][2]
            else:  # entre la palabra anterior y la siguiente
                ini = palabras[i1 - 1][2] if i1 > 0 else palabras[0][1]
                fin = palabras[i1][1] if i1 < len(palabras) else palabras[-1][2]
            n = j2 - j1
            paso = max(fin - ini, 0) / n
            for k in range(n):
                salida[j1 + k] = [nuevos[j1 + k], round(ini + k * paso, 2), round(ini + (k + 1) * paso, 2), None]
    return salida


def unir(a, b):
    """Palabras de dos frases que se unen en un turno. Si alguna no tiene, el turno tampoco."""
    return a + b if a is not None and b is not None else None


def a_tiempo(palabras, convertir):
    """Pasa los tiempos de cada palabra por `convertir` (p. ej., del audio recortado por el VAD al real)."""
    if palabras is None:
        return None
    return [[w[0], convertir(w[1]), convertir(w[2]), w[3]] for w in palabras]
