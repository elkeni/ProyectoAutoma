Ahora ejecuta la FASE 2 para implementar en Python todo el sistema multiagente de Ságale Abogados siguiendo exactamente los IDs de nodos y flujos de secuencia de `02_TOBE_P01_registro.bpmn` (`p1_*`), `03_TOBE_P02_analisis.bpmn` (`p2_*`) y `04_TOBE_P03_cierre.bpmn` (`p3_*`):

1. Configura `pyproject.toml` y construye los paquetes base:
   - `nucleo/`:
     * `modelos.py`: Función única `get_llm()` con soporte para Ollama local, Google Gemini y `FakeListChatModel` (para pruebas deterministas sin red).
     * `estado.py`: Esquema tipado `EstadoExpediente` que conserva `expediente_id`, `version_documental`, `proceso_actual` (`P01` | `P02` | `P03`), `nodo_bpmn_actual`, `contrato_entrada`, `clave_idempotencia_sha256`, `codigo_http`, `calificacion_json`, `decision_sistema1`, `estado_hitl`, `borrador_minuta`, `kardex_notarial`, `historial_whatsapp` y `trazas`.
     * `base_datos.py`: Motor SQLite persistente que almacena clientes, memoria histórica de pedidos por cliente, tabla `Solicitudes`, tabla `Cola_Fallidos`, tabla `Versiones_Recaudos`, tabla `Tareas_HITL`, tabla `Mensajes_WhatsApp` y tabla `Trazas_BPMN`.
   - `rpa/` (Automatización Determinista fuera del LLM):
     * `validador_p01.py`: Implementa `p1_validar` y `p1_valida` sobre los 10 campos (`solicitante`, `contacto`, `tipo_operacion`, `dni_vendedor`, `dni_comprador`, `partida_registral`, `valor_comercial`, `forma_pago`, `adj_dni`, `adj_partida`).
     * `idempotencia_p01.py`: Implementa `p1_normalizar` y `p1_hash` con `SHA-256(partida_registral + dni_vendedor + dni_comprador + tipo_operacion)`, `p1_consultar`, `p1_existe`, `p1_registrar` (`201 Created`), `p1_200` (`200 OK` sin duplicar fila) y `p1_fallidos_datos` + `p1_400` (`400 Bad Request`).
     * `reintentos_p01.py`: Implementa los eventos de borde `p1_error_sheets` (`P1_ERROR_SHEETS` -> `p1_reintento` con 3 intentos a los 2, 4 y 8 s -> `p1_recuperado`) y `p1_error_sistema` (`P1_ERROR_SISTEMA` -> `p1_alertar` -> HITL `p1_incidente`).
     * `plantilla_minuta_p03.py`: Ensamblador determinista de cláusulas base para el borrador de minuta en Word/Markdown.
   - `ingesta/`:
     * `compuerta_pii.py`: Valida y protege datos personales según la Ley 29733 antes de indexar documentos.
     * `gestor_recaudos.py`: Implementa `p2_documentos` (con manejo de `P2_ERROR_DESCARGA`), `p2_version` (`Validar y registrar versión corregida`) y el índice RAG aislado por `expediente_id`.
   - `decision/`:
     * `umbrales.py`, `decisor.py` (función `decide()` del Sistema 1 en <50 ms con compuerta `tau_auto` y `tau_rev`) y `calibracion.py` (cálculo de Brier Score y ECE tras cada decisión humana HITL).
   - `mcp_server/`:
     * `servidor_mcp.py`: Servidor MCP (`sagale-mcp`) que expone recursos y herramientas con privilegio mínimo para `P01`, `P02` y `P03`.

2. En `agentes/`, implementa el contrato base (`agente_base.py`), el Orquestador (`orquestador.py`) y los 4 agentes en LangGraph usando exactamente los IDs de los nodos BPMN:
   - `a00_ingesta_registro.py` (`Agente A0`): Ejecuta todos los nodos `p1_*` de `02_TOBE_P01_registro.bpmn` y la ingesta/versionado (`p2_documentos`, `p2_version`) de `03_TOBE_P02_analisis.bpmn`.
   - `a01_calificador_analisis.py` (`Agente A1`): Ejecuta `p2_agente`, `p2_parser`, `p2_json`, `p2_suficiente`, el manejo de `p2_error_ia`, `p2_error_descarga` y `p2_fallo` -> HITL `p2_revision_fallo`, la revisión jurídica HITL `p2_legal` -> `p2_subsanar` -> `p2_viable` (`p2_fin_viable` vs. `p2_fin_descartada`) y el ciclo de subsanación `p2_subsanacion` -> `p2_esperar` (`p2_respuesta` -> `p2_version` vs. `p2_plazo P5D` -> HITL `p2_archivar` -> `p2_fin_abandono`).
   - `a02_redactor_cierre.py` (`Agente A2`): Ejecuta todos los nodos `p3_*` de `04_TOBE_P03_cierre.bpmn` (`p3_inicio` -> `p3_borrador` -> HITL `p3_revision` -> `p3_aprobacion` -> `p3_corregir` / `p3_enviar` -> `p3_esperar` -> `p3_respuesta` / `p3_plazo PT72H` -> `p3_fin_sin_conformidad` / `p3_conforme` -> HITL `p3_firma` -> `p3_notaria` -> `p3_fin`).
   - `a03_asistente_whatsapp_rag.py` (`Agente A3`): Gestiona la conversación con el cliente en el canal WhatsApp recordando sus pedidos en la BD, envía notificaciones de subsanación y borradores de minuta, procesa las respuestas del cliente (`p2_respuesta` y `p3_respuesta`) y responde consultas internas del estudio jurídico citando los recaudos del expediente.

3. Crea y ejecuta la suite de pruebas en `tests/` con `pytest`, `ruff` y `mypy` cubriendo:
   - Todos los caminos felices de `P01 -> P02 -> P03`.
   - Las 4 pruebas de validación, idempotencia (`200 OK`), `Cola_Fallidos` (`400`) y creación (`201`) de `P01`.
   - Todos los eventos de borde y temporizadores de los nuevos BPMN (`P1_ERROR_SHEETS`, `P1_ERROR_SISTEMA`, `P2_ERROR_DESCARGA`, `P2_ERROR_IA`, `JSON inválido`, `P5D` y `PT72H`) y las pausas `interrupt()` HITL.