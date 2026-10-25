#!/usr/bin/env python3
"""Prueba del ANCLA GPU-MINUTO (25-10-2026).

Que comprueba, contra un Core de verdad cargado en una carpeta temporal (no
toca el ledger real):

  1. PRECIO: con el ancla, precio = presupuesto_del_minuto / (60 * GPU_REF).
     O sea: un minuto de GPU cuesta exactamente lo que la red emite en un
     minuto, que es lo mismo que decir "un mes de emision paga un mes de GPU".
  2. IDENTIDAD MENSUAL: precio * 60 * GPU_REF * 1440 * 30,44 = la emision de
     un mes, al decimal.
  3. PAGO AL MINERO: el pago sigue siendo precio x GFLOP de la tarea, y el
     techo por tarea se respeta igual que antes.
  4. PREPAGO DEL CLIENTE: retener, pagar al minero y devolver el sobrante
     cuadra al céntimo: la suma de los asientos de la tarea es 0 y el
     suministro emitido NO cambia (el dinero del cliente no infla la moneda).
  5. INTERRUPTOR: con KUKIA_ANCLA_GPU_MIN=0 vuelve el precio por trabajo
     medido, que con la red casi parada sale mucho mas caro.

Uso:
  python tools/prueba_ancla.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
GFLOP_MIN = 0.0
DIAS_MES = 30.44

# El runner se ejecuta DENTRO de la carpeta temporal: la base, las claves del
# sello y el estado que crea el Core se quedan ahi.
RUNNER = r'''
import json, os, sys, time
sys.path.insert(0, os.path.join(os.getcwd(), "coordinator"))
sys.path.insert(0, os.getcwd())
import kukia_core as C

DIF = {"n": 1024, "pasos": 1907, "seed": 7, "tolerancia_rel": 1e-9}
MAT_PEQUE = {"n": 64, "seed": 3}
MAT_GRANDE = {"n": 200, "seed": 3}
TID = "tarea_ancla_1"
CLIENTE = "KUK" + "AB12" * 9 + "AB"
NODO = "nodo_prueba_ancla"
BANCO = "KUKIA_BANCO"
CUENTA = C.CUENTA_ESCROW          # la cuenta real del escrow (KUKIA_ESCROW)

def sembrar(n, tipo, params, fin=None):
    """Tareas verificadas en el minuto anterior: dan medida de red."""
    ahora = fin or time.ctime()
    with C._escritura():
        for _ in range(n):
            C.con.execute("INSERT INTO tareas(id,tipo,params,estado,creado,fin) "
                          "VALUES(?,?,?,'ok',?,?)",
                          (os.urandom(6).hex(), tipo, json.dumps(params), ahora, ahora))

def previo():
    return time.ctime((int(time.time() // 60) - 1) * 60 + 30)

def precio_limpio():
    """Precio del minuto recalculado desde cero y bolsa del minuto a cero."""
    C._precio_minuto["minuto"] = -1
    C._precio_minuto["datos"] = None
    C._minuto_actual["minuto"] = -1
    C._minuto_actual["pagado"] = 0.0
    C._minuto_actual["credito"] = 0.0
    return C.precio_unico_minuto()

def saldo(cuenta):
    f = C.con.execute("SELECT total FROM saldos_cuenta WHERE cuenta=?", (cuenta,)).fetchone()
    return float((f["total"] if f else 0.0) or 0.0)

salida = {"ancla": bool(C.ANCLA_GPU_MIN), "gpu_ref": C.GPU_REF_GFLOPS,
          "fusion_x": C.FUSIBLE_MINUTO_X, "techo": C.REWARD_BASE * C.emission_mult()}

sembrar(40, "difusion_calor", DIF, previo())     # red con algo de trabajo medido
presu = C.presupuesto_del_minuto()
pm = precio_limpio()
salida.update(presupuesto=presu, precio=pm["precio"], fuente=pm["fuente"],
              kia_min_gpu=pm["kia_por_minuto_gpu"], gflop_anterior=pm["gflop_anterior"])
salida["gflop_ref_minuto"] = C.GPU_REF_GFLOPS * 60.0

# --- pago al minero -------------------------------------------------------
def paga(tipo, params):
    precio_limpio()
    pago, info = C.pago_de_tarea({"tipo": tipo, "params": json.dumps(params)}, 1.0)
    return {"pago": pago, "info": info,
            "gflop": C._flops_tarea(tipo, params) / 1e9}

salida["pagos"] = {"difusion": paga("difusion_calor", DIF),
                   "matriz_peque": paga("matriz", MAT_PEQUE),
                   "matriz_grande": paga("matriz", MAT_GRANDE)}

# --- prepago del cliente --------------------------------------------------
supply_antes = C.supply_emitido()
with C._escritura():          # fondeo: un cliente con saldo y un "banco" en negativo
    C._asiento("fondeo", CLIENTE, 100.0, "fondeo de prueba")
    C._asiento("fondeo", BANCO, -100.0, "fondeo de prueba")

coste = 2.0            # importe con magnitud real: si fuera 7e-10 la prueba
                        # pasaria sin comprobar nada (el sobrante y el escrow se
                        # quedarian a cero por redondeo)
deposito = 5.0          # deja deposito de mas para ver la devolucion del sobrante

with C._escritura():
    C._retener_prepago(TID, CLIENTE, deposito)

saldos = {"cliente_tras_retener": saldo(CLIENTE), "escrow_tras_retener": saldo(CUENTA),
          "cuenta_escrow": CUENTA}
with C._escritura():
    pagado, info_pago = C._pagar_tarea_de_cliente(TID, [(NODO, coste)], CLIENTE, deposito)

asientos = C.con.execute("SELECT COALESCE(SUM(kuki),0) s FROM ledger WHERE tarea_id=?",
                         (TID,)).fetchone()["s"]

salida["prepago"] = {
    "deposito": deposito, "coste": coste, "pagado_al_minero": pagado,
    "saldos": saldos,
    "cliente_final": saldo(CLIENTE), "escrow_final": saldo(CUENTA), "nodo_final": saldo(NODO),
    "suma_asientos_tarea": float(asientos or 0.0),
    "supply_antes": supply_antes, "supply_despues": C.supply_emitido(),
    "info": info_pago,
}

print("RESULTADO:" + json.dumps(salida))
'''


def cargar(flag, gflop_min=GFLOP_MIN) -> dict:
    """Carga el Core en una carpeta temporal con el ancla encendida o apagada."""
    tmp = Path(tempfile.mkdtemp(prefix="kukia_ancla_"))
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
            KUKIA_GFLOP_MIN=str(gflop_min),
            KUKIA_ANCLA_GPU_MIN=flag,
            PYTHONIOENCODING="utf-8",
        )
        r = subprocess.run([sys.executable, "runner.py"], cwd=str(tmp), env=entorno,
                           capture_output=True, text=True, timeout=240)
        if r.returncode != 0:
            raise SystemExit(f"[prueba] el Core temporal fallo:\n{r.stdout}\n{r.stderr}")
        for linea in r.stdout.splitlines():
            if linea.startswith("RESULTADO:"):
                return json.loads(linea[len("RESULTADO:"):])
        raise SystemExit(f"[prueba] sin resultado:\n{r.stdout}\n{r.stderr}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    fallos = []

    def check(ok: bool, texto: str):
        print(("[ OK ] " if ok else "[FALLO] ") + texto)
        if not ok:
            fallos.append(texto)

    on = cargar("1")
    presu = on["presupuesto"]
    ref = on["gpu_ref"]

    # --- 1. el precio -------------------------------------------------------
    # El ancla es un SUELO: con poca faena la fuente es "ancla GPU-minuto
    # (suelo)" y, cuando la red entrega mas que una GPU, "trabajo medido
    # (reparto proporcional)". Aqui se comprueba que manda el suelo.
    check(on["ancla"] is True and str(on["fuente"]).startswith("ancla GPU-minuto"),
          f"con poca faena manda el suelo del ancla: fuente '{on['fuente']}'")
    esperado = presu / on["gflop_ref_minuto"]
    check(on["precio"] and abs(on["precio"] / esperado - 1) < 1e-12,
          f"precio = presupuesto/(60*{ref:.0f}) = {on['precio']:.12e} KIA/GFLOP")
    check(abs(on["precio"] * on["gflop_ref_minuto"] - presu) < 1e-12,
          f"1 minuto de GPU cuesta {on['kia_min_gpu']:.6f} KIA, igual que el "
          f"presupuesto del minuto ({presu:.6f})")

    # --- 2. identidad mensual ----------------------------------------------
    mes_emision = presu * 1440 * DIAS_MES
    mes_gpu = on["precio"] * on["gflop_ref_minuto"] * 1440 * DIAS_MES
    check(abs(mes_gpu / mes_emision - 1) < 1e-12,
          f"un mes de emision ({mes_emision:,.0f} KIA) paga un mes de GPU "
          f"({mes_gpu:,.0f} KIA)".replace(",", "."))

    # --- 3. pago al minero --------------------------------------------------
    precio = on["precio"]
    p = on["pagos"]
    for nombre in ("matriz_peque", "matriz_grande"):
        calculado = precio * p[nombre]["gflop"]
        check(abs(p[nombre]["pago"] - calculado) <= max(1e-9, calculado * 1e-6),
              f"{nombre}: paga {p[nombre]['pago']:.12f} KIA por {p[nombre]['gflop']:.4f} GFLOP "
              f"(precio x GFLOP = {calculado:.12f})")
    check(p["matriz_grande"]["pago"] > p["matriz_peque"]["pago"] > 0,
          f"mas trabajo cobra mas y nada se queda a cero: "
          f"{p['matriz_peque']['pago']:.12f} < {p['matriz_grande']['pago']:.12f} KIA")
    check(all(v["pago"] <= on["techo"] + 1e-9 for v in p.values()),
          f"ningun pago supera el techo por tarea ({on['techo']:.2f} KIA)")

    # --- 4. prepago del cliente --------------------------------------------
    q = on["prepago"]
    esperado_cliente = 100.0 - q["coste"]
    check(abs(q["cliente_final"] - esperado_cliente) < 1e-6,
          f"el cliente paga {q['coste']:.2f} KIA y recupera el sobrante de los "
          f"{q['deposito']:.2f} depositados: queda en {q['cliente_final']:.6f}")
    check(abs(q["escrow_final"]) < 1e-12,
          f"el deposito del escrow queda a cero: {q['escrow_final']:.12f}")
    check(abs(q["nodo_final"] - q["coste"]) < 1e-9,
          f"el minero cobra del deposito: {q['nodo_final']:.6f} KIA")
    s = q["saldos"]
    check(abs(s["cliente_tras_retener"] - (100.0 - q["deposito"])) < 1e-6
          and abs(s["escrow_tras_retener"] - q["deposito"]) < 1e-6,
          f"al retener, el deposito sale del cliente ({s['cliente_tras_retener']:.2f}) "
          f"y entra en escrow ({s['escrow_tras_retener']:.2f})")
    check(abs(q["suma_asientos_tarea"]) < 1e-12,
          f"la suma de los asientos de la tarea es 0: {q['suma_asientos_tarea']:.9f} "
          f"(el prepago no crea ni destruye dinero)")
    check(abs(q["supply_despues"] - q["supply_antes"]) < 1e-9,
          f"el suministro emitido NO cambia con un cliente pagando: "
          f"{q['supply_antes']:.3f} -> {q['supply_despues']:.3f}")

    # --- 5. el interruptor --------------------------------------------------
    off = cargar("0")
    check(off["ancla"] is False and off["fuente"] != "ancla GPU-minuto",
          f"con KUKIA_ANCLA_GPU_MIN=0 la fuente vuelve a '{off['fuente']}'")
    check(off["precio"] and on["precio"] and off["precio"] > on["precio"] * 2,
          f"sin ancla y con la red casi parada el precio se dispara: "
          f"{off['precio']:.9f} vs {on['precio']:.9f} KIA/GFLOP "
          f"({off['precio'] / on['precio']:.0f} veces)")

    # --- 6. tareas pequenas con suelo facturable ---------------------------
    # El ancla hace que 0,0005 GFLOP valga 7e-10 KIA: se cobra, pero no llega
    # al minimo transferible (0,0001 KIA). Con KUKIA_GFLOP_MIN=15000 una tarea
    # se cobra como minimo 1 segundo de GPU, que si es dinero de verdad.
    piso = cargar("1", 15000)
    pp = piso["pagos"]["matriz_peque"]
    check(abs(pp["pago"] - piso["precio"] * 15000) <= max(1e-12, piso["precio"] * 15000 * 1e-6),
          f"con suelo de 1 segundo de GPU la tarea paga {pp['pago']:.6f} KIA "
          f"(antes {p['matriz_peque']['pago']:.12f})")
    check(pp["pago"] >= 0.0001,
          f"y ya supera el minimo transferible de 0,0001 KIA: {pp['pago']:.6f}")

    print()
    if fallos:
        print(f"{len(fallos)} FALLO(S)")
        return 1
    print("TODO OK: el ancla cobra y reparte sin romper minero ni cliente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
