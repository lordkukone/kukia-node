#!/usr/bin/env python3
"""Arranca la red sobre la REPLICA y comprueba que sale la misma red.

QUE PRUEBA (28-10-2026), sin tocar el Core vivo ni el dinero:

  1. `ARRANCAR_REPLICA.cmd` levanta uvicorn sobre la replica y `/stats` responde.
  2. El suministro que sirve es el de la replica, al decimal (no el del Core).
  3. `/ledger/verificar` dice `cadena_ok` y el numero de asientos es el suyo.
  4. La clave publica con la que sirve es la del ORIGINAL, no una nueva: la
     replica no lleva `sello_key.txt`, asi que la toma de `coordinator/`.
  5. Al arrancar firma un sello nuevo, y ese sello verifica con la publica del
     original. Es la prueba de que la red sigue la misma cadena.
  6. El token de admin es el del original: `/admin/clientes` responde 200 con
     el y 403 con uno falso, y NO aparece un `admin_token.txt` dentro de la
     replica (si apareciera, el Core se habria inventado uno y el tuyo dejaria
     de servir).

La prueba corre sobre una COPIA de la replica en %TEMP% y la borra al final:
el disco D: no se toca, y al terminar se comprueba que la replica de verdad
sigue byte a byte igual. Para no chocar con el Core de produccion se usa un
puerto libre, nunca el 8300 (en una recuperacion real si seria el 8300).

Uso:
    python tools/prueba_arranque_replica.py
    python tools/prueba_arranque_replica.py --origen D:/KUKIA_REPLICA
    python tools/prueba_arranque_replica.py --conservar   # no borra la copia

Sale con 0 si todo pasa y con 1 si algo falla. Cada comprobacion se imprime
como OK o FALLO: nunca se da por buena una que no se haya ejecutado.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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
sys.path.insert(0, str(RAIZ / "tools"))
sys.path.insert(0, str(RAIZ))

from ledger import sellos as S  # noqa: E402
import arrancar_replica as A  # noqa: E402

ORIGEN_DEFECTO = Path("D:/KUKIA_REPLICA")
ESPERA_MAX = 420.0
ESPERA_SELLO = 90.0
RESULTADOS: list[tuple[str, bool, str]] = []


# --------------------------------------------------------------------------
#  Utilidades
# --------------------------------------------------------------------------
def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def comprobar(nombre: str, ok: bool, detalle: str = "") -> bool:
    RESULTADOS.append((nombre, ok, detalle))
    print(f"  {'OK   ' if ok else 'FALLO'} {nombre:34} {detalle}", flush=True)
    return ok


def puerto_libre() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    puerto = s.getsockname()[1]
    s.close()
    return puerto


def get(base: str, ruta: str, cabeceras: dict | None = None):
    """(codigo, cuerpo) de un GET. codigo 0 = no se pudo hablar con el Core."""
    pet = urllib.request.Request(base + ruta, headers=cabeceras or {})
    try:
        with urllib.request.urlopen(pet, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def core_vivo(puerto: int = 8300) -> bool:
    """¿Responde el Core de produccion? Se mide antes y despues: si estaba
    vivo al empezar, tiene que seguir vivo al terminar."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/stats", timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def falta_sellar(carpeta: Path, sellos: list) -> bool:
    """¿Hay asientos que el ultimo sello aun no cubre? Es la condicion que mira
    el Core antes de sellar al arrancar (`hasta` del ultimo sello)."""
    con = sqlite3.connect(f"file:{carpeta / 'kukia.db'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row      # estado_ledger lee las filas por nombre
    try:
        est = S.estado_ledger(con)
    finally:
        con.close()
    if not sellos:
        return True
    return int(est["hasta"]) > int(sellos[-1].get("hasta") or 0)


def suministro_y_asientos(db: Path) -> tuple[float, int]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        suma = con.execute("SELECT COALESCE(SUM(total), 0) FROM saldos_cuenta").fetchone()[0]
        n = con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
    finally:
        con.close()
    return float(suma or 0.0), int(n)


def matar(proc: subprocess.Popen) -> None:
    """Mata el proceso y sus hijos. En Windows `terminate()` deja vivos a los
    nietos, y un uvicorn huerfano se queda con el puerto."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=20)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# --------------------------------------------------------------------------
#  La prueba
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Arranca la red sobre la replica y lo comprueba")
    ap.add_argument("--origen", default=str(ORIGEN_DEFECTO),
                    help="carpeta de la replica (dentro tiene 'actual')")
    ap.add_argument("--conservar", action="store_true",
                    help="no borra la copia temporal al terminar")
    args = ap.parse_args()

    origen = Path(args.origen)
    orig_actual = origen / "actual"
    if not (orig_actual / "kukia.db").exists():
        print(f"[prueba] ERROR: no hay replica en {orig_actual}")
        print("[prueba] creala antes con: python tools\\replica_ledger.py")
        return 2

    for f in (sys.stdout, sys.stderr):
        try:
            f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    print("=" * 92)
    print(" PRUEBA DE ARRANQUE SOBRE LA REPLICA (trabaja sobre una copia en %TEMP%)")
    print(f" origen: {orig_actual}")
    print("=" * 92)

    # --- huella de la replica de verdad, para demostrar que no se toca -------
    db_orig = orig_actual / "kukia.db"
    sellos_orig = orig_actual / "sellos.jsonl"
    antes = {"db": sha256(db_orig),
             "sellos": sha256(sellos_orig) if sellos_orig.exists() else "",
             "mtime_db": db_orig.stat().st_mtime}
    sellos_copia = S.leer_sellos(sellos_orig) if sellos_orig.exists() else []
    n_sellos_antes = len(sellos_copia)
    publica_orig = A.publica_del_original()
    produccion_viva = core_vivo()
    token_file = A.COORD / "admin_token.txt"
    token_orig = token_file.read_text(encoding="utf-8").strip() if token_file.exists() else ""

    if not comprobar("la clave del original esta aqui", bool(publica_orig),
                     publica_orig[:16] + "..." if publica_orig else "no hay sello_key.txt"):
        print("\n  Sin la clave del original esto no es una recuperacion, es otra red.")
        return 4
    if token_orig:
        comprobar("token de admin del original", True, f"{len(token_orig)} caracteres")
    else:
        print("  AVISO: no hay coordinator/admin_token.txt; la prueba del token se omite.")

    # --- copia de trabajo ----------------------------------------------------
    tmp = Path(tempfile.mkdtemp(prefix="kukia_arranque_"))
    destino = tmp / "replica"
    carpeta = destino / "actual"
    puerto = puerto_libre()
    base = f"http://127.0.0.1:{puerto}"
    proc = None
    log = None
    try:
        print(f"  copiando la replica a {carpeta} ...", flush=True)
        shutil.copytree(orig_actual, carpeta)
        suma_rep, asientos_rep = suministro_y_asientos(carpeta / "kukia.db")
        necesita_sello = falta_sellar(carpeta, sellos_copia)
        print(f"  copia: {asientos_rep} asientos, suministro {suma_rep:.6f}, "
              f"{n_sellos_antes} sellos")
        print(f"  asientos sin sellar: {'SI' if necesita_sello else 'no (todo cubierto)'}")
        print(f"  arrancando en el puerto {puerto} (el 8300 es del Core de verdad) ...\n",
              flush=True)

        log = open(tmp / "arranque.log", "w", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-u", str(RAIZ / "tools" / "arrancar_replica.py"),
             "--destino", str(destino), "--puerto", str(puerto), "--aceptar-atraso"],
            cwd=str(RAIZ), stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})

        listo, t0 = False, time.time()
        while time.time() - t0 < ESPERA_MAX:
            if proc.poll() is not None:
                break
            codigo, _ = get(base, "/stats")
            if codigo == 200:
                listo = True
                break
            time.sleep(1.5)

        if not comprobar("el Core arranca sobre la replica", listo,
                         f"{time.time() - t0:.0f} s" if listo else
                         f"salio con codigo {proc.poll()} (ver {tmp / 'arranque.log'})"):
            return 5

        # 1) /stats sirve el suministro de la replica, no el del original
        codigo, cuerpo = get(base, "/stats")
        try:
            stats = json.loads(cuerpo)
        except Exception:
            stats = {}
        suma_api = float(stats.get("supply_emitido")
                         or stats.get("supply") or stats.get("suministro") or 0.0)
        comprobar("/stats responde 200", codigo == 200, f"HTTP {codigo}")
        # supply_emitido es un contador de emision y la suma de saldos redondea
        # distinto: en el Core vivo difieren ~1e-5. Se compara al miliKIA.
        comprobar("el suministro es el de la replica",
                  codigo == 200 and abs(suma_api - suma_rep) < 1e-3,
                  f"api {suma_api:.6f} vs replica {suma_rep:.6f}")

        # 2) cadena y asientos
        codigo, cuerpo = get(base, "/ledger/verificar")
        try:
            ver = json.loads(cuerpo)
        except Exception:
            ver = {}
        comprobar("/ledger/verificar dice cadena_ok", bool(ver.get("cadena_ok")),
                  f"asientos {ver.get('asientos', '?')} (replica {asientos_rep})")
        comprobar("mismos asientos que la copia",
                  int(ver.get("asientos") or 0) == asientos_rep,
                  f"{ver.get('asientos')} vs {asientos_rep}")

        # 3) la clave publica es la del original
        codigo, cuerpo = get(base, "/ledger/sellos")
        try:
            remoto = json.loads(cuerpo)
        except Exception:
            remoto = {}
        pub_api = str(remoto.get("clave_publica") or "").strip()
        comprobar("firma con la clave del ORIGINAL", pub_api == publica_orig,
                  f"{pub_api[:16]}... vs {publica_orig[:16]}...")

        # 4) sellos: la cadena termina en un sello firmado con ESA clave, y si
        #    habia asientos sin sellar, el Core sella al arrancar. Si ya estaba
        #    todo sellado no hay sello nuevo que pedirle, y decir lo contrario
        #    seria una prueba inventada.
        sellos_ahora = S.leer_sellos(carpeta / "sellos.jsonl")
        if necesita_sello:
            t1 = time.time()
            while len(sellos_ahora) <= n_sellos_antes and time.time() - t1 < ESPERA_SELLO:
                time.sleep(3)
                sellos_ahora = S.leer_sellos(carpeta / "sellos.jsonl")
        nuevo = len(sellos_ahora) > n_sellos_antes
        if necesita_sello:
            comprobar("sella los asientos que faltaban", nuevo,
                      f"{n_sellos_antes} -> {len(sellos_ahora)} sellos")
        else:
            print(f"  ----  nada que sellar: el ultimo sello ya cubre {asientos_rep} "
                  f"asientos, asi que no se espera sello nuevo")
        firma_ok = bool(sellos_ahora and S.verificar_firma(sellos_ahora[-1], publica_orig))
        comprobar("el ultimo sello verifica con esa clave", firma_ok,
                  (sellos_ahora[-1].get("hash", "")[:16] + "...") if sellos_ahora else "")

        # 5) el token de admin es el del original y no se inventa otro
        if token_orig:
            c_ok, _ = get(base, "/admin/clientes", {"X-Admin-Token": token_orig})
            c_mal, _ = get(base, "/admin/clientes", {"X-Admin-Token": "0" * 64})
            comprobar("/admin/clientes con TU token da 200", c_ok == 200, f"HTTP {c_ok}")
            comprobar("/admin/clientes con token falso da 403", c_mal == 403, f"HTTP {c_mal}")
            inventado = (carpeta / "admin_token.txt").exists()
            comprobar("no ha creado un token dentro de la replica", not inventado,
                      "aparecio admin_token.txt" if inventado else "no hay fichero")

        # 6) /ledger/verificar sigue siendo publico: la auditoria no lleva token
        c_pub, _ = get(base, "/ledger/verificar")
        comprobar("/ledger/verificar es publico (sin token)", c_pub == 200, f"HTTP {c_pub}")
    finally:
        if proc is not None:
            matar(proc)
        if log is not None:
            log.close()
        # La replica de verdad tiene que seguir igual: la prueba solo copia.
        igual = {"db": sha256(db_orig),
                 "sellos": sha256(sellos_orig) if sellos_orig.exists() else "",
                 "mtime_db": db_orig.stat().st_mtime}
        comprobar("la replica de D: sigue intacta", igual == antes,
                  "misma huella" if igual == antes else "CAMBIARON db o sellos")
        if produccion_viva:
            comprobar("el Core del 8300 sigue vivo", core_vivo(),
                      "no se paro ni se reinicio")
        else:
            print("  ----  el 8300 no respondia al empezar (no habia Core de produccion)")
        if args.conservar:
            print(f"\n  copia conservada en {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    fallos = [r for r in RESULTADOS if not r[1]]
    print("\n" + "=" * 92)
    print(f" {len(RESULTADOS) - len(fallos)}/{len(RESULTADOS)} comprobaciones OK")
    if fallos:
        print(" FALLAN: " + ", ".join(f[0] for f in fallos))
    print("=" * 92)
    return 0 if not fallos else 1


if __name__ == "__main__":
    raise SystemExit(main())
