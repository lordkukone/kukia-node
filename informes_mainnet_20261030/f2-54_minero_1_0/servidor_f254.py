"""Core aislado para las pruebas de F2-53 (base sintetica, puerto aparte).

Copia del Core operativo (sin tocar nada suyo) con la base en `datos/`. Aborta si
la base cae dentro de `coordinator/ledger`. Genera tareas para que el worker
tenga trabajo real y deja las firmas en `dual`.

    python servidor_f253.py [--puerto 8405] [--sin-tareas]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn

AQUI = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--puerto", type=int, default=8405)
    ap.add_argument("--sin-tareas", action="store_true",
                    help="Core sin generador: prueba de cola vacia")
    a = ap.parse_args()

    core = (AQUI / "base" / "coordinator").resolve()
    datos = (AQUI / "datos").resolve()
    datos.mkdir(parents=True, exist_ok=True)
    db = (datos / "kukia.db").resolve()
    if "coordinator" in db.parts and "ledger" in db.parts:
        print(f"[f2-53] ABORTADO: {db} apunta al ledger operativo")
        return 3

    os.environ["KUKIA_DB"] = str(db)
    os.environ["KUKIA_SELLOS_FILE"] = str(datos / "sellos.jsonl")
    os.environ["KUKIA_SELLO_KEY"] = str(core / "sello_key.txt")
    os.environ["KUKIA_ADMIN_TOKEN"] = str(core / "admin_token.txt")
    os.environ["KUKIA_ARCHIVAR_SEG"] = "86400"
    os.environ["KUKIA_SELLO_SEG"] = "86400"
    os.environ["KUKIA_FIRMA_RESULTADO"] = "dual"
    os.environ["KUKIA_FIRMA_REPLICA"] = "dual"
    os.environ["KUKIA_REGISTRO_CUPO_HORA"] = "0"
    os.environ["KUKIA_REGISTRO_CUPO_DIA"] = "0"
    os.environ["KUKIA_MAX_TAREAS_NODO"] = "8"
    os.environ["KUKIA_MAX_TAREAS_NODO_NUEVO"] = "8"
    if a.sin_tareas:
        os.environ["KUKIA_AUTO_COLA"] = "0"
    else:
        os.environ["KUKIA_AUTO_COLA"] = "1"
        os.environ["KUKIA_AUTO_SEG"] = "5"
        os.environ["KUKIA_AUTO_TAREAS"] = "6"
        os.environ["KUKIA_AUTO_LOTE"] = "6"

    sys.path.insert(0, str(core))
    os.chdir(core)
    print(f"[f2-53] core={core} db={db} puerto={a.puerto} "
          f"tareas={'no' if a.sin_tareas else 'si'}", flush=True)
    uvicorn.run("kukia_core:app", host="127.0.0.1", port=a.puerto,
                log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
