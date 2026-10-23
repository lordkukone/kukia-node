#!/usr/bin/env python3
"""Como va el minero, en una ventana de cmd. NO mina: mira.

El minero de produccion lo arranca la guardia oculto (sin ventana) y escribe
todo en worker/minero.log. Abrir otra ventana del minero no sirve: el cerrojo
worker/minero.lock se lo impide, porque dos mineros se reparten el cupo de
tareas y rinden la mitad.

Este visor contesta a "¿esto esta minando?" sin tocar nada: cada pocos segundos
pinta un bloque con el ritmo real (tareas y KIA por minuto, sacados de /stats
del Core), el estado de cada nodo (device, GFLOPS, tareas, KIA/min, ultimo
latido) y el ultimo pago. No escribe ni pide trabajo al Core.

Uso:
    python tools/mina_en_vivo.py             (bloque cada 5 s, Ctrl+C para salir)
    python tools/mina_en_vivo.py --seg 2     (mas seguido)
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent.parent
# Dos sitios segun quien arranque al minero: la guardia lo hace oculto y escribe
# en worker/minero.log; la ventana (VER_MINERO) lo copia a logs/minero_visible.log.
# Se lee el que se haya tocado mas recientemente.
CANDIDATOS = (RAIZ / "worker" / "minero.log", RAIZ / "logs" / "minero_visible.log")

def _log_vivo() -> Path:
    mejor, cuando = CANDIDATOS[0], -1.0
    for f in CANDIDATOS:
        try:
            m = f.stat().st_mtime
        except Exception:
            continue
        if m > cuando:
            mejor, cuando = f, m
    return mejor

LOG = _log_vivo()

# Misma resolucion que el minero: KUKIA_CORE, luego core_minero.txt, si no el
# Core de este PC.
CORE = os.environ.get("KUKIA_CORE") or "http://127.0.0.1:8300"
if not os.environ.get("KUKIA_CORE"):
    try:
        for linea in (RAIZ / "core_minero.txt").read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#"):
                CORE = linea
                break
    except Exception:
        pass

# "[KUKIA] minero_pc[cuda:0] 42.2 GFLOPS | 94 tareas/min | 0.462 KIA/min = ..."
RESUMEN = re.compile(r"\[KUKIA\] (\S+) ([\d.]+) GFLOPS \| (\d+) tareas/min \| "
                     r"([\d.]+) KIA/min")
COBRO = re.compile(r"\[KUKIA\] (\S+?) tarea (\w+) (\w+) -> OK \+([\d.eE+-]+) KIA "
                   r"\((\d+) ms\)")


def num(x, dec=3):
    """Numero con coma decimal (lo que espera ver Kuki), sin reventar por None."""
    try:
        return f"{float(x):,.{dec}f}".replace(",", "@").replace(".", ",").replace("@", ".")
    except Exception:
        return "-"


def cola_del_log(n_lineas=220):
    """Ultimas lineas del log sin leer 25 MB cada vez."""
    try:
        with open(_log_vivo(), "rb") as f:
            f.seek(0, 2)
            tam = f.tell()
            f.seek(max(0, tam - 400_000))
            texto = f.read().decode("utf-8", "replace")
    except Exception:
        return []
    return texto.splitlines()[-n_lineas:]


def foto():
    """(stats, nodos) del Core, o (None, None) si no responde."""
    try:
        stats = requests.get(CORE + "/stats", timeout=5).json()
    except Exception as e:
        return None, None, f"{type(e).__name__}: {e}"
    nodos = []
    try:
        nodos = requests.get(CORE + "/nodos", timeout=5).json()
    except Exception:
        pass
    return stats, nodos, None


def main():
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Visor del minero (no mina)")
    ap.add_argument("--seg", type=float, default=5.0, help="segundos entre bloques")
    a = ap.parse_args()

    print("KUKIA - como va el minero (NO mina, solo mira). Ctrl+C para salir.")
    print(f"Core: {CORE}   log: {LOG}")

    # Muestras para medir el ritmo. Con 5 s de diferencia el KIA/min baila
    # (el Core paga por tareas sueltas), asi que se mide sobre una ventana.
    ventana_seg = 20.0
    hist = []
    while True:
        stats, nodos, fallo = foto()
        lineas = cola_del_log()
        reloj = time.time()
        print("-" * 66, flush=True)
        print("  " + time.strftime("%H:%M:%S"), end="   ", flush=True)
        if fallo:
            print(f"Core SIN RESPUESTA en {CORE}  ({fallo})", flush=True)
            time.sleep(a.seg)
            continue

        tareas = int(stats.get("tareas_ok") or 0)
        supply = float(stats.get("supply_emitido") or 0)
        hist.append((reloj, tareas, supply))
        hist = [h for h in hist if reloj - h[0] <= 300.0]
        ref = next((h for h in hist if reloj - h[0] >= ventana_seg), None)
        if ref is None:
            faltan = max(0.0, ventana_seg - (reloj - hist[0][0]))
            print(f"midiendo el ritmo: faltan {faltan:.0f} s para tener una ventana "
                  f"de {ventana_seg:.0f} s", flush=True)
        else:
            min_ = (reloj - ref[0]) / 60.0
            print(f"{num((tareas - ref[1]) / min_, 0)} tareas/min   "
                  f"{num((supply - ref[2]) / min_)} KIA/min   "
                  f"{num((supply - ref[2]) / min_ * 60, 1)} KIA/h"
                  f"   (ventana {min_ * 60:.0f} s)", flush=True)

        integ = stats.get("integridad") or {}
        print(f"  Core: {integ.get('sellos', '?')} sellos, pagos "
              f"{'BLOQUEADOS' if integ.get('pagos_bloqueados') else 'libres'}   |   "
              f"cola {stats.get('tareas_pend', '?')}   |   supply {num(supply)} KIA",
              flush=True)
        dp = stats.get("detalle_pago") or {}
        print(f"  Precio: {num(dp.get('precio_gflop'), 7)} KIA/GFLOP x "
              f"{num(dp.get('gflop_por_min'), 1)} GFLOP/min = "
              f"{num(dp.get('emision_por_min'), 4)} KIA/min "
              f"(modo {dp.get('modo_pago', '?')})", flush=True)

        # Ritmo por nodo: ultima linea de resumen que escribio cada uno.
        ritmos = {}
        for l in reversed(lineas):
            m = RESUMEN.search(l)
            if m and m.group(1) not in ritmos:
                ritmos[m.group(1)] = m.groups()
        def latido_de(n):
            """Segundos desde el ultimo latido, o None si no consta."""
            try:
                v = float(n.get("visto") or 0)
            except Exception:
                return None
            return (reloj - v) if v > 0 else None

        # Los vivos primero; los nodos viejos de otros ensayos quedan al final
        # y se marcan, para que no parezca que la latencia es de 56 anios.
        vistos = sorted(nodos, key=lambda n: (latido_de(n) is None,
                                              latido_de(n) or 0))[:6]
        print("", flush=True)
        print("  nodo                          device    GFLOPS   tareas   KIA/min   latido", flush=True)
        for n in vistos:
            nombre = n.get("nombre") or "?"
            r = ritmos.get(nombre)
            km = num(r[3]) if r else "  -  "
            gf = num(r[1], 1) if r else num(n.get("gflops_medido"), 1)
            lat = latido_de(n)
            if lat is None:
                texto_lat = "sin latido"
            elif lat > 3600:
                texto_lat = num(lat / 3600, 1) + " h"
            else:
                texto_lat = num(lat, 1) + " s"
            print(f"  {nombre:<28} {str(n.get('device') or '-'):<9} "
                  f"{gf:>7} {num(n.get('tareas_ok'), 0):>9} "
                  f"{km:>8} {texto_lat:>9}", flush=True)

        # Las ultimas tareas tal cual las escribio el minero: el ritmo real.
        cobros = [m for m in (COBRO.search(l) for l in lineas) if m][-3:]
        if cobros:
            print("\n  ultimas tareas", flush=True)
            for m in cobros:
                # Las inferencias se pagan 4,3e-07 KIA: con 6 decimales sale 0.
                kia = f"{float(m.group(4)):.8f}".rstrip("0").replace(".", ",")
                print(f"    {m.group(1):<26} {m.group(3):<14} +{kia} KIA "
                      f"({m.group(5)} ms)", flush=True)
        if not vistos:
            print("  (el Core no devuelve nodos: revisa /nodos)", flush=True)
        time.sleep(a.seg)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[visor cerrado; el minero sigue]")
