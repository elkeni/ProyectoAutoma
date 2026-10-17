"""Contrato P01, idempotencia y política de reintentos."""

from __future__ import annotations

import hashlib

import pytest

from rpa.idempotencia_p01 import (
    RepositorioP01Memoria,
    generar_clave_idempotencia,
    normalizar_contrato,
    procesar_solicitud_p01,
)
from rpa.reintentos_p01 import ESPERAS_P01, ErrorSheets, p1_reintento
from rpa.validador_p01 import ContratoEntradaP01, validar_solicitud


def test_validacion_diez_campos_y_adjunto(contrato_p01: dict[str, object]) -> None:
    valido = validar_solicitud(contrato_p01, expediente_id="EXP-VAL")
    assert valido.valido
    assert valido.contrato is not None
    assert len(ContratoEntradaP01.model_fields) == 10

    faltante = dict(contrato_p01)
    faltante.pop("dni_vendedor")
    assert not validar_solicitud(faltante, expediente_id="EXP-VAL").valido

    formato = dict(contrato_p01, dni_comprador="123")
    assert not validar_solicitud(formato, expediente_id="EXP-VAL").valido

    adjunto = validar_solicitud(
        contrato_p01,
        expediente_id="EXP-VAL",
        verificar_adjunto=lambda referencia: not referencia.endswith("dni.pdf"),
    )
    assert not adjunto.valido
    assert adjunto.errores[0].codigo == "adjunto_no_disponible"


def test_creacion_p01_responde_201(contrato_p01: dict[str, object]) -> None:
    repositorio = RepositorioP01Memoria()
    resultado = procesar_solicitud_p01(
        expediente_id="EXP-201",
        datos=contrato_p01,
        repositorio=repositorio,
    )
    assert resultado.codigo_http == 201
    assert resultado.nodo_final == "p1_fin_nueva"
    assert repositorio.cantidad_solicitudes("EXP-201") == 1


def test_idempotencia_p01_responde_200_sin_duplicar(
    contrato_p01: dict[str, object],
) -> None:
    repositorio = RepositorioP01Memoria()
    primero = procesar_solicitud_p01(
        expediente_id="EXP-IDEM",
        datos=contrato_p01,
        repositorio=repositorio,
    )
    segundo = procesar_solicitud_p01(
        expediente_id="EXP-IDEM",
        datos=contrato_p01,
        repositorio=repositorio,
    )
    assert primero.codigo_http == 201
    assert segundo.codigo_http == 200
    assert segundo.nodo_final == "p1_fin_duplicada"
    assert repositorio.cantidad_solicitudes("EXP-IDEM") == 1


def test_entrada_invalida_va_a_cola_y_responde_400(
    contrato_p01: dict[str, object],
) -> None:
    repositorio = RepositorioP01Memoria()
    invalido = dict(contrato_p01)
    invalido["valor_comercial"] = "no-es-numero"
    resultado = procesar_solicitud_p01(
        expediente_id="EXP-400",
        datos=invalido,
        repositorio=repositorio,
    )
    assert resultado.codigo_http == 400
    assert resultado.nodo_final == "p1_fin_invalida"
    assert repositorio.cantidad_fallidos("EXP-400") == 1
    assert repositorio.cantidad_solicitudes("EXP-400") == 0


def test_hash_es_formula_exacta_sin_separadores(contrato_p01: dict[str, object]) -> None:
    contrato = ContratoEntradaP01.model_validate(contrato_p01)
    normalizado = normalizar_contrato(contrato)
    material = (
        normalizado.partida_registral
        + normalizado.dni_vendedor
        + normalizado.dni_comprador
        + normalizado.tipo_operacion
    )
    assert generar_clave_idempotencia(normalizado) == hashlib.sha256(material.encode()).hexdigest()


def test_reintentos_exactos_2_4_8_y_recuperacion() -> None:
    esperas: list[float] = []
    intentos = 0

    def operacion() -> str:
        nonlocal intentos
        intentos += 1
        if intentos < 3:
            raise ErrorSheets("temporal")
        return "recuperado"

    resultado = p1_reintento(operacion, dormir=esperas.append)
    assert resultado.recuperado
    assert resultado.valor == "recuperado"
    assert resultado.esperas == ESPERAS_P01
    assert esperas == [2.0, 4.0, 8.0]


def test_reintentos_agotados_no_ocultan_error() -> None:
    def siempre_falla() -> object:
        raise ErrorSheets("temporal")

    resultado = p1_reintento(siempre_falla, dormir=lambda _espera: None)
    assert not resultado.recuperado
    assert resultado.intentos == 3
    assert resultado.esperas == (2, 4, 8)


def test_expediente_id_invalido_se_rechaza(contrato_p01: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        validar_solicitud(contrato_p01, expediente_id="../otro")
