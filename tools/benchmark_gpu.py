#!/usr/bin/env python3
"""Mide lo que TU GPU da de verdad, con las mismas cuentas que la red.

Para que sirve:
  1. Saber antes de minar si la GPU esta bien (una GPU mal configurada da 10
     veces menos y se nota aqui, no en la factura de la luz).
  2. Que el minero pida el tier que puede sostener, en vez de adivinarlo por la
     VRAM que dice el driver.
  3. Comparar con lo que el Core mide despues (`nodos.gflops_medido`): si no
     cuadran, algo va mal en ese nodo.

Las cuentas son las mismas familias que las tareas de la red (producto de
matrices y difusion de calor), con la misma formula de FLOPs que usa el Core:
    matriz        : 2*n^3
    difusion_calor: 10*n^2*pasos

Uso:
    python tools/benchmark_gpu.py             # todas las GPU + CPU
    python tools/benchmark_gpu.py --json      # ademas guarda worker/benchmark.json
    python tools/benchmark_gpu.py --solo 0    # solo la GPU 0

Salida: 0 si midio algo; 2 si no hay torch ni GPU utilizable.
"""
import argparse
import json
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "worker" / "benchmark.json"

N_MATRIZ = 768          # 2*768^3 = 9,06e8 FLOPs por pasada
N_DIFUSION = 1024
PASOS_DIFUSION = 200


def _flops_matriz(n: int) -> float:
    return 2.0 * n ** 3


def _flops_difusion(n: int, pasos: int) -> float:
    return 10.0 * n ** 2 * pasos


def medir(torch, device: str, repeticiones: int = 5) -> dict:
    n = N_MATRIZ
    a = torch.rand((n, n), device=device, dtype=torch.float32)
    b = torch.rand((n, n), device=device, dtype=torch.float32)
    # Calentamiento: la primera multiplicacion incluye compilar kernels y
    # reservar memoria; contarla daria una medida pesimista y falsa.
    for _ in range(2):
        _ = a @ b
    torch.cuda.synchronize() if device.startswith("cuda") else None

    t0 = time.perf_counter()
    for _ in range(repeticiones):
        _ = a @ b
    if device.startswith("cuda"):
        torch.cuda.synchronize()
    seg_m = (time.perf_counter() - t0) / repeticiones
    g_m = _flops_matriz(n) / seg_m / 1e9

    # Difusion: bucle de vecinos, como la tarea de la red.
    x = torch.rand((N_DIFUSION, N_DIFUSION), device=device, dtype=torch.float32)
    for _ in range(3):
        y = x[1:-1, 1:-1] * 0.4 + (
            x[:-2, 1:-1] + x[2:, 1:-1] + x[1:-1, :-2] + x[1:-1, 2:]) * 0.15
        x[1:-1, 1:-1] = y
    if device.startswith("cuda"):
        torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(PASOS_DIFUSION):
        y = x[1:-1, 1:-1] * 0.4 + (
            x[:-2, 1:-1] + x[2:, 1:-1] + x[1:-1, :-2] + x[1:-1, 2:]) * 0.15
        x[1:-1, 1:-1] = y
    if device.startswith("cuda"):
        torch.cuda.synchronize()
    seg_d = time.perf_counter() - t0
    g_d = _flops_difusion(N_DIFUSION, PASOS_DIFUSION) / seg_d / 1e9

    del a, b, x, y
    if device.startswith("cuda"):
        torch.cuda.empty_cache()
    return {"matriz_gflops": round(g_m, 2), "difusion_gflops": round(g_d, 2),
            "gflops": round(min(g_m, g_d), 2),
            "seg_matriz": round(seg_m, 4), "seg_difusion": round(seg_d, 4)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Benchmark real de la GPU para KUKIA")
    ap.add_argument("--json", action="store_true", help="guardar worker/benchmark.json")
    ap.add_argument("--solo", type=int, default=-1, help="indice de GPU concreto")
    args = ap.parse_args()

    try:
        import torch
    except Exception as e:
        print(f"[benchmark] sin PyTorch ({type(e).__name__}): no se puede medir la GPU")
        return 2

    resultados = {"fecha": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "torch": getattr(torch, "__version__", "?"), "gpus": []}

    if torch.cuda.is_available():
        total = torch.cuda.device_count()
        indices = range(total) if args.solo < 0 else [args.solo]
        for i in list(indices)[:8]:
            try:
                props = torch.cuda.get_device_properties(i)
                vram = int(round(props.total_memory / (1024 ** 3)))
                r = medir(torch, f"cuda:{i}")
                r.update({"indice": i, "nombre": props.name, "vram_gb": vram})
                resultados["gpus"].append(r)
                print(f"[benchmark] GPU{i} {props.name} ({vram} GB): "
                      f"{r['gflops']} GFLOPS "
                      f"(matriz {r['matriz_gflops']} / difusion {r['difusion_gflops']})")
                # Lo que el Core exige como minimo para pagar el tier.
                print(f"            para tier por VRAM hacen falta "
                      f"{0.25 * vram:.1f} GFLOPS -> "
                      f"{'SI' if r['gflops'] >= 0.25 * vram else 'NO'}")
            except Exception as e:
                print(f"[benchmark] GPU{i}: no se pudo medir ({type(e).__name__}: {e})")
    else:
        print("[benchmark] sin CUDA: midiendo la CPU como referencia")
        r = medir(torch, "cpu", repeticiones=2)
        r.update({"indice": -1, "nombre": "cpu", "vram_gb": 0})
        resultados["gpus"].append(r)
        print(f"[benchmark] CPU: {r['gflops']} GFLOPS")

    if not resultados["gpus"]:
        return 2
    if args.json:
        SALIDA.parent.mkdir(parents=True, exist_ok=True)
        SALIDA.write_text(json.dumps(resultados, indent=2, ensure_ascii=False),
                          encoding="utf-8")
        print(f"[benchmark] guardado en {SALIDA.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
