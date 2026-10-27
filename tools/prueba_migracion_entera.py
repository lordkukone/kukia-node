#!/usr/bin/env python3
"""Pruebas de la migracion del dinero a unidades enteras.

Todo se hace sobre bases falsas en un directorio temporal: el ledger real no
se abre ni para leer. Lo que se comprueba:

  1. La conversion es exacta y simetrica (el mismo importe con el signo
     cambiado da exactamente el importe contrario), incluidos los casos que en
     coma flotante dan guerra: 0,1+0,2, 1e-10, -0,0, importes diminutos.
  2. Caben en un entero de 64 bits, tambien el techo de 100 millones de KIA.
  3. La migracion de una copia conserva la partida doble (cargo y abono del
     mismo dinero suman CERO exacto), el suministro y los saldos.
  4. Se puede repetir sin cambiar nada (idempotente).
  5. La cadena de hashes sigue valida despues de migrar.
  6. La conciliacion DETECTA un saldo tocado a mano (prueba negativa: si esta
     prueba pasara con los datos tocados, el informe no valdria nada).
  7. El script se niega a escribir sobre la propia base de origen.

Uso:  python tools/prueba_migracion_entera.py
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import migrar_entero as M  # noqa: E402

INT64 = 2 ** 63 - 1
fallos: list[str] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(("  OK   " if ok else "  FALLO") + f"  {nombre}" + (f"  [{detalle}]" if detalle else ""))
    if not ok:
        fallos.append(nombre)


# ============================================================
#  1 y 2: conversion
# ============================================================
def probar_conversion(escala: int = M.ESCALA) -> None:
    print("\n1. Conversion exacta y simetrica")
    u = lambda v: M.a_unidades(v, escala)  # noqa: E731
    casos = [
        (0.0, 0), (-0.0, 0), (1.0, escala), (0.5, escala // 2),
        (0.1, escala // 10), (0.01, escala // 100),
        # 0.1+0.2 no es 0.3 en binario, pero SI es 0.3 escrito: tiene que dar
        # los mismos 3.000.000.000 que 0.3, no uno de mas.
        (0.1 + 0.2, 3 * escala // 10),
        (0.0000000001, 1),                 # una unidad: el suelo real
        (0.00000000005, 1),                # media unidad: redondea hacia arriba
        (-0.00000000005, -1),
        (0.00000004, 4 * escala // 10 ** 8),
        (15088160.701462887, 150881607014628870),
        (15000000.0, 15000000 * escala),
    ]
    for valor, esperado in casos:
        obtenido = u(valor)
        check(f"{valor!r} -> {esperado}", obtenido == esperado, f"dio {obtenido}")

    print("\n2. Simetria del signo (lo que sostiene la partida doble)")
    simetricos = [0.1, 0.30000000000000004, 1e-10, 5e-11, 4.6e-7, 12345.6789012345,
                  0.00000000012345, 15_000_000.0]
    for v in simetricos:
        check(f"-({v!r}) == -({v!r})", u(-v) == -u(v), f"{u(-v)} vs {-u(v)}")

    print("\n3. Cabe en un entero de 64 bits")
    tope = M.MAX_SUPPLY * escala
    check("techo de suministro", tope <= INT64, f"{tope} <= {INT64}")
    check("lo emitido hoy (15,1 M KIA)", 15_100_000 * escala <= INT64)
    check("margen del techo", INT64 // tope >= 9, f"x{INT64 // tope}")

    print("\n4. Lo que no es dinero se rechaza")
    for malo in (float("nan"), float("inf"), float("-inf")):
        try:
            u(malo)
            check(f"{malo} rechazado", False, "no salto")
        except ValueError:
            check(f"{malo} rechazado", True)

    print("\n5. El residuo nunca pasa de media unidad")
    peor = Decimal(0)
    for i in range(2000):
        v = (i - 1000) * 1e-11 + 3.7e-7
        peor = max(peor, abs(M.residuo(v, escala)))
    check("|residuo| <= 0,5 unidades", peor <= Decimal("0.5"), f"peor {peor}")


# ============================================================
#  Base sintetica con cadena de hashes de verdad
# ============================================================
def construir_base(ruta: Path, escala: int = M.ESCALA) -> None:
    """Un ledger pequeno pero con todos los casos dificiles.

    - genesis (emision)
    - una tarea de cliente: prepago (cargo+abono), pago al minero, devolucion
    - una transferencia entre dos cuentas
    - un asiento diminuto (4e-8) y otro de media unidad (5e-11)
    - un asiento de 0,1+0,2 (el clasico que no suma 0,3 en binario)
    """
    if ruta.exists():
        ruta.unlink()
    con = sqlite3.connect(str(ruta))
    con.executescript("""
        CREATE TABLE ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, tarea_id TEXT,
            nodo_id TEXT, kuki REAL, motivo TEXT, ts TEXT, hash_prev TEXT, hash TEXT);
        CREATE TABLE saldos_cuenta(cuenta TEXT PRIMARY KEY, total REAL NOT NULL);
        CREATE TABLE transferencias(id INTEGER PRIMARY KEY AUTOINCREMENT, de_nodo TEXT,
            a_direccion TEXT, monto REAL, nonce TEXT, ts TEXT, firma TEXT, clave_publica TEXT);
        CREATE TABLE tareas(id TEXT PRIMARY KEY, cliente TEXT, estado TEXT,
            prepago REAL, precio_cliente REAL, pagado REAL);
        CREATE TABLE clientes(id TEXT PRIMARY KEY, nombre TEXT, activo INT, gastado REAL);
        CREATE TABLE meta(clave TEXT PRIMARY KEY, valor REAL NOT NULL);
    """)
    con.execute("INSERT INTO meta VALUES('supply_emitido',0)")
    for k in ("bolsa_credito", "bolsa_kia", "bolsa_pagado"):
        con.execute("INSERT INTO meta VALUES(?,0)", (k,))
    con.execute("INSERT INTO meta VALUES('bolsa_minuto',29882652)")

    def asiento(tarea, cuenta, kia, motivo):
        prev = (con.execute("SELECT hash FROM ledger WHERE hash IS NOT NULL AND hash<>'' "
                            "ORDER BY id DESC LIMIT 1").fetchone() or [""])[0] or ""
        ts = "Sun Oct 25 21:00:00 2026"
        base = f"{tarea}|{cuenta}|{float(kia)}|{motivo}|{ts}|{prev}"
        h = hashlib.sha256(base.encode()).hexdigest()
        con.execute("INSERT INTO ledger(tarea_id,nodo_id,kuki,motivo,ts,hash_prev,hash) "
                    "VALUES(?,?,?,?,?,?,?)", (tarea, cuenta, float(kia), motivo, ts, prev, h))
        con.execute("INSERT INTO saldos_cuenta(cuenta,total) VALUES(?,?) "
                    "ON CONFLICT(cuenta) DO UPDATE SET total=total+excluded.total",
                    (cuenta, float(kia)))
        con.execute("UPDATE meta SET valor=valor+? WHERE clave='supply_emitido'", (float(kia),))

    ESCROW = "KUKESCROW"
    A, B, C = "KUKA", "KUKB", "KUKC"
    asiento("genesis", "KUKGENESIS", 15_000_000.0, "asignacion fundacional")
    asiento("em1", A, 0.00000004, "trabajo verificado")       # 400 u
    asiento("em2", A, 0.00000000005, "trabajo verificado")    # media unidad
    asiento("em3", B, 0.1 + 0.2, "trabajo verificado")        # 0,30000000000000004
    asiento("em4", C, 1e-10, "trabajo verificado")            # 1 u
    # Tarea de cliente: prepago, pago y devolucion del sobrante.
    deposito = 0.0002
    pago = 0.00015
    sobra = round(deposito - pago, 12)
    asiento("t1", C, -deposito, "prepago cliente")
    asiento("t1", ESCROW, deposito, "prepago cliente")
    asiento("t1", ESCROW, -pago, "pago a minero (cliente)")
    asiento("t1", A, pago, "trabajo verificado")
    asiento("t1", ESCROW, -sobra, "devolucion sobrante")
    asiento("t1", C, sobra, "devolucion sobrante")
    # Transferencia entre cuentas: los dos lados tienen que cuadrar.
    asiento("tr1", A, -0.25, "transferencia salida")
    asiento("tr1", B, 0.25, "transferencia entrada")
    con.execute("INSERT INTO transferencias(de_nodo,a_direccion,monto,nonce,ts) "
                "VALUES(?,?,?,?,?)", (A, B, 0.25, "n1", "Sun Oct 25 21:00:01 2026"))
    con.execute("INSERT INTO tareas VALUES('t1',?, 'pagada', ?, ?, ?)",
                (C, 0.0, deposito, pago))
    con.execute("INSERT INTO clientes VALUES('c1','cliente de prueba',1,?)", (deposito,))
    con.commit()
    con.close()


def abrir(ruta: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(ruta))
    con.row_factory = sqlite3.Row
    return con


# ============================================================
#  6 y 7: migrar, repetir y conciliar
# ============================================================
def probar_migracion(carpeta: Path) -> Path:
    escala = M.ESCALA
    original = carpeta / "sintetica.db"
    construir_base(original, escala)
    copia = carpeta / "copia.db"
    M.copiar_con_backup(original, copia)

    print("\n6. La migracion conserva el dinero")
    antes = {}
    con = abrir(copia)
    antes["supply"] = con.execute("SELECT valor FROM meta WHERE clave='supply_emitido'").fetchone()[0]
    antes["saldo_A"] = con.execute("SELECT total FROM saldos_cuenta WHERE cuenta='KUKA'").fetchone()[0]
    antes["asientos"] = con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
    con.close()

    mig = M.migrar(copia, escala)
    check("columnas convertidas", all("columnas" in mig for _ in [0]))
    con = abrir(copia)
    n_sin = con.execute("SELECT COUNT(*) FROM ledger WHERE kuki_u IS NULL").fetchone()[0]
    check("ningun asiento sin su entero", n_sin == 0, f"{n_sin} a null")
    # Cada fila: el entero tiene que ser la conversion del float de al lado.
    malas = 0
    for r in con.execute("SELECT kuki, kuki_u FROM ledger"):
        if M.a_unidades(r["kuki"], escala) != r["kuki_u"]:
            malas += 1
    check("cada entero coincide con su float", malas == 0, f"{malas} filas")

    print("\n7. Partida doble exacta en enteros")
    for tarea in ("t1", "tr1"):
        s = con.execute("SELECT SUM(kuki_u) s FROM ledger WHERE tarea_id=?", (tarea,)).fetchone()["s"]
        check(f"grupo {tarea} suma 0 exacto (todos sus motivos)", s == 0, f"suma {s}")
    con.close()
    conc = M.conciliar(copia, escala)
    check("la conciliacion sale limpia",
          not conc["cuentas_con_desvio"] and conc["partida_doble"]["ok"],
          f"{conc['cuentas_con_desvio']} / {len(conc['partida_doble']['no_cuadran_u'])}")
    g = conc["global"]
    suma_asientos = con2 = None
    con2 = abrir(copia)
    suma_asientos = con2.execute("SELECT SUM(kuki_u) s FROM ledger").fetchone()["s"]
    con2.close()
    check("el suministro sale de los asientos, no del float acumulado",
          g["supply_u"] == suma_asientos, f"{g['supply_u']} vs {suma_asientos}")
    check("suma de saldos == suministro (enteros)", g["saldos_u"] == g["supply_u"],
          f"{g['saldos_u']} vs {g['supply_u']}")
    # El float acumulado no daba el suministro exacto: la diferencia es justo
    # la deriva que la miga corrige, y tiene que ser pequena (media unidad por
    # asiento como mucho), no una cifra gorda que delate un fallo.
    deriva = g["supply_u"] - M.a_unidades(antes["supply"], escala)
    margen = antes["asientos"] // 2 + 2
    check("la correccion del suministro cabe en el margen de redondeo",
          abs(deriva) <= margen, f"{deriva} u (margen {margen})")

    print("\n8. Cadena de hashes intacta")
    cad = M.verificar_cadena(copia)
    check("la cadena encadena y los hashes cuadran", cad["ok"], str(cad["primera_rota"]))

    print("\n9. Doble escritura")
    doble = M.doble_escritura(copia, escala)
    check("las dos representaciones dicen lo mismo", doble["ok"], str(doble["desvio"]))

    print("\n10. Repetir la migracion no cambia nada (idempotente)")
    con = abrir(copia)
    foto = con.execute("SELECT id, kuki_u FROM ledger ORDER BY id").fetchall()
    con.close()
    M.migrar(copia, escala)
    con = abrir(copia)
    foto2 = con.execute("SELECT id, kuki_u FROM ledger ORDER BY id").fetchall()
    con.close()
    check("mismos valores tras migrar dos veces",
          [tuple(a) for a in foto] == [tuple(b) for b in foto2])

    print("\n11. El asiento diminuto no se pierde")
    con = abrir(copia)
    fila = con.execute("SELECT kuki, kuki_u FROM ledger WHERE tarea_id='em2'").fetchone()
    con.close()
    check("media unidad redondea a 1", fila["kuki_u"] == 1, f"{fila['kuki']!r} -> {fila['kuki_u']}")
    con = abrir(copia)
    fila = con.execute("SELECT kuki_u FROM ledger WHERE tarea_id='em4'").fetchone()
    con.close()
    check("1e-10 es exactamente 1 unidad", fila["kuki_u"] == 1)
    return copia


def probar_deteccion(copia: Path, carpeta: Path) -> None:
    """Pruebas negativas: el informe TIENE que ver lo que esta mal."""
    print("\n12. La conciliacion detecta un saldo tocado a mano")
    rota = carpeta / "rota.db"
    shutil.copy2(copia, rota)
    con = abrir(rota)
    con.execute("UPDATE saldos_cuenta SET total_u = total_u + 5 WHERE cuenta='KUKB'")
    con.commit()
    con.close()
    conc = M.conciliar(rota, M.ESCALA)
    check("la cuenta tocada sale marcada", "KUKB" in conc["cuentas_con_desvio"],
          str(conc["cuentas_con_desvio"]))

    print("\n13. La conciliacion detecta un asiento de dinero inventado")
    rota2 = carpeta / "rota2.db"
    shutil.copy2(copia, rota2)
    con = abrir(rota2)
    # Un cargo sin su abono: el dinero sale de la nada.
    con.execute("INSERT INTO ledger(tarea_id,nodo_id,kuki,kuki_u,motivo,ts,hash_prev,hash) "
                "VALUES('fantasma','KUKA',0.5,?, 'transferencia salida','x','','')",
                (M.a_unidades(0.5),))
    con.commit()
    con.close()
    conc = M.conciliar(rota2, M.ESCALA)
    no_cuadra = [g["tarea_id"] for g in conc["partida_doble"]["no_cuadran_u"]]
    check("el grupo sin pareja sale marcado", "fantasma" in no_cuadra, str(no_cuadra))

    print("\n14. La cadena detecta un importe cambiado")
    rota3 = carpeta / "rota3.db"
    shutil.copy2(copia, rota3)
    con = abrir(rota3)
    con.execute("UPDATE ledger SET kuki = kuki + 1 WHERE id = 2")
    con.commit()
    con.close()
    cad = M.verificar_cadena(rota3)
    check("la cadena se rompe al tocar un importe", not cad["ok"], str(cad["primera_rota"]))

    print("\n15. No se puede escribir sobre la propia base de origen")
    mismo = carpeta / "mismo.db"
    shutil.copy2(copia, mismo)
    check("detecta la misma ruta", M._misma_ruta(mismo, mismo))
    check("distingue rutas distintas", not M._misma_ruta(mismo, copia))

    print("\n16. La base de origen no se toca")
    h_antes = M.huella(copia)
    conc2 = M.conciliar(copia, M.ESCALA)
    h_despues = M.huella(copia)
    check("conciliar no escribe nada", h_antes["sha256"] == h_despues["sha256"])
    check("conciliar sigue dando lo mismo", conc2["global"]["supply_u"] == conc["global"]["supply_u"])


def main() -> int:
    print("=" * 70)
    print(" Pruebas de la migracion del dinero a unidades enteras")
    print("=" * 70)
    probar_conversion()
    with tempfile.TemporaryDirectory(prefix="kukia_entera_") as d:
        carpeta = Path(d)
        copia = probar_migracion(carpeta)
        probar_deteccion(copia, carpeta)
    print()
    if fallos:
        print(f"FALLOS: {len(fallos)}")
        for f in fallos:
            print(f"  - {f}")
        return 1
    print(f"Todo OK a las {time.strftime('%H:%M:%S')}. Nada de esto toco el ledger real.")
    return 0


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
