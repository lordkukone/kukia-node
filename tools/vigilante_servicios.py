#!/usr/bin/env python3
"""Vigila las tres webs y el Core, y avisa cuando algo se cae o vuelve.

QUE MIRA (cada pasada)
  1. la web de Surge        (kukia-network.surge.sh)
  2. la web de Freebuff     (site-1ea296...freebuff.page)
  3. la web de GitHub Pages (lordkukone.github.io/kukia-node)
  4. el Core local          (127.0.0.1:8300/stats)
  5. el Core publico        (el que dice core_actual.json -> tunel /stats)
  6. que las tres webs sirvan EL MISMO paquete (tamano de kukia-node.zip);
     si una se queda con una version vieja, tambien avisa.
  7. que la copia de claves de N:/CLAVES_KUKIA no tenga mas de una semana:
     si la tarea del domingo no corrio (disco desconectado), avisa de eso.
  8. la disponibilidad de HOY: si algun servicio lleva el dia por debajo del
     99 % (con al menos media hora medida), avisa UNA vez por servicio y dia.
     Se calcula con los logs de los dos vigilantes (tools/incidencias.py).

CUANDO AVISA
  Solo al CAMBIAR de estado: cuando algo se cae y cuando vuelve. Nada de avisar
  cada 5 minutos de lo mismo (eso se acaba ignorando).
  Las TRES WEBS avisan en la misma pasada en que dejan de responder: si el
  primer intento falla, se reintenta 3 veces en 10 segundos (un corte de red
  de tres segundos no es una caida) y, si sigue mal, el aviso sale YA, sin
  esperar a la pasada siguiente ni a que la web vuelva. El resto de servicios
  (Core local, Core publico, copias) siguen con el criterio de dos pasadas
  seguidas, que ahi si hay rebotes normales de los tuneles.

USO
    python tools/vigilante_servicios.py            bucle sin fin (cada 5 min)
    python tools/vigilante_servicios.py --una-vez  una sola pasada (lo que hace la tarea)
    python tools/vigilante_servicios.py --estado   que se vio la ultima vez
    python tools/vigilante_servicios.py --verificar-copia  revalidar ya la copia de N:
    python tools/vigilante_servicios.py --probar-aviso   ver como suena el aviso
    python tools/vigilante_servicios.py --instalar tarea de Windows cada 5 min, sin ventana
    python tools/vigilante_servicios.py --quitar   quitar la tarea
    python tools/vigilante_servicios.py --sin-aviso  mirar sin notificar (para probar)

SALIDA
    logs/vigilante_servicios.log    historial con hora
    logs/vigilante_servicios.json   estado de cada servicio
    ESTADO_WEBS.txt                 resumen legible de la ultima pasada

Aviso por DOS canales, independientes:
  - notificacion de Windows (esquina, sin ventana) por PowerShell oculto;
  - Telegram al movil, si esta configurado (`python tools/avisos.py --configurar`).
Si un canal falla, el otro sigue, y siempre queda el log y ESTADO_WEBS.txt.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
LOGS = RAIZ / "logs"
LOG = LOGS / "vigilante_servicios.log"
ESTADO = LOGS / "vigilante_servicios.json"
RESUMEN = RAIZ / "ESTADO_WEBS.txt"
ZIP_LOCAL = RAIZ / "docs" / "kukia-node.zip"
TAREA = "KUKIA_vigilante_webs"
MINUTOS = 5
FALLOS_PARA_CAIDO = 2

# --- Aviso inmediato de las webs (25-10-2026) ------------------------------
# Antes, una web que dejaba de responder a las 21:05 se avisaba a las 21:15
# (hacia falta una segunda pasada mala). Con freebuff caido 12 horas y el
# aviso saliendo tarde, no sirve: se reintenta dentro de la MISMA pasada y, si
# sigue mal, el aviso sale en el momento. Los reintentos existen para no
# despertar a nadie por un corte de red de tres segundos.
INTENTOS_WEB = 3
ESPERA_WEB = 5.0

# --- Disponibilidad del dia (22-10-2026) -----------------------------------
# Que avise cuando un servicio lleve el dia por debajo del 99 %. Dos frenos:
#  - OBSERVADO_MIN: por la mañana hay pocos minutos medidos y un fallo suelto
#    daria 80 %; no se avisa hasta tener media hora de datos.
#  - Una vez por servicio y dia (logs/disponibilidad_avisada.json): si no, el
#    mismo 98 % avisaria cada 5 minutos hasta la medianoche.
DISPONIBLE_MIN = float(os.environ.get("KUKIA_DISPONIBLE_MIN", "99"))
DISPONIBLE_OBSERVADO_MIN = float(os.environ.get("KUKIA_DISPONIBLE_OBSERVADO_MIN", "1800"))
AVISADAS = LOGS / "disponibilidad_avisada.json"
# Lo que NO es disponibilidad: `paquete-igual` compara versiones entre webs, no
# dice si un servicio esta arriba. Si entraba aqui, un desajuste de despliegue
# daba "0,0 % de disponibilidad" y el aviso decia una cosa que no era.
NO_SON_DISPONIBILIDAD = {"paquete-igual"}

SITIOS = {
    "surge": "https://kukia-network.surge.sh",
    "freebuff": "https://site-1ea296595daa4ba7935f1574ab9a5a13.freebuff.page",
    "github": "https://lordkukone.github.io/kukia-node",
}
ESPEJO = "https://kukia-network.surge.sh/core_actual.json"
CORE_LOCAL = "http://127.0.0.1:8300"
# Copia de claves en otro disco (tools/copia_claves.py). Si la ultima tiene mas
# de una semana, es que la tarea del domingo no pudo correr (N: desconectado).
COPIA_CLAVES = Path("N:/CLAVES_KUKIA")
DIAS_COPIA_MAX = 7
# La copia se revalida entera (hashes + clave del sello) una vez al dia y el
# veredicto BUENO se reutiliza el resto; si sale mal se reintenta en cada
# pasada, que un fichero borrado en N: no se arregla solo.
CACHE_INTEGRA = LOGS / "copia_integra.json"
HORAS_INTEGRA = 24

SIN_VENTANA = 0x08000000 if os.name == "nt" else 0

TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$p = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$t = $p.GetElementsByTagName("text")
$t.Item(0).AppendChild($p.CreateTextNode($env:KUKIA_TITULO)) | Out-Null
$t.Item(1).AppendChild($p.CreateTextNode($env:KUKIA_TEXTO)) | Out-Null
$a = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
$n = [Windows.UI.Notifications.ToastNotification]::new($p)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($a).Show($n)
"""


