#!/usr/bin/env python3
"""¿Se puede pasar el ledger a WAL sin romper las copias de seguridad?

Estudio pedido el 25-10-2026. NO toca produccion: trabaja sobre COPIAS en una
carpeta temporal. Mide, con el Core de verdad arrancado sobre esas copias:

  1. DELETE (lo de hoy): con otro programa leyendo el ledger, cuanto tarda y
     que contesta una transferencia.
  2. WAL: lo mismo. Y ademas:
     - que pasa si alguien copia SOLO el .db con el Core escribiendo,
     - que pasa si se copia bien (VACUUM INTO y la API backup()),
     - si un lector en mode=ro sigue funcionando,
  3. Compatibilidad: una copia de seguridad ANTIGUA (hecha en DELETE) sigue
     abriendo y verificando igual.

Uso:
    python tools/estudio_wal.py
    python tools/estudio_wal.py --dejar     (no borra la carpeta temporal)

Salida: 0 si todo lo que se afirma se pudo medir.
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
HERRAMIENTAS = Path(__file__).resolve().parent
sys.path.insert(0, str(HERRAMIENTAS))
sys.path.insert(0, str(RAIZ / "ledger"))

import pruebas_seguridad as PS  # noqa: E402
from copia_ledger import copiar_ledger  # noqa: E402
from pruebas_caos import (arrancar_vivo, clave_nueva, matar,  # noqa: E402
                          entorno_core, transferir)

DB_REAL = PS.DB_REAL
fallos: list[str] = []


def check(ok: bool, texto: str, detalle: str = "") -> None:
    print(("  [ OK ] " if ok else "  [FALLO] ") + texto
          + (f"  -> {detalle}" if detalle else ""), flush=True)
    if not ok:
        fallos.append(texto)


def modo(db: Path) -> str:
    c = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return c.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        c.close()


def asientos(db: Path) -> int:
    c = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=20)
    try:
        return int(c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0])
    finally:
        c.close()


def quick_check(db: Path) -> str:
    try:
        c = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=20)
        try:
            return str(c.execute("PRAGMA quick_check(3)").fetchone()[0])
        finally:
            c.close()
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def transferencia_cronometrada(base: str, cartera, destino: str) -> tuple[int, float, str]:
    _saldo, de, seed, pub, _f = cartera
    nonce = uuid.uuid4().hex
    t0 = time.time()
    r = transferir(base, de, destino, 1.0, nonce, seed, pub)
    return r.status_code, time.time() - t0, r.text[:100]


def con_lector_abierto(tmp: Path, fn):
    """Ejecuta fn() mientras OTRA conexion mantiene una lectura abierta."""
    otro = sqlite3.connect(str(tmp / "kukia.db"), timeout=5, isolation_level=None)
    otro.execute("BEGIN")
    otro.execute("SELECT COUNT(*) FROM ledger").fetchone()
    try:
        return fn()
    finally:
        try:
            otro.execute("ROLLBACK")
        finally:
            otro.close()


def prueba_modo(journal: str, tmp: Path, etiqueta: str) -> dict:
    """Arranca el Core sobre la copia en el modo pedido y mide."""
    db = tmp / "kukia.db"
    print(f"\n=== {etiqueta} (journal_mode={journal}) ===", flush=True)
    c = sqlite3.connect(str(db))
    try:
        puesto = c.execute(f"PRAGMA journal_mode={journal}").fetchone()[0]
        c.execute("PRAGMA wal_autocheckpoint=1000")
    finally:
        c.close()
    check(puesto.lower() == journal, f"la copia queda en {journal}", puesto)

    token = uuid.uuid4().hex
    (tmp / "admin_token.txt").write_text(token, encoding="utf-8")
    entorno = entorno_core(tmp, token)
    proc, log, puerto, base = arrancar_vivo(tmp, entorno)
    datos: dict = {"puerto": puerto}
    try:
        cartera = None
        for _ in range(4):                 # la lectura de claves a veces falla
            cartera = PS.elegir_cartera(base)
            if cartera:
                break
            time.sleep(2.0)
        if not cartera:
            n = len(list(PS.CARTERAS.glob("*.json")))
            check(False, "hay cartera con saldo para probar",
                  f"ninguna de {n} billeteras se pudo usar")
            return datos
        _a, _b, destino = clave_nueva()

        codigo, tardanza, cuerpo = transferencia_cronometrada(base, cartera, destino)
        check(codigo == 200, "transferencia normal con el ledger libre",
              f"{codigo} en {tardanza:.1f} s {cuerpo}")
        if codigo != 200:
            return datos                      # sin pagos no se puede medir nada mas

        # Tres pagos mas, para que haya datos frescos "en vuelo".
        for _ in range(3):
            transferencia_cronometrada(base, cartera, destino)
        vivos = asientos(db)
        check(vivos > 0, "el Core escribe en la copia", f"{vivos} asientos")

        # Con otro programa leyendo el ledger.
        codigo, tardanza, cuerpo = con_lector_abierto(
            tmp, lambda: transferencia_cronometrada(base, cartera, destino))
        datos["codigo_con_lector"] = codigo
        datos["segundos_con_lector"] = tardanza
        check(codigo in (200, 503), "con otro leyendo, contesta (no se cuelga)",
              f"{codigo} en {tardanza:.1f} s")
        if journal == "wal":
            check(codigo == 200 and tardanza < 5.0,
                  "en WAL, el lector NO bloquea el pago", f"{codigo} en {tardanza:.1f} s")
        else:
            check(tardanza > 1.0, "en DELETE, el lector SI frena el pago",
                  f"{codigo} en {tardanza:.1f} s")

        # Fuera de la lectura: copia a pelo del .db con el Core escribiendo.
        copia = tmp / "a_pelo.db"
        shutil.copy2(db, copia)
        mal = quick_check(copia)
        datos["copia_apelo_estado"] = mal
        try:
            leidos = asientos(copia)
        except Exception as e:
            leidos = -1
        datos["copia_apelo_asientos"] = (leidos, vivos)
        if journal == "wal":
            # En WAL lo ultimo vive en el -wal: copiar solo el .db SIEMPRE se
            # deja asientos fuera, y ademas sin avisar (quick_check dice "ok").
            check(leidos < vivos or mal != "ok",
                  "en WAL, copiar el .db a pelo pierde lo ultimo (o sale roto)",
                  f"quick_check={mal}, asientos copiados={leidos} / vivos={vivos}")
        else:
            # En DELETE la copia a pelo suele salir entera, pero puede salir
            # rota si pilla una transaccion a medias: medido el 25-10-2026,
            # 1 de cada 3 copias de la base viva salio mal formada.
            print(f"  (en DELETE la copia a pelo sale entera {leidos}/{vivos} "
                  f"aqui, pero 1 de cada 3 da 'malformed' con trafico fuerte)")

        # Copia BIEN hecha: VACUUM INTO.
        desde_vacuum = tmp / "vacuum.db"
        metodo = copiar_ledger(db, desde_vacuum)
        check(quick_check(desde_vacuum) == "ok", "VACUUM INTO saca copia sana",
              f"{metodo}, quick_check={quick_check(desde_vacuum)}")
        # No se exige igualdad exacta: el Core sigue escribiendo mientras se
        # copia, asi que la copia puede traer ALGUN asiento mas. Lo que no
        # puede es traer menos.
        check(asientos(desde_vacuum) >= vivos, "la copia lleva TODOS los asientos",
              f"{asientos(desde_vacuum)} / {vivos} (o mas, si el Core escribio entretanto)")
        datos["vacuum"] = (asientos(desde_vacuum), vivos)

        # Copia con la API backup() (lo que usa la tarea diaria de las 02:00).
        desde_backup = tmp / "backup_api.db"
        src = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=30)
        dst = sqlite3.connect(str(desde_backup))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        check(quick_check(desde_backup) == "ok" and asientos(desde_backup) >= vivos,
              "la API backup() tambien saca copia completa",
              f"{asientos(desde_backup)} / {vivos} asientos")
        datos["backup_api"] = (asientos(desde_backup), vivos)

        # Un lector en solo-lectura mientras el Core escribe.
        try:
            n = asientos(db)
            check(n > 0, "un lector en mode=ro puede leer con el Core vivo", f"{n} asientos")
        except Exception as e:
            check(False, "un lector en mode=ro puede leer con el Core vivo",
                  f"{type(e).__name__}: {e}")
        return datos
    finally:
        matar(proc)
        try:
            log.close()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dejar", action="store_true")
    args = ap.parse_args()

    if not DB_REAL.exists():
        print(f"ERROR: no existe {DB_REAL}", file=sys.stderr)
        return 2
    tmp_base = Path(tempfile.mkdtemp(prefix="kukia_wal_"))
    print(f"carpeta de trabajo: {tmp_base}")
    print(f"produccion ({DB_REAL.stat().st_size / 1e6:.1f} MB) NO se toca.")
    modo_real = modo(DB_REAL)
    print(f"journal_mode de produccion AHORA: {modo_real}")

    try:
        # --- 1. copia de produccion, tal cual (DELETE) ---
        # Se usa el copiado del arnes (base + sellos + clave): sin los sellos el
        # Core arranca con el ledger "alterado" y bloquea los pagos, y entonces
        # no se puede medir nada del bloqueo.
        tmp_del = tmp_base / "delete"
        tmp_del.mkdir()
        print("\ncopiando estado (ledger + sellos + clave)...", flush=True)
        PS.copiar_estado(tmp_del)
        datos_del = prueba_modo("delete", tmp_del, "LO DE HOY")

        # --- 2. la misma copia en WAL ---
        tmp_wal = tmp_base / "wal"
        tmp_wal.mkdir()
        PS.copiar_estado(tmp_wal)
        datos_wal = prueba_modo("wal", tmp_wal, "CON WAL")

        # --- 3. compatibilidad con las copias de seguridad que ya existen ---
        print("\n=== COPIAS ANTIGUAS (hechas en DELETE) ===")
        candidatas = sorted(glob.glob(str(RAIZ / "backups" / "*" / "kukia.db")))
        if not candidatas:
            check(False, "hay alguna copia de seguridad antigua que probar")
        else:
            vieja = Path(candidatas[-1])
            print(f"  probando: {vieja}")
            check(modo(vieja).lower() == "delete", "la copia antigua sigue en DELETE",
                  modo(vieja))
            check(quick_check(vieja) == "ok", "la copia antigua abre bien",
                  quick_check(vieja))
            # Se levanta un Core sobre ella: si WAL hubiera roto el formato, aqui
            # se veria (y ademas comprueba que la copia sirve para recuperar).
            tmp_vieja = tmp_base / "vieja"
            tmp_vieja.mkdir()
            shutil.copy2(vieja, tmp_vieja / "kukia.db")
            token = uuid.uuid4().hex
            (tmp_vieja / "admin_token.txt").write_text(token, encoding="utf-8")
            proc, log, puerto, base = arrancar_vivo(tmp_vieja, entorno_core(tmp_vieja, token))
            try:
                c = sqlite3.connect(f"file:{(tmp_vieja / 'kukia.db').as_posix()}?mode=ro",
                                    uri=True)
                try:
                    n = int(c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0])
                finally:
                    c.close()
                check(n > 0, "la copia antigua arranca como Core y sirve", f"{n} asientos")
            finally:
                matar(proc)
                try:
                    log.close()
                except Exception:
                    pass

        # --- resumen ---
        print("\n== RESUMEN MEDIDO ==")
        print(f"  produccion sigue en: {modo(DB_REAL)}")
        print(f"  DELETE: con otro leyendo -> {datos_del.get('codigo_con_lector')} en "
              f"{datos_del.get('segundos_con_lector', 0):.1f} s")
        print(f"  WAL   : con otro leyendo -> {datos_wal.get('codigo_con_lector')} en "
              f"{datos_wal.get('segundos_con_lector', 0):.1f} s")
        print(f"  WAL   : copia a pelo del .db -> quick_check="
              f"{datos_wal.get('copia_apelo_estado')}, asientos "
              f"{datos_wal.get('copia_apelo_asientos')}")
        print(f"  WAL   : VACUUM INTO -> {datos_wal.get('vacuum')} · "
              f"backup() -> {datos_wal.get('backup_api')} (copiados / vivos)")
        print(f"\n  {'TODO MEDIDO' if not fallos else str(len(fallos)) + ' FALLO(S)'}")
        return 0 if not fallos else 1
    finally:
        if not args.dejar:
            shutil.rmtree(tmp_base, ignore_errors=True)
        else:
            print(f"\ncarpeta conservada: {tmp_base}")


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
