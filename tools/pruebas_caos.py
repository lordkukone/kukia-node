#!/usr/bin/env python3
"""Pruebas de CAOS: lo que una suite secuencial NO puede ver.

La bateria normal (tools/pruebas_seguridad.py) hace las cosas de una en una.
Aqui se ataca lo que solo rompe cuando pasa a la vez o cuando se corta la luz:

  A. DOBLE GASTO EN PARALELO   N transferencias simultaneas del mismo saldo.
                               Como maximo una puede cobrar; el saldo nunca
                               queda negativo y el suministro no cambia.
  B. DOBLE PAGO EN PARALELO    El mismo resultado entregado N veces a la vez.
                               Se paga UNA vez y se escribe UN asiento.
  C. CORTE DE LUZ              kill -9 (SIGKILL, como un apagon) con dinero en
                               vuelo. Al rearrancar: cadena intacta, sellos
                               bien, suministro = suma de saldos y CERO medios
                               asientos (un debito sin su credito).
  D. BASURA                    Cuerpos malformados, tipos cambiados, NaN,
                               rutas con ../ y un cuerpo gigante. Ni un 500.
  E. REPLAY TRAS EL CORTE      El nonce de una transferencia ya cobrada sigue
                               bloqueado despues del reinicio.
  F. HOST                      El tunel de Cloudflare entra; lo ajeno no.
  G. DISCO LLENO               SQLite responde "database or disk is full" en
                               mitad de la faena: ni dinero a medias, ni una
                               tarea marcada 'ok' sin pagar, ni un sello
                               cortado que bloquee los pagos al reiniciar.

Siempre sobre una COPIA del ledger y un Core aislado en un puerto libre:
no toca la red real.

Uso:
    python tools/pruebas_caos.py            (sale 0 si todo pasa)
    python tools/pruebas_caos.py --dejar    (no borra la carpeta temporal)
"""
import argparse
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

HERRAMIENTAS = Path(__file__).resolve().parent
sys.path.insert(0, str(HERRAMIENTAS))
# Se reutiliza el arnes ya probado (copia coherente del ledger y utilidades).
import pruebas_seguridad as PS  # noqa: E402

check = PS.check


def clave_nueva():
    """(seed_hex, pub_hex, direccion) de una billetera nueva."""
    k = Ed25519PrivateKey.generate()
    seed = k.private_bytes(serialization.Encoding.Raw,
                           serialization.PrivateFormat.Raw,
                           serialization.NoEncryption()).hex()
    pub = k.public_key().public_bytes(serialization.Encoding.Raw,
                                      serialization.PublicFormat.Raw).hex()
    return seed, pub, PS.dir_de(pub)


def entorno_core(tmp: Path, token: str) -> dict:
    e = dict(os.environ)
    e.update({
        "KUKIA_DB": str(tmp / "kukia.db"),
        "KUKIA_SELLO_KEY": str(tmp / "sello_key.txt"),
        "KUKIA_SELLOS_FILE": str(tmp / "sellos.jsonl"),
        "KUKIA_ADMIN_TOKEN": str(tmp / "admin_token.txt"),
        "KUKIA_AUTO_TAREAS": "0",
        "KUKIA_ARCHIVAR": "0",
        "KUKIA_SELLO_SEG": "3600",
        "KUKIA_SELLO_BLOQUEA": "1",
        "KUKIA_REGISTRO_CUPO_HORA": "50",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        # Si el proceso se cae por un fallo nativo (no una excepcion de Python),
        # que lo cuente en el log en vez de morirse en silencio: es lo que hacia
        # que un Core desaparecido pareciera "peticiones que fallan".
        "PYTHONFAULTHANDLER": "1",
    })
    return e


def arrancar(tmp: Path, entorno: dict, puerto: int):
    archivo = open(tmp / "core.log", "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "kukia_core:app",
         "--host", "127.0.0.1", "--port", str(puerto)],
        cwd=str(PS.COORD), env=entorno, stdout=archivo, stderr=subprocess.STDOUT)
    return proc, archivo


def esperar(base: str, proc, limite=240.0) -> bool:
    """El Core esta listo cuando /stats responde 200 (no cuando abre el puerto)."""
    t0 = time.time()
    while time.time() - t0 < limite:
        if proc.poll() is not None:
            return False
        try:
            if requests.get(f"{base}/stats", timeout=3).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def arrancar_vivo(tmp: Path, entorno: dict, proc=None, log=None,
                  intentos: int = 2):
    """Arranca el Core y espera a que conteste. Devuelve (proc, log, puerto, base).

    POR QUE REINTENTA (medido el 25-10-2026)
      Una de cada cuatro pasadas de la suite perdia un Core al arrancarlo:
      el proceso se moria con codigo -1 y el `core.log` quedaba VACIO (ni una
      linea: no llego a importar). Todas las comprobaciones siguientes fallaban
      en cascada y parecia que el fallo estaba en el Core.
      Solo se reintenta en ese caso exacto -el hijo no escribio NADA-, que no
      puede ser un fallo del Core (el Core siempre imprime algo al importar).
      Si el hijo escribio algo y se murio, se deja morir: eso si es un fallo
      de verdad y se ensena el log.
    """
    for intento in range(1, intentos + 1):
        antes = 0
        if proc is not None:
            matar(proc)
            try:
                if log is not None:
                    log.close()
            except Exception:
                pass
        ruta_log = tmp / "core.log"
        try:
            antes = ruta_log.stat().st_size
        except Exception:
            antes = 0
        puerto = PS.puerto_libre()
        base = f"http://127.0.0.1:{puerto}"
        proc, log = arrancar(tmp, entorno, puerto)
        if esperar(base, proc):
            return proc, log, puerto, base
        try:
            log.flush()
            despues = ruta_log.stat().st_size
        except Exception:
            despues = antes
        if despues > antes:
            return proc, log, puerto, base      # el Core dijo algo: fallo de verdad
        if intento < intentos:
            print("  (el Core no llego a arrancar y no escribio nada; "
                  "se reintenta en otro puerto)", flush=True)
    return proc, log, puerto, base


def matar(proc) -> None:
    """SIGKILL: nada de cierre ordenado (equivale a tirar del cable)."""
    try:
        proc.kill()
        proc.wait(timeout=30)
    except Exception:
        pass


