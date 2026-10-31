"""Ejecuta la bateria de regresion de KUKIA y devuelve 0 solo si pasa todo.

Uso:
    python pruebas_regresion/ejecutar_todo.py            # todo
    python pruebas_regresion/ejecutar_todo.py --solo-suite   # solo las de aqui
    python pruebas_regresion/ejecutar_todo.py -k stats   # filtra por nombre

Cada prueba es un script independiente que imprime PASS/FAIL y devuelve su
codigo de salida: este lanzador no reimplementa nada, solo los corre en serie y
resume. Los artefactos (resultado_*.json) se escriben donde cada prueba decida;
aqui se guarda el registro completo en un directorio temporal.
"""
from __future__ import annotations

import argparse
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
    p = subprocess.run([sys.executable, str(ruta)], cwd=str(RAIZ),
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
    if not resumen:
        resumen = next((l.strip() for l in reversed(salida.splitlines())
                        if "PASS" in l or "FAIL" in l), "")
    return p.returncode == 0, dur, resumen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo-suite", action="store_true",
                    help="no ejecutar las pruebas de informes_mainnet_20261030")
    ap.add_argument("-k", "--filtro", default="", help="solo las que contengan este texto")
    args = ap.parse_args()

    lista = list(SUITE) if args.solo_suite else SUITE + VERSIONADAS
    if args.filtro:
        lista = [t for t in lista if args.filtro.lower() in t[0].lower()]

    tmp = Path(tempfile.mkdtemp(prefix="kukia_regresion_"))
    registro = tmp / "registro.txt"
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
    if fallos:
        print("fallan: " + ", ".join(f[1] for f in fallos))
        print(f"detalle en {registro}")
    return 0 if not fallos else 1


if __name__ == "__main__":
    raise SystemExit(main())
