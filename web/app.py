"""Dashboard FastAPI para la demostración en vivo de Ságale Abogados."""

from __future__ import annotations

import hashlib
import json
import threading
import unicodedata
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentes.agente_base import EstadoAgente
from agentes.orquestador import Orquestador, Proceso
from decision.calibracion import Calibrador, ObservacionCalibracion
from ingesta.gestor_recaudos import FragmentoRAG
from nucleo.estado import crear_estado
from rpa.plantilla_minuta_p03 import DatosMinuta, ensamblar_minuta
from web.demo_datos import (
    CALIFICACION_CORREGIDA_EXP_003,
    CALIFICACIONES_DEMO,
    EXPEDIENTES_DEMO,
    calificacion_json,
    cargar_datos_demo,
    cliente_para,
    contrato_para,
    datos_minuta_para,
    recaudos_para,
    ruta_base_demo,
)

NODOS_VISIBLES: dict[str, tuple[str, ...]] = {
    "P01": (
        "p1_validar",
        "p1_hash",
        "p1_consultar",
        "p1_registrar",
        "p1_201",
        "p1_200",
        "p1_400",
    ),
    "P02": (
        "p2_documentos",
        "p2_agente",
        "p2_parser",
        "p2_suficiente",
        "p2_legal",
        "p2_subsanacion",
        "p2_version",
    ),
    "P03": (
        "p3_borrador",
        "p3_revision",
        "p3_esperar",
        "p3_respuesta",
        "p3_firma",
        "p3_notaria",
        "p3_fin",
    ),
}

DECISIONES_POR_NODO: dict[str, tuple[str, ...]] = {
    "p1_incidente": ("recuperar", "continuar", "cerrar_incidente"),
    "p2_revision_fallo": ("continuar",),
    "p2_legal": ("viable", "subsanar", "descartar"),
    "p2_subsanacion": ("solicitar", "notificar"),
    "p2_archivar": ("archivar",),
    "p3_revision": ("aprobar", "corregir"),
    "p3_firma": ("firmar",),
}

ACTORES_HITL = frozenset({"Marcela Colonia", "Abogado Titular"})
FINALES = frozenset(
    {
        "p1_fin_nueva",
        "p1_fin_duplicada",
        "p1_fin_invalida",
        "p1_fin_incidente",
        "p2_fin_viable",
        "p2_fin_descartada",
        "p2_fin_abandono",
        "p3_fin",
        "p3_fin_sin_conformidad",
    }
)
TITULOS_EXPEDIENTE = {
    "EXP-2026-001": "Predio limpio · recorrido completo",
    "EXP-2026-002": "Hipoteca levantable · revisión legal",
    "EXP-2026-003": "Subsanación documental · Partida 4433221100",
    "EXP-2026-004": "Solicitud inválida · 400 Bad Request",
    "EXP-2026-001-LIVE": "Solicitud nueva por WhatsApp · demostración P01",
}
EXPEDIENTE_EN_VIVO = "EXP-2026-001-LIVE"


def _validar_texto_web(valor: str) -> str:
    if any(
        unicodedata.category(caracter) == "Cc" and caracter not in "\n\r\t" for caracter in valor
    ):
        raise ValueError("El texto contiene caracteres de control no permitidos.")
    if valor and not any(
        not caracter.isspace() and unicodedata.category(caracter) != "Cf" for caracter in valor
    ):
        raise ValueError("Escriba texto visible; no se admiten solo espacios.")
    return valor


class MensajeEntrada(BaseModel):
    """Mensaje libre de la caja WhatsApp; no activa controles por inferencia."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    contenido: str = Field(min_length=1, max_length=1800)
    expediente_id: str = Field(
        default="EXP-2026-001",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
    )

    @field_validator("contenido", mode="before")
    @classmethod
    def validar_contenido(cls, valor: object) -> object:
        return _validar_texto_web(valor) if isinstance(valor, str) else valor


class ResolucionHITLEntrada(BaseModel):
    """Respuesta explícita y autenticada de los dos roles habilitados."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    actor: str = Field(min_length=2, max_length=200)
    decision: str = Field(min_length=1, max_length=80)
    observacion: str = Field(default="", max_length=1600)
    kardex_notarial: str | None = Field(default=None, max_length=120)

    @field_validator("observacion", "kardex_notarial", mode="before")
    @classmethod
    def validar_texto(cls, valor: object) -> object:
        return _validar_texto_web(valor) if isinstance(valor, str) else valor

    @model_validator(mode="after")
    def validar_firma(self) -> ResolucionHITLEntrada:
        if self.decision == "firmar" and not self.kardex_notarial:
            raise ValueError("Indique el kárdex notarial para registrar la firma.")
        return self


def _proceso_de_nodo(nodo_bpmn_id: str) -> Proceso:
    if nodo_bpmn_id.startswith("p1_"):
        return "P01"
    if nodo_bpmn_id.startswith("p2_"):
        return "P02"
    if nodo_bpmn_id.startswith("p3_"):
        return "P03"
    raise ValueError("Nodo BPMN no reconocido.")


def _mascara_hash(valor: object) -> str:
    texto = str(valor or "")
    return f"{texto[:12]}…" if len(texto) > 12 else texto


