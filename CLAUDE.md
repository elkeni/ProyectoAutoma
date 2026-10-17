# Ságale Abogados — reglas de trabajo para Claude

Este repositorio automatiza el proceso **Elaborar minuta notarial de compraventa de
bien inmueble**. Toda implementación debe preservar evidencia, revisión humana y
separación estricta entre expedientes.

## Fuente de verdad y precedencia

1. Los XML BPMN de `DIAGRAMAS/editables/` son la única fuente de verdad para flujo,
   tipo de nodo, ID, compuerta, evento, plazo y excepción.
2. `05_Arquitectura.drawio` y `06_Mapa_procesos.drawio` gobiernan las capas y
   responsabilidades, pero nunca sustituyen al BPMN para decidir una transición.
3. Si un prompt, documento, workflow o código contradice un diagrama, se detiene la
   implementación afectada, se documenta el desfase y se sigue el diagrama. No se
   renombran ni se inventan IDs para reconciliar textos.
4. `DIAGRAMAS/` es material de referencia protegido. Solo se modifica por una orden
   explícita de actualización de modelado BPMN/draw.io.

Inventario comprobado directamente el 2026-10-16:

- `01_ASIS_minuta_reconstruido.bpmn`, proceso `D3_Process`: 28 nodos.
- `02_TOBE_P01_registro.bpmn`, proceso `D4_Process`: 22 nodos.
- `03_TOBE_P02_analisis.bpmn`, proceso `D5_Process`: 22 nodos.
- `04_TOBE_P03_cierre.bpmn`, proceso `D6_Process`: **14 nodos**.
- Los TO-BE contienen **58 nodos** (22 + 22 + 14), no 57.
- El texto de FASE 1 menciona `05_Arquitectura_v2.drawio`, pero ese archivo no está
  presente. No se deben atribuir detalles a un archivo inexistente.

## Ocho invariantes técnicos obligatorios

1. **LLM único.** Toda inferencia de lenguaje pasa por `get_llm()`. Está prohibido
   instanciar modelos o clientes de proveedores en agentes, nodos o herramientas.
2. **Aislamiento por expediente.** Cada lectura, escritura, checkpoint, índice RAG,
   memoria, recurso MCP, caché y traza lleva `expediente_id`. Nunca se hace búsqueda
   vectorial ni recuperación sin filtro obligatorio por ese identificador.
3. **IA tipada.** Toda salida de IA se valida al 100 % mediante un modelo Pydantic y
   `Structured Output Parser`, o se obtiene de la capa determinista tipada `decide()`.
   Texto libre nunca gobierna una transición BPMN ni una escritura persistente.
4. **HITL real con LangGraph.** Antes de cada tarea humana se llama a `interrupt()` y
   se persiste el checkpoint. La reanudación exige una decisión humana tipada e
   identificada. Los diagramas contienen **7 puntos HITL, no 9**:
   `p1_incidente`, `p2_revision_fallo`, `p2_legal`, `p2_subsanacion`, `p2_archivar`,
   `p3_revision` y `p3_firma`. No se agregan dos puntos ficticios para satisfacer el
   número erróneo del prompt.
5. **Compuerta de datos personales.** En `p2_documentos`, la compuerta PII conforme
   a la Ley 29733 autoriza, minimiza y, cuando corresponda, enmascara los recaudos
   **antes** de indexar, vectorizar o enviarlos a un modelo.
6. **RPA fuera del LLM.** Son deterministas: validación y normalización de los 10
   campos; `SHA-256(partida_registral + dni_vendedor + dni_comprador +
   tipo_operacion)`; respuestas 201/200/400; backoff exacto de 2, 4 y 8 segundos; y
   temporizadores `P5D`/`PT72H`. El LLM no calcula, altera ni decide estas reglas.
7. **Trazabilidad BPMN.** Cada ejecución registra `expediente_id`, versión documental,
   proceso, ID exacto del nodo BPMN, actor, instante, hashes de entrada/salida,
   resultado tipado y, cuando aplique, decisión humana. No se registran PII ni
   documentos completos en logs.
8. **Comandos verificables.** Instalación y validación usan exclusivamente los
   comandos canónicos de `[tool.sagale.commands]` en `pyproject.toml`. Si la
   estructura del proyecto cambia, primero se actualiza esa tabla y después la
   documentación; nunca se inventa un comando en una respuesta.

## Contrato determinista de P01

Los diez campos son: `solicitante`, `contacto`, `tipo_operacion`, `dni_vendedor`,
`dni_comprador`, `partida_registral`, `valor_comercial`, `forma_pago`, `adj_dni` y
`adj_partida`. La ruta válida conserva esta secuencia:

`p1_inicio` → `p1_validar` → `p1_valida` → `p1_normalizar` → `p1_hash` →
`p1_consultar` → `p1_existe`.

- Nueva: `p1_registrar` → `p1_201` → `p1_fin_nueva`.
- Duplicada: `p1_200` → `p1_fin_duplicada`, sin una segunda inserción.
- Inválida: `p1_fallidos_datos` → `p1_400` → `p1_fin_invalida`.
- `p1_error_sheets` / `P1_ERROR_SHEETS`: `p1_reintento` (2, 4 y 8 s) →
  `p1_recuperado`.
- `p1_error_sistema` / `P1_ERROR_SISTEMA`: `p1_alertar` → `p1_incidente` →
  `p1_fin_incidente`.

## Controles de P02 y P03

- P02: `p2_documentos` aplica la compuerta PII; `p2_version` registra cada versión;
  `p2_agente` solo produce salida estructurada; `p2_parser` valida antes de
  `p2_json` y `p2_suficiente`. `P2_ERROR_DESCARGA` y `P2_ERROR_IA`, además del JSON
  inválido, convergen en `p2_fallo` y `p2_revision_fallo` según el BPMN.
- El ciclo de subsanación usa `p2_esperar`: `p2_respuesta` vuelve a `p2_version` y
  `p2_plazo` (`P5D`) conduce a `p2_archivar` y `p2_fin_abandono`.
- P03 conserva `p3_revision` y `p3_firma` como decisiones/acciones humanas no
  delegables. `p3_esperar` recibe `p3_respuesta` o vence en `p3_plazo` (`PT72H`) y
  `p3_fin_sin_conformidad`.

## Práctica de implementación

- Antes de codificar, leer el elemento y sus `sequenceFlow` en el BPMN vigente.
- Modelar estado, comandos de reanudación, respuestas de IA y errores con tipos.
- Usar tiempo UTC y hashes para evidencia; aplicar redacción de PII antes de logs.
- Un agente no puede aprobar viabilidad jurídica, firmar, simular una identidad
  humana ni reanudar por sí mismo un `interrupt()`.
- Toda consulta de A3 debe citar recaudo, versión y ubicación verificable del mismo
  expediente. Si no hay evidencia, debe decirlo y escalar.
- Ejecutar las validaciones canónicas aplicables antes de declarar terminado un
  cambio. No afirmar que una prueba pasó sin haber ejecutado su comando.

Las instrucciones operativas ampliadas están en `AGENTS.md`; la preparación local,
en `LEEME_entorno.md`; y los procedimientos reutilizables, en `.claude/skills/`.
