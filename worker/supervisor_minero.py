"""Supervisor del minero KUKIAMINER V1.0 BETA (rama, sin commit).
Relanza el worker si termina inesperadamente, con limite de reintentos y log.
NO crea auto-inicio en Windows (solo documentado). Respeta el candado anti-doble.
Uso: python supervisor_minero.py [--billetera NOMBRE] [--max-reintentos 5]
"""
import subprocess
import sys
import time
from pathlib import Path

AQUI = Path(__file__).parent
LOG = AQUI / "minero_supervisor.log"
MAX_REINTENTOS = 5
VENTANA_SEG = 3600


def log(msg):
    linea = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(linea, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except Exception:
        pass


def main(argv=None):
    args = list(argv or sys.argv[1:])
    bil = ""
    if "--billetera" in args:
        i = args.index("--billetera")
        bil = args[i + 1] if i + 1 < len(args) else ""
    max_r = MAX_REINTENTOS
    if "--max-reintentos" in args:
        try:
            max_r = int(args[args.index("--max-reintentos") + 1])
        except Exception:
            pass
    cmd = [sys.executable, str(AQUI / "kukia_worker.py"), "minero_kuki"]
    if bil:
        cmd += ["--billetera", bil]
    intentos = []
    while True:
        log("SUPERVISOR arrancando minero")
        p = subprocess.Popen(cmd, cwd=str(AQUI))
        rc = p.wait()
        log(f"SUPERVISOR minero terminado rc={rc}")
        ahora = time.time()
        intentos = [t for t in intentos if ahora - t < VENTANA_SEG] + [ahora]
        if len(intentos) > max_r:
            log("SUPERVISOR limite de reintentos, no relanzo mas (revisar)")
            return 1
        espera = min(300, 10 * len(intentos))
        log(f"SUPERVISOR relanzo en {espera}s (intento {len(intentos)}/{max_r})")
        time.sleep(espera)


if __name__ == "__main__":
    sys.exit(main())
