"""Envia KIA desde una billetera local, firmando en este PC. En seco por defecto.

Para que sirve: las billeteras de este PC estan cifradas con DPAPI
(`ledger/wallet/*.json`) y la pagina web no las abre: solo trabaja con las 20
palabras. Este comando firma con la clave local, sin sacarla del proceso y sin
pedir la frase.

Que hace, en orden:
  1. Carga la billetera (DPAPI, o en claro si es de las antiguas).
  2. Comprueba que su direccion sale de su clave publica. Si la billetera usa el
     esquema viejo del 13-10 (direccion = sha256 de la privada), el Core NO la
     acepta en /transferir y se dice aqui, antes de firmar nada.
  3. Valida destino (KUK + 38 hex), importe (positivo, 4 decimales como maximo)
     y que haya saldo, preguntando al Core.
  4. Sin `--confirmar` NO envia nada: enseña lo que haria. Con `--confirmar`
     firma `transferir:<de>:<a>:<monto>:<nonce>` y la manda al Core.

Comision: 0 KIA. El nonce es de un solo uso y se imprime como comprobante.
Nunca imprime la clave privada ni las 20 palabras.

Uso:
    python tools/enviar_kia.py --de worker_minero_pc --a KUK... --monto 1.5
    python tools/enviar_kia.py --de worker_minero_pc --a KUK... --monto 1.5 --confirmar
    python tools/enviar_kia.py --de ledger/wallet/worker_minero_pc.json --a KUK... --monto 0.5 \
        --core "auto https://kukia-network.surge.sh/core_actual.json"

`--de` admite el nombre (sin .json) o la ruta al fichero.
"""
import argparse
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "ledger"))
sys.path.insert(0, str(RAIZ / "tools"))
import kukia_wallet as W   # noqa: E402

DIR_RE = re.compile(r"^KUK[0-9A-F]{38}$")
CORE_POR_DEFECTO = "http://127.0.0.1:8300"


def normaliza_core(valor: str) -> str:
    """Acepta una URL normal o la indireccion 'auto <espejo>' del minero."""
    valor = (valor or "").strip()
    if not valor:
        return CORE_POR_DEFECTO
    if valor.lower().startswith("auto"):
        try:
            import core_destino
            return core_destino.resolver(valor, avisar=True).rstrip("/")
        except Exception as e:
            raise SystemExit(f"[enviar] no pude resolver la direccion del Core: {e}")
    return valor.rstrip("/")


def pide(url: str, datos: dict = None, tiempo: float = 15.0) -> tuple:
    """(codigo, cuerpo). cuerpo es dict si el Core devolvio JSON."""
    pet = urllib.request.Request(url, headers={"Content-Type": "application/json"})
    if datos is not None:
        pet.data = json.dumps(datos).encode()
    try:
        with urllib.request.urlopen(pet, timeout=tiempo) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            cuerpo = json.loads(e.read().decode() or "{}")
        except Exception:
            cuerpo = {}
        return e.code, cuerpo
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def cargar(fuente: str) -> dict:
    """Billetera con la clave EN CLARO solo en memoria (DPAPI se descifra al leer)."""
    ruta = Path(fuente)
    objetivo = ruta if ruta.suffix == ".json" else fuente
    try:
        return W.cargar_billetera(objetivo)
    except FileNotFoundError:
        raise SystemExit(f"[enviar] no encuentro la billetera {fuente}")
    except RuntimeError as e:
        raise SystemExit(f"[enviar] {e}")


