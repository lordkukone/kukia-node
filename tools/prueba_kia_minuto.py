#!/usr/bin/env python3
"""Prueba de la DEFINICION: 1 KIA = 1 minuto de GPU (25-10-2026).

Contra un Core de verdad cargado en una carpeta temporal (nunca el ledger real),
con los FLOPs de cada tarea fijados a mano para poder entregar "minutos de GPU"
sin ejecutar trabajo.

Que comprueba:

  1. La definicion es exacta: precio x (60 x 15.000 GFLOP) = 1 KIA, y
     `precio_gflop()` publica EL MISMO precio que `precio_unico_minuto()`
     (que /stats no diga dos cosas distintas).
  2. Un minuto de GPU cobra 1 KIA (redondeo hacia abajo aparte), sin recorte.
  3. Un minero a tope un mes (43.834 minutos) cobra 43.834 KIA, el fusible NO
     recorta en ningun minuto y el suministro sube exactamente lo cobrado.
  4. El fusible solo muerde cuando la red entrega mas GPU-minutos de los que
     paga la emision (`capacidad_gpu_min`): justo por debajo, cero recorte;
     justo por encima, recorta el exceso y NO mas (el minuto paga todo su
     presupuesto, no menos).
  5. El cliente cuadra con el minero: lo que se le cobra por adelantado es lo
     que cobra el minero al verificar (misma cuenta), el escrow queda a cero y
     el suministro total no cambia (el dinero del cliente ya existia).

Uso:
  python tools/prueba_kia_minuto.py                # el mes entero
  python tools/prueba_kia_minuto.py --minutos 600  # muestra rapida
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DIAS_MES = 30.44
MINUTOS_MES = int(round(DIAS_MES * 24 * 60))      # 43.834

RUNNER = r'''
import json, os, sys, time
sys.path.insert(0, os.path.join(os.getcwd(), "coordinator"))
sys.path.insert(0, os.getcwd())
import kukia_core as C

MINUTOS = int(os.environ["KUKIA_MINUTOS_SIM"])
KIA_MIN = C.KIA_POR_MINUTO_GPU
GPU_MIN = C.GPU_REF_GFLOPS * 60.0        # GFLOP de un minuto de GPU de referencia
NODO = "nodo_kia_minuto"

# FLOPs de la tarea a mano (params llega como texto JSON desde la fila).
def _flops_fijos(tipo, params):
    if isinstance(params, str):
        try:
            params = json.loads(params or "{}")
        except Exception:
            return 0.0
    return float((params or {}).get("_flops") or 0.0) * 1e9

C._flops_tarea = _flops_fijos

def minuto_limpio():
    C._minuto_actual["minuto"] = -1
    C._minuto_actual["pagado"] = 0.0
    C._minuto_actual["credito"] = 0.0
    C._minuto_actual["presupuesto"] = None
    C._precio_minuto["minuto"] = -1
    C._precio_minuto["datos"] = None

def paga(gflop, trozos=1, anotar=True):
    total = rec = 0.0
    for _ in range(trozos):
        p, info = C.pago_de_tarea(
            {"tipo": "matriz", "params": json.dumps({"_flops": gflop / trozos})}, 1.0)
        total += float(p)
        rec += float(info.get("recortado_por_fusible") or 0.0)
    if anotar:
        with C._escritura():
            C._asiento("prueba%08d" % int(time.time() * 1e6) % 10**8, NODO, total,
                       "trabajo verificado")
    return total, rec

salida = {}
pm = C.precio_unico_minuto()
salida["modo"] = pm["modo_precio"]
salida["fuente"] = pm["fuente"]
salida["precio"] = pm["precio"]
salida["precio_gflop_publicado"] = C.precio_gflop().get("precio_gflop")
salida["gpu_min_gflop"] = GPU_MIN
salida["kia_por_minuto_gpu"] = pm["kia_por_minuto_gpu"]
salida["capacidad_gpu_min"] = pm["capacidad_gpu_min"]
salida["kia_min_definido"] = KIA_MIN
salida["presupuesto"] = C.presupuesto_del_minuto()
salida["supply_0"] = C.supply_emitido()

# --- 2: un minuto de GPU, limpio ----------------------------------------
minuto_limpio()
p_uno, rec_uno = paga(GPU_MIN, anotar=False)
salida["un_minuto"] = {"pago": p_uno, "recortado": rec_uno,
                       "presupuesto": C.presupuesto_del_minuto()}

# --- 4a: justo por debajo de la capacidad (no debe recortar nada) -------
minuto_limpio()
cap = float(C.precio_unico_minuto()["capacidad_gpu_min"] or 0.0)
carga = GPU_MIN * cap * 0.999
p_cap, rec_cap = paga(carga, anotar=False)
salida["capacidad"] = {"capacidad_gpu_min": cap, "gflop": carga,
                       "pago": p_cap, "recortado": rec_cap}

# --- 4b: rafaga de 2 minutos de GPU en un minuto ------------------------
minuto_limpio()
presu = C.presupuesto_del_minuto()
uno_a, rec_a = paga(GPU_MIN, anotar=False)
uno_b, rec_b = paga(GPU_MIN, anotar=False)
salida["rafaga_2"] = {"presupuesto": presu, "pago_1": uno_a, "pago_2": uno_b,
                      "total": uno_a + uno_b, "pedido": 2 * GPU_MIN,
                      "recortado": rec_a + rec_b,
                      "exceso": 2 * KIA_MIN - (uno_a + uno_b)}

# --- 3: el mes, minuto a minuto -----------------------------------------
tiempo0 = time.time()
total = 0.0
suma_presu = 0.0
recortes = 0.0
minutos_con_recorte = 0
pagomin, pagomax = None, None
for i in range(MINUTOS):
    minuto_limpio()
    presu_min = C.presupuesto_del_minuto()
    p, rec = paga(GPU_MIN, anotar=False)
    with C._escritura():
        C._asiento("mes%06d" % i, NODO, p, "trabajo verificado")
    total += p
    suma_presu += presu_min
    recortes += rec
    if rec > 0:
        minutos_con_recorte += 1
    pagomin = p if pagomin is None else min(pagomin, p)
    pagomax = p if pagomax is None else max(pagomax, p)
salida["mes"] = {"total": total, "suma_presupuestos": suma_presu,
                 "recortes": recortes, "minutos_con_recorte": minutos_con_recorte,
                 "pago_minimo": pagomin, "pago_maximo": pagomax,
                 "segundos": round(time.time() - tiempo0, 1),
                 "supply_1": C.supply_emitido(),
                 "saldo_nodo": C.con.execute(
                     "SELECT total FROM saldos_cuenta WHERE cuenta=?",
                     (NODO,)).fetchone()["total"]}

# --- 5: cliente y minero dan la misma cuenta ---------------------------
CLIENTE = "KUK" + "A" * 38
NODO2 = "nodo_cliente_prueba"
minuto_limpio()
GL = 250.0                                # GFLOP de la tarea del cliente
coste_cliente = C._coste_tarea_cliente("matriz", {"_flops": GL})
with C._escritura():
    # Fondeo de la billetera del cliente (en la red real viene de minar o de
    # comprar; aqui solo para que tenga saldo con el que pagar).
    C._asiento("fondeo", CLIENTE, 5.0, "fondeo de prueba")
supply_fondeo = C.supply_emitido()
presu_antes = C.presupuesto_del_minuto()
minuto_limpio()
pago_minero, info_minero = C.pago_de_tarea(
    {"tipo": "matriz", "params": json.dumps({"_flops": GL})}, 1.0)
coste = C._coste_tarea_cliente("matriz", {"_flops": GL})
with C._escritura():
    C._retener_prepago("tcli", CLIENTE, coste)
    pagado, detalle = C._pagar_tarea_de_cliente("tcli", [(NODO2, coste)], CLIENTE, coste)
def saldo(cuenta):
    f = C.con.execute("SELECT total FROM saldos_cuenta WHERE cuenta=?", (cuenta,)).fetchone()
    return float(f["total"] if f else 0.0)
salida["cliente"] = {
    "gflop": GL, "precio": C.precio_unico_minuto()["precio"],
    "coste_cliente": coste, "pago_minero": pago_minero,
    "pagado_escrow": pagado, "sobrante": detalle.get("sobrante_devuelto"),
    "recortado_minero": info_minero.get("recortado_por_fusible"),
    "saldo_cliente": saldo(CLIENTE), "saldo_minero": saldo(NODO2),
    "saldo_escrow": saldo(C.CUENTA_ESCROW),
    "supply_antes": supply_fondeo, "supply_despues": C.supply_emitido(),
    "presupuesto": presu_antes,
}

print("RESULTADO:" + json.dumps(salida))
'''


def cargar(minutos: int) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="kukia_kia_minuto_"))
    try:
        (tmp / "coordinator").mkdir()
        (tmp / "ledger").mkdir()
        shutil.copy2(RAIZ / "coordinator" / "kukia_core.py", tmp / "coordinator")
        for f in ("sellos.py", "kukia_wallet.py", "wordlist_es.txt"):
            shutil.copy2(RAIZ / "ledger" / f, tmp / "ledger")
        (tmp / "runner.py").write_text(RUNNER, encoding="utf-8")
        entorno = dict(
            {k: v for k, v in os.environ.items() if not k.startswith("KUKIA_")},
            KUKIA_DB=str(tmp / "coordinator" / "ledger" / "kukia.db"),
            KUKIA_AUTO_TAREAS="0",
            # Esta prueba mide la DEFINICION (1 KIA = 1 minuto de GPU). Se fija
            # el modo para que no dependa del defecto del Core (25-10-2026: el
            # defecto paso al ancla por presupuesto).
            KUKIA_PRECIO_DEFINIDO="1",
            KUKIA_MINUTOS_SIM=str(minutos),
            PYTHONIOENCODING="utf-8",
        )
        r = subprocess.run([sys.executable, "runner.py"], cwd=str(tmp), env=entorno,
                           capture_output=True, text=True, timeout=2400)
        if r.returncode != 0:
            raise SystemExit(f"[prueba] el Core temporal fallo:\n"
                             f"{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
        for linea in r.stdout.splitlines():
            if linea.startswith("RESULTADO:"):
                return json.loads(linea[len("RESULTADO:"):])
        raise SystemExit(f"[prueba] sin resultado:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def num(n, dec=2):
    return f"{n:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def main() -> int:
    ap = argparse.ArgumentParser(description="1 KIA = 1 minuto de GPU")
    ap.add_argument("--minutos", type=int, default=MINUTOS_MES,
                    help=f"minutos a simular (el mes entero son {MINUTOS_MES})")
    args = ap.parse_args()

    fallos = []

    def check(ok: bool, texto: str):
        print(("[ OK ] " if ok else "[FALLO] ") + texto)
        if not ok:
            fallos.append(texto)

    print(f"Simulando {num(args.minutos)} minutos de una GPU de referencia a tope...")
    r = cargar(args.minutos)
    precio = r["precio"]
    gpu_min = r["gpu_min_gflop"]
    presu = r["presupuesto"]

    print()
    print(f"  modo: {r['modo']}  ({r['fuente']})")
    print(f"  precio: {precio:.12e} KIA/GFLOP")
    print(f"  1 minuto de GPU = {num(gpu_min)} GFLOP = "
          f"{num(precio * gpu_min, 6)} KIA")

    # --- 1. la definicion, exacta y con un solo precio publicado -----------
    check(abs(precio * gpu_min - r["kia_min_definido"]) <= 1e-12,
          f"precio x GFLOP de un minuto de GPU = {num(r['kia_min_definido'], 6)} KIA "
          f"(desvio {abs(precio * gpu_min - r['kia_min_definido']):.2e})")
    check(r["precio_gflop_publicado"] is not None
          and abs(r["precio_gflop_publicado"] - precio) <= 1e-15,
          f"/stats publica el MISMO precio en los dos sitios "
          f"({r['precio_gflop_publicado']:.12e} vs {precio:.12e})")
    check(r["kia_por_minuto_gpu"] == r["kia_min_definido"],
          f"el Core declara {num(r['kia_por_minuto_gpu'], 6)} KIA por minuto de GPU")

    # --- 2. un minuto de GPU, 1 KIA ---------------------------------------
    u = r["un_minuto"]
    print()
    print(f"  un minuto de GPU: paga {num(u['pago'], 12)} KIA "
          f"(presupuesto {num(u['presupuesto'], 6)})")
    check(abs(u["pago"] - 1.0) <= 1e-9,
          f"un minuto de GPU cobra 1 KIA ({num(u['pago'], 12)})")
    check(u["recortado"] == 0.0,
          f"sin recorte: sobra presupuesto ({num(u['presupuesto'] - u['pago'], 6)} KIA)")

    # --- 4a. capacidad: justo por debajo no se recorta nada ---------------
    cap = r["capacidad"]
    check(cap["capacidad_gpu_min"] > 1.0,
          f"la emision del minuto paga {num(cap['capacidad_gpu_min'], 6)} GPU-minutos "
          f"a la definicion")
    check(cap["recortado"] == 0.0,
          f"por debajo de la capacidad no se recorta NADA "
          f"({num(cap['gflop'] / gpu_min, 6)} GPU-min entregados, recorte "
          f"{cap['recortado']:.2e})")

    # --- 4b. la rafaga: recorta el exceso y ni una milesima mas -----------
    f = r["rafaga_2"]
    print()
    print(f"  rafaga: 2 minutos de GPU en un minuto "
          f"(presupuesto {num(f['presupuesto'], 6)} KIA)")
    print(f"    1er minuto de GPU: {num(f['pago_1'], 6)} KIA")
    print(f"    2o  minuto de GPU: {num(f['pago_2'], 6)} KIA")
    print(f"    total {num(f['total'], 6)} | recortado {num(f['recortado'], 6)}")
    check(f["total"] <= f["presupuesto"] + 1e-9,
          f"el minuto NO pasa de su presupuesto: {num(f['total'], 9)} <= "
          f"{num(f['presupuesto'], 9)}")
    check(abs(f["total"] - f["presupuesto"]) <= 1e-6,
          f"paga TODO el presupuesto (no se queda corto): "
          f"{num(f['total'], 9)} de {num(f['presupuesto'], 9)}")
    check(abs(f["recortado"] - f["exceso"]) <= 1e-6,
          f"recorta EXACTAMENTE el exceso sobre la capacidad: recortado "
          f"{num(f['recortado'], 6)} vs exceso {num(f['exceso'], 6)} KIA")

    # --- 3. el mes --------------------------------------------------------
    m = r["mes"]
    print()
    print(f"  mes ({num(args.minutos)} minutos, simulado en {m['segundos']} s):")
    print(f"    total cobrado {num(m['total'], 6)} KIA | "
          f"pago por minuto {num(m['pago_minimo'], 12)} - {num(m['pago_maximo'], 12)}")
    esperado = r["kia_min_definido"] * args.minutos
    check(abs(m["total"] - esperado) <= max(1e-6, esperado * 1e-9),
          f"cobra {num(args.minutos)} KIA por {num(args.minutos)} minutos de GPU: "
          f"{num(m['total'], 6)} vs {num(esperado, 6)}")
    check(m["recortes"] == 0.0 and m["minutos_con_recorte"] == 0,
          f"el fusible NO recorta en ninguno de los {num(args.minutos)} minutos "
          f"(recortado total {m['recortes']:.2e})")
    emitido = m["supply_1"] - r["supply_0"]
    check(abs(emitido - m["total"]) <= max(1e-6, m["total"] * 1e-9),
          f"al suministro entra lo cobrado: {num(emitido, 6)} emitidos vs "
          f"{num(m['total'], 6)} cobrados")
    check(abs(m["saldo_nodo"] - m["total"]) <= max(1e-6, m["total"] * 1e-9),
          f"el saldo del minero es su cobro: {num(m['saldo_nodo'], 6)} KIA")

    # --- 5. cliente y minero cuadran --------------------------------------
    c = r["cliente"]
    print()
    print(f"  cliente pide {num(c['gflop'])} GFLOP: se le cobra "
          f"{num(c['coste_cliente'], 9)} KIA, el minero cobra "
          f"{num(c['pago_minero'], 9)}")
    check(abs(c["coste_cliente"] - c["pago_minero"]) <= 1e-9,
          f"lo que paga el cliente es lo que cobra el minero "
          f"(diferencia {abs(c['coste_cliente'] - c['pago_minero']):.2e} KIA)")
    check(abs(c["coste_cliente"] - c["gflop"] * c["precio"]) <= 1e-9,
          f"y los dos salen de la definicion: GFLOP x precio = "
          f"{num(c['gflop'] * c['precio'], 9)} KIA")
    check(abs(c["saldo_escrow"]) <= 1e-9,
          f"el escrow queda a cero (nada retenido ni perdido): "
          f"{num(c['saldo_escrow'], 12)}")
    check(abs(c["saldo_cliente"] - (5.0 - c["coste_cliente"])) <= 1e-9,
          f"al cliente le queda exactamente su saldo menos el trabajo: "
          f"{num(c['saldo_cliente'], 9)}")
    check(abs(c["supply_despues"] - c["supply_antes"]) <= 1e-12,
          f"el suministro NO cambia al pagar un cliente (no se emite): "
          f"{num(c['supply_antes'], 6)} -> {num(c['supply_despues'], 6)}")
    check((c["recortado_minero"] or 0.0) == 0.0,
          "el minero cobra su tarea sin recorte del fusible")

    print()
    if fallos:
        print(f"{len(fallos)} FALLO(S)")
        return 1
    print("TODO OK: 1 KIA = 1 minuto de GPU, el cliente paga lo que el minero")
    print("cobra y el fusible solo muerde por encima de la capacidad.")
    return 0


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
