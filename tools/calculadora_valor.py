#!/usr/bin/env python3
"""Calculadora: cuanto valdria 1 KIA segun la demanda REAL de computo.

Que hace y que NO hace
----------------------
NO pone precio a KIA. Calcula un **ancla contable**: si la red vendiera X GFLOP
al dia a un precio P por GFLOP, y ese dinero se repartiera entre los KIA que se
emiten ese dia, cada KIA valdria (X*P)/emision_dia. Eso es aritmetica, no una
cotizacion: sin un cliente que pague, el resultado es 0.

Lo que se lee del Core (comprobado, no supuesto) y lo que se supone se imprime
por separado, para que no se mezcle. Los supuestos se cambian con argumentos.

Uso
---
  python tools/calculadora_valor.py
  python tools/calculadora_valor.py --core https://mi-core
  python tools/calculadora_valor.py --eur-hora 0.35 --tflops 15
  python tools/calculadora_valor.py --precio-gflop 0.00001
  python tools/calculadora_valor.py --demanda 5000000
  python tools/calculadora_valor.py --json

  --demanda es en GFLOP por dia y salta la tabla de utilizacion: calcula el
  valor con ESA demanda concreta (la que un cliente te haya dicho de verdad).
"""
import argparse
import json
import sys
import urllib.request

CORE_POR_DEFECTO = "http://127.0.0.1:8300"

# Supuestos de partida. Son MIOS, no del Core: por eso salen en pantalla y se
# pueden cambiar por argumentos. 0,35 EUR/hora por una GPU tipo 5070 que hace
# ~15 TFLOP/s EFECTIVOS (no de pico: en estas tareas el rendimiento real es
# mucho menor que la ficha del fabricante).
EUR_HORA_DEFECTO = 0.35
TFLOPS_DEFECTO = 15.0

# Utilizaciones de la capacidad de la red que se simulan (fraccion 0..1).
UTILIZACIONES = (0.0, 0.01, 0.05, 0.25, 1.0)

# Anclas de valor por KIA para la pregunta inversa (cuanto habria que vender).
OBJETIVOS_EUR = (0.01, 0.05, 0.10, 0.50, 1.00)


def leer_core(base: str) -> dict:
    """Lee /stats del Core. Lanza RuntimeError si no responde o no trae lo necesario."""
    url = base.rstrip("/") + "/stats"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            dato = json.load(r)
    except Exception as e:
        raise RuntimeError(f"no puedo leer {url}: {e}")
    s = dato.get("stats", dato)
    faltan = [k for k in ("supply_emitido", "max_supply") if k not in s]
    if faltan:
        raise RuntimeError("el Core no devuelve " + ", ".join(faltan))
    return s


def datos_del_core(s: dict) -> dict:
    """Saca los numeros que importan, con las tres formas en que el Core los publica."""
    bolsa = s.get("bolsa_emision") or {}
    pago = s.get("pago_por_potencia") or {}
    detalle = s.get("detalle_pago") or {}

    # Emision diaria: se prefiere el presupuesto del minuto (es lo que se emite
    # de verdad); si no esta, se cae a emision_por_min.
    emision_min = bolsa.get("presupuesto_kia_min") or s.get("emision_por_min") or 0.0
    try:
        emision_min = float(emision_min)
    except (TypeError, ValueError):
        emision_min = 0.0

    # Precio interno del trabajo: KIA que cuesta 1 GFLOP verificado.
    precio_gflop_kia = bolsa.get("precio_minuto") or pago.get("precio_gflop") or 0.0
    try:
        precio_gflop_kia = float(precio_gflop_kia)
    except (TypeError, ValueError):
        precio_gflop_kia = 0.0

    # Capacidad medida de la red en GFLOP/min (la ventana que usa el Core).
    gflop_min = (pago.get("gflop_red_por_min") or detalle.get("gflop_por_min")
                 or bolsa.get("gflop_minuto_anterior") or 0.0)
    try:
        gflop_min = float(gflop_min)
    except (TypeError, ValueError):
        gflop_min = 0.0

    supply = float(s.get("supply_emitido") or 0.0)
    maximo = float(s.get("max_supply") or 0.0)
    return {
        "supply": supply,
        "max_supply": maximo,
        "restante": maximo - supply,
        "emision_por_min": emision_min,
        "emision_dia": emision_min * 1440.0,
        "precio_gflop_kia": precio_gflop_kia,
        "gflop_por_kia": (1.0 / precio_gflop_kia) if precio_gflop_kia > 0 else 0.0,
        "capacidad_gflop_min": gflop_min,
        "capacidad_gflop_dia": gflop_min * 1440.0,
        "nodos": s.get("nodos"),
        "sellos": (s.get("integridad") or {}).get("sellos"),
    }


def valor_por_kia(demanda_gflop_dia: float, eur_por_gflop: float,
                  emision_dia: float) -> float:
    """Ancla: ingresos diarios de la red repartidos entre los KIA emitidos ese dia."""
    if emision_dia <= 0:
        raise ValueError("la emision diaria es 0: no hay con que dividir")
    return (demanda_gflop_dia * eur_por_gflop) / emision_dia


