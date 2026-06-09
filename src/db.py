"""Capa de persistencia (SQLAlchemy ORM).

Esta es la *integración real* del flujo (requisito 7.4): una base de datos
relacional. La conexión se controla por completo con la variable de entorno
``DATABASE_URL``:

    - Local (por defecto): ``sqlite:///solicitudes.db`` — cero setup.
    - Producción: ``postgresql+psycopg2://usuario:pass@host:5432/tumipay``

Para pasar de SQLite a PostgreSQL **solo se cambia esa variable**; el resto del
código no cambia. El tipo ``JSON`` de SQLAlchemy se almacena como TEXT en SQLite
y como JSON nativo en PostgreSQL, así que ``datos_extraidos`` funciona igual en
ambos motores.
"""

from __future__ import annotations

import os
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import JSON, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from models import SolicitudProcesada

# Carga las variables de .env (DATABASE_URL, etc.) una sola vez al importar.
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///solicitudes.db")

# future=True activa el estilo 2.0; SQLite necesita check_same_thread=False solo
# cuando se accede desde varios hilos (aquí el procesamiento es secuencial).
engine = create_engine(DATABASE_URL, future=True)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class SolicitudProcesadaORM(Base):
    """Tabla de resultados. Espeja ``SolicitudProcesada`` (models.py).

    Se usa una clave primaria sustituta autoincremental (``id``) en vez de
    ``id_solicitud`` porque una misma solicitud podría reprocesarse o llegar
    duplicada; ``id_solicitud`` queda indexado para búsquedas pero no es único.
    """

    __tablename__ = "solicitudes_procesadas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_solicitud: Mapped[str] = mapped_column(String(100), index=True)

    # Solicitud original (insumo), para trazabilidad y auditoría.
    mensaje: Mapped[str | None] = mapped_column(Text, nullable=True)
    canal: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tipo_cliente: Mapped[str | None] = mapped_column(String(50), nullable=True)
    nombre_cliente: Mapped[str | None] = mapped_column(String(150), nullable=True)
    fecha_recepcion: Mapped[str | None] = mapped_column(String(40), nullable=True)
    prioridad_reportada: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Nullables: un registro fallido o de revisión manual puede no tener resultado.
    categoria: Mapped[str | None] = mapped_column(String(50), nullable=True)
    prioridad_final: Mapped[str | None] = mapped_column(String(10), nullable=True)
    resumen: Mapped[str | None] = mapped_column(Text, nullable=True)
    datos_extraidos: Mapped[dict] = mapped_column(JSON, default=dict)
    respuesta_sugerida: Mapped[str | None] = mapped_column(Text, nullable=True)
    justificacion_prioridad: Mapped[str | None] = mapped_column(Text, nullable=True)

    estado_procesamiento: Mapped[str] = mapped_column(String(30), index=True)
    fecha_procesamiento: Mapped[datetime] = mapped_column(DateTime)

    # Trazabilidad: motivo de fallo o de envío a revisión manual.
    nota: Mapped[str | None] = mapped_column(Text, nullable=True)


def init_db() -> None:
    """Crea las tablas si no existen. Idempotente."""
    Base.metadata.create_all(engine)


def guardar_resultado(resultado: SolicitudProcesada) -> int:
    """Persiste una ``SolicitudProcesada`` y devuelve el id generado.

    El módulo de procesamiento es agnóstico al almacenamiento: produce el modelo
    Pydantic y esta función lo traduce a una fila ORM.
    """
    fila = SolicitudProcesadaORM(
        id_solicitud=resultado.id_solicitud,
        mensaje=resultado.mensaje,
        canal=resultado.canal,
        tipo_cliente=resultado.tipo_cliente,
        nombre_cliente=resultado.nombre_cliente,
        fecha_recepcion=resultado.fecha_recepcion,
        prioridad_reportada=resultado.prioridad_reportada,
        categoria=resultado.categoria,
        prioridad_final=resultado.prioridad_final,
        resumen=resultado.resumen,
        datos_extraidos=resultado.datos_extraidos,
        respuesta_sugerida=resultado.respuesta_sugerida,
        justificacion_prioridad=resultado.justificacion_prioridad,
        estado_procesamiento=resultado.estado_procesamiento,
        fecha_procesamiento=resultado.fecha_procesamiento,
        nota=resultado.nota,
    )
    with SessionLocal() as session:  # type: Session
        session.add(fila)
        session.commit()
        return fila.id
