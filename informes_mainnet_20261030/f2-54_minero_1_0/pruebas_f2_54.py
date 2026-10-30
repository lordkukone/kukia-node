"""Pruebas locales de F2-54 con el codigo REAL del worker (extraido con ast).

  A. Telemetria por GPU: `_gpu_stats` devuelve temperatura y VRAM libre de ESTA
     maquina (medido, no supuesto).
  B. Cupo acotado: `_soltar` no puede inflar un BoundedSemaphore y la liberacion
     de mas queda anotada; en concurrencia nunca se supera el maximo.
  C. Instancia unica: el cerrojo del minero impide un segundo proceso.
"""
import ast
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
FUENTE = RAIZ / "worker" / "kukia_worker.py"
RES = []


def check(nombre, ok, detalle=""):
    RES.append((nombre, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {nombre}" + (f"  [{detalle}]" if detalle else ""))


def extraer(nombres, ns):
    src = FUENTE.read_text(encoding="utf-8")
    for nodo in ast.parse(src).body:
        if isinstance(nodo, (ast.FunctionDef, ast.ClassDef)) and nodo.name in nombres:
            exec(compile(ast.Module(body=[nodo], type_ignores=[]), str(FUENTE), "exec"), ns)


def prueba_a():
    """Mide temperatura y VRAM libre reales con el codigo del worker."""
    ns = {"os": os, "subprocess": subprocess, "Path": Path}
    extraer(["_indice_gpu", "_gpu_stats"], ns)
    res = ns["_gpu_stats"]("cuda:0")
    check("A1. _gpu_stats devuelve 4 valores", len(res) == 4, str(res))
    util, usada, temp, libre = res
    check("A2. utilizacion y VRAM usada son numeros",
          isinstance(util, int) and isinstance(usada, int))
    check("A3. temperatura real de la GPU medida",
          isinstance(temp, int) and 20 <= temp <= 110, f"{temp} C")
    check("A4. VRAM libre medida y coherente",
          isinstance(libre, int) and 0 < libre < 16 * 1024, f"{libre} MB libres")
    res1 = ns["_gpu_stats"]("cuda:1")
    check("A5. la segunda GPU se mide por separado",
          len(res1) == 4 and isinstance(res1[2], int), f"{res1[2]} C")


def prueba_b():
    """El cupo no puede inflarse; en concurrencia no se pasa del maximo."""
    avisos = []
    ns = {"threading": threading, "_log_arranque": avisos.append,
          "en_vuelo": {"n": 0}, "en_vuelo_lock": threading.Lock(),
          "cupo_vuelo": threading.BoundedSemaphore(2)}
    src = FUENTE.read_text(encoding="utf-8")
    fuente = None
    for nodo in ast.walk(ast.parse(src)):
        if isinstance(nodo, ast.FunctionDef) and nodo.name == "_soltar":
            fuente = ast.get_source_segment(src, nodo)
            break
    exec(fuente, ns)
    soltar = ns["_soltar"]

    # Liberar de mas no infla: el contador esta en 0 y el semaforo lleno.
    for _ in range(5):
        soltar()
    check("B1. liberar de mas no infla el semaforo",
          ns["cupo_vuelo"]._value == 2, f"_value={ns['cupo_vuelo']._value}")
    check("B2. queda anotado en arranque.log",
          sum("liberacion de mas" in a for a in avisos) == 5, f"{len(avisos)} avisos")

    # Bypass del contador: el acotado lo rechaza (por eso es BoundedSemaphore).
    for _ in range(2):
        ns["cupo_vuelo"].acquire()
    rechazado = False
    try:
        ns["cupo_vuelo"].release()
        ns["cupo_vuelo"].release()
        ns["cupo_vuelo"].release()      # tercera: no habia adquisicion
    except ValueError:
        rechazado = True
    check("B3. el semaforo acotado rechaza el exceso", rechazado)

    # Concurrencia: 8 hilos, 200 vueltas, nunca mas de 2 a la vez.
    ns["cupo_vuelo"] = threading.BoundedSemaphore(2)
    ns["en_vuelo"]["n"] = 0
    dentro = [0]
    maximo = [0]
    candado = threading.Lock()

    def curro():
        for _ in range(200):
            ns["cupo_vuelo"].acquire()
            with ns["en_vuelo_lock"]:
                ns["en_vuelo"]["n"] += 1
            with candado:
                dentro[0] += 1
                maximo[0] = max(maximo[0], dentro[0])
            time.sleep(0.0002)
            with candado:
                dentro[0] -= 1
            soltar()

    hilos = [threading.Thread(target=curro) for _ in range(8)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    check("B4. nunca hay mas cupos en vuelo que el maximo", maximo[0] <= 2,
          f"maximo simultaneo={maximo[0]}")
    check("B5. el contador vuelve a cero y el semaforo queda lleno",
          ns["en_vuelo"]["n"] == 0 and ns["cupo_vuelo"]._value == 2,
          f"n={ns['en_vuelo']['n']} _value={ns['cupo_vuelo']._value}")
    check("B6. ninguna liberacion rechazada en uso correcto",
          not any("BoundedSemaphore rechaza" in a for a in avisos))


def prueba_c(worker_py: Path, cwd: Path):
    """Dos procesos en el mismo directorio: el segundo no debe minar."""
    env = dict(os.environ, KUKIA_CORE="http://127.0.0.1:8499")
    procs = []
    for _ in range(2):
        procs.append(subprocess.Popen(
            [sys.executable, "-u", str(worker_py), "unico_f254"],
            cwd=str(cwd), env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL))
        time.sleep(1.5)
    time.sleep(6)
    vivos = sum(1 for p in procs if p.poll() is None)
    for p in procs:
        if p.poll() is None:
            p.kill()
    check("C1. un solo minero activo por directorio", vivos == 1,
          f"procesos vivos={vivos}")
    log = cwd / "minero.log"
    texto = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    check("C2. el segundo proceso lo dice en el registro",
          "un minero" in texto.lower() or "cerrojo" in texto.lower(),
          (texto.strip().splitlines()[-1][:80] if texto.strip() else "sin log"))


def main():
    print(f"worker: {FUENTE}")
    print(f"sha256: {__import__('hashlib').sha256(FUENTE.read_bytes()).hexdigest()}\n")
    prueba_a()
    prueba_b()
    with __import__("tempfile").TemporaryDirectory(prefix="f254_") as tmp:
        d = Path(tmp)
        # Estructura real: <raiz>/worker/kukia_worker.py con <raiz>/ledger y
        # <raiz>/tools al lado. Si el worker vive suelto, no encuentra la
        # billetera y muere al importar (mediria otra cosa).
        (d / "worker").mkdir()
        destino = d / "worker" / "kukia_worker.py"
        destino.write_bytes(FUENTE.read_bytes())
        import shutil
        (d / "ledger").mkdir(exist_ok=True)
        (d / "tools").mkdir(exist_ok=True)
        for f in ("kukia_wallet.py", "sellos.py", "wordlist_es.txt"):
            origen = RAIZ / "ledger" / f
            if origen.exists():
                shutil.copy2(origen, d / "ledger" / f)
        origen = RAIZ / "tools" / "core_destino.py"
        if origen.exists():
            shutil.copy2(origen, d / "tools" / "core_destino.py")
        prueba_c(destino, d / "worker")
    ok = all(r[1] for r in RES)
    print(f"\nRESULTADO: {'PASS' if ok else 'FAIL'} "
          f"({sum(1 for r in RES if r[1])}/{len(RES)})")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        raise SystemExit(2)
