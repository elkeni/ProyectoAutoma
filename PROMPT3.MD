Ahora ejecuta la FASE 3 para construir el Producto Funcional completo en `web/app.py` (FastAPI) que mostraremos en vivo en `http://127.0.0.1:8000` durante nuestro video tutorial:

1. Crea `scripts/cargar_datos_demo.py` para poblar la base de datos SQLite y el índice RAG con 4 expedientes demostrativos alineados a los nuevos BPMN:
   - `EXP-2026-001` (Compraventa con predio limpio - Partida SUNARP `11048592`, DNI vendedor `18124589`, DNI comprador `42567891`, valor `S/ 185,000.00`, listo para mostrar el recorrido completo `P01 -> P02 -> P03` hasta obtener el kárdex en la Notaría Bracamonte).
   - `EXP-2026-002` (Compraventa con hipoteca bancaria levantable - Partida SUNARP `11098765`, donde el `Agente A1` detecta el gravamen en `p2_agente`, el Abogado Titular lo aprueba en `p2_legal` con cláusula de levantamiento y pasa a `P03`).
   - `EXP-2026-003` (Expediente con recaudo insuficiente - Se adjuntó un Acta de Nacimiento en lugar de la Copia Literal de la Partida `4433221100`; `p2_suficiente = No` activa `p2_subsanacion` por WhatsApp para demostrar cómo el cliente envía el documento corregido en `p2_respuesta` -> `p2_version`).
   - `EXP-2026-004` (Solicitud inválida registrada en `Cola_Fallidos` con código `400 Bad Request` en `p1_400`).

2. Construye en `web/app.py` una interfaz web dividida en 3 paneles interactivos:
   - **PANEL IZQUIERDO: Simulador de WhatsApp en Caja de Texto HTML (Canal Cliente <-> Estudio Jurídico):**
     * Incluye una caja de texto HTML interactiva estilo WhatsApp donde el cliente puede escribir libremente o usar botones de prueba en un clic:
       1. `[1. Enviar Solicitud de Minuta por WhatsApp (Disparar P01)]`: Envía los 10 campos y enlaces de recaudos, ejecuta el RPA de `P01`, calcula el hash `SHA-256`, responde `201 Created` y muestra el mensaje de bienvenida del estudio recordando al cliente.
       2. `[2. Reenviar Misma Solicitud (Probar Idempotencia SHA-256 -> 200 OK)]`: Demuestra que `p1_existe` detecta el duplicado y evita crear un segundo expediente.
       3. `[3. Enviar Documentos Corregidos por WhatsApp (Disparar p2_respuesta -> p2_version)]`: Simula al cliente subsanando la partida faltante de `EXP-2026-003`, incrementando la versión documental y reejecutando `p2_documentos` y `p2_agente`.
       4. `[4. Responder "Conforme con el borrador" por WhatsApp (Disparar p3_respuesta)]`: Simula la conformidad del cliente dentro de las 72 h (`PT72H`), habilitando `p3_firma` y `p3_notaria`.

   - **PANEL CENTRAL: Monitor de Procesos TO-BE (`P01`, `P02`, `P03`), Trazabilidad de Nodos BPMN y Chat RAG Interno:**
     * Tarjetas superiores con métricas en vivo (Expedientes activos, Solicitudes en `Cola_Fallidos`, Tareas en espera HITL, Documentos indexados con Compuerta PII y Calibración Brier/ECE).
     * Barra de progreso interactiva mostrando en qué nodo exacto del BPMN (`p1_*`, `p2_*`, `p3_*`) se encuentra el expediente seleccionado y el JSON validado por `p2_parser` (`status`, `hallazgos`, `riesgos`, `evidencias`, `documentos_faltantes`).
     * Asistente Conversacional RAG para los abogados con citación verificable del expediente (`partida_sunarp_11048592.txt`, DNI, minuta) y enlace directo al catálogo tutorial (`/catalogo`).

   - **PANEL DERECHO: Bandeja de Intervención Humana (HITL) y Emisión de Minuta / Kárdex Notarial:**
     * Permite a la Abogada Asistente (`Marcela Colonia`) y al `Abogado Titular` aprobar, observar o archivar en vivo los puntos `interrupt()` de los nuevos BPMN:
       * `p1_incidente` (Recuperar solicitud tras fallo de sistema/Sheets).
       * `p2_revision_fallo` (Resolver fallo técnico de descarga o IA).
       * `p2_legal` (Aprobar viabilidad registral o pedir subsanación).
       * `p2_subsanacion` y `p2_archivar` (Solicitar subsanación o archivar por vencimiento de `P5D`).
       * `p3_revision` (Aprobar fondo legal del borrador de minuta o enviar a `p3_corregir`).
       * `p3_firma` y `p3_notaria` (Firmar minuta con colegiatura CALT y registrar el número de kárdex en la **Notaría Bracamonte**).
     * Visor y botón de descarga del borrador de la **Minuta de Compraventa en Word/Markdown** generado por el `Agente A2`.

3. Ejecuta `scripts/cargar_datos_demo.py`, corre toda la suite de `pytest` para confirmar 100% de éxito y deja iniciado o listo el servidor en `http://127.0.0.1:8000`.