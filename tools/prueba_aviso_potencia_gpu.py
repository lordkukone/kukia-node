#!/usr/bin/env python3
"""Prueba: (1) el minero reintenta CUDA al arrancar y (2) el guardia avisa
cuando el equipo tiene GPU pero el nodo no reporta potencia medida.

POR QUE EXISTE (26-10-2026): los dos fallos que arregla esto son silenciosos.
Un minero que arranca antes que el driver se queda en CPU TODA la sesion (GPU
encendida, cero GFLOPS medidos) y el log no dice nada raro; y el guardia no
miraba la potencia, asi que la maquina podia estar dias minando lento sin que
nadie se enterara. Esta prueba reproduce los dos casos SIN GPU de verdad y sin
tocar el Core: en el minero se le mete un torch falso y en el guardia se le
inyectan la lista de GPU y lo que el nodo dice medir.

Uso:
    python tools/prueba_aviso_potencia_gpu.py          # las pruebas (no toca nada)
    python tools/prueba_aviso_potencia_gpu.py --real   # que ve el guardia AHORA
                                                       # (solo lectura)

Sale con 0 si todo pasa y 1 si algo falla.
"""
import argparse
import sys
import time
from pathlib import Path
from types import SimpleNamespace

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))
sys.path.insert(0, str(RAIZ / "worker"))

FALLOS = []


def ok(que: str, detalle: str = "") -> None:
    print(f"  OK    {que}" + (f"   ({detalle})" if detalle else ""))


def fallo(que: str, detalle: str = "") -> None:
    FALLOS.append(que)
    print(f"  FALLO {que}" + (f"   ({detalle})" if detalle else ""))


# ============================================================
#  1. MINERO: reintento de la deteccion de CUDA
# ============================================================
def prueba_minero_cuda() -> None:
    print("\n1) MINERO - reintento de CUDA al arrancar")
    import kukia_worker as w

    # Esperas de prueba: 50 ms en vez de 10 s. Los intentos se dejan como
    # estan a proposito: probar con menos intentos no probaria el reintento.
    espera_de_verdad = w.CUDA_ESPERA_SEG
    w.CUDA_ESPERA_SEG = 0.05
    guardado_torch = sys.modules.get("torch")

    def con_torch(falso):
        if falso is None:
            sys.modules.pop("torch", None)
        else:
            sys.modules["torch"] = falso

    try:
        # (a) CUDA falla las dos primeras veces y aparece en la tercera: el
        #     minero NO puede quedarse en CPU, tiene que reintentar y usarla.
        class CudaTarda:
            def __init__(self):
                self.llamadas = 0

            def device_count(self):
                self.llamadas += 1
                if self.llamadas < 3:
                    raise RuntimeError("driver aun no listo")
                return 2

        cuda = CudaTarda()
        con_torch(SimpleNamespace(cuda=cuda))
        n, motivo = w._detectar_gpus()
        if n == 2 and cuda.llamadas == 3:
            ok("reintenta hasta que CUDA aparece", f"{cuda.llamadas} intentos, {n} GPU")
        elif n == 2:
            fallo("reintenta hasta que CUDA aparece",
                  f"salio bien pero hizo {cuda.llamadas} intentos")
        else:
            fallo("reintenta hasta que CUDA aparece",
                  f"devolvio {n} GPU con motivo '{motivo}' (se quedo en CPU)")

        # (b) CUDA nunca aparece: se cae a CPU con el motivo escrito, y agota
        #     los intentos en vez de rendirse en el primero.
        class CudaCiego:
            def __init__(self):
                self.llamadas = 0

            def device_count(self):
                self.llamadas += 1
                return 0

        ciego = CudaCiego()
        con_torch(SimpleNamespace(cuda=ciego))
        n, motivo = w._detectar_gpus()
        if n == 0 and ciego.llamadas == w.CUDA_INTENTOS and "no ve ninguna GPU" in motivo:
            ok("sin GPU agota los intentos y dice por que",
               f"{ciego.llamadas} intentos, motivo: {motivo}")
        else:
            fallo("sin GPU agota los intentos y dice por que",
                  f"{n} GPU, {ciego.llamadas} intentos, motivo '{motivo}'")

        # (c) torch no esta instalado: es definitivo, no se espera nada.
        con_torch(None)
        sys.modules["torch"] = None       # importar esto lanza ImportError
        t0 = time.time()
        n, motivo = w._detectar_gpus()
        tardado = time.time() - t0
        if n == 0 and "no esta instalado" in motivo and tardado < 1.0:
            ok("sin torch no se pierde el tiempo reintentando",
               f"{tardado * 1000:.0f} ms, motivo: {motivo}")
        else:
            fallo("sin torch no se pierde el tiempo reintentando",
                  f"{tardado:.1f} s, motivo '{motivo}'")

        # (d) El tope de espera es acotado: nadie se queda mirando al driver
        #     para siempre (con los valores por defecto, ~50 s como mucho).
        tope = w.CUDA_INTENTOS * espera_de_verdad
        if tope <= 120:
            ok("la espera esta acotada", f"{w.CUDA_INTENTOS} intentos x "
               f"{espera_de_verdad:.0f} s = {tope:.0f} s como mucho")
        else:
            fallo("la espera esta acotada", f"{tope:.0f} s es demasiado")
    finally:
        w.CUDA_ESPERA_SEG = espera_de_verdad
        sys.modules.pop("torch", None)
        if guardado_torch is not None:
            sys.modules["torch"] = guardado_torch


