"""Mide la potencia REAL de minado de esta maquina: todas las CPUs y todas las GPUs.

POR QUE EXISTE (23-10-2026)
  El pago por potencia cobra FLOPs de la tarea, pero esos FLOPs salen de una
  FORMULA (2n^3, 18n^2, 10n^2*pasos), no de una medida del hierro. Una RTX y un
  i9 valen lo mismo si las dos entregan el mismo resultado; lo que cambia es
  CUANTO TARDA cada una. Este programa mide eso: segundos por tarea, con las
  MISMAS funciones que ejecuta el minero (float64, mismo seed, misma
  convolucion), y devuelve UNA cifra comparable entre cualquier CPU y GPU:

      GFLOP de red por segundo  =  FLOPs de la tarea / segundos que tarda

  De ahi sale lo que de verdad gana cada dispositivo:
      KIA/dia = GFLOP/s de la mezcla x 86400 x precio_gflop

QUE MIDE (por dispositivo)
  matriz      n segun la dificultad de la red   FLOPs = 2n^3
  difusion    n=1024, pasos segun dificultad    FLOPs = 10n^2*pasos
  inferencia  n segun la dificultad de la red   FLOPs = 18n^2

COMO SE MIDE (para que el numero signifique algo)
  1. Mismo codigo que worker/kukia_worker.py -> trabajo_local(). Si el kernel
     cambia alli, hay que cambiarlo aqui o la medida deja de valer.
  2. Comprobacion de CORRECTITUD antes de medir: cada dispositivo calcula una
     version pequena de cada tarea y se compara con la referencia numpy del
     Core (exacta en matriz/inferencia, 1e-9 relativo en difusion). Si no
     cuadra, la medida de ese dispositivo se marca INVALIDA y no se publica.
  3. Calentamiento (asignacion de memoria, relojes de la GPU) y luego varias
     rondas con sincronizacion real de CUDA antes y despues de cronometrar.
  4. Se informa la MEDIANA (no la mejor): es lo que aguanta la tarjeta cuando
     lleva un rato trabajando, no el pico de los primeros milisegundos.
  5. Presupuesto por kernel: si una sola pasada se pasa, no se repite. Asi el
     programa termina en un tiempo acotado incluso en una CPU lenta.
  6. Difusion en un dispositivo lento: se mide con menos pasos y se escala de
     forma LINEAL (cada paso hace el mismo trabajo). Se marca `escalado: true`
     para que nadie confunda una medida con una estimacion.

LO QUE ESTO **NO** ES
  No es un numero que el nodo pueda usar para cobrar mas. El pago sigue saliendo
  del trabajo VERIFICADO y del reloj del Core (asignado -> entrega). Esto mide
  el hierro para saber que se puede pedir a cada maquina y para disenar la
  recompensa; no sustituye a la verificacion.

USO
  python tools/medir_potencia.py                      # CPU + todas las GPU, de una en una
  python tools/medir_potencia.py --solo cuda:1        # una sola GPU
  python tools/medir_potencia.py --paralelo           # todas a la vez (las dos GPU juntas)
  python tools/medir_potencia.py --rondas 7 --guardar # deja el JSON en medidas/
  python tools/medir_potencia.py --core http://1.2.3.4:8300   # dificultad de un Core remoto
"""
import argparse
import hashlib
import json
import os
import platform
import socket
import statistics
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Mezcla con la que el Core crea trabajo automatico (MEZCLA_AUTO en el Core):
# 4 matriz : 2 difusion : 1 inferencia. La mezcla importa porque una GPU que
# vuela en matriz y se arrastra en difusion no rinde lo que parece.
MEZCLA = (("matriz", 4.0 / 7.0), ("difusion_calor", 2.0 / 7.0), ("inferencia", 1.0 / 7.0))

# Dificultad de reserva si no hay Core a mano (la que estaba el 23-10-2026).
DIFICULTAD_DEF = {"matriz_n": 598, "inferencia_n": 149, "difusion_pasos": 1907}
PRECIO_DEF = None      # sin Core no se inventa precio: KIA/dia sale vacio


