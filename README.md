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

## Gemini real con acceso privado

Guarde `GOOGLE_API_KEY` y `SAGALE_AI_ACCESS_TOKEN` como variables sensibles de
Production en Vercel. La contraseña de acceso debe tener al menos 24 caracteres;
no se incorpora a Git ni al navegador automáticamente. Se introduce en el campo
de acceso privado y permanece solo en memoria de la página, sin almacenamiento local.

En **Consultar documentos → IA privada · Gemini**, la consulta con Gemini usa
únicamente fragmentos protegidos del expediente y versión seleccionados. Sus citas
se validan contra la evidencia disponible; si no hay evidencia no se llama al modelo.
La consulta habitual sin Gemini sigue siendo una extracción determinista.

Para un análisis P02 real, envíe una solicitud con el botón 1 y después pulse
**Analizar solicitud nueva con Gemini**. El análisis recorre `p2_documentos`,
`p2_agente` y `p2_parser`; los fallos se detienen en `p2_revision_fallo` y el
resultado válido exige revisión humana. No se reemplazan decisiones de los cuatro
casos de ejemplo ni se hacen llamadas pagadas al abrir la web.

Para repetir una exposición, el botón **1. Enviar solicitud de minuta** crea
otro expediente demostrativo con una partida sintética propia y resultado `201`.
El botón **2. Reenviar la misma solicitud** repite el expediente seleccionado y
devuelve `200`, conservando los datos, los demás casos y los controles humanos.
Puede volver al botón 1 para iniciar otro ensayo sin borrar la base. Los nuevos
identificadores llevan el prefijo `EXP-2026-001-LIVE-` y un sufijo único.
Los casos interactivos dependen de la instancia: en Vercel pueden reiniciarse;
para una exposición continua se recomienda ejecutar la aplicación localmente.

El modelo se selecciona del catálogo disponible para la clave, priorizando Flash;
puede fijarse con `SAGALE_GEMINI_MODEL`. Que aparezca en el catálogo no garantiza
cuota de generación. Una suscripción a la aplicación Gemini no sustituye la
facturación ni las cuotas del proyecto de API. Esta integración no habilita pagos.

Las respuestas se validan con Pydantic y PydanticOutputParser; toda inferencia usa
`get_llm()`. El borrador continúa usando el ensamblador determinista y ni la IA ni
sus respuestas pueden aprobar viabilidad o firmar. La consulta minimiza PII antes
de enviarse; las trazas guardan identificadores y hashes, no preguntas ni secretos.

Hay un límite local de 10 intentos por hora y expediente/versión y una separación
de 15 segundos. Este contador se reinicia con la instancia Vercel y no sustituye
los límites de gasto y cuotas que configure el propietario en Google AI Studio.
El acceso privado evita exponer la cuota como una API de IA pública.

Verificación del 2026-10-17: 194 pruebas locales aprobadas y dos comprobaciones
reales satisfactorias en Vercel: consulta documental con citas y análisis P02
detenido en `p2_legal`. El alias seleccionado fue `gemini-flash-latest`;
`gemini-2.5-flash-lite` aparecía en el catálogo pero devolvía modelo no disponible
al generar. Por eso se prioriza el alias Flash vigente, validando siempre la salida.
El acceso temporal de pruebas fue retirado; la API key y la contraseña del
propietario permanecieron cifradas en Vercel y no se descargaron al repositorio.
