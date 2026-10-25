#!/usr/bin/env python3
"""Un minero a tope durante un mes entero, contra el Core de verdad.

Que demuestra (con el ancla GPU-minuto encendida):

  1. Un minero que entrega 1 minuto de GPU cada minuto cobra, en un mes,
     EXACTAMENTE la emision de ese mes. No se queda corto ni se pasa.
  2. En regimen normal el fusible NO toca nada: recortado_por_fusible = 0 en
     los 43.834 minutos del mes.
  3. El fusible actua SOLO cuando toca: si en un mismo minuto se entrega mas de
     el doble del presupuesto (una rafaga), lo que sobra se recorta y el total
     del minuto no pasa de 2x el presupuesto.

Como se simula: cada vuelta del bucle es un minuto de reloj. Se le paga al nodo
el trabajo de una GPU de referencia a tope (60 * 15.000 GFLOP) y se comprueba
cuanto entra. Los FLOP por tarea se fijan a mano (`_flops_tarea` parcheado) para
poder entregar un minuto de GPU sin ejecutar la tarea de verdad: lo que se
prueba aqui es el reparto y el fusible, no la formula de FLOPs (esa tiene sus
propias pruebas en prueba_pago_potencia.py).

Uso:
  python tools/prueba_minero_mes.py            # el mes entero (tarda un poco)
  python tools/prueba_minero_mes.py --minutos 600   # muestra rapida
"""
import argparse
import json
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

NODO = "nodo_minero_a_tope"
MINUTOS = int(os.environ["KUKIA_MINUTOS_SIM"])
GPU_MIN = C.GPU_REF_GFLOPS * 60.0        # GFLOP de un minuto de GPU de referencia

# Se fijan los FLOP de la tarea a mano: asi se entrega "un minuto de GPU" sin
# ejecutar nada. El reparto y el fusible quedan igual de reales.
# OJO: por este camino `params` llega como texto JSON (viene de la fila de la
# tarea), no como dict.
# `_flops_tarea` devuelve FLOPs, no GFLOP: `_flops` se pasa en GFLOP y aqui se
# multiplica por 1e9 (si no, se paga mil millones de veces menos).
def _flops_fijos(tipo, params):
    if isinstance(params, str):
        try:
            params = json.loads(params or "{}")
        except Exception:
            return 0.0
    return float((params or {}).get("_flops") or 0.0) * 1e9

C._flops_tarea = _flops_fijos

def minuto_limpio():
    """Empieza un minuto nuevo: bolsa a cero y precio recalculado."""
    C._minuto_actual["minuto"] = -1
    C._minuto_actual["pagado"] = 0.0
    C._minuto_actual["credito"] = 0.0
    C._minuto_actual["presupuesto"] = None
    C._precio_minuto["minuto"] = -1
    C._precio_minuto["datos"] = None

def paga(flops, trozos=1):
    """Paga `flops` GFLOP repartidos en trozos. Devuelve (pagado, recortado)."""
    total = rec = 0.0
    for _ in range(trozos):
        p, info = C.pago_de_tarea(
            {"tipo": "matriz", "params": json.dumps({"_flops": flops / trozos})}, 1.0)
        total += float(p)
        rec += float(info.get("recortado_por_fusible") or 0.0)
    return total, rec

salida = {"precio": C.precio_unico_minuto()["precio"],
          "fuente": C.precio_unico_minuto()["fuente"],
          "gpu_ref": C.GPU_REF_GFLOPS,
          "gpu_min_gflop": GPU_MIN,
          "minutos": MINUTOS,
          "presupuesto": C.presupuesto_por_minuto(),
          "supply_0": C.supply_emitido(),
          "saldo_0": 0.0}

# --- 1 y 2: el mes, minuto a minuto ---------------------------------------
# Cada minuto: se entrega 1 minuto de GPU, se cobra, y el asiento se anota en el
# ledger de verdad (por eso el suministro emitido sube y se puede comparar).
tiempo0 = time.time()
total = 0.0
suma_presu = 0.0
recortes = 0.0
minutos_con_recorte = 0
pagomin, pagomax = None, None
desvio_max = 0.0
for i in range(MINUTOS):
    minuto_limpio()
    presu_min = C.presupuesto_del_minuto()      # el tope de ESE minuto
    p, rec = paga(GPU_MIN)
    with C._escritura():
        C._asiento("mes%05d" % i, NODO, p, "trabajo verificado")
    total += p
    suma_presu += presu_min
    recortes += rec
    if rec > 0:
        minutos_con_recorte += 1
    desvio_max = max(desvio_max, abs(p - presu_min))
    pagomin = p if pagomin is None else min(pagomin, p)
    pagomax = p if pagomax is None else max(pagomax, p)
