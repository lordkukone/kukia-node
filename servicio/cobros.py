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

  Cada intento (entre o no en el ledger) queda apuntado en
  `servicio/cobros_registro.jsonl` con el sha8 del token, nunca el token: un
  403 o un Core caido se puede leer despues en vez de perderse en la consola.

USO
  # anotar el trabajo a partir del .json que deja transcribir.py
  python servicio/cobros.py --trabajo servicio/entregas/20261025/charla.json \
      --cliente "Maria G."

  # si se cobro otro importe (descuento, prueba gratis, precio pactado)
  python servicio/cobros.py --trabajo ... --cliente "Maria G." --importe 4.50

  # cuanto se ha cobrado hasta hoy (lo mismo que publica /ingresos)
  python servicio/cobros.py --resumen

  # lo que se intento y no entro, y volver a mandarlo (una a una, a mano)
  python servicio/cobros.py --pendientes
  python servicio/cobros.py --reintentar

  # lo que queda anotado en el Core, con nombres (necesita el token)
  python servicio/cobros.py --lista

El mismo trabajo no se puede anotar dos veces: el Core lo rechaza con 409. Un
euro contado dos veces dejaria de ser prueba de nada.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
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


def _sha8(valor: str) -> str:
    """Huella corta del token: compara dos tokens sin enseñar ninguno."""
    if not valor:
        return ""
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()[:8]


def token_diagnostico() -> dict:
    """De donde sale el token y por que, sin enseñar el valor.

    Devuelve {token, origen, error, sha8}. `origen` es "fichero", "env" o
    "ninguno". Antes todo esto era un `except Exception: pass`, asi que un 403
    no distinguia "no mande token" de "mande uno viejo": las dos cosas se
    arreglan de forma distinta y ahora se pueden separar.
    """
    error = ""
    try:
        f = archivo_token()
        if f.is_file():
            t = f.read_text(encoding="utf-8").strip()
            if t:
                return {"token": t, "origen": "fichero", "error": "", "sha8": _sha8(t)}
            error = "el fichero del token esta vacio"
        else:
            error = "el fichero del token no existe"
    except Exception as e:
        error = f"no se pudo leer el token ({type(e).__name__}: {e})"
    t = os.environ.get("KUKIA_ADMIN_TOKEN_TEXTO", "").strip()
    if t:
        return {"token": t, "origen": "env", "error": error, "sha8": _sha8(t)}
    return {"token": "", "origen": "ninguno", "error": error, "sha8": ""}


def token_admin() -> str:
    """Token del Core (mismo fichero que usa para administrarse). Vacio si no hay."""
    return token_diagnostico()["token"]


def registro_cobros() -> Path:
    """Donde se apunta cada intento de cobro (se mira en CADA llamada).

    El registro es local y no sustituye al ledger: el ledger dice lo que ENTRO;
    esto dice lo que se INTENTO, para que un 403 o un Core caido no se quede en
    una linea de consola que nadie lee. Lleva nombres de cliente, asi que no se
    publica ni se sube al repositorio.
    """
    return Path(os.environ.get("KUKIA_COBROS_REGISTRO")
                or (RAIZ / "servicio" / "cobros_registro.jsonl"))


def intentos() -> list:
    """Lee el registro local de intentos (una linea JSON por intento).

    Si el fichero no existe o una linea esta rota, se ignora: el registro es
    una ayuda, no la fuente de verdad (esa es el ledger del Core).
    """
    ruta = registro_cobros()
    if not ruta.is_file():
        return []
    salida = []
    try:
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if not linea:
                continue
            try:
                u = json.loads(linea)
            except Exception:
                continue
            if isinstance(u, dict):
                salida.append(u)
    except Exception:
        return salida
    return salida


def _clave(u: dict) -> tuple:
    """Identidad del cobro: la misma que usa el Core para no contar dos veces."""
    try:
        precio = round(float(u.get("precio_eur") or 0.0), 2)
    except (TypeError, ValueError):
        precio = 0.0
    return (u.get("servicio") or "transcripcion", u.get("cliente") or "",
            u.get("trabajo") or "", precio)


def pendientes() -> list:
    """Cobros que se intentaron y NO entraron en el ledger.

    Manda el ULTIMO intento de cada cobro: si ese entro (200) o el Core dijo
    que ya estaba (409), el euro esta dentro y no se repite. Asi un reintento
    que funciono no vuelve a aparecer por culpa de los fallos anteriores.
    """
    ultimo = {}
    orden = []
    for u in intentos():
        k = _clave(u)
        if k not in ultimo:
            orden.append(k)
        ultimo[k] = u
    fuera = []
    for k in orden:
        u = ultimo[k]
        if u.get("ok") is True or u.get("ya_estaba") or u.get("estado") in (200, 409):
            continue
        fuera.append(u)
    return fuera


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


