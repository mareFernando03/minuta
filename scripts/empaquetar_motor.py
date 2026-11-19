"""Arma el motor listo para instalar: Python embebible oficial + dependencias + paquete `minuta`.

    motor/.venv/Scripts/python.exe scripts/empaquetar_motor.py

Deja todo en `app/src-tauri/motor-dist/`, que el instalador copia a `<instalación>/motor/`. La app lanza
`motor/python.exe -m minuta` y Claude Code usa `motor/python.exe -m minuta.mcp`.

Por qué Python embebible y no Nuitka (decidido el 9/10/2026): el python.exe embebible viene firmado por la Python
Software Foundation, que es lo que más baja los falsos positivos de antivirus; se arma en un par de minutos en lugar
de compilar ctranslate2, onnxruntime y PyAV durante media hora; y es el mismo runtime que pasan los tests. Las
versiones de las dependencias salen del venv del motor (`pip freeze`), así se empaqueta exactamente lo probado.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
VENV_PY = RAIZ / "motor" / ".venv" / "Scripts" / "python.exe"
DESTINO = RAIZ / "app" / "src-tauri" / "motor-dist"
CACHE = RAIZ / "build-cache"
VERSION = "3.11.9"  # la del venv: la verifica `comprobar_version`
EMBEBIBLE = f"https://www.python.org/ftp/python/{VERSION}/python-{VERSION}-embed-amd64.zip"
# Hash del zip bajado el 9/10/2026. Si python.org sirviera otro archivo, el empaquetado se frena.
SHA256 = "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b"
SOLO_DESARROLLO = {"pytest", "pluggy", "iniconfig"}


def correr(*args, **kw):
    print("  $", " ".join(str(a) for a in args))
    subprocess.run([str(a) for a in args], check=True, **kw)


def comprobar_version():
    v = subprocess.run([str(VENV_PY), "-c", "import platform; print(platform.python_version())"],
                       capture_output=True, text=True, check=True).stdout.strip()
    if v != VERSION:
        sys.exit(f"el venv usa Python {v} y el script empaqueta {VERSION}: actualizá VERSION")


def bajar_embebible():
    CACHE.mkdir(exist_ok=True)
    zip_ = CACHE / Path(EMBEBIBLE).name
    if not zip_.exists():
        print(f"bajando {EMBEBIBLE}")
        with urllib.request.urlopen(EMBEBIBLE, timeout=120) as r, open(zip_.with_suffix(".tmp"), "wb") as f:
            shutil.copyfileobj(r, f)
        zip_.with_suffix(".tmp").replace(zip_)
    hecho = hashlib.sha256(zip_.read_bytes()).hexdigest()
    if hecho != SHA256:
        zip_.unlink()
        sys.exit(f"el zip de Python no coincide con el hash fijado ({hecho}): se borró, revisá antes de seguir")
    return zip_


def habilitar_site_packages():
    """El embebible ignora site-packages salvo que su archivo ._pth lo nombre y diga `import site`."""
    pth = next(DESTINO.glob("python*._pth"))
    lineas = [l for l in pth.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    pth.write_text("\n".join([*lineas, r"Lib\site-packages", "import site", ""]), encoding="utf-8")


def dependencias():
    congeladas = subprocess.run([str(VENV_PY), "-m", "pip", "freeze", "--exclude-editable"],
                                capture_output=True, text=True, check=True).stdout.splitlines()
    elegidas = [l for l in congeladas if l and l.split("==")[0].lower() not in SOLO_DESARROLLO]
    requisitos = CACHE / "requisitos.txt"
    requisitos.write_text("\n".join(elegidas) + "\n", encoding="utf-8")
    return requisitos, len(elegidas)


def main():
    comprobar_version()
    zip_ = bajar_embebible()
    if DESTINO.exists():
        shutil.rmtree(DESTINO)
    DESTINO.mkdir(parents=True)
    with zipfile.ZipFile(zip_) as z:
        z.extractall(DESTINO)
    habilitar_site_packages()

    sitio = DESTINO / "Lib" / "site-packages"
    requisitos, n = dependencias()
    print(f"instalando {n} dependencias")
    correr(VENV_PY, "-m", "pip", "install", "--quiet", "--no-deps", "--no-warn-script-location",
           "--target", sitio, "-r", requisitos)
    print("instalando el paquete minuta")
    # setuptools reusa motor/build: sin limpiarlo, entran archivos de un armado anterior que ya no están en el código.
    for resto in [RAIZ / "motor" / "build", *(RAIZ / "motor").glob("*.egg-info")]:
        shutil.rmtree(resto, ignore_errors=True)
    correr(VENV_PY, "-m", "pip", "install", "--quiet", "--no-deps", "--no-warn-script-location",
           "--target", sitio, RAIZ / "motor")
    shutil.rmtree(sitio / "bin", ignore_errors=True)  # ejecutables de consola que no se usan

    print("precompilando (arranque más rápido)")
    correr(DESTINO / "python.exe", "-m", "compileall", "-q", "-j", "0", sitio)

    print("prueba de humo")
    correr(DESTINO / "python.exe", "-c",
           "import minuta, minuta.mcp, faster_whisper, av, pyaudiowpatch, uvicorn, keyring;"
           "print('keyring:', type(keyring.get_keyring()).__name__)")
    correr(DESTINO / "python.exe", "-m", "minuta", "--help", stdout=subprocess.DEVNULL)
    # Arranque con una carpeta de datos vacía: copia el glosario y migra la base. Así se detectó que faltaban los
    # recursos .txt en el paquete (las pruebas usaban datos que ya los tenían).
    with tempfile.TemporaryDirectory() as datos:
        correr(DESTINO / "python.exe", "-c", "from minuta import config; from minuta.datos import db; "
               "config.preparar(); print('base en versión', db.migrar())", env={**os.environ, "MINUTA_DATOS": datos})

    total = sum(f.stat().st_size for f in DESTINO.rglob("*") if f.is_file())
    archivos = sum(1 for f in DESTINO.rglob("*") if f.is_file())
    print(f"listo: {DESTINO}  ({total / 2**20:.0f} MB, {archivos} archivos)")


if __name__ == "__main__":
    main()
