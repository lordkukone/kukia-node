"""Precio interno de KUKIA contra el precio real del computo.

Responde a una sola pregunta: si una GPU de este PC se alquila en el mercado por
X euros la hora, que precio en KIA/GFLOP es coherente, y que pasa con la emision
y con lo que cobra un minero.

Como el Core fija el precio (no es un parametro, es un cociente):

    precio = presupuesto_del_minuto / GFLOP medidos el minuto anterior

el precio y el valor del KIA no son dos decisiones, son una: fijar precio es
fijar cuanto vale 1 KIA en euros. Por eso aqui se calculan los dos juntos.

Referencias de mercado (25-10-2026, por GPU y hora):
    RTX 5070   0,08 - 0,18 USD   (el modelo de este PC)
    RTX 4090   0,15 - 0,89 USD
    RTX 5090   0,25 - 1,19 USD
    A100       1,99 USD (Lambda 40 GB)   H100  3,29 USD (Lambda PCIe)
    Luz Espana 0,21 - 0,24 EUR/kWh  -> una 5070 a 250 W cuesta 0,05-0,06 EUR/h

Uso:
    python tools/precio_real_computo.py
    python tools/precio_real_computo.py --eur-hora 0.15 --gpu-s 15000
    python tools/precio_real_computo.py --eur-hora 0.18 --eur-kwh 0.24 --watios 300

Solo hace GET /stats al Core. No escribe nada ni toca el ledger.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
if str(AQUI) not in sys.path:
    sys.path.insert(0, str(AQUI))

from calculadora_valor import CORE_POR_DEFECTO, gflop, leer_core, num  # noqa: E402
from hoja_de_precios import (  # noqa: E402
    DIAS_MES, EMISION_MIN_CONOCIDA, ancla,
)

EUR_HORA_MERCADO = 0.15     # 5070 en el mercado, lado alto de la banda barata
EUR_KWH = 0.22              # tarifa domestica Espana 2026
WATIOS = 250.0              # consumo de placa de una 5070
GPU_S = 15000.0             # GFLOP/s efectivos medidos
MAX_SUPPLY = 100_000_000.0


def mercado_eur_gflop(eur_hora: float, gpu_s: float) -> float:
    """Euros por GFLOP implicados por el alquiler de una GPU."""
    gflop_hora = gpu_s * 3600.0
    if gflop_hora <= 0:
        raise ValueError("los GFLOP/s tienen que ser mayores que 0")
    return eur_hora / gflop_hora


def precio_para_valor(precio_mercado_eur_gflop: float, eur_kia: float) -> dict:
    """KIA/GFLOP que hay que cobrar si 1 KIA vale `eur_kia` euros."""
    if eur_kia <= 0:
        raise ValueError("el valor del KIA tiene que ser mayor que 0")
    kia_gflop = precio_mercado_eur_gflop / eur_kia
    return {"eur_kia": eur_kia, "kia_gflop": kia_gflop,
            "gflop_por_kia": 1.0 / kia_gflop}


def efecto(precio_kia_gflop: float, eur_kia: float, gpu_s: float,
           dias: float, eur_hora: float, pot_w: float, eur_kwh: float,
           emision_mes: float, supply: float) -> dict:
    """Que pasa con una GPU a tope un mes, a este precio."""
    gflop_hora = gpu_s * 3600.0
    gflop_mes = gflop_hora * dias * 24.0
    kia_hora = precio_kia_gflop * gflop_hora
    kia_mes = precio_kia_gflop * gflop_mes
    coste_luz_h = (pot_w / 1000.0) * eur_kwh
    return {
        "precio": precio_kia_gflop,
        "eur_kia": eur_kia,
        "gflop_hora": gflop_hora,
        "kia_hora": kia_hora,
        "kia_dia": kia_hora * 24.0,
        "kia_mes": kia_mes,
        "fraccion_emision": (kia_mes / emision_mes) if emision_mes else 0.0,
        "valor_mes_eur": kia_mes * (eur_kia or 0.0),
        "precio_hora_eur": eur_hora,
        "coste_luz_h": coste_luz_h,
        "margen_h": eur_hora - coste_luz_h,
        "meses_hasta_techo": ((MAX_SUPPLY - supply) / kia_mes) if kia_mes else float("inf"),
    }


def eur(n: float, dec: int = 2) -> str:
    return f"{n:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def kia(n: float) -> str:
    """KIA con decimales segun magnitud: los pequenos no se pueden redondear."""
    if n == 0:
        return "0"
    if abs(n) < 0.01:
        return f"{n:.2e}".replace("e-0", "e-").replace(".", ",")
    if abs(n) < 1:
        return f"{n:.4f}".replace(".", ",")
    return f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Precio interno en KIA/GFLOP coherente con el precio real del computo.")
    ap.add_argument("--core", default=CORE_POR_DEFECTO)
    ap.add_argument("--emision-min", type=float, default=None)
    ap.add_argument("--eur-hora", type=float, default=EUR_HORA_MERCADO,
                    help="precio de alquiler de la GPU por hora en el mercado")
    ap.add_argument("--gpu-s", type=float, default=GPU_S, help="GFLOP/s efectivos")
    ap.add_argument("--eur-kwh", type=float, default=EUR_KWH)
    ap.add_argument("--watios", type=float, default=WATIOS)
    ap.add_argument("--dias", type=float, default=DIAS_MES)
    args = ap.parse_args(argv)

    emision_min = args.emision_min
    precio_actual = None
    supply = 15_000_000.0
    if emision_min is None:
        try:
            s = leer_core(args.core)
            bolsa = s.get("bolsa_emision") or {}
            emision_min = float(bolsa.get("presupuesto_kia_min")
                                or s.get("emision_por_min") or 0.0)
            precio_actual = float(bolsa.get("precio_minuto")
                                  or (s.get("pago_por_potencia") or {}).get("precio_gflop")
                                  or 0.0) or None
            supply = float(s.get("supply_emitido") or supply)
        except Exception as e:
            print(f"[aviso] Core no disponible ({e}); uso {EMISION_MIN_CONOCIDA} KIA/min",
                  file=sys.stderr)
            emision_min = EMISION_MIN_CONOCIDA
    if not emision_min or emision_min <= 0:
        print("error: emision por minuto desconocida; pasa --emision-min", file=sys.stderr)
        return 2

    a = ancla(emision_min, args.gpu_s, args.dias)
    p_mercado = mercado_eur_gflop(args.eur_hora, args.gpu_s)

    print("== El mercado ==")
    print(f"  alquiler de la GPU: {eur(args.eur_hora)} EUR/h  con "
          f"{num(args.gpu_s)} GFLOP/s")
    print(f"  -> {num(p_mercado, 12)} EUR/GFLOP")
    luz = (args.watios / 1000.0) * args.eur_kwh
    print(f"  luz: {num(args.watios)} W a {eur(args.eur_kwh)} EUR/kWh = {eur(luz)} EUR/h")
    print(f"  margen bruto por hora: {eur(args.eur_hora - luz)} EUR "
          f"({num(100.0 * (args.eur_hora - luz) / args.eur_hora)} %)")

    print()
    print("== El precio interno que cuadra ==")
    print("  (precio = EUR/GFLOP del mercado / valor de 1 KIA)")
    print()
    print("  | 1 KIA vale | KIA/GFLOP | 1 KIA compra | 1 h de GPU | 1 mes de GPU |")
    print("  |---|---|---|---|---|")
    filas = []
    for eur_kia in (a["kia_gflop"] and p_mercado / a["kia_gflop"], 0.01, 0.05, 0.10):
        p = precio_para_valor(p_mercado, eur_kia)
        ef = efecto(p["kia_gflop"], eur_kia, args.gpu_s, args.dias, args.eur_hora,
                    args.watios, args.eur_kwh, a["emision_mes"], supply)
        filas.append((p, ef, eur_kia))
        print(f"  | {eur(eur_kia, 4)} EUR | {num(p['kia_gflop'], 10)} | "
              f"{gflop(p['gflop_por_kia'])} | {kia(ef['kia_hora'])} KIA | "
              f"{kia(ef['kia_mes'])} KIA |")
    p_ancla = filas[0]
    print()
    print(f"  La primera fila es el ancla: un mes de emision ({kia(a['emision_mes'])} KIA) "
          f"paga un mes de GPU a tope.")
    print(f"  Sale del propio Core: presupuesto {num(emision_min, 4)} KIA/min dividido por "
          f"una GPU trabajando de verdad.")

    print()
    print("== Efecto en la emision y en el minero ==")
    print()
    print("  | Precio KIA/GFLOP | KIA/mes por GPU | % de la emision | "
          "meses hasta 100 M | minero: EUR/mes |")
    print("  |---|---|---|---|---|")
    casos = []
    if precio_actual:
        casos.append(("ahora", precio_actual, p_ancla[2] if p_ancla[0] else 0.0))
    casos.append(("ancla", p_ancla[0]["kia_gflop"], p_ancla[2]))
    for nombre, precio, eur_kia in casos:
        ef = efecto(precio, eur_kia, args.gpu_s, args.dias, args.eur_hora,
                    args.watios, args.eur_kwh, a["emision_mes"], supply)
        print(f"  | {nombre}: {num(precio, 10)} | {kia(ef['kia_mes'])} | "
              f"{num(100 * ef['fraccion_emision'], 1)} % | "
              f"{num(ef['meses_hasta_techo'] / 12.0)} anos | "
              f"{eur(ef['valor_mes_eur'])} |")

    print()
    print("  La fila 'ahora' es hipotetica: ningun minero cobra eso, porque el fusible")
    print("  del minuto (2x el presupuesto) lo recorta. Sirve para ver a que precio se")
    print("  esta valorando el trabajo, no para calcular una nomina.")
    print()
    if precio_actual and p_ancla[0]["kia_gflop"]:
        veces = precio_actual / p_ancla[0]["kia_gflop"]
        ef_hoy = efecto(precio_actual, p_ancla[2], args.gpu_s, args.dias, args.eur_hora,
                        args.watios, args.eur_kwh, a["emision_mes"], supply)
        print("== Lectura ==")
        print(f"  El precio de ahora ({num(precio_actual, 10)} KIA/GFLOP) esta "
              f"{num(veces)} veces por encima del ancla.")
        print(f"  Con el, 1 KIA compra {gflop(1.0 / precio_actual)} de trabajo, cuando a "
              f"precio de mercado deberia comprar {gflop(1.0 / p_ancla[0]['kia_gflop'])}.")
        print(f"  O sea: hoy un KIA se cobra como si valiera {eur(p_ancla[2] * veces, 4)} EUR, "
              f"y vale {eur(p_ancla[2], 4)} EUR. "
              f"Nadie paga {eur(p_ancla[2] * veces, 2)} EUR por algo que "
              f"en el mercado cuesta {eur(p_ancla[2], 4)}.")
        print()
        print(f"  Un mes de GPU a tope cobraria {kia(ef_hoy['kia_mes'])} KIA, el "
              f"{num(100 * ef_hoy['fraccion_emision'])} % de toda la emision mensual: "
              f"el fusible del minuto lo recorta y el minero cobra el presupuesto, "
              f"no su trabajo.")
    print()
    print("  Nota: la emision NO cambia con el precio. El precio solo decide cuanta")
    print("  trabajo se lleva cada KIA. El total pagado por minuto lo fija el")
    print("  presupuesto y el fusible, no la tarifa.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