def log(msg: str) -> None:
    linea = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    # Con pythonw.exe (la tarea de Windows y su ventana inexistente) sys.stdout
    # es None y `print` reventaria: el vigilante moriria antes de escribir nada.
    if sys.stdout is not None:
        try:
            print(linea, flush=True)
        except Exception:
            pass
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except Exception:
        pass


def avisa(titulo: str, texto: str) -> bool:
    """Aviso al escritorio (Windows) y al movil (Telegram). Nunca lanza.

    Los dos canales son independientes a proposito: si el PC esta apagado o el
    vigilante corre sin sesion, el toast no se ve, pero el movil si suena. Si
    Telegram no esta configurado (`python tools/avisos.py --configurar`),
    simplemente no manda nada y queda el toast y el log.
    """
    hecho = False
    if os.name == "nt":
        entorno = dict(os.environ, KUKIA_TITULO=titulo, KUKIA_TEXTO=texto)
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                                "-Command", TOAST],
                               capture_output=True, text=True, timeout=30,
                               env=entorno, creationflags=SIN_VENTANA)
            hecho = r.returncode == 0
        except Exception:
            hecho = False
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import avisos as _avisos
        hecho = _avisos.enviar(titulo, texto) or hecho
    except Exception:
        pass
    return hecho


def pide(url: str, tiempo: float = 20.0) -> tuple[int, bytes, dict]:
    """(codigo, cuerpo, cabeceras). codigo 0 si no hubo respuesta."""
    pet = urllib.request.Request(url, headers={"User-Agent": "KUKIA-vigilante/1.0"})
    try:
        with urllib.request.urlopen(pet, timeout=tiempo) as r:
            return r.status, r.read(200000), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read(200000), dict(e.headers or {})
        except Exception:
            return e.code, b"", {}
    except Exception:
        return 0, b"", {}


