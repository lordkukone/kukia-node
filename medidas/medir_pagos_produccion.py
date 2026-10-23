"""Lee el ledger de produccion (solo lectura) y resume lo pagado por minuto.

Uso:  python medidas/medir_pagos_produccion.py [minutos]

No escribe nada: abre la base con query_only. Sirve para comparar el pago
antes y despues de reiniciar el Core (mismo codigo, mismos numeros).
"""
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "coordinator" / "ledger" / "kukia.db"
MIN = int(sys.argv[1]) if len(sys.argv) > 1 else 20

db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
db.execute("PRAGMA query_only=1")

filas = db.execute(
    "SELECT kuki, motivo, ts FROM ledger ORDER BY id DESC LIMIT 200000").fetchall()
ahora = datetime.now()
por_minuto = defaultdict(lambda: defaultdict(float))
n_por_minuto = defaultdict(lambda: defaultdict(int))
for kuki, motivo, ts in filas:
    try:
        t = datetime.strptime(ts, "%a %b %d %H:%M:%S %Y")
    except Exception:
        continue
    if (ahora - t).total_seconds() > MIN * 60:
        continue
    clave = t.strftime("%H:%M")
    por_minuto[clave][motivo] += float(kuki or 0.0)
    n_por_minuto[clave][motivo] += 1

print(f"DB: {DB}")
print(f"filas leidas: {len(filas)} · ventana: {MIN} min · ahora {ahora:%H:%M:%S}\n")
totales = defaultdict(float)
n_total = defaultdict(int)
print(f"{'min':>5} {'KIA':>10} {'tareas':>7} {'KIA/tarea':>10}  motivos")
for clave in sorted(por_minuto):
    fila = por_minuto[clave]
    n = sum(n_por_minuto[clave].values())
    total = sum(fila.values())
    for k, v in fila.items():
        totales[k] += v
        n_total[k] += n_por_minuto[clave][k]
    detalle = ", ".join(f"{k}={v:.4f}" for k, v in sorted(fila.items()) if v)
    print(f"{clave:>5} {total:10.4f} {n:7d} {total/max(1,n):10.6f}  {detalle}")

print("\nTOTALES de la ventana:")
gran = sum(totales.values())
for k, v in sorted(totales.items(), key=lambda x: -x[1]):
    print(f"  {k:<42} {v:10.4f}  ({n_total[k]} asientos)")
print(f"  {'TOTAL':<42} {gran:10.4f}  -> {gran / max(1, len(por_minuto)):.4f} KIA/min")
