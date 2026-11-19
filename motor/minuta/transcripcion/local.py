"""Transcripción local con faster-whisper, y la fusión de las dos pistas (mic y sistema) en turnos.

Decisiones medidas el 9/10/2026 (no cambiar sin volver a medir):
- CPU int8, beam 1, lotes de 8: `small` 8 a 16 min por hora de reunión enchufada, `large-v3-turbo` 29 a 55.
  A batería el tiempo se triplica: lo local solo corre enchufada.
- El glosario va como `initial_prompt` (hasta ~223 tokens; Whisper descarta el principio si se pasa). Como
  `hotwords` llevó el error de 12% a 36%.
- Segmentos de 10 s en lugar de 30: 2,6 veces más lento y el glosario se filtra al texto. Por eso se transcribe
  con segmentos de 30 s y se parten después por frase con los tiempos de cada palabra.
- El modo por lotes no reintenta cuando Whisper entra en bucle ("cen- cen- cen-…"): se colapsa la repetición.

Lo que entra por el micrófono y no es del usuario (medido el 13/10/2026 con scripts/medir_mic.py sobre la Fabrikam y el
sync de Altas, mic «Micrófono (Realtek)» de la notebook):
- Durante una llamada, el mic levanta la charla de la oficina y a quien está al lado (Juan), bajo. Percentil 90 de la
  energía del turno en tramos de 0,1 s: los turnos del usuario, 0,017 a 0,107; los ajenos, 0,003 a 0,009. Corte en
  `VOZ_MIC_MIN` = 0,012. Decide la energía sola. La confianza del motor no sirve para rescatar un turno bajo: al
  retranscribir el sync de Altas con AssemblyAI quedaron dos turnos de Juan (energía 0,0045 y 0,0067) con confianza
  0,91 y 0,99. El motor oye claro a quien está al lado.
- En una grabación a mano (presencial) las voces del cuarto son la reunión: no se filtra por energía.
- Whisper inventa «Aplausos» sobre el ruido: esas frases sueltas se descartan siempre (`ALUCINACIONES`).
"""
import ctypes
import difflib
import re
import wave
from pathlib import Path

import numpy as np

from minuta import config
from minuta.transcripcion import palabras as pal

SR = 16000
HILOS = 4
ECO_VENTANA_S = 1.5
ECO_SIMILITUD = 0.6
UNIR_TURNOS_S = 2.0
TRAMO_VOZ_S = 0.1
VOZ_MIC_MIN = 0.012
# Frases que Whisper escribe sobre ruido o silencio, comparadas enteras (sin puntuación ni mayúsculas).
ALUCINACIONES = {"aplausos", "música", "risas", "subtítulos realizados por la comunidad de amara org",
                 "subtítulos por la comunidad de amara org"}
ETIQUETAS = {"mic": "Microphone", "sistema": "System audio"}


def leer_wav(ruta):
    with wave.open(str(ruta)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def bloques_hasta(carpeta, pista, hasta_s=None):
    """Bloques WAV de 5 min de una pista que empiezan antes del corte (+3 s de margen)."""
    bloques = sorted(Path(carpeta).glob(f"{pista}-*.wav"))
    if hasta_s is None:
        return bloques
    return [b for b in bloques if int(b.stem.split("-")[1]) * 300 < hasta_s + 3]


def leer_pista(carpeta, pista, hasta_s=None):
    """Concatena los bloques de una pista (mic o sistema). Con `hasta_s`, solo hasta el corte: lo posterior (los
    minutos de silencio antes de un corte automático, o lo grabado después de soltar el micrófono) ya no es la
    reunión y no se manda a ningún motor."""
    bloques = bloques_hasta(carpeta, pista, hasta_s)
    audio = np.concatenate([leer_wav(b) for b in bloques]) if bloques else np.zeros(0, dtype=np.float32)
    return audio if hasta_s is None else audio[: int((hasta_s + 3) * SR)]


# --- energía y uso -------------------------------------------------------------------------------------------

class _EstadoEnergia(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte), ("BatteryLifePercent", ctypes.c_byte),
                ("SystemStatusFlag", ctypes.c_byte), ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong)]


class _UltimaEntrada(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def enchufada():
    e = _EstadoEnergia()
    ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(e))
    return e.ACLineStatus == 1


def segundos_sin_uso():
    u = _UltimaEntrada(cbSize=ctypes.sizeof(_UltimaEntrada))
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(u))
    return (ctypes.windll.kernel32.GetTickCount() - u.dwTime) / 1000


# --- fusión de pistas ------------------------------------------------------------------------------------------

def palabras(t):
    return re.findall(r"[a-záéíóúüñ0-9]+", t.lower())


def es_eco(seg_mic, segs_sistema):
    """Sin auriculares, el mic repite lo que sale por los parlantes: mismo texto, mismo momento."""
    pm = palabras(seg_mic["texto"])
    if not pm:
        return True
    for s in segs_sistema:
        if s["inicio"] < seg_mic["fin"] + ECO_VENTANA_S and s["fin"] > seg_mic["inicio"] - ECO_VENTANA_S:
            if difflib.SequenceMatcher(None, pm, palabras(s["texto"]), autojunk=False).ratio() >= ECO_SIMILITUD:
                return True
    return False


def envolvente(carpeta, pista, hasta_s=None):
    """Energía (RMS) de una pista en tramos de 0,1 s, bloque por bloque: una hora son 36 mil números y no hace falta
    tener el audio entero en memoria. Los bloques duran 300 s justos, así que los tramos quedan alineados."""
    h = int(SR * TRAMO_VOZ_S)
    partes = []
    for b in bloques_hasta(carpeta, pista, hasta_s):
        x = leer_wav(b)
        k = len(x) // h
        partes.append(np.sqrt((x[: k * h].reshape(k, h) ** 2).mean(1)))
    return np.concatenate(partes) if partes else None


