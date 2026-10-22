#!/usr/bin/env python3
"""Replica del ledger KUKIA en OTRO disco, lista para arrancar a mano.

Para que sirve: hoy el ledger vive en `C:` y las copias de `backups/` tambien.
Si ese disco muere, mueren las dos. Esta herramienta mantiene una copia
COHERENTE, VERIFICADA y COMPARADA con el Core vivo en un disco distinto, con la
clave del sello y el token de admin al lado, para que un Core nuevo pueda
arrancar sobre ella sin pedir nada mas.

No toca el ledger real ni el Core en marcha: solo lee.

    python tools/replica_ledger.py                # copia nueva + comparar con el Core
    python tools/replica_ledger.py --comparar     # ¿la replica sigue siendo la misma red?
    python tools/replica_ledger.py --estado       # foto de la replica actual
    python tools/replica_ledger.py --destino E:/KUKIA_REPLICA

Estructura:
    <destino>/actual/{kukia.db, sellos.jsonl, sello_key.txt, admin_token.txt, replica.json}
    <destino>/anterior/   (la copia buena anterior, por si la nueva sale rara)

Salida: 0 si la replica queda verificada y coincide; 2 disco/ruta, 3 copia,
4 verificacion, 5 no es esta red. Con --comparar, 6 = misma red pero atrasada
(no esta al dia: no la trates como una copia de hoy).
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))
sys.path.insert(0, str(RAIZ))

import backup_ledger as BK  # noqa: E402  (copia coherente + verificacion)
from ledger import sellos as S  # noqa: E402

CORE = os.environ.get("KUKIA_CORE", "http://127.0.0.1:8300")
DESTINO_DEFECTO = Path(os.environ.get("KUKIA_REPLICA") or "D:/KUKIA_REPLICA")
# El Core paga ~0,05 KIA por tarea: entre la copia y la comparacion puede
# emitir unas decimas. 10 KIA es holgado y sigue cazando una replica de otro dia.
TOLERANCIA_SUPPLY = 10.0


def medir(carpeta: Path) -> dict:
    """Datos de la replica leidos de sus propios ficheros (sin Core)."""
    db = carpeta / "kukia.db"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        supply = float(con.execute(
            "SELECT COALESCE(SUM(kuki),0) s FROM ledger").fetchone()["s"] or 0.0)
        asientos = int(con.execute("SELECT COUNT(*) c FROM ledger").fetchone()["c"])
        n_hash = con.execute(
            "SELECT COUNT(*) c FROM ledger WHERE hash IS NOT NULL AND hash<>''").fetchone()["c"]
    finally:
        con.close()
    sellos = S.leer_sellos(carpeta / "sellos.jsonl")
    return {"supply": round(supply, 6), "asientos": asientos,
            "asientos_encadenados": int(n_hash),
            "sellos": len(sellos),
            "ultimo_sello": (sellos[-1].get("hash") if sellos else None)}


def estado_core() -> dict:
    """Lo que publica el Core vivo (None si no contesta)."""
    try:
        st = requests.get(f"{CORE}/stats", timeout=15).json()
    except Exception:
        return None
    datos = {"supply": float(st.get("supply_emitido") or 0.0),
             "sellos": int((st.get("integridad") or {}).get("sellos") or 0),
             "ultimo_sello": (st.get("integridad") or {}).get("ultimo_sello_hash"),
             "tareas_ok": st.get("tareas_ok")}
    try:
        v = requests.get(f"{CORE}/ledger/verificar", timeout=180).json()
        datos["asientos"] = int(v.get("asientos") or 0)
        datos["cadena_ok"] = bool(v.get("cadena_ok"))
    except Exception:
        datos["asientos"] = None
        datos["cadena_ok"] = None
    # CAOS 2026-10-22 (negativas): antes pedia solo los 30 ultimos sellos, asi que
    # una replica de hace dos dias (su sello era el nº 18 de 148) salia como
    # "replica de OTRA historia" cuando era la misma red, solo atrasada. El Core
    # publica hasta 200 por peticion: se piden 200 y se compara contra todos.
    try:
        sl = requests.get(f"{CORE}/ledger/sellos", params={"limite": 200}, timeout=60).json()
        lista = sl.get("sellos") if isinstance(sl, dict) else sl
        datos["hashes_sellos"] = [s.get("hash") for s in (lista or []) if isinstance(s, dict)]
    except Exception:
        datos["hashes_sellos"] = []
    return datos


def comparar(mio: dict, core: dict) -> dict:
    """¿La replica es la MISMA red que el Core, solo que un poco mas atras?

    Tres comprobaciones duras:
      1. La cadena de la replica verifica sola (lo hace BK.verificar antes).
      2. El ultimo sello de la replica lo ha publicado el Core (es historia suya).
      3. La replica no tiene MAS dinero ni MAS asientos que el Core.

    2026-10-22 (negativas): se distingue **atrasada** (misma red, sello conocido,
    solo va por detras) de **no es esta red** (su sello no aparece en la historia
    del Core). Antes todo lo viejo se contaba como "otra historia", y eso en un
    desastre real es un diagnostico falso: la copia buena de hace dos dias es tuya.
    """
    res = {"ok": True, "avisos": [], "dif_supply": None, "sello_conocido": None,
           "misma_red": None, "atraso": None, "ventana_sellos": 0}
    if core is None:
        res["avisos"].append("el Core no contesta: replica verificada sin comparar")
        res["comparado"] = False
        return res
    res["comparado"] = True
    dif = mio["supply"] - core["supply"]
    res["dif_supply"] = round(dif, 6)
    atraso_asientos = int((core.get("asientos") or 0) - mio["asientos"])
    res["atraso"] = {"asientos": atraso_asientos,
                     "supply": round(max(-dif, 0.0), 6)}
    if dif > 1e-6:
        res["ok"] = False
        res["avisos"].append(f"la replica tiene MAS suministro que el Core (+{dif:.6f} KIA): "
                             "no es una copia de esta red")
    if core.get("asientos") is not None and mio["asientos"] > core["asientos"]:
        res["ok"] = False
        res["avisos"].append(f"la replica tiene {mio['asientos']} asientos y el Core "
                             f"{core['asientos']}: no es esta red")
    hashes = set(core.get("hashes_sellos") or [])
    if core.get("ultimo_sello"):
        hashes.add(core["ultimo_sello"])
    res["ventana_sellos"] = len(hashes)
    if mio["ultimo_sello"]:
        conocido = mio["ultimo_sello"] in hashes
        res["sello_conocido"] = conocido
        res["misma_red"] = conocido
        if not conocido:
            res["ok"] = False
            total = core.get("sellos") or res["ventana_sellos"]
            extra = (" (ojo: el Core solo publica los 200 ultimos; si la replica es "
                     "mas vieja que eso, tampoco se podria demostrar)"
                     if total > res["ventana_sellos"] else "")
            res["avisos"].append(
                "no puedo demostrar que sea la misma red: su ultimo sello no esta entre "
                f"los {res['ventana_sellos']} que publica el Core ({total} en total){extra}")
        elif atraso_asientos > 0:
            res["avisos"].append(
                f"misma red, pero ATRASADA: le faltan {atraso_asientos} asientos y "
                f"{max(-dif, 0.0):.6f} KIA hasta el Core")
    else:
        res["ok"] = False
        res["avisos"].append("la replica no tiene sellos con los que demostrar que es esta red")
    return res


def actualizar(base: Path) -> int:
    nueva = base / "nueva"
    actual = base / "actual"
    anterior = base / "anterior"
    if nueva.exists():
        shutil.rmtree(nueva, ignore_errors=True)

    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # CAOS 2026-10-22 (negativas): con el disco de la replica desconectado
        # esto era un traceback de Python. Ahora se dice lo que pasa.
        print(f"[replica] ERROR: no puedo escribir en {base} ({e.strerror or e}).")
        print("[replica] El disco de la replica no responde. No se ha tocado nada.")
        return 2

    print(f"[replica] copiando el estado a {nueva} ...")
    t0 = time.time()
    try:
        n_sellos = BK.copiar_estado(nueva, con_claves=True, sin_clave=False)
    except Exception as e:
        print(f"[replica] ERROR copiando: {type(e).__name__}: {e}")
        return 3
    print(f"[replica] copiados {n_sellos} sellos en {time.time() - t0:.1f} s")

    print("[replica] verificando cadena y firmas de la copia ...")
    r = BK.verificar(nueva, sin_clave=False)
    if not r.get("ok"):
        print(f"[replica] ERROR: la copia NO cuadra: {r.get('problemas')}")
        print(f"[replica] la replica anterior sigue intacta en {actual}")
        return 4

    mio = medir(nueva)
    core = estado_core()
    cmp_ = comparar(mio, core)
    print(f"[replica] suministro={mio['supply']:.6f} asientos={mio['asientos']} "
          f"sellos={mio['sellos']}")
    if core:
        print(f"[replica] Core vivo: suministro={core['supply']:.6f} "
              f"asientos={core['asientos']} sellos={core['sellos']}")
    for a in cmp_["avisos"]:
        print(f"[replica] aviso: {a}")
    if not cmp_["ok"]:
        print("[replica] ERROR: la copia no es esta red. NO se promociona.")
        print(f"[replica] la replica anterior sigue intacta en {actual}")
        return 5

    manifiesto = {
        "fecha": time.strftime("%Y-%m-%d %H:%M:%S"),
        "origen": str(BK.DB),
        "core": CORE,
        "verificado": True,
        "db_sha256": BK.sha256(nueva / "kukia.db"),
        "db_bytes": (nueva / "kukia.db").stat().st_size,
        **mio,
        "comparacion": cmp_,
        "claves": {"sello_key": (nueva / "sello_key.txt").exists(),
                   "admin_token": (nueva / "admin_token.txt").exists()},
    }
    (nueva / "replica.json").write_text(
        json.dumps(manifiesto, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Promocion: la nueva pasa a ser 'actual'; la que habia se guarda como
    # 'anterior' (una sola, la de ayer). Asi un fallo se deshace en un paso.
    if anterior.exists():
        shutil.rmtree(anterior, ignore_errors=True)
    if actual.exists():
        actual.rename(anterior)
    nueva.rename(actual)
    print(f"[replica] LISTA en {actual}  (anterior: {anterior / 'kukia.db'})")
    print(f"[replica] para levantar la red sobre ella: ARRANCAR_REPLICA.cmd")
    return 0


def cmd_comparar(base: Path) -> int:
    actual = base / "actual"
    if not (actual / "kukia.db").exists():
        print(f"[replica] no hay replica en {actual}")
        return 2
    man = {}
    nota_man = "sin manifiesto"
    f_man = actual / "replica.json"
    if f_man.exists():
        try:
            man = json.loads(f_man.read_text(encoding="utf-8"))
            nota_man = man.get("fecha") or "manifiesto sin fecha"
        except Exception:
            nota_man = "manifiesto ILEGIBLE"
    mio = medir(actual)
    core = estado_core()
    cmp_ = comparar(mio, core)
    print(f"replica : suministro={mio['supply']:.6f} asientos={mio['asientos']} "
          f"sellos={mio['sellos']}  ({nota_man})")
    if core is None:
        print(f"Core    : NO CONTESTA en {CORE}")
    else:
        print(f"Core    : suministro={core['supply']:.6f} asientos={core['asientos']} "
              f"sellos={core['sellos']} cadena_ok={core['cadena_ok']}")
    for a in cmp_["avisos"]:
        print(f"aviso   : {a}")
    if not cmp_["ok"]:
        print("RESULTADO: NO coinciden — NO se promociona")
        return 1
    if not cmp_.get("comparado"):
        print("RESULTADO: replica verificada, sin Core con el que comparar")
        return 0
    atraso = cmp_.get("atraso") or {}
    if atraso.get("asientos", 0) > 0:
        print(f"RESULTADO: misma red, pero ATRASADA ({atraso['asientos']} asientos y "
              f"{atraso.get('supply', 0.0):.6f} KIA por detras del Core)")
        # Se separa "va un poco por detras porque acabo de copiarla" (normal,
        # exit 0) de "esta copia es de hace dias" (exit 6, no te fies).
        if atraso.get("supply", 0.0) > TOLERANCIA_SUPPLY:
            print("RESULTADO: NO esta al dia. No la uses como si fuera de hoy.")
            return 6
    else:
        print("RESULTADO: la replica es la misma red (puede ir un poco atras)")
    return 0


def cmd_estado(base: Path) -> int:
    for nombre in ("actual", "anterior"):
        carpeta = base / nombre
        db = carpeta / "kukia.db"
        if not db.exists():
            print(f"{nombre}: (no hay)")
            continue
        man = {}
        f = carpeta / "replica.json"
        if f.exists():
            try:
                man = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass
        mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(db.stat().st_mtime))
        print(f"{nombre}: {db.stat().st_size / 1e6:.1f} MB  copiada {mtime}  "
              f"fecha_manifiesto={man.get('fecha')}  verificado={man.get('verificado')}  "
              f"suministro={man.get('supply')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Replica verificada del ledger KUKIA")
    ap.add_argument("--destino", default=str(DESTINO_DEFECTO),
                    help=f"carpeta de la replica (defecto {DESTINO_DEFECTO})")
    ap.add_argument("--comparar", action="store_true", help="solo comparar con el Core vivo")
    ap.add_argument("--estado", action="store_true", help="foto de la replica")
    args = ap.parse_args()
    base = Path(args.destino)
    if args.estado:
        return cmd_estado(base)
    if args.comparar:
        return cmd_comparar(base)
    if not BK.DB.exists():
        print(f"[replica] ERROR: no existe {BK.DB}")
        return 2
    return actualizar(base)


if __name__ == "__main__":
    sys.exit(main())
