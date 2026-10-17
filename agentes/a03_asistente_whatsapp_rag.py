"""Agente A3: WhatsApp, memoria de cliente y consultas RAG con citas."""

from __future__ import annotations

import hashlib
from typing import Any, Literal, cast

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict

from agentes.agente_base import AgenteBase, EstadoAgente
from ingesta.gestor_recaudos import IndiceRAGAislado
from nucleo.base_datos import BaseDatos


class RespuestaRAG(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    respuesta: str
    citas: tuple[str, ...]
    abstencion: bool


class AgenteA3(AgenteBase):
    nombre = "A3_asistente_whatsapp_rag"

    def __init__(self, base_datos: BaseDatos, indice: IndiceRAGAislado) -> None:
        super().__init__(base_datos)
        self.indice = indice
        self.grafo_p2_respuesta = self._construir_grafo_respuesta("P02")
        self.grafo_p3_respuesta = self._construir_grafo_respuesta("P03")

    @staticmethod
    def _mensaje_id(expediente_id: str, tipo: str, contenido: str) -> str:
        material = f"{expediente_id}|{tipo}|{contenido}".encode()
        return hashlib.sha256(material).hexdigest()

    def _enviar(self, expediente_id: str, cliente_id: str, tipo: str, contenido: str) -> bool:
        mensaje_id = self._mensaje_id(expediente_id, tipo, contenido)
        return self.base_datos.registrar_mensaje_whatsapp(
            mensaje_id=mensaje_id,
            cliente_id=cliente_id,
            expediente_id=expediente_id,
            direccion="salida",
            tipo=tipo,
            contenido=contenido,
        )

    def enviar_subsanacion(
        self, expediente_id: str, cliente_id: str, observaciones: list[str]
    ) -> bool:
        detalle = "\n".join(f"- {item}" for item in observaciones)
        return self._enviar(
            expediente_id,
            cliente_id,
            "subsanacion",
            f"Se requieren los siguientes documentos o precisiones:\n{detalle}",
        )

    def enviar_borrador(self, expediente_id: str, cliente_id: str, borrador: str) -> bool:
        return self._enviar(expediente_id, cliente_id, "borrador_minuta", borrador)

    def recordar_pedido(
        self,
        *,
        cliente_id: str,
        expediente_id: str,
        contenido: str,
    ) -> int:
        return self.base_datos.agregar_memoria_cliente(
            cliente_id,
            expediente_id,
            "cliente",
            contenido,
            {"canal": "whatsapp"},
        )

    def procesar_respuesta(
        self,
        estado: EstadoAgente,
        proceso: Literal["P02", "P03"],
    ) -> dict[str, Any]:
        mensaje = estado.get("mensaje_cliente", "")
        cliente_id = estado.get("cliente_id", "cliente-no-identificado")
        mensaje_id = self._mensaje_id(estado["expediente_id"], f"respuesta-{proceso}", mensaje)
        self.base_datos.registrar_mensaje_whatsapp(
            mensaje_id=mensaje_id,
            cliente_id=cliente_id,
            expediente_id=estado["expediente_id"],
            direccion="entrada",
            tipo=f"respuesta_{proceso.casefold()}",
            contenido=mensaje,
        )
        if not self.base_datos.existe_cliente(cliente_id):
            self.base_datos.upsert_cliente(
                cliente_id,
                "Cliente WhatsApp",
                "[CONTACTO_PROTEGIDO]",
                consentimiento_pii=False,
            )
        self.recordar_pedido(
            cliente_id=cliente_id,
            expediente_id=estado["expediente_id"],
            contenido=mensaje,
        )
        nodo = "p2_respuesta" if proceso == "P02" else "p3_respuesta"
        salida = self.trazar(estado, nodo)
        historial = list(estado.get("historial_whatsapp", []))
        historial.append(
            {
                "mensaje_id": mensaje_id,
                "direccion": "entrada",
                "tipo": f"respuesta_{proceso.casefold()}",
            }
        )
        salida["historial_whatsapp"] = historial
        return salida

    def consultar_expediente(
        self,
        *,
        expediente_id: str,
        version_documental: int,
        consulta: str,
    ) -> RespuestaRAG:
        fragmentos = self.indice.recuperar(
            expediente_id=expediente_id,
            version_documental=version_documental,
            consulta=consulta,
        )
        if not fragmentos:
            return RespuestaRAG(
                respuesta="No existe evidencia indexada suficiente para responder.",
                citas=(),
                abstencion=True,
            )
        citas = tuple(
            f"{item.documento_id}@v{item.version_documental}:{item.ubicacion}"
            for item in fragmentos
        )
        extractos = " ".join(item.texto for item in fragmentos)
        return RespuestaRAG(
            respuesta=f"Según los recaudos autorizados: {extractos}",
            citas=citas,
            abstencion=False,
        )

    def _construir_grafo_respuesta(
        self, proceso: Literal["P02", "P03"]
    ) -> CompiledStateGraph[EstadoAgente, None, EstadoAgente, EstadoAgente]:
        nodo = "p2_respuesta" if proceso == "P02" else "p3_respuesta"
        grafo = StateGraph(EstadoAgente)

        def procesar(estado: EstadoAgente) -> dict[str, Any]:
            return self.procesar_respuesta(estado, proceso)

        grafo.add_node(nodo, cast(Any, procesar))
        grafo.add_edge(START, nodo)
        grafo.add_edge(nodo, END)
        return grafo.compile(checkpointer=MemorySaver(), name=f"A3-{nodo}")


__all__ = ["AgenteA3", "RespuestaRAG"]