# ---------------------------------------------------------------- FLOPs ------
def flops_tarea(tipo: str, params: dict) -> float:
    """Misma formula que el Core (`_flops_tarea`) y que el minero. No tocar."""
    n = float((params or {}).get("n") or 0.0)
    if tipo == "matriz":
        return 2.0 * n ** 3
    if tipo == "inferencia":
        return 18.0 * n ** 2
    if tipo == "difusion_calor":
        return 10.0 * n ** 2 * float((params or {}).get("pasos") or 0.0)
    return 0.0


def params_de_red(dif: dict) -> dict:
    """Parametros de una tarea representativa de cada tipo, a la dificultad actual."""
    return {
        "matriz": {"n": int(dif["matriz_n"]), "seed": 1234},
        "difusion_calor": {"n": 1024, "pasos": int(dif["difusion_pasos"]), "seed": 1234},
        "inferencia": {"n": int(dif["inferencia_n"]), "seed": 1234},
    }


# ------------------------------------------------------------- Kernels -------
# Copia fiel de worker/kukia_worker.py -> trabajo_local(). float64 siempre: es
# el formato de la red (y el que hace la verificacion posible bit a bit).
def trabajo_local(tipo: str, params: dict, device: str):
    if tipo == "matriz":
        import torch
        n = int(params.get("n", 256))
        seed = int(params.get("seed", 42))
        idx = torch.arange(n, device=device, dtype=torch.float64)
        a = ((idx[:, None] * 31 + idx[None, :] * 17 + seed) % 97) - 48
        b = ((idx[:, None] * 31 + idx[None, :] * 17 + (seed + 1)) % 97) - 48
        return str(int(round(float((a @ b).sum().item()))))
    if tipo == "inferencia":
        import torch
        n = int(params.get("n", 64))
        seed = int(params.get("seed", 7))
        idx = torch.arange(n, device=device, dtype=torch.float64)
        x = ((idx[:, None] * 31 + idx[None, :] * 17 + seed) % 97) - 48
        k = torch.tensor([[1.0, 2.0, 1.0], [2.0, 4.0, 2.0], [1.0, 2.0, 1.0]],
                         device=device, dtype=torch.float64)
        out = 0.0
        for i in range(3):
            for j in range(3):
                out += float((x[i:n - 2 + i, j:n - 2 + j] * k[i, j]).sum().item())
        return str(int(round(out)))
    if tipo == "difusion_calor":
        import numpy as np
        import torch
        n = int(params.get("n", 256))
        pasos = int(params.get("pasos", 200))
        seed = int(params.get("seed", 42))
        rejilla = np.random.default_rng(seed).random((n, n))
        x = torch.from_numpy(rejilla).to(device=device, dtype=torch.float64)
        if str(device).startswith("cuda"):
            import torch.nn.functional as F
            lap_k = torch.tensor([[0.0, 1.0, 0.0],
                                  [1.0, -4.0, 1.0],
                                  [0.0, 1.0, 0.0]],
                                 dtype=torch.float64, device=device).view(1, 1, 3, 3)
            for _ in range(pasos):
                lap = F.conv2d(x.view(1, 1, n, n), lap_k)
                x[1:-1, 1:-1] += 0.2 * lap.view(n - 2, n - 2)
        else:
            for _ in range(pasos):
                x[1:-1, 1:-1] = x[1:-1, 1:-1] + 0.2 * (
                    x[:-2, 1:-1] + x[2:, 1:-1] + x[1:-1, :-2] + x[1:-1, 2:] - 4 * x[1:-1, 1:-1])
        return repr(float(x.sum().item()))
    raise ValueError(f"tipo no soportado: {tipo}")


