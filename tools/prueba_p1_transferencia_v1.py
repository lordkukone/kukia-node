"""P-1: la PRIMERA transferencia del camino v1 en una instalacion NUEVA.

QUE DEMUESTRA
    1. Con una base de datos nueva, creada por el propio Core y SIN apaños,
       `POST /transferir` (camino v1, el del dinero) tiene que guardar la
       transferencia y devolver 200.
    2. Antes del arreglo (27-10-2026) fallaba con 409 "nonce ya usado": mensaje
       FALSO. El INSERT lleva `firma` y `clave_publica` (7 valores) y el
       CREATE TABLE que el endpoint hace en linea solo declaraba 6 columnas, asi
       que en base nueva la tabla se creaba sin esas columnas, el INSERT
       reventaba y el `except` generico culpaba al nonce.
    3. El esquema que queda tiene las MISMAS 8 columnas que un ledger real
       (en produccion la tabla tenia 8 columnas y 32 filas: por eso alli nunca
       salto).
    4. El anti-repeticion sigue funcionando de verdad: mismo nonce -> 409.
    5. Una transferencia no crea moneda: el suministro no se mueve.
    6. Una firma que no es del dueno de la direccion de origen se rechaza.

POR QUE HACE FALTA
    Ningun otro banco de pruebas cubria esto: los que tocan `/transferir` crean
    la tabla con 8 columnas a mano para no arrastrar el ruido, y asi el caso
    real -base nueva tal cual- se quedaba sin cubrir. Esta prueba NO hace ese
    apaño: se fondea la billetera y se transfiere.

NO TOCA
    Nada real: base nueva en carpeta temporal, puerto libre, y al terminar se
    borra. El ledger real (`coordinator/ledger/kukia.db`) y el puerto 8300 no se
    abren en ningun momento.

Uso:
    python tools/prueba_p1_transferencia_v1.py            # deja la carpeta
    python tools/prueba_p1_transferencia_v1.py --borrar   # la borra
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

# El esquema VIEJO (el que estaba en el endpoint) y el INSERT REAL. Se usan solo
# para el control negativo: reproducir el fallo sin depender del codigo viejo.
DDL_VIEJO = ("CREATE TABLE IF NOT EXISTS transferencias("
             "id INTEGER PRIMARY KEY AUTOINCREMENT, de_nodo TEXT, a_direccion TEXT, "
             "monto REAL, nonce TEXT, ts TEXT, UNIQUE(de_nodo, nonce))")
INSERT_REAL = ("INSERT INTO transferencias(de_nodo,a_direccion,monto,nonce,ts,firma,clave_publica) "
               "VALUES(?,?,?,?,?,?,?)")


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


def pedir(base: str, ruta: str, datos=None, timeout=20.0):
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
    """La MISMA regla que `direccion_de_pubkey` del Core.

    Se copia aqui a proposito: importar kukia_core en este proceso abriria la
    base de datos real, y esta prueba no la toca ni de lejos.
        KUK + SHA256(clave_publica)[:38] en mayusculas
    """
    return "KUK" + hashlib.sha256(bytes.fromhex(pub_hex)).hexdigest()[:38].upper()


def billetera_v1():
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw).hex()
    return priv, pub, direccion_v1(pub)


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


# OJO: aqui NO se crea la tabla de transferencias a mano. Se fondea y nada mas:
# la tabla la tiene que crear el Core como sepa, que es justo lo que se prueba.
_INYECTAR = r"""
import os, sys
sys.path.insert(0, {raiz!r})
sys.path.insert(0, {coord!r})
import kukia_core as K
with K._pago_lock:
    K.con.execute("BEGIN IMMEDIATE")
    K._asiento("synth-fondos", {cuenta!r}, {monto!r}, "credito sintetico de prueba P-1")
    K.con.commit()
