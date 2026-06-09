# Automatización de solicitudes — TUMIPAY

Prueba técnica · Ingeniero de Automatizaciones & IA.

Automatiza la **recepción, clasificación, análisis y respuesta** de solicitudes
de clientes y comercios de una fintech. Lee solicitudes desde un CSV, las analiza
con un LLM (Anthropic Claude) en una sola llamada de salida estructurada y registra
el resultado en una base de datos relacional.

---

## 1. Descripción general del flujo

El flujo tiene **tres pasos**:

1. **Ingesta:** se lee un CSV de solicitudes y cada fila se valida con Pydantic.
2. **Análisis con IA:** por cada solicitud válida se hace **una** llamada a Claude
   que, con salida JSON estructurada, devuelve a la vez: categoría, prioridad
   final, resumen, datos extraídos y respuesta sugerida.
3. **Almacenamiento:** el resultado se guarda en una base de datos relacional
   (SQLite en local, PostgreSQL en producción).

Cada solicitud termina en uno de tres estados: **procesada**, **requiere revisión
manual** o **fallida**. El lote nunca se cae por una fila problemática.

## 2. Arquitectura / diagrama del proceso

```mermaid
flowchart TD
    A["data/solicitudes_ejemplo.csv"] --> V{"Validación Pydantic<br/>campos mínimos"}
    V -- "inválida / incompleta" --> RM["Estado: requiere revisión manual<br/>(se guarda igual)"]
    V -- "válida" --> P["procesar.py · orquestador<br/>enmascara PII y enruta"]
    P --> L["llm.py · Claude Haiku<br/>1 llamada · tool use, JSON estructurado<br/>clasifica · prioriza · resume · responde"]
    L -- "falla tras reintentos" --> F["Estado: fallida<br/>(se guarda con nota)"]
    L -- "categoría = Otro / revisión manual" --> RM
    L -- "éxito" --> R["Regla de negocio: Riesgo/fraude se eleva a Alta<br/>categoría · prioridad · resumen · respuesta"]
    R --> DB[("db.py · BD relacional<br/>SQLite local / PostgreSQL prod · DATABASE_URL")]
    F --> DB
    RM --> DB
    DB --> O["data/salida_ejemplo.csv · volcado de la tabla"]
```

Diseño **desacoplado**: el módulo de procesamiento (`procesar_solicitud`) recibe
una solicitud ya validada y devuelve un resultado; no conoce el CSV ni la base de
datos. Esto permite cambiar la fuente (API, webhook, formulario) o el destino sin
tocar la lógica central.

## 3. Herramientas, lenguajes y librerías

| Componente        | Elección                          | Por qué                                        |
|-------------------|-----------------------------------|------------------------------------------------|
| Lenguaje          | Python 3.11+                      | Claridad, ecosistema de datos e IA.            |
| Validación        | **Pydantic 2**                    | Contratos de datos y validación de entrada/salida. |
| LLM               | **Anthropic Claude** (`anthropic`)| Salida estructurada vía *tool use*; modelo Haiku (económico). |
| ORM / BD          | **SQLAlchemy 2**                  | Mismo código para SQLite y PostgreSQL.         |
| BD local          | **SQLite**                        | Cero setup, ejecución reproducible.            |
| BD producción     | **PostgreSQL** (`psycopg2-binary`)| Estándar relacional; solo cambia `DATABASE_URL`.|
| Config / secretos | **python-dotenv**                 | Variables sensibles fuera del código.          |

Estructura del repositorio:

```
data/solicitudes_ejemplo.csv   # entrada de ejemplo
data/salida_ejemplo.csv        # salida de ejemplo (volcado de la tabla)
src/models.py                  # modelos Pydantic (contratos de datos)
src/db.py                      # ORM SQLAlchemy + conexión por DATABASE_URL
src/llm.py                     # integración con Claude (salida estructurada)
src/procesar.py                # orquestador del flujo (CSV -> IA -> BD)
.env.example                   # variables esperadas (sin valores)
requirements.txt               # dependencias
docker-compose.yml             # PostgreSQL + app (opción producción)
Dockerfile                     # imagen de la app
```

## 4. Instalación y ejecución

Requisito: Python 3.11 o superior.

```bash
# 1. Crear y activar entorno virtual
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar variables de entorno
cp .env.example .env        # (Windows: copy .env.example .env)
# Editar .env y poner tu ANTHROPIC_API_KEY

# 4. Ejecutar el flujo con el CSV de ejemplo
python src/procesar.py

# (opcional) usar otro CSV
python src/procesar.py ruta/a/mis_solicitudes.csv
```