# ============================================================
#  2. GUARDIA: aviso por GPU sin potencia medida
# ============================================================
def prueba_guardia_aviso() -> None:
    print("\n2) GUARDIA - aviso por GPU sin potencia medida")
    import guardia as g

    enviados = []
    avisar_original = g._avisar
    g._avisar = lambda titulo, texto="": (enviados.append((titulo, texto)), True)[1]
    # Se prueba con la maquina inyectada: 1 GPU en el equipo y el nodo sin
    # medir nada. Nada de esto toca el Core ni el ledger.
    sin_medir = {"por_tarea": 0.0, "benchmark": 0.0, "nodos": 1}
    midiendo = {"por_tarea": 41.7, "benchmark": 42.2, "nodos": 1}
    gpu = ["NVIDIA GeForce RTX 5070"]
    pagando = g.Estado()
    pagando.ultimo_ok_ts = time.time()
    parado = g.Estado()
    parado.ultimo_ok_ts = 0.0

    try:
        # (a) GPU en el equipo + nodo cobrando + 0 GFLOPS medidos -> AVISA.
        g._ULTIMO_AVISO_POTENCIA = 0.0
        enviados.clear()
        texto = g._aviso_potencia({}, pagando, False, gpus=gpu, medido=sin_medir)
        if texto and enviados and "sin potencia medida" in enviados[0][0]:
            ok("avisa con GPU y cero potencia medida", f"titulo: {enviados[0][0]}")
        else:
            fallo("avisa con GPU y cero potencia medida",
                  f"texto={texto!r} enviados={enviados!r}")

        # (b) El mismo fallo no se repite en cada vuelta (30 min por defecto).
        texto2 = g._aviso_potencia({}, pagando, False, gpus=gpu, medido=sin_medir)
        if texto2 is None and len(enviados) == 1:
            ok("no repite el mismo aviso en cada vuelta",
               f"{len(enviados)} aviso, intervalo {g.POTENCIA_AVISO_SEG:.0f} s")
        else:
            fallo("no repite el mismo aviso en cada vuelta",
                  f"texto={texto2!r}, enviados={len(enviados)}")

        # (c) Si el nodo SI mide potencia, no se avisa y ademas se rearma.
        g._ULTIMO_AVISO_POTENCIA = 0.0
        enviados.clear()
        texto = g._aviso_potencia({}, pagando, False, gpus=gpu, medido=midiendo)
        if texto is None and not enviados:
            ok("callado si el nodo mide potencia", f"{midiendo['por_tarea']} GFLOPS/tarea")
        else:
            fallo("callado si el nodo mide potencia", f"texto={texto!r}")

        # (d) Sin GPU en el equipo no hay nada que reclamar.
        enviados.clear()
        texto = g._aviso_potencia({}, pagando, False, gpus=[], medido=sin_medir)
        if texto is None and not enviados:
            ok("callado si el equipo no tiene GPU")
        else:
            fallo("callado si el equipo no tiene GPU", f"texto={texto!r}")

        # (e) Si el nodo no cobra tareas, ese es otro problema (lo llevan los
        #     apartados del minero): aqui no se avisa.
        enviados.clear()
        texto = g._aviso_potencia({}, parado, False, gpus=gpu, medido=sin_medir)
        if texto is None and not enviados:
            ok("callado si el nodo no esta cobrando tareas")
        else:
            fallo("callado si el nodo no esta cobrando tareas", f"texto={texto!r}")

        # (f) Se puede apagar por entorno, que es lo que pide la propia ayuda.
        g.GPU_SIN_POTENCIA = False
        enviados.clear()
        texto = g._aviso_potencia({}, pagando, False, gpus=gpu, medido=sin_medir)
        g.GPU_SIN_POTENCIA = True
        if texto is None and not enviados:
            ok("KUKIA_GUARDIA_POTENCIA=0 lo apaga")
        else:
            fallo("KUKIA_GUARDIA_POTENCIA=0 lo apaga", f"texto={texto!r}")
    finally:
        g._avisar = avisar_original


def ver_estado_real() -> int:
    """Solo lectura: lo que el guardia veria ahora mismo en esta maquina."""
    print("\n--- Lo que ve el guardia AHORA (solo lectura) ---")
    import guardia as g

    nombres = g._gpus_del_equipo(ttl=0.0)
    print(f"GPU en el equipo (nvidia-smi): {nombres or 'ninguna'}")
    ids = g._ids_mineros_propios()
    print(f"nodos de este equipo (identidad_minero.json): {len(ids)}")
    try:
        p = g._potencia_reportada()
    except Exception as e:
        print(f"potencia reportada: no se pudo leer ({type(e).__name__}: {e})")
        return 1
    print(f"GFLOPS medidos por tarea (Core): {p['por_tarea']}")
    print(f"GFLOPS del benchmark (telemetria): {p['benchmark']}")
    print(f"nodos propios vistos en el Core: {p['nodos']}")
    hay = bool(p["por_tarea"] or p["benchmark"])
    print(f"veredicto: {'mide potencia -> no se avisaria' if hay else 'NO mide potencia'}"
          + ("" if not nombres else " (y hay GPU en el equipo)"))
    print("(no se ha avisado de nada: esto es solo la lectura)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Prueba del reintento de CUDA y del aviso de potencia")
    ap.add_argument("--real", action="store_true",
                    help="ademas de las pruebas, ensena lo que ve el guardia ahora (solo lectura)")
    args = ap.parse_args()

    for f in (sys.stdout, sys.stderr):
        try:
            f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    print("=" * 74)
    print(" PRUEBA: reintento de CUDA (minero) + aviso de potencia (guardia)")
    print("=" * 74)
    prueba_minero_cuda()
    prueba_guardia_aviso()

    if args.real:
        ver_estado_real()

    print("\n" + "=" * 74)
    if FALLOS:
        print(f" RESULTADO: {len(FALLOS)} fallo(s)")
        for f in FALLOS:
            print(f"   - {f}")
        return 1
    print(" RESULTADO: todo OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
