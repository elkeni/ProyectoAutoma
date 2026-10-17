"""Contrato común, estado operativo y controles HITL de los agentes."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, Literal, NotRequired

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field

from nucleo.base_datos import BaseDatos
from nucleo.estado import EstadoExpediente, JsonValue, ProcesoActual


class EstadoAgente(EstadoExpediente, total=False):
    """Extensión operativa; conserva íntegro el contrato transversal requerido."""

    trace_id: NotRequired[str]
    resultado_validacion: NotRequired[dict[str, Any]]
    contrato_normalizado: NotRequired[dict[str, Any]]
    existe_solicitud: NotRequired[bool]
    error_codigo: NotRequired[str | None]
    simular_error_p1: NotRequired[str | None]
    recuperar_sheets_en_intento: NotRequired[int | None]
    reintentos_esperas: NotRequired[list[int]]
    recaudos: NotRequired[list[dict[str, Any]]]
    recaudos_corregidos: NotRequired[list[dict[str, Any]]]
    salida_llm_corregida: NotRequired[str]
    documentos_aprobados: NotRequired[int]
    documentos_bloqueados: NotRequired[int]
    simular_error_p2: NotRequired[str | None]
    salida_llm: NotRequired[str]
    json_valido: NotRequired[bool]
    suficiente: NotRequired[bool]
    viable: NotRequired[bool]
    requiere_subsanacion: NotRequired[bool]
    respuesta_hitl: NotRequired[dict[str, Any]]
    evento_espera_p2: NotRequired[str]
    evento_espera_p3: NotRequired[str]
    espera_interactiva_p2: NotRequired[bool]
    espera_interactiva_p3: NotRequired[bool]
    cliente_conforme: NotRequired[bool]
    datos_minuta: NotRequired[dict[str, Any]]
    observaciones_revision: NotRequired[list[str]]
    kardex_notarial_entrada: NotRequired[str]
    cliente_id: NotRequired[str]
    mensaje_cliente: NotRequired[str]
    citas: NotRequired[list[dict[str, Any]]]


class RespuestaHITL(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    actor: str = Field(min_length=2, max_length=200)
    autenticado: bool = Field(strict=True)
    decision: str = Field(min_length=1, max_length=80)
    observacion: str = Field(default="", max_length=2000)


class AgenteBase:
    """Ofrece trazas redactadas y una única forma de pausar/reanudar HITL."""

    nombre = "agente-base"

    def __init__(self, base_datos: BaseDatos) -> None:
        self.base_datos = base_datos

    @staticmethod
    def configuracion(thread_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": thread_id}}

    def trazar(
        self,
        estado: EstadoAgente,
        nodo_bpmn_id: str,
        *,
        resultado: str = "completado",
        metadatos: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        trace_id = estado.get("trace_id") or str(uuid.uuid4())
        traza: dict[str, JsonValue] = {
            "trace_id": trace_id,
            "expediente_id": estado["expediente_id"],
            "version_documental": estado["version_documental"],
            "proceso_actual": estado["proceso_actual"],
            "nodo_bpmn_id": nodo_bpmn_id,
            "actor": self.nombre,
            "resultado": resultado,
        }
        trazas = list(estado.get("trazas", []))
        trazas.append(traza)
        self.base_datos.registrar_traza(
            trace_id=trace_id,
            expediente_id=estado["expediente_id"],
            version_documental=estado["version_documental"],
            proceso_actual=estado["proceso_actual"],
            nodo_bpmn_id=nodo_bpmn_id,
            actor=self.nombre,
            resultado=resultado,
            metadatos=metadatos,
        )
        return {
            "trace_id": trace_id,
            "nodo_bpmn_actual": nodo_bpmn_id,
            "trazas": trazas,
        }

    def pausar_hitl(
        self,
        estado: EstadoAgente,
        nodo_bpmn_id: str,
        solicitud: Mapping[str, object],
        *,
        decisiones_permitidas: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        tarea_id = self.base_datos.crear_tarea_hitl(
            expediente_id=estado["expediente_id"],
            version_documental=estado["version_documental"],
            nodo_bpmn_id=nodo_bpmn_id,
            solicitud=solicitud,
        )
        bruto = interrupt(
            {
                "tarea_id": tarea_id,
                "expediente_id": estado["expediente_id"],
                "version_documental": estado["version_documental"],
                "nodo_bpmn_id": nodo_bpmn_id,
                "solicitud": dict(solicitud),
            }
        )
        if not isinstance(bruto, dict):
            raise ValueError("La reanudación HITL debe ser un objeto autenticado.")
        respuesta = RespuestaHITL.model_validate(bruto)
        if not respuesta.autenticado:
            raise PermissionError("La respuesta HITL no está autenticada.")
        if decisiones_permitidas is not None and respuesta.decision not in decisiones_permitidas:
            raise ValueError(f"Decisión HITL no admitida: {respuesta.decision!r}.")
        payload = respuesta.model_dump(mode="json")
        if not self.base_datos.resolver_tarea_hitl(
            tarea_id,
            actor=respuesta.actor,
            respuesta=payload,
        ):
            raise RuntimeError("La tarea HITL no estaba pendiente.")
        actualizacion = self.trazar(
            estado,
            nodo_bpmn_id,
            resultado="hitl_resuelto",
            metadatos={"tarea_id": tarea_id, "actor": respuesta.actor},
        )
        actualizacion.update(
            {
                "estado_hitl": "resuelto",
                "respuesta_hitl": dict(bruto),
            }
        )
        return actualizacion

    @staticmethod
    def preparar_hitl(actualizacion: dict[str, Any], nodo_bpmn_id: str) -> dict[str, Any]:
        """Marca el nodo humano como estado actual antes de que ``interrupt`` pause."""

        actualizacion["nodo_bpmn_actual"] = nodo_bpmn_id
        actualizacion["estado_hitl"] = "pendiente"
        return actualizacion

    @staticmethod
    def exigir_decision_hitl(
        actualizacion: dict[str, Any], permitidas: frozenset[str]
    ) -> dict[str, Any]:
        respuesta = actualizacion.get("respuesta_hitl", {})
        decision = str(respuesta.get("decision", ""))
        if decision not in permitidas:
            raise ValueError(f"Decisión HITL no admitida: {decision!r}.")
        return actualizacion

    @staticmethod
    def proceso(estado: EstadoAgente, esperado: Literal["P01", "P02", "P03"]) -> None:
        if estado["proceso_actual"] != esperado:
            raise ValueError(f"El estado no pertenece a {esperado}.")

    @staticmethod
    def cambiar_proceso(
        estado: EstadoAgente, proceso: ProcesoActual, nodo_inicial: str
    ) -> EstadoAgente:
        nuevo = EstadoAgente(**estado)
        nuevo["proceso_actual"] = proceso
        nuevo["nodo_bpmn_actual"] = nodo_inicial
        nuevo["estado_hitl"] = "no_aplica"
        return nuevo


__all__ = ["AgenteBase", "EstadoAgente", "RespuestaHITL"]
