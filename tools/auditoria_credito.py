#!/usr/bin/env python3
"""SONDA DE AUDITORIA: que pasa con el dinero del minuto siguiente cuando un
minuto se pasa del presupuesto (toca el fusible) o cuando se queda corto.

NO TOCA PRODUCCION: copia el Core a una carpeta temporal, usa un `kukia.db`
nuevo y mueve un reloj falso dentro del proceso hijo (nadie espera minutos de
verdad).

QUE MIDE
  1. Minuto normal: el precio reparte el presupuesto (precio x GFLOP = presu).
  2. Minuto que paga 2 presupuestos (fusible): el siguiente minuto paga 0 a
     todo el mundo (el credito negativo se come su presupuesto).
  3. Minuto siguiente: vuelve a la normalidad, y lo que se pago en los tres
     minutos suma lo que tocaba (nada se pierde ni se crea).

USO
  python tools/auditoria_credito.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

HIJO = r'''
import json, os, sys, time

# Reloj falso: se suma un desplazamiento a time.time(). Todo el Core lee la
# hora por aqui, asi que los "minutos" se pueden recorrer sin esperar.
_REAL = time.time
_OFF = [0.0]
time.time = lambda: _REAL() + _OFF[0]

sys.path.insert(0, r"%(coord)s")
import kukia_core as K

M0 = int((_REAL() + _OFF[0]) // 60)     # minuto "ahora"
_OFF[0] = (M0 + 1) * 60.0 + 5.0 - _REAL()   # situarse dentro del minuto M0

def sembrar(n, fin_minuto):
    """Tareas verificadas que terminan en un minuto concreto."""
    texto = time.ctime(fin_minuto * 60 + 30)
    params = json.dumps({"n": 1024, "pasos": 1907, "seed": 7, "tolerancia_rel": 1e-9})
    with K._escritura():
        for _ in range(n):
            K.con.execute("INSERT INTO tareas(id,tipo,params,estado,creado,fin) "
                          "VALUES(?,?,?,'ok',?,?)",
                          (os.urandom(6).hex(), "difusion_calor", params, texto, texto))

def ir_a(minuto):
    _OFF[0] = minuto * 60.0 + 5.0 - _REAL()
    K._precio_minuto["minuto"] = -1
    K._precio_minuto["datos"] = None

def precio():
    p = K.precio_unico_minuto()
    return p

salida = {"presupuesto": round(K.presupuesto_por_minuto(), 9)}

# Minuto M0-1: trabajo de referencia (40 difusiones).
sembrar(40, M0 - 1)

# --- 1) minuto normal -------------------------------------------------------
ir_a(M0)
p1 = precio()
salida["normal"] = {"precio": p1["precio"], "fuente": p1["fuente"],
                    "gflop": p1["gflop_anterior"], "objetivo": p1["objetivo_kia_min"],
                    "credito": p1["credito_kia"]}

# --- 2) minuto que se pasa (el fusible deja pagar 2 presupuestos) -----------
K._anotar_pagado(2 * salida["presupuesto"])
ir_a(M0 + 1)
p2 = precio()
salida["tras_pasarse"] = {"precio": p2["precio"], "objetivo": p2["objetivo_kia_min"],
                          "credito": p2["credito_kia"], "fuente": p2["fuente"],
                          "gflop": p2["gflop_anterior"]}
# Y lo que cobra una tarea en ese minuto:
sembrar(40, M0)                       # hubo trabajo de verdad en el minuto M0
ir_a(M0 + 1)
pago, info = K.pago_por_trabajo("difusion_calor",
                                json.dumps({"n": 1024, "pasos": 1907, "seed": 7,
                                            "tolerancia_rel": 1e-9}))
salida["tras_pasarse"]["pago_tarea"] = pago

# --- 3) minuto siguiente ---------------------------------------------------
ir_a(M0 + 2)
p3 = precio()
salida["recuperacion"] = {"precio": p3["precio"], "objetivo": p3["objetivo_kia_min"],
                          "credito": p3["credito_kia"], "fuente": p3["fuente"],
                          "gflop": p3["gflop_anterior"]}
# Detalle: que tareas cuenta el Core en cada tramo (para no confundir el
# denominador del precio con un fallo).
por_minuto = {}
for r in K.con.execute("SELECT fin FROM tareas WHERE estado='ok'").fetchall():
    try:
        t = K._epoch(r["fin"])
    except Exception:
        continue
    if t is None:
        continue
    m = int(t // 60)
    por_minuto[m] = por_minuto.get(m, 0) + 1
salida["tareas_por_minuto"] = {str(k): v for k, v in sorted(por_minuto.items())}
salida["minutos"] = {"M0": M0}

# Si los precios existieran, el gasto de cada minuto seria su objetivo.
salida["suma_objetivos"] = round((p1["objetivo_kia_min"] or 0.0)
                                 + (p2["objetivo_kia_min"] or 0.0)
                                 + (p3["objetivo_kia_min"] or 0.0), 9)

# Que paga de verdad el minuto de recuperacion: 40 difusiones (el mismo trabajo
# que el minuto normal). Si el precio es el de la ventana de 10 min, saldra mas
# de un presupuesto y lo cortara el fusible.
params_dif = json.dumps({"n": 1024, "pasos": 1907, "seed": 7, "tolerancia_rel": 1e-9})
total_rec = 0.0
for _ in range(40):
    pago_r, _info = K.pago_por_trabajo("difusion_calor", params_dif)
    total_rec += pago_r
salida["recuperacion"]["trabajo_gflop"] = round(40 * K._flops_tarea("difusion_calor", params_dif) / 1e9, 3)
salida["recuperacion"]["pagado_40_tareas"] = round(total_rec, 9)
print("SONDA:" + json.dumps(salida, ensure_ascii=False))
os._exit(0)
'''


def main() -> int:
    for f in (sys.stdout, sys.stderr):
        try:
            f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    tmp = Path(tempfile.mkdtemp(prefix="kukia_credito_"))
    (tmp / "coordinator").mkdir()
    (tmp / "ledger").mkdir()
    import shutil
    shutil.copy2(RAIZ / "coordinator" / "kukia_core.py", tmp / "coordinator")
    for f in ("sellos.py", "kukia_wallet.py", "wordlist_es.txt"):
        shutil.copy2(RAIZ / "ledger" / f, tmp / "ledger")
    (tmp / "runner.py").write_text(HIJO % {"coord": (tmp / "coordinator").as_posix()},
                                   encoding="utf-8")
    entorno = {k: v for k, v in os.environ.items() if not k.startswith("KUKIA_")}
    entorno.update({
        "KUKIA_DB": str(tmp / "coordinator" / "ledger" / "kukia.db"),
        "KUKIA_AUTO_TAREAS": "0",
        "KUKIA_ARCHIVAR": "0",
        "KUKIA_SELLO_KEY": str(tmp / "sello_key.txt"),
        "KUKIA_SELLOS_FILE": str(tmp / "sellos.jsonl"),
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    r = subprocess.run([sys.executable, "runner.py"], cwd=str(tmp), env=entorno,
                       capture_output=True, text=True, timeout=240)
    shutil.rmtree(tmp, ignore_errors=True)
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        return 2
    datos = None
    for linea in r.stdout.splitlines():
        if linea.startswith("SONDA:"):
            datos = json.loads(linea[len("SONDA:"):])
    if not datos:
        print(r.stdout[-2000:])
        return 2

    presu = datos["presupuesto"]
    n = datos["normal"]
    t = datos["tras_pasarse"]
    rec = datos["recuperacion"]
    print("=" * 70)
    print(f"presupuesto: {presu:.6f} KIA/min")
    print(f"minuto normal      precio {n['precio']} · GFLOP {n['gflop']} "
          f"· objetivo {n['objetivo']:.6f} · credito {n['credito']:.6f}")
    print(f"tras pasarse x2    precio {t['precio']} · objetivo {t['objetivo']:.6f} "
          f"· credito {t['credito']:.6f} · pago de una difusion {t['pago_tarea']}")
    print(f"minuto siguiente   precio {rec['precio']} · GFLOP {rec['gflop']} "
          f"({rec['fuente']}) · objetivo {rec['objetivo']:.6f} · credito {rec['credito']:.6f}")
    print(f"suma de objetivos de los tres minutos: {datos['suma_objetivos']:.6f} "
          f"(dos presupuestos = {2 * presu:.6f})")
    print(f"recuperacion: {rec['trabajo_gflop']} GFLOP entregados -> pagados "
          f"{rec['pagado_40_tareas']:.6f} (2 presupuestos = {2 * presu:.6f})")
    print(f"tareas por minuto: {datos['tareas_por_minuto']} · M0={datos['minutos']['M0']}")
    print("=" * 70)
    # Lo que se paga de verdad en el par (minuto que se pasa + minuto siguiente)
    # son dos presupuestos: el exceso de uno lo paga el otro con cero.
    ok = ((t["precio"] or 0.0) <= 1e-9 and t["pago_tarea"] == 0
          and abs(datos["suma_objetivos"] - 2 * presu) < 0.01
          and abs(rec["pagado_40_tareas"] - 2 * presu) < 0.01)
    print("OK: un minuto gratis y el siguiente paga el doble (el par suma dos "
          "presupuestos)" if ok else "REVISAR: el patron no es el esperado")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
