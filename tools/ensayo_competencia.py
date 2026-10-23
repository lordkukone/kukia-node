#!/usr/bin/env python3
"""Ensayo de COMPETENCIA: dos nodos peleando por la misma emision.

QUE PRUEBA
  La medicion del 23-10 con un solo nodo dijo que subir la GPU al 100 % no
  pagaba mas, porque la emision va por presupuesto y ese nodo ya se llevaba
  todo el presupuesto. La pregunta que queda: **con otro nodo compitiendo, si
  paga**. Aqui se mide.

MONTAJE
  - Core AISLADO (copia verificada del ledger, su puerto, su clave). La red de
    verdad no se toca.
  - Dos mineros de verdad, cada uno en su arbol (identidad y cerradura propias):
      rival  -> GPU 1, SIEMPRE igual (1 ranura): el que se lleva su parte.
      test   -> GPU 0, con --ranuras 1 o 2 (la variable del ensayo).
    El reparto de la emision depende de cuanta potencia aporta cada uno, asi
    que la comparacion es test contra test: mismo rival, misma GPU, mismo Core.

QUE MIDE, cada 60 s
  Del Core: KIA/min total, precio por GFLOP, GFLOP/min de la red, cola.
  Por nodo (del ledger, no de lo que dice el minero): KIA y tareas, y su parte
  del total.
  De la maquina: uso de cada GPU por separado (para comprobar que el test si
  sube de verdad al 100 %).

USO
  python tools/ensayo_competencia.py --minutos 14 --ranuras 1
  python tools/ensayo_competencia.py --minutos 14 --ranuras 2

SALIDA
  medidas/ensayos/comp_r<ranuras>_<fecha>/ con resumen.json, muestras.jsonl,
  core.log y los logs de cada minero. No se borra nada.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))
import ensayo_estabilidad as H  # noqa: E402  (utilidades ya probadas)

DB_REAL = RAIZ / "coordinator" / "ledger" / "kukia.db"
TEMP_MAXIMA = 85.0


def log(base: Path, texto: str) -> None:
    linea = f"[{time.strftime('%H:%M:%S')}] {texto}"
    print(linea, flush=True)
    with (base / "ensayo.log").open("a", encoding="utf-8") as f:
        f.write(linea + "\n")


def copiar_ledger(base: Path) -> Path:
    dest = base / "ledger"
    dest.mkdir(exist_ok=True)
    r = subprocess.run([sys.executable, str(RAIZ / "tools" / "backup_ledger.py"),
                        "--destino", str(dest), "--sin-clave"],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise SystemExit(f"[comp] backup_ledger fallo: {r.stdout[-600:]} {r.stderr[-600:]}")
    carpetas = sorted(p for p in dest.iterdir() if (p / "kukia.db").exists())
    if not carpetas:
        raise SystemExit("[comp] la copia no tiene kukia.db")
    log(base, f"copia verificada del ledger -> {carpetas[-1].name}")
    return carpetas[-1]


def arrancar_core(base: Path, carpeta: Path, puerto: int) -> subprocess.Popen:
    entorno = dict(os.environ)
    entorno.update({
        "KUKIA_DB": str(carpeta / "kukia.db"),
        "KUKIA_SELLOS_FILE": str(carpeta / "sellos.jsonl"),
        "KUKIA_SELLO_KEY": "",
        "KUKIA_SELLO_BLOQUEA": "0",
        "KUKIA_SELLO_SEG": "3600",
        "KUKIA_AUTO_TAREAS": "1",
        "KUKIA_ARCHIVAR": "0",
        "KUKIA_PAGO_POR_FLOP": "1",
        # 8 en LOS DOS brazos: el tope por nodo no puede ser la diferencia.
        "KUKIA_MAX_TAREAS_NODO": "8",
        # Dos identidades nuevas: el cupo anti-sybil no debe estorbar.
        "KUKIA_REGISTRO_CUPO_HORA": "0",
        "KUKIA_REGISTRO_CUPO_DIA": "0",
        "KUKIA_REGISTRO_CUPO_CLAVE": "0",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    salida = (base / "core.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "kukia_core:app",
         "--host", "127.0.0.1", "--port", str(puerto)],
        cwd=str(RAIZ / "coordinator"), env=entorno,
        stdout=salida, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{puerto}"
    t = time.time()
    while time.time() - t < 180:
        if proc.poll() is not None:
            raise SystemExit(f"[comp] el Core de pruebas murio (codigo {proc.returncode}); "
                             f"ver {base / 'core.log'}")
        try:
            H.get_json(url, "/stats", timeout=3)
            log(base, f"Core de pruebas vivo en {url}")
            return proc
        except Exception:
            time.sleep(1.0)
    raise SystemExit("[comp] el Core de pruebas no respondio en 180 s")


def preparar_nodo(base: Path, nombre: str) -> Path:
    """Un arbol por nodo: worker + billetera, con su identidad y su cerradura."""
    arbol = base / "nodos" / nombre
    (arbol / "worker").mkdir(parents=True, exist_ok=True)
    (arbol / "ledger").mkdir(parents=True, exist_ok=True)
    shutil.copy2(RAIZ / "worker" / "kukia_worker.py", arbol / "worker" / "kukia_worker.py")
    for fichero in ("kukia_wallet.py", "wordlist_es.txt"):
        origen = RAIZ / "ledger" / fichero
        if origen.exists():
            shutil.copy2(origen, arbol / "ledger" / fichero)
    return arbol


def arrancar_minero(base: Path, arbol: Path, nombre: str, url: str, gpu: str,
                    ranuras: int, profundidad: int, en_vuelo: int) -> subprocess.Popen:
    entorno = dict(os.environ)
    entorno.update({
        "KUKIA_CORE": url,
        "KUKIA_USAR_CPU": "0",
        "KUKIA_RESUMEN_SEG": "60",
        "KUKIA_CORE_INSEGURO": "0",
        "CUDA_VISIBLE_DEVICES": gpu,
        "KUKIA_HILOS_GPU": str(ranuras),
        "KUKIA_PROFUNDIDAD": str(profundidad),
        "KUKIA_EN_VUELO": str(en_vuelo),
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    salida = (base / f"{nombre}.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-u", str(arbol / "worker" / "kukia_worker.py"), nombre],
        cwd=str(arbol), env=entorno, stdout=salida, stderr=subprocess.STDOUT)
    return proc


def ids_nodo(arbol: Path) -> list[str]:
    return H.ids_de_nodo(arbol / "worker" / "identidad_minero.json")


def abrir(db: Path):
    """Conexion de lectura, con plan B.

    El Core de pruebas se para en duro al acabar el ensayo, y a veces deja el
    WAL sin cerrar: abrir en `mode=ro` entonces falla ("attempt to write a
    readonly database", el WAL necesita recuperarse). Se abre normal; es una
    copia de pruebas, no la produccion.
    """
    con = H.db_ro(db)
    if con is not None:
        return con
    try:
        con = sqlite3.connect(str(db), timeout=15)
        con.row_factory = sqlite3.Row
        return con
    except Exception:
        return None


def direcciones(db: Path, ids: list[str]) -> dict:
    """id de nodo -> cuenta a la que cobra.

    El ledger apunta la CUENTA (direccion de cobro), no el id del nodo: sin
    este paso se mide cero. Se resuelve contra la tabla `nodos` del Core de
    pruebas, que es la que manda.
    """
    if not ids:
        return {}
    con = abrir(db)
    if con is None:
        return {}
    try:
        marcas = ",".join("?" * len(ids))
        filas = con.execute(f"SELECT id,direccion_cobro FROM nodos WHERE id IN ({marcas})",
                            ids).fetchall()
    except Exception:
        filas = []
    finally:
        con.close()
    return {f["id"]: f["direccion_cobro"] for f in filas if f["direccion_cobro"]}


def por_cuenta(db: Path, desde: float, cuentas: list[str]) -> dict:
    """KIA, asientos y tareas por cuenta de cobro desde `desde` (epoch)."""
    if not cuentas:
        return {}
    con = abrir(db)
    if con is None:
        return {}
    try:
        marcas = ",".join("?" * len(cuentas))
        filas = con.execute(
            f"SELECT nodo_id,tarea_id,kuki,ts FROM ledger WHERE nodo_id IN ({marcas})",
            cuentas).fetchall()
    except Exception:
        filas = []
    finally:
        con.close()
    salida = defaultdict(lambda: {"kia": 0.0, "asientos": 0, "tareas": set()})
    for nodo, tarea, kuki, ts in filas:
        t = H.epoch(ts)
        if t is None or t < desde:
            continue
        d = salida[nodo]
        d["kia"] += float(kuki or 0.0)
        d["asientos"] += 1
        d["tareas"].add(tarea)
    return {k: {"kia": v["kia"], "asientos": v["asientos"], "tareas": len(v["tareas"])}
            for k, v in salida.items()}


def main(argv=None) -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Dos nodos compitiendo por la emision")
    ap.add_argument("--minutos", type=float, default=14)
    ap.add_argument("--ranuras", type=int, default=1,
                    help="ranuras de calculo por tarjeta del nodo de prueba (1 o 2)")
    ap.add_argument("--gpu-test", default="0")
    ap.add_argument("--gpu-rival", default="1")
    ap.add_argument("--base", default=str(RAIZ / "medidas" / "ensayos"))
    a = ap.parse_args(argv)

    if not DB_REAL.exists():
        print("[comp] no existe el ledger; no hay nada que copiar")
        return 2

    base = Path(a.base) / f"comp_r{a.ranuras}_{time.strftime('%Y%m%d_%H%M%S')}"
    base.mkdir(parents=True, exist_ok=True)
    puerto = H.puerto_libre()
    url = f"http://127.0.0.1:{puerto}"

    carpetas = copiar_ledger(base)
    db = carpetas / "kukia.db"
    core = arrancar_core(base, carpetas, puerto)

    prod_antes = None
    try:
        prod_antes = H.get_json("http://127.0.0.1:8300", "/stats")
    except Exception:
        pass

    # El nodo de prueba: 1 ranura = de serie; 2 = el "100 %".
    prof_test = 2 if a.ranuras == 1 else 6
    vuelo_test = 4 if a.ranuras == 1 else 8
    arbol_rival = preparar_nodo(base, "rival")
    arbol_test = preparar_nodo(base, "test")
    rival = arrancar_minero(base, arbol_rival, "rival", url, a.gpu_rival, 1, 2, 4)
    test = arrancar_minero(base, arbol_test, "test", url, a.gpu_test,
                           a.ranuras, prof_test, vuelo_test)
    log(base, f"rival en GPU {a.gpu_rival} (1 ranura) · test en GPU {a.gpu_test} "
              f"({a.ranuras} ranura(s), profundidad {prof_test}, {vuelo_test} en vuelo)")

    t0 = time.time()
    muestras = []
    motivo = "tiempo cumplido"
    i = 0
    try:
        while (time.time() - t0) < a.minutos * 60.0:
            d = {"i": i, "t": time.time(), "minuto": round((time.time() - t0) / 60.0, 2)}
            try:
                s = H.get_json(url, "/stats")
                d["core"] = {
                    "supply": float(s.get("supply_emitido") or 0),
                    "tareas_ok": int(s.get("tareas_ok") or 0),
                    "cola": int(s.get("tareas_pend") or 0),
                    "emision_min": float(s.get("emision_por_min") or 0),
                    "precio_gflop": float((s.get("pago_por_potencia") or {}).get("precio_gflop") or 0),
                    "gflop_min": float((s.get("pago_por_potencia") or {}).get("gflop_red_por_min") or 0),
                }
            except Exception as e:
                d["core"] = {"error": f"{type(e).__name__}: {e}"}
            d["gpus"] = H.gpu_snapshot()
            ids_r, ids_t = ids_nodo(arbol_rival), ids_nodo(arbol_test)
            cuentas = {"rival": list(direcciones(db, ids_r).values()),
                       "test": list(direcciones(db, ids_t).values())}
            d["nodos"] = {"rival": ids_r, "test": ids_t}
            d["cuentas"] = cuentas
            d["por_nodo"] = {**por_cuenta(db, t0, cuentas["test"]),
                             **por_cuenta(db, t0, cuentas["rival"])}
            d["vivos"] = {"rival": rival.poll() is None, "test": test.poll() is None}
            muestras.append(d)
            with (base / "muestras.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
            c = d["core"]
            gpus = " ".join(f"g{g['gpu']}:{g['uso_pct']}%" for g in d["gpus"])
            kia_t = sum((d["por_nodo"].get(c) or {}).get("kia", 0.0) for c in cuentas["test"])
            kia_r = sum((d["por_nodo"].get(c) or {}).get("kia", 0.0) for c in cuentas["rival"])
            log(base, f"t={d['minuto']:5.1f}m  KIA test={kia_t:7.4f} rival={kia_r:7.4f}  "
                      f"precio={c.get('precio_gflop', 0):.9f}  gflop/min={c.get('gflop_min', 0):.1f}  "
                      f"{gpus}")
            i += 1
            if not d["vivos"]["test"]:
                motivo = "el nodo de prueba se paro solo"
                break
            temps = [g["temp_c"] for g in d["gpus"] if g.get("temp_c") is not None]
            if temps and max(temps) >= TEMP_MAXIMA:
                motivo = f"temperatura {max(temps):.0f} C"
                break
            restante = a.minutos * 60.0 - (time.time() - t0)
            time.sleep(max(1.0, min(60.0, restante)))
        # Muestra final para cerrar la ventana.
        ids_r, ids_t = ids_nodo(arbol_rival), ids_nodo(arbol_test)
        cuentas = {"rival": list(direcciones(db, ids_r).values()),
                   "test": list(direcciones(db, ids_t).values())}
        muestras.append({"i": i, "t": time.time(), "minuto": round((time.time() - t0) / 60.0, 2),
                         "cierre": True,
                         "por_nodo": {**por_cuenta(db, t0, cuentas["test"]),
                                      **por_cuenta(db, t0, cuentas["rival"])},
                         "cuentas": cuentas,
                         "nodos": {"rival": ids_r, "test": ids_t}})
    except KeyboardInterrupt:
        motivo = "interrumpido a mano"
    finally:
        for p in (test, rival):
            try:
                p.terminate()
            except Exception:
                pass
        try:
            core.terminate()
            core.wait(timeout=20)
        except Exception:
            try:
                core.kill()
            except Exception:
                pass

    ids_r = ids_nodo(arbol_rival)
    ids_t = ids_nodo(arbol_test)
    cta_r = list(direcciones(db, ids_r).values())
    cta_t = list(direcciones(db, ids_t).values())
    fin = {**por_cuenta(db, t0, cta_t), **por_cuenta(db, t0, cta_r)}
    minutos = max(0.1, (time.time() - t0) / 60.0)

    def suma(cuentas):
        return sum((fin.get(c) or {}).get("kia", 0.0) for c in cuentas)

    kia_t, kia_r = suma(cta_t), suma(cta_r)
    total = kia_t + kia_r
    gpus = muestras[-2].get("gpus") if len(muestras) > 1 else []
    serie = defaultdict(list)
    for m in muestras:
        for g in (m.get("gpus") or []):
            serie[g["gpu"]].append(g.get("uso_pct"))
    resumen = {
        "ranuras_test": a.ranuras,
        "minutos": round(minutos, 2),
        "motivo_fin": motivo,
        "core_aislado": url,
        "db": str(db),
        "ids": {"test": ids_t, "rival": ids_r},
        "cuentas": {"test": cta_t, "rival": cta_r},
        "kia_total": round(total, 6),
        "kia_por_minuto_red": round(total / minutos, 4),
        "test": {
            "kia": round(kia_t, 6),
            "kia_por_minuto": round(kia_t / minutos, 4),
            "parte_pct": round(100.0 * kia_t / total, 1) if total else None,
            "tareas": sum((fin.get(c) or {}).get("tareas", 0) for c in cta_t),
        },
        "rival": {
            "kia": round(kia_r, 6),
            "kia_por_minuto": round(kia_r / minutos, 4),
            "parte_pct": round(100.0 * kia_r / total, 1) if total else None,
            "tareas": sum((fin.get(c) or {}).get("tareas", 0) for c in cta_r),
        },
        "gpu_uso_medio_pct": {k: (round(sum(v) / len(v), 1) if v else None)
                              for k, v in serie.items()},
        "precio_gflop_final": (muestras[-1].get("core") or {}).get("precio_gflop"),
        "gflop_min_final": (muestras[-1].get("core") or {}).get("gflop_min"),
        "muestras": len(muestras),
        "serie_completa": [{k: v for k, v in m.items() if k != "gpus"} for m in muestras],
        "gpus_final": gpus,
    }
    prod_despues = None
    try:
        prod_despues = H.get_json("http://127.0.0.1:8300", "/stats")
    except Exception:
        pass
    if prod_antes and prod_despues:
        resumen["red_de_verdad"] = {
            "tareas_ok_antes": prod_antes.get("tareas_ok"),
            "tareas_ok_despues": prod_despues.get("tareas_ok"),
            "delta": (int(prod_despues.get("tareas_ok") or 0)
                      - int(prod_antes.get("tareas_ok") or 0)),
        }
    (base / "resumen.json").write_text(json.dumps(resumen, indent=2, ensure_ascii=False),
                                       encoding="utf-8")

    print("\n" + "=" * 66)
    print(f"COMPETENCIA · test {a.ranuras} ranura(s) · {minutos:.1f} min · {motivo}")
    print(f"  test   {resumen['test']['kia']:.4f} KIA -> {resumen['test']['kia_por_minuto']:.4f} KIA/min"
          f" ({resumen['test']['parte_pct']} %) · {resumen['test']['tareas']} tareas")
    print(f"  rival  {resumen['rival']['kia']:.4f} KIA -> {resumen['rival']['kia_por_minuto']:.4f} KIA/min"
          f" ({resumen['rival']['parte_pct']} %) · {resumen['rival']['tareas']} tareas")
    print(f"  red    {resumen['kia_por_minuto_red']:.4f} KIA/min (presupuesto ~1,31)")
    print(f"  GPU uso medio {resumen['gpu_uso_medio_pct']}")
    print("=" * 66)
    print(f"\n[comp] todo guardado en {base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
