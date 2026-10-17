"""Política determinista para los eventos de borde de P01."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Generic, TypeVar

P1_ERROR_SHEETS: Final = "P1_ERROR_SHEETS"
P1_ERROR_SISTEMA: Final = "P1_ERROR_SISTEMA"
ESPERAS_P01: Final[tuple[int, int, int]] = (2, 4, 8)

T = TypeVar("T")


class ErrorSheets(RuntimeError):
    """Falla transitoria al consultar el registro externo."""

    codigo = P1_ERROR_SHEETS


class ErrorSistema(RuntimeError):
    """Falla no recuperable que debe derivarse al control humano."""

    codigo = P1_ERROR_SISTEMA


@dataclass(frozen=True, slots=True)
class ResultadoReintento(Generic[T]):
    codigo_error: str
    recuperado: bool
    intentos: int
    esperas: tuple[int, ...]
    valor: T | None = None
    ultimo_error: str | None = None


def p1_reintento(
    operacion: Callable[[], T],
    *,
    dormir: Callable[[float], None] = time.sleep,
) -> ResultadoReintento[T]:
    """Reintenta exactamente a los 2, 4 y 8 s tras ``p1_error_sheets``.

    La excepción inicial ocurre fuera de esta función en el evento de borde. Cada
    elemento de :data:`ESPERAS_P01` precede uno de los tres reintentos.
    """

    ejecutadas: list[int] = []
    ultimo_error: str | None = None
    for espera in ESPERAS_P01:
        dormir(float(espera))
        ejecutadas.append(espera)
        try:
            valor = operacion()
        except ErrorSheets as exc:
            ultimo_error = type(exc).__name__
            continue
        return ResultadoReintento(
            codigo_error=P1_ERROR_SHEETS,
            recuperado=True,
            intentos=len(ejecutadas),
            esperas=tuple(ejecutadas),
            valor=valor,
        )
    return ResultadoReintento(
        codigo_error=P1_ERROR_SHEETS,
        recuperado=False,
        intentos=3,
        esperas=tuple(ejecutadas),
        ultimo_error=ultimo_error,
    )


def p1_recuperado(resultado: ResultadoReintento[object]) -> bool:
    """Compuerta BPMN ``p1_recuperado``."""

    return resultado.recuperado


def p1_alertar(expediente_id: str, codigo_error: str) -> dict[str, str]:
    """Crea el sobre redactado que precede a ``p1_incidente``."""

    if codigo_error not in {P1_ERROR_SHEETS, P1_ERROR_SISTEMA}:
        raise ValueError("Código de error P01 desconocido.")
    return {
        "expediente_id": expediente_id,
        "codigo_error": codigo_error,
        "nodo_bpmn_id": "p1_alertar",
        "siguiente_nodo": "p1_incidente",
    }


__all__ = [
    "ESPERAS_P01",
    "P1_ERROR_SHEETS",
    "P1_ERROR_SISTEMA",
    "ErrorSheets",
    "ErrorSistema",
    "ResultadoReintento",
    "p1_alertar",
    "p1_recuperado",
    "p1_reintento",
]
