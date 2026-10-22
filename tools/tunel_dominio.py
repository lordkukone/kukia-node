"""Tunel con NOMBRE propio: https://kukia.tudominio.com, fijo para siempre.

QUE HACE
    Prepara y mantiene un tunel CON NOMBRE de Cloudflare hacia el Core. A
    diferencia del tunel rapido (tools/tunel_estable.py), la URL no cambia
    nunca: es tu dominio y no depende de que este PC este encendido para
    seguir siendo la misma direccion.

    --preparar    una sola vez: login, crear el tunel, crear el DNS y dejar
                  escrito el config. Pide un dominio ya dado de alta en
                  Cloudflare (gratis) y ~10 EUR/ano de dominio.
    --arrancar    lo que hace la guardia: abre el tunel, comprueba que sirve
                  de verdad y publica la direccion en el espejo. Si se cae,
                  lo relanza.
    --estado      que hay montado y si responde ahora mismo.

POR QUE HACE FALTA ESTE FICHERO Y NO BASTA `cloudflared tunnel run`
    1. El Core solo acepta Host local (HostGuardMiddleware). Por la URL
       publica, Cloudflare manda Host: kukia.tudominio.com y el Core contesta
       403 "Host no permitido". Comprobado a mano: 200 con Host 127.0.0.1,
       403 con el dominio. El config lleva `httpHostHeader: 127.0.0.1` en
       `originRequest` para reescribirlo, igual que el tunel rapido hacia con
       --http-host-header. Sin esa linea el tunel abre, el DNS apunta bien y
       todo parece correcto... y no funciona. Es el fallo caro de este paso.
    2. La credencial del tunel (el .json con el secreto) se queda SIEMPRE en
       %USERPROFILE%\\.cloudflared, fuera del proyecto. El config que si vive
       en el repo solo lleva el UUID y la ruta; ni un secreto.
    3. Un fallo de DNS al principio es normal (propagacion): por eso se espera
       pidiendo /stats de verdad, no solo a que cloudflared diga "conectado".

QUE NO CAMBIA
    El paquete descargable sigue diciendo `auto .../core_actual.json`, y este
    script escribe exactamente el mismo fichero que el tunel rapido. Asi que
    pasar a dominio propio no obliga a reempaquetar nada ni a que nadie
    reinstale: los dos modos son intercambiables y el zip no se entera.

USO
    python tools/tunel_dominio.py --preparar --dominio kukia.tudominio.com
    python tools/tunel_dominio.py --arrancar
    python tools/tunel_dominio.py --estado
    python tools/tunel_dominio.py --arrancar --una-vez     solo abrir y probar
    python tools/tunel_dominio.py --preparar --dominio X --nombre kukia

SALIDA
    tools/cloudflared/config.yml   config del tunel (sin secretos, va al repo)
    dominio_core.txt               el dominio elegido, en una linea
    docs/core_actual.json          la direccion viva (igual que el tunel rapido)
    logs/tunel_dominio.log         historial
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))
import tunel_estable as base  # noqa: E402  (pide, escribe_json, publica, ...)

LOGS = RAIZ / "logs"
LOG = LOGS / "tunel_dominio.log"
CONF_DIR = RAIZ / "tools" / "cloudflared"
CONF = CONF_DIR / "config.yml"
REGISTRO = RAIZ / "dominio_core.txt"
BIN = RAIZ / "tools" / "bin" / "cloudflared.exe"
# La credencial del tunel NO se muda aqui: se queda en la carpeta del usuario.
NUBE = Path(os.environ.get("KUKIA_CLOUDFLARED_HOME") or (Path.home() / ".cloudflared"))
CERT = NUBE / "cert.pem"
LOCAL = os.environ.get("KUKIA_CORE_LOCAL", "http://127.0.0.1:8300")
NOMBRE_DEF = os.environ.get("KUKIA_TUNEL_NOMBRE", "kukia")
# MISMO cerrojo que el tunel rapido (8399), a proposito: solo puede haber un
# supervisor de tunel vivo, sea del tipo que sea. Con cerrojos distintos se
# podian abrir los dos a la vez, cada uno escribiendo su direccion en
# docs/core_actual.json, y el espejo acababa apuntando al que perdia la carrera.
CERROJO = int(os.environ.get("KUKIA_TUNEL_PUERTO", "8399"))
VIGILIA_SEG = float(os.environ.get("KUKIA_TUNEL_VIGILIA", "45"))
DOMINIO_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def log(msg: str) -> None:
    linea = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(linea, flush=True)
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except Exception:
        pass


def exe() -> str:
    if BIN.exists():
        return str(BIN)
    hallado = shutil.which("cloudflared")
    if not hallado:
        raise SystemExit(f"[ERROR] no encuentro cloudflared en {BIN} ni en el PATH")
    return hallado


def corre(argv: list, seg: float = 120.0, interactivo: bool = False) -> tuple:
    """(codigo, salida). interactivo=True deja ver al usuario lo que pasa."""
    if interactivo:
        r = subprocess.run(argv, timeout=seg, stdin=None)
        return r.returncode, ""
    r = subprocess.run(argv, capture_output=True, timeout=seg,
                       stdin=subprocess.DEVNULL)
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


def toma_cerrojo():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", CERROJO))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def supervisor_vivo() -> bool:
    """True si ya hay uno corriendo (probando el cerrojo, no la lista de procesos)."""
    cerrojo = toma_cerrojo()
    if cerrojo is None:
        return True
    cerrojo.close()
    return False


def dominio_guardado() -> str:
    try:
        for linea in REGISTRO.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#"):
                return linea
    except Exception:
        pass
    return ""


def config_leido() -> dict:
    """Lee lo justo del config (uuid, hostname) sin depender de PyYAML."""
    datos = {}
    try:
        texto = CONF.read_text(encoding="utf-8")
    except Exception:
        return datos
    m = re.search(r"^tunnel:\s*([0-9a-f-]{36})\s*$", texto, re.M | re.I)
    if m:
        datos["uuid"] = m.group(1)
    m = re.search(r"^\s*-\s*hostname:\s*(\S+)\s*$", texto, re.M)
    if m:
        datos["dominio"] = m.group(1).strip("\"'")
    return datos


def credencial(uuid: str) -> Path:
    return NUBE / f"{uuid}.json"


def lista_tuneles(salida: str) -> list:
    """Tuneles del `tunnel list --output json`, tolerando ruido alrededor.

    Si algun dia cloudflared mete una linea de log en stdout, el JSON no empieza
    en el primer byte; se intenta el texto entero y, si falla, desde el primer
    corchete. Nunca revienta: sin lista, se crea el tunel.
    """
    texto = (salida or "").strip()
    if not texto:
        return []
    candidatos = [texto]
    if "[" in texto:
        candidatos.append(texto[texto.index("["):])
    for candidato in candidatos:
        try:
            datos = json.loads(candidato)
        except Exception:
            continue
        if isinstance(datos, list):
            return datos
    return []


def dns_resuelve(dominio: str) -> list:
    try:
        info = socket.getaddrinfo(dominio, 443, proto=socket.IPPROTO_TCP)
        return sorted({i[4][0] for i in info})
    except Exception:
        return []


def escribe_config(dominio: str, uuid: str) -> Path:
    """Config del tunel. Sin secretos: solo el UUID y la ruta a la credencial."""
    cred = credencial(uuid).as_posix()
    texto = f"""# KUKIA - tunel CON NOMBRE hacia el Core. Generado por tools/tunel_dominio.py.
