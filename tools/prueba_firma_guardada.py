#!/usr/bin/env python3
"""Prueba de la FIRMA GUARDADA en las transferencias (23-10-2026).

Antes, el Core comprobaba la firma Ed25519 al entrar y la tiraba: para auditar
meses despues solo quedaban el nonce y la pareja de asientos. Desde hoy se
guarda la firma y la clave publica del emisor, para que cualquiera pueda
re-verificarla sin creerse a nadie.

Que comprueba, sobre una COPIA del ledger (produccion no se toca):
  1. Una transferencia firmada de verdad se acepta (200).
  2. En la base queda guardada la firma, la clave publica y el nonce entero.
  3. La firma se puede re-verificar con la clave publica (criptografia real).
  4. El asiento usa el nonce COMPLETO como id (dos transferencias distintas ya
     no pueden compartir id).
  5. Repetir el nonce se rechaza (409).
  6. La prueba no ha escrito en el ledger de produccion (se busca su nonce en
     la base real, en solo-lectura). El tamano y la fecha del fichero si
     pueden moverse: el Core de verdad esta vivo y escribe telemetria.

Uso:
    python tools/prueba_firma_guardada.py
    python tools/prueba_firma_guardada.py --billetera kuki_fundador_nueva --monto 0.01
"""
from __future__ import annotations

import argparse
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
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from copia_ledger import copiar_ledger  # noqa: E402
COORD = RAIZ / "coordinator"
DB_REAL = COORD / "ledger" / "kukia.db"
DESTINO = "KUK90BA61EDA315C341434AF93CC4D82D80D9DD21"

fallos: list[str] = []


def check(ok: bool, texto: str) -> None:
    print(("  [ OK ] " if ok else "  [FALLO] ") + texto, flush=True)
    if not ok:
        fallos.append(texto)


