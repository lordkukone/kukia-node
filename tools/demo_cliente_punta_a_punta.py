#!/usr/bin/env python3
"""Demostracion punta a punta de un cliente contra el CORE REAL.

Hace todo lo que hace un cliente de verdad, en orden, y lo comprueba:

  1. Crea una billetera nueva (Ed25519) para el cliente.
  2. Lo da de alta con el token de administrador (el token se lee del disco y
     NO se imprime nunca).
  3. Le ingresa saldo con una transferencia firmada desde la billetera del
     fundador.
  4. Pide precio de una tarea de inferencia.
  5. Firma el encargo y lo encola DEPOSITANDO MAS QUE EL COSTE: el Core deja
     retenido el deposito, no el coste justo (asi se ve la devolucion).
  6. Espera a que un minero lo resuelva.
  7. Comprueba las cuentas: el minero cobra su tarifa entera, el cliente paga
     lo trabajado y el SOBRANTE vuelve a su saldo, y el recibo (hash +
     verificacion) cuadra.

El sobrante no sale del bolsillo del minero: se paga la tarifa completa y la
diferencia se devuelve del escrow. Con `--extra 0` el deposito es el coste
justo y no hay sobrante (para comparar).

Uso:
  python tools/demo_cliente_punta_a_punta.py
  python tools/demo_cliente_punta_a_punta.py --params '{"n": 1024, "seed": 7}'
  python tools/demo_cliente_punta_a_punta.py --extra 0.05 --monto 0.2 --espera 600

No toca ficheros del Core. Lo unico que cambia en el ledger son los asientos de
esta prueba (unas centesimas de KIA) y los de los mineros que ya estan
trabajando.
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
sys.path.insert(0, str(RAIZ / "ledger"))
import kukia_wallet as WALLET          # noqa: E402

CORE = os.environ.get("KUKIA_CORE", "http://127.0.0.1:8300")


# ------------------------------------------------------------------ HTTP

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


def get(ruta, token=None, params=None):
    q = ""
    if params:
        q = "?" + urllib.parse.urlencode(params)
    return _req("GET", ruta + q, None, {"X-Cliente-Token": token} if token else None)


def post(ruta, cuerpo, cabeceras=None):
    return _req("POST", ruta, cuerpo, cabeceras)


# ------------------------------------------------------------------ ayudas

def ok(texto):
    print(f"  [ OK ] {texto}")


def mal(texto):
    print(f"  [FALLO] {texto}")


def saldo(cuenta: str) -> float:
    c, d = get(f"/saldo/{cuenta}")
    return float(d.get("saldo") or 0.0) if c == 200 else -1.0


def admin_token() -> str:
    f = RAIZ / "coordinator" / "admin_token.txt"
    if not f.is_file():
        raise SystemExit("no encuentro coordinator/admin_token.txt (hace falta para dar de alta)")
    return f.read_text(encoding="utf-8").strip()


def fundador() -> dict:
    """Billetera del fundador con saldo: es de donde sale el ingreso de prueba."""
    for nombre in ("kuki_fundador_nueva", "kuki_fundador"):
        try:
            w = WALLET.cargar_billetera(nombre)
        except Exception:
            continue
        if not w.get("clave_privada"):
            continue
        if saldo(w["direccion"]) > 0.01:
            return w
    raise SystemExit("no encuentro una billetera del fundador con saldo")


def main() -> int:
    global CORE
    ap = argparse.ArgumentParser(description="Demostracion de cliente contra el Core real")
    ap.add_argument("--core", default=CORE)
    ap.add_argument("--tipo", default="inferencia")
    ap.add_argument("--params", default='{"n": 1024, "seed": 7}')
    ap.add_argument("--monto", type=float, default=0.05, help="KIA que se ingresan al cliente")
    ap.add_argument("--extra", type=float, default=0.001,
                    help="KIA de mas que se depositan sobre el coste (0 = coste justo)")
    ap.add_argument("--espera", type=int, default=300)
    args = ap.parse_args()

    CORE = args.core.rstrip("/")
    fallos = []

    print("== 0. El Core ==")
    c, st = get("/stats")
    if c != 200:
        raise SystemExit(f"el Core no responde en {CORE}: {c} {st}")
    st = st.get("stats", st)
    print(f"  version {st.get('version')} · nodos {st.get('nodos')} · "
          f"supply {float(st.get('supply_emitido') or 0):,.2f} KIA".replace(",", "."))

    print("\n== 1. Billetera nueva del cliente ==")
    nombre = "cliente_prueba_" + time.strftime("%Y%m%d_%H%M%S")
    creada = WALLET.crear_billetera(nombre)
    w = creada[0] if isinstance(creada, tuple) else creada
    cliente_dir = w["direccion"]
    print(f"  {nombre}: {cliente_dir}")
    print("  (clave privada y mnemonico quedan en ledger/wallet/, no se imprimen)")

    print("\n== 2. Alta del cliente en el Core ==")
    c, d = post("/admin/clientes", {"nombre": "prueba punta a punta", "direccion": cliente_dir},
                {"X-Admin-Token": admin_token()})
    if c != 200:
        raise SystemExit(f"no se pudo dar de alta: {c} {d}")
    token = d.get("token")
    print(f"  cliente {d.get('id')} dado de alta; token recibido una sola vez "
          f"(no se imprime)")

    print("\n== 3. Ingreso de saldo (transferencia firmada) ==")
    f = fundador()
    monto = round(float(args.monto), 4)
    nonce = "d" + os.urandom(8).hex()
    mensaje = f"transferir:{f['direccion']}:{cliente_dir}:{monto}:{nonce}"
    firma = WALLET.firmar(mensaje, f["clave_privada"])
    c, d = post("/transferir", {"de_nodo": f["direccion"], "a_direccion": cliente_dir,
                                "monto": monto, "nonce": nonce, "firma": firma,
                                "clave_publica": f["clave_publica"]})
    if c != 200:
        raise SystemExit(f"la transferencia fallo: {c} {d}")
    saldo_ini = saldo(cliente_dir)
    print(f"  {monto} KIA desde {f['direccion'][:16]}... -> saldo del cliente: {saldo_ini:.6f} KIA")
    if abs(saldo_ini - monto) > 1e-6:
        fallos.append("el ingreso no llego entero al saldo del cliente")

    print(f"\n== 4. Precio EXACTO de esta tarea de {args.tipo} ==")
    c, d = get("/cliente/precio", token, {"tipo": args.tipo, "params": args.params})
    if c != 200:
        raise SystemExit(f"el Core no da precio: {c} {d}")
    # Los params ya vienen normalizados por el Core: se firma justo lo que el
    # Core va a hashear (si no, la firma no valdria).
    params = d.get("params") or json.loads(args.params)
    coste_prev = float(d.get("coste") or d.get("coste_minimo") or 0.0)
    print(f"  coste: {coste_prev:.9f} KIA (suelo del tipo: "
          f"{float(d.get('coste_minimo') or 0):.9f})   (tope autorizado: 1.0 KIA)")

    print("\n== 5. Pedido firmado (el Core retiene el DEPOSITO) ==")
    tope = 1.0
    extra = max(0.0, float(args.extra))
    deposito = round(coste_prev + extra, 12)
    h = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
    nonce = "c" + os.urandom(8).hex()
    mensaje = f"cliente:{nonce}:{args.tipo}:{h}:{tope}"
    if deposito > 0:
        # Con deposito, el importe retenido va DENTRO de la firma.
        mensaje += f":{deposito}"
    firma = WALLET.firmar(mensaje, w["clave_privada"])
    c, d = post("/cliente/tareas", {"tipo": args.tipo, "params": params, "tope": tope,
                                    "deposito": deposito,
                                    "nonce": nonce, "clave_publica": w["clave_publica"],
                                    "firma": firma}, {"X-Cliente-Token": token})
    if c != 200:
        raise SystemExit(f"no se pudo encolar: {c} {d}")
    tid = d["tarea_id"]
    coste = float(d.get("coste") or 0.0)
    depositado = float(d.get("deposito") or 0.0)
    print(f"  tarea {tid} · coste {coste:.9f} KIA · depositado {depositado:.9f} KIA")
    print(f"  (al cerrar deben volver {depositado - coste:.9f} KIA al cliente)")
    if abs(coste - coste_prev) > 1e-12:
        fallos.append(f"el coste al encolar ({coste:.12f}) no es el que dio el precio "
                      f"({coste_prev:.12f})")
    if abs(depositado - deposito) > 1e-12:
        fallos.append(f"el Core retuvo {depositado:.12f} y se depositaron {deposito:.12f}")

    c, s = get("/cliente/saldo", token)
    retenido = float(s.get("retenido") or 0.0) if c == 200 else -1
    print(f"  saldo durante la espera: {float(s.get('saldo') or 0):.9f} KIA "
          f"· retenido: {retenido:.9f}")
    if abs(retenido - deposito) > 1e-12:
        fallos.append(f"mientras esta en cola deberia haber {deposito:.12f} retenidos "
                      f"y hay {retenido:.12f}")

    print(f"\n== 6. Esperando al minero (max {args.espera} s) ==")
    nodos_antes = {k: float(v) for k, v in (get("/saldos")[1].get("saldos") or {}).items()}
    t = {}
    fin = time.time() + max(10, args.espera)
    while time.time() < fin:
        c, t = get(f"/cliente/tareas/{tid}", token)
        if c != 200:
            raise SystemExit(f"no puedo consultar la tarea: {c} {t}")
        estado = t.get("estado")
        if estado in ("ok", "fallo"):
            print(f"  estado: {estado}")
            break
        print(f"  {estado}... ({int(fin - time.time())} s restantes)")
        time.sleep(5)

    if t.get("estado") != "ok":
        print("\nNo hay resultado: la tarea sigue en cola o fallo. No invento nada.")
        return 2

    print("\n== 7. Resultado ==")
    print(f"  resultado : {str(t.get('resultado'))[:120]}")
    print(f"  hash      : {t.get('hash')}")
    print(f"  verificado: {t.get('verificado')} · terminado: {t.get('terminado')}")
    print(f"  coste     : {float(t.get('coste') or 0):.9f} KIA "
          f"· pagado: {float(t.get('pagado') or 0):.9f} KIA")

    print("\n== 8. Las cuentas ==")
    pagado = float(t.get("pagado") or 0.0)
    c, s = get("/cliente/saldo", token)
    saldo_fin = float(s.get("saldo") or 0.0)
    retenido_fin = float(s.get("retenido") or 0.0)
    devuelto = round(retenido - pagado, 12)
    print(f"  cliente: {saldo_ini:.9f} -> {saldo_fin:.9f} KIA "
          f"(pagado {pagado:.9f} + devuelto {devuelto:.9f} = deposito {retenido:.9f})")
    if devuelto > 0:
        ok(f"el sobrante vuelve: {devuelto:.9f} KIA de los {retenido:.9f} depositados")
    elif abs(devuelto) < 1e-12:
        print("  (depositaste el coste justo: no hay sobrante que devolver; "
              "usa --extra para ver la devolucion)")
    else:
        mal(f"el pago ({pagado:.9f}) supera el deposito ({retenido:.9f}): "
            f"el minero habria cobrado de menos")
        fallos.append("el deposito no cubre la tarifa del minero")
    if abs((saldo_ini - saldo_fin) - pagado) > 1e-9:
        fallos.append(f"el cliente no acaba de pagar lo trabajado: "
                      f"{saldo_ini - saldo_fin:.12f} vs {pagado:.12f}")
    else:
        ok(f"el cliente paga exactamente lo trabajado: {pagado:.9f} KIA")
    if abs(saldo_fin - (saldo_ini - pagado)) > 1e-9:
        fallos.append("el saldo final no cuadra con saldo inicial menos lo pagado")
    if abs(retenido_fin) > 1e-12:
        fallos.append(f"quedo dinero retenido: {retenido_fin:.12f}")
    else:
        ok("no queda nada retenido en escrow")

    nodos_despues = {k: float(v) for k, v in (get("/saldos")[1].get("saldos") or {}).items()}
    subidas = [(k, round(v - nodos_antes.get(k, 0.0), 9)) for k, v in nodos_despues.items()
               if k not in (cliente_dir, WALLET_CUENTA_ESCROW()) and
               v - nodos_antes.get(k, 0.0) > 1e-9]
    subidas.sort(key=lambda x: -x[1])
    if subidas:
        print("  cuentas que subieron mientras corria la tarea:")
        for k, v in subidas[:5]:
            print(f"    {k[:30]:<30} +{v:.9f} KIA")
        cerca = [k for k, v in subidas if abs(v - pagado) < 1e-6]
        if cerca:
            ok(f"el minero {cerca[0][:24]} cobra exactamente el pago de la tarea")
        else:
            print("  (el pago de esta tarea y el trabajo normal de la red se mezclan "
                  "en la misma cuenta: el importe exacto esta en la tarea y en el ledger)")
    else:
        print("  (ninguna cuenta subio: revisa si el minero estaba trabajando)")

    print("\n== 8b. Los asientos de esta tarea, uno a uno ==")
    c, ls = get("/ledger")
    mios = [x for x in (ls if isinstance(ls, list) else []) if x.get("tarea_id") == tid]
    if not mios:
        print("  (sus asientos ya no estan en los ultimos 200 del ledger: la red escribe "
              "mucho. El pago consta en la tarea y en la cadena, no se puede inventar)")
    else:
        for x in reversed(mios):
            print(f"    {str(x.get('motivo'))[:24]:<24} {str(x.get('nodo_id'))[:30]:<30} "
                  f"{float(x.get('kuki') or 0):+.9f} KIA")
        suma = sum(float(x.get("kuki") or 0.0) for x in mios)
        print(f"    suma de la tarea: {suma:+.12f} KIA")
        if abs(suma) < 1e-9:
            ok("los asientos se compensan exactamente: el prepago no crea dinero")
        else:
            mal("los asientos de la tarea no suman cero")
            fallos.append(f"los asientos suman {suma:.12f} en vez de 0")
        escrow = WALLET_CUENTA_ESCROW()
        # Solo los asientos de TRABAJO: la devolucion del sobrante tambien entra
        # en una cuenta (la del cliente) y antes se contaba como pago a minero.
        motivos_pago = ("trabajo verificado", "verificacion redundante")
        a_mineros = sum(float(x.get("kuki") or 0.0) for x in mios
                        if float(x.get("kuki") or 0) > 0
                        and x.get("nodo_id") != escrow
                        and str(x.get("motivo") or "").startswith(motivos_pago))
        if abs(a_mineros - pagado) < 1e-9:
            ok(f"lo que entra en cuentas de mineros es exactamente lo pagado "
               f"({a_mineros:.9f} KIA)")
        else:
            mal(f"a los mineros entran {a_mineros:.9f} y la tarea dice {pagado:.9f}")
            fallos.append("el pago a mineros no coincide con lo que declara la tarea")
        # La devolucion, asiento a asiento: escrow -> cliente, por el sobrante.
        a_cliente = sum(float(x.get("kuki") or 0.0) for x in mios
                        if float(x.get("kuki") or 0) > 0 and x.get("nodo_id") == cliente_dir)
        if devuelto > 0:
            if abs(a_cliente - devuelto) < 1e-9:
                ok(f"el sobrante vuelve asentado ({a_cliente:.9f} KIA al cliente, "
                   f"del escrow, no del minero)")
            else:
                mal(f"al cliente vuelven {a_cliente:.9f} y el sobrante es {devuelto:.9f}")
                fallos.append("la devolucion del sobrante no cuadra en los asientos")

    print("\n== 9. El recibo ==")
    c, v = get("/ledger/verificar")
    cadena = v.get("cadena_ok", v.get("ok"))
    print(f"  /ledger/verificar: {json.dumps(v, ensure_ascii=False)[:220]}")
    if cadena:
        ok("la cadena de hashes del ledger sigue cuadrando con este trabajo dentro")
    else:
        mal("la cadena del ledger no cuadra")
        fallos.append("la verificacion del ledger no da cadena_ok")

    print()
    if fallos:
        print(f"{len(fallos)} FALLO(S):")
        for f_ in fallos:
            print(f"  - {f_}")
        return 1
    print("TODO OK: cliente dado de alta, prepago retenido, minero pagado, "
          "sobrante devuelto y recibo verificable.")
    print(f"Para volver a mirarlo: python tools/cliente_ejemplo.py --token <token> ver {tid}")
    print("(el token no se guarda en claro en ningun sitio: solo su hash en el Core)")
    return 0


def WALLET_CUENTA_ESCROW() -> str:
    return os.environ.get("KUKIA_ESCROW", "KUKIA_ESCROW")


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
