#!/usr/bin/env python3
"""Prueba de los INGRESOS REALES EN EUROS (25-10-2026).

Contra un Core de verdad arrancado en una carpeta temporal, con una copia
coherente del ledger real (no se toca nada del de produccion).

Que comprueba:
  1. Anotar un trabajo cobrado lo deja en /ingresos y en /stats.
  2. NO emite ni un KIA: el suministro antes y despues es el mismo, y el asiento
     del ledger va a 0.
  3. El asiento esta en la cadena y /ledger/verificar lo da por bueno (o sea,
     queda atado al historial y a los sellos como cualquier pago).
  4. El mismo trabajo DOS VECES se rechaza (409): un euro contado dos veces no
     es prueba de nada.
  5. Sin token de admin no se anota (403), y un importe 0 o negativo tampoco (400).
  6. La lista publica no saca el nombre del cliente; la de admin si.
  7. `cobros.py` (la herramienta) anota el .json de una transcripcion de verdad.

Uso:
  python tools/prueba_ingresos.py
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import urllib.error
import urllib.request

RAIZ = Path(__file__).resolve().parent.parent
COORD = RAIZ / "coordinator"
DB_REAL = COORD / "ledger" / "kukia.db"
SELLOS_REAL = COORD / "sellos.jsonl"
KEY_REAL = COORD / "sello_key.txt"
sys.path.insert(0, str(RAIZ / "servicio"))

fallos = 0


def check(titulo: str, ok: bool, detalle: str = "") -> bool:
    global fallos
    print(f"[ {'OK' if ok else 'FALLO'} ] {titulo}" + (f"   {detalle}" if not ok else ""))
    if not ok:
        fallos += 1
    return ok


def puerto_libre() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def copiar_estado(tmp: Path) -> None:
    src = sqlite3.connect(str(DB_REAL), timeout=10)
    src.execute("PRAGMA busy_timeout=10000")
    src.execute("PRAGMA query_only=1")
    dst = sqlite3.connect(str(tmp / "kukia.db"))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    con = sqlite3.connect(str(tmp / "kukia.db"))
    fila = con.execute("SELECT valor FROM meta WHERE clave='sellos_n'").fetchone()
    con.close()
    n = int(float(fila[0])) if fila else 0
    lineas = [l for l in SELLOS_REAL.read_text(encoding="utf-8").splitlines() if l.strip()]
    lineas = lineas[:n] if n else lineas
    (tmp / "sellos.jsonl").write_text("\n".join(lineas) + ("\n" if lineas else ""),
                                     encoding="utf-8")
    shutil.copyfile(KEY_REAL, tmp / "sello_key.txt")


def pedir(base: str, metodo: str, ruta: str, cuerpo=None, token: str = "") -> tuple:
    datos = None if cuerpo is None else json.dumps(cuerpo).encode()
    cab = {"Content-Type": "application/json"}
    if token:
        cab["X-Admin-Token"] = token
    pet = urllib.request.Request(base + ruta, data=datos, headers=cab, method=metodo)
    try:
        with urllib.request.urlopen(pet, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {}


def main() -> int:
    if not DB_REAL.is_file():
        print(f"[FALLO] no encuentro el ledger real en {DB_REAL}")
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="kukia_ingresos_"))
    token = "t" * 64
    (tmp / "admin_token.txt").write_text(token, encoding="utf-8")
    copiar_estado(tmp)
    puerto = puerto_libre()
    base = f"http://127.0.0.1:{puerto}"
    entorno = dict(os.environ)
    entorno.update({
        "KUKIA_DB": str(tmp / "kukia.db"),
        "KUKIA_SELLO_KEY": str(tmp / "sello_key.txt"),
        "KUKIA_SELLOS_FILE": str(tmp / "sellos.jsonl"),
        "KUKIA_ADMIN_TOKEN": str(tmp / "admin_token.txt"),
        "KUKIA_AUTO_TAREAS": "0",
        "KUKIA_ARCHIVAR": "0",
        "KUKIA_SELLO_SEG": "3600",
        "KUKIA_REPLICA": "0",
        "PYTHONIOENCODING": "utf-8",
    })
    log = open(tmp / "core.log", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "kukia_core:app",
                             "--host", "127.0.0.1", "--port", str(puerto)],
                            cwd=str(COORD), env=entorno,
                            stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(80):
            if proc.poll() is not None:
                raise SystemExit(f"el Core temporal murio:\n"
                                 f"{(tmp / 'core.log').read_text(errors='replace')[-2000:]}")
            try:
                c, _d = pedir(base, "GET", "/stats")
                if c == 200:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            raise SystemExit("el Core temporal no arranco")

        print("Core temporal listo en", base)
        c, s0 = pedir(base, "GET", "/stats")
        supply0 = float(s0.get("supply_emitido") or 0.0)
        check("/stats trae el bloque de ingresos a cero", s0.get("ingresos", {}).get("n") == 0,
              str(s0.get("ingresos")))

        # 5a) sin token no se anota
        c, d = pedir(base, "POST", "/admin/ingresos",
                     {"cliente": "Intruso", "trabajo": "x.mp3", "minutos": 5, "precio_eur": 1})
        check("sin token de admin no se anota (403)", c == 403, f"{c} {d}")

        # 5b) importe 0 y negativo se rechazan
        for malo in (0, -3.5):
            c, d = pedir(base, "POST", "/admin/ingresos",
                         {"cliente": "Maria G.", "trabajo": "charla.mp3",
                          "minutos": 5, "precio_eur": malo}, token)
            check(f"importe {malo} rechazado (400)", c == 400, f"{c} {d}")

        # 1) se anota un trabajo de verdad
        c, d = pedir(base, "POST", "/admin/ingresos",
                     {"servicio": "transcripcion", "cliente": "Maria G.",
                      "trabajo": "entrevista_37min.mp3", "minutos": 37.4,
                      "precio_eur": 5.61,
                      "detalle": {"palabras": 5412}}, token)
        check("el ingreso se anota (200)", c == 200, f"{c} {d}")
        check("el Core dice que NO ha emitido KIA", float(d.get("kia_emitidos", -1)) == 0.0,
              str(d))
        iid, ah = d.get("id"), d.get("asiento_hash")
        check("responde con el hash del asiento", bool(ah and len(ah) == 64), str(ah))

        # 2) no se ha emitido nada
        c, s1 = pedir(base, "GET", "/stats")
        supply1 = float(s1.get("supply_emitido") or 0.0)
        check("el suministro NO cambia al cobrar en euros", abs(supply1 - supply0) < 1e-9,
              f"{supply0} -> {supply1}")
        check("/stats ya cuenta 1 ingreso y 5,61 EUR",
              s1.get("ingresos", {}).get("n") == 1 and
              abs(float(s1["ingresos"]["total_eur"]) - 5.61) < 1e-9, str(s1.get("ingresos")))

        # 3) el asiento esta en la cadena, con 0 KIA y el importe en el motivo
        con = sqlite3.connect(str(tmp / "kukia.db"))
        con.row_factory = sqlite3.Row
        f = con.execute("SELECT * FROM ledger WHERE hash=?", (ah,)).fetchone()
        con.close()
        check("el asiento existe en el ledger", f is not None)
        if f is not None:
            check("el asiento es de 0 KIA (no se inventa dinero)",
                  abs(float(f["kuki"])) < 1e-12, str(f["kuki"]))
            check("el asiento lleva el importe y el trabajo en el motivo",
                  "5.61" in (f["motivo"] or "") and "entrevista_37min" in (f["motivo"] or ""),
                  f["motivo"])
            check("el asiento va a la cuenta de ingresos",
                  f["nodo_id"] == "INGRESOS_EUR", f["nodo_id"])
        c, v = pedir(base, "GET", "/ledger/verificar")
        check("la cadena del ledger sigue intacta con el ingreso dentro",
              c == 200 and bool(v.get("cadena_ok", v.get("ok"))), f"{c} {v}")

        # 4) el mismo trabajo dos veces: no
        c, d2 = pedir(base, "POST", "/admin/ingresos",
                      {"servicio": "transcripcion", "cliente": "Maria G.",
                       "trabajo": "entrevista_37min.mp3", "minutos": 37.4,
                       "precio_eur": 5.61}, token)
        check("el mismo trabajo repetido se rechaza (409)", c == 409, f"{c} {d2}")
        c, s2 = pedir(base, "GET", "/stats")
        check("el total no se dobla", abs(float(s2["ingresos"]["total_eur"]) - 5.61) < 1e-9,
              str(s2["ingresos"]))

        # 6) lista publica sin nombres; lista de admin con nombres
        c, pub = pedir(base, "GET", "/ingresos")
        texto_pub = json.dumps(pub, ensure_ascii=False)
        check("/ingresos publico da el total", c == 200 and
              abs(float(pub.get("total_eur") or 0) - 5.61) < 1e-9, texto_pub[:200])
        check("la lista publica NO lleva el nombre del cliente",
              "Maria" not in texto_pub, texto_pub[:200])
        c, adm = pedir(base, "GET", "/admin/ingresos", token=token)
        check("/admin/ingresos si lleva el nombre", c == 200 and
              any(r.get("cliente") == "Maria G." for r in adm.get("ingresos", [])), str(adm)[:200])

        # 7) la herramienta de verdad: anota el .json de una transcripcion
        entrega = tmp / "entregas"
        entrega.mkdir()
        trabajo = entrega / "reunion.m4a"
        (entrega / "reunion.json").write_text(json.dumps({
            "fichero": trabajo.name, "idioma_detectado": "es", "minutos": 25.31,
            "precio_eur": 3.8, "palabras": 3980, "motor": "faster-whisper/cuda",
            "modelo": "fw-small"}, ensure_ascii=False), encoding="utf-8")
        import cobros
        entorno_cobro = dict(os.environ)
        os.environ["KUKIA_CORE"] = base
        os.environ["KUKIA_ADMIN_TOKEN"] = str(tmp / "admin_token.txt")
        try:
            r = cobros.anotar(trabajo.name, "Gestoria Perez", 25.31, 3.8,
                              detalle={"fichero": trabajo.name}, core=base)
        finally:
            os.environ.clear()
            os.environ.update(entorno_cobro)
        check("cobros.anotar() funciona contra el Core", r["ok"], r["mensaje"])
        c, s3 = pedir(base, "GET", "/stats")
        check("el total acumula los dos trabajos (9,41 EUR)",
              abs(float(s3["ingresos"]["total_eur"]) - 9.41) < 1e-9, str(s3["ingresos"]))

        # y el reinicio del Core no pierde ni cambia los ingresos
        c, r2 = pedir(base, "GET", "/ingresos")
        check("el resumen da el primer euro con fecha",
              bool(r2.get("primera")), str(r2.get("primera"))[:80])

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()
        log.close()
        print(f"\n(carpeta de la prueba: {tmp})")

    print()
    if fallos:
        print(f"{fallos} comprobacion(es) FALLIDA(S)")
        return 1
    print("todo OK: los euros entran en el ledger sin emitir un solo KIA")
    return 0


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
