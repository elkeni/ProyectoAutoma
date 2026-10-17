"""Orquestador de procesos; conserva checkpoints y nunca salta controles HITL."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, cast

from langgraph.types import Command

from agentes.a00_ingesta_registro import AgenteA0
from agentes.a01_calificador_analisis import AgenteA1
from agentes.a02_redactor_cierre import AgenteA2
from agentes.a03_asistente_whatsapp_rag import AgenteA3
from agentes.agente_base import EstadoAgente, RespuestaHITL
from ingesta.gestor_recaudos import GestorRecaudos, IndiceRAGAislado
from nucleo.base_datos import BaseDatos
from nucleo.estado import JsonValue, crear_estado

Proceso = Literal["P01", "P02", "P03"]


class Orquestador:
    """Compone A0–A3 sobre una única base persistente y grafos reanudables."""

    def __init__(
        self,
        ruta_base: str | Path = "sagale.sqlite3",
        *,
        dormir: Callable[[float], None] | None = None,
        respuestas_fake: list[str] | None = None,
        proveedor_llm: str = "fake",
    ) -> None:
        self.base_datos = BaseDatos(ruta_base)
        self.indice = IndiceRAGAislado()
        self.gestor = GestorRecaudos(base_datos=self.base_datos, indice=self.indice)
        self.a0 = AgenteA0(self.base_datos, self.gestor, dormir=dormir)
        self.a1 = AgenteA1(
            self.base_datos,
            self.a0,
            proveedor_llm=proveedor_llm,
            respuestas_fake=respuestas_fake,
        )
        self.a3 = AgenteA3(self.base_datos, self.indice, proveedor_llm=proveedor_llm)
        self.a2 = AgenteA2(self.base_datos)

    @staticmethod
    def _resultado(valor: object) -> EstadoAgente:
        if not isinstance(valor, dict):
            raise TypeError("LangGraph devolvió un estado no reconocido.")
        return cast(EstadoAgente, valor)

    def iniciar_p01(
        self,
        *,
        expediente_id: str,
        contrato: Mapping[str, object],
        thread_id: str,
        simular_error: str | None = None,
        recuperar_sheets_en_intento: int | None = None,
    ) -> EstadoAgente:
        contrato_json = cast(dict[str, JsonValue], dict(contrato))
        estado = cast(
            EstadoAgente,
            crear_estado(expediente_id, contrato_entrada=contrato_json),
        )
        estado["simular_error_p1"] = simular_error
        estado["recuperar_sheets_en_intento"] = recuperar_sheets_en_intento
        return self._resultado(
            self.a0.grafo_p01.invoke(estado, config=self.a0.configuracion(thread_id))
        )

    def iniciar_p02(
        self,
        estado_anterior: EstadoAgente,
        *,
        recaudos: list[dict[str, Any]],
        thread_id: str,
        salida_llm: str | None = None,
        simular_error: str | None = None,
        evento_espera: str = "P5D",
        recaudos_corregidos: list[dict[str, Any]] | None = None,
        salida_llm_corregida: str | None = None,
        espera_interactiva: bool = False,
    ) -> EstadoAgente:
        estado = self.a0.cambiar_proceso(estado_anterior, "P02", "p2_inicio")
        estado["recaudos"] = recaudos
        estado["simular_error_p2"] = simular_error
        estado["evento_espera_p2"] = evento_espera
        if salida_llm is not None:
            estado["salida_llm"] = salida_llm
        if recaudos_corregidos is not None:
            estado["recaudos_corregidos"] = recaudos_corregidos
        if salida_llm_corregida is not None:
            estado["salida_llm_corregida"] = salida_llm_corregida
        estado["espera_interactiva_p2"] = espera_interactiva
        return self._resultado(
            self.a1.grafo.invoke(estado, config=self.a1.configuracion(thread_id))
        )

    def iniciar_p03(
        self,
        estado_anterior: EstadoAgente,
        *,
        datos_minuta: Mapping[str, Any],
        thread_id: str,
        evento_espera: str = "respuesta",
        cliente_conforme: bool = True,
        kardex_notarial: str | None = None,
        espera_interactiva: bool = False,
    ) -> EstadoAgente:
        estado = self.a0.cambiar_proceso(estado_anterior, "P03", "p3_inicio")
        estado["datos_minuta"] = dict(datos_minuta)
        estado["evento_espera_p3"] = evento_espera
        estado["cliente_conforme"] = cliente_conforme
        if kardex_notarial is not None:
            estado["kardex_notarial_entrada"] = kardex_notarial
        estado["espera_interactiva_p3"] = espera_interactiva
        return self._resultado(
            self.a2.grafo.invoke(estado, config=self.a2.configuracion(thread_id))
        )

    def reanudar(
        self,
        proceso: Proceso,
        *,
        thread_id: str,
        respuesta_humana: Mapping[str, object],
        actualizacion_estado: Mapping[str, object] | None = None,
    ) -> EstadoAgente:
        """Reanuda solo a petición explícita con una respuesta humana autenticada."""

        respuesta = RespuestaHITL.model_validate(dict(respuesta_humana))
        if not respuesta.autenticado:
            raise PermissionError("La respuesta humana no está autenticada.")
        if actualizacion_estado and set(actualizacion_estado) - {"kardex_notarial_entrada"}:
            raise ValueError("La reanudación no permite alterar identidad, versión ni flujo.")
        comando: Command[Any] = Command(
            resume=respuesta.model_dump(),
            update=dict(actualizacion_estado or {}),
        )
        if proceso == "P01":
            resultado = self.a0.grafo_p01.invoke(comando, config=self.a0.configuracion(thread_id))
        elif proceso == "P02":
            resultado = self.a1.grafo.invoke(comando, config=self.a1.configuracion(thread_id))
        else:
            resultado = self.a2.grafo.invoke(comando, config=self.a2.configuracion(thread_id))
        return self._resultado(resultado)

    def close(self) -> None:
        self.base_datos.close()

    def __enter__(self) -> Orquestador:
        return self

    def __exit__(self, _tipo: object, _valor: object, _traza: object) -> None:
        self.close()


__all__ = ["Orquestador", "Proceso"]
