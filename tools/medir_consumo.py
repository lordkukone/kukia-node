"""Mide los vatios REALES de este PC mientras mina, y con carga sintetica.

QUE MIDE Y QUE NO (importante, para no inventar nada)
  SI se puede leer:
    - Vatios de cada GPU NVIDIA (nvidia-smi, lo mide el driver: es real).
    - Uso de GPU, reloj y temperatura.
    - Uso de CPU en % (GetSystemTimes de Windows).
  NO se puede leer en este PC:
    - Vatios de la CPU. Windows no expone la potencia del paquete Intel sin un
      driver de sensores (RAPL/WinRing0) que aqui no esta instalado. Tampoco hay
      contador de energia ni medidor de pared.
    - Vatios totales de la torre, por lo mismo.
  Por eso el programa imprime "CPU: no medible" en vez de inventarse un numero.
  Para cerrar ese hueco hace falta un enchufe medidor o instalar un lector de
  sensores (LibreHardwareMonitor / HWiNFO).

MODOS
  --carga ninguna   Solo mira (por ejemplo, mientras el minero trabaja).
  --carga gpu       Satura las GPU con las MISMAS funciones del minero
                    (matriz y difusion en float64) y mide vatios a tope.
  --carga cpu       Satura la CPU con el kernel de difusion (float64).

SALIDA
  Media, minimo y maximo de vatios por GPU, uso medio de GPU y de CPU, y los
  kWh/hora de las GPU a esa potencia. El KIA/hora no lo inventa: se saca del
  ledger (ver la nota final del programa).

USO
  python tools/medir_consumo.py --segundos 90 --carga ninguna
  python tools/medir_consumo.py --segundos 60 --carga gpu
  python tools/medir_consumo.py --segundos 60 --carga cpu --hilos 10
"""
import argparse
import ctypes
import os
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- CPU % ------
class _UsoCPU:
    """Uso de CPU sin dependencias: GetSystemTimes (idle, kernel, user)."""

    def __init__(self):
        self._ult = self._leer()

    @staticmethod
    def _leer():
        idle, kernel, user = (ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong())
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel),
                                              ctypes.byref(user))
        return idle.value, kernel.value, user.value

    def uso(self) -> float:
        i1, k1, u1 = self._ult
        time.sleep(0.2)
        i2, k2, u2 = self._leer()
        self._ult = (i2, k2, u2)
        total = (k2 - k1) + (u2 - u1)          # kernel incluye el idle
        if total <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (total - (i2 - i1)) / total))


