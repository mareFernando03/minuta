"""Quién habla dentro de «Ellos»: diarización local del canal del sistema y huellas de voz entre reuniones.

Tu voz ya viene sola por el micrófono; lo difícil es separar a los demás, que llegan mezclados por el loopback. Se
usa sherpa-onnx con la segmentación pyannote 3.0 y la huella CAM++ (35 MB en total, CPU). Medido el 10/10/2026 con
un huddle real: 11 min de audio en 93 s (7 veces tiempo real), y el umbral de agrupamiento cambia mucho el
resultado (1,0 dio 7 voces y 1,2 dio la única que había). El umbral definitivo se fija midiendo reuniones de 3 o más
personas (scripts/medir_diarizacion.py); hasta entonces Minuta solo sugiere nombres, no etiqueta sola.

Los modelos se bajan la primera vez de los releases oficiales de k2-fsa, con el hash fijado.
"""
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

import numpy as np

from minuta import config

log = logging.getLogger("minuta.diarizacion")

SR = 16000
UMBRAL_AGRUPAR = 1.2
MIN_HUELLA_S = 3.0      # una voz con menos audio no tiene huella confiable
MAX_HUELLA_S = 40.0
PROCESO_MAX_S = 90 * 60  # una hora de reunión lleva unos 8 minutos: más que esto es un proceso trabado
SEGMENTACION = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/"
                "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
                "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488")
HUELLA = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
          "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",
          "aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2")


class SinModelos(Exception):
    """No se pudieron bajar los modelos (sin red): se reintenta más tarde."""


def carpeta_modelos():
    return config.DATOS / "modelos"


def _bajar(url, sha, destino):
    tmp = destino.with_suffix(destino.suffix + ".tmp")
    try:
        with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise SinModelos(f"no se pudo bajar {url.rsplit('/', 1)[-1]}: {e}") from e
    hecho = hashlib.sha256(tmp.read_bytes()).hexdigest()
    if hecho != sha:
        tmp.unlink()
        raise RuntimeError(f"el modelo bajado no coincide con el hash fijado ({hecho}): no se usa")
    tmp.replace(destino)


def asegurar_modelos():
    """Rutas (segmentación, huella), bajándolos si hace falta."""
    d = carpeta_modelos()
    d.mkdir(parents=True, exist_ok=True)
    segmentacion, huella = d / "pyannote-segmentation-3-0.onnx", d / "campplus.onnx"
    if not huella.exists():
        _bajar(*HUELLA, huella)
    if not segmentacion.exists():
        with tempfile.TemporaryDirectory() as tmp:
            tar = Path(tmp) / "seg.tar.bz2"
            _bajar(*SEGMENTACION, tar)
            with tarfile.open(tar) as t:
                miembro = next(m for m in t.getmembers() if m.name.endswith("/model.onnx"))
                parcial = segmentacion.with_suffix(".onnx.tmp")  # un corte a mitad no deja un modelo roto con su nombre
                with t.extractfile(miembro) as origen, open(parcial, "wb") as f:
                    shutil.copyfileobj(origen, f)
                parcial.replace(segmentacion)
    return segmentacion, huella


def diarizar(audio, umbral=UMBRAL_AGRUPAR):
    """[(inicio_s, fin_s, voz)] del audio del sistema, con las voces numeradas desde 0 por orden de aparición."""
    import sherpa_onnx as so
    if len(audio) < SR:
        return []
    segmentacion, huella = asegurar_modelos()
    config_ = so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(segmentacion)), num_threads=4),
        embedding=so.SpeakerEmbeddingExtractorConfig(model=str(huella), num_threads=4),
        clustering=so.FastClusteringConfig(num_clusters=-1, threshold=umbral),
        min_duration_on=0.3, min_duration_off=0.5)
    tramos = so.OfflineSpeakerDiarization(config_).process(np.ascontiguousarray(audio, dtype=np.float32)).sort_by_start_time()
    orden = {}
    for t in tramos:
        orden.setdefault(t.speaker, len(orden))
    return [(round(t.start, 2), round(t.end, 2), orden[t.speaker]) for t in tramos]


