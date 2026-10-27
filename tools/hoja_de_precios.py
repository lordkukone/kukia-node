"""Hoja de precios para un cliente de KUKIA.

Convierte la capacidad de la red en una oferta que se puede enviar por correo:
euros por hora de GPU, KIA por trabajo y que implica eso en euros por KIA.

El anclaje es una regla, no un numero inventado:

    un mes de emision paga un mes de GPU a tope

Es decir, `KIA/GFLOP = (emision de 30,44 dias) / (GFLOP de un mes de GPU)`.
Con los datos del 25-10-2026 eso da 0,0000014562 KIA/GFLOP, o lo que es lo
mismo 78,64 KIA la hora de GPU y 57.448 KIA el mes (justo la emision mensual).
Si ademas el cliente paga el precio de mercado de una 5070 (0,15 EUR/h, banda
0,08-0,18 USD), eso implica que 1 KIA vale 0,0019 EUR: el unico valor que
cuadra a la vez con la emision y con lo que cuesta alquilar una GPU.

Sin anclaje, el precio de hoy (0,00070090 KIA/GFLOP) cobra 37.849 KIA por una
sola hora de GPU: medio mes de emision. Con ese numero no hay cliente posible,
porque tendria que comprar medio suministro anual para una tarde de trabajo.

Ejemplos:

    python tools/hoja_de_precios.py
    python tools/hoja_de_precios.py --horas 10 50 200 --venta 0.12
    python tools/hoja_de_precios.py --emision-min 1.3106 --md HOJA_DE_PRECIOS.md

No escribe nada salvo que se le pida --md. No toca el ledger ni el Core de
escritura: solo hace GET /stats.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
if str(AQUI) not in sys.path:
    sys.path.insert(0, str(AQUI))

from calculadora_valor import CORE_POR_DEFECTO, eur, gflop, leer_core, num  # noqa: E402

DIAS_MES = 30.44
GPU_S_POR_DEFECTO = 15000.0   # GFLOP/s efectivos medidos en la 5070
COSTE_EUR_HORA = 0.08         # luz (0,06) + desgaste de la GPU, medido
VENTA_EUR_HORA = 0.15         # techo de mercado de una 5070 (0,08-0,18 USD/h)
# Si el Core no responde, este es el presupuesto por minuto del 25-10-2026.
EMISION_MIN_CONOCIDA = 1.3106


def ancla(emision_min: float, gpu_s: float, dias: float) -> dict:
    """Devuelve el anclaje completo: KIA/GFLOP y sus equivalencias."""
    if emision_min <= 0:
        raise ValueError("la emision por minuto tiene que ser mayor que 0")
    if gpu_s <= 0:
        raise ValueError("los GFLOP/s de la GPU tienen que ser mayores que 0")

    gflop_hora = gpu_s * 3600.0
    gflop_mes = gflop_hora * dias * 24.0
    kia_mes = emision_min * 1440.0 * dias
    kia_gflop = kia_mes / gflop_mes
    return {
        "emision_min": emision_min,
        "emision_mes": kia_mes,
        "gpu_s": gpu_s,
        "gflop_hora": gflop_hora,
        "gflop_mes": gflop_mes,
        "kia_gflop": kia_gflop,
        "gflop_por_kia": 1.0 / kia_gflop,
        "kia_hora": gflop_hora * kia_gflop,
        "kia_mes_gpu": gflop_mes * kia_gflop,
    }


def oferta(a: dict, horas: int, venta: float) -> dict:
    """Precio de un trabajo de `horas` de GPU a `venta` euros la hora."""
    kia = horas * a["kia_hora"]
    euros = horas * venta
    return {
        "horas": horas,
        "euros": euros,
        "kia": kia,
        "eur_por_kia": (euros / kia) if kia else 0.0,
        "coste": horas * COSTE_EUR_HORA,
    }


def dos(n: float, dec: int = 2) -> str:
    """Importe en euros con coma decimal y sin ceros de relleno raros."""
    return f"{n:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def lineas(a: dict, tramos, venta: float, precio_actual: float | None = None) -> list[str]:
    """El texto de la hoja, en Markdown, para copiar y pegar."""
    out: list[str] = []
    out.append("# KUKIA — hoja de precios")
    out.append("")
    out.append("Computo verificado, pagado por trabajo hecho. Nada de promesas.")
    out.append("")
    out.append(f"- Capacidad de la red: **{gflop(a['gflop_hora'])}/hora** por GPU "
               f"({num(a['gpu_s'])} GFLOP/s).")
    out.append(f"- Emision: **{num(a['emision_min'], 4)} KIA/min** = "
               f"{num(a['emision_mes'])} KIA al mes.")
    out.append(f"- Precio interno: **{num(a['kia_gflop'], 10)} KIA/GFLOP** "
               f"(1 KIA = {num(a['gflop_por_kia'])} GFLOP).")
    out.append(f"- Regla del precio: un mes de emision paga un mes de GPU a tope "
               f"({num(a['kia_mes_gpu'])} KIA).")
    out.append("")
    out.append(f"## Tarifa: {dos(venta)} EUR por hora de GPU")
    out.append("")
    out.append("| Trabajo | Precio | En KIA | Coste (luz y desgaste) | Margen |")
    out.append("|---|---|---|---|---|")
    for h in tramos:
        o = oferta(a, h, venta)
        margen = o["euros"] - o["coste"]
        out.append(f"| {h} h de GPU | {dos(o['euros'])} EUR | {num(o['kia'], 1)} KIA "
                   f"| {dos(o['coste'])} EUR | {dos(margen)} EUR |")
    out.append("")
    fila = oferta(a, tramos[0], venta) if tramos else None
    if fila:
        out.append(f"Eso implica **{dos(fila['eur_por_kia'], 4)} EUR por KIA** cuando alguien "
                   f"paga de verdad. Antes de eso el precio de mercado es 0 EUR: "
                   f"no hay comprador, y una moneda sin comprador no vale nada.")
    out.append("")
    out.append("## Que compra el cliente")
    out.append("")
    out.append("1. Pide trabajo por la API; el Core le da tareas y cobra por GFLOP verificado.")
    out.append("2. Paga por adelantado (transferencia o Bizum). El saldo entra en su "
               "billetera del ledger, firmado.")
    out.append("3. Cada entrega queda en la cadena de hashes; el cliente puede verificar "
               "el recibo sin creerse nada de nosotros.")
    out.append("")
    out.append("## Lo que NO se promete")
    out.append("")
    out.append("- Que KIA suba. No hay mercado, no hay exchange, no hay garantia.")
    out.append("- Que el saldo en KIA valga X euros. Vale trabajo hecho, y punto.")
    out.append("- Devoluciones por trabajo ya entregado y sellado.")
    out.append("")
    if precio_actual and precio_actual > 0:
        veces = precio_actual / a["kia_gflop"]
        out.append("## Aviso interno (no enviar al cliente)")
        out.append("")
        out.append(f"El precio que aplica el Core ahora es {num(precio_actual, 8)} KIA/GFLOP, "
                   f"{num(veces)} veces el de esta hoja. A ese precio, una hora de GPU "
                   f"cuesta {num(a['gflop_hora'] * precio_actual)} KIA y la moneda no se "
                   f"puede vender. Cambiarlo es decision de Kuki, no de una herramienta.")
        out.append("")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Hoja de precios para un cliente de KUKIA.")
    ap.add_argument("--core", default=CORE_POR_DEFECTO, help="URL del Core")
    ap.add_argument("--emision-min", type=float, default=None,
                    help="KIA por minuto si no se quiere leer el Core")
    ap.add_argument("--gpu-s", type=float, default=GPU_S_POR_DEFECTO,
                    help="GFLOP/s efectivos de una GPU")
    ap.add_argument("--dias", type=float, default=DIAS_MES, help="dias del mes")
    ap.add_argument("--venta", type=float, default=VENTA_EUR_HORA,
                    help="EUR por hora de GPU que se le pide al cliente")
    ap.add_argument("--horas", type=int, nargs="+", default=[10, 50, 200, 730],
                    help="tramos de trabajo a listar")
    ap.add_argument("--md", default=None, help="escribir la hoja en este fichero")
    args = ap.parse_args(argv)

    emision_min = args.emision_min
    precio_actual = None
    if emision_min is None:
        try:
            s = leer_core(args.core)
            bolsa = s.get("bolsa_emision") or {}
            emision_min = float(bolsa.get("presupuesto_kia_min")
                                or s.get("emision_por_min") or 0.0)
            precio_actual = float(bolsa.get("precio_minuto")
                                  or (s.get("pago_por_potencia") or {}).get("precio_gflop")
                                  or 0.0) or None
        except Exception as e:  # sin Core se sigue con el dato conocido
            print(f"[aviso] no puedo leer el Core ({e}); uso la emision conocida "
                  f"{EMISION_MIN_CONOCIDA} KIA/min", file=sys.stderr)
            emision_min = EMISION_MIN_CONOCIDA

    if not emision_min or emision_min <= 0:
        print("error: la emision por minuto es 0; pasa --emision-min", file=sys.stderr)
        return 2

    a = ancla(emision_min, args.gpu_s, args.dias)
    texto = "\n".join(lineas(a, args.horas, args.venta, precio_actual)) + "\n"
    print(texto, end="")

    if args.md:
        destino = Path(args.md)
        destino.write_text(texto, encoding="utf-8")
        print(f"\n[escrito] {destino}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
