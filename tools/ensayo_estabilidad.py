#!/usr/bin/env python3
"""Ensayo de estabilidad del minero: una hora minando y todo apuntado.

QUE HACE
  Levanta un Core AISLADO (copia verificada del ledger con `backup_ledger.py`,
  su propia clave y su propio puerto) y mina contra el con el worker del repo.
  La red de verdad no se toca: ni un pago, ni un asiento, ni un nodo de
  produccion. El unico contacto con el PC es el uso de CPU/GPU, que es lo que
  se quiere medir.

QUE MIDE, cada 60 s
  Del Core (cifras confirmadas, no lo que dice el minero):
    tareas pedidas / terminadas / fallidas / en vuelo, cola,
    GFLOP/min medidos por el Core, KIA por motivo (trabajo verificado,
    verificacion redundante), precio por GFLOP, suministro,
    rechazos del planificador por motivo.
  Del PC:
    uso y VRAM de cada GPU, temperatura, vatios si hay sensor, CPU total,
    RAM, disco libre.
  Del minero:
    sigue vivo, y sus resumenes (KIA/h, tareas/min) quedan en el log.

COMO SE PARA
  Parada ordenada (W.PARAR, la misma que usa Ctrl+C): entrega lo que lleva
  encima y sale. Si en 30 s no sale, se apunta como parada sucia.

USO
  python tools/ensayo_estabilidad.py --minutos 60 --gpus 0,1 --etiqueta gpu2
  python tools/ensayo_estabilidad.py --minutos 60 --gpus 1 --cpu 1 --etiqueta cpu_gpu
  python tools/ensayo_estabilidad.py --minutos 2 --etiqueta humo   # prueba corta

SALIDA
  medidas/ensayos/<etiqueta>_<fecha>/  con resumen.json, muestras.jsonl,
  core.log y ensayo.log. No se borra nada.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB_REAL = RAIZ / "coordinator" / "ledger" / "kukia.db"
sys.path.insert(0, str(RAIZ))

TEMP_MAXIMA = 85.0          # °C: se para el ensayo si se pasa
MINUTOS_MUESTRA = 60.0


# ---------------------------------------------------------------------------
#  Utilidades
# ---------------------------------------------------------------------------
def ahora() -> float:
    return time.time()


def get_json(base: str, ruta: str, timeout: float = 10.0):
    with urllib.request.urlopen(base + ruta, timeout=timeout) as r:
        return json.loads(r.read().decode())


def puerto_libre() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def epoch(txt) -> float | None:
    """El Core guarda `creado`/`fin` como texto y `asignado` como epoch."""
    if txt is None:
        return None
    if isinstance(txt, (int, float)):
        return float(txt)
    for fmt in ("%a %b %d %H:%M:%S %Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(txt), fmt).timestamp()
        except ValueError:
            continue
    return None


def percentil(valores: list[float], p: float) -> float | None:
    if not valores:
        return None
    v = sorted(valores)
    k = min(len(v) - 1, max(0, int(round((p / 100.0) * (len(v) - 1)))))
    return v[k]


def gpu_snapshot() -> list[dict]:
    """nvidia-smi; si no hay GPU o no responde, lista vacia (no se inventa)."""
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,"
             "temperature.gpu,power.draw,clocks.sm",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return []
        salida = []
        for linea in r.stdout.strip().splitlines():
            cols = [c.strip() for c in linea.split(",")]
            if len(cols) < 8:
                continue

            def num(x):
                try:
                    return float(x)
                except ValueError:
                    return None

            salida.append({"gpu": cols[0], "nombre": cols[1],
                           "uso_pct": num(cols[2]), "vram_mb": num(cols[3]),
                           "vram_total_mb": num(cols[4]), "temp_c": num(cols[5]),
                           "w": num(cols[6]), "reloj_mhz": num(cols[7])})
        return salida
    except Exception:
        return []


def pc_snapshot(pid_core: int | None) -> dict:
    try:
        import psutil
    except Exception:
        return {"psutil": False}
    datos = {
        "psutil": True,
        "cpu_pct": psutil.cpu_percent(interval=None),
        "ram_pct": psutil.virtual_memory().percent,
        "ram_libre_gb": round(psutil.virtual_memory().available / 1e9, 1),
        "disco_libre_gb": round(psutil.disk_usage(str(RAIZ)).free / 1e9, 1),
    }
    if pid_core:
        try:
            p = psutil.Process(pid_core)
            datos["core_cpu_pct"] = round(p.cpu_percent(interval=None), 1)
            datos["core_ram_mb"] = round(p.memory_info().rss / 1e6, 1)
        except Exception:
            pass
    return datos


def peticiones_core(log: Path) -> dict:
    """Cuenta peticiones del minero en el log de acceso del Core de pruebas."""
    fuera: dict[str, int] = {}
    try:
        texto = log.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return fuera
    for linea in texto.splitlines():
        if "GET /tareas/pendiente" in linea:
            fuera["pendiente"] = fuera.get("pendiente", 0) + 1
        elif "POST /tareas/resultado" in linea:
            fuera["resultado"] = fuera.get("resultado", 0) + 1
        elif "POST /tareas/replica" in linea:
            fuera["replica"] = fuera.get("replica", 0) + 1
        if '" 5' in linea or " 500 " in linea:
            fuera["errores_5xx"] = fuera.get("errores_5xx", 0) + 1
    return fuera


def db_ro(ruta: Path) -> sqlite3.Connection | None:
    try:
        con = sqlite3.connect(f"file:{ruta.as_posix()}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        return con
    except Exception:
        return None


def ids_de_nodo(ruta: Path) -> list[str]:
    """Los ids de nodo de esta instalacion de prueba (cpu y/o gpus)."""
    try:
        d = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except Exception:
        return []
    ids = []

    def recorrer(x):
        if isinstance(x, dict):
            if isinstance(x.get("id"), str):
                ids.append(x["id"])
            for v in x.values():
                recorrer(v)
        elif isinstance(x, list):
            for v in x:
                recorrer(v)

    recorrer(d)
    return ids


def metricas_db(db: Path, t0: float, nodos: list[str] | None = None) -> dict:
    con = db_ro(db)
    if con is None:
        return {"db": False}
    try:
        filas = con.execute(
            "SELECT estado, COUNT(*) n FROM tareas WHERE fin IS NOT NULL"
            " AND fin IS NOT NULL GROUP BY estado").fetchall()
        estados = {r["estado"]: r["n"] for r in filas}
        if nodos:
            marcas = ",".join("?" * len(nodos))
            en_vuelo = con.execute(
                f"SELECT COUNT(*) c FROM tareas WHERE estado='ejecutando'"
                f" AND nodo_id IN ({marcas})", nodos).fetchone()["c"]
        else:
            en_vuelo = con.execute(
                "SELECT COUNT(*) c FROM tareas WHERE estado='ejecutando'").fetchone()["c"]
        # Tareas que se reintentaron: sintoma de entregas que fallan.
        reintentos = con.execute(
            "SELECT COUNT(*) c FROM tareas WHERE COALESCE(intentos,0) > 0"
            " AND COALESCE(asignado,0) >= ?", (t0 - 60,)).fetchone()["c"]
        atascadas = con.execute(
            "SELECT COUNT(*) c FROM tareas WHERE estado='ejecutando'"
            " AND COALESCE(asignado,0) > 0 AND asignado < ?", (t0 - 1800,)).fetchone()["c"]
        fallos = con.execute(
            "SELECT COUNT(*) c FROM tareas WHERE estado NOT IN ('ok','queued','ejecutando')"
        ).fetchone()["c"]
        cola = con.execute(
            "SELECT COUNT(*) c FROM tareas WHERE estado='queued'").fetchone()["c"]
        kia = con.execute(
            "SELECT motivo, COUNT(*) n, COALESCE(SUM(kuki),0) s FROM ledger"
            " GROUP BY motivo").fetchall()
        kia_dict = {r["motivo"]: {"n": r["n"], "kuki": round(float(r["s"]), 6)}
                    for r in kia}
        return {
            "db": True,
            "tareas_por_estado": estados,
            "en_vuelo": en_vuelo,
            "cola": cola,
            "reintentos": reintentos,
            "atascadas": atascadas,
            "tareas_fallidas": fallos,
            "kia": kia_dict,
        }
    except Exception as e:
        return {"db": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        con.close()


def latencias(db: Path, desde: float) -> dict:
    """Espera en cola y latencia de entrega, en segundos, de las tareas cerradas."""
    con = db_ro(db)
    if con is None:
        return {}
    try:
        # rowid, no id: los ids son hashes en texto y ordenarlos alfabeticamente
        # no da los mas recientes (fallo detectado el 23-10 en la prueba corta).
        filas = con.execute(
            "SELECT creado, asignado, fin FROM tareas WHERE estado='ok'"
            " AND fin IS NOT NULL ORDER BY rowid DESC LIMIT 20000").fetchall()
    except Exception:
        return {}
    finally:
        con.close()
    espera, entrega = [], []
    for r in filas:
        f = epoch(r["fin"])
        a = r["asignado"]
        c = epoch(r["creado"])
        if f is None or f < desde or a is None:
            continue
        # Solo tareas creadas en este ensayo: las de la copia ya venian hechas.
        if c is not None and c < desde - 60:
            continue
        entrega.append(max(0.0, f - float(a)))
        if c is not None:
            espera.append(max(0.0, float(a) - c))
    return {
        "n": len(entrega),
        "cola_p50": percentil(espera, 50), "cola_p90": percentil(espera, 90),
        "cola_max": max(espera) if espera else None,
        "entrega_p50": percentil(entrega, 50), "entrega_p90": percentil(entrega, 90),
        "entrega_max": max(entrega) if entrega else None,
    }


# ---------------------------------------------------------------------------
#  Ensayo
# ---------------------------------------------------------------------------
class Ensayo:
    def __init__(self, a):
        self.a = a
        self.t0 = None
        self.muestras: list[dict] = []
        self.base = Path(a.base) / f"{a.etiqueta}_{time.strftime('%Y%m%d_%H%M%S')}"
        self.base.mkdir(parents=True, exist_ok=True)
        self.log = self.base / "ensayo.log"
        self.core = None
        self.core_log = None
        self.hilo = None
        self.worker = None
        self.carpeta_db = None
        self.nodos: list[str] = []
        self.serie_final: list[dict] = []
        self.puerto = puerto_libre()
        self.url = f"http://127.0.0.1:{self.puerto}"

    # -- preparacion ------------------------------------------------------
    def apuntar(self, texto: str) -> None:
        linea = f"[{time.strftime('%H:%M:%S')}] {texto}"
        print(linea, flush=True)
        with self.log.open("a", encoding="utf-8") as f:
            f.write(linea + "\n")

    def copiar_ledger(self) -> Path:
        dest = self.base / "ledger"
        dest.mkdir(exist_ok=True)
        self.apuntar(f"copia verificada del ledger -> {dest}")
        r = subprocess.run([sys.executable, str(RAIZ / "tools" / "backup_ledger.py"),
                            "--destino", str(dest), "--sin-clave"],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise SystemExit(f"[ensayo] backup_ledger fallo: {r.stdout[-800:]} {r.stderr[-800:]}")
        carpetas = sorted(p for p in dest.iterdir() if (p / "kukia.db").exists())
        if not carpetas:
            raise SystemExit("[ensayo] la copia no tiene kukia.db")
        return carpetas[-1]

    def arrancar_core(self, carpeta: Path) -> None:
        entorno = dict(os.environ)
        entorno.update({
            "KUKIA_DB": str(carpeta / "kukia.db"),
            "KUKIA_SELLOS_FILE": str(carpeta / "sellos.jsonl"),
            "KUKIA_SELLO_KEY": str(carpeta / "sello_key.txt") if (carpeta / "sello_key.txt").exists() else "",
            "KUKIA_AUTO_TAREAS": "1",
            "KUKIA_ARCHIVAR": "0",
            "KUKIA_SELLO_SEG": "3600",
            # La copia NO lleva la clave del sello (no se duplica un secreto en
            # un temporal): los sellos viejos no cuadraran con la clave nueva,
            # asi que no se bloquean pagos por ellos. Se apunta y ya esta.
            "KUKIA_SELLO_BLOQUEA": "0",
            "KUKIA_PAGO_POR_FLOP": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
        })
        self.core_log = (self.base / "core.log").open("w", encoding="utf-8")
        self.core = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "kukia_core:app",
             "--host", "127.0.0.1", "--port", str(self.puerto)],
            cwd=str(RAIZ / "coordinator"), env=entorno,
            stdout=self.core_log, stderr=subprocess.STDOUT)
        t = time.time()
        while time.time() - t < 180:
            if self.core.poll() is not None:
                raise SystemExit("[ensayo] el Core de pruebas murio al arrancar "
                                 f"(codigo {self.core.returncode}); ver {self.base/'core.log'}")
            try:
                get_json(self.url, "/stats", timeout=3)
                self.apuntar(f"Core de pruebas vivo en {self.url}")
                return
            except Exception:
                time.sleep(1.0)
        raise SystemExit("[ensayo] el Core de pruebas no respondio en 180 s")

    def arrancar_minero(self) -> None:
        os.environ["KUKIA_CORE"] = self.url
        os.environ["KUKIA_USAR_CPU"] = str(self.a.cpu)
        os.environ["KUKIA_PROFUNDIDAD"] = str(self.a.profundidad)
        os.environ["KUKIA_RESUMEN_SEG"] = "60"
        os.environ["KUKIA_CORE_INSEGURO"] = "0"
        if self.a.gpus:
            os.environ["CUDA_VISIBLE_DEVICES"] = self.a.gpus
        else:
            os.environ["CUDA_VISIBLE_DEVICES"] = ""

        if self.a.worker:
            # Variante concreta del worker (p. ej. la copia de antes de un cambio):
            # se monta un arbol aparte para que `../ledger` resuelva donde toca y
            # no se toca el worker del repo.
            import importlib.util
            arbol = self.base / "variante"
            (arbol / "worker").mkdir(parents=True, exist_ok=True)
            (arbol / "ledger").mkdir(parents=True, exist_ok=True)
            destino = arbol / "worker" / "kukia_worker.py"
            destino.write_bytes(Path(self.a.worker).read_bytes())
            for nombre in ("kukia_wallet.py", "wordlist_es.txt"):
                origen = RAIZ / "ledger" / nombre
                if origen.exists():
                    (arbol / "ledger" / nombre).write_bytes(origen.read_bytes())
            spec = importlib.util.spec_from_file_location("kukia_worker_variante", destino)
            W = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(W)
            self.apuntar(f"worker variante: {self.a.worker}")
        else:
            sys.path.insert(0, str(RAIZ / "worker"))
            import kukia_worker as W  # noqa: E402  (despues de fijar el entorno)

        W.LOCK = self.base / "minero.lock"
        W.IDENTIDAD = self.base / "identidad_minero.json"
        sys.argv = ["kukia_worker.py", f"ensayo_{self.a.etiqueta}"]
        self.worker = W
        self.hilo = threading.Thread(target=W.main, name="minero-ensayo", daemon=True)
        self.hilo.start()
        self.apuntar(f"minero lanzado (gpus={self.a.gpus or 'ninguna'}, cpu={self.a.cpu}, "
                     f"profundidad={self.a.profundidad})")

    # -- muestreo ---------------------------------------------------------
    def muestra(self, i: int) -> dict:
        d = {"i": i, "t": ahora()}
        d["minuto"] = round((d["t"] - self.t0) / 60.0, 2)
        try:
            s = get_json(self.url, "/stats")
            b = {"supply": float(s.get("supply_emitido") or 0),
                 "tareas_ok": int(s.get("tareas_ok") or 0),
                 "pend": int(s.get("tareas_pend") or 0),
                 "nodos": int(s.get("nodos") or 0),
                 "gflop_min": float((s.get("detalle_pago") or {}).get("gflop_por_min") or 0),
                 "emision_min": float(s.get("emision_por_min") or 0),
                 "modo_pago": s.get("modo_pago") or s.get("pago_por_potencia")}
            d["core"] = b
        except Exception as e:
            d["core"] = {"error": f"{type(e).__name__}: {e}"}
        if not self.nodos:
            self.nodos = ids_de_nodo(self.base / "identidad_minero.json")
        d.update(metricas_db(self.carpeta_db, self.t0, self.nodos))
        d["gpus"] = gpu_snapshot()
        d["pc"] = pc_snapshot(self.core.pid if self.core else None)
        d["minero_vivo"] = bool(self.hilo and self.hilo.is_alive())
        try:
            serie = get_json(self.url, "/red/serie?minutos=3")
            d["serie"] = (serie.get("puntos") or [])[-1:] or []
            d["gflops_ventana"] = serie.get("gflops_ventana")
        except Exception:
            d["serie"] = []
        self.muestras.append(d)
        with (self.base / "muestras.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
        self.pintar(d)
        return d

    def pintar(self, d: dict) -> None:
        c = d.get("core") or {}
        g = d.get("gpus") or []
        gpu_txt = " ".join(
            f"g{x['gpu']}:{x['uso_pct'] if x['uso_pct'] is not None else '?'}%"
            f"/{x['temp_c'] if x['temp_c'] is not None else '?'}C"
            f"/{x['w'] if x['w'] is not None else '?'}W" for x in g) or "sin GPU"
        self.apuntar(
            f"t={d['minuto']:5.1f}m  ok={c.get('tareas_ok', '?')} cola={d.get('cola', '?')} "
            f"vuelo={d.get('en_vuelo', '?')} gflop/min={c.get('gflop_min', 0):.1f} "
            f"ema={c.get('supply', 0):.2f}  {gpu_txt}  cpu={d.get('pc', {}).get('cpu_pct', '?')}%")

    # -- cierre -----------------------------------------------------------
    def parar(self) -> dict:
        salida = {"parada_ordenada": False, "segundos_parada": None}
        if self.worker is not None and self.hilo is not None:
            t = time.time()
            evento = getattr(self.worker, "PARAR", None)
            if evento is not None:
                evento.set()
            else:
                # Variante antigua (una tarea detras de otra, sin evento de
                # parada): se le lanza un KeyboardInterrupt en su hilo, que es
                # lo que hace Ctrl+C. Es solo para el ensayo comparativo.
                import ctypes
                ctypes.pythonapi.PyThreadState_SetAsyncExc(
                    ctypes.c_ulong(self.hilo.ident), ctypes.py_object(KeyboardInterrupt))
                self.apuntar("variante sin PARAR: interrumpida como Ctrl+C")
            self.hilo.join(timeout=30)
            salida["segundos_parada"] = round(time.time() - t, 1)
            salida["parada_ordenada"] = not self.hilo.is_alive()
            self.apuntar(f"parada del minero: {'limpia' if salida['parada_ordenada'] else 'SUCIA'} "
                         f"en {salida['segundos_parada']} s")
        if self.core is not None and self.core.poll() is None:
            self.core.terminate()
            try:
                self.core.wait(timeout=20)
            except Exception:
                self.core.kill()
        if self.core_log:
            try:
                self.core_log.close()
            except Exception:
                pass
        return salida

    def resumen(self, parada: dict, motivo_fin: str) -> dict:
        db = self.carpeta_db
        m = metricas_db(db, self.t0, self.nodos)
        lat = latencias(db, self.t0)
        serie = self.serie_final
        # KIA DENTRO DE LA VENTANA: diferencia entre la primera y la ultima
        # muestra. El total de la base es historico y no sirve para esto.
        kia = {}
        if self.muestras:
            ini = (self.muestras[0].get("kia") or {})
            fin = (self.muestras[-1].get("kia") or {})
            for motivo in set(ini) | set(fin):
                a = (ini.get(motivo) or {}).get("kuki", 0.0)
                b = (fin.get(motivo) or {}).get("kuki", 0.0)
                na = (ini.get(motivo) or {}).get("n", 0)
                nb = (fin.get(motivo) or {}).get("n", 0)
                kia[motivo] = {"kuki": round(float(b) - float(a), 6),
                               "n": int(nb) - int(na)}
        trabajo = kia.get("trabajo verificado", {})
        redundante = kia.get("verificacion redundante", {})
        marcas = [s for s in self.muestras if s.get("core", {}).get("tareas_ok")]
        primera = marcas[0]["core"]["tareas_ok"] if marcas else None
        ultima = marcas[-1]["core"]["tareas_ok"] if marcas else None
        minutos = (self.muestras[-1]["minuto"] if self.muestras else 0) or 1
        temps = [g["temp_c"] for s in self.muestras for g in (s.get("gpus") or [])
                 if g.get("temp_c") is not None]
        usos = [g["uso_pct"] for s in self.muestras for g in (s.get("gpus") or [])
                if g.get("uso_pct") is not None]
        serie_tareas = [p.get("tareas", 0) for p in serie]
        serie_gflops = [p.get("gflops", 0) for p in serie]
        mitad = max(1, len(serie_tareas) // 6)
        return {
            "etiqueta": self.a.etiqueta,
            "dispositivos": {"gpus": self.a.gpus or "", "cpu": bool(int(self.a.cpu)),
                             "profundidad": self.a.profundidad},
            "minutos_medidos": minutos,
            "motivo_fin": motivo_fin,
            "parada": parada,
            "core_aislado": self.url,
            "db": str(db),
            "tareas": {
                "ok_en_tabla": m.get("tareas_por_estado", {}).get("ok"),
                "delta_ok_en_ventana": (ultima - primera) if (ultima and primera) else None,
                "cola_final": m.get("cola"),
                "en_vuelo_final": m.get("en_vuelo"),
                "por_estado": m.get("tareas_por_estado"),
                "reintentos": m.get("reintentos"),
                "atascadas_mas_de_30m": m.get("atascadas"),
                "fallidas": m.get("tareas_fallidas"),
                "peticiones_al_core": peticiones_core(self.base / "core.log"),
            },
            "gflop": {
                "por_minuto_media": round(sum(serie_gflops) / len(serie_gflops), 2) if serie_gflops else None,
                "por_minuto_min": min(serie_gflops) if serie_gflops else None,
                "por_minuto_max": max(serie_gflops) if serie_gflops else None,
                "ventana": self.muestras[-1].get("gflops_ventana") if self.muestras else None,
            },
            "kia": {
                "trabajo_verificado": trabajo,
                "verificacion_redundante": redundante,
                "otros_motivos": {k: v for k, v in kia.items()
                                  if k not in ("trabajo verificado", "verificacion redundante")},
            },
            "latencias_s": lat,
            "por_minuto": {
                "tareas": serie_tareas,
                "gflops": serie_gflops,
                "minutos": len(serie_tareas),
                "tareas_primer_tercio": round(sum(serie_tareas[:mitad]) / mitad, 2) if serie_tareas else None,
                "tareas_ultimo_tercio": round(sum(serie_tareas[-mitad:]) / mitad, 2) if serie_tareas else None,
            },
            "hardware": {
                "temp_max_c": max(temps) if temps else None,
                "uso_medio_pct": round(sum(usos) / len(usos), 1) if usos else None,
                "cpu_medio_pct": round(sum(s.get("pc", {}).get("cpu_pct") or 0
                                           for s in self.muestras) / max(1, len(self.muestras)), 1),
                "ram_media_pct": round(sum(s.get("pc", {}).get("ram_pct") or 0
                                           for s in self.muestras) / max(1, len(self.muestras)), 1),
                "gpus": self.muestras[-1].get("gpus") if self.muestras else [],
            },
            "muestras": len(self.muestras),
            "serie_completa": serie,
        }

    # -- orquestacion -----------------------------------------------------
    def correr(self) -> int:
        if not DB_REAL.exists():
            print("[ensayo] no existe el ledger; no hay nada que copiar")
            return 2
        carpetas = self.copiar_ledger()
        self.carpeta_db = carpetas / "kukia.db"
        self.arrancar_core(carpetas)

        # Produccion: foto antes, para saber que sigue viva la red de verdad.
        produccion_antes = None
        try:
            produccion_antes = get_json("http://127.0.0.1:8300", "/stats")
        except Exception:
            pass

        self.arrancar_minero()
        self.t0 = ahora()
        try:
            pc_snapshot(None)          # primera medida de psutil (devuelve 0)
        except Exception:
            pass
        motivo = "tiempo cumplido"
        i = 0
        try:
            while (ahora() - self.t0) < self.a.minutos * 60.0:
                d = self.muestra(i)
                i += 1
                if not d.get("minero_vivo"):
                    motivo = "el minero se paro solo"
                    break
                temps = [g["temp_c"] for g in (d.get("gpus") or []) if g.get("temp_c") is not None]
                if temps and max(temps) >= TEMP_MAXIMA:
                    motivo = f"temperatura {max(temps):.0f} C >= {TEMP_MAXIMA:.0f} C"
                    break
                restante = self.a.minutos * 60.0 - (ahora() - self.t0)
                time.sleep(max(1.0, min(MINUTOS_MUESTRA, restante)))
            # Muestra final: cierra la ventana (si no, el ultimo minuto no cuenta).
            self.muestra(i)
        except KeyboardInterrupt:
            motivo = "interrumpido a mano"
        finally:
            # La serie se pide ANTES de apagar el Core, si no no hay de donde.
            try:
                puntos = get_json(self.url, "/red/serie?minutos=180").get("puntos") or []
                self.serie_final = [p for p in puntos if (p.get("t") or 0) >= self.t0 - 60]
            except Exception:
                self.serie_final = []
            parada = self.parar()

        resumen = self.resumen(parada, motivo)
        produccion_despues = None
        try:
            produccion_despues = get_json("http://127.0.0.1:8300", "/stats")
        except Exception:
            pass
        if produccion_antes and produccion_despues:
            resumen["red_de_verdad"] = {
                "tareas_ok_antes": produccion_antes.get("tareas_ok"),
                "tareas_ok_despues": produccion_despues.get("tareas_ok"),
                "delta": (int(produccion_despues.get("tareas_ok") or 0)
                          - int(produccion_antes.get("tareas_ok") or 0)),
                "supply_antes": produccion_antes.get("supply_emitido"),
                "supply_despues": produccion_despues.get("supply_emitido"),
            }
        (self.base / "resumen.json").write_text(
            json.dumps(resumen, indent=2, ensure_ascii=False), encoding="utf-8")
        self.imprimir_resumen(resumen)
        print(f"\n[ensayo] todo guardado en {self.base}")
        return 0

    def imprimir_resumen(self, r: dict) -> None:
        print("\n" + "=" * 68)
        print(f"ENSAYO {r['etiqueta']}  ·  {r['minutos_medidos']} min  ·  {r['motivo_fin']}")
        t = r["tareas"]
        print(f"  tareas ok (delta)      {t['delta_ok_en_ventana']}")
        print(f"  cola final / en vuelo  {t['cola_final']} / {t['en_vuelo_final']}")
        g = r["gflop"]
        print(f"  GFLOP/min (Core)       media {g['por_minuto_media']}  "
              f"min {g['por_minuto_min']}  max {g['por_minuto_max']}")
        print(f"  KIA trabajo            {r['kia']['trabajo_verificado']}")
        print(f"  KIA verificacion       {r['kia']['verificacion_redundante']}")
        l = r["latencias_s"]
        print(f"  latencia cola  p50/p90/max  {l.get('cola_p50')}/{l.get('cola_p90')}/{l.get('cola_max')} s")
        print(f"  latencia entrega p50/p90/max {l.get('entrega_p50')}/{l.get('entrega_p90')}/{l.get('entrega_max')} s")
        h = r["hardware"]
        print(f"  GPU uso medio / temp max  {h['uso_medio_pct']} % / {h['temp_max_c']} C")
        print(f"  CPU media / RAM media     {h['cpu_medio_pct']} % / {h['ram_media_pct']} %")
        pet = t.get("peticiones_al_core") or {}
        if pet:
            print(f"  peticiones al Core     {pet}")
        print(f"  reintentos / atascadas {t.get('reintentos')} / {t.get('atascadas_mas_de_30m')}")
        print(f"  parada                 {r['parada']}")
        print("=" * 68)


def main(argv=None) -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Ensayo de estabilidad del minero (Core aislado)")
    ap.add_argument("--minutos", type=float, default=60)
    ap.add_argument("--gpus", default="0,1", help="CUDA_VISIBLE_DEVICES (vacio = sin GPU)")
    ap.add_argument("--cpu", default="0", help="KUKIA_USAR_CPU (0/1/numero de hilos)")
    ap.add_argument("--profundidad", type=int, default=2)
    ap.add_argument("--worker", default=None,
                    help="ruta a otra version del worker (comparaciones)")
    ap.add_argument("--etiqueta", default="ensayo")
    ap.add_argument("--base", default=str(RAIZ / "medidas" / "ensayos"))
    a = ap.parse_args(argv)
    return Ensayo(a).correr()


if __name__ == "__main__":
    raise SystemExit(main())
