"""Contrato y validacion determinista de ``p1_validar`` / ``p1_valida``.

Los mensajes de error son deliberadamente redactados: identifican el campo y la
regla incumplida, pero nunca repiten el valor recibido (que podria contener PII).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Final, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

CAMPOS_P01: Final[tuple[str, ...]] = (
    "solicitante",
    "contacto",
    "tipo_operacion",
    "dni_vendedor",
    "dni_comprador",
    "partida_registral",
    "valor_comercial",
    "forma_pago",
    "adj_dni",
    "adj_partida",
)

NODOS_VALIDACION: Final[tuple[str, str]] = ("p1_validar", "p1_valida")
TIPOS_OPERACION_PERMITIDOS: Final[frozenset[str]] = frozenset(
    {"compraventa", "compra venta", "venta"}
)
FORMAS_PAGO_PERMITIDAS: Final[frozenset[str]] = frozenset(
    {
        "contado",
        "credito",
        "crédito",
        "transferencia",
        "cheque",
        "mixto",
    }
)

_DNI_RE = re.compile(r"^[0-9]{8}$")
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_TELEFONO_RE = re.compile(r"^\+?[0-9][0-9 ()-]{6,19}$")
_PARTIDA_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/-]{2,39}$")
_EXPEDIENTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

VerificadorAdjunto: TypeAlias = Callable[[str], bool]


def _texto_nfkc(valor: str) -> str:
    return unicodedata.normalize("NFKC", valor).strip()


class ContratoEntradaP01(BaseModel):
    """Los diez campos exactos recibidos por P01, sin campos silenciosos extra."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    solicitante: str = Field(min_length=2, max_length=200)
    contacto: str = Field(min_length=7, max_length=254)
    tipo_operacion: str = Field(min_length=3, max_length=40)
    dni_vendedor: str
    dni_comprador: str
    partida_registral: str = Field(min_length=3, max_length=40)
    valor_comercial: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    forma_pago: str = Field(min_length=3, max_length=80)
    adj_dni: str = Field(min_length=1, max_length=2048)
    adj_partida: str = Field(min_length=1, max_length=2048)

    @field_validator(
        "solicitante",
        "contacto",
        "tipo_operacion",
        "partida_registral",
        "forma_pago",
        "adj_dni",
        "adj_partida",
        mode="before",
    )
    @classmethod
    def validar_texto(cls, valor: object) -> object:
        if isinstance(valor, str):
            return _texto_nfkc(valor)
        return valor

    @field_validator("dni_vendedor", "dni_comprador", mode="before")
    @classmethod
    def validar_dni(cls, valor: object) -> object:
        if not isinstance(valor, str) or _DNI_RE.fullmatch(valor.strip()) is None:
            raise ValueError("debe contener exactamente 8 digitos")
        return valor.strip()

    @field_validator("contacto")
    @classmethod
    def validar_contacto(cls, valor: str) -> str:
        if _EMAIL_RE.fullmatch(valor) is None and _TELEFONO_RE.fullmatch(valor) is None:
            raise ValueError("debe ser un correo o telefono valido")
        return valor

    @field_validator("tipo_operacion")
    @classmethod
    def validar_tipo_operacion(cls, valor: str) -> str:
        if valor.casefold() not in TIPOS_OPERACION_PERMITIDOS:
            raise ValueError("tipo de operacion fuera del dominio permitido")
        return valor

    @field_validator("forma_pago")
    @classmethod
    def validar_forma_pago(cls, valor: str) -> str:
        dominio = valor.casefold()
        # Se permiten combinaciones explicitas (p. ej. "contado y transferencia")
        # si al menos una forma controlada esta presente.
        if not any(forma in dominio for forma in FORMAS_PAGO_PERMITIDAS):
            raise ValueError("forma de pago fuera del dominio permitido")
        return valor

    @field_validator("partida_registral")
    @classmethod
    def validar_partida(cls, valor: str) -> str:
        if _PARTIDA_RE.fullmatch(valor) is None:
            raise ValueError("formato de partida registral invalido")
        return valor


# Alias explicito para integraciones que nombran el contrato por el proceso.
SolicitudP01 = ContratoEntradaP01


class ErrorValidacionP01(BaseModel):
    """Error seguro para ``p1_fallidos_datos`` (no incluye el valor rechazado)."""

    model_config = ConfigDict(frozen=True)

    campo: str
    codigo: str
    mensaje: str


