"""¿Un fallo de escritura se disfraza de «nonce ya usado»?

QUE COMPRUEBA
    1. La repeticion DE VERDAD (mismo nonce, misma firma) sigue siendo 409 con
       el mensaje de siempre. Eso no se toca.
    2. Los demas fallos del INSERT —NOT NULL, CHECK, FOREIGN KEY o el RAISE() de
       un trigger— NO son repeticiones: tienen que salir como fallo de escritura
       (500/507/503) y no pueden decir «nonce ya usado».
    3. Despues de un fallo asi, la transferencia buena sigue funcionando (no se
       queda nada bloqueado) y las cuentas cuadran.

COMO SE FUERZA EL FALLO
    Se pone un trigger en la tabla que aborta CUALQUIER INSERT con un mensaje que
    no es de UNIQUE. En Python eso llega como sqlite3.IntegrityError, igual que
    la repeticion, que es justo el caso que se quiere distinguir. Se quita luego
    y se comprueba que el camino bueno vuelve.

NO TOCA
    Nada real: base nueva en carpeta temporal, puerto libre, borrada al final con
    --borrar. El ledger real y el 8300 no se abren.

Uso:
    python tools/prueba_error_escritura_transferencia.py
    python tools/prueba_error_escritura_transferencia.py --borrar
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
COORD = RAIZ / "coordinator"
sys.path.insert(0, str(RAIZ))

from cryptography.hazmat.primitives import serialization        # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ed25519   # noqa: E402

fallos = 0
MONTO = 5.0
SALDO = 50.0

# AFTER (no BEFORE): asi la fila pasa antes por la UNIQUE (de_nodo, nonce), y el
# trigger solo estorba cuando la transferencia es nueva. Con BEFORE, hasta una
# repeticion legitima acabaria en el mensaje del trigger y no se podria separar
# una cosa de la otra.
TRIGGER = ("CREATE TRIGGER p1_fallo_simulado AFTER INSERT ON transferencias "
           "BEGIN SELECT RAISE(ABORT, 'fallo simulado de escritura (prueba)'); END")


def comprobar(nombre: str, condicion: bool, detalle: str = "") -> bool:
    global fallos
    if condicion:
        print(f"  OK    {nombre}")
    else:
        fallos += 1
        print(f"  FALLO {nombre}" + (f" -> {detalle}" if detalle else ""))
    return bool(condicion)


def puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pedir(base: str, ruta: str, datos=None, timeout=60.0):
    cuerpo = None if datos is None else json.dumps(datos).encode("utf-8")
    cabeceras = {"Content-Type": "application/json"} if cuerpo else {}
    req = urllib.request.Request(base + ruta, data=cuerpo, headers=cabeceras,
                                 method="POST" if cuerpo else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            texto = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(texto)
            except json.JSONDecodeError:
                return r.status, texto
    except urllib.error.HTTPError as e:
        texto = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(texto)
        except json.JSONDecodeError:
            return e.code, texto


def direccion_v1(pub_hex: str) -> str:
    return "KUK" + hashlib.sha256(bytes.fromhex(pub_hex)).hexdigest()[:38].upper()


def billetera():
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw).hex()
    return priv, pub, direccion_v1(pub)


def firmar(priv, de: str, a: str, monto: float, nonce: str) -> str:
    return priv.sign(f"transferir:{de}:{a}:{monto}:{nonce}".encode()).hex()


def entorno(tmp: Path) -> dict:
    env = dict(os.environ)
    env.update({
        "KUKIA_DB": str(tmp / "kukia.db"),
        "KUKIA_SELLO_KEY": str(tmp / "sello_key.txt"),
        "KUKIA_SELLOS_FILE": str(tmp / "sellos.jsonl"),
        "KUKIA_ADMIN_TOKEN": str(tmp / "admin_token.txt"),
        "KUKIA_AUTO_TAREAS": "0",
        "KUKIA_ARCHIVAR": "0",
        "KUKIA_SELLO_SEG": "3600",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    return env


_INYECTAR = r"""
import os, sys
sys.path.insert(0, {raiz!r})
sys.path.insert(0, {coord!r})
import kukia_core as K
with K._pago_lock:
    K.con.execute("BEGIN IMMEDIATE")
    K._asiento("synth-fondos", {cuenta!r}, {monto!r}, "credito sintetico de prueba de escritura")
    K.con.commit()
