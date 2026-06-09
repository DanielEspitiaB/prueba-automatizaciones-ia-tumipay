"""Integración con el LLM (Anthropic Claude) — el corazón del flujo.

En **una sola llamada** con salida estructurada (vía *tool use*) Claude:
  1. Clasifica la solicitud en una de las 6 categorías cerradas.
  2. Asigna una prioridad final (Alta/Media/Baja) justificada.
  3. Resume la solicitud.
  4. Extrae datos clave (montos, ids, fechas, contactos...).
  5. Propone una respuesta sugerida.

La salida es **estructurada por esquema**: se define una herramienta (tool) cuyo
`input_schema` es el contrato `ResultadoLLM`, y se fuerza al modelo a llamarla
(`tool_choice`). No se pide JSON en el texto del prompt; el modelo está obligado a
devolver un objeto que valida contra el esquema.

Decisiones de diseño:
  - **Modelo económico (Claude Haiku):** la tarea (clasificar + extraer) no
    requiere el modelo más avanzado; Haiku ofrece la mejor relación costo/calidad
    y demuestra control de costos. Configurable por `ANTHROPIC_MODEL`.
  - **Prompt caching:** el system prompt y el esquema de la herramienta se cachean,
    abaratando las llamadas repetidas del lote.
  - **Prioridad híbrida:** el modelo propone la prioridad, pero una regla de
    negocio en código garantiza que `Riesgo / fraude` nunca quede por debajo de
    `Alta`.

Manejo de errores: se distingue el error transitorio (rate-limit / 5xx / timeout,
que se reintenta con backoff) del definitivo (clave inválida, petición mal
formada). Si tras los reintentos sigue fallando, se lanza ``LLMError`` para que el
flujo marque la solicitud como ``fallida`` sin tumbar el lote.
"""

from __future__ import annotations

import logging
import os
import time

import anthropic
from dotenv import load_dotenv

from models import Categoria, PrioridadFinal, ResultadoLLM, SolicitudEntrada

load_dotenv()

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

# Reintentos para errores transitorios.
MAX_REINTENTOS = 4
BACKOFF_BASE_SEG = 2.0
MAX_ESPERA_SEG = 30.0
TIMEOUT_SEG = 60.0
MAX_TOKENS = 1500

# Throttling opcional (segundos entre llamadas). 0 = sin throttle. El tier de pago
# de Anthropic tiene límites holgados, así que por defecto se desactiva.
INTERVALO_MIN_SEG = float(os.getenv("LLM_INTERVALO_SEG", "0"))

# Códigos HTTP transitorios que conviene reintentar (529 = "overloaded").
CODIGOS_TRANSITORIOS = {408, 429, 500, 502, 503, 504, 529}

_ultima_llamada: float = 0.0


# ---------------------------------------------------------------------------
# Prompt y herramienta (documentados en el README)
# ---------------------------------------------------------------------------

SYSTEM_INSTRUCTION = """\
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

Responde siempre en español.\
"""

# El esquema de la herramienta se construye desde los enums para tener una sola
# fuente de verdad (si cambian las categorías en models.py, el esquema se actualiza).
HERRAMIENTA = {
    "name": "registrar_analisis",
    "description": "Registra el análisis estructurado de una solicitud de TUMIPAY.",
    "input_schema": {
        "type": "object",
        "properties": {
            "categoria": {
                "type": "string",
                "enum": [c.value for c in Categoria],
                "description": "Categoría de la solicitud.",
            },
            "prioridad_final": {
                "type": "string",
                "enum": [p.value for p in PrioridadFinal],
                "description": "Prioridad según contenido y riesgo, no la reportada.",
            },
            "resumen": {"type": "string", "description": "Resumen breve y claro."},
            "datos_extraidos": {
                "type": "array",
                "description": "Datos clave del mensaje como pares clave/valor.",
                "items": {
                    "type": "object",
                    "properties": {
                        "clave": {"type": "string"},
                        "valor": {"type": "string"},
                    },
                    "required": ["clave", "valor"],
                },
            },
            "respuesta_sugerida": {
                "type": "string",
                "description": "Respuesta inicial propuesta para el cliente o comercio.",
            },
            "justificacion_prioridad": {
                "type": "string",
                "description": "Por qué se asignó esa prioridad.",
            },
        },
        "required": [
            "categoria", "prioridad_final", "resumen",
            "datos_extraidos", "respuesta_sugerida", "justificacion_prioridad",
        ],
    },
}


class LLMError(Exception):
    """Fallo definitivo al obtener una clasificación del LLM."""


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    """Crea (una sola vez) el cliente de Anthropic. Lazy para no requerir la key al importar."""
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            raise LLMError(
                "Falta ANTHROPIC_API_KEY. Defínela en el archivo .env "
                "(ver .env.example). La capa LLM está desacoplada: el resto del "
                "flujo funciona, pero esta llamada requiere la clave."
            )
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=TIMEOUT_SEG)
    return _client


