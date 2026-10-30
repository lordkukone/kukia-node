"""F2-57 - Prueba de regresion del desglose de tareas archivadas.

Que se prueba: el archivado borra 'ok' Y 'fallo'. Antes habia un solo
contador, asi que tareas_ok_en_tabla + tareas_archivadas no daba tareas_ok
(desfase constante de 7 en la base real). Ahora hay dos contadores por estado.

Esta prueba monta una copia aislada del Core en un directorio temporal, con
base sintetica y puerto efimero (nunca 8300 ni 8301), y verifica:

  A. Un lote mixto (7 'ok' + 3 'fallo'): cada contador recibe lo suyo y la
     identidad cuadra. Con la cuenta vieja habria descuadre.
  B. VARIAS RONDAS seguidas de archivado, cada una con otra mezcla, incluida
     una ronda solo de 'fallo' y otra solo de 'ok': tras CADA ronda se
     comprueban los deltas y la identidad, que las tareas vivas ('queued',
     'ejecutando') no se borran nunca, y que las 'ok' borradas son
     exactamente las de esa ronda.
  C. Resiembra: si se pierden las claves nuevas, el arranque las vuelve a
     sembrar con el reparto correcto (es lo que hara con la base real de
     1,1 M archivadas).

No toca la base real, ni el Core operativo, ni los puertos 8300/8301.
"""
import atexit
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
RAIZ = AQUI.parents[1]          # ...\KUKIA
TOKEN = "prueba-f2-57"

# (nombre, ok borradas, fallo borradas) por ronda; el relleno va al final.
# archivar_tareas impone lote >= 100 y retencion >= 1000, asi que cada ronda
# borra 100 y se siembran 500 por encima de la retencion.
RONDAS = [("r1", 80, 20), ("r2", 50, 50), ("r3", 0, 100), ("r4", 100, 0), ("r5", 60, 40)]
LOTE = 100
RELLENO_OK = 1000

RES: list = []


def _puerto_libre() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PUERTO = _puerto_libre()
BASE = f"http://127.0.0.1:{PUERTO}"


def ok(nombre, cond, detalle=""):
    RES.append({"prueba": nombre, "pasa": bool(cond), "detalle": detalle})
    print(f"  [{'PASS' if cond else 'FAIL'}] {nombre}" + (f"  {detalle}" if detalle else ""))
    return bool(cond)


def http(ruta, datos=None, metodo=None):
    req = urllib.request.Request(BASE + ruta, method=metodo or ("GET" if datos is None else "POST"))
    req.add_header("X-Admin-Token", TOKEN)
    if datos is not None:
        req.data = json.dumps(datos).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def montar(destino: Path) -> Path:
    """Copia solo lo que el Core importa, a un arbol temporal."""
    (destino / "coordinator").mkdir(parents=True, exist_ok=True)
    (destino / "ledger").mkdir(parents=True, exist_ok=True)
    (destino / "tools").mkdir(parents=True, exist_ok=True)
    for rel in ("coordinator/kukia_core.py", "coordinator/claves.py",
                "ledger/sellos.py", "tools/guardia.py"):
        shutil.copy2(RAIZ / rel, destino / rel)
    (destino / "admin_token.txt").write_text(TOKEN, encoding="utf-8")
    return destino


def entorno(arbol: Path, db: Path) -> dict:
    e = dict(os.environ)
    e.update({"KUKIA_DB": str(db),
              "KUKIA_ADMIN_TOKEN": str(arbol / "admin_token.txt"),
              "KUKIA_SELLO_KEY": str(arbol / "sello_key.txt"),
              "KUKIA_SELLOS_FILE": str(arbol / "sellos.jsonl"),
              "KUKIA_ARCHIVAR": "0",        # el bucle no archiva: lo pide la prueba
              "KUKIA_AUTO_TAREAS": "0",     # sin generador de trabajo
              "PYTHONIOENCODING": "utf-8"})
    return e


def arrancar(arbol: Path, db: Path):
    p = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "kukia_core:app",
         "--host", "127.0.0.1", "--port", str(PUERTO), "--no-proxy-headers"],
        cwd=str(arbol / "coordinator"), env=entorno(arbol, db),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        time.sleep(0.5)
        try:
            http("/version")
            return p
        except Exception:
            if p.poll() is not None:
                raise RuntimeError(f"el Core de prueba murio (codigo {p.returncode})")
    p.kill()
    raise RuntimeError("el Core de prueba no respondio en 60 s")


