#!/usr/bin/env python3
"""Deja los informes del proyecto visibles dentro de la carpeta del hilo.

EL PROBLEMA
    El visor de ficheros de Freebuff solo abre rutas que esten DENTRO de la
    carpeta del hilo (workspace). Los .md viven en Desktop\\KUKIA, asi que un
    enlace a "SEGURIDAD.md" desde el chat da "invalid path": la ruta es correcta
    para el proyecto, pero no existe en el workspace.

LA SOLUCION
    Enlaces duros (hard links) de cada .md del proyecto a una carpeta del
    workspace. Un enlace duro NO es una copia: es el mismo fichero en disco con
    dos nombres. Se edita uno y cambia el otro, no hay version que se quede
    vieja y no ocupa el doble. En el mismo volumen no hace falta ser admin.

    Si el enlace duro no es posible (otro disco, sistema de ficheros sin
    enlaces, Linux sin permisos), se copia y se avisa: ahi si puede quedarse
    desfasado.

USO
    python tools/enlazar_informes.py --destino "C:\\ruta\\al\\workspace\\informes"
    python tools/enlazar_informes.py --destino ... --copiar   (fuerza copia)
    python tools/enlazar_informes.py --destino ... --listar   (solo comprobar)

Sale con 0 si todos los informes quedaron accesibles, 1 si alguno fallo.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def enlazar(origen: Path, destino: Path, copiar: bool = False) -> str:
    """Crea el acceso en `destino`. Devuelve 'enlace', 'copia', 'ya' o 'fallo'."""
    if destino.exists():
        return "ya"
    if not copiar:
        try:
            os.link(origen, destino)          # mismo volumen: un solo fichero
            return "enlace"
        except Exception:
            if os.name == "nt":
                # os.link falla en algunos Windows/volumenes; mklink /H es lo
                # mismo por la puerta de atras.
                r = subprocess.run(["cmd", "/c", "mklink", "/H",
                                    str(destino), str(origen)],
                                   capture_output=True, text=True)
                if r.returncode == 0:
                    return "enlace"
    try:
        shutil.copy2(origen, destino)
        return "copia"
    except Exception as e:
        print(f"  [FALLO] {origen.name}: {e}", file=sys.stderr)
        return "fallo"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--destino", required=True,
                    help="carpeta del workspace donde dejar los informes")
    ap.add_argument("--copiar", action="store_true",
                    help="copiar en vez de enlazar (se queda desfasado)")
    ap.add_argument("--listar", action="store_true", help="solo comprobar")
    args = ap.parse_args()

    destino = Path(args.destino).expanduser()
    if not args.listar:
        destino.mkdir(parents=True, exist_ok=True)

    informes = sorted(RAIZ.glob("*.md"))
    if not informes:
        print("[enlazar] no hay .md en el proyecto", file=sys.stderr)
        return 1

    cuenta = {"enlace": 0, "copia": 0, "ya": 0, "fallo": 0}
    for f in informes:
        if args.listar:
            estado = "ya" if (destino / f.name).exists() else "fallo"
        else:
            estado = enlazar(f, destino / f.name, args.copiar)
        cuenta[estado] += 1

    print(f"[enlazar] {len(informes)} informes -> {destino}")
    print(f"  enlaces nuevos: {cuenta['enlace']} | copias: {cuenta['copia']} | "
          f"ya estaban: {cuenta['ya']} | fallos: {cuenta['fallo']}")
    if cuenta["copia"]:
        print("  AVISO: las copias no se actualizan solas; vuelve a lanzarlo "
              "si cambian los informes.", file=sys.stderr)
    return 0 if cuenta["fallo"] == 0 else 1


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
