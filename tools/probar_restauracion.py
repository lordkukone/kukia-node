#!/usr/bin/env python3
"""Prueba de RESTAURACION real de una copia del ledger KUKIA.

No toca la base real ni el Core en marcha: copia la copia a una carpeta
temporal, comprueba sus hashes, arranca un Core aislado contra ella y verifica
que responde con el mismo suministro y la cadena/sellos intactos.

Uso:
    python tools/probar_restauracion.py                  # ultima copia de backups/
    python tools/probar_restauracion.py --copia backups/kukia_20261020_133054
    python tools/probar_restauracion.py --dejar          # conserva la temporal

Salida: 0 si la restauracion funciona; 1/2/3 si falla.
"""
import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent.parent
COORD = RAIZ / "coordinator"
BACKUPS = RAIZ / "backups"
sys.path.insert(0, str(RAIZ))

try:
    from ledger import sellos as S
except Exception as e:
    print(f"[restauracion] ERROR: no pude importar ledger/sellos.py ({e})")
    raise SystemExit(2)


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def puerto_libre() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def verificar_offline(carpeta: Path) -> dict:
    """Comprueba hashes del manifiesto + cadena + firmas, sin arrancar nada."""
    man = carpeta / "manifest.json"
    if not man.exists():
        raise RuntimeError("la copia no tiene manifest.json")
    d = json.loads(man.read_text(encoding="utf-8"))
    db = carpeta / "kukia.db"
    sellos = carpeta / "sellos.jsonl"
    if not db.exists() or not sellos.exists():
        raise RuntimeError("faltan kukia.db o sellos.jsonl")
    if d.get("db", {}).get("sha256") and sha256(db) != d["db"]["sha256"]:
        raise RuntimeError("el sha256 de kukia.db NO coincide con el manifiesto")
    if d.get("sellos", {}).get("sha256") and sha256(sellos) != d["sellos"]["sha256"]:
        raise RuntimeError("el sha256 de sellos.jsonl NO coincide con el manifiesto")
    clave = carpeta / "sello_key.txt"
    if not clave.exists():
        raise RuntimeError("la copia no incluye sello_key.txt: no se puede verificar sola")
    pub = S.publica_de(S.leer_clave(clave))
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        r = S.verificar_sellos(con, S.leer_sellos(sellos), pub)
        asientos = con.execute("SELECT COUNT(*) c FROM ledger").fetchone()["c"]
        supply = float(con.execute("SELECT COALESCE(SUM(kuki),0) s FROM ledger").fetchone()["s"] or 0)
    finally:
        con.close()
    return {"ok": bool(r.get("ok")), "problemas": r.get("problemas") or [],
            "sellos": len(S.leer_sellos(sellos)), "asientos": int(asientos),
            "supply": supply, "clave_publica": pub, "manifest": d}


def main() -> int:
    ap = argparse.ArgumentParser(description="Prueba de restauracion de una copia KUKIA")
    ap.add_argument("--copia", default="", help="carpeta de la copia (por defecto, la ultima)")
    ap.add_argument("--dejar", action="store_true", help="conservar la carpeta temporal")
    args = ap.parse_args()

    copia = Path(args.copia) if args.copia else (
        sorted((p for p in BACKUPS.glob("kukia_*") if p.is_dir()), reverse=True) or [None])[0]
    if not copia or not copia.exists():
        print("[restauracion] ERROR: no hay ninguna copia en backups/")
        return 2
    print(f"[restauracion] copia: {copia}")

    try:
        off = verificar_offline(copia)
    except Exception as e:
        print(f"[restauracion] ERROR verificando la copia: {type(e).__name__}: {e}")
        return 2
    print(f"[restauracion] offline: {off['sellos']} sellos, {off['asientos']} asientos, "
          f"{off['supply']:.6f} KIA, firma={'OK' if off['ok'] else 'MAL'}")
    if not off["ok"]:
        print(f"[restauracion] ERROR: la cadena/sellos no cuadran: {off['problemas'][:3]}")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="kukia_restauracion_"))
    try:
        for nombre in ("kukia.db", "sellos.jsonl", "sello_key.txt"):
            shutil.copyfile(copia / nombre, tmp / nombre)
        token = tmp / "admin_token.txt"
        token.write_text("restauracion-" + os.urandom(16).hex(), encoding="utf-8")
        puerto = puerto_libre()
        base = f"http://127.0.0.1:{puerto}"
        entorno = dict(os.environ)
        entorno.update({
            "KUKIA_DB": str(tmp / "kukia.db"),
            "KUKIA_SELLO_KEY": str(tmp / "sello_key.txt"),
            "KUKIA_SELLOS_FILE": str(tmp / "sellos.jsonl"),
            "KUKIA_ADMIN_TOKEN": str(token),
            "KUKIA_AUTO_TAREAS": "0",
            "KUKIA_ARCHIVAR": "0",
            "KUKIA_SELLO_SEG": "3600",
            "KUKIA_PODA_NODOS_H": "0",
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
        })
        log = open(tmp / "core.log", "w", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "kukia_core:app",
             "--host", "127.0.0.1", "--port", str(puerto)],
            cwd=str(COORD), env=entorno, stdout=log, stderr=subprocess.STDOUT)
        try:
            listo = False
            t0 = time.time()
            while time.time() - t0 < 180:
                if proc.poll() is not None:
                    break
                try:
                    if requests.get(f"{base}/stats", timeout=3).status_code == 200:
                        listo = True
                        break
                except Exception:
                    pass
                time.sleep(1.0)
            if not listo:
                print("[restauracion] ERROR: el Core restaurado no arranco")
                print((tmp / "core.log").read_text(encoding="utf-8", errors="replace")[-2000:])
                return 3

            stats = requests.get(f"{base}/stats", timeout=20).json()
            supply = float(stats.get("supply_emitido") or 0)
            dif = abs(supply - off["supply"])
            integ = requests.get(f"{base}/ledger/integridad", timeout=60).json()
            saldos = requests.get(f"{base}/saldos", timeout=60).json().get("saldos") or {}
            print(f"[restauracion] Core arriba: supply={supply:.6f} KIA "
                  f"(diferencia {dif:.9f}), nodos={stats.get('nodos')}, "
                  f"cadena={integ.get('cadena_ok')}, sellos={integ.get('sellos_ok')}, "
                  f"saldos={len(saldos)}")
            if dif > 1e-6:
                print("[restauracion] ERROR: el suministro restaurado no coincide")
                return 1
            if integ.get("cadena_ok") is not True or integ.get("sellos_ok") is not True:
                print(f"[restauracion] ERROR: integridad rota: {integ.get('problemas')}")
                return 1
            if integ.get("pagos_bloqueados") is not False:
                print("[restauracion] ERROR: los pagos quedaron bloqueados")
                return 1
            if not saldos:
                print("[restauracion] ERROR: no se reconstruyeron los saldos")
                return 1
            print("[restauracion] OK: la copia arranca, paga y verifica igual que el original")
            return 0
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=15)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            log.close()
    finally:
        if args.dejar:
            print(f"[restauracion] carpeta conservada: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
