"""Acceso privado y cuota local para las llamadas reales de la demo pública."""

from __future__ import annotations

import os
import secrets
import time
from collections import defaultdict

from fastapi import HTTPException


def autorizar_ia(authorization: str | None) -> None:
    secretos = [os.getenv("SAGALE_AI_ACCESS_TOKEN", ""), os.getenv("SAGALE_AI_TEST_TOKEN", "")]
    secretos = [secreto for secreto in secretos if len(secreto) >= 24]
    if not secretos:
        raise HTTPException(status_code=503, detail="Configure el acceso privado a IA en Vercel.")
    recibido = (authorization or "").removeprefix("Bearer ")
    if not recibido or not any(
        secrets.compare_digest(recibido.encode(), secreto.encode()) for secreto in secretos
    ):
        raise HTTPException(status_code=401, detail="La contraseña de acceso a IA no es válida.")
    if not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")):
        raise HTTPException(status_code=503, detail="Configure la clave Gemini en Vercel.")


class LimiteIA:
    """Contadores aislados por expediente; no es un límite de gasto de Google."""

    def __init__(self) -> None:
        self._intentos: dict[tuple[str, int], list[float]] = defaultdict(list)

    def reservar(self, expediente_id: str, version: int) -> None:
        ahora = time.monotonic()
        llave = (expediente_id, version)
        intentos = [instante for instante in self._intentos[llave] if ahora - instante < 3600]
        self._intentos[llave] = intentos
        if len(intentos) >= 10 or (intentos and ahora - intentos[-1] < 15):
            raise HTTPException(
                status_code=429,
                detail="Espere 15 segundos entre consultas; máximo 10 por hora y expediente.",
            )
        intentos.append(ahora)
