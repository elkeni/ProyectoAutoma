"""Normalizacion, idempotencia y respuestas HTTP deterministas de P01."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import unicodedata
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Final, Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from rpa.validador_p01 import (
    ContratoEntradaP01,
    ErrorValidacionP01,
    ResultadoValidacionP01,
    VerificadorAdjunto,
    validar_solicitud,
)

CODIGO_CREADO: Final[Literal[201]] = 201
CODIGO_DUPLICADO: Final[Literal[200]] = 200
CODIGO_INVALIDO: Final[Literal[400]] = 400

CodigoHttpP01: TypeAlias = Literal[200, 201, 400]
EstadoRegistroP01: TypeAlias = Literal["creada", "duplicada", "invalida"]

_ESPACIOS_RE = re.compile(r"\s+")
_TELEFONO_SEPARADORES_RE = re.compile(r"[ ()-]")


def _texto_estable(valor: str) -> str:
    return _ESPACIOS_RE.sub(" ", unicodedata.normalize("NFKC", valor).strip())


def _sin_tildes(valor: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", valor)
    return "".join(caracter for caracter in descompuesto if not unicodedata.combining(caracter))


def normalizar_contrato(contrato: ContratoEntradaP01) -> ContratoEntradaP01:
    """Implementa ``p1_normalizar`` con una representacion estable.

    Esta funcion no completa campos. Solo aplica reglas mecanicas y repetibles a
    un contrato que ya supero ``p1_validar``.
    """

    contacto = _texto_estable(contrato.contacto)
    if "@" in contacto:
        contacto = contacto.casefold()
    else:
        contacto = _TELEFONO_SEPARADORES_RE.sub("", contacto)

    tipo = _sin_tildes(_texto_estable(contrato.tipo_operacion)).casefold()
    if tipo in {"compra venta", "venta"}:
        tipo = "compraventa"

    forma_pago = _sin_tildes(_texto_estable(contrato.forma_pago)).casefold()
    partida = _ESPACIOS_RE.sub("", _texto_estable(contrato.partida_registral)).upper()
    valor = contrato.valor_comercial.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return ContratoEntradaP01.model_validate(
        {
            "solicitante": _texto_estable(contrato.solicitante),
            "contacto": contacto,
            "tipo_operacion": tipo,
            "dni_vendedor": contrato.dni_vendedor,
            "dni_comprador": contrato.dni_comprador,
            "partida_registral": partida,
            "valor_comercial": valor,
            "forma_pago": forma_pago,
            "adj_dni": _texto_estable(contrato.adj_dni),
            "adj_partida": _texto_estable(contrato.adj_partida),
        }
    )


def generar_clave_idempotencia(contrato: ContratoEntradaP01) -> str:
    """Implementa el ``p1_hash`` normativo sin separador ni sal.

    La entrada debe estar normalizada. El orden es exactamente partida registral,
    DNI del vendedor, DNI del comprador y tipo de operacion.
    """

    material = (
        contrato.partida_registral
        + contrato.dni_vendedor
        + contrato.dni_comprador
        + contrato.tipo_operacion
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def p1_normalizar(contrato: ContratoEntradaP01) -> ContratoEntradaP01:
    return normalizar_contrato(contrato)


def p1_hash(contrato_normalizado: ContratoEntradaP01) -> str:
    return generar_clave_idempotencia(contrato_normalizado)


class RegistroSolicitudP01(BaseModel):
    """Referencia no sensible a una fila de ``Solicitudes``."""

    model_config = ConfigDict(frozen=True)

    expediente_id: str
    clave_idempotencia_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    creado_en_utc: datetime


class FalloDatosP01(BaseModel):
    """Evidencia redactada para ``Cola_Fallidos``."""

    model_config = ConfigDict(frozen=True)

    expediente_id: str
    hash_entrada: str = Field(pattern=r"^[0-9a-f]{64}$")
    errores: tuple[ErrorValidacionP01, ...]
    creado_en_utc: datetime
    nodo_bpmn_id: Literal["p1_fallidos_datos"] = "p1_fallidos_datos"
    codigo_http: Literal[400] = 400


class RepositorioP01(Protocol):
    """Puerto minimo para persistencia P01, siempre filtrado por expediente."""

    def consultar(
        self, expediente_id: str, clave_idempotencia_sha256: str
    ) -> RegistroSolicitudP01 | None: ...

    def registrar(
        self,
        expediente_id: str,
        clave_idempotencia_sha256: str,
        contrato: ContratoEntradaP01,
    ) -> bool: ...

    def registrar_fallido(self, fallo: FalloDatosP01) -> None: ...


class RepositorioP01Memoria:
    """Adaptador en memoria util para pruebas, aislado por ``expediente_id``."""

    def __init__(self) -> None:
        self._solicitudes: dict[tuple[str, str], tuple[RegistroSolicitudP01, str]] = {}
        self._fallidos: dict[str, list[FalloDatosP01]] = {}
        self._lock = threading.RLock()

    def consultar(
        self, expediente_id: str, clave_idempotencia_sha256: str
    ) -> RegistroSolicitudP01 | None:
        with self._lock:
            encontrado = self._solicitudes.get((expediente_id, clave_idempotencia_sha256))
            return encontrado[0] if encontrado is not None else None

    def registrar(
        self,
        expediente_id: str,
        clave_idempotencia_sha256: str,
        contrato: ContratoEntradaP01,
    ) -> bool:
        llave = (expediente_id, clave_idempotencia_sha256)
        with self._lock:
            if llave in self._solicitudes:
                return False
            registro = RegistroSolicitudP01(
                expediente_id=expediente_id,
                clave_idempotencia_sha256=clave_idempotencia_sha256,
                creado_en_utc=datetime.now(UTC),
            )
            payload = contrato.model_dump_json()
            self._solicitudes[llave] = (registro, payload)
            return True

    def registrar_fallido(self, fallo: FalloDatosP01) -> None:
        with self._lock:
            self._fallidos.setdefault(fallo.expediente_id, []).append(fallo)

    def cantidad_solicitudes(self, expediente_id: str) -> int:
        with self._lock:
            return sum(1 for expediente, _ in self._solicitudes if expediente == expediente_id)

    def cantidad_fallidos(self, expediente_id: str) -> int:
        with self._lock:
            return len(self._fallidos.get(expediente_id, ()))


class RepositorioP01SQLite:
    """Adaptador SQLite transaccional compatible con conexiones inyectadas."""

    def __init__(self, base: str | Path | sqlite3.Connection = ":memory:") -> None:
        self._propietario_conexion = not isinstance(base, sqlite3.Connection)
        if isinstance(base, sqlite3.Connection):
            conexion = base
        else:
            conexion = sqlite3.connect(str(base), check_same_thread=False)
        self._conexion: sqlite3.Connection = conexion
        self._conexion.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._crear_esquema()

    def _crear_esquema(self) -> None:
        with self._lock, self._conexion:
            self._conexion.execute(
                """
                CREATE TABLE IF NOT EXISTS Solicitudes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    expediente_id TEXT NOT NULL,
                    clave_idempotencia_sha256 TEXT NOT NULL,
                    contrato_json TEXT NOT NULL,
                    creado_en_utc TEXT NOT NULL,
                    UNIQUE (expediente_id, clave_idempotencia_sha256)
                )
                """
            )
            self._conexion.execute(
                """
                CREATE TABLE IF NOT EXISTS Cola_Fallidos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    expediente_id TEXT NOT NULL,
                    hash_entrada TEXT NOT NULL,
                    errores_json TEXT NOT NULL,
                    nodo_bpmn_id TEXT NOT NULL,
                    codigo_http INTEGER NOT NULL,
                    creado_en_utc TEXT NOT NULL
                )
                """
            )

    def consultar(
        self, expediente_id: str, clave_idempotencia_sha256: str
    ) -> RegistroSolicitudP01 | None:
        with self._lock:
            fila = self._conexion.execute(
                """
                SELECT expediente_id, clave_idempotencia_sha256, creado_en_utc
                FROM Solicitudes
                WHERE expediente_id = ? AND clave_idempotencia_sha256 = ?
                """,
                (expediente_id, clave_idempotencia_sha256),
            ).fetchone()
        if fila is None:
            return None
        return RegistroSolicitudP01(
            expediente_id=str(fila["expediente_id"]),
            clave_idempotencia_sha256=str(fila["clave_idempotencia_sha256"]),
            creado_en_utc=datetime.fromisoformat(str(fila["creado_en_utc"])),
        )

    def registrar(
        self,
        expediente_id: str,
        clave_idempotencia_sha256: str,
        contrato: ContratoEntradaP01,
    ) -> bool:
        try:
            with self._lock, self._conexion:
                self._conexion.execute(
                    """
                    INSERT INTO Solicitudes (
                        expediente_id, clave_idempotencia_sha256,
                        contrato_json, creado_en_utc
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        expediente_id,
                        clave_idempotencia_sha256,
                        contrato.model_dump_json(),
                        datetime.now(UTC).isoformat(),
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def registrar_fallido(self, fallo: FalloDatosP01) -> None:
        errores_json = json.dumps(
            [error.model_dump(mode="json") for error in fallo.errores],
            ensure_ascii=False,
            sort_keys=True,
        )
        with self._lock, self._conexion:
            self._conexion.execute(
                """
                INSERT INTO Cola_Fallidos (
                    expediente_id, hash_entrada, errores_json, nodo_bpmn_id,
                    codigo_http, creado_en_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    fallo.expediente_id,
                    fallo.hash_entrada,
                    errores_json,
                    fallo.nodo_bpmn_id,
                    fallo.codigo_http,
                    fallo.creado_en_utc.isoformat(),
                ),
            )

    def cantidad_solicitudes(self, expediente_id: str) -> int:
        with self._lock:
            fila = self._conexion.execute(
                "SELECT COUNT(*) AS total FROM Solicitudes WHERE expediente_id = ?",
                (expediente_id,),
            ).fetchone()
        return int(fila["total"]) if fila is not None else 0

    def cantidad_fallidos(self, expediente_id: str) -> int:
        with self._lock:
            fila = self._conexion.execute(
                "SELECT COUNT(*) AS total FROM Cola_Fallidos WHERE expediente_id = ?",
                (expediente_id,),
            ).fetchone()
        return int(fila["total"]) if fila is not None else 0

    def close(self) -> None:
        if self._propietario_conexion:
            self._conexion.close()

    def __enter__(self) -> RepositorioP01SQLite:
        return self

    def __exit__(self, _tipo: object, _valor: object, _traza: object) -> None:
        self.close()


class ResultadoRegistroP01(BaseModel):
    """Resultado HTTP y ruta BPMN, sin devolver el contrato con PII."""

    model_config = ConfigDict(frozen=True)

    expediente_id: str
    version_documental: int = 1
    codigo_http: CodigoHttpP01
    estado: EstadoRegistroP01
    clave_idempotencia_sha256: str | None = None
    errores: tuple[ErrorValidacionP01, ...] = ()
    nodos_bpmn: tuple[str, ...]
    nodo_final: str


def _hash_entrada(datos: Mapping[str, object] | ContratoEntradaP01) -> str:
    if isinstance(datos, ContratoEntradaP01):
        serializado = datos.model_dump_json()
    else:
        serializado = json.dumps(
            dict(datos),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=lambda _valor: "<valor-no-serializable>",
        )
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def p1_consultar(
    repositorio: RepositorioP01,
    *,
    expediente_id: str,
    clave_idempotencia_sha256: str,
) -> RegistroSolicitudP01 | None:
    return repositorio.consultar(expediente_id, clave_idempotencia_sha256)


def p1_existe(registro: RegistroSolicitudP01 | None) -> bool:
    return registro is not None


def p1_registrar(
    repositorio: RepositorioP01,
    *,
    expediente_id: str,
    clave_idempotencia_sha256: str,
    contrato: ContratoEntradaP01,
) -> bool:
    return repositorio.registrar(expediente_id, clave_idempotencia_sha256, contrato)


def _resultado_duplicado(expediente_id: str, clave: str) -> ResultadoRegistroP01:
    return ResultadoRegistroP01(
        expediente_id=expediente_id,
        codigo_http=CODIGO_DUPLICADO,
        estado="duplicada",
        clave_idempotencia_sha256=clave,
        nodos_bpmn=("p1_200", "p1_fin_duplicada"),
        nodo_final="p1_fin_duplicada",
    )


def p1_200(expediente_id: str, clave_idempotencia_sha256: str) -> ResultadoRegistroP01:
    return _resultado_duplicado(expediente_id, clave_idempotencia_sha256)


def p1_fallidos_datos(
    repositorio: RepositorioP01,
    *,
    expediente_id: str,
    hash_entrada: str,
    errores: tuple[ErrorValidacionP01, ...],
) -> FalloDatosP01:
    fallo = FalloDatosP01(
        expediente_id=expediente_id,
        hash_entrada=hash_entrada,
        errores=errores,
        creado_en_utc=datetime.now(UTC),
    )
    repositorio.registrar_fallido(fallo)
    return fallo


def p1_400(fallo: FalloDatosP01) -> ResultadoRegistroP01:
    return ResultadoRegistroP01(
        expediente_id=fallo.expediente_id,
        codigo_http=CODIGO_INVALIDO,
        estado="invalida",
        errores=fallo.errores,
        nodos_bpmn=(
            "p1_validar",
            "p1_valida",
            "p1_fallidos_datos",
            "p1_400",
            "p1_fin_invalida",
        ),
        nodo_final="p1_fin_invalida",
    )


def _resultado_creado(expediente_id: str, clave: str) -> ResultadoRegistroP01:
    return ResultadoRegistroP01(
        expediente_id=expediente_id,
        codigo_http=CODIGO_CREADO,
        estado="creada",
        clave_idempotencia_sha256=clave,
        nodos_bpmn=("p1_registrar", "p1_201", "p1_fin_nueva"),
        nodo_final="p1_fin_nueva",
    )


def p1_201(expediente_id: str, clave_idempotencia_sha256: str) -> ResultadoRegistroP01:
    """Respuesta normativa para una inserción nueva."""

    return _resultado_creado(expediente_id, clave_idempotencia_sha256)


def procesar_solicitud_p01(
    *,
    expediente_id: str,
    datos: Mapping[str, object] | ContratoEntradaP01,
    repositorio: RepositorioP01,
    verificar_adjunto: VerificadorAdjunto | None = None,
) -> ResultadoRegistroP01:
    """Recorre la ruta determinista completa de P01 y emite 201/200/400."""

    validacion: ResultadoValidacionP01 = validar_solicitud(
        datos,
        expediente_id=expediente_id,
        verificar_adjunto=verificar_adjunto,
    )
    if not validacion.valido or validacion.contrato is None:
        fallo = p1_fallidos_datos(
            repositorio,
            expediente_id=validacion.expediente_id,
            hash_entrada=_hash_entrada(datos),
            errores=validacion.errores,
        )
        return p1_400(fallo)

    normalizado = p1_normalizar(validacion.contrato)
    clave = p1_hash(normalizado)
    existente = p1_consultar(
        repositorio,
        expediente_id=validacion.expediente_id,
        clave_idempotencia_sha256=clave,
    )
    if p1_existe(existente):
        resultado = p1_200(validacion.expediente_id, clave)
        return resultado.model_copy(
            update={
                "nodos_bpmn": (
                    "p1_validar",
                    "p1_valida",
                    "p1_normalizar",
                    "p1_hash",
                    "p1_consultar",
                    "p1_existe",
                    *resultado.nodos_bpmn,
                )
            }
        )

    insertado = p1_registrar(
        repositorio,
        expediente_id=validacion.expediente_id,
        clave_idempotencia_sha256=clave,
        contrato=normalizado,
    )
    # Una carrera concurrente que inserto la misma clave se resuelve como 200.
    if not insertado:
        resultado = p1_200(validacion.expediente_id, clave)
    else:
        resultado = p1_201(validacion.expediente_id, clave)
    return resultado.model_copy(
        update={
            "nodos_bpmn": (
                "p1_validar",
                "p1_valida",
                "p1_normalizar",
                "p1_hash",
                "p1_consultar",
                "p1_existe",
                *resultado.nodos_bpmn,
            )
        }
    )


__all__ = [
    "CODIGO_CREADO",
    "CODIGO_DUPLICADO",
    "CODIGO_INVALIDO",
    "FalloDatosP01",
    "RegistroSolicitudP01",
    "RepositorioP01",
    "RepositorioP01Memoria",
    "RepositorioP01SQLite",
    "ResultadoRegistroP01",
    "generar_clave_idempotencia",
    "normalizar_contrato",
    "p1_200",
    "p1_201",
    "p1_400",
    "p1_consultar",
    "p1_existe",
    "p1_fallidos_datos",
    "p1_hash",
    "p1_normalizar",
    "p1_registrar",
    "procesar_solicitud_p01",
]
