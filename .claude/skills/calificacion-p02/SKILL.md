---
name: calificacion-p02
description: Implementa, revisa o prueba la calificación RAG/IA tipada, decide(), fallos, revisión legal y ciclo de subsanación del proceso P02.
---

# Calificación y análisis P02

Use este procedimiento para A1, el parser estructurado, la capa de decisión, la
revisión jurídica o el plazo de subsanación.

## Flujo normativo

Consulte siempre `DIAGRAMAS/editables/03_TOBE_P02_analisis.bpmn`:

- `p2_documentos` → `p2_agente` → `p2_parser` → `p2_json`.
- JSON válido: `p2_suficiente`; suficiente va a `p2_legal`, insuficiente a
  `p2_subsanacion`.
- JSON inválido, `P2_ERROR_IA` o `P2_ERROR_DESCARGA`: `p2_fallo` →
  `p2_revision_fallo` → `p2_legal`.
- `p2_legal` → `p2_subsanar`; si requiere subsanar, `p2_subsanacion`; en caso
  contrario, `p2_viable` → `p2_fin_viable` o `p2_fin_descartada`.
- `p2_subsanacion` → `p2_esperar`; `p2_respuesta` → `p2_version` →
  `p2_documentos`, mientras `p2_plazo` (`P5D`) → `p2_archivar` →
  `p2_fin_abandono`.

## Reglas de implementación

1. `p2_agente` obtiene el modelo únicamente mediante `get_llm()` y recupera evidencia
   filtrada por `expediente_id` y versión.
2. El prompt exige referencias verificables, pero la salida solo se acepta como un
   modelo Pydantic mediante `Structured Output Parser`.
3. `p2_parser` rechaza campos faltantes, tipos erróneos, valores fuera del dominio y
   referencias ajenas al expediente. No repare silenciosamente JSON con otro LLM.
4. `decide()` recibe datos tipados y devuelve opción, probabilidad/confianza y ruta
   auditable. Los umbrales viven en configuración versionada; el LLM no los elige.
5. Antes de `p2_revision_fallo`, `p2_legal`, `p2_subsanacion` y `p2_archivar`, guarde
   checkpoint y llame `interrupt()`. La respuesta humana debe ser tipada, autenticada
   y trazable.
6. `P5D` es un temporizador del orquestador. No se pregunta al modelo si el plazo
   venció ni se simula la espera en una llamada LLM.
7. Una calificación no sustituye el juicio jurídico de `p2_legal`.

## Pruebas mínimas

- Salida válida/suficiente, válida/insuficiente, JSON inválido y `P2_ERROR_IA`.
- Rechazo de cita perteneciente a otro expediente o a otra versión.
- Las cuatro tareas humanas generan `interrupt()` y requieren reanudación válida.
- Ramas viable, descartada, respuesta dentro de plazo y abandono por `P5D`.
- `decide()` es determinista para la misma entrada y su resultado queda trazado.
