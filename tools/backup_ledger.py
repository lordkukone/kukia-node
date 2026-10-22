#!/usr/bin/env python3
"""Copia de seguridad COHERENTE y VERIFICADA del ledger KUKIA.

Que hace, en orden:
  1. Copia el .db con la API de backup de SQLite (consistente aunque el Core
     este escribiendo; no hay que pararlo).
  2. Copia `sellos.jsonl` RECORTADO al numero que la propia base declara en
     `meta.sellos_n` (el fichero puede ir por delante, nunca por detras).
  3. Copia `sello_key.txt` por defecto: sin ella la copia no se puede verificar
     sola. Con `--con-claves` copia tambien `admin_token.txt`.
  4. Verifica la copia: recalcula la cadena y comprueba las firmas de los
     sellos. Una copia sin verificar no es una copia.
  5. Escribe `manifest.json` con sha256, totales y resultado.
  6. Borra las copias mas antiguas, dejando las ultimas N.

Uso:
    python tools/backup_ledger.py                          # backups/kukia_FECHA
    python tools/backup_ledger.py --destino D:/KUKIA_backups --mantener 14
    python tools/backup_ledger.py --sin-clave              # no copia la clave

Salida: 0 si la copia se hizo y verifico; 2/3/4 si fallo algo.
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

try:
    from ledger import sellos as S
except Exception as e:
    print(f"[backup] ERROR: no pude importar ledger/sellos.py ({e})")
    raise SystemExit(2)

DB = RAIZ / "coordinator" / "ledger" / "kukia.db"
SELLOS = RAIZ / "coordinator" / "sellos.jsonl"
CLAVE = RAIZ / "coordinator" / "sello_key.txt"
TOKEN = RAIZ / "coordinator" / "admin_token.txt"


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def copiar_db(destino: Path) -> None:
    for intento in (1, 2, 3):
        try:
            src = sqlite3.connect(str(DB), timeout=15)
            src.execute("PRAGMA busy_timeout=15000")
            src.execute("PRAGMA query_only=1")
            dst = sqlite3.connect(str(destino))
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            return
        except Exception:
            if intento == 3:
                raise
            time.sleep(2.0)


def n_sellos_meta(db: Path) -> int:
    con = sqlite3.connect(str(db))
    try:
        fila = con.execute("SELECT valor FROM meta WHERE clave='sellos_n'").fetchone()
        return int(float(fila[0])) if fila else 0
    finally:
        con.close()


def copiar_estado(carpeta: Path, con_claves: bool, sin_clave: bool) -> int:
    carpeta.mkdir(parents=True, exist_ok=True)
    db = carpeta / "kukia.db"
    for intento in range(6):
        if db.exists():
            db.unlink()
        copiar_db(db)
        n_meta = n_sellos_meta(db)
        lineas = []
        if SELLOS.exists():
            lineas = [l for l in SELLOS.read_text(encoding="utf-8").splitlines() if l.strip()]
        if n_meta > len(lineas):
            time.sleep(1.0)
            continue
        lineas = lineas[:n_meta] if n_meta else lineas
        (carpeta / "sellos.jsonl").write_text(
            "\n".join(lineas) + ("\n" if lineas else ""), encoding="utf-8")
        if not sin_clave and CLAVE.exists():
            shutil.copyfile(CLAVE, carpeta / "sello_key.txt")
            try:
                os.chmod(carpeta / "sello_key.txt", 0o600)
            except Exception:
                pass
        if con_claves and TOKEN.exists():
            shutil.copyfile(TOKEN, carpeta / "admin_token.txt")
            try:
                os.chmod(carpeta / "admin_token.txt", 0o600)
            except Exception:
                pass
        return len(lineas)
    raise RuntimeError("no se pudo copiar un estado coherente (sellos/DB)")


def verificar(carpeta: Path, sin_clave: bool) -> dict:
    clave = carpeta / "sello_key.txt"
    if not clave.exists():
        clave = CLAVE
    if not clave.exists():
        return {"ok": False, "problemas": ["falta la clave del sello"],
                "sellos": 0, "asientos": 0, "supply": 0.0, "clave_publica": ""}
    pub = S.publica_de(S.leer_clave(clave))
    con = sqlite3.connect(str(carpeta / "kukia.db"))
    con.row_factory = sqlite3.Row
    try:
        sellos = S.leer_sellos(carpeta / "sellos.jsonl")
        r = S.verificar_sellos(con, sellos, pub)
        supply = float(con.execute("SELECT COALESCE(SUM(kuki),0) s FROM ledger").fetchone()["s"] or 0.0)
        filas = int(con.execute("SELECT COUNT(*) c FROM ledger").fetchone()["c"])
        return {"ok": bool(r["ok"]), "problemas": (r.get("problemas") or [])[:10],
                "sellos": len(sellos), "asientos": filas,
                "supply": round(supply, 6), "clave_publica": pub}
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Copia verificada del ledger KUKIA")
    ap.add_argument("--destino", default="", help="carpeta donde guardar las copias")
    ap.add_argument("--con-claves", action="store_true",
                    help="copiar tambien admin_token.txt (la clave del sello va por defecto)")
    ap.add_argument("--sin-clave", action="store_true",
                    help="no copiar sello_key.txt (los sellos no se podran verificar solos)")
    ap.add_argument("--mantener", type=int, default=14, help="copias a conservar (defecto 14)")
    args = ap.parse_args()
    if not DB.exists():
        print(f"[backup] ERROR: no existe {DB}")
        return 2

    base = Path(args.destino) if args.destino else RAIZ / "backups"
    base.mkdir(parents=True, exist_ok=True)
    carpeta = base / ("kukia_" + time.strftime("%Y%m%d_%H%M%S"))
    if carpeta.exists():
        print(f"[backup] ERROR: {carpeta} ya existe")
        return 2
    print(f"[backup] destino: {carpeta}")

    try:
        n = copiar_estado(carpeta, args.con_claves, args.sin_clave)
        r = verificar(carpeta, args.sin_clave)
    except Exception as e:
        print(f"[backup] ERROR copiando: {type(e).__name__}: {e}")
        return 3
    if not r["ok"]:
        print("[backup] ERROR: la copia NO cuadra:", r["problemas"][:3])
        return 4

    manifiesto = {
        "fecha": time.strftime("%Y-%m-%d %H:%M:%S"),
        "origen": str(DB),
        "db": {"bytes": (carpeta / "kukia.db").stat().st_size,
               "sha256": sha256(carpeta / "kukia.db"),
               "asientos": r["asientos"], "saldo_total": r["supply"]},
        "sellos": {"fichero": "sellos.jsonl",
                   "sha256": sha256(carpeta / "sellos.jsonl"),
                   "total": r["sellos"], "en_meta": n,
                   "ultimo": (S.leer_sellos(carpeta / "sellos.jsonl") or [None])[-1]},
        "claves_incluidas": {
            "sello_key": bool((carpeta / "sello_key.txt").exists()),
            "admin_token": bool((carpeta / "admin_token.txt").exists()),
        },
        "verificacion": {"ok": True, "problemas": []},
        "clave_publica_sello": r["clave_publica"],
    }
    (carpeta / "manifest.json").write_text(
        json.dumps(manifiesto, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[backup] asientos: {r['asientos']} | saldo: {r['supply']:.6f} KIA | "
          f"sellos: {r['sellos']}")
    print("[backup] VERIFICADA: la copia cuadra con los sellos")

    if args.mantener > 0:
        copias = sorted((p for p in base.glob("kukia_*") if p.is_dir()), reverse=True)
        for vieja in copias[args.mantener:]:
            shutil.rmtree(vieja, ignore_errors=True)
            print(f"[backup] borrada copia antigua: {vieja.name}")
    if not (carpeta / "sello_key.txt").exists():
        print("[backup] AVISO: copia sin clave; los sellos no se podran verificar solos.")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
