"""Estado serializable y tipado que comparten los grafos P01–P03."""

from __future__ import annotations

from typing import Literal, NotRequired, Required, TypeAlias, TypedDict

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
ProcesoActual = Literal["P01", "P02", "P03"]
EstadoHITL = Literal["no_aplica", "pendiente", "resuelto"]

NODOS_P01 = frozenset(
    {
        "p1_inicio",
        "p1_validar",
        "p1_valida",
        "p1_normalizar",
        "p1_hash",
        "p1_consultar",
        "p1_existe",
        "p1_registrar",
        "p1_201",
        "p1_fin_nueva",
        "p1_fallidos_datos",
        "p1_400",
        "p1_fin_invalida",
        "p1_200",
        "p1_fin_duplicada",
        "p1_error_sheets",
        "p1_error_sistema",
        "p1_reintento",
        "p1_recuperado",
        "p1_alertar",
        "p1_incidente",
        "p1_fin_incidente",
    }
)
NODOS_P02 = frozenset(
    {
        "p2_inicio",
        "p2_documentos",
        "p2_version",
        "p2_agente",
        "p2_parser",
        "p2_json",
        "p2_suficiente",
        "p2_error_ia",
        "p2_error_descarga",
        "p2_fallo",
        "p2_revision_fallo",
        "p2_legal",
        "p2_subsanar",
        "p2_viable",
        "p2_fin_viable",
        "p2_fin_descartada",
        "p2_subsanacion",
        "p2_esperar",
        "p2_respuesta",
        "p2_plazo",
        "p2_archivar",
        "p2_fin_abandono",
    }
)
NODOS_P03 = frozenset(
    {
        "p3_inicio",
        "p3_borrador",
        "p3_revision",
        "p3_aprobacion",
        "p3_corregir",
        "p3_enviar",
        "p3_esperar",
        "p3_respuesta",
        "p3_plazo",
        "p3_conforme",
        "p3_fin_sin_conformidad",
        "p3_firma",
        "p3_notaria",
        "p3_fin",
    }
)
NODOS_POR_PROCESO: dict[ProcesoActual, frozenset[str]] = {
    "P01": NODOS_P01,
    "P02": NODOS_P02,
    "P03": NODOS_P03,
}
NODOS_BPMN = NODOS_P01 | NODOS_P02 | NODOS_P03
NODOS_HITL = frozenset(
    {
        "p1_incidente",
        "p2_revision_fallo",
        "p2_legal",
        "p2_subsanacion",
        "p2_archivar",
        "p3_revision",
        "p3_firma",
    }
)
INICIO_POR_PROCESO: dict[ProcesoActual, str] = {
    "P01": "p1_inicio",
    "P02": "p2_inicio",
    "P03": "p3_inicio",
}


class EstadoExpediente(TypedDict, total=False):
    """Contrato de estado de LangGraph; admite actualizaciones parciales por nodo."""

    expediente_id: Required[str]
    version_documental: Required[int]
    proceso_actual: Required[ProcesoActual]
    nodo_bpmn_actual: Required[str]
    contrato_entrada: NotRequired[dict[str, JsonValue]]
    clave_idempotencia_sha256: NotRequired[str | None]
    codigo_http: NotRequired[int | None]
    calificacion_json: NotRequired[dict[str, JsonValue] | None]
    decision_sistema1: NotRequired[dict[str, JsonValue] | None]
    estado_hitl: NotRequired[EstadoHITL]
    borrador_minuta: NotRequired[str | None]
    kardex_notarial: NotRequired[str | None]
    historial_whatsapp: NotRequired[list[dict[str, JsonValue]]]
    trazas: NotRequired[list[dict[str, JsonValue]]]


def validar_expediente_id(expediente_id: str) -> str:
    """Valida el filtro de aislamiento obligatorio sin normalizar su identidad."""

    if not isinstance(expediente_id, str) or not expediente_id.strip():
        raise ValueError("expediente_id es obligatorio y no puede estar vacío.")
    if len(expediente_id) > 128:
        raise ValueError("expediente_id excede 128 caracteres.")
    return expediente_id


def validar_nodo_bpmn(proceso: ProcesoActual, nodo_bpmn_id: str) -> str:
    """Impide trazar IDs inventados o pertenecientes a otro proceso."""

    if nodo_bpmn_id not in NODOS_POR_PROCESO[proceso]:
        raise ValueError(f"{nodo_bpmn_id!r} no pertenece al proceso {proceso}.")
    return nodo_bpmn_id


def crear_estado(
    expediente_id: str,
    version_documental: int = 1,
    proceso_actual: ProcesoActual = "P01",
    nodo_bpmn_actual: str | None = None,
    contrato_entrada: dict[str, JsonValue] | None = None,
) -> EstadoExpediente:
    """Crea un estado completo, seguro y listo para serializar/checkpoint."""

    validar_expediente_id(expediente_id)
    if version_documental < 1:
        raise ValueError("version_documental debe ser mayor o igual que 1.")
    nodo = nodo_bpmn_actual or INICIO_POR_PROCESO[proceso_actual]
    validar_nodo_bpmn(proceso_actual, nodo)
    return EstadoExpediente(
        expediente_id=expediente_id,
        version_documental=version_documental,
        proceso_actual=proceso_actual,
        nodo_bpmn_actual=nodo,
        contrato_entrada=dict(contrato_entrada or {}),
        clave_idempotencia_sha256=None,
        codigo_http=None,
        calificacion_json=None,
        decision_sistema1=None,
        estado_hitl="no_aplica",
        borrador_minuta=None,
        kardex_notarial=None,
        historial_whatsapp=[],
        trazas=[],
    )


__all__ = [
    "EstadoExpediente",
    "EstadoHITL",
    "INICIO_POR_PROCESO",
    "JsonValue",
    "NODOS_BPMN",
    "NODOS_HITL",
    "NODOS_P01",
    "NODOS_P02",
    "NODOS_P03",
    "NODOS_POR_PROCESO",
    "ProcesoActual",
    "crear_estado",
    "validar_expediente_id",
    "validar_nodo_bpmn",
]
