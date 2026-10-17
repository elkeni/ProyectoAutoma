"""Comprueba la cuenta y los permisos sin mostrar ni persistir credenciales."""

import json
import os
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main() -> None:
    entorno = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    resultado = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\nusername=elkeni\npath=elkeni/ProyectoAutoma.git\n\n",
        text=True,
        capture_output=True,
        env=entorno,
        check=False,
    )
    if resultado.returncode:
        raise SystemExit("No hay una sesión GitHub utilizable sin interacción.")
    credencial = dict(
        linea.split("=", 1) for linea in resultado.stdout.splitlines() if "=" in linea
    )
    token = credencial.get("password")
    if not token:
        raise SystemExit("No se encontró una credencial GitHub válida.")
    cabeceras = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "ProyectoAutoma-access-check",
    }
    try:
        with urlopen(Request("https://api.github.com/user", headers=cabeceras), timeout=20) as r:
            usuario = json.load(r)
        if usuario.get("login", "").casefold() != "elkeni":
            raise SystemExit(
                "La sesión disponible no pertenece a elkeni; no se publicará con ella."
            )
        with urlopen(
            Request("https://api.github.com/repos/elkeni/ProyectoAutoma", headers=cabeceras),
            timeout=20,
        ) as r:
            repositorio = json.load(r)
    except HTTPError as error:
        raise SystemExit(f"GitHub rechazó la comprobación: HTTP {error.code}.") from None
    if not repositorio.get("permissions", {}).get("push"):
        raise SystemExit("La cuenta elkeni no tiene permiso de escritura en el repositorio.")
    print(
        json.dumps(
            {
                "cuenta": "elkeni",
                "repositorio": repositorio["full_name"],
                "publico": not repositorio["private"],
                "escritura": True,
            }
        )
    )


if __name__ == "__main__":
    main()
