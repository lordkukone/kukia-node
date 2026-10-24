"""Regenera docs/codigo.html y docs/kukia-node.zip desde el codigo real.

Antes estas dos piezas se actualizaban a mano y se quedaron con el codigo
viejo (sin el arreglo de cobro). Este script las reconstruye siempre desde los
ficheros de disco, asi que lo publicado es lo que corre.

Uso:
  python publico/generar_web.py            # escribe en docs/
  python publico/generar_web.py --destino <carpeta>
"""
import argparse
import hashlib
import html
import json
import re
import secrets
import time
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "docs"
# Direccion publica que la gente usa de verdad: es la que se declara como
# canonica para que Google no tenga dos copias compitiendo por la misma pagina.
#
# 21-10-2026: Surge sirve su propio robots.txt con "Disallow: /" en los
# subdominios *.surge.sh e IGNORA el nuestro, asi que ahi Google no puede
# indexar nunca. La copia indexable vive en el sitio de Freebuff y es la que
# se declara canonica; --canonico permite cambiarla sin tocar el codigo.
CANONICO = "https://kukia-network.freebuff.page"

FUENTES = [
    ("Coordinador (Core)", "coordinator/kukia_core.py"),
    ("Minero (Worker multi-GPU)", "worker/kukia_worker.py"),
    ("Validador independiente", "validator/kukia_validator.py"),
    ("Billetera", "ledger/kukia_wallet.py"),
    ("Sellos del ledger (integridad)", "ledger/sellos.py"),
    ("Auditor de sellos", "publico/verificar_sellos.py"),
    ("Rescate de saldos antiguos", "tools/rescatar_saldos.py"),
    ("Salud de la moneda", "tools/salud.py"),
    ("Copia de seguridad verificada", "tools/backup_ledger.py"),
    ("Restauracion probada", "tools/probar_restauracion.py"),
    ("Prueba del rele (sin red)", "publico/probar_relay.mjs"),
    ("Dashboard (panel de operaciones)", "dashboard/kukia_dash.py"),
    ("Pruebas de integracion (copia del ledger)", "tools/pruebas_seguridad.py"),
    ("Rele publico (metricas)", "publico/relay.js"),
    ("Publicador local", "publico/publicar_metricas.py"),
    ("Programar la copia diaria", "tools/programar_backup.ps1"),
    ("Guardia (procesos siempre vivos)", "tools/guardia.py"),
    ("Direccion estable del Core (tunel)", "tools/core_destino.py"),
    ("Tunel publico con direccion estable", "tools/tunel_estable.py"),
    ("Tunel con nombre (dominio propio)", "tools/tunel_dominio.py"),
    ("Anclaje en Bitcoin (OpenTimestamps)", "tools/anclar_ots.py"),
    ("Anclaje: como se verifica", "ANCLAJE.md"),
    ("Benchmark real de la GPU", "tools/benchmark_gpu.py"),
    ("Comprobador del entorno del minero", "tools/instalar_minero.py"),
    ("Estrategia: contra Bitcoin y plan del minero", "ESTRATEGIA.md"),
    ("Multisig: diseno y migracion", "MULTISIG.md"),
]

