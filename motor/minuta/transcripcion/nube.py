"""Transcripción en la nube: AssemblyAI (mejor calidad, crédito único) y Groq (Whisper large-v3, gratis 8 h por día).

Cada pista (mic, sistema) se manda por separado y SOLO con la voz: los silencios se recortan con el VAD de
faster-whisper. Eso hace que Groq no invente texto en los silencios ("www.cdc.gov.ar", "Agradecimientos a los
participantes"), y gasta la mitad de crédito y de cuota. Los tiempos se devuelven en el reloj de la grabación,
así la fusión y el filtro de eco de transcribir.py funcionan igual que con el motor local.

Cuando AssemblyAI rechaza por falta de crédito se marca como agotado y no se vuelve a intentar: todo pasa a Groq.
Medido el 9/10/2026 sobre una llamada real de 11 min: AssemblyAI 59 s, Groq 15 s, turbo local 6 min 43 s.
"""
import io
import json
import time
import urllib.error
import urllib.request
import uuid
import wave
from datetime import datetime

import numpy as np

from minuta import config, secretos
from minuta.transcripcion import palabras as pal

SR = 16000
TRAMO_GROQ_S = 600  # el plan gratis de Groq acepta archivos de hasta 25 MB: 10 min de WAV mono 16 kHz son 19 MB
PRECIO_HORA = {"assemblyai": 0.21, "groq": 0.0}  # US$ por hora de voz enviada (Universal-3.5 Pro, sin agregados)


class SinNube(Exception):
    """El motor no está disponible (sin clave, sin crédito, sin red, límite de uso)."""


def _agotado():
    return config.DATOS / "assemblyai-agotado.json"


