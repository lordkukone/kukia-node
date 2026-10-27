#!/usr/bin/env python3
"""Copiar un ledger SQLite VIVO sin que la copia salga rota.

POR QUE EXISTE (medido el 25-10-2026)
  Las herramientas de prueba copiaban la base de produccion con
  `shutil.copy2`. Con el Core escribiendo (telemetria cada minuto), esa copia
  coge la base a mitad de una transaccion y sale MAL FORMADA: tres copias
  seguidas dieron 1 rota (`database disk image is malformed`), y la prueba que
  arrancaba un Core sobre la copia se moria al arrancar sin decir por que.

  `VACUUM INTO` lee la base en una sola pasada y escribe un fichero nuevo y
  coherente aunque el Core este escribiendo (2,8-3,2 s con 364 MB). Si por lo
  que sea no se puede, se cae a la API `backup()` y, en ultimo caso, a una
  copia a pelo (que es lo que habia antes).

USO
    from copia_ledger import copiar_ledger
    copiar_ledger(DB_REAL, tmp / "kukia.db")

  Si el destino ya existe, se borra antes (VACUUM INTO exige que no exista).
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path


def _coherente(ruta: Path) -> bool:
    """¿La copia abre y pasa una revision rapida?"""
    try:
        c = sqlite3.connect(f"file:{ruta.as_posix()}?mode=ro", uri=True, timeout=15)
        try:
            fila = c.execute("PRAGMA quick_check(3)").fetchone()
            return bool(fila) and fila[0] == "ok"
        finally:
            c.close()
    except Exception:
        return False


def copiar_ledger(origen: Path | str, destino: Path | str) -> str:
    """Copia coherente de una base SQLite que puede estar en uso.

    Devuelve el metodo que funciono: 'vacuum', 'backup' o 'copia' (a pelo).
    Lanza RuntimeError si el origen no existe o si la copia sale mal formada
    por los tres caminos (mejor fallar claro que empezar con una base rota).
    """
    origen = Path(origen)
    destino = Path(destino)
    if not origen.exists():
        raise RuntimeError(f"no existe el ledger de origen: {origen}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        destino.unlink()

    # 1) VACUUM INTO: una pasada de lectura, fichero nuevo y coherente.
    try:
        o = sqlite3.connect(f"file:{origen.as_posix()}?mode=ro", uri=True, timeout=30)
        try:
            o.execute(f"VACUUM INTO '{destino.as_posix()}'")
        finally:
            o.close()
        if _coherente(destino):
            return "vacuum"
    except Exception:
        pass
    if destino.exists():
        destino.unlink()

    # 2) API backup() de SQLite.
    try:
        o = sqlite3.connect(f"file:{origen.as_posix()}?mode=ro", uri=True, timeout=30)
        d = sqlite3.connect(destino)
        try:
            o.backup(d)
        finally:
            d.close()
            o.close()
        if _coherente(destino):
            return "backup"
    except Exception:
        pass
    if destino.exists():
        destino.unlink()

    # 3) Copia a pelo: puede salir rota, pero al menos se avisa.
    shutil.copy2(origen, destino)
    if _coherente(destino):
        return "copia"
    raise RuntimeError(f"la copia de {origen} sale mal formada (¿esta escribiendo "
                       f"otro proceso y el disco va justo?)")


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("uso: python tools/copia_ledger.py <origen.db> <destino.db>")
        raise SystemExit(2)
    metodo = copiar_ledger(sys.argv[1], sys.argv[2])
    print(f"copiado con '{metodo}' -> {sys.argv[2]} "
          f"({Path(sys.argv[2]).stat().st_size / 1e6:.1f} MB)")
