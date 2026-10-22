"""Meter KUKIA en los buscadores: comprobar, avisar y verificar el dominio.

QUE HACE
    --estado        dice, URL por URL, si el buscador PODRIA indexarla: codigo
                    HTTP, si robots.txt lo permite, si la pagina se declara
                    noindex, si la canonica apunta a la misma copia y si el
                    fichero de clave de IndexNow esta publicado.
    --enviar        avisa a IndexNow de que hay URLs nuevas. Un solo envio
                    cubre Bing, Yandex, Seznam, Naver y Yep (comparten API).
                    Si alguna URL no responde 200, NO envia nada: mandar una
                    URL rota solo ensena al buscador a no volver.
    --token-google  / --token-bing / --token-yandex
                    deja en docs/ el fichero de verificacion con el token que
                    te da cada panel. Google y Bing exigen demostrar que el
                    dominio es tuyo; esto evita el paso de "descarga el
                    fichero y colocalo".

POR QUE ASI
    Google retiro en 2023 los avisos por sitemap ping, y Bing/Yandex aceptan
    IndexNow (sin cuenta, sin clave secreta: la clave se publica a proposito en
    https://tu-dominio/<clave>.txt). Asi que:
      - Bing, Yandex, Seznam, Naver: un POST y ya.
      - Google, y los que beben de su indice (Brave...): Search Console, a mano.
      - DuckDuckGo, Ecosia, Qwant: no tienen alta propia; usan Bing o Google.

    La primera vez que un buscador rastrea un dominio nuevo tarda dias o
    semanas. Esto acelera el aviso, no garantiza la fecha.

USO
    python tools/indexar.py --estado
    python tools/indexar.py --estado --base https://kukia.tudominio.com
    python tools/indexar.py --enviar
    python tools/indexar.py --enviar --probar          (no manda nada)
    python tools/indexar.py --enviar --motor yandex
    python tools/indexar.py --token-google abc123...   (crea docs/googleabc123....html)
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from xml.etree import ElementTree

RAIZ = Path(__file__).resolve().parent.parent
DOCS = RAIZ / "docs"
CLAVE_FICHERO = RAIZ / "indexnow_key.txt"
UA = "kukia-indexar/1.0"

NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
ENDPOINTS = {
    "todos": "https://api.indexnow.org/indexnow",   # reparte a los suscritos
    "bing": "https://www.bing.com/indexnow",
    "yandex": "https://yandex.com/indexnow",
    "seznam": "https://search.seznam.cz/indexnow",
    "naver": "https://searchadvisor.naver.com/indexnow",
}


def pide(url: str, tiempo: float = 20.0) -> tuple:
    """(codigo, cabeceras, cuerpo). codigo 0 si no hubo respuesta."""
    pet = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(pet, timeout=tiempo) as r:
            return r.status, dict(r.headers), r.read(400000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), ""
    except Exception as e:
        return 0, {}, f"{type(e).__name__}: {e}"


def clave() -> str:
    try:
        k = CLAVE_FICHERO.read_text(encoding="utf-8").strip()
    except Exception:
        k = ""
    if not re.fullmatch(r"[0-9a-f]{16,128}", k or ""):
        raise SystemExit("[ERROR] falta indexnow_key.txt. Ejecuta antes:\n"
                         "        python publico\\generar_web.py")
    return k


def urls_sitemap(base: str = "") -> list:
    """URLs del sitemap publicado o, si no hay red, del sitemap en disco."""
    if base:
        codigo, _, cuerpo = pide(f"{base}/sitemap.xml")
        if codigo == 200:
            return _del_xml(cuerpo)
    try:
        return _del_xml((DOCS / "sitemap.xml").read_text(encoding="utf-8"))
    except Exception:
        return []


def _del_xml(texto: str) -> list:
    try:
        raiz = ElementTree.fromstring(texto)
    except Exception:
        return []
    return [e.text.strip() for e in raiz.findall("s:url/s:loc", NS) if e.text]


def host_de(url: str) -> str:
    return url.split("://", 1)[-1].split("/", 1)[0]


def prohibe_todo(robots_txt: str) -> bool:
    """True si el robots.txt le dice a cualquier bot que no entre en nada.

    Es lo que sirve Surge en *.surge.sh (Disallow: /). Se mira bloque a bloque
    porque un Disallow suelto pertenece al ultimo User-agent leido.
    """
    agente = ""
    for linea in (robots_txt or "").splitlines():
        limpia = linea.split("#", 1)[0].strip().lower()
        if limpia.startswith("user-agent:"):
            agente = limpia.split(":", 1)[1].strip()
        elif agente == "*" and limpia.startswith("disallow:"):
            if limpia.split(":", 1)[1].strip() == "/":
                return True
    return False


def revisa(url: str, clave_txt: str) -> dict:
    """Todo lo que un rastreador mira antes de indexar una pagina."""
    codigo, cab, cuerpo = pide(url)
    r = {"url": url, "codigo": codigo, "problemas": []}
    if codigo != 200:
        r["problemas"].append(f"la pagina responde {codigo or 'sin respuesta'}"
                              + (" (el host no esta sirviendo: 503 es host caido, "
                                 "no un fallo de la pagina)" if codigo == 503 else ""))
        return r
    bajo = cuerpo.lower()
    cabeza = bajo[:bajo.find("</head>")] if "</head>" in bajo else bajo[:20000]
    r["noindex"] = ('name="robots"' in cabeza and "noindex" in cabeza)
    if r["noindex"]:
        r["problemas"].append("la pagina se declara noindex")
    if "x-robots-tag" in {k.lower() for k in cab}:
        valor = next(v for k, v in cab.items() if k.lower() == "x-robots-tag")
        if "noindex" in valor.lower():
            r["problemas"].append(f"cabecera X-Robots-Tag: {valor}")
    m = re.search(r'rel="canonical" href="([^"]+)"', cabeza)
    r["canonica"] = m.group(1) if m else ""
    if not r["canonica"]:
        r["problemas"].append("no declara <link rel=canonical>")
    elif host_de(r["canonica"]) != host_de(url):
        r["problemas"].append(f"la canonica apunta a otro sitio ({r['canonica']}): "
                              f"Google indexara AQUEL, no este")
    r["jsonld"] = '<script type="application/ld+json">' in cabeza
    if not r["jsonld"]:
        r["problemas"].append("sin datos estructurados (JSON-LD)")
    r["titulo"] = bool(re.search(r"<title>[^<]{10,}", cabeza))
    if not r["titulo"]:
        r["problemas"].append("titulo ausente o demasiado corto")
    return r


def estado(base: str) -> int:
    clave_txt = clave()
    urls = urls_sitemap(base)
    if not urls:
        print("[ERROR] no encuentro URLs: ni el sitemap publicado ni docs/sitemap.xml")
        return 2
    host = host_de(base or urls[0])
    print(f"host:  {host}")
    print(f"clave: {clave_txt}  (IndexNow)")
    print()

    # robots.txt del host: sin esto, nada de lo demas importa.
    codigo_r, _, cuerpo_r = pide(f"https://{host}/robots.txt" if not base else f"{base}/robots.txt")
    if codigo_r != 200:
        print(f"robots.txt: {codigo_r or 'sin respuesta'}  [AVISO] el buscador pedira esto primero")
    else:
        print(f"robots.txt: 200" + ("  [FALLO] prohibe rastrear todo (Disallow: /)"
                                    if prohibe_todo(cuerpo_r) else "  permite rastrear"))
        if "sitemap:" in cuerpo_r.lower():
            print(f"            declara {re.search(r'(?im)^sitemap:\s*(\S+)', cuerpo_r).group(1)}")

    # fichero de clave: si no esta, IndexNow rechaza todos los envios.
    codigo_k, _, cuerpo_k = pide(f"{base or 'https://' + host}/{clave_txt}.txt")
    print(f"/{clave_txt}.txt: {codigo_k}"
          + ("" if codigo_k == 200 and clave_txt in cuerpo_k else "  [FALLO] la clave no se sirve: IndexNow lo rechazaria"))
    print()

    fallos = 0
    for url in urls:
        r = revisa(url, clave_txt)
        marca = "OK " if not r["problemas"] else "MAL"
        print(f"[{marca}] {url}  -> {r['codigo']}")
        for p in r["problemas"]:
            print(f"        - {p}")
        fallos += bool(r["problemas"])
    print()
    if fallos:
        print(f"{fallos} de {len(urls)} URL(s) no se pueden indexar tal cual.")
        print("Ningun buscador puede arreglar eso: primero el sitio tiene que servir 200.")
        return 1
    print("Todo listo para enviar. Siguiente paso:")
    print("  python tools\\indexar.py --enviar")
    print("  y el alta a mano en Google Search Console (ver SEO_BUSCADORES.md)")
    return 0


def enviar(base: str, motor: str, probar: bool) -> int:
    clave_txt = clave()
    urls = urls_sitemap(base)
    if not urls:
        print("[ERROR] sin URLs que enviar")
        return 2
    if not base:
        base = "https://" + host_de(urls[0])
    host = host_de(base)

    # Se comprueba ANTES de avisar: una URL que no responde 200 solo hace que
    # el buscador pierda confianza en el aviso.
    malas = []
    for url in urls:
        r = revisa(url, clave_txt)
        if r["problemas"]:
            malas.append((url, r["problemas"]))
    if malas:
        print("[NO SE ENVIA] estas URL no estan listas:")
        for url, problemas in malas:
            print(f"  {url}")
            for p in problemas:
                print(f"    - {p}")
        print("\nCuando el sitio sirva 200 y sin noindex, repite el envio.")
        return 1

    cuerpo = json.dumps({
        "host": host,
        "key": clave_txt,
        "keyLocation": f"{base}/{clave_txt}.txt",
        "urlList": urls,
    }, ensure_ascii=False).encode("utf-8")
    destino = ENDPOINTS.get(motor.lower())
    if not destino:
        print(f"[ERROR] motor desconocido: {motor}. Vale: {', '.join(ENDPOINTS)}")
        return 2

    print(f"IndexNow -> {destino}")
    print(f"  {len(urls)} URL(s) de {host}")
    if probar:
        print("\n[--probar] no se ha enviado nada. El cuerpo seria:")
        print(json.dumps(json.loads(cuerpo), ensure_ascii=False, indent=2))
        return 0

    pet = urllib.request.Request(destino, data=cuerpo, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8",
                                          "User-Agent": UA})
    try:
        with urllib.request.urlopen(pet, timeout=30) as r:
            codigo, texto = r.status, r.read(500).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        codigo, texto = e.code, (e.read(500) or b"").decode("utf-8", "replace")
    except Exception as e:
        print(f"[ERROR] no se pudo conectar: {type(e).__name__}: {e}")
        return 1

    # 200 = aceptado; 202 = aceptado, clave aun en validacion (normal la 1a vez).
    if codigo in (200, 202):
        print(f"\nACEPTADO (HTTP {codigo}). Bing/Yandex/Seznam/Naver lo tendran en "
              f"su cola en horas o dias.")
        print("Google no usa IndexNow: darlo de alta en Search Console "
              "(SEO_BUSCADORES.md).")
        return 0
    pistas = {
        400: "peticion mal formada (revisa host/key/urlList)",
        403: "clave no valida: el fichero /<clave>.txt no esta publicado o no coincide",
        422: "las URL no pertenecen a ese host",
        429: "demasiadas peticiones: espera y repite",
    }
    print(f"\nRECHAZADO (HTTP {codigo}): {pistas.get(codigo, texto.strip()[:300])}")
    return 1


def buscar() -> int:
    """Busca el fichero que descarga Google en las carpetas tipicas."""
    casa = Path.home()
    sitios = [casa / "Downloads", casa / "Desktop", casa / "Escritorio",
              casa / "Documents", casa / "Documentos", Path.cwd()]
    vistos = []
    for carpeta in sitios:
        if not carpeta.is_dir():
            continue
        for p in sorted(carpeta.glob("google*.html")):
            if p.is_file():
                vistos.append(p)
    if not vistos:
        print("No hay ningun google*.html en:")
        for c in sitios:
            if c.is_dir():
                print(f"  {c}")
        print("\nEn el navegador, junto a la descarga, pulsa 'Mostrar en carpeta' y")
        print("copia aqui la ruta completa, o pasa el fichero con --archivo.")
        return 1
    print("Candidatos encontrados:")
    for p in vistos:
        print(f"  {p}   ({p.stat().st_size} B, {time.strftime('%d-%m %H:%M', time.localtime(p.stat().st_mtime))})")
    print("\nSiguiente paso:  python tools\\indexar.py --archivo \"<ruta>\"")
    return 0


def archivo_verificacion(ruta: str) -> int:
    """Copia el fichero que descarga Google Search Console, tal cual.

    Google entrega un fichero llamado google<token>.html cuyo contenido es
    `google-site-verification: google<token>.html`. No hay que teclearlo: se
    copia con su nombre exacto a docs/ y se publica; Google lo busca en la raiz
    del sitio. Si el nombre no encaja, se para: un fichero mal nombrado no
    verifica nada y solo confunde.
    """
    origen = Path(ruta.strip().strip('"')).expanduser()
    if not origen.is_file():
        print(f"[ERROR] no existe el fichero: {origen}")
        return 1
    if not re.fullmatch(r"google[0-9A-Za-z_\-]+\.html", origen.name):
        print(f"[ERROR] '{origen.name}' no tiene la forma google<token>.html")
        print("        Es el fichero que descarga Search Console, sin renombrar.")
        return 2
    texto = origen.read_text(encoding="utf-8", errors="replace")
    if "google-site-verification" not in texto:
        print("[ERROR] el fichero no lleva dentro 'google-site-verification'.")
        print("        Puede que sea otro fichero, o que se haya descargado a medias.")
        return 2
    destino = DOCS / origen.name
    destino.write_text(texto, encoding="utf-8")
    try:
        mostrado = destino.relative_to(RAIZ)
    except ValueError:
        mostrado = destino
    print(f"copiado a {mostrado}")
    base = ""
    try:
        base = urls_sitemap("")[0].split("/", 3)
        base = "/".join(base[:3])
    except Exception:
        base = ""
    print("Ahora hay que publicar el sitio; despues el fichero tiene que responder aquí:")
    print(f"  {base or 'https://<tu-sitio>'}/{destino.name}")
    print("Comprobarlo:  python tools\\indexar.py --estado")
    print("Luego, en Search Console, pulsa 'Verificar'.")
    return 0


def normaliza_token(valor: str) -> str:
    """Deja solo el token, se pegue como se pegue.

    En el panel se copia de tres formas y las tres son validas para una persona:
       9d6cb22812b81092
       google9d6cb22812b81092
       google9d6cb22812b81092.html
    Si se pasara tal cual a `google{valor}.html`, la segunda daria
    `googlegoogle9d6....html` y no verificaria nada. Se acepta la que venga.
    """
    v = (valor or "").strip().strip('"').strip("'").strip()
    if "/" in v:                      # si pegaron la URL entera
        v = v.rsplit("/", 1)[-1]
    if v.lower().endswith(".html"):
        v = v[:-5]
    if v.lower().startswith("google"):
        v = v[6:]
    return v


def comprobar_google(base: str = "") -> int:
    """Dice si la verificacion esta LISTA, por fichero y por etiqueta.

    Sirve para no darle a 'Verificar' a ciegas: comprueba en vivo las dos vias
    que admite Search Console (fichero en la raiz y meta en la portada) y dice
    cual pasaria ahora mismo.
    """
    if not base:
        try:
            base = "/".join(urls_sitemap("")[0].split("/", 3)[:3])
        except Exception:
            base = ""
    if not base:
        print("[ERROR] no se sabe la URL publica (mira docs/sitemap.xml)")
        return 2
    tokens = set()
    for p in DOCS.glob("google*.html"):
        m = re.fullmatch(r"google([0-9A-Za-z_\-]+)\.html", p.name)
        if m:
            tokens.add(m.group(1))
    if not tokens:
        print("No hay ningun docs/google<token>.html: nada que verificar todavia.")
        return 1

    ok = 0
    for t in sorted(tokens):
        print(f"token: {t}")
        codigo, _, cuerpo = pide(f"{base}/google{t}.html")
        bien = codigo == 200 and f"google-site-verification: google{t}.html" in cuerpo
        print(f"  metodo 'archivo HTML' : {'LISTO' if bien else 'NO'}"
              f"  ({base}/google{t}.html -> {codigo})")
        ok += bien
        codigo_h, _, html_txt = pide(f"{base}/")
        meta = re.search(r'<meta\s+name="google-site-verification"\s+content="([^"]+)"',
                         html_txt)
        bien_meta = codigo_h == 200 and meta is not None and meta.group(1) == t
        if codigo_h == 200 and meta is None:
            print(f"  metodo 'etiqueta HTML': NO  (la portada no trae la meta; publica)")
        else:
            print(f"  metodo 'etiqueta HTML': {'LISTO' if bien_meta else 'NO'}"
                  f"  (portada -> {codigo_h})")
        ok += bien_meta
        if bien or bien_meta:
            print("  -> Pulsa 'Verificar' en Search Console: pasara.")
        else:
            print("  -> Falta publicar. Luego: python tools\\desplegar... o el deploy de siempre.")
    return 0 if ok else 1


def token(que: str, valor: str) -> int:
    """Deja en docs/ el fichero de verificacion del panel correspondiente."""
    if que == "google":
        valor = normaliza_token(valor)
    valor = valor.strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_\-\.]{8,120}", valor or ""):
        print(f"[ERROR] '{valor}' no parece un token valido")
        return 2
    if que == "google":
        ruta = DOCS / f"google{valor}.html"
        # newline="\n": en Windows write_text mete CRLF y el fichero queda con
        # un \r de mas. Google lo tolera, pero se sirve exactamente lo que da
        # Google, que es una linea sin mas.
        ruta.write_text(f"google-site-verification: google{valor}.html\n",
                        encoding="utf-8", newline="\n")
    elif que == "bing":
        ruta = DOCS / "BingSiteAuth.xml"
        ruta.write_text('<?xml version="1.0"?>\n<users>\n  <user>' + valor +
                        "</user>\n</users>\n", encoding="utf-8", newline="\n")
    else:
        ruta = DOCS / f"yandex_{valor}.html"
        ruta.write_text(f"<html><head><meta name=\"yandex-verification\" "
                        f"content=\"{valor}\" /></head><body>Verifying</body></html>\n",
                        encoding="utf-8", newline="\n")
    try:
        mostrado = ruta.relative_to(RAIZ)
    except ValueError:
        mostrado = ruta
    print(f"escrito {mostrado}")
    print("publica el sitio (el mismo comando de siempre) y dale a 'Verificar' "
          "en el panel; no hace falta tocar el HTML.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Indexar KUKIA en los buscadores.")
    ap.add_argument("--estado", action="store_true", help="que impide indexar, si algo")
    ap.add_argument("--enviar", action="store_true", help="avisar a IndexNow")
    ap.add_argument("--base", default="", help="URL base (por defecto, la del sitemap)")
    ap.add_argument("--motor", default="todos", help=f"todos|{'|'.join(k for k in ENDPOINTS if k != 'todos')}")
    ap.add_argument("--probar", action="store_true", help="no enviar, solo ensenar")
    ap.add_argument("--buscar", action="store_true",
                    help="buscar el google*.html descargado")
    ap.add_argument("--comprobar-google", action="store_true",
                    help="ver si la verificacion de Google ya pasaria")
    ap.add_argument("--archivo", default="",
                    help="copiar el google<token>.html descargado (ruta)")
    ap.add_argument("--token-google", default="", help="token de Search Console")
    ap.add_argument("--token-bing", default="", help="token de Bing Webmaster Tools")
    ap.add_argument("--token-yandex", default="", help="token de Yandex Webmaster")
    args = ap.parse_args(argv)

    if args.buscar:
        return buscar()
    if args.comprobar_google:
        return comprobar_google(args.base.rstrip("/"))
    if args.archivo:
        return archivo_verificacion(args.archivo)
    if args.token_google:
        return token("google", args.token_google)
    if args.token_bing:
        return token("bing", args.token_bing)
    if args.token_yandex:
        return token("yandex", args.token_yandex)
    if args.enviar:
        return enviar(args.base.rstrip("/"), args.motor, args.probar)
    if args.estado:
        return estado(args.base.rstrip("/"))
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
