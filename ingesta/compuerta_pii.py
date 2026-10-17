"""Compuerta de minimización y acceso previo a IA conforme a la Ley 29733."""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_DNI = re.compile(r"(?<!\d)\d{8}(?!\d)")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_TELEFONO = re.compile(r"(?<!\d)(?:\+?51\s*)?9\d{8}(?!\d)")


class DocumentoPII(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expediente_id: str = Field(min_length=1, max_length=128)
    documento_id: str = Field(min_length=1, max_length=256)
    fuente_id: str = Field(min_length=1, max_length=512)
    contenido: str
    finalidad: str = Field(min_length=3, max_length=256)
    base_tratamiento: str = Field(min_length=3, max_length=256)
    actor_autorizado: bool
    pertenece_expediente: bool
    consentimiento_o_base_legal: bool


class ResultadoCompuertaPII(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expediente_id: str
    documento_id: str
    aprobado: bool
    contenido_minimizado: str | None
    hash_original_sha256: str
    tipos_detectados: tuple[Literal["dni", "email", "telefono"], ...]
    razones: tuple[str, ...]
    clasificacion_acceso: Literal["restringido", "bloqueado"]
    nodo_bpmn_id: Literal["p2_documentos"] = "p2_documentos"


def _detectar(contenido: str) -> tuple[Literal["dni", "email", "telefono"], ...]:
    tipos: list[Literal["dni", "email", "telefono"]] = []
    if _DNI.search(contenido):
        tipos.append("dni")
    if _EMAIL.search(contenido):
        tipos.append("email")
    if _TELEFONO.search(contenido):
        tipos.append("telefono")
    return tuple(tipos)


def minimizar_pii(contenido: str) -> str:
    """Enmascara identificadores innecesarios antes de embeddings o LLM."""

    salida = _EMAIL.sub("[EMAIL_PROTEGIDO]", contenido)
    salida = _TELEFONO.sub("[TELEFONO_PROTEGIDO]", salida)
    salida = _DNI.sub("[DNI_PROTEGIDO]", salida)
    return salida


def evaluar_compuerta_pii(documento: DocumentoPII) -> ResultadoCompuertaPII:
    """Autoriza y minimiza; una denegación nunca devuelve contenido indexable."""

    razones: list[str] = []
    if not documento.actor_autorizado:
        razones.append("actor_no_autorizado")
    if not documento.pertenece_expediente:
        razones.append("documento_ajeno_al_expediente")
    if not documento.consentimiento_o_base_legal:
        razones.append("sin_consentimiento_o_base_legal")
    if not documento.finalidad.strip() or not documento.base_tratamiento.strip():
        razones.append("finalidad_o_base_no_declarada")
    aprobado = not razones
    return ResultadoCompuertaPII(
        expediente_id=documento.expediente_id,
        documento_id=documento.documento_id,
        aprobado=aprobado,
        contenido_minimizado=minimizar_pii(documento.contenido) if aprobado else None,
        hash_original_sha256=hashlib.sha256(documento.contenido.encode("utf-8")).hexdigest(),
        tipos_detectados=_detectar(documento.contenido),
        razones=tuple(razones) if razones else ("tratamiento_autorizado_y_minimizado",),
        clasificacion_acceso="restringido" if aprobado else "bloqueado",
    )


__all__ = [
    "DocumentoPII",
    "ResultadoCompuertaPII",
    "evaluar_compuerta_pii",
    "minimizar_pii",
]
