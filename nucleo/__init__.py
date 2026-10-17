"""Primitivas compartidas del sistema Ságale."""

from nucleo.base_datos import BaseDatos
from nucleo.estado import EstadoExpediente, crear_estado
from nucleo.modelos import get_llm

__all__ = ["BaseDatos", "EstadoExpediente", "crear_estado", "get_llm"]
