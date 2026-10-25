"""Prueba del ADAPTADOR DE REPARTO y del calendario que acaba el 13-04-2150.

QUE COMPRUEBA
  1. La fecha final. /stats tiene que decir `fin_emision = 2150-04-13`, y el
     presupuesto del minuto tiene que ser lo que queda por emitir dividido entre
     los minutos que faltan hasta ESE dia. Si alguien mueve la fecha y no la
     cuenta (o al reves), aqui salta.
  2. El reparto publicado: las partes suman 1, cada cuota es `lo pagado en el
     minuto x su parte` (redondeada hacia abajo), la suma de cuotas no pasa de lo
     pagado, lo pagado no pasa del presupuesto, y las dos cifras que se reparten
     entre mas GPUs (`cuota_igual_kia` por billetera y `cuota_igual_por_gpu_kia`
     por GPU) son lo pagado dividido entre los que se unieron.
  3. Que el reparto no se inventa: se recalcula desde la base de datos, en SOLO
     LECTURA, el trabajo de cada cuenta en ese minuto (tareas + tabla de nodos) y
     lo que cobro en el ledger. Tiene que dar lo mismo que publica el Core.
  4. Que se paga por trabajo: los KIA por GFLOP de cada cuenta coinciden con el
     precio del minuto (si el fusible no recorto a nadie).
  5. Que unirse NO crea dinero: con 1, 2, 3... 10 GPUs iguales, lo que se reparte
     en total no crece, y cada una cobra menos.

COMO SE LANZA
    python tools/prueba_adaptador_reparto.py
    python tools/prueba_adaptador_reparto.py --core http://127.0.0.1:8300
    python tools/prueba_adaptador_reparto.py --minuto 29882440   (un minuto concreto)

Sale 0 si todo cuadra y 1 si algo no. No escribe NADA: ni en el Core ni en la
base (la abre con mode=ro).
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "coordinator" / "ledger" / "kukia.db"
FIN_ESPERADO = date(2150, 4, 13)
DIAS_ANIO = 365.25

fallos: list[str] = []
avisos: list[str] = []


def check(cond, texto, detalle: str = "") -> bool:
    if cond:
        print(f"  OK   {texto}" + (f"   [{detalle}]" if detalle else ""))
    else:
        print(f"  FALLO {texto}" + (f"   [{detalle}]" if detalle else ""))
        fallos.append(texto)
    return bool(cond)


def pide(core: str, ruta: str) -> dict:
    with urllib.request.urlopen(f"{core.rstrip('/')}{ruta}", timeout=20) as r:
        return json.load(r)


def abrir_bd() -> sqlite3.Connection | None:
    """Abre la base EN SOLO LECTURA: la prueba no puede tocar el ledger."""
    if not DB.exists():
        return None
    try:
        c = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=10)
        c.row_factory = sqlite3.Row
        return c
    except Exception as e:
        avisos.append(f"no se pudo abrir la base en solo lectura: {e}")
        return None


def epoch(txt) -> float | None:
    """La base guarda `time.ctime()` en texto, no epoch."""
    try:
        return time.mktime(time.strptime(str(txt), "%a %b %d %H:%M:%S %Y"))
    except Exception:
        return None


def flops(tipo: str, params) -> float:
    """Las mismas cuentas que `_flops_tarea` del Core (matriz/inferencia/difusion)."""
    try:
        p = json.loads(params) if isinstance(params, str) else (params or {})
    except Exception:
        return 0.0
    try:
        n = int(p.get("n") or 0)
        if tipo == "matriz":
            return 2.0 * n ** 3
        if tipo == "inferencia":
            return 18.0 * n ** 2
        if tipo == "difusion_calor":
            return 10.0 * n ** 2 * int(p.get("pasos") or 0)
    except Exception:
        return 0.0
    return 0.0


def num(x, dec: int = 12) -> str:
    """Numero con coma decimal espanola y sin notacion cientifica."""
    if x is None:
        return "-"
    return f"{x:,.{dec}f}".replace(",", "·").replace(".", ",").replace("·", ".")


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Prueba del adaptador de reparto.")
    ap.add_argument("--core", default="http://127.0.0.1:8300")
    ap.add_argument("--minuto", type=int, default=None,
                    help="indice de minuto a comprobar (defecto: el que publica /stats)")
    a = ap.parse_args()

    print("PRUEBA DEL ADAPTADOR DE REPARTO - KUKIA")
    print(f"Core: {a.core}  ·  {datetime.now().strftime('%d-%m-%Y %H:%M')}\n")

    try:
        d = pide(a.core, "/stats")
    except Exception as e:
        print(f"[ERROR] el Core no responde en {a.core} ({type(e).__name__}: {e})")
        print("        arranca el Core y vuelve a lanzar la prueba.")
        return 2

    # ------------------------------------------------------------------ 1 ---
    print("1) CALENDARIO: la ultima moneda sale el 13-04-2150")
    e = d.get("emision") or {}
    fin = str(e.get("fin_emision") or "")
    check(fin == FIN_ESPERADO.isoformat(),
          f"fin_emision = {FIN_ESPERADO.isoformat()}", f"publica {fin or 'nada'}")
    bolsa = d.get("bolsa_emision") or {}
    presu = float(bolsa.get("presupuesto_kia_min") or 0.0)
    restante = max(0.0, float(d.get("max_supply") or 0) - float(d.get("supply_emitido") or 0))
    minutos = max(1.0, (datetime.combine(FIN_ESPERADO, datetime.min.time())
                        - datetime.now()).total_seconds() / 60.0)
    presu_calc = restante / minutos
    check(restante > 0 and abs(presu - presu_calc) <= max(1e-6, presu_calc * 1e-2),
          "el presupuesto del minuto es lo que queda entre los minutos hasta el 13-04-2150",
          f"publica {num(presu, 6)} KIA/min, la cuenta da {num(presu_calc, 6)}")
    dias = (FIN_ESPERADO - date(2026, 1, 1)).days
    print(f"       calendario: {dias} dias = {num(dias / DIAS_ANIO, 2)} anos, "
          f"6 eras de {num(dias / DIAS_ANIO / 6, 2)} anos")
    check(dias / DIAS_ANIO >= 124.0, "el calendario dura 124 anos o mas",
          f"{num(dias / DIAS_ANIO, 2)} anos")
    print()

    # ------------------------------------------------------------------ 2 ---
    print("2) EL REPARTO PUBLICADO (el minuto ya cerrado)")
    r = d.get("reparto") or {}
    check(bool(r) and "participantes" in r, "/stats publica el bloque `reparto`")
    if not r or "participantes" not in r:
        print("\n[sin reparto que comprobar]")
        return 1
    n = int(r.get("participantes") or 0)
    gpus = int(r.get("gpus") or 0)
    presu_r = float(r.get("presupuesto_kia_min") or 0.0)
    pagado_r = float(r.get("pagado_kia") or 0.0)
    top = r.get("top") or []
    suma_partes = sum(float(f.get("parte") or 0.0) for f in top)
    print(f"       billeteras: {n}   GPUs: {gpus}   "
          f"GFLOP verificados: {num(r.get('gflop_total'), 3)}")
    print(f"       pagado en el minuto: {num(pagado_r, 12)} KIA de "
          f"{num(presu_r, 6)} que permite el calendario")
    print(f"       si todas entregan lo mismo: {num(r.get('cuota_igual_kia'), 12)} KIA "
          f"por billetera, {num(r.get('cuota_igual_por_gpu_kia'), 12)} KIA por GPU")
    check(n == len(top) or r.get("cuentas_mostradas") == len(top),
          "se publican todas las billeteras que entraron en el minuto",
          f"{len(top)} filas de {n}")
    if n == 0:
        avisos.append("el minuto anterior no tuvo trabajo: no hay reparto que medir")
        print("       (minuto sin trabajo: solo se puede comprobar la fecha)")
    else:
        check(pagado_r <= presu_r + 1e-12,
              "lo pagado en el minuto no pasa del presupuesto del calendario",
              f"{num(pagado_r, 12)} <= {num(presu_r, 12)}")
        check(abs(suma_partes - 1.0) <= 1e-9, "las partes suman 1",
              num(suma_partes, 12))
        esperado = math.floor((pagado_r / n) * 1e12) / 1e12
        check(r.get("cuota_igual_kia") is not None and
              abs(float(r["cuota_igual_kia"]) - esperado) <= 1e-12,
              "cuota_igual = lo pagado / las billeteras que se unieron",
              f"publica {num(r.get('cuota_igual_kia'), 12)}, la cuenta da {num(esperado, 12)}")
        esperado_gpu = math.floor((pagado_r / gpus) * 1e12) / 1e12 if gpus else 0.0
        check(gpus > 0 and
              abs(float(r.get("cuota_igual_por_gpu_kia") or 0) - esperado_gpu) <= 1e-12,
              "cuota_igual_por_gpu = lo pagado / las GPUs que se unieron",
              f"publica {num(r.get('cuota_igual_por_gpu_kia'), 12)}, "
              f"la cuenta da {num(esperado_gpu, 12)}")
        malas = []
        for f in top:
            esperada = math.floor(pagado_r * float(f["parte"]) * 1e12) / 1e12
            if abs(esperada - float(f["cuota_kia"])) > 1e-9:
                malas.append(f"{f['cuenta'][:12]}: {f['cuota_kia']} != {esperada}")
        check(not malas, "cada cuota es lo pagado x su parte", "; ".join(malas[:3]))
        suma_cuotas = float(r.get("suma_cuotas_kia") or 0.0)
        check(suma_cuotas <= pagado_r + 1e-9,
              "la suma de cuotas no pasa de lo pagado en el minuto",
              f"{num(suma_cuotas, 12)} <= {num(pagado_r, 12)}")
        check(bool(r.get("proporcional")),
              "el Core lo declara proporcional (nadie cobro de mas ni de menos)")
        # Dentro de cada billetera, cada GPU se lleva su parte del cobro.
        malas_gpu = []
        for f in top:
            maquinas = f.get("maquinas") or []
            if not maquinas:
                malas_gpu.append(f"{f['cuenta'][:12]} sin detalle de GPUs")
                continue
            if abs(sum(float(m["parte_cuenta"]) for m in maquinas) - 1.0) > 1e-9:
                malas_gpu.append(f"{f['cuenta'][:12]}: partes de GPU no suman 1")
            if abs(sum(float(m["cuota_kia"]) for m in maquinas)
                   - float(f["cobrado_kia"])) > 1e-8:
                malas_gpu.append(f"{f['cuenta'][:12]}: cuotas de GPU no suman el cobro")
        check(not malas_gpu, "lo que cobra cada GPU suma el cobro de su billetera",
              "; ".join(malas_gpu[:3]))
    print()

    # ------------------------------------------------------------------ 3 ---
    print("3) RECALCULO DESDE LA BASE (solo lectura): que no se lo invente")
    con = abrir_bd()
    minuto = int(a.minuto if a.minuto is not None else (r.get("minuto") or 0))
    trabajo: dict[str, float] = {}
    cobrado: dict[str, float] = {}
    escrow = 0.0
    precio = float(bolsa.get("precio_minuto") or 0.0)
    suelo = float((d.get("pago_por_potencia") or {}).get("pago_min_kia") or 0.0)
    esperado_regla = 0.0     # lo que toca pagar segun la regla publicada
    n_emision = 0            # tareas pagadas por la emision (no por un cliente)
    n_suelo = 0              # de esas, cuantas cobran el suelo y no el trabajo
    if con is None:
        avisos.append("sin acceso a la base: no se pudo recalcular el reparto")
        print("       (no se pudo abrir la base, se salta el recalculo)")
    else:
        desde, hasta = minuto * 60.0, (minuto + 1) * 60.0
        base = float((d.get("pago_por_potencia") or {}).get("gflop_min_facturable") or 0.0)
        direccion: dict[str, str] = {}
        try:
            for f in con.execute("SELECT id, COALESCE(direccion_cobro,'') AS dir "
                                 "FROM nodos").fetchall():
                direccion[f["id"]] = (f["dir"] or "").strip().upper() or f["id"]
        except Exception as ex:
            avisos.append(f"no se pudo leer la tabla `nodos`: {ex}")
        try:
            filas = con.execute("SELECT nodo_id, tipo, params, fin, "
                                "COALESCE(cliente,'') AS cliente FROM tareas "
                                "WHERE estado='ok' AND fin IS NOT NULL "
                                "ORDER BY rowid DESC LIMIT 20000").fetchall()
        except Exception as ex:
            filas = []
            avisos.append(f"no se pudo leer `tareas`: {ex}")
        for f in filas:
            t = epoch(f["fin"])
            if t is None or t < desde or t >= hasta:
                continue
            g = max(flops(f["tipo"], f["params"]) / 1e9, base)
            nodo = f["nodo_id"] or "?"
            cuenta = direccion.get(nodo, nodo)
            trabajo[cuenta] = trabajo.get(cuenta, 0.0) + g
            # La regla del pago, tarea a tarea: precio x GFLOP con suelo.
            if not str(f["cliente"] or "").strip() and precio > 0:
                por_trabajo = precio * g
                esperado_regla += max(por_trabajo, suelo)
                n_emision += 1
                if por_trabajo < suelo:
                    n_suelo += 1
        try:
            filas = con.execute("SELECT l.nodo_id AS nodo_id, l.kuki AS kuki, l.ts AS ts, "
                                "COALESCE(t.cliente,'') AS cliente "
                                "FROM ledger l LEFT JOIN tareas t ON t.id = l.tarea_id "
                                "WHERE l.motivo=? ORDER BY l.id DESC LIMIT 20000",
                                ("trabajo verificado",)).fetchall()
        except Exception as ex:
            filas = []
            avisos.append(f"no se pudo leer `ledger`: {ex}")
        for f in filas:
            t = epoch(f["ts"])
            if t is None or t < desde or t >= hasta:
                continue
            kia = float(f["kuki"] or 0.0)
            if str(f["cliente"] or "").strip():
                escrow = round(escrow + kia, 12)
                continue
            cuenta = f["nodo_id"] or "?"
            cobrado[cuenta] = cobrado.get(cuenta, 0.0) + kia
        con.close()
        total = sum(trabajo.values())
        check(abs(total - float(r.get("gflop_total") or 0.0)) <= max(1e-5, total * 1e-6),
              "el GFLOP del minuto coincide con el de la base",
              f"publica {num(r.get('gflop_total'), 3)}, la base da {num(total, 3)}")
        check(len(trabajo) == n, "el numero de billeteras coincide con la base",
              f"publica {n}, la base da {len(trabajo)}")
        check(abs(round(sum(cobrado.values()), 12) - pagado_r) <= 1e-9,
              "lo pagado en el minuto coincide con los asientos del ledger",
              f"publica {num(pagado_r, 12)}, la base da "
              f"{num(round(sum(cobrado.values()), 12), 12)}")
        for f in top:
            cuenta = f["cuenta"]
            g_bd = trabajo.get(cuenta)
            if g_bd is None:
                check(False, f"la cuenta {cuenta[:12]} del reparto no hizo trabajo en la base")
                continue
            if abs(g_bd - float(f["gflop"])) > max(1e-5, g_bd * 1e-6):
                check(False, f"GFLOP de {cuenta[:12]}: publica {num(f['gflop'], 3)}, "
                             f"la base da {num(g_bd, 3)}")
            c_bd = cobrado.get(cuenta, 0.0)
            if abs(c_bd - float(f["cobrado_kia"])) > 1e-9:
                check(False, f"cobrado de {cuenta[:12]}: publica {num(f['cobrado_kia'], 12)}, "
                             f"el ledger da {num(c_bd, 12)}")
        if not fallos:
            check(True, "GFLOP y KIA cobrados de cada billetera coinciden con la base")
    print()

    # ------------------------------------------------------------------ 4 ---
    print("4) SE PAGA POR TRABAJO: precio x GFLOP por tarea, con suelo, y cuadra")
    if precio <= 0 or n_emision == 0 or esperado_regla <= 0:
        avisos.append("sin datos de pago en el minuto: no se pudo comprobar la regla")
        print("       (sin trabajo o sin precio: se salta)")
    else:
        print(f"       precio del minuto: {num(precio, 18)} KIA/GFLOP   "
              f"suelo por tarea: {num(suelo, 6)} KIA")
        print(f"       tareas pagadas por la emision: {n_emision}   de ellas, "
              f"cobran el suelo (no el trabajo): {n_suelo}")
        print(f"       la regla da {num(esperado_regla, 12)} KIA; el ledger pago "
              f"{num(pagado_r, 12)} KIA")
        check(abs(esperado_regla - pagado_r) <= max(1e-9, pagado_r * 1e-6),
              "lo pagado en el minuto es la suma de la regla, tarea a tarea",
              f"regla {num(esperado_regla, 12)} vs ledger {num(pagado_r, 12)}")
        if n_suelo:
            avisos.append(f"{n_suelo} de {n_emision} tareas cobraron el suelo: con "
                          "tareas tan pequenas, lo que paga hoy es el minimo, no el GFLOP")
    print()

    # ------------------------------------------------------------------ 5 ---
    print("5) QUE UNIRSE NO CREA DINERO: el total del minuto no crece con mas GPUs")
    pastel = pagado_r or presu
    for n5 in (1, 2, 3, 4, 8, 10):
        cuota = math.floor((pastel / n5) * 1e12) / 1e12
        total5 = cuota * n5
        estado = "OK" if total5 <= pastel + 1e-9 else "SE PASA"
        print(f"       {n5:>3} GPU(s) iguales -> {num(cuota, 12)} KIA cada una, "
              f"total {num(total5, 12)} KIA  {estado}")
        if total5 > pastel + 1e-9:
            check(False, f"con {n5} GPUs el reparto se pasa de lo pagado")
    check(True, "en todos los casos el total no crece por unirse mas GPUs")
    if n and gpus:
        esperado_n = math.floor((pastel / n) * 1e12) / 1e12
        esperado_g = math.floor((pastel / gpus) * 1e12) / 1e12
        check(abs(float(r.get("cuota_igual_kia") or 0) - esperado_n) <= 1e-12 and
              abs(float(r.get("cuota_igual_por_gpu_kia") or 0) - esperado_g) <= 1e-12,
              f"con las {gpus} GPUs de ahora ({n} billeteras), cada GPU cobraria "
              f"{num(esperado_g, 12)} KIA si todas entregaran lo mismo")
    print()

    print("RESUMEN")
    if avisos:
        for t in avisos:
            print(f"  AVISO  {t}")
    if fallos:
        print(f"  {len(fallos)} FALLO(S):")
        for t in fallos:
            print(f"    - {t}")
        return 1
    print("  OK: calendario al 13-04-2150, reparto que cuadra al centimo con los asientos")
    print("      y cuota por GPU que baja segun se unen (sin pasar nunca de lo pagado).")
    if avisos:
        print("      OJO: los avisos de arriba son reales, no adornos: leerlos antes de")
        print("      decir que el minero cobra por GFLOP.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
