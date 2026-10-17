const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const OUT_DIR = __dirname;

class WorkflowBuilder {
  constructor(name, description) {
    this.workflow = {
      name,
      nodes: [],
      pinData: {},
      connections: {},
      active: false,
      settings: {
        executionOrder: 'v1',
        saveManualExecutions: true,
        callerPolicy: 'workflowsFromSameOwner',
        timezone: 'America/Lima',
      },
      versionId: crypto.randomUUID(),
      meta: {
        templateCredsSetupCompleted: false,
        source: 'PROMPT1.MD + BPMN D4/D5/D6',
        description,
      },
      tags: [],
    };
    this.byKey = new Map();
  }

  add(key, spec) {
    if (this.byKey.has(key)) throw new Error(`Duplicate node key: ${key}`);
    const node = {
      parameters: spec.parameters ?? {},
      type: spec.type,
      typeVersion: spec.typeVersion,
      position: spec.position,
      id: crypto.randomUUID(),
      name: spec.name ?? key,
    };
    if (spec.notes) {
      node.notes = spec.notes;
      node.notesInFlow = true;
    }
    if (spec.webhook) node.webhookId = crypto.randomUUID();
    for (const field of [
      'onError',
      'retryOnFail',
      'maxTries',
      'waitBetweenTries',
      'alwaysOutputData',
      'executeOnce',
    ]) {
      if (spec[field] !== undefined) node[field] = spec[field];
    }
    this.workflow.nodes.push(node);
    this.byKey.set(key, node);
    return key;
  }

  node(key) {
    const node = this.byKey.get(key);
    if (!node) throw new Error(`Unknown node key: ${key}`);
    return node;
  }

  name(key) {
    return this.node(key).name;
  }

  connect(sourceKey, targetKey, sourceOutput = 0, targetInput = 0) {
    const source = this.name(sourceKey);
    const target = this.name(targetKey);
    if (!this.workflow.connections[source]) this.workflow.connections[source] = { main: [] };
    const outputs = this.workflow.connections[source].main;
    while (outputs.length <= sourceOutput) outputs.push([]);
    outputs[sourceOutput].push({ node: target, type: 'main', index: targetInput });
  }

  finish() {
    return this.workflow;
  }
}

const BPMN = (process, id, label) =>
  `BPMN_ID=${id} | PROCESS=${process} | ${label}`;

const note = (content, position, width = 660, height = 360) => ({
  type: 'n8n-nodes-base.stickyNote',
  typeVersion: 1,
  position,
  parameters: { content, width, height, color: 5 },
});

const webhook = (pathValue, position, notes) => ({
  type: 'n8n-nodes-base.webhook',
  typeVersion: 2.1,
  position,
  webhook: true,
  notes,
  parameters: {
    httpMethod: 'POST',
    path: pathValue,
    responseMode: 'responseNode',
    options: {},
  },
});

const code = (jsCode, position, notes) => ({
  type: 'n8n-nodes-base.code',
  typeVersion: 2,
  position,
  notes,
  parameters: { jsCode },
});

const noop = (position, notes) => ({
  type: 'n8n-nodes-base.noOp',
  typeVersion: 1,
  position,
  notes,
  parameters: {},
});

const cryptoHash = (value, position, notes) => ({
  type: 'n8n-nodes-base.crypto',
  typeVersion: 1,
  position,
  notes,
  parameters: {
    type: 'SHA256',
    value,
    dataPropertyName: 'idempotency_key',
    encoding: 'hex',
  },
});

const ifBoolean = (leftValue, position, notes) => ({
  type: 'n8n-nodes-base.if',
  typeVersion: 2.2,
  position,
  notes,
  parameters: {
    conditions: {
      options: {
        caseSensitive: true,
        leftValue: '',
        typeValidation: 'strict',
        version: 2,
      },
      conditions: [
        {
          id: crypto.randomUUID(),
          leftValue,
          rightValue: '',
          operator: { type: 'boolean', operation: 'true', singleValue: true },
        },
      ],
      combinator: 'and',
    },
    options: {},
  },
});

const http = (url, bodyExpression, position, notes, options = {}) => {
  const headers = [
    { name: 'Accept', value: 'application/json' },
    { name: 'X-API-Key', value: '={{ $env.SAGALE_INTERNAL_API_KEY }}' },
  ];
  if (options.whatsapp) {
    headers.length = 0;
    headers.push(
      { name: 'Accept', value: 'application/json' },
      { name: 'Authorization', value: '=Bearer {{ $env.WHATSAPP_TOKEN }}' },
    );
  }
  const spec = {
    type: 'n8n-nodes-base.httpRequest',
    typeVersion: 4.2,
    position,
    notes,
    parameters: {
      method: options.method ?? 'POST',
      url,
      sendHeaders: true,
      headerParameters: { parameters: headers },
      sendBody: (options.method ?? 'POST') !== 'GET',
      specifyBody: 'json',
      jsonBody: `={{ JSON.stringify(${bodyExpression}) }}`,
      options: { timeout: options.timeout ?? 30000 },
    },
  };
  if (options.onError) spec.onError = options.onError;
  if (options.retryOnFail) {
    spec.retryOnFail = true;
    spec.maxTries = options.maxTries ?? 2;
    spec.waitBetweenTries = options.waitBetweenTries ?? 1000;
  }
  return spec;
};

const respond = (status, bodyExpression, position, notes) => ({
  type: 'n8n-nodes-base.respondToWebhook',
  typeVersion: 1.4,
  position,
  notes,
  parameters: {
    respondWith: 'json',
    responseBody: `={{ JSON.stringify(${bodyExpression}) }}`,
    options: {
      responseCode: status,
      responseHeaders: {
        entries: [{ name: 'Content-Type', value: 'application/json; charset=utf-8' }],
      },
    },
  },
});

const waitSeconds = (seconds, position, notes) => ({
  type: 'n8n-nodes-base.wait',
  typeVersion: 1.1,
  position,
  webhook: true,
  notes,
  parameters: { resume: 'timeInterval', amount: seconds, unit: 'seconds' },
});

const waitWebhook = (suffix, position, notes, limit) => {
  const parameters = {
    resume: 'webhook',
    httpMethod: 'POST',
    responseCode: 200,
    responseMode: 'onReceived',
    options: { webhookSuffix: suffix },
  };
  if (limit) {
    parameters.limitWaitTime = true;
    parameters.limitType = 'afterTimeInterval';
    parameters.resumeAmount = limit.amount;
    parameters.resumeUnit = limit.unit;
  }
  return {
    type: 'n8n-nodes-base.wait',
    typeVersion: 1.1,
    position,
    webhook: true,
    notes,
    parameters,
  };
};

const postgres = (query, replacements, position, notes, options = {}) => {
  const spec = {
    type: 'n8n-nodes-base.postgres',
    typeVersion: 2.6,
    position,
    notes: `${notes}\nCONFIGURACIÓN: seleccionar una credencial PostgreSQL en n8n; no se exportan secretos.`,
    parameters: {
      operation: 'executeQuery',
      query,
      options: { queryReplacement: replacements },
    },
  };
  if (options.onError) spec.onError = options.onError;
  return spec;
};

function nodeRef(builder, key) {
  return `$('${builder.name(key).replaceAll("'", "\\'")}').first().json`;
}

