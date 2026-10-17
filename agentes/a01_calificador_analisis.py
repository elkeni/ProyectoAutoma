"""Agente A1: calificación, revisión jurídica y subsanación P02."""

from __future__ import annotations

import json
from typing import Any, Final, Literal, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agentes.a00_ingesta_registro import AgenteA0
from agentes.agente_base import AgenteBase, EstadoAgente
from decision.decisor import DecisionEntrada, decide
from nucleo.base_datos import BaseDatos
from nucleo.modelos import get_llm

P2_ERROR_IA: Final = "P2_ERROR_IA"
P2_ERROR_DESCARGA: Final = "P2_ERROR_DESCARGA"


class CitaP02(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expediente_id: str
    version_documental: int = Field(ge=1)
    documento_id: str
    ubicacion: str


class CalificacionP02(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    suficiente: bool
    viable: bool
    requiere_subsanacion: bool
    probabilidad: float = Field(ge=0.0, le=1.0)
    observaciones: tuple[str, ...] = ()
    citas: tuple[CitaP02, ...] = ()

    @model_validator(mode="after")
    def exigir_evidencia(self) -> CalificacionP02:
        if self.suficiente and not self.citas:
            raise ValueError("Una calificación suficiente requiere al menos una cita.")
        if self.viable and not self.suficiente:
            raise ValueError("Un expediente no puede ser viable con evidencia insuficiente.")
        return self


class AgenteA1(AgenteBase):
    nombre = "A1_calificador_analisis"

    def __init__(
        self,
        base_datos: BaseDatos,
        agente_a0: AgenteA0,
        *,
        proveedor_llm: str = "fake",
        respuestas_fake: list[str] | None = None,
    ) -> None:
        super().__init__(base_datos)
        self.a0 = agente_a0
        self._proveedor_llm = proveedor_llm
        self._respuestas_fake = respuestas_fake
        self._llm: BaseChatModel | None = None
        self.grafo = self._construir_grafo()

    def _simple(self, estado: EstadoAgente, nodo: str) -> dict[str, Any]:
        return self.trazar(estado, nodo)

    def p2_inicio(self, estado: EstadoAgente) -> dict[str, Any]:
        self.proceso(estado, "P02")
        return self._simple(estado, "p2_inicio")

    def p2_documentos(self, estado: EstadoAgente) -> dict[str, Any]:
        return self.a0.p2_documentos(estado)

    @staticmethod
    def _ruta_documentos(estado: EstadoAgente) -> Literal["p2_error_descarga", "p2_agente"]:
        return (
            "p2_error_descarga" if estado.get("error_codigo") == P2_ERROR_DESCARGA else "p2_agente"
        )

    def p2_agente(self, estado: EstadoAgente) -> dict[str, Any]:
        salida = self._simple(estado, "p2_agente")
        if estado.get("simular_error_p2") == "ia":
            salida["error_codigo"] = P2_ERROR_IA
            return salida
        if estado.get("documentos_aprobados", 0) == 0:
            salida["salida_llm"] = json.dumps(
                {
                    "suficiente": False,
                    "viable": False,
                    "requiere_subsanacion": True,
                    "probabilidad": 0.0,
                    "observaciones": ["No existen recaudos autorizados e indexables."],
                    "citas": [],
                }
            )
            salida["error_codigo"] = None
            return salida
        evidencia = self.a0.gestor_recaudos.indice.recuperar(
            expediente_id=estado["expediente_id"],
            version_documental=estado["version_documental"],
            consulta="titularidad gravámenes suficiencia viabilidad",
        )
        prompt = {
            "tarea": "calificar expediente sin sustituir la revisión jurídica",
            "expediente_id": estado["expediente_id"],
            "version_documental": estado["version_documental"],
            "evidencia": [item.model_dump(mode="json") for item in evidencia],
            "esquema": CalificacionP02.model_json_schema(),
        }
        if not estado.get("salida_llm"):
            try:
                if self._llm is None:
                    self._llm = get_llm(
                        self._proveedor_llm,
                        respuestas_fake=self._respuestas_fake,
                    )
                mensaje = self._llm.invoke([HumanMessage(content=json.dumps(prompt))])
                contenido = mensaje.content
                salida["salida_llm"] = (
                    contenido if isinstance(contenido, str) else json.dumps(contenido)
                )
            except Exception:
                salida["error_codigo"] = P2_ERROR_IA
                return salida
        salida["error_codigo"] = None
        return salida

    @staticmethod
    def _ruta_agente(estado: EstadoAgente) -> Literal["p2_error_ia", "p2_parser"]:
        return "p2_error_ia" if estado.get("error_codigo") == P2_ERROR_IA else "p2_parser"

    def p2_parser(self, estado: EstadoAgente) -> dict[str, Any]:
        salida = self._simple(estado, "p2_parser")
        if estado.get("simular_error_p2") == "json":
            salida["json_valido"] = False
            return salida
        try:
            bruto = json.loads(estado.get("salida_llm", ""))
            calificacion = CalificacionP02.model_validate(bruto)
            for cita in calificacion.citas:
                if (
                    cita.expediente_id != estado["expediente_id"]
                    or cita.version_documental != estado["version_documental"]
                ):
                    raise ValueError("Cita ajena al expediente o versión.")
                if not self.a0.gestor_recaudos.indice.contiene_cita(
                    expediente_id=cita.expediente_id,
                    version_documental=cita.version_documental,
                    documento_id=cita.documento_id,
                    ubicacion=cita.ubicacion,
                ):
                    raise ValueError("La cita no existe en el índice aislado.")
        except (json.JSONDecodeError, ValidationError, ValueError):
            salida["json_valido"] = False
            return salida
        decision = decide(
            DecisionEntrada(
                probabilidad=calificacion.probabilidad,
                evidencia_suficiente=calificacion.suficiente,
                opcion="continuar_calificacion",
                expediente_id=estado["expediente_id"],
                version_documental=estado["version_documental"],
            )
        )
        salida.update(
            {
                "json_valido": True,
                "suficiente": calificacion.suficiente,
                "viable": calificacion.viable,
                "requiere_subsanacion": calificacion.requiere_subsanacion,
                "calificacion_json": calificacion.model_dump(mode="json"),
                "decision_sistema1": decision.model_dump(mode="json"),
            }
        )
        return salida

    def p2_json(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p2_json")

    @staticmethod
    def _ruta_json(estado: EstadoAgente) -> Literal["p2_suficiente", "p2_fallo"]:
        return "p2_suficiente" if estado.get("json_valido", False) else "p2_fallo"

    def p2_suficiente(self, estado: EstadoAgente) -> dict[str, Any]:
        siguiente = "p2_legal" if estado.get("suficiente", False) else "p2_subsanacion"
        return self.preparar_hitl(self._simple(estado, "p2_suficiente"), siguiente)

    @staticmethod
    def _ruta_suficiente(estado: EstadoAgente) -> Literal["p2_legal", "p2_subsanacion"]:
        return "p2_legal" if estado.get("suficiente", False) else "p2_subsanacion"

    def p2_error_ia(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p2_error_ia")

    def p2_error_descarga(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p2_error_descarga")

    def p2_fallo(self, estado: EstadoAgente) -> dict[str, Any]:
        return self.preparar_hitl(self._simple(estado, "p2_fallo"), "p2_revision_fallo")

    def p2_revision_fallo(self, estado: EstadoAgente) -> dict[str, Any]:
        salida = self.exigir_decision_hitl(
            self.pausar_hitl(
                estado,
                "p2_revision_fallo",
                {"codigo_error": estado.get("error_codigo") or "JSON_INVALIDO"},
                decisiones_permitidas=frozenset({"continuar"}),
            ),
            frozenset({"continuar"}),
        )
        return self.preparar_hitl(salida, "p2_legal")

    def p2_legal(self, estado: EstadoAgente) -> dict[str, Any]:
        return self.exigir_decision_hitl(
            self.pausar_hitl(
                estado,
                "p2_legal",
                {
                    "calificacion": estado.get("calificacion_json"),
                    "decisiones": ["subsanar", "viable", "descartar"],
                },
                decisiones_permitidas=frozenset({"subsanar", "viable", "descartar"}),
            ),
            frozenset({"subsanar", "viable", "descartar"}),
        )

    def p2_subsanar(self, estado: EstadoAgente) -> dict[str, Any]:
        salida = self._simple(estado, "p2_subsanar")
        if str(estado.get("respuesta_hitl", {}).get("decision", "")) == "subsanar":
            return self.preparar_hitl(salida, "p2_subsanacion")
        salida["estado_hitl"] = "no_aplica"
        return salida

    @staticmethod
    def _ruta_subsanar(estado: EstadoAgente) -> Literal["p2_subsanacion", "p2_viable"]:
        decision = str(estado.get("respuesta_hitl", {}).get("decision", ""))
        return "p2_subsanacion" if decision == "subsanar" else "p2_viable"

    def p2_viable(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p2_viable")

    @staticmethod
    def _ruta_viable(estado: EstadoAgente) -> Literal["p2_fin_viable", "p2_fin_descartada"]:
        decision = str(estado.get("respuesta_hitl", {}).get("decision", ""))
        return "p2_fin_viable" if decision == "viable" else "p2_fin_descartada"

    def p2_fin_viable(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p2_fin_viable")

    def p2_fin_descartada(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p2_fin_descartada")

    def p2_subsanacion(self, estado: EstadoAgente) -> dict[str, Any]:
        return self.exigir_decision_hitl(
            self.pausar_hitl(
                estado,
                "p2_subsanacion",
                {"accion": "revisar y autorizar solicitud de subsanación"},
                decisiones_permitidas=frozenset({"notificar", "solicitar"}),
            ),
            frozenset({"notificar", "solicitar"}),
        )

    def p2_esperar(self, estado: EstadoAgente) -> dict[str, Any]:
        salida = self._simple(estado, "p2_esperar")
        if not estado.get("espera_interactiva_p2", False):
            return salida
        respuesta = interrupt(
            {
                "evento": "p2_respuesta",
                "expediente_id": estado["expediente_id"],
                "instruccion": "Esperando documento corregido por WhatsApp.",
            }
        )
        if not isinstance(respuesta, dict):
            raise ValueError("La respuesta del cliente debe ser un objeto.")
        decision = str(respuesta.get("decision", ""))
        if decision not in {"respuesta", "subsanado"}:
            raise ValueError("La respuesta del cliente no habilita p2_version.")
        salida["evento_espera_p2"] = "respuesta"
        salida["espera_interactiva_p2"] = False
        return salida

    @staticmethod
    def _ruta_espera(estado: EstadoAgente) -> Literal["p2_respuesta", "p2_plazo"]:
        return "p2_respuesta" if estado.get("evento_espera_p2") == "respuesta" else "p2_plazo"

    def p2_respuesta(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p2_respuesta")

    def p2_version(self, estado: EstadoAgente) -> dict[str, Any]:
        salida = self.a0.p2_version(estado)
        salida["salida_llm"] = str(estado.get("salida_llm_corregida", ""))
        return salida

    def p2_plazo(self, estado: EstadoAgente) -> dict[str, Any]:
        salida = self._simple(estado, "p2_plazo")
        salida["error_codigo"] = "P5D"
        return self.preparar_hitl(salida, "p2_archivar")

    def p2_archivar(self, estado: EstadoAgente) -> dict[str, Any]:
        return self.exigir_decision_hitl(
            self.pausar_hitl(
                estado,
                "p2_archivar",
                {"temporizador": "P5D", "accion": "confirmar archivo por abandono"},
                decisiones_permitidas=frozenset({"archivar"}),
            ),
            frozenset({"archivar"}),
        )

    def p2_fin_abandono(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p2_fin_abandono")

    def _construir_grafo(
        self,
    ) -> CompiledStateGraph[EstadoAgente, None, EstadoAgente, EstadoAgente]:
        grafo = StateGraph(EstadoAgente)
        nodos = {
            "p2_inicio": self.p2_inicio,
            "p2_documentos": self.p2_documentos,
            "p2_version": self.p2_version,
            "p2_agente": self.p2_agente,
            "p2_parser": self.p2_parser,
            "p2_json": self.p2_json,
            "p2_suficiente": self.p2_suficiente,
            "p2_error_ia": self.p2_error_ia,
            "p2_error_descarga": self.p2_error_descarga,
            "p2_fallo": self.p2_fallo,
            "p2_revision_fallo": self.p2_revision_fallo,
            "p2_legal": self.p2_legal,
            "p2_subsanar": self.p2_subsanar,
            "p2_viable": self.p2_viable,
            "p2_fin_viable": self.p2_fin_viable,
            "p2_fin_descartada": self.p2_fin_descartada,
            "p2_subsanacion": self.p2_subsanacion,
            "p2_esperar": self.p2_esperar,
            "p2_respuesta": self.p2_respuesta,
            "p2_plazo": self.p2_plazo,
            "p2_archivar": self.p2_archivar,
            "p2_fin_abandono": self.p2_fin_abandono,
        }
        for nombre, funcion in nodos.items():
            grafo.add_node(nombre, cast(Any, funcion))
        grafo.add_edge(START, "p2_inicio")
        grafo.add_edge("p2_inicio", "p2_documentos")
        grafo.add_conditional_edges("p2_documentos", self._ruta_documentos)
        grafo.add_conditional_edges("p2_agente", self._ruta_agente)
        grafo.add_edge("p2_parser", "p2_json")
        grafo.add_conditional_edges("p2_json", self._ruta_json)
        grafo.add_conditional_edges("p2_suficiente", self._ruta_suficiente)
        grafo.add_edge("p2_error_ia", "p2_fallo")
        grafo.add_edge("p2_error_descarga", "p2_fallo")
        grafo.add_edge("p2_fallo", "p2_revision_fallo")
        grafo.add_edge("p2_revision_fallo", "p2_legal")
        grafo.add_edge("p2_legal", "p2_subsanar")
        grafo.add_conditional_edges("p2_subsanar", self._ruta_subsanar)
        grafo.add_conditional_edges("p2_viable", self._ruta_viable)
        grafo.add_edge("p2_fin_viable", END)
        grafo.add_edge("p2_fin_descartada", END)
        grafo.add_edge("p2_subsanacion", "p2_esperar")
        grafo.add_conditional_edges("p2_esperar", self._ruta_espera)
        grafo.add_edge("p2_respuesta", "p2_version")
        grafo.add_edge("p2_version", "p2_documentos")
        grafo.add_edge("p2_plazo", "p2_archivar")
        grafo.add_edge("p2_archivar", "p2_fin_abandono")
        grafo.add_edge("p2_fin_abandono", END)
        return grafo.compile(checkpointer=MemorySaver(), name="A1-P02")


__all__ = [
    "P2_ERROR_DESCARGA",
    "P2_ERROR_IA",
    "AgenteA1",
    "CalificacionP02",
    "CitaP02",
]
