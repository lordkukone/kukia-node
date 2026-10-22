"""Saldo real del minero, sin abrir el navegador.

Lee las billeteras que crea el minero (ledger/wallet/worker_*.json), pregunta
al Core por /saldo/<direccion> y ensena el total. El campo "saldo" del JSON es
solo una copia local y siempre marca 0: el bueno es el del Core.

Uso:
  python tools/saldo_minero.py
  python tools/saldo_minero.py --core http://127.0.0.1:8300
  set KUKIA_CORE=https://kukia.tudominio.com && python tools/saldo_minero.py

Sale con 0 si hay alguna billetera, 1 si no hay ninguna todavia.
"""
import argparse
import glob
import json
import os
import sys
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CORE = os.environ.get("KUKIA_CORE", "http://127.0.0.1:8300")


def get_json(url: str, timeout: float = 8.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", default=CORE)
    args = ap.parse_args()
    core = args.core.rstrip("/")
    # La linea de core_minero.txt puede ser una indireccion ("auto https://...")
    # si el Core se publica por tunel. Ver tools/core_destino.py.
    try:
        import core_destino
        core = core_destino.resolver(core)
    except Exception as e:
        print(f"[aviso] no pude resolver la direccion del Core ({e}); uso {core}")

    ficheros = sorted(glob.glob(str(RAIZ / "ledger" / "wallet" / "worker_*.json")))
    if not ficheros:
        print("No hay ninguna billetera de minero todavia.")
        print("Arranca el minero primero (ARRANCAR_MINERO.cmd) y vuelve a mirar.")
        return 1

    try:
        get_json(f"{core}/stats")
    except Exception as e:
        print(f"[ERROR] El Core no responde en {core} ({e}).")
        print("Revisa la URL y que el Core este encendido.")
        return 1

    total = 0.0
    print(f"Core: {core}")
    print()
    print(f"{'BILLETERA':<28} {'DIRECCION':<44} {'SALDO (KIA)':>14}")
    print("-" * 88)
    for f in ficheros:
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"{Path(f).stem:<28} (no se pudo leer: {e})")
            continue
        direccion = (d.get("direccion") or "").strip()
        if not direccion:
            print(f"{Path(f).stem:<28} (sin direccion en el fichero)")
            continue
        try:
            saldo = float(get_json(f"{core}/saldo/{direccion}").get("saldo", 0.0))
        except Exception as e:
            print(f"{Path(f).stem:<28} {direccion:<44} {'?':>14}  ({e})")
            continue
        total += saldo
        print(f"{Path(f).stem:<28} {direccion:<44} {saldo:>14,.4f}")
    print("-" * 88)
    print(f"{'TOTAL':<28} {'':<44} {total:>14,.4f}")
    print()
    print("El saldo es el del Core (el campo 'saldo' del JSON es una copia local).")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