def monto_valido(texto: str) -> float:
    txt = (texto or "").strip().replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d{1,4})?", txt):
        raise SystemExit("[enviar] importe invalido: numero positivo con 4 decimales como maximo")
    monto = round(float(txt), 4)
    if not (monto > 0):
        raise SystemExit("[enviar] el importe tiene que ser mayor que 0")
    return monto


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--de", required=True, help="billetera de origen (nombre o ruta al .json)")
    ap.add_argument("--a", required=True, help="direccion destino KUK...")
    ap.add_argument("--monto", required=True, help="KIA a enviar (maximo 4 decimales)")
    ap.add_argument("--core", default=os.environ.get("KUKIA_CORE", CORE_POR_DEFECTO),
                    help="URL del Core, o 'auto <espejo>'")
    ap.add_argument("--nonce", default="", help="nonce concreto (por defecto, uno nuevo)")
    ap.add_argument("--confirmar", action="store_true",
                    help="firmar y ENVIAR de verdad (sin esto solo se enseña)")
    args = ap.parse_args()

    core = normaliza_core(args.core)
    destino = args.a.strip().upper()
    if not DIR_RE.fullmatch(destino):
        raise SystemExit("[enviar] destino invalido: direccion KUK de 41 caracteres "
                         "(KUK + 38 hex)")
    monto = monto_valido(args.monto)
    nonce = (args.nonce or secrets.token_hex(16)).strip()
    if not nonce or len(nonce) > 64:
        raise SystemExit("[enviar] nonce invalido (1 a 64 caracteres)")

    d = cargar(args.de)
    origen = (d.get("direccion") or "").strip().upper()
    priv = (d.get("clave_privada") or "").strip()
    if not origen or not priv:
        raise SystemExit("[enviar] la billetera no tiene direccion o clave privada legible")
    if origen == destino:
        raise SystemExit("[enviar] origen y destino son la misma direccion")

    # La direccion tiene que derivar de la publica: es lo que exige el Core para
    # gastar desde una billetera. Las antiguas del 13-10 no cumplen esa regla.
    publica, derivada, esquema = W._direccion_y_claves(bytes.fromhex(priv))
    if derivada != origen:
        raise SystemExit(
            f"[enviar] esta billetera usa un esquema de direccion viejo "
            f"({d.get('direccion_esquema') or 'direccion != sha256(publica)'}): el Core no "
            f"puede validar su firma en /transferir. No se envia nada.")

    codigo, cuerpo = pide(f"{core}/saldo/{urllib.parse.quote(origen)}")
    if codigo != 200:
        raise SystemExit(f"[enviar] no pude consultar el saldo en {core} "
                         f"({codigo or 'sin conexion'}: {cuerpo.get('detail') or cuerpo.get('error')})")
    saldo = float(cuerpo.get("saldo") or 0.0)
    if saldo < monto:
        raise SystemExit(f"[enviar] saldo insuficiente: tienes {saldo:.4f} KIA y quieres "
                         f"enviar {monto:.4f}")

    print(f"[enviar] {'ENVIANDO' if args.confirmar else 'EN SECO (nada se envia)'}")
    print(f"  billetera : {args.de}")
    print(f"  origen    : {origen}")
    print(f"  destino   : {destino}")
    print(f"  importe   : {monto} KIA  (comision 0)")
    print(f"  saldo     : {saldo} KIA -> quedarian {round(saldo - monto, 4)} KIA")
    print(f"  core      : {core}")
    print(f"  nonce     : {nonce}")
    print(f"  mensaje   : transferir:{origen}:{destino}:{monto}:{nonce}")

    if not args.confirmar:
        print("\n[enviar] no se ha firmado ni enviado nada. Si esta bien, repite con --confirmar.")
        return 0

    firma = W.firmar(f"transferir:{origen}:{destino}:{monto}:{nonce}", priv)
    codigo, cuerpo = pide(f"{core}/transferir", {
        "de_nodo": origen, "a_direccion": destino, "monto": monto,
        "nonce": nonce, "firma": firma, "clave_publica": publica})
    if codigo != 200:
        print(f"\n[enviar] RECHAZADO {codigo}: {cuerpo.get('detail') or cuerpo}")
        return 1
    print(f"\n[enviar] OK: {cuerpo.get('monto', monto)} KIA a {destino}")
    print(f"  comprobante (nonce): {nonce}")
    print(f"  la clave privada no ha salido de este proceso")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
