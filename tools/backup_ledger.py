#!/usr/bin/env python3
"""Copia de seguridad COHERENTE y VERIFICADA del ledger KUKIA.

Que hace, en orden:
  1. Copia el .db con la API de backup de SQLite (consistente aunque el Core
     este escribiendo; no hay que pararlo).
  2. Copia `sellos.jsonl` RECORTADO al numero que la propia base declara en
     `meta.sellos_n` (el fichero puede ir por delante, nunca por detras).
  3. Escribe `sello_pub.txt` con la clave PUBLICA del sello: con eso la copia se
     verifica sola, sin llevarse la clave privada. La privada NO se copia salvo
     que se pida (`--con-clave`), y el token de admin tampoco (`--con-token`).
  4. Verifica la copia: recalcula la cadena y comprueba las firmas de los
     sellos. Una copia sin verificar no es una copia.
  5. Escribe `manifest.json` con sha256, totales y resultado.
  6. Borra las copias mas antiguas, dejando las ultimas N.

Uso:
    python tools/backup_ledger.py                          # backups/kukia_FECHA
    python tools/backup_ledger.py --destino D:/KUKIA_backups --mantener 14
    python tools/backup_ledger.py --con-clave              # SI copia la privada

Desde el 28-10-2026 la clave privada NO viaja en las copias por defecto: cada
copia en D: dejaba otra copia en claro de la clave que firma los sellos, en un
disco que no es el del Core. Las copias se verifican con `sello_pub.txt`, que
es publico; para ARRANCAR desde una copia hace falta la clave, y esa se restaura
desde el paquete cifrado de claves (tools/cifrar_claves.py).

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


def copiar_estado(carpeta: Path, con_clave: bool = False,
                  con_token: bool = False) -> int:
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
        # Clave PUBLICA: es lo que hace falta para verificar la copia, y no es un
        # secreto. Antes se copiaba la privada "para que la copia se pudiera
        # verificar sola"; con la publica basta y no hay nada que robar.
        if CLAVE.exists():
            (carpeta / "sello_pub.txt").write_text(
                S.publica_de(S.leer_clave(CLAVE)), encoding="utf-8")
        # Clave PRIVADA: solo si se pide a proposito (28-10-2026).
        if con_clave and CLAVE.exists():
            shutil.copyfile(CLAVE, carpeta / "sello_key.txt")
            try:
                os.chmod(carpeta / "sello_key.txt", 0o600)
            except Exception:
                pass
        if con_token and TOKEN.exists():
            shutil.copyfile(TOKEN, carpeta / "admin_token.txt")
            try:
                os.chmod(carpeta / "admin_token.txt", 0o600)
            except Exception:
                pass
        return len(lineas)
    raise RuntimeError("no se pudo copiar un estado coherente (sellos/DB)")


def clave_publica(carpeta: Path) -> tuple[str, str]:
    """(publica_hex, de donde sale). Prefiere lo que no es secreto.

    Orden: la publica que trae la copia, luego la privada si alguien la metio,
    y por ultimo la del Core de esta maquina (asi una copia antigua, que no
    trae ni una ni otra, se sigue pudiendo verificar aqui).
    """
    f_pub = carpeta / "sello_pub.txt"
    if f_pub.exists():
        pub = f_pub.read_text(encoding="utf-8").strip()
        if pub:
            return pub, "sello_pub.txt"
    f_priv = carpeta / "sello_key.txt"
    if f_priv.exists():
        return S.publica_de(S.leer_clave(f_priv)), "sello_key.txt"
    if CLAVE.exists():
        return S.publica_de(S.leer_clave(CLAVE)), "la clave del Core"
    return "", ""


def verificar(carpeta: Path, publica: str = "") -> dict:
    """Verifica la copia contra una publica concreta, si se pasa.

    `publica` existe para el caso de la replica: hay que comprobar sus sellos
    contra la clave del ORIGINAL, no contra la que traiga ella misma, o una
    replica de otra red cuadraria consigo misma.
    """
    if publica:
        pub, de_donde = publica, "la clave del original"
    else:
        pub, de_donde = clave_publica(carpeta)
    if not pub:
        return {"ok": False,
                "problemas": ["falta la clave publica del sello (sello_pub.txt) y no "
                              "hay clave del Core con la que comparar"],
                "sellos": 0, "asientos": 0, "supply": 0.0,
                "clave_publica": "", "clave_de": de_donde}
    con = sqlite3.connect(str(carpeta / "kukia.db"))
    con.row_factory = sqlite3.Row
    try:
        sellos = S.leer_sellos(carpeta / "sellos.jsonl")
        r = S.verificar_sellos(con, sellos, pub)
        supply = float(con.execute("SELECT COALESCE(SUM(kuki),0) s FROM ledger").fetchone()["s"] or 0.0)
        filas = int(con.execute("SELECT COUNT(*) c FROM ledger").fetchone()["c"])
        problemas = list(r.get("problemas") or [])
        ok = bool(r["ok"])
        # AUDITORIA 2026-10-25 (hallazgo B1): con CERO sellos, verificar_sellos
        # devuelve ok (no hay nada que lo contradiga) y esta herramienta decia
        # "copia VERIFICADA". Pero una copia sin sellos no demuestra nada del
        # historial: si el fichero falta o esta vacio, la copia no vale como
        # prueba. Ahora se marca como NO verificada y el script sale con 4.
        if not sellos:
            ok = False
            problemas.insert(0, "la copia no trae sellos: no se puede demostrar "
                                "el historial (sellos.jsonl vacio o ausente)")
        return {"ok": ok, "problemas": problemas[:10],
                "sellos": len(sellos), "asientos": filas,
                "supply": round(supply, 6), "clave_publica": pub,
                "clave_de": de_donde}
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Copia verificada del ledger KUKIA")
    ap.add_argument("--destino", default="", help="carpeta donde guardar las copias")
    ap.add_argument("--con-clave", action="store_true",
                    help="copiar sello_key.txt (la clave PRIVADA) dentro de la copia")
    ap.add_argument("--con-token", action="store_true",
                    help="copiar admin_token.txt dentro de la copia")
    ap.add_argument("--con-claves", action="store_true",
                    help="copiar la clave privada Y el token (la copia deja de ser "
                         "inocua: cifrala con tools/cifrar_claves.py)")
    ap.add_argument("--sin-clave", action="store_true",
                    help="ya no hace nada: sin --con-clave no se copia la clave")
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
        n = copiar_estado(carpeta,
                          con_clave=args.con_clave or args.con_claves,
                          con_token=args.con_token or args.con_claves)
        r = verificar(carpeta)
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
            "sello_pub": bool((carpeta / "sello_pub.txt").exists()),
        },
        "verificacion": {"ok": True, "problemas": [],
                         "verificada_con": r.get("clave_de") or ""},
        "clave_publica_sello": r["clave_publica"],
    }
    (carpeta / "manifest.json").write_text(
        json.dumps(manifiesto, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[backup] asientos: {r['asientos']} | saldo: {r['supply']:.6f} KIA | "
          f"sellos: {r['sellos']}")
    print(f"[backup] VERIFICADA con {r.get('clave_de') or 'la clave del Core'}: "
          "la copia cuadra con los sellos")

    if args.mantener > 0:
        copias = sorted((p for p in base.glob("kukia_*") if p.is_dir()), reverse=True)
        for vieja in copias[args.mantener:]:
            shutil.rmtree(vieja, ignore_errors=True)
            print(f"[backup] borrada copia antigua: {vieja.name}")
    if (carpeta / "sello_key.txt").exists():
        print("[backup] AVISO: esta copia lleva la clave PRIVADA en claro. Guardala "
              "cifrada con tools\\cifrar_claves.py o pasala a un paquete de claves.")
    else:
        print("[backup] sin clave privada (lo normal): se verifica con sello_pub.txt. "
              "Para ARRANCAR desde esta copia hace falta la clave del paquete de claves.")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