# --------------------------------------------- Referencia (numpy, como el Core)
def referencia_numpy(tipo: str, params: dict):
    """Lo que el Core esperaria. Solo se usa en pequeno, para validar el kernel."""
    import numpy as np

    def gen_matriz(n, seed):
        idx = np.arange(n, dtype=np.float64)
        return (idx[:, None] * 31 + idx[None, :] * 17 + seed) % 97 - 48

    if tipo == "matriz":
        n, seed = int(params["n"]), int(params.get("seed", 42))
        a, b = gen_matriz(n, seed), gen_matriz(n, seed + 1)
        return str(int(round(float((a @ b).sum()))))
    if tipo == "inferencia":
        n, seed = int(params["n"]), int(params.get("seed", 7))
        x = gen_matriz(n, seed)
        k = np.array([[1.0, 2.0, 1.0], [2.0, 4.0, 2.0], [1.0, 2.0, 1.0]])
        out = 0.0
        for i in range(3):
            for j in range(3):
                out += float((x[i:n - 2 + i, j:n - 2 + j] * k[i, j]).sum())
        return str(int(round(out)))
    if tipo == "difusion_calor":
        n, pasos = int(params["n"]), int(params["pasos"])
        seed = int(params.get("seed", 42))
        x = np.random.default_rng(seed).random((n, n))
        for _ in range(pasos):
            x[1:-1, 1:-1] = x[1:-1, 1:-1] + 0.2 * (
                x[:-2, 1:-1] + x[2:, 1:-1] + x[1:-1, :-2] + x[1:-1, 2:] - 4 * x[1:-1, 1:-1])
        return repr(float(x.sum()))
    raise ValueError(tipo)


def iguales(tipo: str, a: str, b: str) -> bool:
    if tipo != "difusion_calor":
        return str(a).strip() == str(b).strip()
    try:
        fa, fb = float(a), float(b)
    except Exception:
        return False
    ref = max(abs(fa), abs(fb), 1e-12)
    return abs(fa - fb) / ref <= 1e-9


# ------------------------------------------------------------ Dispositivos ----
def dispositivos(solo: str = "") -> list:
    """[(etiqueta, device, hilos)] con CPU (1 y todos los nucleos) y cada GPU."""
    import torch
    lista = []
    total_hilos = torch.get_num_threads()
    if not solo or solo.lower() in ("cpu", "all", "todas"):
        lista.append((f"cpu (1 hilo)", "cpu", 1))
        if total_hilos > 1:
            lista.append((f"cpu ({total_hilos} hilos)", "cpu", total_hilos))
    if solo:
        s = solo.lower()
        if s.startswith("cuda"):
            nombre = s
            try:
                nombre = f"{s} {torch.cuda.get_device_properties(int(s.split(':')[1])).name}"
            except Exception:
                pass
            lista = [(nombre, s, 0)]
        elif s not in ("cpu", "all", "todas"):
            raise SystemExit(f"dispositivo desconocido: {solo}")
        return lista
    for i in range(torch.cuda.device_count()):
        nombre = torch.cuda.get_device_properties(i).name
        lista.append((f"cuda:{i} {nombre}", f"cuda:{i}", 0))
    return lista


def sincroniza(device: str) -> None:
    if str(device).startswith("cuda"):
        import torch
        torch.cuda.synchronize(device)


# ---------------------------------------------------------------- Medida -----
def una_pasada(tipo: str, params: dict, device: str) -> tuple:
    t0 = time.perf_counter()
    r = trabajo_local(tipo, params, device)
    sincroniza(device)
    return r, time.perf_counter() - t0


