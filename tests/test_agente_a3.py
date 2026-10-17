"""Memoria WhatsApp, idempotencia de notificaciones y citas de A3."""

from __future__ import annotations

from agentes.a03_asistente_whatsapp_rag import AgenteA3
from ingesta.gestor_recaudos import FragmentoRAG, IndiceRAGAislado
from nucleo.base_datos import BaseDatos


def test_a3_recuerda_pedidos_notifica_una_vez_y_cita_rag() -> None:
    with BaseDatos(":memory:") as base:
        base.upsert_cliente("CLI-A3", "Cliente", "protegido", consentimiento_pii=True)
        indice = IndiceRAGAislado()
        indice.indexar(
            FragmentoRAG(
                expediente_id="EXP-A3",
                version_documental=1,
                documento_id="partida",
                fuente_id="drive://partida",
                hash_sha256="a" * 64,
                ubicacion="fragmento-1",
                texto="La partida no registra gravámenes.",
            )
        )
        agente = AgenteA3(base, indice)
        agente.recordar_pedido(
            cliente_id="CLI-A3", expediente_id="EXP-A3", contenido="Enviar borrador"
        )
        assert agente.enviar_subsanacion("EXP-A3", "CLI-A3", ["Adjuntar DNI"])
        assert not agente.enviar_subsanacion("EXP-A3", "CLI-A3", ["Adjuntar DNI"])
        assert len(base.listar_memoria_cliente("CLI-A3", "EXP-A3")) == 1
        assert len(base.listar_mensajes_whatsapp("EXP-A3")) == 1

        respuesta = agente.consultar_expediente(
            expediente_id="EXP-A3", version_documental=1, consulta="gravámenes"
        )
        assert not respuesta.abstencion
        assert respuesta.citas == ("partida@v1:fragmento-1",)
        aislada = agente.consultar_expediente(
            expediente_id="OTRO", version_documental=1, consulta="gravámenes"
        )
        assert aislada.abstencion


def test_a3_grafos_usan_ids_de_respuesta_bpmn() -> None:
    with BaseDatos(":memory:") as base:
        agente = AgenteA3(base, IndiceRAGAislado())
        p2 = {n for n in agente.grafo_p2_respuesta.get_graph().nodes if n.startswith("p2_")}
        p3 = {n for n in agente.grafo_p3_respuesta.get_graph().nodes if n.startswith("p3_")}
        assert p2 == {"p2_respuesta"}
        assert p3 == {"p3_respuesta"}
