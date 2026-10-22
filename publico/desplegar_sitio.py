#!/usr/bin/env python3
"""Prepara la copia publicable de la web para un dominio indexable.

Por que existe: Surge sirve su propio robots.txt con `Disallow: /` en los
subdominios *.surge.sh e ignora el nuestro, asi que en kukia-network.surge.sh
Google no puede indexar NUNCA. La copia que si se indexa vive en el sitio de
Freebuff (.freebuff.page, robots sin restricciones), y este script deja la
carpeta lista para subirla sin depender de copiar a mano.

Que hace:
  1. regenera docs/ con el codigo y la version reales
  2. fija la canonica y el sitemap al dominio publico que se le pasa
  3. copia docs/ a sitio_publicado/public  (nada de wallet, .db ni logs: docs/ no los tiene)
  4. escribe sitio_publicado/worker.js (sirve los ficheros) y su manifest.json

Uso:
  python publico/desplegar_sitio.py --dominio https://<sitio>.freebuff.page
  python publico/desplegar_sitio.py --dominio ... --sin-regenerar

OJO al desplegar en Freebuff (22-10-2026): el publicador solo ve ficheros que
estan DENTRO del workspace del hilo, no en esta carpeta. Si se le pasa un
manifiesto de fuera, el despliegue "sale bien" pero sube lo que haya en el
workspace (o nada) y el sitio sigue con la version vieja sin dar error. Hay que
copiar sitio_publicado/ al workspace (carpeta deploy_site/) y desplegar desde
alli. Perdido 20 minutos por esto: tres despliegues en verde sin cambiar nada.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DOCS = RAIZ / "docs"
# Nombre sin punto delante a proposito: el publicador rechaza rutas ocultas.
SALIDA = RAIZ / "sitio_publicado"
PUBLICO = SALIDA / "public"

# Cloudflare/Workers: sirve los estaticos y convierte un 404 sin extension en
# index.html (asi una ruta como /minar sigue funcionando con o sin .html).
WORKER = """// AUDITORIA 2026-10-21: la plataforma servia los ficheros tal cual, sin una
// sola cabecera de seguridad (comprobado con curl: ni nosniff, ni HSTS, ni
// proteccion de clickjacking). Se anaden aqui, en una sola capa, para todas
// las paginas. La CSP va por pagina (meta) para que la de la billetera siga
// permitiendo su Core local en http.
const SEGURIDAD = {
  'x-content-type-options': 'nosniff',
  'referrer-policy': 'strict-origin-when-cross-origin',
  'x-frame-options': 'DENY',
  'permissions-policy': 'geolocation=(), microphone=(), camera=(), payment=(), usb=()',
  'strict-transport-security': 'max-age=31536000; includeSubDomains',
};

function blindar(resp) {
  if (!resp) return resp;
  const h = new Headers(resp.headers);
  for (const k in SEGURIDAD) h.set(k, SEGURIDAD[k]);
  const ct = h.get('content-type') || '';
  if (ct === 'text/html') h.set('content-type', 'text/html; charset=utf-8');
  return new Response(resp.body, { status: resp.status, statusText: resp.statusText, headers: h });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    // Ficheros de verificacion de buscadores (googleXXXX.html, yandex_XXX.html).
    // La plataforma redirige /algo.html -> /algo con un 307, y los verificadores
    // piden la ruta EXACTA y esperan un 200 con el contenido. Aqui se sirve el
    // contenido en la ruta exacta, sin redireccion. Sin regex, para no pelear
    // con los escapes entre Python y JavaScript.
    const nombre = url.pathname.slice(url.pathname.lastIndexOf('/') + 1);
    const enRaiz = url.pathname.indexOf('/') === url.pathname.lastIndexOf('/');
    const esVerificacion = enRaiz && nombre.endsWith('.html') &&
      (nombre.startsWith('google') || nombre.startsWith('yandex_'));
    if (esVerificacion) {
      const alt = new URL('/' + nombre.slice(0, -5), url);
      const r = await env.ASSETS.fetch(new Request(alt, { method: 'GET' }));
      return blindar(new Response(r.body, { status: r.status, headers: r.headers }));
    }
    let resp = await env.ASSETS.fetch(request);
    if (resp.status === 404 && !url.pathname.split('/').pop().includes('.')) {
      const alt = new URL('/index.html', url);
      resp = await env.ASSETS.fetch(new Request(alt, { method: 'GET' }));
      if (resp.status === 200) {
        resp = new Response(resp.body, { status: 404, headers: resp.headers });
      }
    }
    return blindar(resp);
  },
};
"""

# Lo que NUNCA puede acabar aqui. docs/ se genera sin secretos, pero se
# comprueba igual: un despiste en un script futuro no debe publicar una llave.
PROHIBIDO = ("wallet/", "admin_token", "sello_key", ".db", ".log",
             "recuperacion", "identidad", "telegram")


def comprobar(destino: Path) -> None:
    malos = []
    for ruta in destino.rglob("*"):
        if not ruta.is_file():
            continue
        nombre = "/" + str(ruta.relative_to(destino)).replace("\\", "/").lower()
        if any(p in nombre for p in PROHIBIDO):
            malos.append(nombre)
    if malos:
        raise SystemExit("[desplegar] ABORTADO, esto no se publica: " + ", ".join(malos))
    grandes = [r for r in destino.rglob("*")
               if r.is_file() and r.stat().st_size > 10 * 1024 * 1024]
    if grandes:
        raise SystemExit("[desplegar] ABORTADO, fichero de mas de 10 MB: "
                         + ", ".join(r.name for r in grandes))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dominio", required=True,
                    help="URL publica del sitio, sin barra final")
    ap.add_argument("--sin-regenerar", action="store_true",
                    help="no rehace docs/, solo copia lo que hay")
    args = ap.parse_args()
    dominio = args.dominio.rstrip("/")

    if not args.sin_regenerar:
        r = subprocess.run([sys.executable, str(RAIZ / "publico" / "generar_web.py"),
                            "--canonico", dominio], cwd=str(RAIZ))
        if r.returncode != 0:
            raise SystemExit("[desplegar] generar_web.py fallo "
                             f"(exit {r.returncode}); no se publica nada a medias")

    if PUBLICO.exists():
        shutil.rmtree(PUBLICO)
    PUBLICO.mkdir(parents=True)
    for f in DOCS.iterdir():
        if f.is_file():
            shutil.copy2(f, PUBLICO / f.name)
    comprobar(PUBLICO)

    (SALIDA / "worker.js").write_text(WORKER, encoding="utf-8")
    manifest = {
        "entrypoint": "worker.js",
        "compatibilityDate": "2026-10-19",
        "modules": [{"name": "worker.js", "type": "esm", "path": "./worker.js"}],
        "assetsDirectory": "./public",
    }
    (SALIDA / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                          encoding="utf-8")

    total = sum(f.stat().st_size for f in PUBLICO.iterdir() if f.is_file())
    print(f"[desplegar] {len(list(PUBLICO.iterdir()))} ficheros, "
          f"{total / 1024 / 1024:.1f} MB -> {PUBLICO}")
    print(f"[desplegar] canonica y sitemap apuntan a {dominio}")
    print(f"[desplegar] manifiesto: {SALIDA / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
