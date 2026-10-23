#!/usr/bin/env python3
"""Cuenta cuántas difusiones acaban en un nodo CPU y cuánto tardan.

Lee el ledger de un ensayo de `tools/ensayo_estabilidad.py` y saca, por tipo de
tarea, cuántas se hicieron, cuánto tardaron y cuánto pagaron. Sirve para
comparar antes/después de un cambio en el planificador sin mirar logs a ojo.

Solo cuenta lo hecho DURANTE el ensayo: la copia trae 200.000 tareas de la red
de verdad y si no se filtra, la media de difusiones sale mezclada con las de
GPU. La ventana se toma de la primera muestra de `resumen.json`.

USO
  python tools/medir_difusiones_cpu.py medidas/ensayos/cpu_dif_antes_...
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path


def _db(carpeta: Path) -> Path:
    cand = sorted(carpeta.glob("ledger/*/kukia.db"))
    if not cand:
        raise SystemExit(f"[medir] no hay kukia.db en {carpeta}")
    return cand[-1]


def _desde(carpeta: Path) -> float:
    """Epoch de inicio de la ventana: primera muestra del ensayo menos 2 min."""
    resumen = carpeta / "resumen.json"
    if resumen.exists():
        r = json.loads(resumen.read_text(encoding="utf-8"))
        serie = r.get("serie_completa") or []
        if serie:
            return float(serie[0]["t"]) - 120.0
    # Sin resumen: se deduce del nombre <etiqueta>_AAAAMMDD_HHMMSS
    try:
        sello = carpeta.name.rsplit("_", 2)[-2:]
        t = datetime.strptime("".join(sello), "%Y%m%d%H%M%S")
        return time.mktime(t.timetuple()) - 60.0
    except Exception:
        return 0.0


def _etiqueta(carpeta: Path) -> str:
    """Nombre del ensayo sin el sello de fecha ('cpu_dif_antes')."""
    partes = carpeta.name.rsplit("_", 2)
    if len(partes) == 3 and partes[1].isdigit() and partes[2].isdigit():
        return partes[0]
    return carpeta.name


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    carpeta = Path(argv[1]).resolve()
    # Copia a un temporal y se abre normal: en solo-lectura SQLite no puede
    # reproducir el WAL y suelta "attempt to write a readonly database" al
    # primer SELECT. Se copia el trio db/wal/shm, nunca se toca el original.
    origen = _db(carpeta)
    temp = Path(tempfile.mkdtemp(prefix="medir_"))
    for sufijo in ("", "-wal", "-shm"):
        if Path(str(origen) + sufijo).exists():
            shutil.copy2(str(origen) + sufijo, temp / (origen.name + sufijo))
    con = sqlite3.connect(str(temp / origen.name))
    con.row_factory = sqlite3.Row
    desde = _desde(carpeta)
    # La copia del ledger trae tareas de la red de verdad hechas hace un minuto:
    # si no se filtra por el nodo del ensayo, se cuelan cientos de tareas de GPU
    # en la media (comprobado: 589 filas donde el ensayo hizo 49). El worker del
    # ensayo se llama 'ensayo_<etiqueta>[dispositivo]'.
    patron = f"%{_etiqueta(carpeta)}%"
    nodos_ensayo = [r["id"] for r in con.execute(
        "SELECT id FROM nodos WHERE nombre LIKE ?", (patron,)).fetchall()]
    if not nodos_ensayo:
        raise SystemExit(f"[medir] no hay nodos del ensayo '{_etiqueta(carpeta)}' en el ledger")
    marcas = ",".join("?" * len(nodos_ensayo))

    def filas(sql, args=()):
        return con.execute(sql, args).fetchall()

    print(f"== {carpeta.name}")
    print(f"   ventana: desde {time.strftime('%H:%M:%S', time.localtime(desde))} "
          f"· nodo(s): {', '.join(nodos_ensayo)}")

    print("\n-- tareas terminadas por tipo (solo el ensayo) --")
    print(f"{'tipo':<17}{'n':>6}{'media s':>9}{'max s':>8}{'KIA':>12}")
    total = 0
    for r in filas(f"SELECT tipo, COUNT(*) n, AVG(duracion_ms) m, MAX(duracion_ms) x "
                   f"FROM tareas WHERE estado='ok' AND nodo_id IN ({marcas}) "
                   f"GROUP BY tipo ORDER BY n DESC", tuple(nodos_ensayo)):
        kia = con.execute(
            f"SELECT COALESCE(SUM(l.kuki),0) s FROM ledger l JOIN tareas t ON t.id=l.tarea_id "
            f"WHERE t.tipo=? AND t.estado='ok' AND t.nodo_id IN ({marcas}) AND l.kuki > 0",
            (r["tipo"], *nodos_ensayo)).fetchone()["s"]
        total += r["n"]
        print(f"{r['tipo']:<17}{r['n']:>6}{(r['m'] or 0)/1000:>9.1f}"
              f"{(r['x'] or 0)/1000:>8.1f}{kia:>12.6f}")
    print(f"{'TOTAL':<17}{total:>6}")

    print("\n-- difusiones en el nodo del ensayo --")
    dif = con.execute(f"SELECT COUNT(*) n, AVG(duracion_ms) m, MAX(duracion_ms) x "
                      f"FROM tareas WHERE tipo='difusion_calor' AND estado='ok' "
                      f"AND nodo_id IN ({marcas})", tuple(nodos_ensayo)).fetchone()
    print(f"  terminadas: {dif['n']}   media {(dif['m'] or 0)/1000:.1f} s   "
          f"max {(dif['x'] or 0)/1000:.1f} s")

    print("\n-- cola al terminar (estado del ledger de pruebas) --")
    for r in filas("SELECT tipo, estado, COUNT(*) n FROM tareas "
                   "WHERE estado NOT IN ('ok','fallo') GROUP BY tipo, estado ORDER BY n DESC"):
        print(f"  {r['tipo']:<17}{r['estado']:<12}{r['n']:>4}")

    cols = {c["name"] for c in filas("PRAGMA table_info(nodos)")}
    dev = "device" if "device" in cols else "'(sin columna)'"
    print("\n-- nodos --")
    for n in filas(f"SELECT id,nombre,vram_gb,tops,{dev} AS device FROM nodos"):
        print(f"  {n['id']}  {n['nombre']:<32} vram={n['vram_gb']} "
              f"tops={n['tops']} device={n['device'] or '(sin declarar)'}")
    con.close()

    resumen = carpeta / "resumen.json"
    if resumen.exists():
        r = json.loads(resumen.read_text(encoding="utf-8"))
        t = r.get("tareas", {})
        print("\n-- resumen del ensayo --")
        print(f"  minutos={r.get('minutos_medidos')} "
              f"ok_en_ventana={t.get('delta_ok_en_ventana')} fallidas={t.get('fallidas')} "
              f"reintentos={t.get('reintentos')} "
              f"parada={r.get('parada', {}).get('segundos_parada')}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
