"""Estado compartido, proveedores LLM, persistencia y superficie MCP."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from mcp_server.servidor_mcp import ServicioMCP, crear_servidor
from nucleo.base_datos import BaseDatos
from nucleo.estado import NODOS_HITL, crear_estado, validar_nodo_bpmn
from nucleo.modelos import get_llm


def test_get_llm_fake_es_determinista_y_sin_red() -> None:
    modelo = get_llm("fake", respuestas_fake=["uno", "dos"])
    assert isinstance(modelo, FakeListChatModel)
    assert modelo.invoke("entrada").content == "uno"
    assert modelo.invoke("entrada").content == "dos"


def test_get_llm_valida_proveedor_y_credenciales(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError):
        get_llm("desconocido")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        get_llm("gemini")


def test_estado_conserva_contrato_completo_y_siete_hitl() -> None:
    estado = crear_estado("EXP-ESTADO")
    campos = {
        "expediente_id",
        "version_documental",
        "proceso_actual",
        "nodo_bpmn_actual",
        "contrato_entrada",
        "clave_idempotencia_sha256",
        "codigo_http",
        "calificacion_json",
        "decision_sistema1",
        "estado_hitl",
        "borrador_minuta",
        "kardex_notarial",
        "historial_whatsapp",
        "trazas",
    }
    assert campos <= estado.keys()
    assert len(NODOS_HITL) == 7
    with pytest.raises(ValueError):
        validar_nodo_bpmn("P01", "p2_inicio")


def test_sqlite_persiste_tablas_memoria_hitl_y_trazas(tmp_path: Path) -> None:
    ruta = tmp_path / "persistente.sqlite3"
    with BaseDatos(ruta) as base:
        base.upsert_cliente("CLI-1", "Cliente", "contacto-protegido", consentimiento_pii=True)
        base.agregar_memoria_cliente("CLI-1", "EXP-DB", "cliente", "Deseo una minuta")
        tarea = base.crear_tarea_hitl(
            expediente_id="EXP-DB",
            version_documental=1,
            nodo_bpmn_id="p2_legal",
            solicitud={"accion": "revisar"},
        )
        assert base.resolver_tarea_hitl(tarea, actor="abogado", respuesta={"decision": "viable"})
        base.registrar_traza(
            trace_id="trace-1",
            expediente_id="EXP-DB",
            version_documental=1,
            proceso_actual="P02",
            nodo_bpmn_id="p2_legal",
            actor="abogado",
            resultado="hitl_resuelto",
        )
    with BaseDatos(ruta) as reabierta:
        assert reabierta.contar("Clientes") == 1
        assert len(reabierta.listar_memoria_cliente("CLI-1", "EXP-DB")) == 1
        assert reabierta.listar_tareas_hitl("EXP-DB", "resuelto")[0]["tarea_id"] == tarea
        assert reabierta.listar_trazas("EXP-DB")[0]["nodo_bpmn_id"] == "p2_legal"


def test_mcp_expone_solo_herramientas_segmentadas() -> None:
    with BaseDatos(":memory:") as base:
        servidor = crear_servidor(base)
        herramientas = asyncio.run(servidor.list_tools())
        nombres = {herramienta.name for herramienta in herramientas}
        assert servidor.name == "sagale-mcp"
        assert nombres == {
            "p01_estado_registro",
            "p02_recaudos_version",
            "p03_trazas_cierre",
        }
        servicio = ServicioMCP(base)
        assert servicio.estado_p01("EXP-MCP")["solicitudes"] == 0
