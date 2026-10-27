"""Concurrencia REAL del camino v1: varias transferencias a la vez desde la MISMA billetera.

QUE MIDE (no lo supone)
    1. Correccion: con N peticiones simultaneas, solo pasan las que caben en el
       saldo. Ni una de mas, ni una de menos, y el saldo nunca queda negativo.
    2. Anti-repeticion bajo carrera: el MISMO nonce disparado desde 12 hilos a
       la vez se guarda UNA vez; las otras 11 reciben 409.
    3. El suministro NO se mueve: una transferencia mueve KIA, no crea.
    4. La contabilidad cuadra: origen 0, destino el total, una fila por
       transferencia aceptada, ni un nonce repetido en la tabla.
    5. Latencia y ritmo reales: media, mediana, p95 y maximo de cada peticion, y
       transferencias por segundo del lote. Eso es la medida; lo demas son
       numeros que se comprueban.

POR QUE HACE FALTA
    Lo unico que habia hasta hoy era una suite secuencial de 28-29 casos. El
    propio informe de la fase C lo apunta como pendiente: "dos transferencias
    simultaneas sobre la misma billetera no se han medido". El mecanismo
    (`_pago_lock` + `BEGIN IMMEDIATE` + comprobacion de saldo DENTRO de la
    transaccion) es el mismo que corre en produccion desde octubre, pero eso es
    un argumento, no una medicion.

NO TOCA
    Nada real: base nueva en carpeta temporal, puerto libre, y al terminar se
    borra con --borrar. El ledger real y el puerto 8300 no se abren.

Uso:
    python tools/prueba_concurrencia_v1.py            # deja la carpeta
    python tools/prueba_concurrencia_v1.py --borrar   # la borra
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import statistics
import sqlite3
import subprocess
import sys
import tempfile
import threading
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

SALDO_INICIAL = 100.0      # KIA sinteticos en la billetera de origen
MONTO = 5.0                # KIA por transferencia
BASELINE = 5               # transferencias de 1 KIA, en serie, para tener referencia
IGUAL_NONCE = 12           # hilos con EL MISMO nonce
DISTINTOS = 24             # hilos con nonces distintos


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
    """La MISMA regla que `direccion_de_pubkey` del Core (KUK + sha256[:38])."""
    import hashlib
    return "KUK" + hashlib.sha256(bytes.fromhex(pub_hex)).hexdigest()[:38].upper()


def billetera_v1():
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
    K._asiento("synth-fondos", {cuenta!r}, {monto!r}, "credito sintetico de prueba de concurrencia")
    K.con.commit()
fila = K.con.execute("SELECT total FROM saldos_cuenta WHERE cuenta=?", ({cuenta!r},)).fetchone()
print("saldo", fila["total"] if fila else 0.0)
print("supply", K.supply_emitido())
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


def rafaga(base: str, encargos: list) -> list:
    """Dispara un encargo por hilo A LA VEZ (barrera) y devuelve los resultados.

    Cada encargo es (de, a, monto, nonce, private_key, pub). Se mide la latencia
    de cada peticion por separado, de la salida a la respuesta.
    """
    n = len(encargos)
    barrera = threading.Barrier(n)
    salida = [None] * n

    def uno(i: int, enc: tuple) -> None:
        de, a, monto, nonce, priv, pub = enc
        firma = firmar(priv, de, a, monto, nonce)
        cuerpo = {"de_nodo": de, "a_direccion": a, "monto": monto,
                  "nonce": nonce, "firma": firma, "clave_publica": pub}
        barrera.wait()          # todos en la misma salida
        t0 = time.perf_counter()
        try:
            codigo, r = pedir(base, "/transferir", cuerpo)
        except Exception as e:  # red, timeout...
            codigo, r = 0, f"{type(e).__name__}: {e}"
        salida[i] = (codigo, r, time.perf_counter() - t0, nonce)

    hilos = [threading.Thread(target=uno, args=(i, e)) for i, e in enumerate(encargos)]
    t0 = time.perf_counter()
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    return salida, time.perf_counter() - t0


def resumen(etiqueta: str, resultados: list, pared: float) -> dict:
    tiempos = sorted(r[2] for r in resultados)
    codigos = {}
    for r in resultados:
        codigos[r[0]] = codigos.get(r[0], 0) + 1
    def pct(p: float) -> float:
        k = max(0, min(len(tiempos) - 1, int(round((len(tiempos) - 1) * p))))
        return tiempos[k]
    print(f"\n   {etiqueta}: {len(resultados)} peticiones en {pared:.2f} s "
          f"({len(resultados)/pared:.1f}/s)")
    print(f"     codigos: {codigos}")
    print(f"     latencia  min {tiempos[0]*1000:.0f} ms | mediana {statistics.median(tiempos)*1000:.0f} ms"
          f" | p95 {pct(0.95)*1000:.0f} ms | max {tiempos[-1]*1000:.0f} ms")
    return codigos


def filas(db: Path) -> list:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM transferencias ORDER BY id")]
    finally:
        con.close()


def asientos(db: Path) -> int:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
    finally:
        con.close()


def main(borrar: bool, lote: int = DISTINTOS) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="concurrencia_v1_"))
    print(f"carpeta temporal: {tmp}")
    proc = archivo = None
    try:
        priv, pub, origen = billetera_v1()
        _, _, destino = billetera_v1()
        env = entorno(tmp)
        base_datos = Path(env["KUKIA_DB"])

        print("\n== 0) preparo: base nueva, 100 KIA sinteticos en la billetera A ==")
        print("   " + sembrar(tmp, env, origen, SALDO_INICIAL).replace("\n", "\n   "))
        proc, base, archivo = arrancar(tmp, env)
        print(f"   Core aislado en {base} (uvicorn, un solo proceso)")
        supply_antes = pedir(base, "/stats")[1].get("supply_emitido")
        asientos_antes = asientos(base_datos)
        print(f"   suministro {supply_antes} | asientos {asientos_antes}")

        print(f"\n== 1) referencia en serie: {BASELINE} transferencias de 1 KIA, una tras otra ==")
        serie = []
        for _ in range(BASELINE):
            nonce = "serie-" + secrets.token_hex(6)
            t0 = time.perf_counter()
            codigo, r = pedir(base, "/transferir", {
                "de_nodo": origen, "a_direccion": destino, "monto": 1.0,
                "nonce": nonce, "firma": firmar(priv, origen, destino, 1.0, nonce),
                "clave_publica": pub})
            serie.append((codigo, r, time.perf_counter() - t0, nonce))
        resumen("en serie", serie, sum(x[2] for x in serie))
        comprobar(f"{BASELINE} transferencias en serie: todas 200",
                  all(x[0] == 200 for x in serie), str([x[0] for x in serie]))

        print(f"\n== 2) EL MISMO nonce desde {IGUAL_NONCE} hilos a la vez ==")
        nonce_igual = "mismo-" + secrets.token_hex(6)
        encargos = [(origen, destino, MONTO, nonce_igual, priv, pub)] * IGUAL_NONCE
        res, pared = rafaga(base, encargos)
        codigos = resumen("mismo nonce", res, pared)
        comprobar("una sola se guarda (1 x 200)", codigos.get(200) == 1, str(codigos))
        comprobar(f"las otras {IGUAL_NONCE-1} reciben 409",
                  codigos.get(409) == IGUAL_NONCE - 1, str(codigos))
        comprobar("ninguna acaba en error del servidor (500/503)",
                  500 not in codigos and 503 not in codigos, str(codigos))

        saldo = pedir(base, f"/saldo/{origen}")[1].get("saldo")
        # Ojo con la cuenta: el lote de referencia ya gasto BASELINE x 1 KIA.
        esperado = SALDO_INICIAL - BASELINE * 1.0 - MONTO
        comprobar(f"el saldo baja UNA vez ({SALDO_INICIAL} - {BASELINE} - {MONTO} = {esperado})",
                  abs(saldo - esperado) < 1e-9, f"{saldo}")

        print(f"\n== 3) {lote} transferencias distintas a la vez (mas de las que caben) ==")
        disponibles = saldo
        caben = int(disponibles // MONTO)
        print(f"   saldo {disponibles} / {MONTO} por transferencia -> caben {caben}, se piden {lote}")
        encargos = [(origen, destino, MONTO, "lote-" + secrets.token_hex(6), priv, pub)
                    for _ in range(lote)]
        res, pared = rafaga(base, encargos)
        codigos = resumen("lote", res, pared)
        comprobar(f"pasan exactamente {caben} (ni una mas por carrera)",
                  codigos.get(200) == caben, str(codigos))
        comprobar(f"las {lote-caben} que no caben reciben 409 de saldo",
                  codigos.get(409) == lote - caben, str(codigos))
        comprobar("ninguna acaba en error del servidor (500/503)",
                  500 not in codigos and 503 not in codigos, str(codigos))

        print("\n== 4) la contabilidad: saldos, filas, nonces y suministro ==")
        saldo_final = pedir(base, f"/saldo/{origen}")[1].get("saldo")
        comprobar("el origen se queda a 0 (no negativo)",
                  abs(saldo_final) < 1e-9, f"{saldo_final}")
        saldo_destino = pedir(base, f"/saldo/{destino}")[1].get("saldo")
        comprobar(f"el destino tiene los {SALDO_INICIAL} KIA",
                  abs(saldo_destino - SALDO_INICIAL) < 1e-9, f"{saldo_destino}")

        supply_despues = pedir(base, "/stats")[1].get("supply_emitido")
        comprobar("el suministro NO se mueve", supply_antes == supply_despues,
                  f"{supply_antes} -> {supply_despues}")

        tabla = filas(base_datos)
        aceptadas = BASELINE + 1 + caben
        comprobar(f"una fila por transferencia aceptada ({aceptadas})",
                  len(tabla) == aceptadas, f"{len(tabla)} filas")
        nonces = [f["nonce"] for f in tabla]
        comprobar("ni un nonce repetido en la tabla",
                  len(nonces) == len(set(nonces)), f"{len(nonces)} nonces, {len(set(nonces))} unicos")
        comprobar("todos los importes son los firmados",
                  all(abs(f["monto"] - (1.0 if f["nonce"].startswith("serie-") else MONTO)) < 1e-12
                      for f in tabla),
                  str([(f["nonce"][:12], f["monto"]) for f in tabla][:5]))
        nuevos = asientos(base_datos) - asientos_antes
        comprobar(f"dos asientos por transferencia aceptada ({aceptadas*2})",
                  nuevos == aceptadas * 2, f"{nuevos} asientos nuevos")

        print("\n== 5) el ledger sigue cuadrado y el registro no tiene sustos ==")
        codigo, integ = pedir(base, "/ledger/integridad", timeout=180)
        comprobar("cadena y sellos OK despues de la paliza",
                  codigo == 200 and integ.get("cadena_ok") is True,
                  f"{codigo} {str(integ)[:160]}")
        registro = (tmp / "core.log").read_text(encoding="utf-8", errors="replace")
        for aguja in ("database is locked", "Traceback", "OperationalError"):
            comprobar(f"en core.log no aparece «{aguja}»", aguja not in registro,
                      f"{registro.count(aguja)} vez/veces")
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
    print("TODO OK: la concurrencia del camino v1 respeta saldo, nonce y suministro")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--borrar", action="store_true", help="borra la carpeta temporal")
    # El limite del Core es 60 peticiones/min a /transferir; con 5 + 12 + 40 = 57
    # se queda justo por debajo. Para subir mas, una ejecucion aparte.
    ap.add_argument("--lote", type=int, default=DISTINTOS,
                    help=f"peticiones simultaneas del lote final (por defecto {DISTINTOS})")
    args = ap.parse_args()
    raise SystemExit(main(args.borrar, args.lote))
