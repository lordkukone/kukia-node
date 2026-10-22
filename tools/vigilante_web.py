"""Vigilante del sitio: avisa (y empuja a los buscadores) cuando vuelva a estar vivo.

PARA QUE
    El 21-10-2026 el host del sitio (Freebuff Sites) se quedo en 503 y con el
    se cayo todo lo indexable: mientras no sirva 200, ningun buscador puede
    indexar KUKIA. Este vigilante mira cada pocos minutos, sin abrir NINGUNA
    ventana, y en cuanto el sitio vuelve:

      1. comprueba que lo que sirve ya lleva el SEO generado (la marca
         `<!-- kukia-seo -->`); si no, avisa de que falta publicar,
      2. si ya esta publicado, manda las URLs a IndexNow (Bing, Yandex,
         Seznam, Naver) una sola vez,
      3. avisa con una notificacion de Windows (esquina, no ventana) y lo deja
         escrito en ESTADO_BUSCADORES.txt,
      4. UNA VEZ AL DIA mira si KUKIA ya aparece en Google y en Bing, y avisa el
         dia que sale por primera vez (una sola vez por motor).

    No publica el sitio: eso es un despliegue y se hace a proposito, no solo.

SIN VENTANAS
    Se ejecuta con `pythonw.exe` (sin consola) desde el Programador de tareas,
    y todo hijo que lanza (powershell para la notificacion) lleva
    CREATE_NO_WINDOW. En la practica: no aparece nada en pantalla. La
    alternativa que tenia el repo antes (powershell sin esa bandera) abria una
    ventana negra cada pocos segundos; ver AUDITORIA_FINAL_2026-10-21.md.

USO
    python tools/vigilante_web.py                 una comprobacion (lo que hace la tarea)
    python tools/vigilante_web.py --estado        que hay montado y cuando miro
    python tools/vigilante_web.py --instalar      tarea de Windows cada 10 min, sin ventana
    python tools/vigilante_web.py --quitar        quitar la tarea
    python tools/vigilante_web.py --probar-aviso  ver como es la notificacion
    python tools/vigilante_web.py --no-enviar     mirar y avisar, sin tocar IndexNow
    python tools/vigilante_web.py --indexado      mirar AHORA si ya salimos en Google/Bing
                                                  (lo normal es que lo mire solo, 1 vez al dia)
    python tools/vigilante_web.py --indexado --dominio es.wikipedia.org --solo-ver
                                                  probar la deteccion contra un dominio que
                                                  sale seguro, sin guardar ni avisar

SALIDA
    logs/vigilante_web.log        historial (con hora)
    logs/vigilante_web.json       estado: ultimo chequeo, estado visto, avisado/enviado
    ESTADO_BUSCADORES.txt         una linea legible con lo ultimo que se vio
"""
import argparse
import base64
import json
import os
import re
import subprocess
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DOCS = RAIZ / "docs"
LOGS = RAIZ / "logs"
LOG = LOGS / "vigilante_web.log"
ESTADO = LOGS / "vigilante_web.json"
AVISO = RAIZ / "ESTADO_BUSCADORES.txt"
CLAVE_INDEXNOW = RAIZ / "indexnow_key.txt"
TAREA = "KUKIA Vigilante Web"
MINUTOS = 10
SIN_VENTANA = 0x08000000 if os.name == "nt" else 0

