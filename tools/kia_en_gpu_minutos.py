"""El KIA expresado en minutos de GPU: reajuste y simulacion.

Idea: en vez de que 1 KIA sea "un numero sin unidad", se define

    1 KIA = 1 minuto de GPU de referencia  (15.000 GFLOP/s, tipo RTX 5070)

y el precio por GFLOP sale solo: KIA/GFLOP = K / (60 * GFLOP/s). Asi el precio
interno deja de ser un parametro que alguien elige y pasa a ser una definicion
que cualquiera puede comprobar con una regla de tres y un reloj.

Esto NO necesita tocar el Core: el Core cobra `precio * GFLOP` y el fusible del
minuto reparte el presupuesto. Solo hay que fijar el precio que sale de aqui.

Simula tres cosas:
  1. Que cobra un minero por dia/mes con su GPU a tope, en KIA y en euros.
  2. Cuanto de la emision se lleva una GPU, dos, diez, cien.
  3. Cuanta capacidad aguanta la emision sin que el precio tenga que bajar.

Uso:
    python tools/kia_en_gpu_minutos.py
    python tools/kia_en_gpu_minutos.py --kia-min 1.311      (equivalente al ancla GFLOP)
    python tools/kia_en_gpu_minutos.py --eur-hora 0.18 --emision-min 1.3106
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
if str(AQUI) not in sys.path:
    sys.path.insert(0, str(AQUI))

from calculadora_valor import CORE_POR_DEFECTO, leer_core, num  # noqa: E402
from hoja_de_precios import DIAS_MES, EMISION_MIN_CONOCIDA  # noqa: E402

GPU_S = 15000.0          # GFLOP/s efectivos de la GPU de referencia
EUR_HORA = 0.15          # lo que se paga por esa GPU en el mercado
MAX_SUPPLY = 100_000_000.0
ESCENARIOS = (1, 2, 10, 100)


def eur(n: float, dec: int = 2) -> str:
    return f"{n:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def kia(n: float) -> str:
    if n == 0:
        return "0"
    if abs(n) < 0.01:
        return f"{n:.2e}".replace("e-0", "e-").replace(".", ",")
    if abs(n) < 1:
        return f"{n:.4f}".replace(".", ",")
    return f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def precio_por_gflop(kia_min: float, gpu_s: float) -> float:
    """KIA/GFLOP si un minuto de GPU cuesta `kia_min` KIA."""
    if kia_min <= 0:
        raise ValueError("los KIA por minuto de GPU tienen que ser > 0")
    return kia_min / (60.0 * gpu_s)


def simular(kia_min: float, gpu_s: float, emision_min: float, supply: float,
            eur_hora: float, dias: float) -> dict:
    """Los numeros del reajuste."""
    precio = precio_por_gflop(kia_min, gpu_s)
    # 1 minuto de GPU = 60*gpu_s GFLOP y cuesta kia_min KIA.
    # Cuantos KIA vale una hora y un dia de una GPU:
    kia_hora = kia_min * 60.0
    kia_dia = kia_hora * 24.0
    kia_mes = kia_dia * dias
    emision_dia = emision_min * 1440.0
    emision_mes = emision_dia * dias
    # Capacidad que la emision sostiene sin bajar el precio:
    gpu_min_por_min = emision_min / kia_min
    gpu_equiv = gpu_min_por_min           # GPUs a tope que se pueden pagar 24/7
    # Valor de 1 KIA si la GPU cobra su precio de mercado:
    eur_por_kia = (eur_hora / 60.0) / kia_min
    return {
        "kia_min": kia_min, "precio": precio,
        "gflop_por_kia": 1.0 / precio,
        "minutos_gpu_por_kia": 1.0 / kia_min,
        "kia_hora": kia_hora, "kia_dia": kia_dia, "kia_mes": kia_mes,
        "emision_dia": emision_dia, "emision_mes": emision_mes,
        "gpu_sostenibles": gpu_equiv,
        "eur_kia": eur_por_kia,
        "eur_mes_gpu": kia_mes * eur_por_kia,
        "dias_hasta_techo": ((MAX_SUPPLY - supply) / emision_dia) if emision_dia else 0.0,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="KIA expresado en minutos de GPU: reajuste y simulacion.")
    ap.add_argument("--core", default=CORE_POR_DEFECTO)
    ap.add_argument("--emision-min", type=float, default=None)
    ap.add_argument("--kia-min", type=float, default=1.0,
                    help="KIA que cuesta 1 minuto de GPU (1 = unidad redonda)")
    ap.add_argument("--gpu-s", type=float, default=GPU_S)
    ap.add_argument("--eur-hora", type=float, default=EUR_HORA,
                    help="lo que se paga por esa GPU en el mercado")
    ap.add_argument("--dias", type=float, default=DIAS_MES)
    args = ap.parse_args(argv)

    emision_min = args.emision_min
    supply, precio_actual = 15_000_000.0, None
    if emision_min is None:
        try:
            s = leer_core(args.core)
            bolsa = s.get("bolsa_emision") or {}
            emision_min = float(bolsa.get("presupuesto_kia_min")
                                or s.get("emision_por_min") or 0.0)
            precio_actual = float(bolsa.get("precio_minuto") or 0.0) or None
            supply = float(s.get("supply_emitido") or supply)
        except Exception as e:
            print(f"[aviso] Core no disponible ({e}); uso {EMISION_MIN_CONOCIDA} KIA/min",
                  file=sys.stderr)
            emision_min = EMISION_MIN_CONOCIDA
    if not emision_min or emision_min <= 0:
        print("error: emision desconocida; pasa --emision-min", file=sys.stderr)
        return 2

    r = simular(args.kia_min, args.gpu_s, emision_min, supply, args.eur_hora, args.dias)

    print("== La definicion ==")
    print(f"  1 KIA = {num(r['minutos_gpu_por_kia'], 4)} minuto(s) de GPU de referencia "
          f"({num(args.gpu_s)} GFLOP/s)")
    print(f"  -> precio: {num(r['precio'], 12)} KIA/GFLOP")
    print(f"  -> 1 KIA = {kia(r['gflop_por_kia'])} GFLOP de trabajo")
    print(f"  -> 1 hora de GPU = {kia(r['kia_hora'])} KIA | 1 dia = {kia(r['kia_dia'])} KIA "
          f"| 1 mes = {kia(r['kia_mes'])} KIA")
    print(f"  -> si la GPU se alquila a {eur(args.eur_hora)} EUR/h, 1 KIA = "
          f"{eur(r['eur_kia'], 4)} EUR")

    print()
    print("== La emision ==")
    print(f"  presupuesto: {num(emision_min, 4)} KIA/min = {kia(r['emision_dia'])} KIA/dia "
          f"= {kia(r['emision_mes'])} KIA al mes")
    print(f"  eso paga {num(r['gpu_sostenibles'], 2)} GPUs funcionando 24/7 sin que el "
          f"precio tenga que bajar")
    print(f"  quedan {kia(MAX_SUPPLY - supply)} KIA por emitir: "
          f"{num(r['dias_hasta_techo'] / 365.0)} anos a este ritmo")

    print()
    print("== Pago al minero y gasto de emision ==")
    print()
    print("  | GPUs a tope 24/7 | KIA/dia por GPU | KIA/mes por GPU | EUR/mes por GPU | "
          "gasto total/dia | % de la emision |")
    print("  |---|---|---|---|---|---|")
    for n in ESCENARIOS:
        total = n * r["kia_dia"]
        pct = 100.0 * total / r["emision_dia"] if r["emision_dia"] else 0.0
        print(f"  | {n} | {kia(r['kia_dia'])} | {kia(r['kia_mes'])} | "
              f"{eur(r['eur_mes_gpu'])} | {kia(total)} | {num(pct, 1)} % |")

    print()
    print("  Esa tabla es el mundo 'definicion fija': cada GPU cobra siempre lo")
    print("  mismo... hasta que el presupuesto se queda corto. A partir de "
          f"{num(r['gpu_sostenibles'], 1)}")
    print("  GPUs a tope, el gasto supera la emision y el Core hace lo que sabe")
    print("  hacer: baja el precio para repartir lo que hay. Ese es el mundo real:")
    print()
    print("  | GPUs a tope 24/7 | KIA/dia por GPU | KIA/mes por GPU | EUR/mes por GPU | "
          "1 KIA = minutos de GPU |")
    print("  |---|---|---|---|---|")
    for n in ESCENARIOS:
        if n <= r["gpu_sostenibles"]:
            por_gpu, min_kia = r["kia_dia"], r["minutos_gpu_por_kia"]
        else:
            por_gpu = r["emision_dia"] / n
            min_kia = por_gpu / 1440.0          # minutos de GPU por KIA de verdad
        print(f"  | {n} | {kia(por_gpu)} | {kia(por_gpu * args.dias)} | "
              f"{eur(por_gpu * args.dias * r['eur_kia'])} | {num(min_kia, 4)} |")
    print()
    print("  Aqui esta lo importante: con 2 GPUs a tope, cada una cobra el 65 % de lo")
    print("  que cobra sola, y la emision se reparte entera (el total no sube nunca).")
    print("  Y el KIA deja de comprar 1 minuto: compra 0,65. No es inflacion de")
    print("  imprimir, es inflacion de capacidad: la misma tarta entre mas bocas.")

    print()
    print("== Contra el precio de ahora ==")
    if precio_actual:
        # Cuantos minutos de GPU paga 1 KIA al precio actual:
        min_ahora = (1.0 / precio_actual) / (60.0 * args.gpu_s)
        print(f"  precio del Core ahora: {num(precio_actual, 12)} KIA/GFLOP")
        print(f"  con el, 1 KIA compra {num(min_ahora, 6)} minutos de GPU "
              f"(deberia comprar {num(r['minutos_gpu_por_kia'])}).")
        print(f"  el trabajo esta valorado {num(precio_actual / r['precio'])} veces por "
              f"encima de su definicion en minutos de GPU.")
    else:
        print("  (sin Core: no puedo comparar con el precio vivo)")

    print()
    print("== Que definicion elegir ==")
    print()
    print("  Todo sale de una decision: cuantas GPUs quieres que la emision pague")
    print("  a tope. El resto va detras.")
    print()
    print("  | GPUs que paga | 1 KIA = | KIA por hora de GPU | 1 KIA en EUR | KIA/GFLOP |")
    print("  |---|---|---|---|---|")
    for n in (1, 2, 3, 5):
        km = emision_min / n
        mins = 1.0 / km
        por_hora = km * 60.0
        eur_kia = (args.eur_hora / 60.0) / km
        print(f"  | {n} | {num(mins, 2)} min | {num(por_hora, 1)} | {eur(eur_kia, 4)} | "
              f"{num(km / (60.0 * args.gpu_s), 12)} |")
    print()
    print("  Regla: kia_min = emision_por_minuto / GPUs_a_pagar. Con el presupuesto")
    print(f"  de hoy ({num(emision_min, 4)} KIA/min), si quieres pagar tus dos GPUs a tope")
    print(f"  sale 1 KIA = {num(1.0 / (emision_min / 2.0), 2)} minutos de GPU; si quieres que 1 KIA")
    print("  sea un minuto redondo, la emision paga 1,31 GPUs y la segunda cobra el 65 %.")
    print()
    print("  Nota: el reajuste no toca el Core. Solo hay que fijar el precio que")
    print("  sale de la definicion; el fusible del minuto sigue mandando igual.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
