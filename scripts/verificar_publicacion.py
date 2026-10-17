"""Revisa lo preparado para publicar sin imprimir posibles secretos."""

import re
import subprocess
from pathlib import PurePosixPath


def git(*argumentos: str) -> bytes:
    return subprocess.run(["git", *argumentos], capture_output=True, check=True).stdout


def main() -> None:
    archivos = git("ls-files", "--cached", "-z").decode().split("\0")
    archivos = [archivo for archivo in archivos if archivo]
    prohibidos = {"data", "secrets", "__pycache__", ".venv", ".pytest_cache", ".mypy_cache"}
    secreto = re.compile(
        rb"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
        rb"AIza[0-9A-Za-z_-]{30,}|sk-[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY"
    )
    for archivo in archivos:
        ruta = PurePosixPath(archivo)
        if prohibidos.intersection(ruta.parts) or ruta.name.startswith(".env"):
            raise SystemExit("Hay archivos locales o sensibles preparados para publicar.")
        if ruta.suffix in {".sqlite", ".sqlite3", ".db", ".pyc", ".pem", ".key"}:
            raise SystemExit("Hay datos o claves preparados para publicar.")
        contenido = git("show", f":{archivo}")
        if secreto.search(contenido):
            raise SystemExit(
                "Posible secreto detectado; publicación detenida sin mostrar su valor."
            )
    print(
        f"Publicación revisada: {len(archivos)} archivos; sin bases, cachés ni secretos detectados."
    )


if __name__ == "__main__":
    main()
