# Guía operativa para agentes

Estas reglas se aplican a todo el repositorio. `CLAUDE.md` y los BPMN de
`DIAGRAMAS/editables/` son normativos; ante una contradicción, prevalece el XML BPMN
para el flujo y se informa el desfase.

## Protocolo de trabajo

1. Identificar el proceso y abrir el BPMN correspondiente.
2. Extraer los IDs y `sequenceFlow` implicados; no trabajar desde nombres recordados.
3. Delimitar el `expediente_id`, la versión documental y el actor autorizado.
4. Separar reglas deterministas, inferencia tipada y decisión humana.
5. Implementar la traza del nodo al mismo tiempo que su comportamiento.
6. Añadir o ajustar pruebas de camino feliz, excepción, aislamiento y reanudación.
7. Ejecutar los comandos aplicables registrados en `[tool.sagale.commands]` de
   `pyproject.toml` y comunicar resultados reales.

## Invariantes que ninguna tarea puede relajar

1. Usar exclusivamente `get_llm()` para obtener un modelo.
2. Aislar almacenamiento, RAG, memoria, MCP, checkpoints y trazas por
   `expediente_id`.
3. Validar toda salida IA con Pydantic + `Structured Output Parser`, o usar la salida
   tipada de `decide()`; ningún texto libre controla el grafo.
4. Ejecutar `interrupt()` en los **7** user tasks HITL verificados:
   `p1_incidente`, `p2_revision_fallo`, `p2_legal`, `p2_subsanacion`, `p2_archivar`,
   `p3_revision`, `p3_firma`. La referencia a “9 puntos” del prompt es un error de
   conteo y no autoriza a inventar nodos.
5. Ejecutar la compuerta PII/Ley 29733 antes de cualquier indexación de recaudos.
6. Mantener fuera del LLM los 10 campos de P01, la normalización, SHA-256, HTTP
   201/200/400, backoff 2/4/8 s y temporizadores P5D/PT72H.
7. Trazar cada paso con el ID exacto del BPMN, `expediente_id` y versión documental,
   sin incluir PII cruda.
8. Usar solo comandos declarados en `pyproject.toml`; actualizar primero ese registro
   si un comando cambia.

## Registro BPMN verificado

### P01 — `D4_Process` (22 nodos)

`p1_inicio`, `p1_validar`, `p1_valida`, `p1_normalizar`, `p1_hash`, `p1_consultar`,
`p1_existe`, `p1_registrar`, `p1_201`, `p1_fin_nueva`, `p1_fallidos_datos`, `p1_400`,
`p1_fin_invalida`, `p1_200`, `p1_fin_duplicada`, `p1_error_sheets`,
`p1_error_sistema`, `p1_reintento`, `p1_recuperado`, `p1_alertar`, `p1_incidente`,
`p1_fin_incidente`.

Eventos de borde: `p1_error_sheets` está unido a `p1_consultar` y referencia
`P1_ERROR_SHEETS`; `p1_error_sistema` está unido a `p1_registrar` y referencia
`P1_ERROR_SISTEMA`.

### P02 — `D5_Process` (22 nodos)

`p2_inicio`, `p2_documentos`, `p2_version`, `p2_agente`, `p2_parser`, `p2_json`,
`p2_suficiente`, `p2_error_ia`, `p2_error_descarga`, `p2_fallo`,
`p2_revision_fallo`, `p2_legal`, `p2_subsanar`, `p2_viable`, `p2_fin_viable`,
`p2_fin_descartada`, `p2_subsanacion`, `p2_esperar`, `p2_respuesta`, `p2_plazo`,
`p2_archivar`, `p2_fin_abandono`.

`p2_error_descarga` está unido a `p2_documentos` (`P2_ERROR_DESCARGA`) y
`p2_error_ia` a `p2_agente` (`P2_ERROR_IA`). `p2_plazo` contiene `P5D`.

### P03 — `D6_Process` (14 nodos)

`p3_inicio`, `p3_borrador`, `p3_revision`, `p3_aprobacion`, `p3_corregir`,
`p3_enviar`, `p3_esperar`, `p3_respuesta`, `p3_plazo`, `p3_conforme`,
`p3_fin_sin_conformidad`, `p3_firma`, `p3_notaria`, `p3_fin`.

El BPMN actual tiene 14 nodos —no 13— y `p3_plazo` contiene `PT72H`. Por ello, los
tres TO-BE suman 58 nodos. Corregir documentación derivada; no cambiar el BPMN para
forzar el conteo anterior.

## Límites de autonomía

- A0 automatiza registro/ingesta, pero no omite la compuerta PII.
- A1 analiza y aporta evidencia; no declara por sí solo viabilidad jurídica.
- A2 prepara y corrige el borrador; no aprueba fondo legal ni firma.
- A3 conversa y audita con citas; no mezcla memorias ni documentos de expedientes.
- Solo una respuesta humana autenticada reanuda una tarea HITL. Debe conservarse
  identidad, rol, instante, decisión, observación y relación con el checkpoint.
- Los conectores MCP operan con privilegio mínimo, entradas tipadas e idempotencia.
  Nunca exponen consultas sin filtro de `expediente_id`.

## Trazas y privacidad

La traza mínima contiene: `trace_id`, `expediente_id`, `version_documental`,
`proceso_actual`, `nodo_bpmn_id`, `actor`, `timestamp_utc`, hash/ID de entradas,
resultado o decisión tipada, estado HITL y correlación de error. DNI, adjuntos,
tokens, claves API, prompts con PII y documentos íntegros no se registran.

## Criterio de terminado

Un cambio está terminado cuando respeta los IDs y rutas XML vigentes, conserva los
ocho invariantes, cubre éxito y excepción con pruebas, no cruza expedientes, no
expone PII y pasa todos los comandos aplicables de `pyproject.toml`. Una validación
no disponible o no ejecutada se reporta como tal; nunca se sustituye por una
afirmación de éxito.
