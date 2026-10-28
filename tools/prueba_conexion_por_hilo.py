"""UNA CONEXION SQLITE POR HILO: que dos peticiones a la vez no se pisen el cursor.

EL FALLO QUE ESTO VIGILA (21-10-2026)
    El Core tenia UNA sola conexion para todos los hilos, compartida con
    `check_same_thread=False`. En SQLite, un ROLLBACK (o un BEGIN IMMEDIATE)
    ejecutado en un hilo corta las sentencias QUE ESTAN EN VUELO en esa misma
    conexion, aunque las haya lanzado otro hilo: el `fetchone()` de la otra
    peticion vuelve vacio sin lanzar nada. Eso salia como
      - 404 "nodo no registrado" con el nodo existiendo,
      - 404 "tarea no encontrada" con la tarea existiendo,
      - 500 "error interno en el pago" ('NoneType' object is not subscriptable).
    El arreglo esta puesto desde entonces (`_ConSegura` + `threading.local()`:
    cada hilo abre SU conexion). Lo que NO habia hasta hoy es una medicion que
    lo sujete: esto.

QUE COMPRUEBA (tres partes; la base real y el puerto 8300 no se tocan)
    A) Reproduce el fallo CON una conexion compartida. Si aqui no se pierde ni
       una lectura, la prueba no tiene dientes y se dice en voz alta.
    B) Con el Core de verdad, en proceso y sobre base nueva en carpeta temporal:
       cada hilo recibe SU conexion, la conexion de un hilo no cambia entre
       llamadas, una lectura perezosa NO se corta por el ROLLBACK de otro hilo,
       ningun hilo acaba con transaccion abierta y despues de la paliza una
       escritura nueva entra en seguida (no queda cerrojo de escritura colgando).
    C) De punta a punta por HTTP (uvicorn aislado): 64 peticiones a la vez
       mezclando lecturas (/saldo, /saldos, /stats) con escrituras que hacen
       ROLLBACK (/transferir con firma mala) y escrituras reales. Ninguna
       lectura puede volver vacia ni aparecer un 500/503.

Uso:
    python tools/prueba_conexion_por_hilo.py             # deja las carpetas
    python tools/prueba_conexion_por_hilo.py --borrar    # las borra
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
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
COORD = RAIZ / "coordinator"
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(COORD))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

fallos = 0

# --- Parte A: la conexion compartida (el fallo original) --------------------
FILAS_A = 4000
LECTORES_A = 4
LECTURAS_A_POR_HILO = 120
ESCRITORES_A = 4
ESCRITURAS_A_POR_HILO = 400

# --- Parte B: el Core de verdad ---------------------------------------------
FILAS_B = 3000
LECTORES_B = 4
LECTURAS_B_POR_HILO = 60
ESCRITURAS_B = 400

# --- Parte C: HTTP ----------------------------------------------------------
SALDO_INICIAL = 100.0
# Una SEGUNDA billetera que no recibe ni gasta nada: su saldo tiene que ser el
# mismo en las 20 lecturas aunque al lado se esten moviendo pagos de verdad. Un
# cursor pisado no devuelve "otro numero": devuelve 0.0 o None.
SALDO_OBSERVADOR = 42.0
MONTO = 1.0
N_SALDO = 20          # GET /saldo/{origen}   (lectura perezosa de una fila)
N_SALDOS = 10         # GET /saldos          (lectura perezosa de muchas)
N_STATS = 10          # GET /stats           (varias consultas encadenadas)
N_BUENAS = 12         # POST /transferir 200 (escritura real)
N_MALAS = 12          # POST /transferir 401 (BEGIN + ROLLBACK)
TOTAL_C = N_SALDO + N_SALDOS + N_STATS + N_BUENAS + N_MALAS


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


# ============================================================
#  A) el fallo original, con UNA conexion compartida
# ============================================================

def parte_a(tmp: Path) -> bool:
    """Devuelve True si el fallo se reproduce (la prueba tiene dientes).

    Se replica lo que hacia el Core hasta el 21-10-2026: una conexion, varios
    hilos leyendola y varios mas haciendo BEGIN/INSERT/ROLLBACK sobre la MISMA
    conexion. Una lectura correcta ve siempre FILAS_A filas.
    """
    print("\n== A) con UNA conexion compartida (el fallo original) ==")
    db = tmp / "compartida.db"
    c = sqlite3.connect(str(db), check_same_thread=False, timeout=10)
    vistas: list[int] = []
    errores: list[str] = []
    try:
        c.execute("CREATE TABLE filas(id INTEGER PRIMARY KEY, dato TEXT)")
        c.executemany("INSERT INTO filas(dato) VALUES(?)",
                      [(f"f{i}",) for i in range(FILAS_A)])
        c.commit()

        def lector() -> None:
            arranque.wait()
            for _ in range(LECTURAS_A_POR_HILO):
                try:
                    # Iteracion PEREZOSA a proposito: es la que se corta.
                    vistas.append(sum(1 for _ in c.execute("SELECT id FROM filas")))
                except Exception as e:
                    errores.append(f"lector {type(e).__name__}: {e}")

        def escritor() -> None:
            arranque.wait()
            for _ in range(ESCRITURAS_A_POR_HILO):
                try:
                    c.execute("BEGIN IMMEDIATE")
                    c.execute("INSERT INTO filas(dato) VALUES('z')")
                    c.execute("ROLLBACK")
                except Exception as e:
                    errores.append(f"escritor {type(e).__name__}: {e}")

        arranque = threading.Barrier(LECTORES_A + ESCRITORES_A)
        hilos = [threading.Thread(target=lector) for _ in range(LECTORES_A)]
        hilos += [threading.Thread(target=escritor) for _ in range(ESCRITORES_A)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
    finally:
        c.close()

    cortas = sum(1 for v in vistas if v != FILAS_A)
    total = max(1, len(vistas))
    print(f"   {FILAS_A} filas en la tabla | {LECTORES_A} lectores y {ESCRITORES_A} "
          f"escritores a la vez sobre LA MISMA conexion")
    print(f"   {len(vistas)} lecturas hechas | {cortas} cortas "
          f"({100.0 * cortas / total:.1f} %) | {len(errores)} errores")
    if errores[:3]:
        print(f"   primeros errores: {errores[:3]}")
    if cortas or errores:
        comprobar("el fallo SE REPRODUCE con la conexion compartida "
                  "(la prueba tiene dientes)", True)
        return True
    print("   AVISO: en este Python la conexion compartida no pierde lecturas.")
    print("          La parte A queda como referencia, no como prueba; la B y la C")
    print("          siguen valiendo por si solas.")
    return False


# ============================================================
#  B) el Core de verdad: una conexion por hilo
# ============================================================

def parte_b(tmp: Path) -> None:
    print("\n== B) el Core de verdad: cada hilo, su propia conexion ==")
    # El entorno va ANTES de importar: el Core lee KUKIA_DB al cargarse.
    os.environ.update(entorno(tmp))
    import kukia_core as K

    with K._escritura():
        K.con.execute("CREATE TABLE IF NOT EXISTS prueba_hilos("
                      "id INTEGER PRIMARY KEY AUTOINCREMENT, dato TEXT)")
        K.con.execute("DELETE FROM prueba_hilos")
        K.con.executemany("INSERT INTO prueba_hilos(dato) VALUES(?)",
                          [(f"f{i}",) for i in range(FILAS_B)])
    print(f"   base temporal {os.environ['KUKIA_DB']} | {FILAS_B} filas de prueba")

    vistas: list[int] = []
    errores: list[str] = []
    conexiones: list = []          # una por hilo (referencias fuertes: no se reciclan)
    identidad_estable: list[bool] = []
    sucias: list[str] = []         # hilos que acabaron con transaccion abierta

    def lector(n: int) -> None:
        try:
            for _ in range(LECTURAS_B_POR_HILO):
                # Perezosa Y larga: si otra peticion pudiera pisarla, se veria.
                vistas.append(sum(1 for _ in K.con.execute("SELECT id FROM prueba_hilos")))
        except Exception as e:
            errores.append(f"lector {n} {type(e).__name__}: {e}")
        c1 = K.con._con()
        K.con.execute("SELECT 1").fetchone()
        identidad_estable.append(c1 is K.con._con())
        conexiones.append(c1)
        if K.con.en_transaccion():
            sucias.append(f"lector {n}")

    def escritor(n: int) -> None:
        try:
            for _ in range(ESCRITURAS_B):
                K.con.execute("BEGIN IMMEDIATE")
                K.con.execute("INSERT INTO prueba_hilos(dato) VALUES('z')")
                K.con.execute("ROLLBACK")
        except Exception as e:
            errores.append(f"escritor {n} {type(e).__name__}: {e}")
        c1 = K.con._con()
        identidad_estable.append(c1 is K.con._con())
        conexiones.append(c1)
        if K.con.en_transaccion():
            sucias.append(f"escritor {n}")

    hilos = [threading.Thread(target=lector, args=(i,)) for i in range(LECTORES_B)]
    hilos.append(threading.Thread(target=escritor, args=(0,)))
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    distintas = len({id(c) for c in conexiones})
    print(f"   {len(vistas)} lecturas perezosas de {FILAS_B} filas mientras un hilo "
          f"hace {ESCRITURAS_B} BEGIN/INSERT/ROLLBACK en su conexion")
    print(f"   conexiones distintas entre {len(conexiones)} hilos: {distintas}")
    if errores[:3]:
        print(f"   primeros errores: {errores[:3]}")

    comprobar(f"cada hilo tiene SU conexion ({len(conexiones)} hilos -> {distintas} conexiones)",
              distintas == len(conexiones), f"{distintas} de {len(conexiones)}")
    comprobar("la conexion de un hilo no cambia entre llamadas",
              bool(identidad_estable) and all(identidad_estable),
              f"{identidad_estable.count(False)} cambios de conexion")
    cortas = sum(1 for v in vistas if v != FILAS_B)
    comprobar(f"NINGUNA lectura se corta por el ROLLBACK de otro hilo ({len(vistas)} lecturas)",
              cortas == 0 and len(vistas) == LECTORES_B * LECTURAS_B_POR_HILO,
              f"{cortas} cortas de {len(vistas)}")
    comprobar("ningun error de lectura/escritura en los hilos", not errores, str(errores[:2]))
    comprobar("ningun hilo acaba con transaccion abierta",
              not sucias, str(sucias))

    # Si un hilo dejara el BEGIN colgando, su conexion (que el hilo REUTILIZA en
    # la peticion siguiente) mantendria el cerrojo de escritura para siempre.
    marca = time.perf_counter()
    try:
        with K._escritura():
            K.con.execute("INSERT INTO prueba_hilos(dato) VALUES('despues')")
        escrito = True
    except Exception as e:
        escrito = False
        errores.append(f"escritura final {type(e).__name__}: {e}")
    tardo = time.perf_counter() - marca
    comprobar(f"despues de la paliza, una escritura nueva entra rapido ({tardo:.2f} s)",
              escrito and tardo < 5.0, f"escrito={escrito}, {tardo:.2f} s")


# ============================================================
#  C) de punta a punta por HTTP
# ============================================================

def direccion_v1(pub_hex: str) -> str:
    """La MISMA regla que `direccion_de_pubkey` del Core (KUK + sha256[:38])."""
    return "KUK" + hashlib.sha256(bytes.fromhex(pub_hex)).hexdigest()[:38].upper()


def billetera_v1():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw).hex()
    return priv, pub, direccion_v1(pub)


def firmar(priv, de: str, a: str, monto: float, nonce: str) -> str:
    return priv.sign(f"transferir:{de}:{a}:{monto}:{nonce}".encode()).hex()


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
os._exit(0)
"""


