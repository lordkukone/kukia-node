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

    puerto = PS.puerto_libre()
    base = f"http://127.0.0.1:{puerto}"
    proc, log = arrancar(tmp, entorno, puerto)
    vivo = esperar(base, proc)
    check("el Core rearranca solo tras un corte seco", vivo, f"puerto {puerto}")
    if not vivo:
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
            movs = con.execute(
                "SELECT COUNT(*) FROM ledger WHERE tarea_id=?", (f"tx-{n[:12]}",)).fetchone()[0]
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
    if cobradas:
        n = next(iter(cobradas))
        r = transferir(base, de, destino, monto, n, seed, pub)
        check("nonce ya cobrado sigue bloqueado tras reiniciar (anti-replay persistente)",
              r.status_code == 409, r.text[:120])
    saldo_final = float(requests.get(f"{base}/saldo/{destino}", timeout=30).json()["saldo"])
    check("el destino tiene justo lo que dicen las transferencias cobradas",
          abs(saldo_final - monto * estado_tx) < 1e-6,
          f"{saldo_final} KIA por {estado_tx} transferencias")

    # Las que NO entraron no pueden haber dejado el nonce quemado: el dinero
    # sigue en el origen y se tiene que poder reintentar.
    sin_cobrar = [n for n in nonces if n not in cobradas]
    if sin_cobrar:
        r = transferir(base, de, destino, monto, sin_cobrar[0], seed, pub)
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

        puerto = PS.puerto_libre()
        base = f"http://127.0.0.1:{puerto}"
        proc, log = arrancar(tmp, entorno, puerto)
        print(f"arrancando Core aislado en {base} ...", flush=True)
        if not esperar(base, proc):
            print("ERROR: el Core no arranco")
            print("\n".join((tmp / "core.log").read_text(encoding="utf-8",
                                                         errors="replace").splitlines()[-30:]))
            matar(proc)
            return 3

        prueba_doble_gasto(base, tmp)
        prueba_doble_pago(base, tmp, admin)
        proc, log, puerto, base = prueba_corte(base, tmp, entorno, puerto, proc, log, admin)
        prueba_basura(base, admin)
        prueba_host(base)

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
