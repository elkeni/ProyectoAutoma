"""Descarga, versionado inmutable e índice RAG aislado por expediente."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from ingesta.compuerta_pii import DocumentoPII, evaluar_compuerta_pii
from nucleo.base_datos import BaseDatos

P2_ERROR_DESCARGA: Final = "P2_ERROR_DESCARGA"


class ErrorDescargaRecaudo(RuntimeError):
    codigo = P2_ERROR_DESCARGA

    def __init__(self, documento_id: str) -> None:
        super().__init__(f"No fue posible descargar el recaudo {documento_id!r}.")
        self.documento_id = documento_id


class RecaudoEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    documento_id: str = Field(min_length=1, max_length=256)
    fuente_id: str = Field(min_length=1, max_length=512)
    contenido: str | None = None
    finalidad: str = "calificación jurídica del expediente"
    base_tratamiento: str = "ejecución del servicio jurídico solicitado"
    actor_autorizado: bool = True
    pertenece_expediente: bool = True
    consentimiento_o_base_legal: bool = True


class FragmentoRAG(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expediente_id: str
    version_documental: int
    documento_id: str
    fuente_id: str
    hash_sha256: str
    ubicacion: str
    texto: str


class ResultadoIngesta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expediente_id: str
    version_documental: int
    aprobados: int
    bloqueados: int
    hashes: tuple[str, ...]
    fuentes: tuple[str, ...]
    referencias_indexadas: tuple[str, ...]
    nodo_bpmn_id: Literal["p2_documentos", "p2_version"]


class IndiceRAGAislado:
    """Índice lexical local; toda lectura exige expediente y versión."""

    def __init__(self) -> None:
        self._fragmentos: dict[tuple[str, int], list[FragmentoRAG]] = defaultdict(list)

    def indexar(self, fragmento: FragmentoRAG) -> None:
        llave = (fragmento.expediente_id, fragmento.version_documental)
        existentes = self._fragmentos[llave]
        identidad = (fragmento.documento_id, fragmento.hash_sha256, fragmento.ubicacion)
        if any(
            (item.documento_id, item.hash_sha256, item.ubicacion) == identidad
            for item in existentes
        ):
            return
        existentes.append(fragmento)

    def recuperar(
        self,
        *,
        expediente_id: str,
        version_documental: int,
        consulta: str,
        limite: int = 5,
    ) -> tuple[FragmentoRAG, ...]:
        if limite < 1:
            raise ValueError("limite debe ser positivo.")
        tokens = set(re.findall(r"\w+", consulta.casefold()))
        candidatos = self._fragmentos.get((expediente_id, version_documental), [])
        puntuados = sorted(
            candidatos,
            key=lambda item: len(tokens & set(re.findall(r"\w+", item.texto.casefold()))),
            reverse=True,
        )
        return tuple(puntuados[:limite])

    def cantidad(self, expediente_id: str, version_documental: int) -> int:
        return len(self._fragmentos.get((expediente_id, version_documental), ()))

    def contiene_cita(
        self,
        *,
        expediente_id: str,
        version_documental: int,
        documento_id: str,
        ubicacion: str,
    ) -> bool:
        return any(
            item.documento_id == documento_id and item.ubicacion == ubicacion
            for item in self._fragmentos.get((expediente_id, version_documental), ())
        )


Descargador = Callable[[str], str]


def _fragmentar(texto: str, tamano: int = 700) -> Iterable[tuple[str, str]]:
    limpio = " ".join(texto.split())
    if not limpio:
        return
    for inicio in range(0, len(limpio), tamano):
        indice = inicio // tamano + 1
        yield f"fragmento-{indice}", limpio[inicio : inicio + tamano]


class GestorRecaudos:
    def __init__(
        self,
        *,
        base_datos: BaseDatos,
        indice: IndiceRAGAislado | None = None,
        descargador: Descargador | None = None,
    ) -> None:
        self.base_datos = base_datos
        self.indice = indice or IndiceRAGAislado()
        self.descargador = descargador

    def _obtener_contenido(self, recaudo: RecaudoEntrada) -> str:
        if recaudo.contenido is not None:
            return recaudo.contenido
        if self.descargador is None:
            raise ErrorDescargaRecaudo(recaudo.documento_id)
        try:
            contenido = self.descargador(recaudo.fuente_id)
        except Exception as exc:
            raise ErrorDescargaRecaudo(recaudo.documento_id) from exc
        if not contenido:
            raise ErrorDescargaRecaudo(recaudo.documento_id)
        return contenido

    def p2_documentos(
        self,
        *,
        expediente_id: str,
        version_documental: int,
        recaudos: Iterable[RecaudoEntrada],
        nodo_bpmn_id: Literal["p2_documentos", "p2_version"] = "p2_documentos",
    ) -> ResultadoIngesta:
        hashes: list[str] = []
        fuentes: list[str] = []
        referencias: list[str] = []
        aprobados = 0
        bloqueados = 0
        for recaudo in recaudos:
            contenido = self._obtener_contenido(recaudo)
            decision = evaluar_compuerta_pii(
                DocumentoPII(
                    expediente_id=expediente_id,
                    documento_id=recaudo.documento_id,
                    fuente_id=recaudo.fuente_id,
                    contenido=contenido,
                    finalidad=recaudo.finalidad,
                    base_tratamiento=recaudo.base_tratamiento,
                    actor_autorizado=recaudo.actor_autorizado,
                    pertenece_expediente=recaudo.pertenece_expediente,
                    consentimiento_o_base_legal=recaudo.consentimiento_o_base_legal,
                )
            )
            hashes.append(decision.hash_original_sha256)
            fuentes.append(recaudo.fuente_id)
            if not decision.aprobado or decision.contenido_minimizado is None:
                bloqueados += 1
                continue
            aprobados += 1
            self.base_datos.registrar_version_recaudo(
                expediente_id=expediente_id,
                version_documental=version_documental,
                documento_id=recaudo.documento_id,
                fuente_id=recaudo.fuente_id,
                hash_sha256=decision.hash_original_sha256,
                clasificacion_acceso=decision.clasificacion_acceso,
                contenido_indexable=decision.contenido_minimizado,
            )
            for ubicacion, texto in _fragmentar(decision.contenido_minimizado):
                fragmento = FragmentoRAG(
                    expediente_id=expediente_id,
                    version_documental=version_documental,
                    documento_id=recaudo.documento_id,
                    fuente_id=recaudo.fuente_id,
                    hash_sha256=decision.hash_original_sha256,
                    ubicacion=ubicacion,
                    texto=texto,
                )
                self.indice.indexar(fragmento)
                referencias.append(f"{recaudo.documento_id}@v{version_documental}:{ubicacion}")
        return ResultadoIngesta(
            expediente_id=expediente_id,
            version_documental=version_documental,
            aprobados=aprobados,
            bloqueados=bloqueados,
            hashes=tuple(hashes),
            fuentes=tuple(fuentes),
            referencias_indexadas=tuple(referencias),
            nodo_bpmn_id=nodo_bpmn_id,
        )

    def p2_version(
        self,
        *,
        expediente_id: str,
        version_anterior: int,
        recaudos_corregidos: Iterable[RecaudoEntrada],
    ) -> ResultadoIngesta:
        nueva_version = version_anterior + 1
        if nueva_version < 2:
            raise ValueError("La versión corregida debe ser posterior a la inicial.")
        return self.p2_documentos(
            expediente_id=expediente_id,
            version_documental=nueva_version,
            recaudos=recaudos_corregidos,
            nodo_bpmn_id="p2_version",
        )


def hash_contenido(contenido: str) -> str:
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()


__all__ = [
    "P2_ERROR_DESCARGA",
    "ErrorDescargaRecaudo",
    "FragmentoRAG",
    "GestorRecaudos",
    "IndiceRAGAislado",
    "RecaudoEntrada",
    "ResultadoIngesta",
    "hash_contenido",
]