function addCommonP01(builder, multi) {
  const b = builder;
  b.add('p1_inicio', {
    ...webhook(
      multi ? 'sagale/tobe2/expedientes' : 'sagale/tobe1/solicitudes',
      [-520, 40],
      BPMN('D4_Process', 'p1_inicio', 'Solicitud HTTP POST; responde mediante nodos p1_201/p1_200/p1_400.'),
    ),
    name: 'p1_inicio · Solicitud HTTP POST',
  });

  b.add('p1_validar', {
    ...code(
      `const source = $json.body ?? $json;
const required = ['solicitante','contacto','tipo_operacion','dni_vendedor','dni_comprador','partida_registral','valor_comercial','forma_pago','adj_dni','adj_partida'];
const errors = [];
for (const field of required) {
  const value = source[field];
  if (value === undefined || value === null || (typeof value === 'string' && value.trim() === '')) {
    errors.push({ field, code: 'REQUIRED', message: 'Campo obligatorio ausente' });
  }
}
const digits = (value) => String(value ?? '').replace(/\\D/g, '');
if (source.dni_vendedor !== undefined && !/^\\d{8}$/.test(digits(source.dni_vendedor))) {
  errors.push({ field: 'dni_vendedor', code: 'FORMAT', message: 'Debe contener 8 dígitos' });
}
if (source.dni_comprador !== undefined && !/^\\d{8}$/.test(digits(source.dni_comprador))) {
  errors.push({ field: 'dni_comprador', code: 'FORMAT', message: 'Debe contener 8 dígitos' });
}
if (source.valor_comercial !== undefined && !(Number(source.valor_comercial) > 0)) {
  errors.push({ field: 'valor_comercial', code: 'RANGE', message: 'Debe ser mayor que cero' });
}
if (source.partida_registral !== undefined && !/^[A-Za-z0-9-]{4,30}$/.test(String(source.partida_registral).trim())) {
  errors.push({ field: 'partida_registral', code: 'FORMAT', message: 'Formato de partida inválido' });
}
return [{ json: { ...source, valida: errors.length === 0, errores: errors, contrato_version: 'P01.v1', recibido_en: new Date().toISOString() } }];`,
      [-280, 40],
      BPMN('D4_Process', 'p1_validar', 'Validación determinista del contrato de entrada de 10 campos; no usa LLM.'),
    ),
    name: 'p1_validar · 10 campos',
  });

  b.add('p1_valida', {
    ...ifBoolean(
      '={{ $json.valida === true }}',
      [-40, 40],
      BPMN('D4_Process', 'p1_valida', 'Compuerta exclusiva ¿Válida?; true=Sí, false=No.'),
    ),
    name: 'p1_valida · ¿Válida?',
  });

  b.add('p1_normalizar', {
    ...code(
      `const clean = (value) => String(value ?? '').trim().replace(/\\s+/g, ' ');
const digits = (value) => String(value ?? '').replace(/\\D/g, '');
return [{ json: {
  ...$json,
  solicitante: clean($json.solicitante),
  contacto: clean($json.contacto),
  tipo_operacion: clean($json.tipo_operacion).toLowerCase(),
  dni_vendedor: digits($json.dni_vendedor),
  dni_comprador: digits($json.dni_comprador),
  partida_registral: clean($json.partida_registral).toUpperCase(),
  valor_comercial: Number($json.valor_comercial),
  forma_pago: clean($json.forma_pago).toLowerCase(),
  normalizado_en: new Date().toISOString()
} }];`,
      [200, -80],
      BPMN('D4_Process', 'p1_normalizar', 'Normalización determinista previa a la clave de idempotencia.'),
    ),
    name: 'p1_normalizar · Reglas RPA',
  });

  b.add('p1_hash', {
    ...cryptoHash(
      '={{ $json.partida_registral + $json.dni_vendedor + $json.dni_comprador + $json.tipo_operacion }}',
      [440, -80],
      BPMN('D4_Process', 'p1_hash', 'SHA-256(partida_registral + dni_vendedor + dni_comprador + tipo_operacion).'),
    ),
    name: 'p1_hash · SHA-256',
  });

  b.add('impl_contexto_expediente', {
    ...code(
      `const key = $json.idempotency_key ?? $json.data;
if (!key || !/^[a-f0-9]{64}$/i.test(String(key))) throw new Error('No se generó una clave SHA-256 válida');
return [{ json: { ...$json, idempotency_key: String(key).toLowerCase(), expediente_id: $json.expediente_id ?? ('EXP-' + String(key).slice(0, 16).toUpperCase()) } }];`,
      [680, -80],
      'Implementación n8n: materializa expediente_id estable a partir del hash; mantiene aislamiento por expediente.',
    ),
    name: 'Impl · Contexto aislado por expediente_id',
  });

  if (multi) {
    const base = () => nodeRef(b, 'impl_contexto_expediente');
    b.add('bd_upsert_pedido', {
      ...postgres(
        `WITH expediente AS (
  INSERT INTO solicitudes (expediente_id, idempotency_key, estado, payload, created_at, updated_at)
  VALUES ($1, $2, 'P01_RECIBIDA', $3::jsonb, NOW(), NOW())
  ON CONFLICT (idempotency_key) DO UPDATE SET updated_at = NOW()
  RETURNING expediente_id
), pedido AS (
  INSERT INTO conversaciones (expediente_id, rol, tipo, contenido, created_at)
  SELECT $1, 'cliente', 'solicitud_inicial', $3::jsonb, NOW()
  WHERE NOT EXISTS (
    SELECT 1 FROM conversaciones
    WHERE expediente_id = $1 AND tipo = 'solicitud_inicial'
  )
)
SELECT $1::text AS expediente_id, $2::text AS idempotency_key;`,
        `={{ [${base()}.expediente_id, ${base()}.idempotency_key, JSON.stringify({ solicitante: ${base()}.solicitante, contacto: ${base()}.contacto, tipo_operacion: ${base()}.tipo_operacion, partida_registral: ${base()}.partida_registral, forma_pago: ${base()}.forma_pago })] }}`,
        [920, -80],
        'BD RELACIONAL: upsert de Solicitudes y registro idempotente del pedido del cliente en Memoria Conversacional.',
      ),
      name: 'BD · Solicitud + pedido conversacional',
    });
    b.add('memoria_cargar', {
      ...postgres(
        `SELECT $1::text AS expediente_id,
       COALESCE((
         SELECT jsonb_agg(x ORDER BY x.created_at)
         FROM (
           SELECT rol, tipo, contenido, created_at
           FROM conversaciones
           WHERE expediente_id = $1
           ORDER BY created_at DESC
           LIMIT 20
         ) x
       ), '[]'::jsonb) AS memoria_conversacional;`,
        `={{ [${base()}.expediente_id] }}`,
        [1160, -80],
        'MEMORIA CONVERSACIONAL: últimos 20 pedidos/interacciones, filtrados estrictamente por expediente_id.',
      ),
      name: 'Memoria · Cargar pedidos del cliente',
    });
    b.add('agente_a3_contexto', {
      ...http(
        `={{ $env.AGENT_RUNTIME_URL + '/agents/a3/context' }}`,
        `({ agente: 'A3', tarea: 'resumir_memoria_con_citas', expediente_id: ${base()}.expediente_id, memoria: $json.memoria_conversacional ?? [], aislamiento: 'expediente_id', requiere_citas: true })`,
        [1400, -80],
        'AGENTE A3 · Asistente y Auditoría RAG: resume pedidos previos y exige citas verificables; fallo no bloqueante.',
        { onError: 'continueRegularOutput', timeout: 60000 },
      ),
      name: 'A3 · Contexto conversacional y citas',
    });
    b.add('orquestador_p01', {
      ...http(
        `={{ $env.AGENT_RUNTIME_URL + '/orchestrator/dispatch' }}`,
        `({ proceso: 'P01', siguiente: 'P02', expediente_id: ${base()}.expediente_id, bpmn_node: 'p1_consultar', estado_tipado: ${base()}, contexto_a3: $json, hitl: false })`,
        [1640, -80],
        'ORQUESTADOR LangGraph: gobierna P01→P02→P03 con estado tipado y checkpointer; sin secretos embebidos.',
        { timeout: 60000 },
      ),
      name: 'Orquestador · Despachar P01',
    });
    b.add('agente_a0_p01', {
      ...http(
        `={{ $env.AGENT_RUNTIME_URL + '/agents/a0/invoke' }}`,
        `({ agente: 'A0', proceso: 'P01', expediente_id: ${base()}.expediente_id, contrato: ${base()}, reglas: ['10_campos','normalizacion','sha256','idempotencia'], salida_tipificada: true })`,
        [1880, -80],
        'AGENTE A0 · Ingesta transversal e idempotencia. La decisión final sigue siendo determinista.',
        { onError: 'continueErrorOutput', timeout: 60000 },
      ),
      name: 'A0 · Ingesta e idempotencia P01',
    });
  }

  const base = () => nodeRef(b, 'impl_contexto_expediente');
  b.add('p1_consultar', {
    ...http(
      `={{ $env.SHEETS_ADAPTER_URL + '/solicitudes/lookup' }}`,
      `({ expediente_id: ${base()}.expediente_id, idempotency_key: ${base()}.idempotency_key, sheet: 'Solicitudes' })`,
      [multi ? 2120 : 920, -80],
      BPMN('D4_Process', 'p1_consultar', 'Consulta de la clave en Google Sheets mediante adaptador; error → P1_ERROR_SHEETS.'),
      { onError: 'continueErrorOutput', timeout: 10000 },
    ),
    name: 'p1_consultar · Google Sheets',
  });
  b.add('p1_existe', {
    ...ifBoolean(
      '={{ $json.exists === true }}',
      [multi ? 2360 : 1160, -80],
      BPMN('D4_Process', 'p1_existe', 'Compuerta exclusiva ¿Existe?; true=Sí, false=No.'),
    ),
    name: 'p1_existe · ¿Existe?',
  });
  b.add('p1_registrar', {
    ...http(
      `={{ $env.SHEETS_ADAPTER_URL + '/solicitudes' }}`,
      `({ ...${base()}, sheet: 'Solicitudes', write_mode: 'append_if_absent' })`,
      [multi ? 2600 : 1400, 40],
      BPMN('D4_Process', 'p1_registrar', 'Registrar solicitud nueva; error → P1_ERROR_SISTEMA.'),
      { onError: 'continueErrorOutput', timeout: 15000 },
    ),
    name: 'p1_registrar · Solicitudes',
  });
  if (multi) {
    b.add('bd_registrar_solicitud', {
      ...postgres(
        `UPDATE solicitudes
SET estado = 'P01_REGISTRADA', updated_at = NOW()
WHERE expediente_id = $1;
INSERT INTO trazas (expediente_id, bpmn_id, evento, payload, created_at)
VALUES ($1, 'p1_registrar', '201_CREATED', jsonb_build_object('idempotency_key', $2), NOW());
SELECT $1::text AS expediente_id;`,
        `={{ [${base()}.expediente_id, ${base()}.idempotency_key] }}`,
        [2840, 40],
        'BD RELACIONAL: confirma estado P01_REGISTRADA y registra traza por ID BPMN.',
      ),
      name: 'BD · Confirmar P01 y traza',
    });
  }
  b.add('p1_201', {
    ...respond(
      201,
      `({ status: 201, message: 'Created', expediente_id: ${base()}.expediente_id, idempotency_key: ${base()}.idempotency_key, siguiente_proceso: 'P02' })`,
      [multi ? 3080 : 1640, 40],
      BPMN('D4_Process', 'p1_201', 'Responder 201 Created; la ejecución continúa asíncronamente hacia P02.'),
    ),
    name: 'p1_201 · 201 Created',
  });
  b.add('p1_fin_nueva', {
    ...noop(
      [multi ? 3320 : 1880, 40],
      BPMN('D4_Process', 'p1_fin_nueva', 'Fin P01 para solicitud nueva; continúa P02.'),
    ),
    name: 'p1_fin_nueva · Continúa P02',
  });
  b.add('p1_200', {
    ...respond(
      200,
      `({ status: 200, message: 'OK: reenvío ya atendido', expediente_id: ${base()}.expediente_id, idempotency_key: ${base()}.idempotency_key, duplicated: true })`,
      [multi ? 2600 : 1400, -220],
      BPMN('D4_Process', 'p1_200', 'Responder 200 OK sin otra inserción.'),
    ),
    name: 'p1_200 · 200 OK',
  });
  b.add('p1_fin_duplicada', {
    ...noop(
      [multi ? 2840 : 1640, -220],
      BPMN('D4_Process', 'p1_fin_duplicada', 'Reenvío atendido sin duplicar datos.'),
    ),
    name: 'p1_fin_duplicada · Reenvío atendido',
  });

  if (multi) {
    b.add('p1_fallidos_datos', {
      ...postgres(
        `INSERT INTO cola_fallidos (expediente_id, error_code, detalle, payload, created_at)
VALUES (NULL, 'P1_DATOS_INVALIDOS', $1::jsonb, $2::jsonb, NOW());
SELECT $1::jsonb AS errores;`,
        '={{ [JSON.stringify($json.errores ?? []), JSON.stringify({ contrato_version: $json.contrato_version, recibido_en: $json.recibido_en })] }}',
        [200, 300],
        BPMN('D4_Process', 'p1_fallidos_datos', 'Registrar entrada inválida en Cola_Fallidos sin persistir PII innecesaria.'),
      ),
      name: 'p1_fallidos_datos · Cola_Fallidos',
    });
  } else {
    b.add('p1_fallidos_datos', {
      ...http(
        `={{ $env.SHEETS_ADAPTER_URL + '/cola-fallidos' }}`,
        `({ error_code: 'P1_DATOS_INVALIDOS', errores: $json.errores ?? [], contrato_version: $json.contrato_version, recibido_en: $json.recibido_en })`,
        [200, 300],
        BPMN('D4_Process', 'p1_fallidos_datos', 'Registrar entrada inválida en Cola_Fallidos.'),
        { onError: 'continueRegularOutput' },
      ),
      name: 'p1_fallidos_datos · Cola_Fallidos',
    });
  }
  b.add('p1_400', {
    ...respond(
      400,
      `({ status: 400, message: 'Bad Request', errores: ${nodeRef(b, 'p1_validar')}.errores ?? [] })`,
      [440, 300],
      BPMN('D4_Process', 'p1_400', 'Responder 400 Bad Request y detallar errores deterministas.'),
    ),
    name: 'p1_400 · 400 Bad Request',
  });
  b.add('p1_fin_invalida', {
    ...noop(
      [680, 300],
      BPMN('D4_Process', 'p1_fin_invalida', 'Fin por entrada inválida.'),
    ),
    name: 'p1_fin_invalida · Entrada inválida',
  });

  b.add('p1_error_sheets', {
    ...code(
      `const base = ${base()};
return [{ json: { ...base, error_code: 'P1_ERROR_SHEETS', error_detail: $json.error?.message ?? $json.message ?? 'Fallo de consulta', failed_at: new Date().toISOString() } }];`,
      [multi ? 2120 : 920, 500],
      BPMN('D4_Process', 'p1_error_sheets', 'Evento de borde P1_ERROR_SHEETS conectado a la salida de error del HTTP Request.'),
    ),
    name: 'p1_error_sheets · P1_ERROR_SHEETS',
  });
  b.add('p1_reintento', {
    ...code(
      `return [{ json: { ...$json, retry_policy_seconds: [2,4,8], retry_attempt: 1, retry_started_at: new Date().toISOString() } }];`,
      [multi ? 2360 : 1160, 500],
      BPMN('D4_Process', 'p1_reintento', 'Inicializa exactamente 3 reintentos con backoff determinista 2, 4 y 8 segundos.'),
    ),
    name: 'p1_reintento · Backoff 2/4/8 s',
  });
  b.add('impl_wait_2', {
    ...waitSeconds(2, [multi ? 2600 : 1400, 500], 'Backoff determinista: espera 2 segundos antes del reintento 1/3.'),
    name: 'Impl · Esperar 2 s',
  });
  b.add('impl_retry_1', {
    ...http(
      `={{ $env.SHEETS_ADAPTER_URL + '/solicitudes/lookup' }}`,
      `({ expediente_id: ${base()}.expediente_id, idempotency_key: ${base()}.idempotency_key, retry_attempt: 1 })`,
      [multi ? 2840 : 1640, 500],
      'Reintento Sheets 1/3 después de 2 s.',
      { onError: 'continueErrorOutput', timeout: 10000 },
    ),
    name: 'Impl · Reintento Sheets 1 de 3',
  });
  b.add('impl_wait_4', {
    ...waitSeconds(4, [multi ? 3080 : 1880, 620], 'Backoff determinista: espera 4 segundos antes del reintento 2/3.'),
    name: 'Impl · Esperar 4 s',
  });
  b.add('impl_retry_2', {
    ...http(
      `={{ $env.SHEETS_ADAPTER_URL + '/solicitudes/lookup' }}`,
      `({ expediente_id: ${base()}.expediente_id, idempotency_key: ${base()}.idempotency_key, retry_attempt: 2 })`,
      [multi ? 3320 : 2120, 620],
      'Reintento Sheets 2/3 después de 4 s.',
      { onError: 'continueErrorOutput', timeout: 10000 },
    ),
    name: 'Impl · Reintento Sheets 2 de 3',
  });
  b.add('impl_wait_8', {
    ...waitSeconds(8, [multi ? 3560 : 2360, 740], 'Backoff determinista: espera 8 segundos antes del reintento 3/3.'),
    name: 'Impl · Esperar 8 s',
  });
  b.add('impl_retry_3', {
    ...http(
      `={{ $env.SHEETS_ADAPTER_URL + '/solicitudes/lookup' }}`,
      `({ expediente_id: ${base()}.expediente_id, idempotency_key: ${base()}.idempotency_key, retry_attempt: 3 })`,
      [multi ? 3800 : 2600, 740],
      'Reintento Sheets 3/3 después de 8 s.',
      { onError: 'continueErrorOutput', timeout: 10000 },
    ),
    name: 'Impl · Reintento Sheets 3 de 3',
  });
  b.add('impl_retry_recuperado', {
    ...code(
      `return [{ json: { ...${base()}, recovered: true, retry_response: $json, recovered_at: new Date().toISOString() } }];`,
      [multi ? 4040 : 2840, 500],
      'Consolida cualquier reintento exitoso antes de la compuerta BPMN p1_recuperado.',
    ),
    name: 'Impl · Marcar recuperación',
  });
  b.add('impl_retry_agotado', {
    ...code(
      `return [{ json: { ...${base()}, recovered: false, error_code: 'P1_ERROR_SHEETS', retries_exhausted: 3, backoff_seconds: [2,4,8] } }];`,
      [multi ? 4040 : 2840, 740],
      'Marca agotamiento tras exactamente tres reintentos; no reinicia el contador.',
    ),
    name: 'Impl · Reintentos agotados',
  });
  b.add('p1_recuperado', {
    ...ifBoolean(
      '={{ $json.recovered === true }}',
      [multi ? 4280 : 3080, 620],
      BPMN('D4_Process', 'p1_recuperado', 'Compuerta ¿Recuperado?; true vuelve a p1_consultar, false escala.'),
    ),
    name: 'p1_recuperado · ¿Recuperado?',
  });
  b.add('p1_error_sistema', {
    ...code(
      `return [{ json: { ...${base()}, error_code: 'P1_ERROR_SISTEMA', error_detail: $json.error?.message ?? $json.message ?? 'Fallo al registrar', failed_at: new Date().toISOString() } }];`,
      [multi ? 2840 : 1640, 880],
      BPMN('D4_Process', 'p1_error_sistema', 'Evento de borde P1_ERROR_SISTEMA conectado a la salida de error de p1_registrar.'),
    ),
    name: 'p1_error_sistema · P1_ERROR_SISTEMA',
  });

  if (multi) {
    b.add('bd_hitl_p1', {
      ...postgres(
        `INSERT INTO tareas_hitl (expediente_id, bpmn_id, estado, motivo, resume_url, created_at)
VALUES ($1, 'p1_incidente', 'PENDIENTE', $2, $3, NOW());
SELECT $1::text AS expediente_id, 'p1_incidente'::text AS bpmn_id;`,
        `={{ [${base()}.expediente_id, $json.error_code ?? 'P1_ERROR_SISTEMA', $execution.resumeUrl] }}`,
        [4520, 880],
        'BD RELACIONAL: crea tarea HITL p1_incidente antes de notificar al responsable.',
      ),
      name: 'BD · Crear HITL p1_incidente',
    });
  }
  b.add('p1_alertar', {
    ...http(
      multi
        ? `={{ $env.WHATSAPP_API_URL + '/messages' }}`
        : `={{ $env.ALERTS_API_URL + '/incidents' }}`,
      multi
        ? `({ messaging_product: 'whatsapp', to: $env.WHATSAPP_HITL_TO, type: 'text', text: { body: 'Incidente P01 del expediente ' + ${base()}.expediente_id + '. Revisar y reanudar: ' + $execution.resumeUrl }, metadata: { expediente_id: ${base()}.expediente_id, bpmn_id: 'p1_incidente', resume_url: $execution.resumeUrl } })`
        : `({ expediente_id: ${base()}.expediente_id, bpmn_id: 'p1_incidente', error_code: $json.error_code, resume_url: $execution.resumeUrl })`,
      [multi ? 4760 : 3320, 880],
      BPMN('D4_Process', 'p1_alertar', 'Registrar falla y alertar al responsable incluyendo URL de reanudación HITL.'),
      { onError: 'continueRegularOutput', whatsapp: multi },
    ),
    name: multi ? 'p1_alertar · WhatsApp responsable' : 'p1_alertar · Alerta responsable',
  });
  b.add('p1_incidente', {
    ...waitWebhook(
      'p1-incidente',
      [multi ? 5000 : 3560, 880],
      BPMN('D4_Process', 'p1_incidente', 'HITL: pausa durable hasta revisión y recuperación humana del incidente.'),
    ),
    name: 'p1_incidente · HITL',
  });
  if (multi) {
    b.add('bd_cerrar_hitl_p1', {
      ...postgres(
        `UPDATE tareas_hitl
SET estado = 'RESUELTA', decision = $2::jsonb, resolved_at = NOW()
WHERE expediente_id = $1 AND bpmn_id = 'p1_incidente' AND estado = 'PENDIENTE';
SELECT $1::text AS expediente_id;`,
        `={{ [${base()}.expediente_id, JSON.stringify($json.body ?? $json)] }}`,
        [5240, 800],
        'BD RELACIONAL: cierra tarea HITL p1_incidente y conserva decisión humana auditable.',
      ),
      name: 'BD · Cerrar HITL p1_incidente',
    });
  }
  b.add('p1_fin_incidente', {
    ...noop(
      [multi ? 5240 : 3800, 960],
      BPMN('D4_Process', 'p1_fin_incidente', 'Solicitud derivada a revisión; fin de esta rama.'),
    ),
    name: 'p1_fin_incidente · Derivada a revisión',
  });

  b.connect('p1_inicio', 'p1_validar');
  b.connect('p1_validar', 'p1_valida');
  b.connect('p1_valida', 'p1_normalizar', 0);
  b.connect('p1_valida', 'p1_fallidos_datos', 1);
  b.connect('p1_normalizar', 'p1_hash');
  b.connect('p1_hash', 'impl_contexto_expediente');
  if (multi) {
    b.connect('impl_contexto_expediente', 'bd_upsert_pedido');
    b.connect('bd_upsert_pedido', 'memoria_cargar');
    b.connect('memoria_cargar', 'agente_a3_contexto');
    b.connect('agente_a3_contexto', 'orquestador_p01');
    b.connect('orquestador_p01', 'agente_a0_p01');
    b.connect('agente_a0_p01', 'p1_consultar', 0);
    b.connect('agente_a0_p01', 'p1_error_sistema', 1);
  } else {
    b.connect('impl_contexto_expediente', 'p1_consultar');
  }
  b.connect('p1_consultar', 'p1_existe', 0);
  b.connect('p1_consultar', 'p1_error_sheets', 1);
  b.connect('p1_existe', 'p1_200', 0);
  b.connect('p1_existe', 'p1_registrar', 1);
  b.connect('p1_200', 'p1_fin_duplicada');
  if (multi) {
    b.connect('p1_registrar', 'bd_registrar_solicitud', 0);
    b.connect('bd_registrar_solicitud', 'p1_201');
  } else {
    b.connect('p1_registrar', 'p1_201', 0);
  }
  b.connect('p1_registrar', 'p1_error_sistema', 1);
  b.connect('p1_201', 'p1_fin_nueva');
  b.connect('p1_fallidos_datos', 'p1_400');
  b.connect('p1_400', 'p1_fin_invalida');
  b.connect('p1_error_sheets', 'p1_reintento');
  b.connect('p1_reintento', 'impl_wait_2');
  b.connect('impl_wait_2', 'impl_retry_1');
  b.connect('impl_retry_1', 'impl_retry_recuperado', 0);
  b.connect('impl_retry_1', 'impl_wait_4', 1);
  b.connect('impl_wait_4', 'impl_retry_2');
  b.connect('impl_retry_2', 'impl_retry_recuperado', 0);
  b.connect('impl_retry_2', 'impl_wait_8', 1);
  b.connect('impl_wait_8', 'impl_retry_3');
  b.connect('impl_retry_3', 'impl_retry_recuperado', 0);
  b.connect('impl_retry_3', 'impl_retry_agotado', 1);
  b.connect('impl_retry_recuperado', 'p1_recuperado');
  b.connect('impl_retry_agotado', 'p1_recuperado');
  b.connect('p1_recuperado', 'p1_consultar', 0);
  if (multi) b.connect('p1_recuperado', 'bd_hitl_p1', 1);
  else b.connect('p1_recuperado', 'p1_alertar', 1);
  if (multi) b.connect('p1_error_sistema', 'bd_hitl_p1');
  else b.connect('p1_error_sistema', 'p1_alertar');
  if (multi) b.connect('bd_hitl_p1', 'p1_alertar');
  b.connect('p1_alertar', 'p1_incidente');
  if (multi) b.connect('p1_incidente', 'bd_cerrar_hitl_p1');
  b.connect('p1_incidente', 'p1_fin_incidente');
}