# --- buscadores -----------------------------------------------------------
# Google y Bing no tienen un "¿esta mi pagina dentro?" gratis y sin cuenta.
# Google sirve a los programas una pagina vacia que se rellena con JavaScript
# (comprobado el 22-10: 92 KB y ni un resultado) y Bing, cuando le pregunta un
# robot, devuelve resultados que no son de la consulta (a `site:es.wikipedia.org`
# contesto con tiendas de grava). Asi que:
#
#   - BING se pregunta a DUCKDUCKGO, que si contesta a un programa y usa el
#     indice de Bing: el mismo indice por otra puerta.
#   - GOOGLE se pregunta a la API DE INSPECCION DE URLS de Search Console (la
#     palabra de Google, no una suposicion) cuando existe
#     searchconsole_cuenta.json, la clave de una cuenta de servicio metida como
#     usuario de la propiedad. Sin ese fichero se intenta la web, que a un
#     programa le da una pagina vacia, y se dice "sin comprobar".
#     (El Custom Search JSON API, que era la via facil, esta cerrado a clientes
#     nuevos desde el 20-01-2026: por ahi ya no se puede empezar.)
#
# La diferencia entre "no sales" y "no se ha podido comprobar" es el punto
# entero de esto: decir "no salimos" cuando en realidad no nos han dejado
# preguntar es una mentira que se paga, porque dejas de mandar avisos creyendo
# que no hacen falta. Por eso cada consulta lleva un CONTROL (el `site:` de un
# dominio que sale seguro): si el control no devuelve nada, el motor nos esta
# cerrando la puerta y no se saca ninguna conclusion.
UA_NAVEGADOR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
CONTROL = "es.wikipedia.org"
NO_HAY = ("did not match", "no ha encontrado", "no se han encontrado",
          "no results", "no hay resultados", "comprueba la ortograf")
GOOGLE_CUENTA = RAIZ / "searchconsole_cuenta.json"   # clave de la cuenta de servicio
GOOGLE_SITIO = RAIZ / "google_site.txt"              # opcional, ver _sitio_de_google
GOOGLE_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
API_INSPECCION = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
API_TOKEN = "https://oauth2.googleapis.com/token"
NOMBRE = {"google": "Google", "bing": "Bing"}

# Notificacion de Windows (toast). Va por PowerShell en modo oculto; si el
# sistema no la admite, se ignora: el aviso bueno es el fichero y el log.
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
    print(linea, flush=True)
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except Exception:
        pass


def leer_estado() -> dict:
    try:
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    except Exception:
        return {}


def guardar_estado(d: dict) -> None:
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        ESTADO.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    except Exception:
        pass


def base_publica() -> str:
    """URL publica del sitio: la del sitemap generado (una sola fuente de verdad)."""
    try:
        texto = (DOCS / "sitemap.xml").read_text(encoding="utf-8")
        ini = texto.index("<loc>") + 5
        url = texto[ini:texto.index("</loc>", ini)].strip()
        partes = url.split("/")
        return "/".join(partes[:3])
    except Exception:
        return "https://kukia-network.freebuff.page"


