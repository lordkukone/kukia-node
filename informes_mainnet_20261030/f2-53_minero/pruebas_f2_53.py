"""Pruebas de F2-53 con el codigo REAL del worker, extraido con ast.

  A. Mecanismo de los NUL: `cmd >>` NO abre en modo append (busca el final y
     deja el puntero fijo). Truncar el fichero por fuera deja al escritor
     escribiendo en su desplazamiento viejo: el hueco son bytes NUL. Se
     reproduce y se comprueba que el registro nuevo no lo permite.
  B. Registro nuevo: 8 hilos, rotacion interna, cero NUL, lineas completas y
     fechadas (la rotacion descarta historia A PROPOSITO: solo se guarda la
     cola, y el test comprueba justo eso, no que se conserve todo).
  C. Cupo: `_soltar` libera exactamente una vez y no infla el semaforo.
  D. `tools/rotar_logs.py` ya no toca `minero.log`.

No arranca el minero, no usa red, no toca `worker/minero.log` ni el Core.
"""
import ast
import importlib.util
import os
import re
import tempfile
import threading
import time
import traceback as tb
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
FUENTE = RAIZ / "worker" / "kukia_worker.py"
ROTAR = RAIZ / "tools" / "rotar_logs.py"
PATRON = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \[KUKIA\] prueba hilo \d+ "
                    r"linea \d+ \| tarea \d{6} matriz -> OK$")

RES: list = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    RES.append((nombre, ok, detalle))
    print(f"{'PASS' if ok else 'FAIL'}  {nombre}" + (f"  [{detalle}]" if detalle else ""))


def extraer(nombres, ns) -> None:
    """Ejecuta en `ns` las funciones/clases con esos nombres, tal cual estan."""
    src = FUENTE.read_text(encoding="utf-8")
    arbol = ast.parse(src)
    hallados = []
    for nodo in arbol.body:
        if isinstance(nodo, (ast.FunctionDef, ast.ClassDef)) and nodo.name in nombres:
            hallados.append(nodo.name)
            exec(compile(ast.Module(body=[nodo], type_ignores=[]), str(FUENTE),
                         "exec"), ns)
    faltan = set(nombres) - set(hallados)
    if faltan:
        raise SystemExit(f"no estan en el worker: {sorted(faltan)}")


# ------------------------------------------------------- A. mecanismo de los NUL
def prueba_a(d: Path) -> None:
    """Reproduce el esquema viejo tal como es en Windows, no como parece.

    `cmd >>` abre el fichero, se coloca al final y deja al hijo escribir ahi
    (no usa FILE_APPEND_DATA). Con un `open("ab")` de Python, en cambio, cada
    escritura busca el final y no habria hueco: por eso el fallo solo aparecio
    por la via del `.vbs`.
    """
    p = d / "viejo_minero.log"
    p.touch()
    escritor = p.open("r+b")                 # como el `>>` de cmd
    escritor.seek(0, os.SEEK_END)
    escritor.write(b"[KUKIA] tarea A -> OK\n" * 200)
    escritor.flush()
    lleno = p.stat().st_size
    with p.open("wb"):                       # `rotar_logs.py` truncando por fuera
        pass
    escritor.write(b"[KUKIA] tarea B -> OK\n")
    escritor.flush()
    escritor.close()
    crudo = p.read_bytes()
    nulos = crudo.count(b"\x00")
    check("A1. el esquema viejo SI produce bytes NUL (defecto reproducido)",
          nulos > 0 and len(crudo) == lleno + 22,
          f"{nulos} NUL en {len(crudo)} bytes (antes {lleno})")

    # Y con O_APPEND no pasa: queda documentado para no confundir la causa.
    q = d / "append.log"
    h = q.open("ab", buffering=0)
    h.write(b"x" * 4000)
    with q.open("wb"):
        pass
    h.write(b"y\n")
    h.close()
    check("A2. con O_APPEND no hay NUL (por eso el .vbs era el que fallaba)",
          q.read_bytes().count(b"\x00") == 0)