function addCommonP02(builder, multi) {
  const b = builder;
  const base = () => nodeRef(b, 'impl_contexto_expediente');
  const x0 = multi ? 3560 : 2120;
  const y0 = 1160;

  b.add('p2_inicio', {
    ...noop(
      [x0, y0],
      BPMN('D5_Process', 'p2_inicio', 'Inicio P02 desde el registro exitoso de P01.'),
    ),
    name: 'p2_inicio · Registro de P01',
  });
  if (multi) {
    b.add('orquestador_p02', {
      ...http(
        `={{ $env.AGENT_RUNTIME_URL + '/orchestrator/dispatch' }}`,
        `({ proceso: 'P02', siguiente: 'P03', expediente_id: ${base()}.expediente_id, bpmn_node: 'p2_documentos', estado_tipado: ${base()}, hitl_nodes: ['p2_revision_fallo','p2_legal','p2_subsanacion','p2_archivar'] })`,
        [x0 + 240, y0],
        'ORQUESTADOR LangGraph: despacha P02 y conserva checkpointer aislado por expediente_id.',
        { timeout: 60000 },
      ),
      name: 'Orquestador · Despachar P02',
    });
  }
  b.add('p2_documentos', {
    ...http(
      multi
        ? `={{ $env.AGENT_RUNTIME_URL + '/agents/a0/ingest' }}`
        : `={{ $env.DRIVE_ADAPTER_URL + '/expedientes/' + ${base()}.expediente_id + '/recaudos:ingestar' }}`,
      `({ agente: 'A0', proceso: 'P02', expediente_id: ${base()}.expediente_id, adjuntos: { dni: ${base()}.adj_dni, partida: ${base()}.adj_partida }, origen: 'Google Drive', pii_gate: { ley: '29733', antes_de_indexar: true, estrategia: 'bloquear_o_enmascarar', aislamiento: 'expediente_id' } })`,
      [x0 + (multi ? 480 : 240), y0],
      BPMN('D5_Process', 'p2_documentos', 'A0 obtiene recaudos de Drive y ejecuta compuerta PII antes de indexar; error → P2_ERROR_DESCARGA.'),
      { onError: 'continueErrorOutput', timeout: 120000 },
    ),
    name: 'p2_documentos · A0 + Compuerta PII',
  });
  if (multi) {
    b.add('bd_version_inicial', {
      ...postgres(
        `INSERT INTO versiones_recaudos (expediente_id, version, origen, metadata, created_at)
VALUES ($1, 1, 'Google Drive', $2::jsonb, NOW())
ON CONFLICT (expediente_id, version) DO NOTHING;
INSERT INTO trazas (expediente_id, bpmn_id, evento, payload, created_at)
VALUES ($1, 'p2_documentos', 'PII_GATE_OK', jsonb_build_object('ley','29733'), NOW());
SELECT $1::text AS expediente_id;`,
        `={{ [${base()}.expediente_id, JSON.stringify({ documentos: $json.documentos ?? [], pii_gate: $json.pii_gate ?? 'ok' })] }}`,
        [x0 + 720, y0],
        'BD RELACIONAL: registra versión inicial y traza de la compuerta PII.',
      ),
      name: 'BD · Versión inicial + traza PII',
    });
  }
  b.add('p2_agente', {
    ...http(
      `={{ $env.AGENT_RUNTIME_URL + '/agents/a1/invoke' }}`,
      `({ agente: 'A1', nombre: 'Agente_Calificador_Preliminar', expediente_id: ${base()}.expediente_id, bpmn_node: 'p2_agente', documentos: ${nodeRef(b, 'p2_documentos')}, estrategia: 'RAG + Capa de Decisión', salida_requerida: { suficiente: 'boolean', confianza: 'number_0_1', hallazgos: 'array', recomendacion: 'string', evidencias: 'array_con_citas' } })`,
      [x0 + (multi ? 960 : 480), y0],
      BPMN('D5_Process', 'p2_agente', 'Agente A1 / Agente_Calificador_Preliminar con RAG y salida JSON tipada; error → P2_ERROR_IA.'),
      { onError: 'continueErrorOutput', timeout: 180000 },
    ),
    name: 'p2_agente · A1 Calificador Preliminar',
  });
  b.add('p2_parser', {
    ...code(
      `let raw = $json.output ?? $json.result ?? $json.data ?? $json;
let parsed = raw;
let parseError = null;
try {
  if (typeof parsed === 'string') parsed = JSON.parse(parsed);
  if (parsed && typeof parsed.output === 'string') parsed = JSON.parse(parsed.output);
} catch (error) {
  parseError = error.message;
  parsed = {};
}
const valid = !parseError && parsed && typeof parsed === 'object' &&
  typeof parsed.suficiente === 'boolean' &&
  typeof parsed.confianza === 'number' && parsed.confianza >= 0 && parsed.confianza <= 1 &&
  Array.isArray(parsed.hallazgos) && typeof parsed.recomendacion === 'string' &&
  Array.isArray(parsed.evidencias);
return [{ json: {
  expediente_id: ${base()}.expediente_id,
  json_valido: valid,
  parser_error: parseError ?? (valid ? null : 'La salida no cumple CalificacionPreliminarJSON'),
  suficiente: valid ? parsed.suficiente : false,
  calificacion: valid ? parsed : null,
  parsed_at: new Date().toISOString()
} }];`,
      [x0 + (multi ? 1200 : 720), y0],
      BPMN('D5_Process', 'p2_parser', 'Structured Output Parser: valida estructura, tipos y rangos; no confía en texto libre.'),
    ),
    name: 'p2_parser · Validar JSON tipado',
  });
  b.add('p2_json', {
    ...ifBoolean(
      '={{ $json.json_valido === true }}',
      [x0 + (multi ? 1440 : 960), y0],
      BPMN('D5_Process', 'p2_json', 'Compuerta exclusiva ¿JSON válido?; true=Sí, false=No.'),
    ),
    name: 'p2_json · ¿JSON válido?',
  });
  b.add('p2_suficiente', {
    ...ifBoolean(
      '={{ $json.suficiente === true }}',
      [x0 + (multi ? 1680 : 1200), y0],
      BPMN('D5_Process', 'p2_suficiente', 'Compuerta determinista ¿Suficiente? sobre la salida tipada.'),
    ),
    name: 'p2_suficiente · ¿Suficiente?',
  });

  b.add('p2_error_descarga', {
    ...code(
      `return [{ json: { expediente_id: ${base()}.expediente_id, error_code: 'P2_ERROR_DESCARGA', source_node: 'p2_documentos', error_detail: $json.error?.message ?? $json.message ?? 'Fallo de descarga/PII', failed_at: new Date().toISOString() } }];`,
      [x0 + (multi ? 480 : 240), y0 + 300],
      BPMN('D5_Process', 'p2_error_descarga', 'Evento de borde P2_ERROR_DESCARGA conectado a salida de error de p2_documentos.'),
    ),
    name: 'p2_error_descarga · P2_ERROR_DESCARGA',
  });
  b.add('p2_error_ia', {
    ...code(
      `return [{ json: { expediente_id: ${base()}.expediente_id, error_code: 'P2_ERROR_IA', source_node: 'p2_agente', error_detail: $json.error?.message ?? $json.message ?? 'Fallo del agente A1', failed_at: new Date().toISOString() } }];`,
      [x0 + (multi ? 960 : 480), y0 + 300],
      BPMN('D5_Process', 'p2_error_ia', 'Evento de borde P2_ERROR_IA conectado a salida de error de p2_agente.'),
    ),
    name: 'p2_error_ia · P2_ERROR_IA',
  });
  if (multi) {
    b.add('p2_fallo', {
      ...postgres(
        `INSERT INTO trazas (expediente_id, bpmn_id, evento, payload, created_at)
VALUES ($1, 'p2_fallo', 'FALLO_TECNICO_O_JSON', $2::jsonb, NOW());
SELECT $1::text AS expediente_id, $2::jsonb AS fallo;`,
        `={{ [${base()}.expediente_id, JSON.stringify({ error_code: $json.error_code ?? 'P2_JSON_INVALIDO', detail: $json.parser_error ?? $json.error_detail ?? 'Salida inválida' })] }}`,
        [x0 + 1920, y0 + 300],
        BPMN('D5_Process', 'p2_fallo', 'Registra fallo técnico o salida JSON inválida en BD relacional y Trazas.'),
      ),
      name: 'p2_fallo · Registrar fallo',
    });
  } else {
    b.add('p2_fallo', {
      ...http(
        `={{ $env.ALERTS_API_URL + '/technical-failures' }}`,
        `({ expediente_id: ${base()}.expediente_id, bpmn_id: 'p2_fallo', error_code: $json.error_code ?? 'P2_JSON_INVALIDO', detail: $json.parser_error ?? $json.error_detail ?? 'Salida inválida' })`,
        [x0 + 1440, y0 + 300],
        BPMN('D5_Process', 'p2_fallo', 'Registrar fallo técnico o salida JSON inválida.'),
        { onError: 'continueRegularOutput' },
      ),
      name: 'p2_fallo · Registrar fallo',
    });
  }

  if (multi) {
    b.add('bd_hitl_revision_fallo', {
      ...postgres(
        `INSERT INTO tareas_hitl (expediente_id, bpmn_id, estado, motivo, resume_url, created_at)
VALUES ($1, 'p2_revision_fallo', 'PENDIENTE', 'Fallo técnico o JSON inválido', $2, NOW());
SELECT $1::text AS expediente_id;`,
        `={{ [${base()}.expediente_id, $execution.resumeUrl] }}`,
        [x0 + 2160, y0 + 300],
        'BD RELACIONAL: crea tarea HITL p2_revision_fallo.',
      ),
      name: 'BD · Crear HITL p2_revision_fallo',
    });
    b.add('wa_hitl_revision_fallo', {
      ...http(
        `={{ $env.WHATSAPP_API_URL + '/messages' }}`,
        `({ messaging_product: 'whatsapp', to: $env.WHATSAPP_HITL_TO, type: 'text', text: { body: 'Revisar fallo P02 de ' + ${base()}.expediente_id + ': ' + $execution.resumeUrl }, metadata: { expediente_id: ${base()}.expediente_id, bpmn_id: 'p2_revision_fallo', resume_url: $execution.resumeUrl } })`,
        [x0 + 2400, y0 + 300],
        'WhatsApp al equipo jurídico con URL única de reanudación de p2_revision_fallo.',
        { whatsapp: true, onError: 'continueRegularOutput' },
      ),
      name: 'WhatsApp · HITL p2_revision_fallo',
    });
  }
  b.add('p2_revision_fallo', {
    ...waitWebhook(
      'p2-revision-fallo',
      [x0 + (multi ? 2640 : 1680), y0 + 300],
      BPMN('D5_Process', 'p2_revision_fallo', 'HITL: revisar documentos y resolver incidente.'),
    ),
    name: 'p2_revision_fallo · HITL',
  });
  if (multi) {
    b.add('bd_cerrar_revision_fallo', {
      ...postgres(
        `UPDATE tareas_hitl SET estado='RESUELTA', decision=$2::jsonb, resolved_at=NOW()
WHERE expediente_id=$1 AND bpmn_id='p2_revision_fallo' AND estado='PENDIENTE';
SELECT $1::text AS expediente_id;`,
        `={{ [${base()}.expediente_id, JSON.stringify($json.body ?? $json)] }}`,
        [x0 + 2880, y0 + 220],
        'BD RELACIONAL: cierra y audita HITL p2_revision_fallo.',
      ),
      name: 'BD · Cerrar HITL p2_revision_fallo',
    });
  }

  if (multi) {
    b.add('bd_hitl_legal', {
      ...postgres(
        `INSERT INTO tareas_hitl (expediente_id, bpmn_id, estado, motivo, resume_url, created_at)
VALUES ($1, 'p2_legal', 'PENDIENTE', 'Revisar evidencia y gravámenes', $2, NOW());
SELECT $1::text AS expediente_id;`,
        `={{ [${base()}.expediente_id, $execution.resumeUrl] }}`,
        [x0 + 1920, y0 + 520],
        'BD RELACIONAL: crea tarea jurídica HITL p2_legal.',
      ),
      name: 'BD · Crear HITL p2_legal',
    });
    b.add('wa_hitl_legal', {
      ...http(
        `={{ $env.WHATSAPP_API_URL + '/messages' }}`,
        `({ messaging_product: 'whatsapp', to: $env.WHATSAPP_HITL_TO, type: 'text', text: { body: 'Revisión jurídica/gravámenes de ' + ${base()}.expediente_id + ': ' + $execution.resumeUrl }, metadata: { expediente_id: ${base()}.expediente_id, bpmn_id: 'p2_legal', resume_url: $execution.resumeUrl } })`,
        [x0 + 2160, y0 + 520],
        'WhatsApp al abogado con URL única de reanudación de p2_legal.',
        { whatsapp: true, onError: 'continueRegularOutput' },
      ),
      name: 'WhatsApp · HITL p2_legal',
    });
  }
  b.add('p2_legal', {
    ...waitWebhook(
      'p2-legal',
      [x0 + (multi ? 2400 : 1440), y0 + 520],
      BPMN('D5_Process', 'p2_legal', 'HITL: revisión jurídica obligatoria de evidencia y gravámenes.'),
    ),
    name: 'p2_legal · HITL jurídico',
  });
  if (multi) {
    b.add('bd_cerrar_legal', {
      ...postgres(
        `UPDATE tareas_hitl SET estado='RESUELTA', decision=$2::jsonb, resolved_at=NOW()
WHERE expediente_id=$1 AND bpmn_id='p2_legal' AND estado='PENDIENTE';
INSERT INTO trazas (expediente_id,bpmn_id,evento,payload,created_at)
VALUES ($1,'p2_legal','DECISION_HUMANA',$2::jsonb,NOW());
SELECT $1::text AS expediente_id;`,
        `={{ [${base()}.expediente_id, JSON.stringify($json.body ?? $json)] }}`,
        [x0 + 2640, y0 + 440],
        'BD RELACIONAL + Calibration Loop: registra la decisión humana de p2_legal.',
      ),
      name: 'BD · Cerrar HITL p2_legal + etiqueta',
    });
  }
  b.add('p2_subsanar', {
    ...ifBoolean(
      '={{ Boolean($json.body?.subsanar ?? $json.subsanar) }}',
      [x0 + (multi ? 2640 : 1680), y0 + 600],
      BPMN('D5_Process', 'p2_subsanar', 'Compuerta exclusiva ¿Subsanar?; true=Sí, false=No.'),
    ),
    name: 'p2_subsanar · ¿Subsanar?',
  });
  b.add('p2_viable', {
    ...ifBoolean(
      '={{ Boolean($json.body?.viable ?? $json.viable) }}',
      [x0 + (multi ? 2880 : 1920), y0 + 720],
      BPMN('D5_Process', 'p2_viable', 'Compuerta exclusiva ¿Viable?; true=Sí, false=No.'),
    ),
    name: 'p2_viable · ¿Viable?',
  });
  b.add('p2_fin_viable', {
    ...noop(
      [x0 + (multi ? 3120 : 2160), y0 + 660],
      BPMN('D5_Process', 'p2_fin_viable', 'Expediente viable; continúa P03.'),
    ),
    name: 'p2_fin_viable · Continúa P03',
  });
  b.add('p2_fin_descartada', {
    ...noop(
      [x0 + (multi ? 3120 : 2160), y0 + 800],
      BPMN('D5_Process', 'p2_fin_descartada', 'Fin P02 por operación descartada.'),
    ),
    name: 'p2_fin_descartada · Operación descartada',
  });

  if (multi) {
    b.add('bd_hitl_subsanacion', {
      ...postgres(
        `INSERT INTO tareas_hitl (expediente_id,bpmn_id,estado,motivo,resume_url,created_at)
VALUES ($1,'p2_subsanacion','PENDIENTE','Redactar observaciones de subsanación',$2,NOW());
SELECT $1::text AS expediente_id;`,
        `={{ [${base()}.expediente_id, $execution.resumeUrl] }}`,
        [x0 + 1920, y0 + 920],
        'BD RELACIONAL: crea tarea HITL p2_subsanacion.',
      ),
      name: 'BD · Crear HITL p2_subsanacion',
    });
    b.add('wa_hitl_subsanacion', {
      ...http(
        `={{ $env.WHATSAPP_API_URL + '/messages' }}`,
        `({ messaging_product: 'whatsapp', to: $env.WHATSAPP_HITL_TO, type: 'text', text: { body: 'Preparar subsanación para ' + ${base()}.expediente_id + ': ' + $execution.resumeUrl }, metadata: { expediente_id: ${base()}.expediente_id, bpmn_id: 'p2_subsanacion', resume_url: $execution.resumeUrl } })`,
        [x0 + 2160, y0 + 920],
        'WhatsApp al equipo jurídico para completar p2_subsanacion.',
        { whatsapp: true, onError: 'continueRegularOutput' },
      ),
      name: 'WhatsApp · HITL p2_subsanacion',
    });
  }
  b.add('p2_subsanacion', {
    ...waitWebhook(
      'p2-subsanacion',
      [x0 + (multi ? 2400 : 1440), y0 + 920],
      BPMN('D5_Process', 'p2_subsanacion', 'HITL: abogado detalla observaciones antes de solicitar subsanación al cliente.'),
    ),
    name: 'p2_subsanacion · HITL',
  });
  if (multi) {
    b.add('bd_cerrar_subsanacion', {
      ...postgres(
        `UPDATE tareas_hitl SET estado='RESUELTA', decision=$2::jsonb, resolved_at=NOW()
WHERE expediente_id=$1 AND bpmn_id='p2_subsanacion' AND estado='PENDIENTE';
SELECT $1::text AS expediente_id;`,
        `={{ [${base()}.expediente_id, JSON.stringify($json.body ?? $json)] }}`,
        [x0 + 2640, y0 + 840],
        'BD RELACIONAL + Calibration Loop: cierra p2_subsanacion y registra etiqueta humana.',
      ),
      name: 'BD · Cerrar HITL p2_subsanacion',
    });
  }
  b.add('impl_wa_cliente_subsanacion', {
    ...http(
      `={{ $env.WHATSAPP_API_URL + '/messages' }}`,
      `({ messaging_product: 'whatsapp', to: ${base()}.contacto, type: 'text', text: { body: 'Expediente ' + ${base()}.expediente_id + ': necesitamos documentos corregidos. Responda mediante el enlace seguro: ' + $execution.resumeUrl }, metadata: { expediente_id: ${base()}.expediente_id, bpmn_id: 'p2_esperar', resume_url: $execution.resumeUrl, deadline: 'P5D' } })`,
      [x0 + (multi ? 2640 : 1680), y0 + 1000],
      'WhatsApp al cliente: solicitud de subsanación y URL de respuesta asociada al Wait p2_esperar.',
      { whatsapp: true, onError: 'continueRegularOutput' },
    ),
    name: 'WhatsApp · Solicitar documentos corregidos',
  });
  b.add('impl_preparar_espera_p2', {
    ...code(
      `return [{ json: { expediente_id: ${base()}.expediente_id, wait_kind: 'documentos_corregidos', bpmn_gateway: 'p2_esperar', deadline_iso8601: 'P5D', started_at: new Date().toISOString() } }];`,
      [x0 + (multi ? 2880 : 1920), y0 + 1000],
      'Limpia payload previo para distinguir de forma determinista callback del cliente frente a vencimiento P5D.',
    ),
    name: 'Impl · Preparar carrera evento/P5D',
  });
  b.add('p2_esperar', {
    ...waitWebhook(
      'p2-documentos-corregidos',
      [x0 + (multi ? 3120 : 2160), y0 + 1000],
      BPMN('D5_Process', 'p2_esperar', 'Compuerta basada en eventos implementada como Wait reanudable con límite P5D.'),
      { amount: 5, unit: 'days' },
    ),
    name: 'p2_esperar · Evento o P5D',
  });
  b.add('impl_router_evento_p2', {
    ...ifBoolean(
      '={{ Boolean($json.body || $json.query || $json.headers) }}',
      [x0 + (multi ? 3360 : 2400), y0 + 1000],
      'Implementación n8n de eventBasedGateway: true=webhook del cliente, false=limit wait time P5D.',
    ),
    name: 'Impl · ¿Respuesta antes de P5D?',
  });
  b.add('p2_respuesta', {
    ...code(
      `const payload = $json.body ?? $json;
return [{ json: { ...${base()}, documentos_corregidos: payload.documentos ?? payload, respuesta_cliente_en: new Date().toISOString() } }];`,
      [x0 + (multi ? 3600 : 2640), y0 + 920],
      BPMN('D5_Process', 'p2_respuesta', 'Evento intermedio: documentos corregidos recibidos del cliente.'),
    ),
    name: 'p2_respuesta · Documentos corregidos',
  });
  b.add('p2_version', {
    ...http(
      `={{ $env.DRIVE_ADAPTER_URL + '/expedientes/' + ${base()}.expediente_id + '/versiones' }}`,
      `({ expediente_id: ${base()}.expediente_id, bpmn_id: 'p2_version', source: 'cliente', documentos: $json.documentos_corregidos, action: 'validate_and_register_next_version' })`,
      [x0 + (multi ? 3840 : 2880), y0 + 920],
      BPMN('D5_Process', 'p2_version', 'Validar y registrar versión corregida antes de reingestar.'),
      { timeout: 120000 },
    ),
    name: 'p2_version · Registrar versión corregida',
  });
  b.add('p2_plazo', {
    ...code(
      `return [{ json: { expediente_id: ${base()}.expediente_id, timer: 'P5D', timed_out: true, bpmn_id: 'p2_plazo', occurred_at: new Date().toISOString() } }];`,
      [x0 + (multi ? 3600 : 2640), y0 + 1120],
      BPMN('D5_Process', 'p2_plazo', 'Evento de temporizador: 5 días sin respuesta (P5D), fuera del LLM.'),
    ),
    name: 'p2_plazo · P5D',
  });

  if (multi) {
    b.add('bd_hitl_archivar', {
      ...postgres(
        `INSERT INTO tareas_hitl (expediente_id,bpmn_id,estado,motivo,resume_url,created_at)
VALUES ($1,'p2_archivar','PENDIENTE','P5D sin subsanación',$2,NOW());
SELECT $1::text AS expediente_id;`,
        `={{ [${base()}.expediente_id, $execution.resumeUrl] }}`,
        [x0 + 3840, y0 + 1120],
        'BD RELACIONAL: crea tarea HITL p2_archivar tras P5D.',
      ),
      name: 'BD · Crear HITL p2_archivar',
    });
    b.add('wa_hitl_archivar', {
      ...http(
        `={{ $env.WHATSAPP_API_URL + '/messages' }}`,
        `({ messaging_product: 'whatsapp', to: $env.WHATSAPP_HITL_TO, type: 'text', text: { body: 'P5D vencido para ' + ${base()}.expediente_id + '. Confirmar archivo: ' + $execution.resumeUrl }, metadata: { expediente_id: ${base()}.expediente_id, bpmn_id: 'p2_archivar', resume_url: $execution.resumeUrl } })`,
        [x0 + 4080, y0 + 1120],
        'WhatsApp al responsable para confirmar archivo por falta de subsanación.',
        { whatsapp: true, onError: 'continueRegularOutput' },
      ),
      name: 'WhatsApp · HITL p2_archivar',
    });
  }
  b.add('p2_archivar', {
    ...waitWebhook(
      'p2-archivar',
      [x0 + (multi ? 4320 : 2880), y0 + 1120],
      BPMN('D5_Process', 'p2_archivar', 'HITL: confirmar archivo por falta de subsanación.'),
    ),
    name: 'p2_archivar · HITL',
  });
  if (multi) {
    b.add('bd_cerrar_archivar', {
      ...postgres(
        `UPDATE tareas_hitl SET estado='RESUELTA', decision=$2::jsonb, resolved_at=NOW()
WHERE expediente_id=$1 AND bpmn_id='p2_archivar' AND estado='PENDIENTE';
UPDATE solicitudes SET estado='P02_ABANDONO', updated_at=NOW() WHERE expediente_id=$1;
SELECT $1::text AS expediente_id;`,
        `={{ [${base()}.expediente_id, JSON.stringify($json.body ?? $json)] }}`,
        [x0 + 4560, y0 + 1040],
        'BD RELACIONAL: cierra HITL y archiva estado P02_ABANDONO.',
      ),
      name: 'BD · Cerrar HITL p2_archivar',
    });
  }
  b.add('p2_fin_abandono', {
    ...noop(
      [x0 + (multi ? 4560 : 3120), y0 + 1200],
      BPMN('D5_Process', 'p2_fin_abandono', 'Fin P02: archivada por falta de subsanación.'),
    ),
    name: 'p2_fin_abandono · Archivada',
  });

  b.connect('p1_fin_nueva', 'p2_inicio');
  if (multi) {
    b.connect('p2_inicio', 'orquestador_p02');
    b.connect('orquestador_p02', 'p2_documentos');
  } else {
    b.connect('p2_inicio', 'p2_documentos');
  }
  b.connect('p2_documentos', multi ? 'bd_version_inicial' : 'p2_agente', 0);
  if (multi) b.connect('bd_version_inicial', 'p2_agente');
  b.connect('p2_documentos', 'p2_error_descarga', 1);
  b.connect('p2_agente', 'p2_parser', 0);
  b.connect('p2_agente', 'p2_error_ia', 1);
  b.connect('p2_parser', 'p2_json');
  b.connect('p2_json', 'p2_suficiente', 0);
  b.connect('p2_json', 'p2_fallo', 1);
  b.connect('p2_error_descarga', 'p2_fallo');
  b.connect('p2_error_ia', 'p2_fallo');
  if (multi) {
    b.connect('p2_fallo', 'bd_hitl_revision_fallo');
    b.connect('bd_hitl_revision_fallo', 'wa_hitl_revision_fallo');
    b.connect('wa_hitl_revision_fallo', 'p2_revision_fallo');
    b.connect('p2_revision_fallo', 'bd_cerrar_revision_fallo');
    b.connect('p2_revision_fallo', 'bd_hitl_legal');
    b.connect('p2_suficiente', 'bd_hitl_legal', 0);
    b.connect('bd_hitl_legal', 'wa_hitl_legal');
    b.connect('wa_hitl_legal', 'p2_legal');
  } else {
    b.connect('p2_fallo', 'p2_revision_fallo');
    b.connect('p2_revision_fallo', 'p2_legal');
    b.connect('p2_suficiente', 'p2_legal', 0);
  }
  b.connect('p2_legal', 'p2_subsanar');
  if (multi) b.connect('p2_legal', 'bd_cerrar_legal');
  if (multi) {
    b.connect('p2_suficiente', 'bd_hitl_subsanacion', 1);
    b.connect('p2_subsanar', 'bd_hitl_subsanacion', 0);
    b.connect('bd_hitl_subsanacion', 'wa_hitl_subsanacion');
    b.connect('wa_hitl_subsanacion', 'p2_subsanacion');
  } else {
    b.connect('p2_suficiente', 'p2_subsanacion', 1);
    b.connect('p2_subsanar', 'p2_subsanacion', 0);
  }
  b.connect('p2_subsanar', 'p2_viable', 1);
  b.connect('p2_viable', 'p2_fin_viable', 0);
  b.connect('p2_viable', 'p2_fin_descartada', 1);
  if (multi) b.connect('p2_subsanacion', 'bd_cerrar_subsanacion');
  b.connect('p2_subsanacion', 'impl_wa_cliente_subsanacion');
  b.connect('impl_wa_cliente_subsanacion', 'impl_preparar_espera_p2');
  b.connect('impl_preparar_espera_p2', 'p2_esperar');
  b.connect('p2_esperar', 'impl_router_evento_p2');
  b.connect('impl_router_evento_p2', 'p2_respuesta', 0);
  b.connect('impl_router_evento_p2', 'p2_plazo', 1);
  b.connect('p2_respuesta', 'p2_version');
  b.connect('p2_version', 'p2_documentos');
  if (multi) {
    b.connect('p2_plazo', 'bd_hitl_archivar');
    b.connect('bd_hitl_archivar', 'wa_hitl_archivar');
    b.connect('wa_hitl_archivar', 'p2_archivar');
    b.connect('p2_archivar', 'bd_cerrar_archivar');
  } else {
    b.connect('p2_plazo', 'p2_archivar');
  }
  b.connect('p2_archivar', 'p2_fin_abandono');
}

