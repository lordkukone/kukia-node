"""Rescata saldos atrapados en nodos antiguos hacia su billetera.

Problema: antes el Core pagaba al ID del nodo, no a una direccion. Los nodos
registrados sin `direccion_cobro` acumularon KIA a nombre de un id de 12 hex
que no es una billetera. Si conservas la clave privada de ese nodo (o de la
billetera con la que se registro), ese dinero se puede mover: el Core ya lo
permite con `/transferir` firmado.

Este script NO pide privilegios ni toca la base de datos. Solo:
  1. Lee una billetera (JSON con direccion, clave_publica y clave_privada).
  2. Busca los nodos registrados con ESA clave publica.
  3. Por cada uno con saldo > 0, firma una transferencia a la direccion de la
     billetera y la envia al Core.

Es idempotente: al repetirlo, los nodos ya barridos tienen saldo 0 y no se
reenvia nada.

Uso:
  # Ver que haria (no mueve nada)
  python tools/rescatar_saldos.py --wallet ledger/wallet/worker_minero_kuki.json

  # Ejecutarlo de verdad
  python tools/rescatar_saldos.py --wallet ledger/wallet/worker_minero_kuki.json --confirmar

Sale con 0 si todo lo encontrado se movio (o no habia nada), 1 si algo fallo.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "ledger"))


def _post(url: str, cuerpo: dict, timeout: float = 60.0) -> tuple[int, dict]:
    datos = json.dumps(cuerpo).encode()
    req = urllib.request.Request(url, data=datos, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def _get(url: str, timeout: float = 30.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser(description="Mueve saldos de nodos antiguos a su billetera")
    ap.add_argument("--wallet", required=True, help="JSON con direccion y clave_privada")
    ap.add_argument("--core", default="http://127.0.0.1:8300")
    ap.add_argument("--destino", default="",
                    help="direccion que recibe (por defecto, la de la billetera del --wallet)")
    ap.add_argument("--confirmar", action="store_true", help="enviar de verdad (sin esto solo muestra)")
    args = ap.parse_args()

    # cargar_billetera descifra la clave si esta protegida con DPAPI (21-10-2026).
    # Antes se leia el JSON a pelo y una billetera cifrada parecia "sin clave".
    try:
        from kukia_wallet import cargar_billetera
        w = cargar_billetera(Path(args.wallet))
    except Exception as e:
        w = json.loads(Path(args.wallet).read_text(encoding="utf-8"))
        if not w.get("clave_privada"):
            print(f"[rescate] no puedo leer la clave de esa billetera: {e}", file=sys.stderr)
            return 2
    direccion = (w.get("direccion") or "").strip().upper()
    pubkey = (w.get("clave_publica") or "").strip()
    privkey = (w.get("clave_privada") or "").strip()
    if not (direccion and pubkey and privkey):
        print("[rescate] la billetera no tiene direccion, clave_publica y clave_privada", file=sys.stderr)
        return 2

    destino = (args.destino or direccion).strip().upper()
    if not (len(destino) == 41 and destino.startswith("KUK") and all(c in "0123456789ABCDEF" for c in destino[3:])):
        print(f"[rescate] destino invalido: {destino}", file=sys.stderr)
        return 2

    from kukia_wallet import firmar

    core = args.core.rstrip("/")
    nodos = _get(f"{core}/nodos")
    candidatos = [n for n in nodos if (n.get("clave_publica") or "") == pubkey]
    print(f"[rescate] billetera : {direccion}")
    print(f"[rescate] destino   : {destino}")
    print(f"[rescate] clave pub : {pubkey[:16]}...")
    print(f"[rescate] nodos con esa clave: {len(candidatos)}")

    total = 0.0
    fallos = 0
    for n in candidatos:
        nid = n["id"]
        s = _get(f"{core}/saldo/{urllib.parse.quote(nid)}").get("saldo", 0)
        monto = round(float(s or 0.0), 4)
        if monto <= 0:
            continue
        total += monto
        print(f"[rescate] {nid} ({n.get('nombre') or '-'}): {monto} KIA -> {destino}")
        if not args.confirmar:
            continue
        nonce = f"rescate-{nid}-{int(time.time() * 1000)}"
        firma = firmar(f"transferir:{nid}:{destino}:{monto}:{nonce}", privkey)
        codigo, res = _post(f"{core}/transferir", {"de_nodo": nid, "a_direccion": destino,
                                                    "monto": monto, "nonce": nonce, "firma": firma})
        if codigo == 200:
            print(f"[rescate]   OK: {monto} KIA movidos")
        else:
            fallos += 1
            print(f"[rescate]   RECHAZADO {codigo}: {str(res)[:160]}")

    if total == 0:
        print("[rescate] no hay saldo que mover con esta clave.")
    elif args.confirmar:
        print(f"[rescate] total movido: {round(total - 0, 4)} KIA")
    else:
        print(f"[rescate] total a mover: {round(total, 4)} KIA (añade --confirmar para hacerlo)")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
