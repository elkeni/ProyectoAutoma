"""Métricas de calibración alimentadas exclusivamente por etiquetas humanas."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


def _validar_series(
    etiquetas: Sequence[bool | int], probabilidades: Sequence[float]
) -> tuple[list[int], list[float]]:
    if len(etiquetas) != len(probabilidades):
        raise ValueError("etiquetas y probabilidades deben tener la misma longitud.")
    if not etiquetas:
        raise ValueError("Se requiere al menos una observación para calibrar.")
    y: list[int] = []
    p: list[float] = []
    for etiqueta, probabilidad in zip(etiquetas, probabilidades, strict=True):
        if etiqueta not in (0, 1, False, True):
            raise ValueError("Cada etiqueta humana debe ser binaria (0/1).")
        valor = float(probabilidad)
        if not 0.0 <= valor <= 1.0:
            raise ValueError("Cada probabilidad debe estar entre 0 y 1.")
        y.append(int(etiqueta))
        p.append(valor)
    return y, p


def brier_score(etiquetas: Sequence[bool | int], probabilidades: Sequence[float]) -> float:
    """Calcula el error cuadrático medio de probabilidades binarias."""

    y, p = _validar_series(etiquetas, probabilidades)
    return sum((prob - etiqueta) ** 2 for etiqueta, prob in zip(y, p, strict=True)) / len(y)


def expected_calibration_error(
    etiquetas: Sequence[bool | int],
    probabilidades: Sequence[float],
    n_bins: int = 10,
) -> float:
    """Calcula ECE con intervalos uniformes, incluyendo 1.0 en el último bin."""

    if n_bins < 1:
        raise ValueError("n_bins debe ser mayor o igual que 1.")
    y, p = _validar_series(etiquetas, probabilidades)
    total = len(y)
    acumulado = 0.0
    for indice in range(n_bins):
        inferior = indice / n_bins
        superior = (indice + 1) / n_bins
        posiciones = [
            posicion
            for posicion, probabilidad in enumerate(p)
            if inferior <= probabilidad < superior or (indice == n_bins - 1 and probabilidad == 1.0)
        ]
        if not posiciones:
            continue
        confianza_media = sum(p[posicion] for posicion in posiciones) / len(posiciones)
        exactitud = sum(y[posicion] for posicion in posiciones) / len(posiciones)
        acumulado += len(posiciones) / total * abs(exactitud - confianza_media)
    return acumulado


calcular_brier_score = brier_score
calcular_ece = expected_calibration_error


class ObservacionCalibracion(BaseModel):
    """Etiqueta humana versionada; nunca contiene PII ni texto documental."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expediente_id: str = Field(min_length=1, max_length=128)
    version_documental: int = Field(ge=1)
    nodo_bpmn_id: str
    probabilidad: float = Field(ge=0.0, le=1.0)
    etiqueta_humana: bool
    version_umbrales: str = Field(min_length=1, max_length=64)
    timestamp_utc: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MetricasCalibracion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total: int = Field(ge=1)
    brier_score: float = Field(ge=0.0, le=1.0)
    ece: float = Field(ge=0.0, le=1.0)
    n_bins: int = Field(ge=1)


class Calibrador:
    """Acumula resoluciones humanas y recalcula Brier/ECE tras cada alta."""

    _NODOS_CALIBRABLES = frozenset({"p2_legal", "p2_subsanacion", "p3_revision"})

    def __init__(self, n_bins: int = 10) -> None:
        if n_bins < 1:
            raise ValueError("n_bins debe ser mayor o igual que 1.")
        self._n_bins = n_bins
        self._observaciones: list[ObservacionCalibracion] = []

    @property
    def observaciones(self) -> tuple[ObservacionCalibracion, ...]:
        return tuple(self._observaciones)

    def registrar(self, observacion: ObservacionCalibracion) -> MetricasCalibracion:
        if observacion.nodo_bpmn_id not in self._NODOS_CALIBRABLES:
            raise ValueError("La arquitectura solo calibra p2_legal, p2_subsanacion y p3_revision.")
        self._observaciones.append(observacion)
        return self.metricas()

    def metricas(self) -> MetricasCalibracion:
        if not self._observaciones:
            raise ValueError("Aún no existen etiquetas humanas para calibrar.")
        etiquetas = [item.etiqueta_humana for item in self._observaciones]
        probabilidades = [item.probabilidad for item in self._observaciones]
        return MetricasCalibracion(
            total=len(self._observaciones),
            brier_score=brier_score(etiquetas, probabilidades),
            ece=expected_calibration_error(etiquetas, probabilidades, self._n_bins),
            n_bins=self._n_bins,
        )


__all__ = [
    "Calibrador",
    "MetricasCalibracion",
    "ObservacionCalibracion",
    "brier_score",
    "calcular_brier_score",
    "calcular_ece",
    "expected_calibration_error",
]