def _throttle() -> None:
    """Espera lo necesario para respetar la pausa mínima entre llamadas (si aplica)."""
    global _ultima_llamada
    if INTERVALO_MIN_SEG <= 0:
        return
    transcurrido = time.monotonic() - _ultima_llamada
    if 0 < transcurrido < INTERVALO_MIN_SEG:
        time.sleep(INTERVALO_MIN_SEG - transcurrido)
    _ultima_llamada = time.monotonic()


def _construir_prompt(entrada: SolicitudEntrada) -> str:
    """Arma el contenido del usuario con los campos de la solicitud."""
    return (
        "Analiza la siguiente solicitud entrante.\n\n"
        f"- id_solicitud: {entrada.id_solicitud}\n"
        f"- fecha: {entrada.fecha or 'no informada'}\n"
        f"- canal: {entrada.canal or 'no informado'}\n"
        f"- tipo_cliente: {entrada.tipo_cliente or 'no informado'}\n"
        f"- nombre_cliente: {entrada.nombre_cliente or 'no informado'}\n"
        f"- prioridad_reportada: {entrada.prioridad_reportada or 'no informada'}\n"
        f"- mensaje: {entrada.mensaje}\n"
    )


def _es_transitorio(exc: Exception) -> bool:
    """True si el error es transitorio (rate-limit / 5xx / timeout) y conviene reintentar."""
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return getattr(exc, "status_code", None) in CODIGOS_TRANSITORIOS
    return isinstance(exc, (TimeoutError, ConnectionError))


_MOTIVOS = {
    408: "tiempo de espera agotado",
    429: "límite de uso de la API alcanzado",
    500: "error temporal del servidor",
    502: "error temporal del servidor",
    503: "servicio temporalmente ocupado",
    504: "tiempo de espera del servidor",
    529: "servicio temporalmente sobrecargado",
}


def _descripcion_error(exc: Exception) -> str:
    """Descripción corta y legible del error (sin volcar trazas crudas)."""
    if isinstance(exc, anthropic.APIStatusError):
        codigo = getattr(exc, "status_code", None)
        return _MOTIVOS.get(codigo, f"error de la API (código {codigo})")
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError,
                        TimeoutError, ConnectionError)):
        return "problema de conexión"
    return type(exc).__name__


def _extraer_resultado(mensaje: anthropic.types.Message) -> ResultadoLLM:
    """Extrae el bloque tool_use de la respuesta y lo valida contra ResultadoLLM."""
    for bloque in mensaje.content:
        if bloque.type == "tool_use" and bloque.name == HERRAMIENTA["name"]:
            return ResultadoLLM(**bloque.input)
    raise LLMError("El modelo no devolvió el análisis estructurado esperado.")


def _aplicar_reglas_negocio(resultado: ResultadoLLM) -> ResultadoLLM:
    """Piso de seguridad: Riesgo/fraude nunca queda por debajo de Alta."""
    if (
        resultado.categoria == Categoria.RIESGO_FRAUDE
        and resultado.prioridad_final != PrioridadFinal.ALTA
    ):
        resultado.justificacion_prioridad = (
            "[Regla de negocio] Categoría Riesgo/fraude: la prioridad se eleva a "
            f"Alta de forma automática (el modelo había sugerido "
            f"{resultado.prioridad_final.value}). " + resultado.justificacion_prioridad
        )
        resultado.prioridad_final = PrioridadFinal.ALTA
    return resultado


def clasificar_solicitud(entrada: SolicitudEntrada) -> ResultadoLLM:
    """Llama a Claude y devuelve un ``ResultadoLLM`` validado.

    Reintenta ante errores transitorios con backoff exponencial. Lanza
    ``LLMError`` si el fallo es definitivo o si se agotan los reintentos.
    """
    client = _get_client()
    prompt = _construir_prompt(entrada)

    ultimo_error: Exception | None = None
    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            _throttle()
            mensaje = client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=MAX_TOKENS,
                # Prompt caching: cachea system + herramienta para abaratar el lote.
                system=[{
                    "type": "text",
                    "text": SYSTEM_INSTRUCTION,
                    "cache_control": {"type": "ephemeral"},
                }],
                tools=[HERRAMIENTA],
                tool_choice={"type": "tool", "name": HERRAMIENTA["name"]},
                messages=[{"role": "user", "content": prompt}],
            )
            resultado = _extraer_resultado(mensaje)
            return _aplicar_reglas_negocio(resultado)

        except Exception as exc:  # noqa: BLE001 - se reclasifica abajo
            ultimo_error = exc
            if _es_transitorio(exc) and intento < MAX_REINTENTOS:
                espera = min(BACKOFF_BASE_SEG * (2 ** (intento - 1)), MAX_ESPERA_SEG)
                logger.info(
                    "Solicitud %s: %s. Esperando %.0fs y reintentando (%d/%d)...",
                    entrada.id_solicitud, _descripcion_error(exc), espera,
                    intento, MAX_REINTENTOS,
                )
                time.sleep(espera)
                continue
            break

    intentos_txt = f"{intento} intento" + ("s" if intento > 1 else "")
    raise LLMError(
        f"{_descripcion_error(ultimo_error)} (tras {intentos_txt})"
    ) from ultimo_error
