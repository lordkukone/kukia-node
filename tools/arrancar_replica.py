#!/usr/bin/env python3
"""Arranca la RED sobre la replica del ledger (a mano, cuando el principal cae).

Que hace, en orden, y sin saltarse ningun paso:
  1. Comprueba que hay replica y que su manifiesto dice 'verificado'.
  2. Se niega a arrancar si el Core principal sigue contestando en 8300
     (dos Cores = dos historias; eso es doble gasto, no alta disponibilidad).
  3. Recalcula la cadena y los sellos de la replica. Si no cuadran, no arranca.
  4. Se niega si en disco hay un estado MAS NUEVO que la replica (el original,
     o una copia mas reciente): arrancar sobre la copia vieja seria perder esos
     asientos sin enterarse. Se salta solo con --aceptar-atraso, y dice cuanto.
  5. Se niega si la replica dice saber mas que el original pero NO contiene su
     ultimo sello firmado: no es su continuacion, son dos historias.
  6. Levanta el Core apuntando a la replica (mismo puerto 8300, para que el
     panel, el minero y el tunel no cambien ni una linea). La clave del sello
     se toma del original (`coordinator/sello_key.txt`): desde el 28-10-2026 la
     replica no lleva una copia propia de la clave privada.

Codigos de salida: 0 ok, 2 no hay replica, 3 manifiesto no verificado, 4 Core
vivo, 5 cadena/sellos rotos, 6 falta la clave, 7 hay un estado mas nuevo en
disco, 8 la replica no desciende del original, 9 la replica esta firmada con
otra clave (no es esta red).

Uso:
    python tools/arrancar_replica.py                # replica en D:/KUKIA_REPLICA
    python tools/arrancar_replica.py --puerto 8300
    python tools/arrancar_replica.py --forzar       # si el 8300 contesta pero
                                                    # sabes que es un zombi

Que se pierde: lo que el Core principal hiciera DESPUES de la ultima copia
(tareas en vuelo y pagos de ese rato). La cadena no se rompe: la replica
continua desde su ultimo sello.

Para volver al original: para este proceso (Ctrl+C) y arranca el Core de
siempre desde coordinator/. La replica se queda como esta, sin tocar.
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent.parent
COORD = RAIZ / "coordinator"
sys.path.insert(0, str(RAIZ / "tools"))
sys.path.insert(0, str(RAIZ))

import backup_ledger as BK  # noqa: E402
from ledger import sellos as S  # noqa: E402

DESTINO_DEFECTO = Path(os.environ.get("KUKIA_REPLICA") or "D:/KUKIA_REPLICA")
DB_ORIGINAL = COORD / "ledger" / "kukia.db"
SELLOS_ORIGINAL = COORD / "sellos.jsonl"
# Una replica de hace dias no es "reciente": se avisa a partir de aqui.
EDAD_AVISO_HORAS = 30.0


def core_vivo(puerto: int) -> bool:
    try:
        r = requests.get(f"http://127.0.0.1:{puerto}/stats", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def leer_estado(db: Path) -> dict | None:
    """Asientos y suministro de un .db, en solo lectura (el Core puede estar muerto)."""
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            asientos = int(con.execute("SELECT COUNT(*) c FROM ledger").fetchone()["c"])
            supply = float(con.execute(
                "SELECT COALESCE(SUM(kuki),0) s FROM ledger").fetchone()["s"] or 0.0)
        finally:
            con.close()
    except Exception:
        return None
    return {"asientos": asientos, "supply": round(supply, 6)}


def estados_en_disco() -> list[dict]:
    """Todo estado completo que haya ahora mismo en disco, de mas a menos avanzado.

    Si el original sigue ahi (aunque el Core este caido), o hay una copia de
    ayer, la replica NO es la mejor copia disponible: arrancar sobre ella es
    tirar asientos a la basura sin avisar. Esto lo comprueba.
    """
    candidatos: list[tuple[str, Path]] = [("ledger original (C:)", DB_ORIGINAL)]
    for base in (RAIZ / "backups", Path("D:/KUKIA_backups")):
        try:
            if base.is_dir():
                for sub in sorted(base.iterdir()):
                    if sub.is_dir() and (sub / "kukia.db").exists():
                        candidatos.append((f"copia {sub.name}", sub / "kukia.db"))
        except OSError:
            continue
    salida = []
    for etiqueta, db in candidatos:
        e = leer_estado(db)
        if not e:
            continue
        e.update({"origen": etiqueta, "ruta": str(db),
                  "fecha": time.strftime("%Y-%m-%d %H:%M",
                                          time.localtime(db.stat().st_mtime))})
        salida.append(e)
    salida.sort(key=lambda x: x["asientos"], reverse=True)
    return salida


def ultimo_sello(ruta: Path) -> str | None:
    try:
        lista = S.leer_sellos(ruta)
        return lista[-1].get("hash") if lista else None
    except Exception:
        return None


def publica_del_original() -> str:
    """La publica del sello del Core de esta maquina, si su clave sigue aqui."""
    clave = COORD / "sello_key.txt"
    if not clave.exists():
        return ""
    try:
        return S.publica_de(S.leer_clave(clave))
    except Exception:
        return ""


def publica_de_la_replica(carpeta: Path) -> str:
    """La publica que trae la propia replica: sello_pub.txt o, si es antigua, su privada."""
    f_pub = carpeta / "sello_pub.txt"
    if f_pub.exists():
        return f_pub.read_text(encoding="utf-8").strip()
    clave = carpeta / "sello_key.txt"
    if clave.exists():
        try:
            return S.publica_de(S.leer_clave(clave))
        except Exception:
            return ""
    return ""


def edad_horas(fecha: str) -> float | None:
    try:
        return (time.time() - time.mktime(time.strptime(fecha, "%Y-%m-%d %H:%M:%S"))) / 3600.0
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Arranca el Core sobre la replica")
    ap.add_argument("--destino", default=str(DESTINO_DEFECTO))
    ap.add_argument("--puerto", type=int, default=8300)
    ap.add_argument("--forzar", action="store_true",
                    help="arrancar aunque el puerto conteste (solo si sabes que es un zombi)")
    ap.add_argument("--aceptar-atraso", action="store_true",
                    help="arrancar aunque haya en disco un estado mas nuevo que la replica "
                         "(eso pierde todo lo posterior a la copia)")
    args = ap.parse_args()

    carpeta = Path(args.destino) / "actual"
    db = carpeta / "kukia.db"
    if not db.exists():
        print(f"[replica] ERROR: no hay replica en {carpeta}")
        print("[replica] creala primero con: python tools\\replica_ledger.py")
        return 2

    manifiesto = {}
    f_man = carpeta / "replica.json"
    if f_man.exists():
        try:
            manifiesto = json.loads(f_man.read_text(encoding="utf-8"))
        except Exception:
            manifiesto = {}
    if manifiesto and manifiesto.get("verificado") is not True:
        print("[replica] ERROR: el manifiesto dice que la replica NO esta verificada.")
        return 3

    if core_vivo(args.puerto) and not args.forzar:
        print(f"[replica] El Core principal SIGUE VIVO en el puerto {args.puerto}.")
        print("[replica] Dos Cores a la vez son dos historias distintas: no arranco.")
        print("[replica] Si de verdad esta muerto y lo que contesta es un zombi, "
              "repitelo con --forzar.")
        return 4

    print("[replica] comprobando cadena y sellos de la replica (no toca el original) ...")
    r = BK.verificar(carpeta, publica=publica_del_original())
    if not r.get("ok"):
        print(f"[replica] ERROR: la replica no cuadra: {r.get('problemas')}")
        return 5
    print(f"[replica] OK: {r['asientos']} asientos, {r['sellos']} sellos, "
          f"suministro {r['supply']:.6f}")
    fecha = manifiesto.get("fecha")
    print(f"[replica] copia del {fecha or 'fecha desconocida'}")
    horas = edad_horas(fecha) if fecha else None
    if horas is not None and horas > EDAD_AVISO_HORAS:
        print(f"[replica] AVISO: la copia tiene {horas / 24:.1f} dias.")

    # --- Guardia 1: ¿hay en disco algo mas nuevo que esta replica? ----------
    # CAOS 2026-10-22 (negativas): antes una replica de hace dos dias arrancaba
    # sin decir nada, aunque el estado bueno estuviera en C:. Eso es perder
    # asientos y dinero sin enterarse. Ahora se niega y dice cuanto se perderia.
    mejores = [e for e in estados_en_disco() if e["asientos"] > r["asientos"]]
    if mejores:
        e = mejores[0]
        print("[replica] HAY UN ESTADO MAS NUEVO EN DISCO:")
        print(f"           {e['origen']}  ({e['fecha']}): "
              f"{e['asientos']} asientos, {e['supply']:.6f} KIA")
        print(f"           la replica:            {r['asientos']} asientos, {r['supply']:.6f} KIA")
        print(f"           se perderian {e['asientos'] - r['asientos']} asientos y "
              f"{e['supply'] - r['supply']:.6f} KIA")
        if not args.aceptar_atraso:
            print("[replica] NO arranco: la replica no es la mejor copia que tienes.")
            print("[replica] Si el original esta vivo, arranca ESE (coordinator/).")
            print("[replica] Si de verdad quieres esta copia y aceptas la perdida, "
                  "repitelo con --aceptar-atraso.")
            return 7
        print("[replica] --aceptar-atraso: sigo, se perdera lo de arriba.")

    # --- Guardia 2: ¿la replica DESCIENDE del original? --------------------
    # Si la replica sabe mas asientos que el original pero no contiene su ultimo
    # sello firmado, no es una continuacion: son dos historias firmadas con la
    # misma clave. Eso no se arranca nunca.
    sello_orig = ultimo_sello(SELLOS_ORIGINAL)
    estado_orig = leer_estado(DB_ORIGINAL)
    if sello_orig and estado_orig and r["asientos"] > estado_orig["asientos"]:
        hashes = {s.get("hash") for s in S.leer_sellos(carpeta / "sellos.jsonl")}
        if sello_orig not in hashes:
            print("[replica] ERROR: la replica NO desciende del original.")
            print(f"           su ultimo sello ({sello_orig[:16]}...) no esta en la replica,")
            print(f"           y la replica dice tener mas asientos ({r['asientos']} vs "
                  f"{estado_orig['asientos']}).")
            print("[replica] Son dos historias firmadas con la misma clave. NO arranco.")
            return 8

    # --- Guardia 3: ¿la replica esta firmada con NUESTRA clave? -------------
    # CAOS 2026-10-22 (negativas): la verificacion de arriba usa la clave que
    # trae la propia replica, asi que una replica de OTRA red cuadra consigo
    # misma y arrancaba tan tranquila, sirviendo otra moneda en el mismo 8300.
    # Si la clave del original sigue ahi, se comparan las publicas. Cuando el
    # disco original ha muerto de verdad no hay con que comparar: se avisa.
    # 28-10-2026: la replica ya no lleva la clave privada (era otra copia en
    # claro de la clave que firma, fuera del disco del Core). Se compara la
    # PUBLICA que trae (sello_pub.txt), que sirve igual para detectar otra red.
    clave_rep = carpeta / "sello_key.txt"
    clave_orig = COORD / "sello_key.txt"
    pub_orig = publica_del_original()
    pub_rep = publica_de_la_replica(carpeta)
    if pub_orig and pub_rep:
        if pub_rep != pub_orig:
            print("[replica] ERROR: la replica esta firmada con OTRA clave.")
            print(f"           replica : {pub_rep[:24]}...")
            print(f"           original: {pub_orig[:24]}...")
            print("[replica] Eso no es tu red, es otra moneda con tu nombre. NO arranco.")
            return 9
        print("[replica] clave publica: coincide con la del original (es esta red)")
    elif not pub_orig:
        print("[replica] AVISO: no encuentro la clave del original; no puedo comprobar "
              "que la replica sea de esta red.")
    else:
        print("[replica] AVISO: la replica no trae ni sello_pub.txt ni clave privada; "
              "no puedo comprobar que sea de esta red.")

    # La clave con la que firma: la del original si sigue en su sitio; si no,
    # la que haya traido la replica (recuperacion en otra maquina).
    clave = clave_orig if clave_orig.exists() else clave_rep

    entorno = dict(os.environ)
    entorno.update({
        "KUKIA_DB": str(db),
        "KUKIA_SELLOS_FILE": str(carpeta / "sellos.jsonl"),
        "KUKIA_SELLO_KEY": str(clave),
        "KUKIA_ADMIN_TOKEN": str(carpeta / "admin_token.txt"),
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    if not clave.exists():
        print("[replica] ERROR: no hay clave del sello: ni coordinator/sello_key.txt "
              "ni una dentro de la replica. Sin ella no se pueden firmar sellos.")
        print("[replica] Restaurala desde tu paquete cifrado de claves "
              "(python tools\\cifrar_claves.py --verificar ...).")
        return 6

    print("=" * 62)
    print("  RED KUKIA CORRIENDO SOBRE LA REPLICA")
    print(f"  base: {db}")
    print("  A partir de ahora TODO se escribe en la replica, no en C:.")
    print("  El ledger original NO se toca: queda congelado en la copia.")
    print("  Ctrl+C para parar. Para volver al original, arranca el Core de")
    print("  siempre desde coordinator/ (nunca los dos a la vez).")
    print("=" * 62)
    time.sleep(1)
    orden = [sys.executable, "-u", "-m", "uvicorn", "kukia_core:app",
             "--host", "127.0.0.1", "--port", str(args.puerto)]
    try:
        return subprocess.call(orden, cwd=str(COORD), env=entorno)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
