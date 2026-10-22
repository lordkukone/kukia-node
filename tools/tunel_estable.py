"""Tunel publico al Core, con direccion ESTABLE.

QUE HACE
    1. Comprueba que el Core responde en este PC (127.0.0.1:8300).
    2. Abre un tunel de Cloudflare hacia el Core y espera a que de verdad
       sirva (no solo a que exista la URL: pide /stats hasta que contesta).
    3. Escribe la direccion viva en `docs/core_actual.json` y la publica en
       el espejo (https://kukia-network.surge.sh/core_actual.json).
    4. Se queda vigilando: si el tunel se cae, lo relanza y republica.

POR QUE ASI
    El tunel rapido de Cloudflare es gratis y no pide cuenta, pero su nombre
    cambia en cada arranque. El zip que se descarga NO lleva ese nombre: lleva
    `auto https://kukia-network.surge.sh/core_actual.json` (ver
    tools/core_destino.py), que siempre apunta al que este vivo. Asi el
    paquete no caduca cuando el tunel cambia.

    Esto no es un dominio propio: es una direccion estable. Para una URL fija
    de verdad (https://kukia.tudominio.com) hace falta un dominio y un tunel
    con nombre de Cloudflare; el paso a paso esta en TUNEL_ESTABLE.md.

USO
    python tools/tunel_estable.py                  abrir tunel, publicar y vigilar
    python tools/tunel_estable.py --una-vez        un ciclo y sale (para probar)
    python tools/tunel_estable.py --solo-publicar --url https://x.trycloudflare.com
    python tools/tunel_estable.py --estado         que direccion hay publicada
    python tools/tunel_estable.py --no-publicar    tunel y JSON local, sin subir

    ARRANCAR_TUNEL.cmd es el atajo de doble clic.

SALIDA
    logs/tunel_estable.log    todo lo que pasa (con hora)
    docs/core_actual.json     la direccion viva, tal cual se publica
"""
import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
LOGS = RAIZ / "logs"
LOG = LOGS / "tunel_estable.log"
PUBLICO = RAIZ / "docs"
FICHERO = PUBLICO / "core_actual.json"
CLOUDFLARED = RAIZ / "tools" / "bin" / "cloudflared.exe"
DOMINIO = os.environ.get("KUKIA_WEB_DOMINIO", "kukia-network.surge.sh")
LOCAL = os.environ.get("KUKIA_CORE_LOCAL", "http://127.0.0.1:8300")
# FALLO REAL (22-10-2026): esta expresion aceptaba `https://api.trycloudflare.com`,
# que aparece en el log de cloudflared como destino de su API. El supervisor se
# creia que ese era su tunel, publicaba una direccion muerta en core_actual.json
# y la red se quedaba inaccesible desde fuera (Cloudflare: 530) sin que nada
# gritara. Los tuneles rapidos tienen forma de CUATRO palabras con guiones
# (p. ej. warner-tablets-wonderful-tba) y nunca empiezan por "api.".
URL_RE = re.compile(r"https://[a-z0-9]+(?:-[a-z0-9]+){3}\.trycloudflare\.com")
# Por si Cloudflare cambia la forma: se acepta cualquier nombre del dominio,
# pero NUNCA el host de la API.
URL_LAXA = re.compile(r"https://(?!api\.)[a-z0-9][a-z0-9-]*\.trycloudflare\.com")
# Cada cuanto se comprueba que el tunel sigue sirviendo.
VIGILIA_SEG = float(os.environ.get("KUKIA_TUNEL_VIGILIA", "45"))
# Cerrojo de instancia unica: dos supervisores abririan DOS tuneles y se
# pisarian el fichero de direccion (el minero acabaria apuntando al tunel
# que pierde la carrera). Se toma un puerto local; si esta ocupado, ya hay
# uno vivo.
CERROJO = int(os.environ.get("KUKIA_TUNEL_PUERTO", "8399"))


def log(msg: str) -> None:
    linea = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(linea, flush=True)
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except Exception:
        pass


