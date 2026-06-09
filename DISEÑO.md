# Diseño de la solución — Automatización de solicitudes TUMIPAY

Documento de pensamiento estructurado en cuatro fases: entendimiento y diseño,
planteamiento y diagramas, implementación y justificación (incluido costo), y
verificación de entregables.

---

# Fase 1 — Entendimiento y diseño

## 1.1 Problema

TUMIPAY (fintech) recibe solicitudes de clientes y comercios por varios canales.
Hoy la clasificación, análisis y respuesta inicial son **manuales**: hay reprocesos,
tiempos variables y poca trazabilidad. Se requiere **automatizar** la recepción,
clasificación, extracción de información, registro y propuesta de respuesta.

## 1.2 Requisitos funcionales (RF)

| ID | Requisito |
|----|-----------|
| RF1 | Ingerir solicitudes desde una fuente estructurada (CSV). |
| RF2 | Validar campos mínimos y manejar registros incompletos/ inválidos. |
| RF3 | Clasificar cada solicitud en una de las 6 categorías cerradas (LLM). |
| RF4 | Asignar prioridad final (Alta/Media/Baja) **justificada por el contenido**. |
| RF5 | Extraer información clave del mensaje (datos estructurados). |
| RF6 | Resumir la solicitud. |
| RF7 | Generar una respuesta sugerida para el cliente/comercio. |
| RF8 | Registrar el resultado estructurado en una base de datos relacional. |
| RF9 | Exportar una salida consultable (CSV de resultados). |
| RF10 | Clasificar cada solicitud por estado: procesada / revisión manual / fallida. |

## 1.3 Requisitos no funcionales (RNF)

| ID | Requisito | Cómo se cumple |
|----|-----------|----------------|
| RNF1 Reproducibilidad | Otra persona lo ejecuta siguiendo el README. | venv + requirements.txt + README + CSV de ejemplo. |
| RNF2 Seguridad | No exponer secretos ni PII. | `.env` en `.gitignore`, `.env.example`, logs sin PII. |
| RNF3 Robustez | El lote no se cae ante errores. | 3 estados, reintentos, distinción rate-limit vs error real. |
| RNF4 Portabilidad de datos | Local y producción sin reescribir. | SQLite ↔ PostgreSQL cambiando solo `DATABASE_URL`. |
| RNF5 Salida controlada | Resultado del LLM estructurado. | *Tool use* con esquema cerrado (no JSON en texto). |
| RNF6 Trazabilidad | Saber qué pasó con cada solicitud. | Estado + columna `nota` + logs de avance. |
| RNF7 Costo controlado | Uso responsable de IA. | Modelo económico (Haiku), 1 llamada, prompt caching. |
| RNF8 Mantenibilidad | Cambiar fuente/destino/LLM sin romper. | Módulos desacoplados; cambiar de Gemini a Claude solo tocó `llm.py`. |

## 1.4 Roles (actores)

| Rol | Descripción | Relación con el sistema |
|-----|-------------|--------------------------|
| **Cliente final / Comercio / Aliado / Interno** | Emisores de solicitudes. | Generan la entrada (mensaje). |
| **Agente de operaciones** | Atiende solicitudes. | Consume resultados ya clasificados y priorizados; envía la respuesta. |
| **Analista de riesgo/fraude** | Atiende casos sensibles. | Recibe lo marcado como `Riesgo / fraude` (siempre prioridad Alta). |
| **Supervisor / revisor** | Revisa lo ambiguo. | Trabaja la cola de "requiere revisión manual". |
| **Ingeniero / operador del sistema** | Ejecuta y mantiene. | Corre el flujo, monitorea logs, reprocesa fallidas. |
| **El sistema (automatización)** | Actor automático. | Clasifica, extrae, prioriza, responde y registra. |

## 1.5 Historias de usuario

**Agente de operaciones**
- Como agente, quiero que las solicitudes lleguen **ya clasificadas y priorizadas**
  para atender primero lo crítico y no leer todo manualmente.
- Como agente, quiero una **respuesta sugerida** lista para revisar y enviar, para
  responder más rápido y de forma consistente.
- Como agente, quiero ver las solicitudes de **revisión manual separadas** de las
  procesadas, para enfocar mi esfuerzo donde la IA no fue suficiente.

**Analista de riesgo/fraude**
- Como analista, quiero que **todo lo de fraude entre como prioridad Alta**
  automáticamente, para no perder ningún caso por una mala clasificación.

