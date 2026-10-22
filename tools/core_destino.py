"""Direccion del Core: directa o por indireccion estable.

EL PROBLEMA
    El Core vive en este PC. Para que alguien de fuera mine hace falta una URL
    publica. El tunel rapido de Cloudflare (trycloudflare.com) es gratis y no
    pide cuenta, pero CAMBIA de nombre cada vez que se arranca. Si el zip que
    se descarga lleva la URL metida dentro, el paquete deja de servir a la
    primera vez que el tunel se cae y vuelve.

LA SOLUCION
    El zip no lleva la URL del tunel: lleva una direccion ESTABLE (un fichero
    pequeno en el espejo de la web) que dice cual es la URL viva en este
    momento. El supervisor del tunel (tools/tunel_estable.py) reescribe ese
    fichero cada vez que arranca o se recupera, asi que la direccion del zip
    no caduca aunque el tunel cambie de nombre.

FORMATO DE core_minero.txt (solo cuenta la primera linea que no empieza por #)
    http://127.0.0.1:8300                                         minar en este PC
    https://algo.trycloudflare.com                                una URL fija
    auto https://kukia-network.surge.sh/core_actual.json          seguir el espejo
    auto                                                          espejo de serie

USO
    python tools/core_destino.py --url          imprime la URL resuelta
    python tools/core_destino.py --explicar     cuenta de donde sale
    python tools/core_destino.py --url --fichero otro_core.txt

Sale con 0 si hay URL util, 2 si no se pudo averiguar (espejo caido y sin
copia guardada). Nunca adivina: si no lo sabe, lo dice.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ESPEJO = "https://kukia-network.surge.sh/core_actual.json"
LOOPBACK = "http://127.0.0.1:8300"
FICHERO = RAIZ / "core_minero.txt"
CACHE = RAIZ / "logs" / "core_actual_cache.txt"
# Palabras que marcan "no es una URL, es una indireccion".
AUTO = ("auto", "estable")
LOCALES = ("127.0.0.1", "localhost", "::1", "[::1]")
TIEMPO = 8.0


class CoreNoEncontrado(Exception):
    """No se pudo averiguar la direccion publica del Core."""


def _limpia(url: str) -> str:
    return (url or "").strip().strip('"').strip("'").rstrip("/")


def valida(url: str):
    """(True, "") si la URL sirve para hablar con un Core. (False, motivo) si no."""
    u = urllib.parse.urlsplit(_limpia(url))
    if u.scheme not in ("http", "https") or not u.hostname:
        return False, "tiene que empezar por http:// o https:// y llevar un host"
    if u.username or u.password:
        return False, "no puede llevar usuario ni contrasena dentro de la URL"
    if u.scheme == "http" and (u.hostname or "").lower() not in LOCALES:
        if os.environ.get("KUKIA_CORE_INSEGURO", "0") != "1":
            return False, ("va SIN cifrar hacia un Core remoto: cualquiera en la red "
                           "puede leer las firmas y alterar el trabajo. Usa https://")
    return True, ""


def es_indirecta(valor: str) -> bool:
    partes = (valor or "").strip().split(None, 1)
    return bool(partes) and partes[0].lower() in AUTO


def _destino(valor: str) -> str:
    """URL del fichero que dice cual es el Core vivo (por defecto, el espejo)."""
    partes = (valor or "").strip().split(None, 1)
    return _limpia(partes[1]) if len(partes) > 1 else ESPEJO


def _extrae(crudo: str) -> str:
    """Saca la URL del fichero estable: JSON {\"core\": ...} o texto plano."""
    crudo = (crudo or "").strip()
    if not crudo:
        raise CoreNoEncontrado("el fichero de direccion esta vacio")
    if crudo[0] in "{[":
        try:
            datos = json.loads(crudo)
        except Exception as e:
            raise CoreNoEncontrado(f"el fichero de direccion no es JSON valido: {e}")
        if isinstance(datos, dict):
            for clave in ("core", "url", "core_url", "direccion"):
                if datos.get(clave):
                    return _limpia(str(datos[clave]))
        raise CoreNoEncontrado("el JSON no trae el campo 'core'")
    for linea in crudo.splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#"):
            return _limpia(linea)
    raise CoreNoEncontrado("el fichero de direccion no tiene ninguna linea util")


def _descargar(url: str, tiempo: float) -> str:
    # El espejo esta detras de un CDN y aqui interesa lo de AHORA, no lo de
    # hace diez minutos: se pide con Cache-Control y con un sufijo que cambia
    # en cada consulta, que es lo unico que algunos CDN respetan de verdad.
    sep = "&" if "?" in url else "?"
    real = f"{url}{sep}_kukia={int(time.time())}"
    pet = urllib.request.Request(real, headers={
        "User-Agent": "kukia-core-destino/1.0",
        "Cache-Control": "no-cache",
        "Accept": "application/json, text/plain, */*",
    })
    with urllib.request.urlopen(pet, timeout=tiempo) as r:
        return _extrae(r.read(16384).decode("utf-8", "replace"))


def _cache_leer(origen: str) -> str:
    try:
        for linea in CACHE.read_text(encoding="utf-8").splitlines():
            partes = linea.split("\t")
            if len(partes) >= 2 and partes[0] == origen:
                return _limpia(partes[1])
    except Exception:
        pass
    return ""


def _cache_escribir(origen: str, core: str) -> None:
    """Guarda la ultima direccion buena: si el espejo se cae, se sigue minando."""
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        lineas = {}
        if CACHE.exists():
            for linea in CACHE.read_text(encoding="utf-8").splitlines():
                partes = linea.split("\t")
                if len(partes) >= 2:
                    lineas[partes[0]] = partes[1]
        lineas[origen] = core
        CACHE.write_text("".join(f"{o}\t{c}\n" for o, c in lineas.items()),
                         encoding="utf-8")
    except Exception:
        pass


# Ultima respuesta por origen, en memoria: el guardia pregunta cada 30 s y no
# hace falta bajar el fichero del espejo en cada vuelta.
_MEMO = {}
MEMO_SEG = 60.0


def resolver(valor: str, tiempo: float = TIEMPO, avisar: bool = True,
             ttl: float = MEMO_SEG) -> str:
    """URL del Core a partir de lo que diga core_minero.txt.

    Acepta una URL directa o una indireccion ('auto ...'). Lanza
    CoreNoEncontrado solo si no hay forma de saberlo. `ttl` es cuanto vale la
    respuesta ya bajada (0 = preguntar al espejo ahora mismo).
    """
    valor = _limpia(valor)
    if not valor:
        return LOOPBACK
    if not es_indirecta(valor):
        ok, motivo = valida(valor)
        if not ok:
            raise CoreNoEncontrado(f"la direccion '{valor}' {motivo}")
        return valor

    origen = _destino(valor)
    guardado = _MEMO.get(origen)
    if ttl > 0 and guardado and time.time() - guardado[0] < ttl:
        return guardado[1]
    try:
        core = _descargar(origen, tiempo)
        ok, motivo = valida(core)
        if not ok:
            raise CoreNoEncontrado(f"el espejo publica '{core}' y esa direccion {motivo}")
        _cache_escribir(origen, core)
        _MEMO[origen] = (time.time(), core)
        return core
    except CoreNoEncontrado:
        raise
    except Exception as e:
        # Espejo inalcanzable (sin red, CDN caido, DNS): vale la ultima buena.
        guardada = _cache_leer(origen) or (guardado[1] if guardado else "")
        if guardada:
            ok, _ = valida(guardada)
            if ok:
                if avisar:
                    print(f"[kukia] no pude leer {origen} ({type(e).__name__}); uso la "
                          f"ultima direccion buena: {guardada}", file=sys.stderr)
                return guardada
        raise CoreNoEncontrado(f"no pude leer {origen}: {type(e).__name__}: {e}")


def leer_fichero(ruta=None) -> str:
    """Primera linea util de core_minero.txt (o la de por defecto si no hay)."""
    f = Path(ruta) if ruta else FICHERO
    try:
        for linea in f.read_text(encoding="utf-8", errors="replace").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#"):
                return linea
    except Exception:
        pass
    return LOOPBACK


def url_core(ruta=None, tiempo: float = TIEMPO) -> str:
    """Direccion ya resuelta, leyendo core_minero.txt. Lanza CoreNoEncontrado."""
    return resolver(leer_fichero(ruta), tiempo=tiempo)


def url_core_suave(ruta=None, tiempo: float = TIEMPO, por_defecto: str = LOOPBACK) -> str:
    """Como url_core, pero si no se puede resolver devuelve por_defecto y avisa.

    Pensado para procesos que no deben morir por un espejo caido: el minero
    sigue vivo, reintenta, y en cuanto el espejo vuelve encuentra el Core.
    """
    try:
        return url_core(ruta, tiempo=tiempo)
    except CoreNoEncontrado as e:
        print(f"[kukia] AVISO: {e}", file=sys.stderr)
        return por_defecto


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ruta = None
    if "--fichero" in argv:
        i = argv.index("--fichero")
        if i + 1 < len(argv):
            ruta = argv[i + 1]
            del argv[i:i + 2]

    crudo = leer_fichero(ruta)
    try:
        core = resolver(crudo)
    except CoreNoEncontrado as e:
        if "--explicar" in argv:
            print(f"core_minero.txt dice: {crudo}")
        print(f"[ERROR] {e}", file=sys.stderr)
        print("        Revisa tu conexion, o pon la URL del Core a mano en "
              "core_minero.txt", file=sys.stderr)
        return 2

    if "--explicar" in argv:
        como = (f"siguiendo la direccion estable {_destino(crudo)}" if es_indirecta(crudo)
                else "escrita a mano")
        print(f"core_minero.txt dice: {crudo}", file=sys.stderr)
        print(f"Core: {core}  ({como})", file=sys.stderr)
    else:
        print(core)
    return 0


if __name__ == "__main__":
    sys.exit(main())