#
# Este fichero NO lleva ningun secreto: la credencial del tunel vive en
# %USERPROFILE%\\.cloudflared\\{uuid}.json y no se copia aqui nunca.
#
# httpHostHeader es OBLIGATORIO: el Core rechaza (403) cualquier Host que no
# sea local, y por el dominio llegaria Host: {dominio}.
tunnel: {uuid}
credentials-file: {cred}
no-autoupdate: true

ingress:
  - hostname: {dominio}
    service: http://127.0.0.1:8300
    originRequest:
      httpHostHeader: 127.0.0.1
      connectTimeout: 30s
      noHappyEyeballs: false
  # Todo lo que no sea el dominio, 404: no se publica nada mas de este PC.
  - service: http_status:404
"""
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONF.with_suffix(".yml.tmp")
    tmp.write_text(texto, encoding="utf-8")
    tmp.replace(CONF)
    return CONF


def escribe_registro(dominio: str) -> Path:
    """Deja dicho cual es el dominio. Se escribe SOLO si el config ya valido.

    La guardia exige los dos ficheros, asi que este es el que "arma" el modo
    dominio: si aparece sin config validado, no se cambiaria de tunel.
    """
    REGISTRO.write_text(
        "# Dominio publico del Core KUKIA (una linea, lo demas se ignora).\n"
        "# Lo escribe tools/tunel_dominio.py --preparar. Si lo borras, la\n"
        "# guardia vuelve al tunel rapido con direccion estable (trycloudflare).\n"
        f"{dominio}\n", encoding="utf-8")
    return REGISTRO


def preparar(dominio: str, nombre: str = NOMBRE_DEF, forzar_dns: bool = False) -> int:
    """Una sola vez: login, crear tunel, DNS y config. Idempotente."""
    dominio = (dominio or "").strip().lower().rstrip(".")
    if not DOMINIO_RE.match(dominio):
        print(f"[ERROR] '{dominio}' no parece un dominio valido "
              f"(ejemplo: kukia.tudominio.com)")
        return 2
    if dominio.count(".") < 2:
        print("[AVISO] sin subdominio (ej. tudominio.com): vale, pero lo normal es")
        print("        kukia.tudominio.com, para no ocupar la raiz del dominio.")

    binario = exe()
    print(f"[1/5] cloudflared: {binario}")

    # --- sesion de Cloudflare -------------------------------------------
    if not CERT.exists():
        print("[2/5] no hay sesion de Cloudflare en este PC: hay que autorizarla.")
        print(f"      Se abrira el navegador. Elige ahi tu dominio ({dominio})")
        print("      y pulsa 'Authorize'. Es la unica vez que se hace.")
        if not sys.stdin.isatty():
            print()
            print("[ERROR] esto necesita una consola de verdad (abre el navegador).")
            print("        Ejecutalo a mano en una ventana de CMD:")
            print(f"        \"{binario}\" tunnel login")
            print("        y luego repite:")
            print(f"        python tools\\tunel_dominio.py --preparar --dominio {dominio}")
            return 2
        try:
            codigo, _ = corre([binario, "tunnel", "login"], seg=900, interactivo=True)
        except subprocess.TimeoutExpired:
            print("[ERROR] el navegador no se autorizo en 15 minutos.")
            print("        Repite el comando cuando lo tengas a mano.")
            return 1
        if codigo != 0 or not CERT.exists():
            print("[ERROR] el login no termino: no hay cert.pem.")
            print(f"        Prueba a mano: \"{binario}\" tunnel login")
            return 1
    print(f"[2/5] sesion de Cloudflare: OK ({CERT})")

    # --- el tunel --------------------------------------------------------
    print(f"[3/5] tunel con nombre '{nombre}' ...")
    codigo, salida = corre([binario, "tunnel", "list", "--output", "json",
                            "--name", nombre], seg=90)
    uuid = ""
    for t in lista_tuneles(salida):
        if t.get("name") == nombre and not t.get("deleted_at"):
            uuid = t.get("id", "")
    if not uuid:
        codigo, salida = corre([binario, "tunnel", "create", nombre], seg=120)
        m = re.search(r"with id ([0-9a-f-]{36})", salida, re.I)
        uuid = m.group(1) if m else ""
        if not uuid:
            # Puede existir ya (create falla y no imprime el id): se relee.
            _, salida2 = corre([binario, "tunnel", "list", "--output", "json",
                                "--name", nombre], seg=90)
            uuid = next((t.get("id", "") for t in lista_tuneles(salida2)
                         if t.get("name") == nombre), "")
        if not uuid:
            print("[ERROR] no pude crear ni encontrar el tunel:")
            print("        " + (salida or "(sin salida)").strip()[:600])
            return 1
        print(f"      creado: {uuid}")
    else:
        print(f"      ya existia: {uuid}")
    if not credencial(uuid).exists():
        print(f"[ERROR] falta la credencial {credencial(uuid)}")
        print("        Es la que deja 'cloudflared tunnel create'. Si moviste la")
        print("        carpeta .cloudflared, vuelve a ponerla en su sitio.")
        return 1

    # --- DNS -------------------------------------------------------------
    print(f"[4/5] DNS {dominio} -> tunel ...")
    argv = [binario, "tunnel", "route", "dns"]
    if forzar_dns:
        argv.append("--overwrite-dns")
    argv += [nombre, dominio]
    codigo, salida = corre(argv, seg=90)
    if codigo != 0:
        bajo = salida.lower()
        if "already exists" in bajo or "record with that host" in bajo:
            print("      el registro ya existia: vale igual"
                  " (usa --forzar-dns para reescribirlo)")
        elif "zone" in bajo or "not found" in bajo:
            print("[ERROR] ese dominio no esta en la cuenta de Cloudflare.")
            print("        Da de alta el dominio en Cloudflare (nameservers) y")
            print("        repite. Detalle: " + salida.strip()[:400])
            return 1
        else:
            print("[ERROR] no se pudo crear el DNS: " + salida.strip()[:400])
            return 1
    else:
        print("      registro creado")

    # --- config ----------------------------------------------------------
    escribe_config(dominio, uuid)
    # OJO: en `tunnel ingress` el --config va ANTES del subcomando
    # (`cloudflared tunnel [--config FILE] ingress validate`). Detras, cloudflared
    # contesta "flag provided but not defined" y devuelve 0: parecia validar y
    # no validaba nada.
    codigo, salida = corre([binario, "tunnel", "--config", str(CONF),
                            "ingress", "validate"], seg=60)
    if codigo != 0:
        print("[ERROR] el config generado no valida:")
        print("        " + salida.strip()[:600])
        # Se borra para no dejar un modo dominio a medias: sin config, la
        # guardia sigue con el tunel rapido, que funciona.
        CONF.unlink(missing_ok=True)
        return 1
    escribe_registro(dominio)
    print(f"[5/5] config escrito y validado: {CONF.relative_to(RAIZ)}")

    print()
    print("LISTO. Ahora:")
    print("   ARRANCAR_TUNEL.cmd        (o reinicia la guardia: lo coge solo)")
    print("   python tools\\tunel_dominio.py --estado")
    print()
    print(f"La primera vez el DNS tarda un poco en propagarse; el minero NO se")
    print(f"entera de nada: sigue usando la direccion estable del espejo.")
    return 0


def espera_dominio(dominio: str, proc: subprocess.Popen, seg: float = 300.0) -> bool:
    """El DNS puede tardar: se pide /stats de verdad hasta que conteste."""
    url = f"https://{dominio}"
    fin = time.time() + seg
    ultimo, avisado = 0, False
    while time.time() < fin:
        if proc.poll() is not None:
            log(f"cloudflared murio (codigo {proc.returncode}) antes de servir")
            return False
        codigo, cuerpo = base.pide(f"{url}/stats", 15)
        if codigo == 200 and "supply_emitido" in cuerpo:
            return True
        if codigo == 403 and not avisado:
            avisado = True
            log("el dominio responde 403: el Core rechazo el Host. Revisa que el")
            log("config lleve originRequest.httpHostHeader: 127.0.0.1")
        ultimo = codigo
        time.sleep(5)
    log(f"el dominio no sirvio /stats a tiempo (ultimo codigo: {ultimo})")
    return False


def arranca_cloudflared(uuid: str, nombre: str) -> subprocess.Popen:
    """Arranca el tunel POR UUID, no por nombre.

    Con el nombre, cloudflared necesita antes resolverlo leyendo cert.pem; si
    ese fichero se pierde (o se copia el tunel a otro PC), el tunel no arranca
    aunque el config este perfecto (comprobado: "Error locating origin cert").
    Con el UUID del config solo hace falta la credencial.
    """
    extra = {}
    if os.name == "nt":
        extra["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    argv = [exe(), "tunnel", "--config", str(CONF), "--no-autoupdate", "run", uuid]
    log(f"abriendo tunel '{nombre}' ({uuid[:8]}...) con {CONF.relative_to(RAIZ)}")
    LOGS.mkdir(parents=True, exist_ok=True)
    salida = (LOGS / "tunel_dominio_stdout.log").open("ab")
    return subprocess.Popen(argv, stdout=salida, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, cwd=str(RAIZ), **extra)


def abre_y_publica(no_publicar: bool = False) -> tuple:
    datos = config_leido()
    dominio = datos.get("dominio") or dominio_guardado()
    uuid = datos.get("uuid", "")
    if not CONF.exists() or not dominio or not uuid:
        raise SystemExit("[ERROR] no hay tunel con nombre preparado. Primero:\n"
                         "        python tools\\tunel_dominio.py --preparar "
                         "--dominio kukia.tudominio.com")
    if not base.espera_core():
        raise SystemExit("[ERROR] el Core no responde en este PC: arrancalo antes "
                         "(ARRANCAR_GUARDIA.cmd) o revisa el puerto 8300")
    nombre = NOMBRE_DEF
    proc = arranca_cloudflared(uuid, nombre)
    if not espera_dominio(dominio, proc):
        proc.terminate()
        raise SystemExit("[ERROR] el tunel no llega a servir el Core por el dominio")
    url = f"https://{dominio}"
    d = base.escribe_json(url)
    log(f"direccion escrita en docs/core_actual.json "
        f"(nodos={d['nodos']}, version={d['version'] or '?'})")
    if not no_publicar:
        # Con dominio propio la URL ya es fija: si el espejo falla, no se cae
        # nada. Se publica para que el zip siga funcionando igual.
        if not base.publica():
            log("AVISO: no se pudo publicar en el espejo, pero el dominio funciona")
    return proc, url, dominio


def vigila(proc: subprocess.Popen, url: str, dominio: str, no_publicar: bool) -> None:
    while True:
        time.sleep(VIGILIA_SEG)
        if proc.poll() is not None:
            log(f"el tunel se cerro (codigo {proc.returncode}): relanzando")
        else:
            codigo, _ = base.pide(f"{url}/stats", 15)
            if codigo == 200:
                continue
            log(f"el dominio no responde (codigo {codigo}): relanzando")
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            pass
        try:
            proc, url, dominio = abre_y_publica(no_publicar)
        except SystemExit as e:
            log(f"no se pudo recuperar el tunel: {e}")
            time.sleep(30)


def estado() -> int:
    datos = config_leido()
    dominio = datos.get("dominio") or dominio_guardado()
    uuid = datos.get("uuid", "")
    vivo = False

    try:
        _, version = corre([exe(), "--version"], seg=20)
        print(f"cloudflared:   {(version.strip().splitlines() or ['?'])[0]}")
    except SystemExit as e:
        print(f"cloudflared:   NO ({e})")

    print(f"sesion:        {'si' if CERT.exists() else 'NO (falta tunnel login)'}"
          f"  {CERT}")
    if CONF.exists():
        print(f"config:        {CONF.relative_to(RAIZ)}  (tunel {uuid or '?'})")
        print(f"credencial:    {'si' if uuid and credencial(uuid).exists() else 'NO'}")
    else:
        print("config:        no hay tunel con nombre preparado")
    print(f"dominio:       {dominio or '(ninguno)'}")

    if dominio:
        ips = dns_resuelve(dominio)
        print(f"DNS:           {', '.join(ips) if ips else 'no resuelve'}")
        codigo, cuerpo = base.pide(f"https://{dominio}/stats", 15)
        vivo = codigo == 200
        print(f"HTTPS /stats:  {codigo}"
              + ("" if codigo else "  (sin respuesta)"))
        if codigo == 403:
            print("               OJO: 403 = el Core rechaza el Host."
                  " Falta originRequest.httpHostHeader en el config.")
        if codigo == 200:
            try:
                d = json.loads(cuerpo)
                print(f"nodos:         {d.get('nodos', '?')}   "
                      f"tareas_ok: {d.get('tareas_ok', '?')}")
            except Exception:
                pass
    print(f"supervisor:    {'vivo' if supervisor_vivo() else 'parado'}")
    mirror = ""
    try:
        c, cuerpo = base.pide(f"https://{base.DOMINIO}/core_actual.json"
                              f"?_kukia={int(time.time())}", 15)
        if c == 200:
            mirror = json.loads(cuerpo).get("core", "")
    except Exception:
        pass
    if mirror:
        print(f"publicado:     {mirror}")
        if dominio and mirror.rstrip("/") != f"https://{dominio}":
            print("               (el espejo aun apunta al tunel rapido: "
                  "arranca este modo y se corrige)")
    return 0 if dominio and vivo else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Tunel CON NOMBRE (dominio propio) hacia el Core KUKIA.")
    ap.add_argument("--preparar", action="store_true",
                    help="una vez: login, crear tunel, DNS y config")
    ap.add_argument("--dominio", default="", help="p.ej. kukia.tudominio.com")
    ap.add_argument("--nombre", default=NOMBRE_DEF, help=f"nombre del tunel ({NOMBRE_DEF})")
    ap.add_argument("--forzar-dns", action="store_true",
                    help="reescribir el registro DNS si ya existe")
    ap.add_argument("--arrancar", action="store_true",
                    help="abrir el tunel, verificar, publicar y vigilar")
    ap.add_argument("--una-vez", action="store_true", help="abrir y salir (para probar)")
    ap.add_argument("--no-publicar", action="store_true", help="sin subir al espejo")
    ap.add_argument("--estado", action="store_true", help="que hay montado y si responde")
    args = ap.parse_args(argv)

    if args.estado:
        return estado()
    if args.preparar:
        return preparar(args.dominio, args.nombre, args.forzar_dns)

    if args.una_vez:
        proc, url, _ = abre_y_publica(args.no_publicar)
        log(f"listo. Dominio fijo: {url}")
        log("el tunel queda abierto; cierra este proceso para cortarlo")
        return 0

    if not CONF.exists():
        print("[ERROR] no hay tunel con nombre preparado. Una sola vez:")
        print(f"        python tools\\tunel_dominio.py --preparar --dominio "
              f"{args.dominio or 'kukia.tudominio.com'}")
        return 2

    cerrojo = toma_cerrojo()
    if cerrojo is None:
        log(f"ya hay un supervisor de tunel vivo (puerto {CERROJO}): no se abre otro")
        log("para pasar a dominio propio, reinicia la guardia (o cierra la ventana")
        log("del tunel): al arrancar, elige este modo solo")
        return 3
    log("=== KUKIA: tunel con nombre (dominio propio) ===")
    try:
        proc, url, dominio = abre_y_publica(args.no_publicar)
        log(f"dominio: {url}")
        log(f"vigilando cada {VIGILIA_SEG:.0f}s (Ctrl+C para cerrar el tunel)")
        vigila(proc, url, dominio, args.no_publicar)
    except KeyboardInterrupt:
        log("cerrando el tunel (el Core y el minero siguen en local)")
    finally:
        try:
            cerrojo.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
