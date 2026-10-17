---
name: registro-p01
description: Implementa, revisa o prueba el registro determinista P01, su contrato de diez campos, idempotencia SHA-256, respuestas HTTP y excepciones según D4_Process.
---

# Registro e idempotencia P01

Use este procedimiento para cualquier cambio relacionado con recepción, validación,
normalización, deduplicación o persistencia inicial de una solicitud.

## Fuente obligatoria

Abra `DIAGRAMAS/editables/02_TOBE_P01_registro.bpmn` y confirme los `sequenceFlow`
del proceso `D4_Process`. No derive rutas de un workflow n8n o de documentación.

## Procedimiento

1. Exija un `expediente_id` de correlación y los diez campos exactos:
   `solicitante`, `contacto`, `tipo_operacion`, `dni_vendedor`, `dni_comprador`,
   `partida_registral`, `valor_comercial`, `forma_pago`, `adj_dni`, `adj_partida`.
2. En `p1_validar`, aplique el esquema determinista vigente: presencia, tipo,
   formato, dominio permitido y disponibilidad de adjuntos. No complete ni corrija
   datos con un LLM.
3. Si `p1_valida=No`, escriba un error tipado y redactado en
   `p1_fallidos_datos`, emita `p1_400` y termine en `p1_fin_invalida`.
4. Si es válida, normalice de forma estable en `p1_normalizar`. La misma entrada
   lógica debe producir siempre la misma representación.
5. En `p1_hash`, calcule exactamente
   `SHA-256(partida_registral + dni_vendedor + dni_comprador + tipo_operacion)`
   sobre los valores normalizados. No agregue campos, separadores o sal aleatorios.
6. Consulte la clave en `p1_consultar` y decida en `p1_existe`:
   - `No`: `p1_registrar` → `p1_201` → `p1_fin_nueva`.
   - `Sí`: `p1_200` → `p1_fin_duplicada`, sin insertar otra fila.
7. Para `P1_ERROR_SHEETS` en `p1_error_sheets`, ejecute `p1_reintento` con tres
   esperas deterministas de 2, 4 y 8 segundos. `p1_recuperado=Sí` regresa a
   `p1_consultar`; `No` va a `p1_alertar`.
8. Para `P1_ERROR_SISTEMA` en `p1_error_sistema`, vaya directamente a
   `p1_alertar`. Antes de `p1_incidente`, persista estado y llame `interrupt()`;
   solo una respuesta humana autenticada continúa a `p1_fin_incidente`.
9. Emita una traza por nodo con su ID BPMN exacto y sin PII cruda.

## Pruebas mínimas

- 201 para una clave nueva; 200 idempotente sin una segunda inserción; 400 con
  registro en `Cola_Fallidos`.
- Campo faltante, tipo/formato inválido y adjunto no disponible.
- Secuencia de backoff exactamente `[2, 4, 8]`, recuperación y agotamiento.
- `P1_ERROR_SISTEMA` crea una tarea HITL y no se auto-reanuda.
- Dos expedientes con datos similares no comparten estado ni trazas.

No declare terminado el cambio sin ejecutar los comandos aplicables registrados en
`[tool.sagale.commands]` de `pyproject.toml`.
