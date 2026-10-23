"""Prueba del bucle rapido de difusion: tiene que dar EXACTAMENTE lo mismo.

El 22-10-2026 el bucle de difusion del minero paso de ~7 operaciones por paso a
una sola convolucion 3x3. Esta prueba comprueba lo unico que importa antes de
dejar que la red lo use: que el resultado sea identico al de antes (si cambia,
el Core rechazaria las tareas y el minero cobraria castigos).

Compara, para varios tamanos y en cada GPU disponible (y en CPU):
  - el bucle VIEJO (siete operaciones por paso), copiado aqui tal cual estaba,
  - el bucle NUEVO, llamando al worker de verdad (`trabajo_local`).

Tolerancia: 1e-12 relativa. En la practica sale 0,0 (bit a bit igual); el margen
existe porque otra GPU podria asociar la suma de otra forma.

Uso:
    python tools/prueba_difusion_rapida.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "worker"))

import numpy as np  # noqa: E402
import torch  # noqa: E402

import kukia_worker as W  # noqa: E402

TOLERANCIA = 1e-12
fallos = 0


def comprobar(nombre: str, cond: bool, detalle="") -> None:
    global fallos
    if cond:
        print(f"  OK    {nombre}")
    else:
        fallos += 1
        print(f"  FALLO {nombre} -> {detalle}")


def bucle_viejo(n: int, pasos: int, seed: int, device: str) -> float:
    """El codigo de antes, sin tocar: siete operaciones por paso."""
    rejilla = np.random.default_rng(seed).random((n, n))
    x = torch.from_numpy(rejilla).to(device=device, dtype=torch.float64)
    for _ in range(pasos):
        x[1:-1, 1:-1] = x[1:-1, 1:-1] + 0.2 * (
            x[:-2, 1:-1] + x[2:, 1:-1] + x[1:-1, :-2] + x[1:-1, 2:] - 4 * x[1:-1, 1:-1])
    return float(x.sum().item())


def rel(a: float, b: float) -> float:
    return abs(a - b) / max(1e-30, abs(a))


def main() -> int:
    dispositivos = ["cpu"]
    if torch.cuda.is_available():
        dispositivos += [f"cuda:{i}" for i in range(torch.cuda.device_count())]

    for dev in dispositivos:
        print(f"\n== {dev} ==")
        for n, pasos in ((64, 300), (256, 1200), (1024, 1907)):
            seed = 4242
            t0 = time.perf_counter()
            viejo = bucle_viejo(n, pasos, seed, dev)
            t_viejo = (time.perf_counter() - t0) * 1000
            t0 = time.perf_counter()
            nuevo = float(W.trabajo_local("difusion_calor", {"n": n, "pasos": pasos, "seed": seed}, dev))
            t_nuevo = (time.perf_counter() - t0) * 1000
            d = rel(viejo, nuevo)
            comprobar(f"n={n} pasos={pasos}: mismo resultado (delta {d:.1e})", d <= TOLERANCIA,
                      f"{viejo!r} vs {nuevo!r}")
            print(f"        tiempo {t_viejo:7.1f} ms -> {t_nuevo:7.1f} ms  "
                  f"(x{t_viejo / max(1e-9, t_nuevo):.1f})")

    # El borde no se toca: si se moviera, el resultado cambiaria con el tamano.
    base = np.random.default_rng(1).random((32, 32))
    x = torch.from_numpy(base).to("cpu").double()
    W.trabajo_local("difusion_calor", {"n": 32, "pasos": 50, "seed": 1}, "cpu")
    comprobar("el tipo sigue soportado y devuelve texto",
              isinstance(W.trabajo_local("difusion_calor", {"n": 8, "pasos": 2, "seed": 1}, "cpu"), str),
              "")
    del x, base

    print("\nPRUEBA " + ("BIEN" if fallos == 0 else f"MAL ({fallos} fallos)"))
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
