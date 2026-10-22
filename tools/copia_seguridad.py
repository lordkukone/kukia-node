"""Copia de seguridad VERIFICADA de una o varias carpetas (22-10-2026).

Copia primero y comprueba despues: el SHA256 del original y el de la copia
tienen que coincidir fichero a fichero. Dentro del destino deja:

  MANIFIESTO.txt  que se copio, de donde, cuando y cuanto ocupa
  hashes.txt      sha256 + tamaño + ruta relativa de cada fichero copiado
  RESUMEN.txt     resultado: cuantos ficheros ok, cuantos fallos y por que

Uso:
  python tools/copia_seguridad.py N:\\COPIA_SEGURIDAD_20261022 ^
      "C:\\Users\\kuki\\Desktop\\KUKIA" I:\\CEREBRO H:\\Xoxete

  python tools/copia_seguridad.py --verificar N:\\COPIA_SEGURIDAD_20261022

Reglas:
  - Nunca borra ni mueve nada del origen (solo lee).
  - Si un fichero ya existe en el destino con el mismo hash, no se recopia
    (se puede reanudar). Los .db de SQLite se recopian siempre: hashear una
    base que el Core esta escribiendo no demuestra nada.
  - Las bases SQLite abiertas se copian con la API backup de sqlite, que da
    una copia consistente sin parar el Core. Van marcadas `db:ok` en
    hashes.txt y se verifican con PRAGMA integrity_check.
  - Si el espacio no da, se para ANTES de llenar el disco.
  - Un fichero que el sistema reescribe mientras se copia (logs del minero)
    no da hash estable: se reintenta, se queda la ultima copia y se lista
    como "en movimiento" en RESUMEN.txt. No cuenta como fallo.
  - Sale con codigo 0 solo si no hubo ni un fallo.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

BLOQUE = 1024 * 1024  # 1 MiB: leer en trozos grandes es mucho mas rapido
LINEA_TIEMPO = 2.0     # cada cuanto imprime progreso
SUFIJOS_DB = (".db", ".sqlite", ".sqlite3")
# Compañeros de una base abierta (-wal, -shm, -journal): aparecen y desaparecen
# solos. No se copian: la base se copia entera y coherente con VACUUM INTO, y
# un -wal suelto al lado de una copia asi sobra (y podria confundir a sqlite).
SUFIJOS_TEMPORALES = ("-wal", "-shm", "-journal")
MARGEN_ESPACIO = 1.05  # colchon sobre la estimacion de lo que falta
RESERVA_MINIMA = 200 * 1024 * 1024  # nunca dejar el disco a cero


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        while True:
            trozo = f.read(BLOQUE)
            if not trozo:
                break
            h.update(trozo)
    return h.hexdigest()


def tam_arbol(base: Path) -> tuple[int, int]:
    """(ficheros, bytes) sin seguir enlaces ni entrar en lo ilegible."""
    n = 0
    total = 0
    for raiz, _dirs, ficheros in os.walk(base):
        for nombre in ficheros:
            try:
                total += (Path(raiz) / nombre).stat().st_size
                n += 1
            except OSError:
                pass
    return n, total


def nombre_destino(destino: Path, origen: Path, usados: set) -> Path:
    base = origen.name or origen.drive.replace(":", "") or "copia"
    nombre = base
    i = 2
    while nombre.lower() in usados:
        nombre = f"{base}_{i}"
        i += 1
    usados.add(nombre.lower())
    return destino / nombre


def es_db(ruta: Path) -> bool:
    return ruta.suffix.lower() in SUFIJOS_DB


def db_abierta(ruta: Path, reciente: float = 300.0) -> bool:
    """¿Hay señales de que alguien esta escribiendo en esa base?

    SQLite deja un -wal o un -journal al lado mientras hay una transaccion
    viva. Si esta, no se copia a pelo: se copia con la API backup.

    Un .db que se toco hace menos de `reciente` segundos se trata igual de
    vivo: el Core mete una tarea cada pocos milisegundos, sin dejar -wal, y
    copiarlo a pelo es tirar 240 MB de lectura para acabar repitiendolo.
    """
    for sufijo in ("-wal", "-journal"):
        try:
            if Path(str(ruta) + sufijo).exists():
                return True
        except OSError:
            pass
    try:
        return (time.time() - ruta.stat().st_mtime) < reciente
    except OSError:
        return True


def copia_sqlite(src: Path, out: Path) -> tuple[bool, str]:
    """Copia consistente de una base SQLite, aunque otro proceso escriba.

    (ok, nota). Usa VACUUM INTO, que lee la base en una sola pasada y escribe
    el fichero nuevo: con el Core metiendo una tarea cada pocos milisegundos,
    la API backup() se reinicia cada vez que detecta un cambio y puede no
    acabar nunca (probado el 22-10-2026: 12 min sin terminar). VACUUM INTO
    tardo 14 s con el mismo ledger vivo. Si no estuviera disponible, se cae a
    backup() como antes.

    Se copia a un temporal, se comprueba la integridad y solo entonces se pone
    en su sitio: si algo falla, la copia buena anterior no se toca.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".copiando")
    basura = [tmp, Path(str(tmp) + "-wal"), Path(str(tmp) + "-shm"),
              Path(str(tmp) + "-journal")]

    def limpiar():
        for p in basura:
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass

    def con_origen():
        o = sqlite3.connect(str(src), timeout=30)
        o.execute("PRAGMA busy_timeout=30000")
        return o

    nota = ""
    try:
        limpiar()
        integridad = ""
        for intento in (1, 2, 3):
            try:
                o = con_origen()
                try:
                    # VACUUM INTO no admite parametros: la ruta va entre
                    # comillas simples, con las comillas del nombre escapadas.
                    destino_sql = tmp.as_posix().replace("'", "''")
                    o.execute(f"VACUUM INTO '{destino_sql}'")
                finally:
                    o.close()
                d = sqlite3.connect(str(tmp), timeout=15)
                try:
                    integridad = str(d.execute("PRAGMA integrity_check").fetchone()[0])
                finally:
                    d.close()
                break
            except Exception as e:
                nota = f"{type(e).__name__}: {e}"
                limpiar()
                if intento == 3:
                    # Ultimo recurso: la API backup de siempre.
                    o = con_origen()
                    try:
                        d = sqlite3.connect(str(tmp), timeout=15)
                        try:
                            o.backup(d)
                            integridad = str(
                                d.execute("PRAGMA integrity_check").fetchone()[0])
                        finally:
                            d.close()
                    finally:
                        o.close()
                else:
                    time.sleep(2.0)
        if integridad.lower() != "ok":
            limpiar()
            return False, f"integrity_check = {integridad or nota}"
        os.replace(tmp, out)
        return True, ""
    except Exception as e:
        limpiar()
        return False, f"{type(e).__name__}: {e}"
    finally:
        limpiar()


