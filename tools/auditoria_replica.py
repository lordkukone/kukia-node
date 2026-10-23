#!/usr/bin/env python3
"""SONDA DE AUDITORIA: se puede cobrar un resultado FALSO con dos identidades?

QUE COMPRUEBA (arreglo H1 del 23-10-2026)
  El Core acepta el resultado sin recalcular cuando otro nodo (la replica)
  entrego exactamente lo mismo y la tarea no cae en la muestra. El agujero era
  que dos identidades del mismo dueno se daban la razon entre ellas.

  Ahora:
    A. Mismo dueno (misma cuenta de cobro o misma IP de registro): NO hay
       replica. Se prueba con dos nodos desde 127.0.0.1, como en la sonda que
       descubrio el fallo.
    B. Nodos nuevos (menos de 20 tareas verificadas): aunque la replica exista,
       la tarea SE RECALCULA SIEMPRE. Se simulan dos IPs distintas para que la
       replica se asigne y se comprueba que la mentira no se cobra.
    C. CONTROL del diseno: dos nodos con historial (>= 20 tareas) y una tarea
       fuera de la muestra normal (1 de cada 10) siguen usando la replica sin
       recalcular. Es el RESIDUO conocido del muestreo: se informa, no es un
       fallo del arreglo.

USO
  python tools/auditoria_replica.py
  python tools/auditoria_replica.py --dejar
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
import uuid
from pathlib import Path

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

RAIZ = Path(__file__).resolve().parent.parent
CORE = RAIZ / "coordinator" / "kukia_core.py"
MUESTRA_NORMAL = 10          # KUKIA_AUDITORIA_1_DE por defecto
TAREAS_CONFIANZA = 20        # KUKIA_AUDITORIA_TAREAS_CONFIANZA por defecto

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


def en_muestra(tid: str, n: int) -> bool:
    """Misma regla que _toca_auditoria(): hash(tid)[:8] % n == 0."""
    h = int(hashlib.sha256(str(tid).encode()).hexdigest()[:8], 16)
    return n == 1 or h % n == 0


def puerto_libre() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Nodo:
    def __init__(self, base: str, nombre: str):
        self.base = base
        self.nombre = nombre
        self.seed = secrets.token_hex(32)
        self.pub = pub_de(self.seed)
        self.dir = "KUK" + hashlib.sha256(bytes.fromhex(self.pub)).hexdigest()[:38].upper()
        self.id = ""

    def registrar(self) -> bool:
        r = requests.post(f"{self.base}/nodos/reto", timeout=20,
                          json={"clave_publica": self.pub})
        reto = r.json() if r.status_code == 200 else {}
        r = requests.post(f"{self.base}/nodos/registrar", timeout=20, json={
            "nombre": self.nombre, "vram_gb": 12, "tops": 100, "device": "cuda:0",
            "clave_publica": self.pub, "direccion_cobro": self.dir,
            "reto": reto.get("nonce", ""),
            "firma_reto": firmar(self.seed, reto.get("mensaje") or "")})
        if r.status_code != 200:
            print(f"  registrar {self.nombre}: {r.status_code} {r.text[:200]}")
            return False
        self.id = r.json().get("id") or ""
        return bool(self.id)

    def pendiente(self, excluir: list[str] | None = None) -> dict:
        ts = int(time.time())
        q = {"nodo_id": self.id, "ts": ts,
             "firma": firmar(self.seed, f"pendiente:{self.id}:{ts}")}
        if excluir:
            q["excluir"] = ",".join(excluir)
        r = requests.get(f"{self.base}/tareas/pendiente", timeout=30, params=q)
        if r.status_code != 200:
            print(f"  pendiente {self.nombre}: {r.status_code} {r.text[:160]}")
            return {}
        return r.json() or {}

    def entregar_replica(self, tid: str, metrica: str) -> requests.Response:
        return requests.post(f"{self.base}/tareas/replica", timeout=60, json={
            "tid": tid, "nodo_id": self.id, "metrica": metrica,
            "firma": firmar(self.seed, f"{tid}:{metrica}"),
            "device": "cuda:0", "duracion_ms": 5.0})

    def entregar(self, tid: str, metrica: str) -> requests.Response:
        return requests.post(f"{self.base}/tareas/resultado", timeout=60, params={
            "tid": tid, "nodo_id": self.id, "metrica": metrica,
            "firma": firmar(self.seed, f"{tid}:{metrica}"),
            "device": "cuda:0", "duracion_ms": 5.0})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Sonda de colusion de replicas (H1)")
    ap.add_argument("--dejar", action="store_true")
    a = ap.parse_args(argv)
    for f in (sys.stdout, sys.stderr):
        try:
            f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    tmp = Path(tempfile.mkdtemp(prefix="kukia_replica_"))
    shutil.copy2(CORE, tmp / "kukia_core.py")
    (tmp / "admin_token.txt").write_text(secrets.token_hex(32), encoding="utf-8")
    puerto = puerto_libre()
    base = f"http://127.0.0.1:{puerto}"
    entorno = {k: v for k, v in os.environ.items() if not k.startswith("KUKIA_")}
    entorno.update({
        "KUKIA_DB": str(tmp / "kukia.db"),
        "KUKIA_SELLOS_FILE": str(tmp / "sellos.jsonl"),
        "KUKIA_SELLO_KEY": str(tmp / "sello_key.txt"),
        "KUKIA_ADMIN_TOKEN": str(tmp / "admin_token.txt"),
        "KUKIA_AUTO_TAREAS": "0",
        "KUKIA_ARCHIVAR": "0",
        "KUKIA_SELLO_BLOQUEA": "1",
        "PYTHONPATH": str(RAIZ),
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    log = open(tmp / "core.log", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "kukia_core:app",
                             "--host", "127.0.0.1", "--port", str(puerto)],
                            cwd=str(tmp), env=entorno, stdout=log, stderr=subprocess.STDOUT)
    admin = {"X-Admin-Token": (tmp / "admin_token.txt").read_text(encoding="utf-8").strip()}
    campanas: list[str] = []
    try:
        t0 = time.time()
        listo = False
        while time.time() - t0 < 120:
            if proc.poll() is not None:
                break
            try:
                if requests.get(f"{base}/stats", timeout=3).status_code == 200:
                    listo = True
                    break
            except Exception:
                pass
            time.sleep(0.4)
        if not listo:
            print("el Core no arranco:\n" + (tmp / "core.log").read_text(encoding="utf-8")[-1500:])
            return 2

        db = sqlite3.connect(str(tmp / "kukia.db"), timeout=15)

        def crear(campana: str, dentro: bool | None = None, mod: int = MUESTRA_NORMAL) -> str:
            """Tarea de matriz (1,024 GFLOP, replicable) con id a eleccion."""
            r = requests.post(f"{base}/tareas/crear", headers=admin, timeout=30, json={
                "tipo": "matriz", "params": {"n": 800, "seed": 3},
                "cola": "A", "prioridad": 1e9, "campana": campana})
            if r.status_code != 200:
                print(f"  crear: {r.status_code} {r.text[:200]}")
                return ""
            campanas.append(campana)
            viejo = r.json().get("tarea_id") or ""
            if dentro is None:
                return viejo
            for _ in range(2000):
                nuevo = uuid.uuid4().hex
                if en_muestra(nuevo, mod) == dentro:
                    db.execute("UPDATE tareas SET id=? WHERE id=?", (nuevo, viejo))
                    db.commit()
                    return nuevo
            return ""

        def pareja(etiqueta: str) -> tuple[Nodo, Nodo]:
            A = Nodo(base, f"{etiqueta}-A")
            B = Nodo(base, f"{etiqueta}-B")
            if not (A.registrar() and B.registrar()):
                raise SystemExit("  no se pudieron registrar los nodos de la pareja")
            return A, B

        print("== A. MISMO DUENO (misma IP): no se le da replica de su tarea ==")
        A1, B1 = pareja("mismo")
        tid1 = crear("aud-mismo")      # el id da igual: el corte es por dueno
        ta = A1.pendiente(excluir=campanas[:-1]).get("tarea") or {}
        check(ta.get("id") == tid1, "A toma la tarea como primaria")
        tb = B1.pendiente(excluir=campanas[:-1])
        rep = tb.get("tarea") or {}
        check(rep.get("id") != tid1 and not tb.get("replica"),
              f"B, misma IP, NO recibe la replica (recibio "
              f"{(rep.get('id') or 'nada')[:12]}, replica={bool(tb.get('replica'))})")
        r = A1.entregar(tid1, "0")
        cuerpo = r.json() if r.status_code == 200 else {}
        check(not cuerpo.get("ok") and float(cuerpo.get("kuki") or 0.0) == 0,
              f"la mentira sola tampoco se paga (ok={cuerpo.get('ok')}, "
              f"kuki={float(cuerpo.get('kuki') or 0.0):.6f})")

        print("\n== B. IPs DISTINTAS y nodos NUEVOS: replica si, pero se recalcula ==")
        A2, B2 = pareja("distintas")
        # Se simulan dos maquinas: cada clave con su IP de registro.
        db.execute("UPDATE registros_nodo SET ip='203.0.113.10' WHERE clave_publica=?", (A2.pub,))
        db.execute("UPDATE registros_nodo SET ip='203.0.113.20' WHERE clave_publica=?", (B2.pub,))
        db.commit()
        tid2 = crear("aud-distintas", dentro=False, mod=MUESTRA_NORMAL)
        ta2 = A2.pendiente(excluir=campanas[:-1]).get("tarea") or {}
        check(ta2.get("id") == tid2, "A2 toma la tarea como primaria")
        tb2 = B2.pendiente(excluir=campanas[:-1])
        rep2 = tb2.get("tarea") or {}
        check(bool(tb2.get("replica")) and rep2.get("id") == tid2,
              f"B2 (otra IP) SI recibe la replica (replica={bool(tb2.get('replica'))})")
        rr = B2.entregar_replica(tid2, "0")
        check(rr.status_code == 200, f"la replica falsa se guarda ({rr.status_code})")
        r2 = A2.entregar(tid2, "0")
        c2 = r2.json() if r2.status_code == 200 else {}
        check(not c2.get("ok") and float(c2.get("kuki") or 0.0) == 0,
              f"con nodos nuevos el Core RECALCULA y no paga la mentira "
              f"(ok={c2.get('ok')}, kuki={float(c2.get('kuki') or 0.0):.6f}, "
              f"verificado_por={(c2.get('pago') or {}).get('verificado_por')})")

        print("\n== C. RESIDUO CONOCIDO: dos nodos CON historial y tarea fuera de muestra ==")
        A3, B3 = pareja("maduros")
        # Tambien con IPs distintas: si compartieran IP el arreglo las separa y
        # no habria replica que probar.
        db.execute("UPDATE registros_nodo SET ip='198.51.100.30' WHERE clave_publica=?", (A3.pub,))
        db.execute("UPDATE registros_nodo SET ip='198.51.100.40' WHERE clave_publica=?", (B3.pub,))
        db.execute("UPDATE nodos SET tareas_ok=? WHERE id IN (?,?)",
                   (TAREAS_CONFIANZA + 5, A3.id, B3.id))
        db.commit()
        tid3 = crear("aud-maduros", dentro=False, mod=MUESTRA_NORMAL)
        ta3 = A3.pendiente(excluir=campanas[:-1]).get("tarea") or {}
        check(ta3.get("id") == tid3, "A3 toma la tarea como primaria")
        tb3 = B3.pendiente(excluir=campanas[:-1])
        rep3 = tb3.get("tarea") or {}
        check(bool(tb3.get("replica")) and rep3.get("id") == tid3,
              "B3 recibe la replica (los dos con historial)")
        B3.entregar_replica(tid3, "0")
        r3 = A3.entregar(tid3, "0")
        c3 = r3.json() if r3.status_code == 200 else {}
        pago3 = float(c3.get("kuki") or 0.0)
        print(f"  informativo: con historial y fuera de muestra se pago "
              f"{pago3:.6f} KIA (verificado_por={(c3.get('pago') or {}).get('verificado_por')}). "
              f"Es el muestreo 1 de cada {MUESTRA_NORMAL}: el diseno lo asume.")

        print("\n== RESULTADO ==")
        print("  mismo dueno: bloqueado · nodos nuevos: se recalcula · historial: muestreo 1/10")
        if a.dejar:
            print(f"\ncarpeta conservada: {tmp}")
        return 0 if not fallos else 1
    finally:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except Exception:
                    proc.kill()
        finally:
            log.close()
        if not a.dejar:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