def demanda_para(precio_kia: float, eur_por_gflop: float, emision_dia: float) -> float:
    """Demanda diaria (GFLOP) necesaria para que 1 KIA salga a precio_kia."""
    if eur_por_gflop <= 0:
        return float("inf")
    return (precio_kia * emision_dia) / eur_por_gflop


def eur(n: float) -> str:
    """Importe en euros, legible.

    Los numeros de este problema son muy pequenos (millonesimas de euro por
    GFLOP), asi que por debajo de una milesima se usa notacion cientifica con
    3 cifras significativas: 0,00000675 se lee peor que 6,75e-06 y se confunde
    con un cero a la primera.
    """
    if n == 0:
        return "0,00"
    if abs(n) < 0.001:
        return f"{n:.2e}".replace("e-0", "e-").replace("e+0", "e+").replace(".", ",")
    if abs(n) < 1:
        return f"{n:.4f}".replace(".", ",")
    return f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def gflop(n: float) -> str:
    """Cantidad de GFLOP con la unidad que se lea mejor.

    Escalera correcta (base GFLOP): 1.000 GFLOP = 1 TFLOP, 1e6 = 1 PFLOP,
    1e9 = 1 EFLOP. Antes estaba corrida un escalon y 1,96 millones de GFLOP
    salian como "MFLOP", que es 1e-3 GFLOP: un error de mil millones.
    """
    if abs(n) >= 1e9:
        return f"{num(n / 1e9, 2)} EFLOP"
    if abs(n) >= 1e6:
        return f"{num(n / 1e6, 2)} PFLOP"
    if abs(n) >= 1e3:
        return f"{num(n / 1e3, 2)} TFLOP"
    return f"{num(n)} GFLOP"


