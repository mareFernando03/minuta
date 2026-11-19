"""Rutas y constantes del motor. Todo lo que el motor escribe vive bajo DATOS (configurable para los tests)."""
import os
import shutil
from pathlib import Path

# No es %LOCALAPPDATA%\Minuta: ahí instala el NSIS por usuario, y los datos no se mezclan con los programas. Esta es la
# carpeta estándar de la app (el identificador de Tauri); el desinstalador solo la borra si se lo pide a propósito.
DATOS = Path(os.environ.get("MINUTA_DATOS") or Path.home() / "AppData" / "Local" / "ar.fmare.minuta")
GRABACIONES = DATOS / "grabaciones"
BASE_DE_DATOS = DATOS / "minuta.db"
LOGS = DATOS / "logs"
CACHE_CALENDARIOS = DATOS / "calendarios"
GLOSARIO = DATOS / "glosario"
RECURSOS = Path(__file__).parent / "recursos"

# Exportación compatible con granola-proxy/archivar.py, que leen los corpus de reuniones.
# Hasta el 7/11/2026 va a una carpeta paralela para no duplicar reuniones en granola-archivo.
EXPORTAR_A_POR_DEFECTO = Path.home() / "Documents" / "granola-archivo-paralelo"


def preparar():
    """Crea las carpetas y copia el glosario de fábrica la primera vez (después lo edita el usuario)."""
    for carpeta in (DATOS, GRABACIONES, LOGS, CACHE_CALENDARIOS, GLOSARIO):
        carpeta.mkdir(parents=True, exist_ok=True)
    for nombre in ("glosario-prompt.txt", "glosario-terminos.txt"):
        if not (GLOSARIO / nombre).exists():
            shutil.copy(RECURSOS / nombre, GLOSARIO / nombre)


def glosario_prompt():
    return (GLOSARIO / "glosario-prompt.txt").read_text(encoding="utf-8").strip()


def glosario_terminos():
    return (GLOSARIO / "glosario-terminos.txt").read_text(encoding="utf-8")
