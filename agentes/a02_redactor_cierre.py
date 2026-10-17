"""Agente A2: redacción determinista, conformidad, firma y cierre P03."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, cast

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from agentes.agente_base import AgenteBase, EstadoAgente
from nucleo.base_datos import BaseDatos
from rpa.plantilla_minuta_p03 import DatosMinuta, ensamblar_minuta

Notificador = Callable[[str, str], bool]


class AgenteA2(AgenteBase):
    nombre = "A2_redactor_cierre"

    def __init__(self, base_datos: BaseDatos, *, notificador: Notificador | None = None) -> None:
        super().__init__(base_datos)
        self.notificador = notificador
        self.grafo = self._construir_grafo()

    def _simple(self, estado: EstadoAgente, nodo: str) -> dict[str, Any]:
        return self.trazar(estado, nodo)

    def p3_inicio(self, estado: EstadoAgente) -> dict[str, Any]:
        self.proceso(estado, "P03")
        return self._simple(estado, "p3_inicio")

    def p3_borrador(self, estado: EstadoAgente) -> dict[str, Any]:
        datos_brutos = dict(estado.get("datos_minuta", {}))
        observaciones = list(estado.get("observaciones_revision", []))
        if observaciones:
            existentes = list(datos_brutos.get("obligaciones_adicionales", []))
            datos_brutos["obligaciones_adicionales"] = [*existentes, *observaciones]
        datos = DatosMinuta.model_validate(datos_brutos)
        borrador = ensamblar_minuta(datos)
        salida = self._simple(estado, "p3_borrador")
        salida["borrador_minuta"] = borrador.contenido_markdown
        return self.preparar_hitl(salida, "p3_revision")

    def p3_revision(self, estado: EstadoAgente) -> dict[str, Any]:
        return self.exigir_decision_hitl(
            self.pausar_hitl(
                estado,
                "p3_revision",
                {
                    "accion": "revisar fondo legal y cláusulas",
                    "hash_presente": bool(estado.get("borrador_minuta")),
                },
                decisiones_permitidas=frozenset({"aprobar", "corregir"}),
            ),
            frozenset({"aprobar", "corregir"}),
        )

    def p3_aprobacion(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p3_aprobacion")

    @staticmethod
    def _ruta_aprobacion(estado: EstadoAgente) -> Literal["p3_enviar", "p3_corregir"]:
        decision = str(estado.get("respuesta_hitl", {}).get("decision", ""))
        return "p3_enviar" if decision == "aprobar" else "p3_corregir"

    def p3_corregir(self, estado: EstadoAgente) -> dict[str, Any]:
        respuesta = estado.get("respuesta_hitl", {})
        observacion = str(respuesta.get("observacion", "")).strip()
        observaciones = list(estado.get("observaciones_revision", []))
        if observacion:
            observaciones.append(observacion)
        salida = self._simple(estado, "p3_corregir")
        salida["observaciones_revision"] = observaciones
        return self.preparar_hitl(salida, "p3_revision")

    def p3_enviar(self, estado: EstadoAgente) -> dict[str, Any]:
        borrador = estado.get("borrador_minuta")
        if not borrador:
            raise RuntimeError("No existe borrador aprobado para enviar.")
        if self.notificador is not None:
            self.notificador(estado["expediente_id"], borrador)
        return self._simple(estado, "p3_enviar")

    def p3_esperar(self, estado: EstadoAgente) -> dict[str, Any]:
        salida = self._simple(estado, "p3_esperar")
        if not estado.get("espera_interactiva_p3", False):
            return salida
        respuesta = interrupt(
            {
                "evento": "p3_respuesta",
                "expediente_id": estado["expediente_id"],
                "temporizador": "PT72H",
                "instruccion": "Esperando conformidad del cliente por WhatsApp.",
            }
        )
        if not isinstance(respuesta, dict):
            raise ValueError("La respuesta del cliente debe ser un objeto.")
        decision = str(respuesta.get("decision", ""))
        if decision not in {"conforme", "correccion"}:
            raise ValueError("La respuesta del cliente no corresponde al borrador.")
        salida["evento_espera_p3"] = "respuesta"
        salida["cliente_conforme"] = decision == "conforme"
        salida["espera_interactiva_p3"] = False
        return salida

    @staticmethod
    def _ruta_espera(estado: EstadoAgente) -> Literal["p3_respuesta", "p3_plazo"]:
        return "p3_respuesta" if estado.get("evento_espera_p3") == "respuesta" else "p3_plazo"

    def p3_respuesta(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p3_respuesta")

    def p3_plazo(self, estado: EstadoAgente) -> dict[str, Any]:
        salida = self._simple(estado, "p3_plazo")
        salida["error_codigo"] = "PT72H"
        return salida

    def p3_conforme(self, estado: EstadoAgente) -> dict[str, Any]:
        salida = self._simple(estado, "p3_conforme")
        if estado.get("cliente_conforme", False):
            return self.preparar_hitl(salida, "p3_firma")
        salida["estado_hitl"] = "no_aplica"
        return salida

    @staticmethod
    def _ruta_conforme(estado: EstadoAgente) -> Literal["p3_firma", "p3_borrador"]:
        return "p3_firma" if estado.get("cliente_conforme", False) else "p3_borrador"

    def p3_fin_sin_conformidad(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p3_fin_sin_conformidad")

    def p3_firma(self, estado: EstadoAgente) -> dict[str, Any]:
        return self.exigir_decision_hitl(
            self.pausar_hitl(
                estado,
                "p3_firma",
                {"accion": "confirmar firma y sello físicos", "decision_requerida": "firmar"},
                decisiones_permitidas=frozenset({"firmar"}),
            ),
            frozenset({"firmar"}),
        )

    def p3_notaria(self, estado: EstadoAgente) -> dict[str, Any]:
        respuesta = estado.get("respuesta_hitl", {})
        if respuesta.get("decision") != "firmar":
            raise PermissionError("La minuta no cuenta con firma humana confirmada.")
        kardex = estado.get("kardex_notarial_entrada")
        if not kardex:
            raise RuntimeError("La notaría no devolvió un kárdex; no se inventará uno.")
        salida = self._simple(estado, "p3_notaria")
        salida["kardex_notarial"] = kardex
        return salida

    def p3_fin(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p3_fin")

    def _construir_grafo(
        self,
    ) -> CompiledStateGraph[EstadoAgente, None, EstadoAgente, EstadoAgente]:
        grafo = StateGraph(EstadoAgente)
        nodos = {
            "p3_inicio": self.p3_inicio,
            "p3_borrador": self.p3_borrador,
            "p3_revision": self.p3_revision,
            "p3_aprobacion": self.p3_aprobacion,
            "p3_corregir": self.p3_corregir,
            "p3_enviar": self.p3_enviar,
            "p3_esperar": self.p3_esperar,
            "p3_respuesta": self.p3_respuesta,
            "p3_plazo": self.p3_plazo,
            "p3_conforme": self.p3_conforme,
            "p3_fin_sin_conformidad": self.p3_fin_sin_conformidad,
            "p3_firma": self.p3_firma,
            "p3_notaria": self.p3_notaria,
            "p3_fin": self.p3_fin,
        }
        for nombre, funcion in nodos.items():
            grafo.add_node(nombre, cast(Any, funcion))
        grafo.add_edge(START, "p3_inicio")
        grafo.add_edge("p3_inicio", "p3_borrador")
        grafo.add_edge("p3_borrador", "p3_revision")
        grafo.add_edge("p3_revision", "p3_aprobacion")
        grafo.add_conditional_edges("p3_aprobacion", self._ruta_aprobacion)
        grafo.add_edge("p3_corregir", "p3_revision")
        grafo.add_edge("p3_enviar", "p3_esperar")
        grafo.add_conditional_edges("p3_esperar", self._ruta_espera)
        grafo.add_edge("p3_respuesta", "p3_conforme")
        grafo.add_edge("p3_plazo", "p3_fin_sin_conformidad")
        grafo.add_edge("p3_fin_sin_conformidad", END)
        grafo.add_conditional_edges("p3_conforme", self._ruta_conforme)
        grafo.add_edge("p3_firma", "p3_notaria")
        grafo.add_edge("p3_notaria", "p3_fin")
        grafo.add_edge("p3_fin", END)
        return grafo.compile(checkpointer=MemorySaver(), name="A2-P03")


__all__ = ["AgenteA2", "Notificador"]
