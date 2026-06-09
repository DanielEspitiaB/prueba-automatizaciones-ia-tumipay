# Decisiones técnicas y su justificación

Este documento explica **por qué** se tomó cada decisión de la solución. El
formato es: *qué se decidió · por qué · qué alternativas se descartaron*.

---

## 1. Lenguaje: Python (en vez de Make / Zapier / n8n)

- **Por qué:** la prueba evalúa *criterio técnico* y exige salida estructurada
  validada, manejo de errores diferenciado (procesada / fallida / revisión
  manual), un esquema de base de datos propio y reproducibilidad por README.
  Esos requisitos se controlan mejor con código.
- **Alternativas descartadas:** Make/Zapier/n8n son excelentes para *conectar
  apps SaaS* rápido, pero la lógica de robustez (reintentos que distinguen
  rate-limit de error real, validación de esquema, estados de error) se vuelve
  frágil y poco auditable en low-code. Además, el límite de la API del LLM es
  el mismo en cualquier herramienta, así que low-code no ahorraba ese costo.

## 2. Entrada: CSV (en vez de formulario o endpoint API)

- **Por qué:** el enunciado pide "CSV, formulario o endpoint API". El CSV es lo
  más simple, reproducible y verificable por el evaluador (un archivo en el repo).
- **Clave de diseño:** la fuente está **desacoplada** del procesamiento, así que
  añadir un endpoint API o webhook después no toca la lógica central (ver punto 11).

## 3. Validación: Pydantic

- **Por qué:** valida los campos mínimos de entrada y define contratos de datos
  claros (entrada, salida del LLM, salida final). Convierte "registro incompleto"
  en un error capturable que enruta a revisión manual.
- **Regla:** `id_solicitud` y `mensaje` son obligatorios (sin id no hay
  trazabilidad; sin mensaje no hay nada que clasificar). El resto es opcional.

## 4. LLM: Anthropic Claude (Haiku) con salida estructurada por esquema

- **Por qué Anthropic Claude:** primero se implementó con Google Gemini, pero su
  free tier imponía un límite diario que impedía corridas completas. Gracias al
  diseño desacoplado, migrar a Claude solo tocó `llm.py`. Con Claude (tier de pago)
  la solución queda **funcional de extremo a extremo** a costo de centavos.
- **Por qué el modelo Haiku:** clasificar y extraer no requiere el modelo más
  avanzado; Haiku da la mejor relación costo/calidad y demuestra control de costos.
- **Por qué salida estructurada por esquema (*tool use*):** se define una
  herramienta cuyo `input_schema` es el contrato `ResultadoLLM` y se fuerza al
  modelo a llamarla (`tool_choice`). El modelo está **obligado** a devolver un
  objeto válido; más robusto que "pedir JSON en el texto del prompt".
- **Prompt caching:** el system prompt y el esquema se cachean para abaratar el lote.
- **Por qué una sola llamada:** clasificar + extraer + resumir + responder +
  justificar en **una** petición reduce costo, latencia y puntos de fallo.
- **`datos_extraidos` como lista clave/valor:** la salida estructurada por esquema no
  admite diccionarios de claves arbitrarias; se modela como lista y se convierte a
  diccionario JSON al guardar.

## 5. Prioridad: enfoque **híbrido** (matriz en el prompt + regla dura en código)

Esta fue la decisión más deliberada. Hay tres formas de asignar la prioridad:

| Enfoque | Problema |
|---|---|
| Solo el modelo, a criterio libre | Caja negra, inconsistente, no auditable. |
| Solo reglas en código | Rígido; no entiende matices del lenguaje natural. |
| **Híbrido (elegido)** | La empresa define la política, el modelo la aplica, el código blinda lo no negociable. |

- **La política es una MATRIZ explícita** (detallada en `DISEÑO.md` §1.6), que el
  modelo aplica desde el prompt. Es un **supuesto del negocio**, declarado y
  editable: en producción se calibra con entrevistas a Operaciones/Riesgo/Servicio
  y los SLA reales.
- **Criterio central:** la prioridad se basa en **impacto y riesgo**, NUNCA en la
  urgencia que declara el cliente. Por eso un fraude reportado como "baja" termina
  en **Alta**.
- **Regla dura en código (`llm.py`):** la categoría `Riesgo / fraude` se fuerza a
  **Alta** siempre, pase lo que diga el modelo. En fintech el fraude no puede
  depender de que el modelo "acierte". El ajuste queda registrado en
  `justificacion_prioridad` para auditoría.
- **Por qué NO más reglas duras:** se evaluó forzar "Conciliación/pagos → Alta",
  pero **no todo** lo de conciliación es urgente (un reporte contable interno es
  Media). Ese matiz lo resuelve mejor el modelo dentro de la matriz.
