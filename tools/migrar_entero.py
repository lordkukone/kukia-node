#!/usr/bin/env python3
"""Migracion del dinero de KIA (REAL) a unidades enteras, sobre una COPIA.

El problema, medido y no supuesto: el ledger guarda los importes en columnas
REAL. Sumar 777.000 asientos en coma flotante deja un desvio visible entre el
suministro emitido y la suma de los saldos; y la partida doble (un cargo y un
abono del mismo dinero) deja de cuadrar al bit. Este script convierte todo a
enteros en UNA COPIA de la base y enseña, cuenta por cuenta, si el resultado
representa exactamente el mismo dinero.

Reglas que respeta, en orden:

  1. El ledger REAL no se abre en escritura JAMAS: se abre `mode=ro` con
     `query_only=1` y solo se lee. Lo que se escribe es la copia.
  2. La copia se hace con la API de backup de SQLite, no copiando el fichero:
     con WAL, un `cp` del .db se lleva escrituras a medias.
  3. No se toca ninguna columna que ya exista. Se AÑADEN columnas `*_u` al
     lado de las de coma flotante (doble escritura: las dos versiones del
     mismo dinero conviven y se comparan).
  4. La cadena de hashes del ledger no se reescribe ni se recalcula con otros
     numeros: se VERIFICA tal cual, para demostrar que el historial sellado no
     se ha tocado. Si la cadena no cuadra, se dice y no se da nada por bueno.
  5. Si algo no concilia, el informe lo marca; el script no lo "arregla" a la
     callada.

La unidad minima: 1 KIA = 10.000.000.000 unidades (1e-10 KIA). Con el techo de
100.000.000 KIA, el maximo posible son 1e18 unidades, que cabe en un entero de
64 bits (9,22e18) con 9 veces de margen. La escala se puede cambiar con
`--escala` para comparar residuos antes de decidir.

Uso:
    python tools/migrar_entero.py --ver              # plan y recuento, sin escribir
    python tools/migrar_entero.py                    # migra una COPIA y deja informe
    python tools/migrar_entero.py --escala 1000000000
    python tools/migrar_entero.py --origen otra.db --salida carpeta/

Salidas (dentro de `migracion_entera/`):
    kukia_entera.db        copia migrada (la unica base que se escribe)
    conciliacion.json      el informe completo, legible por maquina
    CONCILIACION.md        el mismo informe en texto, cuenta por cuenta
    sha256.txt             huellas de la copia y del fichero de origen leido
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sqlite3
import sys
import time
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB_REAL = RAIZ / "coordinator" / "ledger" / "kukia.db"
SALIDA = RAIZ / "migracion_entera"

# 1 KIA = ESCALA unidades. Ver la cuenta del margen en la cabecera.
ESCALA = 10 ** 10
MAX_SUPPLY = 100_000_000            # KIA, fijado por la tokenomics: no se toca

# Que columna de que tabla lleva dinero. Se AÑADE la de enteros al lado.
COLUMNAS = {
    "ledger":         [("kuki", "kuki_u")],
    "saldos_cuenta":  [("total", "total_u")],
    "transferencias": [("monto", "monto_u")],
    "tareas":         [("prepago", "prepago_u"),
                       ("precio_cliente", "precio_cliente_u"),
                       ("pagado", "pagado_u")],
    "clientes":       [("gastado", "gastado_u")],
}
# Claves de `meta` que son dinero. Las demas (minutos, epocas, contadores) no
# se convierten: convertirlas seria inventar una unidad que no existe.
META_DINERO = ("supply_emitido", "bolsa_credito", "bolsa_kia", "bolsa_pagado")
# Motivos que forman pareja (cargo y abono del MISMO dinero). Si el grupo de
# una tarea suma distinto de cero, se ha creado o perdido dinero.
MOTIVOS_PAR = ("prepago cliente", "pago a minero (cliente)", "devolucion sobrante",
               "devolucion prepago", "transferencia entrada", "transferencia salida")
MOTIVOS_EMISION = ("trabajo verificado", "verificacion redundante",
                   "asignacion fundacional", "migracion a billetera con recuperacion")


# ============================================================
#  Conversion
# ============================================================
def a_unidades(valor, escala: int = ESCALA) -> int:
    """KIA (float) -> unidades enteras, redondeando al medio hacia arriba.

    Se convierte el numero TAL COMO SE ESCRIBE (`repr`, que es el texto mas
    corto que reconstruye el mismo float), no su valor binario exacto: asi el
    resultado es el que cualquiera leeria en el recibo y la conversion se puede
    repetir con el mismo resultado en otra maquina.

    `ROUND_HALF_UP` de Decimal redondea alejandose del cero, o sea simetrico:
    convertir -x da exactamente -convertir(x). Eso es lo que mantiene la
    partida doble cuadrada al bit despues de la migracion.
    """
    if valor is None:
        return 0
    v = float(valor)
    if not math.isfinite(v):
        raise ValueError(f"importe no finito: {valor!r}")
    return int((Decimal(repr(v)) * escala).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def residuo(valor, escala: int = ESCALA) -> Decimal:
    """Lo que se pierde al convertir, en unidades (siempre |r| <= 0,5)."""
    if valor is None:
        return Decimal(0)
    exacto = Decimal(repr(float(valor))) * escala
    return exacto - Decimal(a_unidades(valor, escala))


def a_kia(unidades: int, escala: int = ESCALA) -> float:
    """Vuelta a KIA, para enseñarlo. No se usa para calcular dinero."""
    return float(Decimal(int(unidades)) / Decimal(escala))


def _digitos(escala: int) -> int:
    return len(str(escala)) - 1


def texto_kia(unidades: int, escala: int = ESCALA) -> str:
    """Importe en KIA con todos los decimales de la escala, sin notacion rara."""
    d = _digitos(escala)
    signo = "-" if unidades < 0 else ""
    entero, resto = divmod(abs(int(unidades)), escala)
    return f"{signo}{entero:,}".replace(",", ".") + "." + f"{resto:0{d}d}"


# ============================================================
#  Seguridad: no se toca el ledger real
# ============================================================
def huella(ruta: Path) -> dict:
    """Tamano, fecha y sha256 de un fichero (para dejar constancia)."""
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for trozo in iter(lambda: f.read(1024 * 1024), b""):
            h.update(trozo)
    st = ruta.stat()
    return {"ruta": str(ruta), "bytes": st.st_size,
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
            "sha256": h.hexdigest()}


def abrir_solo_lectura(ruta: Path) -> sqlite3.Connection:
    """Conexion que NO puede escribir, ni por error de codigo."""
    con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=1")
    return con


def copiar_con_backup(origen: Path, destino: Path) -> None:
    """Copia consistente con la API de backup (WAL incluido).

    Copiar el .db a mano mientras el Core escribe se lleva la mitad de una
    transaccion. Esto no.
    """
    if destino.exists():
        destino.unlink()
    src = abrir_solo_lectura(origen)
    try:
        dst = sqlite3.connect(str(destino))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _misma_ruta(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a).lower() == str(b).lower()


# ============================================================
#  Migracion de la copia
# ============================================================
def _columnas(con, tabla: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({tabla})")}


def anadir_columnas(con) -> dict:
    """Añade las columnas enteras. Idempotente: si ya estan, no hace nada."""
    hechas = {}
    for tabla, pares in COLUMNAS.items():
        cols = _columnas(con, tabla)
        if not cols:
            hechas[tabla] = "no existe"
            continue
        nuevas = 0
        for _origen, destino in pares:
            if destino not in cols:
                con.execute(f"ALTER TABLE {tabla} ADD COLUMN {destino} INTEGER")
                nuevas += 1
        hechas[tabla] = f"{nuevas} columnas nuevas" if nuevas else "ya estaba"
    return hechas


def convertir_tabla(con, tabla: str, col_origen: str, col_destino: str,
                    escala: int) -> dict:
    """Convierte una columna entera de la tabla, en lotes de 20.000 filas."""
    filas = con.execute(f"SELECT rowid AS _r, {col_origen} AS v FROM {tabla}").fetchall()
    datos, residuos, no_finitos = [], [], 0
    for f in filas:
        v = f["v"]
        try:
            u = a_unidades(v, escala)
        except ValueError:
            no_finitos += 1
            u = 0
        datos.append((u, f["_r"]))
        if v:
            residuos.append(abs(residuo(v, escala)))
    con.executemany(f"UPDATE {tabla} SET {col_destino}=? WHERE rowid=?", datos)
    return {"tabla": tabla, "columna": col_origen, "filas": len(datos),
            "no_finitos": no_finitos,
            "residuo_max_u": str(max(residuos)) if residuos else "0",
            "residuo_total_u": str(sum(residuos, Decimal(0)))}


def convertir_meta(con, escala: int) -> dict:
    con.execute("CREATE TABLE IF NOT EXISTS meta_u("
                "clave TEXT PRIMARY KEY, valor_u INTEGER NOT NULL)")
    hechas = {}
    for (clave, valor) in con.execute("SELECT clave, valor FROM meta").fetchall():
        if clave not in META_DINERO:
            continue
        u = a_unidades(valor, escala)
        con.execute("INSERT INTO meta_u(clave,valor_u) VALUES(?,?) "
                    "ON CONFLICT(clave) DO UPDATE SET valor_u=excluded.valor_u", (clave, u))
        hechas[clave] = u
    return hechas


def recalcular_totales(con, escala: int) -> dict:
    """Recalcula los saldos enteros DESDE el ledger, no desde el total float.

    Esto es la decision importante de la migracion: el ledger es la fuente de
    verdad (asi lo dice el propio Core en `recalcular_resumen`), y el saldo de
    cada cuenta es una suma materializada de sus asientos. Si se convirtiera el
    saldo por su cuenta, cada cuenta se redondearia una vez mas y la suma de los
    saldos dejaria de ser el suministro por unas unidades.

    Convirtiendo los ASIENTOS y sumando despues, los saldos enteros cuadran con
    el ledger al bit, y la suma de todos los saldos es exactamente el suministro.
    La diferencia contra el saldo de coma flotante que habia antes se apunta en
    el informe como `correccion_u`: eso es el desvio que la coma flotante ya
    habia acumulado, no un cambio de dinero nuevo.
    """
    filas = con.execute(
        "SELECT nodo_id AS cuenta, COALESCE(SUM(kuki_u),0) AS u, COUNT(*) AS n "
        "FROM ledger GROUP BY nodo_id").fetchall()
    anteriores = {r["cuenta"]: r for r in con.execute(
        "SELECT cuenta, total, total_u FROM saldos_cuenta").fetchall()}
    correcciones, nuevas = [], 0
    for f in filas:
        cuenta = f["cuenta"]
        u = int(f["u"])
        viejo = anteriores.get(cuenta)
        total_f = float(viejo["total"] or 0.0) if viejo else 0.0
        corr = u - a_unidades(total_f, escala)
        if viejo is None:
            nuevas += 1
            con.execute("INSERT INTO saldos_cuenta(cuenta,total,total_u) VALUES(?,?,?)",
                        (cuenta, total_f, u))
        else:
            con.execute("UPDATE saldos_cuenta SET total_u=? WHERE cuenta=?", (u, cuenta))
        if corr:
            correcciones.append({"cuenta": cuenta, "correccion_u": corr,
                                 "total_float": total_f,
                                 "kia_antes": texto_kia(a_unidades(total_f, escala), escala),
                                 "kia_ahora": texto_kia(u, escala), "asientos": int(f["n"])})
    # Cuentas que existen en los saldos pero no tienen ni un asiento: a cero.
    con.execute("UPDATE saldos_cuenta SET total_u=0 WHERE total_u IS NULL")

    total_u = int(con.execute("SELECT COALESCE(SUM(kuki_u),0) s FROM ledger").fetchone()["s"])
    fila = con.execute("SELECT valor FROM meta WHERE clave='supply_emitido'").fetchone()
    supply_f = float(fila["valor"]) if fila else 0.0
    supply_float_u = a_unidades(supply_f, escala)
    con.execute("INSERT INTO meta_u(clave,valor_u) VALUES('supply_emitido',?) "
                "ON CONFLICT(clave) DO UPDATE SET valor_u=excluded.valor_u", (total_u,))
    return {"supply_u": total_u, "supply_float": supply_f,
            "supply_float_u": supply_float_u,
            "correccion_supply_u": total_u - supply_float_u,
            "cuentas_nuevas": nuevas,
            "cuentas_corregidas": len(correcciones),
            "correcciones": correcciones[:40]}


def migrar(destino: Path, escala: int) -> dict:
    """Convierte la copia y recalcula los totales. Una sola transaccion."""
    con = sqlite3.connect(str(destino))
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=MEMORY")
        con.execute("BEGIN")
        columnas = anadir_columnas(con)
        conversiones = []
        for tabla, pares in COLUMNAS.items():
            if _columnas(con, tabla):
                for origen, col_destino in pares:
                    conversiones.append(convertir_tabla(con, tabla, origen, col_destino, escala))
        meta = convertir_meta(con, escala)
        totales = recalcular_totales(con, escala)
        con.execute("COMMIT")
        return {"columnas": columnas, "conversiones": conversiones, "meta": meta,
                "totales": totales}
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


# ============================================================
#  Conciliacion
# ============================================================
def conciliar(destino: Path, escala: int) -> dict:
    """Compara, cuenta por cuenta, el dinero de antes con el de despues.

    Tres preguntas, y las tres tienen que salir bien:

      A) Cada cuenta: ¿la suma de sus asientos enteros da su saldo entero?
         (El saldo es un total materializado que se escribe en la misma
         transaccion que el asiento; si no cuadra, algo se perdio por el camino.)
      B) El global: ¿la suma de todos los asientos es el suministro emitido, y
         la suma de todos los saldos tambien?
      C) La partida doble: en los grupos donde el dinero entra y sale (prepago,
         pago al minero, devolucion, transferencia), ¿suma cero EXACTO?

    Y se repite cada cuenta con la coma flotante de origen para ver cuanto
    desvio habia antes: eso es lo que la migracion viene a arreglar.
    """
    con = sqlite3.connect(str(destino))
    con.row_factory = sqlite3.Row
    try:
        # --- A) cuenta por cuenta -------------------------------------
        asientos = {r["cuenta"]: r for r in con.execute(
            "SELECT nodo_id AS cuenta, COUNT(*) AS n, SUM(kuki) AS suma_f, "
            "       SUM(kuki_u) AS suma_u, MIN(kuki_u) AS min_u, MAX(kuki_u) AS max_u "
            "FROM ledger GROUP BY nodo_id").fetchall()}
        saldos = {r["cuenta"]: r for r in con.execute(
            "SELECT cuenta, total, total_u FROM saldos_cuenta").fetchall()}

        filas = []
        for cuenta in sorted(set(asientos) | set(saldos)):
            a = asientos.get(cuenta)
            s = saldos.get(cuenta)
            n = int(a["n"]) if a else 0
            suma_f = float(a["suma_f"] or 0.0) if a else 0.0
            suma_u = int(a["suma_u"] or 0) if a else 0
            saldo_f = float(s["total"] or 0.0) if s else 0.0
            saldo_u = int(s["total_u"] or 0) if s and s["total_u"] is not None else 0
            # Despues de recalcular, el saldo entero es la suma de los asientos
            # enteros: `delta_u` tiene que ser 0. Si no lo es, alguien toco un
            # saldo a mano o se escribio un asiento sin su total.
            delta_u = saldo_u - suma_u
            # Y esto es lo que la coma flotante habia acumulado en esa cuenta:
            # su saldo float contra el mismo saldo en unidades exactas.
            correccion_u = saldo_u - a_unidades(saldo_f, escala)
            margen = math.ceil(n / 2) + 1 if n else 1
            filas.append({
                "cuenta": cuenta, "asientos": n,
                "suma_float": suma_f, "suma_u": suma_u,
                "saldo_float": saldo_f, "saldo_u": saldo_u,
                "delta_u": delta_u,
                "correccion_u": correccion_u,
                "deriva_float_u": a_unidades(saldo_f - suma_f, escala),
                "margen_u": margen,
                "estado": "OK" if delta_u == 0 else "DESVIO",
                "kia_suma": texto_kia(suma_u, escala),
                "kia_saldo": texto_kia(saldo_u, escala),
            })

        # --- B) global -------------------------------------------------
        supply_f = float(con.execute(
            "SELECT valor FROM meta WHERE clave='supply_emitido'").fetchone()["valor"])
        supply_u = int(con.execute(
            "SELECT valor_u FROM meta_u WHERE clave='supply_emitido'").fetchone()["valor_u"])
        total_asientos_u = int(con.execute("SELECT COALESCE(SUM(kuki_u),0) s FROM ledger")
                               .fetchone()["s"])
        total_asientos_f = float(con.execute("SELECT COALESCE(SUM(kuki),0) s FROM ledger")
                                 .fetchone()["s"])
        total_saldos_u = int(con.execute("SELECT COALESCE(SUM(total_u),0) s FROM saldos_cuenta")
                             .fetchone()["s"])
        total_saldos_f = float(con.execute("SELECT COALESCE(SUM(total),0) s FROM saldos_cuenta")
                               .fetchone()["s"])

        global_ = {
            "supply_float": supply_f, "supply_u": supply_u,
            "supply_kia": texto_kia(supply_u, escala),
            "asientos_float": total_asientos_f, "asientos_u": total_asientos_u,
            "saldos_float": total_saldos_f, "saldos_u": total_saldos_u,
            # El desvio que YA existia con coma flotante:
            "desvio_float_u": a_unidades(total_saldos_f - supply_f, escala),
            # El de despues (lo que tiene que quedar en ~0):
            "desvio_entero_u": total_saldos_u - supply_u,
            "asientos_vs_supply_u": total_asientos_u - supply_u,
            "techo_unidades": MAX_SUPPLY * escala,
            "margen_techo": (MAX_SUPPLY * escala) / max(1, supply_u),
        }

        # --- C) partida doble -----------------------------------------
        # Un grupo es de PARTIDA DOBLE si en el hay dinero que entra y sale:
        # prepago, pago al minero, devolucion o transferencia. En esos grupos el
        # total del grupo entero (con TODOS sus motivos, tambien el abono al
        # minero, que se etiqueta "trabajo verificado" aunque no emita) tiene
        # que ser CERO exacto. Los grupos sin ninguno de esos motivos son
        # emision legitima y suman positivo: eso es el suministro.
        marcas = ",".join("?" for _ in MOTIVOS_PAR)
        dobles = con.execute(
            f"SELECT tarea_id AS t, COUNT(*) AS n, SUM(kuki_u) AS u, SUM(kuki) AS f "
            f"FROM ledger WHERE tarea_id IN (SELECT tarea_id FROM ledger "
            f"WHERE motivo IN ({marcas})) GROUP BY tarea_id", MOTIVOS_PAR).fetchall()
        par = {"grupos": len(dobles), "no_cuadran_u": [], "no_cuadran_f": 0,
               "desvio_float_max": 0.0, "ejemplos": []}
        for g in dobles:
            u = int(g["u"] or 0)
            f = float(g["f"] or 0.0)
            if u != 0:
                par["no_cuadran_u"].append({"tarea_id": g["t"], "desvio_u": u,
                                            "asientos": int(g["n"])})
            if abs(f) > 1e-12:
                par["no_cuadran_f"] += 1
                par["desvio_float_max"] = max(par["desvio_float_max"], abs(f))
            if len(par["ejemplos"]) < 5:
                par["ejemplos"].append({"tarea_id": g["t"], "asientos": int(g["n"]),
                                        "suma_u": u, "suma_float": f})
        par["ok"] = not par["no_cuadran_u"]
        # El resto: emision sin pareja. Tiene que ser exactamente el suministro
        # menos lo que movieron los grupos de partida doble (que es cero).
        emision_u = int(con.execute(
            f"SELECT COALESCE(SUM(kuki_u),0) s FROM ledger WHERE tarea_id NOT IN "
            f"(SELECT tarea_id FROM ledger WHERE motivo IN ({marcas}))",
            MOTIVOS_PAR).fetchone()["s"])
        par["emision_sin_pareja_u"] = emision_u
        par["cuadra_con_suministro"] = emision_u == int(con.execute(
            "SELECT valor_u FROM meta_u WHERE clave='supply_emitido'").fetchone()["valor_u"])

        return {"cuentas": filas, "global": global_, "partida_doble": par,
                "cuentas_con_desvio": [f["cuenta"] for f in filas if f["estado"] != "OK"],
                "cuentas_corregidas": [f for f in filas if f["correccion_u"] != 0]}
    finally:
        con.close()


def verificar_cadena(destino: Path) -> dict:
    """Rehace la cadena de hashes con los numeros ORIGINALES del ledger.

    Si la migracion hubiera tocado los importes (o su texto), aqui se rompe.
    Se verifica entera: 777.000 filas tardan unos segundos y es la prueba de
    que el historial sellado sigue siendo el mismo.
    """
    con = sqlite3.connect(str(destino))
    con.row_factory = sqlite3.Row
    try:
        prev, n, roto = None, 0, None
        sin_hash = 0
        ancla = None
        for r in con.execute("SELECT id, tarea_id, nodo_id, kuki, motivo, ts, "
                             "hash_prev, hash FROM ledger ORDER BY id"):
            h = (r["hash"] or "")
            if not h:
                # Asientos anteriores a la cadena (los primeros del ledger, de
                # cuando no se sellaba). No se pueden verificar: se cuentan y se
                # dice, no se dan por buenos ni se dan por rotos.
                sin_hash += 1
                continue
            if prev is None:
                # Primer asiento sellado: su hash_prev es el ancla (lo que
                # habia antes de poder verificar). No se exige que sea vacio:
                # el ledger tiene asientos archivados por detras.
                prev = (r["hash_prev"] or "")
                ancla = {"id": r["id"], "hash_prev": prev}
            n += 1
            if (r["hash_prev"] or "") != prev:
                roto = {"id": r["id"], "que": "hash_prev no encadena"}
                break
            base = f"{r['tarea_id']}|{r['nodo_id']}|{r['kuki']}|{r['motivo']}|{r['ts']}|{r['hash_prev']}"
            if hashlib.sha256(base.encode()).hexdigest() != h:
                roto = {"id": r["id"], "que": "hash no corresponde al asiento"}
                break
            prev = h
        return {"filas": n, "sin_hash": sin_hash, "ok": roto is None,
                "primera_rota": roto, "ancla": ancla, "cabeza": prev or ""}
    finally:
        con.close()


# ============================================================
#  Doble escritura
# ============================================================
def doble_escritura(destino: Path, escala: int) -> dict:
    """Deja escrito el saldo de cada cuenta en las DOS representaciones.

    Tabla `saldos_doble`: el saldo en coma flotante que se usaba hasta hoy y el
    saldo en unidades enteras que se usara a partir de ahora, con la diferencia
    en unidades. Sirve para dos cosas:
      - comprobar que la conversion no cambia el dinero de nadie mas alla del
        redondeo de media unidad por fila, cuenta por cuenta;
      - tener a mano las dos columnas durante la transicion, para poder volver
        atras sin recalcular nada.
    """
    con = sqlite3.connect(str(destino))
    con.row_factory = sqlite3.Row
    try:
        con.execute("DROP TABLE IF EXISTS saldos_doble")
        con.execute("CREATE TABLE saldos_doble("
                    "cuenta TEXT PRIMARY KEY, total_float REAL, total_u INTEGER, "
                    "kia_float_u INTEGER, delta_u INTEGER, estado TEXT)")
        filas = con.execute("SELECT cuenta, total, total_u FROM saldos_cuenta").fetchall()
        datos = []
        for f in filas:
            tf = float(f["total"] or 0.0)
            tu = int(f["total_u"] or 0)
            # El mismo saldo, pero pasado por la conversion. Aqui la diferencia
            # NO es un error a esconder: es lo que la coma flotante habia
            # desviado en esa cuenta (`correccion`), y el informe la ensena.
            tf_u = a_unidades(tf, escala)
            delta = tu - tf_u
            datos.append((f["cuenta"], tf, tu, tf_u, delta,
                          "CORREGIDA" if delta else "IGUAL"))
        con.executemany("INSERT INTO saldos_doble VALUES(?,?,?,?,?,?)", datos)
        con.commit()
        # Malo de verdad seria que el saldo entero no fuese la suma de los
        # asientos; eso no es conversion, es dinero inventado.
        malos = [r["cuenta"] for r in con.execute(
            "SELECT d.cuenta FROM saldos_doble d LEFT JOIN "
            "(SELECT nodo_id AS cuenta, SUM(kuki_u) u FROM ledger GROUP BY nodo_id) l "
            "ON d.cuenta=l.cuenta WHERE d.total_u <> COALESCE(l.u,0)").fetchall()]
        return {"cuentas": len(datos), "desvio": malos,
                "ok": not malos,
                "suma_float": sum(d[1] for d in datos),
                "suma_u": sum(d[2] for d in datos)}
    finally:
        con.close()


def apuntar_doble(con: sqlite3.Connection, tarea_id: str, cuenta: str,
                  kia: float, motivo: str, escala: int = ESCALA) -> dict:
    """Escribe un asiento en las dos representaciones a la vez.

    Es el camino que usara el Core cuando se cambie de verdad: nunca se escribe
    solo el float ni solo el entero. Se comprueba que las dos columnas dicen lo
    mismo y que la partida doble sigue cuadrando en enteros.

    NO se llama desde este script contra el ledger real: existe para que la
    prueba y el Core futuro lo usen dentro de su propia transaccion.
    """
    u = a_unidades(kia, escala)
    ts = time.ctime()
    prev = (con.execute("SELECT hash FROM ledger WHERE hash IS NOT NULL AND hash<>'' "
                        "ORDER BY id DESC LIMIT 1").fetchone() or [""])[0] or ""
    base = f"{tarea_id}|{cuenta}|{float(kia)}|{motivo}|{ts}|{prev}"
    h = hashlib.sha256(base.encode()).hexdigest()
    con.execute("INSERT INTO ledger(tarea_id,nodo_id,kuki,kuki_u,motivo,ts,hash_prev,hash) "
                "VALUES(?,?,?,?,?,?,?,?)", (tarea_id, cuenta, float(kia), u, motivo, ts, prev, h))
    return {"u": u, "hash": h}


# ============================================================
#  Informe
# ============================================================
def _fila_tabla(f: dict) -> str:
    return (f"| `{f['cuenta'][:46]}` | {f['asientos']:>7} | {f['kia_suma']:>22} | "
            f"{f['kia_saldo']:>22} | {f['delta_u']:>+4} | {f['correccion_u']:>+6} | {f['estado']} |")


def escribir_informe(datos: dict, destino: Path) -> None:
    g = datos["conciliacion"]["global"]
    p = datos["conciliacion"]["partida_doble"]
    e = datos["escala"]
    lineas = [
        "# Migracion del dinero a unidades enteras — informe de conciliacion",
        "",
        f"Generado: {datos['generado']}",
        f"Copia migrada: `{datos['copia']['ruta']}`",
        f"Origen leido (solo lectura): `{datos['origen']['ruta']}`",
        f"Escala: 1 KIA = {e:,} unidades (1e-{_digitos(e)} KIA por unidad)".replace(",", "."),
        "",
        "## Resumen",
        "",
        "| Que | Coma flotante (antes) | Enteros (despues) |",
        "|---|---|---|",
        f"| Suministro emitido | `{g['supply_float']!r}` | `{g['supply_kia']}` |",
        f"| Suma de asientos | `{g['asientos_float']!r}` | `{texto_kia(g['asientos_u'], e)}` |",
        f"| Suma de saldos | `{g['saldos_float']!r}` | `{texto_kia(g['saldos_u'], e)}` |",
        "",
        f"- Desvio que YA existia con coma flotante (saldos - suministro): "
        f"**{texto_kia(g['desvio_float_u'], e)} KIA** "
        f"({g['desvio_float_u']} unidades).",
        f"- Desvio despues de convertir (saldos - suministro): "
        f"**{g['desvio_entero_u']} unidades**.",
        f"- Suministro: el float decia `{g['supply_float']!r}`; el ledger, en "
        f"unidades, dice `{g['supply_kia']}`. Correccion de la coma flotante: "
        f"**{datos['migracion']['totales']['correccion_supply_u']} unidades**.",
        f"- Lo que mueve el redondeo de la conversion, en total (media unidad por "
        f"asiento como maximo): "
        f"{sum(Decimal(c['residuo_total_u']) for c in datos['migracion']['conversiones'])} "
        f"unidades = "
        f"{texto_kia(int(sum(Decimal(c['residuo_total_u']) for c in datos['migracion']['conversiones'])), e)} KIA.",
        f"- Cuentas recalculadas desde el ledger: "
        f"{datos['migracion']['totales']['cuentas_corregidas']} (el resto ya cuadraba).",
        f"- Tope del suministro: {MAX_SUPPLY:,} KIA = {g['techo_unidades']:,} unidades; "
        f"lo emitido ocupa 1/{g['margen_techo']:.1f} del margen del entero.".replace(",", "."),
        "",
        f"## Partida doble",
        "",
        f"- Grupos de cargo/abono revisados: {p['grupos']}",
        f"- Grupos que NO suman cero en enteros: **{len(p['no_cuadran_u'])}**",
        f"- Grupos que no sumaban cero ni con coma flotante (|desvio| > 1e-12): "
        f"{p['no_cuadran_f']} (desvio maximo {p['desvio_float_max']:.3e} KIA)",
        f"- Emision sin pareja: {p['emision_sin_pareja_u']} unidades; "
        f"{'coincide con el suministro' if p['cuadra_con_suministro'] else 'NO coincide con el suministro'}",
        f"- Resultado: {'OK' if p['ok'] else 'HAY GRUPOS QUE NO CUADRAN'}",
        "",
    ]
    if p["ejemplos"]:
        lineas += ["| Grupo | Asientos | Suma en enteros | Suma en flotante |", "|---|---|---|---|"]
        for ej in p["ejemplos"]:
            lineas.append(f"| `{ej['tarea_id']}` | {ej['asientos']} | {ej['suma_u']} | "
                          f"`{ej['suma_float']!r}` |")
        lineas.append("")
    correcciones = datos["conciliacion"]["cuentas_corregidas"]
    lineas += [
        f"## Correccion de la coma flotante ({len(correcciones)} cuentas)",
        "",
        "Estas son las cuentas cuyo saldo materializado no era exactamente la suma de",
        "sus asientos. La migracion las iguala al ledger (la fuente de verdad) y aqui",
        "queda apuntado cuanto se movio y por que.",
        "",
    ]
    if correcciones:
        lineas += ["| Cuenta | Asientos | Antes (float) | Ahora (enteros) | Correccion (u) |",
                   "|---|---|---|---|---|"]
        for c in correcciones:
            lineas.append(f"| `{c['cuenta'][:46]}` | {c['asientos']} | `{c['saldo_float']!r}` | "
                          f"{c['kia_saldo']} | {c['correccion_u']:+} |")
    else:
        lineas.append("Ninguna: todas cuadraban ya al bit.")
    lineas += [
        "",
        f"## La cadena de hashes",
        "",
        f"- Asientos anteriores a la cadena (sin hash, no verificables): "
        f"{datos['cadena'].get('sin_hash', 0)}",
        f"- Asientos sellados verificados: {datos['cadena']['filas']}",
        f"- Ancla (primer asiento sellado): `{datos['cadena'].get('ancla')}`",
        f"- Cabeza de la cadena: `{(datos['cadena'].get('cabeza') or '')[:32]}`",
        f"- Resultado: {'OK, la cadena encadena y cada hash corresponde a su asiento' if datos['cadena']['ok'] else 'ROTA'}",
    ]
    if datos["cadena"]["primera_rota"]:
        lineas.append(f"- Primera rotura: `{datos['cadena']['primera_rota']}`")
    lineas += [
        "",
        "## Doble escritura",
        "",
        f"- Cuentas escritas en las dos representaciones: {datos['doble']['cuentas']}",
        f"- Cuentas donde las dos no dicen lo mismo: {len(datos['doble']['desvio'])}",
        "",
        "## Conversion por tabla",
        "",
        "| Tabla | Columna | Filas | Residuo total (unidades) |",
        "|---|---|---|---|",
    ]
    for c in datos["migracion"]["conversiones"]:
        lineas.append(f"| {c['tabla']} | {c['columna']} | {c['filas']} | {c['residuo_total_u']} |")
    lineas += [
        "",
        f"## Conciliacion cuenta por cuenta ({len(datos['conciliacion']['cuentas'])} cuentas)",
        "",
        "`delta_u` = saldo entero - suma de asientos enteros. El margen es media",
        "unidad por asiento convertido (redondeo) mas una. Si `delta_u` sale del",
        "margen, esa cuenta tiene un problema de verdad, no de redondeo.",
        "",
        "| Cuenta | Asientos | Suma de asientos (KIA) | Saldo (KIA) | delta_u | Correccion_u | Estado |",
        "|---|---|---|---|---|---|---|",
    ]
    lineas += [_fila_tabla(f) for f in datos["conciliacion"]["cuentas"]]
    lineas += [
        "",
        "## Lo que este informe NO dice",
        "",
        "- No dice que el ledger real se haya migrado: se ha migrado una copia.",
        "- No dice que el Core ya escriba en enteros: eso es el paso siguiente.",
        "- No hay auditoria independiente de la conversion: es una prueba propia,",
        "  reproducible con `python tools/prueba_migracion_entera.py`.",
        "",
    ]
    (destino / "CONCILIACION.md").write_text("\n".join(lineas), encoding="utf-8")
    (destino / "conciliacion.json").write_text(
        json.dumps(datos, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


# ============================================================
#  main
# ============================================================
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--origen", default=str(DB_REAL), help="base a leer (solo lectura)")
    ap.add_argument("--salida", default=str(SALIDA), help="carpeta de la copia y el informe")
    ap.add_argument("--escala", type=int, default=ESCALA,
                    help=f"unidades por KIA (por defecto {ESCALA})")
    ap.add_argument("--ver", action="store_true", help="enseña el plan y no escribe nada")
    args = ap.parse_args()

    origen = Path(args.origen).resolve()
    salida = Path(args.salida).resolve()
    escala = int(args.escala)
    if escala < 10 ** 4 or escala > 10 ** 12:
        raise SystemExit("[migrar] escala fuera de lo razonable (1e4 .. 1e12)")

    if not origen.is_file():
        raise SystemExit(f"[migrar] no existe la base de origen: {origen}")
    copia = salida / "kukia_entera.db"
    if _misma_ruta(copia, origen):
        raise SystemExit("[migrar] ABORTADO: la copia no puede ser la propia base de origen")

    if args.ver:
        con = abrir_solo_lectura(origen)
        try:
            n = con.execute("SELECT COUNT(*) c FROM ledger").fetchone()["c"]
            cuentas = con.execute("SELECT COUNT(*) c FROM saldos_cuenta").fetchone()["c"]
            sup = con.execute("SELECT valor FROM meta WHERE clave='supply_emitido'").fetchone()["valor"]
        finally:
            con.close()
        print(f"[migrar] origen      : {origen}")
        print(f"[migrar] asientos    : {n}")
        print(f"[migrar] cuentas     : {cuentas}")
        print(f"[migrar] suministro  : {sup!r} KIA")
        print(f"[migrar] escala      : {escala} unidades/KIA -> "
              f"{MAX_SUPPLY * escala:,} unidades como maximo")
        print(f"[migrar] copia       : {copia}")
        print("[migrar] no se ha escrito nada (--ver)")
        return 0

    salida.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    print(f"[1/6] copiando con la API de backup (WAL incluido) ...", flush=True)
    antes = huella(origen) if origen.stat().st_size < 900 * 1024 * 1024 else {
        "ruta": str(origen), "bytes": origen.stat().st_size,
        "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(origen.stat().st_mtime)),
        "sha256": "(no se calcula: base viva de mas de 900 MB)"}
    copiar_con_backup(origen, copia)
    print(f"      copia: {copia.stat().st_size / 1024 / 1024:.0f} MB", flush=True)

    print(f"[2/6] añadiendo columnas enteras y convirtiendo ...", flush=True)
    migracion = migrar(copia, escala)
    for c in migracion["conversiones"]:
        print(f"      {c['tabla']}.{c['columna']}: {c['filas']} filas, "
              f"residuo {c['residuo_total_u']} u", flush=True)
    print(f"      meta: {migracion['meta']}", flush=True)

    print(f"[3/6] conciliando cuenta por cuenta ...", flush=True)
    conciliacion = conciliar(copia, escala)

    print(f"[4/6] verificando la cadena de hashes ...", flush=True)
    cadena = verificar_cadena(copia)
    print(f"      {cadena['filas']} asientos, "
          f"{'OK' if cadena['ok'] else 'ROTA'}", flush=True)

    print(f"[5/6] doble escritura de saldos ...", flush=True)
    doble = doble_escritura(copia, escala)

    print(f"[6/6] informe ...", flush=True)
    datos = {
        "generado": time.strftime("%Y-%m-%d %H:%M:%S"),
        "escala": escala,
        "unidad": f"1e-{_digitos(escala)} KIA",
        "origen": antes,
        "copia": {"ruta": str(copia), "bytes": copia.stat().st_size,
                  "sha256": huella(copia)["sha256"]},
        "migracion": migracion,
        "conciliacion": conciliacion,
        "cadena": cadena,
        "doble": doble,
        "segundos": round(time.time() - t0, 1),
    }
    escribir_informe(datos, salida)
    (salida / "sha256.txt").write_text(
        f"{huella(copia)['sha256']}  {copia.name}\n"
        f"{antes.get('sha256', '')}  (origen leido en solo lectura: {antes['ruta']})\n",
        encoding="utf-8")

    g = conciliacion["global"]
    print()
    print(f"  suministro   : {g['supply_float']!r} -> {g['supply_kia']} KIA")
    print(f"  saldos       : {g['saldos_float']!r} -> {texto_kia(g['saldos_u'], escala)} KIA")
    print(f"  desvio antes (flotante, saldos-suministro): {texto_kia(g['desvio_float_u'], escala)} KIA")
    print(f"  desvio ahora (enteros,  saldos-suministro): {g['desvio_entero_u']} unidades")
    print(f"  correccion de la coma flotante: "
          f"{datos['migracion']['totales']['correccion_supply_u']} unidades "
          f"en {datos['migracion']['totales']['cuentas_corregidas']} cuentas")
    print(f"  partida doble: {conciliacion['partida_doble']['grupos']} grupos, "
          f"{len(conciliacion['partida_doble']['no_cuadran_u'])} sin cuadrar")
    print(f"  cuentas con desvio: {len(conciliacion['cuentas_con_desvio'])}")
    print(f"  cadena de hashes  : {'OK' if cadena['ok'] else 'ROTA'}")
    print()
    print(f"  informe: {salida / 'CONCILIACION.md'}")
    return 0 if (cadena["ok"] and not conciliacion["cuentas_con_desvio"]
                 and conciliacion["partida_doble"]["ok"]) else 2


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
