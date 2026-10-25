#!/usr/bin/env python3
"""Baja masiva de clientes de prueba: `activo=0` y NADA mas.

Para que el Core quede limpio sin tocar el historial. Lo que hace:

  - da de baja (logica) a los clientes que le digas: `ids`, `--nombre` o `--todos`;
  - NO borra asientos, ni tareas, ni saldos, ni sellos: solo cambia el campo
    `activo` de la tabla `clientes`;
  - con `activo=0` el token deja de valer al instante y el cliente no puede ni
    pedir trabajo ni gastar;
  - es reversible: la misma billetera se puede volver a dar de alta despues.

Comprueba, antes y despues, que el ledger no pierde ni un asiento y que el
suministro no cambia, y al final pasa `/ledger/verificar` para que se vea que la
cadena sigue cuadrando.

Uso:
  python tools/bajas_clientes.py                          # vista previa (no escribe)
  python tools/bajas_clientes.py --todos                  # vista previa de todos los activos
  python tools/bajas_clientes.py --todos --aplicar        # los da de baja
  python tools/bajas_clientes.py --nombre prueba --aplicar
  python tools/bajas_clientes.py --ids a1b2c3d4e5f6,c3d4e5f6a1b2 --aplicar
  python tools/bajas_clientes.py --forzar --todos --aplicar   # tambien con tareas en curso
  python tools/bajas_clientes.py --ver                    # listado, activos e inactivos

El token de administrador se lee de `coordinator/admin_token.txt` y no se imprime.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CORE = os.environ.get("KUKIA_CORE", "http://127.0.0.1:8300")


def _req(metodo, ruta, cuerpo=None, cabeceras=None, timeout=60):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    cab = {"Content-Type": "application/json"}
    cab.update(cabeceras or {})
    r = urllib.request.Request(CORE + ruta, data=datos, headers=cab, method=metodo)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {"detalle": e.read().decode("utf-8", "replace")[:300]}
    except Exception as e:
        return 0, {"detalle": f"{type(e).__name__}: {e}"}


def admin_token() -> str:
    f = RAIZ / "coordinator" / "admin_token.txt"
    if not f.is_file():
        raise SystemExit(f"no encuentro {f} (hace falta para administrar el Core)")
    return f.read_text(encoding="utf-8").strip()


def lista(adm) -> list[dict]:
    c, d = _req("GET", "/admin/clientes", None, adm)
    if c != 200:
        raise SystemExit(f"el Core no da la lista de clientes: {c} {d}")
    return d.get("clientes") or []


def metricas() -> tuple[int, float, float, bool]:
    """(asientos del ledger, supply, escrow, cadena_ok)."""
    c, d = _req("GET", "/ledger/integridad")
    asientos = int((d or {}).get("asientos_vivos") or 0) if c == 200 else -1
    cadena = bool((d or {}).get("cadena_ok")) if c == 200 else False
    c2, s = _req("GET", "/stats")
    supply = float((s or {}).get("supply_emitido") or 0.0) if c2 == 200 else -1.0
    c3, sa = _req("GET", "/saldo/KUKIA_ESCROW")
    escrow = float((sa or {}).get("saldo") or 0.0) if c3 == 200 else -1.0
    return asientos, supply, escrow, cadena


def tabla(filas: list[dict], titulo: str) -> None:
    if not filas:
        print(f"  {titulo}: (ninguno)")
        return
    print(f"  {titulo} ({len(filas)}):")
    print(f"    {'id':<14} {'nombre':<26} {'tareas':>6} {'saldo KIA':>14} {'asientos':>9} {'en curso':>8}")
    for f in filas:
        print(f"    {f['id']:<14} {(f.get('nombre') or '')[:26]:<26} "
              f"{int(f.get('tareas') or 0):>6} {float(f.get('saldo') or 0):>14.9f} "
              f"{int(f.get('asientos') or 0):>9} {int(f.get('en_curso') or 0):>8}")
        if f.get("motivo"):
            print(f"      -> se salta: {f['motivo']}")


def main() -> int:
    global CORE
    ap = argparse.ArgumentParser(description="Baja logica de clientes de prueba (no borra nada)")
    ap.add_argument("--core", default=CORE)
    ap.add_argument("--ids", default="", help="ids separados por comas")
    ap.add_argument("--nombre", default="", help="da de baja a quien contenga este texto en el nombre")
    ap.add_argument("--todos", action="store_true", help="todos los clientes activos")
    ap.add_argument("--aplicar", action="store_true", help="escribe de verdad (sin esto solo enseña)")
    ap.add_argument("--forzar", action="store_true", help="tambien a quien tiene tareas en curso")
    ap.add_argument("--ver", action="store_true", help="lista clientes y sale")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    CORE = args.core.rstrip("/")

    adm = {"X-Admin-Token": admin_token()}
    clientes = lista(adm)
    activos = [c for c in clientes if c.get("activo")]
    print(f"Core: {CORE} · clientes {len(clientes)} (activos {len(activos)})")

    if args.ver:
        for c in clientes:
            print(f"  {c['id']:<14} activo={int(c['activo'])}  {str(c.get('nombre') or '')[:40]:<40} "
                  f"creado {c.get('creado')}")
        return 0

    ids = [x.strip() for x in args.ids.split(",") if x.strip()]
    if not (ids or args.nombre.strip() or args.todos):
        # Sin selector: vista previa de los de prueba (nombre con "prueba").
        args.nombre = "prueba"
        print("(sin selector: vista previa de los que llevan 'prueba' en el nombre)")

    asientos0, supply0, escrow0, cadena0 = metricas()
    print(f"Antes: asientos {asientos0:,} · supply {supply0:,.6f} KIA · "
          f"escrow {escrow0:.9f} · cadena_ok {cadena0}".replace(",", "."))

    cuerpo = {"ids": ids, "nombre_contiene": args.nombre.strip(),
              "todos": bool(args.todos), "seco": not args.aplicar,
              "forzar": bool(args.forzar)}
    c, d = _req("POST", "/admin/clientes/baja", cuerpo, adm)
    if c != 200:
        print(f"el Core rechazo la baja: {c} {d}")
        return 1
    if args.json:
        print(json.dumps(d, indent=2, ensure_ascii=False))
    else:
        if d.get("seco"):
            tabla(d.get("bajarian") or [], "se darian de baja")
        else:
            tabla(d.get("bajadas") or [], "dados de baja")
        tabla(d.get("saltados") or [], "saltados")

    if d.get("seco"):
        print("\nNADA ESCRITO. Repite con --aplicar para darlos de baja de verdad.")
        return 0

    asientos1, supply1, escrow1, cadena1 = metricas()
    borrados = asientos0 - asientos1
    print(f"\nDespues: asientos {asientos1:,} · supply {supply1:,.6f} KIA · "
          f"escrow {escrow1:.9f} · cadena_ok {cadena1}".replace(",", "."))

    fallos = []
    # OJO: el suministro SUBE solo mientras los mineros trabajan (emision). La
    # baja no puede cambiarlo, asi que lo que se compara es el par que el Core
    # toma dentro de la propia operacion, y aparte se comprueba que fuera no
    # BAJE nunca (bajar = dinero destruido).
    sup_core0 = float(d.get("supply_antes") or 0.0)
    sup_core1 = float(d.get("supply_despues") or 0.0)
    if sup_core1 < sup_core0 - 1e-12:
        fallos.append(f"el suministro BAJO dentro de la baja: {sup_core0:.6f} -> {sup_core1:.6f}")
    if supply1 < supply0 - 1e-12:
        fallos.append(f"el suministro BAJO: {supply0:.6f} -> {supply1:.6f} (dinero destruido)")
    elif supply1 > supply0 + 1e-12:
        print(f"(el suministro subio {supply1 - supply0:.9f} KIA por la emision normal del "
              f"minero mientras corria la orden, no por la baja)")
    as_core0 = int(d.get("asientos_antes") or 0)
    as_core1 = int(d.get("asientos_despues") or 0)
    if as_core1 < as_core0:
        fallos.append(f"el Core dice que faltan asientos: {as_core0} -> {as_core1}")
    if borrados > 0:
        fallos.append(f"desaparecieron {borrados} asientos del ledger")
    elif borrados < 0:
        print(f"(el ledger gano {-borrados} asientos por la emision normal mientras "
              f"corria la orden; la baja no escribe en el ledger)")
    if not cadena1:
        fallos.append("la cadena del ledger ya no cuadra")
    if int(d.get("asientos_borrados") or 0) > 0:
        fallos.append("el propio Core dice que borro asientos")

    activos1 = [x for x in lista(adm) if x.get("activo")]
    print(f"\nClientes activos: {len(activos)} -> {len(activos1)}")
    if len(activos1) != len(activos) - len(d.get("bajadas") or []):
        fallos.append("el numero de activos no cuadra con las bajas")

    if fallos:
        print("\nFALLO:")
        for f_ in fallos:
            print(f"  - {f_}")
        return 1
    print("OK: bajas hechas, cero asientos borrados, suministro intacto y cadena buena.")
    print("(para volver a dar de alta uno: POST /admin/clientes con su misma direccion)")
    return 0


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
