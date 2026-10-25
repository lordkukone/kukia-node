#!/usr/bin/env python3
"""Regresion del hallazgo B1 (auditoria 25-10-2026): copia sin sellos NO es copia.

QUE HACIA ANTES
    `verificar_sellos` con CERO sellos devuelve ok=True (no hay nada que lo
    contradiga), y `tools/backup_ledger.py` lo traducia a "copia VERIFICADA".
    Una copia con `sellos.jsonl` vacio o sin el fichero pasaba como buena, y no
    demuestra nada del historial: cualquiera podria quedarse con una copia sin
    sellos creyendo que tiene una prueba.

QUE COMPRUEBA ESTA PRUEBA
    1. Una carpeta con base + clave + `sellos.jsonl` VACIO -> NO verificada, y
       el motivo se dice con palabras.
    2. Una carpeta SIN `sellos.jsonl` -> NO verificada.
    3. Una carpeta con los sellos de una base real -> verificada (para que el
       arreglo no se cargue el caso bueno).

NO TOCA NADA DE PRODUCCION
    Todo se hace en una carpeta temporal y sobre una base SQLite creada aqui.
    No lee el ledger real, no arranca el Core y no usa ningun puerto.

USO
    python tools/prueba_backup_sin_sellos.py     -> 0 si cuadra, 1 si no
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tools"))

from ledger import sellos as S   # noqa: E402

_fallos: list[str] = []


def check(cond, texto: str, detalle: str = "") -> None:
    if cond:
        print(f"  OK    {texto}" + (f"   [{detalle}]" if detalle else ""))
    else:
        print(f"  FALLO {texto}" + (f"   [{detalle}]" if detalle else ""))
        _fallos.append(texto)


def cargar_backup():
    """Importa tools/backup_ledger.py sin ejecutar su main()."""
    ruta = RAIZ / "tools" / "backup_ledger.py"
    spec = importlib.util.spec_from_file_location("backup_ledger_prueba", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def base_con_asientos(ruta: Path, n: int = 3) -> sqlite3.Connection:
    """Base minima con la tabla `ledger` y `meta` que espera el verificador."""
    con = sqlite3.connect(str(ruta))
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, tarea_id TEXT, "
                "nodo_id TEXT, kuki REAL, motivo TEXT, ts TEXT, hash_prev TEXT, hash TEXT)")
    con.execute("CREATE TABLE meta(clave TEXT PRIMARY KEY, valor REAL)")
    prev = ""
    for i in range(n):
        ts = time.ctime()
        base = f"t{i}|nodoA|1.0|trabajo verificado|{ts}|{prev}"
        h = hashlib.sha256(base.encode()).hexdigest()
        con.execute("INSERT INTO ledger(tarea_id,nodo_id,kuki,motivo,ts,hash_prev,hash) "
                    "VALUES(?,?,?,?,?,?,?)", (f"t{i}", "nodoA", 1.0,
                                              "trabajo verificado", ts, prev, h))
        prev = h
    con.commit()
    return con


def main() -> int:
    for f in (sys.stdout, sys.stderr):
        try:
            f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print("REGRESION B1: una copia sin sellos no se anuncia como verificada\n")
    bk = cargar_backup()
    tmp = Path(tempfile.mkdtemp(prefix="aud_backup_"))

    # --- 1: sellos.jsonl VACIO ------------------------------------------------
    c1 = tmp / "copia_vacia"
    c1.mkdir()
    clave = c1 / "sello_key.txt"
    S.crear_clave(clave)
    con = base_con_asientos(c1 / "kukia.db")
    con.close()
    (c1 / "sellos.jsonl").write_text("", encoding="utf-8")
    r1 = bk.verificar(c1, sin_clave=False)
    check(r1["ok"] is False, "copia con sellos.jsonl vacio -> NO verificada",
          f"sellos={r1['sellos']} problemas={r1['problemas'][:1]}")
    check(any("sello" in p.lower() for p in r1["problemas"]),
          "el motivo lo dice con palabras (no un False seco)",
          str(r1["problemas"][:1]))

    # --- 2: sin fichero de sellos --------------------------------------------
    c2 = tmp / "copia_sin_fichero"
    c2.mkdir()
    S.crear_clave(c2 / "sello_key.txt")
    con = base_con_asientos(c2 / "kukia.db")
    con.close()
    r2 = bk.verificar(c2, sin_clave=False)
    check(r2["ok"] is False, "copia sin sellos.jsonl -> NO verificada",
          f"sellos={r2['sellos']}")

    # --- 3: el caso bueno sigue valiendo -------------------------------------
    c3 = tmp / "copia_buena"
    c3.mkdir()
    clave3 = c3 / "sello_key.txt"
    S.crear_clave(clave3)
    priv3 = S.leer_clave(clave3)
    con = base_con_asientos(c3 / "kukia.db")
    # La API real del modulo: `nuevo_sello(con, clave, anterior)` encadena.
    sellos = []
    anterior = None
    for _ in range(3):
        anterior = S.nuevo_sello(con, priv3, anterior)
        sellos.append(anterior)
    con.commit()
    (c3 / "sellos.jsonl").write_text(
        "\n".join(json.dumps(x, sort_keys=True) for x in sellos) + "\n", encoding="utf-8")
    con.execute("INSERT OR REPLACE INTO meta(clave,valor) VALUES('sellos_n',?)",
                (float(len(sellos)),))
    con.commit()
    con.close()
    r3 = bk.verificar(c3, sin_clave=False)
    check(r3["ok"] is True and r3["sellos"] > 0,
          "copia con sus sellos -> SI verificada (el arreglo no rompe el caso bueno)",
          f"sellos={r3['sellos']} supply={r3['supply']}")
    print()

    print("RESUMEN")
    if _fallos:
        print(f"  {len(_fallos)} FALLO(S):")
        for t in _fallos:
            print(f"    - {t}")
        return 1
    print("  OK: una copia sin sellos ya no se puede vender como verificada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
