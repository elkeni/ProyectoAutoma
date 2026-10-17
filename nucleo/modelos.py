"""Factoría única de modelos de lenguaje.

El proveedor predeterminado es ``fake`` para que importar y probar el proyecto no
requiera red ni credenciales.  Ningún otro módulo debe construir clientes de LLM.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from functools import lru_cache
from importlib import import_module
from typing import Literal, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel

ProveedorLLM = Literal["fake", "ollama", "gemini"]

_RESPUESTA_FAKE_SEGURA = (
    '{"resultado":"sin_inferencia","confianza":0.0,"evidencias":[],"requiere_revision":true}'
)


class ErrorIA(RuntimeError):
    """Error de proveedor redactado, sin cuerpo HTTP, prompts ni credenciales."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        self.origen = "ErrorIA"
        mensajes = {
            "CUOTA": "La cuota de Gemini está agotada o no habilitada para este proyecto de API.",
            "ACCESO": "Gemini rechazó el acceso; revise la clave y los permisos en AI Studio.",
            "FACTURACION": "El proyecto Gemini requiere revisar su facturación o saldo.",
            "VALIDACION": "La IA no devolvió una respuesta con evidencia verificable.",
            "MODELO": "El modelo Gemini no está disponible para generar respuestas; revise SAGALE_GEMINI_MODEL.",
        }
        super().__init__(mensajes.get(codigo, "Gemini no respondió; intente de nuevo más tarde."))


def error_ia_seguro(error: Exception) -> ErrorIA:
    detalle = str(error).casefold()
    codigo = "PROVEEDOR"
    if "429" in detalle or "resource_exhausted" in detalle:
        codigo = "CUOTA"
    elif "402" in detalle:
        codigo = "FACTURACION"
    elif "401" in detalle or "403" in detalle or "api_key_invalid" in detalle:
        codigo = "ACCESO"
    elif "404" in detalle or "modelnotfound" in type(error).__name__.casefold():
        codigo = "MODELO"
    elif isinstance(error, ValueError):
        codigo = "VALIDACION"
    seguro = ErrorIA(codigo)
    seguro.origen = type(error).__name__
    if error.__cause__ is not None:
        seguro.origen += "/" + type(error.__cause__).__name__
    return seguro


@lru_cache(maxsize=1)
def listar_modelos_gemini() -> tuple[str, ...]:
    """Consulta solo metadatos; la credencial permanece en el proceso servidor."""
    clave = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not clave:
        raise RuntimeError("No hay clave Gemini configurada.")
    try:
        peticion = Request(
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
            headers={"x-goog-api-key": clave},
        )
        with urlopen(peticion, timeout=15) as respuesta:
            datos = json.load(respuesta)
    except (HTTPError, URLError, TimeoutError, ValueError):
        raise RuntimeError(
            "No se pudo verificar el catálogo Gemini; revise acceso y cuota."
        ) from None
    return tuple(
        str(item["name"]).removeprefix("models/")
        for item in datos.get("models", [])
        if "generateContent" in item.get("supportedGenerationMethods", [])
    )


def seleccionar_modelo_gemini() -> str:
    disponibles = listar_modelos_gemini()
    elegido = os.getenv("SAGALE_GEMINI_MODEL")
    candidatos = (
        (elegido,)
        if elegido
        else (
            "gemini-flash-latest",
            "gemini-3.8-flash",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
        )
    )
    for candidato in candidatos:
        if candidato in disponibles:
            return str(candidato)
    raise RuntimeError("No hay un modelo Flash compatible; configure SAGALE_GEMINI_MODEL.")


def extraer_texto_ia(contenido: object) -> str:
    if isinstance(contenido, str):
        return contenido
    if isinstance(contenido, list):
        return "".join(
            str(bloque["text"])
            for bloque in contenido
            if isinstance(bloque, dict) and bloque.get("type") == "text" and "text" in bloque
        )
    raise ValueError("El modelo no devolvió texto estructurado.")


def _normalizar_proveedor(valor: str) -> ProveedorLLM:
    normalizado = valor.strip().lower()
    alias = {"test": "fake", "pruebas": "fake", "google": "gemini"}
    normalizado = alias.get(normalizado, normalizado)
    if normalizado not in {"fake", "ollama", "gemini"}:
        raise ValueError(f"Proveedor LLM no admitido: {valor!r}. Use 'fake', 'ollama' o 'gemini'.")
    return cast(ProveedorLLM, normalizado)


def _cargar_constructor(modulo: str, nombre: str, extra: str) -> Callable[..., BaseChatModel]:
    try:
        paquete = import_module(modulo)
    except ImportError as exc:
        raise RuntimeError(
            f"El proveedor requiere el paquete opcional {modulo!r}. "
            f"Instale el extra correspondiente ({extra})."
        ) from exc

    constructor = getattr(paquete, nombre, None)
    if constructor is None or not callable(constructor):
        raise RuntimeError(f"{modulo!r} no expone un constructor compatible {nombre!r}.")
    return cast(Callable[..., BaseChatModel], constructor)


def get_llm(
    proveedor: str | None = None,
    *,
    modelo: str | None = None,
    respuestas_fake: Sequence[str] | None = None,
    temperatura: float = 0.0,
    provider: str | None = None,
    model: str | None = None,
    responses: Sequence[str] | None = None,
) -> BaseChatModel:
    """Devuelve el único cliente LLM autorizado por la aplicación.

    Los alias en inglés facilitan integraciones externas, pero no pueden entrar en
    conflicto con sus equivalentes en español. La rama ``fake`` es completamente
    local y determinista.
    """

    if proveedor is not None and provider is not None and proveedor != provider:
        raise ValueError("No combine 'proveedor' y 'provider' con valores distintos.")
    if modelo is not None and model is not None and modelo != model:
        raise ValueError("No combine 'modelo' y 'model' con valores distintos.")
    if respuestas_fake is not None and responses is not None:
        raise ValueError("Use solo 'respuestas_fake' o 'responses'.")
    if not 0.0 <= temperatura <= 2.0:
        raise ValueError("La temperatura debe estar entre 0 y 2.")

    nombre_proveedor = proveedor or provider or os.getenv("SAGALE_LLM_PROVIDER") or "fake"
    seleccionado = _normalizar_proveedor(nombre_proveedor)
    nombre_modelo = modelo or model

    if seleccionado == "fake":
        respuestas = list(respuestas_fake or responses or [_RESPUESTA_FAKE_SEGURA])
        if not respuestas:
            raise ValueError("FakeListChatModel requiere al menos una respuesta.")
        return FakeListChatModel(responses=respuestas)

    if seleccionado == "ollama":
        constructor_ollama = _cargar_constructor("langchain_ollama", "ChatOllama", "ollama")
        return constructor_ollama(
            model=nombre_modelo or os.getenv("SAGALE_OLLAMA_MODEL", "llama3.2"),
            temperature=temperatura,
        )

    if not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")):
        raise RuntimeError(
            "Gemini requiere GOOGLE_API_KEY o GEMINI_API_KEY; no se intentará una llamada sin ella."
        )
    constructor_gemini = _cargar_constructor(
        "langchain_google_genai", "ChatGoogleGenerativeAI", "gemini"
    )
    return constructor_gemini(
        model=nombre_modelo or seleccionar_modelo_gemini(),
        temperature=temperatura,
        timeout=25,
        max_retries=1,
        max_output_tokens=2048,
        vertexai=False,
    )


__all__ = ["ProveedorLLM", "get_llm"]
