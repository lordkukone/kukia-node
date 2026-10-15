"""KUKIA export_charts — genera datos-graficas.json desde el Core local (SOLO LECTURA).

Uso:
    python scripts/export_charts.py [--core http://127.0.0.1:8300] [--out docs/datos-graficas.json]
    python scripts/export_charts.py --cada 15 [--veces N]   (bucle local explicito)

El modo --cada repite la pasada cada N segundos (minimo 5) en esta misma
ventana: para detenerlo, Ctrl+C o cerrar la ventana. No crea tareas
programadas ni servicios. Codigo de salida 0 si hubo al menos un OK.

Origen unico: GET /ledger y GET /nodos del Core local (endpoints publicos,
verificados, de solo lectura). Tambien lee GET /stats solo para el chequeo
cruzado del suministro.

Equivalencia semantica demostrada (kukia_core.py):
- supply_emitido() = SUM(kuki) sobre TODA la tabla ledger;
- /stats devuelve min(esa suma, MAX_SUPPLY).
Por eso el chequeo cruzado compara la suma de las filas visibles con
supply_emitido SOLO cuando la ventana esta completa (ids 1..N contiguos y
menos de 200 filas, que es el tope de /ledger). Con ventana incompleta el
chequeo se degrada a aviso: la API no permite saber que falta mas alla de
la ventana (limitacion documentada, no resuelta).

Reglas:
- No inicia el Core, no abre puertos, no toca BD, ledger, wallets ni configs.
- Lista blanca estricta de campos. Sin nombres, claves, refs, TOPS,
  emails, IPs ni datos personales.
- Las filas de migracion (motivo que empieza por "migracion") se CONSERVAN
  en el fichero (verificables) pero su suma neta debe ser 0; solo la
  representacion del suministro las excluye (documentado en la web).
- Ante cualquier error o validacion fallida: conserva el fichero valido
  anterior y sale con codigo 1. Nunca inventa datos ni convierte errores
  en ceros. Los mensajes de error no incluyen filas, claves ni secretos.
"""

import argparse
import datetime
import json
import os
import sys
import time
import urllib.request

CORE_DEFAULT = "http://127.0.0.1:8300"
TIMEOUT_SEG = 10
VENTANA_MAX = 200  # tope documentado de GET /ledger (ORDER BY id DESC LIMIT 200)
TOL = 1e-6

MOT_VERIFICADO = "trabajo verificado"
PREFIJO_MIGRACION = "migracion"

MESES = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
         "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}


class FalloValidacion(Exception):
    pass


