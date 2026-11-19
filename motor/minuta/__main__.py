"""Arranque del motor: `python -m minuta`.

Lo lanza la app (supervisor en Rust) con el token en la variable MINUTA_TOKEN y `--padre <pid>`. El motor elige un
puerto libre en 127.0.0.1 y lo informa en la primera línea de su salida estándar, como JSON:
    {"listo": true, "puerto": 51234, "version": "0.1.0"}
Si el proceso padre muere, el motor cierra la grabación en curso y se apaga: nunca queda huérfano.

Otros usos:
    python -m minuta --importar          importa el archivo de Granola y el grabador viejo, y sale
    python -m minuta --dev               sin supervisor: token fijo "dev" e imprime la URL
"""
import argparse
import ctypes
import json
import logging
import logging.handlers
import os
import socket
import sys
import threading
from pathlib import Path

import uvicorn

from minuta import __version__, config, secretos
from minuta.api.eventos import Bus
from minuta.api.servidor import crear_app
from minuta.datos import db, importar

GRABADOR_VIEJO = Path.home() / "AppData" / "Local" / "grabador-reuniones" / "grabaciones"
CONFIG_VIEJA = Path.home() / ".claude" / "scripts" / "grabador-reuniones" / "config.local.json"
ARCHIVO_GRANOLA = Path.home() / "Documents" / "granola-archivo"

log = logging.getLogger("minuta")


class _SinReseteos(logging.Filter):
    """En Windows, asyncio registra como ERROR cada conexión que el cliente corta (WinError 10054). Es inofensivo y
    tapaba los errores de verdad en el log."""

    def filter(self, registro):
        return not (registro.exc_info and isinstance(registro.exc_info[1], ConnectionResetError))


def configurar_logs():
    config.LOGS.mkdir(parents=True, exist_ok=True)
    archivo = logging.handlers.RotatingFileHandler(config.LOGS / "motor.log", maxBytes=2_000_000, backupCount=5,
                                                   encoding="utf-8")
    archivo.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    errores = logging.StreamHandler(sys.stderr)
    errores.setLevel(logging.WARNING)
    logging.basicConfig(level=logging.INFO, handlers=[archivo, errores])
    logging.getLogger("asyncio").addFilter(_SinReseteos())


def una_sola_instancia():
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\minuta-motor")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def vigilar_padre(pid, al_morir):
    """Espera a que termine el proceso padre (la app) y avisa."""
    SYNCHRONIZE, INFINITE = 0x00100000, 0xFFFFFFFF
    h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not h:
        log.warning("no se pudo abrir el proceso padre %s: el motor se apaga", pid)
        al_morir()
        return

    def esperar():
        ctypes.windll.kernel32.WaitForSingleObject(h, INFINITE)
        log.info("la app (pid %s) terminó: se apaga el motor", pid)
        al_morir()

    threading.Thread(target=esperar, name="padre", daemon=True).start()


def importar_todo():
    config.preparar()
    db.migrar()
    n_granola = importar.importar_granola(ARCHIVO_GRANOLA) if ARCHIVO_GRANOLA.exists() else 0
    n_viejo = importar.importar_grabador(GRABADOR_VIEJO) if GRABADOR_VIEJO.exists() else 0
    migrados = secretos.migrar_desde_config(CONFIG_VIEJA, borrar=False)
    print(json.dumps({"granola": n_granola, "grabador": n_viejo, "secretos": migrados}, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(prog="minuta")
    ap.add_argument("--padre", type=int)
    ap.add_argument("--sin-captura", action="store_true", help="no graba ni transcribe: solo la API (pruebas de interfaz)")
    ap.add_argument("--simular-reunion", action="store_true",
                    help="hace de cuenta que hay una reunión y graba silencio, sin abrir audio (pruebas de punta a punta)")
    ap.add_argument("--importar", action="store_true")
    ap.add_argument("--dev", action="store_true")
    ap.add_argument("--puerto", type=int, default=0, help="0 = uno libre (lo normal); fijo solo para desarrollo")
    a = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    configurar_logs()
    if a.importar:
        importar_todo()
        return
    if not una_sola_instancia():
        print(json.dumps({"listo": False, "error": "ya hay un motor corriendo"}), flush=True)
        sys.exit(2)
    token = "dev" if a.dev else os.environ.pop("MINUTA_TOKEN", "")
    if len(token) < 32 and not a.dev:
        print(json.dumps({"listo": False, "error": "falta MINUTA_TOKEN"}), flush=True)
        sys.exit(2)

    config.preparar()
    db.migrar()
    bus = Bus()
    vigia = cola = dictado = None
    if not a.sin_captura:
        from minuta.captura.vigia import Vigia
        from minuta.trabajos.cola import Cola
        cola = Cola(evento=bus.publicar, grabando=lambda: vigia is not None and vigia.grab is not None)

        def al_terminar(gid):
            db.encolar("transcribir", gid)
            cola.despertar()

        if a.simular_reunion:
            from minuta.captura import simulada
            vigia = Vigia(al_terminar=al_terminar, evento=bus.publicar, detector=simulada.detector,
                          ventanas=simulada.ventanas, grabador=simulada.GrabacionSimulada)
        else:
            vigia = Vigia(al_terminar=al_terminar, evento=bus.publicar)
        from minuta import dictado as dictado_mod
        dictado = dictado_mod.Dictado(vigia=vigia, evento=bus.publicar,
                                      abrir_mic=dictado_mod.MicMudo if a.simular_reunion else dictado_mod.MicPropio)
        cola.start()
        vigia.start()
        from minuta.inteligencia.brief import Agenda
        Agenda(evento=bus.publicar).start()  # un minuto antes de cada reunión del calendario, el brief

    servidor = None

    def apagar():
        if servidor:
            servidor.should_exit = True

    app = crear_app(token, vigia=vigia, cola=cola, bus=bus, apagar=apagar, dictado=dictado)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", a.puerto))
    puerto = sock.getsockname()[1]
    servidor = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False, ws="websockets-sansio",
                                             timeout_graceful_shutdown=5))

    if a.padre:
        vigilar_padre(a.padre, apagar)

    original_startup = servidor.startup

    async def startup_con_aviso(sockets=None):
        await original_startup(sockets=sockets)
        print(json.dumps({"listo": True, "puerto": puerto, "version": __version__}), flush=True)
        if a.dev:
            print(f"http://127.0.0.1:{puerto}/api/estado  (token: dev)", flush=True)

    servidor.startup = startup_con_aviso
    log.info("motor %s en 127.0.0.1:%s", __version__, puerto)
    try:
        servidor.run(sockets=[sock])
    finally:
        if vigia:
            vigia.parar()
            vigia.detener_grabacion(motivo="cierre")
        if cola:
            cola.parar()
        log.info("motor apagado")


if __name__ == "__main__":
    main()
