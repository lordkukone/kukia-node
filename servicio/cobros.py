#!/usr/bin/env python3
"""Anota en KUKIA un trabajo ya cobrado, en euros.

POR QUE EXISTE
  El ledger lleva KIA; el servicio de transcripcion se cobra en EUROS. Convertir
  el euro a KIA y abonarlo seria emitir moneda por vender un servicio, y la
  emision tiene un calendario que no se toca. Lo que se hace aqui es lo otro:
  dejar ESCRITO en el ledger que alguien pago dinero real, cuanto y por que
  trabajo, con un asiento de 0 KIA cuyo hash queda en la cadena y bajo los
  sellos. Ese es el dato que convierte "KIA vale 0" en "KIA vale lo que alguien
  pago".

  El Core tiene que estar arrancado (127.0.0.1:8300). El token de admin se lee
  de `coordinator/admin_token.txt` o de KUKIA_ADMIN_TOKEN; nunca se imprime.

USO
  # anotar el trabajo a partir del .json que deja transcribir.py
  python servicio/cobros.py --trabajo servicio/entregas/20261025/charla.json \
      --cliente "Maria G."

  # si se cobro otro importe (descuento, prueba gratis, precio pactado)
  python servicio/cobros.py --trabajo ... --cliente "Maria G." --importe 4.50

  # cuanto se ha cobrado hasta hoy (lo mismo que publica /ingresos)
  python servicio/cobros.py --resumen

  # lo que queda anotado en el Core, con nombres (necesita el token)
  python servicio/cobros.py --lista

El mismo trabajo no se puede anotar dos veces: el Core lo rechaza con 409. Un
euro contado dos veces dejaria de ser prueba de nada.
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


def archivo_token() -> Path:
    """Donde esta el token de admin. Se mira en CADA llamada, no al importar.

    Al importar solo, KUKIA_ADMIN_TOKEN cambiado despues (una prueba, otro
    Core) seguia apuntando al fichero viejo y el cobro se rechazaba con 403.
    """
    return Path(os.environ.get("KUKIA_ADMIN_TOKEN")
                or (RAIZ / "coordinator" / "admin_token.txt"))


def token_admin() -> str:
    """Token del Core (mismo fichero que usa para administrarse). Vacio si no hay."""
    try:
        f = archivo_token()
        if f.is_file():
            return f.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return os.environ.get("KUKIA_ADMIN_TOKEN_TEXTO", "").strip()


def _llamar(metodo: str, ruta: str, cuerpo: dict | None = None,
            core: str = CORE, token: str = "", tiempo: float = 20.0) -> tuple:
    """(codigo, json). Nunca lanza: el cobro no puede romper el trabajo."""
    datos = None if cuerpo is None else json.dumps(cuerpo).encode("utf-8")
    cabeceras = {"Content-Type": "application/json"}
    if token:
        cabeceras["X-Admin-Token"] = token
    pet = urllib.request.Request(f"{core.rstrip('/')}{ruta}", data=datos,
                                 headers=cabeceras, method=metodo)
    try:
        with urllib.request.urlopen(pet, timeout=tiempo) as r:
            return r.status, json.loads(r.read(200_000).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read(200_000).decode("utf-8", "replace"))
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"detail": f"{type(e).__name__}: {e}"}


def leer_trabajo(ruta: Path) -> dict:
    """Datos del .json que deja transcribir.py (fichero, minutos, precio)."""
    d = json.loads(Path(ruta).read_text(encoding="utf-8"))
    if not isinstance(d, dict):
        raise ValueError("el .json del trabajo no tiene la forma esperada")
    return d


def anotar(trabajo: str, cliente: str, minutos: float, precio_eur: float,
           servicio: str = "transcripcion", detalle: dict | None = None,
           core: str = CORE) -> dict:
    """Manda el ingreso al Core. Devuelve {ok, estado, mensaje, id}."""
    cuerpo = {"servicio": servicio, "cliente": cliente, "trabajo": trabajo,
              "minutos": round(float(minutos), 2),
              "precio_eur": round(float(precio_eur), 2),
              "detalle": detalle or {}}
    codigo, datos = _llamar("POST", "/admin/ingresos", cuerpo,
                            core=core, token=token_admin())
    if codigo == 200:
        return {"ok": True, "estado": codigo,
                "mensaje": (f"ingreso #{datos.get('id')} anotado: "
                            f"{datos.get('precio_eur')} EUR, asiento "
                            f"{(datos.get('asiento_hash') or '')[:12]}…"),
                "datos": datos, "id": datos.get("id")}
    if codigo == 409:
        return {"ok": False, "estado": codigo, "ya_estaba": True,
                "mensaje": f"ya estaba anotado: {datos.get('detail', '')}"}
    if codigo == 403:
        return {"ok": False, "estado": codigo,
                "mensaje": "el Core no acepta el token de admin "
                           "(¿has arrancado el Core con otro fichero?)"}
    if codigo == 0:
        return {"ok": False, "estado": 0,
                "mensaje": f"el Core no responde en {core}: "
                           f"{datos.get('detail', '')}"}
    if codigo == 404:
        # El Core esta vivo pero es el codigo viejo: /admin/ingresos aun no
        # existe en el proceso que corre. Hay que reiniciarlo.
        return {"ok": False, "estado": 404,
                "mensaje": "el Core que esta corriendo no tiene los ingresos "
                           "todavia: reinicialo con el codigo nuevo"}
    return {"ok": False, "estado": codigo,
            "mensaje": f"el Core respondio {codigo}: {datos.get('detail', datos)}"}


def resumen(core: str = CORE) -> tuple:
    codigo, datos = _llamar("GET", "/ingresos", core=core)
    return codigo, datos


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Anota en KUKIA (ledger) un trabajo cobrado en euros.")
    ap.add_argument("--trabajo", help="el .json del trabajo, o un nombre a mano")
    ap.add_argument("--cliente", default="", help="quien paga (nombre)")
    ap.add_argument("--minutos", type=float, default=None,
                    help="minutos de audio (por defecto, los del .json)")
    ap.add_argument("--importe", type=float, default=None,
                    help="euros cobrados (por defecto, los del .json)")
    ap.add_argument("--servicio", default="transcripcion")
    ap.add_argument("--core", default=CORE)
    ap.add_argument("--resumen", action="store_true",
                    help="cuanto se ha cobrado (lo que publica /ingresos)")
    ap.add_argument("--lista", action="store_true",
                    help="lista con nombres (necesita el token de admin)")
    args = ap.parse_args(argv)

    if args.resumen:
        codigo, d = resumen(args.core)
        if codigo == 404:
            print("[error] el Core que corre no tiene /ingresos: es el codigo "
                  "viejo, reinicialo para que entre en vigor")
            return 2
        if codigo != 200:
            print(f"[error] el Core no responde ({codigo}): {d.get('detail', '')}")
            return 2
        print(f"cobrado en total: {float(d.get('total_eur') or 0):.2f} EUR "
              f"en {d.get('n', 0)} trabajo(s)")
        if d.get("primera"):
            print(f"primer euro: {d['primera']}")
        for f in d.get("ultimos", [])[:10]:
            print(f"  {f.get('fecha')}  {f.get('minutos')} min  "
                  f"{float(f.get('precio_eur') or 0):.2f} EUR  "
                  f"asiento {(f.get('asiento_hash') or '')[:12]}")
        return 0

    if args.lista:
        codigo, d = _llamar("GET", "/admin/ingresos", core=args.core,
                            token=token_admin())
        if codigo != 200:
            print(f"[error] {codigo}: {d.get('detail', '')}")
            return 2
        print(f"total {float(d.get('total_eur') or 0):.2f} EUR en "
              f"{d.get('n', 0)} trabajo(s)")
        for r in d.get("ingresos", []):
            print(f"  #{r['id']}  {r['fecha']}  {r['cliente'] or '(sin nombre)'}  "
                  f"{r['minutos']} min  {float(r['precio_eur']):.2f} EUR  "
                  f"{r['trabajo']}  asiento {(r['asiento_hash'] or '')[:12]}")
        return 0

    if not args.trabajo:
        ap.error("hace falta --trabajo (o --resumen / --lista)")

    ruta = Path(args.trabajo)
    minutos, precio, detalle, nombre = args.minutos, args.importe, {}, args.trabajo
    if ruta.is_file() and ruta.suffix.lower() == ".json":
        try:
            d = leer_trabajo(ruta)
        except Exception as e:
            print(f"[error] no puedo leer {ruta}: {e}")
            return 2
        minutos = d.get("minutos") if minutos is None else minutos
        precio = d.get("precio_eur") if precio is None else precio
        nombre = d.get("fichero") or ruta.stem
        detalle = {"fichero": d.get("fichero"), "idioma": d.get("idioma_detectado"),
                   "palabras": d.get("palabras"), "motor": d.get("motor"),
                   "fichero_json": str(ruta)}
    if minutos is None or precio is None:
        print("[error] sin minutos/importe: pasalos a mano (--minutos, --importe)")
        return 2

    r = anotar(nombre, args.cliente, minutos, precio, args.servicio,
               detalle, args.core)
    print(("[ok] " if r["ok"] else "[aviso] ") + r["mensaje"])
    if not r["ok"]:
        if r.get("ya_estaba"):
            return 0
        return 1
    datos = r.get("datos") or {}
    print(f"       total cobrado hasta hoy: {float(datos.get('total_eur') or 0):.2f} EUR "
          f"en {datos.get('ingresos', '?')} trabajo(s)")
    print(f"       KIA emitidos por esto: {datos.get('kia_emitidos', 0)}")
    return 0


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
