"""F2-14 â€” CICLO DE VIDA DE CLAVES Ed25519 (candidato, NO aplicado al Core).

Este modulo es la pieza central del parche F2-14. Se copiaria a
`coordinator/claves.py` y el Core lo usaria en tres puntos:

  1. `schema()`            -> claves.crear_esquema(con)
  2. verificacion de resultado (:2747-2752) -> claves.verificar_firma_resultado(...)
  3. endpoints nuevos      -> reto/rotacion, recuperacion y configuracion

Nada de esto esta aplicado. El modulo es autocontenido: recibe siempre una
conexion sqlite3 ya abierta, no importa `kukia_core` y no conoce rutas.

Estados de una clave (F2-14 Â§3):
    active           -> autorizada
    revoked          -> anulada (compromiso o revocacion explicita)
    replaced         -> sustituida por otra (rotacion planificada)
    recovery_pending -> nueva clave a la espera de confirmacion (solo perdida)

Garantias:
  - Como maximo UNA clave `active` por nodo (lo impone la transaccion).
  - Toda transicion se audita en `nodos_eventos`.
  - Las operaciones son idempotentes ante reintentos.
  - No se guardan claves privadas ni secretos en claro.
  - No se toca la direccion de cobro al recuperar.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from typing import Iterable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# --- parametros -----------------------------------------------------------
TTL_RETO = 300.0                 # un reto de rotacion caduca en 5 min
GRACIA_MAXIMA = 3600.0           # solo rotacion planificada
MAX_INTENTOS_RECUPERACION = 5    # por nodo y ventana
VENTANA_INTENTOS = 3600.0
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1   # ~16 MB, stdlib
LARGO_SECRETO_MIN = 32           # el secreto lo genera la app, 32+ chars

# F2-22: cuota de emision del reto de configuracion (anti-inundacion).
# Se aplican dentro del mismo BEGIN IMMEDIATE que inserta el reto, asi que dos
# procesos no pueden sobrepasarlas. Los limites son deliberadamente holgados:
# una granja legitima de 40 nodos tras una sola IP necesita ~40 emisiones.
# 0 (o negativo) desactiva el cupo correspondiente, como en KUKIA_REGISTRO_CUPO_*.
def _num_env(nombre, defecto, tipo=float):
    try:
        return tipo(os.environ.get(nombre, defecto))
    except (TypeError, ValueError):
        return tipo(defecto)


RETO_VENTANA = max(1.0, _num_env("KUKIA_RETO_VENTANA", 300.0))
RETO_CUPO_PENDIENTES = max(0, _num_env("KUKIA_RETO_CUPO_PENDIENTES", 8, int))
RETO_CUPO_NODO = max(0, _num_env("KUKIA_RETO_CUPO_NODO", 12, int))
RETO_CUPO_IP = max(0, _num_env("KUKIA_RETO_CUPO_IP", 60, int))

# F2-23: la rotacion tiene presupuesto propio (`rot:`) y numeros mas holgados.
# Rotar es raro, pero las herramientas de operador preparan varios intentos por
# nodo (y las pruebas de carrera piden 10 de golpe); no debe ser mas estrecho
# que inscribir el secreto, que es una operacion de una sola vez.
RETO_ROT_CUPO_PENDIENTES = max(0, _num_env("KUKIA_RETO_ROT_CUPO_PENDIENTES", 16, int))
RETO_ROT_CUPO_NODO = max(0, _num_env("KUKIA_RETO_ROT_CUPO_NODO", 24, int))
RETO_ROT_CUPO_IP = max(0, _num_env("KUKIA_RETO_ROT_CUPO_IP", 120, int))

PREFIJO = b"kukia-f2-14-v1"
PREFIJO_CONFIG = b"kukia-f2-21-config-v1"   # dominio propio; incluye reto de un solo uso
PREFIJO_RESULTADO_V2 = "resultado:v2"   # identico a F2-13: los mineros ya lo conocen
# F2-29: la replica tiene dominio propio. Una firma de resultado primario no
# sirve como replica ni al reves, aunque el nodo, la tarea y el texto sean los
# mismos: el dominio va dentro del mensaje firmado, no en una convencion.
PREFIJO_REPLICA_V2 = "replica:v2"

ESTADOS = ("active", "revoked", "replaced", "recovery_pending")

DDL = [
    "CREATE TABLE IF NOT EXISTS claves_nodo("
    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "nodo_id TEXT NOT NULL,"
    "clave_publica TEXT NOT NULL,"
    "estado TEXT NOT NULL,"
    "desde REAL NOT NULL,"
    "hasta REAL,"
    "gracia_hasta REAL,"
    "motivo TEXT,"
    "recuperacion_hash BLOB,"
    "recuperacion_sal BLOB,"
    "recuperacion_n INTEGER,"
    "recuperacion_r INTEGER,"
    "recuperacion_p INTEGER,"
    "UNIQUE(nodo_id, clave_publica))",

    "CREATE UNIQUE INDEX IF NOT EXISTS ux_claves_una_activa "
    "ON claves_nodo(nodo_id) WHERE estado='active'",

    "CREATE INDEX IF NOT EXISTS idx_claves_nodo ON claves_nodo(nodo_id, estado)",

    "CREATE TABLE IF NOT EXISTS claves_retos("
    "reto TEXT PRIMARY KEY,"
    "nodo_id TEXT NOT NULL,"
    "tipo TEXT NOT NULL,"
    "expira REAL NOT NULL,"
    "usado INTEGER NOT NULL DEFAULT 0)",

    "CREATE TABLE IF NOT EXISTS retos_cuota("
    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "clave TEXT NOT NULL,"
    "ts REAL NOT NULL)",

    "CREATE INDEX IF NOT EXISTS idx_retos_cuota ON retos_cuota(clave, ts)",

    "CREATE INDEX IF NOT EXISTS idx_retos_cuota_ts ON retos_cuota(ts)",

    "CREATE TABLE IF NOT EXISTS recuperacion_intentos("
    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "nodo_id TEXT NOT NULL,"
    "ts REAL NOT NULL)",

    "CREATE TABLE IF NOT EXISTS nodos_eventos("
    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "nodo_id TEXT,"
    "tipo TEXT NOT NULL,"
    "detalle TEXT,"
    "ts REAL NOT NULL)",

    "CREATE INDEX IF NOT EXISTS idx_eventos_nodo ON nodos_eventos(nodo_id, ts)",
]


# --- utilidades -----------------------------------------------------------
def clave_valida(clave: str) -> bool:
    """Clave publica Ed25519: 32 bytes en hex (mismo criterio que el Core)."""
    try:
        return len(bytes.fromhex((clave or "").strip())) == 32
    except ValueError:
        return False


def canon(*campos) -> bytes:
    """Codificacion canonica: prefijo + longitud en bytes por campo.

    `len(campo):campo` para cada uno evita cualquier ambiguedad por separadores
    (':', '|', saltos de linea) sin depender del charset de los datos.
    """
    partes = [PREFIJO]
    for c in campos:
        b = str(c).encode("utf-8")
        partes.append(str(len(b)).encode("ascii") + b":" + b)
    return b"|".join(partes)


def canon_configuracion(nodo_id, reto, secreto, ts) -> bytes:
    """Mensaje que firma la clave ACTIVA para inscribir el secreto.

    Incluye el reto de un solo uso, el nodo, el SHA-256 del secreto (nunca el
    secreto) y un dominio propio: una firma de rotacion o recuperacion no sirve
    aqui, ni al reves, ni una firma capturada se puede reenviar.
    """
    h = hashlib.sha256((secreto or "").encode("utf-8")).hexdigest()
    partes = [PREFIJO_CONFIG]
    for c in ("configurar", nodo_id, reto, h, int(ts)):
        b = str(c).encode("utf-8")
        partes.append(str(len(b)).encode("ascii") + b":" + b)
    return b"|".join(partes)


def canon_rotacion(nodo_id, reto, pub_vieja, pub_nueva, tipo, ts) -> bytes:
    return canon("rotar", nodo_id, reto, pub_vieja, pub_nueva, tipo, int(ts))


def canon_recuperacion(nodo_id, pub_nueva, tipo, ts) -> bytes:
    return canon("recuperar", nodo_id, pub_nueva, tipo, int(ts))


def canon_confirmacion(nodo_id, pub_nueva, ts) -> bytes:
    return canon("confirmar", nodo_id, pub_nueva, int(ts))


def canon_resultado_v2(nodo_id: str, tid: str, metrica: str) -> bytes:
    """Identico al v2 de F2-13 (los mineros ya lo implementan)."""
    nb, tb = nodo_id.encode(), tid.encode()
    h = hashlib.sha256(metrica.encode("utf-8")).hexdigest()
    return f"{PREFIJO_RESULTADO_V2}:{len(nb)}:{nodo_id}:{len(tb)}:{tid}:{h}".encode()


def canon_resultado_v1(tid: str, metrica: str) -> bytes:
    return f"{tid}:{metrica}".encode()


def canon_replica_v2(nodo_id: str, tid: str, metrica: str) -> bytes:
    """F2-29: mensaje de la REPLICA, con dominio propio y longitud por campo.

    Dos diferencias con el v1 que usaba el Core:
    - prefijo `replica:v2`, para que una firma de resultado primario no
      valga aqui ni al reves;
    - longitud en bytes por campo, porque `tid:metrica` era ambiguo:
      tid="a:b", metrica="c" y tid="a", metrica="b:c" daban el MISMO
      mensaje y por tanto la misma firma.
    """
    nb, tb = nodo_id.encode(), tid.encode()
    h = hashlib.sha256(metrica.encode("utf-8")).hexdigest()
    return f"{PREFIJO_REPLICA_V2}:{len(nb)}:{nodo_id}:{len(tb)}:{tid}:{h}".encode()


def firmar(priv_hex: str, mensaje: bytes) -> str:
    priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))
    return priv.sign(mensaje).hex()


def verificar(pub_hex: str, firma_hex: str, mensaje: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex.strip().lower())).verify(
            bytes.fromhex(firma_hex), mensaje)
        return True
    except Exception:
        return False


def _auditar(con, nodo_id, tipo, detalle, ts):
    con.execute("INSERT INTO nodos_eventos(nodo_id,tipo,detalle,ts) VALUES(?,?,?,?)",
                (nodo_id, tipo, detalle, ts))


# --- esquema --------------------------------------------------------------
def crear_esquema(con: sqlite3.Connection) -> dict:
    """Crea las tablas nuevas. Idempotente y aditivo: no toca `nodos`."""
    for sql in DDL:
        con.execute(sql)
    con.commit()
    n = 0
    for (nodo_id, pub) in con.execute(
            "SELECT id, clave_publica FROM nodos WHERE clave_publica IS NOT NULL "
            "AND clave_publica<>''").fetchall():
        cur = con.execute(
            "INSERT OR IGNORE INTO claves_nodo(nodo_id,clave_publica,estado,desde) "
            "VALUES(?,?,?,?)", (nodo_id, pub.strip().lower(), "active", time.time()))
        n += int(cur.rowcount or 0)
    con.commit()
    return {"tablas": len(DDL), "sembradas": n}


# --- la regla de vigencia, en UN solo sitio ------------------------------
# Cada uso tiene UN predicado y es la unica definicion de "esta clave vale":
#   - `claves_para_firmar()` lista las claves que la cumplen;
#   - `clave_vigente()` pregunta por una clave concreta;
#   - `predicado_vigente()` la devuelve como SQL para que el Core la pegue
#     dentro del UPDATE de /tareas/replica.
# Si cambia la gracia, se cambia AQUI y las tres bocas cambian juntas.
# `{t}` es el instante con el que se compara `gracia_hasta`.
# `"replica"` es el uso que pide `verificar_firma_replica()`: una entrega de
# replica nunca admite gracia (F2-29), asi que comparte predicado con `activa`.
# Antes esto era un `if uso == "resultado"` sin nombre y cualquier otro valor
# caia en silencio en "solo la activa"; ahora un uso no declarado es un error.
_REGLA = {
    # Una entrega de replica y un cobro NO admiten gracia: solo la activa.
    "activa": "estado = 'active'",
    "replica": "estado = 'active'",
    "resultado": ("(estado = 'active' OR (estado = 'replaced' "
                  "AND motivo = 'planificada' AND gracia_hasta > {t}))"),
}


def _regla_sql(uso: str) -> tuple[str, bool]:
    """(regla con `?` donde iba el instante, si lleva instante)."""
    try:
        regla = _REGLA[uso]
    except KeyError:
        raise ValueError(f"uso de clave desconocido: {uso!r}") from None
    lleva_ts = "{t}" in regla
    return regla.replace("{t}", "?"), lleva_ts


# --- consultas ------------------------------------------------------------
def _clave_legacy(con, nodo_id) -> str:
    """Clave que el nodo declaro al registrarse (`nodos.clave_publica`).

    Es el respaldo SOLO de nodos sin historial en `claves_nodo`. A partir de la
    primera fila manda `claves_nodo`: una clave revocada no resucita por aqui.
    """
    fila = con.execute("SELECT clave_publica FROM nodos WHERE id=?", (nodo_id,)).fetchone()
    return ((fila[0] if fila else "") or "").strip().lower()


def _sin_historial(con, nodo_id) -> bool:
    """El nodo no tiene NINGUNA fila en `claves_nodo` (registrado y no sembrado)."""
    return con.execute("SELECT 1 FROM claves_nodo WHERE nodo_id=? LIMIT 1",
                       (nodo_id,)).fetchone() is None


def _sembrar_legacy(con, nodo_id) -> None:
    """Siembra el historial de un nodo que aun no lo tiene.

    SIN commit a proposito: la transaccion es de quien llama. Se usa solo en
    rutas de ESCRITURA (registro, rotacion, recuperacion, revocacion), nunca en
    la autenticacion: sembrar alli era el defecto F2-36.
    """
    if not _sin_historial(con, nodo_id):
        return
    legacy = _clave_legacy(con, nodo_id)
    if legacy:
        con.execute(
            "INSERT OR IGNORE INTO claves_nodo(nodo_id,clave_publica,estado,desde) "
            "VALUES(?,?,'active',?)", (nodo_id, legacy, time.time()))


def clave_activa(con, nodo_id) -> str:
    """Clave activa del nodo. SOLO LECTURA.

    F2-36: antes esta funcion sembraba `claves_nodo` (INSERT + COMMIT) la
    primera vez que se preguntaba por un nodo. Como la usan las rutas de
    autenticacion, verificar una firma escribia y, peor, hacia commit de la
    transaccion que otro hilo tuviera abierta en la conexion compartida; y si
    otro proceso tenia la base cogida, el INSERT devolvia
    `sqlite3.OperationalError` y el endpoint contestaba 500.
    """
    fila = con.execute(
        "SELECT clave_publica FROM claves_nodo WHERE nodo_id=? AND "
        + _REGLA["activa"] + " ORDER BY desde DESC LIMIT 1", (nodo_id,)).fetchone()
    if fila is not None and (fila[0] or "").strip():
        return fila[0] or ""
    if _sin_historial(con, nodo_id):
        return _clave_legacy(con, nodo_id)
    return ""


def estado_claves(con, nodo_id) -> list[dict]:
    filas = con.execute(
        "SELECT clave_publica, estado, desde, hasta, gracia_hasta, motivo "
        "FROM claves_nodo WHERE nodo_id=? ORDER BY desde, id", (nodo_id,)).fetchall()
    return [{"clave_publica": f[0], "estado": f[1], "desde": f[2], "hasta": f[3],
             "gracia_hasta": f[4], "motivo": f[5]} for f in filas]


def claves_para_firmar(con, nodo_id, uso="resultado", ahora=None) -> list[str]:
    """Claves aceptables para `uso`.

    Solo los RESULTADOS admiten gracia, y solo si la rotacion fue planificada.
    Una clave `revoked` (compromiso) no vale para nada, jamas.
    """
    ts = time.time() if ahora is None else ahora
    regla, lleva_ts = _regla_sql(uso)
    pubs = [r[0] for r in con.execute(
        f"SELECT clave_publica FROM claves_nodo WHERE nodo_id=? AND {regla}",
        (nodo_id,) + ((ts,) if lleva_ts else ())).fetchall()]
    # F2-36: nodo sin historial (registrado y aun sin sembrar) -> vale su clave
    # de registro. Antes esto lo resolvia la siembra perezosa, que escribia.
    if _sin_historial(con, nodo_id):
        legacy = _clave_legacy(con, nodo_id)
        if legacy:
            pubs.append(legacy)
    return pubs


def predicado_vigente(nodo_id, clave, uso="activa", ahora=None) -> tuple[str, tuple]:
    """(fragmento SQL booleano, parametros) de "esta clave vale para `uso`".

    Es `_REGLA` mas el respaldo de los nodos sin historial, listo para pegar en
    un WHERE sin anadir una consulta aparte:

        pred, args = claves.predicado_vigente(nodo, clave, "activa")
        con.execute("UPDATE ... WHERE tarea_id=? AND nodo_id=? AND " + pred,
                    (... ,) + args)

    La primera condicion (`? <> ''`) hace que una clave vacia NUNCA valide, ni
    siquiera contra un nodo retirado con `claves_nodo` vacia y clave de
    registro vacia: el hueco por el que una firma ausente podria colarse.
    """
    regla, lleva_ts = _regla_sql(uso)
    clave = (clave or "").strip()
    ts = time.time() if ahora is None else float(ahora)
    sql = ("(? <> '' AND (EXISTS (SELECT 1 FROM claves_nodo WHERE nodo_id=? "
           "AND trim(clave_publica)=? AND " + regla + ") OR (NOT EXISTS ("
           "SELECT 1 FROM claves_nodo WHERE nodo_id=?) AND lower(trim("
           "COALESCE((SELECT clave_publica FROM nodos WHERE id=?),'')))=?)))")
    args = (clave, nodo_id, clave)
    if lleva_ts:
        args += (ts,)
    args += (nodo_id, nodo_id, clave)
    return sql, args


def clave_vigente(con, nodo_id, clave, uso="activa", ahora=None) -> bool:
    """¿Esta clave sirve para `uso`? UNA consulta y la regla de `_REGLA`."""
    clave = (clave or "").strip()
    if not clave:
        return False
    pred, args = predicado_vigente(nodo_id, clave, uso, ahora)
    return int(con.execute("SELECT " + pred, args).fetchone()[0] or 0) == 1


def _idempotente(con, nodo_id, pub_nueva) -> bool:
    """La rotacion ya se aplico (reintento del cliente tras perder la respuesta)."""
    actual = clave_activa(con, nodo_id)
    if actual != pub_nueva:
        return False
    previa = con.execute(
        "SELECT 1 FROM claves_nodo WHERE nodo_id=? AND estado IN ('replaced','revoked')",
        (nodo_id,)).fetchone()
    return previa is not None


# --- emision de retos (cuota comun) --------------------------------------
def _hash_ip(ip) -> str:
    """Clave de la IP para la cuota: SHA-256 truncado, nunca la IP en claro.

    Contar no necesita el valor real, asi que la tabla de cuota no se convierte
    en un registro de visitantes. Misma IP -> misma clave, siempre.
    """
    return hashlib.sha256((ip or "").encode("utf-8")).hexdigest()[:32]


def _espera_cuota(con, clave, ahora) -> float:
    """Segundos hasta que la emision mas antigua de `clave` salga de la ventana."""
    r = con.execute("SELECT MIN(ts) FROM retos_cuota WHERE clave=? AND ts>?",
                    (clave, ahora - RETO_VENTANA)).fetchone()
    if not r or r[0] is None:
        return RETO_VENTANA
    return max(1.0, min(RETO_VENTANA, float(r[0]) + RETO_VENTANA - ahora))


def _espera_pendientes(con, nodo_id, tipos, ahora) -> float:
    """Segundos hasta que caduque el reto vivo mas antiguo del nodo."""
    marcas = ",".join("?" * len(tipos))
    r = con.execute(f"SELECT MIN(expira) FROM claves_retos WHERE nodo_id=? "
                    f"AND tipo IN ({marcas}) AND usado=0 AND expira>=?",
                    (nodo_id, *tipos, ahora)).fetchone()
    if not r or r[0] is None:
        return 1.0
    return max(1.0, min(TTL_RETO, float(r[0]) - ahora))


def _cupo_agotado(con, nodo_id, ip, tipos, prefijo, ahora,
                  cupo_nodo, cupo_ip, vivos_max) -> dict | None:
    """None si se puede emitir; el rechazo si no. Debe ir en BEGIN IMMEDIATE.

    Cada proposito usa su propio prefijo de cuota (`""` configuracion,
    `"rot:"` rotacion), asi que inundar uno no deja sin cupo al otro. La
    rotacion es la operacion de emergencia: no debe quedar a merced de un
    atacante que llene la cuota de configuracion.
    """
    if vivos_max > 0:
        marcas = ",".join("?" * len(tipos))
        n = int(con.execute(
            f"SELECT COUNT(*) FROM claves_retos WHERE nodo_id=? "
            f"AND tipo IN ({marcas}) AND usado=0 AND expira>=?",
            (nodo_id, *tipos, ahora)).fetchone()[0])
        if n >= vivos_max:
            return {"ok": False, "motivo": "cuota_reto_excedida",
                    "ambito": "pendientes", "cupo": vivos_max, "usados": n,
                    "reintentar_en": _espera_pendientes(con, nodo_id, tipos, ahora)}
    clave_ip = (prefijo + "ip:" + _hash_ip(ip)) if ip else None
    for clave, cupo, ambito in ((prefijo + "nodo:" + nodo_id, cupo_nodo, "nodo"),
                                (clave_ip, cupo_ip, "ip")):
        if not clave or cupo <= 0:
            continue
        n = int(con.execute(
            "SELECT COUNT(*) FROM retos_cuota WHERE clave=? AND ts>?",
            (clave, ahora - RETO_VENTANA)).fetchone()[0])
        if n >= cupo:
            return {"ok": False, "motivo": "cuota_reto_excedida",
                    "ambito": ambito, "cupo": cupo, "usados": n,
                    "reintentar_en": _espera_cuota(con, clave, ahora)}
    return None


def _emitir_reto(con, nodo_id, ip, tipos, tipo_guardado, prefijo, ahora,
                 cupo_nodo, cupo_ip, vivos_max) -> dict | None:
    """Emite el reto con cuota. `None` = nodo sin clave activa (404).

    Los recuentos y la insercion van en el mismo `BEGIN IMMEDIATE`: dos
    procesos simultaneos no pueden pasar del cupo. Un rechazo no escribe nada
    (ni reto ni fila de cuota): no alarga el castigo de quien ya esta fuera.
    """
    if not clave_activa(con, nodo_id):
        return None
    reto = secrets.token_hex(16)
    claves_cuota = [prefijo + "nodo:" + nodo_id]
    if ip:
        claves_cuota.append(prefijo + "ip:" + _hash_ip(ip))
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute("DELETE FROM claves_retos WHERE expira < ?", (ahora - 60.0,))
        con.execute("DELETE FROM retos_cuota WHERE ts < ?",
                    (ahora - RETO_VENTANA,))
        rechazo = _cupo_agotado(con, nodo_id, ip, tipos, prefijo, ahora,
                                cupo_nodo, cupo_ip, vivos_max)
        if rechazo is not None:
            con.rollback()
            return rechazo
        con.execute("INSERT INTO claves_retos(reto,nodo_id,tipo,expira,usado) "
                    "VALUES(?,?,?,?,0)",
                    (reto, nodo_id, tipo_guardado, ahora + TTL_RETO))
        for clave in claves_cuota:
            con.execute("INSERT INTO retos_cuota(clave, ts) VALUES(?,?)",
                        (clave, ahora))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return {"ok": True, "reto": reto, "expira_seg": int(TTL_RETO),
            "tipo": tipo_guardado}


def _cupos(cupo_nodo, cupo_ip, cupo_pendientes) -> tuple[int, int, int]:
    """Cupos de configuracion (o los que pase la prueba)."""
    return (RETO_CUPO_NODO if cupo_nodo is None else int(cupo_nodo),
            RETO_CUPO_IP if cupo_ip is None else int(cupo_ip),
            RETO_CUPO_PENDIENTES if cupo_pendientes is None
            else int(cupo_pendientes))


def _cupos_rot(cupo_nodo, cupo_ip, cupo_pendientes) -> tuple[int, int, int]:
    """Cupos de rotacion: mismos nombres de parametro, numeros propios."""
    return (RETO_ROT_CUPO_NODO if cupo_nodo is None else int(cupo_nodo),
            RETO_ROT_CUPO_IP if cupo_ip is None else int(cupo_ip),
            RETO_ROT_CUPO_PENDIENTES if cupo_pendientes is None
            else int(cupo_pendientes))


# --- rotacion -------------------------------------------------------------
def reto_rotacion(con, nodo_id, tipo="planificada", ip=None, ahora=None, *,
                  cupo_nodo=None, cupo_ip=None,
                  cupo_pendientes=None) -> dict | None:
    """Reto de un solo uso para rotar la clave. Caduca en 5 minutos.

    F2-23: misma cuota que la configuracion pero con presupuesto propio
    (`rot:nodo:` / `rot:ip:`), para que una inundacion de retos de
    configuracion no deje a un nodo sin poder rotar su clave.
    """
    ts = time.time() if ahora is None else ahora
    if tipo not in ("planificada", "perdida"):
        return {"ok": False, "motivo": "tipo_invalido"}
    return _emitir_reto(con, nodo_id, ip, ("planificada", "perdida"), tipo,
                        "rot:", ts, *_cupos_rot(cupo_nodo, cupo_ip,
                                                cupo_pendientes))


# --- configuracion del secreto -------------------------------------------
def reto_configuracion(con, nodo_id, ip=None, ahora=None, *,
                       cupo_nodo=None, cupo_ip=None,
                       cupo_pendientes=None) -> dict | None:
    """Reto de un solo uso para inscribir el secreto. Caduca en 5 minutos.

    Emitirlo no autoriza nada: la posesion la demuestra la firma de la clave
    ACTIVA. El reto impide que una firma capturada se reenvie.

    F2-22/F2-23: la emision esta acotada para que nadie inunde `claves_retos`:
      - retos vivos sin usar por nodo (`RETO_CUPO_PENDIENTES`)
      - emisiones por nodo y ventana (`RETO_CUPO_NODO`)
      - emisiones por IP y ventana (`RETO_CUPO_IP`)
    """
    ts = time.time() if ahora is None else ahora
    return _emitir_reto(con, nodo_id, ip, ("configurar",), "configurar", "",
                        ts, *_cupos(cupo_nodo, cupo_ip, cupo_pendientes))


def _consumir_reto(con, reto, nodo_id, tipo, ahora) -> bool:
    """Marca el reto como usado. Debe llamarse dentro de BEGIN IMMEDIATE."""
    cur = con.execute(
        "UPDATE claves_retos SET usado=1 WHERE reto=? AND nodo_id=? AND tipo=? "
        "AND usado=0 AND expira>=?", (reto, nodo_id, tipo, ahora))
    return int(cur.rowcount or 0) == 1


def rotar(con, nodo_id, reto, pub_vieja, pub_nueva, firma_vieja, firma_nueva,
          ts, tipo="planificada", gracia_seg=0.0, ahora=None) -> dict:
    """Rotacion con prueba de la clave VIEJA y posesion de la NUEVA.

    Ambas firmas cubren el mismo mensaje canonico, que incluye el reto: sin el
    no se puede preparar la firma antes de pedirlo, y solo vale una vez.
    """
    ahora = time.time() if ahora is None else ahora
    if tipo not in ("planificada", "perdida"):
        return {"ok": False, "motivo": "tipo_invalido"}
    pub_vieja = (pub_vieja or "").strip().lower()
    pub_nueva = (pub_nueva or "").strip().lower()
    if not clave_valida(pub_vieja) or not clave_valida(pub_nueva):
        return {"ok": False, "motivo": "clave_invalida"}
    if pub_vieja == pub_nueva:
        return {"ok": False, "motivo": "clave_igual"}
    try:
        ts = float(ts)
    except Exception:
        return {"ok": False, "motivo": "ts_invalido"}
    if abs(ahora - ts) > TTL_RETO:
        return {"ok": False, "motivo": "ts_fuera_de_ventana"}

    # Idempotencia ANTES de consumir el reto: un reintento no debe fallar.
    if _idempotente(con, nodo_id, pub_nueva):
        return {"ok": True, "motivo": "ya_aplicada", "activa": pub_nueva}

    mensaje = canon_rotacion(nodo_id, reto, pub_vieja, pub_nueva, tipo, ts)
    if not verificar(pub_vieja, firma_vieja, mensaje):
        return {"ok": False, "motivo": "firma_clave_vieja_invalida"}
    if not verificar(pub_nueva, firma_nueva, mensaje):
        return {"ok": False, "motivo": "falta_posesion_clave_nueva"}

    con.execute("BEGIN IMMEDIATE")
    try:
        # F2-36: nodo sin historial -> su clave de registro entra en la MISMA
        # transaccion (antes la sembraba la lectura, con commit propio).
        _sembrar_legacy(con, nodo_id)
        # Consumo atomico: UPDATE condicional; si otro proceso lo gasto, 0 filas.
        cur = con.execute(
            "UPDATE claves_retos SET usado=1 WHERE reto=? AND nodo_id=? AND usado=0 "
            "AND expira>=?", (reto, nodo_id, ahora))
        if int(cur.rowcount or 0) != 1:
            con.rollback()
            return {"ok": False, "motivo": "reto_invalido_o_caducado"}
        fila = con.execute(
            "SELECT clave_publica FROM claves_nodo WHERE nodo_id=? "
            "AND estado='active' ORDER BY desde DESC, id DESC LIMIT 1",
            (nodo_id,)).fetchone()
        actual = ((fila[0] if fila else "") or "").strip().lower()
        if actual != pub_vieja:
            con.rollback()
            return {"ok": False, "motivo": "clave_desactualizada"}
        # Una clave ya registrada (aunque este revocada o reemplazada) no se
        # puede volver a insertar: UNIQUE(nodo_id, clave_publica) daria 500.
        if con.execute(
                "SELECT 1 FROM claves_nodo WHERE nodo_id=? AND clave_publica=?",
                (nodo_id, pub_nueva)).fetchone() is not None:
            con.rollback()
            return {"ok": False, "motivo": "clave_ya_usada"}
        gracia = max(0.0, min(float(gracia_seg or 0.0), GRACIA_MAXIMA))
        cur = con.execute(
            "UPDATE claves_nodo SET estado='replaced', hasta=?, gracia_hasta=?, motivo=? "
            "WHERE nodo_id=? AND clave_publica=? AND estado='active'",
            (ahora, (ahora + gracia) if gracia > 0 else None, tipo, nodo_id, pub_vieja))
        if int(cur.rowcount or 0) != 1:
            con.rollback()
            return {"ok": False, "motivo": "conflicto_concurrente"}
        con.execute(
            "INSERT INTO claves_nodo(nodo_id,clave_publica,estado,desde,motivo) "
            "VALUES(?,?,'active',?,?)", (nodo_id, pub_nueva, ahora, tipo))
        con.execute("UPDATE nodos SET clave_publica=? WHERE id=?",
                    (pub_nueva, nodo_id))
        _auditar(con, nodo_id, "rotacion",
                 f"{pub_vieja[:16]}... -> {pub_nueva[:16]}... ({tipo}, gracia {int(gracia)}s)",
                 ahora)
        con.commit()
    except sqlite3.IntegrityError:
        # Carrera con otra operacion de claves: 409, nunca 500.
        con.rollback()
        return {"ok": False, "motivo": "conflicto_concurrente"}
    except Exception:
        con.rollback()
        raise
    return {"ok": True, "motivo": "ok", "reemplazada": pub_vieja,
            "activa": pub_nueva, "gracia_seg": gracia}


# --- recuperacion ---------------------------------------------------------
def _scrypt(secreto, sal, n, r, p) -> bytes:
    return hashlib.scrypt((secreto or "").encode("utf-8"), salt=sal, n=n, r=r,
                          p=p, dklen=32)


def _coincide_secreto(secreto, fila) -> bool:
    """Compara contra una fila (hash, sal, n, r, p) guardada."""
    h, sal, n, r, p = fila
    if not h or not sal:
        return False
    try:
        return hmac.compare_digest(_scrypt(secreto, sal, n, r, p), h)
    except Exception:
        return False


def configurar_recuperacion(con, nodo_id, secreto, *, reto=None, firma=None,
                            ts=None, ahora=None) -> dict:
    """Inscribe el compromiso scrypt del secreto (nunca el secreto).

    Exige posesion de la clave ACTIVA: `firma` cubre
    `canon_configuracion(nodo_id, reto, secreto, ts)`. El reto lo emite
    `reto_configuracion()`, caduca en 5 minutos y se consume una sola vez: una
    firma capturada no se puede reenviar. Un reintento legitimo necesita un
    reto nuevo; el efecto sigue siendo idempotente:
      - el mismo secreto -> `ya_configurada` (200, sin evento nuevo)
      - otro secreto     -> `recuperacion_ya_configurada` (409, no sobrescribe)
    """
    ahora = time.time() if ahora is None else ahora
    if len(secreto or "") < LARGO_SECRETO_MIN:
        return {"ok": False, "motivo": "secreto_corto"}
    if not firma or not ts:
        return {"ok": False, "motivo": "falta_posesion_clave_activa"}
    try:
        ts = float(ts)
    except Exception:
        return {"ok": False, "motivo": "ts_invalido"}
    if abs(ahora - ts) > TTL_RETO:
        return {"ok": False, "motivo": "ts_fuera_de_ventana"}
    reto = (reto or "").strip()
    if not reto:
        return {"ok": False, "motivo": "falta_reto"}
    pub_activa = clave_activa(con, nodo_id)
    if not pub_activa:
        return {"ok": False, "motivo": "recuperacion_invalida"}
    if not verificar(pub_activa, firma,
                     canon_configuracion(nodo_id, reto, secreto, ts)):
        return {"ok": False, "motivo": "firma_invalida"}
    # El reto se gasta ANTES de derivar scrypt y aunque luego haya conflicto:
    # fail-closed. La firma ya se ha verificado, asi que un no autorizado no
    # puede quemar el reto de otro.
    con.execute("BEGIN IMMEDIATE")
    try:
        if not _consumir_reto(con, reto, nodo_id, "configurar", ahora):
            con.rollback()
            return {"ok": False, "motivo": "reto_invalido_o_caducado"}
        con.commit()
    except Exception:
        con.rollback()
        raise
    sal = secrets.token_bytes(16)
    h = _scrypt(secreto, sal, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    con.execute("BEGIN IMMEDIATE")
    try:
        _sembrar_legacy(con, nodo_id)      # F2-36: ver `rotar`
        fila = con.execute(
            "SELECT clave_publica, recuperacion_hash, recuperacion_sal, "
            "recuperacion_n, recuperacion_r, recuperacion_p FROM claves_nodo "
            "WHERE nodo_id=? AND estado='active' "
            "ORDER BY desde DESC, id DESC LIMIT 1", (nodo_id,)).fetchone()
        if fila is None or (fila[0] or "").strip().lower() != pub_activa:
            con.rollback()
            return {"ok": False, "motivo": "conflicto_concurrente"}
        if fila[1]:
            con.rollback()
            if _coincide_secreto(secreto, fila[1:]):
                return {"ok": True, "motivo": "ya_configurada"}
            return {"ok": False, "motivo": "recuperacion_ya_configurada"}
        cur = con.execute(
            "UPDATE claves_nodo SET recuperacion_hash=?, recuperacion_sal=?, "
            "recuperacion_n=?, recuperacion_r=?, recuperacion_p=? "
            "WHERE nodo_id=? AND estado='active' AND recuperacion_hash IS NULL",
            (h, sal, SCRYPT_N, SCRYPT_R, SCRYPT_P, nodo_id))
        if int(cur.rowcount or 0) != 1:
            con.rollback()
            return {"ok": False, "motivo": "conflicto_concurrente"}
        _auditar(con, nodo_id, "recuperacion_configurada", "scrypt", ahora)
        con.commit()
    except sqlite3.IntegrityError:
        con.rollback()
        return {"ok": False, "motivo": "conflicto_concurrente"}
    except Exception:
        con.rollback()
        raise
    return {"ok": True, "motivo": "ok"}


def _intentos(con, nodo_id, ahora) -> int:
    con.execute("DELETE FROM recuperacion_intentos WHERE ts < ?",
                (ahora - VENTANA_INTENTOS,))
    # El DELETE abre una transaccion implicita (sqlite3 no esta en autocommit por
    # defecto). Sin este commit, el BEGIN IMMEDIATE posterior falla con
    # "cannot start a transaction within a transaction" y la recuperacion
    # (y la revocacion) exitosa devuelve 500. Detectado en la prueba HTTP contra
    # uvicorn; las pruebas unitarias usaban isolation_level=None y no lo veian.
    con.commit()
    return con.execute("SELECT COUNT(*) FROM recuperacion_intentos WHERE nodo_id=?",
                       (nodo_id,)).fetchone()[0]


def _secreto_correcto(con, nodo_id, secreto, ahora) -> tuple[bool, str, str]:
    """Devuelve (ok, motivo, clave_publica_validada).

    Nunca distingue "no existe" de "secreto malo". La clave devuelta es la que
    tenia el compromiso verificado: la recuperacion solo puede revocar ESA, no
    la que otra peticion concurrente haya dejado activa mientras tanto.
    """
    fila = con.execute(
        "SELECT clave_publica, recuperacion_hash, recuperacion_sal, recuperacion_n, "
        "recuperacion_r, recuperacion_p FROM claves_nodo "
        "WHERE nodo_id=? AND estado='active' LIMIT 1", (nodo_id,)).fetchone()
    if not fila or not fila[1]:
        return False, "recuperacion_invalida", ""
    if _intentos(con, nodo_id, ahora) >= MAX_INTENTOS_RECUPERACION:
        return False, "demasiados_intentos", ""
    pub, h, sal, n, r, p = fila
    calc = hashlib.scrypt((secreto or "").encode("utf-8"), salt=sal, n=n, r=r, p=p,
                          dklen=32)
    if not hmac.compare_digest(calc, h):
        con.execute("INSERT INTO recuperacion_intentos(nodo_id,ts) VALUES(?,?)",
                    (nodo_id, ahora))
        con.commit()
        return False, "recuperacion_invalida", ""
    return True, "ok", (pub or "").strip().lower()


def recuperar(con, nodo_id, pub_nueva, firma_nueva, secreto, ts,
              tipo="compromiso", retraso_seg=0.0, ahora=None) -> dict:
    """Recupera el nodo con el secreto preinscripto + posesion de la nueva clave.

    `compromiso` (por defecto): revoca la clave actual EN EL ACTO, sin gracia.
    `perdida`: deja la nueva en `recovery_pending` durante `retraso_seg` y la
    confirma despues con `confirmar_recuperacion` (la clave vieja sigue activa
    hasta entonces porque no hay riesgo de robo).
    """
    ahora = time.time() if ahora is None else ahora
    if tipo not in ("compromiso", "perdida"):
        return {"ok": False, "motivo": "tipo_invalido"}
    pub_nueva = (pub_nueva or "").strip().lower()
    if not clave_valida(pub_nueva):
        return {"ok": False, "motivo": "clave_invalida"}
    try:
        ts = float(ts)
    except Exception:
        return {"ok": False, "motivo": "ts_invalido"}
    if abs(ahora - ts) > TTL_RETO:
        return {"ok": False, "motivo": "ts_fuera_de_ventana"}

    if idempotente := _idempotente(con, nodo_id, pub_nueva):
        return {"ok": True, "motivo": "ya_aplicada", "activa": pub_nueva}

    ok, motivo, pub_validada = _secreto_correcto(con, nodo_id, secreto, ahora)
    if not ok:
        return {"ok": False, "motivo": motivo}
    if not verificar(pub_nueva, firma_nueva,
                     canon_recuperacion(nodo_id, pub_nueva, tipo, ts)):
        return {"ok": False, "motivo": "falta_posesion_clave_nueva"}

    con.execute("BEGIN IMMEDIATE")
    try:
        _sembrar_legacy(con, nodo_id)      # F2-36: ver `rotar`
        # La decision se repite DENTRO de la transaccion. Entre el scrypt y el
        # bloqueo, otra recuperacion pudo aplicarse: sin esto, cada peticion
        # encadenaba una rotacion y las que chocaban con el indice unico
        # devolvian 500. Ahora solo aplica la que valido la clave aun activa.
        fila = con.execute(
            "SELECT clave_publica FROM claves_nodo WHERE nodo_id=? "
            "AND estado='active' ORDER BY desde DESC, id DESC LIMIT 1",
            (nodo_id,)).fetchone()
        actual = ((fila[0] if fila else "") or "").strip().lower()
        previa = con.execute(
            "SELECT 1 FROM claves_nodo WHERE nodo_id=? "
            "AND estado IN ('replaced','revoked')", (nodo_id,)).fetchone()
        if actual == pub_nueva and previa is not None:
            con.rollback()
            return {"ok": True, "motivo": "ya_aplicada", "activa": pub_nueva}
        if actual != pub_validada:
            con.rollback()
            return {"ok": False, "motivo": "conflicto_concurrente"}

        if tipo == "compromiso":
            cur = con.execute(
                "UPDATE claves_nodo SET estado='revoked', hasta=?, gracia_hasta=NULL, "
                "motivo='compromiso' WHERE nodo_id=? AND clave_publica=? "
                "AND estado='active'", (ahora, nodo_id, pub_validada))
            if cur.rowcount != 1:
                con.rollback()
                return {"ok": False, "motivo": "conflicto_concurrente"}
            con.execute(
                "INSERT INTO claves_nodo(nodo_id,clave_publica,estado,desde,motivo) "
                "VALUES(?,?,'active',?,'compromiso')", (nodo_id, pub_nueva, ahora))
            con.execute("UPDATE nodos SET clave_publica=? WHERE id=?",
                        (pub_nueva, nodo_id))
            detalle = f"compromiso: {pub_validada[:16]}... revocada sin gracia"
        else:
            pend = con.execute(
                "SELECT clave_publica FROM claves_nodo WHERE nodo_id=? "
                "AND estado='recovery_pending' ORDER BY id DESC LIMIT 1",
                (nodo_id,)).fetchone()
            if pend is not None:
                con.rollback()
                if (pend[0] or "").strip().lower() == pub_nueva:
                    return {"ok": True, "motivo": "ya_pendiente",
                            "pendiente": pub_nueva}
                return {"ok": False, "motivo": "conflicto_concurrente"}
            desde = ahora + max(0.0, float(retraso_seg or 0.0))
            con.execute(
                "INSERT INTO claves_nodo(nodo_id,clave_publica,estado,desde,motivo) "
                "VALUES(?,?,'recovery_pending',?,'perdida')",
                (nodo_id, pub_nueva, desde))
            detalle = f"perdida: {pub_nueva[:16]}... pendiente hasta {int(desde)}"
        _auditar(con, nodo_id, "recuperacion", detalle, ahora)
        con.commit()
    except sqlite3.IntegrityError:
        # Carrera con otra recuperacion/rotacion: conflicto, nunca 500.
        con.rollback()
        return {"ok": False, "motivo": "conflicto_concurrente"}
    except Exception:
        con.rollback()
        raise
    return {"ok": True, "motivo": "ok", "tipo": tipo, "activa": pub_nueva,
            "revocada": pub_validada if tipo == "compromiso" else None}


def confirmar_recuperacion(con, nodo_id, firma_nueva, ts, ahora=None) -> dict:
    """Cierra una recuperacion por perdida: la nueva pasa a `active`."""
    ahora = time.time() if ahora is None else ahora
    try:
        ts = float(ts)
    except Exception:
        return {"ok": False, "motivo": "ts_invalido"}
    if abs(ahora - ts) > TTL_RETO:
        return {"ok": False, "motivo": "ts_fuera_de_ventana"}
    fila = con.execute(
        "SELECT id, clave_publica, desde FROM claves_nodo WHERE nodo_id=? "
        "AND estado='recovery_pending' ORDER BY id DESC LIMIT 1", (nodo_id,)).fetchone()
    if not fila:
        # Reintento tras confirmar: si la clave activa firma este mensaje, la
        # confirmacion ya se aplico y el reintento es idempotente.
        act = con.execute(
            "SELECT clave_publica FROM claves_nodo WHERE nodo_id=? "
            "AND estado='active' LIMIT 1", (nodo_id,)).fetchone()
        if act is not None and verificar(
                act[0], firma_nueva,
                canon_confirmacion(nodo_id, act[0], ts)):
            return {"ok": True, "motivo": "ya_aplicada", "activa": act[0]}
        return {"ok": False, "motivo": "sin_recuperacion_pendiente"}
    pend_id, pub_nueva, desde = fila[0], fila[1], fila[2]
    if ahora < desde:
        return {"ok": False, "motivo": "todavia_no"}
    if not verificar(pub_nueva, firma_nueva,
                     canon_confirmacion(nodo_id, pub_nueva, ts)):
        return {"ok": False, "motivo": "falta_posesion_clave_nueva"}
    con.execute("BEGIN IMMEDIATE")
    try:
        # Relectura y revalidacion DENTRO de la transaccion: dos confirmaciones
        # simultaneas dejaban el nodo sin ninguna clave activa (la primera
        # confirmaba y la segunda reemplazaba la recien activada).
        fila = con.execute(
            "SELECT id, clave_publica, desde FROM claves_nodo WHERE nodo_id=? "
            "AND estado='recovery_pending' ORDER BY id DESC LIMIT 1",
            (nodo_id,)).fetchone()
        if not fila or fila[0] != pend_id or fila[1] != pub_nueva:
            act = con.execute(
                "SELECT clave_publica FROM claves_nodo WHERE nodo_id=? "
                "AND estado='active' LIMIT 1", (nodo_id,)).fetchone()
            con.rollback()
            if act is not None and act[0] == pub_nueva:
                return {"ok": True, "motivo": "ya_aplicada", "activa": pub_nueva}
            return {"ok": False, "motivo": "conflicto_concurrente"}
        if ahora < fila[2]:
            con.rollback()
            return {"ok": False, "motivo": "todavia_no"}
        # Si otra operacion (rotacion o recuperacion) inserto una clave
        # despues de esta pendiente, confirmarla la resucitaria: conflicto.
        if con.execute(
                "SELECT 1 FROM claves_nodo WHERE nodo_id=? AND id>? LIMIT 1",
                (nodo_id, pend_id)).fetchone() is not None:
            con.rollback()
            return {"ok": False, "motivo": "conflicto_concurrente"}
        act = con.execute(
            "SELECT clave_publica FROM claves_nodo WHERE nodo_id=? "
            "AND estado='active' ORDER BY desde DESC, id DESC LIMIT 1",
            (nodo_id,)).fetchone()
        if act is None:
            con.rollback()
            return {"ok": False, "motivo": "recuperacion_invalida"}
        pub_vieja = (act[0] or "").strip().lower()
        if pub_vieja == pub_nueva:
            con.rollback()
            return {"ok": True, "motivo": "ya_aplicada", "activa": pub_nueva}
        cur = con.execute(
            "UPDATE claves_nodo SET estado='replaced', hasta=?, motivo='perdida' "
            "WHERE nodo_id=? AND clave_publica=? AND estado='active'",
            (ahora, nodo_id, pub_vieja))
        if int(cur.rowcount or 0) != 1:
            con.rollback()
            return {"ok": False, "motivo": "conflicto_concurrente"}
        cur = con.execute(
            "UPDATE claves_nodo SET estado='active', desde=?, motivo='perdida' "
            "WHERE nodo_id=? AND clave_publica=? AND estado='recovery_pending'",
            (ahora, nodo_id, pub_nueva))
        if int(cur.rowcount or 0) != 1:
            con.rollback()
            return {"ok": False, "motivo": "conflicto_concurrente"}
        con.execute("UPDATE nodos SET clave_publica=? WHERE id=?", (pub_nueva, nodo_id))
        _auditar(con, nodo_id, "recuperacion_confirmada",
                 f"{pub_vieja[:16]}... -> {pub_nueva[:16]}...", ahora)
        con.commit()
    except sqlite3.IntegrityError:
        con.rollback()
        return {"ok": False, "motivo": "conflicto_concurrente"}
    except Exception:
        con.rollback()
        raise
    return {"ok": True, "motivo": "ok", "activa": pub_nueva, "reemplazada": pub_vieja}


def revocar(con, nodo_id, secreto, motivo="compromiso", ahora=None) -> dict:
    """Revoca sin sustituta. La identidad deja de firmar nada."""
    ahora = time.time() if ahora is None else ahora
    ok, m, pub = _secreto_correcto(con, nodo_id, secreto, ahora)
    if not ok:
        return {"ok": False, "motivo": m}
    con.execute("BEGIN IMMEDIATE")
    try:
        _sembrar_legacy(con, nodo_id)      # F2-36: ver `rotar`
        # No revocar a ciegas: si otra operacion (recuperacion/rotacion) ya
        # cambio la clave, limpiar `nodos.clave_publica` borraria la nueva.
        act = con.execute(
            "SELECT clave_publica FROM claves_nodo WHERE nodo_id=? "
            "AND estado='active' ORDER BY desde DESC, id DESC LIMIT 1",
            (nodo_id,)).fetchone()
        actual = ((act[0] if act else "") or "").strip().lower()
        if actual != pub:
            con.rollback()
            return {"ok": False, "motivo": "conflicto_concurrente"}
        cur = con.execute(
            "UPDATE claves_nodo SET estado='revoked', hasta=?, gracia_hasta=NULL, "
            "motivo=? WHERE nodo_id=? AND clave_publica=? AND estado='active'",
            (ahora, motivo, nodo_id, pub))
        if int(cur.rowcount or 0) != 1:
            con.rollback()
            return {"ok": False, "motivo": "conflicto_concurrente"}
        con.execute("UPDATE nodos SET clave_publica='' WHERE id=?", (nodo_id,))
        _auditar(con, nodo_id, "revocacion", f"{pub[:16]}... ({motivo})", ahora)
        con.commit()
    except sqlite3.IntegrityError:
        con.rollback()
        return {"ok": False, "motivo": "conflicto_concurrente"}
    except Exception:
        con.rollback()
        raise
    return {"ok": True, "motivo": "ok", "revocada": pub}


# --- verificacion de resultados ------------------------------------------
def verificar_firma_resultado(con, nodo_id, tid, metrica, firma_hex,
                              modo="dual", ahora=None) -> dict:
    """Sustituye al bloque del Core que hoy verifica `{tid}:{metrica}`.

    - Rechaza si el nodo no tiene ninguna clave vigente (todas revocadas).
    - Acepta las claves en gracia SOLO para resultados.
    - v2 (F2-13) preferente; v1 solo fuera del modo `on`.
    """
    # F2-36: sin siembra aqui. Verificar una firma no escribe ni hace commit.
    pubs = claves_para_firmar(con, nodo_id, "resultado", ahora=ahora)
    if not pubs:
        return {"ok": False, "version": 0, "motivo": "sin_clave_vigente"}
    if not firma_hex:
        return {"ok": False, "version": 0, "motivo": "falta_firma"}
    if modo not in ("off", "dual", "on"):
        return {"ok": False, "version": 0, "motivo": "modo_invalido"}
    if modo != "off":
        msg = canon_resultado_v2(nodo_id, tid, metrica)
        for pub in pubs:
            if verificar(pub, firma_hex, msg):
                # F2-38: se devuelve QUE clave valido, para revalidarla en la
                # transaccion de pago (F2-13 y F2-14 intactos: siguen valiendo
                # las claves en gracia).
                return {"ok": True, "version": 2, "motivo": "ok_v2", "clave": pub}
        if modo == "on":
            return {"ok": False, "version": 0, "motivo": "firma_v1_prohibida"}
    msg1 = canon_resultado_v1(tid, metrica)
    for pub in pubs:
        if verificar(pub, firma_hex, msg1):
            return {"ok": True, "version": 1, "motivo": "v1_aceptada_en_transicion",
                    "clave": pub}
    return {"ok": False, "version": 0, "motivo": "firma_invalida"}


def verificar_firma_replica(con, nodo_id, tid, metrica, firma_hex,
                           modo="dual", ahora=None) -> dict:
    """F2-29: autenticacion de la replica, mismo criterio que el primario.

    Diferencias deliberadas con `verificar_firma_resultado`:
    - NO admite claves en gracia (uso="replica"): la replica decide el
      reparto 70/30 y el castigo del primario, asi que una clave ya
      sustituida no debe poder mover dinero ni reputacion.
    - dominio propio `replica:v2`.
    v1 (`tid:metrica`) se acepta solo fuera del modo `on`, para no romper
    a los mineros ya descargados; el modo se declara, no se degrada solo.
    """
    # F2-36: sin siembra aqui (ver `verificar_firma_resultado`).
    pubs = claves_para_firmar(con, nodo_id, "replica", ahora=ahora)
    if not pubs:
        return {"ok": False, "version": 0, "motivo": "sin_clave_vigente"}
    if not firma_hex:
        return {"ok": False, "version": 0, "motivo": "falta_firma"}
    if modo not in ("off", "dual", "on"):
        return {"ok": False, "version": 0, "motivo": "modo_invalido"}
    if modo != "off":
        msg = canon_replica_v2(nodo_id, tid, metrica)
        for pub in pubs:
            if verificar(pub, firma_hex, msg):
                # F2-38: se devuelve QUE clave valido. El Core la revalida dentro
                # del cerrojo de escritura, porque entre esta lectura y el UPDATE
                # cabe una rotacion o una revocacion del mismo nodo.
                return {"ok": True, "version": 2, "motivo": "ok_v2", "clave": pub}
        if modo == "on":
            return {"ok": False, "version": 0, "motivo": "firma_v1_prohibida"}
    msg1 = canon_resultado_v1(tid, metrica)
    for pub in pubs:
        if verificar(pub, firma_hex, msg1):
            return {"ok": True, "version": 1, "motivo": "v1_aceptada_en_transicion",
                    "clave": pub}
    return {"ok": False, "version": 0, "motivo": "firma_invalida"}


def purgar_retos(con, ahora=None) -> int:
    """Limpieza barata; el Core puede llamarla en cada arranque."""
    ts = time.time() if ahora is None else ahora
    cur = con.execute("DELETE FROM claves_retos WHERE expira < ?", (ts - 3600.0,))
    con.commit()
    return int(cur.rowcount or 0)
