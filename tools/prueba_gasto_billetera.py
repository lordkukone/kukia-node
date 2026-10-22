"""Prueba REAL: gastar KIA desde una billetera, sin nodo de por medio.

Hasta el 22-10-2026 el aviso de `verificar_moneda.py` decia que 15 millones de
KIA estaban atrapados en direcciones de billetera "sin endpoint para gastar".
El endpoint existe (`POST /transferir` acepta el origen KUK... con su clave
publica). Lo que faltaba era la prueba: esto la hace, con dinero de verdad y un
importe minimo (0,01 KIA por defecto).

Comprueba, en una sola pasada:

  1. El saldo del origen y del destino antes.
  2. Que una firma con OTRA clave se rechaza (401).
  3. Que la transferencia buena se acepta y el saldo cuadra al importe.
  4. Que el suministro NO cambia (una transferencia no crea moneda).
  5. Que los dos asientos quedan encadenados (hash_prev == hash anterior).
  6. Que repetir el mismo nonce se rechaza (409).

Uso:
    python tools/prueba_gasto_billetera.py --desde kuki_fundador_nueva \\
        --a KUKE1A0EB09FD9BF49FF41B884EDBDF6DC0E3E96F --monto 0.01

    --no-enviar   hace todo menos mover dinero (firma y valida en local)
"""

from __future__ import annotations

import argparse
import json
import secrets
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

DB = RAIZ / "coordinator" / "ledger" / "kukia.db"
CORE = "http://127.0.0.1:8300"

fallos = 0


def comprobar(nombre: str, condicion: bool, detalle: str = "") -> bool:
    global fallos
    if condicion:
        print(f"  OK    {nombre}")
    else:
        fallos += 1
        print(f"  FALLO {nombre}" + (f" -> {detalle}" if detalle else ""))
    return condicion


def pide(url: str, datos: dict | None = None, tiempo: float = 15.0) -> tuple:
    """(codigo, cuerpo). codigo 0 si no hubo respuesta."""
    cuerpo = None if datos is None else json.dumps(datos).encode()
    pet = urllib.request.Request(url, data=cuerpo,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if datos is not None else "GET")
    try:
        with urllib.request.urlopen(pet, timeout=tiempo) as r:
            return r.status, r.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read(65536).decode("utf-8", "replace")
        except Exception:
            return e.code, ""
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def saldo(direccion: str) -> float:
    codigo, cuerpo = pide(f"{CORE}/saldo/{direccion}")
    if codigo != 200:
        return -1.0
    return float(json.loads(cuerpo).get("saldo", 0.0))


def supply() -> float:
    codigo, cuerpo = pide(f"{CORE}/stats")
    if codigo != 200:
        return -1.0
    return float(json.loads(cuerpo).get("supply_emitido", 0.0))


def asientos(nonce: str) -> list:
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT id, nodo_id, kuki, motivo, hash_prev, hash FROM ledger "
            "WHERE tarea_id=? ORDER BY id", (f"tx-{nonce[:12]}",))]
    finally:
        con.close()


