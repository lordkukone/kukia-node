#!/usr/bin/env python3
"""Saca uso, vatios y KIA/kWh de un ensayo de `tools/ensayo_estabilidad.py`.

Dos medidas de "cuánto trabaja la GPU", porque ninguna sola es honesta:

  ocupacion_pct  Suma de `duracion_ms` de las tareas del nodo / tiempo de pared
                 x GPUs. Viene de los datos del propio minero, sin muestreo:
                 es el techo (incluye el Python que rodea al kernel).
  uso_pct        Media de lo que dijo nvidia-smi en cada muestra (1 por minuto).
                 Es lo que ve el panel, pero es una foto por minuto: se le
                 escapan los picos y los huecos.

Vatios: suma de `power.draw` de las GPU en cada muestra (nvidia-smi, 1 Hz).
Con eso, KIA/kWh = KIA cobrados / (vatios medios x horas).

USO
  python tools/medir_kia_kwh.py medidas/ensayos/<carpeta> [...]
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


def _db(carpeta: Path) -> Path:
    cand = sorted(carpeta.glob("ledger/*/kukia.db"))
    if not cand:
        raise SystemExit(f"[kwh] no hay kukia.db en {carpeta}")
    return cand[-1]


def _etiqueta(carpeta: Path) -> str:
    partes = carpeta.name.rsplit("_", 2)
    if len(partes) == 3 and partes[1].isdigit() and partes[2].isdigit():
        return partes[0]
    return carpeta.name


def medir(carpeta: Path) -> dict:
    resumen = json.loads((carpeta / "resumen.json").read_text(encoding="utf-8"))
    minutos = float(resumen.get("minutos_medidos") or 0.0)

    origen = _db(carpeta)
    temp = Path(tempfile.mkdtemp(prefix="kwh_"))
    for suf in ("", "-wal", "-shm"):
        if Path(str(origen) + suf).exists():
            shutil.copy2(str(origen) + suf, temp / (origen.name + suf))
    con = sqlite3.connect(str(temp / origen.name))
    con.row_factory = sqlite3.Row
    patron = f"%{_etiqueta(carpeta)}%"
    nodos = [r["id"] for r in con.execute("SELECT id FROM nodos WHERE nombre LIKE ?", (patron,))]
    marcas = ",".join("?" * len(nodos))

    fila = con.execute(
        f"SELECT COUNT(*) n, COALESCE(SUM(duracion_ms),0) ms, "
        f"COALESCE(SUM(duracion_ms*0.0),0) x FROM tareas "
        f"WHERE estado='ok' AND nodo_id IN ({marcas})", tuple(nodos)).fetchone()
    kia = con.execute(
        f"SELECT COALESCE(SUM(l.kuki),0) s FROM ledger l JOIN tareas t ON t.id=l.tarea_id "
        f"WHERE t.estado='ok' AND t.nodo_id IN ({marcas}) AND l.kuki>0", tuple(nodos)).fetchone()["s"]
    gflop = con.execute(
        "SELECT COUNT(*) n FROM tareas WHERE estado='ok' AND nodo_id IN (%s)" % marcas,
        tuple(nodos)).fetchone()["n"]
    con.close()

    # Muestras: vatios y uso de nvidia-smi.
    vatios, usos, n = [], [], 0
    for linea in (carpeta / "muestras.jsonl").open(encoding="utf-8"):
        m = json.loads(linea)
        gpus = m.get("gpus") or []
        if not gpus:
            continue
        n += 1
        vatios.append(sum(float(g.get("w") or 0.0) for g in gpus))
        usos.extend(float(g.get("uso_pct") or 0.0) for g in gpus)
    w_medios = sum(vatios) / len(vatios) if vatios else 0.0
    uso_medio = sum(usos) / len(usos) if usos else 0.0
    ngpu = max(1, len(gpus)) if n else 1

    horas = minutos / 60.0
    kwh = (w_medios / 1000.0) * horas
    ocupacion = (fila["ms"] / 1000.0) / (minutos * 60.0 * ngpu) * 100.0 if minutos else 0.0
    return {
        "carpeta": carpeta.name,
        "minutos": minutos,
        "gpus": ngpu,
        "tareas": fila["n"],
        "tareas_min": fila["n"] / minutos if minutos else 0.0,
        "tarea_media_ms": (fila["ms"] / fila["n"]) if fila["n"] else 0.0,
        "ocupacion_pct": ocupacion,
        "uso_pct": uso_medio,
        "vatios": w_medios,
        "kwh": kwh,
        "kia": kia,
        "kia_kwh": (kia / kwh) if kwh > 0 else 0.0,
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    filas = [medir(Path(a).resolve()) for a in argv[1:]]
    cab = (f"{'ensayo':<30}{'min':>5}{'GPU':>4}{'tareas':>8}{'t/min':>7}"
           f"{'tarea ms':>10}{'ocup %':>8}{'uso %':>7}{'W':>8}{'KIA':>9}{'KIA/kWh':>10}")
    print(cab)
    print("-" * len(cab))
    for f in filas:
        print(f"{f['carpeta']:<30}{f['minutos']:>5.0f}{f['gpus']:>4}{f['tareas']:>8}"
              f"{f['tareas_min']:>7.1f}{f['tarea_media_ms']:>10.0f}"
              f"{f['ocupacion_pct']:>8.1f}{f['uso_pct']:>7.1f}{f['vatios']:>8.1f}"
              f"{f['kia']:>9.3f}{f['kia_kwh']:>10.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
