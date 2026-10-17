---
name: auditoria-trazabilidad
description: Audita cambios, workflows y ejecuciones contra los BPMN, los ocho invariantes, el aislamiento por expediente y la evidencia de trazabilidad.
---

# Auditoría y trazabilidad transversal

Use este procedimiento al revisar código, workflows n8n, herramientas MCP, reportes,
memoria conversacional, métricas o una ejecución P01–P03.

## Verificación estructural

1. Lea los cuatro BPMN con un parser XML y cuente elementos de flujo, no elementos
   gráficos. El inventario actual es AS-IS 28, P01 22, P02 22 y P03 14.
2. Compruebe cada ID citado contra el atributo `id` y cada transición contra
   `sequenceFlow sourceRef/targetRef`.
3. Verifique códigos `P1_ERROR_SHEETS`, `P1_ERROR_SISTEMA`,
   `P2_ERROR_DESCARGA`, `P2_ERROR_IA` y duraciones `P5D`/`PT72H` en el XML.
4. Marque como desfase cualquier referencia a 9 HITL, 13 nodos P03 o 57 nodos
   TO-BE. Los valores comprobados son 7 HITL, 14 nodos P03 y 58 nodos TO-BE.

## Evidencia por ejecución

Exija al menos:

- `trace_id`, `expediente_id`, `version_documental`, `proceso_actual` y
  `nodo_bpmn_id`;
- `timestamp_utc`, actor/rol y correlación de ejecución/checkpoint;
- hashes o IDs de entrada/salida, no documentos ni PII cruda;
- resultado tipado, ruta tomada, código de error y duración;
- para HITL: tarea, identidad autenticada, decisión tipada, observación, instante y
  comando de reanudación.

Audite que cada consulta RAG, memoria, tabla, checkpoint, caché y herramienta MCP
filtre por `expediente_id`. Una prueba negativa debe demostrar que el expediente B
no puede recuperar datos del A.

## Auditoría de IA y decisiones

- Toda creación de modelo debe converger en `get_llm()`.
- Toda salida debe tener validación Pydantic/`Structured Output Parser` o proceder de
  `decide()` con resultado tipado.
- A3 cita documento, versión y ubicación; si la fuente no existe, se abstiene.
- Las resoluciones humanas usadas para calibración mantienen etiqueta y versión del
  modelo/umbral. La arquitectura menciona específicamente `p2_legal`,
  `p2_subsanacion` y `p3_revision` para Brier Score/ECE; no se mezclan datos sin
  trazabilidad.
- Logs, métricas y errores deben redactar DNI, contacto, adjuntos, tokens y secretos.

## Informe

Reporte hallazgos por severidad con archivo, ID BPMN afectado, evidencia, riesgo y
recomendación. Distinga siempre entre validación ejecutada, validación no disponible
y resultado inferido. Use solo los comandos de `[tool.sagale.commands]`.
