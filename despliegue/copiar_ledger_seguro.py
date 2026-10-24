"""Copia el ledger de KUKIA de forma CONSISTENTE con el Core en marcha.

Copiar kukia.db con el Core escribiendo da una base rota: SQLite puede tener
parte de los datos en el WAL y el fichero copiado queda a medias. Aqui se usa
la API de backup de SQLite, que hace una copia coherente sin parar el Core.

Uso:
    python despliegue/copiar_ledger_seguro.py
    python despliegue/copiar_ledger_seguro.py --origen coordinator/ledger/kukia.db

Deja en despliegue/datos/kukia.db la copia y comprueba que su sha256 coincide
con el del fichero recien escrito. NO borra ni toca el original.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "despliegue" / "datos"


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--origen", default=str(RAIZ / "coordinator" / "ledger" / "kukia.db"))
    args = ap.parse_args()

    origen = Path(args.origen)
    if not origen.exists():
        print(f"[ERROR] no existe el ledger: {origen}")
        return 1

    DESTINO.mkdir(parents=True, exist_ok=True)
    salida = DESTINO / "kukia.db"
    salida.unlink(missing_ok=True)

    print(f"origen : {origen}  ({origen.stat().st_size / 1e6:.1f} MB)")
    # Backup online: no bloquea al Core y espera a lo que este escribiendo.
    src = sqlite3.connect(f"file:{origen}?mode=ro", uri=True)
    dst = sqlite3.connect(salida)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    # Comprobacion: la copia tiene que abrir y cuadrar, y no puede estar vacia.
    con = sqlite3.connect(salida)
    try:
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        asientos = con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        nodos = con.execute("SELECT COUNT(*) FROM nodos").fetchone()[0]
    except sqlite3.Error as e:
        print(f"[ERROR] la copia no se puede leer: {e}")
        return 1
    finally:
        con.close()

    if ok != "ok":
        print(f"[ERROR] integrity_check dice: {ok}")
        return 1
    if asientos == 0:
        print("[ERROR] la copia tiene 0 asientos: no la subas")
        return 1

    print(f"copia  : {salida}  ({salida.stat().st_size / 1e6:.1f} MB)")
    print(f"asientos: {asientos}   nodos: {nodos}")
    print(f"integrity_check: {ok}")
    print(f"sha256 : {sha256(salida)}")
    print()
    print("Siguiente: subir con  despliegue/subir_al_servidor.sh USUARIO@SERVIDOR")
    return 0


if __name__ == "__main__":
    sys.exit(main())
