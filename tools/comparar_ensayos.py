#!/usr/bin/env python3
"""Compara dos ensayos de estabilidad con el MISMO metodo de medida.

Por que existe: dos corridas con la misma carga solo se pueden comparar si las
cuentas se hacen igual. Esto lee las carpetas de `tools/ensayo_estabilidad.py`
y saca de la base del Core aislado y de las muestras:

  - tareas validas por minuto (no las que dice el minero: las que el Core pago)
  - GFLOP totales y GFLOP/s de computo (formula del Core, copiada aqui SOLO
    para medir; si el Core cambia la formula, esto hay que igualarlo)
  - tiempo de GPU parada (muestras con uso < 5 %)
  - latencia de cola y de entrega p50/p90
  - KIA pagados (diferencia entre la primera y la ultima muestra)
  - peticiones al Core (del resumen o del log de acceso)

Uso:
  python tools/comparar_ensayos.py medidas/ensayos/previa_20m_20261023_132805 \
                                   medidas/ensayos/nueva_20m_20261023_135046
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def epoch(txt):
    if txt is None:
        return None
    if isinstance(txt, (int, float)):
        return float(txt)
    for fmt in ("%a %b %d %H:%M:%S %Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(txt), fmt).timestamp()
        except ValueError:
            continue
    return None


def flops_core(tipo: str, params) -> float:
    """Copia de `_flops_tarea` del Core (solo para medir; no decide pagos)."""
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


def cargar(base: Path) -> dict:
    base = Path(base)
    muestras = [json.loads(l) for l in (base / "muestras.jsonl").read_text(
        encoding="utf-8").splitlines() if l.strip()]
    t0 = muestras[0]["t"]
    t1 = muestras[-1]["t"]
    minutos = max((t1 - t0) / 60.0, 1e-9)
    db = sorted(base.glob("ledger/kukia_*/kukia.db"))[-1]
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    filas = con.execute("SELECT tipo, params, creado, asignado, fin, duracion_ms "
                        "FROM tareas WHERE estado='ok' AND fin IS NOT NULL").fetchall()
    con.close()

    n = 0
    flops = 0.0
    seg_computo = 0.0
    espera: list[float] = []
    entrega: list[float] = []
    for r in filas:
        f = epoch(r["fin"])
        if f is None or not (t0 <= f <= t1 + 60):
            continue
        n += 1
        flops += flops_core(r["tipo"], r["params"])
        seg_computo += max(0.0, float(r["duracion_ms"] or 0) / 1000.0)
        if r["asignado"]:
            entrega.append(max(0.0, f - float(r["asignado"])))
            c = epoch(r["creado"])
            if c is not None:
                espera.append(max(0.0, float(r["asignado"]) - c))

    def pct(v, p):
        if not v:
            return None
        v = sorted(v)
        return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]

    usos = [g["uso_pct"] for m in muestras for g in (m.get("gpus") or [])
            if g.get("uso_pct") is not None]
    kia_ini = (muestras[0].get("kia") or {})
    kia_fin = (muestras[-1].get("kia") or {})

    def delta(motivo):
        return round(float((kia_fin.get(motivo) or {}).get("kuki", 0))
                     - float((kia_ini.get(motivo) or {}).get("kuki", 0)), 4)

    pet = {}
    resumen = base / "resumen.json"
    if resumen.exists():
        d = json.loads(resumen.read_text(encoding="utf-8"))
        pet = (d.get("tareas") or {}).get("peticiones_al_core") or {}
    if not pet:
        log = base / "core.log"
        if log.exists():
            texto = log.read_text(encoding="utf-8", errors="replace")
            pet = {"pendiente": texto.count("GET /tareas/pendiente"),
                   "resultado": texto.count("POST /tareas/resultado"),
                   "replica": texto.count("POST /tareas/replica")}

    return {
        "carpeta": base.name,
        "minutos": round(minutos, 1),
        "tareas": n,
        "tareas_min": round(n / minutos, 1),
        "gflop": round(flops / 1e9, 2),
        "gflop_s": round(flops / 1e9 / seg_computo, 1) if seg_computo else None,
        "seg_computo": round(seg_computo, 1),
        "cola_p50": pct(espera, 50), "cola_p90": pct(espera, 90),
        "entrega_p50": pct(entrega, 50), "entrega_p90": pct(entrega, 90),
        "gpu_media": round(sum(usos) / len(usos), 1) if usos else None,
        "gpu_parada_pct": round(100.0 * sum(1 for u in usos if u < 5) / len(usos), 1) if usos else None,
        "kia_trabajo": delta("trabajo verificado"),
        "kia_verificacion": delta("verificacion redundante"),
        "peticiones": pet,
    }


def main(argv=None) -> int:
    for f in (sys.stdout, sys.stderr):
        try:
            f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print(__doc__)
        return 2
    a, b = cargar(Path(argv[0])), cargar(Path(argv[1]))
    claves = [("minutos", "minutos"), ("tareas", "tareas pagadas"),
              ("tareas_min", "tareas / minuto"), ("gflop", "GFLOP totales"),
              ("gflop_s", "GFLOP / s de computo"), ("cola_p50", "espera cola p50 (s)"),
              ("cola_p90", "espera cola p90 (s)"), ("entrega_p50", "entrega p50 (s)"),
              ("entrega_p90", "entrega p90 (s)"), ("gpu_media", "uso GPU medio (%)"),
              ("gpu_parada_pct", "muestras con GPU < 5 % (%)"),
              ("kia_trabajo", "KIA trabajo"), ("kia_verificacion", "KIA verificacion")]
    print(f"{'':30s} {'A: ' + a['carpeta'][:22]:>26s} {'B: ' + b['carpeta'][:22]:>26s}")
    for clave, titulo in claves:
        print(f"{titulo:30s} {str(a[clave]):>26s} {str(b[clave]):>26s}")
    print(f"{'peticiones al Core':30s} {str(a['peticiones']):>26s} {str(b['peticiones']):>26s}")
    if a["tareas_min"] and b["tareas_min"]:
        print(f"\nB/A en tareas por minuto: {b['tareas_min'] / a['tareas_min']:.2f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
