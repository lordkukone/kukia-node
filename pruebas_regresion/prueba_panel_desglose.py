"""Regresion: el panel ensena el desglose ok/fallo de las archivadas.

Defecto que vigila: el panel ponia "N archivadas" y nada mas, asi que un total
de tareas archivadas no se podia cuadrar con nada. Desde F2-57 el Core publica
`tareas_ok_archivadas` y `tareas_fallo_archivadas`, y el panel los muestra en
la linea de tareas: "760 en tabla · 500 archivadas (290 ok · 210 fallo)".

Es una comprobacion de extremo a extremo, con las piezas REALES:

  Core aislado  (copia temporal del coordinator/, base sintetica, puerto efimero)
      -> sembrado con 760 'ok' en tabla y 1.050 ok en total (500 archivadas:
         290 ok + 210 fallo)
  Panel real    (dashboard/kukia_dash.py con KUKIA_CORE apuntando a ese Core)

y se comprueba lo que el navegador va a pintar:

  P1  /api/resumen (lo que consume el panel) trae los tres campos
  P2  los valores son los sembrados
  P3  la identidad cuadra: en_tabla + ok_archivadas == tareas_ok
  P4  el HTML servido conserva el hueco #kTasksArch
  P5  y el codigo que lo rellena usa los tres campos y las palabras "ok" y "fallo"
  P6  el panel responde ok (sin errores del Core)

No toca la base real, ni el Core operativo, ni los puertos 8300/8301.

Uso:  python pruebas_regresion/prueba_panel_desglose.py
"""
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
TOKEN = "prueba-panel-desglose"

# Lo sembrado: 760 siguen en la tabla, 290 'ok' y 210 'fallo' ya archivadas.
EN_TABLA = 760
OK_ARCHIVADAS = 290
FALLO_ARCHIVADAS = 210
OK_TOTAL = EN_TABLA + OK_ARCHIVADAS
RES: list[dict] = []


def ok(nombre, cond, detalle=""):
    RES.append({"prueba": nombre, "pasa": bool(cond), "detalle": detalle})
    print(f"  [{'PASS' if cond else 'FAIL'}] {nombre}" + (f"  {detalle}" if detalle else ""))
    return bool(cond)


def _puerto_libre() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def montar_core(destino: Path) -> Path:
    for sub in ("coordinator", "ledger", "tools"):
        (destino / sub).mkdir(parents=True, exist_ok=True)
    for rel in ("coordinator/kukia_core.py", "coordinator/claves.py",
                "ledger/sellos.py", "tools/guardia.py"):
        shutil.copy2(RAIZ / rel, destino / rel)
    (destino / "admin_token.txt").write_text(TOKEN, encoding="utf-8")
    return destino


def arrancar_core(arbol: Path, db: Path, puerto: int):
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


def arrancar_panel(core_url: str, puerto: int):
    e = dict(os.environ, KUKIA_CORE=core_url, PYTHONIOENCODING="utf-8")
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "kukia_dash:app",
                          "--host", "127.0.0.1", "--port", str(puerto)],
                         cwd=str(RAIZ / "dashboard"), env=e,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{puerto}"
    for _ in range(120):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen(base + "/api/resumen", timeout=20) as r:
                if r.status == 200:
                    return p, base
        except Exception:
            if p.poll() is not None:
                raise RuntimeError(f"el panel murio (codigo {p.returncode})")
    p.kill()
    raise RuntimeError("el panel no respondio")


def parar(p):
    try:
        p.kill()
        p.wait(timeout=20)
    except Exception:
        pass
    time.sleep(0.5)


