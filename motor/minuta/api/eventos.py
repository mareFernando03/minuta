"""Bus de eventos: los hilos del motor (vigía, cola) publican; cada WebSocket de la app recibe una copia.

`publicar` se puede llamar desde cualquier hilo: pasa el evento al loop de asyncio del servidor.
"""
import asyncio
import json
import threading
from datetime import datetime


class Bus:
    def __init__(self):
        self._loop = None
        self._colas = set()
        self._lock = threading.Lock()

    def conectar_loop(self, loop):
        self._loop = loop

    def publicar(self, tipo, datos=None):
        mensaje = json.dumps({"tipo": tipo, "datos": datos or {}, "en": datetime.now().isoformat(timespec="seconds")},
                             ensure_ascii=False)
        if self._loop is None:
            return
        with self._lock:
            colas = list(self._colas)
        for q in colas:
            self._loop.call_soon_threadsafe(_poner, q, mensaje)

    def suscribir(self):
        q = asyncio.Queue(maxsize=200)
        with self._lock:
            self._colas.add(q)
        return q

    def desuscribir(self, q):
        with self._lock:
            self._colas.discard(q)


def _poner(q, mensaje):
    if q.full():  # una app que no lee no frena al resto: se descarta lo más viejo
        q.get_nowait()
    q.put_nowait(mensaje)
