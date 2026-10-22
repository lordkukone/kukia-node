#!/usr/bin/env python3
"""Prepara un PC para minar en KUKIA y dice en claro que falta.

Existe porque el 90 % de la gente que prueba a minar se queda en el primer
paso: Python sin PATH, falta PyTorch, el Core mal escrito... y el .cmd clásico
escupía un error de pip que no explica nada.

Uso:
    python tools/instalar_minero.py              # solo comprueba (no toca nada)
    python tools/instalar_minero.py --instalar   # instala lo que falte
    python tools/instalar_minero.py --core URL   # comprueba ese Core

Salida: 0 si el PC esta listo para minar; 1 si falta algo; 2 si falta algo que
no se puede arreglar solo (por ejemplo, no hay GPU).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MINIMO = (3, 10)

PAQUETES = [
    ("requests", "requests"),
    ("numpy", "numpy"),
    ("cryptography", "cryptography"),
    ("torch", "torch"),
]

fallos = []
avisos = []


def ok(msg: str) -> None:
    print(f"  [OK]    {msg}")


def falta(msg: str) -> None:
    print(f"  [FALTA] {msg}")
    fallos.append(msg)


def aviso(msg: str) -> None:
    print(f"  [AVISO] {msg}")
    avisos.append(msg)


def core_url(arg: str = "") -> str:
    """Direccion del Core ya resuelta (acepta la indireccion "auto ...")."""
    valor = arg.rstrip("/") if arg else "http://127.0.0.1:8300"
    if not arg:
        f = RAIZ / "core_minero.txt"
        try:
            for linea in f.read_text(encoding="utf-8", errors="replace").splitlines():
                linea = linea.strip()
                if linea and not linea.startswith("#"):
                    valor = linea.rstrip("/")
                    break
        except Exception:
            pass
    try:
        import core_destino
        return core_destino.resolver(valor)
    except Exception:
        # Espejo caido y sin copia: se devuelve tal cual y que falle el aviso
        # de "el Core no responde", que es el mensaje util para el usuario.
        return valor


def comprobar_python() -> None:
    v = sys.version_info
    if (v.major, v.minor) >= MINIMO:
        ok(f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        falta(f"Python {v.major}.{v.minor} es antiguo; hace falta "
              f"{MINIMO[0]}.{MINIMO[1]} o superior")


def comprobar_paquetes() -> list:
    import importlib.util
    faltan = []
    for modulo, paquete in PAQUETES:
        if importlib.util.find_spec(modulo) is not None:
            ok(f"{paquete}")
        else:
            falta(f"{paquete} (no instalado)")
            faltan.append(paquete)
    return faltan


def comprobar_gpu() -> None:
    try:
        import torch
    except Exception:
        falta("PyTorch: sin el no se puede calcular nada")
        return
    try:
        if torch.cuda.is_available():
            n = torch.cuda.device_count()
            nombres = []
            for i in range(min(n, 4)):
                p = torch.cuda.get_device_properties(i)
                nombres.append(f"{p.name} ({int(round(p.total_memory / 1024**3))} GB)")
            ok(f"CUDA disponible: {n} GPU(s) -> " + ", ".join(nombres))
        else:
            aviso("sin CUDA: se minaria en CPU, mucho mas lento y con poca recompensa")
    except Exception as e:
        aviso(f"no se pudo consultar CUDA ({type(e).__name__})")


def comprobar_core(url: str) -> None:
    try:
        req = urllib.request.Request(url + "/stats", headers={"User-Agent": "KUKIA-instalador/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        ok(f"Core accesible en {url} (version {d.get('version')}, "
           f"{d.get('nodos')} nodos, {d.get('tareas_pend')} en cola)")
    except Exception as e:
        falta(f"el Core no responde en {url} ({type(e).__name__}). Revisa "
              f"core_minero.txt o que el Core este encendido")


def comprobar_disco() -> None:
    try:
        libre = shutil.disk_usage(str(RAIZ)).free / 1024**3
        if libre < 5:
            aviso(f"quedan {libre:.1f} GB libres; PyTorch CUDA necesita ~3 GB")
        else:
            ok(f"disco: {libre:.1f} GB libres")
    except Exception:
        pass


def instalar(paquetes: list) -> bool:
    if not paquetes:
        print("\n[instalar] no falta ningun paquete")
        return True
    base = [sys.executable, "-m", "pip", "install", "--upgrade"]
    if "torch" in paquetes:
        print("\n[instalar] PyTorch es grande (~2,5 GB). Con GPU NVIDIA conviene "
              "la version CUDA:")
        print("           python -m pip install torch --index-url "
              "https://download.pytorch.org/whl/cu121")
        paquetes = [p for p in paquetes if p != "torch"]
    if paquetes:
        print(f"[instalar] {' '.join(paquetes)}")
        if subprocess.run(base + paquetes).returncode != 0:
            return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Prepara el PC para minar en KUKIA")
    ap.add_argument("--instalar", action="store_true", help="instala lo que falte")
    ap.add_argument("--core", default="", help="URL del Core a comprobar")
    args = ap.parse_args()

    print("=" * 62)
    print(" KUKIA - comprobacion del minero")
    print("=" * 62)
    print("\n[1] Python")
    comprobar_python()
    print("\n[2] Paquetes")
    faltan = comprobar_paquetes()
    print("\n[3] GPU")
    comprobar_gpu()
    print("\n[4] Core")
    url = core_url(args.core)
    comprobar_core(url)
    print("\n[5] Disco")
    comprobar_disco()

    if args.instalar and faltan:
        if not instalar(faltan):
            falta("la instalacion de paquetes fallo (mira el error de pip arriba)")

    print("\n" + "=" * 62)
    if not fallos:
        print(" LISTO PARA MINAR.")
        print(f" Arranca con: python worker/kukia_worker.py minero_pc")
        print(f" (o doble clic en ARRANCAR_MINERO.cmd; vigila con ARRANCAR_GUARDIA.cmd)")
        print("=" * 62)
        return 0
    print(f" FALTAN {len(fallos)} COSA(S):")
    for f in fallos:
        print(f"   - {f}")
    if avisos:
        print(" Avisos:")
        for a in avisos:
            print(f"   - {a}")
    print("=" * 62)
    return 2 if any("GPU" in f or "CUDA" in f for f in fallos) else 1


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
