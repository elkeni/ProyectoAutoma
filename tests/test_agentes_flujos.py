"""Cobertura de rutas BPMN, eventos de borde, temporizadores e HITL."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from agentes.agente_base import EstadoAgente
from agentes.orquestador import Orquestador
from nucleo.estado import NODOS_P01, NODOS_P02, NODOS_P03, crear_estado


def _respuesta(decision: str, actor: str = "abogado") -> dict[str, object]:
    return {
        "actor": actor,
        "autenticado": True,
        "decision": decision,
        "observacion": "",
    }


def _nodo_interrupcion(estado: EstadoAgente) -> str:
    estado_ampliado = cast(dict[str, Any], estado)
    interrupciones = cast(list[Any], estado_ampliado.get("__interrupt__", []))
    assert len(interrupciones) == 1
    valor = getattr(interrupciones[0], "value", None)
    assert isinstance(valor, dict)
    nodo = valor.get("nodo_bpmn_id")
    assert isinstance(nodo, str)
    return nodo


def _estado_p02(expediente_id: str) -> EstadoAgente:
    base = cast(EstadoAgente, crear_estado(expediente_id))
    base["proceso_actual"] = "P02"
    base["nodo_bpmn_actual"] = "p2_inicio"
    return base


def test_respuesta_cliente_no_autenticada_no_reanuda_espera(
    orquestador: Orquestador, datos_minuta: dict[str, Any]
) -> None:
    estado = cast(EstadoAgente, crear_estado("EXP-001"))
    orquestador.iniciar_p03(
        estado, datos_minuta=datos_minuta, thread_id="cliente-auth", espera_interactiva=True
    )
    orquestador.reanudar("P03", thread_id="cliente-auth", respuesta_humana=_respuesta("aprobar"))
    with pytest.raises(PermissionError):
        orquestador.reanudar(
            "P03",
            thread_id="cliente-auth",
            respuesta_humana={**_respuesta("conforme"), "autenticado": False},
        )
    snapshot = orquestador.a2.grafo.get_state(orquestador.a2.configuracion("cliente-auth"))
    assert snapshot.next == ("p3_esperar",)
    resultado = orquestador.reanudar(
        "P03", thread_id="cliente-auth", respuesta_humana=_respuesta("conforme")
    )
    assert _nodo_interrupcion(resultado) == "p3_firma"


@pytest.mark.parametrize("campo", ["expediente_id", "version_documental", "nodo_bpmn_actual"])
def test_reanudacion_no_permite_cambiar_expediente(
    orquestador: Orquestador, datos_minuta: dict[str, Any], campo: str
) -> None:
    estado = cast(EstadoAgente, crear_estado("EXP-001"))
    orquestador.iniciar_p03(estado, datos_minuta=datos_minuta, thread_id="alteracion")
    with pytest.raises(ValueError):
        orquestador.reanudar(
            "P03",
            thread_id="alteracion",
            respuesta_humana=_respuesta("aprobar"),
            actualizacion_estado={campo: "EXP-OTRO"},
        )
    snapshot = orquestador.a2.grafo.get_state(orquestador.a2.configuracion("alteracion"))
    assert snapshot.values["expediente_id"] == "EXP-001"
    assert snapshot.next == ("p3_revision",)


def _calificacion(
    expediente_id: str,
    *,
    suficiente: bool = True,
    viable: bool = True,
    version: int = 1,
) -> str:
    return json.dumps(
        {
            "suficiente": suficiente,
            "viable": viable,
            "requiere_subsanacion": not suficiente,
            "probabilidad": 0.91 if suficiente else 0.40,
            "observaciones": [] if suficiente else ["Falta recaudo"],
            "citas": [
                {
                    "expediente_id": expediente_id,
                    "version_documental": version,
                    "documento_id": "partida-1",
                    "ubicacion": "fragmento-1",
                }
            ],
        }
    )


def test_grafos_contienen_exactamente_ids_bpmn(orquestador: Orquestador) -> None:
    nodos_p1 = {n for n in orquestador.a0.grafo_p01.get_graph().nodes if n.startswith("p1_")}
    nodos_p2 = {n for n in orquestador.a1.grafo.get_graph().nodes if n.startswith("p2_")}
    nodos_p3 = {n for n in orquestador.a2.grafo.get_graph().nodes if n.startswith("p3_")}
    assert nodos_p1 == NODOS_P01
    assert nodos_p2 == NODOS_P02
    assert nodos_p3 == NODOS_P03
    assert (len(nodos_p1), len(nodos_p2), len(nodos_p3)) == (22, 22, 14)


def test_camino_feliz_integral_p01_p02_p03(
    orquestador: Orquestador,
    contrato_p01: dict[str, object],
    recaudos_p02: list[dict[str, Any]],
    datos_minuta: dict[str, Any],
) -> None:
    p1 = orquestador.iniciar_p01(
        expediente_id="EXP-001",
        contrato=contrato_p01,
        thread_id="feliz-p1",
    )
    assert p1["nodo_bpmn_actual"] == "p1_fin_nueva"
    assert p1["codigo_http"] == 201

    p2 = orquestador.iniciar_p02(
        p1,
        recaudos=recaudos_p02,
        thread_id="feliz-p2",
        salida_llm=_calificacion("EXP-001"),
    )
    assert _nodo_interrupcion(p2) == "p2_legal"
    assert p2["nodo_bpmn_actual"] == "p2_legal"
    assert p2["estado_hitl"] == "pendiente"
    p2 = orquestador.reanudar("P02", thread_id="feliz-p2", respuesta_humana=_respuesta("viable"))
    assert p2["nodo_bpmn_actual"] == "p2_fin_viable"

    p3 = orquestador.iniciar_p03(
        p2,
        datos_minuta=datos_minuta,
        thread_id="feliz-p3",
        evento_espera="respuesta",
        cliente_conforme=True,
        kardex_notarial="KARDEX-2026-001",
    )
    assert _nodo_interrupcion(p3) == "p3_revision"
    assert p3["nodo_bpmn_actual"] == "p3_revision"
    assert p3["estado_hitl"] == "pendiente"
    p3 = orquestador.reanudar("P03", thread_id="feliz-p3", respuesta_humana=_respuesta("aprobar"))
    assert _nodo_interrupcion(p3) == "p3_firma"
    p3 = orquestador.reanudar(
        "P03",
        thread_id="feliz-p3",
        respuesta_humana=_respuesta("firmar", "firmante"),
    )
    assert p3["nodo_bpmn_actual"] == "p3_fin"
    assert p3["kardex_notarial"] == "KARDEX-2026-001"
    assert orquestador.base_datos.contar("Solicitudes", "EXP-001") == 1
    assert orquestador.base_datos.contar("Versiones_Recaudos", "EXP-001") == 1


def test_p1_error_sheets_recupera_y_agota_en_hitl(
    orquestador: Orquestador, contrato_p01: dict[str, object]
) -> None:
    recuperado = orquestador.iniciar_p01(
        expediente_id="EXP-SHEETS-R",
        contrato=contrato_p01,
        thread_id="sheets-recuperado",
        simular_error="sheets",
        recuperar_sheets_en_intento=2,
    )
    assert recuperado["nodo_bpmn_actual"] == "p1_fin_nueva"
    assert recuperado["reintentos_esperas"] == [2, 4]

    otro = dict(contrato_p01, partida_registral="P-AGOTADO")
    agotado = orquestador.iniciar_p01(
        expediente_id="EXP-SHEETS-E",
        contrato=otro,
        thread_id="sheets-agotado",
        simular_error="sheets",
    )
    assert agotado["reintentos_esperas"] == [2, 4, 8]
    assert _nodo_interrupcion(agotado) == "p1_incidente"
    assert orquestador.base_datos.contar("Tareas_HITL", "EXP-SHEETS-E") == 1
    final = orquestador.reanudar(
        "P01",
        thread_id="sheets-agotado",
        respuesta_humana=_respuesta("cerrar_incidente", "soporte"),
    )
    assert final["nodo_bpmn_actual"] == "p1_fin_incidente"


def test_p1_error_sistema_va_directo_a_incidente(
    orquestador: Orquestador, contrato_p01: dict[str, object]
) -> None:
    resultado = orquestador.iniciar_p01(
        expediente_id="EXP-SISTEMA",
        contrato=contrato_p01,
        thread_id="error-sistema",
        simular_error="sistema",
    )
    assert _nodo_interrupcion(resultado) == "p1_incidente"
    nodos = [traza["nodo_bpmn_id"] for traza in resultado["trazas"]]
    assert "p1_error_sistema" in nodos
    assert "p1_alertar" in nodos


@pytest.mark.parametrize("evento", ["descarga", "ia", "json"])
def test_p2_errores_y_json_invalido_pausan_revision_fallo(
    orquestador: Orquestador,
    recaudos_p02: list[dict[str, Any]],
    evento: str,
) -> None:
    expediente = f"EXP-P2-{evento}"
    parametros: dict[str, Any] = {
        "recaudos": recaudos_p02,
        "thread_id": f"p2-{evento}",
    }
    if evento == "json":
        parametros["salida_llm"] = "{json-invalido"
    else:
        parametros["simular_error"] = evento
    resultado = orquestador.iniciar_p02(_estado_p02(expediente), **parametros)
    assert _nodo_interrupcion(resultado) == "p2_revision_fallo"
    revisado = orquestador.reanudar(
        "P02",
        thread_id=f"p2-{evento}",
        respuesta_humana=_respuesta("continuar", "soporte"),
    )
    assert _nodo_interrupcion(revisado) == "p2_legal"


def test_p2_subsanacion_p5d_y_archivo_hitl(
    orquestador: Orquestador, recaudos_p02: list[dict[str, Any]]
) -> None:
    resultado = orquestador.iniciar_p02(
        _estado_p02("EXP-P5D"),
        recaudos=recaudos_p02,
        thread_id="p5d",
        salida_llm=_calificacion("EXP-P5D", suficiente=False, viable=False),
        evento_espera="P5D",
    )
    assert _nodo_interrupcion(resultado) == "p2_subsanacion"
    resultado = orquestador.reanudar(
        "P02", thread_id="p5d", respuesta_humana=_respuesta("notificar")
    )
    assert resultado["error_codigo"] == "P5D"
    assert _nodo_interrupcion(resultado) == "p2_archivar"
    resultado = orquestador.reanudar(
        "P02", thread_id="p5d", respuesta_humana=_respuesta("archivar")
    )
    assert resultado["nodo_bpmn_actual"] == "p2_fin_abandono"


def test_p2_rechaza_cita_de_otro_expediente(
    orquestador: Orquestador, recaudos_p02: list[dict[str, Any]]
) -> None:
    salida_ajena = _calificacion("EXP-AJENO")
    resultado = orquestador.iniciar_p02(
        _estado_p02("EXP-CITAS"),
        recaudos=recaudos_p02,
        thread_id="cita-ajena",
        salida_llm=salida_ajena,
    )
    assert _nodo_interrupcion(resultado) == "p2_revision_fallo"


def test_p2_compuerta_pii_bloquea_antes_de_crear_llm(orquestador: Orquestador) -> None:
    bloqueado = [
        {
            "documento_id": "privado",
            "fuente_id": "drive://privado",
            "contenido": "DNI 12345678",
            "actor_autorizado": False,
        }
    ]
    resultado = orquestador.iniciar_p02(
        _estado_p02("EXP-PII-GRAFO"),
        recaudos=bloqueado,
        thread_id="pii-sin-llm",
    )
    assert _nodo_interrupcion(resultado) == "p2_subsanacion"
    assert resultado["documentos_aprobados"] == 0
    assert orquestador.a1._llm is None


def test_p2_respuesta_versiona_y_reingresa_documentos(
    tmp_path: Path,
    recaudos_p02: list[dict[str, Any]],
) -> None:
    respuesta_v2 = _calificacion("EXP-V2", version=2)
    with Orquestador(
        tmp_path / "version.sqlite3",
        dormir=lambda _s: None,
        respuestas_fake=[respuesta_v2],
    ) as orquestador:
        corregidos = [
            {
                "documento_id": "partida-1",
                "fuente_id": "drive://exp/partida-v2.pdf",
                "contenido": "Partida corregida sin gravámenes.",
            }
        ]
        resultado = orquestador.iniciar_p02(
            _estado_p02("EXP-V2"),
            recaudos=recaudos_p02,
            recaudos_corregidos=corregidos,
            thread_id="respuesta-v2",
            salida_llm=_calificacion("EXP-V2", suficiente=False, viable=False),
            evento_espera="respuesta",
        )
        assert _nodo_interrupcion(resultado) == "p2_subsanacion"
        resultado = orquestador.reanudar(
            "P02", thread_id="respuesta-v2", respuesta_humana=_respuesta("notificar")
        )
        assert resultado["version_documental"] == 2
        assert _nodo_interrupcion(resultado) == "p2_legal"
        resultado = orquestador.reanudar(
            "P02", thread_id="respuesta-v2", respuesta_humana=_respuesta("viable")
        )
        assert resultado["nodo_bpmn_actual"] == "p2_fin_viable"
        assert orquestador.base_datos.contar("Versiones_Recaudos", "EXP-V2") == 2


def test_p3_observacion_repite_revision_y_pt72h(
    orquestador: Orquestador, datos_minuta: dict[str, Any]
) -> None:
    base = _estado_p02("EXP-001")
    resultado = orquestador.iniciar_p03(
        base,
        datos_minuta=datos_minuta,
        thread_id="p3-correccion",
        evento_espera="PT72H",
    )
    assert _nodo_interrupcion(resultado) == "p3_revision"
    correccion = _respuesta("corregir")
    correccion["observacion"] = "Precisar medio de pago"
    resultado = orquestador.reanudar("P03", thread_id="p3-correccion", respuesta_humana=correccion)
    assert _nodo_interrupcion(resultado) == "p3_revision"
    assert resultado["observaciones_revision"] == ["Precisar medio de pago"]
    resultado = orquestador.reanudar(
        "P03", thread_id="p3-correccion", respuesta_humana=_respuesta("aprobar")
    )
    assert resultado["error_codigo"] == "PT72H"
    assert resultado["nodo_bpmn_actual"] == "p3_fin_sin_conformidad"


def test_p3_cliente_pide_cambios_regresa_a_borrador_y_revision(
    orquestador: Orquestador, datos_minuta: dict[str, Any]
) -> None:
    resultado = orquestador.iniciar_p03(
        _estado_p02("EXP-001"),
        datos_minuta=datos_minuta,
        thread_id="p3-cambios-cliente",
        evento_espera="respuesta",
        cliente_conforme=False,
    )
    assert _nodo_interrupcion(resultado) == "p3_revision"
    resultado = orquestador.reanudar(
        "P03",
        thread_id="p3-cambios-cliente",
        respuesta_humana=_respuesta("aprobar"),
    )
    assert _nodo_interrupcion(resultado) == "p3_revision"
    nodos = [traza["nodo_bpmn_id"] for traza in resultado["trazas"]]
    assert "p3_respuesta" in nodos
    assert "p3_conforme" in nodos
    assert nodos.count("p3_borrador") == 2


def test_p3_notaria_no_inventa_kardex(
    orquestador: Orquestador, datos_minuta: dict[str, Any]
) -> None:
    resultado = orquestador.iniciar_p03(
        _estado_p02("EXP-001"),
        datos_minuta=datos_minuta,
        thread_id="p3-sin-kardex",
        evento_espera="respuesta",
        cliente_conforme=True,
    )
    resultado = orquestador.reanudar(
        "P03", thread_id="p3-sin-kardex", respuesta_humana=_respuesta("aprobar")
    )
    assert _nodo_interrupcion(resultado) == "p3_firma"
    with pytest.raises(RuntimeError, match="no devolvió un kárdex"):
        orquestador.reanudar(
            "P03",
            thread_id="p3-sin-kardex",
            respuesta_humana=_respuesta("firmar", "firmante"),
        )
    recuperado = orquestador.reanudar(
        "P03",
        thread_id="p3-sin-kardex",
        respuesta_humana=_respuesta("firmar", "firmante"),
        actualizacion_estado={"kardex_notarial_entrada": "KB-RECUPERACION"},
    )
    assert recuperado["nodo_bpmn_actual"] == "p3_fin"
    assert recuperado["kardex_notarial"] == "KB-RECUPERACION"


def test_reanudacion_hitl_exige_autenticacion(
    orquestador: Orquestador, contrato_p01: dict[str, object]
) -> None:
    resultado = orquestador.iniciar_p01(
        expediente_id="EXP-AUTH",
        contrato=contrato_p01,
        thread_id="auth",
        simular_error="sistema",
    )
    assert _nodo_interrupcion(resultado) == "p1_incidente"
    with pytest.raises(PermissionError):
        orquestador.reanudar(
            "P01",
            thread_id="auth",
            respuesta_humana={
                "actor": "intruso",
                "autenticado": False,
                "decision": "cerrar_incidente",
            },
        )
