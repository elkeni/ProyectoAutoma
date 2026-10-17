# Ságale Abogados

Demostración local de automatización del proceso de minuta notarial de compraventa
de inmueble, con FastAPI, LangChain, LangGraph, SQLite y workflows exportables de n8n.

**Demo publicada:** <https://proyecto-automa.vercel.app> ·
**Repositorio:** <https://github.com/elkeni/ProyectoAutoma>

## Ejecución local

Requiere Python 3.11 o 3.12. Desde la carpeta del proyecto:

```powershell
python -m pip install -e ".[dev]"
sagale-web
```

La interfaz está en <http://127.0.0.1:8000>, el catálogo en
<http://127.0.0.1:8000/catalogo> y la documentación de la API en
<http://127.0.0.1:8000/docs>. La base de demostración se prepara al iniciar.

## Organización

- `web/`: interfaz y API FastAPI.
- `agentes/`: orquestador y cuatro agentes con flujos LangGraph.
- `nucleo/`: modelos, estado y almacenamiento.
- `rpa/`, `ingesta/`, `decision/`: reglas deterministas, compuerta PII y análisis tipado.
- `mcp_server/`: herramientas y recursos MCP.
- `n8n_workflows/`: dos workflows importables; no se ejecutan por abrir la web.
- `DIAGRAMAS/editables/`: BPMN y diagramas normativos.
- `tests/`: pruebas funcionales y adversariales.

## Validación y alcance

Los comandos canónicos están en `[tool.sagale.commands]` de `pyproject.toml`.
Consulte [el informe de robustez](docs/pruebas_robustez.md) y
[la guía del entorno](LEEME_entorno.md) para conocer pruebas, configuración y límites.

Esta es una demo con datos y respuestas controladas. Las conexiones reales a
WhatsApp, Drive, Sheets y notaría requieren configuración adicional. La identidad
seleccionable no sustituye autenticación real y los checkpoints en memoria no
proporcionan recuperación tras apagar el proceso. GitHub aloja el código; publicar
la aplicación Python en Internet requiere un servicio de ejecución adicional.

No suba credenciales, bases de datos ni documentos reales de expedientes. Las
exclusiones correspondientes están declaradas en `.gitignore`.

## Publicación de la demo

El alojamiento debe ejecutar Python 3.12 y utilizar el comando `deploy_start`
registrado en `pyproject.toml`, con un solo proceso para conservar el estado en
memoria durante la sesión. La comprobación de disponibilidad utiliza `/health`.
La elección del proveedor y su conexión se realizan desde la cuenta del propietario.

En alojamientos con disco temporal, los datos SQLite locales se pierden al
reiniciar; los checkpoints en memoria también se pierden al terminar el proceso.
No introduzca datos reales ni utilice esta demo como servicio jurídico en producción.

GitHub Actions ejecuta las verificaciones del proyecto en cada cambio sin crear
commits ni añadir colaboradores. Los commits se atribuyen al propietario.

### Vercel

La entrada FastAPI está declarada en `[tool.vercel]` y la versión Python en
`.python-version`. `vercel.json` configura la función y `.vercelignore` excluye
datos, credenciales, cachés y archivos de desarrollo del paquete publicado.

Cuando `VERCEL=1`, la demo crea su SQLite en una carpeta temporal independiente:
no intenta escribir en el código empaquetado. Cada instancia tiene su propia
demo y checkpoints; el estado puede restablecerse al cambiar o reiniciar la
instancia. Esta configuración permite mostrar la aplicación, pero no ofrece
persistencia compartida entre funciones. Un uso real requiere almacenamiento
externo y checkpoints persistentes, además de autenticación.

El despliegue inicial se realizó desde la sesión del propietario mediante Vercel
CLI. Las verificaciones de GitHub Actions están activas; el despliegue automático
de futuros commits requiere completar en Vercel la conexión GitHub con acceso al
repositorio de `elkeni`.
