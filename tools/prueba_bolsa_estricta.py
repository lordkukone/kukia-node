#!/usr/bin/env python3
"""Prueba de la BOLSA ESTRICTA y del REPORTE DE DISPOSITIVO.

QUE COMPRUEBA (23-10-2026)
  1. Si la escritura de la bolsa falla, el pago se ABORTA: ni asiento, ni saldo,
     ni tarea cerrada. Se fuerza el fallo con un trigger en `meta` que aborta
     cualquier escritura de `bolsa_pagado`.
  2. Reiniciar el Core a mitad de minuto NO regala otro presupuesto: la bolsa
     se guarda en `meta` y se recupera al arrancar.
  3. Un nodo antiguo sin `device` (el que se registro antes de existir la
     columna) deja de ser tratado como CPU por el plan B en cuanto manda su
     latido firmado: el Core guarda el dispositivo y le vuelve a mandar
     difusiones.

COMO
  Core aislado sobre un ledger NUEVO en una carpeta temporal (el de verdad no
  se abre). Todo por HTTP: registro con reto firmado, tareas de admin,
  pendiente y resultado firmados, latido firmado.

USO
  python tools/prueba_bolsa_estricta.py
  python tools/prueba_bolsa_estricta.py --dejar
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
from pathlib import Path

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

RAIZ = Path(__file__).resolve().parent.parent
CORE = RAIZ / "coordinator" / "kukia_core.py"

fallos: list[str] = []


def check(ok: bool, texto: str) -> bool:
    print(("  [ OK ] " if ok else "  [FALLO] ") + texto, flush=True)
    if not ok:
        fallos.append(texto)
    return ok


def firmar(seed: str, mensaje: str) -> str:
    k = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed))
    return k.sign(mensaje.encode()).hex()


def pub_de(seed: str) -> str:
    k = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed))
    return k.public_key().public_bytes(serialization.Encoding.Raw,
                                       serialization.PublicFormat.Raw).hex()


def puerto_libre() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Core:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.proc: subprocess.Popen | None = None
        self.log = None
        self.url = ""

    def arrancar(self) -> bool:
        self.puerto = puerto_libre()
        self.url = f"http://127.0.0.1:{self.puerto}"
        entorno = {k: v for k, v in os.environ.items() if not k.startswith("KUKIA_")}
        entorno.update({
            "KUKIA_DB": str(self.tmp / "kukia.db"),
            "KUKIA_SELLOS_FILE": str(self.tmp / "sellos.jsonl"),
            "KUKIA_SELLO_KEY": str(self.tmp / "sello_key.txt"),
            "KUKIA_ADMIN_TOKEN": str(self.tmp / "admin_token.txt"),
            "KUKIA_AUTO_TAREAS": "0",
            "KUKIA_ARCHIVAR": "0",
            "KUKIA_SELLO_SEG": "3600",
            "KUKIA_SELLO_BLOQUEA": "1",
            "PYTHONPATH": str(RAIZ),
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
        })
        self.log = open(self.tmp / "core.log", "a", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "kukia_core:app",
             "--host", "127.0.0.1", "--port", str(self.puerto)],
            cwd=str(self.tmp), env=entorno, stdout=self.log, stderr=subprocess.STDOUT)
        t0 = time.time()
        while time.time() - t0 < 180:
            if self.proc.poll() is not None:
                return False
            try:
                if requests.get(f"{self.url}/stats", timeout=3).status_code == 200:
                    return True
            except Exception:
                pass
            time.sleep(0.4)
        return False

    def parar(self) -> None:
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=15)
                except Exception:
                    self.proc.kill()
        finally:
            try:
                self.log.close()
            except Exception:
                pass

    # -- helpers -------------------------------------------------------------
    def db(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.tmp / "kukia.db"), timeout=15)
        con.row_factory = sqlite3.Row
        return con

    def stats(self) -> dict:
        return requests.get(f"{self.url}/stats", timeout=10).json()

    def bolsa(self) -> dict:
        return self.stats().get("bolsa_emision") or {}

    def registrar(self, device: str, vram_gb: float = 0, tops: float = 0) -> str:
        r = requests.post(f"{self.url}/nodos/reto", timeout=20,
                          json={"clave_publica": self.pub})
        reto = r.json() if r.status_code == 200 else {}
        r = requests.post(f"{self.url}/nodos/registrar", timeout=20, json={
            "nombre": "bolsa-estricta", "vram_gb": vram_gb, "tops": tops,
            "clave_publica": self.pub, "direccion_cobro": self.direccion,
            "device": device,
            "reto": reto.get("nonce", ""),
            "firma_reto": firmar(self.seed, reto.get("mensaje") or "")})
        if r.status_code != 200:
            print(f"    registrar: {r.status_code} {r.text[:200]}")
            return ""
        return r.json().get("id") or ""

    def crear(self, tipo: str, params: dict) -> str:
        r = requests.post(f"{self.url}/tareas/crear", headers=self.admin, timeout=30,
                          json={"tipo": tipo, "params": params, "cola": "A",
                                "prioridad": 1e9, "campana": "bolsa-estricta"})
        if r.status_code != 200:
            print(f"    crear {tipo}: {r.status_code} {r.text[:200]}")
            return ""
        return r.json().get("tarea_id") or ""

    def pendiente(self) -> dict:
        ts = int(time.time())
        r = requests.get(f"{self.url}/tareas/pendiente", timeout=30, params={
            "nodo_id": self.nid, "colas": "A", "ts": ts, "solo_primaria": 1,
            "firma": firmar(self.seed, f"pendiente:{self.nid}:{ts}")})
        if r.status_code != 200:
            print(f"    pendiente: {r.status_code} {r.text[:160]}")
            return {}
        return (r.json() or {}).get("tarea") or {}

    def entregar_tid(self, tid: str, params: dict) -> requests.Response:
        datos = (params or {}).get("data")
        metrica = hashlib.sha256(str(datos).encode()).hexdigest()
        return requests.post(f"{self.url}/tareas/resultado", timeout=60, params={
            "tid": tid, "nodo_id": self.nid, "metrica": metrica,
            "firma": firmar(self.seed, f"{tid}:{metrica}"),
            "device": "cuda:0", "duracion_ms": 4.0})

    def entregar_checksum(self, etiqueta: str) -> tuple[requests.Response, str, float]:
        datos = f"estricta-{etiqueta}-{secrets.token_hex(6)}"
        tid = self.crear("checksum", {"data": datos})
        if not tid:
            return requests.Response(), "", 0.0
        t = self.pendiente()
        if t.get("id") != tid:
            print(f"    pendiente no devolvio la tarea {tid[:12]}")
            return requests.Response(), tid, 0.0
        r = self.entregar_tid(tid, {"data": datos})
        try:
            kuki = float((r.json() or {}).get("kuki") or 0.0)
        except Exception:
            kuki = 0.0
        return r, tid, kuki

    def telemetria(self, device: str, vram_gb: float = 0) -> int:
        datos = {"device": device, "vram_gb": vram_gb, "gpu_util_pct": 0,
                 "gpu_mem_mb": 0, "tarea_ms": 0}
        metricas = json.dumps(datos, separators=(",", ":"), ensure_ascii=False)
        ts = int(time.time())
        h = hashlib.sha256(metricas.encode()).hexdigest()
        return requests.post(f"{self.url}/nodos/telemetria", timeout=20, json={
            "nodo_id": self.nid, "device": device, "metricas": metricas, "ts": ts,
            "firma": firmar(self.seed, f"telemetria:{self.nid}:{ts}:{h}")}).status_code


TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS fallo_bolsa_ins BEFORE INSERT ON meta "
    "WHEN NEW.clave='bolsa_pagado' BEGIN SELECT RAISE(ABORT,'fallo simulado de bolsa'); END;",
    "CREATE TRIGGER IF NOT EXISTS fallo_bolsa_upd BEFORE UPDATE ON meta "
    "WHEN NEW.clave='bolsa_pagado' BEGIN SELECT RAISE(ABORT,'fallo simulado de bolsa'); END;",
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bolsa estricta y reporte de device")
    ap.add_argument("--dejar", action="store_true", help="no borrar la carpeta temporal")
    a = ap.parse_args(argv)
    for f in (sys.stdout, sys.stderr):
        try:
            f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    tmp = Path(tempfile.mkdtemp(prefix="kukia_estricta_"))
    print(f"carpeta temporal: {tmp}")
    shutil.copy2(CORE, tmp / "kukia_core.py")
    (tmp / "admin_token.txt").write_text(secrets.token_hex(32), encoding="utf-8")
    c = Core(tmp)
    c.admin = {"X-Admin-Token": (tmp / "admin_token.txt").read_text(encoding="utf-8").strip()}
    c.seed = secrets.token_hex(32)
    c.pub = pub_de(c.seed)
    c.direccion = "KUK" + hashlib.sha256(bytes.fromhex(c.pub)).hexdigest()[:38].upper()
    c.nid = ""
    try:
        if not c.arrancar():
            texto = (tmp / "core.log").read_text(encoding="utf-8", errors="replace")
            print("el Core no arranco:\n" + "\n".join(texto.splitlines()[-25:]))
            return 2

        presupuesto = float(c.bolsa().get("presupuesto_kia_min") or 0.0)
        print(f"presupuesto {presupuesto:.6f} KIA/min · precio {c.bolsa().get('precio_minuto')}\n")

        print("== 1. SI LA BOLSA NO SE PUEDE ESCRIBIR, NO SE PAGA ==")
        nid = c.registrar(device="cuda:0", vram_gb=12)
        c.nid = nid
        if not check(bool(nid), "nodo registrado"):
            return 2
        supply_antes = float(c.stats().get("supply_emitido") or 0.0)
        datos = f"estricta-sin-bolsa-{secrets.token_hex(6)}"
        tid = c.crear("checksum", {"data": datos})
        check(bool(tid), "tarea creada")
        t = c.pendiente()
        check(t.get("id") == tid, "la tarea queda en manos del nodo")
        con = c.db()
        for sql in TRIGGERS:
            con.execute(sql)
        con.commit()
        con.close()
        r = c.entregar_tid(tid, {"data": datos})
        check(r.status_code >= 500, f"con la bolsa rota el pago se aborta "
                                    f"({r.status_code} {r.text[:90]})")
        supply_tras = float(c.stats().get("supply_emitido") or 0.0)
        check(abs(supply_tras - supply_antes) < 1e-9,
              f"el suministro NO se movio ({supply_antes:.6f} -> {supply_tras:.6f})")
        con = c.db()
        if tid:
            fila = con.execute("SELECT estado FROM tareas WHERE id=?", (tid,)).fetchone()
            check(bool(fila) and fila["estado"] != "ok",
                  f"la tarea no quedo cerrada como buena (estado {(fila['estado'] if fila else '?')})")
            asientos = con.execute("SELECT COUNT(*) c FROM ledger WHERE tarea_id=?",
                                   (tid,)).fetchone()["c"]
            check(asientos == 0, f"no hay asiento en el ledger para esa tarea ({asientos})")
        con.close()
        con = c.db()
        for nombre in ("fallo_bolsa_ins", "fallo_bolsa_upd"):
            con.execute(f"DROP TRIGGER IF EXISTS {nombre}")
        con.commit()
        con.close()
        # El minero reintenta la MISMA tarea: si el rollback fue de verdad, la
        # tarea sigue 'ejecutando' y ahora si se cobra.
        r2 = c.entregar_tid(tid, {"data": datos})
        check(r2.status_code == 200, f"arreglada la bolsa, el pago pasa ({r2.status_code})")
        kuki2 = float((r2.json() or {}).get("kuki") or 0.0)
        check(kuki2 > 0, f"y paga lo que toca ({kuki2:.6f} KIA)")

        print("\n== 3. EL NODO ANTIGUO APRENDE SU DISPOSITIVO ==")
        nid_viejo = c.registrar(device="", vram_gb=0, tops=0)
        check(bool(nid_viejo), "nodo 'antiguo' registrado sin device y sin VRAM/TOPS")
        if nid_viejo:
            guardado = requests.get(f"{c.url}/nodos/{nid_viejo}", timeout=20).json()
            check((guardado.get("device") or "") == "",
                  f"el Core no inventa el dispositivo (device='{guardado.get('device')}')")
            dif = c.crear("difusion_calor", {"n": 1024, "pasos": 1907, "seed": 7,
                                             "tolerancia_rel": 1e-9})
            check(bool(dif), "tarea de difusion creada")
            # Con el otro nodo ocupado, la tarea se la tiene que quedar este.
            c.nid = nid_viejo
            antes = c.pendiente()
            check(antes.get("id") != dif,
                  "sin device, el plan B lo toma por CPU y no le da la difusion")
            st = c.telemetria("cuda:0", vram_gb=12)
            check(st == 200, f"latido firmado aceptado ({st})")
            guardado = requests.get(f"{c.url}/nodos/{nid_viejo}", timeout=20).json()
            check((guardado.get("device") or "") == "cuda:0",
                  f"el Core guarda el dispositivo del latido (device='{guardado.get('device')}')")
            despues = c.pendiente()
            check(despues.get("id") == dif,
                  "con el latido, el mismo nodo recibe la difusion")

        print("\n== 2. REINICIO A MITAD DE MINUTO: LA BOLSA NO SE REGALA ==")
        c.nid = nid
        minuto0 = int(time.time() // 60)
        r3, _t3, k3 = c.entregar_checksum("antes-reinicio")
        check(r3.status_code == 200, "pago antes del reinicio")
        bolsa = c.bolsa()
        pagado_antes = float(bolsa.get("pagado_en_minuto") or 0.0)
        print(f"    pagado en el minuto antes de reiniciar: {pagado_antes:.6f}")
        c.parar()
        if not c.arrancar():
            fallos.append("el Core no rearranco")
            return 1
        if int(time.time() // 60) != minuto0:
            print("    (el minuto cambio durante el reinicio: esta comprobacion no concluye)")
        else:
            pagado_db = float(c.bolsa().get("pagado_en_minuto") or 0.0)
            check(abs(pagado_db - pagado_antes) < 1e-9,
                  f"la bolsa sigue donde estaba tras reiniciar "
                  f"({pagado_db:.6f} vs {pagado_antes:.6f})")
            r4, _t4, k4 = c.entregar_checksum("tras-reinicio")
            check(r4.status_code == 200, "se puede seguir pagando tras el reinicio")
            pagado_fin = float(c.bolsa().get("pagado_en_minuto") or 0.0)
            check(pagado_fin <= presupuesto + 1e-9,
                  f"el minuto entero no pasa del presupuesto "
                  f"({pagado_fin:.6f} <= {presupuesto:.6f})")
            print(f"    tras reiniciar: +{k4:.6f} KIA · total del minuto {pagado_fin:.6f}")
    finally:
        c.parar()
        if a.dejar:
            print(f"\ncarpeta conservada: {tmp}")
        else:
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
    raise SystemExit(main())