def sembrar(db: Path):
    """760 'ok' en la tabla y los contadores materializados de una base archivada."""
    con = sqlite3.connect(str(db))
    for i in range(EN_TABLA):
        con.execute("INSERT INTO tareas(id,tipo,params,estado,creado,fin)"
                    " VALUES(?,?,?,?,?,?)",
                    (f"en-tabla-{i}", "checksum", "{}", "ok",
                     time.ctime(), time.ctime()))
    filas = [("tareas_ok_total", OK_TOTAL),
             ("tareas_archivadas", OK_ARCHIVADAS + FALLO_ARCHIVADAS),
             ("tareas_ok_archivadas", OK_ARCHIVADAS),
             ("tareas_fallo_archivadas", FALLO_ARCHIVADAS)]
    for clave, valor in filas:
        con.execute("INSERT INTO meta(clave,valor) VALUES(?,?) "
                    "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                    (clave, valor))
    con.commit()
    con.close()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="kukia_panel_"))
    arbol = montar_core(tmp / "arbol")
    db = arbol / "datos.db"
    puerto_core, puerto_panel = _puerto_libre(), _puerto_libre()
    print(f"[panel] copia aislada: {arbol}")

    # El esquema lo crea el propio Core al arrancar; luego se siembra y se
    # vuelve a arrancar.
    p, base = arrancar_core(arbol, db, puerto_core)
    parar(p)
    sembrar(db)

    p, base = arrancar_core(arbol, db, puerto_core)
    panel = None
    try:
        panel, base_panel = arrancar_panel(base, puerto_panel)
        print(f"[panel] panel en {base_panel} -> Core {base}\n")

        with urllib.request.urlopen(base_panel + "/api/resumen?_=1", timeout=60) as r:
            d = json.loads(r.read().decode())
        st = d.get("stats") or {}

        ok("P1 el panel dice que el Core responde",
           d.get("ok") is True and not d.get("errores"), str(d.get("errores"))[:80])
        ok("P2 /api/resumen trae los tres campos del desglose",
           all(k in st for k in ("tareas_ok_archivadas", "tareas_fallo_archivadas",
                                 "tareas_archivadas")),
           f"{sorted(k for k in st if 'archiv' in k)}")
        ok("P3 los valores son los sembrados (290 ok / 210 fallo / 500)",
           (st.get("tareas_ok_archivadas"), st.get("tareas_fallo_archivadas"),
            st.get("tareas_archivadas")) == (OK_ARCHIVADAS, FALLO_ARCHIVADAS,
                                             OK_ARCHIVADAS + FALLO_ARCHIVADAS),
           f"{st.get('tareas_ok_archivadas')} / {st.get('tareas_fallo_archivadas')} "
           f"/ {st.get('tareas_archivadas')}")
        ok("P4 la identidad cuadra en lo que consume el panel",
           st.get("tareas_ok_en_tabla", 0) + st.get("tareas_ok_archivadas", 0)
           == st.get("tareas_ok"),
           f"{st.get('tareas_ok_en_tabla')} + {st.get('tareas_ok_archivadas')} "
           f"vs {st.get('tareas_ok')}")

        with urllib.request.urlopen(base_panel + "/", timeout=30) as r:
            html = r.read().decode()
        ok("P5 el HTML conserva el hueco #kTasksArch",
           'id="kTasksArch"' in html, f"{len(html)} bytes")
        trozo = html.split('id="kTasksArch"', 1)[-1].split(";", 1)[0]
        # El codigo que lo rellena esta mas abajo; se busca la asignacion.
        asignacion = html.split("getElementById('kTasksArch').textContent", 1)
        usa = len(asignacion) == 2 and all(
            campo in asignacion[1][:600] for campo in
            ("tareas_ok_en_tabla", "tareas_archivadas", "tareas_ok_archivadas",
             "tareas_fallo_archivadas", "' ok · '", "' fallo)'"))
        ok("P6 el codigo compone 'en tabla / N archivadas (X ok / Y fallo)'",
           usa, "asignacion de #kTasksArch" if usa else "no encontrada o incompleta")
    finally:
        if panel:
            parar(panel)
        parar(p)
        shutil.rmtree(tmp, ignore_errors=True)

    fallos = [r for r in RES if not r["pasa"]]
    print(f"\n[panel desglose] {len(RES) - len(fallos)}/{len(RES)} PASS")

    salidas = RAIZ / "pruebas_regresion" / "salidas"
    salidas.mkdir(parents=True, exist_ok=True)
    destino = salidas / f"resultado_panel_desglose_{time.strftime('%Y%m%d_%H%M%S')}.json"
    destino.write_text(json.dumps(
        {"cuando": time.strftime("%Y-%m-%d %H:%M:%S"),
         "prueba": "desglose ok/fallo en el panel kukia_dash",
         "sembrado": {"en_tabla": EN_TABLA, "ok_archivadas": OK_ARCHIVADAS,
                      "fallo_archivadas": FALLO_ARCHIVADAS, "ok_total": OK_TOTAL},
         "pruebas": RES, "pass": len(RES) - len(fallos), "total": len(RES)},
        indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[panel desglose] artefacto: {destino}")
    print(json.dumps({"total": len(RES), "pass": len(RES) - len(fallos),
                      "fail": len(fallos)}, ensure_ascii=False))
    return 0 if not fallos else 1


if __name__ == "__main__":
    raise SystemExit(main())
