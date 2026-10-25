#!/usr/bin/env python3
"""Prueba del PAGO POR POTENCIA DEMOSTRADA (22-10-2026).

Que comprueba, contra un Core de verdad cargado en una carpeta temporal
(no toca el ledger real):

  1. APAGADO (por defecto): dos tareas de tamano muy distinto cobran lo mismo.
     Es el comportamiento de siempre: tarifa plana por tarea.
  2. ENCENDIDO: el pago es proporcional a los GFLOP de la tarea.
  3. SUELO: un checksum (0 FLOPs) cobra lo que valdria GFLOP_MIN_FACTURABLE,
     no cero. Es el "fijo pequeño por tarea" (servicios).
  4. TECHO: ningun pago supera lo que queda por emitir (100M intacto).
  5. Si la red entrega el doble de GFLOP/min, el precio por GFLOP baja a la
     mitad: la emision total no se dispara.

Uso:
  python tools/prueba_pago_potencia.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
GFLOP_MIN = 0.0   # sin suelo: pago estrictamente proporcional al trabajo

# El runner se ejecuta DENTRO de la carpeta temporal: asi las claves del sello,
# la base de datos y los ficheros de estado que crea el Core se quedan ahi y no
# ensucian el ledger real.
RUNNER = r'''
import json, os, sys, time
sys.path.insert(0, os.path.join(os.getcwd(), "coordinator"))
sys.path.insert(0, os.getcwd())
import kukia_core as C

def flops(tipo, params):
    return C._flops_tarea(tipo, params)

def sembrar(n, tipo, params, fin=None):
    """Tareas ya verificadas para que el Core tenga una medida de red.

    `fin` en texto ctime: sirve para sembrar en el MINUTO ANTERIOR, que es el
    que manda para el precio del minuto en curso.
    """
    ahora = fin or time.ctime()
    with C._escritura():
        for _ in range(n):
            C.con.execute("INSERT INTO tareas(id,tipo,params,estado,creado,fin) "
                          "VALUES(?,?,?,'ok',?,?)",
                          (os.urandom(6).hex(), tipo, json.dumps(params), ahora, ahora))

def precio_del_minuto():
    """Precio unico del minuto en curso, recalculado desde cero.

    Se tira la cache a proposito: el precio se fija al empezar el minuto y sin
    esto no habria forma de ver que el trabajo sembrado lo cambia.
    """
    C._precio_minuto["minuto"] = -1
    C._precio_minuto["datos"] = None
    return C.precio_unico_minuto()

def ancla_minuto_anterior():
    """Marca de tiempo dentro del minuto anterior al actual."""
    return time.ctime((int(time.time() // 60) - 1) * 60 + 30)

def bolsa_nueva():
    """Deja la bolsa del minuto a cero.

    Desde el 23-10-2026 TODO pago pasa por la bolsa, tambien el plano, asi que
    sin reiniciarla la segunda tarea de una prueba cobra 0: el minuto ya se
    gasto con la primera. Para comparar "cuanto pide cada tarea" hay que darle
    a cada una su minuto limpio.
    """
    C._minuto_actual["minuto"] = -1
    C._minuto_actual["pagado"] = 0.0
    C._minuto_actual["credito"] = 0.0
    C._precio_minuto["minuto"] = -1
    C._precio_minuto["datos"] = None

def medida():
    C._precio_cache["ts"] = 0.0
    C._precio_cache["datos"] = None
    return C.precio_gflop()

DIF = {"n": 1024, "pasos": 1907, "seed": 7, "tolerancia_rel": 1e-9}
MAT_GRANDE = {"n": 1000, "seed": 3}
MAT_PEQUE = {"n": 64, "seed": 3}
CHK = {"data": "kukia-prueba"}
INF = {"n": 149, "seed": 5}

salida = {"flag": bool(C.PAGO_POR_FLOP),
          "gflop_min_facturable": C.GFLOP_MIN_FACTURABLE,
          "flops": {k: flops(t, p) / 1e9 for k, (t, p) in {
              "difusion": ("difusion_calor", DIF),
              "matriz_grande": ("matriz", MAT_GRANDE),
              "matriz_pequena": ("matriz", MAT_PEQUE),
              "checksum": ("checksum", CHK),
              "inferencia": ("inferencia", INF)}.items()}}

# Medida de red: 40 difusiones verificadas ahora mismo.
sembrar(40, "difusion_calor", DIF)
p1 = medida()
salida["precio_1x"] = p1
salida["pagos_1x"] = {}
for k, (t, p) in {"difusion": ("difusion_calor", DIF),
                  "matriz_grande": ("matriz", MAT_GRANDE),
                  "matriz_pequena": ("matriz", MAT_PEQUE),
                  "checksum": ("checksum", CHK),
                  "inferencia": ("inferencia", INF)}.items():
    bolsa_nueva()
    pago, info = C.pago_de_tarea({"tipo": t, "params": json.dumps(p)}, 1.0)
    salida["pagos_1x"][k] = {"kuki": pago, "info": info}
salida["presupuesto"] = C.presupuesto_por_minuto()

salida["restante"] = C.MAX_SUPPLY - C.supply_emitido()
salida["techo"] = C.REWARD_BASE * C.emission_mult()

# El doble de trabajo en la VENTANA de 10 min -> el estimador baja.
if C.PAGO_POR_FLOP:
    sembrar(40, "difusion_calor", DIF)
    p2 = medida()
    salida["precio_2x"] = p2
    pago3, _ = C.pago_de_tarea({"tipo": "difusion_calor", "params": json.dumps(DIF)}, 1.0)
    salida["pago_difusion_2x"] = pago3

# El doble de trabajo en el MINUTO ANTERIOR -> el precio de este minuto baja a
# la mitad. Es la prueba del precio unico por minuto: mismo trabajo, mismo KIA,
# y si la red calcula el doble el KIA por tarea se parte.
if C.PAGO_POR_FLOP:
    for intento in range(3):
        ancla = ancla_minuto_anterior()
        minuto = int(time.time() // 60)
        sembrar(40, "difusion_calor", DIF, ancla)
        pm1 = precio_del_minuto()
        if pm1.get("fuente") != "minuto anterior":
            continue                      # el minuto giro: se repite
        p1, _ = C.pago_de_tarea({"tipo": "difusion_calor", "params": json.dumps(DIF)}, 1.0)
        sembrar(40, "difusion_calor", DIF, ancla)   # el doble en ese minuto
        if int(time.time() // 60) != minuto:
            continue                      # giro entre las dos medidas
        pm2 = precio_del_minuto()
        p2, _ = C.pago_de_tarea({"tipo": "difusion_calor", "params": json.dumps(DIF)}, 1.0)
        salida["precio_minuto_1x"] = pm1
        salida["precio_minuto_2x"] = pm2
        salida["pago_minuto_1x"] = p1
        salida["pago_minuto_2x"] = p2
        break

print("RESULTADO:" + json.dumps(salida))
'''


def cargar(flag) -> dict:
    """Carga el Core en una carpeta temporal con el interruptor dado.

    flag=None -> no se define la variable: se prueba lo que hace POR DEFECTO.
    """
    tmp = Path(tempfile.mkdtemp(prefix="kukia_pago_"))
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
            KUKIA_GFLOP_MIN=str(GFLOP_MIN),
            # Esta suite prueba el reparto por TRABAJO MEDIDO (el precio sale
            # de los GFLOP del minuto anterior). Desde el 25-10-2026 el modo por
            # defecto es el ancla GPU-minuto, que fija el precio y no depende de
            # la medida, asi que aqui se apaga a proposito. El ancla tiene su
            # propia prueba en tools/prueba_ancla.py.
            KUKIA_ANCLA_GPU_MIN="0",
            PYTHONIOENCODING="utf-8",
        )
        if flag is not None:
            entorno["KUKIA_PAGO_POR_FLOP"] = flag
        r = subprocess.run([sys.executable, "runner.py"], cwd=str(tmp), env=entorno,
                           capture_output=True, text=True, timeout=180)
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

    # --- 0. por defecto tiene que estar ENCENDIDO ---------------------------
    auto = cargar(None)
    check(auto["flag"] is True and auto["precio_1x"].get("modo") == "potencia",
          "por defecto el pago va por potencia (sin definir la variable)")

    # --- 1. apagado: tarifa plana, pague lo que pague la tarea ---------------
    off = cargar("0")
    p = off["pagos_1x"]
    check(not off["flag"], "con KUKIA_PAGO_POR_FLOP=0 el modo es plano")
    check(abs(p["difusion"]["kuki"] - p["checksum"]["kuki"]) < 1e-9,
          f"plano: difusion ({p['difusion']['kuki']:.6f}) y checksum "
          f"({p['checksum']['kuki']:.6f}) cobran igual")
    check(p["difusion"]["kuki"] > 0, "plano: se sigue pagando algo")

    # --- 2. encendido: proporcional a los GFLOP -----------------------------
    on = cargar("1")
    p = on["pagos_1x"]
    precio = on["precio_1x"].get("precio_gflop")
    check(on["flag"] and on["precio_1x"].get("modo") == "potencia",
          f"con el interruptor activo el modo es potencia (precio {precio:.9f} KIA/GFLOP)")
    check(precio and precio > 0, "el precio por GFLOP es positivo")

    f = on["flops"]
    ratio_flops = f["difusion"] / f["matriz_grande"]
    ratio_pago = p["difusion"]["kuki"] / p["matriz_grande"]["kuki"]
    check(abs(ratio_pago / ratio_flops - 1) < 0.01,
          f"el pago sigue a los FLOP: difusion/matriz = {ratio_flops:.2f}x en GFLOP "
          f"y {ratio_pago:.2f}x en KIA")

    # --- 3. sin suelo: el que no tiene potencia cobra menos ------------------
    check(p["checksum"]["kuki"] == 0.0,
          f"checksum (0 FLOPs) cobra 0 KIA: {p['checksum']['kuki']:.9f}")
    check(p["checksum"]["info"].get("suelo_servicios") is False,
          "checksum no recibe ningun minimo por tarea")
    check(0 < p["inferencia"]["kuki"] < p["matriz_pequena"]["kuki"] < p["matriz_grande"]["kuki"],
          "inferencia < matriz pequena < matriz grande, en ese orden y todos > 0")
    ratio_peq = p["matriz_pequena"]["kuki"] / p["matriz_grande"]["kuki"]
    ratio_flops_peq = f["matriz_pequena"] / f["matriz_grande"]
    check(abs(ratio_peq / ratio_flops_peq - 1) < 0.01,
          f"una matriz pequena cobra lo que pesa: {ratio_peq:.4f}x en KIA "
          f"vs {ratio_flops_peq:.4f}x en GFLOP")

    # --- 4. techo de emision ------------------------------------------------
    check(all(v["kuki"] <= on["restante"] + 1e-9 for v in p.values()),
          "ningun pago supera lo que queda por emitir")
    check(all(v["kuki"] <= on["techo"] + 1e-9 for v in p.values()),
          f"ningun pago supera el techo por tarea ({on['techo']:.2f} KIA)")

    # --- 5. mas potencia no dispara la emision ------------------------------
    # Con el precio unico por minuto, lo que se calcula AHORA no cambia el
    # precio de ahora (eso es a proposito: misma tarea, mismo KIA). Lo que
    # cambia es el precio del minuto siguiente, que se calcula con el trabajo
    # del minuto anterior.
    pm1 = on.get("precio_minuto_1x") or {}
    pm2 = on.get("precio_minuto_2x") or {}
    check(pm1.get("fuente") == "minuto anterior" and pm1.get("precio"),
          f"el precio del minuto sale del trabajo del minuto anterior "
          f"({pm1.get('precio')})")
    if pm1.get("precio") and pm2.get("precio"):
        ratio = pm2["precio"] / pm1["precio"]
        check(abs(ratio - 0.5) < 0.02,
              f"si el minuto anterior calcula el doble, el precio de este minuto "
              f"baja a la mitad ({pm1['precio']:.9f} -> {pm2['precio']:.9f}, x{ratio:.3f})")
        check(abs(on["pago_minuto_2x"] - on["pago_minuto_1x"] / 2) < 1e-6,
              f"y el pago de la misma difusion se parte "
              f"({on['pago_minuto_1x']:.6f} -> {on['pago_minuto_2x']:.6f})")
    # El estimador de 10 min (pago_por_potencia) si reacciona al instante;
    # sigue publicado, pero NO es el precio con el que se paga.
    check(on["precio_2x"].get("precio_gflop", 0) < on["precio_1x"].get("precio_gflop", 0),
          f"el estimador de 10 min baja al doble de trabajo "
          f"({on['precio_1x'].get('precio_gflop', 0):.9f} -> "
          f"{on['precio_2x'].get('precio_gflop', 0):.9f})")

    # --- 6. la emision por minuto cuadra con el calendario de 2150 ----------
    pr = on["precio_1x"]
    if pr.get("modo") == "potencia":
        emitido = pr["precio_gflop"] * pr["gflop_por_min"]
        objetivo = pr["objetivo_kia_min"]
        check(objetivo > 0 and abs(emitido / objetivo - 1) < 0.01,
              f"precio x GFLOP facturables/min = emision objetivo "
              f"({emitido:.6f} vs {objetivo:.6f} KIA/min)")

    print()
    if fallos:
        print(f"[prueba] {len(fallos)} FALLO(S)")
        return 1
    print("[prueba] todo OK")
    return 0


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
