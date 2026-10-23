#!/usr/bin/env python3
"""Trae la cadena de la REPLICA al ledger original (vuelta del failover).

Cuando la red ha estado corriendo sobre `D:\\KUKIA_REPLICA`, el original de `C:`
se ha quedado atras. Volver a el sin mas perderia las tareas y pagos de ese
rato. Esto los trae, sin inventar nada:

  1. Exige que NO haya ningun Core contestando en 8300 (nada de escribir con el
     Core vivo; ni el original ni la replica).
  2. Copia el `.db` de la replica a un temporal y comprueba el sha256.
  3. Guarda el original tal cual esta (`kukia.db.predrill_AAAAAMMDD`) antes de
     sustituirlo. No borra nada: si ya existe, no lo pisa.
  4. Sustituye el `.db` y el `sellos.jsonl` (atomico con os.replace).
  5. Verifica la cadena entera y las firmas de los sellos YA en su sitio. Si algo
     no cuadra, sale con error y dice de donde tirar (las copias guardadas).

Uso:
    python tools/volver_al_original.py --ver     # solo comprueba, no escribe
    python tools/volver_al_original.py           # hace la vuelta

Despues, arranca el Core de siempre (coordinator/). No lo arranca este script.
"""
import argparse
import hashlib
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from ledger import sellos as S  # noqa: E402

DB = RAIZ / "coordinator" / "ledger" / "kukia.db"
SELLOS = RAIZ / "coordinator" / "sellos.jsonl"
CLAVE = RAIZ / "coordinator" / "sello_key.txt"
DESTINO_DEFECTO = Path(os.environ.get("KUKIA_REPLICA") or "D:/KUKIA_REPLICA")
CORE = os.environ.get("KUKIA_CORE", "http://127.0.0.1:8300")


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def core_vivo() -> bool:
    try:
        return requests.get(f"{CORE}/stats", timeout=5).status_code == 200
    except Exception:
        return False


def guardar_copia(origen: Path, destino: Path, unico: Path) -> Path:
    """Copia origen sin pisar nunca una copia distinta que ya exista."""
    if destino.exists():
        if sha256(destino) == sha256(origen):
            return destino
        destino = unico
    if not destino.exists():
        shutil.copyfile(origen, destino)
    return destino


def ultimo_sello(ruta: Path) -> str | None:
    try:
        lista = S.leer_sellos(ruta)
        return lista[-1].get("hash") if lista else None
    except Exception:
        return None


