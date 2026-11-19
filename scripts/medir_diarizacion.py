"""TASK-25: medir la diarización del canal «Ellos» antes de construirla.

    motor/.venv/Scripts/python.exe scripts/medir_diarizacion.py <carpeta de la grabación> --modelos <carpeta>
        [--umbral 0.5] [--hablantes N] [--datos <MINUTA_DATOS>]

Corre sherpa-onnx (segmentación pyannote 3.0 + huella CAM++) sobre la pista del sistema y muestra: cuánto tardó
(factor de tiempo real), cuántas voces encontró, cuánto habló cada una y, si la grabación ya es reunión, los
turnos de «Ellos» con la voz asignada, para anotar a mano quién dijo qué y contar los errores.

El criterio del plan (docs/plan-v1.1.md): si el error de asignación supera el 20 %, Minuta solo sugiere voces y no
etiqueta sola.
"""
import argparse
import os
import sys
import time
from collections import Counter
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("carpeta", type=Path)
    ap.add_argument("--modelos", type=Path, required=True)
    ap.add_argument("--umbral", type=float, default=0.5)
    ap.add_argument("--hablantes", type=int, default=-1)
    ap.add_argument("--datos")
    a = ap.parse_args()
    if a.datos:
        os.environ["MINUTA_DATOS"] = a.datos

    import sherpa_onnx as so
    from minuta.datos import db
    from minuta.transcripcion import local

    gid = a.carpeta.name
    g = db.grabacion(gid) if a.datos else None
    audio = local.leer_pista(a.carpeta, "sistema", (g or {}).get("corte_s"))
    config = so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(a.modelos / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx")),
            num_threads=4),
        embedding=so.SpeakerEmbeddingExtractorConfig(model=str(a.modelos / "campplus.onnx"), num_threads=4),
        clustering=so.FastClusteringConfig(num_clusters=a.hablantes, threshold=a.umbral),
        min_duration_on=0.3, min_duration_off=0.5)
    if not config.validate():
        sys.exit("configuración inválida: revisá las rutas de los modelos")
    d = so.OfflineSpeakerDiarization(config)
    t0 = time.perf_counter()
    tramos = d.process(audio).sort_by_start_time()
    seg = time.perf_counter() - t0
    dur = len(audio) / 16000
    print(f"{gid}: {dur / 60:.1f} min de audio en {seg:.1f} s (x{dur / max(seg, 0.01):.0f} tiempo real)")
    hablado = Counter()
    for t in tramos:
        hablado[t.speaker] += t.end - t.start
    print(f"voces: {len(hablado)}  ", "  ".join(f"V{k + 1}: {v / 60:.1f} min" for k, v in sorted(hablado.items())))

    if g and g.get("reunion_id"):
        r = db.reunion(g["reunion_id"])
        print("\nTurnos de «Ellos» con la voz que más suena en cada uno (para anotar a mano):")
        for s in r["segmentos"]:
            if s["canal"] != "sistema" or s["inicio"] is None:
                continue
            fin = s["fin"] or s["inicio"] + 2
            votos = Counter()
            for t in tramos:
                solape = min(fin, t.end) - max(s["inicio"], t.start)
                if solape > 0:
                    votos[t.speaker] += solape
            voz = f"V{votos.most_common(1)[0][0] + 1}" if votos else "--"
            print(f"  [{int(s['inicio'] // 60):02d}:{int(s['inicio'] % 60):02d}] {voz}  {s['texto'][:90]}")


if __name__ == "__main__":
    main()