os._exit(0)
"""


def sembrar(env: dict, cuenta: str, monto: float) -> None:
    codigo = _INYECTAR.format(raiz=str(RAIZ), coord=str(COORD), cuenta=cuenta, monto=monto)
    r = subprocess.run([sys.executable, "-c", codigo], cwd=str(COORD), env=env,
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise SystemExit(f"ERROR sembrando fondos:\n{r.stdout}\n{r.stderr}")


def arrancar(tmp: Path, env: dict):
    puerto = puerto_libre()
    archivo = open(tmp / "core.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "kukia_core:app",
         "--host", "127.0.0.1", "--port", str(puerto)],
        cwd=str(COORD), env=env, stdout=archivo, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{puerto}"
    for _ in range(160):
        if proc.poll() is not None:
            raise SystemExit(f"el Core murio al arrancar (codigo {proc.returncode}); ver {tmp/'core.log'}")
        try:
            if pedir(base, "/stats", timeout=2.0)[0] == 200:
                return proc, base, archivo
        except Exception:
            pass
        time.sleep(0.25)
    proc.kill()
    raise SystemExit(f"el Core no respondio; ver {tmp/'core.log'}")


def sql(db: Path, sentencia: str) -> None:
    con = sqlite3.connect(str(db), timeout=30)
    try:
        con.execute(sentencia)
        con.commit()
    finally:
        con.close()


def main(borrar: bool) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="error_escritura_"))
    print(f"carpeta temporal: {tmp}")
    proc = archivo = None
    try:
        priv, pub, origen = billetera()
        _, _, destino = billetera()
        env = entorno(tmp)
        db = Path(env["KUKIA_DB"])
        sembrar(env, origen, SALDO)
        proc, base, archivo = arrancar(tmp, env)
        print(f"   Core aislado en {base}")

        def transferir(nonce: str, monto: float = MONTO):
            return pedir(base, "/transferir", {
                "de_nodo": origen, "a_direccion": destino, "monto": monto,
                "nonce": nonce, "firma": firmar(priv, origen, destino, monto, nonce),
                "clave_publica": pub})

        print("\n== 1) la transferencia buena, con su nonce ==")
        nonce = "e-" + secrets.token_hex(6)
        codigo, r = transferir(nonce)
        comprobar("primera transferencia: 200 (y crea la tabla)", codigo == 200, f"{codigo} {r}")

        print("\n== 2) la repeticion de verdad: mismo nonce y misma firma ==")
        codigo, r = transferir(nonce)
        texto = json.dumps(r, ensure_ascii=False).lower()
        comprobar("sigue siendo 409 (no se toca el caso que ya funcionaba)",
                  codigo == 409, f"{codigo} {r}")
        comprobar("y sigue diciendo que el nonce esta usado",
                  "nonce" in texto and "usado" in texto, f"{r}")

        print("\n== 3) un fallo de escritura que NO es repeticion (trigger que aborta) ==")
        sql(db, TRIGGER)
        nonce_fallo = "e-" + secrets.token_hex(6)
        codigo, r = transferir(nonce_fallo)
        texto = json.dumps(r, ensure_ascii=False).lower()
        print(f"   respuesta: {codigo} {r}")
        comprobar("no se disfraza de 409", codigo != 409, f"{codigo} {r}")
        comprobar("no dice «nonce ya usado»", "nonce" not in texto, f"{r}")
        comprobar("sale como fallo de escritura/5xx", 500 <= codigo < 600 or codigo == 0,
                  f"{codigo}")

        print("\n== 4) la repeticion sigue siendo 409 aunque haya trigger ==")
        codigo, r = transferir(nonce)
        comprobar("mismo nonce otra vez: 409 (la UNIQUE se comprueba antes)",
                  codigo == 409, f"{codigo} {r}")

        print("\n== 5) quitado el trigger, todo vuelve a funcionar ==")
        sql(db, "DROP TRIGGER p1_fallo_simulado")
        nonce_bueno = "e-" + secrets.token_hex(6)
        codigo, r = transferir(nonce_bueno)
        comprobar("transferencia nueva: 200", codigo == 200, f"{codigo} {r}")

        print("\n== 6) las cuentas tras el susto ==")
        saldo = pedir(base, f"/saldo/{origen}")[1].get("saldo")
        comprobar(f"el origen tiene {SALDO} - 2 x {MONTO} = {SALDO - 2*MONTO}",
                  abs(saldo - (SALDO - 2 * MONTO)) < 1e-9, f"{saldo}")
        saldo_destino = pedir(base, f"/saldo/{destino}")[1].get("saldo")
        comprobar(f"el destino tiene 2 x {MONTO}",
                  abs(saldo_destino - 2 * MONTO) < 1e-9, f"{saldo_destino}")
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            filas = con.execute("SELECT COUNT(*) FROM transferencias").fetchone()[0]
            nonces = [f[0] for f in con.execute("SELECT nonce FROM transferencias")]
        finally:
            con.close()
        comprobar("solo quedan las 2 transferencias buenas en la tabla", filas == 2, f"{filas}")
        comprobar("la del fallo NO se guardo", nonce_fallo not in nonces, "se guardo el fallo")
        comprobar("ningun nonce repetido", len(nonces) == len(set(nonces)), str(nonces))

        print("\n== 7) el registro del Core dice el fallo de verdad ==")
        registro = (tmp / "core.log").read_text(encoding="utf-8", errors="replace")
        comprobar("el fallo queda en el log (no se traga)",
                  "fallo simulado de escritura" in registro
                  or "error guardando la transferencia" in registro,
                  "no aparece ni el mensaje del trigger ni el de escritura")
    finally:
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=15)
            except Exception:
                pass
        if archivo is not None:
            try:
                archivo.close()
            except Exception:
                pass
        if borrar:
            shutil.rmtree(tmp, ignore_errors=True)
            print(f"\ncarpeta temporal borrada: {tmp}")
        else:
            print(f"\ncarpeta temporal conservada: {tmp}")

    print("\n== RESUMEN ==")
    if fallos:
        print(f"{fallos} comprobacion(es) FALLIDAS")
        return 1
    print("TODO OK: la repeticion se distingue de un fallo de escritura")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--borrar", action="store_true", help="borra la carpeta temporal")
    args = ap.parse_args()
    raise SystemExit(main(args.borrar))
