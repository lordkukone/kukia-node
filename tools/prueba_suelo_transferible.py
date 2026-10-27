#!/usr/bin/env python3
"""Prueba del SUELO TRANSFERIBLE POR TAREA (25-10-2026).

Decision de Kuki: ninguna tarea paga menos de lo que se puede mover por
`POST /transferir`. Ese endpoint redondea el monto a 4 decimales, asi que por
debajo de **0,0001 KIA** un saldo no se puede enviar nunca: son tareas
trabajadas y no cobrables.

El suelo se aplica EN KIA (`KUKIA_PAGO_MIN_KIA`, 0,0001 por defecto), no en
GFLOP, para que siga valiendo lo mismo si un dia cambia el precio.

Contra un Core de verdad en una carpeta temporal (nunca el ledger real).

Que comprueba:

  1. Esta configurado: `PAGO_MIN_KIA` = 0,0001 y `GFLOP_MIN_FACTURABLE` = 0
     (el suelo no infla los GFLOP medidos de la red).
  2. TODOS los tipos de tarea, en su tamano minimo, pagan >= 0,0001 KIA y
     `round(pago, 4) > 0`, o sea son transferibles.
  3. Sin el suelo (KUKIA_PAGO_MIN_KIA=0) esas mismas tareas pagaban 0 o 1e-9:
     quedaban sin poder moverse. Es la prueba de que el suelo hace falta.
  4. Solo muerde por debajo de 90 GFLOP (6 ms de GPU de referencia): una
     matriz de 355 (89,5 GFLOP) paga el suelo; una de 356 (90,2 GFLOP) ya paga
     por trabajo; un minuto de GPU entero (900.000 GFLOP) sigue pagando 1 KIA.
  5. El cliente paga lo mismo que cobra el minero, el escrow queda a cero y el
     suministro no cambia (el suelo no crea dinero).
  6. El tope del minuto sigue mandando: con el suelo caben 13.105 tareas por
     minuto (1,31059 / 0,0001) y el minuto NO se pasa ni un decimal; a partir
     de ahi la tarea paga 0, nunca una cifra negativa ni fuera de presupuesto.
  7. `/stats` publica el suelo (`pago_por_potencia.pago_min_kia`) y
     `pago_por_tarea` ya no dice 0.

Uso:
  python tools/prueba_suelo_transferible.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Lo que exige /transferir: `round(monto, 4)` y despues `monto <= 0` -> 400.
MIN_TRANSFERIBLE = 0.0001

RUNNER = r'''
import json, os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "coordinator"))
sys.path.insert(0, os.getcwd())
import kukia_core as C

TIPOS = (
    ("checksum",        {"n": 10}),
    ("inferencia",      {"n": 8}),
    ("matriz",          {"n": 8}),
    ("embeddings",      {"n": 8}),
    ("lote_embeddings", {"n": 8}),
    ("difusion_calor",  {"n": 8, "pasos": 1}),
)

def minuto_limpio():
    C._minuto_actual["minuto"] = -1
    C._minuto_actual["pagado"] = 0.0
    C._minuto_actual["credito"] = 0.0
    C._minuto_actual["presupuesto"] = None
    C._precio_minuto["minuto"] = -1
    C._precio_minuto["datos"] = None

def cobra(tipo, params):
    minuto_limpio()
    p, info = C.pago_de_tarea({"tipo": tipo, "params": json.dumps(params)}, 1.0)
    return float(p), info

def saldo(cuenta):
    f = C.con.execute("SELECT total FROM saldos_cuenta WHERE cuenta=?", (cuenta,)).fetchone()
    return float(f["total"] if f else 0.0)

salida = {}
salida["pago_min_kia"] = C.PAGO_MIN_KIA
salida["gflop_min"] = C.GFLOP_MIN_FACTURABLE
salida["precio"] = C.precio_unico_minuto()["precio"]
salida["presupuesto"] = C.presupuesto_del_minuto()

# --- 2: cada tipo minimo, con el suelo puesto ---------------------------
con_suelo = {}
for tipo, params in TIPOS:
    p, info = cobra(tipo, params)
    con_suelo[tipo] = {"pago": p, "gflop": info.get("gflop_tarea"),
                       "por_suelo": bool(info.get("pagado_por_suelo")),
                       "suelo_publicado": info.get("pago_min_kia")}
salida["con_suelo"] = con_suelo

# --- 3: los mismos, con el suelo apagado (lo que pasaba antes) ----------
C.PAGO_MIN_KIA = 0.0
sin_suelo = {}
for tipo, params in TIPOS:
    p, _ = cobra(tipo, params)
    sin_suelo[tipo] = p
salida["sin_suelo"] = sin_suelo
C.PAGO_MIN_KIA = 0.0001

# --- 4: donde muerde el suelo -------------------------------------------
# 90 GFLOP es el borde: 90 GFLOP x 0,000001111111 = 0,0001 KIA. En FLOPs de una
# matriz (2n^3) el borde cae en n ~ 3.556,5; se toman tamanos claramente a cada
# lado para que la prueba no dependa del ultimo decimal.
borde = {}
for n in (3500, 3600):
    p, info = cobra("matriz", {"n": n})
    borde[str(n)] = {"gflop": info.get("gflop_tarea"), "pago": p,
                     "por_suelo": bool(info.get("pagado_por_suelo"))}
p_gpu, info_gpu = cobra("matriz", {"n": 76630})     # 900.000 GFLOP = 1 GPU-min
borde["un_minuto_gpu"] = {"gflop": info_gpu.get("gflop_tarea"), "pago": p_gpu,
                          "por_suelo": bool(info_gpu.get("pagado_por_suelo"))}
salida["borde"] = borde

# --- 5: cliente minimo: paga lo mismo que cobra el minero ---------------
CLIENTE = "KUK" + "B" * 38
NODO = "nodo_suelo_prueba"
coste = C._coste_tarea_cliente("checksum", {"n": 10})
with C._escritura():
    C._asiento("fondeo_suelo", CLIENTE, 1.0, "fondeo de prueba")
supply_antes = C.supply_emitido()
minuto_limpio()
pago_minero, info_minero = C.pago_de_tarea(
    {"tipo": "checksum", "params": json.dumps({"n": 10})}, 1.0)
with C._escritura():
    C._retener_prepago("tsuelo", CLIENTE, coste)
    pagado, detalle = C._pagar_tarea_de_cliente("tsuelo", [(NODO, coste)], CLIENTE, coste)
salida["cliente"] = {
    "coste_cliente": coste, "pago_minero": float(pago_minero),
    "pagado_escrow": float(pagado), "sobrante": detalle.get("sobrante_devuelto"),
    "saldo_cliente": saldo(CLIENTE), "saldo_minero": saldo(NODO),
    "saldo_escrow": saldo(C.CUENTA_ESCROW),
    "supply_antes": supply_antes, "supply_despues": C.supply_emitido(),
    "recortado_fusible": info_minero.get("recortado_por_fusible"),
}

# --- 6: el tope del minuto con tareas de suelo --------------------------
# Se arranca al principio de un minuto de RELOJ de verdad: la bolsa se cierra y
# se abre con el minuto real, asi que si el bucle cruza el cambio de minuto
# estrena presupuesto y la cuenta de "caben 13.119" no significaria nada.
import time
while time.time() % 60 > 2.0:
    time.sleep(0.2)
minuto_limpio()
presu = C.presupuesto_del_minuto()
minuto_inicio = C._minuto_actual["minuto"]
n = 0
total = 0.0
ultimo = None
while n < 200000:
    p, _ = C.pago_de_tarea({"tipo": "checksum", "params": json.dumps({"n": 10})}, 1.0)
    ultimo = p
    if p <= 0:
        break
    total += p
    n += 1
cruzado = C._minuto_actual["minuto"] != minuto_inicio
salida["tope"] = {"tareas_pagadas": n, "total": total, "presupuesto": presu,
                  "cruzado": cruzado, "ultimo_pago": ultimo,
                  "pago_tras_agotar": C.pago_de_tarea(
                      {"tipo": "checksum", "params": json.dumps({"n": 10})}, 1.0)[0]}

# --- 7: lo que publica /stats ------------------------------------------
d = C.stats()
salida["stats"] = {"pago_min_kia": (d.get("pago_por_potencia") or {}).get("pago_min_kia"),
                   "pago_por_tarea": d.get("pago_por_tarea"),
                   "gflop_red_por_min": (d.get("pago_por_potencia") or {}).get("gflop_red_por_min")}

print("RESULTADO:" + json.dumps(salida))
'''


def cargar() -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="kukia_suelo_"))
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
            # Los bordes del suelo (90 GFLOP = 0,0001 KIA) valen con la
            # DEFINICION; se fija el modo para no heredar el defecto nuevo.
            KUKIA_PRECIO_DEFINIDO="1",
            PYTHONIOENCODING="utf-8",
        )
        r = subprocess.run([sys.executable, "runner.py"], cwd=str(tmp), env=entorno,
                           capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            raise SystemExit(f"[prueba] el Core temporal fallo:\n"
                             f"{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
        for linea in r.stdout.splitlines():
            if linea.startswith("RESULTADO:"):
                return json.loads(linea[len("RESULTADO:"):])
        raise SystemExit(f"[prueba] sin resultado:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def num(n, dec=6):
    return f"{n:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def main() -> int:
    fallos = []

    def check(ok: bool, texto: str):
        print(("[ OK ] " if ok else "[FALLO] ") + texto)
        if not ok:
            fallos.append(texto)

    r = cargar()
    print(f"precio {r['precio']:.12e} KIA/GFLOP · presupuesto {num(r['presupuesto'])} KIA/min")
    print(f"suelo por tarea: {num(r['pago_min_kia'], 4)} KIA · "
          f"suelo en GFLOP: {num(r['gflop_min'], 2)} (0 = no se toca la medicion)\n")

    # --- 1. configurado ---------------------------------------------------
    check(abs(r["pago_min_kia"] - MIN_TRANSFERIBLE) < 1e-12,
          f"el suelo esta puesto en {num(r['pago_min_kia'], 4)} KIA (lo minimo transferible)")
    check(r["gflop_min"] == 0.0,
          "el suelo es en KIA: los GFLOP medidos de la red no se inflan")

    # --- 2. todos los tipos, transferibles --------------------------------
    print()
    print(f"  {'tipo':17} {'GFLOP':>12} {'pago KIA':>14} {'round(4)':>10}  por suelo")
    for tipo, d in r["con_suelo"].items():
        print(f"  {tipo:17} {d['gflop']:12.6f} {d['pago']:14.10f} "
              f"{round(d['pago'], 4):10.4f}  {'si' if d['por_suelo'] else 'no'}")
        check(d["pago"] >= MIN_TRANSFERIBLE - 1e-12,
              f"{tipo}: paga {num(d['pago'], 10)} KIA, no menos de {num(MIN_TRANSFERIBLE, 4)}")
        check(round(d["pago"], 4) > 0,
              f"{tipo}: /transferir lo aceptaria (round(monto,4) = {round(d['pago'], 4):.4f})")
        check(abs(float(d["suelo_publicado"] or 0) - MIN_TRANSFERIBLE) < 1e-12,
              f"{tipo}: el detalle del pago declara el suelo ({num(MIN_TRANSFERIBLE, 4)})")

    # --- 3. sin el suelo, no eran transferibles --------------------------
    print()
    for tipo, p in r["sin_suelo"].items():
        check(round(p, 4) == 0,
              f"sin suelo, {tipo} pagaba {p:.3e} KIA: /transferir lo rechazaria (redondea a 0)")

    # --- 4. solo muerde por debajo de 90 GFLOP ---------------------------
    b = r["borde"]
    print()
    print(f"  matriz 3500: {num(b['3500']['gflop'], 3)} GFLOP -> {num(b['3500']['pago'], 10)} KIA"
          f"  (suelo: {'si' if b['3500']['por_suelo'] else 'no'})")
    print(f"  matriz 3600: {num(b['3600']['gflop'], 3)} GFLOP -> {num(b['3600']['pago'], 10)} KIA"
          f"  (suelo: {'si' if b['3600']['por_suelo'] else 'no'})")
    check(b["3500"]["por_suelo"] and abs(b["3500"]["pago"] - MIN_TRANSFERIBLE) < 1e-12,
          "por debajo de 90 GFLOP manda el suelo (matriz 3500 = 85,75 GFLOP)")
    check(not b["3600"]["por_suelo"] and b["3600"]["pago"] > MIN_TRANSFERIBLE,
          "a partir de 90 GFLOP manda la definicion (matriz 3600 = 93,3 GFLOP paga mas)")
    check(abs(b["un_minuto_gpu"]["pago"] - 1.0) < 1e-4 and not b["un_minuto_gpu"]["por_suelo"],
          f"un minuto de GPU entero sigue pagando ~1 KIA "
          f"({num(b['un_minuto_gpu']['pago'], 6)} por "
          f"{num(b['un_minuto_gpu']['gflop'], 0)} GFLOP: manda la definicion, no el suelo)")

    # --- 5. cliente y minero, la misma cuenta ----------------------------
    c = r["cliente"]
    print()
    print(f"  cliente paga {num(c['coste_cliente'], 10)} · minero cobra "
          f"{num(c['pago_minero'], 10)} · escrow {num(c['saldo_escrow'], 12)}")
    check(abs(c["coste_cliente"] - MIN_TRANSFERIBLE) < 1e-12,
          f"la tarea minima de cliente cuesta el suelo ({num(c['coste_cliente'], 6)} KIA)")
    check(abs(c["pago_minero"] - c["coste_cliente"]) < 1e-12,
          "lo que paga el cliente es exactamente lo que cobra el minero")
    check(abs(c["saldo_minero"] - MIN_TRANSFERIBLE) < 1e-12,
          f"al minero le entra el suelo entero ({num(c['saldo_minero'], 6)} KIA)")
    check(abs(c["saldo_escrow"]) < 1e-12, "el escrow queda a cero (nada retenido de mas)")
    check(abs(float(c["sobrante"])) < 1e-12, "no hay sobrante: el deposito era el precio")
    check(abs(c["supply_antes"] - c["supply_despues"]) < 1e-9,
          "el suministro NO cambia: el suelo no crea dinero, solo mueve el del cliente")

    # --- 6. el tope del minuto sigue mandando ----------------------------
    t = r["tope"]
    print()
    print(f"  tareas de suelo que caben en un minuto: {t['tareas_pagadas']:,}".replace(",", ".")
          + f" (tope {num(t['presupuesto'])})".replace(",", "."))
    check(not t["cruzado"], "la prueba del tope cabe entera en un minuto de reloj")
    check(t["total"] <= t["presupuesto"] + 1e-9,
          f"el minuto no se pasa: {num(t['total'], 9)} <= {num(t['presupuesto'], 9)} KIA")
    check(abs(t["total"] - t["presupuesto"]) <= MIN_TRANSFERIBLE,
          "el minuto paga casi todo su presupuesto (no se queda corto por el suelo)")
    esperadas = int(t["presupuesto"] / MIN_TRANSFERIBLE)
    check(abs(t["tareas_pagadas"] - esperadas) <= 2,
          f"caben {esperadas:,} tareas de 0,0001 KIA (presupuesto / suelo)".replace(",", "."))
    check(t["pago_tras_agotar"] == 0,
          "agotado el minuto, la siguiente paga 0 (nunca negativo)")

    # --- 7. lo que ve el mundo -------------------------------------------
    s = r["stats"]
    check(abs(float(s["pago_min_kia"]) - MIN_TRANSFERIBLE) < 1e-12,
          f"/stats publica el suelo: pago_por_potencia.pago_min_kia = {s['pago_min_kia']}")
    check(float(s["pago_por_tarea"]) > 0,
          f"/stats ya no dice 'pago_por_tarea: 0' (dice {s['pago_por_tarea']})")
    check(s["gflop_red_por_min"] is not None,
          f"la potencia medida se sigue publicando ({s['gflop_red_por_min']} GFLOP/min)")

    print()
    if fallos:
        print(f"FALLOS: {len(fallos)}")
        return 1
    print("TODO OK: ninguna tarea paga menos de lo transferible, el suelo solo muerde")
    print("por debajo de 90 GFLOP y el tope del minuto sigue mandando.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