def medir_tipo(tipo: str, params: dict, device: str, rondas: int,
               presupuesto: float) -> dict:
    """Devuelve tiempos y marca si hubo que escalar la difusion."""
    p = dict(params)
    escalado = False
    # Difusion en un hierro lento: bajar pasos hasta que una pasada quepa.
    if tipo == "difusion_calor":
        while True:
            _, t = una_pasada(tipo, p, device)
            if t <= presupuesto / 3 or p["pasos"] <= 50:
                break
            p["pasos"] = max(50, p["pasos"] // 2)
            escalado = p["pasos"] != params["pasos"]
        tiempos, primera = [], t
    else:
        # Calentamiento fuera de la cuenta (asignacion de buffers, relojes).
        una_pasada(tipo, p, device)
        tiempos, primera = [], None

    if primera is None:
        _, primera = una_pasada(tipo, p, device)
    tiempos.append(primera)

    t_ini = time.perf_counter()
    while len(tiempos) < max(1, rondas):
        if primera * 1.6 > presupuesto:
            break
        if time.perf_counter() - t_ini > presupuesto:
            break
        _, t = una_pasada(tipo, p, device)
        tiempos.append(t)

    escala = (float(params["pasos"]) / float(p["pasos"])) if tipo == "difusion_calor" else 1.0
    med = statistics.median(tiempos) * escala
    fl = flops_tarea(tipo, params)          # FLOPs de la tarea REAL de la red
    return {
        "tipo": tipo,
        "n": p["n"],
        "pasos": params.get("pasos") if tipo == "difusion_calor" else None,
        "pasos_medidos": p["pasos"] if tipo == "difusion_calor" else None,
        "escalado": escalado,
        "pasadas": len(tiempos),
        "ms_mediana": round(med * 1000.0, 3),
        "ms_min": round(min(tiempos) * 1000.0 * escala, 3),
        "ms_max": round(max(tiempos) * 1000.0 * escala, 3),
        "gflop": round(fl / 1e9, 6),
        "gflops": round(fl / med / 1e9, 4) if med > 0 else 0.0,
    }


def comprobar_kernel(device: str) -> dict:
    """El kernel del dispositivo da el MISMO resultado que el Core? En pequeno."""
    pruebas = {
        "matriz": {"n": 160, "seed": 99},
        "inferencia": {"n": 64, "seed": 7},
        "difusion_calor": {"n": 128, "pasos": 20, "seed": 3},
    }
    fallos = []
    for tipo, p in pruebas.items():
        try:
            obtenido = trabajo_local(tipo, p, device)
            esperado = referencia_numpy(tipo, p)
            if not iguales(tipo, obtenido, esperado):
                fallos.append(f"{tipo}: {str(obtenido)[:40]} != {str(esperado)[:40]}")
        except Exception as e:
            fallos.append(f"{tipo}: {type(e).__name__}: {e}")
    return {"ok": not fallos, "fallos": fallos}


def medir_dispositivo(etiqueta: str, device: str, hilos: int, dif: dict,
                      rondas: int, presupuesto: float) -> dict:
    import torch
    if hilos:
        torch.set_num_threads(hilos)
    out = {"dispositivo": etiqueta, "device": device, "hilos": hilos,
           "correcto": comprobar_kernel(device), "tipos": {}}
    if not out["correcto"]["ok"]:
        out["valido"] = False
        return out
    params = params_de_red(dif)
    for tipo in ("matriz", "difusion_calor", "inferencia"):
        try:
            out["tipos"][tipo] = medir_tipo(tipo, params[tipo], device, rondas, presupuesto)
        except Exception as e:
            out["tipos"][tipo] = {"tipo": tipo, "error": f"{type(e).__name__}: {e}"}
    out["valido"] = all("error" not in v for v in out["tipos"].values())

    # --- Mezcla de la red: GFLOP/s cuando las tareas llegan 4:2:1 -----------
    F, T, ok = {}, {}, True
    for tipo, w in MEZCLA:
        d = out["tipos"].get(tipo, {})
        if "error" in d or not d:
            ok = False
            continue
        F[tipo] = d["gflop"]
        T[tipo] = d["ms_mediana"] / 1000.0
    if ok:
        seg_por_tarea = sum(w * T[t] for t, w in MEZCLA)
        gflop_por_tarea = sum(w * F[t] for t, w in MEZCLA)
        gps = gflop_por_tarea / seg_por_tarea if seg_por_tarea > 0 else 0.0
        out["mezcla"] = {
            "gflops": round(gps, 4),
            "tareas_por_min": round(60.0 / seg_por_tarea, 2) if seg_por_tarea > 0 else 0.0,
            "gflop_por_tarea": round(gflop_por_tarea, 4),
            "seg_por_tarea": round(seg_por_tarea, 4),
        }
    return out


# ------------------------------------------------------------------- Core ----
def leer_core(url: str) -> tuple:
    """(dificultad, precio_gflop) del Core vivo. Si no hay, reserva y None."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/stats", timeout=5) as r:
            d = json.loads(r.read().decode("utf-8"))
        dif = d.get("dificultad") or {}
        precio = (d.get("pago_por_potencia") or {}).get("precio_gflop")
        if not precio:
            precio = (d.get("detalle_pago") or {}).get("precio_gflop")
        # Trabajo que la red esta dando AHORA: sin esto, el numero de arriba se
        # lee como sueldo cuando solo es capacidad de hierro.
        tareas_min = None
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/red/serie?minutos=5", timeout=5) as r:
                serie = json.loads(r.read().decode("utf-8"))
            pts = (serie.get("puntos") if isinstance(serie, dict) else serie) or []
            vals = [int(p.get("tareas") or 0) for p in pts if isinstance(p, dict)]
            if vals:
                tareas_min = round(sum(vals) / len(vals), 1)
        except Exception:
            tareas_min = None
        return ({k: int(dif.get(k) or v) for k, v in DIFICULTAD_DEF.items()},
                float(precio) if precio else None, d.get("supply_emitido"), tareas_min)
    except Exception as e:
        print(f"[aviso] Core no disponible ({type(e).__name__}: {e}); uso dificultad de reserva")
        return dict(DIFICULTAD_DEF), PRECIO_DEF, None, None


# ------------------------------------------------------------------ Salida ---
def tabla(res: dict, precio: float) -> str:
    lineas = []
    cab = (f"{'dispositivo':<34} {'matriz':>11} {'difusion':>11} {'infer.':>10} "
           f"{'GFLOP/s':>9} {'tareas/min':>11} {'KIA/dia':>10}")
    lineas.append(cab)
    lineas.append("-" * len(cab))
    for d in res["dispositivos"]:
        if not d.get("valido"):
            lineas.append(f"{d['dispositivo']:<34} INVÁLIDO: "
                          + "; ".join(d["correcto"]["fallos"])[:80])
            continue
        t = d["tipos"]
        cel = []
        for tipo in ("matriz", "difusion_calor", "inferencia"):
            v = t.get(tipo, {})
            cel.append(f"{v.get('gflops', 0):>9.2f}" + ("*" if v.get("escalado") else " "))
        m = d.get("mezcla") or {}
        kia = (m.get("gflops", 0) * 86400.0 * precio) if precio else 0.0
        lineas.append(f"{d['dispositivo']:<34} {cel[0]:>11} {cel[1]:>11} {cel[2]:>10} "
                      f"{m.get('gflops', 0):>9.2f} {m.get('tareas_por_min', 0):>11} "
                      f"{(f'{kia:,.0f}' if precio else '—'):>10}")
    if any(t.get("escalado") for d in res["dispositivos"] if d.get("valido")
           for t in d["tipos"].values()):
        lineas.append("* medida con menos pasos y escalada (trabajo por paso identico)")
    return "\n".join(lineas)


def main() -> int:
    ap = argparse.ArgumentParser(description="Potencia real de minado (CPU y todas las GPU)")
    ap.add_argument("--core", default=os.environ.get("KUKIA_CORE", "http://127.0.0.1:8300"),
                    help="Core del que copiar dificultad y precio (por defecto loopback)")
    ap.add_argument("--solo", default="", help="cpu | cuda:0 | cuda:1 ... (por defecto todo)")
    ap.add_argument("--rondas", type=int, default=5, help="pasadas cronometradas por kernel")
    ap.add_argument("--presupuesto", type=float, default=40.0,
                    help="segundos maximos por kernel (una sola pasada si no cabe)")
    ap.add_argument("--paralelo", action="store_true",
                    help="medir todos los dispositivos A LA VEZ (las dos GPU juntas)")
    ap.add_argument("--guardar", action="store_true",
                    help="deja el resultado en medidas/potencia_<host>_<fecha>.json")
    args = ap.parse_args()

    try:
        import torch  # noqa: F401
    except Exception as e:
        print(f"Falta torch: {e}")
        return 2

    dif, precio, supply, tareas_min = leer_core(args.core)
    print(f"maquina: {socket.gethostname()} | {platform.system()} {platform.machine()} | "
          f"python {platform.python_version()}")
    print(f"dificultad de la red: matriz n={dif['matriz_n']} | "
          f"difusion 1024x{dif['difusion_pasos']} pasos | inferencia n={dif['inferencia_n']}")
    print(f"precio ahora: {precio} KIA/GFLOP" if precio else "precio: sin Core, no se calcula KIA/dia")
    if tareas_min is not None:
        print(f"la red esta dando: {tareas_min} tareas/min")
    print()

    devs = dispositivos(args.solo)
    resultados = []
    if args.paralelo and len(devs) > 1:
        import threading
        caja = [None] * len(devs)

        def _hilo(i, et, dev, hi):
            caja[i] = medir_dispositivo(et, dev, hi, dif, args.rondas, args.presupuesto)

        hilos = [threading.Thread(target=_hilo, args=(i, e, d, h)) for i, (e, d, h) in enumerate(devs)]
        [h.start() for h in hilos]
        [h.join() for h in hilos]
        resultados = [r for r in caja if r]
    else:
        for et, dev, hi in devs:
            print(f"midiendo {et} ...", flush=True)
            resultados.append(medir_dispositivo(et, dev, hi, dif, args.rondas, args.presupuesto))

    res = {
        "host": socket.gethostname(),
        "cuando": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "paralelo": bool(args.paralelo),
        "dificultad": dif,
        "precio_gflop": precio,
        "supply_emitido": supply,
        "tareas_red_por_min": tareas_min,
        "mezcla_pesos": {t: round(w, 4) for t, w in MEZCLA},
        "dispositivos": resultados,
    }
    print()
    print(tabla(res, precio or 0.0))
    validos = [d for d in resultados if d.get("valido")]
    # Solo tiene sentido comparar con la red si se han medido TODOS los
    # dispositivos: midiendo solo la CPU, "capacidad de esta maquina" seria
    # la capacidad de una parte y el porcentaje saldria disparatado.
    if validos and tareas_min and not args.solo:
        cap = sum((d.get("mezcla") or {}).get("tareas_por_min", 0.0) for d in validos)
        if cap:
            print(f"\ncapacidad de esta maquina: {cap:.0f} tareas/min de la mezcla; "
                  f"la red entrega {tareas_min}/min -> trabaja al {100*tareas_min/cap:.1f} % de su hierro")
    if not precio:
        print("\n(sin precio no hay KIA/dia: arranca el Core o pasa --core)")
    else:
        print(f"\nKIA/dia = GFLOP/s x 86400 x {precio}. Es TECHO DE HIERRO, no sueldo: la red "
              f"entera emite ~1.857 KIA/dia y el precio baja cuando entra mas potencia, "
              f"asi que lo que se cobra de verdad es tu parte del trabajo que la red reparte.")

    if args.guardar:
        destino = RAIZ / "medidas"
        destino.mkdir(exist_ok=True)
        texto = json.dumps(res, indent=2, ensure_ascii=False)
        ruta = destino / f"potencia_{res['host']}_{datetime.now():%Y%m%d_%H%M%S}.json"
        ruta.write_text(texto + "\n", encoding="utf-8")
        print(f"\nguardado: {ruta}")
        print(f"sha256:   {hashlib.sha256(texto.encode()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
