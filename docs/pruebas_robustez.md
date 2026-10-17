# Informe de pruebas de robustez

Resultado de la última ejecución: **182 pruebas aprobadas en 30,04 segundos**.
La suite anterior tenía 57 casos; se añadieron 125 casos parametrizados y pruebas
de regresión. No se calculó un porcentaje de cobertura.

## Comprobaciones ejecutadas

Todos los comandos siguientes están registrados en `pyproject.toml`:

| Comando | Resultado |
| --- | --- |
| `python -m pytest -q` | 182 aprobadas |
| `python -m ruff check .` | Sin errores |
| `python -m ruff format --check .` | 47 archivos con formato correcto |
| `python -m mypy nucleo rpa ingesta decision mcp_server agentes web tests` | 36 archivos, sin errores |
| `python -m compileall -q nucleo rpa ingesta decision mcp_server agentes web tests` | Correcto |
| `python scripts/validar_fase1.py` | 1.158 comprobaciones correctas |

Se utilizó el Python 3.12 del entorno incluido mediante PATH temporal, porque el
comando Python del sistema apunta al acceso de Microsoft Store.

## Escenarios cubiertos

- Los diez campos P01 ausentes, vacíos, nulos, con listas, objetos y booleanos.
- Importes negativos, cero, exceso de decimales, NaN, infinitos, exponentes enormes
  y los extremos válidos de precisión.
- JSON mal formado, cuerpos de tipo incorrecto, campos extra, Unicode roto,
  texto invisible, exceso de longitud y los 62 controles C0/C1 no admitidos.
- Texto con HTML, SQL e instrucciones para intentar aprobar o firmar; permanece
  como contenido, sin gobernar transiciones.
- Aislamiento entre expedientes, citas documentales y rechazo de identificadores
  inexistentes sin sustituirlos por otro expediente.
- Dos aprobaciones concurrentes: una aceptada y una rechazada.
- Ocho registros concurrentes: una creación y siete duplicados, sin segunda alta.
- Acciones fuera de orden, respuestas repetidas, decisiones incompatibles y
  actores no permitidos; conservan el estado pendiente.
- Autorización humana antes de recibir la subsanación, incremento documental y
  continuación del análisis.
- Respuesta de cliente no autenticada durante la espera: se rechaza y luego
  permite continuar con una respuesta válida.
- Intentos de modificar expediente, versión o nodo durante la reanudación.
- Ausencia de kárdex, excepción y recuperación posterior con kárdex explícito.
- Las pruebas existentes mantienen caminos felices, errores de borde, compuerta
  PII, RAG, idempotencia, temporizadores y los siete puntos HITL.

## Fallos corregidos durante estas pruebas

1. Unicode mal formado rompía la serialización de errores. Las respuestas de
   validación ya no reflejan la entrada ni nombres arbitrarios que puedan tener PII.
2. El botón de documentos autorizaba automáticamente una tarea humana. Ahora exige
   autorización previa desde la bandeja y acepta documentos en la espera correcta.
3. Un expediente desconocido mostraba el primero por defecto. Ahora devuelve 404.
4. La asistente podía resolver revisión y firma de P03; ahora se exige al titular.
5. La espera del cliente podía reanudarse con autenticación falsa. Se valida antes
   de invocar el grafo y no se convierten cadenas o números a booleanos.
6. Una actualización de reanudación podía alterar el expediente del checkpoint.
   Solo se admite la actualización explícita del kárdex.
7. Algunos controles al final del texto se limpiaban antes de validarse. Se
   comprueba ahora el texto original.
8. Reglas antiguas de modo oscuro dejaban texto sin contraste; se retiraron.
9. Las solicitudes inválidas se contaban como activas y las trazas sembradas
   marcaban etapas futuras como completadas. Se corrigieron ambos indicadores.
10. Se ajustó el salto de líneas de mensajes y referencias largas y se vincularon
    las etiquetas de responsable y decisión con sus controles.

## Pruebas en navegador

Se usó `python scripts/servidor_pruebas_web.py`, con una base temporal independiente
y escucha local en el puerto 8011. No se operó sobre la base habitual de la demo.

Se comprobaron mensajes con espacios y HTML literal, consultas invisibles, cambio
de expediente, rechazo de subsanación sin autorización, revisión, conformidad,
firma con espacios rechazada y cierre con kárdex sintético `KB-PRUEBA-UI`.
Se inspeccionaron tres columnas en escritorio y una en móvil, sin desbordamiento
horizontal del documento; el texto de consulta conserva contraste con el fondo.
La captura `pruebas-interfaz.png` muestra la interfaz final con datos de prueba.

## Límites reales

- Es una demo local: elegir un responsable en un desplegable no constituye un
  inicio de sesión real. El indicador tipado `autenticado` necesita provenir de
  una identidad verificada antes de usar el producto con usuarios reales.
- Los grafos usan `MemorySaver`; no se acreditó recuperación de checkpoints tras
  terminar el proceso. La persistencia SQLite de datos no sustituye esa capacidad.
- No se probaron WhatsApp, Drive, Sheets, notaría ni proveedores LLM reales.
  Las pruebas usan adaptadores y datos deterministas sin red.
- No se realizó una prueba de carga prolongada, auditoría de seguridad completa,
  recuperación ante pérdida de disco ni matriz de todos los navegadores.

Estos resultados describen escenarios reproducibles; no garantizan ausencia de
defectos para cualquier entrada o entorno imaginable.
