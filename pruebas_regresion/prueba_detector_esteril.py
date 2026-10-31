"""Regresion: el detector de dispositivo esteril (M-02) sigue disparando y deja pilas.

Origen: F2-52 (informes_mainnet_20261029/f2-52_operacion/prueba_detector_m02.py).
Ejecuta el codigo REAL de `_hilo_salud`, `_volcar_pilas`, `_cuenta*` y
`_log_arranque` extraido con `ast` de `worker/kukia_worker.py` (no una copia a
mano, que podria divergir). No importa el modulo del worker: sin red, sin Core,
sin GPU, y no escribe en `worker/arranque.log`.

Que se demuestra:
  1. Un dispositivo con `ultimo_pedido` viejo produce una linea `ESTERIL`.
  2. Detras va el volcado `PILAS` con una pila por hilo, con sus nombres.
  3. Un dispositivo que sigue pidiendo NO produce aviso (no hay falsos positivos).

Sin este aviso, M-02 (una GPU que se queda 104 minutos sin trabajar) no se puede
diagnosticar: la prueba es la que garantiza que la instrumentacion no se pierde.

Uso:  python pruebas_regresion/prueba_detector_esteril.py
"""
import ast
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
FUENTE = RAIZ / "worker" / "kukia_worker.py"

FUNCIONES = ("_log_arranque", "_cuenta", "_cuenta_inc", "_cuenta_marca",
             "_volcar_pilas", "_hilo_salud")
RES: list[dict] = []


def ok(nombre, cond, detalle=""):
    RES.append({"prueba": nombre, "pasa": bool(cond), "detalle": detalle})
    print(f"  [{'PASS' if cond else 'FAIL'}] {nombre}" + (f"  {detalle}" if detalle else ""))
    return bool(cond)


def cargar_codigo_real(ns: dict) -> None:
    src = FUENTE.read_text(encoding="utf-8")
    arbol = ast.parse(src)
    halladas = []
    for nodo in arbol.body:
        if isinstance(nodo, ast.FunctionDef) and nodo.name in FUNCIONES:
            halladas.append(nodo.name)
            exec(compile(ast.Module(body=[nodo], type_ignores=[]), str(FUENTE), "exec"),
                 ns)
    faltan = set(FUNCIONES) - set(halladas)
    if faltan:
        raise SystemExit(f"no se encontraron en el worker: {sorted(faltan)}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="kukia_esteril_"))
    log = tmp / "arranque.log"
    try:
        ns = {
            "__name__": "detector_m02",
            "os": os, "sys": sys, "time": time, "threading": threading,
            "traceback": traceback, "datetime": datetime,
            "Path": Path,
            "ARRANQUE": log, "ARRANQUE_MAX": 2 * 1024 * 1024,
            "_ARRANQUE_LOCK": threading.Lock(),
            "CUENTAS": {}, "_CUENTAS_LOCK": threading.Lock(),
            "CAMPOS_CUENTA": ("pedidas", "recibidas", "ejecutadas", "entregadas",
                              "rechazadas"),
        }
        cargar_codigo_real(ns)

        # Dos hilos con los nombres reales del minero, uno bloqueado y otro vivo.
        parado = threading.Event()
        threading.Thread(target=parado.wait, name="pedir-cuda1", daemon=True).start()
        threading.Thread(target=parado.wait, name="calc-cuda1-0", daemon=True).start()

        # cuda:1 lleva 900 s sin pedir (esteril); cuda:0 acaba de pedir.
        ns["_cuenta_marca"]("cuda:1", "ultimo_pedido")
        ns["CUENTAS"]["cuda:1"]["ultimo_pedido"] = time.time() - 900
        ns["CUENTAS"]["cuda:1"]["pedidas"] = 77
        ns["_cuenta_marca"]("cuda:0", "ultimo_pedido")

        os.environ["KUKIA_ESTERIL_SEG"] = "60"
        os.environ["KUKIA_SALUD_SEG"] = "0.5"
        threading.Thread(target=ns["_hilo_salud"], name="salud", daemon=True).start()

        limite = time.time() + 8.0
        while time.time() < limite:
            if log.exists() and "PILAS" in log.read_text(encoding="utf-8"):
                break
            time.sleep(0.2)
        parado.set()
        time.sleep(0.3)

        if not log.exists():
            print("  [FAIL] el detector no escribio nada")
            print(json.dumps({"total": 1, "pass": 0, "fail": 1}))
            return 1

        texto = log.read_text(encoding="utf-8")
        avisos = [l for l in texto.splitlines() if "ESTERIL" in l]
        print("  --- registro del detector ---")
        for l in texto.splitlines()[:12]:
            print("   ", l[:150])

        ok("avisa del dispositivo esteril (cuda:1)",
           bool(avisos) and "cuda:1" in avisos[0], avisos[0][:90] if avisos else "sin aviso")
        ok("no avisa del que sigue pidiendo (cuda:0)",
           not any("ESTERIL cuda:0" in l for l in avisos),
           f"{len(avisos)} avisos")
        ok("deja el volcado PILAS con los nombres de hilo",
           "PILAS" in texto and "hilo pedir-cuda1" in texto
           and "hilo calc-cuda1-0" in texto,
           "pilas con nombres")
        ok("la linea del aviso lleva fecha",
           bool(avisos) and avisos[0][:4].isdigit(), avisos[0][:19] if avisos else "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    fallos = [r for r in RES if not r["pasa"]]
    print(f"\n[detector esteril] {len(RES) - len(fallos)}/{len(RES)} PASS")
    print(json.dumps({"total": len(RES), "pass": len(RES) - len(fallos),
                      "fail": len(fallos),
                      "fallos": [r["prueba"] for r in fallos]}, ensure_ascii=False))
    return 0 if not fallos else 1


if __name__ == "__main__":
    raise SystemExit(main())