Al terminar verás un resumen por estado y se generará `data/salida_ejemplo.csv`.
La base de datos local queda en `solicitudes.db`.

## 5. Variables de entorno

| Variable             | Descripción                                          | Ejemplo |
|----------------------|------------------------------------------------------|---------|
| `ANTHROPIC_API_KEY`  | Clave de la consola de Anthropic. **Obligatoria.**   | `sk-ant-...` |
| `ANTHROPIC_MODEL`    | Modelo a usar (Haiku = económico).                   | `claude-haiku-4-5-20251001` |
| `LLM_INTERVALO_SEG`  | Pausa entre llamadas (0 = sin throttle).             | `0` |
| `DATABASE_URL`       | Cadena de conexión. SQLite por defecto.              | `sqlite:///solicitudes.db` |

Para producción solo se cambia `DATABASE_URL`:
`postgresql+psycopg2://usuario:password@host:5432/tumipay`

La clave se obtiene en https://console.anthropic.com/ (sección API Keys).

## 6. Ejemplo de entrada y de salida

**Entrada** (`data/solicitudes_ejemplo.csv`):

```csv
id_solicitud,fecha,canal,tipo_cliente,nombre_cliente,mensaje,prioridad_reportada
SOL-003,2026-06-02,WhatsApp,cliente final,Carlos Ruiz,"Vi un cobro de $480.000 ... creo que clonaron mi tarjeta, bloquéenla ya.",baja
```

**Salida** (registro en la tabla / `data/salida_ejemplo.csv`):

| Campo | Valor |
|-------|-------|
| categoria | `Riesgo / fraude` |
| prioridad_final | `Alta` (reportada era `baja`) |
| resumen | El cliente reporta un cobro no reconocido de $480.000 y sospecha clonación... |
| datos_extraidos | `{"monto_cobro_no_reconocido": "$480.000", "tipo_incidente": "tarjeta clonada", ...}` |
| respuesta_sugerida | Estimado Carlos Ruiz, hemos procedido con el bloqueo inmediato... |
| estado_procesamiento | `procesada` |

Resultado del lote de ejemplo (10 solicitudes): **8 procesadas, 2 requieren
revisión manual, 0 fallidas**.

## 7. Uso del LLM y diseño del prompt

Se usa **una sola llamada** por solicitud que concentra clasificación, extracción,
resumen y respuesta. La salida es **estructurada por esquema** mediante *tool use*:
se define una herramienta (`registrar_analisis`) cuyo `input_schema` es el contrato
`ResultadoLLM`, y se fuerza al modelo a llamarla (`tool_choice`). No se pide JSON en
el texto del prompt: el modelo está obligado a devolver un objeto que valida contra
el esquema. El system prompt y el esquema se **cachean** (prompt caching) para
abaratar las llamadas del lote.

**Modelo:** Claude Haiku (configurable por `ANTHROPIC_MODEL`). Para clasificar y
extraer no se necesita el modelo más avanzado; Haiku da la mejor relación
costo/calidad (ver costos en `DISEÑO.md`).

El *system prompt* (en `src/llm.py`) instruye al modelo a:
1. Clasificar en una de las 6 categorías cerradas (con definición de cada una).
2. Asignar prioridad final según el **contenido y el riesgo**, no según lo reportado.
3. Resumir en una o dos frases.
4. Extraer datos clave como pares clave/valor (sin inventar).
5. Proponer una respuesta cordial, profesional y accionable en español.
6. Justificar la prioridad asignada.

**Prioridad híbrida (decisión de diseño):** la prioridad sigue una **matriz de
priorización** explícita (un supuesto del negocio, detallada en `DISEÑO.md` §1.6)
que el modelo aplica desde el prompt; y una **regla de negocio en código** garantiza
que la categoría `Riesgo / fraude` nunca quede por debajo de `Alta`. En fintech el
fraude no puede despriorizarse por criterio del modelo ni del cliente. El ajuste
queda registrado en `justificacion_prioridad` para trazabilidad.

`datos_extraidos` se modela como una **lista de pares clave/valor** porque la salida
estructurada por esquema requiere un esquema cerrado (no admite objetos con claves
arbitrarias); al guardar se convierte a un diccionario JSON.

**Prompt real** (constante `SYSTEM_INSTRUCTION` en `src/llm.py`, verbatim):

