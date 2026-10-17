"""Persistencia SQLite transaccional para expedientes, HITL y trazabilidad."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def ahora_utc() -> str:
    """Devuelve una marca ISO-8601 comparable y siempre consciente de zona."""

    return datetime.now(UTC).isoformat()


def _json(valor: object) -> str:
    return json.dumps(valor, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class BaseDatos:
    """Motor SQLite persistente con aislamiento explícito por expediente.

    La clase no guarda contenido binario. Los documentos originales permanecen en
    almacenamiento protegido y SQLite conserva únicamente referencias, hashes y la
    representación minimizada autorizada.
    """

    TABLAS = frozenset(
        {
            "Clientes",
            "Memoria_Cliente",
            "Solicitudes",
            "Cola_Fallidos",
            "Versiones_Recaudos",
            "Tareas_HITL",
            "Mensajes_WhatsApp",
            "Trazas_BPMN",
        }
    )

    def __init__(self, ruta: str | Path = "sagale.sqlite3") -> None:
        self.ruta = str(ruta)
        self._conexion = sqlite3.connect(self.ruta, check_same_thread=False)
        self._conexion.row_factory = sqlite3.Row
        self._conexion.execute("PRAGMA foreign_keys = ON")
        self._conexion.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.RLock()
        self._cerrada = False
        self._crear_esquema()

    @property
    def conexion(self) -> sqlite3.Connection:
        """Expone la conexión para adaptadores internos que comparten transacción."""

        if self._cerrada:
            raise RuntimeError("La base de datos está cerrada.")
        return self._conexion

    @contextmanager
    def transaccion(self) -> Iterator[sqlite3.Connection]:
        """Serializa una transacción y revierte automáticamente ante errores."""

        with self._lock:
            try:
                yield self.conexion
                self._conexion.commit()
            except Exception:
                self._conexion.rollback()
                raise

    def _crear_esquema(self) -> None:
        esquema = """
        CREATE TABLE IF NOT EXISTS Clientes (
            cliente_id TEXT PRIMARY KEY,
            nombre TEXT NOT NULL,
            contacto_protegido TEXT NOT NULL,
            consentimiento_pii INTEGER NOT NULL CHECK (consentimiento_pii IN (0, 1)),
            creado_en_utc TEXT NOT NULL,
            actualizado_en_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS Memoria_Cliente (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id TEXT NOT NULL REFERENCES Clientes(cliente_id),
            expediente_id TEXT NOT NULL,
            rol TEXT NOT NULL,
            contenido TEXT NOT NULL,
            metadatos_json TEXT NOT NULL DEFAULT '{}',
            creado_en_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memoria_cliente_exp
            ON Memoria_Cliente(cliente_id, expediente_id, id);

        CREATE TABLE IF NOT EXISTS Solicitudes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            expediente_id TEXT NOT NULL,
            clave_idempotencia_sha256 TEXT NOT NULL,
            contrato_json TEXT NOT NULL,
            creado_en_utc TEXT NOT NULL,
            UNIQUE (expediente_id, clave_idempotencia_sha256)
        );
        CREATE INDEX IF NOT EXISTS idx_solicitudes_exp
            ON Solicitudes(expediente_id, clave_idempotencia_sha256);

        CREATE TABLE IF NOT EXISTS Cola_Fallidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            expediente_id TEXT NOT NULL,
            hash_entrada TEXT NOT NULL,
            errores_json TEXT NOT NULL,
            nodo_bpmn_id TEXT NOT NULL,
            codigo_http INTEGER NOT NULL,
            creado_en_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS Versiones_Recaudos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            expediente_id TEXT NOT NULL,
            version_documental INTEGER NOT NULL CHECK (version_documental >= 1),
            documento_id TEXT NOT NULL,
            fuente_id TEXT NOT NULL,
            hash_sha256 TEXT NOT NULL,
            clasificacion_acceso TEXT NOT NULL,
            contenido_indexable TEXT NOT NULL,
            creado_en_utc TEXT NOT NULL,
            UNIQUE (expediente_id, version_documental, documento_id)
        );
        CREATE INDEX IF NOT EXISTS idx_recaudos_aislamiento
            ON Versiones_Recaudos(expediente_id, version_documental);

        CREATE TABLE IF NOT EXISTS Tareas_HITL (
            tarea_id TEXT PRIMARY KEY,
            expediente_id TEXT NOT NULL,
            version_documental INTEGER NOT NULL,
            nodo_bpmn_id TEXT NOT NULL,
            estado TEXT NOT NULL CHECK (estado IN ('pendiente', 'resuelto')),
            solicitud_json TEXT NOT NULL,
            respuesta_json TEXT,
            actor_resolucion TEXT,
            creado_en_utc TEXT NOT NULL,
            resuelto_en_utc TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_hitl_exp ON Tareas_HITL(expediente_id, estado);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_hitl_pendiente_unica
            ON Tareas_HITL(expediente_id, version_documental, nodo_bpmn_id)
            WHERE estado = 'pendiente';

        CREATE TABLE IF NOT EXISTS Mensajes_WhatsApp (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mensaje_id TEXT NOT NULL UNIQUE,
            cliente_id TEXT NOT NULL,
            expediente_id TEXT NOT NULL,
            direccion TEXT NOT NULL CHECK (direccion IN ('entrada', 'salida')),
            tipo TEXT NOT NULL,
            contenido TEXT NOT NULL,
            creado_en_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_whatsapp_exp
            ON Mensajes_WhatsApp(expediente_id, id);

        CREATE TABLE IF NOT EXISTS Trazas_BPMN (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT NOT NULL,
            expediente_id TEXT NOT NULL,
            version_documental INTEGER NOT NULL,
            proceso_actual TEXT NOT NULL CHECK (proceso_actual IN ('P01', 'P02', 'P03')),
            nodo_bpmn_id TEXT NOT NULL,
            actor TEXT NOT NULL,
            resultado TEXT NOT NULL,
            metadatos_json TEXT NOT NULL DEFAULT '{}',
            timestamp_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_trazas_exp
            ON Trazas_BPMN(expediente_id, version_documental, id);
        """
        with self._lock:
            self._conexion.executescript(esquema)
            self._conexion.commit()

    def upsert_cliente(
        self,
        cliente_id: str,
        nombre: str,
        contacto_protegido: str,
        *,
        consentimiento_pii: bool,
    ) -> None:
        instante = ahora_utc()
        with self.transaccion() as conexion:
            conexion.execute(
                """
                INSERT INTO Clientes (
                    cliente_id, nombre, contacto_protegido, consentimiento_pii,
                    creado_en_utc, actualizado_en_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cliente_id) DO UPDATE SET
                    nombre = excluded.nombre,
                    contacto_protegido = excluded.contacto_protegido,
                    consentimiento_pii = excluded.consentimiento_pii,
                    actualizado_en_utc = excluded.actualizado_en_utc
                """,
                (
                    cliente_id,
                    nombre,
                    contacto_protegido,
                    int(consentimiento_pii),
                    instante,
                    instante,
                ),
            )

    def agregar_memoria_cliente(
        self,
        cliente_id: str,
        expediente_id: str,
        rol: str,
        contenido: str,
        metadatos: Mapping[str, object] | None = None,
    ) -> int:
        with self.transaccion() as conexion:
            cursor = conexion.execute(
                """
                INSERT INTO Memoria_Cliente (
                    cliente_id, expediente_id, rol, contenido, metadatos_json, creado_en_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (cliente_id, expediente_id, rol, contenido, _json(metadatos or {}), ahora_utc()),
            )
        return int(cursor.lastrowid or 0)

    def existe_cliente(self, cliente_id: str) -> bool:
        filas = self._consultar(
            "SELECT 1 AS existe FROM Clientes WHERE cliente_id = ? LIMIT 1",
            (cliente_id,),
        )
        return bool(filas)

    def listar_memoria_cliente(self, cliente_id: str, expediente_id: str) -> list[dict[str, Any]]:
        filas = self._consultar(
            """
            SELECT id, rol, contenido, metadatos_json, creado_en_utc
            FROM Memoria_Cliente
            WHERE cliente_id = ? AND expediente_id = ? ORDER BY id
            """,
            (cliente_id, expediente_id),
        )
        return [
            {
                "id": int(fila["id"]),
                "rol": str(fila["rol"]),
                "contenido": str(fila["contenido"]),
                "metadatos": json.loads(str(fila["metadatos_json"])),
                "creado_en_utc": str(fila["creado_en_utc"]),
            }
            for fila in filas
        ]

    def registrar_version_recaudo(
        self,
        *,
        expediente_id: str,
        version_documental: int,
        documento_id: str,
        fuente_id: str,
        hash_sha256: str,
        clasificacion_acceso: str,
        contenido_indexable: str,
    ) -> int:
        with self.transaccion() as conexion:
            cursor = conexion.execute(
                """
                INSERT OR IGNORE INTO Versiones_Recaudos (
                    expediente_id, version_documental, documento_id, fuente_id,
                    hash_sha256, clasificacion_acceso, contenido_indexable, creado_en_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    expediente_id,
                    version_documental,
                    documento_id,
                    fuente_id,
                    hash_sha256,
                    clasificacion_acceso,
                    contenido_indexable,
                    ahora_utc(),
                ),
            )
            if cursor.rowcount == 0:
                existente = conexion.execute(
                    """
                    SELECT id FROM Versiones_Recaudos
                    WHERE expediente_id = ? AND version_documental = ? AND documento_id = ?
                    """,
                    (expediente_id, version_documental, documento_id),
                ).fetchone()
                if existente is None:
                    raise RuntimeError("No se pudo recuperar la versión idempotente.")
                return int(existente["id"])
        return int(cursor.lastrowid or 0)

    def listar_recaudos(
        self, expediente_id: str, version_documental: int | None = None
    ) -> list[dict[str, Any]]:
        parametros: tuple[object, ...]
        if version_documental is None:
            sql = """
                SELECT version_documental, documento_id, fuente_id, hash_sha256,
                       clasificacion_acceso, contenido_indexable
                FROM Versiones_Recaudos WHERE expediente_id = ? ORDER BY id
            """
            parametros = (expediente_id,)
        else:
            sql = """
                SELECT version_documental, documento_id, fuente_id, hash_sha256,
                       clasificacion_acceso, contenido_indexable
                FROM Versiones_Recaudos
                WHERE expediente_id = ? AND version_documental = ? ORDER BY id
            """
            parametros = (expediente_id, version_documental)
        return [dict(fila) for fila in self._consultar(sql, parametros)]

    def crear_tarea_hitl(
        self,
        *,
        expediente_id: str,
        version_documental: int,
        nodo_bpmn_id: str,
        solicitud: Mapping[str, object],
    ) -> str:
        """Crea una tarea pendiente o devuelve la pendiente idempotente existente."""

        existente = self._consultar(
            """
            SELECT tarea_id FROM Tareas_HITL
            WHERE expediente_id = ? AND version_documental = ?
              AND nodo_bpmn_id = ? AND estado = 'pendiente'
            """,
            (expediente_id, version_documental, nodo_bpmn_id),
        )
        if existente:
            return str(existente[0]["tarea_id"])
        tarea_id = str(uuid.uuid4())
        with self.transaccion() as conexion:
            conexion.execute(
                """
                INSERT INTO Tareas_HITL (
                    tarea_id, expediente_id, version_documental, nodo_bpmn_id,
                    estado, solicitud_json, creado_en_utc
                ) VALUES (?, ?, ?, ?, 'pendiente', ?, ?)
                """,
                (
                    tarea_id,
                    expediente_id,
                    version_documental,
                    nodo_bpmn_id,
                    _json(dict(solicitud)),
                    ahora_utc(),
                ),
            )
        return tarea_id

    def resolver_tarea_hitl(
        self,
        tarea_id: str,
        *,
        actor: str,
        respuesta: Mapping[str, object],
    ) -> bool:
        with self.transaccion() as conexion:
            cursor = conexion.execute(
                """
                UPDATE Tareas_HITL SET estado = 'resuelto', respuesta_json = ?,
                    actor_resolucion = ?, resuelto_en_utc = ?
                WHERE tarea_id = ? AND estado = 'pendiente'
                """,
                (_json(dict(respuesta)), actor, ahora_utc(), tarea_id),
            )
        return cursor.rowcount == 1

    def listar_tareas_hitl(
        self, expediente_id: str, estado: str | None = None
    ) -> list[dict[str, Any]]:
        if estado is None:
            sql = "SELECT * FROM Tareas_HITL WHERE expediente_id = ? ORDER BY creado_en_utc"
            parametros: tuple[object, ...] = (expediente_id,)
        else:
            sql = (
                "SELECT * FROM Tareas_HITL WHERE expediente_id = ? "
                "AND estado = ? ORDER BY creado_en_utc"
            )
            parametros = (expediente_id, estado)
        return [dict(fila) for fila in self._consultar(sql, parametros)]

    def registrar_mensaje_whatsapp(
        self,
        *,
        mensaje_id: str,
        cliente_id: str,
        expediente_id: str,
        direccion: str,
        tipo: str,
        contenido: str,
    ) -> bool:
        try:
            with self.transaccion() as conexion:
                conexion.execute(
                    """
                    INSERT INTO Mensajes_WhatsApp (
                        mensaje_id, cliente_id, expediente_id, direccion,
                        tipo, contenido, creado_en_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mensaje_id,
                        cliente_id,
                        expediente_id,
                        direccion,
                        tipo,
                        contenido,
                        ahora_utc(),
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def listar_mensajes_whatsapp(self, expediente_id: str) -> list[dict[str, Any]]:
        return [
            dict(fila)
            for fila in self._consultar(
                """
                SELECT mensaje_id, cliente_id, direccion, tipo, contenido, creado_en_utc
                FROM Mensajes_WhatsApp WHERE expediente_id = ? ORDER BY id
                """,
                (expediente_id,),
            )
        ]

    def registrar_traza(
        self,
        *,
        trace_id: str,
        expediente_id: str,
        version_documental: int,
        proceso_actual: str,
        nodo_bpmn_id: str,
        actor: str,
        resultado: str,
        metadatos: Mapping[str, object] | None = None,
    ) -> int:
        with self.transaccion() as conexion:
            cursor = conexion.execute(
                """
                INSERT INTO Trazas_BPMN (
                    trace_id, expediente_id, version_documental, proceso_actual,
                    nodo_bpmn_id, actor, resultado, metadatos_json, timestamp_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    expediente_id,
                    version_documental,
                    proceso_actual,
                    nodo_bpmn_id,
                    actor,
                    resultado,
                    _json(metadatos or {}),
                    ahora_utc(),
                ),
            )
        return int(cursor.lastrowid or 0)

    def listar_trazas(self, expediente_id: str) -> list[dict[str, Any]]:
        return [
            dict(fila)
            for fila in self._consultar(
                "SELECT * FROM Trazas_BPMN WHERE expediente_id = ? ORDER BY id",
                (expediente_id,),
            )
        ]

    def contar(self, tabla: str, expediente_id: str | None = None) -> int:
        if tabla not in self.TABLAS:
            raise ValueError("Tabla no autorizada.")
        if expediente_id is None or tabla == "Clientes":
            sql = f'SELECT COUNT(*) AS total FROM "{tabla}"'
            parametros: tuple[object, ...] = ()
        else:
            sql = f'SELECT COUNT(*) AS total FROM "{tabla}" WHERE expediente_id = ?'
            parametros = (expediente_id,)
        filas = self._consultar(sql, parametros)
        return int(filas[0]["total"])

    def _consultar(self, sql: str, parametros: tuple[object, ...]) -> list[sqlite3.Row]:
        with self._lock:
            return list(self.conexion.execute(sql, parametros).fetchall())

    def close(self) -> None:
        with self._lock:
            if not self._cerrada:
                self._conexion.close()
                self._cerrada = True

    def __enter__(self) -> BaseDatos:
        return self

    def __exit__(self, _tipo: object, _valor: object, _traza: object) -> None:
        self.close()


__all__ = ["BaseDatos", "ahora_utc"]
