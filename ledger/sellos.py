"""Sellos del ledger KUKIA: fotos firmadas e inmutables del historial.

Un sello dice, en un momento dado: "hasta el asiento N, la cadena tiene este
ultimo hash, hay estos asientos y suman este dinero". Va firmado con la clave
Ed25519 del Core y se guarda en un fichero append-only (sellos.jsonl).

Que aporta sobre la cadena de hashes que ya existia:
  - La cadena (ledger.hash_prev/hash) detecta un asiento editado a mano, pero
    el que edita puede reescribir TODA la cadena desde el punto tocado y
    volver a cuadrarla: nadie se entera.
  - Un sello firmado fija el pasado. Para reescribirlo hay que generar sellos
    nuevos y firmarlos con la clave del Core, que no esta en la base de datos.
    Y si el sello se copia fuera de la maquina (web, papel, git, Bitcoin), ni
    eso sirve: el historial viejo queda contradicho en publico.

Verificar no exige confiar en el Core: se recalcula la cadena desde el .db y
se comparan los sellos y sus firmas. Ver `python ledger/sellos.py verificar`.

Este modulo NO importa FastAPI ni toca el reloj de la red: se puede usar como
herramienta suelta sobre una copia de la base de datos.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

FORMATO = 2
# v1: sello simple. v2: ademas fija el tramo anterior a la cadena ("legado").
FORMATOS_VALIDOS = (1, 2)
# 4 decimales: es la precision con la que se mueve el dinero en el ledger
# (los montos se redondean a 4). Sin esto, sumar 80.000 asientos en float
# daria diferencias de 1e-10 y un sello "no cuadraria" por ruido.
DECIMALES = 4


# ============================================================
#  Claves del sello (Ed25519, igual que la billetera)
# ============================================================

def crear_clave(ruta) -> tuple[str, str]:
    """Crea (si no existe) la clave del sello. Devuelve (privada_hex, publica_hex)."""
    ruta = Path(ruta)
    if ruta.exists():
        priv = ruta.read_text(encoding="utf-8").strip()
        # Tambien al reutilizarla: una clave creada por una version anterior
        # pudo quedarse legible por todo el mundo.
        try:
            os.chmod(ruta, 0o600)
        except Exception:
            pass
    else:
        priv = Ed25519PrivateKey.generate().private_bytes_raw().hex()
        ruta.write_text(priv, encoding="utf-8")
        try:
            os.chmod(ruta, 0o600)
        except Exception:
            pass
    return priv, publica_de(priv)


def leer_clave(ruta) -> str:
    return Path(ruta).read_text(encoding="utf-8").strip()


def publica_de(privada_hex: str) -> str:
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(privada_hex)).public_key().public_bytes_raw().hex()


# ============================================================
#  Canonico / firma
# ============================================================

def canonico(d: dict) -> str:
    """JSON estable: las mismas claves siempre producen los mismos bytes."""
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def huella_sello(sello: dict) -> str:
    """Hash del sello SIN la firma: es lo que encadena un sello con el anterior."""
    cuerpo = {k: v for k, v in sello.items() if k != "firma"}
    return hashlib.sha256(canonico(cuerpo).encode()).hexdigest()


def _firmar(sello: dict, privada_hex: str) -> dict:
    sello = dict(sello)
    sello.pop("firma", None)
    sello["firma"] = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(privada_hex)).sign(canonico(sello).encode()).hex()
    return sello


def verificar_firma(sello: dict, publica_hex: str) -> bool:
    try:
        firma = bytes.fromhex(sello.get("firma", ""))
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(publica_hex))
        cuerpo = {k: v for k, v in sello.items() if k != "firma"}
        pub.verify(firma, canonico(cuerpo).encode())
        return True
    except Exception:
        return False


# ============================================================
#  Estado del ledger
# ============================================================

def _con(db) -> sqlite3.Connection:
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    return c


def estado_ledger(con) -> dict:
    """Ultimo eslabon de la cadena y totales, tal como estan AHORA en el .db."""
    fila = con.execute(
        "SELECT id, hash FROM ledger WHERE hash IS NOT NULL AND hash<>'' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    if not fila:
        return {"hasta": 0, "hash": "", "asientos": 0, "suma": 0.0}
    hasta = int(fila["id"])
    # AUDITORIA 21-10-2026: asientos y suma salen de UNA sola consulta y las dos
    # acotadas a id<=hasta. Antes `asientos` contaba TODOS los asientos con
    # hash y `suma` solo los de id<=hasta: si otro hilo insertaba un asiento
    # entre las dos consultas, el sello nacia con un asiento de mas y no
    # cuadraba NUNCA (paso con el sello 91: 199188 vivos y el sello firmado con
    # un asiento mas, con el hash y la suma correctos). Un sello mal firmado es
    # irreversible: los sellos no se reescriben.
    fila2 = con.execute(
        "SELECT SUM(CASE WHEN hash IS NOT NULL AND hash<>'' THEN 1 ELSE 0 END) c, "
        "COALESCE(SUM(kuki),0) s FROM ledger WHERE id<=?", (hasta,)).fetchone()
    return {"hasta": hasta, "hash": fila["hash"],
            "asientos": int(fila2["c"] or 0),
            "suma": round(float(fila2["s"] or 0.0), DECIMALES)}


def estado_legado(con) -> dict | None:
    """Huella del tramo ANTERIOR a la cadena de hashes.

    Los primeros asientos se escribieron antes de que existiera la cadena, asi
    que no tienen hash. Encadenarlos ahora obligaria a recalcular TODA la cadena
    y a invalidar los sellos ya firmados (justo lo que se quiere impedir). En su
    lugar se calcula un digest propio y viaja dentro de cada sello nuevo: desde
    ese momento, editar cualquier asiento de ese tramo deja de cuadrar.
    """
    filas = con.execute(
        "SELECT id,tarea_id,nodo_id,kuki,motivo,ts FROM ledger "
        "WHERE hash IS NULL OR hash='' ORDER BY id").fetchall()
    if not filas:
        return None
    h = ""
    suma = 0.0
    for r in filas:
        suma += float(r["kuki"] or 0.0)
        h = hashlib.sha256(
            f"{h}|{r['id']}|{r['tarea_id']}|{r['nodo_id']}|{r['kuki']}|{r['motivo']}|{r['ts']}".encode()
        ).hexdigest()
    return {"n": len(filas), "hasta": int(filas[-1]["id"]),
            "suma": round(suma, 6), "hash": h}


def nuevo_sello(con, privada_hex: str, anterior: dict | None = None) -> dict:
    """Crea y firma un sello con el estado actual del ledger."""
    est = estado_ledger(con)
    sello = {
        "v": FORMATO,
        "hasta": est["hasta"],
        "hash": est["hash"],
        "asientos": est["asientos"],
        "suma": est["suma"],
        "ts": int(time.time()),
        "prev": huella_sello(anterior) if anterior else "",
    }
    legado = estado_legado(con)
    if legado:
        sello["legado"] = legado
    return _firmar(sello, privada_hex)


# ============================================================
#  Verificacion (sin confiar en el Core)
# ============================================================

def verificar_cadena_db(con) -> dict:
    """Recalcula la cadena de hashes del .db. Devuelve el veredicto y el estado.

    Misma regla que /ledger/verificar del Core: empieza en el primer asiento
    con hash (los de versiones antiguas sin encadenar se ignoran).
    """
    prev = ""
    problemas = []
    estado = {"hasta": 0, "hash": "", "asientos": 0}
    # 27-10-2026 (H-01): recorrido en STREAMING. Con .fetchall() esta pasada
    # materializaba el ledger entero en memoria (medido: 352 MB con 400.000
    # asientos, ~725 MB con los 913.838 reales) para devolver un veredicto.
    # El calculo es identico: lo unico que cambia es que no se guardan todas
    # las filas a la vez.
    for r in con.execute(
            "SELECT id,tarea_id,nodo_id,kuki,motivo,ts,hash_prev,hash "
            "FROM ledger ORDER BY id"):
        if not r["hash"]:
            continue
        base = f"{r['tarea_id']}|{r['nodo_id']}|{r['kuki']}|{r['motivo']}|{r['ts']}|{r['hash_prev'] or ''}"
        if hashlib.sha256(base.encode()).hexdigest() != r["hash"] or (r["hash_prev"] or "") != prev:
            problemas.append(int(r["id"]))
        prev = r["hash"]
        estado = {"hasta": int(r["id"]), "hash": r["hash"], "asientos": estado["asientos"] + 1}
    return {"ok": not problemas, "ids_alterados": problemas[:50], "estado": estado}


def _parece_huella(h: str) -> bool:
    """¿Tiene forma de sha256 en hex? (64 caracteres, 0-9a-f)."""
    return len(h) == 64 and all(c in "0123456789abcdef" for c in h.lower())


def verificar_sellos(con, sellos: list, publica_hex: str,
                     cadena: dict | None = None) -> dict:
    """Comprueba la cadena de sellos Y que cada sello cuadre con el .db.

    Devuelve {ok, problemas:[...], cadena:{...}, ultimo:{...}}.

    La comparacion es EXACTA a proposito: si algo no cuadra, se bloquea. Que
    sea exacta sin falsos positivos depende de calcular la suma igual que el
    sello (ver el punto 4).
    """
    problemas = []
    # 1) Cadena de hashes del propio .db.
    # 27-10-2026 (H-01): si quien llama ya la calculo, se reutiliza. Antes esta
    # funcion volvia a recorrer el ledger entero, asi que UNA peticion a
    # /ledger/integridad lo recorria dos veces (y una tercera con la ventana).
    if cadena is None:
        cadena = verificar_cadena_db(con)
    if not cadena["ok"]:
        problemas.append(f"asientos alterados en el .db: {cadena['ids_alterados'][:10]}")
    # 2) Firmas y encadenado de los sellos.
    #
    # AUDITORIA 24-10-2026 (falso positivo): antes se exigia que el PRIMER
    # sello de la lista tuviera `prev` vacio. Eso solo es cierto si la lista
    # empieza en el sello 0. Con `--core` se piden `?limite=200` y el Core
    # devuelve los 200 ULTIMOS (hoy hay 319): el primero de los 200 trae el
    # `prev` de su antecesor, que no viaja en la respuesta, y el auditor decia
    # ALTERADO con el historial perfectamente sano. Lo mismo con un
    # sellos.jsonl rotado o recortado. Un tramo no se puede comprobar hacia
    # atras: se comprueba todo lo que SI esta (firmas, cada eslabon entre los
    # sellos presentes y cada sello contra el .db) y se avisa de que falta la
    # cabeza, en vez de inventarse un fallo.
    prev = None
    ultimo = None
    tramo = {"desde": None, "cabeza": True}
    for i, s in enumerate(sellos):
        if not isinstance(s, dict):
            problemas.append(f"sello {i}: no es un objeto")
            continue
        if not verificar_firma(s, publica_hex):
            problemas.append(f"sello {i} (hasta {s.get('hasta')}): firma invalida")
        sprev = s.get("prev") or ""
        if prev is None:
            # Primer sello de la lista: su `prev` no se puede contrastar con
            # nada. Solo se mira que, si lo trae, sea una huella con forma de
            # sha256 (un `prev` inventado no cuela como "tramo").
            tramo["desde"] = s.get("hasta")
            tramo["cabeza"] = not sprev
            if sprev and not _parece_huella(sprev):
                problemas.append(
                    f"sello 0 (hasta {s.get('hasta')}): 'prev' malformado")
        elif sprev != prev:
            problemas.append(f"sello {i} (hasta {s.get('hasta')}): no encadena con el anterior")
        if s.get("v") not in FORMATOS_VALIDOS:
            problemas.append(f"sello {i}: version {s.get('v')} desconocida")
        prev = huella_sello(s)
        ultimo = s
    # 3) El tramo anterior a la cadena, fijado dentro de los sellos v2.
    legado_actual = estado_legado(con)
    for i, s in enumerate(sellos):
        if "legado" not in s:
            continue
        if legado_actual is None or s["legado"] != legado_actual:
            problemas.append(f"sello {i}: el tramo sin cadena no coincide")

    # 4) Cada sello contra el historial real: hash, numero de asientos y suma.
    #
    # AUDITORIA 21-10-2026: la suma acumulada se calcula CON SQL, no sumando en
    # Python. Antes se hacia `acum += kuki` fila a fila mientras el sello se
    # creo con `SUM(kuki)` de SQLite; sumar en coma flotante en orden distinto
    # da diferencias de 1e-4 (paso en el sello 91: 15084442.6813 en Python
    # frente a 15084442.6814 en SQL). Eso marcaba el ledger como alterado sin
    # que nadie hubiera tocado nada, y tras un reinicio del Core bloqueaba
    # TODOS los pagos, con los sellos ya firmados y sin poder reescribirlos.
    # La ventana de SQL acumula en el mismo motor y el mismo orden que usa el
    # sello, asi que la comparacion vuelve a ser exacta.
    if sellos:
        # 27-10-2026 (H-01): solo hacen falta las filas que son corte de sello.
        # Se recorre el cursor en streaming y se guardan decenas de entradas en
        # vez de un dict con TODAS (913.838 en el ledger real). La suma la sigue
        # haciendo SQLite con la misma ventana que usa el sello, para no
        # reintroducir la deriva de coma flotante del sello 91
        # (15084442,6813 en Python frente a ,6814 en SQL).
        cortes = {int(s.get("hasta") or 0) for s in sellos if isinstance(s, dict)}
        cortes.discard(0)
        por_id = {}
        n_hash = 0
        for r in con.execute(
                "SELECT id,hash,SUM(kuki) OVER (ORDER BY id ROWS UNBOUNDED PRECEDING) s "
                "FROM ledger ORDER BY id"):
            if r["hash"]:
                n_hash += 1
            i_id = int(r["id"])
            if i_id in cortes:
                por_id[i_id] = (r["hash"], n_hash,
                                round(float(r["s"] or 0.0), DECIMALES))
        for i, s in enumerate(sellos):
            if not isinstance(s, dict):
                continue
            hasta = int(s.get("hasta") or 0)
            if hasta == 0:
                continue
            dato = por_id.get(hasta)
            if dato is None:
                problemas.append(f"sello {i}: el asiento {hasta} ya no existe")
                continue
            h, nh, suma = dato
            if h != s.get("hash") or nh != s.get("asientos") or suma != s.get("suma"):
                problemas.append(
                    f"sello {i} (hasta {hasta}): el .db no coincide "
                    f"(hash/asientos/suma)")
    return {"ok": not problemas, "problemas": problemas,
            "cadena": cadena, "ultimo": ultimo, "tramo": tramo}


# ============================================================
#  Fichero append-only
# ============================================================

def leer_sellos(ruta) -> list:
    """Lee el fichero append-only, saltandose una linea final a medias.

    DISCO LLENO (22-10-2026): una escritura que se corta deja la ultima linea
    SIN salto de linea. Esa linea nunca llego a ser un sello, pero antes se
    contaba como "ilegible" y `verificar_sellos` la tomaba por historial
    alterado: el Core bloqueaba TODOS los pagos por un sello que no existio.
    Si el fichero no acaba en salto de linea y esa ultima linea no parsea, se
    ignora. Cualquier otra linea ilegible sigue siendo un problema real.
    """
    ruta = Path(ruta)
    if not ruta.exists():
        return []
    crudo = ruta.read_text(encoding="utf-8")
    sin_cerrar = bool(crudo) and not crudo.endswith("\n")
    lineas = crudo.splitlines()
    out = []
    for n, linea in enumerate(lineas, 1):
        linea = linea.strip()
        if not linea:
            continue
        try:
            out.append(json.loads(linea))
        except Exception:
            if sin_cerrar and n == len(lineas):
                continue        # resto de una escritura cortada: no es un sello
            out.append({"v": -1, "hasta": 0, "error": f"linea {n} ilegible"})
    return out


def anadir_sello(ruta, sello: dict) -> None:
    """Anade un sello al final, con flush a disco (no se pierde si se corta).

    DISCO LLENO (22-10-2026): antes se escribia la linea de una vez y, si el
    disco se llenaba a mitad, quedaba una linea CORTADA que el siguiente
    arranque leia como historial alterado (pagos bloqueados). Ahora:
      - si el fichero no acaba en salto de linea, se mira ese resto: si es un
        sello valido solo se cierra la linea; si es una escritura cortada se
        tira (nunca fue un sello) para que no cuente como historial alterado;
      - si la escritura falla a medias, se recorta el fichero al tamano que
        tenia: o entra el sello entero, o no entra nada.
    """
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tam = ruta.stat().st_size if ruta.exists() else 0
    prefijo = ""
    if tam > 0:
        with ruta.open("rb") as f:
            crudo = f.read()
        if not crudo.endswith(b"\n"):
            corte = crudo.rfind(b"\n") + 1
            try:
                json.loads(crudo[corte:].decode("utf-8"))
                prefijo = "\n"          # sello bueno, solo le faltaba el salto
            except Exception:
                with ruta.open("r+b") as f:
                    f.truncate(corte)
                    f.flush()
                    os.fsync(f.fileno())
                tam = corte
    texto = prefijo + canonico(sello) + "\n"
    try:
        with ruta.open("a+b") as f:
            f.write(texto.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        try:
            with ruta.open("r+b") as f:
                f.truncate(tam)
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            pass
        raise


# ============================================================
#  CLI: verificar una copia del ledger sin el Core
# ============================================================

def _main(argv=None) -> int:
    raiz = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="Sellos del ledger KUKIA")
    ap.add_argument("--db", default=str(raiz / "coordinator" / "ledger" / "kukia.db"))
    ap.add_argument("--sellos", default=str(raiz / "coordinator" / "sellos.jsonl"))
    ap.add_argument("--clave", default=str(raiz / "coordinator" / "sello_key.txt"))
    ap.add_argument("--json", action="store_true", help="salida en JSON")
    args = ap.parse_args(argv)

    con = _con(args.db)
    sellos = leer_sellos(args.sellos)
    publica = publica_de(leer_clave(args.clave)) if Path(args.clave).exists() else ""
    r = verificar_sellos(con, sellos, publica) if publica else {
        "ok": False, "problemas": ["falta la clave publica del sello"], "cadena": None, "ultimo": None}

    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        c = r.get("cadena") or {}
        print(f"[KUKIA] asientos en el .db : {c.get('estado', {}).get('asientos', 0)}")
        print(f"[KUKIA] sellos            : {len(sellos)}")
        if r.get("ultimo"):
            print(f"[KUKIA] ultimo sello      : hasta {r['ultimo'].get('hasta')} "
                  f"({r['ultimo'].get('asientos')} asientos, {r['ultimo'].get('suma')} KIA)")
        print(f"[KUKIA] firma publica     : {publica}")
        if r["ok"]:
            print("[KUKIA] OK: el historial cuadra con los sellos y las firmas.")
        else:
            print("[KUKIA] ALTERADO:")
            for p in r["problemas"][:20]:
                print("  -", p)
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(_main())
