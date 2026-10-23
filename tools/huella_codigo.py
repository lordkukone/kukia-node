#!/usr/bin/env python3
"""Compara el codigo que CORRE con el que hay en el disco y en GitHub.

POR QUE EXISTE (auditoria del 23-10-2026): "una revision de main no demuestra
que codigo se esta ejecutando". El Core publica desde hoy el SHA-256 de los
ficheros clave en /version y en /stats. Este script lo lee y lo compara:

  1. Core vivo  -> /version de 127.0.0.1:8300 (hash del codigo CARGADO)
  2. Disco      -> sha256 de los mismos ficheros aqui
  3. GitHub     -> sha256 de los ficheros bajados de main (opcional, -g)

Si los tres coinciden, lo publicado, lo que hay en el PC y lo que esta
ejecutandose son lo mismo. Si el disco cambio despues de arrancar el Core, el
script lo dice: hay que reiniciar el Core para que cargue lo nuevo.

Uso:
    python tools/huella_codigo.py            # Core vivo vs disco
    python tools/huella_codigo.py -g         # y ademas contra GitHub
    python tools/huella_codigo.py --url http://127.0.0.1:8300
    python tools/huella_codigo.py --json     # salida para maquinas
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CORE = "http://127.0.0.1:8300"
REPO = "lordkukone/kukia-node"
RAMA = "main"


def sha256_corto(ruta: Path, largo: int = 16) -> str:
    try:
        return hashlib.sha256(ruta.read_bytes()).hexdigest()[:largo]
    except Exception:
        return ""


def pedir(url: str, tiempo: float = 15.0) -> bytes:
    pet = urllib.request.Request(url, headers={"User-Agent": "kukia-huella/1.0",
                                               "Cache-Control": "no-cache"})
    with urllib.request.urlopen(pet, timeout=tiempo) as r:
        return r.read()


def sha256_normalizado(ruta: Path, largo: int = 16) -> str:
    """sha256 ignorando finales de linea (CRLF en Windows, LF en el repo).

    Git guarda LF y en el disco se ve CRLF, asi que los bytes NO son iguales
    aunque el codigo sea el mismo. Para comparar con GitHub se normaliza; el
    hash que publica el Core sigue siendo el crudo, que es el del fichero que
    de verdad tiene cargado.
    """
    try:
        return hashlib.sha256(ruta.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:largo]
    except Exception:
        return ""


def desde_github(rel: str, largo: int = 16) -> str:
    """sha256 del fichero tal como esta en main (raw.githubusercontent)."""
    try:
        datos = pedir(f"https://raw.githubusercontent.com/{REPO}/{RAMA}/{rel}", 25)
        return hashlib.sha256(datos.replace(b"\r\n", b"\n")).hexdigest()[:largo]
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=CORE, help=f"Core a consultar (por defecto {CORE})")
    ap.add_argument("-g", "--github", action="store_true", help="comparar tambien con GitHub")
    ap.add_argument("--json", action="store_true", help="salida JSON")
    args = ap.parse_args()

    try:
        vivo = json.loads(pedir(args.url.rstrip("/") + "/version"))
    except urllib.error.HTTPError as e:
        print(f"ERROR: el Core contesto {e.code} en /version "
              f"(?esta arrancado con el codigo de hoy?)", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: no se pudo hablar con {args.url}/version: {e}", file=sys.stderr)
        return 2

    filas = []
    iguales_disco = iguales_github = 0
    for rel, datos in (vivo.get("ficheros") or {}).items():
        en_disco = sha256_corto(RAIZ / rel)
        fila = {"fichero": rel, "core": datos.get("sha256", ""), "disco": en_disco,
                "mtime": datos.get("mtime", ""), "coincide": datos.get("sha256") == en_disco}
        iguales_disco += 1 if fila["coincide"] else 0
        if args.github:
            fila["github"] = desde_github(rel)
            # Se compara normalizado: git guarda LF y aqui el fichero esta en CRLF.
            fila["coincide_github"] = fila["github"] == sha256_normalizado(RAIZ / rel)
            iguales_github += 1 if fila["coincide_github"] else 0
        filas.append(fila)

    if args.json:
        print(json.dumps({"core": vivo, "comparacion": filas,
                          "coinciden_disco": iguales_disco,
                          "total": len(filas)}, indent=2, ensure_ascii=False))
        return 0 if iguales_disco == len(filas) else 1

    print("=" * 74)
    print(f" Core {vivo.get('version')} · arrancado {vivo.get('arranque')} · "
          f"commit {vivo.get('commit') or '(sin repo)'}")
    print("=" * 74)
    cabecera = f" {'estado':4} {'fichero':36} {'Core (cargado)':17} {'disco':17}"
    if args.github:
        cabecera += " github"
    print(cabecera)
    for f in filas:
        estado = "OK" if f["coincide"] else "DIF"
        linea = f" {estado:4} {f['fichero']:36} {f['core']:17} {f['disco']:17}"
        if args.github:
            linea += " " + ("OK" if f.get("coincide_github") else "DIF")
        print(linea)
    print("-" * 74)
    if iguales_disco == len(filas):
        print(f" El Core vivo ejecuta EXACTAMENTE el codigo del disco ({len(filas)}/{len(filas)}).")
    else:
        print(f" OJO: {len(filas) - iguales_disco} fichero(s) del disco NO son los que corre el Core.")
        print(" Se han editado despues de arrancar: hay que REINICIAR el Core para que carguen.")
    if args.github:
        if iguales_github == len(filas):
            print(f" Y son tambien los de GitHub main ({iguales_github}/{len(filas)}).")
        else:
            print(f" OJO: {len(filas) - iguales_github} fichero(s) no coinciden con GitHub main:")
            for f in filas:
                if not f.get("coincide_github"):
                    print(f"   - {f['fichero']} (github {f.get('github') or 'no bajado'})")
            print(" Publica con tools/publicar_github.py para que lo publicado sea esto.")
    print(f" Comprobado {time.strftime('%Y-%m-%d %H:%M:%S')}")
    return 0 if iguales_disco == len(filas) else 1


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
