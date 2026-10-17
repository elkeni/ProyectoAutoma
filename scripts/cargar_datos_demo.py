"""Carga los cuatro expedientes demostrativos y deja el SQLite listo para la web."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from web.demo_datos import cargar_datos_demo, ruta_base_demo  # noqa: E402


def _eliminar_base(ruta: Path) -> None:
    """Borra solo la base demo indicada junto a sus archivos WAL asociados."""

    for candidata in (ruta, ruta.with_name(f"{ruta.name}-wal"), ruta.with_name(f"{ruta.name}-shm")):
        candidata.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ruta",
        type=Path,
        default=ruta_base_demo(),
        help="Ruta del SQLite de demostración.",
    )
    parser.add_argument(
        "--reiniciar",
        action="store_true",
        help="Recrear exclusivamente el SQLite indicado antes de cargar las semillas.",
    )
    argumentos = parser.parse_args()
    if argumentos.reiniciar:
        _eliminar_base(argumentos.ruta)
    resultado = cargar_datos_demo(argumentos.ruta)
    print(json.dumps(resultado, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
