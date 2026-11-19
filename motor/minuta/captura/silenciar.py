"""Silenciar un tramo de una grabación: «Borrar este tramo» saca el texto y también el audio.

Se ponen en cero las muestras de las dos pistas (mic y sistema) entre `desde_s` y `hasta_s`, en los bloques WAV de
5 minutos que lo tocan, y se regenera la mezcla del reproductor. Se silencian las dos porque el tramo puede tener
la voz de cualquiera. También se borran los resultados parciales de la transcripción local (`seg-*.json`): son una
caché para retomar, y si quedaran, el texto borrado volvería al reprocesar.
"""
import wave
from pathlib import Path

from minuta.captura import mezcla

SR = 16000
BLOQUE_S = 300
MARGEN_S = 0.3


def silenciar(carpeta, desde_s, hasta_s):
    carpeta = Path(carpeta)
    desde_s, hasta_s = max(0.0, desde_s - MARGEN_S), hasta_s + MARGEN_S
    tocados = 0
    for pista in ("mic", "sistema"):
        for wav in sorted(carpeta.glob(f"{pista}-*.wav")):
            n = int(wav.stem.split("-")[1])
            inicio_bloque = n * BLOQUE_S
            a = int((desde_s - inicio_bloque) * SR)
            b = int((hasta_s - inicio_bloque) * SR)
            with wave.open(str(wav), "rb") as w:
                parametros, cuadros = w.getparams(), w.getnframes()
            a, b = max(0, a), min(cuadros, b)
            if a >= b:
                continue  # este bloque no toca el tramo: ni se lee
            with wave.open(str(wav), "rb") as w:
                datos = bytearray(w.readframes(cuadros))
            datos[a * 2:b * 2] = bytes((b - a) * 2)
            with wave.open(str(wav), "wb") as w:
                w.setparams(parametros)
                w.writeframes(datos)
            tocados += 1
    for cache in carpeta.glob("seg-*.json"):
        cache.unlink()
    if tocados and (carpeta / mezcla.NOMBRE).exists():
        mezcla.generar(carpeta)
    return tocados
