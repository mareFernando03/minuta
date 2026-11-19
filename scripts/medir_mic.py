"""TASK-47: medir el canal del micrófono antes de filtrarlo.

    motor/.venv/Scripts/python.exe scripts/medir_mic.py <carpeta de la grabación> [<otra> ...] [--datos <MINUTA_DATOS>]

Por cada turno del micrófono («Vos») de la reunión ya transcripta muestra:
- la energía del mic en ese tramo, en dB respecto de la voz del propio mic en la reunión (su percentil 95);
- la energía del sistema en el mismo tramo (si suena la llamada, lo del mic puede ser fuga de los auriculares);
- la probabilidad media de las palabras según el motor (None si el motor no la da);
- el texto.

Se anota a mano qué turnos son del usuario y cuáles basura (alucinación, fuga, charla de la oficina), y de ahí salen
los umbrales del filtro de `transcripcion/local.py`. Lee la base en solo lectura: se puede correr con Minuta abierta.
"""
import argparse
import json
import os
import sqlite3
from pathlib import Path

import numpy as np

SR = 16000
TRAMO_S = 0.5


def db_solo_lectura(datos):
    c = sqlite3.connect(f"file:{Path(datos) / 'minuta.db'}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def rms(audio, ini, fin):
    x = audio[int(ini * SR): int(fin * SR)]
    return float(np.sqrt(np.mean(x * x))) if len(x) else 0.0


def referencia_de_voz(audio):
    """Percentil 95 de la energía en tramos de medio segundo: lo que suena la voz más cercana al mic."""
    h = int(TRAMO_S * SR)
    k = len(audio) // h
    tramos = np.sqrt((audio[: k * h].reshape(k, h) ** 2).mean(1))
    return float(np.percentile(tramos, 95)) if k else 0.0


def pico_de_voz(audio, ini, fin):
    """Percentil 90 de la energía en tramos de 0,1 s dentro del turno: un turno largo con pausas no se diluye."""
    x = audio[int(ini * SR): int(fin * SR)]
    h = SR // 10
    k = len(x) // h
    if not k:
        return rms(audio, ini, fin)
    return float(np.percentile(np.sqrt((x[: k * h].reshape(k, h) ** 2).mean(1)), 90))


def db_rel(x, ref):
    return 20 * np.log10(max(x, 1e-6) / max(ref, 1e-6))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("carpetas", type=Path, nargs="+")
    ap.add_argument("--datos", default=str(Path(os.environ["LOCALAPPDATA"]) / "ar.fmare.minuta"))
    a = ap.parse_args()
    os.environ["MINUTA_DATOS"] = a.datos
    from minuta.transcripcion import local

    c = db_solo_lectura(a.datos)
    for carpeta in a.carpetas:
        g = c.execute("SELECT * FROM grabaciones WHERE id = ?", (carpeta.name,)).fetchone()
        if not g or not g["reunion_id"]:
            print(f"{carpeta.name}: sin reunión transcripta")
            continue
        r = c.execute("SELECT titulo, motor FROM reuniones WHERE id = ?", (g["reunion_id"],)).fetchone()
        mic = local.leer_pista(carpeta, "mic", g["corte_s"])
        sistema = local.leer_pista(carpeta, "sistema", g["corte_s"])
        ref = referencia_de_voz(mic)
        print(f"\n{carpeta.name} · {r['titulo']} · motor {r['motor']} · voz del mic (p95) {ref:.4f}")
        print("  #   minuto  dur  mic dB  pico   p90abs  sistema  prob  texto")
        segs = c.execute("SELECT inicio, fin, texto, palabras FROM segmentos WHERE reunion_id = ? AND canal = 'mic' "
                         "ORDER BY orden", (g["reunion_id"],)).fetchall()
        for n, s in enumerate(segs, 1):
            ini, fin = s["inicio"] or 0.0, s["fin"] or (s["inicio"] or 0.0) + 2
            probs = [p[3] for p in json.loads(s["palabras"] or "[]") if p[3] is not None]
            prob = f"{np.mean(probs):.2f}" if probs else "  - "
            print(f"  {n:<3} {int(ini // 60):02d}:{int(ini % 60):02d}  {fin - ini:4.1f}  {db_rel(rms(mic, ini, fin), ref):6.1f}"
                  f"  {db_rel(pico_de_voz(mic, ini, fin), ref):5.1f}  {pico_de_voz(mic, ini, fin):.4f}"
                  f"  {rms(sistema, ini, fin):7.4f}  {prob}  {s['texto'][:80]}")


if __name__ == "__main__":
    main()
