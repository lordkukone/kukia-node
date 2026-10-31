"""A/B de /stats: la foto mezclaba dos instantes, y ahora no.

Origen: F2-59 (informes_mainnet_20261030/f2-59_stats_coherente/prueba_f2_59.py).

Que se mide: `/stats` leia cada contador con su propia consulta, asi que un
commit podia caer ENTRE dos lecturas y la foto mezclaba dos instantes: la
identidad de F2-57 (`tareas_ok_en_tabla + tareas_ok_archivadas == tareas_ok`)
parecia rota por 1 o 2 en muestras sueltas. Ahora todos los contadores salen de
una sola transaccion de lectura.

Es una prueba A/B de verdad: las dos variantes se miden igual, con la misma
carga y el mismo esquema, y solo cambia el fichero del Core:

  ANTES   respaldo/core_pre_f259.py         (F2-58: cada contador en su consulta)
  DESPUES coordinator/kukia_core.py         (F2-59: una sola foto)

Metodo:
  - copia aislada en un directorio temporal, base sintetica, puerto efimero;
  - 3 hilos escribiendo tareas 'ok' en bucle (BEGIN IMMEDIATE + INSERT + UPDATE
    meta, igual que el camino real) durante SEGUNDOS por variante;
  - un hilo pidiendo /stats sin parar y comprobando en cada muestra:
        tareas_ok_en_tabla + tareas_ok_archivadas == tareas_ok

PASS si DESPUES no tiene ni un descuadre y ANTES si tiene alguno: sin el
descuadre del antes, la prueba no demuestra nada y lo dice.

No toca la base real, ni el Core operativo, ni los puertos 8300/8301. El
resultado se escribe en pruebas_regresion/salidas/ con marca de tiempo; el
resultado_referencia_20261030.json de esta carpeta es la medicion que valido
F2-59 y no se reescribe.

Uso:  python pruebas_regresion/ab_stats/prueba_ab_stats.py
"""
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parents[1]                 # ...\KUKIA
CORE_AHORA = RAIZ / "coordinator" / "kukia_core.py"
# El fixture NO se llama kukia_core_* a proposito: .gitignore ignora KUKIA_*
# (mayusculas o no, en Windows) y se quedaba fuera del repositorio en silencio.
CORE_ANTES = AQUI / "respaldo" / "core_pre_f259.py"
TOKEN = "prueba-ab-stats"
SEGUNDOS = 12
ESCRITORES = 3
RES: list = []


def _puerto_libre() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def ok(nombre, cond, detalle=""):
    RES.append({"prueba": nombre, "pasa": bool(cond), "detalle": detalle})
    print(f"  [{'PASS' if cond else 'FAIL'}] {nombre}" + (f"  {detalle}" if detalle else ""))
    return bool(cond)


def montar(destino: Path, core: Path) -> Path:
    """Copia solo lo que el Core importa, con el fichero del Core que toque."""
    for sub in ("coordinator", "ledger", "tools"):
        (destino / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy2(core, destino / "coordinator" / "kukia_core.py")
    for rel in ("coordinator/claves.py", "ledger/sellos.py", "tools/guardia.py"):
        shutil.copy2(RAIZ / rel, destino / rel)
    (destino / "admin_token.txt").write_text(TOKEN, encoding="utf-8")
    return destino


def arrancar(arbol: Path, db: Path, puerto: int):
    e = dict(os.environ)
    e.update({"KUKIA_DB": str(db),
              "KUKIA_ADMIN_TOKEN": str(arbol / "admin_token.txt"),
              "KUKIA_SELLO_KEY": str(arbol / "sello_key.txt"),
              "KUKIA_SELLOS_FILE": str(arbol / "sellos.jsonl"),
              "KUKIA_ARCHIVAR": "0", "KUKIA_AUTO_TAREAS": "0",
              "PYTHONIOENCODING": "utf-8"})
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "kukia_core:app",
                          "--host", "127.0.0.1", "--port", str(puerto),
                          "--no-proxy-headers"],
                         cwd=str(arbol / "coordinator"), env=e,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{puerto}"
    for _ in range(160):
        time.sleep(0.5)
        try:
            urllib.request.urlopen(base + "/version", timeout=5).read()
            return p, base
        except Exception:
            if p.poll() is not None:
                raise RuntimeError(f"el Core de prueba murio (codigo {p.returncode})")
    p.kill()
    raise RuntimeError("el Core de prueba no respondio")


def parar(p):
    try:
        p.kill()
        p.wait(timeout=20)
    except Exception:
        pass
    time.sleep(1.0)


def escritor(db: Path, parar_flag: threading.Event, cuenta: list):
    """Escribe como el camino real: meta + fila 'ok' en la MISMA transaccion."""
    c = sqlite3.connect(str(db), timeout=10)
    c.execute("PRAGMA busy_timeout=10000")
    n = 0
    while not parar_flag.is_set():
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT INTO tareas(id,tipo,params,estado,creado,fin)"
                      " VALUES(?,?,?,?,?,?)",
                      (f"t-{uuid.uuid4().hex[:12]}", "checksum", "{}", "ok",
                       time.ctime(), time.ctime()))
            c.execute("INSERT INTO meta(clave,valor) VALUES('tareas_ok_total',1) "
                      "ON CONFLICT(clave) DO UPDATE SET valor=valor+1")
            c.commit()
            n += 1
            cuenta[0] += 1
        except Exception:
            try:
                c.rollback()
            except Exception:
                pass
            time.sleep(0.001)
    c.close()


