#!/usr/bin/env python3
"""Tunel publico al Core con ngrok y DOMINIO FIJO (plan gratis).

Por que existe: el tunel rapido de Cloudflare cambia de nombre cada vez que se
reinicia el PC (`algo.trycloudflare.com`), asi que la direccion del Core no
sirve de nada pegado en ningun sitio. Un tunel CON NOMBRE de Cloudflare lo
arregla pero exige un dominio propio (~10 EUR/ano). Ngrok da un dominio fijo
en su plan gratis (`xxxx.ngrok-free.app`): no es tuyo, pero no cambia.

Lo que hace este proceso:
  1. lee el dominio de `ngrok_dominio.txt` (una linea, sin https://)
  2. arranca `ngrok http 8300 --url https://<dominio>`
  3. espera a que la URL publica sirva /version y publica la direccion en
     docs/core_actual.json (reutiliza la publicacion del tunel de Cloudflare)
  4. vigila: si ngrok se cae o la URL deja de responder, lo relanza con espera
     creciente. Como la direccion es fija, el espejo no cambia nunca.

Uso:
  python tools/tunel_ngrok.py                 # supervisor (lo que usa la guardia)
  python tools/tunel_ngrok.py --estado        # que hay configurado y si responde
  python tools/tunel_ngrok.py --dominio x.ngrok-free.app   # sin escribir el fichero
  python tools/tunel_ngrok.py --no-publicar   # sin tocar docs/core_actual.json
  python tools/tunel_ngrok.py --una-vez       # un ciclo y salir (para probar)

Antes de usarlo, una sola vez:
  tools\\bin\\ngrok.exe config add-authtoken <tu token>   (gratis en ngrok.com)
  echo xxxx.ngrok-free.app > ngrok_dominio.txt
"""
import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

# Se reutiliza lo que ya esta probado en el tunel de Cloudflare: el cerrojo
# (un solo supervisor vivo), la escritura atomica de core_actual.json y la
# publicacion en el espejo. Duplicarlo seria tener dos versiones que se separan.
import tunel_estable as te  # noqa: E402  (va despues de sys.path a proposito)

RAIZ = te.RAIZ
NGROK = RAIZ / "tools" / "bin" / "ngrok.exe"
REGISTRO = RAIZ / "ngrok_dominio.txt"
LOCAL = "http://127.0.0.1:8300"
VIGILIA_SEG = float(os.environ.get("KUKIA_NGROK_VIGILIA", "30"))
FALLOS_SEGUIDOS = 3
ESPERA_MAX = 60


def log(msg: str) -> None:
    te.log(f"[ngrok] {msg}")


def leer_dominio(forzado: str = "") -> str:
    """Dominio configurado, sin esquema ni barra, en minusculas."""
    dom = (forzado or "").strip()
    if not dom and REGISTRO.exists():
        for linea in REGISTRO.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#"):
                dom = linea
                break
    dom = dom.replace("https://", "").replace("http://", "").strip().rstrip("/")
    return dom.lower()


