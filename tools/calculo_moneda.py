"""Los calculos de la moneda, a la vista: cuanto sale por tarea y cuando se acaba.

PARA QUE
    Dos preguntas que hay que poder contestar sin abrir el codigo:
      1. ¿Cuantos KIA salen por tarea, y cuantos por minuto, con la red como
         esta ahora?
      2. ¿Cuando se emite la ULTIMA moneda? El diseno pide 124 anos de
         calendario o mas (fin en 2150), y eso hay que comprobarlo, no suponerlo.

DE DONDE SALEN LOS DATOS
    Del Core vivo (`/stats`): techo, emitido, restante, era, pago por tarea y
    emision por minuto. El calendario (eras, halvings y fecha final) se recalcula
    aqui a partir de esos mismos numeros, asi que si el Core cambia de fecha,
    esto lo ensena sin tocar nada.

USO
    python tools/calculo_moneda.py                 resumen completo
    python tools/calculo_moneda.py --json          para scripts
    python tools/calculo_moneda.py --core http://otro:8300
    python tools/calculo_moneda.py --tareas-min 40 --tareas-min 120
                                                    simulacion por potencia
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import date, datetime, timedelta

DIAS_ANIO = 365.25
# Requisito del calendario: 124 anos desde el inicio, o sea la ultima moneda en
# 2150 (decision del dueno, 23-10-2026; antes estaba en 2152, 126 anos).
ANIOS_MINIMOS = 124
CORE = "http://127.0.0.1:8300"


def pide(core: str) -> dict:
    with urllib.request.urlopen(f"{core.rstrip('/')}/stats", timeout=15) as r:
        return json.load(r)


def fecha(texto: str) -> date:
    return datetime.strptime(texto, "%Y-%m-%d").date()


def num(x: float, dec: int = 2) -> str:
    """Numero con separador de miles espanol (15.085.776,55)."""
    s = f"{x:,.{dec}f}"
    return s.replace(",", "·").replace(".", ",").replace("·", ".")


def tabla_eras(fin_emision: date, inicio: date, restante: float, era_actual: int) -> list:
    """Una fila por era: fechas, años y KIA que quedan al empezar cada una.

    Cada halving deja la mitad por emitir (es lo que hace `emission_mult`), asi
    que el reparto por era es 1/2, 1/4, 1/8... del restante de hoy.
    """
    anios = (fin_emision - inicio).days / DIAS_ANIO / 6
    filas = []
    for k in range(7):
        ini = inicio + timedelta(days=round(k * anios * DIAS_ANIO))
        fin = inicio + timedelta(days=round((k + 1) * anios * DIAS_ANIO))
        filas.append({"era": k, "mult": 0.5 ** k, "inicio": ini, "fin": fin,
                      "anios": anios, "restante": restante * (0.5 ** k),
                      "actual": k == era_actual})
    return filas


def main() -> int:
    # Consola de Windows en cp1252: sin esto las tildes y el signo de "¿"
    # salen como simbolos raros (o revientan si se redirige la salida).
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Calculos de emision de KUKIA.")
    ap.add_argument("--core", default=CORE, help=f"Core a consultar (defecto {CORE})")
    ap.add_argument("--tareas-min", type=float, action="append", default=None,
                    help="tareas/min para simular potencia (se puede repetir)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    try:
        d = pide(a.core)
    except Exception as e:
        print(f"[ERROR] el Core no responde en {a.core} ({type(e).__name__}: {e})")
        print("        el calculo se saca de /stats; arranca el Core (o pasa --core).")
        return 2

    e = d.get("emision") or {}
    techo = float(d.get("max_supply") or 0)
    emitido = float(d.get("supply_emitido") or 0)
    restante = max(0.0, techo - emitido)
    pago = float(d.get("pago_por_tarea") or 0)
    emision_min = float(d.get("emision_por_min") or 0)
    fin = fecha(str(e.get("fin_emision") or "2150-01-01"))
    # El Core no publica la fecha de inicio: se deshacen las 6 eras y se baja al
    # 1 de enero, que es como esta definido. (Restando dias de calendario el
    # redondeo de 365,25 se comia dos dias y se caia al año anterior.)
    eras_anios = 6 * float(e.get("halving_years") or 20.67)
    inicio = date(int(round(fin.year - eras_anios)), 1, 1)
    hoy = date.today()
    anios_calendario = (fin - inicio).days / DIAS_ANIO
    anios_que_quedan = (fin - hoy).days / DIAS_ANIO
    # El liston se mide sobre el calendario (fijo), no sobre "desde hoy": si no,
    # el mismo diseno dejaria de cumplir solo porque pasen los anos.
    cumple = anios_calendario >= ANIOS_MINIMOS - 0.1
    det = d.get("detalle_pago") or {}
    tareas_dia = float(det.get("tasa_dia") or 0)
    tareas_que_faltan = (restante / pago) if pago > 0 else 0.0

    simulaciones = []
    for t in (a.tareas_min or [10.0, 40.0, 120.0, 300.0]):
        kia_min = t * pago
        simulaciones.append({"tareas_min": t, "kia_min": kia_min, "kia_h": kia_min * 60,
                             "kia_dia": kia_min * 1440,
                             "dias_para_1_kia": (1 / kia_min / 1440) if kia_min else None})

    if a.json:
        print(json.dumps({
            "techo": techo, "emitido": emitido, "restante": restante,
            "era": e.get("era_actual"), "multiplicador": e.get("multiplicador"),
            "proximo_halving": e.get("proximo_halving"),
            "pago_por_tarea": pago, "emision_por_min": emision_min,
            "fin_emision": fin.isoformat(), "inicio_emision": inicio.isoformat(),
            "anios_calendario": round(anios_calendario, 2),
            "anios_que_quedan": round(anios_que_quedan, 2),
            "anios_minimos": ANIOS_MINIMOS, "cumple": cumple,
            "tareas_que_faltan": round(tareas_que_faltan, 0),
            "tareas_dia_red": tareas_dia,
            "simulaciones": simulaciones,
        }, indent=2, ensure_ascii=False))
        return 0

    print("CALCULOS DE LA MONEDA - KUKIA")
    print(f"Core: {a.core}  ·  {datetime.now().strftime('%d-%m-%Y %H:%M')}  ·  "
          f"version {d.get('version')}\n")

    print("1) CUANTO HAY Y CUANTO QUEDA")
    print(f"   techo                 {num(techo, 0):>16} KIA")
    print(f"   emitido               {num(emitido):>16} KIA  ({num(100 * emitido / techo, 2)} %)")
    print(f"   por emitir            {num(restante):>16} KIA  ({num(100 * restante / techo, 2)} %)")
    print(f"   era                   {e.get('era_actual')} (x{e.get('multiplicador')}), "
          f"halvings cada {e.get('halving_years')} anos")
    print(f"   proximo halving       {e.get('proximo_halving')}")
    print()

    print("2) QUE PAGA CADA TAREA HOY")
    print(f"   pago por tarea        {num(pago, 6):>16} KIA   (tier 1,0)")
    print(f"   tier 16 GB (x1,3)     {num(pago * 1.3, 6):>16} KIA")
    print(f"   tier 24 GB (x1,6)     {num(pago * 1.6, 6):>16} KIA")
    if pago > 0:
        print(f"   1 KIA cuesta          {num(1 / pago, 0):>16} tareas")
    print(f"   emision de la red     {num(emision_min, 4):>16} KIA/min "
          f"({num(tareas_dia, 0)} tareas/dia a este pago)")
    print()

    print("3) CALENDARIO HASTA LA ULTIMA MONEDA")
    print(f"   {'era':<5}{'mult':>7}  {'empieza':<12}{'termina':<12}"
          f"{'anos':>6}{'queda al empezar':>20}")
    for f in tabla_eras(fin, inicio, restante, int(e.get("era_actual") or 0)):
        marca = " <-- ahora" if f["actual"] else ""
        if f["era"] >= 6:
            print(f"   {'fin':<5}{'-':>8}  {'':<12}{fin.isoformat():<12}"
                  f"{'':>6}{'-':>20}   emision = 0")
            break
        mult = f"{f['mult']:.4f}".rstrip("0").rstrip(".")
        print(f"   {f['era']:<5}{'x' + mult.replace('.', ','):>8}  {f['inicio'].isoformat():<12}"
              f"{f['fin'].isoformat():<12}{num(f['anios'], 2):>6}"
              f"{num(f['restante'], 0) + ' KIA':>20}{marca}")
    print()

    print("4) ¿CUMPLE QUE EL CALENDARIO DURE 124 ANOS O MAS (FIN EN 2150)?")
    print(f"   ultima moneda         {fin.isoformat()}")
    print(f"   calendario            {num(anios_calendario, 2)} anos desde "
          f"{inicio.isoformat()}")
    print(f"   desde hoy             {num(anios_que_quedan, 2)} anos")
    print(f"   requisito             >= {ANIOS_MINIMOS} anos"
          f"   ->   {'SI CUMPLE' if cumple else 'NO CUMPLE'}")
    if not cumple:
        faltan = ANIOS_MINIMOS - anios_calendario
        minimo = date(inicio.year + ANIOS_MINIMOS, inicio.month, inicio.day)
        print(f"   faltan                {num(faltan, 2)} anos: el fin no puede ser "
              f"antes de {minimo.isoformat()}")
    print()

    print("5) LA CUENTA, COMPROBADA")
    print(f"   para agotar {num(restante, 0)} KIA en {num(anios_que_quedan, 2)} anos hacen falta")
    print(f"   {num(emision_min, 4)} KIA/min  x  {num(anios_que_quedan * DIAS_ANIO * 1440, 0)} min "
          f"= {num(emision_min * anios_que_quedan * DIAS_ANIO * 1440, 0)} KIA")
    if tareas_que_faltan:
        print(f"   y a {num(pago, 6)} KIA/tarea son {num(tareas_que_faltan, 0)} tareas")
        if tareas_dia > 0:
            print(f"   con la red actual ({num(tareas_dia, 0)} tareas/dia) serian "
                  f"{num(tareas_que_faltan / tareas_dia / DIAS_ANIO, 1)} anos")
    print("   (si la red mina mas tareas/min, el pago por tarea BAJA solo: el total no cambia)")
    print()

    print("6) KIA SEGUN POTENCIA (lo que rinde cada ritmo de tareas)")
    print(f"   {'tareas/min':>11}{'KIA/min':>12}{'KIA/h':>12}{'KIA/dia':>12}"
          f"{'1 KIA cada':>18}")
    for s in simulaciones:
        if not s["dias_para_1_kia"]:
            rato = "-"
        elif s["dias_para_1_kia"] >= 1 / 24:
            rato = f"{num(s['dias_para_1_kia'] * 24, 1)} h"
        elif s["dias_para_1_kia"] >= 1 / 1440:
            rato = f"{num(s['dias_para_1_kia'] * 1440, 0)} min"
        else:
            rato = f"{num(s['dias_para_1_kia'] * 86400, 0)} s"
        print(f"   {num(s['tareas_min'], 0):>11}{num(s['kia_min'], 4):>12}"
              f"{num(s['kia_h'], 2):>12}{num(s['kia_dia'], 0):>12}{rato:>18}")
    print("\n   el ritmo de tareas lo pone la GPU (mas GFLOPS = mas tareas/min);")
    print("   el minero enseña ese ritmo y los KIA/h por GFLOPS en cada resumen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
