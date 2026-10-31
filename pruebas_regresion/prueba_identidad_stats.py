"""Regresion: /stats cuadra, y su foto es de un solo instante.

Cubre dos defectos que ya se corrigieron y que volverian a colarse sin avisar:

  F2-57  el archivado borra 'ok' Y 'fallo'. Con un solo contador,
         `tareas_ok_en_tabla + tareas_archivadas != tareas_ok` (en la base real
         eran 7 de desfase constante). Ahora hay un contador por estado.
  F2-58/59  cada contador se leia con su propia consulta, asi que un commit
         podia caer ENTRE dos lecturas y la foto mezclaba dos instantes. Ahora
         todos los contadores salen de una transaccion de lectura.

Y de paso comprueba la huella de /version (F2-48): que `claves.py` este en
FICHEROS_HUELLA y que las dos formas del hash coincidan con el fichero del
repositorio.

Metodo (sin tocar nada de produccion):
  - copia aislada del Core (solo lo que importa) en un directorio temporal;
  - base sintetica y puerto efimero (nunca 8300 ni 8301);
  - A. lote mixto + archivado: cada contador recibe lo suyo y la identidad cuadra;
  - B. 3 hilos escribiendo en bucle (INSERT + meta en la MISMA transaccion, como
       el camino real) mientras se piden /stats: cero descuadres, cero errores;
  - C. huella de /version contra los ficheros del repositorio.

Uso:  python pruebas_regresion/prueba_identidad_stats.py
"""
import hashlib
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
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
TOKEN = "prueba-regresion-stats"
SEGUNDOS_CARGA = 8
ESCRITORES = 3
# La carga se mantiene hasta juntar MIN_ESCRITURAS o agotar MAX_CARGA: en un PC
# ocupado el escritor tarda en conseguir el cerrojo y un plazo fijo medía 15
# escrituras un dia y 577 otro. Asi la prueba mide concurrencia de verdad sin
# depender de la carga ajena.
MIN_ESCRITURAS = 100
MAX_CARGA = 60
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


class Core:
    """Core aislado en un arbol temporal, con su propia base y su puerto."""

    def __init__(self, arbol: Path, db: Path):
        self.arbol, self.db = arbol, db
        self.puerto = _puerto_libre()
        self.base = f"http://127.0.0.1:{self.puerto}"
        self.proc = None

    def arrancar(self):
        e = dict(os.environ)
        e.update({"KUKIA_DB": str(self.db),
                  "KUKIA_ADMIN_TOKEN": str(self.arbol / "admin_token.txt"),
                  "KUKIA_SELLO_KEY": str(self.arbol / "sello_key.txt"),
                  "KUKIA_SELLOS_FILE": str(self.arbol / "sellos.jsonl"),
                  "KUKIA_ARCHIVAR": "0",        # el bucle no archiva: lo pide la prueba
                  "KUKIA_AUTO_TAREAS": "0",     # sin generador de trabajo
                  "PYTHONIOENCODING": "utf-8"})
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "kukia_core:app",
             "--host", "127.0.0.1", "--port", str(self.puerto), "--no-proxy-headers"],
            cwd=str(self.arbol / "coordinator"), env=e,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(160):
            time.sleep(0.5)
            try:
                self.http("/version")
                return self
            except Exception:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"el Core de prueba murio (codigo {self.proc.returncode})")
        self.parar()
        raise RuntimeError("el Core de prueba no respondio en 80 s")

    def http(self, ruta, datos=None, metodo=None):
        req = urllib.request.Request(
            self.base + ruta, method=metodo or ("GET" if datos is None else "POST"))
        req.add_header("X-Admin-Token", TOKEN)
        if datos is not None:
            req.data = json.dumps(datos).encode()
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())

    def parar(self):
        if not self.proc:
            return
        try:
            self.proc.kill()
            self.proc.wait(timeout=20)
        except Exception:
            pass
        self.proc = None
        time.sleep(1.0)


def montar(destino: Path) -> Path:
    """Copia solo lo que el Core importa, a un arbol temporal."""
    for sub in ("coordinator", "ledger", "tools"):
        (destino / sub).mkdir(parents=True, exist_ok=True)
    for rel in ("coordinator/kukia_core.py", "coordinator/claves.py",
                "ledger/sellos.py", "tools/guardia.py"):
        shutil.copy2(RAIZ / rel, destino / rel)
    (destino / "admin_token.txt").write_text(TOKEN, encoding="utf-8")
    return destino