function addP03(builder) {
  const b = builder;
  const base = () => nodeRef(b, 'impl_contexto_expediente');
  const x0 = 6920;
  const y0 = 2540;

  b.add('p3_inicio', {
    ...noop([x0, y0], BPMN('D6_Process', 'p3_inicio', 'Inicio P03 desde expediente viable de P02.')),
    name: 'p3_inicio · Expediente viable',
  });
  b.add('orquestador_p03', {
    ...http(
      `={{ $env.AGENT_RUNTIME_URL + '/orchestrator/dispatch' }}`,
      `({ proceso: 'P03', expediente_id: ${base()}.expediente_id, bpmn_node: 'p3_borrador', estado_tipado: ${base()}, hitl_nodes: ['p3_revision','p3_firma'] })`,
      [x0 + 240, y0],
      'ORQUESTADOR LangGraph: despacha P03 y conserva estado/checkpointer aislado por expediente_id.',
      { timeout: 60000 },
    ),
    name: 'Orquestador · Despachar P03',
  });
  b.add('p3_borrador', {
    ...http(
      `={{ $env.AGENT_RUNTIME_URL + '/agents/a2/draft' }}`,
      `({ agente: 'A2', expediente_id: ${base()}.expediente_id, bpmn_node: 'p3_borrador', accion: 'generar_borrador_minuta', formatos: ['docx','markdown'], expediente: ${base()}, analisis: ${nodeRef(b, 'p2_parser')}, cambios_cliente: $json.cambios ?? $json.body?.cambios ?? null, salida_tipificada: true })`,
      [x0 + 480, y0],
      BPMN('D6_Process', 'p3_borrador', 'Agente A2 genera borrador de minuta en Word/Markdown; admite bucle por cambios del cliente.'),
      { onError: 'continueErrorOutput', timeout: 180000 },
    ),
    name: 'p3_borrador · A2 Redactor',
  });
  b.add('impl_p3_error', {
    ...postgres(
      `INSERT INTO trazas (expediente_id,bpmn_id,evento,payload,created_at)
VALUES ($1,'p3_borrador','ERROR_OPERATIVO',$2::jsonb,NOW());
SELECT $1::text AS expediente_id;`,
      `={{ [${base()}.expediente_id, JSON.stringify({ detail: $json.error?.message ?? $json.message ?? 'Fallo A2' })] }}`,
      [x0 + 720, y0 + 240],
      'Control técnico n8n (sin crear un ID BPMN inexistente): registra fallo operativo de A2.',
    ),
    name: 'BD · Error operativo A2',
  });
  b.add('bd_hitl_revision_p3', {
    ...postgres(
      `INSERT INTO tareas_hitl (expediente_id,bpmn_id,estado,motivo,resume_url,created_at)
VALUES ($1,'p3_revision','PENDIENTE','Revisión de fondo legal y cláusulas',$2,NOW());
SELECT $1::text AS expediente_id;`,
      `={{ [${base()}.expediente_id, $execution.resumeUrl] }}`,
      [x0 + 720, y0],
      'BD RELACIONAL: crea tarea HITL p3_revision.',
    ),
    name: 'BD · Crear HITL p3_revision',
  });
  b.add('wa_hitl_revision_p3', {
    ...http(
      `={{ $env.WHATSAPP_API_URL + '/messages' }}`,
      `({ messaging_product: 'whatsapp', to: $env.WHATSAPP_TITULAR_TO, type: 'text', text: { body: 'Revisar minuta de ' + ${base()}.expediente_id + ': ' + $execution.resumeUrl }, metadata: { expediente_id: ${base()}.expediente_id, bpmn_id: 'p3_revision', resume_url: $execution.resumeUrl } })`,
      [x0 + 960, y0],
      'WhatsApp al Abogado Titular con URL única de p3_revision.',
      { whatsapp: true, onError: 'continueRegularOutput' },
    ),
    name: 'WhatsApp · HITL p3_revision',
  });
  b.add('p3_revision', {
    ...waitWebhook(
      'p3-revision',
      [x0 + 1200, y0],
      BPMN('D6_Process', 'p3_revision', 'HITL obligatorio: revisar fondo legal y cláusulas.'),
    ),
    name: 'p3_revision · HITL Titular',
  });
  b.add('bd_cerrar_revision_p3', {
    ...postgres(
      `UPDATE tareas_hitl SET estado='RESUELTA',decision=$2::jsonb,resolved_at=NOW()
WHERE expediente_id=$1 AND bpmn_id='p3_revision' AND estado='PENDIENTE';
INSERT INTO trazas (expediente_id,bpmn_id,evento,payload,created_at)
VALUES ($1,'p3_revision','DECISION_HUMANA',$2::jsonb,NOW());
SELECT $1::text AS expediente_id;`,
      `={{ [${base()}.expediente_id, JSON.stringify($json.body ?? $json)] }}`,
      [x0 + 1440, y0 - 100],
      'BD RELACIONAL + Calibration Loop: conserva decisión humana de p3_revision.',
    ),
    name: 'BD · Cerrar HITL p3_revision + etiqueta',
  });
  b.add('p3_aprobacion', {
    ...ifBoolean(
      '={{ Boolean($json.body?.aprobado ?? $json.aprobado) }}',
      [x0 + 1440, y0 + 80],
      BPMN('D6_Process', 'p3_aprobacion', 'Compuerta exclusiva ¿Aprobado?; true=Sí, false=No.'),
    ),
    name: 'p3_aprobacion · ¿Aprobado?',
  });
  b.add('p3_corregir', {
    ...http(
      `={{ $env.AGENT_RUNTIME_URL + '/agents/a2/correct' }}`,
      `({ agente: 'A2', expediente_id: ${base()}.expediente_id, bpmn_node: 'p3_corregir', borrador: ${nodeRef(b, 'p3_borrador')}, observaciones: $json.body?.observaciones ?? $json.observaciones ?? [], accion: 'subsanar_observaciones_y_transcripcion' })`,
      [x0 + 1680, y0 + 220],
      BPMN('D6_Process', 'p3_corregir', 'A2 subsana observaciones y errores de transcripción; vuelve a revisión.'),
      { timeout: 180000 },
    ),
    name: 'p3_corregir · A2 Subsanar',
  });
  b.add('p3_enviar', {
    ...http(
      `={{ $env.WHATSAPP_API_URL + '/messages' }}`,
      `({ messaging_product: 'whatsapp', to: ${base()}.contacto, type: 'text', text: { body: 'Borrador de minuta del expediente ' + ${base()}.expediente_id + ' disponible. Confirme o solicite cambios: ' + $execution.resumeUrl }, document: ${nodeRef(b, 'p3_borrador')}.document_url ?? ${nodeRef(b, 'p3_borrador')}.docx_url, metadata: { expediente_id: ${base()}.expediente_id, bpmn_id: 'p3_enviar', resume_url: $execution.resumeUrl, deadline: 'PT72H' } })`,
      [x0 + 1680, y0 - 40],
      BPMN('D6_Process', 'p3_enviar', 'A2 envía borrador al cliente por API/WhatsApp e incluye URL de respuesta.'),
      { whatsapp: true, onError: 'continueRegularOutput' },
    ),
    name: 'p3_enviar · A2 + WhatsApp',
  });
  b.add('impl_preparar_espera_p3', {
    ...code(
      `return [{ json: { expediente_id: ${base()}.expediente_id, wait_kind: 'conformidad_cliente', bpmn_gateway: 'p3_esperar', deadline_iso8601: 'PT72H', started_at: new Date().toISOString() } }];`,
      [x0 + 1920, y0 - 40],
      'Limpia payload para distinguir callback del cliente frente a vencimiento PT72H.',
    ),
    name: 'Impl · Preparar carrera evento/PT72H',
  });
  b.add('p3_esperar', {
    ...waitWebhook(
      'p3-conformidad-cliente',
      [x0 + 2160, y0 - 40],
      BPMN('D6_Process', 'p3_esperar', 'Compuerta basada en eventos implementada como Wait con límite PT72H.'),
      { amount: 72, unit: 'hours' },
    ),
    name: 'p3_esperar · Evento o PT72H',
  });
  b.add('impl_router_evento_p3', {
    ...ifBoolean(
      '={{ Boolean($json.body || $json.query || $json.headers) }}',
      [x0 + 2400, y0 - 40],
      'Implementación n8n de eventBasedGateway: true=respuesta; false=vencimiento PT72H.',
    ),
    name: 'Impl · ¿Respuesta antes de PT72H?',
  });
  b.add('p3_respuesta', {
    ...code(
      `const payload = $json.body ?? $json;
return [{ json: { expediente_id: ${base()}.expediente_id, cliente_conforme: Boolean(payload.conforme), cambios: payload.cambios ?? payload.observaciones ?? [], respuesta_cliente_en: new Date().toISOString() } }];`,
      [x0 + 2640, y0 - 140],
      BPMN('D6_Process', 'p3_respuesta', 'Evento intermedio: conformidad o cambios recibidos del cliente.'),
    ),
    name: 'p3_respuesta · Respuesta cliente',
  });
  b.add('p3_conforme', {
    ...ifBoolean(
      '={{ $json.cliente_conforme === true }}',
      [x0 + 2880, y0 - 140],
      BPMN('D6_Process', 'p3_conforme', 'Compuerta exclusiva ¿Conforme?; true=Sí, false=pide cambios.'),
    ),
    name: 'p3_conforme · ¿Conforme?',
  });
  b.add('p3_plazo', {
    ...code(
      `return [{ json: { expediente_id: ${base()}.expediente_id, timer: 'PT72H', timed_out: true, bpmn_id: 'p3_plazo', occurred_at: new Date().toISOString() } }];`,
      [x0 + 2640, y0 + 100],
      BPMN('D6_Process', 'p3_plazo', 'Evento de temporizador: 72 horas sin respuesta (PT72H), fuera del LLM.'),
    ),
    name: 'p3_plazo · PT72H',
  });
  b.add('bd_archivar_sin_conformidad', {
    ...postgres(
      `UPDATE solicitudes SET estado='P03_SIN_CONFORMIDAD',updated_at=NOW() WHERE expediente_id=$1;
INSERT INTO trazas (expediente_id,bpmn_id,evento,payload,created_at)
VALUES ($1,'p3_plazo','PT72H',jsonb_build_object('timer','PT72H'),NOW());
SELECT $1::text AS expediente_id;`,
      `={{ [${base()}.expediente_id] }}`,
      [x0 + 2880, y0 + 100],
      'BD RELACIONAL: persiste cierre P03_SIN_CONFORMIDAD por PT72H.',
    ),
    name: 'BD · Archivar sin conformidad',
  });
  b.add('wa_sin_conformidad', {
    ...http(
      `={{ $env.WHATSAPP_API_URL + '/messages' }}`,
      `({ messaging_product: 'whatsapp', to: ${base()}.contacto, type: 'text', text: { body: 'El expediente ' + ${base()}.expediente_id + ' fue archivado al no recibirse conformidad en 72 horas.' }, metadata: { expediente_id: ${base()}.expediente_id, bpmn_id: 'p3_plazo' } })`,
      [x0 + 3120, y0 + 100],
      'WhatsApp informativo al cliente tras vencer PT72H.',
      { whatsapp: true, onError: 'continueRegularOutput' },
    ),
    name: 'WhatsApp · Archivo PT72H',
  });
  b.add('p3_fin_sin_conformidad', {
    ...noop(
      [x0 + 3360, y0 + 100],
      BPMN('D6_Process', 'p3_fin_sin_conformidad', 'Fin P03: archivada sin conformidad.'),
    ),
    name: 'p3_fin_sin_conformidad · Archivada',
  });

  b.add('bd_hitl_firma', {
    ...postgres(
      `INSERT INTO tareas_hitl (expediente_id,bpmn_id,estado,motivo,resume_url,created_at)
VALUES ($1,'p3_firma','PENDIENTE','Firma y sello físico de minuta',$2,NOW());
SELECT $1::text AS expediente_id;`,
      `={{ [${base()}.expediente_id, $execution.resumeUrl] }}`,
      [x0 + 3120, y0 - 220],
      'BD RELACIONAL: crea tarea HITL p3_firma.',
    ),
    name: 'BD · Crear HITL p3_firma',
  });
  b.add('wa_hitl_firma', {
    ...http(
      `={{ $env.WHATSAPP_API_URL + '/messages' }}`,
      `({ messaging_product: 'whatsapp', to: $env.WHATSAPP_TITULAR_TO, type: 'text', text: { body: 'Firma física pendiente para ' + ${base()}.expediente_id + ': ' + $execution.resumeUrl }, metadata: { expediente_id: ${base()}.expediente_id, bpmn_id: 'p3_firma', resume_url: $execution.resumeUrl } })`,
      [x0 + 3360, y0 - 220],
      'WhatsApp al Titular para firma física y sello.',
      { whatsapp: true, onError: 'continueRegularOutput' },
    ),
    name: 'WhatsApp · HITL p3_firma',
  });
  b.add('p3_firma', {
    ...waitWebhook(
      'p3-firma',
      [x0 + 3600, y0 - 220],
      BPMN('D6_Process', 'p3_firma', 'HITL obligatorio: firma y sello físico de la minuta.'),
    ),
    name: 'p3_firma · HITL firma física',
  });
  b.add('bd_cerrar_firma', {
    ...postgres(
      `UPDATE tareas_hitl SET estado='RESUELTA',decision=$2::jsonb,resolved_at=NOW()
WHERE expediente_id=$1 AND bpmn_id='p3_firma' AND estado='PENDIENTE';
SELECT $1::text AS expediente_id;`,
      `={{ [${base()}.expediente_id, JSON.stringify($json.body ?? $json)] }}`,
      [x0 + 3840, y0 - 300],
      'BD RELACIONAL: cierra y audita HITL p3_firma.',
    ),
    name: 'BD · Cerrar HITL p3_firma',
  });
  b.add('p3_notaria', {
    ...http(
      `={{ $env.NOTARIA_ADAPTER_URL + '/minutas' }}`,
      `({ expediente_id: ${base()}.expediente_id, bpmn_id: 'p3_notaria', notaria: 'Bracamonte', borrador: ${nodeRef(b, 'p3_borrador')}, firma: $json.body ?? $json, action: 'ingresar_y_obtener_kardex' })`,
      [x0 + 3840, y0 - 140],
      BPMN('D6_Process', 'p3_notaria', 'Ingresar minuta en Notaría Bracamonte y obtener kárdex.'),
      { timeout: 120000 },
    ),
    name: 'p3_notaria · Ingreso y kárdex',
  });
  b.add('p3_fin', {
    ...noop(
      [x0 + 4080, y0 - 140],
      BPMN('D6_Process', 'p3_fin', 'Fin exitoso del proceso de minuta notarial.'),
    ),
    name: 'p3_fin · Fin',
  });
  b.add('agente_a3_auditoria', {
    ...http(
      `={{ $env.AGENT_RUNTIME_URL + '/agents/a3/audit' }}`,
      `({ agente: 'A3', expediente_id: ${base()}.expediente_id, alcance: ['P01','P02','P03'], requiere_citas: true, indicadores: ['tiempo_ciclo','reproceso','hitl','excepciones'], kardex: ${nodeRef(b, 'p3_notaria')}, bpmn_fin: 'p3_fin' })`,
      [x0 + 4320, y0 - 140],
      'AGENTE A3 · Asistente y Auditoría RAG: genera cierre con citas y métricas P01–P03.',
      { onError: 'continueRegularOutput', timeout: 120000 },
    ),
    name: 'A3 · Auditoría E2E con citas',
  });
  b.add('bd_cierre_memoria', {
    ...postgres(
      `UPDATE solicitudes SET estado='P03_CERRADA',updated_at=NOW() WHERE expediente_id=$1;
INSERT INTO conversaciones (expediente_id,rol,tipo,contenido,created_at)
VALUES ($1,'sistema','cierre_notarial',$2::jsonb,NOW());
INSERT INTO trazas (expediente_id,bpmn_id,evento,payload,created_at)
VALUES ($1,'p3_fin','PROCESO_CERRADO',$2::jsonb,NOW());
SELECT $1::text AS expediente_id, 'P03_CERRADA'::text AS estado;`,
      `={{ [${base()}.expediente_id, JSON.stringify({ auditoria: $json, kardex: ${nodeRef(b, 'p3_notaria')}.kardex ?? null })] }}`,
      [x0 + 4560, y0 - 140],
      'BD RELACIONAL + MEMORIA: cierra expediente, guarda resumen conversacional y traza final.',
    ),
    name: 'BD + Memoria · Cierre P03',
  });

  b.connect('p2_fin_viable', 'p3_inicio');
  b.connect('p3_inicio', 'orquestador_p03');
  b.connect('orquestador_p03', 'p3_borrador');
  b.connect('p3_borrador', 'bd_hitl_revision_p3', 0);
  b.connect('p3_borrador', 'impl_p3_error', 1);
  b.connect('bd_hitl_revision_p3', 'wa_hitl_revision_p3');
  b.connect('wa_hitl_revision_p3', 'p3_revision');
  b.connect('p3_revision', 'bd_cerrar_revision_p3');
  b.connect('p3_revision', 'p3_aprobacion');
  b.connect('p3_aprobacion', 'p3_enviar', 0);
  b.connect('p3_aprobacion', 'p3_corregir', 1);
  b.connect('p3_corregir', 'bd_hitl_revision_p3');
  b.connect('p3_enviar', 'impl_preparar_espera_p3');
  b.connect('impl_preparar_espera_p3', 'p3_esperar');
  b.connect('p3_esperar', 'impl_router_evento_p3');
  b.connect('impl_router_evento_p3', 'p3_respuesta', 0);
  b.connect('impl_router_evento_p3', 'p3_plazo', 1);
  b.connect('p3_respuesta', 'p3_conforme');
  b.connect('p3_conforme', 'bd_hitl_firma', 0);
  b.connect('p3_conforme', 'p3_borrador', 1);
  b.connect('p3_plazo', 'bd_archivar_sin_conformidad');
  b.connect('bd_archivar_sin_conformidad', 'wa_sin_conformidad');
  b.connect('wa_sin_conformidad', 'p3_fin_sin_conformidad');
  b.connect('bd_hitl_firma', 'wa_hitl_firma');
  b.connect('wa_hitl_firma', 'p3_firma');
  b.connect('p3_firma', 'bd_cerrar_firma');
  b.connect('p3_firma', 'p3_notaria');
  b.connect('p3_notaria', 'p3_fin');
  b.connect('p3_fin', 'agente_a3_auditoria');
  b.connect('agente_a3_auditoria', 'bd_cierre_memoria');
}

