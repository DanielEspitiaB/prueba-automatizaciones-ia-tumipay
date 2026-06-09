"""Orquestador del flujo: CSV -> validación -> LLM (Claude) -> base de datos.

Diseño desacoplado:
  - ``procesar_solicitud(entrada)`` es la unidad de procesamiento: recibe una
    solicitud YA validada y devuelve un ``SolicitudProcesada``. No conoce el CSV
    ni la base de datos, y nunca lanza excepción (si el LLM falla, devuelve un
    resultado en estado ``fallida``).
  - ``procesar_csv(ruta)`` se encarga de la fuente: lee el CSV, valida cada fila,
    enruta los tres estados y persiste cada resultado.

Manejo de errores (no tumbar el lote):
  - Fila con campos mínimos faltantes  -> "requiere revisión manual" (se guarda).
  - LLM clasifica como "Otro/..."       -> "requiere revisión manual".
  - Falla del LLM tras reintentos        -> "fallida" (se guarda con la nota).
  - Caso normal                          -> "procesada".

Uso:
    python src/procesar.py                       # usa data/solicitudes_ejemplo.csv
    python src/procesar.py ruta/a/otro.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

import db
from llm import LLMError, clasificar_solicitud
from models import (
    Categoria,
    EstadoProcesamiento,
    SolicitudEntrada,
    SolicitudProcesada,
)
from seguridad import enmascarar_pii

logger = logging.getLogger("procesar")

CSV_POR_DEFECTO = Path(__file__).resolve().parent.parent / "data" / "solicitudes_ejemplo.csv"
SALIDA_POR_DEFECTO = Path(__file__).resolve().parent.parent / "data" / "salida_ejemplo.csv"


# ---------------------------------------------------------------------------
# Unidad de procesamiento (desacoplada de la fuente y del almacenamiento)
# ---------------------------------------------------------------------------

def _campos_entrada(entrada: SolicitudEntrada) -> dict:
    """Campos del insumo que se guardan junto al resultado (trazabilidad)."""
    return {
        "mensaje": entrada.mensaje,
        "canal": entrada.canal,
        "tipo_cliente": entrada.tipo_cliente,
        "nombre_cliente": entrada.nombre_cliente,
        "fecha_recepcion": entrada.fecha,
        "prioridad_reportada": entrada.prioridad_reportada,
    }


def procesar_solicitud(entrada: SolicitudEntrada) -> SolicitudProcesada:
    """Procesa una solicitud validada y devuelve el resultado. Nunca lanza."""
    ahora = datetime.now()
    # Seguridad: enmascara PII de alto riesgo (tarjetas, cuentas) ANTES de enviar
    # el texto al LLM (un tercero) y antes de guardarlo.
    entrada.mensaje = enmascarar_pii(entrada.mensaje)
    entrada_campos = _campos_entrada(entrada)
    try:
        resultado = clasificar_solicitud(entrada)
    except LLMError as exc:
        logger.warning(
            "Solicitud %s -> no procesada (%s). Queda registrada para reintentar.",
            entrada.id_solicitud, exc,
        )
        return SolicitudProcesada(
            id_solicitud=entrada.id_solicitud,
            **entrada_campos,
            estado_procesamiento=EstadoProcesamiento.FALLIDA,
            fecha_procesamiento=ahora,
            nota=f"No procesada: {exc}. Reintentar más tarde.",
        )

    # El modelo mismo puede derivar a revisión manual.
    estado = (
        EstadoProcesamiento.REVISION_MANUAL
        if resultado.categoria == Categoria.OTRO_REVISION_MANUAL
        else EstadoProcesamiento.PROCESADA
    )
    logger.info(
        "Solicitud %s -> %s | %s | prioridad %s",
        entrada.id_solicitud, estado.value, resultado.categoria.value,
        resultado.prioridad_final.value,
    )
    return SolicitudProcesada(
        id_solicitud=entrada.id_solicitud,
        **entrada_campos,
        categoria=resultado.categoria,
        prioridad_final=resultado.prioridad_final,
        resumen=resultado.resumen,
        datos_extraidos=resultado.datos_extraidos_dict(),
        respuesta_sugerida=resultado.respuesta_sugerida,
        justificacion_prioridad=resultado.justificacion_prioridad,
        estado_procesamiento=estado,
        fecha_procesamiento=ahora,
    )


def _a_revision_manual(
    id_solicitud: str, error: ValidationError, fila: dict
) -> SolicitudProcesada:
    """Construye un registro de revisión manual para una fila inválida.

    Se guarda el insumo crudo (lo que venía en el CSV) para poder revisarlo a mano.
    """
    campos = ", ".join(str(e["loc"][0]) for e in error.errors())
    return SolicitudProcesada(
        id_solicitud=id_solicitud,
        mensaje=enmascarar_pii((fila.get("mensaje") or "").strip()) or None,
        canal=(fila.get("canal") or "").strip() or None,
        tipo_cliente=(fila.get("tipo_cliente") or "").strip() or None,
        nombre_cliente=(fila.get("nombre_cliente") or "").strip() or None,
        fecha_recepcion=(fila.get("fecha") or "").strip() or None,
        prioridad_reportada=(fila.get("prioridad_reportada") or "").strip() or None,
        estado_procesamiento=EstadoProcesamiento.REVISION_MANUAL,
        fecha_procesamiento=datetime.now(),
        nota=f"Entrada inválida o incompleta. Campos con problema: {campos}.",
    )


# ---------------------------------------------------------------------------
# Orquestación desde CSV
# ---------------------------------------------------------------------------

def procesar_csv(ruta: Path) -> dict[str, int]:
    """Lee el CSV, procesa cada fila y persiste. Devuelve un conteo por estado."""
    db.init_db()
    conteo = {e.value: 0 for e in EstadoProcesamiento}

    with open(ruta, newline="", encoding="utf-8") as f:
        lector = csv.DictReader(f)
        for indice, fila in enumerate(lector, start=1):
            id_crudo = (fila.get("id_solicitud") or "").strip() or f"FILA-{indice}"
            try:
                entrada = SolicitudEntrada(**fila)
            except ValidationError as exc:
                resultado = _a_revision_manual(id_crudo, exc, fila)
                logger.warning(
                    "Solicitud %s -> REVISIÓN MANUAL (entrada inválida)", id_crudo
                )
            else:
                resultado = procesar_solicitud(entrada)

            db.guardar_resultado(resultado)
            conteo[resultado.estado_procesamiento] += 1

    return conteo


def exportar_salida_csv(destino: Path = SALIDA_POR_DEFECTO) -> None:
    """Vuelca la tabla de resultados a un CSV (entregable de salida de ejemplo)."""
    from sqlalchemy import select

    columnas = [
        # Insumo (solicitud original)
        "id_solicitud", "fecha_recepcion", "canal", "tipo_cliente",
        "nombre_cliente", "mensaje", "prioridad_reportada",
        # Resultado del análisis
        "categoria", "prioridad_final", "resumen", "datos_extraidos",
        "respuesta_sugerida", "justificacion_prioridad",
        "estado_procesamiento", "fecha_procesamiento", "nota",
    ]
    with db.SessionLocal() as session, open(destino, "w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=columnas)
        escritor.writeheader()
        for fila in session.scalars(select(db.SolicitudProcesadaORM)).all():
            escritor.writerow({c: getattr(fila, c) for c in columnas})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Procesa solicitudes TUMIPAY desde un CSV.")
    parser.add_argument(
        "csv", nargs="?", default=str(CSV_POR_DEFECTO),
        help="Ruta del CSV de entrada (por defecto: data/solicitudes_ejemplo.csv)",
    )
    parser.add_argument(
        "--sin-exportar", action="store_true",
        help="No exportar el CSV de salida de ejemplo.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    # Silenciar el ruido de las librerías (peticiones HTTP, mensajes internos del
    # SDK) para que el log muestre solo el avance del procesamiento.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    ruta = Path(args.csv)
    if not ruta.exists():
        logger.error("No existe el archivo de entrada: %s", ruta)
        return 1

    logger.info("Procesando %s", ruta)
    conteo = procesar_csv(ruta)

    total = sum(conteo.values())
    logger.info("Lote terminado. Total: %d", total)
    for estado, n in conteo.items():
        logger.info("  - %s: %d", estado, n)

    if not args.sin_exportar:
        try:
            exportar_salida_csv()
            logger.info("Salida exportada a %s", SALIDA_POR_DEFECTO)
        except PermissionError:
            logger.warning(
                "No se pudo escribir %s (¿está abierto en Excel?). "
                "Ciérralo y vuelve a ejecutar; los datos ya quedaron en la BD.",
                SALIDA_POR_DEFECTO,
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
