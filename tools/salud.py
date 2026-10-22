"""Chequeo de salud de KUKIA: dice si la moneda esta sana AHORA.

Comprueba, en este orden:
  1. El Core responde (/stats y /ledger/integridad).
  2. La cadena de hashes y los sellos cuadran y los pagos no estan bloqueados.
  3. El ledger se esta escribiendo (minero entregando trabajo).
  4. Disco libre y tamano del .db.
  5. Antiguedad del ultimo sello.
  6. Emision dentro del presupuesto (aviso, no fallo).
  7. Antiguedad de la ultima copia de seguridad (si se le pasa --backups).

Uso:
  python tools/salud.py
  python tools/salud.py --backups D:/KUKIA_backups --json

Codigo de salida: 0 = todo bien (puede haber avisos), 1 = algun fallo.
"""
import argparse
import json
import shutil
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "coordinator" / "ledger" / "kukia.db"
SELLOS = RAIZ / "coordinator" / "sellos.jsonl"
CORE = "http://127.0.0.1:8300"
sys.path.insert(0, str(RAIZ))


def _get(ruta: str, timeout: float = 10.0):
    with urllib.request.urlopen(CORE + ruta, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main() -> int:
    global CORE
    ap = argparse.ArgumentParser(description="Salud de KUKIA")
    ap.add_argument("--core", default=CORE)
    ap.add_argument("--backups", default="", help="carpeta de copias para comprobar su antiguedad")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    CORE = args.core.rstrip("/")

    fallos, avisos, datos = [], [], {}

    # 1) Core vivo
    try:
        stats = _get("/stats")
        integ = _get("/ledger/integridad")
        datos["core"] = "ok"
    except Exception as e:
        print(f"FALLO  el Core no responde en {CORE}: {e}")
        return 1

    # 2) integridad
    if not integ.get("cadena_ok"):
        fallos.append(f"cadena de hashes rota: {integ.get('ids_alterados')}")
    if not integ.get("sellos_ok"):
        fallos.append(f"sellos no cuadran: {integ.get('problemas')}")
    if integ.get("pagos_bloqueados"):
        fallos.append("pagos BLOQUEADOS por integridad")
    datos["sellos"] = integ.get("sellos")
    datos["ultimo_sello"] = integ.get("ultimo_sello")

    # 3) el ledger se mueve (mtime del .db: se actualiza al commitear)
    edad_db = time.time() - DB.stat().st_mtime if DB.exists() else 1e9
    datos["segundos_sin_escribir"] = round(edad_db)
    if edad_db > 180:
        fallos.append(f"el ledger lleva {int(edad_db)} s sin escribir (minero parado?)")

    # 4) disco y tamano
    uso = shutil.disk_usage(DB.parent)
    libre_pct = 100.0 * uso.free / uso.total
    datos["disco_libre_pct"] = round(libre_pct, 1)
    datos["db_mb"] = round(DB.stat().st_size / 1e6, 1) if DB.exists() else 0
    libre_gb = uso.free / 1024 ** 3
    datos["disco_libre_gb"] = round(libre_gb, 1)
    if libre_pct < 10:
        # El porcentaje solo no dice nada: 5 % de un disco de 2 TB son 98 GB y
        # no es "casi lleno"; 5 % de uno de 256 GB son 13 GB y si lo es. Lo que
        # importa es cuanto queda de verdad para el ledger (crece) y las copias.
        if libre_gb < 20:
            fallos.append(f"disco casi lleno: {libre_pct:.1f} % libre ({libre_gb:.0f} GB)")
        else:
            avisos.append(f"disco al {libre_pct:.1f} % libre, pero quedan {libre_gb:.0f} GB")
    elif libre_pct < 20:
        avisos.append(f"disco al {libre_pct:.1f} % libre ({libre_gb:.0f} GB)")

    # 5) antiguedad del ultimo sello
    try:
        sys.path.insert(0, str(RAIZ))
        from ledger import sellos as S
        ss = S.leer_sellos(SELLOS)
        if ss:
            edad_sello = time.time() - float(ss[-1].get("ts") or 0)
            datos["sellos_total"] = len(ss)
            datos["segundos_desde_sello"] = round(edad_sello)
            if edad_sello > 3600:
                avisos.append(f"ultimo sello hace {int(edad_sello / 60)} min")
    except Exception as e:
        avisos.append(f"no se pudieron leer los sellos: {e}")

    # 6) emision
    emision = float(stats.get("emision_por_min") or 0)
    datos["emision_por_min"] = emision
    if emision and abs(emision - 1.3075) > 0.35:
        avisos.append(f"emision fuera de presupuesto: {emision} KIA/min (objetivo 1,3075)")

    # 7) copias
    if args.backups:
        copias = sorted(Path(args.backups).glob("kukia_*"))
        if not copias:
            fallos.append("no hay ninguna copia de seguridad en la carpeta indicada")
        else:
            edad_h = (time.time() - copias[-1].stat().st_mtime) / 3600.0
            datos["horas_desde_backup"] = round(edad_h, 1)
            if edad_h > 30:
                avisos.append(f"ultima copia hace {edad_h:.0f} h")

    estado = "FALLO" if fallos else ("AVISO" if avisos else "OK")
    if args.json:
        print(json.dumps({"estado": estado, "fallos": fallos, "avisos": avisos, "datos": datos},
                         ensure_ascii=False, indent=2))
    else:
        print(f"[salud] {estado}  |  sellos {datos.get('sellos')}  |  "
              f"db {datos.get('db_mb')} MB  |  disco {datos.get('disco_libre_pct')} % libre  |  "
              f"emision {datos.get('emision_por_min')} KIA/min")
        print(f"[salud] ultima escritura hace {datos.get('segundos_sin_escribir')} s  |  "
              f"ultimo sello hace {datos.get('segundos_desde_sello', '?')} s")
        for f in fallos:
            print(f"[salud] FALLO  {f}")
        for a in avisos:
            print(f"[salud] AVISO  {a}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
