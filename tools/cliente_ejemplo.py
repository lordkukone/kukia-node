#!/usr/bin/env python3
"""Cliente de ejemplo de la red KUKIA: pedir trabajo y recoger el resultado.

Esto es lo que hace un cliente de verdad, contra un Core de verdad:

  1. firma el encargo con SU billetera (Ed25519),
  2. el Core le cobra por adelantado y retiene el dinero,
  3. espera a que un minero lo resuelva,
  4. recoge el resultado y paga solo lo trabajado (el sobrante vuelve).

La clave de API la da el administrador del Core
(`POST /admin/clientes`). Perderla no es perder dinero: para gastar hace falta
ademas la billetera. Perder la billetera si es perder el saldo.

Uso:
  set KUKIA_CLIENTE_TOKEN=kia_...
  python tools/cliente_ejemplo.py precio matriz
  python tools/cliente_ejemplo.py saldo
  python tools/cliente_ejemplo.py pedir checksum --params "{\\"data\\": \\"hola\\"}"
  python tools/cliente_ejemplo.py pedir matriz --params "{\\"n\\": 256, \\"seed\\": 7}"

Opciones:
  --core URL        por defecto http://127.0.0.1:8300
  --token TOKEN     por defecto la variable KUKIA_CLIENTE_TOKEN
  --billetera NOMBRE  billetera local que firma (por defecto 'cliente')
  --espera SEG      cuanto esperar el resultado (por defecto 300)
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "ledger"))
import kukia_wallet as WALLET          # noqa: E402

CORE = os.environ.get("KUKIA_CORE", "http://127.0.0.1:8300")


def _norm(params: dict) -> dict:
    """Mismos tipos que aplica el Core antes de firmar.

    Se firma el sha256 de los params YA normalizados: si se firma "256" y el
    Core entiende 256, la firma no vale. Por eso se convierte aqui igual.
    """
    d = dict(params or {})
    for campo in ("n", "pasos", "seed"):
        if campo in d:
            d[campo] = int(d[campo])
    return d


def _post(ruta, cuerpo, token, timeout=60):
    import requests
    return requests.post(CORE + ruta, json=cuerpo,
                         headers={"X-Cliente-Token": token}, timeout=timeout)


def _get(ruta, token, params=None, timeout=60):
    import requests
    return requests.get(CORE + ruta, params=params or {},
                        headers={"X-Cliente-Token": token}, timeout=timeout)


def billetera(nombre: str) -> dict:
    d = WALLET.cargar_billetera(nombre)
    if not d.get("clave_privada"):
        raise SystemExit(f"[cliente] no puedo leer la clave privada de '{nombre}'")
    return d


def manda(args) -> int:
    w = billetera(args.billetera)
    params = _norm(json.loads(args.params or "{}"))

    r = _get("/cliente/precio", args.token, {"tipo": args.tipo})
    if r.status_code != 200:
        print(f"[cliente] el Core no da precio: {r.status_code} {r.text[:200]}")
        return 1
    suelo = float(r.json().get("coste_minimo") or 0.0)
    tope = float(args.tope) if (args.tope or "").strip() else max(1.0, suelo * 100)
    print(f"[cliente] coste minimo {suelo:.6f} KIA; autorizo hasta {tope:.6f} KIA")

    nonce = "c" + os.urandom(8).hex()
    h = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
    mensaje = f"cliente:{nonce}:{args.tipo}:{h}:{tope}"
    firma = WALLET.firmar(mensaje, w["clave_privada"])

    r = _post("/cliente/tareas", {"tipo": args.tipo, "params": params, "tope": tope,
                                  "nonce": nonce, "clave_publica": w["clave_publica"],
                                  "firma": firma}, args.token)
    if r.status_code != 200:
        print(f"[cliente] no se pudo encolar: {r.status_code} {r.text[:300]}")
        return 1
    d = r.json()
    tid = d["tarea_id"]
    print(f"[cliente] encolada {tid} · coste {float(d.get('coste') or 0):.6f} KIA pagados por adelantado")

    fin = time.time() + max(5, args.espera)
    while time.time() < fin:
        r = _get(f"/cliente/tareas/{tid}", args.token)
        if r.status_code != 200:
            print(f"[cliente] error consultando: {r.status_code} {r.text[:200]}")
            return 1
        t = r.json()
        if t.get("estado") == "ok":
            print(f"[cliente] LISTO en {tid}")
            print(f"  resultado : {t.get('resultado')}")
            print(f"  pagado    : {float(t.get('pagado') or 0):.6f} KIA")
            if args.json:
                print(json.dumps(t, indent=2, ensure_ascii=False))
            return 0
        if t.get("estado") == "fallo":
            print(f"[cliente] la tarea fallo y el dinero volvio al saldo: {t}")
            return 1
        if args.verboso:
            print(f"[cliente] {t.get('estado')}...")
        time.sleep(2)
    print(f"[cliente] sin resultado en {args.espera}s; la tarea {tid} sigue en cola "
          f"(puedes recogerla luego con 'ver {tid}')")
    return 2


def main() -> int:
    global CORE
    ap = argparse.ArgumentParser(description="Cliente de ejemplo de KUKIA")
    ap.add_argument("--core", default=CORE)
    ap.add_argument("--token", default=os.environ.get("KUKIA_CLIENTE_TOKEN", ""))
    ap.add_argument("--billetera", default="cliente")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verboso", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("saldo")

    p = sub.add_parser("precio")
    p.add_argument("tipo")

    p = sub.add_parser("pedir")
    p.add_argument("tipo")
    p.add_argument("--params", default="{}")
    p.add_argument("--tope", default="")
    p.add_argument("--espera", type=int, default=300)

    p = sub.add_parser("ver")
    p.add_argument("tarea_id")

    args = ap.parse_args()
    CORE = args.core.rstrip("/")
    if not args.token:
        print("[cliente] falta el token: --token o KUKIA_CLIENTE_TOKEN")
        return 2

    if args.cmd == "saldo":
        r = _get("/cliente/saldo", args.token)
        print(json.dumps(r.json(), indent=2, ensure_ascii=False) if r.status_code == 200
              else f"[cliente] {r.status_code} {r.text[:200]}")
        return 0 if r.status_code == 200 else 1

    if args.cmd == "precio":
        r = _get("/cliente/precio", args.token, {"tipo": args.tipo})
        print(json.dumps(r.json(), indent=2, ensure_ascii=False) if r.status_code == 200
              else f"[cliente] {r.status_code} {r.text[:200]}")
        return 0 if r.status_code == 200 else 1

    if args.cmd == "ver":
        r = _get(f"/cliente/tareas/{args.tarea_id}", args.token)
        print(json.dumps(r.json(), indent=2, ensure_ascii=False) if r.status_code == 200
              else f"[cliente] {r.status_code} {r.text[:200]}")
        return 0 if r.status_code == 200 else 1

    return manda(args)


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