def _pedir(url, datos=None, headers=None, timeout=900):
    # Cloudflare (Groq) rechaza el User-Agent por defecto de urllib con "error code: 1010".
    req = urllib.request.Request(url, data=datos, headers={"user-agent": "minuta/0.1", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        cuerpo = e.read()[:400].decode("utf-8", "replace")
        raise SinNube(f"HTTP {e.code}: {cuerpo}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SinNube(f"red: {e!r}") from None


def _wav(audio):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def solo_voz(pista, max_s):
    """Tramos de hasta max_s segundos con solo la voz de la pista. Cada tramo trae su mapa
    [(inicio_en_tramo, inicio_real, duración)] para devolver los tiempos al reloj de la grabación."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    partes = get_speech_timestamps(pista, VadOptions(min_silence_duration_ms=700, speech_pad_ms=300))
    pausa = np.zeros(int(0.3 * SR), dtype=np.float32)
    tramos, actual, mapa, largo = [], [], [], 0
    for p in partes:
        trozo = pista[p["start"]:p["end"]]
        if actual and largo + len(trozo) > max_s * SR:
            tramos.append((np.concatenate(actual), mapa))
            actual, mapa, largo = [], [], 0
        mapa.append((largo / SR, p["start"] / SR, len(trozo) / SR))
        actual += [trozo, pausa]
        largo += len(trozo) + len(pausa)
    if actual:
        tramos.append((np.concatenate(actual), mapa))
    return tramos


def a_tiempo_real(t, mapa):
    for ini_tramo, ini_real, dur in reversed(mapa):
        if t >= ini_tramo:
            return round(ini_real + min(t - ini_tramo, dur), 2)
    return round(t, 2)


def _frases(palabras):
    """[(texto, inicio_s, fin_s[, confianza])] -> segmentos por frase: corta tras punto final con pausa o en pausa
    larga. Cada segmento lleva sus palabras (ver transcripcion/palabras.py)."""
    segs, actual = [], []
    for w in palabras:
        if actual:
            pausa = w[1] - actual[-1][2]
            if pausa >= 1.5 or (pausa >= 0.5 and actual[-1][0].rstrip()[-1:] in ".?!"):
                segs.append(actual)
                actual = []
        actual.append(w)
    if actual:
        segs.append(actual)
    salida = []
    for s in segs:
        texto = " ".join(w[0] for w in s).strip()
        crudas = [[w[0].strip(), round(w[1], 2), round(w[2], 2), (round(w[3], 3) if len(w) > 3 and w[3] is not None else None)]
                  for w in s if w[0].strip()]
        salida.append({"inicio": s[0][1], "fin": s[-1][2], "texto": texto, "palabras": pal.alinear(texto, crudas)})
    return salida


def _a_tiempo_real_seg(s, mapa):
    convertir = lambda t: a_tiempo_real(t, mapa)
    return dict(s, inicio=convertir(s["inicio"]), fin=convertir(s["fin"]),
                palabras=pal.a_tiempo(s.get("palabras"), convertir))


def _palabras_de_groq(seg, palabras):
    """Las palabras de Groq vienen sueltas: cada segmento se queda con las que empiezan dentro de él."""
    dentro = [[w["word"].strip(), round(w["start"], 2), round(w["end"], 2), None] for w in palabras
              if seg["start"] - 0.01 <= w["start"] < seg["end"] and w.get("word", "").strip()]
    return pal.alinear(seg["text"].strip(), dentro)


# --- AssemblyAI ---------------------------------------------------------------------------------------------

def assemblyai_disponible():
    return bool(secretos.leer("assemblyai")) and not _agotado().exists()


def _assemblyai_tramo(audio, clave):
    h = {"authorization": clave}
    subido = _pedir("https://api.assemblyai.com/v2/upload", _wav(audio), dict(h, **{"content-type": "application/octet-stream"}))
    cuerpo = {"audio_url": subido["upload_url"], "language_code": "es",
              "speech_models": ["universal-3-5-pro", "universal-2"], "punctuate": True, "format_text": True}
    t = _pedir("https://api.assemblyai.com/v2/transcript", json.dumps(cuerpo).encode(), dict(h, **{"content-type": "application/json"}))
    while t["status"] not in ("completed", "error"):
        time.sleep(3)
        t = _pedir(f"https://api.assemblyai.com/v2/transcript/{t['id']}", headers=h)
    if t["status"] == "error":
        raise SinNube(f"AssemblyAI: {t.get('error')}")
    return _frases([(w["text"], w["start"] / 1000, w["end"] / 1000, w.get("confidence")) for w in t.get("words") or []])


def assemblyai(pista):
    """Devuelve (segmentos, segundos de voz enviados)."""
    clave = secretos.leer("assemblyai")
    if not clave or _agotado().exists():
        raise SinNube("AssemblyAI sin clave o con el crédito agotado")
    segs, voz = [], 0.0
    try:
        for audio, mapa in solo_voz(pista, max_s=3 * 3600):
            voz += len(audio) / SR
            segs += [_a_tiempo_real_seg(s, mapa) for s in _assemblyai_tramo(audio, clave)]
    except SinNube as e:
        texto = str(e).lower()
        if any(k in texto for k in ("http 401", "http 402", "balance", "credit", "payment", "insufficient")):
            _agotado().write_text(json.dumps({"desde": datetime.now().isoformat(timespec="seconds"), "error": str(e)},
                                             ensure_ascii=False), encoding="utf-8")
        raise
    return segs, voz


# --- Groq -----------------------------------------------------------------------------------------------------

def _multipart(campos, archivo):
    limite = uuid.uuid4().hex
    partes = [f'--{limite}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in campos]
    partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
                  f"Content-Type: audio/wav\r\n\r\n".encode() + archivo + b"\r\n")
    partes.append(f"--{limite}--\r\n".encode())
    return b"".join(partes), f"multipart/form-data; boundary={limite}"


def groq_dictado(audio, timeout=15):
    """Un dictado corto: (texto, segundos de voz enviados). Turbo porque la demora es lo que se siente; sin tiempos ni
    palabras, que el dictado no usa. Sin voz no se manda nada."""
    clave = secretos.leer("groq")
    if not clave:
        raise SinNube("Groq sin clave")
    tramos = solo_voz(audio, max_s=TRAMO_GROQ_S)
    if not tramos:
        return "", 0.0
    voz = np.concatenate([t for t, _ in tramos])
    cuerpo, tipo = _multipart([("model", "whisper-large-v3-turbo"), ("language", "es"), ("response_format", "json"),
                               ("prompt", config.glosario_prompt())], _wav(voz))
    r = _pedir("https://api.groq.com/openai/v1/audio/transcriptions", cuerpo,
               {"authorization": f"Bearer {clave}", "content-type": tipo}, timeout=timeout)
    return (r.get("text") or "").strip(), len(voz) / SR


def groq(pista):
    """Devuelve (segmentos, segundos de voz enviados)."""
    clave = secretos.leer("groq")
    if not clave:
        raise SinNube("Groq sin clave")
    prompt = config.glosario_prompt()
    segs, voz = [], 0.0
    for audio, mapa in solo_voz(pista, max_s=TRAMO_GROQ_S):
        voz += len(audio) / SR
        cuerpo, tipo = _multipart([("model", "whisper-large-v3"), ("language", "es"),
                                   ("response_format", "verbose_json"), ("prompt", prompt),
                                   ("timestamp_granularities[]", "segment"), ("timestamp_granularities[]", "word")],
                                  _wav(audio))
        r = _pedir("https://api.groq.com/openai/v1/audio/transcriptions", cuerpo,
                   {"authorization": f"Bearer {clave}", "content-type": tipo})
        segs += [_a_tiempo_real_seg({"inicio": s["start"], "fin": s["end"], "texto": s["text"].strip(),
                                     "palabras": _palabras_de_groq(s, r.get("words") or [])}, mapa)
                 for s in r.get("segments") or [] if s.get("text", "").strip()]
    return segs, voz


MOTORES = {"assemblyai": assemblyai, "groq": groq}
