"""Mezcla de las dos pistas (mic + sistema) para el reproductor de la app, en Opus.

Medido el 9/10/2026 con una reunión de 12,5 min: WAV 24 MB (el reproductor tardaba segundos en leer los metadatos);
Opus 32 kb/s, perfil voz, complejidad 0: 1,4 MB en 1,9 s. AAC tardó 14,7 s y MP3 8,1 s. Se genera al archivar la
reunión, así el primer play es instantáneo.
"""
from pathlib import Path

import time

import av
import numpy as np

from minuta.transcripcion.local import SR, leer_pista

NOMBRE = "mezcla.ogg"


def generar(carpeta):
    carpeta = Path(carpeta)
    destino = carpeta / NOMBRE
    mic, sistema = leer_pista(carpeta, "mic"), leer_pista(carpeta, "sistema")
    n = max(len(mic), len(sistema))
    if n == 0:
        return None
    mezcla = np.zeros(n, dtype=np.float32)
    mezcla[:len(mic)] += mic
    mezcla[:len(sistema)] += sistema
    mezcla /= max(float(np.max(np.abs(mezcla))), 1.0)
    pcm = (mezcla * 32767).astype(np.int16)
    temporal = destino.with_suffix(".tmp")
    with av.open(str(temporal), "w", format="ogg") as c:
        s = c.add_stream("libopus", rate=SR, options={"compression_level": "0", "application": "voip", "frame_duration": "60"})
        s.bit_rate, s.layout = 32000, "mono"
        for i in range(0, n, SR * 30):
            frame = av.AudioFrame.from_ndarray(pcm[i:i + SR * 30].reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate = SR
            for p in s.encode(frame):
                c.mux(p)
        for p in s.encode(None):
            c.mux(p)
    # En Windows el reemplazo falla mientras el reproductor tiene abierta la mezcla vieja: se reintenta un momento.
    for intento in range(10):
        try:
            temporal.replace(destino)
            break
        except PermissionError:
            if intento == 9:
                raise
            time.sleep(0.3)
    return destino