def hash_de(asiento_id: int) -> str:
    """Hash del asiento anterior (por id) en la cadena."""
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=20)
    try:
        f = con.execute("SELECT hash FROM ledger WHERE id=?", (asiento_id,)).fetchone()
        return (f[0] or "") if f else ""
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", required=True,
                    help="nombre de la billetera en ledger/wallet (sin .json)")
    ap.add_argument("--a", required=True, help="direccion KUK... de destino")
    ap.add_argument("--monto", type=float, default=0.01)
    ap.add_argument("--no-enviar", action="store_true")
    args = ap.parse_args()

    from ledger import kukia_wallet as W

    print("KUKIA · gasto desde billetera sin nodo")
    print("=" * 60)

    # --- 1. La billetera y su clave ------------------------------------
    billetera = W.cargar_billetera(args.desde)
    de = (billetera.get("direccion") or "").strip().upper()
    priv = billetera.get("clave_privada") or ""
    pub = (billetera.get("clave_publica") or "").strip().lower()
    if not (de and priv and pub):
        print(f"FALLO la billetera {args.desde} no tiene direccion/clave")
        return 2
    print(f"origen : {de}  ({args.desde})")
    print(f"destino: {args.a}")
    # _direccion_y_claves devuelve (clave_publica, direccion, tipo)
    pub_derivada, dir_derivada, _ = W._direccion_y_claves(bytes.fromhex(priv))
    comprobar("la clave privada deriva en la direccion de origen",
              dir_derivada == de, f"{dir_derivada} != {de}")
    comprobar("la clave publica del fichero es la de la direccion",
              pub_derivada == pub)

    monto = round(float(args.monto), 4)
    nonce = secrets.token_hex(16)
    mensaje = f"transferir:{de}:{args.a}:{monto}:{nonce}"   # firmar() pide str
    firma = W.firmar(mensaje, priv)

    s0_de, s0_a, sup0 = saldo(de), saldo(args.a), supply()
    print(f"saldo antes: {s0_de} -> {s0_a} | suministro {sup0}")
    if s0_de < monto:
        print(f"FALLO el origen no tiene saldo ({s0_de} < {monto})")
        return 2

    # --- 2. Firma con otra clave: debe rechazarse ----------------------
    # La clave del intruso se genera EN MEMORIA: no se escribe ninguna
    # billetera en el proyecto por hacer una prueba.
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    otra = Ed25519PrivateKey.generate()
    intruso_pub = otra.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw).hex()
    cod, _ = pide(f"{CORE}/transferir", {
        "de_nodo": de, "a_direccion": args.a, "monto": monto, "nonce": nonce,
        "firma": otra.sign(mensaje.encode()).hex(),
        "clave_publica": intruso_pub})
    comprobar("firmar con otra clave se rechaza (401)", cod == 401, f"HTTP {cod}")

    # El saldo del origen no se mueve con un intento rechazado.
    comprobar("el intento rechazado no toca el saldo", saldo(de) == s0_de)

    if args.no_enviar:
        print("\n(--no-enviar: no se mueve dinero)")
        return 0 if fallos == 0 else 1

    # --- 3. La transferencia buena -------------------------------------
    cod, cuerpo = pide(f"{CORE}/transferir", {
        "de_nodo": de, "a_direccion": args.a, "monto": monto, "nonce": nonce,
        "firma": firma, "clave_publica": pub})
    print(f"POST /transferir -> HTTP {cod} {cuerpo[:200]}")
    if not comprobar("transferencia aceptada (200)", cod == 200, f"HTTP {cod}"):
        return 1

    s1_de, s1_a, sup1 = saldo(de), saldo(args.a), supply()
    print(f"saldo despues: {s1_de} -> {s1_a} | suministro {sup1}")
    # La red sigue minando mientras se prueba, asi que el destino tambien puede
    # ingresar por trabajo. Lo exacto se mira en los asientos; aqui, la
    # direccion del cambio.
    comprobar("el origen baja (nunca sube) por el importe", s0_de - s1_de >= monto - 1e-6,
              f"{s0_de} -> {s1_de}")
    comprobar("el destino sube al menos el importe", s1_a - s0_a >= monto - 1e-6,
              f"{s0_a} -> {s1_a}")

    # --- 4. Los dos asientos, encadenados ------------------------------
    filas = asientos(nonce)
    comprobar("hay dos asientos (-monto y +monto)", len(filas) == 2, f"{len(filas)}")
    if len(filas) == 2:
        salida, entrada = filas
        comprobar("el asiento de salida es negativo",
                  abs(float(salida["kuki"]) + monto) < 1e-6, str(salida["kuki"]))
        comprobar("el asiento de entrada es positivo",
                  abs(float(entrada["kuki"]) - monto) < 1e-6, str(entrada["kuki"]))
        comprobar("los dos asientos se compensan: no se crea moneda",
                  abs(float(salida["kuki"]) + float(entrada["kuki"])) < 1e-9)
        # Encadenado REAL: el hash_prev del primer asiento es el hash del
        # asiento anterior en la tabla (no el ultimo que vimos antes: entre
        # medias el minero cobra y la cadena avanza).
        anterior = hash_de(salida["id"] - 1)
        comprobar("el primer asiento engancha con el asiento anterior",
                  salida["hash_prev"] == anterior,
                  f"{salida['hash_prev'][:12]} != {anterior[:12]}")
        comprobar("el segundo engancha con el primero",
                  entrada["hash_prev"] == salida["hash"])
        comprobar("los dos llevan hash", bool(salida["hash"]) and bool(entrada["hash"]))

    # --- 5. Repetir el nonce: rechazado -------------------------------
    cod, _ = pide(f"{CORE}/transferir", {
        "de_nodo": de, "a_direccion": args.a, "monto": monto, "nonce": nonce,
        "firma": firma, "clave_publica": pub})
    comprobar("repetir el mismo nonce se rechaza (409)", cod == 409, f"HTTP {cod}")

    print("=" * 60)
    print("RESULTADO:", "OK" if fallos == 0 else f"{fallos} fallo(s)")
    return 0 if fallos == 0 else 1


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
