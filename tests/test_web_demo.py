"""Pruebas del cargador y del producto FastAPI de la FASE 3."""

from __future__ import annotations

from pathlib import Path
from tempfile import mkdtemp
from typing import cast

import pytest
from fastapi.testclient import TestClient

from nucleo.base_datos import BaseDatos
from web.app import crear_app
from web.demo_datos import cargar_datos_demo


def test_vercel_usa_base_temporal_y_aisla_instancias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(
        "web.demo_datos.mkdtemp", lambda prefix: mkdtemp(prefix=prefix, dir=tmp_path)
    )
    app_uno = crear_app()
    app_dos = crear_app()
    with TestClient(app_uno) as uno, TestClient(app_dos) as dos:
        assert app_uno.state.servicio_demo.ruta_base != app_dos.state.servicio_demo.ruta_base
        assert app_uno.state.servicio_demo.ruta_base.is_relative_to(tmp_path)
        antes = dos.get("/api/dashboard").json()
        assert (
            uno.post("/api/whatsapp", json={"contenido": "Prueba de instancia Vercel"}).status_code
            == 200
        )
        assert dos.get("/api/dashboard").json()["mensajes"] == antes["mensajes"]
        assert uno.get("/catalogo").status_code == 200
        assert uno.get("/api/minuta/EXP-2026-001/word").content.startswith(b"PK")


@pytest.mark.parametrize("ruta", ["/api/whatsapp", "/api/rag"])
@pytest.mark.parametrize("contenido", ["", "   ", "\u200b", "texto\x00", "x" * 1801, 123])
def test_texto_invalido_no_modifica_expediente(
    tmp_path: Path, ruta: str, contenido: object
) -> None:
    with TestClient(crear_app(tmp_path / "validacion.sqlite3")) as client:
        antes = client.get("/api/dashboard").json()
        respuesta = client.post(
            ruta, json={"contenido": contenido, "expediente_id": "EXP-2026-001"}
        )
        assert respuesta.status_code == 422
        despues = client.get("/api/dashboard").json()
        assert despues["mensajes"] == antes["mensajes"]
        assert despues["tareas"] == antes["tareas"]
        assert despues["seleccionado"]["fase"] == antes["seleccionado"]["fase"]


@pytest.mark.parametrize(
    "cambios",
    [
        {"observacion": "x" * 1601},
        {"observacion": "dato\x00"},
        {"kardex_notarial": "x" * 121},
        {"kardex_notarial": "\u200b"},
        {"decision": "firmar", "kardex_notarial": "   "},
    ],
)
def test_hitl_rechaza_campos_invalidos_sin_reanudar(
    tmp_path: Path, cambios: dict[str, str]
) -> None:
    with TestClient(crear_app(tmp_path / "hitl-validacion.sqlite3")) as client:
        tarea = _tarea(client, expediente_id="EXP-2026-001", nodo_bpmn_id="p3_revision")
        respuesta = client.post(
            f"/api/hitl/{tarea['tarea_id']}",
            json={"actor": "Abogado Titular", "decision": "aprobar", **cambios},
        )
        assert respuesta.status_code == 422
        pendiente = _tarea(client, expediente_id="EXP-2026-001", nodo_bpmn_id="p3_revision")
        assert pendiente["tarea_id"] == tarea["tarea_id"]


def test_texto_limite_y_aislamiento(tmp_path: Path) -> None:
    with TestClient(crear_app(tmp_path / "limites.sqlite3")) as client:
        otro = client.get("/api/dashboard", params={"expediente_id": "EXP-2026-002"}).json()
        respuesta = client.post(
            "/api/whatsapp", json={"contenido": "á" * 1800, "expediente_id": "EXP-2026-001"}
        )
        assert respuesta.status_code == 200
        despues = client.get("/api/dashboard", params={"expediente_id": "EXP-2026-002"}).json()
        assert despues["mensajes"] == otro["mensajes"]
        assert (
            client.post(
                "/api/whatsapp", json={"contenido": "Hola", "expediente_id": "../otro"}
            ).status_code
            == 422
        )


def _tarea(
    client: TestClient,
    *,
    expediente_id: str,
    nodo_bpmn_id: str,
) -> dict[str, object]:
    respuesta = client.get("/api/dashboard")
    assert respuesta.status_code == 200
    carga: object = respuesta.json()
    assert isinstance(carga, dict)
    tareas = carga["tareas"]
    assert isinstance(tareas, list)
    for tarea in tareas:
        assert isinstance(tarea, dict)
        if tarea["expediente_id"] == expediente_id and tarea["nodo_bpmn_id"] == nodo_bpmn_id:
            return cast(dict[str, object], tarea)
    raise AssertionError(f"No se encontró {nodo_bpmn_id} para {expediente_id}.")