fila = K.con.execute("SELECT total FROM saldos_cuenta WHERE cuenta=?", ({cuenta!r},)).fetchone()
print("saldo", fila["total"] if fila else 0.0)
print("supply", K.supply_emitido())
tablas = [r[0] for r in K.con.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='transferencias'")]
print("tabla_transferencias_existe", bool(tablas))
os._exit(0)
"""


def sembrar(tmp: Path, env: dict, cuenta: str, monto: float) -> str:
    codigo = _INYECTAR.format(raiz=str(RAIZ), coord=str(COORD), cuenta=cuenta, monto=monto)
    r = subprocess.run([sys.executable, "-c", codigo], cwd=str(COORD), env=env,
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise SystemExit(f"ERROR sembrando fondos:\n{r.stdout}\n{r.stderr}")
    return r.stdout.strip()


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
            codigo, _ = pedir(base, "/stats", timeout=2.0)
            if codigo == 200:
                return proc, base, archivo
        except Exception:
            pass
        time.sleep(0.25)
    proc.kill()
    raise SystemExit(f"el Core no respondio; ver {tmp/'core.log'}")


def columnas(db: Path, tabla: str) -> list:
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return [f[1] for f in con.execute(f"PRAGMA table_info({tabla})")]
    finally:
        con.close()


def firmar(priv, de: str, a: str, monto: float, nonce: str) -> str:
    return priv.sign(f"transferir:{de}:{a}:{monto}:{nonce}".encode()).hex()


def control_negativo(tmp: Path) -> None:
    """Reproduce P-1 a nivel SQL, sin depender del codigo viejo.

    Es la prueba de que el fallo existia y de que era ESTE: la tabla del
    esquema viejo no tiene la columna que el INSERT usa.
    """
    print("\n== 0) control negativo: el esquema viejo contra el INSERT real ==")
    db = tmp / "control_negativo.db"
    con = sqlite3.connect(db)
    try:
        con.execute(DDL_VIEJO)
        try:
            con.execute(INSERT_REAL, ("KUKEJEMPLO", "KUKEJEMPLO2", 1.0, "n1", "ts", "ff", "cc"))
            comprobar("el esquema viejo acepta el INSERT real (NO deberia)",
                      False, "acepto el INSERT: no se reproduce P-1")
        except sqlite3.OperationalError as e:
            comprobar("el esquema viejo NO acepta el INSERT real (P-1 reproducido)",
                      "no column named firma" in str(e), f"{type(e).__name__}: {e}")
    finally:
        con.close()


def main(borrar: bool) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="p1_transferencia_v1_"))
    print(f"carpeta temporal: {tmp}")
    proc = archivo = None
    try:
        control_negativo(tmp)

        priv, pub, dir_origen = billetera_v1()
        priv2, pub2, dir_destino = billetera_v1()
        env = entorno(tmp)
        salida = sembrar(tmp, env, dir_origen, 10.0)
        print("   " + salida.replace("\n", "\n   "))

        print("\n== 1) base nueva: el Core crea el esquema por su cuenta ==")
        comprobar("antes de la primera transferencia, la tabla NO existe",
                  not columnas(Path(env["KUKIA_DB"]), "transferencias")
                  and "tabla_transferencias_existe False" in salida,
                  "la tabla ya existia: no seria una base nueva limpia")

        proc, base, archivo = arrancar(tmp, env)
        print(f"   Core aislado en {base}")

        print("\n== 2) la PRIMERA transferencia v1 de una instalacion nueva ==")
        nonce = "p1-" + secrets.token_hex(6)
        firma = firmar(priv, dir_origen, dir_destino, 5.0, nonce)
        codigo, r = pedir(base, "/transferir", {
            "de_nodo": dir_origen, "a_direccion": dir_destino, "monto": 5.0,
            "nonce": nonce, "firma": firma, "clave_publica": pub})
        comprobar("POST /transferir (v1) guarda la transferencia: 200",
                  codigo == 200, f"{codigo} {r}")
        comprobar("la transferencia quedo EN LA TABLA",
                  codigo == 200 and _fila(Path(env["KUKIA_DB"]), dir_origen, nonce) is not None,
                  "no hay fila con ese nonce")

        print("\n== 3) el esquema que queda es el de un ledger real (8 columnas) ==")
        cols = columnas(Path(env["KUKIA_DB"]), "transferencias")
        comprobar("8 columnas y las dos que faltaban estan",
                  len(cols) == 8 and "firma" in cols and "clave_publica" in cols,
                  f"{cols}")
        fila = _fila(Path(env["KUKIA_DB"]), dir_origen, nonce)
        comprobar("la fila guarda la firma y la clave publica (auditoria)",
                  bool(fila) and fila["firma"] == firma and fila["clave_publica"] == pub,
                  f"{dict(fila) if fila else None}")

        print("\n== 4) el anti-repeticion sigue funcionando (y ahora dice la verdad) ==")
        codigo, r = pedir(base, "/transferir", {
            "de_nodo": dir_origen, "a_direccion": dir_destino, "monto": 5.0,
            "nonce": nonce, "firma": firma, "clave_publica": pub})
        comprobar("mismo nonce otra vez: 409", codigo == 409, f"{codigo} {r}")

        print("\n== 5) una transferencia no crea moneda ==")
        supply_antes = pedir(base, "/stats")[1].get("supply_emitido")
        nonce2 = "p1-" + secrets.token_hex(6)
        codigo, r = pedir(base, "/transferir", {
            "de_nodo": dir_origen, "a_direccion": dir_destino, "monto": 1.0,
            "nonce": nonce2, "firma": firmar(priv, dir_origen, dir_destino, 1.0, nonce2),
            "clave_publica": pub})
        supply_despues = pedir(base, "/stats")[1].get("supply_emitido")
        comprobar("segunda transferencia (nonce nuevo): 200", codigo == 200, f"{codigo} {r}")
        comprobar("el suministro no se mueve",
                  supply_antes == supply_despues, f"{supply_antes} -> {supply_despues}")
        saldo_destino = pedir(base, f"/saldo/{dir_destino}")[1].get("saldo")
        comprobar("el destino tiene los 6 KIA", saldo_destino == 6.0, f"{saldo_destino}")

        print("\n== 6) la contabilidad sigue cuadrando ==")
        codigo, integ = pedir(base, "/ledger/integridad", timeout=120)
        comprobar("cadena y sellos OK con transferencias hechas",
                  codigo == 200 and integ.get("cadena_ok") is True,
                  f"{codigo} {str(integ)[:160]}")

        print("\n== 7) una firma que no es del dueno se rechaza ==")
        nonce3 = "p1-" + secrets.token_hex(6)
        codigo, r = pedir(base, "/transferir", {
            "de_nodo": dir_origen, "a_direccion": dir_destino, "monto": 1.0,
            "nonce": nonce3, "firma": firmar(priv2, dir_origen, dir_destino, 1.0, nonce3),
            "clave_publica": pub2})
        comprobar("firmado con otra clave: 401", codigo == 401, f"{codigo} {r}")
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
            errores = []
            try:
                shutil.rmtree(tmp, ignore_errors=False)
            except Exception as e:
                errores.append(str(e))
            print(f"\ncarpeta temporal borrada: {tmp}" if not errores
                  else f"\nno se pudo borrar {tmp}: {errores}")
        else:
            print(f"\ncarpeta temporal conservada: {tmp}")

    print("\n== RESUMEN ==")
    if fallos:
        print(f"{fallos} comprobacion(es) FALLIDAS")
        print("Si el fallo es 'POST /transferir (v1) guarda la transferencia: 409 nonce ya usado',")
        print("estas viendo P-1: el CREATE TABLE del endpoint no declara firma/clave_publica.")
        return 1
    print("TODO OK (P-1 resuelto)")
    return 0


def _fila(db: Path, de: str, nonce: str):
    """Lee la fila de la tabla, en SOLO LECTURA, de la base temporal.

    No hay endpoint que liste transferencias (el Core no las publica), asi que
    se mira la base de la prueba por fuera. Es la copia temporal de la prueba:
    el ledger real no se abre en ningun momento.
    """
    if not db.exists():
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        r = con.execute("SELECT * FROM transferencias WHERE nonce=? ORDER BY id DESC LIMIT 1",
                        (nonce,)).fetchone()
        return dict(r) if r else None
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--borrar", action="store_true", help="borra la carpeta temporal")
    args = ap.parse_args()
    raise SystemExit(main(args.borrar))