def huella_zip(base: str) -> str:
    """Huella (sha256) del paquete que sirve la web, leida de su .sha256.

    Se mira el .sha256 y no el zip entero a proposito: son 81 bytes en vez de
    687 KB, y dice exactamente que version esta sirviendo. Surge, ademas, no
    manda Content-Length, asi que por tamano no se podria comparar.
    """
    codigo, cuerpo, _ = pide(f"{base}/kukia-node.zip.sha256", tiempo=25.0)
    if codigo != 200:
        return ""
    trozo = cuerpo.decode("utf-8", "replace").split()
    return trozo[0].strip().lower() if trozo else ""


def huella_local() -> str:
    try:
        texto = (ZIP_LOCAL.parent / "kukia-node.zip.sha256").read_text(encoding="utf-8")
        trozo = texto.split()
        return trozo[0].strip().lower() if trozo else ""
    except Exception:
        return ""


def estado_previo() -> dict:
    try:
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    except Exception:
        return {}


def guarda_estado(d: dict) -> None:
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        ESTADO.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    except Exception:
        pass


def core_publico() -> tuple[str, str]:
    """(url, detalle): la URL viva del Core segun el espejo."""
    codigo, cuerpo, _ = pide(ESPEJO, tiempo=20.0)
    if codigo != 200:
        return "", f"el espejo no responde ({codigo or 'sin conexion'})"
    try:
        url = (json.loads(cuerpo.decode("utf-8", "replace")).get("core") or "").rstrip("/")
    except Exception:
        return "", "el espejo devuelve algo que no es JSON"
    return url, url or "el espejo no trae direccion"


def ultima_copia(base: Path = COPIA_CLAVES) -> tuple[Path | None, datetime | None, str]:
    """(carpeta, cuando, nombre) de la copia mas reciente con MANIFIESTO.json."""
    if not base.exists():
        return None, None, ""
    cuando, cual, carpeta = None, "", None
    try:
        carpetas = [c for c in base.iterdir() if c.is_dir()]
    except OSError:
        return None, None, ""
    for c in carpetas:
        man = c / "MANIFIESTO.json"
        if not man.is_file():
            continue
        try:
            t = datetime.strptime(
                str(json.loads(man.read_text(encoding="utf-8")).get("copiado"))[:19],
                "%Y-%m-%d %H:%M:%S")
        except Exception:
            try:
                t = datetime.fromtimestamp(man.stat().st_mtime)
            except OSError:
                continue
        if cuando is None or t > cuando:
            cuando, cual, carpeta = t, c.name, c
    return carpeta, cuando, cual