class ResultadoValidacionP01(BaseModel):
    """Salida tipada de los nodos ``p1_validar`` y ``p1_valida``."""

    model_config = ConfigDict(frozen=True)

    expediente_id: str
    valido: bool
    contrato: ContratoEntradaP01 | None = None
    errores: tuple[ErrorValidacionP01, ...] = ()
    nodos_bpmn: tuple[str, ...] = NODOS_VALIDACION
    siguiente_nodo: str


def validar_expediente_id(expediente_id: str) -> str:
    """Valida el identificador de aislamiento sin aceptar rutas ni espacios."""

    valor = expediente_id.strip()
    if _EXPEDIENTE_RE.fullmatch(valor) is None:
        raise ValueError("expediente_id invalido")
    return valor


def _error_seguro(error: Mapping[str, object]) -> ErrorValidacionP01:
    ubicacion = error.get("loc", ())
    campo = "contrato"
    if isinstance(ubicacion, (tuple, list)) and ubicacion:
        campo = str(ubicacion[0])
    codigo = str(error.get("type", "value_error"))
    return ErrorValidacionP01(
        campo=campo,
        codigo=codigo,
        mensaje=f"El campo {campo} no cumple el contrato P01",
    )


def validar_solicitud(
    datos: Mapping[str, object] | ContratoEntradaP01,
    *,
    expediente_id: str,
    verificar_adjunto: VerificadorAdjunto | None = None,
) -> ResultadoValidacionP01:
    """Ejecuta ``p1_validar`` y decide ``p1_valida`` sin usar un LLM.

    ``verificar_adjunto`` permite comprobar de manera inyectable la disponibilidad
    real en Drive, un almacen de objetos o pruebas. Sin adaptador, el contrato solo
    puede comprobar que exista una referencia no vacia.
    """

    expediente = validar_expediente_id(expediente_id)
    try:
        contrato = (
            datos
            if isinstance(datos, ContratoEntradaP01)
            else ContratoEntradaP01.model_validate(dict(datos))
        )
    except ValidationError as exc:
        errores = tuple(_error_seguro(error) for error in exc.errors(include_input=False))
        return ResultadoValidacionP01(
            expediente_id=expediente,
            valido=False,
            errores=errores,
            siguiente_nodo="p1_fallidos_datos",
        )

    if verificar_adjunto is not None:
        errores_adjuntos: list[ErrorValidacionP01] = []
        for campo, referencia in (
            ("adj_dni", contrato.adj_dni),
            ("adj_partida", contrato.adj_partida),
        ):
            try:
                disponible = verificar_adjunto(referencia)
            except Exception:  # el adaptador no debe filtrar secretos en el error
                disponible = False
            if not disponible:
                errores_adjuntos.append(
                    ErrorValidacionP01(
                        campo=campo,
                        codigo="adjunto_no_disponible",
                        mensaje=f"El adjunto {campo} no esta disponible",
                    )
                )
        if errores_adjuntos:
            return ResultadoValidacionP01(
                expediente_id=expediente,
                valido=False,
                errores=tuple(errores_adjuntos),
                siguiente_nodo="p1_fallidos_datos",
            )

    return ResultadoValidacionP01(
        expediente_id=expediente,
        valido=True,
        contrato=contrato,
        siguiente_nodo="p1_normalizar",
    )


def p1_validar(
    datos: Mapping[str, object] | ContratoEntradaP01,
    *,
    expediente_id: str,
    verificar_adjunto: VerificadorAdjunto | None = None,
) -> ResultadoValidacionP01:
    """Alias con el ID BPMN exacto para integracion con el orquestador."""

    return validar_solicitud(
        datos,
        expediente_id=expediente_id,
        verificar_adjunto=verificar_adjunto,
    )


def p1_valida(resultado: ResultadoValidacionP01) -> bool:
    """Evalua la compuerta exclusiva ``p1_valida``."""

    return resultado.valido


__all__ = [
    "CAMPOS_P01",
    "FORMAS_PAGO_PERMITIDAS",
    "NODOS_VALIDACION",
    "TIPOS_OPERACION_PERMITIDOS",
    "ContratoEntradaP01",
    "ErrorValidacionP01",
    "ResultadoValidacionP01",
    "SolicitudP01",
    "VerificadorAdjunto",
    "p1_valida",
    "p1_validar",
    "validar_expediente_id",
    "validar_solicitud",
]
