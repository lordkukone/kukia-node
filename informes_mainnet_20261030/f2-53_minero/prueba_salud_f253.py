"""Comprueba el aviso HILO_MUERTO (F2-53) con el codigo real de `_hilo_salud`.

Escenario: existe telemetria de cuda:1 en CUENTAS pero sus hilos `pedir-cuda1`
y `calc-cuda1-0` no estan. El detector debe escribirlo y volcar las pilas.

    python prueba_salud_f253.py
"""
import ast
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
FUENTE = AQUI.parents[1] / "worker" / "kukia_worker.py"
LOG = AQUI / "prueba_salud_arranque.log"

FUNCIONES = ("_log_arranque", "_cuenta", "_cuenta_inc", "_cuenta_marca",
             "_volcar_pilas", "_hilo_salud")


def main() -> int:
    if LOG.exists():
        LOG.unlink()
    src = FUENTE.read_text(encoding="utf-8")
    ns = {
        "os": os, "sys": sys, "time": time, "threading": threading,
        "traceback": traceback, "datetime": datetime, "Path": Path,
        "ARRANQUE": LOG, "ARRANQUE_MAX": 2 * 1024 * 1024,
        "_ARRANQUE_LOCK": threading.Lock(),
        "CUENTAS": {}, "_CUENTAS_LOCK": threading.Lock(),
        "CAMPOS_CUENTA": ("pedidas", "recibidas", "ejecutadas", "entregadas",
                          "rechazadas"),
    }
    for nodo in ast.parse(src).body:
        if isinstance(nodo, ast.FunctionDef) and nodo.name in FUNCIONES:
            exec(compile(ast.Module(body=[nodo], type_ignores=[]), str(FUENTE),
                         "exec"), ns)

    # cuda:1 con actividad reciente (no es el caso "esteril") pero SIN sus hilos.
    ns["_cuenta_marca"]("cuda:1", "ultimo_pedido")
    ns["CUENTAS"]["cuda:1"]["pedidas"] = 12
    os.environ["KUKIA_ESTERIL_SEG"] = "600"     # que no salte ESTERIL
    os.environ["KUKIA_SALUD_SEG"] = "0.4"
    threading.Thread(target=ns["_hilo_salud"], name="salud", daemon=True).start()

    limite = time.time() + 5.0
    while time.time() < limite:
        if LOG.exists() and "PILAS" in LOG.read_text(encoding="utf-8"):
            break
        time.sleep(0.2)
    texto = LOG.read_text(encoding="utf-8") if LOG.exists() else ""
    print(texto[:1800])

    ok_aviso = any("HILO_MUERTO cuda:1" in l and "pedir-cuda1" in l
                   for l in texto.splitlines())
    ok_pilas = "PILAS" in texto
    ok_sin_esteril = "ESTERIL" not in texto
    print(f"avisa del hilo que falta ................. {'PASS' if ok_aviso else 'FAIL'}")
    print(f"deja pilas ............................... {'PASS' if ok_pilas else 'FAIL'}")
    print(f"no confunde con dispositivo esteril ...... {'PASS' if ok_sin_esteril else 'FAIL'}")
    return 0 if all((ok_aviso, ok_pilas, ok_sin_esteril)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
