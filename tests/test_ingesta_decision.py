"""Compuerta PII, RAG, Sistema 1, calibración y plantilla P03."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from time import perf_counter

import pytest
from docx import Document

from decision.calibracion import Calibrador, ObservacionCalibracion, brier_score
from decision.decisor import decide
from decision.umbrales import UmbralesDecision
from ingesta.compuerta_pii import DocumentoPII, evaluar_compuerta_pii
from ingesta.gestor_recaudos import (
    ErrorDescargaRecaudo,
    GestorRecaudos,
    IndiceRAGAislado,
    RecaudoEntrada,
)
from nucleo.base_datos import BaseDatos
from rpa.plantilla_minuta_p03 import DatosMinuta, ensamblar_minuta


def _documento(**cambios: object) -> DocumentoPII:
    base: dict[str, object] = {
        "expediente_id": "EXP-PII",
        "documento_id": "doc-1",
        "fuente_id": "drive://doc-1",
        "contenido": "DNI 12345678, correo ana@example.com, teléfono 987654321",
        "finalidad": "calificación jurídica",
        "base_tratamiento": "servicio solicitado",
        "actor_autorizado": True,
        "pertenece_expediente": True,
        "consentimiento_o_base_legal": True,
    }
    base.update(cambios)
    return DocumentoPII.model_validate(base)


def test_compuerta_pii_bloquea_antes_de_indexar_y_minimiza() -> None:
    bloqueado = evaluar_compuerta_pii(_documento(actor_autorizado=False))
    assert not bloqueado.aprobado
    assert bloqueado.contenido_minimizado is None
    assert bloqueado.clasificacion_acceso == "bloqueado"

    aprobado = evaluar_compuerta_pii(_documento())
    assert aprobado.aprobado
    assert aprobado.contenido_minimizado is not None
    assert "12345678" not in aprobado.contenido_minimizado
    assert "ana@example.com" not in aprobado.contenido_minimizado
    assert set(aprobado.tipos_detectados) == {"dni", "email", "telefono"}


def test_rag_aislado_por_expediente_y_version() -> None:
    with BaseDatos(":memory:") as base:
        indice = IndiceRAGAislado()
        gestor = GestorRecaudos(base_datos=base, indice=indice)
        recaudo = RecaudoEntrada(
            documento_id="partida",
            fuente_id="drive://partida",
            contenido="Partida sin gravámenes para el inmueble.",
        )
        gestor.p2_documentos(expediente_id="EXP-A", version_documental=1, recaudos=[recaudo])
        assert indice.recuperar(expediente_id="EXP-A", version_documental=1, consulta="gravámenes")
        assert not indice.recuperar(
            expediente_id="EXP-B", version_documental=1, consulta="gravámenes"
        )
        assert not indice.recuperar(
            expediente_id="EXP-A", version_documental=2, consulta="gravámenes"
        )


def test_version_corregida_es_inmutable_y_error_descarga() -> None:
    with BaseDatos(":memory:") as base:
        gestor = GestorRecaudos(base_datos=base)
        original = RecaudoEntrada(
            documento_id="doc", fuente_id="fuente-v1", contenido="versión inicial"
        )
        corregido = RecaudoEntrada(
            documento_id="doc", fuente_id="fuente-v2", contenido="versión corregida"
        )
        gestor.p2_documentos(expediente_id="EXP-V", version_documental=1, recaudos=[original])
        resultado = gestor.p2_version(
            expediente_id="EXP-V", version_anterior=1, recaudos_corregidos=[corregido]
        )
        assert resultado.version_documental == 2
        assert len(base.listar_recaudos("EXP-V")) == 2
        assert base.listar_recaudos("EXP-V", 1)[0]["fuente_id"] == "fuente-v1"

        sin_descargador = RecaudoEntrada(documento_id="x", fuente_id="remota")
        with pytest.raises(ErrorDescargaRecaudo) as error:
            gestor.p2_documentos(
                expediente_id="EXP-X", version_documental=1, recaudos=[sin_descargador]
            )
        assert error.value.codigo == "P2_ERROR_DESCARGA"


def test_decide_umbrales_determinista_y_menor_50_ms() -> None:
    umbrales = UmbralesDecision(tau_auto=0.85, tau_rev=0.60, version="prueba")
    inicio = perf_counter()
    automatico = decide(0.90, umbrales)
    revision = decide(0.70, umbrales)
    incidente = decide(0.40, umbrales)
    duracion_ms = (perf_counter() - inicio) * 1000
    assert automatico.decision == "automatico"
    assert revision.decision == "revision_humana"
    assert incidente.decision == "escalar_incidente"
    assert duracion_ms < 50
    assert decide(0.90, umbrales).model_dump(exclude={"duracion_ms"}) == automatico.model_dump(
        exclude={"duracion_ms"}
    )


def test_calibracion_brier_y_ece_tras_hitl() -> None:
    assert brier_score([1, 0], [0.8, 0.2]) == pytest.approx(0.04)
    calibrador = Calibrador(n_bins=2)
    metricas = calibrador.registrar(
        ObservacionCalibracion(
            expediente_id="EXP-CAL",
            version_documental=1,
            nodo_bpmn_id="p2_legal",
            probabilidad=0.8,
            etiqueta_humana=True,
            version_umbrales="1.0",
        )
    )
    assert metricas.total == 1
    assert metricas.brier_score == pytest.approx(0.04)
    assert metricas.ece == pytest.approx(0.2)


def test_plantilla_markdown_y_word_son_deterministas(tmp_path: Path) -> None:
    datos = DatosMinuta(
        expediente_id="EXP-MIN",
        vendedor="Ana Torres",
        comprador="Luis Pérez",
        inmueble="inmueble de Lima",
        partida_registral="P-100",
        valor_comercial="S/ 100 000",
        forma_pago="contado",
    )
    primero = ensamblar_minuta(datos)
    segundo = ensamblar_minuta(datos)
    assert primero.contenido_markdown == segundo.contenido_markdown
    assert primero.hash_sha256 == segundo.hash_sha256
    ruta = tmp_path / "minuta.docx"
    word = ensamblar_minuta(datos, formato="docx", ruta_salida=ruta)
    assert ruta.exists()
    assert word.contenido_docx is not None
    documento = Document(BytesIO(word.contenido_docx))
    assert "BORRADOR DE MINUTA" in documento.paragraphs[0].text
