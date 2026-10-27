"""Prueba de integracion del camino poscuantico (/transferir/v2) en un Core aislado.

Que hace, y que NO hace:

  - Arranca un Core de VERDAD (uvicorn kukia_core:app) desde esta copia del
    repo, en un puerto libre, con una base de datos NUEVA en una carpeta
    temporal. Nunca abre el ledger real ni el puerto 8300.
  - Los fondos son sinteticos: se ingresan con el propio `_asiento` del Core,
    asi que la cadena de hashes sigue cuadrando y /ledger/integridad vale como
    prueba de que la integracion no rompio la contabilidad.
  - Comprueba lo que hay que comprobar: que el sobre hibrido se acepta, que el
    downgrade a Ed25519 sola NO, que un sobre repetido no cobra dos veces, que
    el importe firmado es el importe movido, y que el camino v1 sigue igual.

Uso:
  python tools/prueba_pqc_core.py            # deja la carpeta temporal
  python tools/prueba_pqc_core.py --borrar   # la borra al terminar
"""

from __future__ import annotations

import argparse
import json
import os
import re
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

from cryptography.hazmat.primitives import serialization          # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ed25519     # noqa: E402

from pqc import firma_hibrida, llaves                             # noqa: E402


# ----------------------------------------------------------------------------
#  Utilidades
# ----------------------------------------------------------------------------
def puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# Columnas que escribe el INSERT del camino v1 (/transferir). Solo se usan como
# control negativo: la comprobacion que importa las saca del FUENTE.
COLUMNAS_INSERT_V1 = ["de_nodo", "a_direccion", "monto", "nonce", "ts",
                      "firma", "clave_publica"]


def esquema_v1_del_fuente(fuente) -> tuple:
    """(CREATE TABLE, columnas del INSERT) del camino v1, leidos del FUENTE.

    Se leen del codigo y no de una copia a mano para que la prueba no pueda
    quedarse obsoleta: si el CREATE vuelve a declarar menos columnas de las que
    escribe su INSERT, aqui se nota (hallazgo P-1, corregido el 27-10-2026).
    """
    texto = Path(fuente).read_text(encoding="utf-8", errors="replace")
    trozo = texto[texto.rindex('@app.post("/transferir")'):]
    crear = re.findall(r"CREATE TABLE IF NOT EXISTS transferencias\((.*?)"
                       r"UNIQUE\(de_nodo, nonce\)\)", trozo, re.S)[-1]
    insertar = re.findall(r"INSERT INTO transferencias\(([^)]*)\)", trozo)[-1]
    declaracion = ("CREATE TABLE IF NOT EXISTS transferencias("
                   + crear.replace('"', " ") + "UNIQUE(de_nodo, nonce))")
    return (" ".join(declaracion.split()),
            [c.strip() for c in insertar.replace('"', " ").split(",") if c.strip()])


def cabe_el_insert(declaracion: str, columnas: list, tmp: Path, nombre: str) -> bool:
    """¿El esquema declarado admite el INSERT con esas columnas? Base NUEVA."""
    frasco = tmp / nombre
    if frasco.exists():
        frasco.unlink()
    con = sqlite3.connect(str(frasco))
    try:
        con.execute(declaracion)
        marcas = ",".join("?" * len(columnas))
        con.execute(f"INSERT INTO transferencias({','.join(columnas)}) "
                    f"VALUES({marcas})", tuple("x" for _ in columnas))
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()


def pedir(base: str, ruta: str, datos=None, metodo=None, timeout=20.0):
    """Devuelve (codigo, cuerpo_json_o_texto). No lanza en 4xx/5xx."""
    url = base + ruta
    cuerpo = None
    cabeceras = {}
    if datos is not None:
        cuerpo = json.dumps(datos).encode("utf-8")
        cabeceras["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=cuerpo, headers=cabeceras,
                                 method=metodo or ("POST" if cuerpo is not None else "GET"))
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


def billetera_v2() -> tuple:
    """Billetera v2 sintetica. Las 'palabras' son aleatorias: no hay semilla real."""
    palabras = [secrets.token_hex(4) for _ in range(20)]
    b, _, _ = llaves.crear(palabras, secrets.token_hex(8))
    return b


