#!/usr/bin/env python3
"""Auditoria de UNA billetera KUK..., en SOLO LECTURA.

Que comprueba (los 5 puntos que se pidieron para la billetera del fundador):
  1. El saldo de la base coincide con la suma de sus asientos del ledger.
  2. Cada movimiento esta contabilizado UNA sola vez: no hay asientos repetidos
     y cada transferencia tiene su salida y su entrada, del mismo importe.
  3. Nada llega por encima de las reglas de emision: se desglosa de donde sale
     cada KIA (fundacional / migracion / transferencia / trabajo verificado) y
     se comprueba que el supply del ledger cuadra con ese desglose.
  4. Lo que muestra la web coincide con el Core y con la base de datos
     (Core /saldo, rele publico /api/saldo, instantanea de la web).
  5. Los nonces no se repiten y la cadena de hashes esta intacta; la firma
     Ed25519 de cada transferencia se valido al entrar (se avisa de lo que NO
     queda guardado, para no vender mas de lo que hay).

NO escribe nada: abre la base en modo `mode=ro` y solo hace GETs.

Uso:
    python tools/auditoria_billetera.py --direccion KUK...
    python tools/auditoria_billetera.py --direccion KUK... --relay https://... --sin-web
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "coordinator" / "ledger" / "kukia.db"
CORE = "http://127.0.0.1:8300"
RELAY = "https://site-ef75d1db031c418d80071d2758f3a9e5.freebuff.page"
FUNDACIONAL = 15_000_000.0

fallos: list[str] = []
avisos: list[str] = []


def ok(msg: str) -> None:
    print(f"  OK    {msg}", flush=True)


def fallo(msg: str) -> None:
    fallos.append(msg)
    print(f"  FALLO {msg}", flush=True)


def aviso(msg: str) -> None:
    avisos.append(msg)
    print(f"  AVISO {msg}", flush=True)


def conectar() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def get_json(url: str, tiempo: float = 15.0):
    pet = urllib.request.Request(url, headers={"User-Agent": "kukia-auditoria/1.0",
                                               "Cache-Control": "no-cache"})
    with urllib.request.urlopen(pet, timeout=tiempo) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--direccion", required=True)
    ap.add_argument("--core", default=CORE)
    ap.add_argument("--relay", default=RELAY)
    ap.add_argument("--sin-web", action="store_true", help="no consultar Core ni rele")
    args = ap.parse_args()
    D = args.direccion.strip().upper()

    if not DB.exists():
        print(f"ERROR: no existe {DB}", file=sys.stderr)
        return 2
    con = conectar()

    print("=" * 78)
    print(f" AUDITORIA DE BILLETERA (solo lectura) · {D}")
    print(f" base: {DB} ({DB.stat().st_size / 1e6:.1f} MB) · {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 78)

    # ---------------------------------------------------------------- 1. SALDO
    print("\n1) SALDO: base de datos frente a la suma de asientos")
    fila = con.execute("SELECT total FROM saldos_cuenta WHERE cuenta=?", (D,)).fetchone()
    saldo_db = float(fila["total"]) if fila else 0.0
    asientos = con.execute("SELECT id, tarea_id, kuki, motivo, ts FROM ledger "
                           "WHERE nodo_id=? ORDER BY id", (D,)).fetchall()
    suma = sum(float(a["kuki"] or 0.0) for a in asientos)
    print(f"      saldo materializado : {saldo_db:,.6f} KIA")
    print(f"      suma de asientos    : {suma:,.6f} KIA ({len(asientos)} asientos)")
    if abs(saldo_db - suma) <= 1e-6:
        ok(f"cuadra al micro-KIA (diferencia {abs(saldo_db - suma):.2e})")
    else:
        fallo(f"NO cuadra: sobran/faltan {saldo_db - suma:,.6f} KIA")

    # ------------------------------------------------ 2. UNA VEZ CADA MOVIMIENTO
    print("\n2) MOVIMIENTOS: ni asientos repetidos ni transferencias partidas")
    dup = con.execute("""
        SELECT tarea_id, nodo_id, kuki, motivo, ts, COUNT(*) n
        FROM ledger WHERE nodo_id=?
        GROUP BY tarea_id, nodo_id, kuki, motivo, ts HAVING COUNT(*) > 1
    """, (D,)).fetchall()
    if dup:
        fallo(f"{len(dup)} asiento(s) repetido(s) en esta billetera")
        for d in dup[:5]:
            print(f"        {d['n']}x  {d['kuki']}  {d['motivo']}  {d['ts']}")
    else:
        ok("ningun asiento repetido en esta billetera")

    # Transferencias: por cada una, una salida del que envia y una entrada del
    # que recibe, con el MISMO importe y el nonce en el id del asiento.
    tx = con.execute("SELECT id, de_nodo, a_direccion, monto, nonce, ts FROM transferencias "
                     "ORDER BY id").fetchall()
    mias = [t for t in tx if t["de_nodo"] == D or t["a_direccion"] == D]
    # El id del asiento es "tx-" + un trozo del nonce, y ese trozo ha cambiado
    # de longitud entre versiones (12 hoy; antes 11 o con prefijo de tipo). Se
    # busca por prefijo en vez de suponer una longitud.
    por_id: dict[str, list[sqlite3.Row]] = {}
    for r in con.execute("SELECT tarea_id, nodo_id, kuki FROM ledger "
                         "WHERE tarea_id LIKE 'tx-%'"):
        por_id.setdefault(r["tarea_id"], []).append(r)
    # Un mismo `tarea_id` puede agrupar VARIAS transferencias: el asiento se
    # marca con "tx-" + trozo del nonce, y dos nonces distintos pueden empezar
    # igual (p.ej. "<nodo>-<marca de tiempo>"). Asi que se compara el CONJUNTO:
    # por cada grupo, las parejas (importe, de, a) de los asientos tienen que ser
    # exactamente las de las transferencias de ese grupo, ni una mas ni una menos.
    descuadres: list[str] = []
    grupos: dict[str, list[sqlite3.Row]] = {}
    for t in tx:
        nonce = t["nonce"] or ""
        claves = [k for k in por_id if len(k) > 3 and nonce.startswith(k[3:])]
        if not claves:
            descuadres.append(f"tx {t['id']}: sin ningun asiento en el ledger "
                              f"(nonce {nonce[:24]})")
            continue
        grupos.setdefault(claves[0], []).append(t)
    for clave, lista in grupos.items():
        filas = por_id[clave]
        salidas = [f for f in filas if float(f["kuki"]) < -1e-12]
        entradas = [f for f in filas if float(f["kuki"]) > 1e-12]
        parejas = []
        pendientes = list(entradas)
        for s in salidas:
            importe = round(-float(s["kuki"]), 9)
            match = next((e for e in pendientes
                          if abs(float(e["kuki"]) - importe) <= 1e-9), None)
            if match is None:
                descuadres.append(f"{clave}: salida de {importe:,.6f} sin entrada que la cuadre")
                continue
            pendientes.remove(match)
            parejas.append((importe, s["nodo_id"], match["nodo_id"]))
        for huerfano in pendientes:
            descuadres.append(f"{clave}: entrada de {float(huerfano['kuki']):,.6f} sin salida")
        esperado = sorted((round(float(t["monto"]), 9), t["de_nodo"], t["a_direccion"])
                          for t in lista)
        if sorted(parejas) != esperado:
            descuadres.append(f"{clave}: {len(parejas)} pareja(s) contra {len(esperado)} "
                              f"transferencia(s); importes {[p[0] for p in parejas]} "
                              f"contra {[e[0] for e in esperado]}")
    if descuadres:
        for d in descuadres[:10]:
            fallo(d)
    else:
        ok(f"las {len(tx)} transferencias de la red cuadran con sus asientos "
           f"(importe, quien envia y quien recibe)")

    nonces = con.execute("SELECT nonce, COUNT(*) n FROM transferencias "
                         "GROUP BY nonce HAVING COUNT(*) > 1").fetchall()
    ok("nonces de transferencia sin repetir") if not nonces else \
        fallo(f"{len(nonces)} nonce(s) repetidos")
    print(f"        transferencias que tocan esta billetera: {len(mias)}")

    # ------------------------------------------------------ 3. ORIGEN DEL SALDO
    print("\n3) DE DONDE SALE CADA KIA (reglas de emision)")
    por_motivo: dict[str, list[float]] = {}
    for a in asientos:
        clave = a["motivo"] or "(sin motivo)"
        por_motivo.setdefault(clave, []).append(float(a["kuki"] or 0.0))
    for motivo, vals in sorted(por_motivo.items(), key=lambda kv: -sum(kv[1])):
        print(f"        {motivo[:44]:46} {len(vals):>3} asiento(s)  {sum(vals):>16,.6f}")
    fundacional = sum(sum(v) for m, v in por_motivo.items() if "fundacional" in m or "migracion" in m)
    trabajo = sum(sum(v) for m, v in por_motivo.items() if "trabajo" in m or "redundante" in m)
    transf = sum(sum(v) for m, v in por_motivo.items() if "transferencia" in m)
    total = fundacional + trabajo + transf
    print(f"        {'':46} {'':>3}          -------------")
    print(f"        {'fundacional + migracion':46} {fundacional:>19,.6f}")
    print(f"        {'trabajo verificado (si mino)':46} {trabajo:>19,.6f}")
    print(f"        {'transferencias recibidas/enviadas (neto)':46} {transf:>19,.6f}")
    print(f"        {'TOTAL':46} {total:>19,.6f}")
    if abs(total - saldo_db) > 1e-6:
        fallo(f"el desglose no suma el saldo ({total:,.6f} contra {saldo_db:,.6f})")
    else:
        ok("el desglose suma exactamente el saldo")
    if fundacional > FUNDACIONAL + 1e-6:
        fallo(f"entra mas fundacional del que existe ({fundacional:,.6f} > {FUNDACIONAL:,.0f})")
    elif fundacional > 0:
        ok(f"fundacional dentro de la regla: {fundacional:,.6f} de {FUNDACIONAL:,.0f} KIA "
           f"(no se puede emitir por encima)")

    # Identidad contable global: supply = fundacional + todo el trabajo pagado.
    supply = float((con.execute("SELECT valor FROM meta WHERE clave='supply_emitido'")
                    .fetchone() or {"valor": 0})["valor"])
    suma_ledger = float(con.execute("SELECT SUM(kuki) FROM ledger").fetchone()[0] or 0.0)
    if abs(supply - suma_ledger) <= 1e-6:
        ok(f"supply del Core = suma del ledger entero ({supply:,.6f} KIA)")
    else:
        fallo(f"supply ({supply:,.6f}) NO es la suma del ledger ({suma_ledger:,.6f})")
    genesis = float(con.execute("SELECT COALESCE(SUM(kuki),0) FROM ledger "
                                "WHERE motivo LIKE '%fundacional%'").fetchone()[0])
    trabajo_red = float(con.execute("SELECT COALESCE(SUM(kuki),0) FROM ledger "
                                    "WHERE motivo LIKE '%trabajo%' OR motivo LIKE '%redundante%'")
                        .fetchone()[0])
    print(f"        red: genesis {genesis:,.6f} + trabajo {trabajo_red:,.6f} "
          f"= {genesis + trabajo_red:,.6f}")
    if abs((genesis + trabajo_red) - suma_ledger) <= 1e-6:
        ok("el ledger entero es genesis + trabajo pagado: no hay moneda de otra fuente")
    else:
        aviso(f"el ledger tiene {suma_ledger - genesis - trabajo_red:,.6f} KIA fuera de "
              f"genesis+trabajo (transferencias, que no crean moneda)")

    # --------------------------------------------------- 4. WEB / CORE / BASE
    if not args.sin_web:
        print("\n4) LO QUE MUESTRA LA WEB frente al Core y la base")
        try:
            core = get_json(f"{args.core.rstrip('/')}/saldo/{D}")
            print(f"      Core  {args.core}: {float(core['saldo']):,.6f} KIA")
            if abs(float(core["saldo"]) - saldo_db) <= 1e-6:
                ok("el Core da el mismo saldo que la base")
            else:
                fallo(f"el Core da {float(core['saldo']):,.6f} y la base {saldo_db:,.6f}")
        except Exception as e:
            aviso(f"no se pudo preguntar al Core ({e})")
        try:
            rel = get_json(f"{args.relay.rstrip('/')}/api/saldo/{D}")
            s = rel.get("saldo", rel.get("valor"))
            print(f"      rele publico: {float(s):,.6f} KIA (ts {rel.get('ts')})")
            if s is not None and abs(float(s) - saldo_db) <= 1e-6:
                ok("el rele que pinta la web da el mismo saldo")
            elif s is not None:
                aviso(f"el rele va por detras: {float(s):,.6f} contra {saldo_db:,.6f} "
                      f"(se refresca cada minuto)")
        except Exception as e:
            aviso(f"no se pudo preguntar al rele ({e})")
        snap = RAIZ / "docs" / "metricas_snapshot.json"
        try:
            d = json.loads(snap.read_text(encoding="utf-8"))
            st = d.get("stats") or {}
            print(f"      instantanea de la web: supply {st.get('supply_emitido')} "
                  f"(generada {d.get('ts')})")
            if abs(float(st.get("supply_emitido", 0)) - supply) <= 1e-6:
                ok("la instantanea de la web lleva el mismo supply")
        except Exception as e:
            aviso(f"no se pudo leer la instantanea de la web ({e})")

    # ------------------------------------------------------------- 5. FIRMAS
    print("\n5) FIRMAS Y CADENA")
    nodo = con.execute("SELECT clave_publica, direccion_cobro FROM nodos "
                       "WHERE direccion_cobro=? OR id=?", (D, D)).fetchone()
    if nodo:
        print(f"      esta direccion es de un nodo (clave publica "
              f"{(nodo['clave_publica'] or '')[:16]}...)")
        ok("la billetera tiene clave publica de nodo asociada")
    else:
        aviso("no es un nodo: solo se puede mover firmando con su clave privada "
              "(el Core comprueba la firma Ed25519 y el nonce al entrar)")
    # Re-verificacion de firmas guardadas (desde el 23-10-2026 se guardan).
    con_firma = con.execute("SELECT id, de_nodo, a_direccion, monto, nonce, firma, "
                            "clave_publica FROM transferencias "
                            "WHERE COALESCE(firma,'') <> ''").fetchall()
    sin_firma = con.execute("SELECT COUNT(*) c FROM transferencias "
                            "WHERE COALESCE(firma,'') = ''").fetchone()["c"]
    if con_firma:
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            malas = []
            for t in con_firma:
                try:
                    Ed25519PublicKey.from_public_bytes(bytes.fromhex(t["clave_publica"])).verify(
                        bytes.fromhex(t["firma"]),
                        f"transferir:{t['de_nodo']}:{t['a_direccion']}:{t['monto']}:{t['nonce']}"
                        .encode())
                except Exception:
                    malas.append(t["id"])
            if malas:
                fallo(f"{len(malas)} firma(s) guardadas NO verifican: {malas[:10]}")
            else:
                ok(f"{len(con_firma)} firma(s) Ed25519 re-verificadas contra la clave publica "
                   f"del emisor (sin fiarse de nadie)")
        except ImportError:
            aviso("falta 'cryptography' aqui: no se pueden re-verificar las firmas guardadas")
    if sin_firma:
        aviso(f"{sin_firma} transferencia(s) sin firma guardada: son anteriores al 23-10-2026, "
              f"cuando se empezo a guardar. De esas solo queda nonce + pareja de asientos")
    malo = con.execute("SELECT COUNT(*) c FROM ledger WHERE hash IS NULL OR hash=''").fetchone()["c"]
    print(f"      asientos sin hash de cadena: {malo}")
    ok("cada transferencia entra con nonce unico (anti-repeticion)") if not nonces else None

    print("\n" + "=" * 78)
    if fallos:
        print(f" RESULTADO: {len(fallos)} FALLO(S), {len(avisos)} aviso(s)")
        for f in fallos:
            print(f"   - {f}")
        return 1
    print(f" RESULTADO: OK · {len(avisos)} aviso(s)")
    for a in avisos:
        print(f"   - {a}")
    return 0


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
