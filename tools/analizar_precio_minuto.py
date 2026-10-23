#!/usr/bin/env python3
"""Analiza un ensayo de competencia para ver si el reparto es proporcional.

PARA QUE
  Comprueba, sobre el ledger de un ensayo de `tools/ensayo_competencia.py`:
    1. Lo que paga la red por minuto (deberia quedar en el presupuesto).
    2. El precio efectivo por GFLOP de cada nodo, calculado desde su propio
       trabajo (KIA de trabajo verificado / GFLOP facturables de sus tareas).
       Si es el mismo para los dos, el reparto es proporcional al trabajo.

USO
  python tools/analizar_precio_minuto.py medidas/ensayos/comp_r2_<fecha>

Los GFLOP se cuentan con la MISMA formula que el Core:
  matriz 2n^3 · inferencia 18n^2 · difusion 10n^2*pasos · resto 0
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def epoch(txt) -> float | None:
    if txt is None:
        return None
    if isinstance(txt, (int, float)):
        return float(txt)
    for fmt in ("%a %b %d %H:%M:%S %Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(txt)[:24], fmt).timestamp()
        except ValueError:
            continue
    return None


def flops(tipo: str, params) -> float:
    try:
        p = json.loads(params) if isinstance(params, str) else (params or {})
        n = int(p.get("n") or 0)
        if tipo == "matriz":
            return 2.0 * n ** 3
        if tipo == "inferencia":
            return 18.0 * n ** 2
        if tipo == "difusion_calor":
            return 10.0 * n ** 2 * int(p.get("pasos") or 0)
    except Exception:
        return 0.0
    return 0.0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    base = Path(sys.argv[1])
    r = json.loads((base / "resumen.json").read_text(encoding="utf-8"))
    db = r["db"]
    t0 = min(m["t"] for m in r["serie_completa"] if m.get("t"))
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        con.execute("SELECT 1 FROM ledger LIMIT 1")
    except Exception:
        # El Core de pruebas se paro en duro y dejo el WAL sin cerrar: se abre
        # normal (es una copia de pruebas, no la produccion).
        con = sqlite3.connect(db, timeout=15)
    con.row_factory = sqlite3.Row

    ids = {"test": r["ids"]["test"], "rival": r["ids"]["rival"]}
    cuentas = {k: list(v or []) for k, v in (r.get("cuentas") or {}).items()}
    # Si el resumen no trae las cuentas (paso con un WAL sin cerrar), se sacan
    # de la tabla nodos, que es de donde las coge el propio ensayo.
    for nombre, lista in ids.items():
        if not cuentas.get(nombre) and lista:
            marcas = ",".join("?" * len(lista))
            try:
                filas = con.execute(f"SELECT direccion_cobro FROM nodos WHERE id IN ({marcas})",
                                    lista).fetchall()
                cuentas[nombre] = [f["direccion_cobro"] for f in filas if f["direccion_cobro"]]
            except Exception:
                cuentas[nombre] = []
    print(f"cuentas: {cuentas}")
    id_a_nombre = {}
    for nombre, lista in ids.items():
        for i in lista:
            id_a_nombre[i] = nombre

    # Trabajo facturable por nodo y minuto (de sus tareas cerradas).
    gflop = defaultdict(float)
    for f in con.execute("SELECT nodo_id,tipo,params,fin FROM tareas WHERE estado='ok'"):
        nombre = id_a_nombre.get(f["nodo_id"])
        if not nombre:
            continue
        t = epoch(f["fin"])
        if t is None or t < t0:
            continue
        gflop[(int(t // 60), nombre)] += flops(f["tipo"], f["params"]) / 1e9

    # Pagos por cuenta y minuto.
    cuenta_a_nombre = {}
    for nombre, lista in cuentas.items():
        for c in lista:
            cuenta_a_nombre[c] = nombre
    kia = defaultdict(float)
    kia_work = defaultdict(float)
    for f in con.execute("SELECT nodo_id,motivo,kuki,ts FROM ledger"):
        nombre = cuenta_a_nombre.get(f["nodo_id"])
        if not nombre:
            continue
        t = epoch(f["ts"])
        if t is None or t < t0:
            continue
        k = float(f["kuki"] or 0.0)
        kia[(int(t // 60), nombre)] += k
        if f["motivo"] == "trabajo verificado":
            kia_work[(int(t // 60), nombre)] += k
    con.close()

    minutos = sorted({m for m, _ in list(gflop) + list(kia)})
    print(f"Ensayo {base.name} · {r['ranuras_test']} ranura(s) · presupuesto 1,3106 KIA/min\n")
    print(f"{'hora':>5} {'KIA red':>8} {'KIA test':>9} {'KIA rival':>9} "
          f"{'GFLOP test':>10} {'GFLOP rival':>11} {'KIA/GFLOP t':>12} {'KIA/GFLOP r':>12}")
    tot_red, tot_test, tot_rival = 0.0, 0.0, 0.0
    precios_t, precios_r = [], []
    for m in minutos:
        kt, kr = kia[(m, "test")], kia[(m, "rival")]
        gt, gr = gflop[(m, "test")], gflop[(m, "rival")]
        wt, wr = kia_work[(m, "test")], kia_work[(m, "rival")]
        pt = (wt / gt) if gt else None
        pr = (wr / gr) if gr else None
        if pt:
            precios_t.append(pt)
        if pr:
            precios_r.append(pr)
        tot_red += kt + kr
        tot_test += kt
        tot_rival += kr
        hora = datetime.fromtimestamp(m * 60).strftime("%H:%M")
        print(f"{hora:>5} {kt + kr:8.4f} {kt:9.4f} {kr:9.4f} {gt:10.1f} {gr:11.1f} "
              f"{(pt if pt else 0):12.9f} {(pr if pr else 0):12.9f}")

    n = max(1, len(minutos))
    print(f"\n  media      {tot_red / n:8.4f} {tot_test / n:9.4f} {tot_rival / n:9.4f}")
    if precios_t and precios_r:
        dif = abs(sum(precios_t) / len(precios_t) - sum(precios_r) / len(precios_r))
        rel = 100.0 * dif / (sum(precios_t) / len(precios_t))
        print(f"  precio medio por GFLOP:  test {sum(precios_t) / len(precios_t):.9f}  "
              f"rival {sum(precios_r) / len(precios_r):.9f}  -> diferencia {rel:.2f} %")
        print("  (si la diferencia es ~0, el reparto es proporcional al trabajo)")
    print(f"\n  total de la ventana: {tot_red:.4f} KIA en {len(minutos)} min "
          f"-> {tot_red / n:.4f} KIA/min (presupuesto 1,3106)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
