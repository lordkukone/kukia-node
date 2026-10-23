#!/usr/bin/env python3
"""Prueba de la BOLSA DE EMISION en un ARRANQUE CON LA VENTANA VACIA.

QUE MIDE
  Arranca un Core aislado con un ledger NUEVO (sin una sola tarea: no hay
  historial de precio, asi que el minuto cae a modo plano) y entrega tareas de
  verdad por HTTP, apuntando el KIA de cada minuto. Comprueba que en ningun
  minuto se pasa del presupuesto por tres caminos:

    A. Como viene de serie (pago por potencia, sin precio -> cae a plano).
    B. Con KUKIA_PAGO_POR_FLOP=0 (modo plano de siempre).
    C. El mismo Core que B pero con el pago plano VIEJO (`recompensa_pago` a
       pelo, sin pasar por la bolsa): sirve para medir la fuga que se cerro.

  Antes del arreglo del 23-10-2026, B y C pagaban 10 KIA por tarea (el techo)
  sin mirar el minuto: 25 tareas = 250 KIA en un minuto con presupuesto 1,31.

COMO
  Cada escenario usa su propia carpeta temporal con una copia del
  coordinator/kukia_core.py y un kukia.db vacio. El ledger real no se abre.

USO
  python tools/prueba_bolsa_arranque.py
  python tools/prueba_bolsa_arranque.py --tareas 25 --dejar
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

RAIZ = Path(__file__).resolve().parent.parent
CORE_ACTUAL = RAIZ / "coordinator" / "kukia_core.py"

# Parche del escenario C: el pago plano de antes del arreglo. Se cambia la
# funcion del modulo (el endpoint la busca en el modulo al llamar), asi que lo
# que se mide por HTTP es exactamente la fuga vieja.
PARCHE_VIEJO = '''
import uvicorn
import kukia_core as K

def pago_viejo(task, tier):
    """recompensa_pago a pelo: sin tope por minuto y sin anotarlo en la bolsa."""
    return K.recompensa_pago(tier)

K.pago_de_tarea = pago_viejo
uvicorn.run(K.app, host="127.0.0.1", port=PORT)
'''

fallos: list[str] = []


def check(ok: bool, texto: str) -> bool:
    print(("  [ OK ] " if ok else "  [FALLO] ") + texto, flush=True)
    if not ok:
        fallos.append(texto)
    return ok


def firmar(seed_hex: str, mensaje: str) -> str:
    k = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed_hex))
    return k.sign(mensaje.encode()).hex()


def publ_de(seed_hex: str) -> str:
    k = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed_hex))
    return k.public_key().public_bytes(serialization.Encoding.Raw,
                                       serialization.PublicFormat.Raw).hex()


def dir_de(pub_hex: str) -> str:
    return "KUK" + hashlib.sha256(bytes.fromhex(pub_hex)).hexdigest()[:38].upper()


def puerto_libre() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _esperar(url: str, proc: subprocess.Popen, seg: float = 180.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < seg:
        if proc.poll() is not None:
            return False
        try:
            if requests.get(f"{url}/stats", timeout=3).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


class Escenario:
    """Un Core aislado sobre un ledger vacio."""

    def __init__(self, nombre: str, entorno_extra: dict, viejo: bool = False):
        self.nombre = nombre
        self.entorno_extra = dict(entorno_extra)
        self.viejo = viejo
        self.tmp = Path(tempfile.mkdtemp(prefix=f"kukia_bolsa_{nombre}_"))
        self.proc: subprocess.Popen | None = None
        self.url = ""
        self.nid = ""
        self.presupuesto = 0.0
        self.supply0 = 0.0
        self.pagos: list[tuple[int, float]] = []      # (minuto, KIA)

    # -- ciclo de vida -------------------------------------------------------
    def arrancar(self) -> bool:
        shutil.copy2(CORE_ACTUAL, self.tmp / "kukia_core.py")
        (self.tmp / "admin_token.txt").write_text(secrets.token_hex(32), encoding="utf-8")
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
        entorno.update(self.entorno_extra)
        if self.viejo:
            (self.tmp / "parche.py").write_text(
                PARCHE_VIEJO.replace("PORT", str(self.puerto)), encoding="utf-8")
            orden = [sys.executable, "parche.py"]
        else:
            orden = [sys.executable, "-m", "uvicorn", "kukia_core:app",
                     "--host", "127.0.0.1", "--port", str(self.puerto)]
        self.log = open(self.tmp / "core.log", "w", encoding="utf-8")
        self.proc = subprocess.Popen(orden, cwd=str(self.tmp), env=entorno,
                                     stdout=self.log, stderr=subprocess.STDOUT)
        if not _esperar(self.url, self.proc):
            return False
        st = requests.get(f"{self.url}/stats", timeout=10).json()
        self.token = (self.tmp / "admin_token.txt").read_text(encoding="utf-8").strip()
        self.admin = {"X-Admin-Token": self.token}
        self.presupuesto = float((st.get("bolsa_emision") or {}).get("presupuesto_kia_min") or 0.0)
        self.supply0 = float(st.get("supply_emitido") or 0.0)
        self.precio_inicial = (st.get("bolsa_emision") or {}).get("precio_minuto")
        self.fuente_inicial = (st.get("bolsa_emision") or {}).get("fuente_precio")
        return True

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

    def limpiar(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- flujo del minero ----------------------------------------------------
    def registrar(self) -> bool:
        self.seed = secrets.token_hex(32)
        self.pub = publ_de(self.seed)
        self.direccion = dir_de(self.pub)
        r = requests.post(f"{self.url}/nodos/reto", timeout=20,
                          json={"clave_publica": self.pub})
        reto = r.json() if r.status_code == 200 else {}
        r = requests.post(f"{self.url}/nodos/registrar", timeout=20, json={
            "nombre": f"bolsa-{self.nombre}", "vram_gb": 12, "tops": 100,
            "clave_publica": self.pub, "direccion_cobro": self.direccion,
            "device": "cuda:0",
            "reto": reto.get("nonce", ""),
            "firma_reto": firmar(self.seed, reto.get("mensaje") or "")})
        if r.status_code != 200:
            print(f"    registrar: {r.status_code} {r.text[:200]}")
            return False
        self.nid = r.json().get("id") or ""
        return bool(self.nid)

    def entregar(self, i: int) -> float | None:
        """Crea una tarea, la pide firmada y entrega el resultado. Devuelve KIA."""
        datos = f"bolsa-{self.nombre}-{i}-{secrets.token_hex(6)}"
        r = requests.post(f"{self.url}/tareas/crear", headers=self.admin, timeout=30,
                          json={"tipo": "checksum", "params": {"data": datos},
                                "cola": "A", "prioridad": 1e9,
                                "campana": f"bolsa-{self.nombre}"})
        if r.status_code != 200:
            print(f"    crear {i}: {r.status_code} {r.text[:200]}")
            return None
        tid = r.json().get("tarea_id") or ""
        ts = int(time.time())
        pend = requests.get(f"{self.url}/tareas/pendiente", timeout=30, params={
            "nodo_id": self.nid, "colas": "A", "ts": ts, "solo_primaria": 1,
            "firma": firmar(self.seed, f"pendiente:{self.nid}:{ts}")})
        tarea = (pend.json() or {}).get("tarea") or {} if pend.status_code == 200 else {}
        if tarea.get("id") != tid:
            print(f"    pendiente {i}: {pend.status_code} "
                  f"recibida {(tarea.get('id') or 'ninguna')[:12]}")
            return None
        metrica = hashlib.sha256(datos.encode()).hexdigest()
        res = requests.post(f"{self.url}/tareas/resultado", timeout=120, params={
            "tid": tid, "nodo_id": self.nid, "metrica": metrica,
            "firma": firmar(self.seed, f"{tid}:{metrica}"),
            "device": "cuda:0", "duracion_ms": 4.0})
        if res.status_code != 200:
            print(f"    resultado {i}: {res.status_code} {res.text[:200]}")
            return None
        kuki = float((res.json() or {}).get("kuki") or 0.0)
        self.pagos.append((int(time.time() // 60), kuki))
        return kuki

    # -- medidas -------------------------------------------------------------
    def por_minuto(self) -> dict[int, float]:
        d: dict[int, float] = {}
        for m, k in self.pagos:
            d[m] = round(d.get(m, 0.0) + k, 9)
        return d

    def total(self) -> float:
        return round(sum(k for _m, k in self.pagos), 9)


def correr(nombre: str, entorno: dict, tareas: int, viejo: bool = False,
           dejar: bool = False) -> Escenario | None:
    print(f"\n== ESCENARIO {nombre} ==", flush=True)
    e = Escenario(nombre, entorno, viejo=viejo)
    try:
        if not e.arrancar():
            texto = (e.tmp / "core.log").read_text(encoding="utf-8", errors="replace")
            print("  el Core no arranco:\n" + "\n".join(texto.splitlines()[-25:]))
            fallos.append(f"{nombre}: el Core no arranco")
            return None
        if not e.registrar():
            fallos.append(f"{nombre}: no se pudo registrar el nodo")
            return None
        print(f"  presupuesto {e.presupuesto:.6f} KIA/min · precio inicial "
              f"{e.precio_inicial} (fuente: {e.fuente_inicial})", flush=True)
        if not check(e.precio_inicial is None,
                     "ventana vacia: el Core no tiene precio y cae a modo plano"):
            pass
        for i in range(tareas):
            if e.entregar(i) is None:
                break
        st = requests.get(f"{e.url}/stats", timeout=10).json()
        e.supply1 = float(st.get("supply_emitido") or 0.0)
        e.bolsa_final = st.get("bolsa_emision") or {}
        for m, k in sorted(e.por_minuto().items()):
            print(f"    minuto {m}: {k:.6f} KIA")
        print(f"  total entregado: {e.total():.6f} KIA en {len(e.pagos)} tareas",
              flush=True)
        return e
    finally:
        e.parar()
        if not dejar:
            # se conserva hasta imprimir: el llamador ya copio lo que necesita
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bolsa de emision en arranque con ventana vacia")
    ap.add_argument("--tareas", type=int, default=25,
                    help="tareas a entregar en cada escenario (por defecto 25)")
    ap.add_argument("--dejar", action="store_true", help="no borrar las carpetas temporales")
    a = ap.parse_args(argv)
    for f in (sys.stdout, sys.stderr):
        try:
            f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    escenarios = []
    for nombre, entorno, viejo in (
            ("A_potencia", {}, False),
            ("B_plano", {"KUKIA_PAGO_POR_FLOP": "0"}, False),
            ("C_plano_viejo", {"KUKIA_PAGO_POR_FLOP": "0"}, True)):
        e = correr(nombre, entorno, a.tareas, viejo=viejo, dejar=a.dejar)
        if e is not None:
            escenarios.append(e)
        if e is not None and not a.dejar:
            e.limpiar()

    if len(escenarios) < 3:
        print("\n[prueba] faltan escenarios; no se puede concluir")
        return 2

    print("\n== RESULTADO ==")
    for e in escenarios:
        peor = max(e.por_minuto().values()) if e.pagos else 0.0
        ok = peor <= e.presupuesto + 1e-9
        print(f"  {e.nombre:14} peor minuto {peor:10.6f} KIA · "
              f"presupuesto {e.presupuesto:.6f} · "
              f"{'dentro' if ok else 'PASADO'} · total {e.total():.4f} en "
              f"{len(e.pagos)} tareas", flush=True)
        if e.nombre in ("A_potencia", "B_plano"):
            check(ok, f"{e.nombre}: ningun minuto pasa del presupuesto "
                      f"({peor:.6f} <= {e.presupuesto:.6f})")
            check(e.total() > 0, f"{e.nombre}: el arranque paga algo ({e.total():.6f} KIA)")
            delta = round(e.supply1 - e.supply0, 6)
            check(abs(delta - e.total()) < 0.01,
                  f"{e.nombre}: el suministro sube lo que se pago "
                  f"({delta:.6f} vs {e.total():.6f})")
        else:
            check(not ok, f"{e.nombre}: la fuga vieja SI se pasa "
                          f"({peor:.6f} contra {e.presupuesto:.6f})")

    nuevo = escenarios[1].total()
    viejo = escenarios[2].total()
    if nuevo > 0 and viejo > 0:
        print(f"\n  la fuga pagaba x{viejo / nuevo:.1f} mas en el mismo minuto "
              f"({viejo:.4f} contra {nuevo:.4f} KIA) por las mismas "
              f"{len(escenarios[1].pagos)} tareas")
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