def huellas(audio, tramos):
    """{voz: (huella normalizada, segundos hablados)} para las voces con audio suficiente."""
    import sherpa_onnx as so
    _, huella = asegurar_modelos()
    extractor = so.SpeakerEmbeddingExtractor(so.SpeakerEmbeddingExtractorConfig(model=str(huella), num_threads=4))
    por_voz = {}
    for ini, fin, voz in tramos:
        por_voz.setdefault(voz, []).append((ini, fin))
    salida = {}
    for voz, lista in por_voz.items():
        hablado = sum(f - i for i, f in lista)
        if hablado < MIN_HUELLA_S:
            continue
        trozos, juntado = [], 0.0
        for i, f in sorted(lista, key=lambda x: x[0] - x[1]):  # los tramos más largos primero
            trozos.append(audio[int(i * SR):int(f * SR)])
            juntado += f - i
            if juntado >= MAX_HUELLA_S:
                break
        stream = extractor.create_stream()
        stream.accept_waveform(SR, np.ascontiguousarray(np.concatenate(trozos), dtype=np.float32))
        stream.input_finished()
        v = np.array(extractor.compute(stream), dtype=np.float32)
        salida[voz] = (v / (np.linalg.norm(v) or 1.0), round(hablado, 1))
    return salida


def en_proceso_aparte(carpeta, corte_s, umbral):
    """(tramos, huellas) del canal del sistema de una grabación, calculados en otro proceso (ver __main__.py): dentro
    del motor, sherpa-onnx retiene el GIL y frena al vigía. Corre con prioridad baja para no pelearle el procesador a
    una reunión."""
    with tempfile.TemporaryDirectory() as tmp:
        salida = Path(tmp) / "voces.json"
        r = subprocess.run(
            [sys.executable, "-m", "minuta.diarizacion", str(carpeta), "-" if corte_s is None else str(corte_s),
             str(umbral), str(salida)],
            env={**os.environ, "MINUTA_DATOS": str(config.DATOS)}, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=PROCESO_MAX_S,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        if r.returncode or not salida.exists():
            raise RuntimeError(f"la diarización terminó con el código {r.returncode}: {r.stderr.strip()[-500:]}")
        resultado = json.loads(salida.read_text(encoding="utf-8"))
    if "sin_modelos" in resultado:
        raise SinModelos(resultado["sin_modelos"])
    tramos = [tuple(t) for t in resultado["tramos"]]
    huellas_ = {int(v): (np.array(h, dtype=np.float32), s) for v, (h, s) in resultado["huellas"].items()}
    return tramos, huellas_


def voz_en(tramos, inicio, fin):
    """La voz que más suena entre inicio y fin, o None."""
    votos = {}
    for i, f, voz in tramos:
        solape = min(fin, f) - max(inicio, i)
        if solape > 0:
            votos[voz] = votos.get(voz, 0) + solape
    return max(votos, key=votos.get) if votos else None


def repartir(segmentos, tramos):
    """Asigna voz a cada turno de «Ellos». Si en un turno hablaron dos voces y hay palabras, se parte donde cambia la
    voz. Los turnos del micrófono quedan igual (sin voz: son «Vos»)."""
    salida = []
    for s in segmentos:
        if s["canal"] != "sistema" or s.get("inicio") is None:
            salida.append(dict(s, voz=None))
            continue
        palabras = s.get("palabras")
        if not palabras:
            v = voz_en(tramos, s["inicio"], s.get("fin") or s["inicio"] + 2)
            salida.append(dict(s, voz=_etiqueta(v)))
            continue
        # Una palabra que no cae en ningún tramo (el modelo no la tomó como voz) va con sus vecinas: la anterior, o la
        # siguiente si es el comienzo del turno. Si no, «mira,» quedaba como un turno suelto y sin voz.
        voces = [voz_en(tramos, p[1], p[2]) for p in palabras]
        primera = next((v for v in voces if v is not None), None)
        anterior = primera
        for k, v in enumerate(voces):
            voces[k] = anterior = v if v is not None else anterior
        grupos = []
        for p, v in zip(palabras, voces):
            if grupos and v == grupos[-1][0]:
                grupos[-1][1].append(p)
            else:
                grupos.append([v, [p]])
        # Un pedacito de menos de 3 palabras entre dos de la misma voz no es un cambio de voz: es ruido del modelo.
        unidos = []
        for g in grupos:
            if unidos and (len(g[1]) < 3 or g[0] == unidos[-1][0]):
                unidos[-1][1].extend(g[1])
            else:
                unidos.append(g)
        # El primer pedazo conserva el id del turno (la app puede tenerlo abierto); `de` dice de qué turno salió.
        for k, (v, ps) in enumerate(unidos):
            salida.append({**s, "id": s.get("id") if k == 0 else None, "de": s.get("id"), "partido": len(unidos) > 1,
                           "inicio": ps[0][1], "fin": ps[-1][2], "texto": " ".join(p[0] for p in ps), "palabras": ps,
                           "voz": _etiqueta(v)})
    return salida


def _etiqueta(voz):
    return None if voz is None else f"V{voz + 1}"


def similitud(a, b):
    return float(np.dot(a, b))
