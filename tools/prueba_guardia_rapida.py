#!/usr/bin/env python3
"""Prueba el reinicio rapido del guardia (sin tocar el Core de verdad).

QUE COMPRUEBA
  1. Un hijo que se muere se nota por el PROCESO (no por la red) y despierta
     al guardia en el acto, en vez de esperar al sondeo de /stats.
  2. `ciclo()` relanza ese hijo en cuanto ve que esta caido.
  3. `esperar()` corta la espera cuando muere un hijo (no espera 10 s).
  4. Parar un hijo a proposito NO cuenta como caida (no lo relanza).
  5. Un proceso vivo no se duplica.

No arranca el Core real ni escribe en el ledger: el hijo de prueba es un
`python -c "time.sleep(2)"`. Se usa un patron de proceso inventado y se
desactivan las comprobaciones de red para no chocar con el Core que este
corriendo en este PC.

Uso:  python tools/prueba_guardia_rapida.py
Sale con 0 si las 6 comprobaciones pasan, 1 si alguna falla.
"""
import importlib.util
import sys
import tempfile
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
resultados = []


def check(nombre, ok, detalle=""):
    resultados.append(bool(ok))
    print(("  [OK]   " if ok else "  [FALLO]") + f" {nombre}"
          + (f"  -> {detalle}" if detalle else ""), flush=True)


def cargar_guardia():
    spec = importlib.util.spec_from_file_location("guardia", RAIZ / "tools" / "guardia.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    g = cargar_guardia()

    # Aislamiento: patron de proceso que no existe y sin red.
    g.PATRONES["core"] = "prueba_guardia_proceso_que_no_existe"
    g._ya_corriendo = lambda *a, **k: False

    def sin_core(*a, **k):
        raise RuntimeError("core caido (prueba)")
    g._peticion = sin_core

    tmp = Path(tempfile.mkdtemp(prefix="kukia_guardia_"))
    hijo = g.Hijo("core", [sys.executable, "-c", "import time; time.sleep(2)"],
                  tmp, tmp / "hijo.log")
    # El puerto "escucha" si el hijo vive: es lo que pasa de verdad.
    g._puerto_abierto = lambda *a, **k: hijo.vivo()
    hijos = {"core": hijo}
    estado = g.Estado()

    print("== 1. proceso vivo: no se duplica ==")
    check("el hijo arranca", hijo.arrancar() is True, f"pid={hijo.proc.pid}")
    pid1 = hijo.proc.pid
    g.ciclo(hijos, estado, False)
    check("un ciclo con el hijo vivo no lanza otro", hijo.arranques == 1,
          f"arranques={hijo.arranques}")

    print("\n== 2. la muerte se nota por el proceso, no por la red ==")
    t0 = time.time()
    detectado = False
    while time.time() - t0 < 8:
        if g.DESPERTAR.is_set():
            detectado = True
            break
        time.sleep(0.05)
    dt = time.time() - t0
    check("el vigia avisa en cuanto muere (~2 s)", detectado and dt < 3.5,
          f"{dt:.2f} s")
    check("el hijo consta como caido", hijo.caido() is True)

    print("\n== 3. ciclo() lo relanza ya ==")
    # El freno anti-crashloop son 20 s desde el ultimo arranque; en la vida real
    # el Core lleva horas en pie, asi que se emula eso.
    hijo.ultimo_arranque = time.time() - 30
    g.DESPERTAR.clear()
    t1 = time.time()
    g.ciclo(hijos, estado, False)
    dt2 = time.time() - t1
    check("relanzado en el mismo ciclo", hijo.vivo() and hijo.proc.pid != pid1,
          f"pid {pid1} -> {hijo.proc.pid}, {dt2:.2f} s")
    check("el reinicio tarda menos de 30 s", dt2 < 30.0, f"{dt2:.2f} s")
    pid2 = hijo.proc.pid
    g.ciclo(hijos, estado, False)
    check("y no se lanza un tercero", hijo.arranques == 2 and hijo.proc.pid == pid2,
          f"arranques={hijo.arranques}")

    print("\n== 4. parar a proposito no es una caida ==")
    g.DESPERTAR.clear()
    hijo.detener()
    time.sleep(0.4)
    check("detener() no dispara el aviso de caida", not g.DESPERTAR.is_set())
    check("y no queda marcado como caido", hijo.caido() is False)

    print("\n== 5. esperar() corta la espera con el aviso ==")
    g.DESPERTAR.set()
    t2 = time.time()
    g.esperar(10)
    dt3 = time.time() - t2
    check("esperar(10) vuelve en el acto", dt3 < 1.5, f"{dt3:.2f} s")

    print("\n== 6. sin aviso si duerme lo que le piden ==")
    t3 = time.time()
    g.esperar(1.2)
    dt4 = time.time() - t3
    check("esperar(1.2) tarda ~1,2 s", 1.0 <= dt4 <= 3.0, f"{dt4:.2f} s")

    # VA AL FINAL: este hilo se queda mirando el puerto para siempre.
    print("\n== 7. sin nadie escuchando, avisa en ~2 s (Core de otro) ==")
    g.CORE = "http://127.0.0.1:8300"
    g._puerto_abierto = lambda *a, **k: False   # nadie escucha
    g._AVISO_PUERTO = 0.0
    g.DESPERTAR.clear()
    threading.Thread(target=g.vigia_core, daemon=True).start()
    t4 = time.time()
    avisado = g.DESPERTAR.wait(timeout=6)
    dt5 = time.time() - t4
    check("el vigia del puerto avisa sin esperar 30 s", avisado and dt5 < 4.0,
          f"{dt5:.2f} s")

    hijo.detener()
    ok = sum(1 for r in resultados if r)
    print(f"\n== RESUMEN == {ok}/{len(resultados)} pruebas OK")
    return 0 if ok == len(resultados) else 1


if __name__ == "__main__":
    for f in (sys.stdout, sys.stderr):
        try:
            f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main())
