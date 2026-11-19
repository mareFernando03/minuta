"""Cada test corre contra una carpeta de datos propia y un llavero en memoria: nunca toca los datos reales
ni el Administrador de credenciales de Windows."""
import importlib

import keyring
import keyring.errors
import pytest
from keyring.backend import KeyringBackend


class LlaveroEnMemoria(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.datos = {}

    def get_password(self, servicio, usuario):
        return self.datos.get((servicio, usuario))

    def set_password(self, servicio, usuario, clave):
        self.datos[(servicio, usuario)] = clave

    def delete_password(self, servicio, usuario):
        if (servicio, usuario) not in self.datos:
            raise keyring.errors.PasswordDeleteError()
        del self.datos[(servicio, usuario)]


@pytest.fixture(autouse=True)
def llavero_en_memoria():
    anterior = keyring.get_keyring()
    keyring.set_keyring(LlaveroEnMemoria())
    yield
    keyring.set_keyring(anterior)


@pytest.fixture(autouse=True)
def datos_temporales(tmp_path, monkeypatch, llavero_en_memoria):
    monkeypatch.setenv("MINUTA_DATOS", str(tmp_path / "datos"))
    from minuta import config
    importlib.reload(config)
    from minuta.datos import db
    db.cerrar()
    importlib.reload(db)
    config.preparar()
    db.migrar()
    yield tmp_path
    db.cerrar()
