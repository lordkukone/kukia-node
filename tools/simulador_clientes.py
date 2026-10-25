#!/usr/bin/env python3
"""Simulador: con clientes REALES (X GFLOP al mes a Y euros), que precio sale por KIA.

Que es y que no es
------------------
Los numeros del Core (emision, suministro, precio interno del GFLOP, capacidad)
se LEEN, no se suponen. Lo que se supone es tu precio de venta y el volumen de
cada cliente, y se cambia por argumentos: cada supuesto sale impreso.

NO pone precio a KIA. Contesta a: "si estos clientes pagan esto por este
trabajo, cuanto sale 1 KIA y cuanto hardware hace falta para servirlo".

Dos cuentas distintas, y las dos importan:
  A) Ancla de emision: el dinero del mes repartido entre los KIA que se emiten
     ese mes. Es la que dice "cuanto valdria" si todo el ingreso fuese a los
     mineros.
  B) Precio implicito del trabajo: lo que el cliente paga por GFLOP dividido
     entre los KIA que la red cobra por GFLOP. Dice "a que precio por KIA
     cuadraria cobrar el trabajo".
  Con demanda pequeña A se hunde (se emite por tiempo, no por trabajo) y B no:
  por eso se ensenan las dos.

Uso
---
  python tools/simulador_clientes.py
  python tools/simulador_clientes.py --clientes 5 --gflop-mes 1000000 --eur-mes 150
  python tools/simulador_clientes.py --clientes 20 --gflop-mes 1e7 --eur-gflop 0.0002
  python tools/simulador_clientes.py --gflops-gpu 15000 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from calculadora_valor import (CORE_POR_DEFECTO, datos_del_core, eur,  # noqa: E402
                               gflop, leer_core, num, valor_por_kia)

DIAS_MES = 30.44
SEG_MES = DIAS_MES * 86400.0

# Escenarios de partida: (nombre, clientes, GFLOP/mes cada uno, EUR/mes cada uno)
#
# Por que estos numeros: un cliente no compra "GFLOP", compra HORAS de grafica.
# Una 5070 a 15.000 GFLOP/s hace 5,4e16 FLOP en una hora = 54 PFLOP. A 0,35
# EUR/h, 10 horas son 3,50 EUR de coste y ~20 EUR de factura minima razonable.
# Los escenarios van de 10 h de GPU al mes a llenar una grafica entera (730 h).
H_GPU_GFLOP = 54e6           # GFLOP que hace una 5070 en una hora (15 TFLOP/s)
ESCENARIOS = [
    ("1 cliente de prueba (10 h GPU)",        1,  10 * H_GPU_GFLOP,    20.0),
    ("5 clientes (50 h GPU cada uno)",        5,  50 * H_GPU_GFLOP,   150.0),
    ("20 clientes (200 h GPU cada uno)",     20, 200 * H_GPU_GFLOP,   500.0),
    ("100 clientes (7,3 h, llena 1 GPU)",   100, 7.3 * H_GPU_GFLOP,    25.0),
]

OBJETIVOS_EUR_KIA = (0.0001, 0.001, 0.01, 0.1)


def precio_implicito(eur_por_gflop: float, gflop_por_kia: float) -> float:
    """EUR por KIA que sale de lo que se cobra por GFLOP, al precio interno."""
    if gflop_por_kia <= 0:
        return 0.0
    return eur_por_gflop / gflop_por_kia


def escenario(d: dict, nombre: str, clientes: int, gflop_mes: float,
              eur_mes: float | None, eur_gflop: float | None,
              gflops_gpu: float) -> dict:
    total_gflop = clientes * gflop_mes
    if eur_gflop is not None:
        ingresos = total_gflop * eur_gflop
    else:
        ingresos = clientes * float(eur_mes or 0.0)
    por_gflop = (ingresos / total_gflop) if total_gflop > 0 else 0.0
    kia_mes = d["emision_dia"] * DIAS_MES

    # A) ancla de emision: todo el ingreso entre los KIA emitidos ese mes.
    ancla = valor_por_kia(total_gflop / DIAS_MES, por_gflop, d["emision_dia"]) \
        if d["emision_dia"] > 0 else 0.0

    # B) precio implicito del trabajo (€/GFLOP cobrado / KIA por GFLOP).
    implicito = precio_implicito(por_gflop, d["gflop_por_kia"])

    # Capacidad para servirlo: GFLOP/mes -> GFLOP/s -> GPUs y horas de GPU.
    gflop_s = total_gflop / SEG_MES
    gpus = (gflop_s / gflops_gpu) if gflops_gpu > 0 else 0.0
    horas_gpu = (total_gflop / (gflops_gpu * 3600.0)) if gflops_gpu > 0 else 0.0
    # Se compara POR DIA: la capacidad del Core es diaria.
    pct_red = ((total_gflop / DIAS_MES) / d["capacidad_gflop_dia"] * 100.0
               if d["capacidad_gflop_dia"] > 0 else 0.0)

    # KIA que haria falta para PAGAR ese trabajo al precio interno, vs los que
    # se emiten de verdad en el mes (que van por tiempo, no por trabajo).
    kia_pago = total_gflop * d["precio_gflop_kia"]
    return {
        "nombre": nombre,
        "clientes": clientes,
        "gflop_mes": total_gflop,
        "ingresos_mes": ingresos,
        "eur_por_gflop": por_gflop,
        "eur_kia_ancla": ancla,
        "eur_kia_trabajo": implicito,
        "kia_que_compra_1_eur": (1.0 / ancla) if ancla > 0 else 0.0,
        "kia_emitido_mes": kia_mes,
        "kia_pago_trabajo": kia_pago,
        "gflop_s": gflop_s,
        "gpus": gpus,
        "horas_gpu": horas_gpu,
        "pct_capacidad_red": pct_red,
        "ingresos_anio": ingresos * 12.0,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Que precio por KIA sale con clientes reales (GFLOP/mes a euros).")
    ap.add_argument("--core", default=CORE_POR_DEFECTO, help="URL del Core")
    ap.add_argument("--clientes", type=int, default=None,
                    help="numero de clientes del escenario a medir")
    ap.add_argument("--gflop-mes", type=float, default=None,
                    help="GFLOP al mes que consume CADA cliente")
    ap.add_argument("--eur-mes", type=float, default=None,
                    help="EUR al mes que paga CADA cliente")
    ap.add_argument("--eur-gflop", type=float, default=None,
                    help="en vez de EUR/mes: precio por GFLOP")
    ap.add_argument("--gflops-gpu", type=float, default=15000.0,
                    help="GFLOP/s efectivos por GPU (supuesto; una 5070 da 15.000)")
    ap.add_argument("--json", action="store_true", help="salida en JSON")
    args = ap.parse_args(argv)

    try:
        crudo = leer_core(args.core)
        d = datos_del_core(crudo)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("  (arranca el Core o pasa --core https://tu-core)", file=sys.stderr)
        return 2

    if d["emision_dia"] <= 0 or d["gflop_por_kia"] <= 0:
        print("ERROR: el Core no da emision o precio del GFLOP; sin eso no hay cuenta",
              file=sys.stderr)
        return 2

    sueltos = []
    if args.clientes is not None:
        sueltos.append(("escenario a medida", args.clientes,
                        args.gflop_mes or 0.0, args.eur_mes))
    filas = [escenario(d, nombre, n, g, e, args.eur_gflop, args.gflops_gpu)
             for nombre, n, g, e in (sueltos or ESCENARIOS)]

    if args.json:
        print(json.dumps({"core": d, "escenarios": filas}, indent=2, ensure_ascii=False))
        return 0

    print()
    print("  SIMULADOR DE CLIENTES -> PRECIO POR KIA Y CAPACIDAD NECESARIA")
    print("  " + "=" * 74)
    print("\n  DEL CORE (leido, no supuesto)")
    print(f"    emision ................ {num(d['emision_por_min'], 4)} KIA/min = "
          f"{num(d['emision_dia'])} KIA/dia")
    print(f"    suministro emitido ..... {num(d['supply'], 1)} de "
          f"{num(d['max_supply'])} KIA")
    print(f"    lo que cuesta el trabajo {num(d['precio_gflop_kia'], 8)} KIA/GFLOP "
          f"-> 1 KIA = {num(d['gflop_por_kia'])} GFLOP")
    print(f"    capacidad medida hoy ... {gflop(d['capacidad_gflop_min'])}/min = "
          f"{gflop(d['capacidad_gflop_dia'])}/dia")

    print("\n  SUPUESTOS (mios, cambialos con los argumentos)")
    print(f"    GPU efectiva ........... {num(args.gflops_gpu)} GFLOP/s "
          f"({gflop(args.gflops_gpu * SEG_MES)} al mes si va a tope)")
    if args.eur_gflop is not None:
        print(f"    precio de venta ........ {eur(args.eur_gflop)} EUR/GFLOP")
    print(f"    meses de 30,44 dias: {num(d['emision_dia'] * DIAS_MES)} KIA "
          f"emitidos al mes (la emision va por tiempo, no por trabajo)")

    for f in filas:
        print("\n  " + "-" * 74)
        print(f"  {f['nombre'].upper()}")
        print(f"    clientes ............... {num(f['clientes'])} x "
              f"{gflop(f['gflop_mes'] / max(f['clientes'], 1))}/mes")
        print(f"    trabajo servido ........ {gflop(f['gflop_mes'])}/mes "
              f"({num(f['gflop_s'], 2)} GFLOP/s)")
        print(f"    dinero que entra ....... {eur(f['ingresos_mes'])} EUR/mes "
              f"({eur(f['ingresos_anio'])} EUR/anio)")
        print(f"    lo cobrado por GFLOP ... {eur(f['eur_por_gflop'])} EUR/GFLOP")
        print(f"    -> precio por KIA")
        print(f"         ancla de emision .. {eur(f['eur_kia_ancla'])} EUR/KIA "
              f"(1 EUR = {num(f['kia_que_compra_1_eur'])} KIA)")
        print(f"         precio del trabajo  {eur(f['eur_kia_trabajo'])} EUR/KIA")
        print(f"    capacidad necesaria .... {num(f['horas_gpu'], 1)} h de una GPU "
              f"al mes = {num(f['gpus'], 4)} GPU enteras")
        if f["gpus"] > 0:
            print(f"         eso es ............ {num(f['gpus'] * 100, 2)} % de UNA 5070")
        print(f"    frente a la red de hoy . {num(f['pct_capacidad_red'], 1)} % de la "
              f"capacidad que hay ahora mismo")
        print(f"    KIA para pagar el trabajo {num(f['kia_pago_trabajo'])} KIA/mes; "
              f"se emiten {num(f['kia_emitido_mes'])} KIA/mes")

    print("\n  " + "=" * 74)
    print("  QUE HARIA FALTA PARA QUE 1 KIA VALGA...")
    print("    (con lo que paga el cliente por GFLOP, el trabajo que habria que vender)")
    print(f"    {'EUR/KIA':>12}  {'TRABAJO/MES':>16}  {'H DE 1 GPU':>12}  "
          f"{'% RED HOY':>10}")
    for objetivo in OBJETIVOS_EUR_KIA:
        # GFLOP al mes que harian falta: ingresos = objetivo * KIA_mes y
        # GFLOP = ingresos / (EUR por GFLOP). Con el precio del primer escenario.
        ref = filas[0]
        if ref["eur_por_gflop"] <= 0:
            continue
        kia_mes = d["emision_dia"] * DIAS_MES
        ingresos = objetivo * kia_mes
        gflop_mes = ingresos / ref["eur_por_gflop"]
        horas = (gflop_mes / (args.gflops_gpu * 3600.0)
                 if args.gflops_gpu > 0 else 0.0)
        pct = ((gflop_mes / DIAS_MES) / d["capacidad_gflop_dia"] * 100.0
               if d["capacidad_gflop_dia"] > 0 else 0.0)
        print(f"    {eur(objetivo):>12}  {gflop(gflop_mes):>16}  {num(horas, 0):>12}  "
              f"{num(pct, 0) + ' %':>10}")

    print("\n  LECTURA HONESTA")
    print("    - El precio por KIA sale de dividir dinero real entre KIA emitidos.")
    print("      Si no hay clientes, la division da cero. No hay suelo.")
    print("    - La emision va por TIEMPO (KIA/min fijos), no por trabajo: con poca")
    print("      demanda se emite KIA que no paga nada, y eso diluye el precio.")
    print("    - Una sola GPU hace 15.000 GFLOP/s = 39 EFLOP/mes a tope (730 h).")
    print("      La red de HOY (23 GFLOP/s) es el 0,15 % de una sola grafica:")
    print("      el cuello no es el precio, es que no hay trabajo que servir.")
    print("    - Nada de esto es una promesa ni una cotizacion: es aritmetica con")
    print("      los supuestos a la vista.")
    print()
    return 0


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
