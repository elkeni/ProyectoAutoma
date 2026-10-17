---
name: cierre-p03
description: Implementa, revisa o prueba la generación, revisión, conformidad, firma e ingreso notarial del proceso P03 con sus controles HITL y PT72H.
---

# Redacción y cierre P03

Use este procedimiento para A2, borradores de minuta, notificaciones al cliente,
conformidad, firma o kárdex notarial.

## Flujo normativo

Abra `DIAGRAMAS/editables/04_TOBE_P03_cierre.bpmn`. El proceso vigente contiene 14
nodos, no 13:

1. `p3_inicio` → `p3_borrador` → `p3_revision` → `p3_aprobacion`.
2. No aprobado: `p3_corregir` → `p3_revision`.
3. Aprobado: `p3_enviar` → `p3_esperar`.
4. `p3_plazo` (`PT72H`) → `p3_fin_sin_conformidad`.
5. `p3_respuesta` → `p3_conforme`; si pide cambios, vuelve a `p3_borrador`; si está
   conforme, avanza a `p3_firma` → `p3_notaria` → `p3_fin`.

## Reglas de implementación

- Ensamble cláusulas base con reglas deterministas. Si se usa IA para redacción,
  obtenga el modelo solo con `get_llm()` y valide la salida con Pydantic antes de
  producir Word/Markdown.
- Toda afirmación derivada de recaudos conserva cita, hash y versión del mismo
  `expediente_id`. No copie contexto entre expedientes.
- Antes de `p3_revision` y `p3_firma`, persista checkpoint y ejecute `interrupt()`.
  A2 no aprueba el fondo jurídico ni firma en nombre del titular.
- Las observaciones humanas son entradas tipadas a `p3_corregir`; no se pierden al
  regenerar el borrador.
- `PT72H` lo controla el orquestador fuera del LLM. Una notificación o respuesta
  repetida debe ser idempotente.
- `p3_notaria` solo registra un kárdex devuelto por la integración/notaría; nunca lo
  inventa un modelo. La traza vincula borrador aprobado, conformidad, firma y kárdex.

## Pruebas mínimas

- Bucle de observaciones y nueva revisión sin perder historial/versiones.
- Dos `interrupt()` independientes, autenticados y no auto-reanudables.
- Respuesta conforme, petición de cambios y vencimiento exacto `PT72H`.
- Notificación duplicada sin doble envío lógico o transición repetida.
- Error de notaría sin kárdex ficticio y aislamiento entre expedientes.
