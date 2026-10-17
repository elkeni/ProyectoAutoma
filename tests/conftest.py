"""Datos deterministas compartidos por la suite de FASE 2."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agentes.orquestador import Orquestador


@pytest.fixture
def contrato_p01() -> dict[str, object]:
    return {
        "solicitante": "Ana Torres",
        "contacto": "ana@example.com",
        "tipo_operacion": "compraventa",
        "dni_vendedor": "12345678",
        "dni_comprador": "87654321",
        "partida_registral": "P-12345",
        "valor_comercial": "350000.00",
        "forma_pago": "transferencia",
        "adj_dni": "drive://exp/dni.pdf",
        "adj_partida": "drive://exp/partida.pdf",
    }


@pytest.fixture
def recaudos_p02() -> list[dict[str, Any]]:
    return [
        {
            "documento_id": "partida-1",
            "fuente_id": "drive://exp/partida.pdf",
            "contenido": (
                "La partida P-12345 corresponde al inmueble de Lima y no registra "
                "gravámenes. El titular presenta DNI 12345678."
            ),
        }
    ]


@pytest.fixture
def datos_minuta() -> dict[str, Any]:
    return {
        "expediente_id": "EXP-001",
        "vendedor": "Ana Torres",
        "comprador": "Luis Pérez",
        "inmueble": "inmueble ubicado en Lima",
        "partida_registral": "P-12345",
        "valor_comercial": "S/ 350 000.00",
        "forma_pago": "transferencia bancaria",
        "referencias": ["partida-1@v1:fragmento-1"],
    }


@pytest.fixture
def orquestador(tmp_path: Path) -> Iterator[Orquestador]:
    instancia = Orquestador(tmp_path / "sagale-pruebas.sqlite3", dormir=lambda _s: None)
    try:
        yield instancia
    finally:
        instancia.close()