def sembrar(env: dict, cuenta: str, monto: float) -> str:
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
            raise SystemExit(f"el Core murio al arrancar (codigo {proc.returncode}); "
                             f"ver {tmp/'core.log'}")
        try:
            codigo, _ = pedir(base, "/stats", timeout=2.0)
            if codigo == 200:
                return proc, base, archivo
        except Exception:
            pass
        time.sleep(0.25)
    proc.kill()
    raise SystemExit(f"el Core no respondio; ver {tmp/'core.log'}")


def parte_c(tmp: Path) -> None:
    print("\n== C) de punta a punta por HTTP: lecturas y escrituras a la vez ==")
    priv, pub, origen = billetera_v1()
    _, _, destino = billetera_v1()
    _, _, observador = billetera_v1()
    env = entorno(tmp)
    print("   " + sembrar(env, origen, SALDO_INICIAL).replace("\n", "\n   "))
    print("   " + sembrar(env, observador, SALDO_OBSERVADOR).replace("\n", "\n   "))
    proc = archivo = None
    registro = ""
    saldo_final = saldo_observador = supply_despues = None
    try:
        proc, base, archivo = arrancar(tmp, env)
        print(f"   Core aislado en {base} (uvicorn, un solo proceso)")
        supply_antes = pedir(base, "/stats")[1].get("supply_emitido")

        barrera = threading.Barrier(TOTAL_C)
        salidas: list[tuple] = [None] * TOTAL_C   # type: ignore[list-item]

        def leer_saldo(i: int) -> None:
            barrera.wait()
            t0 = time.perf_counter()
            try:
                codigo, r = pedir(base, f"/saldo/{observador}")
            except Exception as e:
                codigo, r = 0, f"{type(e).__name__}: {e}"
            salidas[i] = ("saldo", codigo, r, time.perf_counter() - t0)

        def leer_saldos(i: int) -> None:
            barrera.wait()
            t0 = time.perf_counter()
            try:
                codigo, r = pedir(base, "/saldos")
            except Exception as e:
                codigo, r = 0, f"{type(e).__name__}: {e}"
            salidas[i] = ("saldos", codigo, r, time.perf_counter() - t0)

        def leer_stats(i: int) -> None:
            barrera.wait()
            t0 = time.perf_counter()
            try:
                codigo, r = pedir(base, "/stats")
            except Exception as e:
                codigo, r = 0, f"{type(e).__name__}: {e}"
            salidas[i] = ("stats", codigo, r, time.perf_counter() - t0)

        def transferir(i: int, buena: bool) -> None:
            nonce = ("b-" if buena else "m-") + secrets.token_hex(6)
            cuerpo = {"de_nodo": origen, "a_direccion": destino, "monto": MONTO,
                      "nonce": nonce, "clave_publica": pub,
                      "firma": (firmar(priv, origen, destino, MONTO, nonce) if buena
                                else "00" * 64)}
            barrera.wait()
            t0 = time.perf_counter()
            try:
                codigo, r = pedir(base, "/transferir", cuerpo)
            except Exception as e:
                codigo, r = 0, f"{type(e).__name__}: {e}"
            salidas[i] = (("transferir buena" if buena else "transferir mala"),
                          codigo, r, time.perf_counter() - t0)

        hilos = []
        i = 0
        for _ in range(N_SALDO):
            hilos.append(threading.Thread(target=leer_saldo, args=(i,))); i += 1
        for _ in range(N_SALDOS):
            hilos.append(threading.Thread(target=leer_saldos, args=(i,))); i += 1
        for _ in range(N_STATS):
            hilos.append(threading.Thread(target=leer_stats, args=(i,))); i += 1
        for _ in range(N_BUENAS):
            hilos.append(threading.Thread(target=transferir, args=(i, True))); i += 1
        for _ in range(N_MALAS):
            hilos.append(threading.Thread(target=transferir, args=(i, False))); i += 1

        t0 = time.perf_counter()
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        pared = time.perf_counter() - t0
        print(f"   {TOTAL_C} peticiones a la vez (barrera) en {pared:.2f} s "
              f"({TOTAL_C/pared:.1f}/s)")
        # Lo que se lee del Core VIVO, antes de apagarlo.
        saldo_final = pedir(base, f"/saldo/{origen}")[1].get("saldo")
        saldo_observador = pedir(base, f"/saldo/{observador}")[1].get("saldo")
        supply_despues = pedir(base, "/stats")[1].get("supply_emitido")
        # Lo que ha escrito el Core durante la paliza (se lee con el proceso aun
        # vivo: un Traceback ya esta volcado en ese momento).
        registro = (tmp / "core.log").read_text(encoding="utf-8", errors="replace")
    finally:
        if archivo is not None:
            try:
                archivo.close()
            except Exception:
                pass
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=15)

    codigos: dict = {}
    for tipo, codigo, _, _ in salidas:  # type: ignore[misc]
        codigos[(tipo, codigo)] = codigos.get((tipo, codigo), 0) + 1
    for clave in sorted(codigos, key=lambda k: (k[0], k[1])):
        print(f"     {clave[0]:<17} {clave[1]}: {codigos[clave]}")

    raros = sum(n for (tipo, codigo), n in codigos.items() if codigo in (0, 500, 502, 503))
    comprobar("ninguna peticion acaba en error del servidor (0/500/502/503)",
              raros == 0, str({k: v for k, v in codigos.items() if k[1] in (0, 500, 502, 503)}))

    valores = [r.get("saldo") for tipo, _, r, _ in salidas  # type: ignore[misc]
               if tipo == "saldo" and isinstance(r, dict)]
    comprobar(f"las {N_SALDO} lecturas de /saldo de la billetera quieta devuelven "
              f"{SALDO_OBSERVADOR} exactos, con pagos reales al lado "
              f"(un cursor pisado devolveria 0 o None)",
              len(valores) == N_SALDO and all(v == SALDO_OBSERVADOR for v in valores),
              f"{len(valores)} valores, {sorted(set(v for v in valores if v is not None))[:5]}")

    mapa = [r for tipo, _, r, _ in salidas  # type: ignore[misc]
            if tipo == "saldos" and isinstance(r, dict)]
    enteros = [r for r in mapa
               if (r.get("saldos") or {}).get(observador) == SALDO_OBSERVADOR]
    comprobar(f"las {N_SALDOS} lecturas de /saldos traen la billetera quieta con su "
              f"saldo (lista truncada = cursor pisado)",
              len(enteros) == N_SALDOS,
              f"{len(enteros)} de {N_SALDOS}; direcciones vistas "
              f"{sorted({r.get('direcciones') for r in mapa})}")

    suministros = [r.get("supply_emitido") for tipo, _, r, _ in salidas  # type: ignore[misc]
                   if tipo == "stats" and isinstance(r, dict)]
    comprobar(f"las {N_STATS} lecturas de /stats son coherentes entre si",
              len(suministros) == N_STATS and len(set(suministros)) == 1,
              f"{len(suministros)} lecturas, valores {sorted(set(suministros))[:5]}")

    buenas = codigos.get(("transferir buena", 200), 0)
    malas = codigos.get(("transferir mala", 401), 0)
    comprobar(f"las {N_BUENAS} transferencias buenas pasan (200)", buenas == N_BUENAS,
              f"{buenas}")
    comprobar(f"las {N_MALAS} con firma mala reciben 401 (y no un 500 en el ROLLBACK)",
              malas == N_MALAS, f"{malas}")

    esperado = SALDO_INICIAL - MONTO * N_BUENAS
    comprobar(f"el saldo de origen cuadra tras la carrera "
              f"({SALDO_INICIAL} - {N_BUENAS} = {esperado})",
              saldo_final == esperado, f"{saldo_final}")
    comprobar(f"la billetera quieta sigue con {SALDO_OBSERVADOR} al final",
              saldo_observador == SALDO_OBSERVADOR, f"{saldo_observador}")
    comprobar("el suministro no se mueve con las transferencias",
              supply_antes == supply_despues, f"{supply_antes} -> {supply_despues}")
    agujas = ("Traceback", "database is locked", "cannot start a transaction within a transaction",
              "Recursive use of cursors", "NoneType' object is not subscriptable")
    for aguja in agujas:
        comprobar(f"en core.log no aparece «{aguja}»", aguja not in registro,
                  f"{registro.count(aguja)} vez/veces")


def main(borrar: bool) -> int:
    tmp_a = Path(tempfile.mkdtemp(prefix="hilos_a_"))
    tmp_b = Path(tempfile.mkdtemp(prefix="hilos_b_"))
    tmp_c = Path(tempfile.mkdtemp(prefix="hilos_c_"))
    print(f"carpetas temporales:\n   {tmp_a}\n   {tmp_b}\n   {tmp_c}")
    try:
        parte_a(tmp_a)
        parte_b(tmp_b)
        parte_c(tmp_c)
    finally:
        if borrar:
            for t in (tmp_a, tmp_b, tmp_c):
                shutil.rmtree(t, ignore_errors=True)
            print("\ncarpetas temporales borradas")
        else:
            print("\ncarpetas temporales conservadas (--borrar para quitarlas)")

    print("\n== RESUMEN ==")
    if fallos:
        print(f"{fallos} comprobacion(es) FALLIDAS")
        return 1
    print("TODO OK: una conexion por hilo, y el cursor de una peticion no lo pisa otra")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--borrar", action="store_true", help="borra las carpetas temporales")
    args = ap.parse_args()
    raise SystemExit(main(args.borrar))