def pedir(ruta: str, core: str):
    """GET JSON del Core local. Solo lectura. Timeout corto."""
    url = core.rstrip("/") + ruta
    req = urllib.request.Request(url, headers={"User-Agent": "kukia-export-charts/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEG) as r:
        return json.loads(r.read().decode("utf-8"))


def es_numero(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def parse_ts(s):
    """'Wed Oct 14 12:06:10 2026' -> datetime o None. Sin dependencias de locale."""
    try:
        p = str(s).split(" ")
        if len(p) < 5 or p[1] not in MESES:
            return None
        h = p[3].split(":")
        return datetime.datetime(int(p[4]), MESES[p[1]], int(p[2]),
                                 int(h[0]), int(h[1]), int(h[2]))
    except Exception:
        return None


def validar_ledger(rows):
    """Estructura, tipos y fechas. Devuelve (filas, avisos). Lanza FalloValidacion."""
    if not isinstance(rows, list):
        raise FalloValidacion("/ledger no devolvio una lista")
    avisos = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            raise FalloValidacion(f"fila {i} de /ledger no es un objeto")
        for campo, tipo in (("ts", str), ("motivo", str), ("tarea_id", str), ("id", int)):
            if campo not in r:
                raise FalloValidacion(f"fila {i} de /ledger sin campo '{campo}'")
            v = r[campo]
            ok = isinstance(v, tipo) if tipo is not str else isinstance(v, str)
            if tipo is int and isinstance(v, bool):
                ok = False
            if not ok:
                raise FalloValidacion(f"fila {i} de /ledger: tipo invalido en '{campo}'")
        if not es_numero(r.get("kuki")):
            raise FalloValidacion(f"fila {i} de /ledger: 'kuki' no numerico")
    malas = sum(1 for r in rows if parse_ts(r["ts"]) is None)
    if rows and malas:
        avisos.append(f"{malas}/{len(rows)} fechas no interpretables (se omiten en graficas)")
    if malas and malas == len(rows) and rows:
        raise FalloValidacion("ninguna fecha de /ledger es interpretable")
    return rows, avisos


def validar_nodos(nodes):
    if not isinstance(nodes, list):
        raise FalloValidacion("/nodos no devolvio una lista")
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            raise FalloValidacion(f"fila {i} de /nodos no es un objeto")
        for campo in ("id", "creado"):
            if campo not in n or not isinstance(n[campo], str):
                raise FalloValidacion(f"fila {i} de /nodos sin campo '{campo}' valido")
        for campo in ("vram_gb", "tareas_ok", "reputacion"):
            if campo not in n or not es_numero(n[campo]):
                raise FalloValidacion(f"fila {i} de /nodos: '{campo}' no numerico")
        if parse_ts(n["creado"]) is None:
            raise FalloValidacion(f"fila {i} de /nodos: fecha 'creado' no interpretable")
    return nodes


def chequear_duplicados(rows):
    """Los ids de trabajo verificado deben ser unicos (una fila = un pago)."""
    vistos = set()
    for r in rows:
        if r["motivo"] == MOT_VERIFICADO:
            if r["tarea_id"] in vistos:
                raise FalloValidacion("tarea verificada duplicada (investigar antes de publicar)")
            vistos.add(r["tarea_id"])
    return len(vistos)


def chequear_migracion(rows):
    """Las filas de migracion son movimientos internos y deben compensarse (neto 0)."""
    neto = sum(r["kuki"] for r in rows
               if r["motivo"].startswith(PREFIJO_MIGRACION))
    hay = any(r["motivo"].startswith(PREFIJO_MIGRACION) for r in rows)
    if hay and abs(neto) > TOL:
        raise FalloValidacion(f"migracion con neto distinto de 0 ({neto}): no publicar")
    return neto


def ventana_completa(rows):
    """True si lo visible es TODA la tabla: menos de 200 filas, ids 1..N contiguos."""
    if len(rows) >= VENTANA_MAX:
        return False
    ids = sorted(r["id"] for r in rows)
    return bool(ids) and ids[0] == 1 and ids == list(range(1, len(ids) + 1))


def chequear_suministro(rows, stats):
    """Solo exigible con ventana completa (ver docstring). Si no, aviso honesto."""
    suma = sum(r["kuki"] for r in rows)
    supply = stats.get("supply_emitido")
    maximo = stats.get("max_supply")
    if not es_numero(supply) or not es_numero(maximo):
        raise FalloValidacion("/stats sin supply_emitido/max_supply numericos")
    if ventana_completa(rows):
        esperado = min(suma, maximo)
        if abs(esperado - supply) > TOL:
            raise FalloValidacion(
                f"suma visible ({suma}) no coincide con supply_emitido ({supply})")
        return f"chequeo cruzado OK ({suma})"
    return (f"AVISO: ventana incompleta ({len(rows)} filas visibles); "
            "el chequeo contra supply_emitido queda degradado a aviso")


def sanear(rows, nodes, ahora: datetime.datetime):
    return {
        "estado": "ok",
        "testnet": True,
        "generado": ahora.isoformat(timespec="seconds"),
        "generado_ms": int(ahora.timestamp() * 1000),
        "source_status": "ok",
        "fuente": ["GET /ledger", "GET /nodos"],
        "cobertura": (f"ultimas {len(rows)} operaciones disponibles "
                      f"(maximo {VENTANA_MAX} por respuesta); sin historico anterior"),
        "ledger": [{"t": r["ts"], "k": r["kuki"], "m": r["motivo"], "id": r["tarea_id"]}
                   for r in rows],
        "nodos": [{"id": n["id"][:8], "c": n["creado"], "v": n["vram_gb"],
                   "ok": n["tareas_ok"], "r": round(float(n["reputacion"]), 2)}
                  for n in nodes],
    }


def construir_no_disponible(ahora: datetime.datetime, detalle: str):
    return {
        "estado": "no_disponible",
        "testnet": True,
        "generado": ahora.isoformat(timespec="seconds"),
        "detalle": detalle,
        "cobertura": "sin datos",
        "ledger": [],
        "nodos": [],
    }


def fichero_valido(out: str):
    try:
        with open(out, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("ledger"), list) \
                and isinstance(d.get("nodos"), list):
            return d
    except Exception:
        pass
    return None


def escribir(out: str, data: dict) -> None:
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    with open(tmp, encoding="utf-8") as f:
        json.load(f)  # valida lo escrito antes de sustituir
    os.replace(tmp, out)


def iterar(core: str, out: str) -> bool:
    """Una pasada completa: leer, validar, escribir. True si OK."""
    ahora = datetime.datetime.now()

    def error(detalle: str):
        if fichero_valido(out):
            print(f"[export_charts] ERROR: {detalle}. Se conserva el fichero valido.",
                  file=sys.stderr)
        else:
            escribir(out, construir_no_disponible(ahora, detalle))
            print(f"[export_charts] ERROR: {detalle}. Marcador 'no_disponible' escrito.",
                  file=sys.stderr)
        return False

    try:
        rows = pedir("/ledger", core)
        nodes = pedir("/nodos", core)
        stats = pedir("/stats", core)
    except Exception as e:
        return error(f"Core sin respuesta ({e.__class__.__name__})")

    try:
        rows, avisos = validar_ledger(rows)
        nodes = validar_nodos(nodes)
        n_ver = chequear_duplicados(rows)
        neto_mig = chequear_migracion(rows)
        aviso_suministro = chequear_suministro(rows, stats)
        avisos.append(aviso_suministro)
        previo = fichero_valido(out)
        if previo is not None and len(rows) < len(previo.get("ledger", [])):
            raise FalloValidacion(
                f"ventana menor que la previa ({len(rows)} < {len(previo['ledger'])}): "
                "posible truncado")
        datos = sanear(rows, nodes, ahora)
    except FalloValidacion as e:
        return error(f"validacion fallida: {e}")

    escribir(out, datos)
    print(f"[export_charts] OK: {out} "
          f"(ledger={len(rows)}, nodos={len(nodes)}, verificadas={n_ver}, "
          f"neto_migracion={neto_mig})")
    for a in avisos:
        print(f"[export_charts] aviso: {a}")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--core", default=CORE_DEFAULT, help="URL del Core (solo lectura)")
    ap.add_argument("--out", default=None,
                    help="Ruta del datos-graficas.json (por defecto: docs/datos-graficas.json)")
    ap.add_argument("--cada", type=float, default=0,
                    help="repetir cada N segundos (0 = una sola vez). Minimo 5.")
    ap.add_argument("--veces", type=int, default=0,
                    help="nº maximo de iteraciones con --cada (0 = sin limite). "
                         "Util para pruebas. Parar con Ctrl+C.")
    args = ap.parse_args()

    if args.out:
        out = os.path.abspath(args.out)
    else:
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = os.path.join(raiz, "docs", "datos-graficas.json")

    if not args.cada:
        sys.exit(0 if iterar(args.core, out) else 1)

    if args.cada < 5:
        print("[export_charts] ERROR: --cada minimo 5 segundos.", file=sys.stderr)
        sys.exit(2)
    n_ok, i = 0, 0
    try:
        while True:
            i += 1
            print(f"[export_charts] iteracion {i} ...")
            if iterar(args.core, out):
                n_ok += 1
            if args.veces and i >= args.veces:
                break
            time.sleep(args.cada)
    except KeyboardInterrupt:
        print("[export_charts] interrumpido por el usuario.", file=sys.stderr)
    sys.exit(0 if n_ok else 1)


if __name__ == "__main__":
    main()