def pide(url: str, tiempo: float = 20.0) -> tuple:
    pet = urllib.request.Request(url, headers={"User-Agent": "kukia-vigilante/1.0",
                                               "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(pet, timeout=tiempo) as r:
            return r.status, r.read(600000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def clave() -> str:
    try:
        return CLAVE_INDEXNOW.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def revisa(base: str) -> tuple:
    """('listo'|'sin_publicar'|'sin_clave'|'caido', detalle)."""
    codigo, cuerpo = pide(f"{base}/")
    if codigo != 200:
        return "caido", f"HTTP {codigo or 'sin respuesta'}"
    if "<!-- kukia-seo -->" not in cuerpo:
        return "sin_publicar", "sirve 200 pero sin el SEO generado"
    k = clave()
    if not k:
        return "sin_clave", "falta indexnow_key.txt"
    codigo_k, contenido = pide(f"{base}/{k}.txt")
    if codigo_k != 200 or k not in contenido:
        return "sin_clave", f"la clave de IndexNow no se sirve (HTTP {codigo_k})"
    return "listo", ""


def avisa(titulo: str, texto: str) -> bool:
    """Notificacion del sistema, sin ventana. False si no se pudo."""
    if os.name != "nt":
        return False
    try:
        env = dict(os.environ, KUKIA_TITULO=titulo[:64], KUKIA_TEXTO=texto[:256])
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-Command", TOAST],
            capture_output=True, timeout=25, env=env, creationflags=SIN_VENTANA)
        if r.returncode != 0:
            log(f"aviso del sistema no disponible (codigo {r.returncode})")
            return False
        return True
    except Exception as e:
        log(f"aviso del sistema no disponible ({type(e).__name__})")
        return False


def escribe_aviso(texto: str) -> None:
    try:
        aviso = ("KUKIA - estado para buscadores\n"
                 f"Actualizado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                 f"{texto}\n\n")
        extra = linea_indexado(leer_estado().get("indexado") or {})
        if extra:
            aviso += f"{extra}\n\n"
        aviso += ("Comprobar a mano:  python tools\\indexar.py --estado\n"
                  "Empujar a IndexNow: python tools\\indexar.py --enviar\n")
        AVISO.write_text(aviso, encoding="utf-8")
    except Exception:
        pass


def _pide(url: str, tiempo: float = 25.0) -> tuple:
    """Como pide(), pero con cara de navegador: los buscadores no atienden a
    'kukia-vigilante/1.0'."""
    pet = urllib.request.Request(url, headers={
        "User-Agent": UA_NAVEGADOR,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(pet, timeout=tiempo) as r:
            return r.status, r.read(900000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def _del_mismo_sitio(url: str, host: str) -> bool:
    """¿Ese enlace es del dominio (o de un subdominio)?"""
    try:
        h = (urllib.parse.urlsplit(url).hostname or "").lower()
    except Exception:
        return False
    host = (host or "").lower()
    return bool(h and host) and (h == host or h.endswith("." + host))


def _enlaces_ddg(cuerpo: str) -> list:
    urls = []
    for etiqueta in re.findall(r'<a[^>]*class="result__a"[^>]*>', cuerpo):
        m = re.search(r'href="([^"]+)"', etiqueta)
        if not m:
            continue
        destino = m.group(1)
        dentro = re.search(r"uddg=([^&]+)", destino)
        urls.append(urllib.parse.unquote(dentro.group(1)) if dentro else destino)
    return urls


def _ddg(consulta: str, intentos: int = 2) -> tuple:
    """(['url', ...], nota). Lista vacia y nota si no se pudo leer la respuesta.

    DuckDuckGo no cobra ni pide cuenta, pero cuando se le pregunta mucho
    (o deprisa) contesta 202 con una pagina vacia. Eso NO es "no hay
    resultados": es "vuelve luego". Se reintenta una vez y, si sigue igual, se
    devuelve la nota para que la llamada lo cuente como "sin comprobar".
    """
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(consulta)
    nota = ""
    for n in range(max(1, intentos)):
        codigo, cuerpo = _pide(url)
        if codigo == 200:
            urls = _enlaces_ddg(cuerpo)
            if urls:
                return urls, ""
            if any(x in cuerpo.lower() for x in NO_HAY):
                return [], ""
            return [], "DuckDuckGo devolvio una pagina sin resultados legibles"
        nota = f"DuckDuckGo respondio HTTP {codigo or 'sin respuesta'}"
        if codigo not in (202, 429, 503) or n == intentos - 1:
            break
        time.sleep(12.0)  # 202 = "vuelve luego": una pausa y otra vez
    return [], nota


def comprueba_bing(host: str) -> tuple:
    """('sale'|'no'|'sin_comprobar', detalle, url). Indice de Bing, preguntado a
    DuckDuckGo porque Bing no contesta a un programa."""
    control, nota = _ddg("site:" + CONTROL)
    if not control:
        return "sin_comprobar", f"control {CONTROL}: {nota or 'sin resultados'}", ""
    time.sleep(1.0)  # dos consultas seguidas sin pausa se cortan
    urls, nota = _ddg("site:" + host)
    if nota:
        return "sin_comprobar", nota, ""
    for u in urls:
        if _del_mismo_sitio(u, host):
            return "sale", "", u
    return "no", "", ""


def _b64url(datos: bytes) -> str:
    """Base64 de URL, sin relleno: lo que pide un JWT."""
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode()


def _jwt_servicio(cuenta: dict, cuando: int | None = None) -> str:
    """JWT firmado (RS256) para pedirle el token a Google, sin librerias de mas.

    Es el baile que pide una cuenta de servicio: se firma un documento con su
    clave privada y Google devuelve un token de un rato a cambio.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    ahora = int(time.time()) if cuando is None else int(cuando)
    cabecera = {"alg": "RS256", "typ": "JWT"}
    claims = {"iss": cuenta["client_email"], "scope": GOOGLE_SCOPE,
              "aud": API_TOKEN, "iat": ahora, "exp": ahora + 3600}
    mensaje = (_b64url(json.dumps(cabecera, separators=(",", ":")).encode())
               + "."
               + _b64url(json.dumps(claims, separators=(",", ":")).encode()))
    llave = serialization.load_pem_private_key(cuenta["private_key"].encode(),
                                               password=None)
    firma = llave.sign(mensaje.encode(), padding.PKCS1v15(), hashes.SHA256())
    return mensaje + "." + _b64url(firma)


def _post_json(url: str, datos: dict, cabeceras: dict | None = None,
               tiempo: float = 25.0) -> tuple:
    """POST con JSON: (codigo, cuerpo). No lanza excepciones."""
    cab = {"User-Agent": UA_NAVEGADOR, "Content-Type": "application/json",
           "Accept": "application/json"}
    cab.update(cabeceras or {})
    pet = urllib.request.Request(url, data=json.dumps(datos).encode(),
                                 headers=cab, method="POST")
    try:
        with urllib.request.urlopen(pet, timeout=tiempo) as r:
            return r.status, r.read(400000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read(400000).decode("utf-8", "replace")
        except Exception:
            return e.code, ""
    except Exception:
        return 0, ""


def _token_google(cuenta: dict) -> tuple:
    """(token, nota)."""
    try:
        afirmacion = _jwt_servicio(cuenta)
    except Exception as e:
        return "", f"no se pudo firmar el JWT: {type(e).__name__}: {e}"
    codigo, cuerpo = _post_json(API_TOKEN, {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": afirmacion})
    if codigo != 200:
        motivo = ""
        try:
            motivo = (json.loads(cuerpo).get("error_description") or "")[:160]
        except Exception:
            pass
        return "", (f"Google no dio token (HTTP {codigo or 'sin respuesta'})"
                     + (f": {motivo}" if motivo else ""))
    try:
        token = json.loads(cuerpo).get("access_token") or ""
    except Exception:
        token = ""
    if not token:
        return "", "la respuesta del token no traia access_token"
    return token, ""


def _sitio_de_google(host: str) -> str:
    """La propiedad TAL CUAL esta en Search Console.

    Si se dio de alta como «prefijo de URL», es https://host/. Si es una
    propiedad de «dominio», Google la llama sc-domain:host y hay que ponerlo en
    google_site.txt; si no coincide, la API contesta 403 y se ve en el detalle.
    """
    try:
        texto = GOOGLE_SITIO.read_text(encoding="utf-8").strip()
        if texto:
            return texto
    except Exception:
        pass
    return f"https://{host}/"


def _google_inspeccion(host: str) -> tuple:
    """Pregunta a la API de inspeccion de URLs de Search Console."""
    try:
        cuenta = json.loads(GOOGLE_CUENTA.read_text(encoding="utf-8"))
    except Exception as e:
        return "sin_comprobar", f"no se pudo leer searchconsole_cuenta.json ({type(e).__name__})", ""
    if not cuenta.get("client_email") or not cuenta.get("private_key"):
        return ("sin_comprobar",
                "searchconsole_cuenta.json no parece una clave de cuenta de servicio", "")
    sitio = _sitio_de_google(host)
    url = f"https://{host}/"
    token, nota = _token_google(cuenta)
    if not token:
        return "sin_comprobar", nota, ""
    codigo, cuerpo = _post_json(API_INSPECCION,
                                {"inspectionUrl": url, "siteUrl": sitio,
                                 "languageCode": "es"},
                                {"Authorization": f"Bearer {token}"})
    if codigo != 200:
        motivo = ""
        try:
            motivo = (json.loads(cuerpo).get("error") or {}).get("message", "")[:160]
        except Exception:
            pass
        return ("sin_comprobar",
                f"Search Console respondio HTTP {codigo or 'sin respuesta'}"
                + (f": {motivo}" if motivo else ""), "")
    try:
        estado = ((json.loads(cuerpo).get("inspectionResult") or {})
                  .get("indexStatusResult") or {})
    except Exception:
        return "sin_comprobar", "Search Console no devolvio JSON", ""
    veredicto = (estado.get("verdict") or "").upper()
    cobertura = estado.get("coverageState") or ""
    detalle = (veredicto or "sin veredicto") + (f" · {cobertura}" if cobertura else "")
    if veredicto in ("PASS", "PARTIAL"):
        return "sale", detalle, url
    if veredicto == "FAIL":
        return "no", detalle, ""
    # NEUTRAL o vacio: Google no se moja (pagina recien descubierta, sin rastrear).
    # No se convierte en "no": eso apagaria los avisos sin motivo.
    return "sin_comprobar", detalle or "Google no da veredicto", ""


def _google_web(host: str) -> tuple:
    """Sin cuenta de servicio: la web de Google, que a un programa le da vacio."""
    codigo, cuerpo = _pide("https://www.google.com/search?hl=es&num=10&q="
                           + urllib.parse.quote("site:" + host))
    if codigo != 200:
        return "sin_comprobar", f"Google respondio HTTP {codigo or 'sin respuesta'}", ""
    enlaces = re.findall(r'/url\?q=(https?://[^&"]+)', cuerpo)
    if not enlaces:
        return ("sin_comprobar",
                "Google no sirve resultados a un programa: se mira en Search "
                "Console -> Inspeccion de URL (ver SEO_BUSCADORES.md)",
                "")
    for u in enlaces:
        if _del_mismo_sitio(u, host):
            return "sale", "", u
    return "no", "", ""


def comprueba_google(host: str) -> tuple:
    """('sale'|'no'|'sin_comprobar', detalle, url)."""
    if GOOGLE_CUENTA.exists():
        return _google_inspeccion(host)
    return _google_web(host)


def _consulta(motor: str, host: str) -> tuple:
    return comprueba_google(host) if motor == "google" else comprueba_bing(host)


def linea_indexado(idx: dict) -> str:
    """Una linea legible para ESTADO_BUSCADORES.txt (vacia si aun no se miro)."""
    if not idx or not idx.get("dia"):
        return ""
    partes = []
    for motor in ("google", "bing"):
        estado = idx.get(motor, "")
        if estado == "sale":
            desde = idx.get("primera_" + motor) or idx["dia"]
            partes.append(f"{NOMBRE[motor]} SALE desde el {desde}")
        elif estado == "no":
            partes.append(f"{NOMBRE[motor]} todavia no")
        else:
            partes.append(f"{NOMBRE[motor]} sin comprobar")
    return f"Buscadores ({idx['dia']}): " + " | ".join(partes)


def _bloqueo_pasajero(antes: dict) -> bool:
    """¿Se quedo algo sin comprobar por algo que puede cambiar solo?

    El bloqueo de Bing/DuckDuckGo lo es. El de Google sin cuenta de servicio NO:
    si no hay clave, Google va a seguir sin contestar manana igual, y reintentar
    tres veces al dia solo gasta consultas (y cabrea mas a DuckDuckGo, que es el
    que nos hace falta).
    """
    if antes.get("bing") == "sin_comprobar":
        return True
    if antes.get("google") == "sin_comprobar" and GOOGLE_CUENTA.exists():
        return True
    return False


def _toca_mirar(antes: dict, hoy: str, ahora) -> bool:
    """¿Toca preguntar? Una vez al dia; y si nos bloquearon, hasta tres veces al
    dia separadas dos horas (un 202 de DuckDuckGo no puede costarnos el dia)."""
    if antes.get("dia") != hoy:
        return True
    if not _bloqueo_pasajero(antes):
        return False
    if int(antes.get("intentos") or 0) >= 3:
        return False
    try:
        ultimo = datetime.strptime(antes.get("cuando", ""), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return True
    return (ahora - ultimo).total_seconds() >= 2 * 3600


def revisa_indexado(base: str, previo: dict, forzar: bool = False,
                    consultar=None, avisar=None) -> dict:
    """Mira una vez al dia si el sitio ya aparece en Google y en Bing.

    `consultar(motor, host)` y `avisar(titulo, texto)` se pueden cambiar para
    probar la logica sin red ni notificaciones: ver
    tools/prueba_vigilante_indexado.py.
    """
    ahora = datetime.now()
    hoy = ahora.strftime("%Y-%m-%d")
    antes = dict(previo.get("indexado") or {})
    if not forzar and not _toca_mirar(antes, hoy, ahora):
        return antes  # ya se miro hoy (y no hay nada pendiente de reintentar)
    host = (urllib.parse.urlsplit(base).hostname or "").lower()
    if not host:
        return antes
    consultar = consultar or _consulta
    avisar = avisar or avisa
    intentos = (int(antes.get("intentos") or 0) + 1) if antes.get("dia") == hoy else 1
    nuevo = {"dia": hoy, "host": host, "intentos": intentos,
             "cuando": ahora.strftime("%Y-%m-%d %H:%M:%S")}
    for motor in ("google", "bing"):
        try:
            estado, detalle, url = consultar(motor, host)
        except Exception as e:
            estado, detalle, url = "sin_comprobar", f"{type(e).__name__}: {e}", ""
        if estado not in ("sale", "no", "sin_comprobar"):
            estado = "sin_comprobar"
        nuevo[motor] = estado
        nuevo["detalle_" + motor] = detalle
        nuevo["url_" + motor] = url
        primera = antes.get("primera_" + motor, "")
        if primera:
            nuevo["primera_" + motor] = primera
        elif estado == "sale":
            nuevo["primera_" + motor] = hoy
        if estado == "sale" and not antes.get("avisado_" + motor):
            # Lo que se pidio: el dia que sale por primera vez, una vez.
            nuevo["avisado_" + motor] = True
            log(f"AVISO: KUKIA ya sale en {NOMBRE[motor]} ({url or host})")
            avisar(f"KUKIA ya sale en {NOMBRE[motor]}",
                   f"{url or host} ya aparece en {NOMBRE[motor]}. "
                   f"Comprobado el {hoy}.")
        log(f"indexado {NOMBRE[motor]}: {estado}"
            + (f" ({detalle})" if detalle else "")
            + (f" -> {url}" if url and estado == "sale" else ""))
    return nuevo


def una_vez(base: str, enviar: bool, forzar_indexado: bool = False) -> int:
    estado_prev = leer_estado()
    try:
        estado_prev = dict(estado_prev, indexado=revisa_indexado(
            base, estado_prev, forzar=forzar_indexado))
    except Exception as e:
        log(f"no se pudo comprobar si ya salimos en los buscadores "
            f"({type(e).__name__}: {e})")
    visto, detalle = revisa(base)
    anterior = estado_prev.get("visto", "")
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M")
    nuevo = dict(estado_prev, visto=visto, detalle=detalle, ultimo=ahora, base=base)
    log(f"{base} -> {visto}" + (f" ({detalle})" if detalle else ""))

    # `avisado_para` guarda DE QUE estado se aviso ya, no un si/no: asi una
    # transicion nueva (p.ej. sin_publicar -> listo) vuelve a avisar una vez, y
    # no se repite en cada vuelta mientras el estado no cambie.
    avisado_de = estado_prev.get("avisado_para", "")

    if visto == "caido":
        # Mientras este caido no se avisa nada: ya lo sabe, y seria un aviso
        # cada diez minutos. Solo interesa la transicion.
        nuevo["avisado_para"] = "caido"
        guardar_estado(nuevo)
        escribe_aviso(f"El sitio esta caido ({detalle}).\n"
                      f"Ultimo chequeo: {ahora}. No se puede indexar nada.")
        return 1

    if visto == "sin_publicar":
        if avisado_de != visto:
            avisa("KUKIA ya sirve, falta publicar",
                  "El host vuelve a responder, pero lo que sirve no lleva el SEO "
                  "nuevo. Publica y luego: python tools\\indexar.py --enviar")
            log("AVISO: el host sirvio pero sin el SEO generado; hay que publicar")
        nuevo["avisado_para"] = visto
        guardar_estado(nuevo)
        escribe_aviso("El host responde, pero sin el SEO nuevo: hay que publicar\n"
                      "la version de docs/ (desplegar_sitio.py + deploy).")
        return 1

    if visto == "sin_clave":
        if avisado_de != visto:
            avisa("KUKIA: falta la clave de IndexNow",
                  "El sitio sirve, pero /<clave>.txt no. Vuelve a publicar docs/.")
            log("AVISO: la clave de IndexNow no se sirve; IndexNow rechazaria el envio")
        nuevo["avisado_para"] = visto
        guardar_estado(nuevo)
        escribe_aviso(f"El sitio sirve, pero la clave de IndexNow no ({detalle}).")
        return 1

    # --- listo -----------------------------------------------------------
    if avisado_de != "listo":
        avisa("KUKIA ya esta sirviendo",
              "El sitio responde 200 con el SEO nuevo. Enviando el aviso a los "
              "buscadores...")
        nuevo["avisado_para"] = "listo"
        log("el sitio ha vuelto: 200 y con el SEO generado")

    if enviar and not nuevo.get("enviado"):
        log("mandando las URLs a IndexNow...")
        r = subprocess.run([sys.executable, str(RAIZ / "tools" / "indexar.py"),
                            "--enviar", "--base", base],
                           capture_output=True, text=True, timeout=180,
                           creationflags=SIN_VENTANA)
        salida = (r.stdout or "") + (r.stderr or "")
        for linea in salida.strip().splitlines()[-4:]:
            log(f"  indexar: {linea}")
        nuevo["enviado"] = ahora if r.returncode == 0 else ""
        if r.returncode == 0:
            avisa("KUKIA avisada a los buscadores",
                  "IndexNow acepto las URLs (Bing, Yandex, Seznam, Naver).")
    guardar_estado(nuevo)
    enviado = nuevo.get("enviado")
    escribe_aviso("El sitio responde 200 y con el SEO nuevo. IndexNow: "
                  + (f"enviado el {enviado}" if enviado
                     else "pendiente (se manda en la siguiente vuelta)"))
    return 0


def pythonw() -> str:
    """pythonw.exe: igual que python pero sin consola (lo que evita la ventana)."""
    candidato = Path(sys.executable).with_name("pythonw.exe")
    if candidato.exists():
        return str(candidato)
    return shutil.which("pythonw") or "pythonw.exe"


def corre_oculto(argv: list, seg: float = 60.0) -> tuple:
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
    script = RAIZ / "tools" / "vigilante_web.py"
    if not script.exists():
        print(f"[ERROR] falta {script}")
        return 1
    # pythonw: sin consola. Es la pieza que hace que no aparezca ninguna ventana.
    orden = f'"{pythonw()}" "{script}"'
    codigo, salida = corre_oculto([
        "schtasks", "/Create", "/SC", "MINUTE", "/MO", str(MINUTOS),
        "/TN", TAREA, "/TR", orden, "/F"])
    if codigo != 0:
        print("[ERROR] no se pudo crear la tarea:")
        print("        " + salida.strip()[:400])
        print("        Si pide permisos, abre una consola como administrador y repite.")
        return 1
    print(f"tarea '{TAREA}' creada: cada {MINUTOS} min, con pythonw (sin ventana)")
    print(f"  {orden}")
    estado()   # se informa, pero instalar bien es exito aunque el sitio siga caido
    return 0


def quitar() -> int:
    codigo, salida = corre_oculto(["schtasks", "/Delete", "/TN", TAREA, "/F"])
    if codigo != 0:
        print(f"[AVISO] no habia tarea o no se pudo borrar: {salida.strip()[:200]}")
        return 0
    print(f"tarea '{TAREA}' borrada. El vigilante ya no se ejecuta.")
    return 0


def estado() -> int:
    d = leer_estado()
    print(f"base:        {d.get('base') or base_publica()}")
    print(f"ultimo:      {d.get('ultimo') or '(nunca)'}")
    print(f"visto:       {d.get('visto') or '(nunca)'}"
          + (f"  ({d['detalle']})" if d.get("detalle") else ""))
    print(f"indexnow:    {d.get('enviado') or 'sin enviar'}")
    idx_linea = linea_indexado(d.get("indexado") or {})
    print(f"indexado:    {idx_linea or '(se mira una vez al dia; aun no)'}")
    if os.name == "nt":
        codigo, salida = corre_oculto(["schtasks", "/Query", "/TN", TAREA, "/FO", "LIST"])
        if codigo == 0:
            # schtasks responde en el idioma de Windows (aqui, espanol), asi que
            # no se buscan nombres exactos en ingles: se filtran las lineas que
            # importan y se ensenan tal cual. Menos bonito, pero no se rompe.
            claves = ("nombre de tarea", "taskname", "siguiente ejecuci", "next run",
                      "ultima ejecuci", "última ejecuci", "last run", "resultado",
                      "last result", "estado", "status")
            print(f"tarea:       {TAREA}")
            for linea in salida.splitlines():
                bajo = linea.lower()
                if ":" in linea and any(k in bajo for k in claves):
                    print("   " + linea.strip())
        else:
            print(f"tarea:       no instalada (python tools\\vigilante_web.py --instalar)")
    print(f"log:         {LOG.relative_to(RAIZ)}")
    return 0 if d.get("visto") == "listo" else 1


def solo_indexado(args) -> int:
    """--indexado: mirar ahora, sin esperar al dia siguiente."""
    base = ((f"https://{args.dominio}") if args.dominio
            else (args.base or base_publica())).rstrip("/")
    previo = {} if args.solo_ver else leer_estado()
    avisar = (lambda t, x: print(f"(aqui avisaria: {t} - {x})")) if args.solo_ver else None
    idx = revisa_indexado(base, previo, forzar=True, avisar=avisar)
    if not args.solo_ver:
        guardar_estado(dict(leer_estado(), indexado=idx))
    print(f"host:        {idx.get('host')}")
    for motor in ("google", "bing"):
        detalle = idx.get("detalle_" + motor) or ""
        print(f"{NOMBRE[motor]:<12} {idx.get(motor, '(sin dato)')}"
              + (f"  ({detalle})" if detalle else ""))
        if idx.get("url_" + motor):
            print(f"             {idx['url_' + motor]}")
    print("\n" + (linea_indexado(idx) or "(sin datos)"))
    if args.solo_ver:
        print("(modo prueba: no se ha guardado nada ni se ha avisado)")
    return 0 if (idx.get("google") == "sale" or idx.get("bing") == "sale") else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Vigila el sitio y avisa cuando vuelva a estar indexable.")
    ap.add_argument("--base", default="", help="URL publica (por defecto, la del sitemap)")
    ap.add_argument("--instalar", action="store_true", help="tarea de Windows cada 10 min")
    ap.add_argument("--quitar", action="store_true", help="quitar la tarea")
    ap.add_argument("--estado", action="store_true", help="que hay montado")
    ap.add_argument("--probar-aviso", action="store_true", help="ver la notificacion")
    ap.add_argument("--no-enviar", action="store_true", help="no mandar a IndexNow")
    ap.add_argument("--indexado", action="store_true",
                    help="mirar AHORA si ya salimos en Google/Bing (si no, se mira 1 vez al dia)")
    ap.add_argument("--dominio", default="",
                    help="con --indexado: comprobar otro dominio (pruebas)")
    ap.add_argument("--solo-ver", action="store_true",
                    help="con --indexado: no guarda estado ni avisa")
    args = ap.parse_args(argv)

    if args.instalar:
        return instalar()
    if args.quitar:
        return quitar()
    if args.estado:
        return estado()
    if args.indexado:
        return solo_indexado(args)
    if args.probar_aviso:
        ok = avisa("KUKIA - prueba de aviso",
                   "Asi se veria el aviso cuando el sitio vuelva a estar vivo.")
        print("notificacion enviada" if ok else "no se pudo mostrar la notificacion")
        return 0 if ok else 1
    return una_vez((args.base or base_publica()).rstrip("/"), not args.no_enviar)


if __name__ == "__main__":
    sys.exit(main())