- **Por qué NO un motor determinista de señales:** se evaluó que el LLM extrajera
  booleanos y que el código calculara la prioridad. Se descartó por sobreingeniería
  para este alcance: la parte difícil (*detectar* las señales) la sigue haciendo el
  modelo, y forzar booleanos es más frágil que dejarlo razonar de forma holística.
  Sería justificable en un sistema regulado a gran escala.

## 6. Almacenamiento: SQLAlchemy con SQLite (local) y PostgreSQL (producción)

- **Por qué SQLite en local:** cero setup, ejecución reproducible inmediata.
- **Por qué SQLAlchemy:** el **mismo código** sirve para SQLite y PostgreSQL; el
  cambio a producción es **solo** la variable `DATABASE_URL`.
- **Integración real (requisito 7.4):** la base de datos relacional es la
  integración elegida (entre API/webhook/BD/herramienta externa).
- **Diseño de la tabla:**
  - **PK sustituta** (`id` autoincremental) en vez de usar `id_solicitud`: permite
    reprocesar o recibir duplicados sin colisión.
  - **Columnas nullables** (categoría, prioridad, resumen...): un registro
    `fallida` o de revisión manual puede no tener resultado del LLM. Forzar valores
    falsos sería deshonesto.
  - **`datos_extraidos` tipo JSON:** se guarda como TEXT en SQLite y JSON en
    PostgreSQL, sin cambiar código.
  - **Columna `nota`:** guarda el motivo del fallo o de la revisión manual
    (trazabilidad / evidencia mínima).

## 7. Manejo de errores: tres estados + reintentos inteligentes

- **Tres estados separados** (requisito 8): `procesada`, `requiere revisión
  manual`, `fallida`. Cada solicitud se guarda **siempre**, nunca se pierde.
  - Entrada incompleta/inválida → revisión manual (no se llama al LLM).
  - Modelo clasifica como "Otro/..." → revisión manual.
  - Falla del LLM tras reintentos → fallida (con la causa en `nota`).
- **Transitorio vs. definitivo:** se distingue el error que conviene reintentar
  (rate-limit `429`, `5xx`, timeout) del definitivo (clave inválida, petición mal
  formada, que no se reintenta).
- **Backoff exponencial** ante errores transitorios, acotado por un tope.
- **Procesamiento secuencial:** se procesa una solicitud a la vez; hay un *throttle*
  opcional (`LLM_INTERVALO_SEG`) para limitar el ritmo si fuera necesario.
- **El lote nunca se cae:** una fila problemática no detiene el resto.

## 8. Seguridad

- `ANTHROPIC_API_KEY` y `DATABASE_URL` en `.env`, que está en `.gitignore`.
- `.env.example` documenta las variables **sin valores reales**.
- **Logs sin PII:** se registra id + estado + categoría + prioridad, nunca el
  mensaje ni el nombre del cliente.
- En producción real se usaría un *secrets manager*, cifrado en reposo/tránsito,
  minimización de datos enviados al LLM y control de acceso por roles.

## 9. Desacople del módulo de procesamiento

- `procesar_solicitud(entrada)` recibe una solicitud **ya validada** y devuelve un
  resultado. No conoce el CSV ni la base de datos.
- **Por qué:** permite cambiar la fuente (API/webhook) o el destino sin tocar la
  lógica central, y facilita testear esa unidad de forma aislada.

## 10. Docker + PostgreSQL como cierre

- **Por qué al final:** primero se prioriza una solución que corre en local sin
  fricción (SQLite). Docker se añade como **demostración** del destino de
  producción: levanta PostgreSQL + la app y procesa contra Postgres **sin cambiar
  una línea de código**, solo `DATABASE_URL`. Prueba que la decisión de
  portabilidad (punto 6) es real.

## 11. Escalabilidad (a más volumen de solicitudes)

No se implementó para no sobre-complicar la prueba, pero la ruta está clara:

- **Throttling:** autolimitar el ritmo para quedar por debajo del límite y no
  tocar el `429`.
- **Estado "pendiente" + reproceso e idempotencia:** reintentar las pendientes
  cuando la cuota se recupere, sin duplicar (deduplicar por `id_solicitud`).
- **Batching:** enviar varias solicitudes por llamada (array de resultados) reduce
  el número de peticiones ~10×; es el mayor ahorro de costo a escala.
- **Costo:** con Claude Haiku cada solicitud cuesta ~$0.002 (~$2 por 1.000). El
  modelo es configurable; se podría enrutar solo los casos difíciles a un modelo
  superior (Sonnet) manteniendo Haiku por defecto. Ver costos en `DISEÑO.md`.