def detectar_dominio() -> str:
    """Pregunta a ngrok cual es el dominio gratis de la cuenta (si hay token).

    El plan gratis asigna UN dominio automaticamente y el nombre no se elige, asi
    que pedirselo a Kuki para copiarlo a mano es un paso tonto: ngrok lo devuelve
    por su API con el mismo token. Si la API no lo dice (otra version, sin red),
    se sigue pidiendo a mano como antes.
    """
    import json
    try:
        r = subprocess.run([str(NGROK), "api", "reserved-domains", "list", "--json"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=45)
    except Exception:
        return ""
    if r.returncode != 0:
        return ""
    try:
        datos = json.loads(r.stdout or "{}")
    except Exception:
        return ""
    candidatos = datos.get("reserved_domains") or datos.get("domains") or []
    for d in candidatos:
        if not isinstance(d, dict):
            continue
        nombre = (d.get("domain") or d.get("hostname") or "").strip().lower()
        if nombre and (nombre.endswith(".ngrok-free.app") or nombre.endswith(".ngrok.app")
                       or nombre.endswith(".ngrok.dev") or nombre.endswith(".ngrok.io")):
            return nombre
    return ""


def _comprobar(dominio: str) -> bool:
    """GET https://<dominio>/version: la unica prueba de que el tunel sirve el Core."""
    try:
        with urllib.request.urlopen(f"https://{dominio}/version", timeout=15) as r:
            return r.status == 200
    except Exception:
        return False


def _arrancar(dominio: str) -> subprocess.Popen:
    cmd = [str(NGROK), "http", "8300", f"--url=https://{dominio}", "--log", "stdout"]
    log("arrancando: " + " ".join(cmd))
    return subprocess.Popen(cmd, cwd=str(RAIZ), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", bufsize=1)


def _motivo_de_muerte(proc: subprocess.Popen) -> str:
    """Ultimas lineas de ngrok: sirven para decir POR QUE se cayo (token, dominio)."""
    salida = ""
    try:
        if proc.stdout is not None:
            salida = proc.stdout.read() or ""
    except Exception:
        pass
    lineas = [l.strip() for l in salida.splitlines() if l.strip()]
    if "ERR_NGROK_4018" in salida:
        return ("ngrok no esta autenticado. Falta el token:\n"
                "        tools\\bin\\ngrok.exe config add-authtoken <tu token>")
    if "ERR_NGROK_3200" in salida or "not found" in salida.lower():
        return ("ngrok no reconoce ese dominio. Comprueba que en el panel de ngrok "
                "tienes reclamado el dominio y que ngrok_dominio.txt lo escribe igual.")
    return lineas[-1] if lineas else "se cerro sin decir nada"


def _publicar(dominio: str, no_publicar: bool) -> None:
    if no_publicar:
        log("sin publicar (--no-publicar): docs/core_actual.json no se toca")
        return
    te.escribe_json(f"https://{dominio}")
    log(f"direccion escrita en docs/core_actual.json -> https://{dominio}")
    try:
        if te.publica():
            log("espejo actualizado")
        else:
            log("AVISO: el espejo no se pudo actualizar (surge). La URL no cambia, "
                "asi que solo afecta a quien lea el espejo por primera vez.")
    except Exception as e:
        log(f"AVISO: publicacion fallida: {type(e).__name__}: {e}")


def estado() -> int:
    dom = leer_dominio()
    print()
    print("  TUNEL NGROK — estado")
    print("  " + "-" * 52)
    print(f"  binario ............. {'SI' if NGROK.exists() else 'NO FALTA'}  {NGROK}")
    print(f"  dominio configurado . {dom or 'ninguno (falta ngrok_dominio.txt)'}")
    cfg = Path(os.environ.get("LOCALAPPDATA", "")) / "ngrok" / "ngrok.yml"
    print(f"  token (ngrok.yml) ... {'SI' if cfg.exists() else 'NO (falta el authtoken)'}")
    publicado = ""
    try:
        import json
        d = json.loads((RAIZ / "docs" / "core_actual.json").read_text(encoding="utf-8"))
        publicado = d.get("core", "")
    except Exception:
        publicado = "(no se puede leer docs/core_actual.json)"
    print(f"  publicado en la web . {publicado or '(sin direccion)'}")
    if dom:
        print(f"  responde ahora ...... {'SI' if _comprobar(dom) else 'NO'}")
    print()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tunel publico al Core con ngrok y dominio fijo.")
    ap.add_argument("--dominio", default="", help="dominio de ngrok (si no, ngrok_dominio.txt)")
    ap.add_argument("--no-publicar", action="store_true", help="no tocar docs/core_actual.json")
    ap.add_argument("--estado", action="store_true", help="ver configuracion y si responde")
    ap.add_argument("--una-vez", action="store_true", help="un ciclo y salir (para probar)")
    args = ap.parse_args(argv)

    if args.estado:
        return estado()

    if not NGROK.exists():
        log(f"falta el binario: {NGROK}")
        log("descarga: https://ngrok.com/download (ngrok-v3-stable-windows-amd64.zip)")
        return 2

    dominio = leer_dominio(args.dominio)
    if not dominio:
        # Sin fichero: se intenta sacar el dominio de la propia cuenta (hace
        # falta el token ya puesto). Si no hay token, esto devuelve "" y se
        # explica lo que falta; nunca se inventa un nombre.
        dominio = detectar_dominio()
        if dominio:
            REGISTRO.write_text(dominio + "\n", encoding="utf-8")
            log(f"dominio de la cuenta detectado y guardado: {dominio}")
    if not dominio:
        log("no hay dominio configurado.")
        log("  1. cuenta gratis en https://dashboard.ngrok.com/signup")
        log("  2. tools\\bin\\ngrok.exe config add-authtoken <tu token>")
        log("  3. vuelve a arrancar: el dominio se detecta solo (o escribe")
        log("     TU-DOMINIO.ngrok-free.app en ngrok_dominio.txt)")
        return 2

    cerrojo = te.toma_cerrojo()
    if cerrojo is None:
        log(f"ya hay un supervisor del tunel vivo (puerto {te.CERROJO}): este no hace nada")
        return 3

    log(f"=== KUKIA: tunel ngrok con dominio fijo: https://{dominio} ===")
    espera = 5
    fallos = 0
    proc = None
    try:
        while True:
            if proc is None or proc.poll() is not None:
                if proc is not None:
                    log("ngrok se ha cerrado: " + _motivo_de_muerte(proc))
                proc = _arrancar(dominio)
                # esperar a que sirva (ngrok tarda unos segundos en levantar el tunel)
                listo = False
                for _ in range(30):
                    time.sleep(2)
                    if proc.poll() is not None:
                        break
                    if _comprobar(dominio):
                        listo = True
                        break
                if proc.poll() is not None:
                    # murio al arrancar (token, dominio mal escrito...): no insistir en bucle
                    log("no arranca: " + _motivo_de_muerte(proc))
                    log(f"reintento en {espera:.0f}s")
                    time.sleep(espera)
                    espera = min(espera * 2, ESPERA_MAX)
                    proc = None
                    if args.una_vez:
                        return 1
                    continue
                if listo:
                    espera = 5
                    fallos = 0
                    log(f"publicado y respondiendo en https://{dominio}")
                    _publicar(dominio, args.no_publicar)
                else:
                    log("ngrok esta arriba pero la URL aun no responde; sigo mirando")
                if args.una_vez:
                    return 0 if listo else 1
            time.sleep(VIGILIA_SEG)
            if _comprobar(dominio):
                fallos = 0
                continue
            fallos += 1
            log(f"la URL no responde ({fallos}/{FALLOS_SEGUIDOS})")
            if fallos >= FALLOS_SEGUIDOS:
                log("relanzo ngrok")
                try:
                    proc.terminate()
                    proc.wait(timeout=10)
                except Exception:
                    pass
                proc = None
                fallos = 0
    except KeyboardInterrupt:
        log("cerrando el tunel (el Core y el minero siguen en local)")
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                pass
        try:
            cerrojo.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
