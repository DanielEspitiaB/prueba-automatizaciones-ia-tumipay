"""Modelos de datos (Pydantic) para la automatización de solicitudes TUMIPAY.

Este módulo define los *contratos* de datos del flujo:

    CSV -> SolicitudEntrada -> [LLM/Claude] -> ResultadoLLM -> SolicitudProcesada -> BD

Solo contiene validación y tipado. El acceso a base de datos (ORM) vive en
``db.py`` y la integración con el LLM en ``llm.py``. Mantener los modelos aquí
permite que el módulo de procesamiento sea independiente de la fuente y del
almacenamiento.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Enums (conjuntos cerrados definidos por el enunciado)
# ---------------------------------------------------------------------------

class Categoria(str, Enum):
    """Categorías mínimas de clasificación (sección 5 del enunciado)."""

    SOPORTE_TECNICO = "Soporte técnico"
    SOLICITUD_COMERCIAL = "Solicitud comercial"
    RIESGO_FRAUDE = "Riesgo / fraude"
    CONCILIACION_PAGOS = "Conciliación / pagos"
    ACTUALIZACION_DATOS = "Actualización de datos"
    OTRO_REVISION_MANUAL = "Otro / requiere revisión manual"


class PrioridadFinal(str, Enum):
    """Prioridad final asignada por la solución (no necesariamente la reportada)."""

    ALTA = "Alta"
    MEDIA = "Media"
    BAJA = "Baja"


class EstadoProcesamiento(str, Enum):
    """Estado del registro tras pasar por el flujo."""

    PROCESADA = "procesada"
    REVISION_MANUAL = "requiere revisión manual"
    FALLIDA = "fallida"


# ---------------------------------------------------------------------------
# Entrada: una fila del CSV ya validada
# ---------------------------------------------------------------------------

class SolicitudEntrada(BaseModel):
    """Solicitud de entrada validada.

    ``id_solicitud`` y ``mensaje`` son obligatorios: sin un identificador no hay
    trazabilidad y sin mensaje no hay nada que clasificar. El resto de campos son
    opcionales; una fila con campos faltantes se acepta como entrada parcial y el
    módulo de procesamiento decidirá enviarla a revisión manual (pero se guarda
    igual).
    """

    id_solicitud: str
    mensaje: str
    fecha: str | None = None
    canal: str | None = None
    tipo_cliente: str | None = None
    nombre_cliente: str | None = None
    prioridad_reportada: str | None = None

    @field_validator(
        "fecha", "canal", "tipo_cliente", "nombre_cliente", "prioridad_reportada",
        mode="before",
    )
    @classmethod
    def _vacio_a_none(cls, v: Any) -> Any:
        """Normaliza strings vacíos o solo-espacios a None en campos opcionales."""
        if isinstance(v, str) and not v.strip():
            return None
        return v.strip() if isinstance(v, str) else v

    @field_validator("id_solicitud", "mensaje", mode="before")
    @classmethod
    def _obligatorio_no_vacio(cls, v: Any) -> Any:
        """Rechaza obligatorios vacíos para que la fila caiga a revisión manual."""
        if v is None or (isinstance(v, str) and not v.strip()):
            raise ValueError("campo obligatorio vacío")
        return v.strip() if isinstance(v, str) else v


# ---------------------------------------------------------------------------
# Salida del LLM (esquema estructurado que devuelve el modelo)
# ---------------------------------------------------------------------------

class DatoExtraido(BaseModel):
    """Par clave/valor de información extraída del mensaje.

    Se usa una lista de pares en lugar de un diccionario abierto porque la salida
    estructurada por esquema (tool use) requiere un esquema cerrado y no admite
    objetos con claves arbitrarias.
    """

    clave: str
    valor: str


class ResultadoLLM(BaseModel):
    """Salida estructurada del LLM para una solicitud.

    Este es el esquema que se usa como contrato de la herramienta (tool use).
    Concentra en una sola llamada: clasificación, extracción, resumen y respuesta.
    """

    categoria: Categoria
    prioridad_final: PrioridadFinal
    resumen: str = Field(description="Resumen breve y claro de la solicitud.")
    datos_extraidos: list[DatoExtraido] = Field(
        default_factory=list,
        description="Información clave extraída del mensaje (montos, ids, fechas, etc.).",
    )
    respuesta_sugerida: str = Field(
        description="Respuesta inicial propuesta para el cliente o comercio."
    )
    justificacion_prioridad: str = Field(
        description="Por qué se asignó esa prioridad final, según el contenido."
    )

    def datos_extraidos_dict(self) -> dict[str, str]:
        """Convierte la lista clave/valor a un diccionario para almacenar como JSON."""
        return {d.clave: d.valor for d in self.datos_extraidos}


# ---------------------------------------------------------------------------
# Salida final: el registro que se persiste en la base de datos
# ---------------------------------------------------------------------------

class SolicitudProcesada(BaseModel):
    """Registro de salida completo (entrada + resultado del LLM + metadatos).

    Es lo que devuelve el módulo de procesamiento y lo que ``db.py`` persiste.
    """

    model_config = ConfigDict(use_enum_values=True)

    id_solicitud: str

    # --- Solicitud original (insumo) ---
    # Se guarda junto al resultado para trazabilidad: poder auditar la clasificación
    # viendo el mensaje original, y comparar la prioridad reportada vs. la final.
    mensaje: str | None = None
    canal: str | None = None
    tipo_cliente: str | None = None
    nombre_cliente: str | None = None
    fecha_recepcion: str | None = None
    prioridad_reportada: str | None = None

    # --- Resultado del análisis ---
    # categoría/prioridad/resumen/respuesta son opcionales: un registro "fallida"
    # o "requiere revisión manual" puede no tener resultado del LLM.
    categoria: Categoria | None = None
    prioridad_final: PrioridadFinal | None = None
    resumen: str | None = None
    datos_extraidos: dict[str, Any] = Field(default_factory=dict)
    respuesta_sugerida: str | None = None
    justificacion_prioridad: str | None = None
    estado_procesamiento: EstadoProcesamiento
    fecha_procesamiento: datetime
    # Trazabilidad: motivo de fallo o de envío a revisión manual (evidencia/logs).
    nota: str | None = None
