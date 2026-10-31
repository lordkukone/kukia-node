"""Ejecuta la bateria de regresion de KUKIA y devuelve 0 solo si pasa todo.

Uso:
    python pruebas_regresion/ejecutar_todo.py            # todo lo rapido
    python pruebas_regresion/ejecutar_todo.py --con-ab   # y la A/B de /stats (~1 min mas)
    python pruebas_regresion/ejecutar_todo.py --solo-suite   # solo las de aqui
    python pruebas_regresion/ejecutar_todo.py -k stats   # filtra por nombre

Cada prueba es un script independiente que imprime PASS/FAIL y devuelve su
codigo de salida: este lanzador no reimplementa nada, solo los corre en serie y
resume. Los artefactos (resultado_*.json) se escriben donde cada prueba decida;
aqui se guarda el registro completo en un directorio temporal.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent

# (nombre, ruta relativa a la raiz del repositorio)
SUITE = [
    ("minero: detector de dispositivo esteril (M-02)", "pruebas_regresion/prueba_detector_esteril.py"),
    ("claves: la regla de vigencia vive en un solo sitio", "pruebas_regresion/prueba_regla_claves.py"),
    ("core: identidad de /stats y huella de /version", "pruebas_regresion/prueba_identidad_stats.py"),
    ("panel: desglose ok/fallo de las archivadas", "pruebas_regresion/prueba_panel_desglose.py"),
]

# La A/B se queda fuera de la pasada rapida: arranca DOS Cores y mide 12 s en
# cada uno. Su invariante ya la cubre prueba_identidad_stats.py; esto es la
# prueba de que el defecto existia (antes) y de que ya no (despues).
AB = [
    ("core: A/B de coherencia de /stats (F2-59)", "pruebas_regresion/ab_stats/prueba_ab_stats.py"),
]

VERSIONADAS = [
    ("minero: NUL, rotacion y cupo (F2-53)", "informes_mainnet_20261030/f2-53_minero/pruebas_f2_53.py"),
    ("minero: aviso HILO_MUERTO (F2-53)", "informes_mainnet_20261030/f2-53_minero/prueba_salud_f253.py"),
    ("minero: telemetria y cupo acotado (F2-54)", "informes_mainnet_20261030/f2-54_minero_1_0/pruebas_f2_54.py"),
    ("core: desglose de archivadas, varias rondas (F2-57)", "informes_mainnet_20261030/f2-57_desglose_archivadas/prueba_f2_57.py"),
]


def correr(nombre: str, rel: str, registro: Path) -> tuple[bool, float, str]:
    ruta = RAIZ / rel
    if not ruta.exists():
        return False, 0.0, f"no existe {rel}"
    t0 = time.time()
    # PYTHONIOENCODING/PYTHONUTF8: sin esto, una prueba que imprime un caracter
    # fuera de cp1252 revienta al escribir en la tuberia y el lanzador solo ve
    # un codigo 2 con la salida vacia (paso con pruebas_f2_54 el 31-10-2026).
    entorno = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    p = subprocess.run([sys.executable, str(ruta)], cwd=str(RAIZ), env=entorno,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=1800)
    dur = time.time() - t0
    salida = (p.stdout or "") + (p.stderr or "")
    with open(registro, "a", encoding="utf-8") as f:
        f.write(f"\n{'=' * 70}\n{nombre}  ({rel})  codigo={p.returncode}  {dur:.1f}s\n{'=' * 70}\n")
        f.write(salida)
    resumen = ""
    for linea in reversed(salida.splitlines()):
        s = linea.strip()
        if s.startswith("{"):
            resumen = s
            break
    if not resumen and p.returncode != 0:
        # Sin resumen y con error: lo util es la ultima linea, no el silencio.
        lineas = [l.strip() for l in salida.splitlines() if l.strip()]
        resumen = lineas[-1][:120] if lineas else f"sin salida (codigo {p.returncode})"
    if not resumen:
        resumen = next((l.strip() for l in reversed(salida.splitlines())
                        if "PASS" in l or "FAIL" in l), "")
    return p.returncode == 0, dur, resumen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo-suite", action="store_true",
                    help="no ejecutar las pruebas de informes_mainnet_20261030")
    ap.add_argument("--con-ab", action="store_true",
                    help="incluir la A/B de coherencia de /stats (2 Cores, ~1 min)")
    ap.add_argument("-k", "--filtro", default="", help="solo las que contengan este texto")
    args = ap.parse_args()

    lista = list(SUITE) if args.solo_suite else SUITE + VERSIONADAS
    # Con --con-ab entra la A/B; con -k tambien, para poder pedir solo esa.
    if args.con_ab or args.filtro:
        lista = lista + AB
    if args.filtro:
        lista = [t for t in lista if args.filtro.lower() in t[0].lower()]

    tmp = Path(tempfile.mkdtemp(prefix="kukia_regresion_"))
    registro = tmp / "registro.txt"
    salidas = AQUI / "salidas"
    inicio = time.time()
    print(f"[regresion] {len(lista)} pruebas | registro: {registro}\n")

    filas = []
    for nombre, rel in lista:
        print(f"--- {nombre}")
        paso, dur, resumen = correr(nombre, rel, registro)
        filas.append((paso, nombre, dur, resumen))
        print(f"    {'PASS' if paso else 'FAIL'}  {dur:.1f}s  {resumen[:120]}\n")

    print("=" * 78)
    print(f"{'res':6} {'seg':>7}  prueba")
    print("-" * 78)
    for paso, nombre, dur, resumen in filas:
        print(f"{'PASS' if paso else 'FAIL':6} {dur:7.1f}  {nombre}")
    fallos = [f for f in filas if not f[0]]
    print("=" * 78)
    print(f"{len(filas) - len(fallos)}/{len(filas)} pruebas PASS")
    # Artefactos de esta pasada: las pruebas escriben en salidas/ (ignorada por
    # git) con marca de tiempo, asi que correr la bateria no ensucia el arbol.
    nuevas = sorted(p.name for p in salidas.glob("*")
                    if p.is_file() and p.stat().st_mtime >= inicio) if salidas.exists() else []
    if nuevas:
        print(f"artefactos en {salidas}:")
        for n in nuevas:
            print(f"  {n}")
    if fallos:
        print("fallan: " + ", ".join(f[1] for f in fallos))
        print(f"detalle en {registro}")
    return 0 if not fallos else 1


if __name__ == "__main__":
    raise SystemExit(main())
