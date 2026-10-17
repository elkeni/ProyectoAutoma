"""Sistema 1: compuerta pura, tipada, auditable y sin llamadas de red."""

from __future__ import annotations

from time import perf_counter_ns
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from decision.umbrales import UMBRALES_PREDETERMINADOS, UmbralesDecision

RutaDecision = Literal["automatico", "revision_humana", "escalar_incidente"]


class DecisionEntrada(BaseModel):
    """Señal probabilística ya calculada y evidencia determinista asociada."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    probabilidad: float = Field(ge=0.0, le=1.0)
    evidencia_suficiente: bool = True
    opcion: str = Field(default="continuar", min_length=1, max_length=128)
    expediente_id: str | None = Field(default=None, min_length=1, max_length=128)
    version_documental: int | None = Field(default=None, ge=1)


class ResultadoDecision(BaseModel):
    """Resultado estable de la compuerta; no equivale a dictamen jurídico."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: RutaDecision
    opcion: str
    probabilidad: float = Field(ge=0.0, le=1.0)
    confianza: float = Field(ge=0.0, le=1.0)
    requiere_hitl: bool
    razon: str
    version_umbrales: str
    tau_auto: float
    tau_rev: float
    duracion_ms: float = Field(ge=0.0)

    @property
    def ruta(self) -> RutaDecision:
        """Alias semántico para consumidores que llaman ``ruta`` al resultado."""

        return self.decision


def decide(
    entrada: DecisionEntrada | float | None = None,
    umbrales: UmbralesDecision = UMBRALES_PREDETERMINADOS,
    *,
    probabilidad: float | None = None,
    evidencia_suficiente: bool = True,
    opcion: str = "continuar",
) -> ResultadoDecision:
    """Aplica ``tau_auto``/``tau_rev`` en memoria, habitualmente en mucho menos de 50 ms.

    Una evidencia insuficiente nunca toma la ruta automática, incluso con una
    probabilidad alta. La ruta automática solo es una recomendación de Sistema 1;
    no reemplaza los siete controles humanos definidos por BPMN.
    """

    inicio = perf_counter_ns()
    if entrada is not None and probabilidad is not None:
        raise ValueError("Indique 'entrada' o 'probabilidad', no ambas.")
    if entrada is None:
        if probabilidad is None:
            raise ValueError("Se requiere una probabilidad o DecisionEntrada.")
        datos = DecisionEntrada(
            probabilidad=probabilidad,
            evidencia_suficiente=evidencia_suficiente,
            opcion=opcion,
        )
    elif isinstance(entrada, bool):
        raise TypeError("Una probabilidad no puede ser booleana.")
    elif isinstance(entrada, (float, int)):
        datos = DecisionEntrada(
            probabilidad=float(entrada),
            evidencia_suficiente=evidencia_suficiente,
            opcion=opcion,
        )
    else:
        datos = entrada

    if not datos.evidencia_suficiente:
        decision: RutaDecision = "escalar_incidente"
        requiere_hitl = True
        razon = "evidencia_insuficiente"
    elif datos.probabilidad >= umbrales.tau_auto:
        decision = "automatico"
        requiere_hitl = False
        razon = "probabilidad_mayor_o_igual_tau_auto"
    elif datos.probabilidad >= umbrales.tau_rev:
        decision = "revision_humana"
        requiere_hitl = True
        razon = "probabilidad_en_zona_revision"
    else:
        decision = "escalar_incidente"
        requiere_hitl = True
        razon = "probabilidad_menor_tau_rev"

    duracion_ms = (perf_counter_ns() - inicio) / 1_000_000
    return ResultadoDecision(
        decision=decision,
        opcion=datos.opcion,
        probabilidad=datos.probabilidad,
        confianza=datos.probabilidad,
        requiere_hitl=requiere_hitl,
        razon=razon,
        version_umbrales=umbrales.version,
        tau_auto=umbrales.tau_auto,
        tau_rev=umbrales.tau_rev,
        duracion_ms=duracion_ms,
    )


__all__ = ["DecisionEntrada", "ResultadoDecision", "RutaDecision", "decide"]