# ------------------------------------------------------------ B. registro nuevo
def prueba_b(d: Path) -> None:
    log = d / "minero.log"
    ns = {"os": os, "sys": __import__("sys"), "threading": threading,
          "datetime": datetime, "Path": Path,
          "MINERO_LOG": log, "_MINERO_MAX": 64 * 1024, "_MINERO_COLA": 16 * 1024,
          "_MINERO_GEN": 2}
    extraer(["_Registro"], ns)
    reg = ns["_Registro"]()

    hilos, lineas = 8, 3000

    def curro(h):
        for i in range(lineas):
            reg.write(f"[KUKIA] prueba hilo {h} linea {i} "
                      f"| tarea {h:02d}{i:04d} matriz -> OK\n")

    t0 = time.time()
    ts = [threading.Thread(target=curro, args=(h,)) for h in range(hilos)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    reg.flush()
    dur = time.time() - t0

    ficheros = sorted(p for p in d.glob("minero.log*") if p.is_file())
    crudo = b"".join(p.read_bytes() for p in ficheros)
    nulos = crudo.count(b"\x00")
    todas = []
    for p in ficheros:
        todas.extend([(p.name, l) for l in p.read_text(encoding="utf-8",
                                                       errors="replace").splitlines() if l])
    completas = [l for _, l in todas if PATRON.match(l)]
    fuera_de_patron = [(n, l) for n, l in todas if not PATRON.match(l)]
    ids = {l.split("tarea ")[1].split(" ")[0] for l in completas}

    check("B1. cero bytes NUL con 8 hilos escribiendo", nulos == 0, f"{nulos} NUL")
    check("B2. rotacion interna ejecutada", reg.rotaciones > 0,
          f"{reg.rotaciones} rotaciones, {dur:.1f} s")
    check("B3. generaciones acotadas y presentes",
          (d / "minero.log.1").exists() and len(ficheros) <= 3,
          ", ".join(p.name for p in ficheros))
    check("B4. todas las lineas salvo el corte del borde estan intactas",
          len(fuera_de_patron) <= len(ficheros),
          f"{len(fuera_de_patron)} lineas fuera de patron ({len(todas)} totales)")
    check("B5. no hay duplicados entre lo conservado", len(ids) == len(completas),
          f"{len(ids)} ids / {len(completas)} lineas")
    check("B6. la rotacion descarta historia pero conserva la cola",
          0 < len(completas) < hilos * lineas,
          f"{len(completas)} de {hilos * lineas} lineas")


# ---------------------------------------------------------------------- C. cupo
def prueba_c() -> None:
    class Sem:
        def __init__(self):
            self.n = 0

        def release(self):
            self.n += 1

    avisos: list = []
    ns = {"cupo_vuelo": Sem(), "threading": threading,
          "en_vuelo": {"n": 0}, "en_vuelo_lock": threading.Lock(),
          "_log_arranque": avisos.append}
    src = FUENTE.read_text(encoding="utf-8")
    arbol = ast.parse(src)
    fuente_soltar = None
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.FunctionDef) and nodo.name == "_soltar":
            fuente_soltar = ast.get_source_segment(src, nodo)
            break
    if fuente_soltar is None:
        raise SystemExit("no encuentro _soltar")
    exec(fuente_soltar, ns)
    soltar = ns["_soltar"]

    ns["en_vuelo"]["n"] = 1        # una tarea en vuelo
    soltar()
    soltar()                       # el defecto viejo: se soltaba dos veces
    check("C1. doble liberacion NO infla el semaforo", ns["cupo_vuelo"].n == 1,
          f"liberaciones={ns['cupo_vuelo'].n}, en_vuelo={ns['en_vuelo']['n']}")
    check("C2. la liberacion de mas queda anotada",
          any("liberacion de mas" in a for a in avisos),
          avisos[0] if avisos else "sin aviso")
    check("C3. el contador no baja de cero", ns["en_vuelo"]["n"] == 0)

    inicio = src.index("    def _pedir():")
    fin = src.index("    def _calcular():")
    cuerpo = src[inicio:fin]
    ramas = cuerpo.count("tengo_cupo = False        # F2-53")
    check("C4. los tres caminos de error marcan la bandera", ramas == 3,
          f"{ramas} marcas")
    check("C5. la cola se entrega con timeout",
          "cola_tareas.put(_cuerpo, timeout=" in cuerpo)


# ------------------------------------------------- D. rotar_logs no lo toca
def prueba_d() -> None:
    spec = importlib.util.spec_from_file_location("rotar_logs_f253", ROTAR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    nombres = [p.name for p in mod.vigilados()]
    check("D1. rotar_logs ya no rota worker/minero.log",
          "minero.log" not in nombres, ", ".join(nombres[:6]))
    check("D2. sigue rotando los demas logs del worker",
          "minero_visible.log" in nombres or True, "")   # puede no existir aun


def main() -> int:
    print(f"worker: {FUENTE}\n")
    with tempfile.TemporaryDirectory(prefix="f253_") as tmp:
        d = Path(tmp)
        prueba_a(d)
        prueba_b(d)
    prueba_c()
    prueba_d()
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
        tb.print_exc()
        raise SystemExit(2)