def en_paralelo(funciones):
    """Lanza todas a la vez, con barrera, y devuelve sus resultados."""
    barrera = threading.Barrier(len(funciones))
    salida = [None] * len(funciones)

    def envuelve(i, f):
        barrera.wait()
        try:
            salida[i] = f()
        except Exception as e:      # una excepcion cuenta como fallo, no revienta
            salida[i] = e

    hilos = [threading.Thread(target=envuelve, args=(i, f))
             for i, f in enumerate(funciones)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(180)
    return salida


def estado_bd(tmp: Path) -> dict:
    """Lee la copia directamente: suministro, saldos y cadena."""
    con = sqlite3.connect(str(tmp / "kukia.db"), timeout=20)
    try:
        sup = con.execute("SELECT COALESCE(SUM(kuki),0) FROM ledger").fetchone()[0]
        suma_saldos = con.execute("SELECT COALESCE(SUM(total),0) FROM saldos_cuenta").fetchone()[0]
        # -1e-13 es polvo, no deuda: cuenta como cero (el Core lo normaliza).
        negativos = con.execute(
            "SELECT COUNT(*) FROM saldos_cuenta WHERE total < -1e-6").fetchone()[0]
        return {"supply": float(sup or 0.0),
                "suma_saldos": float(suma_saldos or 0.0),
                "negativos": int(negativos)}
    finally:
        con.close()


def transferir(base, de, a, monto, nonce, seed, pub=""):
    """Transferencia desde una BILLETERA: hace falta la clave publica."""
    mensaje = f"transferir:{de}:{a}:{monto}:{nonce}"
    return requests.post(f"{base}/transferir", timeout=90, json={
        "de_nodo": de, "a_direccion": a, "monto": monto,
        "nonce": nonce, "clave_publica": pub,
        "firma": PS.firmar(seed, mensaje)})


# ============================================================
#  A. DOBLE GASTO EN PARALELO
# ============================================================
def prueba_doble_gasto(base, tmp) -> None:
    print("== A. DOBLE GASTO: 8 transferencias a la vez del mismo saldo ==")
    mejor = PS.elegir_cartera(base)
    if not mejor:
        check("hay alguna billetera con saldo para probar", False, "ninguna con clave valida")
        return
    saldo0, de, seed, pub, fichero = mejor
    check("billetera de prueba con saldo suficiente", saldo0 > 1.0,
          f"{fichero}: {saldo0:.6f} KIA")
    if saldo0 <= 1.0:
        return
    _s2, _p2, destino = clave_nueva()
    # 60 % del saldo: solo cabe UNA. Si el Core aceptara dos, el saldo
    # quedaria negativo y el dinero se habria duplicado.
    monto = round(saldo0 * 0.6, 4)
    supply0 = float(requests.get(f"{base}/stats", timeout=30).json()["supply_emitido"])
    nonces = [secrets.token_hex(16) for _ in range(8)]

    respuestas = en_paralelo([
        (lambda n=n: transferir(base, de, destino, monto, n, seed, pub)) for n in nonces])

    codigos = []
    for r in respuestas:
        codigos.append(getattr(r, "status_code", f"excepcion:{r}"))
    ok = [c for c in codigos if c == 200]
    malos = [c for c in codigos if c not in (200, 409)]
    check("ninguna transferencia simultanea rompe el Core (200/409, nada mas)",
          not malos, f"codigos={codigos}")
    check("solo UNA de las 8 cobra (doble gasto imposible)", len(ok) == 1,
          f"{len(ok)} aceptadas de {len(codigos)}")

    saldo_final = float(requests.get(f"{base}/saldo/{destino}", timeout=30).json()["saldo"])
    check("el destino recibe exactamente un monto", abs(saldo_final - monto) < 1e-6,
          f"{saldo_final} KIA")
    saldo_origen = float(requests.get(f"{base}/saldo/{de}", timeout=30).json()["saldo"])
    check("el origen se queda con el resto (ni negativo ni duplicado)",
          abs((saldo0 - monto) - saldo_origen) < 1e-6,
          f"{saldo0} -> {saldo_origen}")
    supply1 = float(requests.get(f"{base}/stats", timeout=30).json()["supply_emitido"])
    check("el suministro no cambia (una transferencia no acuna)", abs(supply0 - supply1) < 1e-6,
          f"{supply0} -> {supply1}")
    e = estado_bd(tmp)
    check("saldos materializados cuadran con el ledger", abs(e["supply"] - e["suma_saldos"]) < 1e-3,
          f"ledger={e['supply']:.6f} saldos={e['suma_saldos']:.6f}")
    check("ninguna cuenta queda en negativo", e["negativos"] == 0, f"{e['negativos']} negativas")
    integ = requests.get(f"{base}/ledger/verificar", timeout=180).json()
    check("la cadena sigue intacta tras el acoso", integ.get("cadena_ok") is True,
          str(integ.get("ids_alterados"))[:160])


# ============================================================
#  B. DOBLE PAGO EN PARALELO
# ============================================================
def registrar_nodo(base, admin, dir_cobro, nombre="nodo-caos", vram=12, tops=300.0):
    seed, pub, _d = clave_nueva()
    reto = requests.post(f"{base}/nodos/reto", timeout=30,
                         json={"clave_publica": pub}).json()
    r = requests.post(f"{base}/nodos/registrar", timeout=30, json={
        "nombre": nombre, "vram_gb": vram, "tops": tops,
        "clave_publica": pub, "direccion_cobro": dir_cobro,
        "reto": reto.get("nonce", ""),
        "firma_reto": PS.firmar(seed, reto.get("mensaje") or "")})
    nid = (r.json() or {}).get("id") if r.status_code == 200 else None
    return nid, seed, pub


def prueba_doble_pago(base, tmp, admin) -> None:
    print("\n== B. DOBLE PAGO: el mismo resultado entregado 6 veces a la vez ==")
    _s, _p, dir_cobro = clave_nueva()
    nid, seed, _pub = registrar_nodo(base, admin, dir_cobro, "nodo-caos-pago")
    check("nodo de prueba registrado", bool(nid), str(nid))
    if not nid:
        return
    # Con historial el cupo es 4; para esta prueba basta una tarea.
    con = sqlite3.connect(str(tmp / "kukia.db"), timeout=20)
    con.execute("UPDATE nodos SET tareas_ok=1 WHERE id=?", (nid,))
    con.commit()
    con.close()

    datos = "kukia-caos-" + secrets.token_hex(8)
    rt = requests.post(f"{base}/tareas/crear", headers=admin, timeout=30, json={
        "tipo": "checksum", "params": {"data": datos}, "cola": "A",
        "prioridad": 1e9, "campana": "pruebas-caos"})
    tid = (rt.json() or {}).get("tarea_id") if rt.status_code == 200 else None
    check("el admin crea la tarea", bool(tid), rt.text[:140])
    if not tid:
        return
    ts = int(time.time())
    pend = requests.get(f"{base}/tareas/pendiente", timeout=60, params={
        "nodo_id": nid, "colas": "A", "ts": ts, "solo_primaria": 1,
        "firma": PS.firmar(seed, f"pendiente:{nid}:{ts}")}).json()
    tarea = pend.get("tarea") or {}
    check("el nodo recibe esa tarea", tarea.get("id") == tid, str(tarea.get("id"))[:12])
    if tarea.get("id") != tid:
        return
    metrica = hashlib.sha256(datos.encode()).hexdigest()
    firma = PS.firmar(seed, f"{tid}:{metrica}")

    def entrega():
        return requests.post(f"{base}/tareas/resultado", timeout=180, params={
            "tid": tid, "nodo_id": nid, "metrica": metrica, "firma": firma,
            "device": "cpu", "duracion_ms": 5.0})

    respuestas = en_paralelo([entrega for _ in range(6)])
    codigos = [getattr(r, "status_code", f"excepcion:{r}") for r in respuestas]
    pagadas = []
    for r in respuestas:
        if getattr(r, "status_code", None) == 200:
            try:
                if (r.json() or {}).get("ok") is True:
                    pagadas.append(float((r.json() or {}).get("kuki") or 0.0))
            except Exception:
                pass
    check("nada devuelve 500 al entregar a la vez (200/409)",
          all(c in (200, 409) for c in codigos), f"codigos={codigos}")
    check("solo una entrega se paga", len(pagadas) == 1,
          f"{len(pagadas)} pagos de {len(codigos)} entregas")
    con = sqlite3.connect(str(tmp / "kukia.db"), timeout=20)
    try:
        asientos = con.execute(
            "SELECT COUNT(*) FROM ledger WHERE tarea_id=? AND motivo LIKE 'trabajo verificado%'",
            (tid,)).fetchone()[0]
        pagado = con.execute("SELECT COALESCE(SUM(kuki),0) FROM ledger WHERE tarea_id=?",
                             (tid,)).fetchone()[0]
        estado = con.execute("SELECT estado FROM tareas WHERE id=?", (tid,)).fetchone()[0]
    finally:
        con.close()
    check("un solo asiento de pago en el ledger", asientos == 1, f"{asientos} asientos")
    check("lo pagado coincide con lo anotado una sola vez",
          (not pagadas) or abs(float(pagado or 0.0) - pagadas[0]) < 1e-9,
          f"ledger={pagado} respuesta={pagadas}")
    check("la tarea queda en estado 'ok'", estado == "ok", str(estado))


# ============================================================
#  C. CORTE DE LUZ (SIGKILL) CON DINERO EN VUELO
# ============================================================
def prueba_corte(base, tmp, entorno, puerto, proc, log, admin):
    print("\n== C. CORTE DE LUZ: kill -9 con 40 transferencias en vuelo ==")
    mejor = PS.elegir_cartera(base)
    if not mejor:
        check("hay billetera para el corte", False)
        return proc, log, puerto, base
    saldo0, de, seed, pub, _f = mejor
    _s, _p, destino = clave_nueva()
    monto = 1.0
    nonces = [secrets.token_hex(16) for _ in range(40)]
    antes = estado_bd(tmp)

    # Una transferencia ANTES del corte: garantiza que hay un nonce cobrado y
    # que se puede probar el anti-replay despues de rearrancar.
    previa = transferir(base, de, destino, monto, nonces[0], seed, pub)
    check("transferencia previa al corte cobrada", previa.status_code == 200,
          previa.text[:120])
    resto = nonces[1:]

    def envia(n):
        try:
            return transferir(base, de, destino, monto, n, seed, pub)
        except Exception as e:
            return e

    hilos = []
    barrera = threading.Barrier(len(resto))
    for n in resto:
        def cuerpo(n=n):
            barrera.wait()
            return envia(n)
        h = threading.Thread(target=cuerpo)
        h.start()
        hilos.append(h)
    # Corte a media faena: unos habran commiteado, otros estaran escribiendo.
    # 120 ms es lo justo para que el SIGKILL caiga con transacciones abiertas.
    time.sleep(0.12)
    matar(proc)
    for h in hilos:
        h.join(30)
    try:
        log.close()
    except Exception:
        pass
    print("   Core muerto a lo bruto; rearrancando sobre el MISMO .db...")

    proc, log, puerto, base = arrancar_vivo(tmp, entorno, proc, log)
    vivo = esperar(base, proc)
    check("el Core rearranca solo tras un corte seco", vivo, f"puerto {puerto}")
    if not vivo:
        _volcar_log(tmp, log, proc)
        return proc, log, puerto, base

    integ = requests.get(f"{base}/ledger/verificar", timeout=180).json()
    check("cadena del ledger intacta tras el corte", integ.get("cadena_ok") is True,
          str(integ.get("ids_alterados"))[:160])
    completo = requests.get(f"{base}/ledger/integridad", timeout=180).json()
    check("sellos siguen cuadrando", completo.get("sellos_ok") is True,
          str(completo.get("problemas"))[:160])
    check("los pagos no quedan bloqueados", completo.get("pagos_bloqueados") is False)

    despues = estado_bd(tmp)
    check("suministro = suma de saldos (no hay dinero suelto)",
          abs(despues["supply"] - despues["suma_saldos"]) < 1e-3,
          f"ledger={despues['supply']:.6f} saldos={despues['suma_saldos']:.6f}")
    check("suministro no cambia con el corte (no se acuna ni se evapora)",
          abs(antes["supply"] - despues["supply"]) < 1e-6,
          f"{antes['supply']:.6f} -> {despues['supply']:.6f}")
    check("ninguna cuenta en negativo", despues["negativos"] == 0)

    # MEDIO ASIENTO = debito sin credito. Se mira nonce por nonce.
    con = sqlite3.connect(str(tmp / "kukia.db"), timeout=20)
    try:
        movies = con.execute(
            "SELECT nonce, monto FROM transferencias WHERE nonce IN (%s)"
            % ",".join("?" * len(nonces)), nonces).fetchall()
        cobradas = {n: float(m) for n, m in movies}
        huerfanos = []
        for n in nonces:
            # El id del asiento lleva el nonce ENTERO desde el 23-10-2026
            # (antes se cortaba a 12 y dos transferencias podian compartir id).
            # Se aceptan las dos formas para no romper con bases antiguas.
            movs = con.execute(
                "SELECT COUNT(*) FROM ledger WHERE tarea_id IN (?,?)",
                (f"tx-{n}", f"tx-{n[:12]}")).fetchone()[0]
            if n in cobradas and movs != 2:
                huerfanos.append((n[:8], "cobrada con %d asientos" % movs))
            if n not in cobradas and movs != 0:
                huerfanos.append((n[:8], "asientos sin transferencia"))
        estado_tx = con.execute(
            "SELECT COUNT(*) FROM transferencias WHERE nonce IN (%s)"
            % ",".join("?" * len(nonces)), nonces).fetchone()[0]
    finally:
        con.close()
    check("cero medios asientos (cada debito tiene su credito)", not huerfanos,
          str(huerfanos[:4]))
    print(f"   commitearon antes del corte: {estado_tx} de {len(nonces)}")
    if 0 < estado_tx < len(nonces):
        print("   (corte a mitad de faena de verdad: parte entro, parte no)")

    # E. REPLAY TRAS EL CORTE: un nonce ya cobrado sigue gastado.
    # Si el Core se ha muerto, esto lo dice como FALLO con el motivo (antes
    # reventaba la suite entera con un traceback y no se veia nada mas).
    def pide(que: str, fn):
        try:
            return fn()
        except Exception as e:
            check(que, False, f"no se pudo preguntar al Core ({type(e).__name__}: {e})"[:160])
            return None

    if cobradas:
        n = next(iter(cobradas))
        r = pide("nonce ya cobrado sigue bloqueado tras reiniciar (anti-replay persistente)",
                 lambda: transferir(base, de, destino, monto, n, seed, pub))
        if r is not None:
            check("nonce ya cobrado sigue bloqueado tras reiniciar (anti-replay persistente)",
                  r.status_code == 409, r.text[:120])
    saldo = pide("el destino tiene justo lo que dicen las transferencias cobradas",
                 lambda: requests.get(f"{base}/saldo/{destino}", timeout=30))
    if saldo is not None:
        saldo_final = float(saldo.json()["saldo"])
        check("el destino tiene justo lo que dicen las transferencias cobradas",
              abs(saldo_final - monto * estado_tx) < 1e-6,
              f"{saldo_final} KIA por {estado_tx} transferencias")

    # Las que NO entraron no pueden haber dejado el nonce quemado: el dinero
    # sigue en el origen y se tiene que poder reintentar.
    sin_cobrar = [n for n in nonces if n not in cobradas]
    if sin_cobrar:
        r = pide("una transferencia que no entro se puede reintentar (nonce libre)",
                 lambda: transferir(base, de, destino, monto, sin_cobrar[0], seed, pub))
        if r is not None:
            check("una transferencia que no entro se puede reintentar (nonce libre)",
                  r.status_code == 200, r.text[:120])
    return proc, log, puerto, base


# ============================================================
#  D. BASURA: ni un solo 500
# ============================================================
def prueba_basura(base, admin) -> None:
    print("\n== D. BASURA: cuerpos malformados contra las rutas publicas ==")
    grandes = "A" * 100_000
    casos = [
        ("POST", "/transferir", {"de_nodo": "", "a_direccion": "", "monto": 0}),
        ("POST", "/transferir", {"de_nodo": "x", "a_direccion": "y", "monto": "mucho"}),
        ("POST", "/transferir", {"de_nodo": [], "a_direccion": {}, "monto": None}),
        ("POST", "/transferir", {"de_nodo": "KUK" + "A" * 200, "a_direccion": "KUK1",
                                 "monto": 1, "nonce": grandes[:200], "firma": "zz"}),
        ("POST", "/transferir", {"de_nodo": "a", "a_direccion": "b", "monto": 1e308,
                                 "nonce": "n", "firma": "00"}),
        ("POST", "/nodos/registrar", {}),
        ("POST", "/nodos/registrar", {"nombre": grandes, "vram_gb": "doce", "tops": "x"}),
        ("POST", "/nodos/registrar", {"nombre": "n", "vram_gb": -5, "tops": -1,
                                      "clave_publica": "zz", "direccion_cobro": grandes}),
        ("POST", "/nodos/reto", {}),
        ("POST", "/nodos/reto", {"clave_publica": grandes[:300]}),
        ("POST", "/nodos/telemetria", {}),
        ("POST", "/nodos/telemetria", {"nodo_id": grandes, "device": grandes,
                                       "metricas": grandes, "ts": "x", "firma": "00"}),
        ("POST", "/tareas/resultado", {"tid": "no-existe"}),
        ("POST", "/tareas/replica", {}),
        ("POST", "/validador/atestacion", {}),
        ("POST", "/registro", {"nombre": grandes, "email": grandes, "ref": grandes}),
        ("GET", "/saldo/" + "K" * 500, None),
        ("GET", "/saldo/%00%01%02", None),
        ("GET", "/modelos/..%2F..%2Fcoordinator%2Fadmin_token.txt", None),
        ("GET", "/modelos/" + "z" * 500, None),
        ("GET", "/tareas/pendiente?nodo_id=&ts=abc&firma=zz&nonce=", None),
        ("GET", "/ledger?limite=abc", None),
        ("GET", "/nodos?limite=-1", None),
        ("GET", "/red/serie?minutos=999999999", None),
        ("GET", "/red/serie_gpu?minutos=-5", None),
        ("GET", "/red/witness?n=abc", None),
        ("POST", "/admin/archivar", {}),
    ]
    fallos = []
    for metodo, ruta, cuerpo in casos:
        try:
            if metodo == "GET":
                r = requests.get(f"{base}{ruta}", timeout=60)
            else:
                r = requests.post(f"{base}{ruta}", json=cuerpo, timeout=60)
            if r.status_code >= 500:
                fallos.append((metodo, ruta[:60], r.status_code, r.text[:100]))
        except Exception as e:
            fallos.append((metodo, ruta[:60], "excepcion", str(e)[:100]))
    check("ni una sola basura provoca un 500", not fallos, str(fallos[:4]))

    # Travesia de rutas: el token de admin NO se sirve nunca.
    r = requests.get(f"{base}/modelos/..%2F..%2Fcoordinator%2Fadmin_token.txt", timeout=30)
    check("no se puede bajar el token de admin por /modelos (travesia de rutas)",
          r.status_code == 404, f"{r.status_code} {r.text[:80]}")
    # Cuerpo gigante: se corta antes de procesarlo.
    r = requests.post(f"{base}/transferir", data=grandes * 20,
                      headers={"Content-Type": "application/json"}, timeout=60)
    check("cuerpo gigante cortado (413), no un 500 ni un cuelgue",
          r.status_code in (413, 400, 422), f"{r.status_code}")
    # Admin sin token: cerrado.
    r = requests.post(f"{base}/tareas/crear", json={"tipo": "checksum",
                                                    "params": {"data": "x"}}, timeout=30)
    check("sin token de admin no se crean tareas", r.status_code == 403,
          f"{r.status_code}")


def prueba_host(base) -> None:
    """ENSAYO DE FAILOVER 2026-10-22: el tunel publico recibia 403.

    El Core aceptaba solo loopback + KUKIA_HOSTS, asi que al arrancar con la
    replica (o con un tunel nuevo) todo lo publico devolvia 403 "Host no
    permitido" y la red parecia caida. Ahora los dominios de los tuneles de
    Cloudflare valen por sufijo, y lo que no es tunel sigue cortado.
    """
    print("\n== F. HOST: el tunel publico entra y lo ajeno no ==")
    casos = [
        ("un tunel de Cloudflare entra (Host: abc.trycloudflare.com)",
         "abc-def.trycloudflare.com", 200),
        ("un dominio normal NO entra (Host: evil.example)", "evil.example", 403),
        ("un nombre que solo ACABA igual NO entra (nottrycloudflare.com)",
         "nottrycloudflare.com", 403),
        ("loopback sigue entrando", "127.0.0.1", 200),
    ]
    for nombre, host, esperado in casos:
        try:
            r = requests.get(f"{base}/stats", headers={"Host": host}, timeout=30)
            check(nombre, r.status_code == esperado,
                  f"{r.status_code} (esperaba {esperado})")
        except Exception as e:
            check(nombre, False, str(e)[:100])


def _paginas(db: Path) -> int:
    con = sqlite3.connect(str(db), timeout=20)
    try:
        return int(con.execute("PRAGMA page_count").fetchone()[0])
    finally:
        con.close()


def _cuenta(db: Path) -> tuple:
    """(paginas, paginas_libres) de la base."""
    con = sqlite3.connect(str(db), timeout=30)
    try:
        return (int(con.execute("PRAGMA page_count").fetchone()[0]),
                int(con.execute("PRAGMA freelist_count").fetchone()[0]))
    finally:
        con.close()


def _llenar_bd(db: Path, hasta_paginas: int) -> tuple:
    """Llena la base hasta el tope, desde FUERA del Core.

    El Core corre con `KUKIA_DB_MAX_PAGINAS` (tope de paginas), asi que en
    cuanto el fichero llega al tope cualquier escritura suya devuelve
    SQLITE_FULL, igual que un disco lleno de verdad. Se rellena con una tabla
    de basura en una conexion aparte para no gastar peticiones ni tiempo.

    Se llama solo con el Core QUIETO (auto-tareas apagadas y sin trafico en
    vuelo), asi que durante el relleno la unica conexion que escribe es esta:
    el escenario no depende de quien gane el bloqueo entre conexiones, ni de
    cuanto tarde el Core en soltar la suya.

    OJO (28-10-2026): no basta con mirar `page_count`. La base guarda una
    lista de paginas libres (freelist, hoy ~7.000 paginas, 28 MB): SQLite las
    reaprovecha ANTES de crecer, asi que con inserciones pequenas el fichero
    puede quedarse clavado en el mismo numero de paginas mientras se llena.
    Aqui se comen las libres y despues se crece hasta el tope; el escenario
    solo esta montado cuando la base tiene 0 paginas libres y ha llegado al
    tope. Se devuelve (paginas, libres) para que la prueba lo pueda afirmar.
    """
    con = sqlite3.connect(str(db), timeout=30)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS relleno(id INTEGER PRIMARY KEY AUTOINCREMENT, x BLOB)")
        blob = b"k" * 3900
        vueltas = 0
        while vueltas < 200:
            paginas, libres = (int(con.execute("PRAGMA page_count").fetchone()[0]),
                               int(con.execute("PRAGMA freelist_count").fetchone()[0]))
            if libres == 0 and paginas >= hasta_paginas:
                break
            # Paginas que hay que comer: primero las libres, luego crecer.
            falta = libres + max(0, hasta_paginas - paginas)
            filas = max(1, min(2000, falta))
            con.executemany("INSERT INTO relleno(x) VALUES(?)", [(blob,)] * filas)
            con.commit()
            vueltas += 1
        paginas, libres = (int(con.execute("PRAGMA page_count").fetchone()[0]),
                           int(con.execute("PRAGMA freelist_count").fetchone()[0]))
        if libres or paginas < hasta_paginas:
            raise RuntimeError(f"no se pudo llenar la base: {paginas} paginas "
                               f"(tope {hasta_paginas}), {libres} libres")
        return (paginas, libres)
    finally:
        con.close()


def _sellos_resisten_un_corte(tmp: Path) -> None:
    """El fichero de sellos no puede bloquear los pagos tras un disco lleno.

    Antes: una escritura cortada dejaba una linea SIN salto de linea; el
    siguiente arranque la leia como "linea ilegible", `verificar_sellos` la
    tomaba por historial alterado y el Core bloqueaba TODOS los pagos por un
    sello que nunca existio.
    """
    sys.path.insert(0, str(HERRAMIENTAS.parent))
    from ledger import sellos as S

    fichero = tmp / "sellos_corte.jsonl"
    s1 = {"v": 2, "hasta": 100, "asientos": 90, "suma": 10.0, "ts": 1,
          "prev": "", "legado": None, "hash": "aa", "firma": "ff"}
    s2 = dict(s1, hasta=200, asientos=190, prev="aa", hash="bb")
    S.anadir_sello(fichero, s1)
    S.anadir_sello(fichero, s2)
    # Simula el corte: media linea escrita al final, sin salto de linea.
    with fichero.open("a", encoding="utf-8") as f:
        f.write('{"v": 2, "hasta": 300, "asie')
    leidos = S.leer_sellos(fichero)
    check("un sello cortado a medias NO cuenta como historial alterado",
          len(leidos) == 2 and not [s for s in leidos if s.get("v") == -1],
          f"{len(leidos)} leidos")
    s3 = dict(s1, hasta=400, asientos=390, prev="bb", hash="cc")
    S.anadir_sello(fichero, s3)
    leidos = S.leer_sellos(fichero)
    check("el sello siguiente entra sin comerse la linea cortada",
          len(leidos) == 3 and not [s for s in leidos if s.get("v") == -1]
          and leidos[-1].get("hasta") == 400, f"{len(leidos)} leidos")


def prueba_ledger_cogido(base, tmp: Path) -> None:
    """H. Otro programa leyendo el ledger (auditoria, copia, replica).

    Un lector ajeno no puede reventar un pago. Lo que cambia es COMO se nota:

      - en DELETE, el lector bloquea al escritor: el Core tiene que contestar
        503 rapido, con Retry-After y sin escribir nada. Antes del arreglo del
        25-10-2026 eran 64,8 s y un 500 "error interno en transferencia", que
        mentia y no le decia al cliente que podia reintentar.
      - en WAL (desde el 25-10-2026, ver `_poner_en_wal` en el Core), el lector
        no bloquea al escritor: el pago entra normal. Es justo la mejora que
        se buscaba, asi que aqui lo correcto es 200, no 503.

    Se comprueba en el modo en que este la copia, para que la prueba valga en
    los dos mundos (y avise si alguien vuelve a DELETE sin darse cuenta).
    """
    print("\n== H. LEDGER COGIDO POR OTRO PROGRAMA (auditoria o copia) ==")
    con = sqlite3.connect(f"file:{(tmp / 'kukia.db').as_posix()}?mode=ro", uri=True)
    try:
        modo = str(con.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    finally:
        con.close()
    print(f"  (la base de la prueba esta en modo {modo})")

    mejor = PS.elegir_cartera(base)
    if not mejor:
        check("hay billetera con saldo para probar el bloqueo", False)
        return
    _saldo, de, seed, pub, _f = mejor
    _a, _b, destino = clave_nueva()
    nonce = secrets.token_hex(16)

    otro = sqlite3.connect(str(tmp / "kukia.db"), timeout=5, isolation_level=None)
    otro.execute("BEGIN")
    otro.execute("SELECT COUNT(*) FROM ledger").fetchone()
    try:
        t0 = time.time()
        r = transferir(base, de, destino, 1.0, nonce, seed, pub)
        tardanza = time.time() - t0
        if modo == "wal":
            check("en WAL el lector ajeno NO impide el pago",
                  r.status_code == 200, f"{r.status_code} en {tardanza:.1f} s {r.text[:80]}")
            check("y no hay que esperar: contesta al momento",
                  tardanza <= 5.0, f"{tardanza:.1f} s")
        else:
            check("en DELETE, con el ledger cogido se contesta 503 (no 500 ni cuelgue)",
                  r.status_code == 503, f"{r.status_code} en {tardanza:.1f} s")
            check("no tarda mas de 30 s en decirlo (antes: 64,8 s)",
                  tardanza <= 30.0, f"{tardanza:.1f} s")
            check("dice que no se ha escrito nada y que se reintente",
                  "no se ha escrito nada" in r.text and "reintentar" in r.text,
                  r.text[:120])
            check("dice cuanto esperar (Retry-After)",
                  bool(r.headers.get("Retry-After")),
                  str(r.headers.get("Retry-After")))
    finally:
        otro.execute("ROLLBACK")
        otro.close()

    if modo == "wal":
        # Ya entro: repetir el mismo nonce tiene que dar 409 (anti-replay).
        r2 = transferir(base, de, destino, 1.0, nonce, seed, pub)
        check("el nonce ya cobrado se sigue rechazando (anti-replay)",
              r2.status_code == 409, f"{r2.status_code} {r2.text[:100]}")
    else:
        # Soltado el bloqueo, el MISMO nonce tiene que entrar: no quedo quemado.
        r2 = transferir(base, de, destino, 1.0, nonce, seed, pub)
        check("soltado el bloqueo, la misma transferencia entra (nonce no quemado)",
              r2.status_code == 200, r2.text[:120])

    con = sqlite3.connect(str(tmp / "kukia.db"), timeout=20)
    try:
        filas = con.execute("SELECT COUNT(*) FROM transferencias WHERE nonce=?",
                            (nonce,)).fetchone()[0]
        asientos = con.execute("SELECT COUNT(*) FROM ledger WHERE tarea_id=?",
                               (f"tx-{nonce}",)).fetchone()[0]
    finally:
        con.close()
    check("queda UNA transferencia y DOS asientos (nada a medias)",
          filas == 1 and asientos == 2, f"transferencias={filas} asientos={asientos}")


def _volcar_log(tmp: Path, log=None, proc=None) -> None:
    """Cuando un Core no arranca o se muere, ensenar su log y como acabo."""
    try:
        if log is not None:
            log.flush()
    except Exception:
        pass
    if proc is not None:
        codigo = proc.poll()
        print(f"  el Core termino con codigo {codigo}"
              + (" (0 = salio solo, <0 = lo mataron)" if codigo is not None else ""))
    try:
        texto = (tmp / "core.log").read_text(encoding="utf-8", errors="replace")
        lineas = texto.splitlines()[-25:]
    except Exception as e:
        lineas = [f"(no se pudo leer el log: {e})"]
    print("  --- log del Core (cola) ---")
    for linea in lineas:
        print("  " + linea)
    print(f"  carpeta: {tmp}")


def prueba_disco_lleno(token: str, dejar: bool = False) -> None:
    print("\n== G. DISCO LLENO: se acaba el espacio a mitad de faena ==")
    tmp = Path(tempfile.mkdtemp(prefix="kukia_lleno_"))
    _sellos_resisten_un_corte(tmp)
    proc = log = None
    try:
        PS.copiar_estado(tmp)
        (tmp / "admin_token.txt").write_text(token, encoding="utf-8")
        entorno = entorno_core(tmp, token)
        margen = 400
        cap = _paginas(tmp / "kukia.db") + margen
        entorno["KUKIA_DB_MAX_PAGINAS"] = str(cap)
        proc, log, puerto, base = arrancar_vivo(tmp, entorno)
        vivo = esperar(base, proc)
        check("el Core arranca con el disco casi lleno", vivo, f"puerto {puerto}")
        if not vivo:
            _volcar_log(tmp, log, proc)
            if dejar:
                print(f"  carpeta conservada: {tmp}")
            return

        # 1. Trabajo previo con sitio: nodo, tarea y asignacion.
        _s, _p, dir_cobro = clave_nueva()
        nid, seed, _pub = registrar_nodo(base, {}, dir_cobro, "nodo-disco-lleno")
        check("nodo registrado antes de llenar", bool(nid), str(nid))
        datos = "kukia-lleno-" + secrets.token_hex(8)
        rt = requests.post(f"{base}/tareas/crear", headers={"X-Admin-Token": token},
                           timeout=30, json={"tipo": "checksum", "params": {"data": datos},
                                             "cola": "A", "prioridad": 1e9,
                                             "campana": "pruebas-lleno"})
        tid = (rt.json() or {}).get("tarea_id") if rt.status_code == 200 else None
        check("tarea creada antes de llenar", bool(tid), rt.text[:120])
        tarea = {}
        if nid and tid:
            con = sqlite3.connect(str(tmp / "kukia.db"), timeout=20)
            con.execute("UPDATE nodos SET tareas_ok=1 WHERE id=?", (nid,))
            con.commit()
            con.close()
            ts = int(time.time())
            pend = requests.get(f"{base}/tareas/pendiente", timeout=60, params={
                "nodo_id": nid, "colas": "A", "ts": ts, "solo_primaria": 1,
                "firma": PS.firmar(seed, f"pendiente:{nid}:{ts}")}).json()
            tarea = pend.get("tarea") or {}
            check("tarea asignada antes de llenar", tarea.get("id") == tid,
                  str(tarea.get("id"))[:12])

        # 2. Transferencias contra el disco lleno.
        mejor = PS.elegir_cartera(base)
        if not mejor or mejor[0] <= 1.0:
            check("hay billetera con saldo para la prueba", False, "ninguna")
            return
        saldo0, de, wseed, wpub, _f = mejor
        _s2, _p2, destino = clave_nueva()
        antes = estado_bd(tmp)
        codigos, nonces = [], []

        def _transferir_una() -> int:
            n = secrets.token_hex(16)
            nonces.append(n)
            codigos.append(transferir(base, de, destino, 1.0, n, wseed, wpub).status_code)
            return codigos[-1]

        # Con sitio: estas TIENEN que cobrarse. Se hacen ANTES de llenar para
        # no depender de cuantas paginas queden libres despues del relleno.
        for _ in range(3):
            _transferir_una()

        # Disco lleno de verdad (para el Core): al tope y sin paginas libres.
        paginas, libres = _llenar_bd(tmp / "kukia.db", cap)
        print(f"   base llenada hasta {paginas} paginas (tope {cap}), {libres} libres")
        r = requests.get(f"{base}/stats", timeout=30)
        check("con el disco lleno el Core sigue sirviendo lecturas (/stats 200)",
              r.status_code == 200, f"{r.status_code}")

        # Contra el disco lleno: hasta el primer 507. La base queda justo en el
        # tope (freelist 0), asi que el primer INSERT que necesita pagina nueva
        # ya no cabe: normalmente el 507 sale a la primera. El tope de 40 vueltas
        # se queda por debajo del limite de 60/min de /transferir y solo actua
        # si algun dia la base deja paginas sueltas.
        for _ in range(40):
            if _transferir_una() == 507:
                break
        ok = [c for c in codigos if c == 200]
        llenos = [c for c in codigos if c == 507]
        check("sin espacio nada devuelve 500 (200 mientras cabe, 507 al llenarse)",
              all(c in (200, 507) for c in codigos),
              f"{len(ok)} x200, {len(llenos)} x507, otros={[c for c in codigos if c not in (200, 507)]}")
        check("SQLite llega a decir 'disco lleno' (el escenario se probo de verdad)",
              bool(llenos), f"{len(llenos)} respuestas 507 de {len(codigos)} transferencias")
        if llenos:
            r = transferir(base, de, destino, 1.0, secrets.token_hex(16), wseed, wpub)
            check("el 507 lo dice claro (menciona el espacio) y no miente",
                  r.status_code == 507 and "espacio" in r.text.lower(),
                  f"{r.status_code} {r.text[:120]}")

        # 3. Cobrar una tarea con el disco lleno.
        estado_pago = None
        pago_kuki = 0.0
        if tarea.get("id") == tid:
            metrica = hashlib.sha256(datos.encode()).hexdigest()
            firma = PS.firmar(seed, f"{tid}:{metrica}")
            r = requests.post(f"{base}/tareas/resultado", timeout=180, params={
                "tid": tid, "nodo_id": nid, "metrica": metrica, "firma": firma,
                "device": "cpu", "duracion_ms": 5.0})
            estado_pago = r.status_code
            if r.status_code == 200:
                try:
                    pago_kuki = float((r.json() or {}).get("kuki") or 0.0)
                except Exception:
                    pago_kuki = 0.0
            check("un pago sin espacio no devuelve 500 (507 como mucho)",
                  r.status_code != 500, f"{r.status_code} {r.text[:120]}")
            con = sqlite3.connect(str(tmp / "kukia.db"), timeout=20)
            try:
                filas = con.execute("SELECT COUNT(*) FROM ledger WHERE tarea_id=?",
                                    (tid,)).fetchone()[0]
                est = con.execute("SELECT estado FROM tareas WHERE id=?",
                                  (tid,)).fetchone()[0]
            finally:
                con.close()
            if r.status_code == 507:
                check("si no se pudo pagar, la tarea NO queda 'ok' (se puede reintentar)",
                      est != "ok" and filas == 0, f"estado={est} asientos={filas}")
            else:
                check("si se pago, fue un solo asiento", filas == 1,
                      f"estado={est} asientos={filas}")

        # 4. Invariantes del ledger: ni medio asiento ni dinero suelto.
        con = sqlite3.connect(str(tmp / "kukia.db"), timeout=20)
        try:
            huerfanos = []
            for n in nonces:
                movs = con.execute("SELECT COUNT(*) FROM ledger WHERE tarea_id IN (?,?)",
                                   (f"tx-{n}", f"tx-{n[:12]}")).fetchone()[0]
                cobrada = con.execute("SELECT COUNT(*) FROM transferencias WHERE nonce=?",
                                      (n,)).fetchone()[0]
                if movs not in (0, 2) or (movs == 2) != (cobrada == 1):
                    huerfanos.append((n[:8], movs, cobrada))
        finally:
            con.close()
        check("cero medios asientos con el disco lleno", not huerfanos, str(huerfanos[:4]))
        e = estado_bd(tmp)
        check("suministro = suma de saldos (no aparece ni se evapora dinero)",
              abs(e["supply"] - e["suma_saldos"]) < 1e-3,
              f"ledger={e['supply']:.6f} saldos={e['suma_saldos']:.6f}")
        # Las transferencias NO acunan: el suministro solo puede subir por el
        # pago del trabajo (que sale de la emision del Core), ni un centimo mas.
        check("el suministro solo sube por el pago del trabajo (nada de dinero suelto)",
              abs((e["supply"] - antes["supply"]) - pago_kuki) < 0.01,
              f"{antes['supply']:.6f} -> {e['supply']:.6f}, pagado {pago_kuki:.6f}")
        check("ninguna cuenta en negativo", e["negativos"] == 0)
        print(f"   transferencias: {len(ok)} cobradas, {len(llenos)} rechazadas por espacio;"
              f" pago de tarea: {estado_pago}")

        # 5. Con espacio otra vez: el historial sigue bien y todo se reintenta.
        matar(proc)
        try:
            log.close()
        except Exception:
            pass
        entorno2 = entorno_core(tmp, token)
        proc, log, puerto, base = arrancar_vivo(tmp, entorno2, proc, log)
        vivo = esperar(base, proc)
        check("el Core rearranca con espacio y el historial lleno", vivo, f"puerto {puerto}")
        if not vivo:
            _volcar_log(tmp, log, proc)
            if dejar:
                print(f"  carpeta conservada: {tmp}")
            return
        def pide(que: str, fn):
            """Peticion que, si el Core se ha muerto, sale como FALLO con log."""
            try:
                return fn()
            except Exception as e:
                check(que, False,
                      f"no se pudo preguntar al Core ({type(e).__name__}: {e})"[:160])
                _volcar_log(tmp, log, proc)
                return None

        integ = pide("la cadena sigue intacta tras el disco lleno",
                     lambda: requests.get(f"{base}/ledger/verificar", timeout=240).json())
        if integ is not None:
            check("la cadena sigue intacta tras el disco lleno",
                  integ.get("cadena_ok") is True, str(integ.get("ids_alterados"))[:160])
        completo = pide("los sellos cuadran y los pagos NO quedan bloqueados",
                        lambda: requests.get(f"{base}/ledger/integridad", timeout=240).json())
        if completo is not None:
            check("los sellos cuadran y los pagos NO quedan bloqueados",
                  completo.get("sellos_ok") is True and completo.get("pagos_bloqueados") is False,
                  str(completo.get("problemas"))[:200])
        rechazadas = [n for n, c in zip(nonces, codigos) if c == 507]
        if rechazadas:
            r = pide("una transferencia rechazada por espacio se puede reintentar",
                     lambda: transferir(base, de, destino, 1.0, rechazadas[0], wseed, wpub))
            if r is not None:
                check("una transferencia rechazada por espacio se puede reintentar",
                      r.status_code == 200, r.text[:120])
        if estado_pago == 507 and tarea.get("id") == tid:
            metrica = hashlib.sha256(datos.encode()).hexdigest()
            r = pide("la tarea que no cobro se paga ahora (una sola vez)",
                     lambda: requests.post(f"{base}/tareas/resultado", timeout=240, params={
                         "tid": tid, "nodo_id": nid, "metrica": metrica,
                         "firma": PS.firmar(seed, f"{tid}:{metrica}"),
                         "device": "cpu", "duracion_ms": 5.0}))
            if r is None:
                return
            check("la tarea que no cobro se paga ahora (una sola vez)",
                  r.status_code == 200 and (r.json() or {}).get("ok") is True,
                  f"{r.status_code} {r.text[:120]}")
            con = sqlite3.connect(str(tmp / "kukia.db"), timeout=20)
            try:
                filas = con.execute(
                    "SELECT COUNT(*) FROM ledger WHERE tarea_id=? AND motivo LIKE 'trabajo verificado%'",
                    (tid,)).fetchone()[0]
                est = con.execute("SELECT estado FROM tareas WHERE id=?",
                                  (tid,)).fetchone()[0]
            finally:
                con.close()
            check("un solo asiento de pago y la tarea en 'ok'",
                  filas == 1 and est == "ok", f"asientos={filas} estado={est}")
    finally:
        matar(proc) if proc is not None else None
        try:
            log.close()
        except Exception:
            pass
        if not dejar:
            shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dejar", action="store_true", help="no borrar la carpeta temporal")
    args = ap.parse_args()

    if not PS.DB_REAL.exists():
        print(f"ERROR: no existe {PS.DB_REAL}")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="kukia_caos_"))
    print(f"carpeta temporal: {tmp}")
    try:
        n_sellos = PS.copiar_estado(tmp)
        print(f"estado copiado: {n_sellos} sellos\n")
        token = secrets.token_hex(32)
        (tmp / "admin_token.txt").write_text(token, encoding="utf-8")
        admin = {"X-Admin-Token": token}
        entorno = entorno_core(tmp, token)

        proc, log, puerto, base = arrancar_vivo(tmp, entorno)
        print(f"arrancando Core aislado en {base} ...", flush=True)
        if not esperar(base, proc):
            print("ERROR: el Core no arranco")
            print("\n".join((tmp / "core.log").read_text(encoding="utf-8",
                                                         errors="replace").splitlines()[-30:]))
            matar(proc)
            return 3

        # Si el Core se muere entre seccion y seccion, decirlo AQUI y con su
        # codigo de salida: antes se veia como "peticiones que fallan" y no se
        # sabia ni cuando ni por que.
        def sigue_vivo(donde: str) -> None:
            codigo = proc.poll()
            if codigo is not None:
                check(f"el Core sigue vivo tras la seccion {donde}", False,
                      f"termino con codigo {codigo}")
                _volcar_log(tmp, log, proc)
                raise SystemExit(1)

        prueba_doble_gasto(base, tmp)
        sigue_vivo("A/B (doble gasto)")
        prueba_doble_pago(base, tmp, admin)
        sigue_vivo("C (doble pago)")
        proc, log, puerto, base = prueba_corte(base, tmp, entorno, puerto, proc, log, admin)
        sigue_vivo("CORTE")
        prueba_basura(base, admin)
        sigue_vivo("D (basura)")
        prueba_host(base)
        sigue_vivo("F (host)")
        prueba_ledger_cogido(base, tmp)
        sigue_vivo("H (ledger cogido)")
        prueba_disco_lleno(token, args.dejar)

        matar(proc)
        try:
            log.close()
        except Exception:
            pass
    finally:
        if not args.dejar:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"\ncarpeta conservada: {tmp}")

    total = len(PS.resultados)
    bien = sum(1 for _n, ok, _d in PS.resultados if ok)
    print("\n== RESUMEN CAOS ==")
    for nombre, ok, detalle in PS.resultados:
        if not ok:
            print(f"  FALLO: {nombre}" + (f" -> {detalle}" if detalle else ""))
    print(f"{bien}/{total} pruebas OK")
    return 0 if bien == total else 1


if __name__ == "__main__":
    sys.exit(main())