class ServicioDemo:
    """Coordina la base demo, grafos reanudables y datos del monitor web."""

    def __init__(self, ruta_base: str | Path | None = None) -> None:
        self.ruta_base = Path(ruta_base) if ruta_base is not None else ruta_base_demo()
        cargar_datos_demo(self.ruta_base)
        self.orquestador = Orquestador(self.ruta_base, dormir=lambda _segundos: None)
        self._bloqueo = threading.RLock()
        self._estados: dict[str, EstadoAgente] = {}
        self._hilos: dict[str, tuple[Proceso, str]] = {}
        self._fases: dict[str, str] = {
            "EXP-2026-004": "p1_400",
        }
        self._calificaciones: dict[str, dict[str, object]] = {
            expediente_id: dict(calificacion)
            for expediente_id, calificacion in CALIFICACIONES_DEMO.items()
        }
        self._calibrador = Calibrador(n_bins=5)
        self._hidratar_indice_rag()
        self._sembrar_calibracion()
        self._preparar_hilos_interactivos()

    def close(self) -> None:
        self.orquestador.close()

    def _hidratar_indice_rag(self) -> None:
        """Reconstruye el índice solo desde registros ya aceptados por PII."""

        for expediente_id in EXPEDIENTES_DEMO:
            for recaudo in self.orquestador.base_datos.listar_recaudos(expediente_id):
                self.orquestador.indice.indexar(
                    FragmentoRAG(
                        expediente_id=expediente_id,
                        version_documental=int(recaudo["version_documental"]),
                        documento_id=str(recaudo["documento_id"]),
                        fuente_id=str(recaudo["fuente_id"]),
                        hash_sha256=str(recaudo["hash_sha256"]),
                        ubicacion="fragmento-1",
                        texto=str(recaudo["contenido_indexable"]),
                    )
                )

    def _sembrar_calibracion(self) -> None:
        for expediente_id, nodo, probabilidad, etiqueta in (
            ("EXP-2026-001", "p2_legal", 0.96, True),
            ("EXP-2026-002", "p2_legal", 0.82, True),
            ("EXP-2026-003", "p2_subsanacion", 0.31, False),
        ):
            self._calibrador.registrar(
                ObservacionCalibracion(
                    expediente_id=expediente_id,
                    version_documental=1,
                    nodo_bpmn_id=nodo,
                    probabilidad=probabilidad,
                    etiqueta_humana=etiqueta,
                    version_umbrales="demo-2026",
                )
            )

    @staticmethod
    def _estado_inicial(expediente_id: str) -> EstadoAgente:
        return cast(EstadoAgente, crear_estado(expediente_id))

    def _preparar_hilos_interactivos(self) -> None:
        """Ubica los casos en sus nodos reales de revisión o espera de mensaje."""

        estado_hipoteca = self.orquestador.iniciar_p02(
            self._estado_inicial("EXP-2026-002"),
            recaudos=recaudos_para("EXP-2026-002"),
            thread_id="demo-exp-002-p2",
            salida_llm=calificacion_json("EXP-2026-002"),
        )
        self._estados["EXP-2026-002"] = estado_hipoteca
        self._hilos["EXP-2026-002"] = ("P02", "demo-exp-002-p2")
        self._fases["EXP-2026-002"] = "p2_legal"

        estado_subsanacion = self.orquestador.iniciar_p02(
            self._estado_inicial("EXP-2026-003"),
            recaudos=recaudos_para("EXP-2026-003"),
            thread_id="demo-exp-003-p2",
            salida_llm=calificacion_json("EXP-2026-003"),
            salida_llm_corregida=calificacion_json("EXP-2026-003", corregida=True),
            recaudos_corregidos=recaudos_para("EXP-2026-003", version_documental=2),
            evento_espera="respuesta",
            espera_interactiva=True,
        )
        self._estados["EXP-2026-003"] = estado_subsanacion
        self._hilos["EXP-2026-003"] = ("P02", "demo-exp-003-p2")
        self._fases["EXP-2026-003"] = "p2_subsanacion"

        estado_minuta = self.orquestador.iniciar_p03(
            self._estado_inicial("EXP-2026-001"),
            datos_minuta=datos_minuta_para("EXP-2026-001"),
            thread_id="demo-exp-001-p3",
            evento_espera="respuesta",
            cliente_conforme=True,
            espera_interactiva=True,
        )
        self._estados["EXP-2026-001"] = estado_minuta
        self._hilos["EXP-2026-001"] = ("P03", "demo-exp-001-p3")
        self._fases["EXP-2026-001"] = "p3_revision"

    def _cliente(self, expediente_id: str) -> dict[str, str]:
        if expediente_id == EXPEDIENTE_EN_VIVO:
            return {
                "cliente_id": "CLI-2026-001-LIVE",
                "nombre": "Cliente WhatsApp en vivo",
                "contacto": "+51 [CONTACTO_PROTEGIDO]",
            }
        return cliente_para(expediente_id)

    def _asegurar_cliente(self, expediente_id: str) -> dict[str, str]:
        cliente = self._cliente(expediente_id)
        self.orquestador.base_datos.upsert_cliente(
            cliente["cliente_id"],
            cliente["nombre"],
            cliente["contacto"],
            consentimiento_pii=True,
        )
        return cliente

    def _registrar_mensaje(
        self,
        *,
        expediente_id: str,
        direccion: Literal["entrada", "salida"],
        tipo: str,
        contenido: str,
    ) -> None:
        cliente = self._asegurar_cliente(expediente_id)
        huella = hashlib.sha256(
            f"{expediente_id}|{direccion}|{tipo}|{contenido}".encode()
        ).hexdigest()
        self.orquestador.base_datos.registrar_mensaje_whatsapp(
            mensaje_id=huella,
            cliente_id=cliente["cliente_id"],
            expediente_id=expediente_id,
            direccion=direccion,
            tipo=tipo,
            contenido=contenido,
        )

    def _fase_actual(self, expediente_id: str) -> str:
        if expediente_id in self._fases:
            return self._fases[expediente_id]
        trazas = self.orquestador.base_datos.listar_trazas(expediente_id)
        return str(trazas[-1]["nodo_bpmn_id"]) if trazas else "p1_inicio"

    def _version_actual(self, expediente_id: str) -> int:
        recaudos = self.orquestador.base_datos.listar_recaudos(expediente_id)
        if recaudos:
            return max(int(item["version_documental"]) for item in recaudos)
        estado = self._estados.get(expediente_id)
        return int(estado["version_documental"]) if estado else 1

    def _parser_visible(self, expediente_id: str) -> dict[str, object]:
        calificacion = self._calificaciones.get(expediente_id)
        if calificacion is None:
            return {
                "status": "sin_calificación",
                "hallazgos": [],
                "riesgos": [],
                "evidencias": [],
                "documentos_faltantes": [],
            }
        suficiente = bool(calificacion["suficiente"])
        observaciones = [str(valor) for valor in cast(list[object], calificacion["observaciones"])]
        citas = cast(list[dict[str, object]], calificacion["citas"])
        riesgos = [valor for valor in observaciones if "gravamen" in valor.casefold()]
        faltantes: list[str] = []
        if not suficiente and expediente_id == "EXP-2026-003":
            faltantes.append("Copia Literal de la Partida 4433221100")
        return {
            "status": "suficiente" if suficiente else "requiere_subsanación",
            "hallazgos": observaciones,
            "riesgos": riesgos
            or (["No se detectan riesgos registrales críticos."] if suficiente else []),
            "evidencias": [
                f"{cita['documento_id']}@v{cita['version_documental']}:{cita['ubicacion']}"
                for cita in citas
            ],
            "documentos_faltantes": faltantes,
        }

    def _tareas_pendientes(self) -> list[dict[str, object]]:
        expedientes = [*EXPEDIENTES_DEMO]
        if EXPEDIENTE_EN_VIVO in self._fases:
            expedientes.append(EXPEDIENTE_EN_VIVO)
        tareas: list[dict[str, object]] = []
        for expediente_id in expedientes:
            for tarea in self.orquestador.base_datos.listar_tareas_hitl(expediente_id, "pendiente"):
                nodo = str(tarea["nodo_bpmn_id"])
                try:
                    solicitud = json.loads(str(tarea["solicitud_json"]))
                except json.JSONDecodeError:
                    solicitud = {"descripcion": "Solicitud de revisión registrada."}
                tareas.append(
                    {
                        "tarea_id": str(tarea["tarea_id"]),
                        "expediente_id": expediente_id,
                        "version_documental": int(tarea["version_documental"]),
                        "nodo_bpmn_id": nodo,
                        "solicitud": solicitud,
                        "decisiones": DECISIONES_POR_NODO.get(nodo, ()),
                        "requiere_kardex": nodo == "p3_firma",
                    }
                )
        return tareas

    def _metricas(self) -> dict[str, object]:
        activos = sum(1 for fase in self._fases.values() if fase not in FINALES | {"p1_400"})
        calibracion = self._calibrador.metricas()
        return {
            "expedientes_activos": activos,
            "cola_fallidos": self.orquestador.base_datos.contar("Cola_Fallidos"),
            "hitl_pendiente": len(self._tareas_pendientes()),
            "documentos_pii_indexados": self.orquestador.base_datos.contar("Versiones_Recaudos"),
            "brier": round(calibracion.brier_score, 3),
            "ece": round(calibracion.ece, 3),
            "muestras_calibracion": calibracion.total,
        }

    def tablero(self, expediente_id: str) -> dict[str, object]:
        with self._bloqueo:
            expedientes = [*EXPEDIENTES_DEMO]
            if EXPEDIENTE_EN_VIVO in self._fases:
                expedientes.append(EXPEDIENTE_EN_VIVO)
            if expediente_id not in expedientes:
                raise ValueError("El expediente solicitado no está disponible.")
            seleccionado = expediente_id
            fase = self._fase_actual(seleccionado)
            proceso = _proceso_de_nodo(fase)
            estado: EstadoAgente | None = self._estados.get(seleccionado)
            trazas = self.orquestador.base_datos.listar_trazas(seleccionado)[-14:]
            return {
                "metricas": self._metricas(),
                "expedientes": [
                    {
                        "expediente_id": expediente,
                        "titulo": TITULOS_EXPEDIENTE[expediente],
                        "fase": self._fase_actual(expediente),
                        "version_documental": self._version_actual(expediente),
                    }
                    for expediente in expedientes
                ],
                "seleccionado": {
                    "expediente_id": seleccionado,
                    "titulo": TITULOS_EXPEDIENTE[seleccionado],
                    "fase": fase,
                    "proceso": proceso,
                    "version_documental": self._version_actual(seleccionado),
                    "hash_p01": _mascara_hash(
                        estado.get("clave_idempotencia_sha256") if estado else None
                    ),
                    "nodos_proceso": NODOS_VISIBLES[proceso],
                    "parser": self._parser_visible(seleccionado),
                    "trazas": [
                        {
                            "nodo": str(traza["nodo_bpmn_id"]),
                            "proceso": str(traza["proceso_actual"]),
                            "resultado": "referencia_demo"
                            if traza["actor"] == "semilla_demo"
                            else str(traza["resultado"]),
                        }
                        for traza in trazas
                    ],
                    "minuta_disponible": bool(estado and estado.get("borrador_minuta")),
                    "minuta_previa": self.minuta_markdown(seleccionado)
                    if seleccionado in {"EXP-2026-001", "EXP-2026-002", "EXP-2026-003"}
                    else None,
                    "kardex_notarial": estado.get("kardex_notarial") if estado else None,
                },
                "mensajes": self.orquestador.base_datos.listar_mensajes_whatsapp(seleccionado),
                "tareas": self._tareas_pendientes(),
            }

    def mensaje_libre(self, entrada: MensajeEntrada) -> dict[str, object]:
        with self._bloqueo:
            if entrada.expediente_id not in self._fases:
                raise ValueError("El expediente indicado no está disponible en la demostración.")
            self._registrar_mensaje(
                expediente_id=entrada.expediente_id,
                direccion="entrada",
                tipo="mensaje_libre",
                contenido=entrada.contenido,
            )
            cliente = self._asegurar_cliente(entrada.expediente_id)
            self.orquestador.a3.recordar_pedido(
                cliente_id=cliente["cliente_id"],
                expediente_id=entrada.expediente_id,
                contenido=entrada.contenido,
            )
            return {
                "mensaje": "Mensaje registrado. Los controles BPMN se activan únicamente mediante los botones o HITL explícitos.",
                "expediente_id": entrada.expediente_id,
            }

    def enviar_solicitud(self) -> dict[str, object]:
        with self._bloqueo:
            contrato = contrato_para("EXP-2026-001")
            estado = self.orquestador.iniciar_p01(
                expediente_id=EXPEDIENTE_EN_VIVO,
                contrato=contrato,
                thread_id=f"demo-live-p1-{uuid.uuid4()}",
            )
            self._estados[EXPEDIENTE_EN_VIVO] = estado
            self._fases[EXPEDIENTE_EN_VIVO] = str(estado["nodo_bpmn_actual"])
            self._registrar_mensaje(
                expediente_id=EXPEDIENTE_EN_VIVO,
                direccion="entrada",
                tipo="solicitud_minuta",
                contenido="Solicitud de minuta con 10 campos y enlaces protegidos de recaudos.",
            )
            self._registrar_mensaje(
                expediente_id=EXPEDIENTE_EN_VIVO,
                direccion="salida",
                tipo="bienvenida",
                contenido=(
                    "Solicitud recibida. El Estudio recuerda sus recaudos protegidos y continuará "
                    "la calificación sin exponer datos personales."
                ),
            )
            codigo = int(estado.get("codigo_http") or 500)
            return {
                "mensaje": f"P01 completado: {codigo} {'Created' if codigo == 201 else 'OK'} · hash SHA-256 {_mascara_hash(estado.get('clave_idempotencia_sha256'))}",
                "expediente_id": EXPEDIENTE_EN_VIVO,
                "codigo_http": codigo,
            }

    def reenviar_solicitud(self) -> dict[str, object]:
        with self._bloqueo:
            if EXPEDIENTE_EN_VIVO not in self._estados:
                self.enviar_solicitud()
            estado = self.orquestador.iniciar_p01(
                expediente_id=EXPEDIENTE_EN_VIVO,
                contrato=contrato_para("EXP-2026-001"),
                thread_id=f"demo-live-p1-replay-{uuid.uuid4()}",
            )
            self._estados[EXPEDIENTE_EN_VIVO] = estado
            self._fases[EXPEDIENTE_EN_VIVO] = str(estado["nodo_bpmn_actual"])
            self._registrar_mensaje(
                expediente_id=EXPEDIENTE_EN_VIVO,
                direccion="entrada",
                tipo="reenvio_solicitud",
                contenido="Reenvío de la misma solicitud para validar idempotencia SHA-256.",
            )
            return {
                "mensaje": "p1_existe detectó la misma clave: 200 OK. No se creó un segundo expediente.",
                "expediente_id": EXPEDIENTE_EN_VIVO,
                "codigo_http": int(estado.get("codigo_http") or 500),
            }

    def enviar_documentos_corregidos(self) -> dict[str, object]:
        with self._bloqueo:
            expediente_id = "EXP-2026-003"
            if self._fase_actual(expediente_id) != "p2_esperar":
                raise RuntimeError(
                    "Autorice primero la solicitud de documentos desde la bandeja de revisión."
                )
            proceso, thread_id = self._hilos[expediente_id]
            if proceso != "P02":
                raise RuntimeError("El hilo P02 de subsanación no está disponible.")
            cliente = self._asegurar_cliente(expediente_id)
            estado_mensaje = EstadoAgente(**self._estados[expediente_id])
            estado_mensaje["cliente_id"] = cliente["cliente_id"]
            estado_mensaje["mensaje_cliente"] = (
                "Adjunto la Copia Literal corregida de la Partida 4433221100."
            )
            self.orquestador.a3.procesar_respuesta(estado_mensaje, "P02")
            resultado = self.orquestador.reanudar(
                "P02",
                thread_id=thread_id,
                respuesta_humana={
                    "actor": "Cliente WhatsApp",
                    "autenticado": True,
                    "decision": "respuesta",
                    "observacion": "Copia Literal adjunta.",
                },
            )
            self._estados[expediente_id] = resultado
            self._fases[expediente_id] = "p2_legal"
            self._calificaciones[expediente_id] = dict(CALIFICACION_CORREGIDA_EXP_003)
            return {
                "mensaje": "WhatsApp recibido: p2_respuesta → p2_version v2 → p2_documentos → p2_agente. La revisión legal quedó lista.",
                "expediente_id": expediente_id,
                "codigo_http": 200,
            }

    def confirmar_borrador(self) -> dict[str, object]:
        with self._bloqueo:
            expediente_id = "EXP-2026-001"
            if self._fase_actual(expediente_id) != "p3_esperar":
                raise RuntimeError("Primero apruebe la revisión p3_revision desde la bandeja HITL.")
            proceso, thread_id = self._hilos[expediente_id]
            if proceso != "P03":
                raise RuntimeError("El hilo P03 no está disponible.")
            cliente = self._asegurar_cliente(expediente_id)
            estado_mensaje = EstadoAgente(**self._estados[expediente_id])
            estado_mensaje["cliente_id"] = cliente["cliente_id"]
            estado_mensaje["mensaje_cliente"] = "Conforme con el borrador"
            self.orquestador.a3.procesar_respuesta(estado_mensaje, "P03")
            resultado = self.orquestador.reanudar(
                "P03",
                thread_id=thread_id,
                respuesta_humana={
                    "actor": "Cliente WhatsApp",
                    "autenticado": True,
                    "decision": "conforme",
                    "observacion": "Conformidad emitida dentro de PT72H.",
                },
            )
            self._estados[expediente_id] = resultado
            self._fases[expediente_id] = "p3_firma"
            return {
                "mensaje": "p3_respuesta registrada dentro de PT72H. Se habilitó p3_firma y la captura del kárdex notarial.",
                "expediente_id": expediente_id,
                "codigo_http": 200,
            }

    def _buscar_tarea(self, tarea_id: str) -> dict[str, object]:
        for tarea in self._tareas_pendientes():
            if tarea["tarea_id"] == tarea_id:
                return tarea
        raise ValueError("La tarea HITL no está pendiente o no pertenece a esta demostración.")

    def _calibrar_resolucion(self, *, expediente_id: str, nodo_bpmn_id: str, decision: str) -> None:
        if nodo_bpmn_id not in {"p2_legal", "p2_subsanacion", "p3_revision"}:
            return
        calificacion = self._calificaciones.get(expediente_id, {})
        valor_probabilidad = calificacion.get("probabilidad", 0.5)
        probabilidad = (
            float(valor_probabilidad) if isinstance(valor_probabilidad, (int, float)) else 0.5
        )
        etiqueta = decision in {"viable", "aprobar", "solicitar", "notificar"}
        self._calibrador.registrar(
            ObservacionCalibracion(
                expediente_id=expediente_id,
                version_documental=self._version_actual(expediente_id),
                nodo_bpmn_id=nodo_bpmn_id,
                probabilidad=probabilidad,
                etiqueta_humana=etiqueta,
                version_umbrales="demo-2026",
            )
        )

    def _iniciar_p3(self, expediente_id: str, estado_p2: EstadoAgente) -> None:
        thread_id = f"demo-{expediente_id.casefold()}-p3-{uuid.uuid4()}"
        resultado = self.orquestador.iniciar_p03(
            estado_p2,
            datos_minuta=datos_minuta_para(expediente_id),
            thread_id=thread_id,
            evento_espera="respuesta",
            cliente_conforme=True,
            espera_interactiva=True,
        )
        self._estados[expediente_id] = resultado
        self._hilos[expediente_id] = ("P03", thread_id)
        self._fases[expediente_id] = "p3_revision"

    def resolver_hitl(self, tarea_id: str, entrada: ResolucionHITLEntrada) -> dict[str, object]:
        with self._bloqueo:
            if entrada.actor not in ACTORES_HITL:
                raise ValueError("Solo Marcela Colonia o el Abogado Titular pueden resolver HITL.")
            tarea = self._buscar_tarea(tarea_id)
            expediente_id = str(tarea["expediente_id"])
            nodo = str(tarea["nodo_bpmn_id"])
            if nodo in {"p3_revision", "p3_firma"} and entrada.actor != "Abogado Titular":
                raise PermissionError("La revisión de fondo y firma requieren al Abogado Titular.")
            if entrada.decision not in DECISIONES_POR_NODO.get(nodo, ()):
                raise ValueError("La decisión no está permitida para este nodo BPMN.")
            if nodo == "p3_firma" and not entrada.kardex_notarial:
                raise ValueError("La firma requiere registrar el kárdex de la Notaría Bracamonte.")

            respuesta = {
                "actor": entrada.actor,
                "autenticado": True,
                "decision": entrada.decision,
                "observacion": entrada.observacion,
            }
            estado: EstadoAgente | None = None
            hilo = self._hilos.get(expediente_id)
            if hilo is not None and hilo[0] == _proceso_de_nodo(nodo):
                actualizacion: Mapping[str, object] | None = None
                if entrada.kardex_notarial:
                    actualizacion = {"kardex_notarial_entrada": entrada.kardex_notarial}
                estado = self.orquestador.reanudar(
                    hilo[0],
                    thread_id=hilo[1],
                    respuesta_humana=respuesta,
                    actualizacion_estado=actualizacion,
                )
                self._estados[expediente_id] = estado
            else:
                resuelta = self.orquestador.base_datos.resolver_tarea_hitl(
                    tarea_id,
                    actor=entrada.actor,
                    respuesta=respuesta,
                )
                if not resuelta:
                    raise RuntimeError("La tarea HITL ya fue resuelta.")
                self.orquestador.base_datos.registrar_traza(
                    trace_id=f"web-{uuid.uuid4()}",
                    expediente_id=expediente_id,
                    version_documental=int(str(tarea["version_documental"])),
                    proceso_actual=_proceso_de_nodo(nodo),
                    nodo_bpmn_id=nodo,
                    actor=entrada.actor,
                    resultado="hitl_resuelto",
                    metadatos={"decision": entrada.decision},
                )

            self._calibrar_resolucion(
                expediente_id=expediente_id,
                nodo_bpmn_id=nodo,
                decision=entrada.decision,
            )
            if nodo == "p2_legal":
                if entrada.decision == "viable" and estado is not None:
                    self._iniciar_p3(expediente_id, estado)
                elif entrada.decision == "subsanar":
                    self._fases[expediente_id] = "p2_subsanacion"
                else:
                    self._fases[expediente_id] = "p2_fin_descartada"
            elif nodo == "p2_subsanacion":
                self._fases[expediente_id] = "p2_esperar"
            elif nodo == "p2_archivar":
                self._fases[expediente_id] = "p2_fin_abandono"
            elif nodo == "p3_revision":
                if entrada.decision == "aprobar":
                    borrador = str(self._estados[expediente_id].get("borrador_minuta", ""))
                    cliente = self._asegurar_cliente(expediente_id)
                    self.orquestador.a3.enviar_borrador(
                        expediente_id, cliente["cliente_id"], borrador
                    )
                    self._fases[expediente_id] = "p3_esperar"
                else:
                    self._fases[expediente_id] = "p3_revision"
            elif nodo == "p3_firma":
                self._fases[expediente_id] = "p3_fin"
            elif nodo == "p1_incidente":
                self._fases[expediente_id] = "p1_fin_incidente"
            elif nodo == "p2_revision_fallo":
                self._fases[expediente_id] = "p2_legal"
            return {
                "mensaje": f"{nodo} resuelto por {entrada.actor}. Decisión auditada: {entrada.decision}.",
                "expediente_id": expediente_id,
                "kardex_notarial": estado.get("kardex_notarial") if estado else None,
            }

    def consultar_rag(self, expediente_id: str, consulta: str) -> dict[str, object]:
        with self._bloqueo:
            if expediente_id not in self._fases:
                raise ValueError("El expediente no está disponible para consulta RAG.")
            version = self._version_actual(expediente_id)
            respuesta = self.orquestador.a3.consultar_expediente(
                expediente_id=expediente_id,
                version_documental=version,
                consulta=consulta,
            )
            return respuesta.model_dump(mode="json")

    def minuta_markdown(self, expediente_id: str) -> str:
        if expediente_id not in {"EXP-2026-001", "EXP-2026-002", "EXP-2026-003"}:
            raise ValueError("No hay minuta para este expediente.")
        estado: EstadoAgente | None = self._estados.get(expediente_id)
        borrador = estado.get("borrador_minuta") if estado else None
        if isinstance(borrador, str) and borrador:
            return borrador
        datos = DatosMinuta.model_validate(datos_minuta_para(expediente_id))
        return ensamblar_minuta(datos).contenido_markdown

    def minuta_docx(self, expediente_id: str) -> bytes:
        if expediente_id not in {"EXP-2026-001", "EXP-2026-002", "EXP-2026-003"}:
            raise ValueError("No hay minuta para este expediente.")
        datos = DatosMinuta.model_validate(datos_minuta_para(expediente_id))
        documento = ensamblar_minuta(datos, formato="docx")
        if documento.contenido_docx is None:
            raise RuntimeError("No se pudo generar el Word de la minuta.")
        return documento.contenido_docx