def puerto_libre() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def post(url: str, datos: dict, tiempo: float = 20.0):
    pet = urllib.request.Request(url, data=json.dumps(datos).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(pet, timeout=tiempo) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--billetera", default="kuki_fundador_nueva")
    ap.add_argument("--destino", default=DESTINO)
    ap.add_argument("--monto", type=float, default=0.01)
    ap.add_argument("--dejar", action="store_true")
    args = ap.parse_args()

    if not DB_REAL.exists():
        print(f"ERROR: no existe {DB_REAL}", file=sys.stderr)
        return 2

    sys.path.insert(0, str(RAIZ / "ledger"))
    import kukia_wallet as W  # noqa: E402

    b = W.cargar_billetera(args.billetera)
    de, pub, priv = b["direccion"], b["clave_publica"], b.get("clave_privada", "")
    if not priv:
        print("ERROR: no se pudo descifrar la billetera (clave atada al usuario de Windows)",
              file=sys.stderr)
        return 2
    print(f"billetera: {args.billetera} -> {de}")

    antes = (DB_REAL.stat().st_size, DB_REAL.stat().st_mtime)
    tmp = Path(tempfile.mkdtemp(prefix="kukia_firma_"))
    db = tmp / "kukia.db"
    # Copia COHERENTE con el Core escribiendo (VACUUM INTO). Con shutil.copy2
    # la base salia mal formada una de cada tres veces: medido el 25-10-2026.
    metodo = copiar_ledger(DB_REAL, db)
    print(f"copia del ledger ({metodo}): {db} ({db.stat().st_size / 1e6:.1f} MB)")

    puerto = puerto_libre()
    base = f"http://127.0.0.1:{puerto}"
    entorno = dict(os.environ)
    entorno.update({
        "KUKIA_DB": str(db),
        "KUKIA_AUTO_TAREAS": "0",
        "KUKIA_ARCHIVAR": "0",
        "KUKIA_SELLO_SEG": "3600",
        "KUKIA_PAGO_POR_FLOP": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    log = open(tmp / "core.log", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "kukia_core:app",
                             "--host", "127.0.0.1", "--port", str(puerto)],
                            cwd=str(COORD), env=entorno,
                            stdout=log, stderr=subprocess.STDOUT)
    try:
        listo = False
        t0 = time.time()
        while time.time() - t0 < 240:
            if proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(f"{base}/stats", timeout=3) as r:
                    if r.status == 200:
                        listo = True
                        break
            except Exception:
                time.sleep(1)
        if not listo:
            print("ERROR: el Core aislado no arranco; ultimas lineas:", file=sys.stderr)
            print("\n".join((tmp / "core.log").read_text(encoding="utf-8",
                                                         errors="replace").splitlines()[-25:]),
                  file=sys.stderr)
            return 3

        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        saldo = con.execute("SELECT COALESCE(SUM(kuki),0) s FROM ledger WHERE nodo_id=?",
                            (de,)).fetchone()["s"]

        print("\n== 1. transferencia firmada de verdad ==")
        nonce = uuid.uuid4().hex
        mensaje = f"transferir:{de}:{args.destino}:{args.monto}:{nonce}"
        firma = W.firmar(mensaje, priv)
        print(f"  nonce {nonce[:16]}... · saldo origen {float(saldo):,.6f} KIA")
        codigo, cuerpo = post(f"{base}/transferir", {
            "de_nodo": de, "a_direccion": args.destino, "monto": args.monto,
            "nonce": nonce, "firma": firma, "clave_publica": pub})
        check(codigo == 200, f"el Core acepta la transferencia ({codigo}) {cuerpo}")

        print("\n== 2. lo que ha quedado guardado ==")
        fila = con.execute("SELECT * FROM transferencias WHERE nonce=?", (nonce,)).fetchone()
        check(fila is not None, "la transferencia esta en la tabla")
        if fila:
            check((fila["firma"] or "") == firma, "la firma esta guardada tal cual")
            check((fila["clave_publica"] or "") == pub, "la clave publica del emisor esta guardada")

        print("\n== 3. re-verificacion con criptografia real ==")
        if fila and fila["firma"]:
            check(W.verificar_firma(f"transferir:{fila['de_nodo']}:{fila['a_direccion']}:"
                                    f"{fila['monto']}:{fila['nonce']}",
                                    fila["firma"], fila["clave_publica"]),
                  "la firma guardada verifica contra la clave publica")
            check(not W.verificar_firma(mensaje + "x", fila["firma"], fila["clave_publica"]),
                  "un mensaje manipulado NO verifica (no se puede reutilizar)")

        print("\n== 4. id del asiento con el nonce completo ==")
        ids = [r["tarea_id"] for r in con.execute(
            "SELECT tarea_id FROM ledger WHERE tarea_id LIKE ?", (f"tx-{nonce}%",))]
        check(any(i == f"tx-{nonce}" for i in ids), f"asiento con id completo ({ids[:2]})")
        filas = con.execute("SELECT nodo_id, kuki FROM ledger WHERE tarea_id=?",
                            (f"tx-{nonce}",)).fetchall()
        check(len(filas) == 2, f"dos asientos para esa transferencia ({len(filas)})")
        if len(filas) == 2:
            check(abs(sum(float(f["kuki"]) for f in filas)) < 1e-12,
                  "los dos asientos se compensan (no se crea moneda)")

        print("\n== 5. repetir el mismo nonce ==")
        codigo2, _ = post(f"{base}/transferir", {
            "de_nodo": de, "a_direccion": args.destino, "monto": args.monto,
            "nonce": nonce, "firma": firma, "clave_publica": pub})
        check(codigo2 == 409, f"se rechaza la repeticion ({codigo2} esperado 409)")

        print("\n== 6. produccion intacta ==")
        # Ojo: el Core de produccion esta vivo y escribe (telemetria, poda,
        # sellos), asi que el tamano y la fecha del fichero se mueven solos y
        # compararlos daba FALSO fallo. Lo que de verdad importa es que la
        # transferencia de ESTA prueba no haya acabado en la base real, asi que
        # se busca su nonce leyendo produccion en solo-lectura.
        despues = (DB_REAL.stat().st_size, DB_REAL.stat().st_mtime)
        nota = ""
        if antes != despues:
            nota = (f" (el fichero si cambio: {antes[0]} -> {despues[0]} bytes;"
                    " es el Core vivo, no la prueba)")
        try:
            ro = sqlite3.connect(f"file:{DB_REAL.as_posix()}?mode=ro", uri=True)
            try:
                en_transf = ro.execute("SELECT 1 FROM transferencias WHERE nonce=?",
                                       (nonce,)).fetchone()
                en_ledger = ro.execute("SELECT 1 FROM ledger WHERE tarea_id=?",
                                       (f"tx-{nonce}",)).fetchone()
            finally:
                ro.close()
            check(en_transf is None and en_ledger is None,
                  f"la transferencia de la prueba NO esta en el ledger real{nota}")
        except Exception as e:
            check(False, f"no se pudo mirar el ledger real ({type(e).__name__}: {e})")
        con.close()
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=20)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        log.close()
        if not args.dejar:
            shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fallos:
        print(f"[prueba] {len(fallos)} FALLO(S)")
        for f in fallos:
            print(f"   - {f}")
        return 1
    print("[prueba] todo OK")
    return 0


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