def parar(p):
    try:
        p.kill()
        p.wait(timeout=20)
    except Exception:
        pass
    time.sleep(1.0)


def _insertar(con, tid, estado, asignado=None):
    con.execute("INSERT INTO tareas(id,tipo,params,estado,creado,fin,asignado)"
                " VALUES(?,?,?,?,?,?,?)",
                (tid, "checksum", "{}", estado, time.ctime(),
                 time.ctime() if estado in ("ok", "fallo") else None,
                 time.time() if asignado is None and estado == "ejecutando" else asignado))


def sembrar_lote_mixto(db: Path):
    """7 'ok' + 3 'fallo' (los mas antiguos) y 1.000 'ok' detras."""
    con = sqlite3.connect(db)
    for i in range(7):
        _insertar(con, f"ok-vieja-{i}", "ok")
    for i in range(3):
        _insertar(con, f"fallo-vieja-{i}", "fallo")
    for i in range(1000):
        _insertar(con, f"ok-{i}", "ok")
    con.execute("UPDATE meta SET valor=1007 WHERE clave='tareas_ok_total'")
    con.commit()
    con.close()


def sembrar_rondas(db: Path):
    """5 rondas de 10 terminadas con mezclas distintas, tareas vivas y relleno.

    Devuelve (ids_por_ronda, vivas, ok_total, fallo_total).
    """
    con = sqlite3.connect(db)
    por_ronda, vivas = {}, []
    total_ok = total_fallo = 0
    for nombre, n_ok, n_fallo in RONDAS:
        ids = []
        for i in range(n_ok):
            tid = f"{nombre}-ok-{i}"
            _insertar(con, tid, "ok")
            ids.append(tid)
        for i in range(n_fallo):
            tid = f"{nombre}-fallo-{i}"
            _insertar(con, tid, "fallo")
            ids.append(tid)
        # Las tareas vivas se intercalan entre las terminadas: si el archivado
        # se llevara por delante un 'queued' o un 'ejecutando', se notaria.
        tid = f"{nombre}-cola"
        _insertar(con, tid, "queued")
        vivas.append(tid)
        ids.append(tid)
        por_ronda[nombre] = ids
        total_ok += n_ok
        total_fallo += n_fallo
    # Una 'ejecutando' recien asignada (no la recoge el recuperador) y otra
    # 'queued' suelta: ninguna debe borrarse ni contarse.
    _insertar(con, "ejecutando-fresca", "ejecutando", asignado=time.time())
    _insertar(con, "cola-suelta", "queued")
    vivas += ["ejecutando-fresca", "cola-suelta"]
    for i in range(RELLENO_OK):
        _insertar(con, f"relleno-{i}", "ok")
    con.execute("UPDATE meta SET valor=? WHERE clave='tareas_ok_total'",
                (total_ok + RELLENO_OK,))
    con.commit()
    con.close()
    return por_ronda, vivas, total_ok + RELLENO_OK, total_fallo


def existentes(db: Path) -> set:
    con = sqlite3.connect(db)
    ids = {r[0] for r in con.execute("SELECT id FROM tareas")}
    con.close()
    return ids


def contadores(db: Path) -> dict:
    con = sqlite3.connect(db)
    d = {r[0]: r[1] for r in con.execute(
        "SELECT clave, valor FROM meta WHERE clave IN "
        "('tareas_ok_total','tareas_archivadas','tareas_ok_archivadas','tareas_fallo_archivadas')")}
    con.close()
    return d