def estado_copia_integra(base: Path = COPIA_CLAVES, forzar: bool = False) -> dict:
    """La ultima copia, revalidada de verdad: hashes y clave del sello.

    No basta con que el MANIFIESTO exista: si un fichero se corrompe o alguien
    lo cambia en el disco externo, el manifiesto seguiria ahi tan feliz. Esto
    relee cada fichero, compara su sha256 y comprueba que la clave copiada es
    la que firma los sellos vivos.

    Cadencia: se hace UNA VEZ AL DIA (cache en logs/copia_integra.json), que
    releer el disco externo cada 5 minutos no aporta. Si el resultado es malo
    NO se cachea como bueno: se reintenta en cada pasada hasta que se arregle.
    """
    nombre = "copia-integra"
    carpeta, cuando, cual = ultima_copia(base)
    if carpeta is None:
        return {"nombre": nombre, "ok": False,
                "detalle": "no hay ninguna copia con MANIFIESTO.json que verificar"}

    cache = {}
    try:
        cache = json.loads(CACHE_INTEGRA.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    if not forzar and cache.get("carpeta") == str(carpeta) and cache.get("ok"):
        try:
            edad = (datetime.now()
                    - datetime.fromisoformat(str(cache.get("cuando")))).total_seconds() / 3600
        except Exception:
            edad = HORAS_INTEGRA + 1
        if edad < HORAS_INTEGRA:
            return {"nombre": nombre, "ok": True,
                    "detalle": (f"{cual}: {cache.get('ficheros', '?')} ficheros y clave OK "
                                f"(verificado hace {edad:.0f} h)")}

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import copia_claves as cc
        r = cc.revisar(carpeta)
    except Exception as e:
        return {"nombre": nombre, "ok": False,
                "detalle": f"no pude verificar {cual}: {type(e).__name__}: {e}"}

    ok = bool(r["ok"])
    if ok:
        detalle = (f"{cual}: {len(r['ficheros'])} ficheros, hashes y clave del sello OK")
    else:
        detalle = f"{cual}: " + "; ".join(r["problemas"][:3])
        if len(r["problemas"]) > 3:
            detalle += f" (+{len(r['problemas']) - 3} mas)"
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        CACHE_INTEGRA.write_text(json.dumps(
            {"carpeta": str(carpeta), "cuando": datetime.now().isoformat(timespec="seconds"),
             "ok": ok, "ficheros": len(r["ficheros"]), "detalle": detalle},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass
    return {"nombre": nombre, "ok": ok, "detalle": detalle}


def estado_copia_claves(base: Path = COPIA_CLAVES) -> dict:
    """La copia de claves en otro disco: que exista y que no tenga mas de una semana.

    La tarea `KUKIA copia de claves` copia los domingos a las 04:20. Si el disco
    externo estaba desconectado, la copia no se hizo y hasta hoy nadie se
    enteraba: aqui salta. Lo que se mira es la fecha que el propio script dejo
    en MANIFIESTO.json (o, si no la hubiera, la del fichero).
    """
    nombre = "copia-claves"
    if not base.exists():
        return {"nombre": nombre, "ok": False,
                "detalle": f"{base} no existe (disco sin conectar?)"}
    carpeta, cuando, cual = ultima_copia(base)
    if cuando is None:
        return {"nombre": nombre, "ok": False,
                "detalle": f"ninguna copia con MANIFIESTO.json en {base}"}
    dias = (datetime.now() - cuando).total_seconds() / 86400
    ok = dias <= DIAS_COPIA_MAX
    if ok:
        detalle = f"{cual}, hace {dias:.0f} dias"
    else:
        detalle = (f"la ultima es del {cuando:%d-%m-%Y} ({dias:.0f} dias): "
                   f"el disco N: estaba desconectado?")
    return {"nombre": nombre, "ok": ok, "detalle": detalle}


def hay_internet(tiempo: float = 8.0) -> bool:
    """Si el PC sale a internet. Sirve para no culpar a las webs de un corte de casa.

    El 25/10 a las 09:01 las tres webs "fallaron" a la vez: era la red del PC
    (reinicio). Con el aviso inmediato, eso habria mandado tres avisos falsos.
    """
    for url in ("https://cloudflare.com/cdn-cgi/trace",
                "https://www.google.com/generate_204"):
        codigo, _, _ = pide(url, tiempo=tiempo)
        if 200 <= codigo < 400:
            return True
    return False


def mira_sitio(base: str, esperada: str = "") -> tuple[bool, str, str]:
    """(ok, detalle, huella) de una web, con reintentos dentro de la pasada.

    Un unico fallo puede ser un corte de red de tres segundos, no una caida.
    Se prueba INTENTOS_WEB veces separadas ESPERA_WEB segundos: si una sale
    bien, la web esta arriba; si fallan todas, esta caida de verdad y quien
    llama debe avisar YA (no esperar a la pasada siguiente, que son 5 minutos
    mas). Se reintenta tambien el caso de "sirve otro paquete": una CDN puede
    tardar unos segundos en cambiar de version.
    """
    detalle, huella = "", ""
    intentos = 0
    for intento in range(INTENTOS_WEB):
        intentos = intento + 1
        codigo, cuerpo, _ = pide(base + "/")
        huella = ""
        if codigo == 200 and b"KUKIA" in cuerpo[:4000]:
            huella = huella_zip(base)
            if huella and esperada and huella != esperada:
                detalle = (f"200 pero sirve otro paquete ({huella[:8]}...), "
                           f"el bueno es {esperada[:8]}...")
            else:
                return (True,
                        f"200, paquete {huella[:8]}..." if huella
                        else "200, no se pudo leer la huella del zip",
                        huella)
        else:
            detalle = f"HTTP {codigo}" if codigo else "sin respuesta"
        if intentos < INTENTOS_WEB:
            time.sleep(ESPERA_WEB)
    if intentos > 1:
        detalle += f" ({intentos} intentos en {int(ESPERA_WEB * (intentos - 1))} s)"
    return False, detalle, huella


def comprueba(core_local: str = CORE_LOCAL,
              copias: Path = COPIA_CLAVES) -> list[dict]:
    """Una pasada. Devuelve [{nombre, ok, detalle}]."""
    salida = []
    huellas = {}
    esperada = huella_local()

    for nombre, base in SITIOS.items():
        ok, detalle, h = mira_sitio(base, esperada)
        if h:
            huellas[nombre] = h
        r = {"nombre": nombre, "ok": ok, "detalle": detalle}
        # Web caida tras los reintentos: se avisa en esta misma pasada, no en
        # la siguiente (ver INTENTOS_WEB arriba).
        if not ok:
            r["inmediato"] = True
        salida.append(r)

    # Las tres webs a la vez no se caen por separado: si ademas el PC no sale a
    # internet, el problema es de casa y esos fallos NO se avisan al momento (ni
    # se avisarian luego, porque al volver la red todo estaria bien otra vez).
    webs_mal = [r for r in salida if r["nombre"] in SITIOS and not r["ok"]]
    if len(webs_mal) == len(SITIOS) and not hay_internet():
        for r in webs_mal:
            r["inmediato"] = False
            r["detalle"] += " (el PC no tiene internet: no es la web)"

    # Que las tres sirvan el mismo paquete (si se pudo leer en todas).
    vistas = {k: v for k, v in huellas.items() if v}
    if len(vistas) >= 2 and len(set(vistas.values())) > 1:
        salida.append({"nombre": "paquete-igual",
                       "ok": False,
                       "detalle": "las webs sirven paquetes distintos: "
                                  + ", ".join(f"{k}={v[:8]}" for k, v in vistas.items())})

    # Core local.
    codigo, cuerpo, _ = pide(core_local + "/stats")
    ok = codigo == 200 and b"supply_emitido" in cuerpo
    detalle = f"HTTP {codigo}" if codigo else "sin respuesta"
    if ok:
        try:
            d = json.loads(cuerpo.decode("utf-8", "replace"))
            detalle = f"200, {d.get('nodos', '?')} nodos, v{d.get('version', '?')}"
        except Exception:
            pass
    salida.append({"nombre": "core-local", "ok": ok, "detalle": detalle})

    # Core publico (el que usan los mineros de fuera).
    url, info = core_publico()
    if not url:
        salida.append({"nombre": "core-publico", "ok": False, "detalle": info})
    else:
        codigo, cuerpo, _ = pide(url + "/stats", tiempo=25.0)
        ok = codigo == 200 and b"supply_emitido" in cuerpo
        salida.append({"nombre": "core-publico", "ok": ok,
                       "detalle": (f"200 en {url}" if ok
                                   else f"{codigo or 'sin respuesta'} en {url}")})

    # Que las claves sigan saliendo del disco del Core. No es una web ni un
    # servicio que responder: es que la tarea semanal haya corrido de verdad.
    salida.append(estado_copia_claves(copias))
    # Y que la copia siga siendo la copia: hashes y clave, una vez al dia.
    salida.append(estado_copia_integra(copias))
    return salida


def resumen_texto(estado: dict, disponibilidad: dict | None = None) -> str:
    lineas = [f"KUKIA - estado de webs y Core ({datetime.now().strftime('%d-%m-%Y %H:%M')})"]
    for nombre, d in estado.get("servicios", {}).items():
        marca = "OK  " if d.get("ok") else "CAIDO"
        lineas.append(f"  [{marca}] {nombre:13} {d.get('detalle', '')}")
    if disponibilidad:
        lineas.append("")
        lineas.append(f"Disponibilidad de hoy (avisa por debajo del "
                      f"{DISPONIBLE_MIN:g} %):")
        for nombre, s in sorted(disponibilidad.items()):
            if not s.get("observado") or nombre in NO_SON_DISPONIBILIDAD:
                continue
            disp = s.get("disponibilidad")
            valor = "sin datos" if disp is None else _pct(disp)
            if disp is not None and disp < DISPONIBLE_MIN:
                valor += "  << por debajo del minimo"
            lineas.append(f"  {nombre:14} {valor:>10}   {s.get('caidas', 0)} caida(s), "
                          f"{_minutos(s.get('caido'))} caido")
    return "\n".join(lineas) + "\n"


def _minutos(seg) -> str:
    seg = int(seg or 0)
    if seg < 60:
        return f"{seg} s"
    return f"{seg // 60} min"


def disponibilidad_hoy(ahora: datetime | None = None, eventos: list | None = None) -> dict:
    """{servicio: {disponibilidad, caido, observado, caidas}} de HOY, lo medido.

    La cuenta la hace tools/incidencias.py, que es quien sabe leer los dos logs
    y descontar los huecos (PC apagado). Si algo falla se devuelve {}: la
    disponibilidad es un extra, no puede tumbar la pasada del vigilante.
    """
    ahora = ahora or datetime.now()
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import incidencias as inc
        if eventos is None:
            eventos = inc.lee(inc.LOG_SERVICIOS) + inc.lee(inc.LOG_WEB)
        desde = datetime.combine(ahora.date(), datetime.min.time())
        dias = inc.resumen(eventos, desde, ahora)
    except Exception:
        return {}
    return dias.get(ahora.strftime("%Y-%m-%d"), {})


def _avisadas() -> dict:
    """{dia: [servicios ya avisados]} del fichero de marcas."""
    try:
        return json.loads(AVISADAS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _marca_avisado(dia: str, servicios: list) -> None:
    """Apunta los servicios avisados hoy (y tira los dias viejos)."""
    d = _avisadas()
    lista = d.setdefault(dia, [])
    for s in servicios:
        if s not in lista:
            lista.append(s)
    for viejo in sorted(d)[:-14]:
        d.pop(viejo, None)
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        AVISADAS.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    except Exception:
        pass


def _pct(x: float) -> str:
    return f"{x:.1f}".replace(".", ",") + " %"


def linea_disponibilidad(datos: dict) -> str:
    """'surge 89,6 % · core-local 99,8 %' con lo que tenga datos hoy."""
    trozos = []
    for nombre, s in sorted(datos.items()):
        if not s.get("observado"):
            continue
        disp = s.get("disponibilidad")
        trozos.append(f"{nombre} {'-' if disp is None else _pct(disp)}")
    return " · ".join(trozos)


def revisa_disponibilidad(ahora: datetime | None = None, eventos: list | None = None,
                          avisar: bool = True) -> list:
    """Avisos [(titulo, texto)] de lo que hoy esta por debajo del minimo.

    Un aviso por servicio y dia como mucho: si `surge` se queda en 98 % a las
    14:00, no se repite a las 14:05, pero si por la tarde cae `github`, ese
    servicio si avisa (se lleva la cuenta por separado).
    """
    ahora = ahora or datetime.now()
    dia = ahora.strftime("%Y-%m-%d")
    datos = disponibilidad_hoy(ahora, eventos)
    ya = _avisadas().get(dia, [])
    bajos = []
    for servicio, s in sorted(datos.items()):
        if servicio in ya or servicio in NO_SON_DISPONIBILIDAD:
            continue
        if float(s.get("observado") or 0) < DISPONIBLE_OBSERVADO_MIN:
            continue          # muy pocos minutos medidos: aun no dice nada
        disp = s.get("disponibilidad")
        if disp is None or disp >= DISPONIBLE_MIN:
            continue
        bajos.append((servicio, s, float(disp)))
    if not bajos:
        return []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import incidencias as inc
        hhmm = inc.hhmm
    except Exception:
        def hhmm(seg):
            seg = int(seg or 0)
            return f"{seg // 60} min"
    bajos.sort(key=lambda x: x[2])
    nombre, s, disp = bajos[0]
    piezas = [f"{n} {_pct(d)} ({hhmm(s2.get('caido'))} caido de "
              f"{hhmm(s2.get('observado'))} mirados)" for n, s2, d in bajos[:4]]
    titulo = f"KUKIA: {nombre} al {_pct(disp)} hoy"
    texto = ("; ".join(piezas)
             + f". Minimo: {DISPONIBLE_MIN:g} %. Detalle: VER_INCIDENCIAS.cmd")
    # La marca va SOLO si se va a avisar de verdad: una pasada con --sin-aviso
    # (o una prueba) no debe gastar el aviso del dia.
    if avisar:
        _marca_avisado(dia, [n for n, _, _ in bajos])
        log(f"disponibilidad por debajo del {DISPONIBLE_MIN:g} %: {texto}")
    return [(titulo, texto)]


def una_vez(avisar: bool = True, core_local: str = CORE_LOCAL,
            copias: Path = COPIA_CLAVES) -> int:
    antes = estado_previo().get("servicios", {})
    resultados = comprueba(core_local, copias)
    ahora = {}
    avisos = []

    for r in resultados:
        previo = antes.get(r["nombre"], {})
        if r["ok"]:
            fallos = 0
        elif r.get("inmediato"):
            # Ya se reintento dentro de la pasada (mira_sitio): no hace falta
            # otra pasada mala para darlo por caido y avisar.
            fallos = FALLOS_PARA_CAIDO
        else:
            fallos = int(previo.get("fallos", 0)) + 1
        caido = fallos >= FALLOS_PARA_CAIDO
        ahora[r["nombre"]] = {"ok": r["ok"], "detalle": r["detalle"], "fallos": fallos,
                              "visto": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        # Solo se avisa al CAMBIAR: si antes estaba bien y ahora lleva 2 fallos,
        # o si antes estaba caido y ahora responde.
        # "Estaba bien" = no se habia avisado ya de que estaba caido. Se mira
        # solo el contador, no el ok: en la segunda pasada mala el ok anterior
        # ya es False y si se mirara eso no se avisaria nunca de la caida.
        estaba_bien = int(previo.get("fallos", 0)) < FALLOS_PARA_CAIDO
        if not r["ok"] and caido and estaba_bien:
            # Al movil llega mejor la palabra exacta que un "CAIDO" para todo.
            estado_malo = {"copia-claves": "ATRASADA",
                           "copia-integra": "CORRUPTA"}.get(r["nombre"], "CAIDO")
            avisos.append((f"KUKIA: {r['nombre']} {estado_malo}", r["detalle"]))
        # "Ha vuelto" solo si de verdad se llego a avisar de que estaba caido:
        # un fallo suelto de una pasada no genera ni caida ni vuelta.
        if (r["ok"] and previo.get("ok") is False
                and int(previo.get("fallos", 0)) >= FALLOS_PARA_CAIDO):
            avisos.append((f"KUKIA: {r['nombre']} ha vuelto", r["detalle"]))

    # Disponibilidad del dia: se mira DESPUES de la pasada (asi el propio log de
    # esta pasada ya cuenta) y se avisa una sola vez por servicio y dia.
    disp = disponibilidad_hoy()
    try:
        avisos.extend(revisa_disponibilidad(avisar=avisar))
    except Exception as e:
        log(f"no pude calcular la disponibilidad de hoy ({type(e).__name__}: {e})")

    estado = {"ultima": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "servicios": ahora, "disponibilidad_hoy": disp}
    guarda_estado(estado)
    try:
        RESUMEN.write_text(resumen_texto(estado, disp), encoding="utf-8")
    except Exception:
        pass

    caidos = [n for n, d in ahora.items() if not d["ok"] and d["fallos"] >= FALLOS_PARA_CAIDO]
    log("pasada: " + ", ".join(f"{n}={'ok' if d['ok'] else 'fallo(' + str(d['fallos']) + ')'}"
                               for n, d in ahora.items()))
    if disp:
        log("hoy: " + linea_disponibilidad(disp))
    for titulo, texto in avisos:
        if avisar:
            avisa(titulo, texto)
        log("AVISO: " + titulo + " -> " + texto)
    return 1 if caidos else 0


def pythonw() -> str:
    candidato = Path(sys.executable).with_name("pythonw.exe")
    if candidato.exists():
        return str(candidato)
    return shutil.which("pythonw") or "pythonw.exe"


def corre_oculto(argv: list, seg: float = 60.0) -> tuple[int, str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=seg,
                           creationflags=SIN_VENTANA)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 1, f"{type(e).__name__}: {e}"


def instalar() -> int:
    if os.name != "nt":
        print("[ERROR] esto es para Windows (Programador de tareas)")
        return 2
    script = RAIZ / "tools" / "vigilante_servicios.py"
    orden = f'"{pythonw()}" "{script}" --una-vez'
    codigo, salida = corre_oculto(["schtasks", "/Create", "/SC", "MINUTE", "/MO", str(MINUTOS),
                                   "/TN", TAREA, "/TR", orden, "/F"])
    if codigo != 0:
        print("[ERROR] no se pudo crear la tarea:")
        print("        " + salida.strip()[:400])
        print("        Si pide permisos, abre una consola como administrador y repite.")
        return 1
    print(f"tarea '{TAREA}' creada: cada {MINUTOS} min, sin ventana")
    print(f"  {orden}")
    print(f"  quitar:  python tools/vigilante_servicios.py --quitar")
    una_vez()
    return 0


def quitar() -> int:
    codigo, salida = corre_oculto(["schtasks", "/Delete", "/TN", TAREA, "/F"])
    if codigo != 0:
        print(f"[AVISO] no habia tarea o no se pudo borrar: {salida.strip()[:200]}")
        return 0
    print(f"tarea '{TAREA}' borrada.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Vigila las webs y el Core de KUKIA")
    ap.add_argument("--una-vez", action="store_true", help="una pasada y salir")
    ap.add_argument("--estado", action="store_true", help="ultimo estado guardado")
    ap.add_argument("--probar-aviso", action="store_true", help="lanzar un aviso de prueba")
    ap.add_argument("--instalar", action="store_true", help="tarea de Windows cada 5 min")
    ap.add_argument("--quitar", action="store_true", help="quitar la tarea")
    ap.add_argument("--sin-aviso", action="store_true", help="mirar sin notificar")
    ap.add_argument("--cada", type=int, default=MINUTOS, help="minutos entre pasadas en bucle")
    ap.add_argument("--core", default=CORE_LOCAL, help="URL del Core local a mirar")
    ap.add_argument("--copias", default=str(COPIA_CLAVES),
                    help="carpeta de la copia de claves (por defecto N:/CLAVES_KUKIA)")
    ap.add_argument("--verificar-copia", action="store_true",
                    help="revalidar ya la ultima copia (hashes + clave), sin esperar al dia")
    args = ap.parse_args()

    if args.verificar_copia:
        e = estado_copia_integra(Path(args.copias), forzar=True)
        print(("OK   " if e["ok"] else "MAL  ") + e["detalle"])
        return 0 if e["ok"] else 1

    if args.instalar:
        return instalar()
    if args.quitar:
        return quitar()
    if args.probar_aviso:
        ok = avisa("KUKIA: aviso de prueba", "Si ves esto, el vigilante avisa bien.")
        print("aviso enviado" if ok else "no se pudo lanzar el aviso (revisa el log)")
        return 0 if ok else 1
    if args.estado:
        d = estado_previo()
        if not d:
            print("todavia no hay ninguna pasada guardada")
            return 0
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0
    if args.una_vez:
        return una_vez(not args.sin_aviso, args.core, Path(args.copias))

    # Bucle sin fin (por defecto).
    log(f"vigilante arrancado: cada {args.cada} min")
    try:
        while True:
            una_vez(True, args.core, Path(args.copias))
            time.sleep(max(60, args.cada * 60))
    except KeyboardInterrupt:
        log("vigilante detenido a mano")
        return 0


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