function addOverview(builder, multi) {
  const title = multi ? 'TO-BE 2 · Ecosistema multiagéntico E2E' : 'TO-BE 1 · RPA + análisis documental';
  const scope = multi ? 'D4/P01 + D5/P02 + D6/P03' : 'D4/P01 + D5/P02';
  const body = multi
    ? `## ${title}\n\n**Fuente de verdad:** BPMN ${scope}.\n\n- Orquestador LangGraph y agentes A0, A1, A2, A3.\n- PostgreSQL: Solicitudes, Cola_Fallidos, Versiones_Recaudos, Tareas_HITL, Trazas y conversaciones.\n- Memoria aislada por expediente_id.\n- HITL durable y avisos WhatsApp.\n- Excepciones P1/P2, backoff 2/4/8 s, P5D y PT72H deterministas.\n\nAntes de activar, configure credencial PostgreSQL y variables de entorno indicadas en la nota de configuración.`
    : `## ${title}\n\n**Fuente de verdad:** BPMN ${scope}.\n\n- Contrato determinista de 10 campos.\n- SHA-256 e idempotencia 201/200/400.\n- P1_ERROR_SHEETS con exactamente 2/4/8 s y P1_ERROR_SISTEMA.\n- Drive + compuerta PII, A1, parser JSON y excepciones P2.\n- HITL durable y carrera evento/P5D.\n\nLas URLs y tokens se leen de variables de entorno; este export no contiene secretos.`;
  builder.add('overview', {
    ...note(body, [-1280, -360], 720, multi ? 520 : 460),
    name: `Guía · ${title}`,
  });
  const config = `## Configuración sin secretos\n\nVariables de entorno usadas:\n- \`SHEETS_ADAPTER_URL\`\n- \`DRIVE_ADAPTER_URL\`\n- \`AGENT_RUNTIME_URL\`\n- \`ALERTS_API_URL\`\n- \`SAGALE_INTERNAL_API_KEY\`\n- \`WHATSAPP_API_URL\`, \`WHATSAPP_TOKEN\`\n- \`WHATSAPP_HITL_TO\`, \`WHATSAPP_TITULAR_TO\`\n- \`NOTARIA_ADAPTER_URL\` (TO-BE 2)\n\nPostgreSQL (TO-BE 2): asigne una credencial desde n8n a cada nodo BD; el JSON no exporta IDs ni claves.\n\nContratos de adaptadores: respuesta lookup \`{exists:boolean}\`; A1 devuelve \`CalificacionPreliminarJSON\`; callbacks HITL/cliente usan POST JSON.`;
  builder.add('configuration', {
    ...note(config, [-1280, multi ? 220 : 150], 720, 520),
    name: 'Configuración · Variables y contratos',
  });
}