# --------------------------------------------------------------- GPU W -------
def vatios_gpu() -> list:
    """[(indice, nombre, W, uso%, MHz, C)] de nvidia-smi. Lista vacia si no hay."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,power.draw,utilization.gpu,"
                           "clocks.sm,temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10)
    except Exception:
        return []
    salida = []
    for linea in (r.stdout or "").strip().splitlines():
        partes = [c.strip() for c in linea.split(",")]
        if len(partes) < 6:
            continue
        try:
            salida.append({"i": int(partes[0]), "nombre": partes[1],
                           "w": float(partes[2]), "uso": float(partes[3]),
                           "mhz": float(partes[4]), "c": float(partes[5])})
        except ValueError:
            continue
    return salida


# ------------------------------------------------------------- cargas -------
def _saturar_gpu(parar: threading.Event, n_gpu: int):
    """Bucle de matriz y difusion en float64, las mismas funciones del minero."""
    import torch
    sys.path.insert(0, str(RAIZ / "worker"))
    from kukia_worker import trabajo_local

    def _hilo(idx):
        dev = f"cuda:{idx}"
        tareas = [("matriz", {"n": 1024, "seed": 1}),
                  ("difusion_calor", {"n": 1024, "pasos": 400, "seed": 2})]
        k = 0
        while not parar.is_set():
            tipo, p = tareas[k % 2]
            k += 1
            try:
                trabajo_local(tipo, p, dev)
            except Exception:
                break

    for i in range(n_gpu):
        threading.Thread(target=_hilo, args=(i,), daemon=True).start()


def _saturar_cpu(parar: threading.Event, hilos: int):
    """Bucle de difusion en CPU con torch, float64, todos los hilos pedidos."""
    import torch
    sys.path.insert(0, str(RAIZ / "worker"))
    from kukia_worker import trabajo_local
    torch.set_num_threads(max(1, hilos))

    def _hilo():
        while not parar.is_set():
            try:
                trabajo_local("difusion_calor", {"n": 512, "pasos": 300, "seed": 3}, "cpu")
            except Exception:
                break

    for _ in range(max(1, hilos // 2)):
        threading.Thread(target=_hilo, daemon=True).start()


# ------------------------------------------------------------------ main -----
def main() -> int:
    ap = argparse.ArgumentParser(description="Vatios reales de GPU y uso de CPU")
    ap.add_argument("--segundos", type=float, default=90.0)
    ap.add_argument("--carga", choices=("ninguna", "gpu", "cpu"), default="ninguna")
    ap.add_argument("--hilos", type=int, default=8)
    ap.add_argument("--guardar", action="store_true")
    args = ap.parse_args()

    parar = threading.Event()
    if args.carga == "gpu":
        try:
            import torch
            n = torch.cuda.device_count()
        except Exception:
            n = 0
        if n <= 0:
            print("sin GPU CUDA: no hay nada que saturar")
            return 2
        _saturar_gpu(parar, n)
        print(f"[carga] GPU x{n} al 100 % con matriz y difusion float64", flush=True)
    elif args.carga == "cpu":
        try:
            import torch
        except Exception:
            print("falta torch para la carga de CPU")
            return 2
        _saturar_cpu(parar, args.hilos)
        print(f"[carga] CPU al 100 % ({args.hilos} hilos de torch, difusion float64)",
              flush=True)
    else:
        print("[carga] ninguna: se mide lo que este pasando ahora mismo", flush=True)

    time.sleep(3.0)                      # estabilizar (relojes, turbos, cache)
    uso_cpu = _UsoCPU()
    muestras, cpu_muestras = [], []
    t0 = time.time()
    while time.time() - t0 < args.segundos:
        g = vatios_gpu()
        if g:
            muestras.append(g)
        cpu_muestras.append(uso_cpu.uso())
        time.sleep(0.8)
    parar.set()

    if not muestras:
        print("nvidia-smi no devolvio vatios (sin GPU NVIDIA o sin permisos)")
        return 2

    por_gpu = {}
    for m in muestras:
        for d in m:
            por_gpu.setdefault(d["i"], {"nombre": d["nombre"], "w": [], "uso": [], "mhz": [], "c": []})
            por_gpu[d["i"]]["w"].append(d["w"])
            por_gpu[d["i"]]["uso"].append(d["uso"])
            por_gpu[d["i"]]["mhz"].append(d["mhz"])
            por_gpu[d["i"]]["c"].append(d["c"])

    print(f"\nventana {args.segundos:.0f} s · {len(muestras)} muestras")
    print(f"{'GPU':<28} {'W medio':>8} {'W min':>7} {'W max':>7} {'uso%':>6} {'MHz':>6} {'C':>5}")
    total_w = 0.0
    for i in sorted(por_gpu):
        d = por_gpu[i]
        media = statistics.mean(d["w"])
        total_w += media
        print(f"{d['nombre']:<28} {media:>8.1f} {min(d['w']):>7.1f} {max(d['w']):>7.1f} "
              f"{statistics.mean(d['uso']):>6.0f} {statistics.mean(d['mhz']):>6.0f} "
              f"{statistics.mean(d['c']):>5.1f}")
    print(f"{'TOTAL GPU':<28} {total_w:>8.1f} W   -> {total_w/1000:.3f} kWh/hora")
    cpu_media = statistics.mean(cpu_muestras) if cpu_muestras else 0.0
    print(f"{'CPU':<28} {'no medible':>8}   uso medio {cpu_media:.0f} %")
    print("\nLa CPU no tiene sensor de potencia accesible en este PC (ni RAPL ni contador "
          "de energia): su consumo hay que medirlo con un enchufe medidor o con un lector "
          "de sensores instalado.")

    if args.guardar:
        import json
        from datetime import datetime
        dest = RAIZ / "medidas"
        dest.mkdir(exist_ok=True)
        datos = {"cuando": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 "segundos": args.segundos, "carga": args.carga,
                 "cpu_pct_medio": round(cpu_media, 1), "cpu_w": None,
                 "gpu": {str(i): {"nombre": d["nombre"],
                                  "w_medio": round(statistics.mean(d["w"]), 2),
                                  "w_max": round(max(d["w"]), 2),
                                  "uso_medio": round(statistics.mean(d["uso"]), 1)}
                         for i, d in por_gpu.items()},
                 "gpu_w_total": round(total_w, 2)}
        ruta = dest / f"consumo_{args.carga}_{datetime.now():%Y%m%d_%H%M%S}.json"
        ruta.write_text(json.dumps(datos, indent=2) + "\n", encoding="utf-8")
        print(f"\nguardado: {ruta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
