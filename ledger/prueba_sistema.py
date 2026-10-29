"""Prueba de sistema de KUKIA: ciclo completo por HTTP sobre una red AISLADA.

HISTORIA (por que este fichero cambio)
-------------------------------------
La version anterior hacia `POST /nodos/registrar` sin `clave_publica` contra
`http://127.0.0.1:8300`, es decir contra el Core OPERATIVO. Desde que el
registro exige prueba de posesion, esa peticion devuelve 400 y el script moria
con `KeyError: 'id'`. Ademas apuntaba al ledger vivo: un fallo de la prueba no
podia dejar de ser un fallo de la red.

Ahora la prueba:
  * arranca SU PROPIO Core en un puerto libre, con la base en %TEMP%,
  * se niega a correr si la base o el puerto son los del Core operativo,
  * registra el nodo con reto firmado Ed25519 y direccion de cobro derivada,
  * crea tareas como administrador (X-Admin-Token del Core aislado),
  * firma la peticion de trabajo y el resultado,
  * comprueba pago, saldo, cadena del ledger y los rechazos que importan
    (resultado falso, doble pago, firma de otra clave).

Firmas de resultado: se envia v2 (`resultado:v2:...`, ligada al nodo). Si el
arbol probado todavia solo acepta v1, la prueba reintenta con v1 y lo deja
escrito en el resultado. No se acepta una degradacion silenciosa: se informa.

La canonizacion se reimplementa aqui a proposito. Si esta prueba importase el
modulo del servidor, un "PASS" solo demostraria que el servidor coincide
consigo mismo.

Uso
---
    python ledger/prueba_sistema.py
    python ledger/prueba_sistema.py --core informes_mainnet_20261029/f2-25_integrado/combined/coordinator
    python ledger/prueba_sistema.py --conservar      # deja la base sintetica

Codigos de salida: 0 todo PASS, 1 algun FAIL, 2 abortado por seguridad.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPO = Path(__file__).resolve().parent.parent
CORE_OPERATIVO = REPO / "coordinator"
DB_OPERATIVA = CORE_OPERATIVO / "ledger" / "kukia.db"
PUERTO_OPERATIVO = 8300

RES: list[dict] = []
BASE = ""          # se fija al arrancar el Core aislado
NO_500: list[str] = []


# ------------------------------------------------------------------ utilidades
def comprobar(cid: str, nombre: str, ok: bool, esperado, obtenido, nota: str = "") -> None:
    RES.append({"id": cid, "nombre": nombre, "pass": bool(ok),
                "esperado": esperado, "obtenido": obtenido, "nota": nota})
    print(f"[{'PASS' if ok else 'FAIL'}] {cid} {nombre}\n"
          f"        esperado={esperado}\n"
          f"        obtenido={obtenido}" + (f"\n        nota={nota}" if nota else ""),
          flush=True)


def par():
    k = Ed25519PrivateKey.generate()
    pub = k.public_key().public_bytes(serialization.Encoding.Raw,
                                      serialization.PublicFormat.Raw).hex()
    return k, pub


def direccion_de(pub: str) -> str:
    """Misma regla que la billetera y el Core: KUK + SHA256(pub)[:38]."""
    return "KUK" + hashlib.sha256(pub.encode()).hexdigest()[:38].upper()


def canon_resultado_v2(nid: str, tid: str, metrica: str) -> bytes:
    h = hashlib.sha256(metrica.encode("utf-8")).hexdigest()
    return (f"resultado:v2:{len(nid.encode())}:{nid}:"
            f"{len(tid.encode())}:{tid}:{h}").encode()


def canon_resultado_v1(tid: str, metrica: str) -> bytes:
    return f"{tid}:{metrica}".encode()


def canon_pendiente(nid: str, ts: int) -> bytes:
    return f"pendiente:{nid}:{int(ts)}".encode()


def pedir(metodo: str, ruta: str, cuerpo=None, cabeceras: dict | None = None,
          tiempo: int = 120):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    cab = {"Content-Type": "application/json"}
    cab.update(cabeceras or {})
    req = urllib.request.Request(BASE + ruta, data=datos, method=metodo, headers=cab)
    try:
        with urllib.request.urlopen(req, timeout=tiempo) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        crudo = e.read()
        if e.code >= 500:
            NO_500.append(f"{metodo} {ruta.split('?')[0]} -> {e.code}")
        try:
            return e.code, json.loads(crudo or b"{}")
        except Exception:
            return e.code, {"crudo": crudo[:300].decode("utf-8", "replace")}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def post(ruta: str, cuerpo=None, cabeceras: dict | None = None):
    return pedir("POST", ruta, cuerpo, cabeceras)


def get(ruta: str):
    return pedir("GET", ruta)


def puerto_libre(preferido: int) -> int:
    for p in [preferido] + list(range(preferido + 1, preferido + 20)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise SystemExit("[prueba] no hay puerto libre")


# ------------------------------------------------------------------- arranque
def preparar_datos(datos: Path, puerto: int) -> None:
    datos.mkdir(parents=True, exist_ok=True)
    lanzador = datos / "lanzar_core.py"
    lanzador.write_text(
        "import os, sys\n"
        "core = os.environ['KUKIA_CORE_DIR']\n"
        "sys.path.insert(0, core)\n"
        "sys.path.insert(0, os.path.dirname(core))\n"
        "os.chdir(core)\n"
        "import uvicorn\n"
        f"uvicorn.run('kukia_core:app', host='127.0.0.1', port={puerto},"
        " log_level='warning')\n",
        encoding="utf-8")


def entorno(core: Path, datos: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("KUKIA_")}
    env.update({
        "KUKIA_CORE_DIR": str(core),
        "KUKIA_DB": str(datos / "kukia.db"),
        "KUKIA_SELLOS_FILE": str(datos / "sellos.jsonl"),
        "KUKIA_SELLO_KEY": str(datos / "sello_key.txt"),
        "KUKIA_ADMIN_TOKEN": str(datos / "admin_token.txt"),
        # Bucles de fondo domesticados: la prueba manda que trabajo hay.
        "KUKIA_AUTO_TAREAS": "0",
        "KUKIA_AUTO_COLA": "0",
        "KUKIA_SELLO_SEG": "86400",
        "KUKIA_ARCHIVAR_SEG": "86400",
        # Sin cupo por IP: no aporta nada aqui y cortaria las repeticiones.
        "KUKIA_REGISTRO_CUPO_HORA": "0",
        "KUKIA_REGISTRO_CUPO_DIA": "0",
        "KUKIA_REGISTRO_CUPO_CLAVE": "0",
        # Las tareas de la prueba se recalculan de verdad (nada de GPU).
        "KUKIA_VERIF_GPU": "0",
        "PYTHONIOENCODING": "utf-8",
    })
    return env


def arrancar(core: Path, datos: Path, puerto: int):
    p = subprocess.Popen([sys.executable, str(datos / "lanzar_core.py")],
                         env=entorno(core, datos), cwd=str(core),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fin = time.monotonic() + 90
    while time.monotonic() < fin:
        if p.poll() is not None:
            raise SystemExit(f"[prueba] el Core aislado murio con codigo {p.returncode}")
        try:
            est, _ = get("/version")
            if est == 200:
                return p
        except Exception:
            pass
        time.sleep(0.3)
    p.terminate()
    raise SystemExit("[prueba] el Core aislado no responde en 90 s")


def parar(p) -> None:
    if p is None:
        return
    p.terminate()
    try:
        p.wait(timeout=20)
    except Exception:
        p.kill()


# ------------------------------------------------------------- trabajo del nodo
def gen_matriz(n: int, seed: int) -> np.ndarray:
    idx = np.arange(n, dtype=np.float64)
    return (idx[:, None] * 31 + idx[None, :] * 17 + seed) % 97 - 48


def resolver(tipo: str, params: dict) -> str | None:
    if tipo == "checksum":
        return hashlib.sha256(str(params.get("data", "kukia")).encode()).hexdigest()
    if tipo == "matriz":
        n = int(params.get("n", 256))
        seed = int(params.get("seed", 42))
        a = gen_matriz(n, seed)
        b = gen_matriz(n, seed + 1)
        return str(int(round(float((a @ b).sum()))))
    return None


def pedir_trabajo(nid: str, priv) -> tuple[int, dict]:
    ts = int(time.time())
    firma = priv.sign(canon_pendiente(nid, ts)).hex()
    est, r = get(f"/tareas/pendiente?nodo_id={nid}&ts={ts}&firma={firma}"
                 f"&solo_primaria=1")
    return est, r


def entregar(nid: str, priv, tid: str, metrica: str,
             version: int | None = None) -> tuple[int, dict, int]:
    """Entrega un resultado firmado. v2 por defecto, v1 si el Core la rechaza.

    Devuelve (estado, cuerpo, version_aceptada). Si v2 se rechaza por la firma
    y v1 funciona, se informa: el arbol probado esta en modo de transicion.
    """
    intentos = [version] if version else [2, 1]
    ultimo = (0, {}, 0)
    for v in intentos:
        msg = canon_resultado_v2(nid, tid, metrica) if v == 2 \
            else canon_resultado_v1(tid, metrica)
        firma = priv.sign(msg).hex()
        est, r = post("/tareas/resultado?" + urllib.parse.urlencode(
            {"tid": tid, "nodo_id": nid, "metrica": metrica, "firma": firma}))
        ultimo = (est, r, v)
        if est == 200:
            return ultimo
        if est != 401:
            return ultimo
    return ultimo


def sql(db: Path, consulta: str, args: tuple = ()):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        return c.execute(consulta, args).fetchall()
    finally:
        c.close()


# ------------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(description="Prueba de sistema aislada de KUKIA")
    ap.add_argument("--core", default=str(CORE_OPERATIVO),
                    help="arbol del Core a probar (por defecto, el del repositorio)")
    ap.add_argument("--datos", default="", help="directorio de datos (por defecto, %TEMP%)")
    ap.add_argument("--puerto", type=int, default=8311)
    ap.add_argument("--conservar", action="store_true",
                    help="no borrar la base sintetica al terminar")
    a = ap.parse_args()

    core = Path(a.core).resolve()
    if not (core / "kukia_core.py").exists():
        print(f"[prueba] ABORTADO: {core} no contiene kukia_core.py")
        return 2

    if a.datos:
        datos = Path(a.datos).resolve()
    else:
        datos = Path(tempfile.mkdtemp(prefix="kukia_prueba_sistema_"))
    db = datos / "kukia.db"

    # --- guardas: nada de tocar la red operativa -----------------------------
    try:
        if db.resolve() == DB_OPERATIVA.resolve():
            print(f"[prueba] ABORTADO: {db} es el ledger operativo")
            return 2
    except Exception:
        pass
    partes = [p.lower() for p in db.parts]
    if "coordinator" in partes and "ledger" in partes:
        print(f"[prueba] ABORTADO: {db} cae dentro de un ledger de coordinator")
        return 2
    puerto = puerto_libre(a.puerto)
    if puerto == PUERTO_OPERATIVO:
        print("[prueba] ABORTADO: el puerto es el del Core operativo")
        return 2

    global BASE
    BASE = f"http://127.0.0.1:{puerto}"
    print(f"[prueba] core={core}\n[prueba] db={db}\n[prueba] puerto={puerto}\n", flush=True)

    preparar_datos(datos, puerto)
    priv_a, pub_a = par()
    priv_b, pub_b = par()
    dir_a = direccion_de(pub_a)
    nombre = f"prueba_sistema_{int(time.time()) % 100000}"
    proc = None
    try:
        proc = arrancar(core, datos, puerto)
        admin = (datos / "admin_token.txt").read_text(encoding="utf-8").strip()
        cab_admin = {"X-Admin-Token": admin}

        # ---------------------------------------------------------- S01
        est, ver = get("/version")
        comprobar("S01", "el Core aislado arranca y /version responde 200",
                  est == 200 and isinstance(ver, dict), "200 + JSON",
                  f"{est} {str(ver)[:90]}",
                  f"no se usa el Core operativo ({PUERTO_OPERATIVO})")

        # ---------------------------------------------------------- S02
        est_r, r02 = post("/nodos/reto", {"clave_publica": "zz"})
        comprobar("S02", "el reto rechaza una clave publica mal formada",
                  est_r == 400, "400", f"{est_r} {str(r02)[:90]}")

        # ---------------------------------------------------------- S03
        est, r03 = post("/nodos/registrar", {
            "nombre": nombre, "vram_gb": 8, "tops": 1.0,
            "clave_publica": pub_a, "direccion_cobro": dir_a})
        comprobar("S03", "registrar sin firmar el reto se rechaza (401)",
                  est == 401, "401 falta reto", f"{est} {str(r03)[:90]}")

        # ---------------------------------------------------------- S04
        est_r, reto = post("/nodos/reto", {"clave_publica": pub_a})
        firma_reto = priv_a.sign(reto["mensaje"].encode()).hex() if est_r == 200 else ""
        est, reg = post("/nodos/registrar", {
            "nombre": nombre, "vram_gb": 8, "tops": 1.0,
            "clave_publica": pub_a, "reto": reto.get("nonce", ""),
            "firma_reto": firma_reto, "direccion_cobro": dir_a, "device": "cpu"})
        nid = reg.get("id")
        comprobar("S04", "registro con reto firmado devuelve id de nodo",
                  est_r == 200 and est == 200 and bool(nid),
                  "200 + id", f"{est_r}/{est} id={nid} {str(reg)[:110]}")

        # La misma clave publicada en el nodo, y la direccion de cobro ligada.
        est_n, nodo = get(f"/nodos/{nid}")
        comprobar("S05", "el nodo queda con su clave publica y su direccion de cobro",
                  est_n == 200 and (nodo.get("clave_publica") == pub_a)
                  and (nodo.get("direccion_cobro") == dir_a),
                  "clave_publica y direccion_cobro iguales a las enviadas",
                  f"{est_n} clave={str(nodo.get('clave_publica'))[:16]}... "
                  f"dir={nodo.get('direccion_cobro')}")

        # ---------------------------------------------------------- S06
        est_r, reto2 = post("/nodos/reto", {"clave_publica": pub_a})
        est, reg2 = post("/nodos/registrar", {
            "nombre": nombre, "vram_gb": 8, "tops": 1.0,
            "clave_publica": pub_a, "reto": reto2.get("nonce", ""),
            "firma_reto": priv_a.sign(reto2["mensaje"].encode()).hex()
            if est_r == 200 else "",
            "direccion_cobro": dir_a, "device": "cpu"})
        comprobar("S06", "reintentar el registro de la misma identidad reutiliza la fila",
                  est == 200 and reg2.get("id") == nid,
                  f"200 con id={nid}", f"{est} id={reg2.get('id')}")

        # Nodo B: solo para intentar firmar como si fuera otro.
        est_rb, retob = post("/nodos/reto", {"clave_publica": pub_b})
        post("/nodos/registrar", {
            "nombre": nombre + "_b", "vram_gb": 8, "tops": 1.0,
            "clave_publica": pub_b, "reto": retob.get("nonce", ""),
            "firma_reto": priv_b.sign(retob["mensaje"].encode()).hex()
            if est_rb == 200 else "",
            "direccion_cobro": direccion_de(pub_b), "device": "cpu"})

        # ---------------------------------------------------------- S07
        est, r07 = post("/tareas/crear", {"tipo": "matriz",
                                          "params": {"n": 192, "seed": 1234}})
        comprobar("S07", "crear tarea sin token de administrador se rechaza",
                  est in (401, 403), "401/403", f"{est} {str(r07)[:90]}")

        # ---------------------------------------------------------- S08
        tareas = []
        for tipo, params in (("matriz", {"n": 192, "seed": 1234}),
                             ("matriz", {"n": 128, "seed": 99}),
                             ("checksum", {"data": "kukia-prueba-sistema"})):
            est, r = post("/tareas/crear", {"tipo": tipo, "params": params}, cab_admin)
            if est == 200:
                tareas.append((r["tarea_id"], tipo))
        comprobar("S08", "el administrador crea 3 tareas (2 matriz + 1 checksum)",
                  len(tareas) == 3, "3 tareas creadas",
                  f"{len(tareas)} creadas: {[t for t, _ in tareas]}")

        # ---------------------------------------------------------- S09
        est, r09 = get(f"/tareas/pendiente?nodo_id={nid}")
        comprobar("S09", "pedir trabajo sin firmar se rechaza",
                  est == 401, "401 falta firma", f"{est} {str(r09)[:90]}")

        # ---------------------------------------------------------- S10
        est_p, rp = pedir_trabajo(nid, priv_a)
        tarea = (rp.get("tarea") or {}) if isinstance(rp, dict) else {}
        comprobar("S10", "pedir trabajo firmado asigna una tarea propia",
                  est_p == 200 and bool(tarea.get("id")),
                  "200 con tarea ejecutando",
                  f"{est_p} tarea={tarea.get('id')} tipo={tarea.get('tipo')}")

        tid1 = tarea.get("id")
        metrica1 = resolver(tarea.get("tipo") or "", tarea.get("params") or {}) \
            if tid1 else None
        if not (tid1 and metrica1):
            comprobar("S11", "sin tarea asignada no se puede probar el pago",
                      False, "una tarea matriz/checksum",
                      f"tarea={tid1} metrica={str(metrica1)[:40]}")
            raise SystemExit(1)

        # ---------------------------------------------------------- S11
        est, r11, ver_firma = entregar(nid, priv_a, tid1, metrica1)
        comprobar("S11", "resultado correcto y firmado: verificado y pagado",
                  est == 200 and r11.get("ok") is True,
                  "200 ok=True",
                  f"{est} ok={r11.get('ok')} kuki={r11.get('kuki')} "
                  f"firma=v{ver_firma}",
                  "firma v2 (ligada al nodo)" if ver_firma == 2
                  else f"v{ver_firma}: el arbol probado aun no acepta v2")

        kuki1 = float(r11.get("kuki") or 0.0)
        filas = sql(db, "SELECT COUNT(*) c, COALESCE(SUM(kuki),0) s FROM ledger "
                        "WHERE tarea_id=?", (tid1,))
        asientos = int(filas[0]["c"])
        suma = float(filas[0]["s"])
        comprobar("S12", "el pago queda en el ledger y en el saldo de la direccion",
                  asientos >= 1 and abs(suma - kuki1) < 1e-9 and kuki1 > 0,
                  ">=1 asiento, suma = kuki devuelto, kuki > 0",
                  f"asientos={asientos} suma={suma} kuki={kuki1}")

        est_s, saldo = get(f"/saldo/{dir_a}")
        comprobar("S13", "la direccion de cobro del nodo tiene saldo",
                  est_s == 200 and float(saldo.get("saldo") or 0) > 0,
                  "saldo > 0", f"{est_s} saldo={saldo.get('saldo')}")

        # ---------------------------------------------------------- S14
        # El reenvio usa la MISMA version de firma que acepto S11: si no, el
        # Core corta en la autenticacion (401) y nunca llega a comprobar que la
        # tarea ya esta pagada.
        est, r14, _ = entregar(nid, priv_a, tid1, metrica1, version=ver_firma)
        filas2 = sql(db, "SELECT COUNT(*) c FROM ledger WHERE tarea_id=?", (tid1,))
        comprobar("S14", "reenviar el mismo resultado no paga dos veces",
                  est == 409 and int(filas2[0]["c"]) == asientos,
                  "409 y el mismo numero de asientos",
                  f"{est} asientos={int(filas2[0]['c'])} (antes {asientos})")

        # ---------------------------------------------------------- S15
        est_p2, rp2 = pedir_trabajo(nid, priv_a)
        tarea2 = (rp2.get("tarea") or {}) if isinstance(rp2, dict) else {}
        tid2 = tarea2.get("id")
        metrica2 = resolver(tarea2.get("tipo") or "", tarea2.get("params") or {}) \
            if tid2 else None
        est, r15 = (0, {})
        if tid2 and metrica2:
            est, r15 = post("/tareas/resultado?" + urllib.parse.urlencode(
                {"tid": tid2, "nodo_id": nid, "metrica": metrica2,
                 "firma": priv_b.sign(
                     canon_resultado_v2(nid, tid2, metrica2)).hex()}))
        comprobar("S15", "un resultado firmado con la clave de OTRO nodo se rechaza",
                  est == 401, "401 firma rechazada",
                  f"{est} {str(r15)[:110]}" if est else f"sin segunda tarea: {est_p2}")

        # ---------------------------------------------------------- S16
        est, r16, ver2 = entregar(nid, priv_a, tid2, metrica2) \
            if (tid2 and metrica2) else (0, {}, 0)
        comprobar("S16", "tras el rechazo, el resultado correcto si se paga",
                  est == 200 and r16.get("ok") is True,
                  "200 ok=True", f"{est} ok={r16.get('ok')} kuki={r16.get('kuki')}",
                  "el intento con clave ajena no dejo efectos")

        # ---------------------------------------------------------- S17
        est_c, rp3 = pedir_trabajo(nid, priv_a)
        tarea3 = (rp3.get("tarea") or {}) if isinstance(rp3, dict) else {}
        tid3 = tarea3.get("id")
        metrica3 = resolver(tarea3.get("tipo") or "", tarea3.get("params") or {}) \
            if tid3 else None
        est, r17 = 0, {}
        if tid3 and metrica3:
            est, r17, _ = entregar(nid, priv_a, tid3, metrica3 + "-falso")
        comprobar("S17", "un resultado inventado no se paga y no da 500",
                  est == 200 and r17.get("ok") is False
                  and float(r17.get("kuki") or 0.0) == 0.0,
                  "200 ok=False kuki=0",
                  f"{est} ok={r17.get('ok')} kuki={r17.get('kuki')}"
                  if est else f"no quedaban tareas para el resultado falso ({est_c})")

        # ---------------------------------------------------------- S18
        est, v = get("/ledger/verificar")
        comprobar("S18", "la cadena de hashes del ledger verifica",
                  est == 200 and v.get("cadena_ok") is True
                  and not v.get("ids_alterados"),
                  "cadena_ok=True sin ids alterados",
                  f"{est} cadena_ok={v.get('cadena_ok')} "
                  f"asientos={v.get('asientos')} encadenados={v.get('encadenados')}")

        # ---------------------------------------------------------- S19
        est, st = get("/stats")
        total_ledger = float(sql(db, "SELECT COALESCE(SUM(kuki),0) s FROM ledger")[0]["s"])
        supply = float(st.get("supply_emitido") or 0.0)
        comprobar("S19", "/stats cuadra con la suma del ledger",
                  est == 200 and abs(supply - total_ledger) < 1e-6,
                  f"supply_emitido = suma del ledger ({total_ledger:.9f})",
                  f"{est} supply={supply} ledger={total_ledger}")

        # ---------------------------------------------------------- S20
        comprobar("S20", "ninguna peticion devolvio 5xx",
                  not NO_500, "0 respuestas 5xx", f"{NO_500 or 'ninguna'}")

    finally:
        parar(proc)

    fallos = [r for r in RES if not r["pass"]]
    print("\n===================== RESUMEN =====================")
    print(f"PASS: {len(RES) - len(fallos)}   FAIL: {len(fallos)}   "
          f"total: {len(RES)}")
    for r in fallos:
        print(f"  FAIL {r['id']} {r['nombre']}")
    print(f"datos de la prueba: {datos}")
    try:
        (datos / "resultado.json").write_text(
            json.dumps({"core": str(core), "base": BASE, "resultados": RES},
                       indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    if not a.conservar and not fallos:
        import shutil
        shutil.rmtree(datos, ignore_errors=True)
        print("datos sinteticos borrados (--conservar para quedarselos)")
    print("===================================================")
    return 0 if not fallos else 1


if __name__ == "__main__":
    raise SystemExit(main())
