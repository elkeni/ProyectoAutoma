"""Prueba remota autorizada sin extraer la API key ni la contraseña del usuario."""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = "https://proyecto-automa.vercel.app"


def vercel(argumentos: list[str], valor: str | None = None) -> None:
    ejecutable = shutil.which("vercel.cmd") or shutil.which("vercel")
    if not ejecutable:
        raise SystemExit("No se encontró Vercel CLI.")
    resultado = subprocess.run(
        [ejecutable, *argumentos],
        input=valor,
        text=True,
        capture_output=True,
        timeout=300,
        check=False,
    )
    if resultado.returncode:
        # Nunca imprimir la salida de operaciones que reciben secretos.
        raise RuntimeError(
            "La operación Vercel no terminó correctamente; revise el panel del proyecto."
        )


def llamada(
    token: str, ruta: str, datos: dict[str, object] | None = None
) -> tuple[int, dict[str, object]]:
    peticion = Request(
        BASE + ruta,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "User-Agent": "ProyectoAutoma-smoke-test",
        },
        data=json.dumps(datos).encode() if datos is not None else None,
    )
    try:
        with urlopen(peticion, timeout=60) as r:
            return r.status, json.load(r)
    except HTTPError as error:
        cuerpo = json.load(error)
        tipo = error.headers.get("X-Sagale-IA-Tipo")
        if tipo:
            cuerpo["tipo_error"] = tipo
        return error.code, cuerpo


def main() -> None:
    token = secrets.token_urlsafe(32)
    print(
        "Creando acceso temporal de prueba; el valor no se imprime ni se guarda en archivos.",
        flush=True,
    )
    vercel(
        ["env", "add", "SAGALE_AI_TEST_TOKEN", "production", "--sensitive", "--force", "--yes"],
        token + "\n",
    )
    try:
        print("Desplegando integración Gemini con secretos del servidor…", flush=True)
        vercel(["deploy", "--prod", "--yes", "--name", "proyecto-automa"])
        codigo, estado = llamada(token, "/api/ia/estado?verificacion=" + secrets.token_hex(6))
        for _ in range(4):
            if codigo == 200 and estado.get("configurada") and estado.get("protegida"):
                break
            time.sleep(3)
            codigo, estado = llamada(token, "/api/ia/estado?verificacion=" + secrets.token_hex(6))
        if codigo != 200 or not estado.get("configurada") or not estado.get("protegida"):
            raise RuntimeError(f"Configuración IA no verificada: HTTP {codigo}, estado {estado}.")
        codigo, modelos = llamada(token, "/api/ia/modelos")
        if codigo != 200:
            raise RuntimeError("Google no permitió consultar modelos con la clave configurada.")
        print("Modelo disponible seleccionado: " + str(modelos.get("seleccionado")), flush=True)
        print(
            "Modelos Flash del catálogo: "
            + str([nombre for nombre in modelos.get("disponibles", []) if "flash" in nombre]),
            flush=True,
        )
        codigo, respuesta = llamada(
            token,
            "/api/ia/consultar",
            {
                "expediente_id": "EXP-2026-002",
                "contenido": "¿Qué condición debe cumplirse para levantar la hipoteca?",
            },
        )
        if codigo != 200:
            # detail proviene únicamente de errores redactados por nuestro backend.
            raise RuntimeError(
                "Prueba de generación: "
                + str(respuesta.get("detail", codigo))
                + " Tipo: "
                + str(respuesta.get("tipo_error", "no indicado"))
            )
        if not respuesta.get("citas"):
            raise RuntimeError("La prueba no devolvió evidencia citada verificable.")
        print("OK: respuesta real de Gemini validada y con citas del expediente.", flush=True)
        llamada(token, "/api/demo/solicitud", {})
        codigo, analisis = llamada(
            token, "/api/ia/analizar", {"expediente_id": "EXP-2026-001-LIVE"}
        )
        if codigo != 200 or analisis.get("fase") not in {"p2_legal", "p2_subsanacion"}:
            raise RuntimeError(
                "El análisis real no llegó a una revisión jurídica válida; se conserva la pausa humana."
            )
        print("OK: análisis real P02 detenido en " + str(analisis["fase"]) + ".", flush=True)
    finally:
        print(
            "Retirando el acceso temporal y publicando la versión con la contraseña del propietario…",
            flush=True,
        )
        vercel(["env", "rm", "SAGALE_AI_TEST_TOKEN", "production", "--yes"])
        vercel(["deploy", "--prod", "--yes", "--name", "proyecto-automa"])
    print("Publicación comprobada; ninguna clave del usuario salió de Vercel.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, TimeoutError) as error:
        raise SystemExit(str(error)) from None