def _error_http(error: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(error))


def crear_app(ruta_base: str | Path | None = None) -> FastAPI:
    """Crea una app aislable en pruebas y lista para ``uvicorn``."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        servicio = ServicioDemo(ruta_base)
        app.state.servicio_demo = servicio
        try:
            yield
        finally:
            servicio.close()

    app = FastAPI(
        title="Ságale Operaciones",
        version="0.3.0",
        description="Demostración gobernada P01, P02 y P03.",
        lifespan=lifespan,
    )

    @app.exception_handler(RequestValidationError)
    async def error_validacion(_request: Request, error: RequestValidationError) -> JSONResponse:
        # No reflejar entradas, contexto ni nombres de campos arbitrarios: pueden
        # contener PII, Unicode inválido o datos enormes suministrados por el cliente.
        campos = set(MensajeEntrada.model_fields) | set(ResolucionHITLEntrada.model_fields)
        detalles = [
            {
                "loc": ["body", next((parte for parte in item["loc"] if parte in campos), "datos")],
                "msg": "Valor inválido: revise el tipo, contenido y longitud del campo.",
                "type": "validation_error",
            }
            for item in error.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": detalles})

    def servicio(request: Request) -> ServicioDemo:
        return cast(ServicioDemo, request.app.state.servicio_demo)

    @app.get("/", response_class=HTMLResponse)
    def inicio() -> str:
        return PAGINA_HTML

    @app.get("/catalogo")
    def catalogo() -> FileResponse:
        archivo = Path(__file__).resolve().parents[1] / "docs" / "catalogo_tutorial.html"
        if not archivo.exists():
            raise HTTPException(status_code=404, detail="El catálogo tutorial no está disponible.")
        return FileResponse(archivo, media_type="text/html; charset=utf-8")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"estado": "ok"}

    @app.get("/api/dashboard")
    def dashboard(request: Request, expediente_id: str = "EXP-2026-001") -> dict[str, object]:
        try:
            return servicio(request).tablero(expediente_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/whatsapp")
    def whatsapp(request: Request, entrada: MensajeEntrada) -> dict[str, object]:
        try:
            return servicio(request).mensaje_libre(entrada)
        except (RuntimeError, ValueError) as error:
            raise _error_http(error) from error

    @app.post("/api/demo/solicitud")
    def demo_solicitud(request: Request) -> dict[str, object]:
        try:
            return servicio(request).enviar_solicitud()
        except (RuntimeError, ValueError) as error:
            raise _error_http(error) from error

    @app.post("/api/demo/idempotencia")
    def demo_idempotencia(request: Request) -> dict[str, object]:
        try:
            return servicio(request).reenviar_solicitud()
        except (RuntimeError, ValueError) as error:
            raise _error_http(error) from error

    @app.post("/api/demo/subsanacion")
    def demo_subsanacion(request: Request) -> dict[str, object]:
        try:
            return servicio(request).enviar_documentos_corregidos()
        except (RuntimeError, ValueError) as error:
            raise _error_http(error) from error

    @app.post("/api/demo/conforme")
    def demo_conforme(request: Request) -> dict[str, object]:
        try:
            return servicio(request).confirmar_borrador()
        except (RuntimeError, ValueError) as error:
            raise _error_http(error) from error

    @app.post("/api/hitl/{tarea_id}")
    def hitl(request: Request, tarea_id: str, entrada: ResolucionHITLEntrada) -> dict[str, object]:
        try:
            return servicio(request).resolver_hitl(tarea_id, entrada)
        except (RuntimeError, ValueError, PermissionError) as error:
            raise _error_http(error) from error

    @app.post("/api/rag")
    def rag(request: Request, entrada: MensajeEntrada) -> dict[str, object]:
        try:
            return servicio(request).consultar_rag(entrada.expediente_id, entrada.contenido)
        except ValueError as error:
            raise _error_http(error) from error

    @app.get("/api/minuta/{expediente_id}/markdown")
    def descargar_markdown(request: Request, expediente_id: str) -> Response:
        try:
            contenido = servicio(request).minuta_markdown(expediente_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return Response(
            content=contenido,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{expediente_id}-minuta.md"'},
        )

    @app.get("/api/minuta/{expediente_id}/word")
    def descargar_word(request: Request, expediente_id: str) -> Response:
        try:
            contenido = servicio(request).minuta_docx(expediente_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return Response(
            content=contenido,
            media_type=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            headers={"Content-Disposition": f'attachment; filename="{expediente_id}-minuta.docx"'},
        )

    return app


app = crear_app()


def main() -> None:
    import uvicorn

    uvicorn.run("web.app:app", host="127.0.0.1", port=8000, reload=False)


PAGINA_HTML = r"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>Ságale Operaciones · Demostración BPMN</title>
  <style>
    :root {
      --navy: #1e3a5f;
      --blue: #2563eb;
      --green: #167a42;
      --red: #b42318;
      --ink: #172033;
      --muted-ink: #526174;
      --canvas: #f4f7fb;
      --card: #ffffff;
      --soft: #e9eef5;
      --line: #c8d3e2;
      --ring: #1e3a5f;
      --shadow: 0 12px 30px rgba(23, 32, 51, .08);
      --radius: 14px;
      font-family: Lato, "Segoe UI", Arial, sans-serif;
    }
    * { box-sizing: border-box; }
    body { margin: 0; color: var(--ink); background: var(--canvas); }
    button, input, textarea, select { font: inherit; }
    button, a { -webkit-tap-highlight-color: transparent; touch-action: manipulation; }
    a { color: var(--blue); }
    .topbar {
      display: flex; align-items: center; justify-content: space-between; gap: 18px;
      min-height: 72px; padding: 14px clamp(18px, 3vw, 44px); background: var(--navy);
      color: #fff; border-bottom: 3px solid #167a42;
    }
    .brand { display: flex; align-items: baseline; gap: 12px; }
    .brand strong { font-family: Georgia, "Times New Roman", serif; font-size: 1.45rem; letter-spacing: .01em; }
    .brand span { color: #dce8f7; font-size: .86rem; }
    .topbar a { color: #fff; font-weight: 700; text-underline-offset: 3px; }
    .skip-link { position: absolute; z-index: 10; top: -60px; left: 14px; padding: 10px 13px; border-radius: 0 0 8px 8px; color: #fff; background: var(--navy); font-weight: 700; }
    .skip-link:focus { top: 0; }
    main { max-width: 1800px; margin: 0 auto; padding: 22px clamp(16px, 2vw, 36px) 34px; }
    .intro { display: flex; align-items: end; justify-content: space-between; gap: 18px; margin: 0 0 18px; }
    h1, h2, h3, p { margin-top: 0; }
    h1 { font: 700 clamp(1.65rem, 2.6vw, 2.5rem)/1.05 Georgia, "Times New Roman", serif; margin-bottom: 8px; }
    h2 { font-size: .95rem; letter-spacing: .035em; text-transform: uppercase; margin-bottom: 12px; }
    h3 { font-size: .85rem; margin: 0 0 7px; }
    .intro p { color: var(--muted-ink); max-width: 850px; margin-bottom: 0; }
    .environment { display: flex; align-items: center; gap: 8px; white-space: nowrap; color: var(--green); font-size: .86rem; font-weight: 700; }
    .environment::before { content: ""; width: 9px; height: 9px; border-radius: 99px; background: currentColor; }
    .layout { display: grid; grid-template-columns: minmax(260px, .92fr) minmax(460px, 1.65fr) minmax(290px, 1fr); gap: 18px; align-items: start; }
    .panel { min-width: 0; background: var(--card); border: 1px solid var(--line); box-shadow: var(--shadow); border-radius: var(--radius); overflow: hidden; }
    .panel-head { padding: 16px 18px 13px; border-bottom: 1px solid var(--line); background: linear-gradient(180deg, rgba(233,238,245,.7), transparent); }
    .panel-head p { margin: 0; color: var(--muted-ink); font-size: .83rem; line-height: 1.4; }
    .panel-body { padding: 16px 18px 18px; }
    label { display: block; margin: 0 0 6px; color: var(--muted-ink); font-size: .78rem; font-weight: 700; }
    textarea, input, select {
      width: 100%; border: 1px solid var(--line); border-radius: 9px; color: var(--ink); background: var(--card); padding: 10px 11px;
    }
    input, select { min-height: 44px; }
    textarea { min-height: 88px; resize: vertical; line-height: 1.42; }
    textarea:focus, input:focus, select:focus, button:focus-visible, a:focus-visible { outline: 3px solid color-mix(in srgb, var(--ring) 45%, transparent); outline-offset: 2px; }
    .actions { display: grid; gap: 8px; margin-top: 11px; }
    button { min-height: 44px; border: 1px solid transparent; border-radius: 9px; padding: 9px 11px; cursor: pointer; font-size: .84rem; font-weight: 700; line-height: 1.25; transition: background-color .2s ease, border-color .2s ease, transform .2s ease; }
    button:hover { transform: translateY(-1px); }
    button:active { transform: none; }
    button:disabled { cursor: not-allowed; opacity: .55; transform: none; }
    .primary { color: #fff; background: var(--navy); }
    .primary:hover { background: #142d4d; }
    .secondary { color: var(--navy); background: #edf4ff; border-color: #afc7ef; }
    .secondary:hover { background: #e0ecff; }
    .accent { color: #fff; background: var(--green); }
    .accent:hover { background: #0d6030; }
    .danger { color: #fff; background: var(--red); }
    .muted { color: var(--muted-ink); background: var(--soft); border-color: var(--line); }
    .conversation { display: grid; gap: 9px; max-height: 320px; margin-top: 18px; overflow-y: auto; padding: 3px; }
    .bubble { max-width: 92%; padding: 9px 11px; border-radius: 11px; border: 1px solid var(--line); font-size: .83rem; line-height: 1.4; white-space: pre-wrap; }
    .bubble.in { justify-self: end; color: #13355a; background: #e7f2ff; border-bottom-right-radius: 3px; }
    .bubble.out { justify-self: start; background: #f7fafc; border-bottom-left-radius: 3px; }
    .bubble small { display: block; margin-bottom: 4px; color: var(--muted-ink); font-size: .7rem; font-weight: 700; }
    .metrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px; margin-bottom: 14px; }
    .metric { min-height: 84px; padding: 12px; border: 1px solid var(--line); border-radius: 10px; background: linear-gradient(145deg, var(--card), #f5f8fc); }
    .metric strong { display: block; font: 700 1.4rem/1 Georgia, "Times New Roman", serif; color: var(--navy); }
    .metric span { display: block; margin-top: 6px; color: var(--muted-ink); font-size: .72rem; line-height: 1.18; }
    .case-head { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; align-items: end; margin: 16px 0 12px; }
    .tag { display: inline-flex; align-items: center; min-height: 27px; padding: 4px 8px; border: 1px solid #a9c5ee; border-radius: 99px; color: #17416e; background: #edf5ff; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .76rem; font-weight: 700; }
    .process { display: flex; gap: 7px; overflow-x: auto; padding: 3px 0 8px; scrollbar-width: thin; }
    .node { position: relative; flex: 0 0 auto; padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; color: var(--muted-ink); background: #f8fafc; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .74rem; }
    .node.active { color: #fff; border-color: var(--navy); background: var(--navy); }
    .node.done { color: #115c34; border-color: #9cd3b1; background: #e8f7ed; }
    .split { display: grid; grid-template-columns: minmax(0, 1fr) minmax(220px, .8fr); gap: 13px; margin-top: 14px; }
    .subcard { border: 1px solid var(--line); border-radius: 10px; background: #fbfcfe; overflow: hidden; }
    .subcard > header { padding: 10px 12px; border-bottom: 1px solid var(--line); background: #f0f4f9; }
    .subcard > header h3 { margin: 0; }
    pre { margin: 0; padding: 12px; overflow: auto; color: #153454; background: #f8fbff; font: .74rem/1.45 ui-monospace, SFMono-Regular, Consolas, monospace; white-space: pre-wrap; word-break: break-word; }
    .trace-list { display: grid; gap: 0; max-height: 235px; overflow-y: auto; }
    .trace { display: grid; grid-template-columns: 1fr auto; gap: 8px; padding: 8px 11px; border-bottom: 1px solid var(--line); font-size: .76rem; }
    .trace code { color: var(--navy); font-weight: 700; }
    .trace span { color: var(--muted-ink); }
    .rag { margin-top: 14px; }
    .rag form { display: grid; grid-template-columns: 1fr auto; gap: 8px; }
    .rag-result { min-height: 75px; margin-top: 9px; padding: 10px; border: 1px solid var(--line); border-radius: 9px; background: #f8fbff; color: var(--ink); font-size: .82rem; line-height: 1.43; }
    .citations { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; }
    .citation { color: #17416e; background: #e7f2ff; border: 1px solid #b9d0ef; border-radius: 5px; padding: 3px 5px; font: .7rem ui-monospace, SFMono-Regular, Consolas, monospace; }
    .hitl-list { display: grid; gap: 11px; }
    .task { padding: 12px; border: 1px solid var(--line); border-left: 4px solid var(--navy); border-radius: 9px; background: #fbfcfe; }
    .task h3 { display: flex; justify-content: space-between; gap: 6px; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; color: var(--navy); }
    .task p { margin: 0 0 9px; color: var(--muted-ink); font-size: .77rem; line-height: 1.35; }
    .task form { display: grid; gap: 7px; }
    .task textarea { min-height: 55px; }
    .task form button { min-height: 39px; }
    .empty { padding: 14px; border: 1px dashed var(--line); border-radius: 9px; color: var(--muted-ink); background: #fbfcfe; font-size: .83rem; }
    .minuta { margin-top: 18px; border-top: 1px solid var(--line); padding-top: 16px; }
    .download { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .download a { display: flex; align-items: center; justify-content: center; min-height: 44px; border: 1px solid #afc7ef; border-radius: 9px; padding: 9px 11px; text-align: center; text-decoration: none; font-size: .84rem; font-weight: 700; }
    .download a[aria-disabled="true"] { pointer-events: none; cursor: not-allowed; opacity: .55; }
    .preview { max-height: 220px; margin-top: 10px; overflow: auto; border: 1px solid var(--line); border-radius: 9px; background: #fbfcfe; padding: 10px; color: #344154; font: .72rem/1.42 ui-monospace, SFMono-Regular, Consolas, monospace; white-space: pre-wrap; }
    #notice { min-height: 22px; margin: 10px 0 0; color: var(--muted-ink); font-size: .82rem; }
    #notice.error { color: var(--red); font-weight: 700; }
    #notice.ok { color: var(--green); font-weight: 700; }
    @media (max-width: 1220px) { .layout { grid-template-columns: minmax(270px, .9fr) minmax(440px, 1.45fr); } .right-panel { grid-column: 1 / -1; } .hitl-list { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
    @media (max-width: 800px) { .topbar, .intro { align-items: flex-start; flex-direction: column; } .layout { grid-template-columns: 1fr; } .right-panel { grid-column: auto; } .split { grid-template-columns: 1fr; } .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } .hitl-list { grid-template-columns: 1fr; } .rag form { grid-template-columns: 1fr; } }
    @media (max-width: 430px) { main { padding: 16px 12px 28px; } .panel-body, .panel-head { padding-left: 13px; padding-right: 13px; } .metrics { grid-template-columns: 1fr 1fr; } .download { grid-template-columns: 1fr; } }
    @media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; } }
    /* Tema editorial claro: superficies neutras y acento reservado a acciones. */
    :root { --navy:#263b34; --blue:#315d4d; --green:#26704e; --red:#a33b35; --ink:#252b28; --muted-ink:#626c65; --canvas:#f5f6f3; --card:#fff; --soft:#f0f2ee; --line:#dfe4dd; --ring:#315d4d; --shadow:0 4px 18px rgba(28,40,30,.035); --radius:16px; font-family:"Segoe UI",Arial,sans-serif; color-scheme:light; }
    body { line-height:1.55; }
    .topbar { background:#fff; color:var(--ink); border-bottom:1px solid var(--line); padding:20px 32px; }
    .brand { align-items:center; }
    .brand strong { font-size:1.6rem; }
    .brand span,.topbar a { color:var(--muted-ink); font-size:.8rem; }
    .topbar a { text-decoration:none; border:1px solid var(--line); padding:10px 16px; border-radius:8px; }
    main { max-width:1800px; padding:32px; }
    .intro { margin-bottom:24px; }
    h1 { font-size:clamp(1.7rem,2.4vw,2.4rem); font-weight:500; line-height:1.2; }
    h2 { text-transform:none; letter-spacing:0; font-size:1.05rem; margin-bottom:6px; }
    .intro p { font-size:.9rem; }
    .environment { background:#eaf1eb; padding:7px 12px; border-radius:99px; font-size:.75rem; }
    .layout { grid-template-columns:minmax(0,1fr) minmax(0,1.5fr) minmax(0,1fr); gap:24px; }
    .panel-head { background:#fff; padding:24px; }
    .panel-body { padding:24px; }
    .primary,.accent { background:#315d4d; color:#fff; }
    .primary:hover,.accent:hover { background:#244a3b; }
    .secondary,.muted { background:#f6f7f4; color:var(--ink); border-color:var(--line); }
    .secondary:hover { background:#eaf0e8; }
    .download a { border-color:var(--line); }
    .task form button { margin-top:6px; }
    .actions { gap:10px; margin-top:20px; }
    .actions button { text-align:left; padding:13px; }
    .conversation { margin-top:24px; padding:12px; background:#f5f6f2; border-radius:12px; }
    .bubble,.citation,.preview { overflow-wrap:anywhere; min-width:0; }
    .citation { max-width:100%; }
    .bubble.in { color:#254937; background:#e9f0e6; border-color:#d7e2d2; }
    .bubble.out { background:#fff; }
    .metrics { gap:12px; margin-bottom:24px; }
    .metric { background:#f8f9f6; border:0; padding:16px; min-height:94px; }
    .metric strong { font-family:"Segoe UI",sans-serif; font-weight:600; font-size:1.6rem; }
    .metric span { line-height:1.4; }
    .case-head { grid-template-columns:1fr; gap:12px; }
    .tag,.citation { background:#edf2eb; color:#31513d; border-color:#d7e2d2; font-family:inherit; }
    .tag { justify-self:start; }
    .process { flex-wrap:wrap; gap:8px; margin:16px 0; }
    .node { font-family:inherit; background:#f6f7f4; font-size:.75rem; }
    .node.active { color:#fff; background:#315d4d; border-color:#315d4d; }
    .node.done { color:#315d4d; background:#edf3e9; border-color:#d7e2d2; }
    .split { grid-template-columns:1fr; gap:20px; }
    .subcard,.task,.empty { background:#fff; }
    .subcard > header { background:#f8f9f6; padding:14px 16px; }
    .trace code,.task h3 { font-family:inherit; }
    .trace { padding:12px 16px; align-items:center; }
    .task { border-left:3px solid #b7cbb7; padding:16px; }
    .task h3 { flex-direction:column; font-size:.95rem; }
    .task h3 span:last-child { font-size:.72rem; color:var(--muted-ink); font-weight:400; }
    .task form { gap:10px; }
    .rag { margin-top:28px; }
    .rag-result { background:#f8f9f6; color:var(--ink); padding:16px; }
    .preview { background:#fafbf8; color:var(--ink); font-family:Georgia,serif; font-size:.85rem; line-height:1.65; padding:16px; }
    pre { background:#f6f7f4; color:var(--ink); }
    details { border-top:1px solid var(--line); margin-top:14px; }
    summary { cursor:pointer; color:var(--muted-ink); padding:12px 16px; font-size:.78rem; }
    summary:focus-visible { outline:3px solid var(--ring); outline-offset:2px; }
    .analysis-summary { padding:16px; }
    .analysis-summary p { font-size:.83rem; margin:10px 0 0; }
    .status { display:inline-block; border-radius:99px; padding:4px 10px; font-size:.75rem; font-weight:600; background:#edf3e9; color:#315d4d; }
    .status.warning { background:#fbf1df; color:#805c22; }
    .status.neutral { background:#f0f2ee; color:#626c65; }
    @media (max-width:1150px) { .layout { grid-template-columns:1fr 1.4fr; } .right-panel { grid-column:1 / -1; } }
    @media (min-width:1151px) { .right-panel { grid-column:auto; } .hitl-list { grid-template-columns:1fr; } }
    @media (max-width:800px) { main { padding:20px 14px; } .layout { grid-template-columns:minmax(0,1fr); gap:20px; } .right-panel { grid-column:auto; } .panel-head,.panel-body { padding:20px; } .brand { align-items:flex-start; flex-direction:column; gap:0; } .hitl-list { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <a class="skip-link" href="#main-content">Saltar al contenido principal</a>
  <header class="topbar">
    <div class="brand"><strong>Ságale Abogados</strong><span>Operaciones notariales gobernadas</span></div>
    <a href="/catalogo" target="_blank" rel="noopener">Abrir catálogo tutorial</a>
  </header>
  <main id="main-content" tabindex="-1">
    <section class="intro" aria-labelledby="page-title">
      <div><h1 id="page-title">Cada expediente, bajo control.</h1><p>Del primer mensaje al cierre notarial. Documentos, seguimiento y decisiones en un solo lugar.</p></div>
      <div class="environment">Demostración · datos de prueba</div>
    </section>
    <p id="notice" aria-live="polite"></p>
    <section class="layout" aria-label="Tablero de automatización inteligente">
      <section class="panel" aria-labelledby="whatsapp-title">
        <header class="panel-head"><h2 id="whatsapp-title">Canal cliente · WhatsApp</h2><p>Conversaciones y solicitudes del expediente.</p></header>
        <div class="panel-body">
          <form id="free-message-form">
            <label for="whatsapp-message">Mensaje libre</label>
            <textarea id="whatsapp-message" required minlength="1" maxlength="1800" placeholder="Escriba un mensaje para el expediente seleccionado"></textarea>
            <button class="primary" type="submit">Registrar mensaje</button>
          </form>
          <div class="actions" aria-label="Acciones de demostración">
            <button class="accent" type="button" data-demo="solicitud">1. Enviar solicitud de minuta</button>
            <button class="secondary" type="button" data-demo="idempotencia">2. Reenviar la misma solicitud</button>
            <button class="secondary" type="button" data-demo="subsanacion">3. Enviar documentos corregidos</button>
            <button class="secondary" type="button" data-demo="conforme">4. Confirmar conformidad del borrador</button>
          </div>
          <div id="conversation" class="conversation" aria-live="polite"></div>
        </div>
      </section>
      <section class="panel" aria-labelledby="monitor-title">
        <header class="panel-head"><h2 id="monitor-title">Seguimiento del expediente</h2><p>Registro, revisión legal y cierre notarial.</p></header>
        <div class="panel-body">
          <div id="metrics" class="metrics" aria-label="Métricas operativas"></div>
          <div class="case-head">
            <div><label for="case-select">Expediente seleccionado</label><select id="case-select"></select></div>
            <span id="phase-tag" class="tag">Cargando</span>
          </div>
          <div id="process" class="process" aria-label="Progreso BPMN"></div>
          <div class="split">
            <section class="subcard" aria-labelledby="parser-title"><header><h3 id="parser-title">Análisis documental</h3></header><div id="analysis-summary" class="analysis-summary"></div><details><summary>Ver detalles técnicos</summary><pre id="parser-json">{}</pre></details></section>
            <section class="subcard" aria-labelledby="trace-title"><header><h3 id="trace-title">Últimas trazas</h3></header><div id="traces" class="trace-list"></div></section>
          </div>
          <section class="rag" aria-labelledby="rag-title">
            <h2 id="rag-title">Consultar documentos</h2>
            <form id="rag-form"><input id="rag-question" type="text" required minlength="1" maxlength="1800" aria-label="Consulta RAG" placeholder="Ej.: ¿Qué evidencia respalda la viabilidad?" value="¿Qué evidencia respalda el expediente?"><button class="primary" type="submit">Consultar</button></form>
            <div id="rag-result" class="rag-result">Seleccione un expediente y formule una consulta. Las respuestas incluyen citas verificables.</div>
          </section>
        </div>
      </section>
      <section class="panel right-panel" aria-labelledby="hitl-title">
        <header class="panel-head"><h2 id="hitl-title">Revisión y aprobación</h2><p>Decisiones del equipo jurídico y cierre en Notaría Bracamonte.</p></header>
        <div class="panel-body">
          <div id="hitl-list" class="hitl-list" aria-live="polite"></div>
          <section class="minuta" aria-labelledby="minuta-title">
            <h2 id="minuta-title">Borrador de minuta</h2>
            <div class="download"><a id="download-md" class="secondary" href="#">Descargar Markdown</a><a id="download-docx" class="secondary" href="#">Descargar Word</a></div>
            <div id="minuta-preview" class="preview">Seleccione un expediente con minuta para verla.</div>
          </section>
        </div>
      </section>
    </section>
  </main>
  <script>
    const state = { selected: 'EXP-2026-001', dashboard: null };
    const labels = {
      p1_inicio:'Inicio del registro', p1_validar:'Validar solicitud', p1_valida:'Comprobar datos', p1_normalizar:'Preparar datos', p1_hash:'Identificar solicitud', p1_consultar:'Consultar registro', p1_existe:'Comprobar duplicados', p1_registrar:'Registrar solicitud', p1_201:'Solicitud creada', p1_200:'Solicitud ya registrada', p1_400:'Datos inválidos', p1_fallidos_datos:'Registrar datos incompletos', p1_fin_nueva:'Registro completo', p1_fin_duplicada:'Duplicado identificado', p1_fin_invalida:'Solicitud rechazada', p1_error_sheets:'Error de consulta', p1_error_sistema:'Error de registro', p1_reintento:'Reintentar consulta', p1_recuperado:'Comprobar recuperación', p1_alertar:'Notificar incidencia', p1_incidente:'Revisar incidencia', p1_fin_incidente:'Incidencia cerrada',
      p2_inicio:'Iniciar análisis', p2_documentos:'Recibir documentos', p2_version:'Actualizar documentos', p2_agente:'Analizar evidencia', p2_parser:'Validar análisis', p2_json:'Comprobar estructura', p2_suficiente:'Comprobar recaudos', p2_error_ia:'Error de análisis', p2_error_descarga:'Error de descarga', p2_fallo:'Registrar incidencia', p2_revision_fallo:'Resolver incidencia técnica', p2_legal:'Revisión legal', p2_subsanar:'Evaluar observaciones', p2_viable:'Evaluar viabilidad', p2_fin_viable:'Viabilidad aprobada', p2_fin_descartada:'Expediente descartado', p2_subsanacion:'Solicitar documentos', p2_esperar:'Esperar documentos', p2_respuesta:'Documentos recibidos', p2_plazo:'Plazo de 5 días vencido', p2_archivar:'Revisar archivo', p2_fin_abandono:'Archivado por abandono',
      p3_inicio:'Iniciar cierre', p3_borrador:'Preparar borrador', p3_revision:'Revisar minuta', p3_aprobacion:'Evaluar aprobación', p3_corregir:'Corregir minuta', p3_enviar:'Enviar al cliente', p3_esperar:'Esperar conformidad', p3_respuesta:'Respuesta recibida', p3_plazo:'Plazo de 72 horas vencido', p3_conforme:'Evaluar conformidad', p3_fin_sin_conformidad:'Archivado sin conformidad', p3_firma:'Firma de minuta', p3_notaria:'Registrar kárdex', p3_fin:'Cierre completado'
    };
    const nodeLabel = id => labels[id] || 'Actividad registrada';
    const processLabels = {P01:'Paso 1: Registro', P02:'Paso 2: Revisión Legal', P03:'Paso 3: Cierre notarial'};
    function renderAnalysis(parser) {
      const box = document.getElementById('analysis-summary'); box.replaceChildren();
      const badge = document.createElement('span');
      badge.className = 'status' + (parser.status === 'requiere_subsanación' ? ' warning' : parser.status === 'suficiente' ? '' : ' neutral');
      badge.textContent = parser.status === 'suficiente' ? 'Recaudos suficientes' : parser.status === 'requiere_subsanación' ? 'Requiere documentos adicionales' : 'Sin análisis disponible'; box.append(badge);
      ['hallazgos','riesgos','documentos_faltantes'].forEach(key => { (parser[key] || []).forEach(value => { const p = document.createElement('p'); p.textContent = (key === 'documentos_faltantes' ? 'Documento pendiente: ' : '') + value; box.append(p); }); });
    }
    const notice = document.getElementById('notice');
    const text = value => value == null ? '' : String(value);
    function setNotice(message, error = false) { notice.textContent = message; notice.className = message ? (error ? 'error' : 'ok') : ''; }
    async function api(path, options = {}) {
      const response = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(Array.isArray(body.detail) ? 'Revise los campos: ' + body.detail.map(item => item.loc.slice(1).join('.') + ' · ' + item.msg).join('; ') : body.detail || 'No fue posible completar la operación.');
      return body;
    }
    function metric(label, value) { const box = document.createElement('div'); box.className = 'metric'; const strong = document.createElement('strong'); strong.textContent = value; const span = document.createElement('span'); span.textContent = label; box.append(strong, span); return box; }
    function renderMetrics(metrics) {
      const container = document.getElementById('metrics'); container.replaceChildren(
        metric('Expedientes activos', metrics.expedientes_activos), metric('Solicitudes por corregir', metrics.cola_fallidos), metric('Revisiones pendientes', metrics.hitl_pendiente), metric('Documentos protegidos', metrics.documentos_pii_indexados), metric('Calibración Brier', metrics.brier), metric('Calibración ECE · n=' + metrics.muestras_calibracion, metrics.ece)
      );
    }
    function renderCases(data) {
      const select = document.getElementById('case-select'); const previous = state.selected; select.replaceChildren();
      data.expedientes.forEach(item => { const option = document.createElement('option'); option.value = item.expediente_id; option.textContent = item.expediente_id + ' · ' + item.titulo; option.selected = item.expediente_id === previous; select.append(option); });
    }
    function renderProcess(selected, traces) {
      document.getElementById('phase-tag').textContent = processLabels[selected.proceso] + ' · ' + nodeLabel(selected.fase) + ' · v' + selected.version_documental;
      const completed = new Set(traces.filter(trace => !['pendiente','referencia_demo'].includes(trace.resultado)).map(trace => trace.nodo)); const container = document.getElementById('process'); container.replaceChildren();
      selected.nodos_proceso.forEach(node => { const item = document.createElement('span'); item.className = 'node' + (node === selected.fase ? ' active' : completed.has(node) ? ' done' : ''); item.textContent = nodeLabel(node); item.title = node; if (node === selected.fase) item.setAttribute('aria-current', 'step'); container.append(item); });
      renderAnalysis(selected.parser);
      document.getElementById('parser-json').textContent = JSON.stringify(selected.parser, null, 2);
      const traceBox = document.getElementById('traces'); traceBox.replaceChildren();
      if (!traces.length) { const empty = document.createElement('div'); empty.className = 'empty'; empty.textContent = 'Sin trazas para este expediente.'; traceBox.append(empty); return; }
      traces.slice().reverse().forEach(trace => { const row = document.createElement('details'); const summary = document.createElement('summary'); summary.textContent = nodeLabel(trace.nodo); const detail = document.createElement('pre'); detail.textContent = JSON.stringify(trace, null, 2); row.append(summary, detail); traceBox.append(row); });
    }
    function renderConversation(messages) {
      const box = document.getElementById('conversation'); box.replaceChildren();
      if (!messages.length) { const empty = document.createElement('div'); empty.className = 'empty'; empty.textContent = 'Aún no hay mensajes para este expediente.'; box.append(empty); return; }
      messages.forEach(message => { const bubble = document.createElement('div'); bubble.className = 'bubble ' + (message.direccion === 'entrada' ? 'in' : 'out'); const meta = document.createElement('small'); meta.textContent = message.direccion === 'entrada' ? 'Cliente · ' + message.tipo : 'Estudio · ' + message.tipo; const content = document.createElement('div'); content.textContent = message.contenido; bubble.append(meta, content); box.append(bubble); });
      box.scrollTop = box.scrollHeight;
    }
    function renderTasks(tasks) {
      const list = document.getElementById('hitl-list'); list.replaceChildren();
      if (!tasks.length) { const empty = document.createElement('div'); empty.className = 'empty'; empty.textContent = 'No hay decisiones humanas pendientes. La bandeja conserva las rutas BPMN disponibles cuando se activa un control.'; list.append(empty); return; }
      tasks.forEach(task => {
        const article = document.createElement('article'); article.className = 'task'; const title = document.createElement('h3'); const code = document.createElement('span'); code.textContent = nodeLabel(task.nodo_bpmn_id); const version = document.createElement('span'); version.textContent = task.expediente_id + ' · v' + task.version_documental; title.append(code, version); const description = document.createElement('details'); const summary = document.createElement('summary'); summary.textContent = 'Ver detalles técnicos'; const detail = document.createElement('pre'); detail.textContent = JSON.stringify({nodo:task.nodo_bpmn_id, solicitud:task.solicitud}, null, 2); description.append(summary, detail);
        const form = document.createElement('form'); const actorLabel = document.createElement('label'); actorLabel.textContent = 'Responsable'; const actor = document.createElement('select'); ['Marcela Colonia', 'Abogado Titular'].forEach(name => { const option = document.createElement('option'); option.value = name; option.textContent = name; actor.append(option); }); const decisionLabel = document.createElement('label'); decisionLabel.textContent = 'Decisión permitida'; const decision = document.createElement('select'); task.decisiones.forEach(value => { const option = document.createElement('option'); option.value = value; option.textContent = value; decision.append(option); }); const observation = document.createElement('textarea'); observation.placeholder = 'Observación auditada (opcional)'; observation.setAttribute('aria-label', 'Observación para ' + task.nodo_bpmn_id); form.append(actorLabel, actor, decisionLabel, decision);
        observation.maxLength = 1600;
        actor.id = 'actor-' + task.tarea_id; actorLabel.htmlFor = actor.id;
        decision.id = 'decision-' + task.tarea_id; decisionLabel.htmlFor = decision.id;
        if (['p3_revision', 'p3_firma'].includes(task.nodo_bpmn_id)) { actor.value = 'Abogado Titular'; actor.querySelector('option').disabled = true; }
        if (task.requiere_kardex) { const kardexLabel = document.createElement('label'); kardexLabel.textContent = 'Kárdex · Notaría Bracamonte'; const kardex = document.createElement('input'); kardex.type = 'text'; kardex.required = true; kardex.minLength = 1; kardex.maxLength = 120; kardex.placeholder = 'Ej.: KB-2026-01842'; kardex.setAttribute('aria-label', 'Kárdex notarial'); form.append(kardexLabel, kardex); form.dataset.kardex = 'true'; }
        const submit = document.createElement('button'); submit.type = 'submit'; submit.className = 'primary'; submit.textContent = 'Resolver control HITL'; form.append(observation, submit); form.addEventListener('submit', async event => { event.preventDefault(); try { submit.disabled = true; const payload = { actor: actor.value, decision: decision.value, observacion: observation.value, kardex_notarial: form.dataset.kardex ? form.querySelector('input').value : null }; const result = await api('/api/hitl/' + encodeURIComponent(task.tarea_id), { method: 'POST', body: JSON.stringify(payload) }); state.selected = result.expediente_id || state.selected; setNotice(result.mensaje); await refresh(); } catch (error) { setNotice(error.message, true); } finally { submit.disabled = false; } }); article.append(title, description, form); list.append(article);
      });
    }
    function renderMinuta(selected) {
      const preview = document.getElementById('minuta-preview'); const md = document.getElementById('download-md'); const word = document.getElementById('download-docx'); const allowed = ['EXP-2026-001','EXP-2026-002','EXP-2026-003'].includes(selected.expediente_id); md.href = allowed ? '/api/minuta/' + encodeURIComponent(selected.expediente_id) + '/markdown' : '#'; word.href = allowed ? '/api/minuta/' + encodeURIComponent(selected.expediente_id) + '/word' : '#'; md.setAttribute('aria-disabled', String(!allowed)); word.setAttribute('aria-disabled', String(!allowed)); preview.textContent = allowed ? (selected.minuta_previa || 'Borrador aún no disponible.') + (selected.kardex_notarial ? '\n\nKárdex registrado: ' + selected.kardex_notarial : '') : 'El expediente seleccionado no genera minuta.';
    }
    function renderRag(result) { const box = document.getElementById('rag-result'); box.replaceChildren(); const answer = document.createElement('div'); answer.textContent = result.respuesta; box.append(answer); if (result.citas && result.citas.length) { const citations = document.createElement('div'); citations.className = 'citations'; result.citas.forEach(cite => { const chip = document.createElement('span'); chip.className = 'citation'; chip.textContent = cite; citations.append(chip); }); box.append(citations); } }
    async function refresh() { const data = await api('/api/dashboard?expediente_id=' + encodeURIComponent(state.selected)); state.dashboard = data; state.selected = data.seleccionado.expediente_id; renderMetrics(data.metricas); renderCases(data); renderProcess(data.seleccionado, data.seleccionado.trazas); renderConversation(data.mensajes); renderTasks(data.tareas); renderMinuta(data.seleccionado); }
    document.getElementById('case-select').addEventListener('change', async event => { state.selected = event.target.value; document.getElementById('rag-result').textContent = 'Consulte los documentos del expediente seleccionado.'; setNotice(''); try { await refresh(); } catch (error) { setNotice(error.message, true); } });
    document.getElementById('free-message-form').addEventListener('submit', async event => { event.preventDefault(); const field = document.getElementById('whatsapp-message'); if (!field.value.trim()) return; try { const result = await api('/api/whatsapp', { method: 'POST', body: JSON.stringify({ contenido: field.value, expediente_id: state.selected }) }); field.value = ''; setNotice(result.mensaje); await refresh(); } catch (error) { setNotice(error.message, true); } });
    document.querySelectorAll('[data-demo]').forEach(button => { button.addEventListener('click', async () => { const action = button.dataset.demo; try { button.disabled = true; const result = await api('/api/demo/' + action, { method: 'POST', body: '{}' }); state.selected = result.expediente_id || state.selected; setNotice(result.mensaje); await refresh(); } catch (error) { setNotice(error.message, true); } finally { button.disabled = false; } }); });
    document.getElementById('rag-form').addEventListener('submit', async event => { event.preventDefault(); try { const query = document.getElementById('rag-question').value; const result = await api('/api/rag', { method: 'POST', body: JSON.stringify({ contenido: query, expediente_id: state.selected }) }); renderRag(result); } catch (error) { setNotice(error.message, true); } });
    function validateField(field) {
      field.setCustomValidity('');
      const value = field.value.trim();
      if (field.required && !value) field.setCustomValidity('Complete este campo; no se admiten solo espacios.');
      else if (field.maxLength > 0 && field.value.length > field.maxLength) field.setCustomValidity('El máximo es de ' + field.maxLength + ' caracteres.');
      else if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/u.test(value)) field.setCustomValidity('El texto contiene caracteres de control no permitidos.');
      else if (value && !/[^\s\p{Cf}]/u.test(value)) field.setCustomValidity('Escriba texto visible.');
    }
    document.addEventListener('input', event => { if (event.target.matches('input, textarea')) validateField(event.target); });
    document.addEventListener('submit', event => {
      event.target.querySelectorAll('input, textarea').forEach(validateField);
      if (!event.target.reportValidity()) { event.preventDefault(); event.stopImmediatePropagation(); }
    }, true);
    refresh().catch(error => setNotice(error.message, true));
  </script>
</body>
</html>"""


__all__ = ["ServicioDemo", "app", "crear_app", "main"]