def medir(arbol: Path, core: Path, etiqueta: str) -> dict:
    """Arranca un Core con ese fichero y mide descuadres bajo carga."""
    sub = montar(arbol / etiqueta, core)
    db = sub / f"datos_{etiqueta}.db"
    puerto = _puerto_libre()
    p, base = arrancar(sub, db, puerto)
    stop = threading.Event()
    cuenta = [0]
    hilos = [threading.Thread(target=escritor, args=(db, stop, cuenta), daemon=True)
             for _ in range(ESCRITORES)]
    muestras = descuadres = errores = 0
    ejemplos = []
    try:
        for h in hilos:
            h.start()
        t0 = time.time()
        while time.time() - t0 < SEGUNDOS:
            try:
                d = json.loads(urllib.request.urlopen(base + "/stats", timeout=10).read())
                muestras += 1
                desvio = d["tareas_ok_en_tabla"] + d["tareas_ok_archivadas"] - d["tareas_ok"]
                if desvio != 0:
                    descuadres += 1
                    if len(ejemplos) < 5:
                        ejemplos.append({"ok": d["tareas_ok"],
                                         "en_tabla": d["tareas_ok_en_tabla"],
                                         "desvio": desvio})
            except Exception as e:
                errores += 1
                if len(ejemplos) < 5:
                    ejemplos.append({"error": str(e)[:80]})
    finally:
        stop.set()
        for h in hilos:
            h.join(timeout=5)
        parar(p)
    return {"etiqueta": etiqueta, "muestras": muestras, "escrituras": cuenta[0],
            "descuadres": descuadres, "errores": errores, "ejemplos": ejemplos}


def main() -> int:
    if not CORE_ANTES.exists():
        print(f"falta el respaldo {CORE_ANTES}")
        return 1

    arbol = Path(tempfile.mkdtemp(prefix="kukia_abstats_"))
    print(f"[A/B stats] copia aislada: {arbol}")
    print(f"[A/B stats] carga: {ESCRITORES} escritores, {SEGUNDOS} s por variante\n")
    try:
        print("ANTES (respaldo F2-58: cada contador en su consulta)")
        antes = medir(arbol, CORE_ANTES, "antes")
        print(f"  muestras {antes['muestras']} | escrituras {antes['escrituras']} | "
              f"descuadres {antes['descuadres']} | errores {antes['errores']}")
        for e in antes["ejemplos"]:
            print("   ", e)

        print("\nDESPUES (F2-59: una sola transaccion de lectura)")
        despues = medir(arbol, CORE_AHORA, "despues")
        print(f"  muestras {despues['muestras']} | escrituras {despues['escrituras']} | "
              f"descuadres {despues['descuadres']} | errores {despues['errores']}")
        for e in despues["ejemplos"]:
            print("   ", e)
    finally:
        shutil.rmtree(arbol, ignore_errors=True)

    print()
    ok("la carga genero escrituras de verdad",
       antes["escrituras"] > 100 and despues["escrituras"] > 100,
       f"{antes['escrituras']} / {despues['escrituras']}")
    ok("DESPUES: cero descuadres en la identidad",
       despues["descuadres"] == 0, f"{despues['descuadres']} de {despues['muestras']} muestras")
    ok("DESPUES: ninguna peticion fallo", despues["errores"] == 0, f"{despues['errores']} errores")
    ok("DESPUES: la foto sigue cuadrando al final",
       despues["muestras"] > 20, f"{despues['muestras']} muestras")
    ok("ANTES: el descuadre existia (medido, no supuesto)",
       antes["descuadres"] > 0, f"{antes['descuadres']} de {antes['muestras']} muestras")

    fallos = [r for r in RES if not r["pasa"]]
    print(f"\n[A/B stats] {len(RES) - len(fallos)}/{len(RES)} PASS")

    salidas = RAIZ / "pruebas_regresion" / "salidas"
    salidas.mkdir(parents=True, exist_ok=True)
    ahora = time.strftime("%Y%m%d_%H%M%S")
    destino = salidas / f"resultado_ab_stats_{ahora}.json"
    destino.write_text(json.dumps(
        {"cuando": time.strftime("%Y-%m-%d %H:%M:%S"),
         "prueba": "A/B de coherencia de /stats (F2-59)",
         "antes": antes, "despues": despues, "pruebas": RES,
         "pass": len(RES) - len(fallos), "total": len(RES)},
        indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[A/B stats] artefacto: {destino}")
    # Resumen de una linea al FINAL: es lo que lee el lanzador.
    print(json.dumps({"total": len(RES), "pass": len(RES) - len(fallos),
                      "fail": len(fallos),
                      "antes_descuadres": antes["descuadres"],
                      "despues_descuadres": despues["descuadres"]},
                     ensure_ascii=False))
    return 0 if not fallos else 1


if __name__ == "__main__":
    raise SystemExit(main())