EN_ZIP = [
    "coordinator/kukia_core.py",
    "dashboard/kukia_dash.py",
    "ledger/kukia_wallet.py",
    "ledger/sellos.py",
    "publico/verificar_sellos.py",
    "tools/rescatar_saldos.py",
    "tools/salud.py",
    "tools/backup_ledger.py",
    "tools/probar_restauracion.py",
    "tools/programar_backup.ps1",
    "TAREA_BACKUP.cmd",
    "ARRANCAR_GUARDIA.cmd",
    "ARRANCAR_GUARDIA_SILENCIOSO.vbs",
    "tools/guardia.py",
    "ledger/wordlist_es.txt",
    "worker/kukia_worker.py",
    "validator/kukia_validator.py",
    "publico/relay.js",
    "publico/publicar_metricas.py",
    "publico/probar_relay.mjs",
    "publico/ARRANCAR_METRICAS.cmd",
    "ARRANCAR_MINERO.cmd",
    "VER_SALDO_MINERO.cmd",
    # 23-10-2026: GUIA_MINERO.md (que si va en el paquete) manda abrir
    # VER_MINERO.cmd para ver el minero y nombra el visor y el ayudante que se
    # queda con el minero oculto. Misma norma que ARRANCAR_TUNEL.cmd: si un
    # documento del paquete nombra un fichero, el fichero va dentro.
    "VER_MINERO.cmd",
    "tools/mina_en_vivo.py",
    "tools/minero_en_ventana.py",
    "COPIAR_LEDGER.cmd",
    "ARRANCAR_VALIDADOR.cmd",
    "ARRANCAR_PANEL.cmd",
    "GUIA_MINERO.md",
    "GUIA_VALIDADORES.md",
    # 24-10-2026: la portada publica la API de cliente, pero el paquete no traia
    # ni la guia ni el cliente de ejemplo: quien se descargaba el nodo no podia
    # ser cliente. Misma regla que con el tunel (ver mas abajo): lo que un
    # documento del paquete nombra, va dentro. La guia manda ejecutar
    # tools/prueba_cliente.py para repetir las 40 comprobaciones, asi que va.
    "GUIA_CLIENTE.md",
    "tools/cliente_ejemplo.py",
    "tools/prueba_cliente.py",
    "TUNEL_ESTABLE.md",
    "MINERO_EXTERNO.md",
    "SEGURIDAD.md",
    "ESTRATEGIA.md",
    "ANCLAJE.md",
    "MULTISIG.md",
    "tools/anclar_ots.py",
    "tools/benchmark_gpu.py",
    "tools/instalar_minero.py",
    "INSTALAR_Y_MINAR.cmd",
    "core_minero.txt",
    "tools/core_destino.py",
    "tools/tunel_estable.py",
    "tools/tunel_dominio.py",
    # Los dos atajos del tunel van en el paquete porque TUNEL_ESTABLE.md (que si
    # va) los nombra: sin ellos, el documento manda a ficheros que no existen.
    "ARRANCAR_TUNEL.cmd",
    "PREPARAR_DOMINIO.cmd",
    "tools/saldo_minero.py",
    # 23-10-2026: GUIA_MINERO.md (que va en el paquete) explica como enviar
    # desde una billetera local cifrada con este comando; el fichero va dentro.
    "tools/enviar_kia.py",
    # AUDITORIA 2026-10-22: el paquete llevaba los documentos pero no los ficheros
    # que esos documentos mandan ejecutar. SEGURIDAD.md daba la orden exacta
    # `python tools/prueba_gasto_billetera.py ...` y ese fichero no venia; el
    # README prometia las suites y tampoco venian. Quien se descargaba el nodo
    # no podia comprobar nada de lo que el propio paquete afirma: se queda sin
    # la prueba de que el gasto de la billetera funciona y sin las herramientas
    # de blindaje. Se incluyen aqui (son codigo, no llevan claves); seguir la
    # norma que ya estaba escrita para ARRANCAR_TUNEL.cmd: si un documento del
    # paquete nombra un fichero, el fichero va dentro.
    "tools/prueba_gasto_billetera.py",
    "tools/verificar_moneda.py",
    "tools/pruebas_seguridad.py",
    "tools/pruebas_caos.py",
    "tools/prueba_failover_negativas.py",
    "tools/prueba_vigilante_indexado.py",
    "tools/replica_ledger.py",
    "tools/volver_al_original.py",
    "tools/cerrar_puertos_abiertos.ps1",
    "tools/proteger_claves.ps1",
    "README.md",
    "requirements.txt",
]

