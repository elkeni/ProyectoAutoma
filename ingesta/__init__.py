"""Ingesta documental segura y recuperación RAG aislada."""

from ingesta.compuerta_pii import DocumentoPII, ResultadoCompuertaPII, evaluar_compuerta_pii
from ingesta.gestor_recaudos import (
    P2_ERROR_DESCARGA,
    ErrorDescargaRecaudo,
    GestorRecaudos,
    IndiceRAGAislado,
)

__all__ = [
    "P2_ERROR_DESCARGA",
    "DocumentoPII",
    "ErrorDescargaRecaudo",
    "GestorRecaudos",
    "IndiceRAGAislado",
    "ResultadoCompuertaPII",
    "evaluar_compuerta_pii",
]