def pide(url: str, tiempo: float = 10.0) -> tuple:
    """(codigo, cuerpo). codigo 0 si ni siquiera hubo respuesta."""
    pet = urllib.request.Request(url, headers={
        "User-Agent": "kukia-tunel/1.0", "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(pet, timeout=tiempo) as r:
            return r.status, r.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def toma_cerrojo():
    """Socket que hace de cerrojo (None si ya hay otro supervisor vivo)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", CERROJO))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def espera_core(seg: float = 60.0) -> bool:
    """El Core tiene que estar vivo ANTES de abrir el tunel."""
    fin = time.time() + seg
    while time.time() < fin:
        codigo, _ = pide(f"{LOCAL}/stats", 5)
        if codigo == 200:
            return True
        time.sleep(3)
    return False


def datos_core() -> dict:
    codigo, cuerpo = pide(f"{LOCAL}/stats", 8)
    if codigo != 200:
        return {}
    try:
        return json.loads(cuerpo)
    except Exception:
        return {}


def arranca_cloudflared() -> subprocess.Popen:
    exe = str(CLOUDFLARED) if CLOUDFLARED.exists() else (shutil.which("cloudflared") or "")
    if not exe:
        raise SystemExit(f"[ERROR] no encuentro cloudflared en {CLOUDFLARED} ni en el PATH")
    argv = [exe, "tunnel", "--url", LOCAL,
            "--http-host-header", "127.0.0.1", "--no-autoupdate"]
    extra = {}
    if os.name == "nt":
        extra["creationflags"] = 0x08000000   # CREATE_NO_WINDOW
    log(f"abriendo tunel: {LOCAL} -> Cloudflare")
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                            errors="replace", bufsize=1, **extra)


def lee_url(proc: subprocess.Popen, seg: float = 90.0) -> str:
    """Espera la URL en la salida de cloudflared (la imprime al conectar)."""
    cola = queue.Queue()

    def bomba():
        try:
            for linea in proc.stdout:
                cola.put(linea)
        except Exception:
            pass
        cola.put(None)

    threading.Thread(target=bomba, daemon=True).start()
    fin = time.time() + seg
    flojo = ""
    while time.time() < fin:
        try:
            linea = cola.get(timeout=1)
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue
        if linea is None:
            break
        m = URL_RE.search(linea)
        if m:
            return m.group(0)     # la buena: cuatro palabras con guiones
        if not flojo:
            m2 = URL_LAXA.search(linea)
            if m2:
                flojo = m2.group(0)   # se guarda por si al final no hay mejor
    return flojo


def espera_tunel(url: str, seg: float = 120.0) -> bool:
    """La URL existe antes de servir. Aqui se pide /stats por el tunel."""
    fin = time.time() + seg
    ultimo = 0
    while time.time() < fin:
        codigo, cuerpo = pide(f"{url}/stats", 12)
        if codigo == 200 and "supply_emitido" in cuerpo:
            return True
        ultimo = codigo
        time.sleep(4)
    log(f"el tunel no sirvio /stats a tiempo (ultimo codigo: {ultimo})")
    return False


def escribe_json(url: str) -> dict:
    """Deja docs/core_actual.json listo para publicar (escritura atomica)."""
    d = datos_core()
    datos = {
        "core": url,
        "actualizado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": d.get("version", ""),
        "nodos": d.get("nodos", 0),
        "tareas_ok": d.get("tareas_ok", 0),
        "core_local": LOCAL,
        "nota": ("Direccion viva del Core KUKIA. El minero la lee al arrancar; "
                 "si cambia, el paquete sigue valiendo."),
    }
    PUBLICO.mkdir(parents=True, exist_ok=True)
    tmp = FICHERO.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(FICHERO)
    return datos


def publica(destino: str = DOMINIO, seg: float = 120.0) -> bool:
    """Sube el espejo (docs) a surge y comprueba que la direccion llego."""
    surge = shutil.which("surge")
    cmd = ([surge, str(PUBLICO), destino] if surge
           else ["cmd", "/c", "surge", str(PUBLICO), destino])
    log(f"publicando en {destino} ...")
    try:
        # Sin text=True a proposito: surge (Node en Windows) mezcla UTF-8 y
        # cp850 en la misma salida y decodificar "como toque" revienta con
        # UnicodeDecodeError. Se leen bytes y se convierten a la brava.
        r = subprocess.run(cmd, cwd=str(RAIZ), capture_output=True,
                           timeout=300, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            salida = (r.stdout + r.stderr).decode("utf-8", "replace")
            log(f"surge fallo (codigo {r.returncode}): {salida[-400:]}")
            return False
    except Exception as e:
        log(f"no se pudo ejecutar surge: {type(e).__name__}: {e}")
        return False

    # No basta con que surge diga "Success": se comprueba por HTTP.
    esperado = json.loads(FICHERO.read_text(encoding="utf-8"))["core"]
    fin = time.time() + seg
    while time.time() < fin:
        codigo, cuerpo = pide(f"https://{destino}/core_actual.json?_kukia={int(time.time())}", 15)
        if codigo == 200:
            try:
                if json.loads(cuerpo).get("core") == esperado:
                    log(f"publicado y verificado: https://{destino}/core_actual.json -> {esperado}")
                    return True
            except Exception:
                pass
        time.sleep(5)
    log("el espejo no devolvio la direccion nueva a tiempo")
    return False


def abre_y_publica(no_publicar: bool = False) -> tuple:
    """Un ciclo completo: tunel arriba, verificado y publicado. (proc, url)."""
    if not espera_core():
        raise SystemExit("[ERROR] el Core no responde en este PC: arrancalo antes "
                         "(ARRANCAR_GUARDIA.cmd) o revisa el puerto 8300")
    proc = arranca_cloudflared()
    url = lee_url(proc)
    if not url:
        proc.terminate()
        raise SystemExit("[ERROR] cloudflared no dio ninguna URL (mira logs/tunel.log)")
    log(f"URL del tunel: {url}")
    if not espera_tunel(url):
        proc.terminate()
        raise SystemExit("[ERROR] el tunel no llega a servir el Core")
    datos = escribe_json(url)
    log(f"direccion escrita en {FICHERO.relative_to(RAIZ)} "
        f"(nodos={datos['nodos']}, version={datos['version'] or '?'})")
    if not no_publicar:
        publica()
    return proc, url


def vigila(proc: subprocess.Popen, url: str, no_publicar: bool) -> None:
    """Bucle: si el tunel muere o deja de servir, se relanza y se republica."""
    while True:
        time.sleep(VIGILIA_SEG)
        if proc.poll() is not None:
            log(f"el tunel se cerro (codigo {proc.returncode}): relanzando")
        else:
            codigo, _ = pide(f"{url}/stats", 15)
            if codigo == 200:
                continue
            log(f"el tunel no responde (codigo {codigo}): relanzando")
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            pass
        try:
            proc, url = abre_y_publica(no_publicar)
        except SystemExit as e:
            log(f"no se pudo recuperar el tunel: {e}")
            time.sleep(30)


def _dominio_preparado() -> bool:
    """True si ya hay un tunel con nombre preparado (lo hace tunel_dominio.py).

    Se exigen los dos ficheros: los escribe juntos al final de --preparar, asi
    que uno solo significa preparacion a medias y toca seguir con este tunel.
    """
    conf = RAIZ / "tools" / "cloudflared" / "config.yml"
    registro = RAIZ / "dominio_core.txt"
    if not (conf.exists() and registro.exists()):
        return False
    try:
        return any(l.strip() and not l.startswith("#")
                   for l in registro.read_text(encoding="utf-8").splitlines())
    except Exception:
        return False


def estado() -> int:
    print(f"fichero local: {FICHERO}")
    try:
        datos = json.loads(FICHERO.read_text(encoding="utf-8"))
    except Exception:
        datos = {}
    print(f"direccion local: {datos.get('core', '(no hay)')}")
    codigo, cuerpo = pide(f"https://{DOMINIO}/core_actual.json?_kukia={int(time.time())}", 15)
    if codigo == 200:
        try:
            d = json.loads(cuerpo)
        except Exception:
            d = {}
        print(f"publicada:      {d.get('core', '(no se entiende)')}  "
              f"({d.get('actualizado', '?')})")
        url = d.get("core", "")
        if url:
            c, _ = pide(f"{url}/stats", 12)
            print(f"tunel vivo:     {'si' if c == 200 else 'no'} (codigo {c})")
            return 0 if c == 200 else 1
        return 1
    print(f"publicada:      no se pudo leer (codigo {codigo})")
    return 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tunel publico al Core KUKIA, con direccion estable.")
    ap.add_argument("--una-vez", action="store_true", help="un ciclo y salir (para probar)")
    ap.add_argument("--solo-publicar", action="store_true", help="publicar una URL ya abierta")
    ap.add_argument("--url", default="", help="URL del tunel (con --solo-publicar)")
    ap.add_argument("--no-publicar", action="store_true", help="sin subir a surge")
    ap.add_argument("--estado", action="store_true", help="ver la direccion publicada")
    args = ap.parse_args(argv)

    if args.estado:
        return estado()

    if args.solo_publicar:
        url = (args.url or "").strip().rstrip("/")
        if not URL_LAXA.fullmatch(url):
            print("[ERROR] --url tiene que ser una URL https://algo.trycloudflare.com")
            return 2
        if not espera_tunel(url, 60):
            print("[ERROR] esa URL no sirve el Core ahora mismo")
            return 1
        escribe_json(url)
        return 0 if publica() else 1

    if args.una_vez:
        proc, url = abre_y_publica(args.no_publicar)
        log(f"listo. Direccion estable: https://{DOMINIO}/core_actual.json -> {url}")
        log("el tunel queda abierto en segundo plano; cierra este proceso para cortarlo")
        return 0

    cerrojo = toma_cerrojo()
    if cerrojo is None:
        log(f"ya hay un supervisor del tunel vivo (puerto {CERROJO}): este no hace nada")
        return 3

    log("=== KUKIA: tunel con direccion estable ===")
    if _dominio_preparado():
        # El cerrojo es comun a los dos supervisores, asi que este puede haber
        # arrancado antes de que se preparara el dominio (o a mano). Avisar es
        # mejor que dejar un trycloudflare cuando ya hay URL fija.
        log("AVISO: hay un dominio preparado (dominio_core.txt). Este es el tunel")
        log("rapido; para usar el dominio, reinicia la guardia: al arrancar elige")
        log("el modo con nombre y esta direccion deja de usarse.")
    proc, url = abre_y_publica(args.no_publicar)
    log(f"direccion estable: https://{DOMINIO}/core_actual.json -> {url}")
    log(f"vigilando cada {VIGILIA_SEG:.0f}s (Ctrl+C para cerrar el tunel)")
    try:
        vigila(proc, url, args.no_publicar)
    except KeyboardInterrupt:
        log("cerrando el tunel (el Core y el minero siguen en local)")
    finally:
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
    sys.exit(main())