function buildTobe1() {
  const builder = new WorkflowBuilder(
    '01_TOBE1_flujo_rpa_y_analisis_actualizado',
    'P01 + P02 sincronizados con D4_Process y D5_Process.',
  );
  addOverview(builder, false);
  addCommonP01(builder, false);
  addCommonP02(builder, false);
  return builder.finish();
}

function buildTobe2() {
  const builder = new WorkflowBuilder(
    '02_TOBE2_flujo_multiagentico_completo',
    'P01 + P02 + P03, Orquestador, A0–A3, PostgreSQL, memoria, HITL y WhatsApp.',
  );
  addOverview(builder, true);
  addCommonP01(builder, true);
  addCommonP02(builder, true);
  addP03(builder);
  return builder.finish();
}

function validateWorkflow(workflow, requiredBpmnIds) {
  const errors = [];
  const names = new Set();
  const ids = new Set();
  for (const node of workflow.nodes) {
    if (!node.name || !node.type || node.typeVersion === undefined || !Array.isArray(node.position)) {
      errors.push(`Nodo incompleto: ${JSON.stringify(node)}`);
    }
    if (names.has(node.name)) errors.push(`Nombre duplicado: ${node.name}`);
    if (ids.has(node.id)) errors.push(`ID duplicado: ${node.id}`);
    names.add(node.name);
    ids.add(node.id);
  }
  for (const [source, connection] of Object.entries(workflow.connections)) {
    if (!names.has(source)) errors.push(`Conexión desde nodo inexistente: ${source}`);
    if (!connection || !Array.isArray(connection.main)) errors.push(`Conexión main inválida: ${source}`);
    for (const output of connection.main ?? []) {
      if (!Array.isArray(output)) errors.push(`Salida no es arreglo: ${source}`);
      for (const edge of output ?? []) {
        if (!names.has(edge.node)) errors.push(`Conexión hacia nodo inexistente: ${source} -> ${edge.node}`);
        if (edge.type !== 'main' || edge.index !== 0) errors.push(`Conexión no estándar: ${source}`);
      }
    }
  }
  const searchable = workflow.nodes.map((n) => `${n.name}\n${n.notes ?? ''}`).join('\n');
  for (const id of requiredBpmnIds) {
    const exact = new RegExp(`(^|[^A-Za-z0-9_])${id}([^A-Za-z0-9_]|$)`);
    if (!exact.test(searchable)) errors.push(`Falta ID BPMN: ${id}`);
  }
  const webhookNodes = workflow.nodes.filter((n) => n.type === 'n8n-nodes-base.webhook');
  if (webhookNodes.length !== 1) errors.push(`Se esperaba un Webhook de entrada; hay ${webhookNodes.length}`);
  const errorOutputSources = workflow.nodes.filter((n) => n.onError === 'continueErrorOutput');
  for (const node of errorOutputSources) {
    const outputs = workflow.connections[node.name]?.main ?? [];
    if (!outputs[1]?.length) errors.push(`Nodo con onError sin salida de error conectada: ${node.name}`);
  }
  if (errors.length) throw new Error(`${workflow.name}:\n${errors.join('\n')}`);
}

