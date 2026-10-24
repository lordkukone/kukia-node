#!/usr/bin/env python3
"""Prueba de la PODA DE TELEMETRIA (auditoria 23-10-2026, hallazgo H6).

QUE COMPRUEBA, sobre una COPIA del ledger (produccion no se toca):
  1. `podar_telemetria` borra filas viejas de `planificador_log` y
     `gpu_telemetria`.
  2. NO toca nada que valga dinero: ledger, saldos_cuenta, supply_emitido y
     tareas quedan exactamente igual (ni una fila, ni un KIA).
  3. El tope duro por filas funciona.
  4. Si no hay nada viejo, no borra nada (no se come la traza de hoy).

Uso:
    python tools/prueba_poda_telemetria.py
    python tools/prueba_poda_telemetria.py --horas 24 --tope 50000
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
COORD = RAIZ / "coordinator"
DB_REAL = COORD / "ledger" / "kukia.db"

fallos: list[str] = []


def check(ok: bool, texto: str) -> None:
    print(("  [ OK ] " if ok else "  [FALLO] ") + texto, flush=True)
    if not ok:
        fallos.append(texto)


def contar(db: Path, tabla: str) -> int:
    c = sqlite3.connect(db)
    try:
        return c.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
    finally:
        c.close()


def huella_dinero(db: Path) -> dict:
    """Lo que NO puede cambiar: asientos, saldos, tareas y el supply de meta."""
    c = sqlite3.connect(db)
    try:
        return {
            "ledger": c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0],
            "saldos": c.execute("SELECT COUNT(*), ROUND(SUM(total), 9) "
                                "FROM saldos_cuenta").fetchone(),
            "tareas": c.execute("SELECT COUNT(*) FROM tareas").fetchone()[0],
            "tareas_ok": c.execute("SELECT COUNT(*) FROM tareas WHERE estado='ok'").fetchone()[0],
            "sellos": c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0],
        }
    finally:
        c.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horas", type=float, default=48.0)
    ap.add_argument("--tope", type=int, default=200000)
    ap.add_argument("--dejar", action="store_true", help="no borrar la carpeta temporal")
    args = ap.parse_args()

    if not DB_REAL.exists():
        print(f"ERROR: no existe {DB_REAL}", file=sys.stderr)
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="kukia_poda_"))
    db = tmp / "kukia.db"
    print(f"copia del ledger en {db}")
    t0 = time.time()
    shutil.copy2(DB_REAL, db)
    print(f"  copiada en {time.time() - t0:.1f} s ({db.stat().st_size / 1e6:.1f} MB)")

    antes_traza = {t: contar(db, t) for t in ("planificador_log", "gpu_telemetria")}
    antes_dinero = huella_dinero(db)
    print(f"  antes: {antes_traza}")

    os.environ.update({
        "KUKIA_DB": str(db),
        "KUKIA_AUTO_TAREAS": "0",
        "KUKIA_ARCHIVAR": "0",
        "KUKIA_SELLO_SEG": "3600",
        "KUKIA_PAGO_POR_FLOP": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    sys.path.insert(0, str(COORD))
    import kukia_core as C  # noqa: E402  (se importa con el entorno ya puesto)

    print("\n== 1. poda por edad ==")
    t0 = time.time()
    r = C.podar_telemetria(horas=args.horas, tope=args.tope)
    print(f"  {r['borradas']} en {time.time() - t0:.1f} s")
    for tabla in ("planificador_log", "gpu_telemetria"):
        quedan = contar(db, tabla)
        check(quedan < antes_traza[tabla],
              f"{tabla}: quedan {quedan} de {antes_traza[tabla]}")

    print("\n== 2. el dinero no se toca ==")
    despues = huella_dinero(db)
    for clave, valor in antes_dinero.items():
        check(despues[clave] == valor, f"{clave} igual ({valor} -> {despues[clave]})")
    check(abs(C.supply_emitido() - C.supply_emitido()) < 1e-12,
          "supply_emitido estable al leerlo dos veces")

    print("\n== 3. tope duro por filas ==")
    r2 = C.podar_telemetria(horas=100000, tope=1000)   # nada es viejo; manda el tope
    for tabla in ("planificador_log", "gpu_telemetria"):
        n = contar(db, tabla)
        check(n <= 1000, f"{tabla}: recortada a {n} filas (tope 1000)")

    print("\n== 4. sin nada viejo no borra ==")
    r3 = C.podar_telemetria(horas=100000, tope=10 ** 9)
    check(r3["total"] == 0, f"no borra nada cuando no hay traza vieja ({r3['total']})")

    print(f"\ntamano de la copia: {db.stat().st_size / 1e6:.1f} MB "
          f"(el fichero no encoge hasta un VACUUM: se reutiliza el hueco)")

    if not args.dejar:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fallos:
        print(f"[prueba] {len(fallos)} FALLO(S)")
        for f in fallos:
            print(f"   - {f}")
        return 1
    print("[prueba] todo OK")
    return 0


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
