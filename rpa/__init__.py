"""Automatizaciones deterministas de los procesos P01 y P03.

Este paquete no contiene inferencia generativa. Las funciones exportadas operan
con contratos tipados y conservan los identificadores BPMN normativos.
"""

from rpa.validador_p01 import (
    CAMPOS_P01,
    ContratoEntradaP01,
    ResultadoValidacionP01,
    validar_solicitud,
)

__all__ = [
    "CAMPOS_P01",
    "ContratoEntradaP01",
    "ResultadoValidacionP01",
    "validar_solicitud",
]
