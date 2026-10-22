#!/usr/bin/env python3
"""Ancla el ultimo sello del ledger KUKIA en la cadena de BITCOIN.

POR QUE ESTO ES LO MAS IMPORTANTE QUE FALTA (21-10-2026): hoy el ancla la
publica el rele, y el rele es de la misma persona que produce los sellos. Si
alguien controla ese PC, reescribe el ledger, rehace la cadena y firma sellos
nuevos: el rele publica el ancla nueva y todo "cuadra". Los sellos sirven para
DETECTAR una edicion, no para impedirla.

OpenTimestamps manda el sha256 del sello a tres calendarios independientes que
lo meten en un arbol de Merkle y lo atan a un bloque de Bitcoin. A partir de
ahi, el sello queda fechado por una red que no es tuya: ni tu ni el rele podeis
mover esa fecha.

MODO DE USO
    python tools/anclar_ots.py                 # ancla el ultimo sello (si es nuevo)
    python tools/anclar_ots.py --actualizar    # pregunta a los calendarios si ya hay bloque
    python tools/anclar_ots.py --verificar     # comprueba contra el bloque de Bitcoin
    python tools/anclar_ots.py --estado        # que hay anclado y en que punto esta

FICHEROS
    anclas/kukia_sello_<N>.txt        el sello en texto (hash, asientos, suma, firma)
    anclas/kukia_sello_<N>.txt.ots    la prueba OpenTimestamps (formato oficial)
    anclas/registro.jsonl             una linea por anclaje (digest, calendarios, fecha)
    anclas/cabeceras/<altura>.hex     cabecera del bloque, para verificar sin red

La prueba .ots se puede verificar tambien con el cliente oficial:
    ots verify anclas/kukia_sello_<N>.txt.ots

Salida: 0 si todo correcto, 2 si no habia nada nuevo que anclar, 3 si fallo.
"""
import argparse
import hashlib
import io
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

ANCLAS = RAIZ / "anclas"
CABECERAS = ANCLAS / "cabeceras"
REGISTRO = ANCLAS / "registro.jsonl"
SELLOS_LOCAL = RAIZ / "coordinator" / "sellos.jsonl"
CORE = (os.environ.get("KUKIA_CORE", "") or "").strip() or None

CALENDARIOS = (
    "https://alice.btc.calendar.opentimestamps.org",
    "https://bob.btc.calendar.opentimestamps.org",
    "https://finney.calendar.eternitywall.com",
)
# Exploradores publicos para bajar la cabecera del bloque. Se prueban en orden.
EXPLORADORES = (
    "https://mempool.space/api",
    "https://blockstream.info/api",
)

UA = "KUKIA-anclaje/1.0"

# Presupuesto de una pasada de --actualizar. El guardia lo llama cada ~14 min
# con un tope de 240 s: si el script se pasa de ahi, lo mata y no guarda nada.
# Con 150 s de red queda margen de sobra y siempre se cierra a tiempo.
PRESUPUESTO_SEG = max(20.0, float(os.environ.get("KUKIA_ANCLA_SEG", "150")))


def _core() -> str:
    if CORE:
        return CORE.rstrip("/")
    f = RAIZ / "core_minero.txt"
    try:
        for linea in f.read_text(encoding="utf-8", errors="replace").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#"):
                return linea.rstrip("/")
    except Exception:
        pass
    return "http://127.0.0.1:8300"


def sello_actual() -> dict:
    """Ultimo sello firmado. Del Core si responde; si no, del fichero local."""
    try:
        req = urllib.request.Request(_core() + "/ledger/sellos?limite=1",
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=8) as r:
            ss = (json.loads(r.read().decode("utf-8", "replace")) or {}).get("sellos") or []
        if ss:
            return ss[-1]
    except Exception:
        pass
    try:
        lineas = [l for l in SELLOS_LOCAL.read_text(encoding="utf-8").splitlines() if l.strip()]
        return json.loads(lineas[-1])
    except Exception as e:
        raise RuntimeError(f"no pude leer ningun sello ({e})")


def compromiso(sello: dict) -> str:
    """Texto exacto que se ancla. Legible a proposito: quien lo lea ve el sello."""
    from ledger import sellos as S
    cuerpo = {k: v for k, v in sello.items() if k != "firma"}
    return (
        "KUKIA - sello del ledger (anclado en Bitcoin con OpenTimestamps)\n"
        f"hasta      : {sello.get('hasta')}\n"
        f"asientos   : {sello.get('asientos')}\n"
        f"suma_kia   : {sello.get('suma')}\n"
        f"hash_ultimo: {sello.get('hash_ultimo')}\n"
        f"firma      : {sello.get('firma')}\n"
        f"canonico   : {S.canonico(cuerpo)}\n"
    )