CABECERA = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<link rel="canonical" href="{canonico}/codigo.html">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KUKIA | Codigo fuente completo - Transparencia total</title>
<meta name="description" content="Codigo fuente completo de KUKIA: core, worker, billetera y dashboard. Todo legible y verificable.">
<style>
:root {{ --gold:#d4af37; --navy:#081a3a; --line:#27487c; --text:#c3d1ea; }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:'Segoe UI',system-ui,sans-serif; background:var(--navy); color:#fff; line-height:1.6; }}
.wrap {{ max-width:1000px; margin:0 auto; padding:3rem 1.5rem; }}
h1 {{ color:var(--gold); font-size:2.2rem; margin-bottom:.4rem; }}
.sub {{ color:var(--text); margin-bottom:1rem; font-style:italic; }}
.intro {{ background:rgba(18,48,94,.4); border-left:3px solid var(--gold); padding:1rem 1.3rem; border-radius:0 10px 10px 0; margin:1.5rem 0 2.5rem; color:var(--text); font-size:.95rem; }}
h2 {{ color:var(--gold); font-size:1.2rem; margin:2.5rem 0 .8rem; border-bottom:1px solid var(--line); padding-bottom:.5rem; }}
pre {{ background:#050f22; border:1px solid var(--line); border-radius:12px; padding:1.2rem; overflow-x:auto; }}
code {{ font-family:'Cascadia Code',Consolas,monospace; font-size:.82rem; color:#c8d6f0; white-space:pre; }}
.back {{ display:inline-block; margin-bottom:2rem; color:var(--gold); text-decoration:none; }}
.back:hover {{ text-decoration:underline; }}
.foot {{ margin-top:3rem; color:#789; font-size:.85rem; }}
</style>
</head>
<body>
<div class="wrap">
<a class="back" href="index.html">&larr; Volver a KUKIA</a>
<h1>Codigo fuente completo</h1>
<div class="sub">Transparencia total: todo el software de KUKIA a la vista. Nada oculto.</div>
<div class="intro">Este es el codigo exacto que corre la red: el coordinador (Core), el minero (Worker), la billetera, el dashboard
y el rele que publica las metricas de esta web. Cualquiera puede leerlo, copiarlo y auditarlo. Si encuentras un error o mejoras,
se corrigen. Tambien puedes descargarlo empaquetado en <a href="kukia-node.zip" style="color:var(--gold);">kukia-node.zip</a>
(huella SHA-256: <code>{hash_zip}</code>, tambien en <a href="kukia-node.zip.sha256" style="color:var(--gold);">kukia-node.zip.sha256</a>).
<br><br>El paquete <b>no lleva ninguna clave, ninguna billetera y ningun dato de nadie</b>: solo el programa. La billetera se crea en tu PC
en la primera vuelta y su clave privada queda cifrada con tu usuario de Windows. Comprueba la huella antes de ejecutarlo: si no coincide, no es el nuestro.
<br><br>Generado desde los ficheros reales el {fecha}. Si no coincide con lo que descargas, es un fallo: dilo y se arregla.</div>
"""

PIE = """<div class="foot">KUKIA Network - prototipo en fase de pruebas. Sin auditoria externa todavia. Sin precio hasta demanda real.</div>
</div>
</body>
</html>
"""


def generar_codigo(destino: Path, fecha: str, hash_zip: str = "",
                   canonico: str = CANONICO) -> Path:
    partes = [CABECERA.format(fecha=fecha, hash_zip=hash_zip or "(pendiente)",
                              canonico=canonico)]
    for titulo, rel in FUENTES:
        ruta = RAIZ / rel
        if not ruta.exists():
            continue
        texto = ruta.read_text(encoding="utf-8")
        partes.append(f"<h2>{html.escape(titulo)} - {html.escape(rel)}</h2>\n")
        partes.append("<pre><code>" + html.escape(texto) + "</code></pre>\n")
    partes.append(PIE)
    salida = destino / "codigo.html"
    salida.write_text("\n".join(partes), encoding="utf-8")
    return salida


# Nada de esto puede acabar NUNCA en el paquete publico: son claves, bases de
# datos, registros o ficheros de recuperacion. Se comprueba antes de escribir
# (no basta con revisar EN_ZIP a mano: un despiste publica una llave).
PROHIBIDO = ("ledger/wallet/", "/wallet/", "admin_token", "sello_key",
             "recuperacion", ".db", ".sqlite", ".log", ".jsonl", "identidad",
             # Token del bot de avisos: es una credencial, no sale de aqui.
             "telegram",
             # Config y credenciales del tunel con nombre: llevan la ruta del
             # usuario y, sobre todo, no tienen por que salir de este PC.
             "tools/cloudflared/", ".cloudflared", "dominio_core.txt")


def _prohibido(rel: str) -> bool:
    """True si el fichero es material secreto. El codigo de la billetera SI
    puede publicarse (kukia_wallet.py); lo que no sale nunca es una billetera
    concreta (ledger/wallet/*.json)."""
    bajo = rel.replace("\\", "/").lower()
    nombre = bajo.rsplit("/", 1)[-1]
    if nombre.endswith(".json") and "wallet" in nombre:
        return True
    return any(p in bajo for p in PROHIBIDO)


# En ESTE PC core_minero.txt apunta al Core local (minar aqui). El paquete que
# se descarga NO: quien lo baje tiene que minar contra el Core publico, asi que
# lleva la direccion ESTABLE (el espejo dice cual es la URL viva del tunel, que
# cambia cuando se renueva). Ver tools/core_destino.py.
CORE_MINERO_PUBLICO = (
    "# KUKIA - direccion del Core\n"
    "#\n"
    "# No hace falta que la toques: esta linea sigue la direccion publica del\n"
    "# Core, que se renueva sola cuando el tunel cambia de nombre. Si algun dia\n"
    "# quieres minar contra OTRO Core, sustituye la linea entera por su URL\n"
    "# (por ejemplo https://kukia.tudominio.com).\n"
    "#\n"
    "# Las lineas que empiezan por # se ignoran; solo cuenta la primera que no.\n"
    "auto https://kukia-network.surge.sh/core_actual.json\n"
)


# Fecha FIJA para todas las entradas del zip. Sin esto cada build metia la
# hora de disco de cada fichero y la huella cambiaba aunque el codigo fuera el
# mismo: en la web se vieron dos SHA-256 distintos del "mismo" paquete (22-10-
# 2026) y el vigilante daba por "otra version" un despliegue que solo se habia
# regenerado. Con fecha fija, mismos ficheros = misma huella, y cualquiera
# puede comprobar que el paquete que descarga es el que se publico.
FECHA_ZIP = (2026, 1, 1, 0, 0, 0)


def _meter(z: zipfile.ZipFile, rel: str, datos: bytes) -> None:
    """Mete un fichero con fecha fija (zip reproducible)."""
    zi = zipfile.ZipInfo(rel.replace("\\", "/"), date_time=FECHA_ZIP)
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.external_attr = (0o644 & 0xFFFF) << 16
    z.writestr(zi, datos)


def generar_zip(destino: Path) -> Path:
    salida = destino / "kukia-node.zip"
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in EN_ZIP:
            if rel == "core_minero.txt":
                _meter(z, rel, CORE_MINERO_PUBLICO.encode("utf-8"))
                continue
            ruta = RAIZ / rel
            if not ruta.exists():
                continue
            if _prohibido(rel):
                raise SystemExit(f"[generar_web] ABORTADO: {rel} no puede publicarse "
                                 f"(clave, base de datos o registro)")
            _meter(z, rel, ruta.read_bytes())
        # Anclas en Bitcoin: se incluyen para que cualquiera pueda verificar el
        # historial sin pedirnos nada (son 2 KB y no llevan ningun secreto).
        for ruta in sorted((RAIZ / "anclas").glob("kukia_sello_*")):
            if ruta.is_file():
                _meter(z, str(ruta.relative_to(RAIZ)), ruta.read_bytes())
    return salida


def generar_version(destino: Path) -> Path:
    """Escribe version.json con la MISMA version que lleva el index.html.

    La web compara su propio meta `kukia-build` con este fichero: si no
    coinciden, es que el navegador tiene una copia vieja y se recarga sola.
    Por eso los dos valores salen del mismo sitio (el HTML en disco), no de
    dos numeros escritos a mano.
    """
    html_path = destino / "index.html"
    if not html_path.exists():
        raise SystemExit(f"[generar_web] falta {html_path}")
    m = re.search(r'<meta\s+name="kukia-build"\s+content="([^"]+)"',
                  html_path.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("[generar_web] index.html no declara <meta name=\"kukia-build\">")
    # PAQUETE VERIFICABLE (auditoria 23-10-2026, hallazgo P2): la version no
    # basta con un nombre; se publica el SHA-256 del zip que hay en ESTA misma
    # carpeta y la version del Core que lo genero. Asi cualquiera comprueba que
    # lo que descarga es lo que se genero, sin creerse la fecha.
    huella_zip = ""
    try:
        huella_zip = (destino / "kukia-node.zip.sha256").read_text(
            encoding="ascii").split()[0].strip()
    except Exception:
        pass
    version_core = ""
    try:
        m_core = re.search(r'app = FastAPI\(title="KUKIA Core", version="([^"]+)"',
                           (RAIZ / "coordinator" / "kukia_core.py").read_text(
                               encoding="utf-8"))
        version_core = m_core.group(1) if m_core else ""
    except Exception:
        pass
    dato = {"v": m.group(1), "generado": time.strftime("%Y-%m-%d %H:%M:%S"),
            "core": version_core,
            "zip": "kukia-node.zip",
            "zip_sha256": huella_zip,
            "verificar": ("Descarga kukia-node.zip y ejecuta 'sha256sum kukia-node.zip' "
                          "(Windows: 'certutil -hashfile kukia-node.zip SHA256'): tiene que "
                          "dar el zip_sha256 de este fichero. La version del Core se ve en "
                          "http://127.0.0.1:8300/version, que ademas publica el hash de "
                          "cada fichero que esta ejecutando.")}
    salida = destino / "version.json"
    salida.write_text(json.dumps(dato, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    return salida


def url_limpia(canonico: str, rel: str) -> str:
    """URL final de una pagina, sin `.html`.

    El sitio de Freebuff responde `307 /codigo.html -> /codigo` (URLs limpias).
    Declarar la canonica con `.html` mandaria a Google a una redireccion
    temporal en vez de a la pagina buena, asi que se publica la forma final.
    """
    base = canonico.rstrip("/")
    if rel in ("", "index.html"):
        return base + "/"
    return base + "/" + (rel[:-5] if rel.endswith(".html") else rel)


def fijar_canonico(destino: Path, canonico: str) -> int:
    """Pone en TODAS las .html de destino la canonica que toca.

    21-10-2026: la web se sirve en dos sitios (Surge, que Google no puede
    rastrear, y Freebuff, que si). Si cada copia declarara su propia URL como
    buena, Google veria duplicados; la canonica tiene que ser UNA y la misma en
    las dos copias, y sale de --canonico para no volver a olvidarla a mano.
    """
    patron = re.compile(r'(<link\s+rel="canonical"\s+href=")([^"]*)(")')
    tocadas = 0
    for ruta in sorted(destino.glob("*.html")):
        texto = ruta.read_text(encoding="utf-8")
        def _sub(m, nombre=ruta.name):
            # El destino sale del NOMBRE DEL FICHERO, no del camino que ya
            # llevaba la etiqueta. Antes se reutilizaba el camino viejo y, si la
            # canonica tiene subcarpeta (sitio de proyecto en GitHub Pages,
            # https://usuario.github.io/kukia-node), la segunda pasada ponia
            # /kukia-node/kukia-node/codigo. Con el nombre del fichero la
            # operacion es idempotente y vale para dominio raiz y subcarpeta.
            return m.group(1) + url_limpia(canonico, nombre) + m.group(3)
        nuevo, n = patron.subn(_sub, texto)
        if n and nuevo != texto:
            ruta.write_text(nuevo, encoding="utf-8")
            tocadas += 1
    return tocadas


# --- SEO tecnico: se GENERA, no se escribe a mano ---------------------
#
# 21-10-2026: cada pagina tenia sus etiquetas puestas a mano y ninguna llevaba
# datos estructurados. Los buscadores necesitan, en TODAS las paginas y con la
# MISMA forma: robots explicito, Open Graph con URL absoluta y JSON-LD. Si se
# mantiene a mano, la pagina nueva se queda sin ello y nadie se entera; por eso
# lo inyecta el generador en cada vuelta.
MARCA_INI = "<!-- kukia-seo -->"
MARCA_FIN = "<!-- /kukia-seo -->"
# Etiquetas que este bloque SUSTITUYE (si no, quedarian duplicadas y con URLs
# relativas: og:image="moneda.jpg" no lo entienden los rastreadores).
_REDUNDANTES = re.compile(
    r'[ \t]*<meta\s+(?:name="robots"|name="twitter:[^"]*"|property="og:[^"]*")[^>]*>\s*\n?',
    re.I)
_EXTRAE = {
    "titulo": re.compile(r"<title>(.*?)</title>", re.I | re.S),
    "descripcion": re.compile(r'<meta\s+name="description"\s+content="([^"]*)"', re.I),
}


def _texto(html_txt: str) -> str:
    """Texto plano de un fragmento (para og/twitter sin etiquetas dentro)."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", html_txt))).strip()


def _tokens_google(destino: Path) -> list:
    """Tokens de verificacion de Google, sacados de los ficheros googleXXXX.html.

    Google admite dos formas de verificar: el fichero en la raiz o una meta en
    el HTML. La plataforma sirve /x.html redirigiendo (ver worker), asi que se
    ponen LAS DOS: si una falla, la otra verifica. El token no es secreto (viaja
    en el HTML publico), asi que se lee del fichero que deja Search Console.
    """
    tokens = []
    for ruta in sorted(destino.glob("google*.html")):
        m = re.fullmatch(r"google([0-9A-Za-z_\-]+)\.html", ruta.name)
        if m:
            tokens.append(m.group(1))
    return tokens


def _bloque_seo(url: str, base: str, titulo: str, desc: str,
                verificacion=()) -> str:
    """Etiquetas + JSON-LD de una pagina. Sin datos que no sean ciertos."""
    desc_js = json.dumps(desc, ensure_ascii=False)
    graf = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "WebSite",
                "@id": f"{base}/#sitio",
                "url": f"{base}/",
                "name": "KUKIA Network",
                "alternateName": "KUKIA",
                "inLanguage": "es",
                "description": ("Red descentralizada de computo GPU: conecta tu "
                                "grafica y gana KIA por trabajo verificado."),
                "publisher": {"@id": f"{base}/#organizacion"},
            },
            {
                "@type": "Organization",
                "@id": f"{base}/#organizacion",
                "name": "KUKIA Network",
                "url": f"{base}/",
                "logo": {"@type": "ImageObject",
                         "url": f"{base}/moneda.jpg",
                         "width": 512, "height": 512},
            },
            {
                "@type": "WebPage",
                "@id": f"{url}#pagina",
                "url": url,
                "name": titulo,
                "description": desc,
                "inLanguage": "es",
                "isPartOf": {"@id": f"{base}/#sitio"},
            },
        ],
    }
    if desc_js == "\"\"":
        graf["@graph"][2].pop("description", None)
    etiquetas = [
        MARCA_INI,
        '<meta name="robots" content="index,follow,max-image-preview:large">',
        f'<meta property="og:type" content="{"website" if url.rstrip("/") == base else "article"}">',
        '<meta property="og:site_name" content="KUKIA Network">',
        '<meta property="og:locale" content="es_ES">',
        f'<meta property="og:url" content="{url}">',
        f'<meta property="og:title" content="{html.escape(titulo)}">',
        f'<meta property="og:description" content="{html.escape(desc)}">',
        f'<meta property="og:image" content="{base}/moneda.jpg">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{html.escape(titulo)}">',
        f'<meta name="twitter:description" content="{html.escape(desc)}">',
        f'<meta name="twitter:image" content="{base}/moneda.jpg">',
        '<script type="application/ld+json">',
        json.dumps(graf, ensure_ascii=False, indent=1),
        "</script>",
        MARCA_FIN,
    ]
    # Justo despues del robots: la verificacion de Google, si hay token.
    etiquetas[2:2] = [f'<meta name="google-site-verification" content="{t}">'
                      for t in verificacion]
    return "\n".join(etiquetas)


def inyectar_seo(destino: Path, canonico: str = CANONICO) -> int:
    """Pone el bloque SEO en todas las .html. Idempotente: si ya esta, lo cambia."""
    base = canonico.rstrip("/")
    verificacion = _tokens_google(destino)
    tocadas = 0
    for ruta in sorted(destino.glob("*.html")):
        texto = ruta.read_text(encoding="utf-8")
        rel = "" if ruta.name == "index.html" else ruta.name
        url = url_limpia(base, rel)
        titulo = _texto((_EXTRAE["titulo"].search(texto) or [None, ruta.stem])[1])
        m = _EXTRAE["descripcion"].search(texto)
        desc = _texto(m.group(1)) if m else ""
        # Se quita el bloque anterior y las etiquetas que este sustituye, solo
        # en el <head> (nunca en el cuerpo, donde podria haber JSON de la app).
        corte = texto.lower().find("</head>")
        if corte < 0:
            continue
        cabeza, resto = texto[:corte], texto[corte:]
        if MARCA_INI in cabeza:
            cabeza = re.sub(re.escape(MARCA_INI) + r".*?" + re.escape(MARCA_FIN),
                            "", cabeza, flags=re.S)
        cabeza = _REDUNDANTES.sub("", cabeza)
        cabeza = (cabeza.rstrip() + "\n"
                  + _bloque_seo(url, base, titulo, desc, verificacion) + "\n")
        nuevo = cabeza + resto
        if nuevo != texto:
            ruta.write_text(nuevo, encoding="utf-8")
            tocadas += 1
    return tocadas


def generar_clave_indexnow(destino: Path) -> Path:
    """Deja publico el fichero de clave de IndexNow (Bing, Yandex, Seznam...).

    IndexNow no pide cuenta: se publica un fichero `<clave>.txt` con la clave y
    se avisa a la API de que hay URLs nuevas. La clave NO es un secreto (va
    servida a todo el mundo por diseno), pero se guarda en el repo para no
    rotarla sin querer en cada publicacion: si cambiara, los envios anteriores
    dejarian de valer.
    """
    fichero = RAIZ / "indexnow_key.txt"
    clave = fichero.read_text(encoding="utf-8").strip() if fichero.exists() else ""
    if not re.fullmatch(r"[0-9a-f]{16,128}", clave or ""):
        clave = secrets.token_hex(16)
        fichero.write_text(clave + "\n", encoding="utf-8")
    salida = destino / f"{clave}.txt"
    salida.write_text(clave + "\n", encoding="utf-8")
    return salida


def generar_seo(destino: Path, canonico: str = CANONICO):
    """Escribe robots.txt y sitemap.xml para que Google pueda indexar la web.

    Publicar en Google es gratis (Search Console); lo que hace falta es que el
    rastreador encuentre las paginas y sepa cual es la copia buena. El sitemap
    sale de los .html que hay en docs/, no de una lista escrita a mano, asi que
    una pagina nueva entra sola.
    """
    # prioridad y frecuencia por pagina (la portada primero: es la que importa).
    plan = {"index.html": (1.0, "daily"), "minar.html": (0.9, "weekly"),
            "whitepaper.html": (0.8, "monthly"), "wallet.html": (0.7, "weekly"),
            "codigo.html": (0.6, "monthly")}
    paginas = []
    for ruta in sorted(destino.glob("*.html")):
        # Los ficheros de verificacion NO son paginas: no van en el sitemap.
        if re.fullmatch(r"(google|yandex_)[0-9A-Za-z_\-]+\.html", ruta.name):
            continue
        rel = "" if ruta.name == "index.html" else ruta.name
        prio, frec = plan.get(ruta.name, (0.5, "monthly"))
        # lastmod REAL (fecha del fichero), no la de hoy: si se pone la de hoy en
        # cada publicacion, Google acaba ignorando la senal.
        fecha = time.strftime("%Y-%m-%d",
                              time.gmtime(ruta.stat().st_mtime))
        paginas.append((url_limpia(canonico, rel), fecha, prio, frec))
    paginas.sort(key=lambda p: -p[2])
    robots = ("User-agent: *\n"
              "Allow: /\n"
              "\n"
              "# Indice unico y bueno: la copia de Surge sirve su propio\n"
              "# robots.txt con Disallow: / y no se puede cambiar.\n"
              f"Sitemap: {canonico}/sitemap.xml\n")
    salida_robots = destino / "robots.txt"
    salida_robots.write_text(robots, encoding="utf-8")

    lineas = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for url, fecha, prio, frec in paginas:
        lineas.append(f"  <url><loc>{url}</loc><lastmod>{fecha}</lastmod>"
                      f"<changefreq>{frec}</changefreq>"
                      f"<priority>{prio:.1f}</priority></url>")
    lineas.append("</urlset>")
    salida_mapa = destino / "sitemap.xml"
    salida_mapa.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return salida_robots, salida_mapa


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--destino", default=str(DESTINO))
    ap.add_argument("--fecha", default="20-10-2026")
    ap.add_argument("--canonico", default=CANONICO,
                    help="URL publica base (sitemap y canonical salen de aqui)")
    args = ap.parse_args()
    canonico = args.canonico.rstrip("/")
    destino = Path(args.destino)
    destino.mkdir(parents=True, exist_ok=True)
    z = generar_zip(destino)
    huella = hashlib.sha256(z.read_bytes()).hexdigest()
    # newline="\n": en Windows write_text escribia CRLF y `sha256sum -c` no lo
    # encontraba. El fichero de huella tiene que ser identico en Linux y Windows.
    (destino / "kukia-node.zip.sha256").write_text(
        f"{huella}  kukia-node.zip\n", encoding="ascii", newline="\n")
    h = generar_codigo(destino, args.fecha, huella, canonico)
    v = generar_version(destino)
    n_html = fijar_canonico(destino, canonico)
    n_seo = inyectar_seo(destino, canonico)
    r, s = generar_seo(destino, canonico)
    k = generar_clave_indexnow(destino)
    print(f"codigo.html {h.stat().st_size} bytes")
    print(f"kukia-node.zip {z.stat().st_size} bytes | sha256 {huella}")
    print(f"version.json {json.loads(v.read_text(encoding='utf-8'))['v']}")
    print(f"robots.txt {r.stat().st_size} bytes / sitemap.xml {s.stat().st_size} bytes")
    print(f"canonica {canonico} en {n_html} pagina(s)")
    print(f"SEO tecnico (robots, Open Graph, JSON-LD) en {n_seo} pagina(s)")
    print(f"IndexNow: clave publicada en /{k.name} (para Bing, Yandex, Seznam, Naver)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