def voz_del_tramo(env, inicio, fin):
    """Percentil 90 de la energía del turno (un turno largo con pausas no se diluye). None si no hay audio ahí."""
    if env is None or inicio is None:
        return None
    a = int(inicio / TRAMO_VOZ_S)
    x = env[a: max(int((fin if fin is not None else inicio) / TRAMO_VOZ_S), a + 1)]
    return float(np.percentile(x, 90)) if len(x) else None


def es_alucinacion(seg):
    return " ".join(palabras(seg["texto"])) in ALUCINACIONES


def es_ajeno(seg, voz_mic):
    """Lo que entra bajo por el micrófono en una llamada: la oficina, quien está al lado. Sin `voz_mic` (grabación a
    mano, otro micrófono, o sin audio) no se filtra."""
    voz = voz_del_tramo(voz_mic, seg["inicio"], seg.get("fin"))
    return voz is not None and voz < VOZ_MIC_MIN


def fusionar_turnos(segs_mic, segs_sistema, corte_s=None, voz_mic=None):
    """Intercala las dos pistas por tiempo, descarta del mic el eco, las alucinaciones y lo ajeno (con `voz_mic`, la
    envolvente del mic), corta lo posterior al corte y une los segmentos seguidos del mismo canal en un turno.
    Devuelve [{canal, inicio, fin, texto, palabras}]."""
    limite = (corte_s + 3) if corte_s is not None else float("inf")
    todos = [dict(s, canal="sistema") for s in segs_sistema if s["inicio"] < limite]
    todos += [dict(s, canal="mic") for s in segs_mic
              if s["inicio"] < limite and not es_eco(s, segs_sistema) and not es_alucinacion(s)]
    todos.sort(key=lambda s: s["inicio"])
    turnos = []
    for s in todos:
        if turnos and turnos[-1]["canal"] == s["canal"] and s["inicio"] - turnos[-1]["fin"] < UNIR_TURNOS_S:
            turnos[-1]["texto"] += " " + s["texto"]
            turnos[-1]["fin"] = s["fin"]
            turnos[-1]["palabras"] = pal.unir(turnos[-1]["palabras"], s.get("palabras"))
        else:
            turnos.append({"canal": s["canal"], "inicio": s["inicio"], "fin": s["fin"], "texto": s["texto"],
                           "palabras": s.get("palabras")})
    # Lo ajeno se decide por turno, como se midió (medir_mic.py lee los turnos guardados): una frase corta del usuario
    # que baja la voz al final no se pierde del medio de su turno.
    return [t for t in turnos if t["texto"].strip() and not (t["canal"] == "mic" and es_ajeno(t, voz_mic))]


def como_texto(turnos):
    """Formato de granola-archivo: 'Etiqueta: texto' separados por línea en blanco."""
    return "\n\n".join(f"{ETIQUETAS.get(t['canal'], t['canal'])}: {t['texto']}" for t in turnos)


def fusionar(segs_mic, segs_sistema):
    return como_texto(fusionar_turnos(segs_mic, segs_sistema))


# --- Whisper ---------------------------------------------------------------------------------------------------

_modelos = {}


def modelo(nombre):
    if nombre not in _modelos:
        from faster_whisper import BatchedInferencePipeline, WhisperModel
        _modelos[nombre] = BatchedInferencePipeline(
            WhisperModel(nombre, device="cpu", compute_type="int8", cpu_threads=HILOS))
    return _modelos[nombre]


REPETICION = re.compile(r"(\b[\w-]+[\s,.-]*?)(?:\1){3,}", re.I)


def limpiar_repeticiones(texto):
    anterior = None
    while anterior != texto:
        anterior, texto = texto, REPETICION.sub(r"\1", texto)
    return texto.strip()


def partir_en_frases(seg, desde_s):
    """Corta tras un punto final con pausa, o en una pausa larga; un pedazo de menos de 3 palabras vuelve al
    anterior para no cortar una frase al medio."""
    trozos, actual = [], []
    for w in seg.words or []:
        if actual:
            pausa = w.start - actual[-1].end
            if pausa >= 1.5 or (pausa >= 0.5 and actual[-1].word.strip()[-1:] in ".?!"):
                trozos.append(actual)
                actual = []
        actual.append(w)
    if actual:
        trozos.append(actual)
    unidos = []
    for t in trozos:
        if unidos and len(t) < 3:
            unidos[-1] += t
        else:
            unidos.append(t)
    frases = []
    for t in unidos:
        texto = limpiar_repeticiones("".join(w.word for w in t))
        crudas = [[w.word.strip(), round(desde_s + w.start, 2), round(desde_s + w.end, 2),
                   round(w.probability, 3) if getattr(w, "probability", None) is not None else None]
                  for w in t if w.word.strip()]
        frases.append({"inicio": round(desde_s + t[0].start, 2), "fin": round(desde_s + t[-1].end, 2),
                       "texto": texto, "palabras": pal.alinear(texto, crudas)})
    return frases


def transcribir_bloque(nombre_modelo, ruta, desde_s):
    segs, _ = modelo(nombre_modelo).transcribe(leer_wav(ruta), language="es", vad_filter=True, beam_size=1,
                                               batch_size=8, initial_prompt=config.glosario_prompt(),
                                               word_timestamps=True)
    return [f for s in segs for f in partir_en_frases(s, desde_s) if f["texto"]]
