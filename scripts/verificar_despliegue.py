"""Prueba de humo pública, con datos sintéticos, del despliegue autorizado."""

import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = "https://proyecto-automa.vercel.app"


def llamar(ruta: str, datos: dict[str, object] | None = None) -> tuple[int, bytes]:
    solicitud = Request(
        BASE + ruta,
        data=json.dumps(datos).encode() if datos is not None else None,
        headers={"Content-Type": "application/json", "User-Agent": "ProyectoAutoma-smoke-test"},
    )
    try:
        with urlopen(solicitud, timeout=45) as respuesta:
            return respuesta.status, respuesta.read()
    except HTTPError as error:
        return error.code, error.read()


def main() -> None:
    for ruta in ("/", "/health", "/catalogo", "/api/dashboard", "/api/ia/estado"):
        codigo, cuerpo = llamar(ruta)
        if codigo != 200:
            raise SystemExit(f"Fallo en {ruta}: HTTP {codigo}; se omite el cuerpo por privacidad.")
        if ruta == "/health" and json.loads(cuerpo) != {"estado": "ok"}:
            raise SystemExit("La comprobación de disponibilidad no devolvió el estado esperado.")
        if ruta == "/api/ia/estado":
            print("Configuración IA: " + json.dumps(json.loads(cuerpo)))
        print(f"OK {ruta}")
    codigo, _ = llamar("/api/whatsapp", {"contenido": "   "})
    if codigo != 422:
        raise SystemExit(f"Fallo de validación remota: HTTP {codigo}.")
    print("OK rechazo de texto vacío")
    codigo, cuerpo = llamar("/api/rag", {"contenido": "partida", "expediente_id": "EXP-2026-001"})
    if codigo != 200 or not json.loads(cuerpo).get("citas"):
        raise SystemExit("La consulta documental remota no devolvió citas verificables.")
    print("OK consulta documental con citas")
    codigo, cuerpo = llamar("/api/minuta/EXP-2026-001/word")
    if codigo != 200 or not cuerpo.startswith(b"PK"):
        raise SystemExit("La descarga Word remota no es válida.")
    print("OK descarga Word")


if __name__ == "__main__":
    main()