**Supervisor / revisor**
- Como supervisor, quiero saber **por qué** una solicitud quedó en revisión manual
  o fallida (campo `nota`), para resolverla rápido.

**Ingeniero / operador**
- Como operador, quiero que **un registro inválido no detenga el lote**, para que un
  dato malo no bloquee a los demás clientes.
- Como operador, quiero que ante una caída del LLM el sistema **reintente y, si no,
  registre el fallo** sin perder la solicitud.
- Como operador, quiero **ejecutarlo de forma reproducible** y poder cambiar a
  PostgreSQL en producción sin reescribir código.

**Responsable de datos / seguridad**
- Como responsable, quiero que **no se expongan claves ni datos personales** en el
  repositorio ni en los logs.

## 1.6 Matriz de priorización (SUPUESTO del negocio)

La prioridad final no se asigna al azar ni "a criterio del modelo": sigue una
**matriz de priorización** explícita. Gana la condición más alta que aplique:

| Señal en la solicitud | Prioridad | Quién decide |
|-----------------------|-----------|--------------|
| Riesgo de seguridad / fraude / suplantación / phishing | **Alta** | 🔒 Regla determinista en código (no negociable) |
| Fondos en riesgo (dinero perdido, retenido, cobrado de más, no reflejado) | **Alta** | Matriz (aplicada por el modelo) |
| Bloqueo operativo total (comercio sin vender / cliente sin acceso) | **Alta** | Matriz |
| Afecta el servicio sin pérdida de dinero ni bloqueo total | **Media** | Matriz |
| Trámite / actualización con impacto en la cuenta | **Media** | Matriz |
| Consulta informativa, comercial o sin impacto operativo | **Baja** | Matriz |

> **Esto es un SUPUESTO nuestro.** En un proyecto real, esta matriz se construiría
> con el negocio: entrevistas con Operaciones, Riesgo y Servicio al Cliente, los SLA
> reales por categoría y el costo relativo de los errores (un falso negativo en
> fraude es mucho más caro que saturar la cola de Media). Se deja **explícita,
> versionada y editable** para que el negocio la ajuste según su insight, sin tocar
> la lógica del flujo.

**Por qué se aplica en el prompt y no como motor determinista de señales.** Se
evaluó que el LLM extrajera señales booleanas y que una matriz en código calculara
la prioridad. Se descartó para este alcance porque: (1) la parte difícil —*detectar*
las señales— la sigue haciendo el modelo, así que la "determinación" sería en parte
aparente; (2) forzar booleanos es más frágil que dejar al modelo razonar la
prioridad de forma holística; y (3) añade complejidad que el enunciado pide evitar.
Lo verdaderamente crítico (fraude → Alta) **sí** es una regla determinista en
código. El motor de señales sería justificable en un sistema **regulado a gran
escala** donde haya que probar que la regla de decisión es fija.

---

# Fase 2 — Planteamiento y diagramas

## 2.1 Enfoque

Pipeline de **tres pasos** con un módulo de procesamiento **desacoplado** de la
fuente y del almacenamiento:

```
CSV de entrada ──▶ Validación (Pydantic) ──▶ Seguridad (enmascarado PII) ──▶
       LLM (1 llamada, salida estructurada) ──▶ Reglas de negocio ──▶
       Persistencia (BD relacional) ──▶ Salida CSV
```

## 2.2 Arquitectura por capas

La solución está organizada en capas con responsabilidad única; cada una se puede
cambiar sin afectar a las demás (p. ej. la migración de Gemini a Claude solo tocó
la capa de IA).

```
┌─────────────────────────────────────────────────────────────────┐
│  1. INGESTA            procesar.py (lectura CSV)                  │  ← fuente intercambiable
├─────────────────────────────────────────────────────────────────┤
│  2. VALIDACIÓN         models.py (Pydantic)                       │  ← inválido → revisión manual
├─────────────────────────────────────────────────────────────────┤
│  3. SEGURIDAD / PII    seguridad.py (enmascaramiento)             │  ← antes de salir a terceros
├─────────────────────────────────────────────────────────────────┤
│  4. INTELIGENCIA (IA)  llm.py (Claude · tool use · reintentos)    │  ← clasifica/extrae/responde
├─────────────────────────────────────────────────────────────────┤
│  5. REGLAS DE NEGOCIO  llm.py (regla fraude) + procesar.py (ruteo)│  ← matriz + estados
├─────────────────────────────────────────────────────────────────┤
│  6. PERSISTENCIA       db.py (SQLAlchemy → SQLite / PostgreSQL)   │  ← integración real
├─────────────────────────────────────────────────────────────────┤
│  7. SALIDA / CONSULTA  procesar.py (export CSV) · consultar_db.py │  ← resultados utilizables
└─────────────────────────────────────────────────────────────────┘
   Transversales: configuración (.env) · trazabilidad/logging sin PII ·
                  contratos de datos compartidos (models.py)
```

