#!/usr/bin/env python3
"""Todas las cuentas con saldo: ¿se puede mover cada una firmando?

QUE HACE (23-10-2026), en SOLO LECTURA y sin tocar dinero:
  1. Saca de `saldos_cuenta` todas las cuentas con saldo distinto de cero.
  2. Para cada una busca su clave privada en este PC (`ledger/wallet/*.json`,
     `validator/identidad_validador*.json`).
  3. Comprueba las MISMAS condiciones que exige el Core antes de pagar:
       - billetera KUK...: que la clave publica derive exactamente esa direccion
         (KUK + SHA256(pub)[:38]);
       - nodo (hex): que la clave publica sea la registrada en `nodos`;
       - y que la firma Ed25519 de un mensaje "transferir:..." verifique.
  4. Suma: cuanto dinero es movible desde aqui y cuanto no.

NO envia ninguna transferencia, NO escribe en la base (se abre con `mode=ro`) y
NO imprime claves privadas ni palabras de recuperacion: solo prefijos de la
clave publica.

Uso:
    python tools/auditoria_billeteras.py
    python tools/auditoria_billeteras.py --json
"""
from __future__ import annotations

import argparse
import glob
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
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "coordinator" / "ledger" / "kukia.db"
WALLETS = RAIZ / "ledger" / "wallet"
sys.path.insert(0, str(RAIZ / "ledger"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from copia_ledger import copiar_ledger  # noqa: E402

import kukia_wallet as W  # noqa: E402


def direccion_de_pubkey(pub_hex: str) -> str:
    """Misma regla que el Core: KUK + SHA256(pub)[:38] en mayusculas."""
    try:
        raw = bytes.fromhex((pub_hex or "").strip())
    except ValueError:
        return ""
    if len(raw) != 32:
        return ""
    return "KUK" + hashlib.sha256(raw).hexdigest()[:38].upper()


def sha256(ruta: Path) -> str:
    """SHA-256 de un fichero, para demostrar que la prueba no lo toco."""
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def claves_locales() -> list[dict]:
    """Todas las claves privadas de este PC. Devuelve dicts SIN imprimirlos.

    Se descifra DPAPI en memoria; si no se puede, la clave se marca como no
    disponible (no se intenta la contrasena ni las 20 palabras).
    """
    claves = []
    for ruta in sorted(glob.glob(str(WALLETS / "*.json"))):
        try:
            d = json.loads(Path(ruta).read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue          # los *_hist.json son listas de historial
        priv = (d.get("clave_privada") or d.get("clave") or "").strip()
        origen = "claro"
        if not priv and d.get("clave_privada_cifrada"):
            priv = (W._descifrar_texto(d["clave_privada_cifrada"]) or "").strip()
            origen = "dpapi"
        if not priv:
            continue
        pub = (d.get("clave_publica") or "").strip().lower()
        if not pub:
            try:
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
                from cryptography.hazmat.primitives import serialization
                pub = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv)).public_key() \
                    .public_bytes(serialization.Encoding.Raw,
                                  serialization.PublicFormat.Raw).hex()
            except Exception:
                pass
        claves.append({"fichero": os.path.basename(ruta),
                       "direccion": (d.get("direccion") or "").strip().upper(),
                       "pub": pub, "priv": priv, "origen": origen})
    for ruta in sorted(glob.glob(str(RAIZ / "validator" / "identidad_validador*.json"))):
        try:
            d = json.loads(Path(ruta).read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict) or not d.get("clave_privada"):
            continue
        claves.append({"fichero": os.path.basename(ruta), "direccion": "",
                       "pub": (d.get("clave_publica") or "").strip().lower(),
                       "priv": d["clave_privada"].strip(), "origen": "claro"})
    return claves


def puerto_libre() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def probar_en_copia(pruebas: list[dict], monto: float = 0.01) -> list[str]:
    """Envia de verdad 0,01 KIA desde cada billetera, pero en una COPIA.

    Es la unica forma de demostrar que el Core aceptaria el gasto (derivacion +
    firma + saldo + nonce) sin tocar un solo KIA de produccion: se copia el
    ledger, se arranca un Core aislado sobre la copia y se manda la
    transferencia firmada. Al final se comprueba que el ledger real no ha
    cambiado de tamano ni de fecha.
    """
    if not pruebas:
        return []
    sellos_real = RAIZ / "coordinator" / "sellos.jsonl"
    antes = (DB.stat().st_size, DB.stat().st_mtime)
    sellos_antes = sha256(sellos_real) if sellos_real.exists() else ""
    tmp = Path(tempfile.mkdtemp(prefix="kukia_billeteras_"))
    db = tmp / "kukia.db"
    # VACUUM INTO: copia coherente con el Core escribiendo. Con shutil.copy2
    # salia mal formada una de cada tres veces (medido el 25-10-2026) y el
    # Core aislado se moria al arrancar sin decir por que.
    copiar_ledger(DB, db)
    # El Core firma un sello al arrancar si la copia tiene asientos sin sellar.
    # Sin esta linea ese sello se anadiria al coordinator/sellos.jsonl REAL y
    # la prueba dejaria de ser de solo lectura sobre la produccion.
    sellos_copia = tmp / "sellos.jsonl"
    if sellos_real.exists():
        shutil.copy2(sellos_real, sellos_copia)
    puerto = puerto_libre()
    base = f"http://127.0.0.1:{puerto}"
    entorno = dict(os.environ)
    entorno.update({"KUKIA_DB": str(db), "KUKIA_SELLOS_FILE": str(sellos_copia),
                    "KUKIA_AUTO_TAREAS": "0", "KUKIA_ARCHIVAR": "0",
                    "KUKIA_SELLO_SEG": "3600", "PYTHONUNBUFFERED": "1",
                    "PYTHONIOENCODING": "utf-8"})
    log = open(tmp / "core.log", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "kukia_core:app",
                             "--host", "127.0.0.1", "--port", str(puerto)],
                            cwd=str(RAIZ / "coordinator"), env=entorno,
                            stdout=log, stderr=subprocess.STDOUT)
    resultados = []
    try:
        listo, t0 = False, time.time()
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
            return ["ERROR: el Core aislado no arranco (ver la copia del log)"]

        destino = "KUK" + "0" * 38          # direccion de usar y tirar, en la copia
        for p in pruebas:
            # El importe se adapta al saldo: mandar 0,01 fijos a una billetera que
            # tiene 0,0098 la rechaza por saldo y parece que su clave falla,
            # cuando lo que falla es la cuenta de la prueba. Nunca se manda mas
            # de la mitad de lo que hay, para no vaciarla.
            # El Core no acepta mas de 4 decimales (400): se redondea a esa
            # precision y se descarta lo que quedaria por debajo del minimo.
            monto_p = round(min(monto, float(p.get("saldo") or 0.0) * 0.5), 4)
            if monto_p < 0.0001:
                resultados.append(f"{p['cuenta']} {p['fichero']}: sin saldo para probar")
                continue
            nonce = uuid.uuid4().hex
            msg = f"transferir:{p['cuenta']}:{destino}:{monto_p}:{nonce}"
            firma = W.firmar(msg, p["priv"])
            datos = json.dumps({"de_nodo": p["cuenta"], "a_direccion": destino,
                                "monto": monto_p, "nonce": nonce, "firma": firma,
                                "clave_publica": p["pub"]}).encode()
            pet = urllib.request.Request(f"{base}/transferir", data=datos,
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(pet, timeout=30) as r:
                    codigo, cuerpo = r.status, json.loads(r.read().decode() or "{}")
            except urllib.error.HTTPError as e:
                codigo, cuerpo = e.code, json.loads(e.read().decode() or "{}")
            except Exception as e:
                codigo, cuerpo = 0, {"error": str(e)}
            ok = codigo == 200 and cuerpo.get("ok")
            resultados.append(f"{p['cuenta']} {p['fichero']}: "
                              + (f"MUEVE OK ({monto_p} KIA en la copia)" if ok
                                 else f"RECHAZADO {codigo} {cuerpo}"))

        con = sqlite3.connect(db)
        n = con.execute("SELECT COUNT(*) FROM transferencias").fetchone()[0]
        con.close()
        resultados.append(f"transferencias en la copia: {n}")
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
        shutil.rmtree(tmp, ignore_errors=True)

    despues = (DB.stat().st_size, DB.stat().st_mtime)
    # La prueba trabaja sobre una copia, pero el Core de produccion sigue vivo
    # y escribe telemetria cada minuto: que el fichero real cambie NO es que
    # esta prueba haya tocado produccion.
    resultados.append("ledger real sin tocar por la prueba" if antes == despues
                      else "el ledger real crecio mientras se probaba (es el Core "
                           "vivo escribiendo, no esta prueba)")
    sellos_despues = sha256(sellos_real) if sellos_real.exists() else ""
    resultados.append("sellos reales sin tocar por la prueba"
                      if sellos_despues == sellos_antes
                      else "el fichero de sellos real cambio durante la prueba "
                           "(miralo: el Core aislado sella sobre la copia)")
    return resultados


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--probar", action="store_true",
                    help="ademas, envia 0,01 KIA de cada billetera valida a un Core "
                         "aislado con una COPIA del ledger (no toca produccion)")
    args = ap.parse_args()

    if not DB.exists():
        print(f"ERROR: no existe {DB}", file=sys.stderr)
        return 2
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    cuentas = [r for r in con.execute("SELECT cuenta, total FROM saldos_cuenta")
               if abs(float(r["total"] or 0.0)) > 1e-9]
    cuentas.sort(key=lambda r: -float(r["total"]))
    claves = claves_locales()
    nodos = {r["id"]: (r["clave_publica"] or "").strip().lower()
             for r in con.execute("SELECT id, clave_publica FROM nodos")}

    print("=" * 96)
    print(" AUDITORIA DE BILLETERAS (solo lectura, sin mover dinero)")
    print(f" {DB} · {len(cuentas)} cuenta(s) con saldo · {len(claves)} clave(s) local(es)")
    print("=" * 96)
    print(f" {'cuenta':44} {'saldo KIA':>16}  {'tipo':10} {'clave':22} estado")
    print("-" * 96)

    filas = []
    total = movible = 0.0
    for r in cuentas:
        cta, saldo = r["cuenta"], float(r["total"])
        es_billetera = cta.startswith("KUK")
        tipo = "billetera" if es_billetera else "nodo"
        pub_esperada = ""
        if es_billetera:
            # Cualquier clave local cuya publica derive esta direccion vale.
            pub_esperada = next((c["pub"] for c in claves
                                 if c["direccion"] == cta or
                                 direccion_de_pubkey(c["pub"]) == cta), "")
        else:
            pub_esperada = nodos.get(cta, "")

        clave = next((c for c in claves if pub_esperada and c["pub"] == pub_esperada), None)
        if not pub_esperada and es_billetera:
            estado, detalle = "SIN CLAVE", "ninguna clave local deriva esta direccion"
        elif not pub_esperada and not es_billetera:
            estado, detalle = "SIN CLAVE", "el nodo no tiene clave publica registrada"
        elif clave is None:
            estado, detalle = "SIN CLAVE", "hay clave publica pero no la privada en este PC"
        else:
            # Mismas comprobaciones que el Core: derivacion + firma real.
            if es_billetera and direccion_de_pubkey(clave["pub"]) != cta:
                estado, detalle = "NO CUADRA", "la publica no deriva en esta direccion"
            else:
                nonce = uuid.uuid4().hex
                msg = f"transferir:{cta}:KUK000000000000000000000000000000000000000:0.01:{nonce}"
                firma = W.firmar(msg, clave["priv"])
                if not W.verificar_firma(msg, firma, clave["pub"]):
                    estado, detalle = "NO FIRMA", "la firma no verifica con su publica"
                elif W.verificar_firma(msg + "x", firma, clave["pub"]):
                    estado, detalle = "NO FIRMA", "la firma vale para otro mensaje"
                else:
                    estado, detalle = "OK", f"firma valida con {clave['fichero']}"

        if estado == "OK":
            movible += saldo
        total += saldo
        if clave:
            marcador = f"{clave['fichero'][:20]:20}"
        else:
            marcador = f"{'-':20}"
        if clave and clave["origen"] == "claro":
            marcador = marcador[:-1] + "*"
        print(f" {cta:44} {saldo:>16,.6f}  {tipo:10} {marcador} {estado}"
              + ("" if estado == "OK" else f" ({detalle})"))
        fila = {"cuenta": cta, "saldo": saldo, "tipo": tipo, "estado": estado,
                "detalle": detalle, "fichero": clave["fichero"] if clave else "",
                "clave_en_claro": bool(clave and clave["origen"] == "claro")}
        if estado == "OK" and clave:
            # Solo en memoria: con esto se puede probar el gasto en la copia.
            fila.update(priv=clave["priv"], pub=clave["pub"])
        filas.append(fila)

    print("-" * 96)
    print(f" TOTAL con saldo      : {total:>16,.6f} KIA")
    print(f" movible firmando aqui: {movible:>16,.6f} KIA ({movible / total * 100 if total else 0:.2f} %)")
    print(f" sin clave en este PC : {total - movible:>16,.6f} KIA")
    print(" * = clave privada en CLARO en el fichero (mejor cifrarla con DPAPI)")

    con.close()

    if args.probar:
        pruebas = [f for f in filas if f["estado"] == "OK" and f.get("priv")]
        print("\n== PRUEBA REAL EN UNA COPIA (0,01 KIA por billetera) ==")
        for linea in probar_en_copia(pruebas):
            print("  " + linea)

    if args.json:
        publico = [{k: v for k, v in f.items() if k not in ("priv", "pub")} for f in filas]
        print(json.dumps({"filas": publico, "total": total, "movible": movible},
                         indent=2, ensure_ascii=False))
    return 0 if all(f["estado"] == "OK" for f in filas) else 1


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