def _apuntar(registro: dict) -> None:
    """Deja constancia en disco de un intento de cobro. Nunca lanza.

    El cobro no puede romper el trabajo, asi que esto va lo ultimo y cualquier
    fallo (disco lleno, permisos, ruta rara) se ignora: el trabajo ya esta
    entregado y su .json sigue ahi para volver a anotarlo a mano.
    """
    try:
        ruta = registro_cobros()
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with ruta.open("a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
            f.flush()
    except Exception:
        pass


def _resultado(codigo: int, datos: dict, diag: dict, core: str) -> dict:
    """Traduce la respuesta del Core a algo legible y registrable."""
    base = {"estado": codigo, "token_origen": diag["origen"],
            "token_sha8": diag["sha8"]}
    if codigo == 200:
        return {**base, "ok": True, "motivo": "anotado",
                "mensaje": (f"ingreso #{datos.get('id')} anotado: "
                            f"{datos.get('precio_eur')} EUR, asiento "
                            f"{(datos.get('asiento_hash') or '')[:12]}…"),
                "datos": datos, "id": datos.get("id")}
    if codigo == 409:
        return {**base, "ok": False, "motivo": "ya_estaba", "ya_estaba": True,
                "mensaje": f"ya estaba anotado: {datos.get('detail', '')}"}
    if codigo == 403:
        # El 403 tiene DOS causas que antes se confundian: no mandar token (el
        # fichero no existe, esta vacio o no se puede leer) o mandar uno que el
        # Core no tiene en memoria. El sha8 las separa sin enseñar el secreto.
        if not diag["sha8"]:
            return {**base, "ok": False, "motivo": "sin_token",
                    "mensaje": (f"no se envio token al Core ({diag['error'] or 'sin token'}); "
                                f"revisa {archivo_token()} o KUKIA_ADMIN_TOKEN")}
        return {**base, "ok": False, "motivo": "token_rechazado",
                "mensaje": (f"el Core rechaza el token enviado "
                            f"(sha8={diag['sha8']}, origen={diag['origen']}): si lo has "
                            f"rotado, el Core sigue con el viejo en memoria hasta "
                            f"que lo reinicies")}
    if codigo == 0:
        return {**base, "ok": False, "motivo": "core_no_responde",
                "mensaje": f"el Core no responde en {core}: {datos.get('detail', '')}"}
    if codigo == 404:
        # El Core esta vivo pero es el codigo viejo: /admin/ingresos aun no
        # existe en el proceso que corre. Hay que reiniciarlo.
        return {**base, "ok": False, "motivo": "core_sin_ingresos",
                "mensaje": "el Core que esta corriendo no tiene los ingresos "
                           "todavia: reinicialo con el codigo nuevo"}
    return {**base, "ok": False, "motivo": "otro",
            "mensaje": f"el Core respondio {codigo}: {datos.get('detail', datos)}"}


def anotar(trabajo: str, cliente: str, minutos: float, precio_eur: float,
           servicio: str = "transcripcion", detalle: dict | None = None,
           core: str = CORE) -> dict:
    """Manda el ingreso al Core. Devuelve {ok, estado, motivo, mensaje, id}."""
    cuerpo = {"servicio": servicio, "cliente": cliente, "trabajo": trabajo,
              "minutos": round(float(minutos), 2),
              "precio_eur": round(float(precio_eur), 2),
              "detalle": detalle or {}}
    diag = token_diagnostico()
    codigo, datos = _llamar("POST", "/admin/ingresos", cuerpo,
                            core=core, token=diag["token"])
    r = _resultado(codigo, datos, diag, core)
    # Constancia local: se apunta el intento entre o no en el ledger. El sha8
    # del token sirve para comparar con el que tiene el Core; el token, nunca.
    _apuntar({"fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "ok": r["ok"], "estado": codigo, "motivo": r.get("motivo", ""),
              "servicio": servicio, "trabajo": trabajo, "cliente": cliente,
              "minutos": round(float(minutos), 2),
              "precio_eur": round(float(precio_eur), 2), "core": core,
              "detalle": detalle or {},
              "token_origen": diag["origen"], "token_sha8": diag["sha8"],
              "token_error": diag["error"],
              "mensaje": str(r.get("mensaje", ""))[:300]})
    return r


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
    ap.add_argument("--pendientes", action="store_true",
                    help="cobros intentados que NO entraron en el ledger")
    ap.add_argument("--reintentar", action="store_true",
                    help="vuelve a mandar los pendientes, uno a uno (manual)")
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

    if args.pendientes:
        cola = pendientes()
        if not cola:
            print("no hay cobros pendientes: todo lo intentado esta en el ledger")
            return 0
        print(f"{len(cola)} cobro(s) intentado(s) y no anotado(s):")
        total = 0.0
        for u in cola:
            total += float(u.get("precio_eur") or 0.0)
            print(f"  {u.get('fecha', '?')}  {float(u.get('precio_eur') or 0):7.2f} EUR  "
                  f"estado {u.get('estado')} ({u.get('motivo') or '?'})  "
                  f"{u.get('trabajo') or '(sin nombre)'}  ·  "
                  f"{u.get('cliente') or '(sin cliente)'}")
        print(f"  total sin anotar: {total:.2f} EUR")
        return 0

    if args.reintentar:
        cola = pendientes()
        if not cola:
            print("no hay cobros pendientes: nada que reintentar")
            return 0
        print(f"reintentando {len(cola)} cobro(s) contra {args.core}…")
        bien = mal = 0
        for u in cola:
            r = anotar(u.get("trabajo") or "transcripcion", u.get("cliente") or "",
                       float(u.get("minutos") or 0.0),
                       float(u.get("precio_eur") or 0.0),
                       u.get("servicio") or "transcripcion",
                       u.get("detalle") or {}, args.core)
            if r["ok"] or r.get("ya_estaba"):
                bien += 1
                print(f"  [ok] {u.get('trabajo')}: {r['mensaje']}")
            else:
                mal += 1
                print(f"  [aviso] {u.get('trabajo')}: {r['mensaje']}")
        print(f"\n{bien} resuelto(s), {mal} sigue(n) fuera")
        return 0 if mal == 0 else 1

    if not args.trabajo:
        ap.error("hace falta --trabajo (o --resumen / --lista / --pendientes / --reintentar)")

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