| Capa | Archivo | Responsabilidad |
|------|---------|-----------------|
| 1. Ingesta | `procesar.py` | Lee la fuente (CSV) fila a fila. Desacoplada: intercambiable por API/webhook. |
| 2. Validación | `models.py` | Valida campos mínimos y normaliza; lo inválido va a revisión manual. |
| 3. Seguridad/PII | `seguridad.py` | Enmascara tarjetas/cuentas antes de enviar a terceros o guardar. |
| 4. Inteligencia | `llm.py` | Clasifica, extrae, resume y responde con Claude (1 llamada, tool use); reintentos. |
| 5. Reglas de negocio | `llm.py` + `procesar.py` | Matriz de prioridad, regla dura fraude→Alta, ruteo de estados. |
| 6. Persistencia | `db.py` | Guarda en BD relacional (SQLite local / PostgreSQL prod) por `DATABASE_URL`. |
| 7. Salida/consulta | `procesar.py`, `consultar_db.py` | Exporta CSV de resultados y permite consultar la BD. |

## 2.3 Diagrama de componentes

```
┌──────────────┐   ┌───────────────────────────────┐   ┌──────────────┐
│  data/*.csv  │──▶│         procesar.py            │──▶│    db.py     │
│  (entrada)   │   │  orquestador (lee, valida,     │   │ SQLAlchemy   │
└──────────────┘   │  enruta estados, persiste)     │   │ ORM          │
                   └───────────────┬───────────────┘   └──────┬───────┘
                                   │                          │
                          ┌────────▼────────┐         ┌───────▼────────┐
                          │     llm.py      │         │  SQLite (local)│
                          │ Claude (Haiku)  │         │  PostgreSQL    │
                          │ tool use → JSON │         │  (producción)  │
                          └────────┬────────┘         └────────────────┘
                                   │
                          ┌────────▼────────┐
                          │    models.py    │  (contratos Pydantic compartidos)
                          └─────────────────┘
```

## 2.4 Diagrama de secuencia (una solicitud)

```
procesar.py        models.py          llm.py (Claude)        db.py
    │  fila CSV        │                   │                    │
    │ ───validar──────▶│                   │                    │
    │ ◀─SolicitudEntrada (o ValidationError)                    │
    │                                       │                    │
    │ ──── clasificar_solicitud ───────────▶│                    │
    │                                       │ generate (tool)    │
    │                                       │ ─── 1 llamada ───▶ Anthropic
    │                                       │ ◀── ResultadoLLM ──│
    │ ◀──── ResultadoLLM (+regla fraude) ───│                    │
    │ ──────────── guardar_resultado ───────────────────────────▶│
    │ ◀──────────────── id ──────────────────────────────────────│
```

## 2.5 Diagrama de estados

```
                 ┌─────────────┐
   fila CSV ────▶│  validación  │
                 └──────┬───────┘
            inválida │       │ válida
                     ▼       ▼
        ┌────────────────┐  ┌──────────────┐
        │ revisión manual│  │ llamada LLM  │
        └────────────────┘  └──────┬───────┘
                          éxito │       │ falla tras reintentos
                       ┌────────▼──┐  ┌──▼───────┐
              "Otro/…" │ procesada │  │ fallida  │
                  │    └───────────┘  └──────────┘
                  ▼
         ┌────────────────┐
         │ revisión manual│
         └────────────────┘
```

## 2.6 Modelo de datos (tabla `solicitudes_procesadas`)

Se guarda **el insumo y el resultado juntos** para trazabilidad:

- **Insumo (solicitud original):** `id_solicitud`, `mensaje`, `canal`,
  `tipo_cliente`, `nombre_cliente`, `fecha_recepcion`, `prioridad_reportada`.