def num(n: float, dec: int = 0) -> str:
    return f"{n:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Cuanto valdria 1 KIA segun la demanda real de computo "
                    "(ancla contable, no precio de mercado).")
    ap.add_argument("--core", default=CORE_POR_DEFECTO, help="URL del Core")
    ap.add_argument("--eur-hora", type=float, default=EUR_HORA_DEFECTO,
                    help=f"coste de una GPU de referencia en EUR/hora (def. {EUR_HORA_DEFECTO})")
    ap.add_argument("--tflops", type=float, default=TFLOPS_DEFECTO,
                    help=f"TFLOP/s EFECTIVOS de esa GPU, no de pico (def. {TFLOPS_DEFECTO})")
    ap.add_argument("--precio-gflop", type=float, default=None,
                    help="EUR por GFLOP, si lo sabes: salta --eur-hora/--tflops")
    ap.add_argument("--demanda", type=float, default=None,
                    help="GFLOP/dia vendidos de verdad (salta la tabla de utilizacion)")
    ap.add_argument("--json", action="store_true", help="salida en JSON")
    args = ap.parse_args(argv)

    try:
        s = leer_core(args.core)
        d = datos_del_core(s)
    except RuntimeError as e:
        print(f"[error] {e}", file=sys.stderr)
        print("       Arranca el Core o pasa --core <url>. Sin datos del ledger, "
              "cualquier cifra seria inventada.", file=sys.stderr)
        return 2

    # --- supuestos -------------------------------------------------------
    if args.precio_gflop is not None:
        eur_gflop = args.precio_gflop
        origen_precio = "puesto a mano (--precio-gflop)"
    else:
        gflop_por_hora = args.tflops * 1000.0 * 3600.0        # 1 TFLOP/s = 1000 GFLOP/s
        eur_gflop = args.eur_hora / gflop_por_hora if gflop_por_hora > 0 else 0.0
        origen_precio = (f"{args.eur_hora} EUR/hora entre {args.tflops} TFLOP/s efectivos "
                         f"= {num(gflop_por_hora)} GFLOP/hora")

    if eur_gflop <= 0:
        print("[error] el precio por GFLOP sale 0: revisa --eur-hora y --tflops",
              file=sys.stderr)
        return 2
    if d["emision_dia"] <= 0:
        print("[error] el Core dice emision 0 KIA/dia: no hay nada que valorar",
              file=sys.stderr)
        return 2

    filas = []
    if args.demanda is not None:
        demanda = max(0.0, args.demanda)
        filas.append({
            "utilizacion": (demanda / d["capacidad_gflop_dia"]
                            if d["capacidad_gflop_dia"] > 0 else None),
            "demanda_gflop_dia": demanda,
            "ingresos_dia": demanda * eur_gflop,
            "eur_por_kia": valor_por_kia(demanda, eur_gflop, d["emision_dia"]),
            "etiqueta": "demanda indicada",
        })
    else:
        for u in UTILIZACIONES:
            demanda = d["capacidad_gflop_dia"] * u
            filas.append({
                "utilizacion": u,
                "demanda_gflop_dia": demanda,
                "ingresos_dia": demanda * eur_gflop,
                "eur_por_kia": valor_por_kia(demanda, eur_gflop, d["emision_dia"]),
                "etiqueta": f"{num(u * 100)} % de la capacidad de hoy",
            })

    inversa = []
    for objetivo in OBJETIVOS_EUR:
        necesaria = demanda_para(objetivo, eur_gflop, d["emision_dia"])
        inversa.append({
            "eur_por_kia": objetivo,
            "demanda_gflop_dia": necesaria,
            "utilizacion": (necesaria / d["capacidad_gflop_dia"]
                            if d["capacidad_gflop_dia"] > 0 else None),
        })

    if args.json:
        print(json.dumps({
            "core_de": s.get("version"),
            "comprobado": d,
            "supuestos": {
                "eur_por_gflop": eur_gflop,
                "origen": origen_precio,
            },
            "escenarios": filas,
            "demanda_necesaria": inversa,
            "aviso": "Ancla contable, no precio de mercado: sin clientes que paguen, vale 0.",
        }, indent=2, ensure_ascii=False))
        return 0

    # --- salida de pantalla ---------------------------------------------
    print()
    print("  VALOR DE 1 KIA SEGUN LA DEMANDA DE COMPUTO")
    print("  " + "=" * 60)
    print()
    print("  LO QUE DICE EL CORE AHORA MISMO (leido, no supuesto)")
    print(f"    supply emitido .......... {num(d['supply'], 1)} de {num(d['max_supply'])} KIA")
    print(f"    emision ................. {num(d['emision_por_min'], 4)} KIA/min "
          f"= {num(d['emision_dia'])} KIA/dia")
    print(f"    precio interno .......... {d['precio_gflop_kia']:.8f} KIA/GFLOP "
          f"-> 1 KIA = {num(d['gflop_por_kia'])} GFLOP")
    print(f"    capacidad medida ........ {num(d['capacidad_gflop_min'], 1)} GFLOP/min "
          f"= {gflop(d['capacidad_gflop_dia'])}/dia")
    print(f"    eso son ................. {num(d['capacidad_gflop_min'] / 60, 1)} GFLOP/s "
          f"de red (una GPU sola hace {num(args.tflops * 1000)} GFLOP/s)")
    print()
    print("  SUPUESTOS (mios, no del Core; cambialos con --eur-hora / --tflops)")
    print(f"    1 GFLOP cuesta .......... {eur_gflop:.10f} EUR")
    print(f"    de donde sale ........... {origen_precio}")
    print()
    print("  SI LA RED VENDIERA ESO AL DIA, 1 KIA VALDRIA")
    print(f"    {'utilizacion':<34}{'trabajo/dia':>15}{'EUR/dia':>12}{'EUR/KIA':>13}")
    print("    " + "-" * 71)
    for f in filas:
        u = "-" if f["utilizacion"] is None else f"{num(f['utilizacion'] * 100)} %"
        etiqueta = u if args.demanda is None else f["etiqueta"]
        print(f"    {etiqueta:<34}{gflop(f['demanda_gflop_dia']):>15}"
              f"{eur(f['ingresos_dia']):>12}{eur(f['eur_por_kia']):>13}")
    print()
    print("  QUE HARIA FALTA VENDER PARA QUE 1 KIA VALGA...")
    for inv in inversa:
        nece = inv["demanda_gflop_dia"]
        u = ("-" if inv["utilizacion"] is None
             else f"{num(inv['utilizacion'] * 100)} % de la capacidad de hoy")
        print(f"    {eur(inv['eur_por_kia']):>10} EUR  ->  {gflop(nece):>18}/dia   ({u})")
    print()
    base = filas[-1] if args.demanda is None else filas[0]
    print("  LECTURA HONESTA")
    print("    - Con demanda 0 el resultado es 0. No es un suelo: es que hoy no hay")
    print("      ni un cliente externo pagando por el trabajo de la red.")
    print(f"    - Al 100 % de la capacidad de hoy, el trabajo entero de la red vale")
    print(f"      {eur(d['capacidad_gflop_dia'] * eur_gflop)} EUR/dia a precio de nube barata:")
    print("      la red calcula muy poco todavia. No es que el KIA sea malo, es que")
    print("      la red esta al 0,1 % de lo que hace UNA sola grafica.")
    print(f"    - Ese mismo reparto da {eur(base['eur_por_kia'])} EUR por KIA. Si ademas")
    print(f"      se valorase TODO el supply emitido ({num(d['supply'])} KIA) a ese ancla,")
    print(f"      saldrian {eur(d['supply'] * base['eur_por_kia'])} EUR: es aritmetica,")
    print("      no una capitalizacion, porque nadie esta comprando.")
    print("    - Esto es un ancla contable, no una oferta ni una promesa. Un token sin")
    print("      comprador no tiene precio: tiene una division.")
    print()
    return 0


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
