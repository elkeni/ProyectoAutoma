"""Semillas deterministas y seguras para la demostración funcional."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from pathlib import Path
from tempfile import mkdtemp

from ingesta.gestor_recaudos import GestorRecaudos, IndiceRAGAislado, RecaudoEntrada
from nucleo.base_datos import BaseDatos
from rpa.idempotencia_p01 import RepositorioP01SQLite, procesar_solicitud_p01
from rpa.plantilla_minuta_p03 import DatosMinuta, generar_markdown

EXPEDIENTES_DEMO: tuple[str, ...] = (
    "EXP-2026-001",
    "EXP-2026-002",
    "EXP-2026-003",
    "EXP-2026-004",
)

CLIENTES_DEMO: dict[str, dict[str, str]] = {
    "EXP-2026-001": {
        "cliente_id": "CLI-2026-001",
        "nombre": "Cliente predio limpio",
        "contacto": "+51 [CONTACTO_PROTEGIDO]",
    },
    "EXP-2026-002": {
        "cliente_id": "CLI-2026-002",
        "nombre": "Cliente hipoteca levantable",
        "contacto": "+51 [CONTACTO_PROTEGIDO]",
    },
    "EXP-2026-003": {
        "cliente_id": "CLI-2026-003",
        "nombre": "Cliente subsanación",
        "contacto": "+51 [CONTACTO_PROTEGIDO]",
    },
    "EXP-2026-004": {
        "cliente_id": "CLI-2026-004",
        "nombre": "Solicitud inválida",
        "contacto": "+51 [CONTACTO_PROTEGIDO]",
    },
}

CONTRATOS_DEMO: dict[str, dict[str, object]] = {
    "EXP-2026-001": {
        "solicitante": "Cliente predio limpio",
        "contacto": "cliente001@example.com",
        "tipo_operacion": "compraventa",
        "dni_vendedor": "18124589",
        "dni_comprador": "42567891",
        "partida_registral": "11048592",
        "valor_comercial": "185000.00",
        "forma_pago": "transferencia bancaria",
        "adj_dni": "drive://demo/exp-001/dni-partes.pdf",
        "adj_partida": "drive://demo/exp-001/partida-11048592.pdf",
    },
    "EXP-2026-002": {
        "solicitante": "Cliente hipoteca levantable",
        "contacto": "cliente002@example.com",
        "tipo_operacion": "compraventa",
        "dni_vendedor": "18124580",
        "dni_comprador": "42567890",
        "partida_registral": "11098765",
        "valor_comercial": "260000.00",
        "forma_pago": "transferencia y crédito",
        "adj_dni": "drive://demo/exp-002/dni-partes.pdf",
        "adj_partida": "drive://demo/exp-002/partida-11098765.pdf",
    },
    "EXP-2026-003": {
        "solicitante": "Cliente subsanación",
        "contacto": "cliente003@example.com",
        "tipo_operacion": "compraventa",
        "dni_vendedor": "18124581",
        "dni_comprador": "42567892",
        "partida_registral": "4433221100",
        "valor_comercial": "148000.00",
        "forma_pago": "contado",
        "adj_dni": "drive://demo/exp-003/dni-partes.pdf",
        "adj_partida": "drive://demo/exp-003/acta-nacimiento-erronea.pdf",
    },
    "EXP-2026-004": {
        "solicitante": "Solicitud incompleta",
        "contacto": "contacto-invalido",
        "tipo_operacion": "compraventa",
        "dni_vendedor": "123",
        "dni_comprador": "42567893",
        "partida_registral": "?",
        "valor_comercial": "0",
        "forma_pago": "desconocido",
        "adj_dni": "",
        "adj_partida": "",
    },
}

RECAUDOS_DEMO: dict[str, dict[int, tuple[dict[str, object], ...]]] = {
    "EXP-2026-001": {
        1: (
            {
                "documento_id": "partida_sunarp_11048592.txt",
                "fuente_id": "drive://demo/exp-001/partida-11048592.pdf",
                "contenido": (
                    "Partida SUNARP 11048592. Predio urbano sin gravámenes, embargos ni "
                    "cargas vigentes. Titularidad verificada para la compraventa."
                ),
            },
            {
                "documento_id": "dni_vendedor.txt",
                "fuente_id": "drive://demo/exp-001/dni-vendedor.pdf",
                "contenido": "DNI de parte vendedora 18124589 validado para el expediente.",
            },
            {
                "documento_id": "dni_comprador.txt",
                "fuente_id": "drive://demo/exp-001/dni-comprador.pdf",
                "contenido": "DNI de parte compradora 42567891 validado para el expediente.",
            },
        )
    },
    "EXP-2026-002": {
        1: (
            {
                "documento_id": "partida_sunarp_11098765.txt",
                "fuente_id": "drive://demo/exp-002/partida-11098765.pdf",
                "contenido": (
                    "Partida SUNARP 11098765. Se detecta hipoteca bancaria vigente a favor de "
                    "Banco de Crédito. El levantamiento está condicionado a la cancelación "
                    "registral previa a la escritura pública."
                ),
            },
            {
                "documento_id": "constancia_levantamiento_banco.txt",
                "fuente_id": "drive://demo/exp-002/constancia-banco.pdf",
                "contenido": (
                    "Banco confirma trámite de levantamiento de hipoteca. La minuta debe incluir "
                    "cláusula de levantamiento y retención hasta la cancelación registral."
                ),
            },
            {
                "documento_id": "dni_partes.txt",
                "fuente_id": "drive://demo/exp-002/dni-partes.pdf",
                "contenido": "DNI de vendedores y compradores verificados bajo acceso restringido.",
            },
        )
    },
    "EXP-2026-003": {
        1: (
            {
                "documento_id": "acta_nacimiento_adjuntada_por_error.txt",
                "fuente_id": "drive://demo/exp-003/acta-nacimiento.pdf",
                "contenido": (
                    "Acta de Nacimiento adjuntada por error. No acredita la situación registral "
                    "del predio ni sustituye la Copia Literal de la Partida 4433221100."
                ),
            },
            {
                "documento_id": "dni_partes.txt",
                "fuente_id": "drive://demo/exp-003/dni-partes.pdf",
                "contenido": "Identificación de las partes recibida para el expediente.",
            },
        ),
        2: (
            {
                "documento_id": "copia_literal_partida_4433221100.txt",
                "fuente_id": "drive://demo/exp-003/copia-literal-corregida.pdf",
                "contenido": (
                    "Copia Literal de la Partida 4433221100. Predio identificado, titularidad "
                    "verificada y sin gravámenes que impidan la compraventa. Documento corregido "
                    "recibido por WhatsApp y asociado a la versión documental 2."
                ),
            },
        ),
    },
}

DATOS_MINUTA_DEMO: dict[str, dict[str, object]] = {
    "EXP-2026-001": {
        "expediente_id": "EXP-2026-001",
        "vendedor": "Parte vendedora identificada en recaudos",
        "comprador": "Parte compradora identificada en recaudos",
        "inmueble": "predio urbano inscrito en SUNARP",
        "partida_registral": "11048592",
        "valor_comercial": "S/ 185,000.00",
        "forma_pago": "transferencia bancaria contra firma",
        "referencias": (
            "partida_sunarp_11048592.txt@v1:fragmento-1",
            "dni_vendedor.txt@v1:fragmento-1",
        ),
    },
    "EXP-2026-002": {
        "expediente_id": "EXP-2026-002",
        "vendedor": "Parte vendedora identificada en recaudos",
        "comprador": "Parte compradora identificada en recaudos",
        "inmueble": "predio urbano sujeto a levantamiento bancario",
        "partida_registral": "11098765",
        "valor_comercial": "S/ 260,000.00",
        "forma_pago": "transferencia con retención de garantía",
        "obligaciones_adicionales": (
            "El desembolso final queda sujeto al levantamiento registral de la hipoteca bancaria.",
        ),
        "referencias": (
            "partida_sunarp_11098765.txt@v1:fragmento-1",
            "constancia_levantamiento_banco.txt@v1:fragmento-1",
        ),
    },
    "EXP-2026-003": {
        "expediente_id": "EXP-2026-003",
        "vendedor": "Parte vendedora identificada en recaudos",
        "comprador": "Parte compradora identificada en recaudos",
        "inmueble": "predio urbano subsanado",
        "partida_registral": "4433221100",
        "valor_comercial": "S/ 148,000.00",
        "forma_pago": "contado",
        "referencias": ("copia_literal_partida_4433221100.txt@v2:fragmento-1",),
    },
}

CALIFICACIONES_DEMO: dict[str, dict[str, object]] = {
    "EXP-2026-001": {
        "suficiente": True,
        "viable": True,
        "requiere_subsanacion": False,
        "probabilidad": 0.96,
        "observaciones": ["Predio limpio y recaudos completos."],
        "citas": [
            {
                "expediente_id": "EXP-2026-001",
                "version_documental": 1,
                "documento_id": "partida_sunarp_11048592.txt",
                "ubicacion": "fragmento-1",
            }
        ],
    },
    "EXP-2026-002": {
        "suficiente": True,
        "viable": True,
        "requiere_subsanacion": False,
        "probabilidad": 0.82,
        "observaciones": [
            "Gravamen hipotecario detectado; procede con cláusula de levantamiento registral."
        ],
        "citas": [
            {
                "expediente_id": "EXP-2026-002",
                "version_documental": 1,
                "documento_id": "partida_sunarp_11098765.txt",
                "ubicacion": "fragmento-1",
            }
        ],
    },
    "EXP-2026-003": {
        "suficiente": False,
        "viable": False,
        "requiere_subsanacion": True,
        "probabilidad": 0.31,
        "observaciones": [
            "Se adjuntó un Acta de Nacimiento en lugar de la Copia Literal requerida."
        ],
        "citas": [
            {
                "expediente_id": "EXP-2026-003",
                "version_documental": 1,
                "documento_id": "acta_nacimiento_adjuntada_por_error.txt",
                "ubicacion": "fragmento-1",
            }
        ],
    },
}

CALIFICACION_CORREGIDA_EXP_003: dict[str, object] = {
    "suficiente": True,
    "viable": True,
    "requiere_subsanacion": False,
    "probabilidad": 0.91,
    "observaciones": ["Copia Literal corregida recibida y validada en la versión 2."],
    "citas": [
        {
            "expediente_id": "EXP-2026-003",
            "version_documental": 2,
            "documento_id": "copia_literal_partida_4433221100.txt",
            "ubicacion": "fragmento-1",
        }
    ],
}


def ruta_base_demo() -> Path:
    """Ubicación local no compartida del SQLite de la demostración."""

    if os.environ.get("VERCEL") == "1":
        # El paquete desplegado es de solo lectura; cada instancia demo usa
        # almacenamiento temporal independiente de las bases locales.
        return Path(mkdtemp(prefix="sagale-demo-")) / "sagale_demo.sqlite3"
    return Path(__file__).resolve().parents[1] / "data" / "sagale_demo.sqlite3"


def cliente_para(expediente_id: str) -> dict[str, str]:
    """Devuelve una copia para que la interfaz no altere las semillas."""

    return dict(CLIENTES_DEMO[expediente_id])


def contrato_para(expediente_id: str) -> dict[str, object]:
    return dict(CONTRATOS_DEMO[expediente_id])


def recaudos_para(expediente_id: str, version_documental: int = 1) -> list[dict[str, object]]:
    return [dict(item) for item in RECAUDOS_DEMO[expediente_id][version_documental]]


def datos_minuta_para(expediente_id: str) -> dict[str, object]:
    return dict(DATOS_MINUTA_DEMO[expediente_id])


def calificacion_json(expediente_id: str, *, corregida: bool = False) -> str:
    datos = CALIFICACION_CORREGIDA_EXP_003 if corregida else CALIFICACIONES_DEMO[expediente_id]
    return json.dumps(datos, ensure_ascii=False)


def _mensaje_id(expediente_id: str, tipo: str, contenido: str) -> str:
    return hashlib.sha256(f"{expediente_id}|{tipo}|{contenido}".encode()).hexdigest()


def _registrar_mensaje(
    base: BaseDatos,
    *,
    expediente_id: str,
    direccion: str,
    tipo: str,
    contenido: str,
) -> None:
    cliente = CLIENTES_DEMO[expediente_id]
    base.registrar_mensaje_whatsapp(
        mensaje_id=_mensaje_id(expediente_id, tipo, contenido),
        cliente_id=cliente["cliente_id"],
        expediente_id=expediente_id,
        direccion=direccion,
        tipo=tipo,
        contenido=contenido,
    )


def _sembrar_ruta(
    base: BaseDatos,
    *,
    expediente_id: str,
    proceso: str,
    nodos: Sequence[str],
    version_documental: int = 1,
) -> None:
    existentes = {
        (str(item["proceso_actual"]), str(item["nodo_bpmn_id"]), int(item["version_documental"]))
        for item in base.listar_trazas(expediente_id)
    }
    trace_id = f"semilla-{expediente_id.lower()}-{proceso.lower()}-v{version_documental}"
    for nodo in nodos:
        identidad = (proceso, nodo, version_documental)
        if identidad in existentes:
            continue
        base.registrar_traza(
            trace_id=trace_id,
            expediente_id=expediente_id,
            version_documental=version_documental,
            proceso_actual=proceso,
            nodo_bpmn_id=nodo,
            actor="semilla_demo",
            resultado="completado",
            metadatos={"origen": "cargar_datos_demo"},
        )


def _rutas_demo(base: BaseDatos) -> None:
    _sembrar_ruta(
        base,
        expediente_id="EXP-2026-001",
        proceso="P01",
        nodos=(
            "p1_inicio",
            "p1_validar",
            "p1_valida",
            "p1_normalizar",
            "p1_hash",
            "p1_consultar",
            "p1_existe",
            "p1_registrar",
            "p1_201",
            "p1_fin_nueva",
        ),
    )
    _sembrar_ruta(
        base,
        expediente_id="EXP-2026-001",
        proceso="P02",
        nodos=(
            "p2_inicio",
            "p2_documentos",
            "p2_agente",
            "p2_parser",
            "p2_json",
            "p2_suficiente",
            "p2_legal",
            "p2_subsanar",
            "p2_viable",
            "p2_fin_viable",
        ),
    )
    _sembrar_ruta(
        base,
        expediente_id="EXP-2026-001",
        proceso="P03",
        nodos=(
            "p3_inicio",
            "p3_borrador",
            "p3_revision",
            "p3_aprobacion",
            "p3_enviar",
            "p3_esperar",
            "p3_respuesta",
            "p3_conforme",
            "p3_firma",
            "p3_notaria",
            "p3_fin",
        ),
    )
    _sembrar_ruta(
        base,
        expediente_id="EXP-2026-002",
        proceso="P01",
        nodos=("p1_inicio", "p1_validar", "p1_valida", "p1_201", "p1_fin_nueva"),
    )
    _sembrar_ruta(
        base,
        expediente_id="EXP-2026-002",
        proceso="P02",
        nodos=(
            "p2_inicio",
            "p2_documentos",
            "p2_agente",
            "p2_parser",
            "p2_json",
            "p2_suficiente",
            "p2_legal",
            "p2_subsanar",
            "p2_viable",
            "p2_fin_viable",
        ),
    )
    _sembrar_ruta(
        base,
        expediente_id="EXP-2026-002",
        proceso="P03",
        nodos=("p3_inicio", "p3_borrador", "p3_revision", "p3_aprobacion"),
    )
    _sembrar_ruta(
        base,
        expediente_id="EXP-2026-003",
        proceso="P02",
        nodos=(
            "p2_inicio",
            "p2_documentos",
            "p2_agente",
            "p2_parser",
            "p2_json",
            "p2_suficiente",
            "p2_subsanacion",
            "p2_esperar",
        ),
    )
    _sembrar_ruta(
        base,
        expediente_id="EXP-2026-004",
        proceso="P01",
        nodos=(
            "p1_inicio",
            "p1_validar",
            "p1_valida",
            "p1_fallidos_datos",
            "p1_400",
            "p1_fin_invalida",
        ),
    )


def cargar_datos_demo(ruta: str | Path | None = None) -> dict[str, int | str]:
    """Puebla SQLite y construye el índice RAG aislado para los cuatro expedientes.

    La función es idempotente respecto de solicitudes, mensajes, versiones y
    trazas. El índice es en memoria por diseño: la aplicación lo rehidrata solo
    desde los recaudos autorizados que permanecen en SQLite.
    """

    destino = Path(ruta) if ruta is not None else ruta_base_demo()
    destino.parent.mkdir(parents=True, exist_ok=True)
    indice = IndiceRAGAislado()
    with BaseDatos(destino) as base:
        repositorio = RepositorioP01SQLite(base.conexion)
        gestor = GestorRecaudos(base_datos=base, indice=indice)
        for expediente_id, cliente in CLIENTES_DEMO.items():
            base.upsert_cliente(
                cliente["cliente_id"],
                cliente["nombre"],
                cliente["contacto"],
                consentimiento_pii=True,
            )
            contenido_memoria = (
                "Caso demostrativo cargado; identidad y documentos bajo acceso restringido."
            )
            memorias = base.listar_memoria_cliente(cliente["cliente_id"], expediente_id)
            if not any(item["contenido"] == contenido_memoria for item in memorias):
                base.agregar_memoria_cliente(
                    cliente["cliente_id"],
                    expediente_id,
                    "estudio",
                    contenido_memoria,
                    {"origen": "cargar_datos_demo"},
                )

        for expediente_id in EXPEDIENTES_DEMO[:3]:
            if base.contar("Solicitudes", expediente_id) == 0:
                procesar_solicitud_p01(
                    expediente_id=expediente_id,
                    datos=CONTRATOS_DEMO[expediente_id],
                    repositorio=repositorio,
                )
        if base.contar("Cola_Fallidos", "EXP-2026-004") == 0:
            procesar_solicitud_p01(
                expediente_id="EXP-2026-004",
                datos=CONTRATOS_DEMO["EXP-2026-004"],
                repositorio=repositorio,
            )

        for expediente_id, versiones in RECAUDOS_DEMO.items():
            for version_documental, recaudos in versiones.items():
                if version_documental > 1:
                    continue
                gestor.p2_documentos(
                    expediente_id=expediente_id,
                    version_documental=version_documental,
                    recaudos=(RecaudoEntrada.model_validate(item) for item in recaudos),
                    nodo_bpmn_id="p2_version" if version_documental > 1 else "p2_documentos",
                )

        minuta_datos = DatosMinuta.model_validate(DATOS_MINUTA_DEMO["EXP-2026-001"])
        minuta_markdown, _ = generar_markdown(minuta_datos)
        gestor.p2_documentos(
            expediente_id="EXP-2026-001",
            version_documental=1,
            recaudos=(
                RecaudoEntrada(
                    documento_id="minuta_compraventa_exp_2026_001.md",
                    fuente_id="interno://p3/minuta-exp-2026-001.md",
                    contenido=minuta_markdown,
                ),
            ),
        )

        _registrar_mensaje(
            base,
            expediente_id="EXP-2026-001",
            direccion="salida",
            tipo="bienvenida",
            contenido="Bienvenido al Estudio. Recordamos su solicitud de minuta y los recaudos protegidos.",
        )
        _registrar_mensaje(
            base,
            expediente_id="EXP-2026-003",
            direccion="salida",
            tipo="subsanacion",
            contenido="Por favor envíe la Copia Literal de la Partida 4433221100; el Acta de Nacimiento no la sustituye.",
        )
        _rutas_demo(base)
        return {
            "ruta_base": str(destino),
            "expedientes": len(EXPEDIENTES_DEMO),
            "solicitudes": base.contar("Solicitudes"),
            "cola_fallidos": base.contar("Cola_Fallidos"),
            "documentos_indexados": base.contar("Versiones_Recaudos"),
            "fragmentos_rag_en_memoria": sum(
                indice.cantidad(expediente_id, version)
                for expediente_id, versiones in RECAUDOS_DEMO.items()
                for version in versiones
                if version == 1
            ),
        }


__all__ = [
    "CALIFICACION_CORREGIDA_EXP_003",
    "CALIFICACIONES_DEMO",
    "CLIENTES_DEMO",
    "CONTRATOS_DEMO",
    "DATOS_MINUTA_DEMO",
    "EXPEDIENTES_DEMO",
    "RECAUDOS_DEMO",
    "calificacion_json",
    "cargar_datos_demo",
    "cliente_para",
    "contrato_para",
    "datos_minuta_para",
    "recaudos_para",
    "ruta_base_demo",
]
