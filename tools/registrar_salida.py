#!/usr/bin/env python3
"""Lanza un hijo y guarda su salida en un log, rotandolo mientras corre.

POR QUE EXISTE (medido el 27-10-2026)
    `lanzar_core.cmd` arranca uvicorn con `>> logs\\core.log`. Con la
    redireccion de cmd, el fichero queda abierto de una forma en la que NINGUN
    otro proceso puede escribirlo: `open("r+b").truncate(0)` y `os.truncate`
    dan las dos `PermissionError [Errno 13]` con el Core vivo (comprobado dos
    veces, con el log de 209 MB). O sea: por ese camino, el log del Core NO se
    puede cortar desde fuera, por mucho que otro programa lo intente.

    Aqui el fichero lo tiene ESTE programa, asi que manda: lee la salida del
    hijo, la ensena por su propia consola (como se veia antes) y la escribe en
    el log. Cuando el log pasa del tope se queda con la cola en `.1`, corre las
    generaciones y empieza de cero. El hijo no se entera: no se le toca nada.

    Los hijos que lanza la guardia no necesitan esto: la guardia abre su log en
    modo `ab` (O_APPEND) y ese si se puede truncar desde fuera, que es lo que
    hace `tools/rotar_logs.py` en cada vuelta.

USO
    python tools/registrar_salida.py <log> -- <comando> [args...]
    python tools/registrar_salida.py logs\\core.log -- python -u -m uvicorn kukia_core:app ...

    Sale con el MISMO codigo que el hijo. Ctrl+C se le pasa al hijo y espera a
    que termine: no se queda ninguno colgando.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
sys.path.insert(0, str(AQUI))
try:
    import rotar_logs
except Exception:  # pragma: no cover
    rotar_logs = None


def main() -> int:
    ap = argparse.ArgumentParser(description="Guarda la salida de un hijo en un log con rotacion.")
    ap.add_argument("log", help="fichero de log (se anade a lo que ya hubiera)")
    ap.add_argument("comando", nargs=argparse.REMAINDER, help="-- y el comando a lanzar")
    args = ap.parse_args()

    comando = list(args.comando)
    if comando and comando[0] == "--":
        comando = comando[1:]
    if not comando:
        print("[salida] ERROR: falta el comando (usa: registrar_salida.py <log> -- <comando>)",
              file=sys.stderr)
        return 2

    destino = Path(args.log)
    if not destino.is_absolute():
        destino = RAIZ / destino
    destino.parent.mkdir(parents=True, exist_ok=True)

    tope = rotar_logs.MAX_BYTES if rotar_logs else 5 * 1024 * 1024
    # Cada cuanto se mira el tamano. Proporcional al tope: mirar en cada linea
    # seria un `tell()` por linea de uvicorn, y mirar cada 1 MB con un tope
    # pequeno (pruebas) no mira nunca. Con el tope de siempre (5 MB) se mira
    # cada 1 MB y el fichero se pasa como mucho 1 MB del tope.
    paso = max(4096, min(1048576, tope // 4))

    # El hijo hereda el entorno; su salida va a una tuberia y la escribe este
    # proceso. De la rotacion se encarga `rotar_logs.rota_ahora`, pero aqui el
    # fichero lo tiene SOLO este programa, asi que se cierra antes de rotar.
    salida = destino.open("ab", buffering=0)
    salida.write((f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} arranca: "
                  f"{' '.join(comando)} ---\n").encode("utf-8", "replace"))
    escritos = 0
    try:
        # cwd = el de quien nos llama, NO el de este fichero: el Core se lanza
        # desde `coordinator` (`uvicorn kukia_core:app` solo encuentra el modulo
        # desde ahi) y el minero desde `worker`.
        p = subprocess.Popen(comando, cwd=os.getcwd(), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    except Exception as e:
        salida.write(f"[salida] ERROR al lanzar: {type(e).__name__}: {e}\n".encode())
        salida.close()
        print(f"[salida] ERROR al lanzar {comando[0]}: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 127

    def apagar(senal=None, marco=None):
        try:
            p.terminate()
            p.wait(timeout=15)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

    try:
        for linea in iter(p.stdout.readline, b""):
            try:
                sys.stdout.buffer.write(linea)
                sys.stdout.buffer.flush()
            except Exception:
                pass
            try:
                salida.write(linea)
            except OSError:
                pass
            escritos += len(linea)
            if escritos >= paso and rotar_logs is not None:
                escritos = 0
                try:
                    if salida.tell() > tope:
                        salida.close()
                        n = rotar_logs.rota_ahora(destino, forzar=True)
                        salida = destino.open("ab", buffering=0)
                        print(f"[salida] log rotado: se sueltan "
                              f"{rotar_logs.mb(n)}; sigue en {destino.name}",
                              flush=True)
                except Exception as e:
                    print(f"[salida] aviso: no se pudo rotar el log "
                          f"({type(e).__name__}: {e})", file=sys.stderr, flush=True)
        codigo = p.wait()
    except KeyboardInterrupt:
        apagar()
        codigo = 130
        print("\n[salida] detenido por el usuario", flush=True)
    finally:
        try:
            salida.write((f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} termina "
                          f"(codigo {p.returncode if p.returncode is not None else '?'}) ---\n"
                          ).encode("utf-8", "replace"))
            salida.close()
        except Exception:
            pass
    return int(codigo or 0)


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