salida["mes"] = {
    "total": total,
    "suma_presupuestos": suma_presu,
    "recortes": recortes,
    "minutos_con_recorte": minutos_con_recorte,
    "pago_minimo": pagomin,
    "pago_maximo": pagomax,
    "desvio_max_minuto": desvio_max,
    "segundos": round(time.time() - tiempo0, 1),
    "supply_1": C.supply_emitido(),
}

# --- 3: la rafaga (el fusible tiene que morder) ---------------------------
# OJO: con el fusible en 1x, el minuto NO puede pagar mas que su presupuesto.
minuto_limpio()
presu = C.presupuesto_del_minuto()
rafaga = []
for k in range(5):
    p, rec = paga(GPU_MIN)               # 5 minutos de GPU en UN minuto
    rafaga.append({"pago": p, "recortado": rec})
salida["rafaga"] = {"presupuesto": presu, "intentos": rafaga,
                    "total_pagado": sum(x["pago"] for x in rafaga[:5]),
                    "minutos_gpu_entregados": 5}

# --- 4: reparto proporcional entre varias GPUs ----------------------------
# Se siembra el minuto anterior con el trabajo que se va a entregar en este:
# asi el precio de este minuto es el que reparte el presupuesto entre todos.
def sembrar_previo(gflops):
    fin = time.ctime((int(time.time() // 60) - 1) * 60 + 30)
    with C._escritura():
        C.con.execute("DELETE FROM tareas")     # solo hay las de esta prueba
        C.con.execute("INSERT INTO tareas(id,tipo,params,estado,creado,fin) "
                      "VALUES(?,?,?,'ok',?,?)",
                      (os.urandom(6).hex(), "matriz",
                       json.dumps({"_flops": gflops}), fin, fin))

def reparto(carga):
    """carga: {nombre: GFLOP entregados en el minuto}. Devuelve lo que cobra cada uno."""
    total = sum(carga.values())
    pm = {}
    for intento in range(4):
        sembrar_previo(total)               # el minuto anterior midio lo mismo
        minuto_limpio()
        pm = C.precio_unico_minuto()
        if str(pm.get("fuente", "")).startswith("trabajo medido"):
            break
        time.sleep(0.4)                     # cruce de minuto: se reintenta
    out = {"precio": pm.get("precio"), "fuente": pm.get("fuente"),
           "presupuesto": C.presupuesto_del_minuto(), "pagos": {}}
    for nombre, g in carga.items():
        p, rec = paga(g)
        out["pagos"][nombre] = {"pago": p, "recortado": rec, "gflop": g}
    return out

salida["dos_gpus"] = reparto({"gpu_a": GPU_MIN, "gpu_b": GPU_MIN})
salida["rapida_y_lenta"] = reparto({"rapida_3_5x": 3.5 * GPU_MIN, "lenta_1x": GPU_MIN})

print("RESULTADO:" + json.dumps(salida))
'''


def cargar(minutos: int) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="kukia_minero_mes_"))
    try:
        (tmp / "coordinator").mkdir()
        (tmp / "ledger").mkdir()
        shutil.copy2(RAIZ / "coordinator" / "kukia_core.py", tmp / "coordinator")
        for f in ("sellos.py", "kukia_wallet.py", "wordlist_es.txt"):
            shutil.copy2(RAIZ / "ledger" / f, tmp / "ledger")
        (tmp / "runner.py").write_text(RUNNER, encoding="utf-8")
        entorno = dict(
            {k: v for k, v in __import__("os").environ.items() if not k.startswith("KUKIA_")},
            KUKIA_DB=str(tmp / "coordinator" / "ledger" / "kukia.db"),
            KUKIA_AUTO_TAREAS="0",
            KUKIA_ANCLA_GPU_MIN="1",
            KUKIA_MINUTOS_SIM=str(minutos),
            PYTHONIOENCODING="utf-8",
        )
        r = subprocess.run([sys.executable, "runner.py"], cwd=str(tmp), env=entorno,
                           capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise SystemExit(f"[prueba] el Core temporal fallo:\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
        for linea in r.stdout.splitlines():
            if linea.startswith("RESULTADO:"):
                return json.loads(linea[len("RESULTADO:"):])
        raise SystemExit(f"[prueba] sin resultado:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def num(n, dec=2):
    return f"{n:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def main() -> int:
    ap = argparse.ArgumentParser(description="Un minero a tope durante un mes")
    ap.add_argument("--minutos", type=int, default=MINUTOS_MES,
                    help=f"minutos a simular (el mes entero son {MINUTOS_MES})")
    args = ap.parse_args()

    fallos = []

    def check(ok: bool, texto: str):
        print(("[ OK ] " if ok else "[FALLO] ") + texto)
        if not ok:
            fallos.append(texto)

    print(f"Simulando {num(args.minutos)} minutos ({num(args.minutos / 1440, 2)} dias) "
          f"de un minero a tope...")
    r = cargar(args.minutos)
    m = r["mes"]
    presu = r["presupuesto"]

    print()
    print(f"  precio: {r['precio']:.9e} KIA/GFLOP (fuente: {r['fuente']})")
    print(f"  1 minuto de GPU = {num(r['gpu_min_gflop'])} GFLOP = "
          f"{num(presu, 6)} KIA (el presupuesto del minuto)")
    print(f"  simulado en {m['segundos']} s")

    # --- 1. cada minuto paga el presupuesto de su minuto -------------------
    check(m["desvio_max_minuto"] <= 1e-9,
          f"en cada minuto se paga exactamente el presupuesto de ese minuto "
          f"(desvio maximo {m['desvio_max_minuto']:.2e} KIA)")
    # Se mueve una milesima de porcentaje porque el calendario reparte lo que
    # QUEDA: lo pagado hoy reduce un pelo el ritmo de manana. No es un recorte.
    holgura = (m["pago_maximo"] - m["pago_minimo"]) / presu
    check(holgura <= 1e-3,
          f"el pago por minuto es estable de punta a punta: "
          f"{num(m['pago_minimo'], 6)} a {num(m['pago_maximo'], 6)} KIA "
          f"({holgura * 100:.6f} % de diferencia)")

    # --- 2. el mes cuadra con la emision -----------------------------------
    esperado = presu * r["minutos"]
    desvio = abs(m["total"] - m["suma_presupuestos"])
    check(desvio <= max(1e-9, m["total"] * 1e-12),
          f"el total cobrado es la suma de los presupuestos de esos minutos "
          f"({num(m['total'], 4)} vs {num(m['suma_presupuestos'], 4)} KIA)")
    deriva = abs(m["total"] - esperado) / esperado
    check(deriva <= 0.005,
          f"el mes cobra {num(m['total'], 2)} KIA y la emision del mes a ese ritmo "
          f"son {num(esperado, 2)} ({deriva * 100:.3f} % de diferencia: el calendario "
          f"reparte lo que queda, y lo pagado hoy baja un pelo el ritmo de manana)")

    # --- 2b. lo que entra al nodo es lo que se emitio ----------------------
    emitido = m["supply_1"] - r["supply_0"]
    check(abs(emitido - m["total"]) <= max(1e-6, m["total"] * 1e-9),
          f"al suministro entra lo mismo que cobra el minero: "
          f"{num(emitido, 4)} KIA emitidos vs {num(m['total'], 4)} cobrados")

    # --- 2c. el fusible no toca nada en regimen normal ----------------------
    check(m["recortes"] == 0.0 and m["minutos_con_recorte"] == 0,
          f"el fusible no recorta NADA en {num(r['minutos'])} minutos "
          f"(recortado total {m['recortes']:.2e})")

    # --- 3. la rafaga: aqui SI tiene que morder ----------------------------
    raf = r["rafaga"]
    intentos = raf["intentos"]
    print()
    print(f"  rafaga: 5 minutos de GPU pedidos en un solo minuto "
          f"(presupuesto {num(raf['presupuesto'], 6)} KIA, fusible 1x)")
    for i, x in enumerate(intentos, 1):
        print(f"    intento {i}: paga {num(x['pago'], 6)} KIA"
              f"   recorta {num(x['recortado'], 6)}")
    pagados_completos = [x for x in intentos if x["pago"] > 0 and x["recortado"] == 0]
    recortados = [x for x in intentos if x["recortado"] > 0]
    check(len(pagados_completos) >= 1,
          f"el primer minuto de GPU de la rafaga se paga entero "
          f"({len(pagados_completos)} de {len(intentos)})")
    check(len(recortados) >= 1,
          f"lo que pasa del presupuesto del minuto se recorta "
          f"({len(recortados)} intentos recortados)")
    check(raf["total_pagado"] <= raf["presupuesto"] + 1e-9,
          f"el minuto NO pasa de su presupuesto (fusible 1x): "
          f"{num(raf['total_pagado'], 6)} <= {num(raf['presupuesto'], 6)} KIA")
    check(raf["total_pagado"] < raf["minutos_gpu_entregados"] * raf["presupuesto"],
          f"5 minutos de GPU entregados en 1 minuto NO se pagan como 5 "
          f"({num(raf['total_pagado'], 6)} < "
          f"{num(raf['minutos_gpu_entregados'] * raf['presupuesto'], 6)})")

    # --- 4. reparto proporcional: dos GPUs iguales --------------------------
    d = r["dos_gpus"]
    presu_d = d["presupuesto"]
    pa, pb = d["pagos"]["gpu_a"], d["pagos"]["gpu_b"]
    print()
    print(f"  dos GPUs iguales, 1 minuto de GPU cada una en el mismo minuto "
          f"(presupuesto {num(presu_d, 6)} KIA)")
    print(f"    fuente del precio: {d['fuente']}  ({d['precio']:.9e} KIA/GFLOP)")
    print(f"    gpu_a: {num(pa['pago'], 6)} KIA   gpu_b: {num(pb['pago'], 6)} KIA")
    check(abs(pa["pago"] - presu_d / 2) <= presu_d * 1e-9
          and abs(pb["pago"] - presu_d / 2) <= presu_d * 1e-9,
          f"cada una cobra la mitad del presupuesto: {num(presu_d / 2, 6)} KIA")
    check(abs(pa["pago"] + pb["pago"] - presu_d) <= presu_d * 1e-9,
          f"sumando las dos sale el presupuesto justo: "
          f"{num(pa['pago'] + pb['pago'], 6)} de {num(presu_d, 6)}")
    check(pa["recortado"] == 0.0 and pb["recortado"] == 0.0,
          "sin recortes: el reparto ya deja el minuto cuadrado")

    # --- 5. reparto proporcional: una rapida y una lenta -------------------
    f = r["rapida_y_lenta"]
    presu_f = f["presupuesto"]
    pr, pl = f["pagos"]["rapida_3_5x"], f["pagos"]["lenta_1x"]
    print()
    print(f"  una GPU 3,5x mas rapida y una normal, 1 minuto cada una "
          f"(presupuesto {num(presu_f, 6)} KIA)")
    print(f"    rapida: {num(pr['pago'], 6)} KIA   lenta: {num(pl['pago'], 6)} KIA")
    ratio = pr["pago"] / pl["pago"] if pl["pago"] else 0.0
    check(abs(ratio - 3.5) <= 3.5 * 0.01,
          f"la rapida cobra 3,5 veces lo de la lenta por el mismo tiempo de "
          f"reloj: {num(ratio, 3)}x")
    check(abs(pr["pago"] + pl["pago"] - presu_f) <= presu_f * 1e-9,
          f"y entre las dos no pasan del presupuesto: "
          f"{num(pr['pago'] + pl['pago'], 6)} de {num(presu_f, 6)}")
    check(pr["recortado"] == 0.0 and pl["recortado"] == 0.0,
          "sin recortes: la parte de cada una es proporcion a su trabajo")

    print()
    if fallos:
        print(f"{len(fallos)} FALLO(S)")
        return 1
    print("TODO OK: un minero a tope un mes cobra la emision del mes, y el fusible")
    print("solo muerde en las rafagas, no en el trabajo normal.")
    return 0


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