function getBpmnIds(file) {
  const xml = fs.readFileSync(file, 'utf8');
  const tags = 'startEvent|endEvent|serviceTask|userTask|exclusiveGateway|eventBasedGateway|intermediateCatchEvent|boundaryEvent';
  const regex = new RegExp(`<bpmn:(?:${tags})\\b[^>]*\\bid="([^"]+)"`, 'g');
  return [...xml.matchAll(regex)].map((m) => m[1]);
}

const bpmnDir = path.resolve(OUT_DIR, '..', 'DIAGRAMAS', 'editables');
const p1Ids = getBpmnIds(path.join(bpmnDir, '02_TOBE_P01_registro.bpmn'));
const p2Ids = getBpmnIds(path.join(bpmnDir, '03_TOBE_P02_analisis.bpmn'));
const p3Ids = getBpmnIds(path.join(bpmnDir, '04_TOBE_P03_cierre.bpmn'));

const workflow1 = buildTobe1();
const workflow2 = buildTobe2();
validateWorkflow(workflow1, [...p1Ids, ...p2Ids]);
validateWorkflow(workflow2, [...p1Ids, ...p2Ids, ...p3Ids]);

fs.writeFileSync(
  path.join(OUT_DIR, '01_TOBE1_flujo_rpa_y_analisis_actualizado.json'),
  `${JSON.stringify(workflow1, null, 2)}\n`,
  'utf8',
);
fs.writeFileSync(
  path.join(OUT_DIR, '02_TOBE2_flujo_multiagentico_completo.json'),
  `${JSON.stringify(workflow2, null, 2)}\n`,
  'utf8',
);

console.log(JSON.stringify({
  workflow1: { nodes: workflow1.nodes.length, connections: Object.keys(workflow1.connections).length, bpmnIds: p1Ids.length + p2Ids.length },
  workflow2: { nodes: workflow2.nodes.length, connections: Object.keys(workflow2.connections).length, bpmnIds: p1Ids.length + p2Ids.length + p3Ids.length },
  countsFromBpmn: { P01: p1Ids.length, P02: p2Ids.length, P03: p3Ids.length },
}, null, 2));
