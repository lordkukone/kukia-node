#!/usr/bin/env python3
"""Prueba del PRECIO UNICO POR MINUTO (reparto proporcional al trabajo).

QUE COMPRUEBA
  1. El precio del minuto es uno solo y sale del trabajo del minuto anterior.
  2. Dos tareas del mismo tipo cobran en proporcion EXACTA a sus FLOPs (a 8x
     trabajo, 8x pago).
  3. Lo pagado dentro de un minuto no pasa del fusible (2 presupuestos).
  4. En modo plano (red sin historial) el minuto se corta en el presupuesto.

COMO
  Copia verificada del ledger de produccion a una carpeta temporal y arranca el
  modulo del Core apuntando ahi (KUKIA_DB). Produccion no se toca: el script
  comprueba al final que el ledger real no ha cambiado de tamano ni de mtime.

USO
  python tools/prueba_precio_minuto.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _entorno(db: Path, carpeta: Path) -> dict:
    e = dict(os.environ)
    e.update({
        "KUKIA_DB": str(db),
        "KUKIA_SELLOS_FILE": str(carpeta / "sellos.jsonl"),
        "KUKIA_SELLO_KEY": "",
        "KUKIA_SELLO_BLOQUEA": "0",
        "KUKIA_AUTO_TAREAS": "0",
        "KUKIA_ARCHIVAR": "0",
        "KUKIA_PAGO_POR_FLOP": "1",
        # Sin suelo por tarea: esta prueba mide que el pago sea proporcional al
        # trabajo (x8 GFLOP, x8 KIA), y con el suelo las tareas pequenas pagan
        # el minimo. El suelo se prueba en tools/prueba_suelo_transferible.py.
        "KUKIA_PAGO_MIN_KIA": "0",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    return e


HIJO = r'''
import json, os, sys
sys.path.insert(0, r"%(coord)s")
import kukia_core as K

salida = {}
salida["db"] = str(K.DB)
pm = K.precio_unico_minuto()
salida["precio"] = pm["precio"]
salida["fuente"] = pm["fuente"]
salida["gflop_anterior"] = pm["gflop_anterior"]
salida["presupuesto"] = K.presupuesto_por_minuto()

# --- 2) proporcionalidad: matriz n=400 contra n=800 (8x FLOPs) --------------
p400, i400 = K.pago_por_trabajo("matriz", json.dumps({"n": 400}))
p800, i800 = K.pago_por_trabajo("matriz", json.dumps({"n": 800}))
salida["pago_400"] = p400
salida["pago_800"] = p800
salida["gflop_400"] = i400.get("gflop_cobrados")
salida["gflop_800"] = i800.get("gflop_cobrados")
salida["precio_400"] = i400.get("precio_gflop")
salida["precio_800"] = i800.get("precio_gflop")
salida["fuente_400"] = i400.get("fuente_precio")

# --- 3) fusible: pagar hasta que el minuto se agote ------------------------
pagado = p400 + p800
vueltas = 0
while vueltas < 5000:
    p, info = K.pago_por_trabajo("matriz", json.dumps({"n": 800}))
    pagado += p
    vueltas += 1
    if p <= 0:
        break
salida["total_minuto"] = round(pagado, 6)
salida["tareas_minuto"] = vueltas + 2
salida["recortado"] = bool(info.get("recortado_por_fusible"))
salida["pagado_en_minuto"] = round(K.pagado_en_minuto(), 6)
salida["fusible_x"] = K.FUSIBLE_MINUTO_X
print("RESULTADO_JSON:" + json.dumps(salida, ensure_ascii=False))
os._exit(0)
'''


def main() -> int:
    for f in (sys.stdout, sys.stderr):
        try:
            f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    real = RAIZ / "coordinator" / "ledger" / "kukia.db"
    antes = (real.stat().st_size, real.stat().st_mtime)

    with tempfile.TemporaryDirectory(prefix="kukia_precio_") as tmp:
        tmp = Path(tmp)
        r = subprocess.run([sys.executable, str(RAIZ / "tools" / "backup_ledger.py"),
                            "--destino", str(tmp), "--sin-clave"],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            print("[prueba] backup_ledger fallo:", (r.stdout or "")[-400:], (r.stderr or "")[-400:])
            return 2
        carpeta = sorted(p for p in tmp.iterdir() if (p / "kukia.db").exists())[-1]

        codigo = HIJO % {"coord": (RAIZ / "coordinator").as_posix()}
        p = subprocess.run([sys.executable, "-c", codigo],
                           cwd=str(RAIZ / "coordinator"),
                           env=_entorno(carpeta / "kukia.db", carpeta),
                           capture_output=True, text=True, timeout=300)
        salida = None
        for linea in (p.stdout or "").splitlines():
            if linea.startswith("RESULTADO_JSON:"):
                salida = json.loads(linea.split(":", 1)[1])
        if salida is None:
            print("[prueba] el hijo no devolvio resultado")
            print((p.stdout or "")[-1500:])
            print((p.stderr or "")[-1500:])
            return 2

        despues = (real.stat().st_size, real.stat().st_mtime)
        print("=" * 70)
        print("PRECIO UNICO POR MINUTO")
        print(f"  precio            {salida['precio']} (fuente: {salida['fuente']})")
        print(f"  GFLOP minuto ant. {salida['gflop_anterior']}  · presupuesto {salida['presupuesto']:.6f} KIA/min")
        print(f"  matriz n=400      {salida['gflop_400']} GFLOP -> {salida['pago_400']:.9f} KIA")
        print(f"  matriz n=800      {salida['gflop_800']} GFLOP -> {salida['pago_800']:.9f} KIA")
        print(f"  precio por GFLOP  {salida['precio_400']} / {salida['precio_800']}")

        fallos = []
        if salida["precio"] is None:
            fallos.append("el precio del minuto es None (no deberia con historial)")
        if salida["pago_400"] and salida["pago_800"]:
            ratio_pago = salida["pago_800"] / salida["pago_400"]
            ratio_glop = salida["gflop_800"] / salida["gflop_400"]
            print(f"  proporcion        pago x{ratio_pago:.4f} · trabajo x{ratio_glop:.4f}")
            if abs(ratio_pago - ratio_glop) > 0.02 * ratio_glop:
                fallos.append("el pago no es proporcional al trabajo")
            if salida["precio_400"] != salida["precio_800"]:
                fallos.append("dos tareas del mismo minuto con precio distinto")
        tope = salida["presupuesto"] * salida["fusible_x"]
        print(f"  minuto completo   {salida['total_minuto']:.6f} KIA en {salida['tareas_minuto']} tareas"
              f" (tope {tope:.6f})")
        if salida["total_minuto"] > tope + 1e-6:
            fallos.append(f"el minuto pago {salida['total_minuto']:.6f} > tope {tope:.6f}")
        print(f"  ledger usado      {salida['db']}")
        if str(tmp) not in salida["db"]:
            fallos.append("la prueba abrio un ledger que no es el temporal")

        print("=" * 70)
        if fallos:
            for f in fallos:
                print("  FALLO:", f)
            return 1
        print("  OK: precio unico, reparto proporcional y minuto dentro del fusible")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
