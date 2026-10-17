"""Agente A0: registro P01 e ingesta/versionado documental de P02."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, cast

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agentes.agente_base import AgenteBase, EstadoAgente
from ingesta.gestor_recaudos import ErrorDescargaRecaudo, GestorRecaudos, RecaudoEntrada
from nucleo.base_datos import BaseDatos
from rpa.idempotencia_p01 import (
    FalloDatosP01,
    RepositorioP01SQLite,
    p1_hash,
    p1_normalizar,
)
from rpa.reintentos_p01 import (
    P1_ERROR_SHEETS,
    P1_ERROR_SISTEMA,
    ErrorSheets,
    p1_reintento,
)
from rpa.validador_p01 import ContratoEntradaP01, ErrorValidacionP01, validar_solicitud


class AgenteA0(AgenteBase):
    nombre = "A0_ingesta_registro"

    def __init__(
        self,
        base_datos: BaseDatos,
        gestor_recaudos: GestorRecaudos,
        *,
        dormir: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__(base_datos)
        self.gestor_recaudos = gestor_recaudos
        self.repositorio = RepositorioP01SQLite(base_datos.conexion)
        self._dormir = dormir
        self.grafo_p01 = self._construir_grafo_p01()

    def _simple(self, estado: EstadoAgente, nodo: str) -> dict[str, Any]:
        return self.trazar(estado, nodo)

    def p1_inicio(self, estado: EstadoAgente) -> dict[str, Any]:
        self.proceso(estado, "P01")
        return self._simple(estado, "p1_inicio")

    def p1_validar(self, estado: EstadoAgente) -> dict[str, Any]:
        resultado = validar_solicitud(
            cast(dict[str, object], estado.get("contrato_entrada", {})),
            expediente_id=estado["expediente_id"],
        )
        salida = self._simple(estado, "p1_validar")
        salida["resultado_validacion"] = resultado.model_dump(mode="json")
        return salida

    def p1_valida(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p1_valida")

    @staticmethod
    def _ruta_valida(estado: EstadoAgente) -> Literal["p1_normalizar", "p1_fallidos_datos"]:
        return (
            "p1_normalizar"
            if bool(estado.get("resultado_validacion", {}).get("valido"))
            else "p1_fallidos_datos"
        )

    def p1_normalizar(self, estado: EstadoAgente) -> dict[str, Any]:
        contrato_bruto = estado.get("resultado_validacion", {}).get("contrato")
        contrato = ContratoEntradaP01.model_validate(contrato_bruto)
        normalizado = p1_normalizar(contrato)
        salida = self._simple(estado, "p1_normalizar")
        salida["contrato_normalizado"] = normalizado.model_dump(mode="json")
        return salida

    def p1_hash(self, estado: EstadoAgente) -> dict[str, Any]:
        contrato = ContratoEntradaP01.model_validate(estado["contrato_normalizado"])
        salida = self._simple(estado, "p1_hash")
        salida["clave_idempotencia_sha256"] = p1_hash(contrato)
        return salida

    def p1_consultar(self, estado: EstadoAgente) -> dict[str, Any]:
        salida = self._simple(estado, "p1_consultar")
        if estado.get("simular_error_p1") == "sheets":
            salida["error_codigo"] = P1_ERROR_SHEETS
            return salida
        clave = estado.get("clave_idempotencia_sha256")
        if clave is None:
            raise RuntimeError("p1_hash no generó la clave.")
        try:
            registro = self.repositorio.consultar(estado["expediente_id"], clave)
        except ErrorSheets:
            salida["error_codigo"] = P1_ERROR_SHEETS
            return salida
        salida["existe_solicitud"] = registro is not None
        salida["error_codigo"] = None
        return salida

    @staticmethod
    def _ruta_consulta(estado: EstadoAgente) -> Literal["p1_error_sheets", "p1_existe"]:
        return "p1_error_sheets" if estado.get("error_codigo") == P1_ERROR_SHEETS else "p1_existe"

    def p1_existe(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p1_existe")

    @staticmethod
    def _ruta_existe(estado: EstadoAgente) -> Literal["p1_200", "p1_registrar"]:
        return "p1_200" if estado.get("existe_solicitud", False) else "p1_registrar"

    def p1_registrar(self, estado: EstadoAgente) -> dict[str, Any]:
        salida = self._simple(estado, "p1_registrar")
        if estado.get("simular_error_p1") == "sistema":
            salida["error_codigo"] = P1_ERROR_SISTEMA
            return salida
        clave = estado.get("clave_idempotencia_sha256")
        if clave is None:
            raise RuntimeError("Falta clave de idempotencia.")
        contrato = ContratoEntradaP01.model_validate(estado["contrato_normalizado"])
        try:
            creado = self.repositorio.registrar(estado["expediente_id"], clave, contrato)
        except Exception:
            salida["error_codigo"] = P1_ERROR_SISTEMA
            return salida
        salida["codigo_http"] = 201 if creado else 200
        salida["existe_solicitud"] = not creado
        salida["error_codigo"] = None
        return salida

    @staticmethod
    def _ruta_registro(
        estado: EstadoAgente,
    ) -> Literal["p1_error_sistema", "p1_200", "p1_201"]:
        if estado.get("error_codigo") == P1_ERROR_SISTEMA:
            return "p1_error_sistema"
        return "p1_200" if estado.get("codigo_http") == 200 else "p1_201"

    def p1_201(self, estado: EstadoAgente) -> dict[str, Any]:
        salida = self._simple(estado, "p1_201")
        salida["codigo_http"] = 201
        return salida

    def p1_fin_nueva(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p1_fin_nueva")

    def p1_200(self, estado: EstadoAgente) -> dict[str, Any]:
        salida = self._simple(estado, "p1_200")
        salida["codigo_http"] = 200
        return salida

    def p1_fin_duplicada(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p1_fin_duplicada")

    def p1_fallidos_datos(self, estado: EstadoAgente) -> dict[str, Any]:
        datos = estado.get("resultado_validacion", {})
        errores = tuple(
            ErrorValidacionP01.model_validate(error) for error in datos.get("errores", [])
        )
        serializado = json.dumps(
            estado.get("contrato_entrada", {}), sort_keys=True, default=str
        ).encode("utf-8")
        self.repositorio.registrar_fallido(
            FalloDatosP01(
                expediente_id=estado["expediente_id"],
                hash_entrada=hashlib.sha256(serializado).hexdigest(),
                errores=errores,
                creado_en_utc=datetime.now(UTC),
            )
        )
        return self._simple(estado, "p1_fallidos_datos")

    def p1_400(self, estado: EstadoAgente) -> dict[str, Any]:
        salida = self._simple(estado, "p1_400")
        salida["codigo_http"] = 400
        return salida

    def p1_fin_invalida(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p1_fin_invalida")

    def p1_error_sheets(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p1_error_sheets")

    def p1_reintento(self, estado: EstadoAgente) -> dict[str, Any]:
        recuperar_en = estado.get("recuperar_sheets_en_intento")
        contador = 0

        def consultar() -> bool:
            nonlocal contador
            contador += 1
            if recuperar_en is None or contador < recuperar_en:
                raise ErrorSheets("falla transitoria redactada")
            return True

        argumentos: dict[str, Any] = {}
        if self._dormir is not None:
            argumentos["dormir"] = self._dormir
        resultado = p1_reintento(consultar, **argumentos)
        salida = self._simple(estado, "p1_reintento")
        salida.update(
            {
                "existe_solicitud": False,
                "simular_error_p1": None,
                "error_codigo": None if resultado.recuperado else P1_ERROR_SHEETS,
                "reintentos_esperas": list(resultado.esperas),
            }
        )
        return salida

    def p1_recuperado(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p1_recuperado")

    @staticmethod
    def _ruta_recuperado(estado: EstadoAgente) -> Literal["p1_consultar", "p1_alertar"]:
        return "p1_alertar" if estado.get("error_codigo") == P1_ERROR_SHEETS else "p1_consultar"

    def p1_error_sistema(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p1_error_sistema")

    def p1_alertar(self, estado: EstadoAgente) -> dict[str, Any]:
        return self.preparar_hitl(self._simple(estado, "p1_alertar"), "p1_incidente")

    def p1_incidente(self, estado: EstadoAgente) -> dict[str, Any]:
        return self.exigir_decision_hitl(
            self.pausar_hitl(
                estado,
                "p1_incidente",
                {"codigo_error": estado.get("error_codigo") or P1_ERROR_SISTEMA},
                decisiones_permitidas=frozenset({"cerrar_incidente", "recuperar", "continuar"}),
            ),
            frozenset({"cerrar_incidente", "recuperar", "continuar"}),
        )

    def p1_fin_incidente(self, estado: EstadoAgente) -> dict[str, Any]:
        return self._simple(estado, "p1_fin_incidente")

    def p2_documentos(self, estado: EstadoAgente) -> dict[str, Any]:
        self.proceso(estado, "P02")
        if estado.get("simular_error_p2") == "descarga":
            salida = self._simple(estado, "p2_documentos")
            salida["error_codigo"] = "P2_ERROR_DESCARGA"
            return salida
        recaudos = [RecaudoEntrada.model_validate(item) for item in estado.get("recaudos", [])]
        try:
            resultado = self.gestor_recaudos.p2_documentos(
                expediente_id=estado["expediente_id"],
                version_documental=estado["version_documental"],
                recaudos=recaudos,
            )
        except ErrorDescargaRecaudo:
            salida = self._simple(estado, "p2_documentos")
            salida["error_codigo"] = "P2_ERROR_DESCARGA"
            return salida
        salida = self._simple(estado, "p2_documentos")
        salida.update(
            {
                "error_codigo": None,
                "documentos_aprobados": resultado.aprobados,
                "documentos_bloqueados": resultado.bloqueados,
                "citas": [
                    {"referencia": referencia} for referencia in resultado.referencias_indexadas
                ],
            }
        )
        return salida

    def p2_version(self, estado: EstadoAgente) -> dict[str, Any]:
        corregidos = estado.get("recaudos_corregidos", [])
        if not corregidos:
            raise ValueError("p2_version requiere recaudos corregidos.")
        salida = self._simple(estado, "p2_version")
        salida.update(
            {
                "version_documental": estado["version_documental"] + 1,
                "recaudos": corregidos,
                "recaudos_corregidos": [],
                "simular_error_p2": None,
            }
        )
        return salida

    def _construir_grafo_p01(
        self,
    ) -> CompiledStateGraph[EstadoAgente, None, EstadoAgente, EstadoAgente]:
        grafo = StateGraph(EstadoAgente)
        nodos = {
            "p1_inicio": self.p1_inicio,
            "p1_validar": self.p1_validar,
            "p1_valida": self.p1_valida,
            "p1_normalizar": self.p1_normalizar,
            "p1_hash": self.p1_hash,
            "p1_consultar": self.p1_consultar,
            "p1_existe": self.p1_existe,
            "p1_registrar": self.p1_registrar,
            "p1_201": self.p1_201,
            "p1_fin_nueva": self.p1_fin_nueva,
            "p1_fallidos_datos": self.p1_fallidos_datos,
            "p1_400": self.p1_400,
            "p1_fin_invalida": self.p1_fin_invalida,
            "p1_200": self.p1_200,
            "p1_fin_duplicada": self.p1_fin_duplicada,
            "p1_error_sheets": self.p1_error_sheets,
            "p1_reintento": self.p1_reintento,
            "p1_recuperado": self.p1_recuperado,
            "p1_error_sistema": self.p1_error_sistema,
            "p1_alertar": self.p1_alertar,
            "p1_incidente": self.p1_incidente,
            "p1_fin_incidente": self.p1_fin_incidente,
        }
        for nombre, funcion in nodos.items():
            grafo.add_node(nombre, cast(Any, funcion))
        grafo.add_edge(START, "p1_inicio")
        grafo.add_edge("p1_inicio", "p1_validar")
        grafo.add_edge("p1_validar", "p1_valida")
        grafo.add_conditional_edges("p1_valida", self._ruta_valida)
        grafo.add_edge("p1_normalizar", "p1_hash")
        grafo.add_edge("p1_hash", "p1_consultar")
        grafo.add_conditional_edges("p1_consultar", self._ruta_consulta)
        grafo.add_conditional_edges("p1_existe", self._ruta_existe)
        grafo.add_conditional_edges("p1_registrar", self._ruta_registro)
        grafo.add_edge("p1_201", "p1_fin_nueva")
        grafo.add_edge("p1_fin_nueva", END)
        grafo.add_edge("p1_fallidos_datos", "p1_400")
        grafo.add_edge("p1_400", "p1_fin_invalida")
        grafo.add_edge("p1_fin_invalida", END)
        grafo.add_edge("p1_200", "p1_fin_duplicada")
        grafo.add_edge("p1_fin_duplicada", END)
        grafo.add_edge("p1_error_sheets", "p1_reintento")
        grafo.add_edge("p1_reintento", "p1_recuperado")
        grafo.add_conditional_edges("p1_recuperado", self._ruta_recuperado)
        grafo.add_edge("p1_error_sistema", "p1_alertar")
        grafo.add_edge("p1_alertar", "p1_incidente")
        grafo.add_edge("p1_incidente", "p1_fin_incidente")
        grafo.add_edge("p1_fin_incidente", END)
        return grafo.compile(checkpointer=MemorySaver(), name="A0-P01")


__all__ = ["AgenteA0"]