```text
Eres un asistente de operaciones de TUMIPAY, una fintech. Tu tarea es analizar
solicitudes entrantes de clientes y comercios y registrar un análisis estructurado
llamando a la herramienta 'registrar_analisis'.

Debes determinar todo lo siguiente:

1. CATEGORÍA (exactamente una):
   - "Soporte técnico": fallas de la app, terminal/POS, errores de acceso, bugs.
   - "Solicitud comercial": ventas, afiliación, planes, comisiones, alianzas.
   - "Riesgo / fraude": cobros no reconocidos, tarjetas clonadas, phishing, suplantación.
   - "Conciliación / pagos": dinero no reflejado, cuadres, liquidaciones, transferencias pendientes, facturas.
   - "Actualización de datos": cambio de correo, teléfono, datos personales o del comercio.
   - "Otro / requiere revisión manual": mensajes ambiguos, vacíos de contexto o que no encajan claramente.

2. PRIORIDAD FINAL ("Alta", "Media" o "Baja"). NO copies la prioridad reportada por
   el cliente; aplica esta MATRIZ DE PRIORIZACIÓN del negocio. Gana la condición
   más alta que aplique:
   - ALTA si se cumple alguna de estas señales:
       * riesgo de seguridad: fraude, suplantación, acceso no autorizado, phishing;
       * fondos en riesgo: dinero perdido, retenido, cobrado de más o no reflejado;
       * bloqueo operativo total: un comercio que no puede vender o un cliente sin
         acceso a su cuenta.
   - MEDIA si: afecta el servicio o el uso pero SIN pérdida de dinero ni bloqueo
     total; o es un trámite/actualización con impacto en la cuenta.
   - BAJA si: es una consulta informativa, comercial o sin impacto operativo.

3. RESUMEN: una o dos frases claras y neutrales.

4. DATOS EXTRAÍDOS: pares clave/valor presentes en el mensaje (por ejemplo: monto,
   fecha_evento, id_transaccion, correo_nuevo, telefono, canal_afectado). Solo lo
   que aparezca explícitamente; no inventes.

5. RESPUESTA SUGERIDA: cordial, profesional, en español, accionable y sin prometer
   nada que no se pueda cumplir.

6. JUSTIFICACIÓN DE LA PRIORIDAD: por qué asignaste esa prioridad final.

Responde siempre en español.
```

El **esquema de salida** es el `input_schema` de la herramienta `registrar_analisis`
(constante `HERRAMIENTA` en `src/llm.py`), que el modelo está obligado a llenar
mediante *tool use*; así documentación y código no se desincronizan.

## 8. Almacenamiento

La integración real del flujo es una **base de datos relacional** vía SQLAlchemy.
Tabla `solicitudes_procesadas`:

| Columna | Tipo | Notas |
|---------|------|-------|
| `id` | entero | PK sustituta autoincremental (permite reprocesos). |
| `id_solicitud` | texto | Identificador de negocio, indexado. |
| `mensaje` | texto | Solicitud original (insumo) con la PII de alto riesgo (tarjetas/cuentas) **enmascarada** antes de guardar; sirve para auditar la clasificación. |
| `canal` | texto | Canal de recepción (insumo). |
| `tipo_cliente` | texto | Tipo de cliente (insumo). |
| `nombre_cliente` | texto | Nombre del cliente/comercio (insumo). |
| `fecha_recepcion` | texto | Fecha original de la solicitud (insumo). |
| `prioridad_reportada` | texto | Prioridad que indicó el cliente — para comparar con `prioridad_final`. |
| `categoria` | texto | Nullable (vacío si falla/revisión manual). |
| `prioridad_final` | texto | `Alta` / `Media` / `Baja`. |
| `resumen` | texto | Resumen del modelo. |
| `datos_extraidos` | JSON | Funciona como TEXT en SQLite y JSON en PostgreSQL. |
| `respuesta_sugerida` | texto | Respuesta propuesta. |
| `justificacion_prioridad` | texto | Por qué esa prioridad (incluye reglas de negocio). |
| `estado_procesamiento` | texto | `procesada` / `requiere revisión manual` / `fallida`. Indexado. |
| `fecha_procesamiento` | fecha-hora | Cuándo se procesó. |
| `nota` | texto | Motivo de fallo o de revisión manual (trazabilidad). |

