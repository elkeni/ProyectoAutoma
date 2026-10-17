"""Restablece solo la base demo local, conservando los archivos anteriores."""

import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from web.app import ServicioDemo


def main() -> None:
    if os.getenv("VERCEL") == "1":
        raise SystemExit("Este reinicio es exclusivamente local.")
    with socket.socket() as conexion:
        conexion.settimeout(1)
        if conexion.connect_ex(("127.0.0.1", 8000)) == 0:
            raise SystemExit("Cierre primero el servidor local del puerto 8000.")

    carpeta = (Path(__file__).resolve().parents[1] / "data").resolve()
    carpeta.mkdir(parents=True, exist_ok=True)
    base = carpeta / "sagale_demo.sqlite3"
    respaldo = (
        carpeta
        / "respaldos"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    )
    archivos = [base, Path(str(base) + "-wal"), Path(str(base) + "-shm")]
    # Nunca mover un enlace o una ruta que resuelva fuera de la carpeta demo.
    for archivo in archivos:
        if archivo.is_symlink() or archivo.resolve().parent != carpeta:
            raise SystemExit("La ruta de la base demo no es segura para este reinicio.")
    if not respaldo.resolve().is_relative_to(carpeta):
        raise SystemExit("El respaldo debe permanecer dentro de la carpeta demo.")
    if any(archivo.exists() for archivo in archivos):
        respaldo.mkdir(parents=True)
        for archivo in archivos:
            if archivo.exists():
                archivo.rename(respaldo / archivo.name)
        print(f"Base anterior conservada en: {respaldo}")

    servicio = ServicioDemo(base)
    try:
        tablero = servicio.tablero("EXP-2026-001")
        assert len(tablero["expedientes"]) == 4
        assert len(tablero["tareas"]) == 3
        for expediente, fase in (
            ("EXP-2026-001", "p3_revision"),
            ("EXP-2026-002", "p2_legal"),
            ("EXP-2026-003", "p2_subsanacion"),
            ("EXP-2026-004", "p1_400"),
        ):
            assert servicio.tablero(expediente)["seleccionado"]["fase"] == fase
        print(
            "Demo preparada: 4 expedientes iniciales y 3 revisiones pendientes; sin ensayos LIVE."
        )
    finally:
        servicio.close()


if __name__ == "__main__":
    main()