def _resolver(
    client: TestClient,
    tarea: dict[str, object],
    *,
    decision: str,
    kardex_notarial: str | None = None,
) -> dict[str, object]:
    respuesta = client.post(
        f"/api/hitl/{tarea['tarea_id']}",
        json={
            "actor": "Abogado Titular",
            "decision": decision,
            "observacion": "Validación de prueba.",
            "kardex_notarial": kardex_notarial,
        },
    )
    assert respuesta.status_code == 200, respuesta.text
    carga: object = respuesta.json()
    assert isinstance(carga, dict)
    return cast(dict[str, object], carga)


def test_cargador_demo_siembra_cuatro_expedientes_de_forma_idempotente(tmp_path: Path) -> None:
    ruta = tmp_path / "sagale-demo.sqlite3"
    primera = cargar_datos_demo(ruta)
    segunda = cargar_datos_demo(ruta)

    assert primera["expedientes"] == 4
    assert primera["solicitudes"] == 3
    assert primera["cola_fallidos"] == 1
    assert primera["documentos_indexados"] == 9
    assert segunda["solicitudes"] == primera["solicitudes"]
    assert segunda["cola_fallidos"] == primera["cola_fallidos"]
    assert segunda["documentos_indexados"] == primera["documentos_indexados"]

    with BaseDatos(ruta) as base:
        assert base.contar("Clientes") == 4
        assert base.contar("Cola_Fallidos", "EXP-2026-004") == 1
        versiones_exp_003 = base.listar_recaudos("EXP-2026-003")
        assert {item["version_documental"] for item in versiones_exp_003} == {1}
        assert any(
            item["documento_id"] == "partida_sunarp_11048592.txt"
            for item in base.listar_recaudos("EXP-2026-001")
        )


def test_producto_web_recorridos_p01_p02_p03_y_descargas(tmp_path: Path) -> None:
    ruta = tmp_path / "sagale-web.sqlite3"
    with TestClient(crear_app(ruta)) as client:
        inicio = client.get("/")
        assert inicio.status_code == 200
        assert "Canal cliente" in inicio.text
        assert '<meta name="color-scheme" content="light">' in inicio.text
        assert '<summary>Ver detalles técnicos</summary><pre id="parser-json">' in inicio.text
        assert "Paso 2: Revisión Legal" in inicio.text
        assert 'id="analysis-summary"' in inicio.text
        assert client.get("/catalogo").status_code == 200

        tablero = client.get("/api/dashboard")
        assert tablero.status_code == 200
        datos = tablero.json()
        assert datos["metricas"]["cola_fallidos"] == 1
        assert datos["seleccionado"]["fase"] == "p3_revision"

        creado = client.post("/api/demo/solicitud")
        assert creado.status_code == 200
        assert creado.json()["codigo_http"] == 201
        repetido = client.post("/api/demo/idempotencia")
        assert repetido.status_code == 200
        assert repetido.json()["codigo_http"] == 200

        rag = client.post(
            "/api/rag",
            json={"expediente_id": "EXP-2026-001", "contenido": "partida DNI minuta"},
        )
        assert rag.status_code == 200
        assert any("partida_sunarp_11048592.txt" in cita for cita in rag.json()["citas"])

        revision = _tarea(
            client,
            expediente_id="EXP-2026-001",
            nodo_bpmn_id="p3_revision",
        )
        _resolver(client, revision, decision="aprobar")
        espera = client.get("/api/dashboard", params={"expediente_id": "EXP-2026-001"}).json()
        assert espera["seleccionado"]["fase"] == "p3_esperar"

        conforme = client.post("/api/demo/conforme")
        assert conforme.status_code == 200
        firma = _tarea(client, expediente_id="EXP-2026-001", nodo_bpmn_id="p3_firma")
        _resolver(client, firma, decision="firmar", kardex_notarial="KB-2026-01842")
        final = client.get("/api/dashboard", params={"expediente_id": "EXP-2026-001"}).json()
        assert final["seleccionado"]["fase"] == "p3_fin"
        assert final["seleccionado"]["kardex_notarial"] == "KB-2026-01842"

        markdown = client.get("/api/minuta/EXP-2026-001/markdown")
        word = client.get("/api/minuta/EXP-2026-001/word")
        assert markdown.status_code == 200
        assert "BORRADOR DE MINUTA" in markdown.text
        assert word.status_code == 200
        assert word.content.startswith(b"PK")


def test_subsanacion_reindexa_version_dos_y_habilita_revision_legal(tmp_path: Path) -> None:
    with TestClient(crear_app(tmp_path / "subsanacion.sqlite3")) as client:
        tarea = _tarea(client, expediente_id="EXP-2026-003", nodo_bpmn_id="p2_subsanacion")
        _resolver(client, tarea, decision="solicitar")
        respuesta = client.post("/api/demo/subsanacion")
        assert respuesta.status_code == 200
        tablero = client.get("/api/dashboard", params={"expediente_id": "EXP-2026-003"}).json()
        assert tablero["seleccionado"]["fase"] == "p2_legal"
        assert tablero["seleccionado"]["version_documental"] == 2
        assert tablero["seleccionado"]["parser"]["status"] == "suficiente"
        assert "copia_literal_partida_4433221100.txt" in " ".join(
            tablero["seleccionado"]["parser"]["evidencias"]
        )
