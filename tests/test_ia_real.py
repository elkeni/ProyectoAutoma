"""Integración de IA con respuestas controladas; la suite no consume APIs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nucleo.modelos import get_llm, seleccionar_modelo_gemini
from web.app import EXPEDIENTE_EN_VIVO, crear_app

TOKEN_PRUEBA = "acceso-sintetico-exclusivo-de-pruebas"
AUTH = {"Authorization": "Bearer " + TOKEN_PRUEBA}


@pytest.fixture
def acceso(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "clave-sintetica-no-valida")
    monkeypatch.setenv("SAGALE_AI_ACCESS_TOKEN", TOKEN_PRUEBA)
    monkeypatch.delenv("SAGALE_AI_TEST_TOKEN", raising=False)


@pytest.mark.parametrize("ruta", ["/api/ia/consultar", "/api/ia/analizar"])
def test_ia_requiere_acceso_privado(tmp_path: Path, acceso: None, ruta: str) -> None:
    with TestClient(crear_app(tmp_path / "acceso.sqlite3")) as client:
        datos: dict[str, object] = {"expediente_id": "EXP-2026-001"}
        if ruta.endswith("consultar"):
            datos["contenido"] = "partida"
        assert client.post(ruta, json=datos).status_code == 401
        assert (
            client.post(
                ruta, json=datos, headers={"Authorization": "Bearer incorrecto"}
            ).status_code
            == 401
        )


@pytest.mark.parametrize("citas,abstencion", [(["otra@v2:fragmento-99"], False), ([], False)])
def test_rechaza_citas_inventadas(
    tmp_path: Path,
    acceso: None,
    monkeypatch: pytest.MonkeyPatch,
    citas: list[str],
    abstencion: bool,
) -> None:
    modelo = get_llm(
        "fake",
        respuestas_fake=[
            json.dumps({"respuesta": "Hay evidencia", "citas": citas, "abstencion": abstencion})
        ],
    )
    monkeypatch.setattr("agentes.a03_asistente_whatsapp_rag.get_llm", lambda *a, **kw: modelo)
    with TestClient(crear_app(tmp_path / "citas.sqlite3")) as client:
        respuesta = client.post("/api/ia/consultar", json={"contenido": "partida"}, headers=AUTH)
        assert respuesta.status_code == 502
        assert "otra@" not in respuesta.text


def test_ia_cita_evidencia_limita_uso_y_no_revela_secretos(
    tmp_path: Path, acceso: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    modelo = get_llm(
        "fake",
        respuestas_fake=[
            json.dumps(
                {
                    "respuesta": "La partida no registra gravámenes.",
                    "citas": ["partida_sunarp_11048592.txt@v1:fragmento-1"],
                    "abstencion": False,
                }
            )
        ],
    )
    monkeypatch.setattr("agentes.a03_asistente_whatsapp_rag.get_llm", lambda *a, **kw: modelo)
    with TestClient(crear_app(tmp_path / "cuota.sqlite3")) as client:
        respuesta = client.post("/api/ia/consultar", json={"contenido": "partida"}, headers=AUTH)
        assert respuesta.status_code == 200
        assert respuesta.json()["citas"]
        assert TOKEN_PRUEBA not in respuesta.text
        assert (
            client.post("/api/ia/consultar", json={"contenido": "otra"}, headers=AUTH).status_code
            == 429
        )
        assert TOKEN_PRUEBA not in client.get("/api/dashboard").text
        assert "clave-sintetica" not in client.get("/api/ia/estado").text


def test_sin_evidencia_no_invoca_modelo(
    tmp_path: Path, acceso: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def prohibido(*a: Any, **kw: Any) -> None:
        raise AssertionError("No se debe llamar a IA sin evidencia")

    monkeypatch.setattr("agentes.a03_asistente_whatsapp_rag.get_llm", prohibido)
    with TestClient(crear_app(tmp_path / "sin-evidencia.sqlite3")) as client:
        respuesta = client.post(
            "/api/ia/consultar",
            json={"contenido": "partida", "expediente_id": "EXP-2026-004"},
            headers=AUTH,
        )
        assert respuesta.status_code == 200
        assert respuesta.json()["abstencion"]


def test_nueva_solicitud_conserva_evidencia_hitl_y_cierre(
    tmp_path: Path, acceso: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(crear_app(tmp_path / "nuevo-integral.sqlite3")) as client:
        creado = client.post("/api/demo/nueva-solicitud").json()
        expediente = creado["expediente_id"]
        partida = "DEMO-" + expediente.rsplit("-", 1)[1]
        modelo = get_llm(
            "fake",
            respuestas_fake=[
                json.dumps(
                    {
                        "suficiente": True,
                        "viable": True,
                        "requiere_subsanacion": False,
                        "probabilidad": 0.9,
                        "observaciones": ["Evidencia completa."],
                        "citas": [
                            {
                                "expediente_id": expediente,
                                "version_documental": 1,
                                "documento_id": f"partida_sunarp_{partida}.txt",
                                "ubicacion": "fragmento-1",
                            }
                        ],
                    }
                )
            ],
        )
        monkeypatch.setattr("agentes.a01_calificador_analisis.get_llm", lambda *a, **kw: modelo)
        assert (
            client.post(
                "/api/ia/analizar", json={"expediente_id": expediente}, headers=AUTH
            ).json()["fase"]
            == "p2_legal"
        )
        for nodo, decision in (
            ("p2_legal", "viable"),
            ("p3_revision", "aprobar"),
            ("p3_firma", "firmar"),
        ):
            if nodo == "p3_firma":
                assert (
                    client.post(
                        "/api/demo/conforme", json={"expediente_id": expediente}
                    ).status_code
                    == 200
                )
            tablero = client.get("/api/dashboard", params={"expediente_id": expediente}).json()
            tarea = next(
                t
                for t in tablero["tareas"]
                if t["expediente_id"] == expediente and t["nodo_bpmn_id"] == nodo
            )
            assert (
                client.post(
                    f"/api/hitl/{tarea['tarea_id']}",
                    json={
                        "actor": "Abogado Titular",
                        "decision": decision,
                        "kardex_notarial": "KB-DEMO-NUEVO" if nodo == "p3_firma" else None,
                    },
                ).status_code
                == 200
            )
        markdown = client.get(f"/api/minuta/{expediente}/markdown")
        assert partida in markdown.text
        assert client.get(f"/api/minuta/{expediente}/word").content.startswith(b"PK")
        assert (
            client.post("/api/demo/idempotencia", json={"expediente_id": expediente}).json()[
                "codigo_http"
            ]
            == 200
        )
        tablero = client.get("/api/dashboard", params={"expediente_id": expediente}).json()
        assert tablero["seleccionado"]["fase"] == "p3_fin"


def test_p02_ia_real_simulada_conserva_hitl_y_cierre(
    tmp_path: Path, acceso: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    modelo = get_llm(
        "fake",
        respuestas_fake=[
            json.dumps(
                {
                    "suficiente": True,
                    "viable": True,
                    "requiere_subsanacion": False,
                    "probabilidad": 0.9,
                    "observaciones": ["Evidencia documental completa."],
                    "citas": [
                        {
                            "expediente_id": EXPEDIENTE_EN_VIVO,
                            "version_documental": 1,
                            "documento_id": "partida_sunarp_11048592.txt",
                            "ubicacion": "fragmento-1",
                        }
                    ],
                }
            )
        ],
    )
    monkeypatch.setattr("agentes.a01_calificador_analisis.get_llm", lambda *a, **kw: modelo)
    with TestClient(crear_app(tmp_path / "integral.sqlite3")) as client:
        assert client.post("/api/demo/solicitud").status_code == 200
        analisis = client.post(
            "/api/ia/analizar", json={"expediente_id": EXPEDIENTE_EN_VIVO}, headers=AUTH
        )
        assert analisis.status_code == 200
        assert analisis.json()["fase"] == "p2_legal"

        def tablero() -> dict[str, Any]:
            return dict(
                client.get("/api/dashboard", params={"expediente_id": EXPEDIENTE_EN_VIVO}).json()
            )

        def resolver(nodo: str, decision: str, kardex: str | None = None) -> None:
            tarea = next(
                t
                for t in tablero()["tareas"]
                if t["expediente_id"] == EXPEDIENTE_EN_VIVO and t["nodo_bpmn_id"] == nodo
            )
            respuesta = client.post(
                f"/api/hitl/{tarea['tarea_id']}",
                json={"actor": "Abogado Titular", "decision": decision, "kardex_notarial": kardex},
            )
            assert respuesta.status_code == 200

        resolver("p2_legal", "viable")
        assert tablero()["seleccionado"]["fase"] == "p3_revision"
        resolver("p3_revision", "aprobar")
        assert (
            client.post(
                "/api/demo/conforme", json={"expediente_id": EXPEDIENTE_EN_VIVO}
            ).status_code
            == 200
        )
        resolver("p3_firma", "firmar", "KB-IA-PRUEBA")
        assert tablero()["seleccionado"]["fase"] == "p3_fin"
        assert client.get(f"/api/minuta/{EXPEDIENTE_EN_VIVO}/word").content.startswith(b"PK")
        assert client.post("/api/demo/idempotencia").json()["codigo_http"] == 200
        assert tablero()["seleccionado"]["fase"] == "p3_fin"
        assert (
            client.post(
                "/api/ia/analizar", json={"expediente_id": EXPEDIENTE_EN_VIVO}, headers=AUTH
            ).status_code
            == 409
        )


def test_fallo_ia_p02_termina_en_revision_humana(
    tmp_path: Path, acceso: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fallo(*a: Any, **kw: Any) -> None:
        raise TimeoutError("detalle no publicable de proveedor")

    monkeypatch.setattr("agentes.a01_calificador_analisis.get_llm", fallo)
    with TestClient(crear_app(tmp_path / "fallo.sqlite3")) as client:
        client.post("/api/demo/solicitud")
        respuesta = client.post(
            "/api/ia/analizar", json={"expediente_id": EXPEDIENTE_EN_VIVO}, headers=AUTH
        )
        assert respuesta.json()["fase"] == "p2_revision_fallo"
        assert "detalle no publicable" not in client.get("/api/dashboard").text


def test_consulta_minimiza_pii_antes_del_modelo(
    tmp_path: Path, acceso: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    modelo = get_llm("fake", respuestas_fake=["no usado"])
    capturado: list[str] = []

    def capturar(self: Any, messages: list[Any], **kwargs: Any) -> str:
        capturado.append(str(messages))
        return json.dumps(
            {
                "respuesta": "Documento identificado.",
                "citas": ["dni_vendedor.txt@v1:fragmento-1"],
                "abstencion": False,
            }
        )

    monkeypatch.setattr(type(modelo), "_call", capturar)
    monkeypatch.setattr("agentes.a03_asistente_whatsapp_rag.get_llm", lambda *a, **kw: modelo)
    with TestClient(crear_app(tmp_path / "pii.sqlite3")) as client:
        respuesta = client.post(
            "/api/ia/consultar",
            json={"contenido": "Mi DNI 98765432 y correo prueba-secreta@example.com; consulta DNI"},
            headers=AUTH,
        )
        assert respuesta.status_code == 200
        assert capturado
        assert "98765432" not in capturado[0]
        assert "prueba-secreta@example.com" not in capturado[0]
        assert "prueba-secreta@example.com" not in client.get("/api/dashboard").text


def test_seleccion_modelo_usa_catalogo_no_suscripcion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SAGALE_GEMINI_MODEL", raising=False)
    monkeypatch.setattr("nucleo.modelos.listar_modelos_gemini", lambda: ("gemini-3.8-flash",))
    assert seleccionar_modelo_gemini() == "gemini-3.8-flash"
    monkeypatch.setenv("SAGALE_GEMINI_MODEL", "modelo-inexistente")
    with pytest.raises(RuntimeError):
        seleccionar_modelo_gemini()


def test_error_cuota_redactado_sin_exponer_proveedor(
    tmp_path: Path, acceso: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fallo(*a: Any, **kw: Any) -> None:
        raise RuntimeError("429 RESOURCE_EXHAUSTED: DATO-SECRETO-PROVEEDOR")

    monkeypatch.setattr("agentes.a03_asistente_whatsapp_rag.get_llm", fallo)
    with TestClient(crear_app(tmp_path / "cuota-error.sqlite3")) as client:
        respuesta = client.post("/api/ia/consultar", json={"contenido": "partida"}, headers=AUTH)
        assert respuesta.status_code == 502
        assert "cuota" in respuesta.text.lower()
        assert "DATO-SECRETO" not in respuesta.text
