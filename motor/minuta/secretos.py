"""Claves de API y URL privadas de calendarios, en el Administrador de credenciales de Windows.

Nada de esto se guarda en archivos. `migrar_desde_config` trae lo que el grabador viejo tenía en texto plano en
`config.local.json` y, si todo quedó guardado, borra ese archivo.
"""
import json
from pathlib import Path

import keyring

SERVICIO = "Minuta"
CLAVES_API = ("assemblyai", "groq")


def leer(nombre):
    return keyring.get_password(SERVICIO, nombre)


def guardar(nombre, valor):
    if valor:
        keyring.set_password(SERVICIO, nombre, valor)
    else:
        borrar(nombre)


def borrar(nombre):
    try:
        keyring.delete_password(SERVICIO, nombre)
    except keyring.errors.PasswordDeleteError:
        pass


def calendarios():
    """{nombre: url iCal privada}. Se guarda como un solo secreto JSON."""
    crudo = leer("calendarios")
    return json.loads(crudo) if crudo else {}


def guardar_calendarios(urls):
    guardar("calendarios", json.dumps(urls, ensure_ascii=False) if urls else "")


def estado():
    """Qué está configurado, sin exponer ningún valor."""
    return {"assemblyai": bool(leer("assemblyai")), "groq": bool(leer("groq")),
            "calendarios": sorted(calendarios())}


def migrar_desde_config(ruta, borrar=True):
    """Importa claves y calendarios de un config.local.json viejo. Devuelve qué migró; con `borrar`, elimina el
    archivo si todo quedó legible en el Administrador de credenciales (mientras el grabador viejo siga corriendo
    se llama con borrar=False, porque todavía lo lee)."""
    ruta = Path(ruta)
    if not ruta.exists():
        return []
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    migrado = []
    for nombre, valor in (datos.get("api_keys") or {}).items():
        if nombre in CLAVES_API and valor:
            guardar(nombre, valor)
            migrado.append(nombre)
    urls = {k: v for k, v in (datos.get("calendarios_ical") or {}).items() if v}
    if urls:
        guardar_calendarios({**calendarios(), **urls})
        migrado.append("calendarios")
    completo = all(leer(n) for n in migrado if n != "calendarios") and (not urls or calendarios())
    if completo and borrar:
        ruta.unlink()
    return migrado
