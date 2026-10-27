#!/usr/bin/env python3
"""Cuadre del precio y de la emision hasta 2150, con TODO tipo de trabajo.

Pregunta: con la definicion de hoy (1 KIA = 1 minuto de GPU), el suelo de
0,0001 KIA por tarea y el tope del minuto, ¿se agota el suministro el
13-04-2150? ¿Y si la red hace videos, inferencia de LLM, embeddings o calculo
pesado en vez de matrices de milisegundos?

Las tres reglas que se simulan son las que corren hoy en el Core:

  1. Precio definido: 1 KIA = `f` minutos de GPU (hoy f = 1), o sea
     precio = f / 900.000 KIA por GFLOP (GPU de referencia: 15.000 GFLOP/s).
  2. Tope del minuto: solo se emite `presupuesto = (100M - emitido) / minutos
     que faltan hasta 2150`, con fusible 1x. Se puede quedar corto, nunca
     pasarse.
  3. Suelo por tarea: ninguna paga menos de 0,0001 KIA.

Lo que sale:

  - Por cada perfil de demanda (videos, LLM, la red de hoy, crecimiento), el
    KIA emitido hasta 2150 con f = 1, si se llega al techo y cuando.
  - El `f` que cuadra ese perfil EXACTAMENTE en 2150 (o sea, cuantos minutos de
    GPU tendria que valer 1 KIA con esa demanda).
  - Cuanto cobra de verdad el minero por minuto de GPU cuando hay mas demanda
    que presupuesto (el fusible): ahi el "1 KIA = 1 minuto" deja de cumplirse.

Uso:
  python tools/cuadre_2150.py
  python tools/cuadre_2150.py --f 1 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

MAX_SUPPLY = 100_000_000.0
FIN = date(2150, 4, 13)
HOY = date(2026, 10, 25)
# Lo que hay emitido hoy (Core en vivo, 25-10-2026 16:00). El fundador ya esta
# dentro de estos 15,088M; el techo son 100M.
EMITIDO_HOY = 15_088_152.28
GPU_REF_GFLOPS = 15_000.0                 # RTX 5070 medida en esta maquina
GFLOP_POR_MIN_GPU = GPU_REF_GFLOPS * 60.0  # 900.000 GFLOP = 1 minuto de GPU
SUELO_KIA = 0.0001                         # suelo por tarea (lo transferible)
DIAS_MES = 30.44
MINUTOS_MES = DIAS_MES * 24 * 60.0

# ---------------------------------------------------------------------------
#  PERFILES DE DEMANDA
#  Cada perfil dice cuantos MINUTOS DE GPU se entregan por minuto de reloj.
#  1,0 = una GPU de referencia calculando el 100 % del tiempo, para siempre.
# ---------------------------------------------------------------------------


def _lineal(a: float, b: float, desde: int, hasta: int):
    """De `a` a `b` (GPU-min/min) entre dos anos, y plano despues."""
    def f(anio: float) -> float:
        if anio <= desde:
            return a
        if anio >= hasta:
            return b
        return a + (b - a) * (anio - desde) / (hasta - desde)
    return f


def _exponencial(inicio: float, doble_anios: float, tope: float):
    """Crece sin parar hasta `tope` (GPU-min/min)."""
    def f(anio: float) -> float:
        if anio <= HOY.year:
            return inicio
        factor = 2.0 ** ((anio - HOY.year) / doble_anios)
        return min(tope, inicio * factor)
    return f


PERFILES = {
    # La red de hoy, medida: 0,0233 KIA/min con el suelo (230 tareas/min).
    "hoy (medido 25-10)": (lambda a: 0.0233, "matrices e inferencia de milisegundos"),
    # Videos: uno de 5 minutos de GPU al dia.
    "1 video de 5 min al dia": (lambda a: 5.0 / 1440.0, "video corto, 5 GPU-min por pieza"),
    # Servicio de video de verdad: 50 videos de 5 min al dia.
    "50 videos de 5 min al dia": (lambda a: 250.0 / 1440.0, "un pequeno estudio de video"),
    # Una GPU al 30 % sirviendo inferencia (LLM, imagenes, embeddings).
    "1 GPU al 30 %": (lambda a: 0.30, "una tarjeta con trafico real"),
    # Crecimiento moderado: de 0,1 a 30 GPU-min/min (30 GPUs al 100 %) en 2045.
    "crecimiento (0,1 -> 30)": (_lineal(0.1, 30.0, 2026, 2045), "la red crece 20 anos"),
    # El tope del calendario: lo maximo que la emision puede pagar.
    "capacidad del calendario": (lambda a: 1.31059, "1,31 GPUs a tope, sin parar"),
}


def simular(demanda, f: float, emitido: float = EMITIDO_HOY,
            inicio: date = HOY) -> dict:
    """Emite mes a mes hasta 2150 con la regla del Core. Devuelve el resumen."""
    s = float(emitido)
    t = inicio
    meses = 0
    kia_total = 0.0
    gpu_min_total = 0.0
    minuto_con_fusible = 0
    kia_con_fusible = 0.0
    gpu_min_con_fusible = 0.0
    fin_alcanzado = None
    topeado = False
    kia_mes = 0.0
    while t < FIN and s < MAX_SUPPLY:
        dias = (FIN - t).days
        if dias <= 0:
            break
        presupuesto = (MAX_SUPPLY - s) / (dias * 1440.0)     # KIA por minuto
        anio = t.year + (t.timetuple().tm_yday - 1) / 365.25
        gpu_min = max(0.0, float(demanda(anio)))             # GPU-min por minuto
        demanda_kia = gpu_min * f
        emision = min(demanda_kia, presupuesto)
        # Si la demanda iguala o pasa el tope, el sistema va pegado al
        # calendario: en tiempo continuo S(t) = MAX - (MAX-S0)(T-t)/(T-t0), que
        # llega a 100M EXACTAMENTE en 2150. Con pasos mensuales se queda a unas
        # milesimas de KIA, asi que ese caso se marca y se cierra a mano.
        topeado = demanda_kia >= presupuesto and presupuesto > 0
        if topeado:
            minuto_con_fusible += 1
            kia_con_fusible += presupuesto * MINUTOS_MES
            gpu_min_con_fusible += gpu_min * MINUTOS_MES
        kia_mes = emision * MINUTOS_MES
        s += kia_mes
        kia_total += kia_mes
        gpu_min_total += gpu_min * MINUTOS_MES
        meses += 1
        t = date(t.year + (t.month + 1 - 1) // 12,
                 (t.month % 12) + 1, 1)
        if s >= MAX_SUPPLY - 1e-6:
            fin_alcanzado = t
            break
    if fin_alcanzado is None and topeado:
        # La ultima KIA cae justo el 13-04-2150 (el tope la pone ahi).
        fin_alcanzado = FIN
        s = MAX_SUPPLY
    # Si aun sobra suministro, a que ano se llegaria manteniendo el ultimo
    # ritmo de emision (la demanda de despues de 2150 se congela).
    fin_estimado_anio = None
    if fin_alcanzado is None and kia_mes > 0:
        restante = MAX_SUPPLY - min(s, MAX_SUPPLY)
        meses_faltan = restante / kia_mes
        fin_estimado_anio = t.year + meses_faltan / 12.0
    return {
        "emitido_2150": min(s, MAX_SUPPLY),
        "kia_emitidos": kia_total,
        "restante_2150": max(0.0, MAX_SUPPLY - min(s, MAX_SUPPLY)),
        "gpu_min_total": gpu_min_total,
        "fin": fin_alcanzado.isoformat() if fin_alcanzado else None,
        "fin_estimado_anio": fin_estimado_anio,
        "meses": meses,
        "meses_con_fusible": minuto_con_fusible,
        "kia_perdido_por_fusible": max(0.0, gpu_min_total * f - kia_total),
        "kia_por_gpu_min_efectivo": (kia_total / gpu_min_total) if gpu_min_total else None,
    }


def f_que_cuadra(demanda, emitido: float = EMITIDO_HOY) -> float | None:
    """`f` (KIA por minuto de GPU) que agota los 100M justo el 13-04-2150.

    Sin tope, el KIA emitido es `f` x (minutos de GPU entregados), asi que el
    precio que cuadra es el que reparte lo que falta entre TODO el trabajo que
    se va a entregar hasta 2150:

        f = (100M - emitido) / minutos de GPU del horizonte

    El trabajo del horizonte no depende del precio (la demanda es del mundo,
    no del Core), asi que sale de la propia simulacion. Si con ese `f` algun
    mes pide mas que el tope, el tope recorta y la fecha se cumple igual: se
    avisa en la columna de meses topados.
    """
    r = simular(demanda, 0.0, emitido)          # solo cuenta GPU-minutos
    if r["gpu_min_total"] <= 0:
        return None
    return (MAX_SUPPLY - emitido) / r["gpu_min_total"]


def num(n, dec=2):
    if n is None:
        return "—"
    return f"{n:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


# Coste de trabajos reales con la definicion de hoy (f = 1).
# Los FLOPs son cuentas de manual de cada modelo; los minutos de GPU, medidos en
# esta maquina o publicados por el fabricante. El numero que manda es el KIA.
TRABAJOS = [
    ("checksum / tarea vacia", 0.0, "0 GFLOP"),
    ("embeddings 1M tokens (0,3B)", 600.0, "2 x 0,3e9 x 1e6"),
    ("inferencia 7B, 500 tokens", 7_000.0, "2 x 7e9 x 500"),
    ("inferencia 70B, 1.000 tokens", 140_000.0, "2 x 70e9 x 1000"),
    ("matriz 800 x 800", 1.024, "2 n^3"),
    ("imagen SDXL 1024, 30 pasos", 90_000.0, "~6 s de GPU x 15.000 GFLOP/s"),
    ("video 5 s 720p (14B)", 4_500_000.0, "~5 min de GPU x 15.000 GFLOP/s"),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Cuadre del precio hasta 2150")
    ap.add_argument("--f", type=float, default=1.0,
                    help="KIA por minuto de GPU (hoy: 1)")
    ap.add_argument("--emitido", type=float, default=EMITIDO_HOY)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    faltan = MAX_SUPPLY - args.emitido
    minutos_restantes = (FIN - HOY).days * 1440.0
    capacidad = faltan / minutos_restantes

    print("=" * 78)
    print("CUADRE DEL PRECIO Y LA EMISION HASTA 2150")
    print("=" * 78)
    print(f"  emitido hoy           {num(args.emitido)} KIA")
    print(f"  falta por emitir      {num(faltan)} KIA")
    print(f"  minutos hasta 2150    {num(minutos_restantes, 0)} ({num(minutos_restantes/525960, 1)} anos)")
    print(f"  tope del minuto       {num(capacidad, 6)} KIA/min = {num(capacidad, 2)} minutos de GPU por minuto")
    print(f"  GPU-minutos que faltan {num(faltan, 0)} (a 1 KIA = 1 minuto de GPU)")
    print(f"  suelo por tarea       {num(SUELO_KIA, 4)} KIA = {
        num(SUELO_KIA * GFLOP_POR_MIN_GPU, 0)} GFLOP = 6 ms de GPU de referencia")
    print()

    print("1) QUE CUESTA CADA TRABAJO CON 1 KIA = 1 MINUTO DE GPU")
    print(f"   {'trabajo':32} {'GFLOP':>14} {'KIA':>14}   nota")
    for nombre, gflop, nota in TRABAJOS:
        kia = max(gflop / GFLOP_POR_MIN_GPU, SUELO_KIA)
        print(f"   {nombre:32} {num(gflop, 0):>14} {num(kia, 8):>14}   {nota}")
    print()

    print("2) CADA PERFIL DE DEMANDA CON EL PRECIO DE HOY (1 KIA = 1 minuto de GPU)")
    print(f"   {'perfil':28} {'KIA/dia':>12} {'emitido 2150':>16} {'restante':>14} {'se agota':>14}")
    filas = []
    for nombre, (demanda, nota) in PERFILES.items():
        r = simular(demanda, args.f, args.emitido)
        kia_dia = demanda(HOY.year) * args.f * 1440.0
        if r["fin"]:
            cuando = r["fin"][:7]
        elif r["fin_estimado_anio"]:
            cuando = "ano %.0f" % r["fin_estimado_anio"]
        else:
            cuando = "nunca"
        print(f"   {nombre:28} {num(kia_dia, 2):>12} {num(r['emitido_2150'], 0):>16} "
              f"{num(r['restante_2150'], 0):>14} {cuando:>14}")
        filas.append((nombre, nota, r, kia_dia))
    print()

    print("3) QUE PRECIO CUADRA CADA PERFIL EN 2150 (reparte los 84,9M entre su trabajo)")
    print(f"   {'perfil':28} {'GPU-min horizonte':>18} {'1 GPU-min = ... KIA':>21} {'1 KIA = ... min':>16} {'% del tope':>11}")
    for nombre, (demanda, nota) in PERFILES.items():
        f = f_que_cuadra(demanda, args.emitido)
        if f is None:
            print(f"   {nombre:28} {'sin trabajo':>18}")
            continue
        gpu = simular(demanda, 0.0, args.emitido)["gpu_min_total"]
        uso = 100.0 * (gpu / minutos_restantes) / capacidad
        print(f"   {nombre:28} {num(gpu, 0):>18} {num(f, 4):>21} {num(1.0 / f, 4):>16} "
              f"{num(uso, 1):>11}")
    print()

    print("4) SI LA RED CRECE MAS QUE EL TOPE: lo que cobra de verdad")
    print(f"   {'perfil':28} {'KIA/GPU-min':>14} {'% del precio':>14} {'meses topados':>14}")
    for nombre, (demanda, nota) in PERFILES.items():
        r = simular(demanda, args.f, args.emitido)
        if r["meses_con_fusible"] == 0:
            continue
        efectivo = r["kia_por_gpu_min_efectivo"]
        print(f"   {nombre:28} {num(efectivo, 4):>14} {num(100 * efectivo / args.f, 1):>14} "
              f"{num(r['meses_con_fusible'] / 12, 0):>14}")
    print()

    if args.json:
        print(json.dumps({n: simular(d, args.f, args.emitido)
                          for n, (d, _) in PERFILES.items()}, indent=2, default=str))
    print("Leelo asi: si la red entrega MENOS de 1,31 minutos de GPU por minuto, la")
    print("ultima KIA llega DESPUES de 2150 (la fecha solo se cumple a tope). Si")
    print("entrega MAS, el tope recorta y cada GPU-minuto cobra menos de 1 KIA.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
