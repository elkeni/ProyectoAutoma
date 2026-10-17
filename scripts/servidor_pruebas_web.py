"""Servidor desechable para pruebas manuales; nunca abre la base de la demo."""

from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from web.app import crear_app

if __name__ == "__main__":
    with TemporaryDirectory(prefix="sagale-ui-") as carpeta:
        uvicorn.run(crear_app(Path(carpeta) / "pruebas.sqlite3"), host="127.0.0.1", port=8011)
