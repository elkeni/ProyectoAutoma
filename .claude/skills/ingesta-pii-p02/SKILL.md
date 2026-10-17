---
name: ingesta-pii-p02
description: Implementa o audita la descarga, compuerta de datos personales, versionado e indexación RAG aislada de recaudos en p2_documentos y p2_version.
---

# Ingesta documental y compuerta PII

Use este procedimiento al tocar Drive, adjuntos, OCR, versiones, embeddings,
vectorización o recuperación documental.

## Fuente obligatoria

Lea `D5_Process` en `DIAGRAMAS/editables/03_TOBE_P02_analisis.bpmn`. En el XML
vigente, la entrada inicial sigue `p2_inicio` → `p2_documentos` → `p2_agente`. Una
respuesta corregida sigue `p2_respuesta` → `p2_version` → `p2_documentos`.

## Orden de control no negociable

1. Verifique `expediente_id`, autorización del actor y pertenencia del adjunto al
   expediente antes de descargarlo.
2. Descargue en un espacio temporal aislado; calcule hash y mantenga el identificador
   de fuente. No escriba el contenido en logs.
3. En `p2_documentos`, aplique la compuerta de la Ley 29733 antes de indexar o llamar
   a `get_llm()`:
   - verificar finalidad/base de tratamiento y acceso;
   - minimizar a los campos necesarios;
   - bloquear o enmascarar PII no autorizada;
   - separar el original protegido de la representación apta para IA;
   - registrar únicamente evidencia no sensible de la decisión de la compuerta.
4. Solo si la compuerta aprueba, fragmente e indexe con filtro obligatorio de
   `expediente_id`, hash y `version_documental`. Ninguna colección global puede
   recuperarse sin ese filtro.
5. En el ciclo de subsanación, `p2_version` valida y registra una versión corregida
   antes de regresar a `p2_documentos`. Una versión nunca sobrescribe a otra.
6. Una falla de descarga dispara el evento de borde `p2_error_descarga` con código
   `P2_ERROR_DESCARGA`, luego `p2_fallo` y el HITL `p2_revision_fallo`. Persista el
   checkpoint y llame `interrupt()`; no improvise una ruta alternativa.

## Resultado esperado

Devuelva un resultado tipado con `expediente_id`, versión, hashes, fuentes,
clasificación de acceso, estado de la compuerta y referencias indexadas. No devuelva
DNI completo, binarios, secretos ni texto documental innecesario.

## Pruebas mínimas

- Rechazo previo a indexación cuando falta autorización o existe PII no permitida.
- Enmascaramiento/minimización antes de embeddings y de llamadas al LLM.
- Recuperación imposible entre dos `expediente_id` distintos.
- Nueva versión inmutable tras `p2_respuesta` y reingesta correcta.
- `P2_ERROR_DESCARGA` alcanza `p2_revision_fallo` y queda pausado en `interrupt()`.
