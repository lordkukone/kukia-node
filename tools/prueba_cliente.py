#!/usr/bin/env python3
"""Prueba de la PUERTA DE CLIENTE (24-10-2026).

Carga un Core de verdad en una carpeta temporal (no toca el ledger real) y
recorre el ciclo completo como lo haria un cliente de la red:

  1. ALTA: el Core da un token de API y lo ensena UNA vez (en la base solo
     queda el hash).
  2. PAGO POR ADELANTADO: al encolar, el dinero sale del saldo del cliente y
     queda RETENIDO en escrow. Sin saldo no hay tarea.
  3. FIRMA: el token solo no basta; el prepago exige la firma Ed25519 de la
     billetera del cliente sobre nonce + tipo + params + tope.
  4. COBRO: el minero cobra DEL ESCROW, no de la emision. Un cliente que paga
     no infla el suministro.
  5. RECOGIDA: el cliente lee el resultado, y solo el suyo.
  6. DEVOLUCION: si la tarea se cae (nodo abandonado), el dinero vuelve.
  7. ATAQUES: token invalido, firma invalida, nonce reutilizado, tarea ajena,
     tope insuficiente, y un cliente sin firma intentando gastar.

Uso:
  python tools/prueba_cliente.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

RUNNER = r'''
import hashlib, json, os, sys, time
sys.path.insert(0, os.path.join(os.getcwd(), "coordinator"))
sys.path.insert(0, os.getcwd())
import kukia_core as C
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

def par():
    k = Ed25519PrivateKey.generate()
    pub = k.public_key().public_bytes(serialization.Encoding.Raw,
                                      serialization.PublicFormat.Raw).hex()
    priv = k.private_bytes(serialization.Encoding.Raw,
                           serialization.PrivateFormat.Raw,
                           serialization.NoEncryption()).hex()
    return pub, priv

def firmar(priv, msg):
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv)).sign(msg.encode()).hex()

def saldo(cta):
    # 12 decimales: con la definicion (1 KIA = 1 minuto de GPU) una tarea cuesta
    # ~4e-8 KIA y redondear a 6 lo convertia en 0, falseando las comprobaciones
    # del escrow.
    f = C.con.execute("SELECT total FROM saldos_cuenta WHERE cuenta=?", (cta,)).fetchone()
    return round(float(f["total"] if f else 0.0), 12)

def meter_saldo(cta, monto):
    """Fondea al cliente: simula que ya compro/mino KIA antes de pedir trabajo."""
    with C._escritura():
        C._asiento("prueba_fondeo", cta, monto, "fondeo de prueba")

def metricas():
    return {
        "clientes": C.con.execute("SELECT COUNT(*) c FROM clientes").fetchone()["c"],
        "supply": round(C.supply_emitido(), 6),
        "escrow": saldo(C.CUENTA_ESCROW),
    }

out = {"pasos": {}}
fallos = []

def check(ok, texto, extra=None):
    out["pasos"][texto] = bool(ok)
    if not ok:
        fallos.append({"texto": texto, "extra": extra})

def post(cli, url, **kw):
    kw.setdefault("timeout", 60)
    return cli.post(url, **kw)

def get(cli, url, **kw):
    kw.setdefault("timeout", 60)
    return cli.get(url, **kw)

# base_url loopback y no "testserver": el Core tiene guardia de Host.
with TestClient(C.app, base_url="http://127.0.0.1") as cli:
    adm = {"X-Admin-Token": C.ADMIN_TOKEN}

    # ---------- 1. ALTA DEL CLIENTE -------------------------------------
    pub_c, priv_c = par()
    dir_c = C.direccion_de_pubkey(pub_c)
    r = post(cli, "/admin/clientes", json={"nombre": "empresa de prueba", "direccion": dir_c}, headers=adm)
    check(r.status_code == 200, "el Core da de alta al cliente", r.text[:300])
    tok = (r.json() or {}).get("token") or ""
    out["cliente"] = {"direccion": dir_c, "token_len": len(tok), "alta": r.json()}
    check(len(tok) >= 32, "el token de API se devuelve UNA vez y es largo")

    fila = C.con.execute("SELECT token_hash FROM clientes WHERE direccion=?", (dir_c,)).fetchone()
    check(bool(fila) and fila["token_hash"] != tok, "en la base solo queda el HASH del token")
    check(hashlib.sha256(tok.encode()).hexdigest() == (fila["token_hash"] if fila else ""),
          "el hash guardado corresponde al token entregado")

    r = post(cli, "/admin/clientes", json={"nombre": "otro", "direccion": dir_c})
    check(r.status_code == 403, "sin token de admin no se pueden crear clientes", r.text[:200])
    r = post(cli, "/admin/clientes", json={"nombre": "c2", "direccion": dir_c}, headers=adm)
    check(r.status_code == 409 or r.status_code == 400, "no se puede duplicar la billetera de un cliente",
          r.text[:200])

    cli_h = {"X-Cliente-Token": tok}

    params = C.validar_params("matriz", {"n": 256, "seed": 3})
    h_params = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()

    def pide(nonce, tope=50.0, h=None):
        msg = "cliente:%s:matriz:%s:%s" % (nonce, h or h_params, tope)
        return {"tipo": "matriz", "params": params, "tope": tope, "nonce": nonce,
                "clave_publica": pub_c, "firma": firmar(priv_c, msg)}

    # ---------- 2. SIN SALDO NO HAY TAREA -------------------------------
    nonce = "n-" + os.urandom(6).hex()
    r = post(cli, "/cliente/tareas", json=pide(nonce), headers=cli_h)
    check(r.status_code == 402 and "saldo" in r.text.lower(),
          "un cliente sin saldo no puede encolar aunque firme bien (402)", r.text[:200])

    # Se le dan 100 KIA (como si los hubiera comprado).
    meter_saldo(dir_c, 100.0)
    antes = metricas()

    # La tarifa de un Core recien nacido no tiene con que medirse (no hay
    # trabajo previo que fije el precio del minuto) y pagaria 0. Se siembra la
    # medida de red que tendria un Core en marcha, como hace la prueba de
    # potencia: 40 difusiones verificadas en el minuto anterior.
    ancla = time.ctime((int(time.time() // 60) - 1) * 60 + 30)
    with C._escritura():
        for _ in range(40):
            C.con.execute("INSERT INTO tareas(id,tipo,params,estado,creado,fin) "
                          "VALUES(?,?,?,'ok',?,?)",
                          (os.urandom(6).hex(), "difusion_calor",
                           json.dumps({"n": 1024, "pasos": 1500, "seed": 7,
                                       "tolerancia_rel": 1e-9}), ancla, ancla))
    C._precio_minuto["minuto"] = -1
    C._precio_minuto["datos"] = None
    C._minuto_actual["minuto"] = -1
    C._minuto_actual["pagado"] = 0.0
    C._minuto_actual["credito"] = 0.0
    C._precio_cache["ts"] = 0.0
    C._precio_cache["datos"] = None
    out["auto"] = C.AUTO_ACTIVO
    out["tareas_antes"] = [dict(f) for f in C.con.execute(
        "SELECT id,tipo,campana,estado FROM tareas WHERE estado NOT IN ('ok') LIMIT 10").fetchall()]

    # ---------- 3. PRECIO ANTES DE FIRMAR -------------------------------
    r = get(cli, "/cliente/precio", params={"tipo": "matriz"}, headers=cli_h)
    check(r.status_code == 200, "el cliente puede consultar el coste antes de firmar", r.text[:200])
    suelo = float(r.json()["coste_minimo"])
    out["coste_minimo"] = suelo

    # ---------- 4. FIRMA OBLIGATORIA ------------------------------------
    nonce = "n-" + os.urandom(6).hex()
    r = post(cli, "/cliente/tareas", json={"tipo": "matriz", "params": params, "tope": 50.0,
                                          "nonce": nonce, "clave_publica": pub_c}, headers=cli_h)
    check(r.status_code == 401 and "firma" in r.text.lower(),
          "con token y clave correcta pero SIN firma no se puede gastar (401)", r.text[:200])

    # Firmo un tope barato y mando otro: el importe autorizado no se puede cambiar.
    nonce = "n-" + os.urandom(6).hex()
    cuerpo = pide(nonce, tope=50.0)
    msg = "cliente:%s:matriz:%s:%s" % (nonce, h_params, 1.0)   # firma de tope 1
    cuerpo["firma"] = firmar(priv_c, msg)
    r = post(cli, "/cliente/tareas", json=cuerpo, headers=cli_h)
    check(r.status_code == 401, "cambiar el tope despues de firmar invalida la firma (401)",
          r.text[:200])

    # ---------- 5. TOPE INSUFICIENTE ------------------------------------
    # El tope se calcula A PARTIR DEL COSTE REAL, no con un numero fijo: con la
    # definicion (1 KIA = 1 minuto de GPU) esta tarea cuesta ~4e-8 KIA y un tope
    # fijo de 1e-7 la dejaba pasar (con el ancla ya pasaba igual y la prueba no
    # lo cazaba porque nunca se habia corrido con ella).
    coste_esta = float(C._coste_tarea_cliente("matriz", params) or 0.0)
    tope_bajo = max(coste_esta / 2.0, 1e-15) if coste_esta > 0 else 1e-15
    nonce = "n-" + os.urandom(6).hex()
    msg = "cliente:%s:matriz:%s:%s" % (nonce, h_params, tope_bajo)
    r = post(cli, "/cliente/tareas", json={"tipo": "matriz", "params": params, "tope": tope_bajo,
                                          "nonce": nonce, "clave_publica": pub_c,
                                          "firma": firmar(priv_c, msg)}, headers=cli_h)
    check(r.status_code == 402 and "tope" in r.text.lower(),
          "un tope por debajo del coste se rechaza (402)", r.text[:200])

    # ---------- 6. FIRMA DE OTRA CLAVE ----------------------------------
    pub_x, priv_x = par()
    nonce = "n-" + os.urandom(6).hex()
    msg = "cliente:%s:matriz:%s:%s" % (nonce, h_params, 50.0)
    r = post(cli, "/cliente/tareas", json={"tipo": "matriz", "params": params, "tope": 50.0,
                                          "nonce": nonce, "clave_publica": pub_c,
                                          "firma": firmar(priv_x, msg)}, headers=cli_h)
    check(r.status_code == 401, "firmar con otra clave se rechaza (401)", r.text[:200])
    r = post(cli, "/cliente/tareas", json={"tipo": "matriz", "params": params, "tope": 50.0,
                                          "nonce": nonce, "clave_publica": pub_x,
                                          "firma": firmar(priv_x, msg)}, headers=cli_h)
    check(r.status_code == 401, "firmar bien pero con una clave que NO es la del cliente se rechaza",
          r.text[:200])

    # ---------- 7. TAREA BUENA: PAGO POR ADELANTADO ---------------------
    nonce = "n-" + os.urandom(6).hex()
    msg = "cliente:%s:matriz:%s:%s" % (nonce, h_params, 50.0)
    r = post(cli, "/cliente/tareas", json={"tipo": "matriz", "params": params, "tope": 50.0,
                                          "nonce": nonce, "clave_publica": pub_c,
                                          "firma": firmar(priv_c, msg)}, headers=cli_h)
    check(r.status_code == 200, "el cliente encola con firma valida", r.text[:300])
    t1 = (r.json() or {}).get("tarea_id")
    coste = float((r.json() or {}).get("coste") or 0.0)
    out["tarea"] = {"id": t1, "coste": coste, "respuesta": r.json()}
    check(coste > 0, "la tarea tiene un coste positivo (%s KIA)" % coste)

    check(abs(saldo(dir_c) - (100.0 - coste)) <= 1e-9,
          "el dinero sale del cliente al encolar", {"saldo": saldo(dir_c), "coste": coste})
    check(saldo(C.CUENTA_ESCROW) == coste,
          "y queda RETENIDO en escrow, no en el bolsillo de nadie", {"escrow": saldo(C.CUENTA_ESCROW)})
    check(metricas()["supply"] == antes["supply"],
          "encolar NO emite dinero nuevo (un prepago no infla el suministro)",
          {"antes": antes["supply"], "ahora": metricas()["supply"]})

    # nonce reutilizado
    r = post(cli, "/cliente/tareas", json={"tipo": "matriz", "params": params, "tope": 50.0,
                                          "nonce": nonce, "clave_publica": pub_c,
                                          "firma": firmar(priv_c, msg)}, headers=cli_h)
    check(r.status_code == 409, "el mismo nonce no se puede usar dos veces (409)", r.text[:200])

    # ---------- 8. UN MINERO LA RESUELVE --------------------------------
    pub_n, priv_n = par()
    dir_n = C.direccion_de_pubkey(pub_n)
    r = post(cli, "/nodos/reto", json={"clave_publica": pub_n})
    reto = r.json()
    r = post(cli, "/nodos/registrar", json={
        "nombre": "minero_prueba", "vram_gb": 12, "tops": 100, "device": "cpu",
        "clave_publica": pub_n, "direccion_cobro": dir_n,
        "reto": reto["nonce"], "firma_reto": firmar(priv_n, reto["mensaje"])})
    check(r.status_code == 200, "el minero de prueba se registra", r.text[:300])
    nid = (r.json() or {}).get("id")

    ts = int(time.time())
    npend = "p" + os.urandom(5).hex()
    r = get(cli, "/tareas/pendiente", params={
        "nodo_id": nid, "ts": ts, "nonce": npend,
        "firma": firmar(priv_n, "pendiente:%s:%s:%s" % (nid, ts, npend))})
    check(r.status_code == 200, "el minero recibe la tarea pendiente", r.text[:300])
    pedida = ((r.json() or {}).get("tarea") or {})
    out["pendiente"] = pedida
    check(pedida.get("id") == t1, "y es justo la tarea que encargo el cliente",
          {"pedida": pedida.get("id"), "cliente": t1})

    # Calculo determinista de la matriz (identico al worker y al Core).
    import numpy as np
    n = 256
    idx = np.arange(n)
    a = ((idx[:, None] * 31 + idx[None, :] * 17 + 3) % 97) - 48
    b = ((idx[:, None] * 31 + idx[None, :] * 17 + 4) % 97) - 48
    metrica = str(int(round(float((a @ b).sum()))))

    r = post(cli, "/tareas/resultado_json", json={
        "tid": t1, "nodo_id": nid, "metrica": metrica,
        "firma": firmar(priv_n, "%s:%s" % (t1, metrica)),
        "device": "cpu", "duracion_ms": 12.0, "telemetria": "{}"})
    check(r.status_code == 200, "el minero entrega el resultado", r.text[:400])
    out["entrega"] = r.json()

    # ---------- 9. COBRO Y RECOGIDA -------------------------------------
    r = get(cli, "/cliente/tareas/%s" % t1, headers=cli_h)
    check(r.status_code == 200, "el cliente recoge su tarea", r.text[:300])
    tarea = r.json()
    out["recogida"] = tarea
    check(tarea.get("estado") == "ok", "la tarea queda en ok", tarea)
    check(tarea.get("resultado") == metrica, "el resultado que recibe es el que calculo el minero")
    pagado = float(tarea.get("pagado") or 0.0)
    check(pagado > 0, "se le pago al minero desde el deposito (%s KIA)" % pagado)

    check(abs(saldo(dir_n) - pagado) <= 1e-9,
          "el minero cobra exactamente lo pagado", {"nodo": saldo(dir_n), "pagado": pagado})
    check(abs(saldo(C.CUENTA_ESCROW) - max(0.0, coste - pagado)) < 1e-6,
          "el escrow de esa tarea queda a cero (lo demas volvio al cliente)",
          {"escrow": saldo(C.CUENTA_ESCROW), "coste": coste, "pagado": pagado})
    check(abs(saldo(dir_c) - (100.0 - pagado)) <= 1e-9,
          "el cliente paga SOLO lo que se trabajo, no el deposito entero",
          {"cliente": saldo(dir_c), "pagado": pagado})
    check(metricas()["supply"] == antes["supply"],
          "el ciclo completo no creo ni destruyo suministro (asientos compensados)",
          {"antes": antes["supply"], "ahora": metricas()["supply"]})

    # ---------- 9b. DEPOSITO MAYOR QUE EL COSTE: VUELVE EL SOBRANTE ------
    # Un cliente puede dejar retenido mas que el coste. El minero cobra su
    # tarifa ENTERA y la diferencia vuelve al cliente: el sobrante sale del
    # deposito, nunca del bolsillo del que trabajo (25-10-2026).
    r = get(cli, "/cliente/precio", headers=cli_h,
            params={"tipo": "matriz", "params": json.dumps(params)})
    check(r.status_code == 200 and "coste" in (r.json() or {}),
          "el precio con params da el coste exacto de la tarea", r.text[:200])
    coste_ex = float((r.json() or {}).get("coste") or 0.0)
    check(abs(coste_ex - coste) <= 1e-12,
          "y coincide con lo que costo la misma tarea",
          {"exacto": coste_ex, "cobrado": coste})

    extra = 5.0
    deposito_ex = round(coste_ex + extra, 12)
    antes3 = saldo(dir_c)
    nonce = "n-" + os.urandom(6).hex()
    msg = "cliente:%s:matriz:%s:%s:%s" % (nonce, h_params, 50.0, deposito_ex)
    r = post(cli, "/cliente/tareas", headers=cli_h,
             json={"tipo": "matriz", "params": params, "tope": 50.0,
                   "deposito": deposito_ex, "nonce": nonce,
                   "clave_publica": pub_c, "firma": firmar(priv_c, msg)})
    check(r.status_code == 200, "se puede depositar MAS que el coste", r.text[:300])
    t3 = (r.json() or {}).get("tarea_id")
    retenido3 = float((r.json() or {}).get("deposito") or 0.0)
    out["deposito"] = {"tarea": t3, "coste": coste_ex, "depositado": retenido3,
                       "respuesta": r.json()}
    check(abs(retenido3 - deposito_ex) <= 1e-12,
          "se retiene el DEPOSITO entero, no el coste",
          {"retenido": retenido3, "deposito": deposito_ex})
    check(abs(saldo(dir_c) - (antes3 - deposito_ex)) <= 1e-9,
          "al cliente se le retiene el deposito", {"saldo": saldo(dir_c)})
    check(abs(saldo(C.CUENTA_ESCROW) - deposito_ex) <= 1e-9,
          "y queda entero en escrow", {"escrow": saldo(C.CUENTA_ESCROW)})
    check(metricas()["supply"] == antes["supply"],
          "depositar no emite dinero", {"supply": metricas()["supply"]})

    # El deposito va firmado: firmar la cadena vieja (sin importe) no vale.
    nonce = "n-" + os.urandom(6).hex()
    msg_vieja = "cliente:%s:matriz:%s:%s" % (nonce, h_params, 50.0)
    r = post(cli, "/cliente/tareas", headers=cli_h,
             json={"tipo": "matriz", "params": params, "tope": 50.0,
                   "deposito": deposito_ex, "nonce": nonce,
                   "clave_publica": pub_c, "firma": firmar(priv_c, msg_vieja)})
    check(r.status_code == 401,
          "el deposito va firmado: firmar sin el importe se rechaza (401)", r.text[:200])

    # Depositar menos que el coste dejaria al minero cobrando de menos.
    corto = round(coste_ex / 4.0, 12)
    nonce = "n-" + os.urandom(6).hex()
    msg_corto = "cliente:%s:matriz:%s:%s:%s" % (nonce, h_params, 50.0, corto)
    r = post(cli, "/cliente/tareas", headers=cli_h,
             json={"tipo": "matriz", "params": params, "tope": 50.0,
                   "deposito": corto, "nonce": nonce,
                   "clave_publica": pub_c, "firma": firmar(priv_c, msg_corto)})
    check(r.status_code == 402 and "deposito" in r.text.lower(),
          "un deposito por debajo del coste se rechaza (el minero cobraria de menos)",
          r.text[:200])

    # El minero resuelve la tercera tarea.
    ts = int(time.time())
    npend = "p" + os.urandom(5).hex()
    r = get(cli, "/tareas/pendiente", params={
        "nodo_id": nid, "ts": ts, "nonce": npend,
        "firma": firmar(priv_n, "pendiente:%s:%s:%s" % (nid, ts, npend))})
    pedida3 = ((r.json() or {}).get("tarea") or {})
    check(pedida3.get("id") == t3, "el minero recibe la tarea con deposito", pedida3.get("id"))
    r = post(cli, "/tareas/resultado_json", json={
        "tid": t3, "nodo_id": nid, "metrica": metrica,
        "firma": firmar(priv_n, "%s:%s" % (t3, metrica)),
        "device": "cpu", "duracion_ms": 12.0, "telemetria": "{}"})
    check(r.status_code == 200, "el minero entrega el resultado", r.text[:300])

    r = get(cli, "/cliente/tareas/%s" % t3, headers=cli_h)
    t3d = r.json() or {}
    pagado3 = float(t3d.get("pagado") or 0.0)
    devuelto3 = round(deposito_ex - pagado3, 12)
    out["sobrante"] = {"pagado": pagado3, "devuelto": devuelto3, "tarea": t3d}
    check(t3d.get("estado") == "ok", "la tarea con deposito queda en ok", t3d)
    check(pagado3 > 0, "se le pago al minero (%s KIA)" % pagado3)
    check(abs(pagado3 - pagado) <= 1e-9,
          "el minero cobra LO MISMO que sin deposito extra (el sobrante no sale de su pago)",
          {"con_deposito": pagado3, "sin_deposito": pagado})
    check(devuelto3 > 0, "hay sobrante que devolver (%s KIA)" % devuelto3)
    check(abs(saldo(dir_c) - (antes3 - pagado3)) <= 1e-9,
          "al cliente le vuelve el sobrante: paga solo lo trabajado",
          {"saldo": saldo(dir_c), "esperado": antes3 - pagado3})
    check(abs(saldo(C.CUENTA_ESCROW)) <= 1e-6,
          "el escrow de esa tarea queda a cero", {"escrow": saldo(C.CUENTA_ESCROW)})
    asientos3 = C.con.execute("SELECT nodo_id, kuki, motivo FROM ledger WHERE tarea_id=? "
                              "ORDER BY id", (t3,)).fetchall()
    dev_as = sum(float(a["kuki"]) for a in asientos3
                 if a["motivo"] == "devolucion sobrante" and float(a["kuki"]) > 0)
    check(abs(dev_as - devuelto3) <= 1e-9,
          "el asiento de devolucion es exactamente el sobrante",
          {"asiento": dev_as, "sobrante": devuelto3})
    check(abs(sum(float(a["kuki"]) for a in asientos3)) <= 1e-9,
          "los asientos del deposito se compensan (ni se crea ni se pierde dinero)",
          {"suma": sum(float(a["kuki"]) for a in asientos3)})
    check(metricas()["supply"] == antes["supply"],
          "con deposito tampoco se emite dinero", {"supply": metricas()["supply"]})

    # ---------- 10. TAREA AJENA -----------------------------------------
    pub_d, priv_d = par()
    dir_d = C.direccion_de_pubkey(pub_d)
    r = post(cli, "/admin/clientes", json={"nombre": "curioso", "direccion": dir_d}, headers=adm)
    tok_d = (r.json() or {}).get("token") or ""
    r = get(cli, "/cliente/tareas/%s" % t1, headers={"X-Cliente-Token": tok_d})
    check(r.status_code == 404, "otro cliente no puede leer tu tarea (404, sin filtrar que existe)",
          r.text[:200])
    r = get(cli, "/cliente/tareas/%s" % t1, headers={"X-Cliente-Token": "inventado"})
    check(r.status_code == 401, "un token inventado no entra (401)", r.text[:200])

    # ---------- 11. DEVOLUCION SI LA TAREA SE CAE -----------------------
    antes2 = saldo(dir_c)
    nonce = "n-" + os.urandom(6).hex()
    msg = "cliente:%s:matriz:%s:%s" % (nonce, h_params, 50.0)
    r = post(cli, "/cliente/tareas", json={"tipo": "matriz", "params": params, "tope": 50.0,
                                          "nonce": nonce, "clave_publica": pub_c,
                                          "firma": firmar(priv_c, msg)}, headers=cli_h)
    t2 = (r.json() or {}).get("tarea_id")
    coste2 = float((r.json() or {}).get("coste") or 0.0)
    check(r.status_code == 200 and abs(saldo(dir_c) - (antes2 - coste2)) <= 1e-9,
          "segunda tarea: retenida igual", {"saldo": saldo(dir_c), "coste": coste2})
    ts = int(time.time())
    npend = "p" + os.urandom(5).hex()
    get(cli, "/tareas/pendiente", params={
        "nodo_id": nid, "ts": ts, "nonce": npend,
        "firma": firmar(priv_n, "pendiente:%s:%s:%s" % (nid, ts, npend))})
    # El nodo desaparece: el Core la cierra como fallo y devuelve el deposito.
    rec, cer = C.liberar_atascadas(max_abandonos=1, forzar=True)
    f = C.con.execute("SELECT estado,prepago FROM tareas WHERE id=?", (t2,)).fetchone()
    out["devolucion"] = {"recuperadas": rec, "cerradas": cer, "estado": dict(f) if f else None}
    check(f is not None and f["estado"] == "fallo", "si el nodo abandona, la tarea se cierra (fallo)")
    check(saldo(dir_c) == antes2, "y el dinero retenido VUELVE al cliente",
          {"antes": antes2, "ahora": saldo(dir_c), "coste": coste2})
    check(saldo(C.CUENTA_ESCROW) == 0.0, "el escrow queda a cero: no se pierde dinero",
          {"escrow": saldo(C.CUENTA_ESCROW)})
    check(float(f["prepago"] or 0.0) == 0.0, "no se puede devolver dos veces (prepago ya a 0)")

    # ---------- 12. EL TOKEN NO DA ADMIN ---------------------------------
    r = get(cli, "/admin/clientes", headers=cli_h)
    check(r.status_code == 403, "el token de cliente no sirve como admin (403)", r.text[:200])

    # ---------- 12b. BAJA MASIVA SIN BORRAR NADA -------------------------
    # Dar de baja = activo=0. No se borra un asiento: la cadena de hashes y los
    # sellos seguirian valiendo aunque se editara la base, pero borrar filas los
    # rompe, asi que aqui no se borra nada (25-10-2026).
    cid = (out.get("cliente", {}).get("alta") or {}).get("id")
    r = post(cli, "/admin/clientes/baja", json={"todos": True})
    check(r.status_code == 403, "la baja masiva exige token de admin (403)", r.text[:200])
    r = post(cli, "/admin/clientes/baja", json={}, headers=adm)
    check(r.status_code == 400, "sin decir a quien, no se da de baja a nadie (400)", r.text[:200])

    asientos0 = C.con.execute("SELECT COUNT(*) n FROM ledger").fetchone()["n"]
    supply0 = round(C.supply_emitido(), 9)
    r = post(cli, "/admin/clientes/baja", json={"ids": [cid]}, headers=adm)
    check(r.status_code == 200 and (r.json() or {}).get("seco") is True,
          "por defecto la baja es una vista previa: no escribe", r.text[:300])
    fila = C.con.execute("SELECT activo FROM clientes WHERE id=?", (cid,)).fetchone()
    check(fila is not None and int(fila["activo"]) == 1,
          "tras la vista previa el cliente sigue activo", dict(fila) if fila else None)

    r = post(cli, "/admin/clientes/baja", json={"ids": [cid], "seco": False}, headers=adm)
    bajada = r.json() or {}
    check(r.status_code == 200 and len(bajada.get("bajadas") or []) == 1,
          "con seco=false se da de baja", r.text[:300])
    fila = C.con.execute("SELECT activo FROM clientes WHERE id=?", (cid,)).fetchone()
    check(fila is not None and int(fila["activo"]) == 0, "el cliente queda activo=0")
    check(int(bajada.get("asientos_borrados", -1)) == 0, "el Core declara cero asientos borrados")
    asientos1 = C.con.execute("SELECT COUNT(*) n FROM ledger").fetchone()["n"]
    check(asientos1 == asientos0, "el ledger conserva TODOS sus asientos",
          {"antes": asientos0, "despues": asientos1})
    check(round(C.supply_emitido(), 9) == supply0, "el suministro no cambia con una baja")
    fila = C.con.execute("SELECT COUNT(*) n FROM tareas WHERE cliente=?", (dir_c,)).fetchone()
    check(int(fila["n"]) >= 2, "sus tareas siguen ahi (no se borra el historial)",
          {"tareas": int(fila["n"])})

    # Con la baja, el token deja de valer al instante.
    r = get(cli, "/cliente/saldo", headers=cli_h)
    check(r.status_code == 401, "el token de un cliente dado de baja ya no entra (401)",
          r.text[:200])

    # Y es reversible: la misma billetera se puede volver a dar de alta.
    r = post(cli, "/admin/clientes", json={"nombre": "empresa de prueba",
                                           "direccion": dir_c}, headers=adm)
    check(r.status_code == 200, "se puede volver a dar de alta con la misma billetera",
          r.text[:200])
    tok2 = (r.json() or {}).get("token") or ""
    # El alta crea una fila NUEVA (id nuevo) con la misma billetera.
    cid2 = (r.json() or {}).get("id")
    cli_h2 = {"X-Cliente-Token": tok2}

    # Quien tiene tareas en curso no se da de baja sin querer.
    params = C.validar_params("matriz", {"n": 256, "seed": 3})
    h2 = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
    nonce = "n-" + os.urandom(6).hex()
    msg = "cliente:%s:matriz:%s:%s" % (nonce, h2, 50.0)
    r = post(cli, "/cliente/tareas", headers=cli_h2,
             json={"tipo": "matriz", "params": params, "tope": 50.0, "nonce": nonce,
                   "clave_publica": pub_c, "firma": firmar(priv_c, msg)})
    t4 = (r.json() or {}).get("tarea_id")
    check(r.status_code == 200, "tras el alta puede volver a pedir trabajo", r.text[:200])
    r = post(cli, "/admin/clientes/baja", json={"ids": [cid2], "seco": False}, headers=adm)
    check(r.status_code == 200 and not (r.json() or {}).get("bajadas"),
          "con una tarea en curso se SALTA la baja", r.text[:300])
    check(len((r.json() or {}).get("saltados") or []) == 1, "y se avisa de por que")
    r = post(cli, "/admin/clientes/baja", json={"ids": [cid2], "seco": False,
                                                "forzar": True}, headers=adm)
    check(r.status_code == 200 and len((r.json() or {}).get("bajadas") or []) == 1,
          "con forzar=True si se da de baja (decision explicita)", r.text[:300])
    out["baja"] = {"bajada": bajada, "tarea_en_curso": t4,
                   "asientos": {"antes": asientos0, "despues": asientos1}}

out["fallos"] = fallos
out["ok"] = not fallos
print("RESULTADO:" + json.dumps(out))
'''


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="kukia_cliente_"))
    try:
        (tmp / "coordinator").mkdir()
        (tmp / "ledger").mkdir()
        shutil.copy2(RAIZ / "coordinator" / "kukia_core.py", tmp / "coordinator")
        for f in ("sellos.py", "kukia_wallet.py", "wordlist_es.txt"):
            origen = RAIZ / "ledger" / f
            if origen.exists():
                shutil.copy2(origen, tmp / "ledger")
        (tmp / "runner.py").write_text(RUNNER, encoding="utf-8")
        entorno = dict(
            {k: v for k, v in os.environ.items() if not k.startswith("KUKIA_")},
            KUKIA_DB=str(tmp / "coordinator" / "ledger" / "kukia.db"),
            KUKIA_AUTO_TAREAS="0",
            KUKIA_REPLICA="0",
            PYTHONIOENCODING="utf-8",
        )
        r = subprocess.run([sys.executable, "runner.py"], cwd=str(tmp), env=entorno,
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            print(r.stdout)
            print(r.stderr)
            raise SystemExit("[prueba] el Core temporal fallo")
        datos = None
        for linea in r.stdout.splitlines():
            if linea.startswith("RESULTADO:"):
                datos = json.loads(linea[len("RESULTADO:"):])
        if datos is None:
            print(r.stdout)
            print(r.stderr)
            raise SystemExit("[prueba] sin resultado del runner")

        for texto, ok in datos["pasos"].items():
            print(("[ OK ] " if ok else "[FALLO] ") + texto)
        print()
        for fallo in datos["fallos"]:
            print("[FALLO] %s -> %s" % (fallo["texto"], fallo.get("extra")))
        if datos["ok"]:
            print("[prueba] todo OK")
            return 0
        print("[prueba] %d FALLO(S)" % len(datos["fallos"]))
        print(json.dumps(datos, indent=2, ensure_ascii=False)[:4000])
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
