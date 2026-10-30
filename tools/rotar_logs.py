#!/usr/bin/env python3
"""Rotacion de logs: que ninguno crezca sin limite (27-10-2026).

QUE PASABA
    logs/core.log llego a 219 MB y 1.348.000 lineas, casi todas
    `INFO: 127.0.0.1:xxxxx - "GET /tareas/pendiente HTTP/1.1" 200 OK`.
    logs/minero_visible.log, 71 MB. worker/minero.log, 30 MB. La guardia rota
    SU log (guardia.log, 5 MB) desde siempre, pero la SALIDA DE SUS HIJOS no
    pasaba por ahi: nadie la cortaba.

COMO SE ROTA, Y POR QUE ASI
    Los logs de los hijos los abre la guardia en modo "ab" (O_APPEND) y les
    pasa el descriptor; quien escribe a mano lo hace con `>> fichero`
    (tambien O_APPEND). Con O_APPEND cada escritura va al FINAL del fichero,
    nunca a un desplazamiento guardado. Eso permite rotar asi:

      1. se corre una generacion: `x.log.1` -> `x.log.2`, y se tira lo que
         pase de `COPIAS` generaciones;
      2. `x.log.1` pasa a ser una COPIA de la cola del fichero vivo (los
         ultimos MAX_COLA bytes, cortando en un salto de linea);
      3. el fichero vivo se TRUNCA a 0.

    El proceso que escribe no se entera: sigue escribiendo al final, ya vacio.
    Renombrar el fichero vivo NO vale en Windows: el hijo lo tiene abierto y
    Windows no deja moverlo. Truncar si: abrir en "r+b" y `truncate(0)` se
    permite aunque otro proceso lo tenga abierto para anadir.

    Si algun dia un escritor dejara de usar O_APPEND, el sintoma seria un
    fichero que vuelve a su tamano anterior lleno de ceros al principio. No
    pasa con los de aqui: todos anaden (`"a"`, `"ab"` o `>>`).

QUIEN LO LLAMA
    - tools/guardia.py, en cada vuelta (30 s): rota la salida de sus hijos.
    - tools/minero_en_ventana.py, antes de abrir logs/minero_visible.log.
    - tools/lanzar_core.cmd, antes de arrancar el Core.
    - A mano:  python tools/rotar_logs.py --estado   (que hay y cuanto ocupa)

LO QUE NO ROMPE
    worker/minero.log lo lee el publicador de metricas por desplazamiento
    (`publicar_metricas.py`, `_leer_log`) y ya contempla el caso "log rotado o
    truncado": reinicia el desplazamiento y sigue. Al truncar, el publicador
    pierde las muestras viejas, que es justo lo que se quiere.

    Ningun log se borra: se rotan generaciones (`x.log.1`, `x.log.2`) y la
    mas vieja se va. Lo que hay que guardar de verdad (ledger, sellos,
    incidencias, `disponibilidad_avisada.json`) no es un `.log` y no se toca.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
LOGS = RAIZ / "logs"


def _entero(nombre: str, defecto: int) -> int:
    try:
        return max(0, int(os.environ.get(nombre) or defecto))
    except Exception:
        return defecto


# Tamano a partir del cual se rota el fichero vivo.
MAX_BYTES = _entero("KUKIA_LOG_MAX_BYTES", 5 * 1024 * 1024)
# Cuanto de la cola se guarda en la generacion .1 (lo ultimo es lo que importa).
MAX_COLA = _entero("KUKIA_LOG_MAX_COLA", 8 * 1024 * 1024)
# Cuantas generaciones se conservan por log (.1 .. .N).
COPIAS = _entero("KUKIA_LOG_COPIAS", 2)


def tam(ruta: Path) -> int:
    try:
        return ruta.stat().st_size
    except OSError:
        return 0


def mb(n: int) -> str:
    return f"{n / 1048576:.1f} MB"


def vigilados() -> list:
    """Los logs que se vigilan: la salida de los hijos de la guardia y el
    minero (oculto y en ventana). Se descubre por nombre, no por lista fija:
    si manana se anade otro hijo, entra solo.
    """
    encontrados = []
    try:
        encontrados.extend(sorted(LOGS.glob("*.log")))
    except OSError:
        pass
    # F2-53 (M-12): `minero.log` ya NO se rota desde fuera. Lo escribe el propio
    # worker y lo rota el, con su cerrojo: truncar por fuera un fichero que otro
    # proceso tiene abierto es justo lo que lo llenaba de bytes NUL.
    for extra in ("minero_visible.log", "minero_relanzado.log"):
        p = RAIZ / "worker" / extra
        if p.exists():
            encontrados.append(p)
    # Sin duplicados y sin generaciones (.1, .2...): esas no se rotan, se corren.
    vistos = set()
    salida = []
    for p in encontrados:
        if p.suffix != ".log" or p in vistos:
            continue
        vistos.add(p)
        salida.append(p)
    return salida


def _cola(origen: Path, destino: Path, max_bytes: int) -> int:
    """Copia los ultimos `max_bytes` de `origen` en `destino`.

    Se empieza en el primer salto de linea que haya dentro de la ventana, para
    no dejar una linea cortada al principio del fichero.
    """
    total = tam(origen)
    if total <= 0:
        destino.write_bytes(b"")
        return 0
    desde = max(0, total - max_bytes)
    with origen.open("rb") as f:
        if desde:
            f.seek(desde - 1)
            if f.read(1) != b"\n":
                # Retroceder hasta el salto anterior, sin pasarse de la ventana.
                bloque = min(desde, 65536)
                f.seek(desde - bloque)
                datos = f.read(bloque)
                corte = datos.rfind(b"\n")
                desde = desde - bloque + corte + 1 if corte >= 0 else desde
        f.seek(desde)
        datos = f.read()
    cabecera = (f"--- rotado {time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"(cola de {len(datos)} bytes de {total}) ---\n").encode("utf-8")
    destino.write_bytes(cabecera + datos)
    return len(datos)


def _generaciones(ruta: Path, copias: int) -> None:
    """Corre x.log.1 -> x.log.2 ... y borra lo que pase de `copias`."""
    generaciones = []
    for g in ruta.parent.glob(ruta.name + ".*"):
        sufijo = g.name[len(ruta.name) + 1:]
        if sufijo.isdigit():
            generaciones.append((int(sufijo), g))
    if copias <= 0:
        for _, g in generaciones:
            _quitar(g)
        return
    for n, g in generaciones:
        if n >= copias:
            _quitar(g)
    for n in range(copias - 1, 0, -1):
        viejo = ruta.with_name(f"{ruta.name}.{n}")
        if viejo.exists():
            try:
                viejo.replace(ruta.with_name(f"{ruta.name}.{n + 1}"))
            except OSError:
                pass


def _quitar(ruta: Path) -> None:
    try:
        ruta.unlink()
    except OSError:
        pass


def rota_ahora(ruta: Path, max_bytes: int = MAX_BYTES, copias: int = COPIAS,
               forzar: bool = False) -> int:
    """Rota `ruta` si pasa de `max_bytes`.

    Devuelve los bytes que se sueltan, 0 si no habia nada que hacer y **-1 si no
    se pudo** (fichero abierto por otro proceso que no deja escribirlo). El -1
    existe porque distinguirlo importa: el 27-10-2026 el log del Core lanzado
    con `>>` en cmd daba `PermissionError` al intentar truncarlo, y un 0 mudo
    hacia creer que simplemente no hacia falta rotar.
    """
    antes = tam(ruta)
    if antes <= 0 or (antes <= max_bytes and not forzar):
        return 0
    if ruta.suffix != ".log":
        return 0
    try:
        _generaciones(ruta, copias)
        _cola(ruta, ruta.with_name(ruta.name + ".1"), MAX_COLA)
        with ruta.open("r+b") as f:
            f.truncate(0)
    except OSError as e:
        print(f"[rotar] AVISO: no se pudo rotar {ruta.name}: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        return -1
    despues = tam(ruta)
    if despues > 4096:
        # El escritor NO estaba anadiendo (ver la cabecera): se avisa y se deja
        # como estaba antes de tocarlo, para no perder nada.
        print(f"[rotar] AVISO: {ruta.name} sigue con {mb(despues)} tras truncarlo: "
              f"ese escritor no usa O_APPEND. Revisar antes de volver a rotarlo.",
              file=sys.stderr)
        return -1
    return antes - despues


def estado() -> int:
    print(f"tope {mb(MAX_BYTES)} por log | cola guardada {mb(MAX_COLA)} | "
          f"{COPIAS} generaciones (.1 .. .{COPIAS})")
    print(f"{'fichero':<46} {'tamano':>10}  {'estado':<10} generaciones")
    total = 0
    for p in vigilados():
        t = tam(p)
        total += t
        gens = []
        for g in sorted(p.parent.glob(p.name + ".*")):
            if g.name[len(p.name) + 1:].isdigit():
                gens.append(f"{g.name.rsplit('.', 1)[1]}:{mb(tam(g))}")
        total += sum(tam(p.parent / f"{p.name}.{n}")
                     for n in range(1, COPIAS + 1)
                     if (p.parent / f"{p.name}.{n}").exists())
        rel = str(p.relative_to(RAIZ)) if RAIZ in p.parents else str(p)
        marca = "ROTAR" if t > MAX_BYTES else "ok"
        print(f"{rel:<46} {mb(t):>10}  {marca:<10} {' '.join(gens) or '-'}")
    print(f"\ntotal vigilado (con generaciones): {mb(total)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Rota los logs que pasen del tope.")
    ap.add_argument("--estado", action="store_true",
                    help="ensena tamano y generaciones de cada log, y no toca nada")
    ap.add_argument("--ruta", help="rota SOLO ese fichero")
    ap.add_argument("--forzar", action="store_true",
                    help="rota aunque no pase del tope (para el arranque)")
    ap.add_argument("--silencioso", action="store_true",
                    help="sin salida (para llamarlo desde un .cmd)")
    args = ap.parse_args()

    if args.estado:
        return estado()

    if args.ruta:
        p = Path(args.ruta)
        soltado = rota_ahora(p, forzar=args.forzar)
        if not args.silencioso:
            if soltado > 0:
                print(f"{p}: rotado, se sueltan {mb(soltado)}")
            elif soltado == 0:
                print(f"{p}: nada que hacer")
            else:
                print(f"{p}: NO se pudo rotar (lo tiene abierto otro proceso sin "
                      f"permiso de escritura; ver tools/registrar_salida.py)")
        return 0 if soltado >= 0 else 1

    soltado = 0
    rotados = []
    fallos = []
    for p in vigilados():
        n = rota_ahora(p, forzar=args.forzar)
        if n > 0:
            soltado += n
            rotados.append(f"{p.name} ({mb(n)})")
        elif n < 0:
            fallos.append(p.name)
    if not args.silencioso:
        if rotados:
            print(f"rotados {len(rotados)}: " + ", ".join(rotados)
                  + f"  |  se sueltan {mb(soltado)}")
        else:
            print("ninguno pasaba del tope: no se toca nada")
        if fallos:
            print(f"no se pudieron rotar {len(fallos)} (abiertos por otro proceso "
                  f"que no deja escribirlos): " + ", ".join(fallos))
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