def main() -> int:
    arbol = montar(Path(tempfile.mkdtemp(prefix="kukia_f257_")))
    # Si la prueba revienta a mitad, el arbol temporal se borra igual.
    atexit.register(shutil.rmtree, arbol, True)
    print(f"[F2-57] copia aislada: {arbol}")
    print(f"[F2-57] puerto efimero: {PUERTO}\n")

    # ---------------- A. un lote mixto ----------------
    print("A. Lote mixto (7 ok + 3 fallo de 1.010 terminadas)")
    db = arbol / "datos.db"
    parar(arrancar(arbol, db))          # crea el esquema
    sembrar_lote_mixto(db)
    p = arrancar(arbol, db)
    try:
        antes = http("/stats")
        r = http("/admin/archivar?retencion=1000&lote=20000", {}, "POST")
        ok("borradas = 10", r.get("borradas") == 10, f"borradas={r.get('borradas')}")
        ok("borradas_ok = 7", r.get("borradas_ok") == 7, f"{r.get('borradas_ok')}")
        ok("borradas_fallo = 3", r.get("borradas_fallo") == 3, f"{r.get('borradas_fallo')}")
        ok("archivadas_total = 10", r.get("archivadas_total") == 10, f"{r.get('archivadas_total')}")
        ok("archivadas_ok_total = 7", r.get("archivadas_ok_total") == 7, f"{r.get('archivadas_ok_total')}")
        ok("archivadas_fallo_total = 3", r.get("archivadas_fallo_total") == 3, f"{r.get('archivadas_fallo_total')}")

        d = http("/stats")
        ok("identidad: en_tabla + ok_archivadas == tareas_ok",
           d["tareas_ok_en_tabla"] + d["tareas_ok_archivadas"] == d["tareas_ok"],
           f"{d['tareas_ok_en_tabla']} + {d['tareas_ok_archivadas']} vs {d['tareas_ok']}")
        ok("total: ok_archivadas + fallo_archivadas == archivadas",
           d["tareas_ok_archivadas"] + d["tareas_fallo_archivadas"] == d["tareas_archivadas"],
           f"{d['tareas_ok_archivadas']} + {d['tareas_fallo_archivadas']} vs {d['tareas_archivadas']}")
        ok("con la cuenta vieja habria descuadre de 3",
           d["tareas_ok_en_tabla"] + d["tareas_archivadas"] != d["tareas_ok"],
           f"{d['tareas_ok_en_tabla']} + {d['tareas_archivadas']} vs {d['tareas_ok']}")
        r2 = http("/admin/archivar?retencion=1000&lote=20000", {}, "POST")
        ok("pasada sin nada que borrar: 0 y sin error",
           r2.get("borradas") == 0 and "error" not in r2, str(r2))
        ok("antes del archivado ya cuadraba con 0 archivadas",
           antes["tareas_ok_archivadas"] == 0 and antes["tareas_fallo_archivadas"] == 0,
           f"{antes['tareas_ok_archivadas']}/{antes['tareas_fallo_archivadas']}")
    finally:
        parar(p)

    # ---------------- B. varias rondas ----------------
    print("\nB. Cinco rondas consecutivas con mezclas distintas")
    db2 = arbol / "datos_rondas.db"
    parar(arrancar(arbol, db2))
    por_ronda, vivas, ok_total, fallo_total = sembrar_rondas(db2)
    p = arrancar(arbol, db2)
    try:
        base = http("/stats")
        ok("siembra: 1.050 terminadas y ok_total correcto",
           base["tareas_ok"] == ok_total, f"ok_total={base['tareas_ok']} esperado={ok_total}")
        print(f"     rondas: {[(n, a, b) for n, a, b in RONDAS]}  relleno ok: {RELLENO_OK}")

        for idx, (nombre, n_ok, n_fallo) in enumerate(RONDAS, 1):
            antes = http("/stats")
            r = http(f"/admin/archivar?retencion=1000&lote={LOTE}", {}, "POST")
            desp = http("/stats")
            c_desp = contadores(db2)

            ok(f"r{idx} [{nombre}] borradas {LOTE}",
               r.get("borradas") == LOTE, f"borradas={r.get('borradas')}")
            ok(f"r{idx} [{nombre}] mezcla {n_ok} ok / {n_fallo} fallo",
               (r.get("borradas_ok"), r.get("borradas_fallo")) == (n_ok, n_fallo),
               f"{r.get('borradas_ok')}/{r.get('borradas_fallo')}")
            ok(f"r{idx} ok_archivadas +{n_ok}",
               desp["tareas_ok_archivadas"] - antes["tareas_ok_archivadas"] == n_ok,
               f"{antes['tareas_ok_archivadas']} -> {desp['tareas_ok_archivadas']}")
            ok(f"r{idx} fallo_archivadas +{n_fallo}",
               desp["tareas_fallo_archivadas"] - antes["tareas_fallo_archivadas"] == n_fallo,
               f"{antes['tareas_fallo_archivadas']} -> {desp['tareas_fallo_archivadas']}")
            ok(f"r{idx} identidad cuadra",
               desp["tareas_ok_en_tabla"] + desp["tareas_ok_archivadas"] == desp["tareas_ok"]
               and desp["tareas_ok_archivadas"] + desp["tareas_fallo_archivadas"] == desp["tareas_archivadas"],
               f"{desp['tareas_ok_en_tabla']}+{desp['tareas_ok_archivadas']} vs {desp['tareas_ok']}")
            ok(f"r{idx} tareas_ok no la toca el archivado",
               desp["tareas_ok"] == antes["tareas_ok"] == ok_total,
               f"{antes['tareas_ok']} -> {desp['tareas_ok']}")

            # Los ids borrados son exactamente los de esa ronda, y las vivas siguen.
            quedan = existentes(db2)
            borradas_ronda = [t for t in por_ronda[nombre] if not t.endswith("-cola")]
            colas_ronda = [t for t in por_ronda[nombre] if t.endswith("-cola")]
            ok(f"r{idx} desaparecen exactamente las suyas",
               not (set(borradas_ronda) & quedan) and all(t in quedan for t in colas_ronda),
               f"esperadas {len(borradas_ronda)} fuera, siguen {len(set(borradas_ronda) & quedan)}")
            ok(f"r{idx} las tareas vivas siguen ahi",
               all(t in quedan for t in vivas), f"{len(vivas)} vivas")
            ok(f"r{idx} contadores de meta coherentes con /stats",
               c_desp["tareas_ok_archivadas"] == desp["tareas_ok_archivadas"]
               and c_desp["tareas_fallo_archivadas"] == desp["tareas_fallo_archivadas"]
               and c_desp["tareas_archivadas"] == desp["tareas_archivadas"],
               "meta == /stats")

        # Sexta pasada: ya no queda nada que borrar.
        r6 = http(f"/admin/archivar?retencion=1000&lote={LOTE}", {}, "POST")
        d6 = http("/stats")
        ok("sexta ronda: 0 borradas", r6.get("borradas") == 0, str(r6))
        ok("final: ok_archivadas = 290 y fallo_archivadas = 210",
           (d6["tareas_ok_archivadas"], d6["tareas_fallo_archivadas"]) == (290, 210),
           f"{d6['tareas_ok_archivadas']}/{d6['tareas_fallo_archivadas']}")
        ok("final: archivadas = 500 = 290 + 210",
           d6["tareas_archivadas"] == 500, f"{d6['tareas_archivadas']}")
        ok("final: identidad tras 5 rondas",
           d6["tareas_ok_en_tabla"] + d6["tareas_ok_archivadas"] == d6["tareas_ok"],
           f"{d6['tareas_ok_en_tabla']} + {d6['tareas_ok_archivadas']} vs {d6['tareas_ok']}")
        ok("final: ninguna viva borrada en todo el recorrido",
           all(t in existentes(db2) for t in vivas), f"{len(vivas)} vivas")
    finally:
        parar(p)

    # ---------------- C. resiembra ----------------
    print("\nC. Resiembra al perder las claves nuevas (caso de la base real)")
    con = sqlite3.connect(db)
    con.execute("DELETE FROM meta WHERE clave IN ('tareas_ok_archivadas','tareas_fallo_archivadas')")
    con.commit()
    con.close()
    p = arrancar(arbol, db)
    try:
        d3 = http("/stats")
        ok("resiembra: ok_archivadas = 7", d3["tareas_ok_archivadas"] == 7, f"{d3['tareas_ok_archivadas']}")
        ok("resiembra: fallo_archivadas = 3", d3["tareas_fallo_archivadas"] == 3, f"{d3['tareas_fallo_archivadas']}")
        ok("resiembra: identidad sigue cuadrando",
           d3["tareas_ok_en_tabla"] + d3["tareas_ok_archivadas"] == d3["tareas_ok"],
           f"{d3['tareas_ok_en_tabla']} + {d3['tareas_ok_archivadas']} vs {d3['tareas_ok']}")
    finally:
        parar(p)

    fallos = [r for r in RES if not r["pasa"]]
    print(f"\n[F2-57] {len(RES) - len(fallos)}/{len(RES)} PASS")
    (AQUI / "resultado_f2_57.json").write_text(
        json.dumps({"pruebas": RES, "pass": len(RES) - len(fallos), "total": len(RES)}, indent=2),
        encoding="utf-8")
    print(f"[F2-57] artefacto: {AQUI / 'resultado_f2_57.json'}")
    return 0 if not fallos else 1


if __name__ == "__main__":
    raise SystemExit(main())
