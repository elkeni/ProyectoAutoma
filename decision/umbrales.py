"""Configuración versionada de las compuertas del Sistema 1."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UmbralesDecision(BaseModel):
    """Umbrales inmutables; ``tau_rev`` nunca puede superar ``tau_auto``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tau_auto: float = Field(default=0.85, ge=0.0, le=1.0)
    tau_rev: float = Field(default=0.60, ge=0.0, le=1.0)
    version: str = Field(default="1.0.0", min_length=1, max_length=64)

    @model_validator(mode="after")
    def validar_orden(self) -> UmbralesDecision:
        if self.tau_rev > self.tau_auto:
            raise ValueError("tau_rev debe ser menor o igual que tau_auto.")
        return self


TAU_AUTO = 0.85
TAU_REV = 0.60
UMBRALES_PREDETERMINADOS = UmbralesDecision(tau_auto=TAU_AUTO, tau_rev=TAU_REV)
Umbrales = UmbralesDecision

__all__ = ["TAU_AUTO", "TAU_REV", "UMBRALES_PREDETERMINADOS", "Umbrales", "UmbralesDecision"]
