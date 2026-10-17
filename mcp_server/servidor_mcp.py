"""Servidor MCP ``sagale-mcp`` con herramientas segmentadas por proceso."""

from __future__ import annotations

import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from nucleo.base_datos import BaseDatos


class ServicioMCP:
    """Fachada explícita: no expone SQL, rutas locales ni consultas globales."""

    def __init__(self, base_datos: BaseDatos) -> None:
        self.base_datos = base_datos

    def estado_p01(self, expediente_id: str) -> dict[str, object]:
        return {
            "expediente_id": expediente_id,
            "solicitudes": self.base_datos.contar("Solicitudes", expediente_id),
            "fallidos": self.base_datos.contar("Cola_Fallidos", expediente_id),
        }

    def recaudos_p02(self, expediente_id: str, version_documental: int) -> list[dict[str, Any]]:
        return self.base_datos.listar_recaudos(expediente_id, version_documental)

    def trazas_p03(self, expediente_id: str) -> list[dict[str, Any]]:
        return [
            traza
            for traza in self.base_datos.listar_trazas(expediente_id)
            if traza["proceso_actual"] == "P03"
        ]


def crear_servidor(base_datos: BaseDatos | None = None) -> FastMCP:
    """Construye una instancia aislable para producción o pruebas."""

    base = base_datos or BaseDatos(os.getenv("SAGALE_DB_PATH", ":memory:"))
    servicio = ServicioMCP(base)
    servidor = FastMCP(
        "sagale-mcp",
        instructions=(
            "Use únicamente herramientas del proceso autorizado y siempre filtre "
            "por expediente_id. Nunca solicite PII cruda."
        ),
    )

    @servidor.tool(
        name="p01_estado_registro",
        description="Consulta solo contadores P01 del expediente indicado.",
    )
    def p01_estado_registro(expediente_id: str) -> str:
        return json.dumps(servicio.estado_p01(expediente_id), ensure_ascii=False)

    @servidor.tool(
        name="p02_recaudos_version",
        description="Recupera recaudos minimizados de una versión y expediente exactos.",
    )
    def p02_recaudos_version(expediente_id: str, version_documental: int) -> str:
        datos = servicio.recaudos_p02(expediente_id, version_documental)
        return json.dumps(datos, ensure_ascii=False)

    @servidor.tool(
        name="p03_trazas_cierre",
        description="Consulta exclusivamente trazas P03 del expediente indicado.",
    )
    def p03_trazas_cierre(expediente_id: str) -> str:
        return json.dumps(servicio.trazas_p03(expediente_id), ensure_ascii=False)

    @servidor.resource("sagale://p01/{expediente_id}")
    def recurso_p01(expediente_id: str) -> str:
        return json.dumps(servicio.estado_p01(expediente_id), ensure_ascii=False)

    @servidor.resource("sagale://p02/{expediente_id}/version/{version_documental}")
    def recurso_p02(expediente_id: str, version_documental: str) -> str:
        return json.dumps(
            servicio.recaudos_p02(expediente_id, int(version_documental)),
            ensure_ascii=False,
        )

    @servidor.resource("sagale://p03/{expediente_id}")
    def recurso_p03(expediente_id: str) -> str:
        return json.dumps(servicio.trazas_p03(expediente_id), ensure_ascii=False)

    return servidor


mcp = crear_servidor()


def main() -> None:
    """Punto de entrada stdio usado por clientes MCP."""

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()


__all__ = ["ServicioMCP", "crear_servidor", "main", "mcp"]
