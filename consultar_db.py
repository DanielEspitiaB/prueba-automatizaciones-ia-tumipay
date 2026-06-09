"""Utilidad para inspeccionar la base de datos local (SQLite).

Lee directamente el archivo SQLite (no usa el ORM) para demostrar que los datos
quedaron realmente almacenados. Solo lectura: nunca modifica nada.

Uso:
    python consultar_db.py                 # resumen + todas las filas
    python consultar_db.py --estado fallida    # filtra por estado
    python consultar_db.py --id SOL-003        # ver una solicitud en detalle
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

BD = Path(__file__).resolve().parent / "solicitudes.db"


def main() -> int:
    parser = argparse.ArgumentParser(description="Consulta la BD de solicitudes (solo lectura).")
    parser.add_argument("--estado", help="Filtrar por estado (procesada / 'requiere revisión manual' / fallida).")
    parser.add_argument("--id", dest="id_solicitud", help="Ver el detalle completo de una solicitud.")
    args = parser.parse_args()

    if not BD.exists():
        print(f"No existe {BD}. Ejecuta primero: python src/procesar.py")
        return 1

    con = sqlite3.connect(BD)
    con.row_factory = sqlite3.Row

    # Resumen por estado
    print("\n=== Resumen por estado ===")
    for fila in con.execute(
        "SELECT estado_procesamiento AS estado, COUNT(*) AS n "
        "FROM solicitudes_procesadas GROUP BY estado_procesamiento ORDER BY n DESC"
    ):
        print(f"  {fila['estado']:28} {fila['n']}")

    # Detalle de una solicitud
    if args.id_solicitud:
        print(f"\n=== Detalle de {args.id_solicitud} ===")
        fila = con.execute(
            "SELECT * FROM solicitudes_procesadas WHERE id_solicitud = ? ORDER BY id DESC LIMIT 1",
            (args.id_solicitud,),
        ).fetchone()
        if fila is None:
            print("  (no encontrada)")
        else:
            for clave in fila.keys():
                print(f"  {clave:24}: {fila[clave]}")
        con.close()
        return 0

    # Listado (con filtro opcional por estado)
    print("\n=== Solicitudes ===")
    if args.estado:
        cur = con.execute(
            "SELECT id_solicitud, estado_procesamiento, categoria, prioridad_final "
            "FROM solicitudes_procesadas WHERE estado_procesamiento = ? ORDER BY id",
            (args.estado,),
        )
    else:
        cur = con.execute(
            "SELECT id_solicitud, estado_procesamiento, categoria, prioridad_final "
            "FROM solicitudes_procesadas ORDER BY id"
        )
    for fila in cur:
        print(
            f"  {fila['id_solicitud']:10} | {str(fila['estado_procesamiento']):26} | "
            f"{str(fila['categoria'] or '-'):28} | {str(fila['prioridad_final'] or '-')}"
        )

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