def _insertar(con, tid, estado):
    con.execute("INSERT INTO tareas(id,tipo,params,estado,creado,fin)"
                " VALUES(?,?,?,?,?,?)",
                (tid, "checksum", "{}", estado, time.ctime(), time.ctime()))


def sembrar_mixto(db: Path):
    """7 'ok' + 3 'fallo' (las mas antiguas) y 1.000 'ok' detras."""
    con = sqlite3.connect(str(db))
    for i in range(7):
        _insertar(con, f"ok-vieja-{i}", "ok")
    for i in range(3):
        _insertar(con, f"fallo-vieja-{i}", "fallo")
    for i in range(1000):
        _insertar(con, f"ok-{i}", "ok")
    con.execute("UPDATE meta SET valor=1007 WHERE clave='tareas_ok_total'")
    con.commit()
    con.close()


def escritor(db: Path, parar_flag: threading.Event, cuenta: list):
    """Escribe como el camino real: meta + fila 'ok' en la MISMA transaccion.

    `cuenta[0]` se actualiza en cada escritura (no al salir): el bucle de carga
    lo consulta para saber si ya ha habido concurrencia de verdad.
    """
    c = sqlite3.connect(str(db), timeout=10)
    c.execute("PRAGMA busy_timeout=10000")
    n = 0
    while not parar_flag.is_set():
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT INTO tareas(id,tipo,params,estado,creado,fin)"
                      " VALUES(?,?,?,?,?,?)",
                      (f"t-{time.time_ns()}-{n}", "checksum", "{}", "ok",
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


def identidad(d: dict):
    """Las dos identidades contables que /stats debe cumplir siempre."""
    a = d["tareas_ok_en_tabla"] + d["tareas_ok_archivadas"] - d["tareas_ok"]
    b = (d["tareas_ok_archivadas"] + d["tareas_fallo_archivadas"]
         - d["tareas_archivadas"])
    return a, b


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="kukia_regstats_"))
    try:
        arbol = montar(tmp / "arbol")
        print(f"[stats] copia aislada: {arbol}")

        # --- esquema: se crea arrancando una vez, y luego se siembra ---
        db = arbol / "datos.db"
        Core(arbol, db).arrancar().parar()
        sembrar_mixto(db)

        core = Core(arbol, db).arrancar()
        try:
            # ---------------- A. lote mixto + archivado ----------------
            print("\nA. Lote mixto (7 ok + 3 fallo) y archivado")
            antes = core.http("/stats")
            r = core.http("/admin/archivar?retencion=1000&lote=20000", {}, "POST")
            ok("borradas = 10", r.get("borradas") == 10, f"{r.get('borradas')}")
            ok("borradas_ok = 7", r.get("borradas_ok") == 7, f"{r.get('borradas_ok')}")
            ok("borradas_fallo = 3", r.get("borradas_fallo") == 3, f"{r.get('borradas_fallo')}")
            ok("antes del archivado no habia nada archivado",
               antes["tareas_ok_archivadas"] == 0 and antes["tareas_fallo_archivadas"] == 0,
               f"{antes['tareas_ok_archivadas']}/{antes['tareas_fallo_archivadas']}")

            d = core.http("/stats")
            a, b = identidad(d)
            ok("identidad: en_tabla + ok_archivadas == tareas_ok", a == 0,
               f"{d['tareas_ok_en_tabla']} + {d['tareas_ok_archivadas']} vs {d['tareas_ok']}")
            ok("identidad: ok_archivadas + fallo_archivadas == archivadas", b == 0,
               f"{d['tareas_ok_archivadas']} + {d['tareas_fallo_archivadas']} "
               f"vs {d['tareas_archivadas']}")
            ok("con la cuenta vieja (un solo contador) habria descuadre de 3",
               d["tareas_ok_en_tabla"] + d["tareas_archivadas"] != d["tareas_ok"],
               f"{d['tareas_ok_en_tabla']} + {d['tareas_archivadas']} vs {d['tareas_ok']}")

            # ---------------- B. /stats bajo carga ----------------
            # El muestreador va a ~50 peticiones/s: con un bucle sin pausa el
            # escritor no consigue el cerrojo de escritura y la prueba no mide
            # nada (medido: 1 escritura en 8 s y 1.820 muestras).
            print(f"\nB. /stats con {ESCRITORES} escritores durante {SEGUNDOS_CARGA} s")
            stop = threading.Event()
            cuenta = [0]
            hilos = [threading.Thread(target=escritor, args=(db, stop, cuenta), daemon=True)
                     for _ in range(ESCRITORES)]
            for h in hilos:
                h.start()
            muestras = descuadres = errores = 0
            ejemplos = []
            inicio = time.time()
            try:
                while True:
                    transcurrido = time.time() - inicio
                    if transcurrido >= MAX_CARGA:
                        break
                    if transcurrido >= SEGUNDOS_CARGA and cuenta[0] >= MIN_ESCRITURAS:
                        break
                    try:
                        s = core.http("/stats")
                        muestras += 1
                        a, b = identidad(s)
                        if a != 0 or b != 0:
                            descuadres += 1
                            if len(ejemplos) < 5:
                                ejemplos.append({"ok": s["tareas_ok"],
                                                 "en_tabla": s["tareas_ok_en_tabla"],
                                                 "desvio": a, "desvio_arch": b})
                    except Exception as e:
                        errores += 1
                        if len(ejemplos) < 5:
                            ejemplos.append({"error": str(e)[:80]})
                    time.sleep(0.05)
            finally:
                stop.set()
                for h in hilos:
                    h.join(timeout=5)

            print(f"     {time.time() - inicio:.1f} s | muestras {muestras} | "
                  f"escrituras {cuenta[0]} | descuadres {descuadres} | errores {errores}")
            ok("la carga escribio de verdad", cuenta[0] >= MIN_ESCRITURAS,
               f"{cuenta[0]} escrituras (minimo {MIN_ESCRITURAS})")
            ok("cero descuadres en la identidad bajo carga", descuadres == 0,
               f"{descuadres} de {muestras} muestras")
            ok("ninguna peticion a /stats fallo", errores == 0, f"{errores} errores")
            for e in ejemplos:
                print("    ", e)

            # ---------------- C. huella de /version ----------------
            print("\nC. Huella de /version incluye claves.py y cuadra con el repositorio")
            ver = core.http("/version")
            fich = ver.get("ficheros") or {}
            claves = fich.get("coordinator/claves.py") or {}
            ok("claves.py esta en la huella", bool(claves.get("sha256")),
               f"{len(fich)} ficheros en la huella")
            disco = (RAIZ / "coordinator" / "claves.py").read_bytes()
            crudo = hashlib.sha256(disco).hexdigest()
            norm = hashlib.sha256(disco.replace(b"\r\n", b"\n")).hexdigest()
            # /version publica el hash recortado a 16 hex (64 bits), no los 64
            # caracteres: se comprueba el prefijo, que es lo que permite
            # verificar el fichero desde fuera con sha256sum.
            ok("sha256 (bytes en disco) es prefijo del fichero real",
               crudo.startswith(claves.get("sha256", "-")),
               f"{claves.get('sha256', '')} vs {crudo[:16]}")
            ok("sha256_git (normalizada a LF) es prefijo del fichero real",
               norm.startswith(claves.get("sha256_git", "-")),
               f"{claves.get('sha256_git', '')} vs {norm[:16]}")
            ok("las dos formas publicadas son distintas (CRLF vs LF)",
               claves.get("sha256") != claves.get("sha256_git"),
               f"{claves.get('sha256', '')} / {claves.get('sha256_git', '')}")

            core_h = fich.get("coordinator/kukia_core.py") or {}
            core_disco = hashlib.sha256(
                (RAIZ / "coordinator" / "kukia_core.py").read_bytes()).hexdigest()
            ok("kukia_core.py tambien esta en la huella", bool(core_h.get("sha256")),
               f"{core_h.get('sha256', '')}")
            ok("la huella del Core es el fichero, no un modulo importado",
               core_disco.startswith(core_h.get("sha256", "-")),
               f"{core_h.get('sha256', '')} vs {core_disco[:16]}")
        finally:
            core.parar()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    fallos = [r for r in RES if not r["pasa"]]
    print(f"\n[identidad stats] {len(RES) - len(fallos)}/{len(RES)} PASS")
    print(json.dumps({"total": len(RES), "pass": len(RES) - len(fallos),
                      "fail": len(fallos),
                      "fallos": [r["prueba"] for r in fallos]}, ensure_ascii=False))
    return 0 if not fallos else 1


if __name__ == "__main__":
    raise SystemExit(main())