def billetera_v1() -> tuple:
    """Billetera v1 sintetica: Ed25519 sola, direccion KUK... de siempre."""
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw).hex()
    return priv, pub, llaves.direccion_v1(pub)


def entorno(tmp: Path, pqc: bool) -> dict:
    env = dict(os.environ)
    env.update({
        "KUKIA_DB": str(tmp / "kukia.db"),
        "KUKIA_SELLO_KEY": str(tmp / "sello_key.txt"),
        "KUKIA_SELLOS_FILE": str(tmp / "sellos.jsonl"),
        "KUKIA_ADMIN_TOKEN": str(tmp / "admin_token.txt"),
        "KUKIA_AUTO_TAREAS": "0",
        "KUKIA_ARCHIVAR": "0",
        "KUKIA_SELLO_SEG": "3600",
        "KUKIA_SELLO_BLOQUEA": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    env["KUKIA_PQC"] = "1" if pqc else "0"
    return env


_INYECTAR = r"""
import os, sys
sys.path.insert(0, {raiz!r})
sys.path.insert(0, {coord!r})
import kukia_core as K
cuentas = {cuentas!r}
with K._pago_lock:
    K.con.execute("BEGIN IMMEDIATE")
    # La tabla de transferencias de un ledger REAL creado antes del
    # 27-10-2026 tiene 8 columnas (firma y clave_publica incluidas). Aqui se
    # reproduce ese esquema para medir la regresion de v1 sin ruido. El apaño
    # ya no es obligatorio: el CREATE de /transferir declaraba 6 columnas
    # (hallazgo P-1, corregido) y ahora declara las 8, asi que esto solo
    # emula una base antigua. Lo vigilan N1 y N2 de mas abajo.
    K.con.execute("CREATE TABLE IF NOT EXISTS transferencias("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, de_nodo TEXT, "
                  "a_direccion TEXT, monto REAL, nonce TEXT, ts TEXT, "
                  "firma TEXT, clave_publica TEXT, UNIQUE(de_nodo, nonce))")
    for cuenta, monto in cuentas:
        K._asiento("synth-fondos", cuenta, float(monto), "credito sintetico de prueba")
    K.con.commit()
for cuenta, _ in cuentas:
    fila = K.con.execute("SELECT total FROM saldos_cuenta WHERE cuenta=?",
                         (cuenta,)).fetchone()
    print("saldo", cuenta[:12] + "...", fila["total"] if fila else 0.0)
print("supply", K.supply_emitido())
os._exit(0)
"""


# El mismo ingreso, pero SIN crear la tabla de transferencias: es lo que hace
# falta para probar P-1 de punta a punta (que la cree el Core, no el apaño).
_INYECTAR_LIMPIO = r"""
import os, sys
sys.path.insert(0, {raiz!r})
sys.path.insert(0, {coord!r})
import kukia_core as K
cuentas = {cuentas!r}
with K._pago_lock:
    K.con.execute("BEGIN IMMEDIATE")
    for cuenta, monto in cuentas:
        K._asiento("synth-fondos", cuenta, float(monto), "credito sintetico de prueba P-1")
    K.con.commit()
print("supply", K.supply_emitido())
tablas = [r[0] for r in K.con.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='transferencias'")]
print("tabla_transferencias_existe", bool(tablas))
os._exit(0)
"""


def sembrar(tmp: Path, env: dict, cuentas: list, crear_tabla: bool = True) -> str:
    """Ingresa fondos sinteticos con el _asiento real (cadena de hashes intacta).

    Con `crear_tabla=False` NO se crea la tabla de transferencias: la tiene que
    crear el Core al vuelo. Devuelve la salida del proceso (lleva el suministro
    y si la tabla existia), que es lo que mira la prueba de P-1.
    """
    plantilla = _INYECTAR if crear_tabla else _INYECTAR_LIMPIO
    codigo = plantilla.format(raiz=str(RAIZ), coord=str(COORD), cuentas=cuentas)
    r = subprocess.run([sys.executable, "-c", codigo], cwd=str(COORD), env=env,
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise SystemExit(f"ERROR sembrando fondos:\n{r.stdout}\n{r.stderr}")
    print("   " + "\n   ".join(l for l in r.stdout.strip().splitlines() if l.startswith("supply")))
    return r.stdout


def _columnas(db: Path, tabla: str) -> list:
    """Columnas de una tabla de la base de la prueba, en SOLO LECTURA."""
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return [f[1] for f in con.execute(f"PRAGMA table_info({tabla})")]
    finally:
        con.close()


def _fila_nonce(db: Path, nonce: str):
    """La fila de transferencias de ese nonce, en SOLO LECTURA (o None)."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return con.execute("SELECT * FROM transferencias WHERE nonce=? "
                           "ORDER BY id DESC LIMIT 1", (nonce,)).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def arrancar(tmp: Path, env: dict, nombre_log: str) -> tuple:
    puerto = puerto_libre()
    archivo = open(tmp / nombre_log, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "kukia_core:app",
         "--host", "127.0.0.1", "--port", str(puerto)],
        cwd=str(COORD), env=env, stdout=archivo, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{puerto}"
    for _ in range(120):
        if proc.poll() is not None:
            raise SystemExit(f"el Core murio al arrancar (codigo {proc.returncode}); "
                             f"ver {tmp / nombre_log}")
        try:
            codigo, _ = pedir(base, "/stats", timeout=2.0)
            if codigo == 200:
                return proc, base, archivo
        except Exception:
            pass
        time.sleep(0.25)
    proc.kill()
    raise SystemExit(f"el Core no respondio en 30 s; ver {tmp / nombre_log}")


def parar(proc, archivo) -> None:
    try:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=15)
    except Exception:
        pass
    try:
        archivo.close()
    except Exception:
        pass


# ----------------------------------------------------------------------------
#  Casos
# ----------------------------------------------------------------------------
class Casos:
    def __init__(self):
        self.filas = []

    def apunta(self, ident: str, descripcion: str, ok: bool, detalle: str):
        self.filas.append((ident, descripcion, ok, detalle))
        print(f"   [{'OK ' if ok else 'FALLO'}] {ident}: {detalle}")

    @property
    def fallos(self) -> list:
        return [f for f in self.filas if not f[2]]


def sobre_de(w, de: str, a: str, monto: str, nonce: str) -> dict:
    return firma_hibrida.firmar("transferir", w, de=de, a=a, monto=monto, nonce=nonce)


def enviar(base, sobre, w, ed=None, pq=None) -> tuple:
    return pedir(base, "/transferir/v2", {
        "sobre": sobre,
        "clave_publica_ed25519": ed if ed is not None else w.pub_ed,
        "clave_publica_mldsa65": pq if pq is not None else w.pub_pq,
    })


def prueba_hibrida(tmp: Path, c: Casos):
    print("\n== CORE CON KUKIA_PQC=1 ==")
    w1, w2 = billetera_v2(), billetera_v2()
    priv_v1, pub_v1, dir_v1 = billetera_v1()
    _, pub_v1b, dir_v1b = billetera_v1()          # destino del camino v1
    env = entorno(tmp, pqc=True)
    print(f"   billetera v2 #1: {w1.direccion}")
    print(f"   billetera v1   : {dir_v1}")
    sembrar(tmp, env, [(w1.direccion, 100.0), (dir_v1, 50.0)])

    proc, base, log = arrancar(tmp, env, "core_pqc.log")
    try:
        codigo, est = pedir(base, "/pqc/estado")
        c.apunta("P1", "el Core dice que verifica poscuantico",
                 codigo == 200 and est.get("activo") is True, f"{codigo} {est}")

        codigo, st0 = pedir(base, "/stats")
        supply0 = st0.get("supply_emitido") if isinstance(st0, dict) else None

        # --- A. el camino feliz -------------------------------------------
        s = sobre_de(w1, w1.direccion, w2.direccion, "12.5", "n-hib-001")
        codigo, r = enviar(base, s, w1)
        c.apunta("A1", "transferencia hibrida valida",
                 codigo == 200 and isinstance(r, dict) and r.get("monto") == 12.5,
                 f"{codigo} {r if isinstance(r, dict) else r}")

        codigo, sal = pedir(base, f"/saldo/{w1.direccion}")
        c.apunta("A2", "el origen queda con 87.5",
                 codigo == 200 and sal.get("saldo") == 87.5, f"{sal}")
        codigo, sal = pedir(base, f"/saldo/{w2.direccion}")
        c.apunta("A3", "el destino queda con 12.5",
                 codigo == 200 and sal.get("saldo") == 12.5, f"{sal}")
        codigo, st1 = pedir(base, "/stats")
        c.apunta("A4", "una transferencia no crea moneda (supply igual)",
                 isinstance(st1, dict) and st1.get("supply_emitido") == supply0,
                 f"antes={supply0} despues={st1.get('supply_emitido') if isinstance(st1, dict) else '?'}")

        # --- B. downgrade: Ed25519 sola, bien firmada ----------------------
        # El atacante que quiera saltarse la firma poscuantica tiene que
        # rehacer el sobre con alg=[ed25519] y volver a firmar. Aqui se hace
        # EXACTAMENTE eso, bien hecho: la firma Ed25519 es valida. Si el Core
        # la acepta, el anti-downgrade no sirve.
        campos = dict(de=w1.direccion, a=w2.direccion, monto="1", nonce="n-downgrade")
        cuerpo_ed = firma_hibrida.mensaje.cuerpo("transferir", ["ed25519"], **campos)
        s = {"v": 2, "red": firma_hibrida.mensaje.RED, "tipo": "transferir",
             "alg": ["ed25519"], "cuerpo": cuerpo_ed, "firmas": {}}
        s["firmas"]["ed25519"] = w1.firmar_ed(
            firma_hibrida.mensaje.cabecera("transferir", ["ed25519"])
            + firma_hibrida.mensaje.canonico(cuerpo_ed)).hex()
        codigo, r = enviar(base, s, w1)
        c.apunta("B1", "sobre solo-Ed25519 bien firmado rechazado (anti-downgrade)",
                 codigo == 401, f"{codigo} {r}")

        # --- C. sobre al que le falta la firma poscuantica ------------------
        s = sobre_de(w1, w1.direccion, w2.direccion, "1", "n-sin-pq")
        s["firmas"].pop("mldsa65")
        codigo, r = enviar(base, s, w1)
        c.apunta("C1", "sobre sin firma ML-DSA rechazado", codigo == 401, f"{codigo} {r}")

        # --- D. repeticion ------------------------------------------------
        s = sobre_de(w1, w1.direccion, w2.direccion, "1", "n-repetido")
        c1, r1 = enviar(base, s, w1)
        c2, r2 = enviar(base, s, w1)
        c.apunta("D1", "primer envio aceptado", c1 == 200, f"{c1}")
        c.apunta("D2", "repetir el MISMO sobre no cobra dos veces",
                 c2 == 409, f"{c2} {r2}")
        # y con firma nueva sobre el mismo nonce (nonce gastado)
        s_nuevo = sobre_de(w1, w1.direccion, w2.direccion, "1", "n-repetido")
        c3, r3 = enviar(base, s_nuevo, w1)
        c.apunta("D3", "nonce ya usado con otra firma: 409", c3 == 409, f"{c3} {r3}")

        # --- E. manipulacion despues de firmar -----------------------------
        s = sobre_de(w1, w1.direccion, w2.direccion, "1", "n-tamper-monto")
        s["cuerpo"]["monto"] = "99"
        codigo, r = enviar(base, s, w1)
        c.apunta("E1", "importe cambiado tras firmar: 401", codigo == 401, f"{codigo} {r}")

        s = sobre_de(w1, w1.direccion, w2.direccion, "1", "n-tamper-destino")
        s["cuerpo"]["a"] = w1.direccion
        codigo, r = enviar(base, s, w1)
        c.apunta("E2", "destino cambiado tras firmar: 401", codigo == 401, f"{codigo} {r}")

        s = sobre_de(w1, w1.direccion, w2.direccion, "1", "n-no-canonico")
        s["cuerpo"]["monto"] = "1.00"
        codigo, r = enviar(base, s, w1)
        c.apunta("E3", "importe no canonico ('1.00'): 401", codigo == 401, f"{codigo} {r}")

        # --- F. claves de otra billetera -----------------------------------
        s = sobre_de(w1, w1.direccion, w2.direccion, "1", "n-claves-ajenas")
        codigo, r = enviar(base, s, w1, ed=w2.pub_ed, pq=w2.pub_pq)
        c.apunta("F1", "claves que no son duenas de la direccion: 401",
                 codigo == 401, f"{codigo} {r}")

        # --- G. dinero y destino -------------------------------------------
        s = sobre_de(w1, w1.direccion, w2.direccion, "5000", "n-sin-saldo")
        codigo, r = enviar(base, s, w1)
        c.apunta("G1", "mas importe que saldo: 409", codigo == 409, f"{codigo} {r}")

        s = sobre_de(w1, w1.direccion, "destino-inventado", "1", "n-destino-malo")
        codigo, r = enviar(base, s, w1)
        c.apunta("G2", "destino que no existe: 400", codigo == 400, f"{codigo} {r}")

        s = sobre_de(w1, w1.direccion, w1.direccion, "1", "n-a-si-mismo")
        codigo, r = enviar(base, s, w1)
        c.apunta("G3", "origen y destino iguales: 401", codigo == 401, f"{codigo} {r}")

        # --- H. sobre de otra operacion ------------------------------------
        otro = firma_hibrida.firmar("telemetria", w1, nodo="x", nonce="n-otro")
        codigo, r = enviar(base, otro, w1)
        c.apunta("H1", "sobre que no es 'transferir': 401", codigo == 401, f"{codigo} {r}")

        # --- I. origen v1 en el camino v2 ----------------------------------
        s = sobre_de(w1, w1.direccion, w2.direccion, "1", "n-origen-v1")
        s["cuerpo"]["de"] = dir_v1
        codigo, r = enviar(base, s, w1)
        c.apunta("I1", "origen KUK... (v1) no entra por el camino v2: 401",
                 codigo == 401, f"{codigo} {r}")

        # --- J. (la mas importante) el camino v1 sigue funcionando ---------
        a_v1 = round(float(5), 4)
        nonce_v1 = "n-v1-001"
        firma_v1 = priv_v1.sign(f"transferir:{dir_v1}:{dir_v1b}:{a_v1}:{nonce_v1}".encode()).hex()
        codigo, r = pedir(base, "/transferir", {
            "de_nodo": dir_v1, "a_direccion": dir_v1b, "monto": a_v1,
            "nonce": nonce_v1, "firma": firma_v1, "clave_publica": pub_v1})
        c.apunta("J1", "REGRESION: /transferir v1 sigue aceptando Ed25519 sola",
                 codigo == 200, f"{codigo} {r}")
        codigo, sal = pedir(base, f"/saldo/{dir_v1b}")
        c.apunta("J2", "el destino v1 cobra 5",
                 codigo == 200 and sal.get("saldo") == 5.0, f"{sal}")

        # --- K. el ledger sigue cuadrando -----------------------------------
        codigo, integ = pedir(base, "/ledger/integridad")
        ok_ledger = (codigo == 200 and isinstance(integ, dict)
                     and integ.get("cadena_ok") is True
                     and not integ.get("problemas")
                     and integ.get("sellos_ok") is True)
        c.apunta("K1", "cadena de hashes y sellos intactos tras todo lo anterior",
                 ok_ledger, f"{codigo} {integ}")

        # --- L. el sobre queda registrado por su huella ---------------------
        codigo, r = pedir(base, "/ledger")
        c.apunta("L1", "hay asientos con el id propio 'tx-pq-'",
                 codigo == 200 and "tx-pq-" in json.dumps(r, ensure_ascii=False),
                 f"{codigo} ({len(json.dumps(r)) if isinstance(r, (dict, list)) else 0} B)")

        # --- N. P-1: el esquema del v1 tiene que cubrir su propio INSERT ---
        # (27-10-2026) El CREATE TABLE de /transferir declaraba 6 columnas y su
        # INSERT escribia 7 (firma, clave_publica): en una instalacion NUEVA la
        # primera transferencia fallaba SIEMPRE y el except generico de
        # /transferir lo disfrazaba de "nonce ya usado (repeticion bloqueada)".
        # P-1 esta corregido, asi que esto deja de ser una copia a mano del SQL
        # viejo y pasa a leer el FUENTE: si alguien vuelve a dejar el CREATE
        # corto, la suite se pone en rojo en vez de quedarse verde por inercia.
        # Es el mismo defecto que reproduce tools/prueba_p1_transferencia_v1.py
        # de punta a punta (Core aislado, base nueva, transferencia real).
        viejo = ("CREATE TABLE IF NOT EXISTS transferencias("
                 "id INTEGER PRIMARY KEY AUTOINCREMENT, de_nodo TEXT, "
                 "a_direccion TEXT, monto REAL, nonce TEXT, ts TEXT, "
                 "UNIQUE(de_nodo, nonce))")
        c.apunta("N1", "control negativo: el esquema viejo (6 col.) NO acepta el INSERT",
                 not cabe_el_insert(viejo, COLUMNAS_INSERT_V1, tmp, "db_vieja.db"),
                 "el esquema corto acepto el INSERT de 7 columnas: control invalido")
        declaracion, columnas = esquema_v1_del_fuente(COORD / "kukia_core.py")
        c.apunta("N2", "P-1 resuelto: el CREATE del fuente cubre su propio INSERT",
                 cabe_el_insert(declaracion, columnas, tmp, "db_fresca.db"),
                 f"el fuente declara: {declaracion}; INSERT: {columnas}")
    finally:
        parar(proc, log)
    return w1, w2


def prueba_apagado(tmp: Path, c: Casos):
    print("\n== CORE CON KUKIA_PQC=0 (por defecto) ==")
    w1, w2 = billetera_v2(), billetera_v2()
    priv_v1, pub_v1, dir_v1 = billetera_v1()
    _, pub_v1b, dir_v1b = billetera_v1()
    env = entorno(tmp, pqc=False)
    sembrar(tmp, env, [(w1.direccion, 10.0), (dir_v1, 10.0)])

    proc, base, log = arrancar(tmp, env, "core_sin_pqc.log")
    try:
        codigo, est = pedir(base, "/pqc/estado")
        c.apunta("M1", "con el flag apagado, /pqc/estado dice activo=false",
                 codigo == 200 and est.get("activo") is False, f"{codigo} {est}")

        s = sobre_de(w1, w1.direccion, w2.direccion, "1", "n-apagado")
        codigo, r = enviar(base, s, w1)
        c.apunta("M2", "con el flag apagado, /transferir/v2 rechaza (503)",
                 codigo == 503, f"{codigo} {r}")

        a_v1 = round(float(1), 4)
        nonce_v1 = "n-v1-sinpq"
        firma = priv_v1.sign(f"transferir:{dir_v1}:{dir_v1b}:{a_v1}:{nonce_v1}".encode()).hex()
        codigo, r = pedir(base, "/transferir", {
            "de_nodo": dir_v1, "a_direccion": dir_v1b, "monto": a_v1,
            "nonce": nonce_v1, "firma": firma, "clave_publica": pub_v1})
        c.apunta("M3", "REGRESION: v1 funciona igual con el flag apagado",
                 codigo == 200, f"{codigo} {r}")

        codigo, sal = pedir(base, f"/saldo/{w1.direccion}")
        c.apunta("M4", "ninguna transferencia poscuantica ocurrio",
                 codigo == 200 and sal.get("saldo") == 10.0, f"{sal}")
    finally:
        parar(proc, log)


def prueba_p1_punta_a_punta(tmp: Path, c: Casos):
    """P-1 de punta a punta: base limpia y una transferencia v1 de verdad.

    N1 y N2 miran el SQL en una base de usar y tirar. Esto mira el dinero: Core
    arrancado sobre una base donde NADIE ha creado la tabla de transferencias
    (el apaño de `sembrar` va desactivado a proposito) y una transferencia
    firmada tiene que guardarse. Si alguien vuelve a dejar el CREATE corto, aqui
    se cae la primera transferencia de una instalacion nueva, que es el caso
    real; en la suite de siempre no se veria porque la tabla ya existe.
    """
    print("\n== P-1 DE PUNTA A PUNTA: BASE LIMPIA, SIN EL APAÑO ==")
    sub = tmp / "p1_limpia"
    sub.mkdir(parents=True, exist_ok=True)
    priv, pub, origen = billetera_v1()
    _, _, destino = billetera_v1()
    env = entorno(sub, pqc=False)          # esto es el camino v1: sin poscuantico
    salida = sembrar(sub, env, [(origen, 10.0)], crear_tabla=False)
    c.apunta("N3", "base limpia: nadie ha creado la tabla de transferencias",
             "tabla_transferencias_existe False" in salida,
             "la tabla ya existia: no seria una base limpia")

    proc, base, log = arrancar(sub, env, "core_p1.log")
    db = Path(env["KUKIA_DB"])
    try:
        nonce = "p1-e2e-" + secrets.token_hex(4)
        monto = 5.0
        supply_antes = pedir(base, "/stats")[1].get("supply_emitido")
        firma = priv.sign(f"transferir:{origen}:{destino}:{monto}:{nonce}".encode()).hex()
        codigo, r = pedir(base, "/transferir", {
            "de_nodo": origen, "a_direccion": destino, "monto": monto,
            "nonce": nonce, "firma": firma, "clave_publica": pub})
        c.apunta("N4", "la PRIMERA transferencia v1 de una base limpia se guarda (200)",
                 codigo == 200, f"{codigo} {r}")

        cols = _columnas(db, "transferencias")
        fila = _fila_nonce(db, nonce)
        c.apunta("N5", "la tabla la crea el Core con 8 columnas y la fila guarda firma y clave",
                 len(cols) == 8 and "firma" in cols and "clave_publica" in cols
                 and bool(fila) and fila["firma"] == firma and fila["clave_publica"] == pub,
                 f"columnas={cols} fila={dict(fila) if fila else None}")

        saldo = pedir(base, f"/saldo/{destino}")[1].get("saldo")
        supply_despues = pedir(base, "/stats")[1].get("supply_emitido")
        c.apunta("N6", "el destino cobra los 5 y el suministro no se mueve",
                 abs(float(saldo or 0) - monto) < 1e-9
                 and supply_antes == supply_despues,
                 f"saldo={saldo} supply {supply_antes} -> {supply_despues}")
    finally:
        parar(proc, log)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--borrar", action="store_true", help="borra la carpeta temporal")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="kukia_pqc_faseC_"))
    print(f"carpeta temporal: {tmp}")
    print(f"repo: {RAIZ}  (DB del Core: {tmp / 'kukia.db'})")
    c = Casos()
    try:
        prueba_hibrida(tmp, c)
        prueba_p1_punta_a_punta(tmp, c)
        prueba_apagado(tmp, c)
    finally:
        print("\n== RESULTADO ==")
        ancho = max(len(d) for _, d, _, _ in c.filas)
        for ident, desc, ok, detalle in c.filas:
            print(f"  {ident:<4} {'OK   ' if ok else 'FALLO'}  {desc:<{ancho}}")
        print(f"\n  {len(c.filas) - len(c.fallos)}/{len(c.filas)} correctos")
        if c.fallos:
            print("  FALLAN: " + ", ".join(f[0] for f in c.fallos))
        for ruta in ("core_pqc.log", "core_sin_pqc.log"):
            p = tmp / ruta
            if p.exists() and p.stat().st_size:
                lineas = p.read_text(encoding="utf-8", errors="replace").strip().splitlines()
                errores = [l for l in lineas if "Traceback" in l or "ERROR" in l]
                if errores:
                    print(f"  {ruta}: {len(errores)} lineas con error")
        print(f"\n  logs en {tmp}")

    codigo = 0 if not c.fallos else 1
    if args.borrar and codigo == 0:
        shutil.rmtree(tmp, ignore_errors=True)
        print("  carpeta temporal borrada")
    return codigo


if __name__ == "__main__":
    raise SystemExit(main())