**SQLite vs PostgreSQL:** se usa SQLite en local por cero setup y reproducibilidad.
El paso a PostgreSQL no cambia el código, solo `DATABASE_URL` (ver "Ejecución con
Docker + PostgreSQL").

### Ejecución con Docker + PostgreSQL

Demuestra el destino de producción (PostgreSQL) sin cambiar una línea de código:
solo cambia `DATABASE_URL`. Requiere Docker y un `.env` con `ANTHROPIC_API_KEY`.

```bash
docker compose up --build
```

`docker-compose.yml` levanta dos servicios: `db` (PostgreSQL 16, con healthcheck) y
`app` (esta aplicación). El servicio `app` arranca cuando la base está lista y
recibe `DATABASE_URL=postgresql+psycopg2://tumipay:tumipay@db:5432/tumipay`. La
carpeta `./data` se monta para usar el CSV de entrada y escribir la salida.

## 9. Manejo de errores y validaciones

- **Registros incompletos/ inválidos:** si una fila no pasa la validación (p. ej.
  `mensaje` vacío), no se llama al LLM; se guarda en estado **requiere revisión
  manual** con la nota del campo problemático. *(El lote no se detiene.)*
- **Casos ambiguos:** si el modelo clasifica como `Otro / requiere revisión
  manual`, el registro queda en ese mismo estado.
- **Falla del LLM:** se distingue el **error transitorio** (rate-limit `429`, `5xx`,
  timeout) del **error definitivo** (clave inválida, petición mal formada). Los
  transitorios se **reintentan con backoff exponencial**. Si tras los reintentos
  sigue fallando, la solicitud queda **fallida** con la causa en `nota` y se
  registra en el log.
- **Procesamiento secuencial:** las solicitudes se procesan una a una; existe un
  *throttle* opcional (`LLM_INTERVALO_SEG`) para limitar el ritmo si hiciera falta.
- **Logs / evidencia:** cada solicitud registra su id, estado, categoría y
  prioridad. Los estados quedan separados y consultables en la tabla.

## 10. Consideraciones de seguridad y privacidad

**Secretos:**
- `ANTHROPIC_API_KEY` y `DATABASE_URL` viven en `.env`, que está en `.gitignore` —
  git nunca lo rastrea, así que no llega al repositorio. Solo se sube `.env.example`
  (plantilla sin valores reales).
- No se suben claves, tokens ni credenciales. Una clave que toca un canal no seguro
  se considera comprometida y se **rota**.

**Datos sensibles (PII):**
- **Enmascaramiento implementado** (`src/seguridad.py`): antes de enviar el mensaje
  al LLM (un tercero) y antes de guardarlo, se enmascaran los identificadores
  financieros de alto riesgo — **números de tarjeta (PAN) y de cuenta bancaria** —,
  conservando solo los últimos 4 dígitos. No se enmascaran nombres ni montos porque
  el modelo los necesita para clasificar (enmascarar de más degrada el resultado).
- Los **logs no contienen PII**: registran id, estado, categoría y prioridad, nunca
  el mensaje ni el nombre.

**En un entorno fintech real**, además, aplicarían los estándares de la industria:

| Estándar | Aplicación |
|----------|------------|
| **PCI DSS** | Datos de tarjeta nunca en claro: tokenización/cifrado (aquí se enmascaran). |
| **Ley 1581 / Habeas Data (CO)** + GDPR | Consentimiento, finalidad, minimización, derecho a acceso/borrado, retención. |
| **SOC 2 / ISO 27001** | Acceso por roles (RBAC), least privilege, auditoría, gestión de cambios. |
| **Cifrado** | TLS en tránsito; AES-256 en reposo (BD cifrada). |
| **Gestión de secretos** | *Secrets manager* (Vault, AWS Secrets Manager, KMS) en vez de `.env`. |
| **Detección de PII** | Servicio dedicado (Microsoft Presidio, AWS Comprehend, Google DLP) en vez de regex. |
| **DPA con el proveedor de IA** | Acuerdo de tratamiento + garantía de no usar los datos para entrenamiento. |
| **Residencia de datos** | Mantener los datos en la región (LatAm). |

## 11. Limitaciones conocidas y mejoras futuras

- **Costo por volumen:** cada solicitud es una llamada al LLM (~$0.002 con Haiku);
  a gran escala conviene *batching* (varias solicitudes por llamada). Ver `DISEÑO.md`.
- **Datos extraídos redundantes:** el modelo a veces repite metadatos de la entrada
  (canal, nombre) dentro de `datos_extraidos`; se puede afinar el prompt.
- **Ingesta:** hoy solo CSV. La arquitectura desacoplada permite añadir un endpoint
  API o webhook reutilizando `procesar_solicitud` sin tocar la lógica.
- **Sin tests automatizados:** se validó con smoke tests manuales; un siguiente paso
  sería `pytest` con la capa LLM mockeada.
- **Idempotencia:** hoy un mismo CSV reprocesado genera filas nuevas; se podría
  deduplicar por `id_solicitud`.
- **Respuesta automática:** hoy se sugiere la respuesta; en producción podría
  enviarse por el canal de origen (correo/WhatsApp) con aprobación humana.
