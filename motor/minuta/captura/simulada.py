"""Reunión simulada: `python -m minuta --simular-reunion`.

Hace de cuenta que Chrome tomó el micrófono y graba silencio, sin abrir ningún dispositivo de audio. Sirve para
probar la app de punta a punta (bandeja, panel, marcas, «cortar acá», cola, Playwright) sin una llamada real y sin
capturar el audio de la habitación. Los niveles se inventan con una onda para que los medidores se muevan.
"""
import math
import time
import wave
from pathlib import Path

SR = 16000
VENTANA = "Meet - Reunión simulada - Google Chrome"


class PistaSimulada:
    def __init__(self, dispositivo, fase):
        self.dispositivo, self.fase = dispositivo, fase

    @property
    def nivel(self):
        return 0.04 + 0.03 * math.sin(time.monotonic() * 1.7 + self.fase)

    @property
    def ultimo_audio(self):
        return time.monotonic()

    def leer_pico(self):
        return self.nivel


class GrabacionSimulada:
    def __init__(self, carpeta, log=print, al_cambiar=lambda tipo, nombre: None):
        self.carpeta = Path(carpeta)
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self.t0 = time.monotonic()
        self.mic = PistaSimulada("Micrófono (simulado)", 0.0)
        self.sistema = PistaSimulada("Parlantes (simulados)", 1.3)
        self.salida = "Parlantes (simulados)"
        log("grabación simulada: no se abre ningún dispositivo")

    def reabrir(self, salida=None):
        pass

    def cerrar(self, hasta=None):
        dur = round((time.monotonic() if hasta is None else hasta) - self.t0, 1)
        for pista in ("mic", "sistema"):
            with wave.open(str(self.carpeta / f"{pista}-000.wav"), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SR)
                w.writeframes(b"\x00\x00" * int(min(dur, 300) * SR))
        return dur


def detector():
    return ["chrome.exe"]


def ventanas():
    return [VENTANA]
