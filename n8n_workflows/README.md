# Workflows n8n · FASE 1

Importe desde n8n **uno** de estos archivos JSON:

- `01_TOBE1_flujo_rpa_y_analisis_actualizado.json`: P01 + P02, 58 nodos n8n y
  cobertura de los 44 IDs BPMN de ambos procesos.
- `02_TOBE2_flujo_multiagentico_completo.json`: P01 + P02 + P03, 108 nodos n8n y
  cobertura de los 58 IDs BPMN reales.

Los exports están inactivos y no contienen secretos ni IDs de credenciales. Antes de
activarlos, configure las variables indicadas en sus notas adhesivas. En TO-BE 2,
asigne una credencial PostgreSQL a cada nodo `BD · …`.

Los nodos HTTP esperan adaptadores internos para Sheets, Drive, runtime de agentes,
orquestador, WhatsApp y Notaría. Sus contratos mínimos están documentados dentro de
cada nodo. Los siete controles humanos se materializan con nodos Wait reanudables;
P5D y PT72H se implementan como límite de espera, y el backoff usa tres Wait de 2,
4 y 8 segundos.

Para regenerar ambos exports desde los BPMN:

```powershell
node n8n_workflows/_generate_workflows.js
```

Para validarlos sin instalar n8n ni dependencias Python:

```powershell
python scripts/validar_fase1.py
```

Nota de sincronización: el prompt menciona 57 nodos TO-BE, pero los BPMN vigentes
contienen 58 (22 P01 + 22 P02 + 14 P03). Los exports siguen el XML.
