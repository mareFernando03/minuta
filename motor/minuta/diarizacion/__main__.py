"""Diarización en un proceso aparte: `python -m minuta.diarizacion <carpeta> <corte_s|-> <umbral> <salida.json>`.

sherpa-onnx no suelta el GIL mientras procesa: dentro del motor, el 28/10/2026 una diarización de 62 min frenó al
vigía 21 minutos y se perdió el comienzo de una reunión. Acá corre solo, y el motor espera el resultado.
"""
import json
import sys
from pathlib import Path

from minuta import diarizacion
from minuta.transcripcion import local


def main(carpeta, corte_s, umbral, salida):
    audio = local.leer_pista(Path(carpeta), "sistema", None if corte_s == "-" else float(corte_s))
    try:
        tramos = diarizacion.diarizar(audio, umbral=float(umbral))
        huellas = diarizacion.huellas(audio, tramos) if tramos else {}  # sin voces no hace falta bajar la huella
    except diarizacion.SinModelos as e:
        resultado = {"sin_modelos": str(e)}
    else:
        resultado = {"tramos": tramos, "huellas": {str(v): [h.tolist(), s] for v, (h, s) in huellas.items()}}
    Path(salida).write_text(json.dumps(resultado), encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:5])
