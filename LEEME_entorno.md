# Preparación del entorno

Este paquete contiene la gobernanza y la implementación Python multiagente de
Ságale Abogados para P01, P02 y P03. Los grafos usan los IDs BPMN normativos,
persisten trazas en SQLite y detienen los siete controles humanos con
`langgraph.types.interrupt()`.

## Requisitos

- Python 3.11 o 3.12.
- Git.
- Bash para los hooks de `.claude/hooks/` (Git Bash en Windows es suficiente).
- Acceso al endpoint MCP de Ságale cuando se vaya a usar la integración remota.
- Ollama o una credencial de Google Gemini solo cuando se pruebe un proveedor real;
  las pruebas deben preferir `FakeListChatModel` y no depender de red.

## Instalación

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Bash:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

No se guardan secretos en el repositorio. Cree variables en su gestor de secretos o
en un archivo local ignorado por Git. Variables previstas:

- `SAGALE_LLM_PROVIDER`: `fake`, `ollama` o `gemini`.
- `OLLAMA_HOST` y `SAGALE_OLLAMA_MODEL`, si se usa Ollama.
- `GOOGLE_API_KEY`, si se usa Gemini.
- `SAGALE_GEMINI_MODEL`, para seleccionar el modelo Gemini.
- `SAGALE_MCP_URL` y `SAGALE_MCP_TOKEN`, para `.mcp.json`.
- `SAGALE_DB_PATH`, para la base SQLite local de desarrollo.

`SAGALE_LLM_PROVIDER=fake` debe ser el valor de CI. Una clave, token, DNI o adjunto real no
debe aparecer en commits, logs, fixtures ni prompts de prueba.

## Comandos canónicos

La fuente única de estos comandos es `[tool.sagale.commands]` en
`pyproject.toml`. No copie variantes en automatizaciones sin actualizar antes esa
tabla.

```text
python -m pip install -e ".[dev]"
python scripts/validar_fase1.py
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy nucleo rpa ingesta decision mcp_server agentes web tests
python -m compileall -q nucleo rpa ingesta decision mcp_server agentes web tests
```

El validador de FASE 1 contrasta BPMN, gobernanza, workflows n8n y portal. La suite
de FASE 2 cubre rutas felices, idempotencia, PII/RAG, eventos de borde,
temporizadores y las siete pausas HITL. Ninguna prueba funcional requiere red.

## Paquetes de FASE 2

- `nucleo/`: estado compartido, factoría única de LLM y SQLite persistente.
- `rpa/`: validación/idempotencia/reintentos P01 y minuta determinista P03.
- `ingesta/`: compuerta Ley 29733, versiones inmutables e índice RAG aislado.
- `decision/`: umbrales, decisión Sistema 1, Brier Score y ECE.
- `agentes/`: A0, A1, A2, A3 y orquestador con checkpoints LangGraph.
- `mcp_server/`: servidor `sagale-mcp` con herramientas separadas para P01–P03.

Para iniciar MCP por `stdio` después de instalar el proyecto:

```powershell
sagale-mcp
```

## Producto funcional de FASE 3

La demostración FastAPI carga cuatro expedientes en `data/sagale_demo.sqlite3`,
rehidrata el índice RAG únicamente a partir de recaudos autorizados por la
compuerta PII y muestra el tablero en tres paneles.

```powershell
python scripts/cargar_datos_demo.py --reiniciar
python -m uvicorn web.app:app --host 127.0.0.1 --port 8000
```

Abra `http://127.0.0.1:8000`. La interfaz incluye los cuatro disparadores de
WhatsApp, trazabilidad P01/P02/P03, consulta RAG con citas, bandeja HITL de
Marcela Colonia y el Abogado Titular, descarga Word/Markdown de minuta y captura
obligatoria del kárdex de la Notaría Bracamonte tras la firma. El catálogo del
tutorial queda disponible en `/catalogo`.

## Claude, skills y hooks

- `CLAUDE.md` y `AGENTS.md` fijan los ocho invariantes.
- `.claude/skills/` contiene cinco procedimientos: registro P01, ingesta/PII,
  calificación P02, cierre P03 y auditoría/trazabilidad.
- `proteger.sh` bloquea escrituras fuera del proyecto, sobre `DIAGRAMAS/`, `.git/` y
  archivos de secretos; también intercepta comandos de shell evidentemente
  mutantes contra esos destinos.
- `verificar_python.sh` analiza sintaxis de cada `.py` escrito y valida
  `pyproject.toml`; si Ruff está instalado, también revisa el archivo Python.

Para una comprobación manual en Windows use Git Bash explícitamente si `bash` apunta
a WSL sin distribución:

```powershell
& 'C:\Program Files\Git\bin\bash.exe' -n .claude/hooks/proteger.sh
& 'C:\Program Files\Git\bin\bash.exe' -n .claude/hooks/verificar_python.sh
```

La ubicación de Git puede variar. En este equipo también puede estar bajo `D:\Git`.

## MCP y mínimo privilegio

`.mcp.json` declara solo el servidor remoto `sagale-mcp`. El token llega por variable
de entorno y nunca se escribe en el JSON. El servidor debe exigir
`expediente_id`, comprobar autorización por herramienta y devolver recursos del
expediente solicitado únicamente. Si faltan URL o token, deje MCP desconectado; no
reemplace los valores por credenciales de ejemplo reales.

## Comprobaciones previas al desarrollo

1. Confirme que los cuatro BPMN de `DIAGRAMAS/editables/` abren como XML.
2. Tenga presente que P01/P02/P03 contienen 22/22/14 nodos: 58 en total.
3. Use exactamente los siete HITL indicados en `CLAUDE.md`.
4. No busque `05_Arquitectura_v2.drawio`: el prompt lo menciona, pero el archivo no
   está presente en el inventario actual. Registre el desfase si ese artefacto pasa a
   ser necesario.