- **Resultado del análisis:** `categoria`, `prioridad_final`, `resumen`,
  `datos_extraidos` (JSON), `respuesta_sugerida`, `justificacion_prioridad`,
  `estado_procesamiento`, `fecha_procesamiento`, `nota`.
- `id` (PK sustituta autoincremental).

Guardar la solicitud original permite **auditar la clasificación** (ver el mensaje
junto al resultado) y **comparar `prioridad_reportada` vs. `prioridad_final`**
(medir cuándo y por qué el sistema corrige la prioridad del cliente). (Detalle de
tipos en README §8.)

## 2.7 Decisión de proveedor LLM

Se diseñó la capa LLM **desacoplada** detrás de `clasificar_solicitud(entrada)`.
Esto permitió **migrar de Google Gemini a Anthropic Claude tocando solo `llm.py`**,
sin cambiar `models.py`, `db.py` ni `procesar.py`. Motivo del cambio: el free tier
de Gemini imponía un límite diario que impedía corridas completas; con Claude (tier
de pago, modelo Haiku) la solución queda **funcional de extremo a extremo** a un
costo de centavos.

---

# Fase 3 — Implementación y justificación

## 3.1 Módulos

| Archivo | Responsabilidad |
|---------|-----------------|
| `src/models.py` | Contratos de datos (Pydantic): entrada, salida del LLM, salida final, enums. |
| `src/db.py` | ORM SQLAlchemy; conexión por `DATABASE_URL`; `init_db`, `guardar_resultado`. |
| `src/llm.py` | Integración con Claude: 1 llamada con *tool use*, reintentos, regla de negocio. |
| `src/procesar.py` | Orquestador: lee CSV, valida, enruta estados, persiste, exporta. |

## 3.2 Justificación de las decisiones clave

El detalle completo está en **`DECISIONES.md`**. Resumen:

- **Salida estructurada por esquema (tool use)**, no "pedir JSON en el texto":
  garantiza un objeto válido y evita parseos frágiles.
- **Una sola llamada** que clasifica + extrae + resume + responde + justifica:
  menos costo, menos latencia, menos puntos de fallo.
- **Prioridad híbrida**: el modelo propone; una **regla dura en código** fuerza
  `Riesgo/fraude → Alta`. Lo no negociable no depende del modelo.
- **SQLite local / PostgreSQL producción** con el mismo código (solo `DATABASE_URL`).
- **Tres estados** y columna `nota`: trazabilidad y robustez (el lote no se cae).
- **Desacople**: por eso cambiar de proveedor de IA fue trivial.

## 3.3 Costos del uso de IA

Cada solicitud = **una** llamada a Claude. Con **Claude Haiku** y **prompt
caching** del system prompt + esquema de herramienta:

| Concepto | Tokens aprox. | Precio (Haiku) |
|----------|---------------|----------------|
| Entrada (system+tool, cacheado) | ~700 | $0.80/M (lectura caché $0.08/M) |
| Entrada (mensaje) | ~150 | $0.80/M |
| Salida (JSON estructurado) | ~400 | $4.00/M |
| **Total por solicitud** | — | **≈ $0.002** |

Estimaciones:

| Volumen | Costo aproximado |
|---------|------------------|
| 10 solicitudes (lote de ejemplo) | **≈ $0.02** |
| 1.000 solicitudes | ≈ $2 |
| 10.000 solicitudes / mes | ≈ $20 / mes |

**Palancas de costo ya aplicadas:** modelo económico (Haiku, no Opus/Sonnet),
una sola llamada por solicitud, prompt caching, y pre-filtrado por reglas (las
entradas inválidas **no** llaman al LLM). **A futuro:** *batching* (varias
solicitudes por llamada) para reducir aún más el número de peticiones.

> Nota: el modelo es configurable por `ANTHROPIC_MODEL`. Si se necesitara mayor
> calidad en casos difíciles, se podría escalar a Sonnet solo para esos casos
> (enrutamiento por confianza), manteniendo Haiku como predeterminado.

---

# Fase 4 — Entregables de la prueba (checklist)

Mapeo de lo que pide el enunciado (secciones 7, 9 y 10) contra lo entregado:

| Requisito del enunciado | Entregable | Estado |
|-------------------------|-----------|--------|
| 7.1 Ingesta CSV + ejemplo + validación | `data/solicitudes_ejemplo.csv`, `procesar.py`, `models.py` | ✅ |
| 7.2 LLM con salida estructurada + prompt documentado | `llm.py` (tool use), README §7 | ✅ |
| 7.3 Automatización reproducible | `procesar.py`, README, `requirements.txt` | ✅ |
| 7.4 Integración real | Base de datos relacional (`db.py`) | ✅ |
| 7.5 Almacenamiento + modelo de datos explicado | SQLite/PostgreSQL, README §8 | ✅ |
| 8 Manejo de errores, trazabilidad, seguridad | 3 estados, `nota`, `.env`, logs sin PII | ✅ |
| 9 Código fuente | `src/` | ✅ |
| 9 Archivo de entrada de ejemplo | `data/solicitudes_ejemplo.csv` | ✅ |
| 9 Archivo de salida de ejemplo | `data/salida_ejemplo.csv` | ✅ |
| 9 Instrucciones de ejecución | README, `COMO_PROBAR.md` | ✅ |
| 9 README completo | `README.md` (11 secciones) | ✅ |
| 9 Dependencias | `requirements.txt`, `Dockerfile`, `docker-compose.yml` | ✅ |
| 9 `.env.example` | `.env.example` | ✅ |
| 9 Decisiones, supuestos, limitaciones, mejoras | `DECISIONES.md`, `DISEÑO.md`, README §11 | ✅ |
| 10 README (11 puntos) | `README.md` | ✅ |
| 11 Repositorio GitHub | _pendiente: `git init` + push_ | ⏳ |

**Pendiente final:** subir a GitHub.

---

# Apéndice A — Arquitectura de confiabilidad en producción

La clasificación con LLM siempre tiene zonas grises (un cobro no autorizado tras
cancelar, ¿es fraude o disputa de pagos?). En la industria **no se busca un modelo
perfecto: se construye un sistema confiable alrededor de un modelo imperfecto.** La
confiabilidad es arquitectónica. Lo que añadiría una versión productiva:

**1. Separar entender / decidir / actuar**
- *Entender* (clasificar, extraer) → LLM (lo que hace bien).
- *Decidir* (prioridad, ruteo, SLA) → reglas/política deterministas y auditables.
- *Actuar* (bloquear tarjeta, reembolsar) → workflow con **aprobación humana**;
  nunca una acción irreversible decidida por el modelo.

**2. Human-in-the-loop por diseño**
La cola de "revisión manual" no es un fallback, es la estrategia. El objetivo no es
0% humanos, sino automatizar bien la mayoría y derivar lo dudoso. Se añade **ruteo
por confianza** (logprobs, self-consistency o un clasificador calibrado) para enviar
los casos de baja certeza a revisión.

**3. Guardrails en código para lo no negociable**
Lo crítico (fraude, cumplimiento) no depende del modelo: reglas duras que siempre
enrutan al equipo de riesgo. (Ya implementado: `fraude → Alta`.)

**4. Evaluación + monitoreo + feedback (el verdadero diferenciador)**
- **Golden set** etiquetado por el negocio (verdad de referencia).
- **Métricas**: matriz de confusión por categoría, *recall* de fraude (la métrica
  sagrada en fintech), % auto-resuelto, % escalado.
- **Regression testing**: cada cambio de prompt/modelo se mide contra el golden set
  ANTES de desplegar — así se evita el "prompt whack-a-mole".
- **Monitoreo de drift**: alertas si la distribución se desvía (p. ej. si "Alta"
  supera un umbral razonable).
- **Loop de feedback**: las correcciones humanas vuelven al golden set y a los
  ejemplos few-shot del prompt; el sistema mejora con el uso.

**5. Few-shot sobre reglas abstractas**
Para errores de razonamiento (p. ej. "pedir un reporte *sobre* fraude no es un
incidente de fraude"), se incluyen 2-3 ejemplos reales corregidos en el prompt, más
efectivos que instrucciones abstractas. Salen del loop de feedback.

**6. Asimetría del costo del error**
En fintech un falso negativo en fraude es catastrófico; un falso positivo solo gasta
tiempo de un analista. Por eso los sistemas se calibran **conservadores hacia el
fraude a propósito**. Ese umbral lo define el negocio, no el ingeniero.

> En resumen: el modelo es **un componente**, no el sistema. La confiabilidad viene
> de las reglas + el humano-en-el-loop + la evaluación continua + el monitoreo, no
> de exigirle perfección al modelo.