def _pedir(url: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _guardar_ots(ts, ruta: Path) -> None:
    from opentimestamps.core.op import OpSHA256
    from opentimestamps.core.serialize import StreamSerializationContext
    from opentimestamps.core.timestamp import DetachedTimestampFile
    buf = io.BytesIO()
    DetachedTimestampFile(OpSHA256(), ts).serialize(StreamSerializationContext(buf))
    ruta.write_bytes(buf.getvalue())


def _leer_ots(ruta: Path):
    from opentimestamps.core.serialize import StreamDeserializationContext
    from opentimestamps.core.timestamp import DetachedTimestampFile
    return DetachedTimestampFile.deserialize(
        StreamDeserializationContext(io.BytesIO(ruta.read_bytes())))


def anclar() -> int:
    from opentimestamps.calendar import RemoteCalendar
    from opentimestamps.core.timestamp import Timestamp

    sello = sello_actual()
    hasta = str(sello.get("hasta"))
    txt = ANCLAS / f"kukia_sello_{hasta}.txt"
    ots = ANCLAS / f"kukia_sello_{hasta}.txt.ots"
    ANCLAS.mkdir(parents=True, exist_ok=True)

    if ots.exists():
        print(f"[ancla] el sello {hasta} ya esta anclado ({ots.name}); nada que hacer")
        return 0

    texto = compromiso(sello)
    # write_bytes, NO write_text: en Windows write_text convierte \n en \r\n y
    # el sha256 del fichero deja de coincidir con el que se anclo (fallo real
    # de la primera version: --verificar decia "el .txt se edito despues"
    # cuando el unico cambio lo habia hecho el propio Windows al escribirlo).
    txt.write_bytes(texto.encode("utf-8"))
    digest = hashlib.sha256(txt.read_bytes()).digest()

    ts = Timestamp(digest)
    buenos = []
    for url in CALENDARIOS:
        try:
            r = RemoteCalendar(url).submit(digest, timeout=20)
            if r is not None:
                ts.merge(r)
                buenos.append(url)
        except Exception as e:
            print(f"[ancla] calendario no disponible: {url} ({type(e).__name__})")
    if not buenos:
        print("[ancla] ERROR: ningun calendario acepto el sello. Sin red o todos caidos.")
        try:
            txt.unlink()
        except Exception:
            pass
        return 3

    _guardar_ots(ts, ots)
    registro = {
        "fecha": time.strftime("%Y-%m-%d %H:%M:%S"),
        "hasta": sello.get("hasta"),
        "asientos": sello.get("asientos"),
        "suma": sello.get("suma"),
        "hash_ultimo": sello.get("hash_ultimo"),
        "digest_sha256": digest.hex(),
        "calendarios": buenos,
        "fichero": txt.name,
    }
    with REGISTRO.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")

    print(f"[ancla] sello {hasta} enviado a {len(buenos)} calendario(s)")
    print(f"[ancla] digest sha256 : {digest.hex()}")
    print(f"[ancla] prueba        : {ots.relative_to(RAIZ)}")
    print("[ancla] queda PENDIENTE hasta que un bloque de Bitcoin lo confirme "
          "(normalmente 1-2 h). Luego: python tools/anclar_ots.py --actualizar")
    return 0


def _confirmados(ts) -> list:
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation
    return [(msg, a) for msg, a in ts.all_attestations()
            if isinstance(a, BitcoinBlockHeaderAttestation)]


def _nodos(raiz):
    """Todos los nodos del arbol de una prueba: la raiz y cada rama.

    Hace falta recorrerlos porque la attestation pendiente NO cuelga de la
    raiz: cada calendario mete su rama (con su operacion) y deja el aviso al
    final. Fusionar en la raiz una prueba de un nodo de mas abajo lanza
    "Can't merge timestamps for different messages".
    """
    pila = [raiz]
    while pila:
        nodo = pila.pop()
        yield nodo
        pila.extend(getattr(nodo, "ops", {}).values())


def actualizar() -> int:
    """Re-pregunta a los calendarios por las pruebas pendientes y las fusiona.

    FALLO ARREGLADO (21-10-2026): se fusionaba el bloque de Bitcoin en la raiz
    de la prueba en vez de en el nodo que el calendario habia devuelto, asi que
    TODAS las fusiones lanzaban ValueError, se tragaban en silencio y ninguna
    prueba llegaba a confirmarse: 90 anclajes "pendientes" para siempre. Ahora
    se busca el nodo con el mensaje correcto, se fusiona ahi y se guarda.

    Ademas hay presupuesto de tiempo: con 90 pruebas x 3 calendarios la pasada
    duraba mas que el tope del guardia (240 s), que lo mataba antes de guardar.
    """
    from opentimestamps.calendar import RemoteCalendar
    from opentimestamps.core.notary import PendingAttestation

    pruebas = sorted(ANCLAS.glob("kukia_sello_*.txt.ots"))
    if not pruebas:
        print("[ancla] no hay ninguna prueba .ots todavia")
        return 2
    limite = time.time() + PRESUPUESTO_SEG
    cambios = 0
    confirmadas = 0
    pendientes_total = 0
    sin_tiempo = 0
    for ruta in pruebas:
        try:
            archivo = _leer_ots(ruta)
        except Exception as e:
            print(f"[ancla] {ruta.name}: no se pudo leer ({e})")
            continue
        ts = archivo.timestamp
        # Una prueba ya confirmada por un calendario esta probada: no gasta red.
        if _confirmados(ts):
            continue
        if time.time() > limite:
            sin_tiempo += 1
            pendientes_total += 1
            continue
        antes = len(_confirmados(ts))
        pendientes = 0
        for nodo in list(_nodos(ts)):
            for att in list(nodo.attestations):
                if not isinstance(att, PendingAttestation):
                    continue
                if time.time() > limite:
                    break
                try:
                    nuevo = RemoteCalendar(att.uri).get_timestamp(nodo.msg, timeout=20)
                except Exception:
                    pendientes += 1
                    continue
                if nuevo is not None and nuevo.msg == nodo.msg:
                    nodo.merge(nuevo)
                else:
                    pendientes += 1
        if len(_confirmados(ts)) > antes:
            _guardar_ots(ts, ruta)
            cambios += 1
        conf = _confirmados(ts)
        if conf:
            confirmadas += 1
            alturas = sorted({a.height for _, a in conf})
            print(f"[ancla] {ruta.name}: CONFIRMADO en bloque(es) {alturas}")
        else:
            pendientes_total += 1
            print(f"[ancla] {ruta.name}: aun pendiente "
                  f"({pendientes} calendario/s sin bloque)")
    if cambios:
        print(f"[ancla] {cambios} prueba(s) actualizada(s) en disco")
    if sin_tiempo:
        print(f"[ancla] presupuesto de {PRESUPUESTO_SEG:.0f} s agotado: "
              f"quedan {sin_tiempo} sin mirar, seguimos en la proxima pasada")
    print(f"[ancla] resumen: {confirmadas} confirmadas, {pendientes_total} pendientes")
    return 0 if (cambios or pruebas) else 2


def _cabecera(altura: int) -> bytes:
    """Cabecera de 80 bytes del bloque `altura`, con cache local."""
    CABECERAS.mkdir(parents=True, exist_ok=True)
    cache = CABECERAS / f"{altura}.hex"
    if cache.exists():
        return bytes.fromhex(cache.read_text(encoding="utf-8").strip())
    errores = []
    for api in EXPLORADORES:
        try:
            h = _pedir(f"{api}/block-height/{altura}").decode().strip()
            hexhdr = _pedir(f"{api}/block/{h}/header").decode().strip()
            cab = bytes.fromhex(hexhdr)
            if len(cab) != 80:
                raise ValueError(f"cabecera de {len(cab)} bytes")
            cache.write_text(hexhdr, encoding="utf-8")
            return cab
        except Exception as e:
            errores.append(f"{api}: {type(e).__name__}")
    raise RuntimeError("no pude bajar la cabecera (" + ", ".join(errores) + ")")


def verificar() -> int:
    """Comprueba la prueba contra el bloque real de Bitcoin (raiz de Merkle)."""
    pruebas = sorted(ANCLAS.glob("kukia_sello_*.txt.ots"))
    if not pruebas:
        print("[ancla] no hay ninguna prueba .ots todavia")
        return 2
    fallos = 0
    revisadas = 0
    for ruta in pruebas:
        txt = Path(str(ruta)[: -len(".ots")])
        if not txt.exists():
            print(f"[ancla] {ruta.name}: falta {txt.name}")
            fallos += 1
            continue
        digest = hashlib.sha256(txt.read_bytes()).digest()
        archivo = _leer_ots(ruta)
        if archivo.file_digest != digest:
            # El .txt se ha cambiado despues de anclar: la prueba ya no habla de el.
            print(f"[ancla] {ruta.name}: el .txt NO coincide con el anclado (se edito despues)")
            fallos += 1
            continue
        conf = _confirmados(archivo.timestamp)
        if not conf:
            print(f"[ancla] {ruta.name}: pendiente (todavia sin bloque)")
            continue
        revisadas += 1
        for msg, att in conf:
            try:
                cab = _cabecera(att.height)
            except Exception as e:
                print(f"[ancla] {ruta.name}: {e}")
                fallos += 1
                continue
            raiz = cab[36:68]          # merkle root, orden del bloque
            if msg != raiz:
                print(f"[ancla] {ruta.name}: FALLO en bloque {att.height} "
                      f"(raiz {raiz.hex()[:16]}... != {msg.hex()[:16]}...)")
                fallos += 1
            else:
                print(f"[ancla] {ruta.name}: OK en bloque {att.height} "
                      f"(raiz {raiz.hex()[:16]}...)")
    if fallos:
        print(f"[ancla] {fallos} problema(s)")
        return 3
    if not revisadas:
        print("[ancla] nada confirmado aun: vuelve a intentarlo en un rato")
        return 2
    print(f"[ancla] {revisadas} prueba(s) verificada(s) contra Bitcoin")
    return 0


def autoprueba() -> int:
    """Comprueba la verificacion contra un bloque REAL, sin esperar a que toque.

    Construye una prueba en memoria cuya raiz de Merkle es la del ultimo bloque
    de Bitcoin y otra con una raiz falsa, y pasa las dos por el mismo codigo
    que usa --verificar. Asi se sabe hoy que la comprobacion funciona, en vez
    de descubrirlo cuando por fin llegue el bloque (1-2 h despues).
    """
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation
    from opentimestamps.core.timestamp import Timestamp

    altura = int(_pedir(EXPLORADORES[0] + "/blocks/tip/height").decode().strip())
    cab = _cabecera(altura)
    raiz = cab[36:68]
    buena = Timestamp(raiz)
    buena.attestations.add(BitcoinBlockHeaderAttestation(altura))
    falsa = Timestamp(bytes(32))
    falsa.attestations.add(BitcoinBlockHeaderAttestation(altura))

    ok = _confirmados(buena)[0][0] == raiz
    rechaza = _confirmados(falsa)[0][0] != raiz
    print(f"[autoprueba] bloque {altura} raiz {raiz.hex()[:16]}...")
    print(f"  {'[OK]  ' if ok else '[FALLO]'} una prueba con la raiz real se acepta")
    print(f"  {'[OK]  ' if rechaza else '[FALLO]'} una prueba con raiz falsa se rechaza")
    return 0 if (ok and rechaza) else 3


def estado() -> int:
    pruebas = sorted(ANCLAS.glob("kukia_sello_*.txt.ots"))
    if not pruebas:
        print("[ancla] sin anclajes")
        return 2
    for ruta in pruebas:
        conf = _confirmados(_leer_ots(ruta).timestamp)
        if conf:
            alturas = sorted({a.height for _, a in conf})
            print(f"  {ruta.name}: confirmado en bloque(s) {alturas}")
        else:
            print(f"  {ruta.name}: pendiente")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Ancla los sellos de KUKIA en Bitcoin")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--actualizar", action="store_true", help="pedir el bloque a los calendarios")
    g.add_argument("--verificar", action="store_true", help="comprobar contra Bitcoin")
    g.add_argument("--estado", action="store_true", help="resumen de lo anclado")
    g.add_argument("--autoprueba", action="store_true",
                   help="prueba la verificacion contra el ultimo bloque de Bitcoin")
    args = ap.parse_args()
    try:
        import opentimestamps  # noqa: F401
    except ImportError:
        print("[ancla] falta la libreria: python -m pip install opentimestamps-client")
        return 3
    try:
        if args.actualizar:
            return actualizar()
        if args.verificar:
            return verificar()
        if args.estado:
            return estado()
        if args.autoprueba:
            return autoprueba()
        return anclar()
    except Exception as e:
        print(f"[ancla] ERROR: {type(e).__name__}: {e}")
        return 3


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