def integridad_db(ruta: Path) -> str:
    """'ok' o el motivo. Cadena vacia si no se pudo ni abrir."""
    try:
        con = sqlite3.connect(str(ruta), timeout=15)
        try:
            return str(con.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            con.close()
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def estima_pendiente(planes) -> int:
    """Bytes que faltan por copiar, a ojo: lo que no esta o cambia de tamaño.

    Es una estimacion: un fichero del mismo tamaño pero distinto contenido
    tambien se copiara. Por eso luego se comprueba el disco fichero a fichero.
    """
    total = 0
    for origen, dst in planes:
        for raiz, _dirs, ficheros in os.walk(origen):
            for nombre in ficheros:
                src = Path(raiz) / nombre
                try:
                    tam = src.stat().st_size
                except OSError:
                    continue
                out = dst / src.relative_to(origen)
                try:
                    if out.exists() and out.stat().st_size == tam and not es_db(src):
                        continue
                except OSError:
                    pass
                total += tam
    return total


def verificar(destino: Path) -> int:
    """Recomprueba un destino ya copiado contra su hashes.txt."""
    lista = destino / "hashes.txt"
    if not lista.exists():
        print(f"ERROR: falta {lista}")
        return 2
    buenos = malos = faltan = 0
    with lista.open(encoding="utf-8") as f:
        for linea in f:
            partes = linea.rstrip("\n").split("  ", 2)
            if len(partes) != 3:
                continue
            esperado, _tam, rel = partes
            p = destino / rel
            if not p.exists():
                faltan += 1
                print(f"  FALTA: {rel}")
                continue
            # Las bases SQLite vivas se marcan `db:ok`: su hash cambia con cada
            # tarea del Core, asi que lo que se comprueba es la integridad.
            if esperado.startswith("db:"):
                estado = integridad_db(p)
                if estado.lower() == "ok":
                    buenos += 1
                else:
                    malos += 1
                    print(f"  DB MAL: {rel} -> {estado}")
                continue
            if sha256(p) == esperado:
                buenos += 1
            else:
                malos += 1
                print(f"  DISTINTO: {rel}")
    print(f"verificados {buenos}, distintos {malos}, que faltan {faltan}")
    return 0 if (malos == 0 and faltan == 0) else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--verificar":
        if len(args) != 2:
            print(__doc__)
            return 2
        return verificar(Path(args[1]))
    if len(args) < 2:
        print(__doc__)
        return 2

    destino = Path(args[0])
    origenes = [Path(a) for a in args[1:]]

    for o in origenes:
        if not o.exists():
            print(f"ERROR: no existe el origen {o}")
            return 2
    try:
        destino.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"ERROR: no puedo crear el destino {destino}: {e}")
        return 2

    # Espacio: si no cabe lo que falta, no se empieza.
    print("midiendo origenes...", flush=True)
    planes = []
    total_bytes = 0
    usados: set = set()
    for o in origenes:
        n, b = tam_arbol(o)
        total_bytes += b
        planes.append((o, nombre_destino(destino, o, usados)))
        print(f"  {o}  {n} ficheros  {b / 1024 / 1024:.1f} MB", flush=True)

    print("mirando que falta en el destino...", flush=True)
    pendiente = estima_pendiente(planes)
    libre = shutil.disk_usage(destino).free
    print(f"destino {destino}: libre {libre / 1024 / 1024:.1f} MB, "
          f"total origen {total_bytes / 1024 / 1024:.1f} MB, "
          f"pendiente ~{pendiente / 1024 / 1024:.1f} MB", flush=True)
    if pendiente * MARGEN_ESPACIO + RESERVA_MINIMA > libre:
        print("ERROR: no hay espacio suficiente para lo que falta. No se copia nada.")
        return 2

    copiados = 0
    saltados = 0
    omitidos = 0     # temporales de sqlite, no se copian a proposito
    desaparecidos = 0  # se fueron entre el listado y la copia: no es un fallo
    en_movimiento: list[str] = []  # se reescribian mientras se copiaban
    fallos: list[str] = []
    hashes: list[tuple[str, int, str]] = []
    t0 = time.time()
    ultimo_aviso = 0.0

    for origen, dst in planes:
        print(f"\n=== {origen}  ->  {dst} ===", flush=True)
        for raiz, _dirs, ficheros in os.walk(origen):
            for nombre in ficheros:
                src = Path(raiz) / nombre
                rel = src.relative_to(origen)
                out = dst / rel
                rel_dest = f"{dst.name}/{rel}"
                if str(src).lower().endswith(SUFIJOS_TEMPORALES):
                    omitidos += 1
                    continue
                try:
                    if es_db(src):
                        # Base de datos: o esta quieta y vale el hash, o esta
                        # viva y hay que copiarla con la API backup.
                        if (out.exists() and not db_abierta(src)
                                and out.stat().st_size == src.stat().st_size):
                            h_src = sha256(src)
                            if sha256(out) == h_src:
                                saltados += 1
                                hashes.append((h_src, src.stat().st_size, rel_dest))
                                continue
                        if not db_abierta(src):
                            # A un temporal: si el hash no cuadra, la copia
                            # buena que ya habia en el destino no se toca.
                            out.parent.mkdir(parents=True, exist_ok=True)
                            tmp = out.with_name(out.name + ".copiando")
                            try:
                                shutil.copy2(src, tmp)
                                h_src = sha256(src)
                                if h_src == sha256(tmp):
                                    os.replace(tmp, out)
                                    hashes.append((h_src, src.stat().st_size, rel_dest))
                                    copiados += 1
                                    continue  # quieta y verificada por hash
                            finally:
                                try:
                                    if tmp.exists():
                                        tmp.unlink()
                                except OSError:
                                    pass
                        ok, nota = copia_sqlite(src, out)
                        if ok:
                            hashes.append(("db:ok", out.stat().st_size, rel_dest))
                            copiados += 1
                        else:
                            fallos.append(f"SQLITE: {src}: {nota}")
                        continue

                    # Si ya esta copiado e identico, no se recopia (reanudable).
                    if out.exists() and out.stat().st_size == src.stat().st_size:
                        h_src = sha256(src)
                        if sha256(out) == h_src:
                            saltados += 1
                            hashes.append((h_src, src.stat().st_size, rel_dest))
                            continue
                    tam = src.stat().st_size
                    if shutil.disk_usage(destino).free < tam + RESERVA_MINIMA:
                        print("ERROR: me quedo sin espacio. Paro aqui.", flush=True)
                        fallos.append(f"SIN ESPACIO al copiar {src}")
                        return 2
                    out.parent.mkdir(parents=True, exist_ok=True)
                    # Un fichero que el sistema reescribe sin parar (minero.log)
                    # no puede dar un hash estable. Se reintenta un par de veces
                    # y, si sigue cambiando, se queda la ultima copia y se avisa:
                    # un log que crece no es una copia rota.
                    igual = False
                    for _intento in (1, 2, 3):
                        shutil.copy2(src, out)
                        h_src = sha256(src)
                        if h_src == sha256(out):
                            hashes.append((h_src, src.stat().st_size, rel_dest))
                            copiados += 1
                            igual = True
                            break
                        time.sleep(0.5)
                    if not igual:
                        en_movimiento.append(str(src))
                except FileNotFoundError:
                    # Un fichero que se borra mientras se copia (temporales de
                    # programas, logs rotados) no es un fallo de la copia.
                    desaparecidos += 1
                except Exception as e:
                    fallos.append(f"{src}: {type(e).__name__}: {e}")
                ahora = time.time()
                if ahora - ultimo_aviso > LINEA_TIEMPO:
                    ultimo_aviso = ahora
                    print(f"  ... {copiados} copiados, {saltados} ya estaban, "
                          f"{len(fallos)} fallos", flush=True)

    seg = time.time() - t0

    with (destino / "hashes.txt").open("w", encoding="utf-8") as f:
        for h, t, r in hashes:
            f.write(f"{h}  {t}  {r}\n")

    ok_bytes = sum(t for _h, t, _r in hashes)
    with (destino / "MANIFIESTO.txt").open("w", encoding="utf-8") as f:
        f.write(f"Copia de seguridad KUKIA - {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        f.write(f"Destino: {destino}\n\nOrigenes:\n")
        for origen, dst in planes:
            n, b = tam_arbol(origen)
            f.write(f"  {origen}  ->  {dst}   ({n} ficheros, {b / 1024 / 1024:.1f} MB)\n")
        f.write(f"\nTotal origen:   {total_bytes / 1024 / 1024:.1f} MB\n")
        f.write(f"Total copiado:  {ok_bytes / 1024 / 1024:.1f} MB en {len(hashes)} ficheros\n")
        f.write(f"Tiempo: {seg:.1f} s\n\n"
                "Verificar en cualquier momento con:\n"
                "  python tools/copia_seguridad.py --verificar <destino>\n")

    with (destino / "RESUMEN.txt").open("w", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}\n")
        f.write(f"ficheros copiados ahora: {copiados}\n")
        f.write(f"ya estaban identicos:    {saltados}\n")
        f.write(f"total verificado:        {len(hashes)}\n")
        f.write(f"temporales sqlite omitidos: {omitidos}\n")
        f.write(f"desaparecidos al copiar: {desaparecidos}\n")
        f.write(f"cambiaban mientras se copiaban: {len(en_movimiento)}\n")
        for linea in en_movimiento[:50]:
            f.write(f"  vivo: {linea}\n")
        f.write(f"fallos:                  {len(fallos)}\n")
        for linea in fallos[:200]:
            f.write(f"  {linea}\n")

    print(f"\nfin: {copiados} copiados, {saltados} ya estaban, "
          f"{omitidos} temporales omitidos, {desaparecidos} desaparecieron, "
          f"{len(en_movimiento)} en movimiento, "
          f"{len(fallos)} fallos, {seg:.1f} s", flush=True)
    if fallos:
        print("FALLOS (primeros 10):")
        for linea in fallos[:10]:
            print("  " + linea)
        return 1
    print("TODO VERIFICADO: cada fichero copiado coincide con el original.")
    return 0


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