def verificar(db: Path, sellos: Path) -> dict:
    """Cadena encadenada + firmas de los sellos, de la base que se le pase.

    Si la base no se puede leer (copia a medias, fichero truncado), NO se lanza
    la excepcion: se devuelve cadena_ok=False con el motivo. Este es un camino
    de emergencia (volver al original tras un failover): un traceback aqui deja
    al operador sin saber si la replica vale, y lo prudente es negarse.
    """
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        prev, alterados, n = "", [], 0
        for r in con.execute("SELECT id,tarea_id,nodo_id,kuki,motivo,ts,hash_prev,hash "
                             "FROM ledger ORDER BY id"):
            if not r["hash"]:
                continue
            base = (f"{r['tarea_id']}|{r['nodo_id']}|{r['kuki']}|{r['motivo']}|"
                    f"{r['ts']}|{r['hash_prev'] or ''}")
            if hashlib.sha256(base.encode()).hexdigest() != r["hash"] or \
                    (r["hash_prev"] or "") != prev:
                alterados.append(r["id"])
            prev = r["hash"]
            n += 1
        supply = float(con.execute("SELECT COALESCE(SUM(kuki),0) s FROM ledger").fetchone()["s"])
        asientos = int(con.execute("SELECT COUNT(*) c FROM ledger").fetchone()["c"])
        pub = S.publica_de(S.leer_clave(CLAVE))
        lista = S.leer_sellos(sellos)
        r = S.verificar_sellos(con, lista, pub)
        return {"cadena_ok": not alterados, "alterados": alterados[:10],
                "encadenados": n, "asientos": asientos,
                "supply": round(supply, 6), "sellos": len(lista),
                "sellos_ok": bool(r["ok"]), "problemas": (r.get("problemas") or [])[:5]}
    except sqlite3.DatabaseError as e:
        # Base ilegible: se trata como replica que no cuadra, nunca como buena.
        return {"cadena_ok": False, "alterados": [], "encadenados": 0,
                "asientos": 0, "supply": 0.0, "sellos": 0, "sellos_ok": False,
                "problemas": [f"base ilegible: {type(e).__name__}: {e}"]}
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Vuelta verificada de la replica al original")
    ap.add_argument("--destino", default=str(DESTINO_DEFECTO))
    ap.add_argument("--ver", action="store_true", help="solo comprobar, no escribir nada")
    args = ap.parse_args()

    rep = Path(args.destino) / "actual"
    db_rep = rep / "kukia.db"
    sellos_rep = rep / "sellos.jsonl"
    if not db_rep.exists() or not sellos_rep.exists():
        print(f"[vuelta] ERROR: no hay replica en {rep}")
        return 2
    if core_vivo():
        print(f"[vuelta] El Core SIGUE CONTESTANDO en {CORE}.")
        print("[vuelta] Para el (o para la replica) antes de traer la cadena.")
        return 3

    vivo = verificar(db_rep, sellos_rep)
    print(f"[vuelta] replica : suministro={vivo['supply']:.6f} asientos={vivo['asientos']} "
          f"sellos={vivo['sellos']} cadena_ok={vivo['cadena_ok']} sellos_ok={vivo['sellos_ok']}")
    if not (vivo["cadena_ok"] and vivo["sellos_ok"]):
        print(f"[vuelta] ERROR: la replica no cuadra: {vivo['problemas']} / {vivo['alterados']}")
        return 4

    actual = verificar(DB, SELLOS) if DB.exists() else None
    if actual:
        print(f"[vuelta] original: suministro={actual['supply']:.6f} asientos={actual['asientos']} "
              f"sellos={actual['sellos']}")
        if actual["asientos"] >= vivo["asientos"]:
            print("[vuelta] El original ya esta igual o por delante: no hay nada que traer.")
            return 0
        # CAOS 2026-10-22 (negativas): antes solo se miraba el numero de
        # asientos. Una replica que se separo del original (dos Cores con la
        # misma clave) puede tener MAS asientos y no ser su continuacion: eso
        # sustituia el original por una historia que no viene de el. Ahora se
        # exige que la replica contenga el ultimo sello firmado del original.
        sello_orig = ultimo_sello(SELLOS)
        hashes_rep = {s.get("hash") for s in S.leer_sellos(sellos_rep)}
        if sello_orig and sello_orig not in hashes_rep:
            print("[vuelta] ERROR: la replica NO desciende del original.")
            print(f"[vuelta]         su ultimo sello ({sello_orig[:16]}...) no esta en la replica,")
            print("[vuelta]         aunque la replica tenga mas asientos: son dos historias.")
            print("[vuelta]         No se sustituye nada. Revisa cual es la buena.")
            return 7
        if not actual["cadena_ok"] or not actual["sellos_ok"]:
            print("[vuelta] AVISO: el original ya no verifica (cadena o sellos). "
                  "La replica si verifica; se guarda copia del original antes de tocar.")
    if args.ver:
        print("[vuelta] --ver: comprobado, no se ha escrito nada.")
        return 0

    marca = time.strftime("%Y%m%d")
    con_hora = time.strftime("%Y%m%d_%H%M%S")
    tmp = DB.with_suffix(".db.nuevo")
    print("[vuelta] copiando la replica a un temporal ...")
    shutil.copyfile(db_rep, tmp)
    if sha256(tmp) != sha256(db_rep):
        tmp.unlink(missing_ok=True)
        print("[vuelta] ERROR: la copia no coincide con la replica.")
        return 5

    # CAOS 2026-10-22 (negativas): la copia de seguridad estaba fechada solo por
    # dia, asi que una SEGUNDA vuelta el mismo dia no guardaba el estado que iba
    # a destruir (el nombre ya existia y no se pisaba). Ahora, si el nombre del
    # dia esta ocupado por un estado DISTINTO, se usa uno con hora: nunca se
    # sustituye nada sin tener copia de lo que habia.
    guarda_db = guardar_copia(DB, DB.with_suffix(f".db.predrill_{marca}"),
                              DB.with_suffix(f".db.predrill_{con_hora}"))
    guarda_sellos = guardar_copia(SELLOS, SELLOS.with_name(f"sellos.predrill_{marca}.jsonl"),
                                  SELLOS.with_name(f"sellos.predrill_{con_hora}.jsonl"))
    print(f"[vuelta] original guardado en {guarda_db.name}")
    print(f"[vuelta] sellos guardados en {guarda_sellos.name}")

    os.replace(tmp, DB)
    shutil.copyfile(sellos_rep, SELLOS)
    print("[vuelta] sustituido. Verificando en su sitio ...")

    fin = verificar(DB, SELLOS)
    print(f"[vuelta] resultado: suministro={fin['supply']:.6f} asientos={fin['asientos']} "
          f"sellos={fin['sellos']} cadena_ok={fin['cadena_ok']} sellos_ok={fin['sellos_ok']}")
    if not (fin["cadena_ok"] and fin["sellos_ok"]):
        print(f"[vuelta] ERROR: la base en su sitio NO cuadra. Restaura "
              f"{guarda_db.name} y {guarda_sellos.name} (estan en coordinator/).")
        return 6
    print("[vuelta] LISTO. Arranca el Core de siempre desde coordinator/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
