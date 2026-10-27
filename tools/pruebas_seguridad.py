#!/usr/bin/env python3
"""Pruebas de integracion de KUKIA sobre una COPIA del ledger.

Nunca toca la base real: copia kukia.db (API de backup de SQLite), los sellos y
la clave del sello a una carpeta temporal, arranca un Core aislado en un puerto
libre y prueba de verdad:

  TRANSFERENCIAS
    1. Una billetera (KUK... + clave publica) puede enviar KIA.
    2. El saldo se mueve y el suministro NO cambia (no se crea dinero).
    3. La cadena del ledger sigue intacta y los sellos siguen cuadrando.
    4. Repetir el nonce se rechaza (409).
    5. Firma invalida / clave que no corresponde a la direccion (401).
    6. Destino basura (400), origen=destino (400), saldo insuficiente (409).
  COMPATIBILIDAD
    7. Un NODO registrado sigue pudiendo transferir (camino antiguo).
  MINERIA
    8. Tarea checksum -> pendiente firmado -> resultado firmado -> pago real.
    9. Tarea de difusion con n=1024/pasos=20000 se recorta al presupuesto.
   10. /red/serie y /stats.integridad responden con datos coherentes.

Uso:
    python tools/pruebas_seguridad.py            (sale 0 si todo pasa)
    python tools/pruebas_seguridad.py --dejar    (no borra la carpeta temporal)
"""
import argparse
import hashlib
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

RAIZ = Path(__file__).resolve().parent.parent
COORD = RAIZ / "coordinator"
DB_REAL = COORD / "ledger" / "kukia.db"
SELLOS_REAL = COORD / "sellos.jsonl"
KEY_REAL = COORD / "sello_key.txt"
CARTERAS = RAIZ / "ledger" / "wallet"
sys.path.insert(0, str(RAIZ / "ledger"))
from kukia_wallet import cargar_billetera   # noqa: E402  (descifra DPAPI)

resultados = []


def check(nombre, ok, detalle=""):
    resultados.append((nombre, bool(ok), detalle))
    print(("  [OK]   " if ok else "  [FALLO]") + f" {nombre}" + (f"  -> {detalle}" if detalle else ""),
          flush=True)
    return ok


def dir_de(pub_hex: str) -> str:
    return "KUK" + hashlib.sha256(bytes.fromhex(pub_hex)).hexdigest()[:38].upper()


def pub_de(seed_hex: str) -> str:
    k = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed_hex))
    return k.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def firmar(seed_hex: str, mensaje: str) -> str:
    k = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed_hex))
    return k.sign(mensaje.encode()).hex()


def puerto_libre() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def copiar_estado(tmp: Path) -> int:
    """Copia coherente .db + sellos + clave. Devuelve el n de sellos copiados."""
    for intento in range(6):
        for f in tmp.glob("kukia.db*"):
            f.unlink()
        src = sqlite3.connect(str(DB_REAL), timeout=10)
        src.execute("PRAGMA busy_timeout=10000")
        src.execute("PRAGMA query_only=1")
        dst = sqlite3.connect(str(tmp / "kukia.db"))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        # Los sellos se leen DESPUES del .db. El Core escribe el sello en el
        # fichero y luego actualiza meta.sellos_n, asi que el fichero puede ir
        # por delante (sobra) pero nunca por detras. Se recorta a meta.sellos_n.
        con = sqlite3.connect(str(tmp / "kukia.db"))
        fila = con.execute("SELECT valor FROM meta WHERE clave='sellos_n'").fetchone()
        con.close()
        n_meta = int(float(fila[0])) if fila else 0
        lineas = [l for l in SELLOS_REAL.read_text(encoding="utf-8").splitlines() if l.strip()]
        if n_meta > len(lineas):
            time.sleep(1.0)
            continue
        lineas = lineas[:n_meta] if n_meta else lineas
        (tmp / "sellos.jsonl").write_text("\n".join(lineas) + ("\n" if lineas else ""),
                                          encoding="utf-8")
        shutil.copyfile(KEY_REAL, tmp / "sello_key.txt")
        # AISLAMIENTO (21-10-2026): el cupo anti-Sybil es estado OPERATIVO de
        # esta maquina (que IP registro cuanto en la ultima hora), no historia
        # del ledger. Si la copia lo hereda, la suite falla por registros
        # reales de hace media hora y el resultado depende de la hora a la que
        # se ejecute. Se limpia: cada ejecucion empieza en el mismo punto.
        con_q = sqlite3.connect(str(tmp / "kukia.db"))
        try:
            con_q.execute("DELETE FROM registros_nodo")
            # Tareas que la base real tenia en vuelo (de mineros que no estan
            # en esta prueba): se devuelven a la cola. Si no, el Core aislado
            # ofrece replicas de trabajo ajeno y las pruebas reciben la tarea
            # equivocada.
            con_q.execute("UPDATE tareas SET estado='queued', nodo_id=NULL, asignado=NULL "
                          "WHERE estado='ejecutando'")
            try:
                con_q.execute("DELETE FROM tareas_replica")
            except Exception:
                pass
            con_q.commit()
        finally:
            con_q.close()
        return len(lineas)
    raise RuntimeError("no se pudo copiar un estado coherente (sellos/DB)")


def prueba_sellos(tmp: Path) -> None:
    """Sellos: ni falsos positivos por redondeo ni ceguera ante una alteracion.

    Regresion del 21-10-2026: la verificacion sumaba en Python (`acum += kuki`)
    mientras el sello se firmaba con `SUM(kuki)` de SQLite. Con muchos asientos
    pequenos las dos sumas difieren en la 4a decimal (sello 91 del ledger real:
    15084442.6813 frente a .6814) y el historial se marcaba como alterado sin
    que nadie lo hubiera tocado; tras un reinicio eso bloquea TODOS los pagos.
    Aqui se reproduce el caso exacto con una base de mentira, y despues se
    comprueba que un asiento cambiado de verdad se sigue cazando.
    """
    import sellos as S

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "tarea_id TEXT, nodo_id TEXT, kuki REAL, motivo TEXT, ts TEXT, "
                "hash_prev TEXT, hash TEXT)")
    kuki = 0.007797
    prev = ""
    for i in range(5000):
        base = f"t{i}|nodo|{kuki}|pago|t{i}|{prev}"
        h = hashlib.sha256(base.encode()).hexdigest()
        con.execute("INSERT INTO ledger(tarea_id,nodo_id,kuki,motivo,ts,hash_prev,hash) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (f"t{i}", "nodo", kuki, "pago", f"t{i}", prev, h))
        prev = h
    con.commit()

    priv, pub = S.crear_clave(tmp / "prueba_sello_key.txt")
    sello = S.nuevo_sello(con, priv, None)
    r = S.verificar_sellos(con, [sello], pub)
    check("sello de 5.000 asientos cuadra (sin falso positivo por redondeo)",
          r["ok"] is True, str(r["problemas"][:2]))

    # Un sello fija el pasado: que el ledger crezca despues no lo invalida.
    base = f"t5000|nodo|{kuki}|pago|t5000|{prev}"
    h = hashlib.sha256(base.encode()).hexdigest()
    con.execute("INSERT INTO ledger(tarea_id,nodo_id,kuki,motivo,ts,hash_prev,hash) "
                "VALUES(?,?,?,?,?,?,?)",
                ("t5000", "nodo", kuki, "pago", "t5000", prev, h))
    con.commit()
    r2 = S.verificar_sellos(con, [sello], pub)
    check("un sello sigue valido cuando el ledger crece despues",
          r2["ok"] is True, str(r2["problemas"][:2]))

    # Y una alteracion de verdad se sigue cazando (no se ha relajado nada).
    con.execute("UPDATE ledger SET kuki=1.5 WHERE id=4000")
    con.commit()
    r3 = S.verificar_sellos(con, [sello], pub)
    check("un asiento cambiado sigue invalidando los sellos", r3["ok"] is False)
    con.close()


def otra_pub(propia: str):
    """Clave publica de OTRA billetera (para probar que no puede gastar)."""
    for f in sorted(CARTERAS.glob("*.json")):
        try:
            # cargar_billetera descifra la clave (DPAPI) si esta protegida; leer
            # el JSON a pelo solo funciona con billeteras antiguas en claro.
            d = cargar_billetera(f)
            seed = (d.get("clave_privada") or "").strip()
            if len(bytes.fromhex(seed)) != 32:
                continue
            p = pub_de(seed)
            if p != propia:
                return p
        except Exception:
            continue
    return "00" * 32


def elegir_cartera(base: str):
    """Billetera con mas saldo de la COPIA (no imprime claves)."""
    mejor = None
    for f in sorted(CARTERAS.glob("*.json")):
        try:
            d = cargar_billetera(f)
            seed = (d.get("clave_privada") or "").strip()
            if len(bytes.fromhex(seed)) != 32:
                continue
            pub = pub_de(seed)
            direccion = dir_de(pub)
            saldo = float(requests.get(f"{base}/saldo/{direccion}", timeout=15).json()["saldo"])
            if mejor is None or saldo > mejor[0]:
                mejor = (saldo, direccion, seed, pub, f.name)
        except Exception:
            continue
    return mejor


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dejar", action="store_true", help="no borrar la carpeta temporal")
    args = ap.parse_args()

    if not DB_REAL.exists():
        print(f"ERROR: no existe {DB_REAL}")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="kukia_pruebas_"))
    print(f"carpeta temporal: {tmp}")
    n_sellos = copiar_estado(tmp)
    print(f"estado copiado: {n_sellos} sellos\n")

    print("== SELLOS (antes de arrancar el Core) ==")
    prueba_sellos(tmp)
    print()

    token = secrets.token_hex(32)
    (tmp / "admin_token.txt").write_text(token, encoding="utf-8")

    puerto = puerto_libre()
    base = f"http://127.0.0.1:{puerto}"
    entorno = dict(os.environ)
    entorno.update({
        "KUKIA_DB": str(tmp / "kukia.db"),
        "KUKIA_SELLO_KEY": str(tmp / "sello_key.txt"),
        "KUKIA_SELLOS_FILE": str(tmp / "sellos.jsonl"),
        "KUKIA_ADMIN_TOKEN": str(tmp / "admin_token.txt"),
        "KUKIA_AUTO_TAREAS": "0",
        "KUKIA_ARCHIVAR": "0",
        "KUKIA_SELLO_SEG": "3600",
        "KUKIA_SELLO_BLOQUEA": "1",
        # Cupo bajo para probar el corte sin crear 10 nodos de verdad.
        "KUKIA_REGISTRO_CUPO_HORA": "3",
        "KUKIA_REGISTRO_CUPO_DIA": "50",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    def arrancar(puerto_n: int):
        """Lanza el Core aislado en ese puerto. Devuelve (proc, log)."""
        archivo = open(tmp / "core.log", "w", encoding="utf-8")
        proceso = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "kukia_core:app",
             "--host", "127.0.0.1", "--port", str(puerto_n)],
            cwd=str(COORD), env=entorno, stdout=archivo, stderr=subprocess.STDOUT)
        return proceso, archivo

    # UN SOLO arranque, sin reintento.
    #
    # Aqui habia un parche de "dos intentos con puerto nuevo" que se puso cuando
    # el Core aislado se veia morir al arrancar sin escribir NADA en el log (ni
    # traceback). Esa firma -morir sin rastro- era la del vigilante `Watch_Core`
    # matando al Core de pruebas, no un fallo del Core. La causa ya no existe:
    # el vigilante solo puede tocar al proceso que OCUPA el puerto 8300
    # (tools/vigilante_core.ps1) y esta suite usa un puerto libre.
    #
    # Comprobado el 27-10-2026 con 6 vueltas (107/107 OK y codigo 0 en todas):
    # 3 seguidas y 3 alineadas para que un tick del vigilante cayera DENTRO de
    # la vuelta con el Core ya vivo. Las 6 arrancaron al primer intento y no hubo
    # ni un aviso. Se quita para que un fallo de arranque se vea tal cual, sin
    # que un reintento lo disimule, y sin gastar un puerto extra por si acaso.
    proc, log = arrancar(puerto)

    def fallo_arranque(msg):
        print(f"\nERROR: {msg}")
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
        try:
            log.close()
        except Exception:
            pass
        texto = (tmp / "core.log").read_text(encoding="utf-8", errors="replace").splitlines()
        print("\n--- ultimas lineas del Core ---")
        print("\n".join(texto[-40:]))
        print(f"log completo: {tmp / 'core.log'}")
        return 3

    try:
        listo = False
        print(f"arrancando Core aislado en {base}...", flush=True)
        t0 = time.time()
        while time.time() - t0 < 240:
            if proc.poll() is not None:
                break
            try:
                if requests.get(f"{base}/stats", timeout=3).status_code == 200:
                    listo = True
                    break
            except Exception:
                pass
            time.sleep(1)
        if not listo:
            # Se cierra el log antes de leerlo para que la cola salga entera y se
            # vea POR QUE no arranco (import roto, DB ilegible, puerto ocupado).
            escrito = ""
            try:
                log.flush()
                log.close()
                escrito = (tmp / "core.log").read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
            print(f"[aviso] sin respuesta al arrancar (codigo {proc.returncode}, "
                  f"{len(escrito.splitlines())} lineas de log)", flush=True)
            return fallo_arranque("el Core no respondio al arrancar")

        s = requests.Session()
        # 27-10-2026 (H-01/F-02): aqui habia un reintento de CONEXION (un `Retry`
        # de urllib3 montado en la sesion) que se puso como parche cuando el
        # vigilante `Watch_Core` mataba el Core aislado de esta suite y el corte
        # se veia como un `ConnectionResetError 10054`. La causa ya no existe: el
        # vigilante solo puede tocar al proceso que OCUPA el puerto 8300
        # (tools/vigilante_core.ps1) y este Core de prueba usa un puerto libre.
        # Se ha quitado para que un corte de verdad se vea tal cual, sin que un
        # reintento lo disimule. Comprobado el 27-10-2026: 3 vueltas sin este
        # reintento, 107/107 OK las tres (exit 0), con un tick del vigilante A
        # MITAD de la suite en dos de ellas. Las 3 vueltas previas, con el
        # reintento puesto, tambien dieron 107/107.
        admin = {"X-Admin-Token": token}

        print("\n== INTEGRIDAD INICIAL ==")
        integ = s.get(f"{base}/ledger/integridad", timeout=60).json()
        check("cadena del ledger intacta", integ.get("cadena_ok") is True, str(integ.get("problemas")))
        check("sellos verificados", integ.get("sellos_ok") is True)
        check("pagos activos", integ.get("pagos_bloqueados") is False)
        if not (integ.get("cadena_ok") and integ.get("sellos_ok")):
            return fallo_arranque("la copia arranco con integridad rota; revisar la copia")

        con_tmp = sqlite3.connect(str(tmp / "kukia.db"))
        con_tmp.row_factory = sqlite3.Row
        cola = con_tmp.execute("SELECT id, params FROM tareas WHERE tipo='difusion_calor' "
                               "AND estado='queued'").fetchall()
        con_tmp.close()
        sobran = []
        for f in cola:
            try:
                p = json.loads(f["params"])
                if int(p.get("pasos") or 0) > max(1, 2_000_000_000 // (int(p.get("n") or 256) ** 2)):
                    sobran.append(f["id"])
            except Exception:
                pass
        check("tareas de difusion encoladas saneadas al arrancar", not sobran,
              f"{len(cola)} en cola, {len(sobran)} sin recortar")

        elegido = elegir_cartera(base)
        if not elegido or elegido[0] <= 1.0:
            return fallo_arranque("ninguna billetera de la copia tiene saldo suficiente")
        saldo0, origen, seed, pub, fichero = elegido
        print(f"\n== TRANSFERENCIAS (billetera {origen[:12]}..., {saldo0:.4f} KIA) ==")
        destino = dir_de(hashlib.sha256(b"destino-pruebas-kukia").hexdigest())
        if destino == origen:
            destino = dir_de(hashlib.sha256(b"otro-destino").hexdigest())
        supply0 = float(s.get(f"{base}/stats").json()["supply_emitido"])
        monto = 0.5
        nonce = "prueba-" + secrets.token_hex(8)
        msg = f"transferir:{origen}:{destino}:{monto}:{nonce}"

        r = s.post(f"{base}/transferir", timeout=30, json={
            "de_nodo": origen, "a_direccion": destino, "monto": monto,
            "nonce": nonce, "firma": firmar(seed, msg), "clave_publica": pub})
        check("billetera puede enviar KIA", r.status_code == 200, r.text[:160])

        saldo_origen = float(s.get(f"{base}/saldo/{origen}").json()["saldo"])
        saldo_destino = float(s.get(f"{base}/saldo/{destino}").json()["saldo"])
        supply1 = float(s.get(f"{base}/stats").json()["supply_emitido"])
        check("el saldo baja en el origen", abs((saldo0 - monto) - saldo_origen) < 1e-6,
              f"{saldo0:.6f} -> {saldo_origen:.6f}")
        check("el saldo sube en el destino", abs(saldo_destino - monto) < 1e-6,
              f"{saldo_destino:.6f}")
        check("el suministro NO cambia (una transferencia no acuna)", abs(supply0 - supply1) < 1e-6,
              f"{supply0:.6f} -> {supply1:.6f}")
        sd = s.get(f"{base}/saldos", timeout=30).json().get("saldos") or {}
        check("los saldos materializados coinciden con /saldo",
              abs(float(sd.get(origen, 0)) - saldo_origen) < 1e-6
              and abs(float(sd.get(destino, 0)) - saldo_destino) < 1e-6,
              f"origen {sd.get(origen)} vs {saldo_origen}")

        r2 = s.post(f"{base}/transferir", timeout=30, json={
            "de_nodo": origen, "a_direccion": destino, "monto": monto,
            "nonce": nonce, "firma": firmar(seed, msg), "clave_publica": pub})
        check("nonce repetido rechazado (anti-replay)", r2.status_code == 409, r2.text[:120])

        firma_mala = firmar(seed, msg + "x")
        r3 = s.post(f"{base}/transferir", timeout=30, json={
            "de_nodo": origen, "a_direccion": destino, "monto": monto,
            "nonce": "firma-mala-" + secrets.token_hex(4), "firma": firma_mala,
            "clave_publica": pub})
        check("firma invalida rechazada", r3.status_code == 401, r3.text[:120])

        pub_otra = otra_pub(pub)
        r4 = s.post(f"{base}/transferir", timeout=30, json={
            "de_nodo": origen, "a_direccion": destino, "monto": 0.1,
            "nonce": "clave-ajena-" + secrets.token_hex(4),
            "firma": firmar(seed, f"transferir:{origen}:{destino}:0.1:clave-ajena"),
            "clave_publica": pub_otra})
        check("clave publica que no deriva en la direccion rechazada", r4.status_code == 401,
              r4.text[:120])

        r5 = s.post(f"{base}/transferir", timeout=30, json={
            "de_nodo": origen, "a_direccion": "basura", "monto": 0.1,
            "nonce": "destino-malo-" + secrets.token_hex(4),
            "firma": firmar(seed, "x"), "clave_publica": pub})
        check("destino invalido rechazado (no se quema saldo)", r5.status_code == 400,
              r5.text[:120])

        r6 = s.post(f"{base}/transferir", timeout=30, json={
            "de_nodo": origen, "a_direccion": origen, "monto": 0.1,
            "nonce": "mismo-" + secrets.token_hex(4),
            "firma": firmar(seed, "x"), "clave_publica": pub})
        check("origen = destino rechazado", r6.status_code == 400, r6.text[:120])

        grande = round(saldo_origen + 1000.0, 4)
        nonce_g = "sin-saldo-" + secrets.token_hex(4)
        r7 = s.post(f"{base}/transferir", timeout=30, json={
            "de_nodo": origen, "a_direccion": destino, "monto": grande,
            "nonce": nonce_g,
            "firma": firmar(seed, f"transferir:{origen}:{destino}:{grande}:{nonce_g}"),
            "clave_publica": pub})
        check("saldo insuficiente rechazado", r7.status_code == 409, r7.text[:120])

        # AUDITORIA 2026-10-25 (A2): el importe se redondeaba a 4 decimales ANTES
        # de comprobar la firma, asi que la firma podia no cubrir el importe que
        # se movia (se firmaba 0,50005 y se verificaba 0,5001). Ahora un importe
        # con mas de 4 decimales se rechaza con 400 y NO se mueve el saldo.
        saldo_a = float(s.get(f"{base}/saldo/{origen}").json()["saldo"])
        nonce_cinco = "cinco-decimales-" + secrets.token_hex(4)
        r8 = s.post(f"{base}/transferir", timeout=30, json={
            "de_nodo": origen, "a_direccion": destino, "monto": 0.50005,
            "nonce": nonce_cinco,
            "firma": firmar(seed, f"transferir:{origen}:{destino}:0.50005:{nonce_cinco}"),
            "clave_publica": pub})
        saldo_b = float(s.get(f"{base}/saldo/{origen}").json()["saldo"])
        check("importe con 5 decimales rechazado y sin mover saldo",
              r8.status_code == 400 and abs(saldo_a - saldo_b) < 1e-12,
              f"{r8.status_code} {r8.text[:80]} | saldo {saldo_a:.6f} -> {saldo_b:.6f}")
        nonce_cuatro = "cuatro-decimales-" + secrets.token_hex(4)
        r9 = s.post(f"{base}/transferir", timeout=30, json={
            "de_nodo": origen, "a_direccion": destino, "monto": 0.0001,
            "nonce": nonce_cuatro,
            "firma": firmar(seed, f"transferir:{origen}:{destino}:0.0001:{nonce_cuatro}"),
            "clave_publica": pub})
        check("4 decimales (el suelo transferible) sigue valiendo",
              r9.status_code == 200, r9.text[:120])

        # AUDITORIA 24-10-2026 (A1): POST /cliente/tareas aceptaba un tipo
        # inventado. _flops_tarea devolvia 0 y la tarea se cobraba al minimo
        # facturable y se quedaba en la cola para siempre, porque ningun
        # minero sabe ejecutarla. Ahora se rechaza con 400 antes de tocar
        # dinero. La prueba comprueba las dos cosas: el codigo Y que no se
        # retiene nada.
        print("\n== API DE CLIENTE ==")
        seed_c = hashlib.sha256(b"cliente-pruebas-kukia").hexdigest()
        pub_c = pub_de(seed_c)
        dir_c = dir_de(pub_c)
        alta = s.post(f"{base}/admin/clientes", timeout=30,
                      json={"nombre": "cliente-pruebas", "direccion": dir_c}, headers=admin)
        if alta.status_code == 409:
            # Ya existe de una ejecucion anterior de la suite sobre esta copia:
            # sin token no hay forma de usarlo, asi que se prueba con otra.
            seed_c = hashlib.sha256(secrets.token_hex(8).encode()).hexdigest()
            pub_c = pub_de(seed_c)
            dir_c = dir_de(pub_c)
            alta = s.post(f"{base}/admin/clientes", timeout=30,
                          json={"nombre": "cliente-pruebas", "direccion": dir_c}, headers=admin)
        check("alta de cliente para las pruebas", alta.status_code == 200, alta.text[:140])
        if alta.status_code == 200:
            cab_c = {"X-Cliente-Token": alta.json()["token"]}
            saldo_antes = s.get(f"{base}/cliente/saldo", timeout=30, headers=cab_c).json()
            inventado = s.post(f"{base}/cliente/tareas", timeout=30, headers=cab_c, json={
                "tipo": "tarea_inventada", "params": {}, "tope": 1.0,
                "nonce": "cli-" + secrets.token_hex(4), "clave_publica": pub_c,
                "firma": "00"})
            check("tipo de tarea inventado rechazado con 400",
                  inventado.status_code == 400, inventado.text[:140])
            saldo_despues = s.get(f"{base}/cliente/saldo", timeout=30, headers=cab_c).json()
            check("el tipo inventado no retiene ni encola nada",
                  abs(float(saldo_despues["retenido"]) - float(saldo_antes["retenido"])) < 1e-9
                  and int(saldo_despues["tareas"]) == int(saldo_antes["tareas"]),
                  f"retenido {saldo_antes['retenido']} -> {saldo_despues['retenido']}, "
                  f"tareas {saldo_antes['tareas']} -> {saldo_despues['tareas']}")
            precio_malo = s.get(f"{base}/cliente/precio", timeout=30, headers=cab_c,
                                params={"tipo": "tarea_inventada"})
            check("/cliente/precio de un tipo inventado tambien es 400",
                  precio_malo.status_code == 400, precio_malo.text[:140])

        print("\n== COMPATIBILIDAD: NODO ==")
        tops_antes = float(s.get(f"{base}/stats", timeout=30).json()["dificultad"]["tops_red"])
        sin_prueba = s.post(f"{base}/nodos/registrar", timeout=30, json={
            "nombre": "sin-prueba", "vram_gb": 8, "tops": 10,
            "clave_publica": pub, "direccion_cobro": origen})
        check("registro sin reto firmado rechazado (anti-Sybil)",
              sin_prueba.status_code == 401, sin_prueba.text[:120])

        r_reto = s.post(f"{base}/nodos/reto", timeout=30, json={"clave_publica": pub})
        reto = r_reto.json() if r_reto.status_code == 200 else {}
        mala_reto = s.post(f"{base}/nodos/registrar", timeout=30, json={
            "nombre": "prueba-seguridad", "vram_gb": 12, "tops": 300,
            "clave_publica": pub, "direccion_cobro": origen,
            "reto": reto.get("nonce", ""), "firma_reto": firmar(seed, "otra-cosa")})
        check("reto de registro con firma falsa rechazado",
              mala_reto.status_code == 401, mala_reto.text[:120])

        r_reto = s.post(f"{base}/nodos/reto", timeout=30, json={"clave_publica": pub})
        reto = r_reto.json() if r_reto.status_code == 200 else {}
        carga_reg = {"nombre": "prueba-seguridad", "vram_gb": 12, "tops": 300,
                     "clave_publica": pub, "direccion_cobro": origen,
                     "reto": reto.get("nonce", ""),
                     "firma_reto": firmar(seed, reto.get("mensaje") or "")}
        reg = s.post(f"{base}/nodos/registrar", timeout=30, json=carga_reg)
        nid = reg.json().get("id") if reg.status_code == 200 else None
        check("registro de nodo de prueba", bool(nid), reg.text[:120])
        reg_rep = s.post(f"{base}/nodos/registrar", timeout=30, json=carga_reg)
        check("reto de registro no se puede reutilizar",
              reg_rep.status_code == 401, reg_rep.text[:120])
        if nid:
            det = s.get(f"{base}/nodos/{nid}", timeout=30)
            check("/nodos/{id} devuelve el nodo concreto",
                  det.status_code == 200 and (det.json() or {}).get("id") == nid,
                  det.text[:120])
            corta = s.get(f"{base}/nodos", timeout=30, params={"limite": 5})
            corta_d = corta.json() if corta.status_code == 200 else []
            check("/nodos respeta el limite",
                  corta.status_code == 200 and len(corta_d) <= 5,
                  f"{len(corta_d)} filas")

        print("\n== SEGURIDAD DEL MINERO ==")
        reg_mala = s.post(f"{base}/nodos/registrar", timeout=30, json={
            "nombre": "pub-basura", "vram_gb": 8, "tops": 10,
            "clave_publica": "zz", "direccion_cobro": origen})
        check("registro con clave publica invalida rechazado",
              reg_mala.status_code == 400, reg_mala.text[:120])
        if nid:
            nodos = s.get(f"{base}/nodos", timeout=30).json()
            mio = next((x for x in nodos if x.get("id") == nid), {})
            check("un nodo no cuenta como activo hasta pedir trabajo",
                  float(mio.get("visto") or 0.0) == 0.0,
                  f"visto={mio.get('visto')}")

            ts = int(time.time())
            nonce = secrets.token_hex(8)
            f_nonce = firmar(seed, f"pendiente:{nid}:{ts}:{nonce}")
            q = {"nodo_id": nid, "ts": ts, "nonce": nonce, "firma": f_nonce}
            r1 = s.get(f"{base}/tareas/pendiente", params=q, timeout=30)
            r2 = s.get(f"{base}/tareas/pendiente", params=q, timeout=30)
            check("pendiente con nonce aceptado", r1.status_code == 200, r1.text[:120])
            check("nonce repetido rechazado (anti-replay)", r2.status_code == 401,
                  r2.text[:120])
            con_n = sqlite3.connect(str(tmp / "kukia.db"), timeout=10)
            n_nonces = con_n.execute("SELECT COUNT(*) FROM nonces_pendiente WHERE nodo_id=?",
                                     (nid,)).fetchone()[0]
            con_n.close()
            check("nonce de pendiente persistido en SQLite (sobrevive a reinicios)",
                  n_nonces >= 1, f"{n_nonces} nonces")
            ts2 = int(time.time())
            r3 = s.get(f"{base}/tareas/pendiente", timeout=30, params={
                "nodo_id": nid, "ts": ts2,
                "firma": firmar(seed, f"pendiente:{nid}:{ts2}")})
            check("minero antiguo sin nonce sigue funcionando", r3.status_code == 200,
                  r3.text[:120])
            tops_sin_trabajo = float(s.get(f"{base}/stats", timeout=30).json()["dificultad"]["tops_red"])
            check("un nodo sin trabajo pagado NO infla la dificultad",
                  abs(tops_sin_trabajo - tops_antes) < 1e-6,
                  f"tops_red {tops_antes} -> {tops_sin_trabajo}")
            # El nodo de prueba ya tiene una tarea pedida; se le da historial
            # para que el tope sea 4 y las pruebas de minado/penalizacion
            # puedan pedir trabajo (un nodo nuevo solo puede 1).
            con_h = sqlite3.connect(str(tmp / "kukia.db"), timeout=10)
            con_h.execute("UPDATE nodos SET tareas_ok=1 WHERE id=?", (nid,))
            con_h.commit()
            con_h.close()

            metricas_gpu = json.dumps({"device": "cpu", "vram_gb": 12,
                                       "gpu_util_pct": 87, "gpu_mem_mb": 1234,
                                       "tarea_ms": 10}, separators=(",", ":"))
            ts_t = int(time.time())
            h_t = hashlib.sha256(metricas_gpu.encode()).hexdigest()
            r_t = s.post(f"{base}/nodos/telemetria", timeout=30, json={
                "nodo_id": nid, "device": "cpu", "metricas": metricas_gpu,
                "ts": ts_t, "firma": firmar(seed, f"telemetria:{nid}:{ts_t}:{h_t}")})
            check("latido de GPU firmado aceptado", r_t.status_code == 200, r_t.text[:120])
            tel = s.get(f"{base}/red/telemetria", timeout=30).json().get("telemetria") or []
            mia = next((x for x in tel if x.get("nodo_id") == nid and x.get("device") == "cpu"), None)
            datos_t = {}
            if mia:
                try:
                    datos_t = json.loads(mia.get("metricas") or "{}")
                except Exception:
                    pass
            check("el panel recibe la utilizacion real", datos_t.get("gpu_util_pct") == 87,
                  f"gpu_util_pct={datos_t.get('gpu_util_pct')}")
            r_t2 = s.post(f"{base}/nodos/telemetria", timeout=30, json={
                "nodo_id": nid, "device": "cpu", "metricas": metricas_gpu,
                "ts": ts_t, "firma": "00"})
            check("telemetria con firma falsa rechazada", r_t2.status_code == 401,
                  r_t2.text[:120])
            metricas_big = "x" * 3000
            h_b = hashlib.sha256(metricas_big.encode()).hexdigest()
            r_t3 = s.post(f"{base}/nodos/telemetria", timeout=30, json={
                "nodo_id": nid, "device": "cpu", "metricas": metricas_big,
                "ts": ts_t, "firma": firmar(seed, f"telemetria:{nid}:{ts_t}:{h_b}")})
            check("telemetria gigante rechazada", r_t3.status_code == 400, r_t3.text[:120])
            r_t4 = s.post(f"{base}/nodos/telemetria", timeout=30, json={
                "nodo_id": nid, "device": "gpu:0", "metricas": metricas_gpu,
                "ts": ts_t, "firma": firmar(seed, f"telemetria:{nid}:{ts_t}:{h_t}")})
            check("device inventado rechazado", r_t4.status_code == 400, r_t4.text[:120])

            grande = s.post(f"{base}/tareas/resultado", timeout=30, params={
                "tid": "x" * 64, "nodo_id": nid, "metrica": "a" * 20000,
                "firma": "00"})
            check("metrica gigante rechazada antes de verificar",
                  grande.status_code in (400, 413, 414), grande.text[:120])
            nan = s.post(f"{base}/tareas/resultado", timeout=30, params={
                "tid": "x" * 64, "nodo_id": nid, "metrica": "1",
                "duracion_ms": "nan", "firma": "00"})
            check("duracion_ms NaN rechazada", nan.status_code == 400, nan.text[:120])
            tele = s.post(f"{base}/tareas/resultado", timeout=30, params={
                "tid": "x" * 64, "nodo_id": nid, "metrica": "1",
                "telemetria": "t" * 9000, "firma": "00"})
            check("telemetria gigante rechazada", tele.status_code == 400, tele.text[:120])

            grande_body = s.post(f"{base}/tareas/crear", headers=admin, timeout=30,
                                 json={"tipo": "checksum",
                                       "params": {"data": "x" * 300000}})
            check("cuerpo HTTP gigante rechazado (413)",
                  grande_body.status_code == 413, grande_body.text[:120])

            # AUDITORIA 2026-10-20: tolerancias sin tope y lotes de embeddings
            # que se verificaban solo a medias.
            r_tol = s.post(f"{base}/tareas/crear", headers=admin, timeout=30, json={
                "tipo": "difusion_calor",
                "params": {"n": 64, "pasos": 1, "seed": 1, "tolerancia_rel": 0.5},
                "cola": "C", "campana": "pruebas-tolerancia"})
            tol_ok = (r_tol.status_code == 200 and
                      float((r_tol.json().get("params") or {}).get("tolerancia_rel", 1)) <= 1e-6)
            check("tolerancia de difusion acotada al crear la tarea", tol_ok,
                  r_tol.text[:160])
            r_lote = s.post(f"{base}/tareas/crear", headers=admin, timeout=30, json={
                "tipo": "lote_embeddings",
                "params": {"model_path": "modelo.onnx", "inputs": ["a"] * 65}})
            check("lote de embeddings limitado para verificarlo entero",
                  r_lote.status_code == 400, r_lote.text[:120])

            # Poda de nodos que nunca trabajaron (Sybil sin trabajo).
            viejo = "podametest"
            con_p = sqlite3.connect(str(tmp / "kukia.db"), timeout=10)
            con_p.execute("INSERT OR REPLACE INTO nodos(id,nombre,vram_gb,tier,reputacion,ref,"
                          "tareas_ok,creado,tops,clave_publica,tops_estimado,direccion_cobro,visto) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (viejo, "nodo viejo", 8, 1.0, 1.0, "", 0,
                           time.ctime(time.time() - 3 * 86400), 0, "", 1, "", 0))
            con_p.commit()
            con_p.close()
            rp = s.post(f"{base}/admin/podar_nodos", headers=admin, timeout=30)
            det_v = s.get(f"{base}/nodos/{viejo}", timeout=30)
            check("poda de nodos que nunca trabajaron",
                  rp.status_code == 200 and int(rp.json().get("podados") or 0) >= 1
                  and det_v.status_code == 404,
                  f"podados={rp.json().get('podados') if rp.status_code == 200 else rp.text[:80]}"
                  f", detalle={det_v.status_code}")

        codigo_w = r'''
import sys
sys.path.insert(0, r"%s")
import kukia_worker as w
assert w.validar_tarea("checksum", {"data": "ok"})[0]
assert not w.validar_tarea("checksum", {"data": "x" * 5000})[0]
assert w.validar_tarea("difusion_calor", {"n": 1024, "pasos": 1907})[0]
assert not w.validar_tarea("difusion_calor", {"n": 2048, "pasos": 20000})[0]
assert not w.validar_tarea("matriz", {"n": 999999})[0]
assert not w.validar_tarea("desconocido", {})[0]
assert w._core_seguro("https://ejemplo.com")[0]
assert not w._core_seguro("http://192.168.1.5:8300")[0]
print("OK")
''' % (RAIZ / "worker")
        w = subprocess.run([sys.executable, "-c", codigo_w],
                           capture_output=True, text=True, timeout=60)
        check("worker valida el trabajo y la URL antes de la GPU",
              w.returncode == 0, (w.stdout + w.stderr).strip()[-200:])

        # POTENCIA REAL EN EL PANEL (21-10-2026): las cifras de GFLOPS no
        # pueden salir de `duracion_ms`, que lo escribe el minero. Salen del
        # benchmark de la GPU (`gflops_base` en la telemetria) sumado por GPU.
        codigo_p = r'''
import sys, json
sys.path.insert(0, r"%s")
import kukia_dash as d

tel = [
  {"nodo_id": "a", "device": "cuda:0", "metricas": json.dumps({"gflops_base": 42.23, "vram_gb": 12})},
  {"nodo_id": "a", "device": "cuda:0", "metricas": json.dumps({"gflops_base": 99.9})},
  {"nodo_id": "b", "device": "cuda:1", "metricas": json.dumps({"gflops_base": 41.54, "vram_gb": 16})},
  {"nodo_id": "c", "device": "cpu", "metricas": json.dumps({"gflops_base": 0})},
]
r = d._potencia_real(tel, [], {"gflops_ventana": 28.2})
assert r["gflops_medidos"] == 83.77, r
assert len(r["gpus"]) == 2, r          # la GPU repetida no se suma dos veces
assert r["gpus"][0]["gflops"] == 42.23  # y se usa la mas reciente
assert r["gflops_verificados"] == 28.2
vacio = d._potencia_real([], [], {})
assert vacio["gflops_medidos"] is None and vacio["gpus"] == [], vacio
print("OK")
''' % (RAIZ / "dashboard")
        p = subprocess.run([sys.executable, "-c", codigo_p],
                           capture_output=True, text=True, timeout=60)
        check("el panel suma los GFLOPS medidos por GPU, no los declarados",
              p.returncode == 0, (p.stdout + p.stderr).strip()[-200:])

        if nid:
            nonce_f = "fondeo-" + secrets.token_hex(6)
            rf = s.post(f"{base}/transferir", timeout=30, json={
                "de_nodo": origen, "a_direccion": nid, "monto": 0.2, "nonce": nonce_f,
                "firma": firmar(seed, f"transferir:{origen}:{nid}:0.2:{nonce_f}"),
                "clave_publica": pub})
            check("fondear un nodo (destino = id de nodo)", rf.status_code == 200, rf.text[:120])
            nonce_n = "nodo-" + secrets.token_hex(6)
            rn = s.post(f"{base}/transferir", timeout=30, json={
                "de_nodo": nid, "a_direccion": destino, "monto": 0.1, "nonce": nonce_n,
                "firma": firmar(seed, f"transferir:{nid}:{destino}:0.1:{nonce_n}")})
            check("un NODO sigue pudiendo transferir (camino antiguo)",
                  rn.status_code == 200, rn.text[:120])

        print("\n== MINERIA: tarea verificada y pagada ==")
        datos = "kukia-prueba-" + secrets.token_hex(8)
        rt = s.post(f"{base}/tareas/crear", headers=admin, timeout=30, json={
            "tipo": "checksum", "params": {"data": datos}, "cola": "A",
            "prioridad": 1e9, "campana": "pruebas-seguridad"})
        tid = rt.json().get("tarea_id") if rt.status_code == 200 else None
        check("el admin crea una tarea", bool(tid), rt.text[:160])
        if tid:
            ts = int(time.time())
            pend = s.get(f"{base}/tareas/pendiente", timeout=30, params={
                "nodo_id": nid, "colas": "A", "ts": ts, "solo_primaria": 1,
                "firma": firmar(seed, f"pendiente:{nid}:{ts}")}).json()
            tarea = pend.get("tarea") or {}
            check("el nodo recibe trabajo firmado", tarea.get("id") == tid,
                  f"recibida {(tarea.get('id') or 'ninguna')[:12]}")
            if tarea.get("id") == tid:
                metrica = hashlib.sha256(datos.encode()).hexdigest()
                rr = s.post(f"{base}/tareas/resultado", timeout=120, params={
                    "tid": tid, "nodo_id": nid, "metrica": metrica,
                    "firma": firmar(seed, f"{tid}:{metrica}"),
                    "device": "cpu", "duracion_ms": 5.0})
                cuerpo = rr.json() if rr.status_code == 200 else {}
                check("resultado verificado y pagado", rr.status_code == 200 and cuerpo.get("ok") is True,
                      rr.text[:160])
                # CONTRATO DEL 25-10-2026 (suelo transferible): una tarea de 0
                # FLOP (checksum) cobra el SUELO, 0,0001 KIA, que es el minimo
                # que `POST /transferir` acepta (hasta 4 decimales; desde el
                # 25-10 con mas de 4 se rechaza, ver hallazgo A2). Antes
                # del suelo cobraba 0, y ese trabajo no se podia mover nunca.
                # Ojo: se sigue pagando por trabajo, el checksum solo tiene el
                # minimo de factura. Ver PAGO_POR_POTENCIA.md y
                # tools/prueba_suelo_transferible.py.
                check("checksum (0 FLOP) cobra el suelo transferible (0,0001 KIA)",
                      abs(float(cuerpo.get("kuki") or 0) - 0.0001) < 1e-12,
                      f"{cuerpo.get('kuki')} KIA")
                tops_con_trabajo = float(s.get(f"{base}/stats", timeout=30).json()["dificultad"]["tops_red"])
                check("un nodo con trabajo pagado SI cuenta para la dificultad",
                      abs((tops_con_trabajo - tops_antes) - 300.0) < 1e-6,
                      f"tops_red {tops_antes} -> {tops_con_trabajo}")

                # NOMBRE DE GPU CON HTML (AUDITORIA 2026-10-21). El 'device' lo
                # elige el minero y llegaba a la portada sin limpiar (la portada
                # lo pintaba con innerHTML). Se entrega una tarea REAL con un
                # nombre con etiquetas y se mira lo que publica el Core.
                print("\n== NOMBRE DE GPU: sin HTML en lo que se publica ==")
                datos_g = "kukia-device-" + secrets.token_hex(8)
                rg = s.post(f"{base}/tareas/crear", headers=admin, timeout=30, json={
                    "tipo": "checksum", "params": {"data": datos_g}, "cola": "A",
                    "prioridad": 1e9, "campana": "pruebas-device"})
                tg = rg.json().get("tarea_id") if rg.status_code == 200 else None
                check("el admin crea la tarea del nombre de GPU", bool(tg), rg.text[:160])
                if tg:
                    ts_g = int(time.time())
                    pend_g = s.get(f"{base}/tareas/pendiente", timeout=30, params={
                        "nodo_id": nid, "colas": "A", "ts": ts_g, "solo_primaria": 1,
                        "firma": firmar(seed, f"pendiente:{nid}:{ts_g}")}).json()
                    check("el nodo recibe esa tarea",
                          (pend_g.get("tarea") or {}).get("id") == tg,
                          str((pend_g.get("tarea") or {}).get("id"))[:12])
                    metrica_g = hashlib.sha256(datos_g.encode()).hexdigest()
                    rres_g = s.post(f"{base}/tareas/resultado", timeout=120, params={
                        "tid": tg, "nodo_id": nid, "metrica": metrica_g,
                        "firma": firmar(seed, f"{tg}:{metrica_g}"),
                        "device": '<img src=x onerror="alert(1)">',
                        "duracion_ms": 4.0,
                        "telemetria": '{"tarea_ms": 4.0, "flops": 1000000}'})
                    check("la tarea con nombre de GPU raro se verifica y se paga",
                          rres_g.status_code == 200, rres_g.text[:120])
                    serie_g = s.get(f"{base}/red/serie_gpu", timeout=30,
                                    params={"minutos": 5}).json()
                    devs = [str(d) for d in (serie_g.get("devices") or [])]
                    check("el Core publica el nombre de GPU sin HTML",
                          devs and all(c not in d for d in devs for c in "<>\"'&"),
                          str(devs))
                    check("y recortado a 16 caracteres",
                          all(len(d) <= 16 for d in devs), str(devs))

                # REINICIO DEL MINERO (21-10-2026). Al relanzar el minero, las
                # tareas del proceso muerto seguian contando en su cupo y lo
                # dejaban sin minar hasta 30 minutos. El nodo avisa firmando y
                # el Core le devuelve SUS tareas a la cola.
                print("\n== REINICIO DEL MINERO: suelta sus tareas colgadas ==")
                datos_r = "kukia-reinicio-" + secrets.token_hex(8)
                rt_r = s.post(f"{base}/tareas/crear", headers=admin, timeout=30, json={
                    "tipo": "checksum", "params": {"data": datos_r}, "cola": "A",
                    "prioridad": 1e9, "campana": "pruebas-reinicio"})
                tid_r = rt_r.json().get("tarea_id") if rt_r.status_code == 200 else None
                ts_r = int(time.time())
                pend_r = s.get(f"{base}/tareas/pendiente", timeout=30, params={
                    "nodo_id": nid, "colas": "A", "ts": ts_r, "solo_primaria": 1,
                    "firma": firmar(seed, f"pendiente:{nid}:{ts_r}")}).json()
                check("el nodo se queda con una tarea antes de reiniciar",
                      (pend_r.get("tarea") or {}).get("id") == tid_r,
                      str((pend_r.get("tarea") or {}).get("id"))[:12])
                seed_otra = secrets.token_hex(32)
                rmal = s.post(f"{base}/nodos/{nid}/reinicio", timeout=30, json={
                    "ts": ts_r, "firma": firmar(seed_otra, f"reinicio:{nid}:{ts_r}")})
                check("reinicio con la clave de otro nodo rechazado",
                      rmal.status_code == 401, rmal.text[:120])
                rrein = s.post(f"{base}/nodos/{nid}/reinicio", timeout=30, json={
                    "ts": ts_r, "firma": firmar(seed, f"reinicio:{nid}:{ts_r}")})
                libres = int((rrein.json() or {}).get("liberadas") or 0) if rrein.status_code == 200 else 0
                check("el reinicio firmado devuelve sus tareas a la cola",
                      rrein.status_code == 200 and libres >= 1, f"liberadas={libres}")
                ts_r2 = int(time.time()) + 1
                pend_r2 = s.get(f"{base}/tareas/pendiente", timeout=30, params={
                    "nodo_id": nid, "colas": "A", "ts": ts_r2, "solo_primaria": 1,
                    "firma": firmar(seed, f"pendiente:{nid}:{ts_r2}")}).json()
                check("tras el reinicio el nodo vuelve a recibir trabajo",
                      (pend_r2.get("tarea") or {}).get("id") == tid_r,
                      str((pend_r2.get("tarea") or {}).get("id"))[:12])

                # EJECUCION REDUNDANTE (21-10-2026). El Core recalculaba TODO:
                # costaba lo mismo minar que verificar. Ahora las tareas caras
                # las resuelven dos nodos y, si coinciden, el Core no ejecuta
                # nada. Estas pruebas usan matrices n=800 (1,02 GFLOP), que es
                # el umbral a partir del cual merece la pena duplicar.
                # Segundo nodo (B), el que verifica. Se registra aqui porque
                # las pruebas de ejecucion redundante van antes que las de
                # potencia, que reutilizan este mismo nodo.
                seed_f = secrets.token_hex(32)
                pub_f = pub_de(seed_f)
                dir_f = dir_de(pub_f)
                reto_f = s.post(f"{base}/nodos/reto", timeout=30,
                                json={"clave_publica": pub_f}).json()
                reg_f = s.post(f"{base}/nodos/registrar", timeout=30, json={
                    "nombre": "falso-24gb", "vram_gb": 24, "tops": 10,
                    "clave_publica": pub_f, "direccion_cobro": dir_f,
                    "reto": reto_f.get("nonce", ""),
                    "firma_reto": firmar(seed_f, reto_f.get("mensaje") or "")})
                nid_f = reg_f.json().get("id") if reg_f.status_code == 200 else None
                if nid_f:
                    # AUDITORIA 24-10-2026: `_mismo_dueno()` no deja que un nodo
                    # verifique a otro con la MISMA IP o la misma cuenta de
                    # cobro (anti-Sybil). Aqui los dos nodos salen de este PC,
                    # asi que el Core hacia bien en no ofrecer la replica y la
                    # suite se quedaba en rojo por un detalle del entorno, no
                    # del mecanismo. Para probar el camino real (dos operadores
                    # distintos) se le da a B una IP de registro distinta, de la
                    # red reservada para documentacion (RFC 5737).
                    con_ip = sqlite3.connect(str(tmp / "kukia.db"), timeout=10)
                    con_ip.execute("DELETE FROM registros_nodo WHERE clave_publica=?",
                                   ((pub_f or "").lower(),))
                    con_ip.execute("INSERT INTO registros_nodo(clave_publica, ip, ts) "
                                   "VALUES(?,?,?)",
                                   ((pub_f or "").lower(), "198.51.100.23", time.time()))
                    con_ip.commit()
                    con_ip.close()
                    print("\n== EJECUCION REDUNDANTE: dos nodos, el Core no recalcula ==")

                    def _matriz_valor(n_, seed_):
                        i = np.arange(n_, dtype=np.float64)
                        a = (i[:, None] * 31 + i[None, :] * 17 + seed_) % 97 - 48
                        b = (i[:, None] * 31 + i[None, :] * 17 + seed_ + 1) % 97 - 48
                        return str(int(round(float((a @ b).sum()))))

                    def _pedir(nodo_, seed_, ts_, solo=True):
                        # solo=True pide trabajo PROPIO: estas pruebas comparan
                        # contra un tid concreto y con replicas por delante el
                        # nodo podria recibir la verificacion de otra tarea.
                        nz = secrets.token_hex(8)
                        r = s.get(f"{base}/tareas/pendiente", timeout=30, params={
                            "nodo_id": nodo_, "colas": "A", "ts": ts_, "nonce": nz,
                            "solo_primaria": 1 if solo else 0,
                            "firma": firmar(seed_, f"pendiente:{nodo_}:{ts_}:{nz}")})
                        # 24-10-2026: si el Core contesta 429/500, antes esto
                        # devolvia None y la suite se caia con un AttributeError
                        # que ocultaba el motivo real. Ahora se devuelve el
                        # error y la comprobacion falla diciendo que paso.
                        try:
                            return r.json()
                        except Exception:
                            return {"_status": r.status_code, "_texto": r.text[:160]}

                    def _perdonar(nodo_):
                        """Quita el bloqueo temporal en la base de prueba.

                        El castigo (dinero + 30 min sin trabajo) ya se ha
                        comprobado justo antes; las escenas siguientes usan
                        esos mismos nodos y solo hay 3 registros por IP.
                        """
                        c = sqlite3.connect(str(tmp / "kukia.db"), timeout=10)
                        c.execute("UPDATE nodos SET castigado_hasta=0 WHERE id=?", (nodo_,))
                        c.commit()
                        c.close()

                    # --- (a) dos nodos coinciden: pago repartido, sin recalculo
                    rt_a = s.post(f"{base}/tareas/crear", headers=admin, timeout=30, json={
                        "tipo": "matriz", "params": {"n": 800, "seed": 5}, "cola": "A",
                        "prioridad": 1e9, "campana": "pruebas-replica"})
                    tid_a = rt_a.json().get("tarea_id") if rt_a.status_code == 200 else None
                    valor_a = _matriz_valor(800, 5)
                    ts_a = int(time.time())
                    pa = _pedir(nid, seed, ts_a)
                    check("el nodo A recibe la tarea cara (matriz n=800)",
                          (pa.get("tarea") or {}).get("id") == tid_a,
                          str((pa.get("tarea") or {}).get("id"))[:12])
                    pb = _pedir(nid_f, seed_f, int(time.time()), solo=False)
                    check("el nodo B recibe la REPLICA de esa misma tarea",
                          pb.get("replica") is True and
                          (pb.get("tarea") or {}).get("id") == tid_a,
                          f"replica={pb.get('replica')} id={(pb.get('tarea') or {}).get('id')}")
                    rr_b = s.post(f"{base}/tareas/replica", timeout=60, json={
                        "tid": tid_a, "nodo_id": nid_f, "metrica": valor_a,
                        "firma": firmar(seed_f, f"{tid_a}:{valor_a}"),
                        "device": "cpu", "duracion_ms": 40.0})
                    check("la replica se entrega y se guarda sin pagar aun",
                          rr_b.status_code == 200, rr_b.text[:120])
                    rr_a = s.post(f"{base}/tareas/resultado_json", timeout=120, json={
                        "tid": tid_a, "nodo_id": nid, "metrica": valor_a,
                        "firma": firmar(seed, f"{tid_a}:{valor_a}"),
                        "device": "cpu", "duracion_ms": 45.0})
                    ca = rr_a.json() if rr_a.status_code == 200 else {}
                    pa_i = ca.get("pago") or {}
                    check("dos nodos que coinciden: pagado sin recalcular en el Core",
                          rr_a.status_code == 200 and ca.get("ok") is True
                          and pa_i.get("verificado_por") in ("replica", "auditoria"),
                          f"verificado_por={pa_i.get('verificado_por')}")
                    check("el premio se reparte con el que verifico (total igual)",
                          float(pa_i.get("parte_replica") or 0) > 0,
                          f"parte_replica={pa_i.get('parte_replica')} kuki={ca.get('kuki')}")

                    # --- (b) la replica miente: se paga al primario y se castiga a B
                    rep_b_antes = float(s.get(f"{base}/nodos/{nid_f}", timeout=30).json()["reputacion"])
                    rt_b = s.post(f"{base}/tareas/crear", headers=admin, timeout=30, json={
                        "tipo": "matriz", "params": {"n": 800, "seed": 9}, "cola": "A",
                        "prioridad": 1e9, "campana": "pruebas-replica-mala"})
                    tid_b = rt_b.json().get("tarea_id") if rt_b.status_code == 200 else None
                    valor_b = _matriz_valor(800, 9)
                    _pedir(nid, seed, int(time.time()))
                    pb2 = _pedir(nid_f, seed_f, int(time.time()), solo=False)
                    if pb2.get("replica") is True and (pb2.get("tarea") or {}).get("id") == tid_b:
                        mentira_r = "0"
                        s.post(f"{base}/tareas/replica", timeout=60, json={
                            "tid": tid_b, "nodo_id": nid_f, "metrica": mentira_r,
                            "firma": firmar(seed_f, f"{tid_b}:{mentira_r}"),
                            "device": "cpu", "duracion_ms": 40.0})
                        rb = s.post(f"{base}/tareas/resultado_json", timeout=120, json={
                            "tid": tid_b, "nodo_id": nid, "metrica": valor_b,
                            "firma": firmar(seed, f"{tid_b}:{valor_b}"),
                            "device": "cpu", "duracion_ms": 45.0})
                        cb = rb.json() if rb.status_code == 200 else {}
                        check("si la replica discrepa, el Core recalcula y paga al honesto",
                              rb.status_code == 200 and cb.get("ok") is True
                              and (cb.get("pago") or {}).get("verificado_por") == "recalculo",
                              f"verificado_por={(cb.get('pago') or {}).get('verificado_por')}")
                        check("y el que mintio al verificar pierde reputacion",
                              float(s.get(f"{base}/nodos/{nid_f}", timeout=30).json()["reputacion"]) < rep_b_antes,
                              f"reputacion {rep_b_antes} -> "
                              f"{s.get(f'{base}/nodos/{nid_f}', timeout=30).json()['reputacion']}")
                        ts_b2 = int(time.time())
                        quiza = s.get(f"{base}/tareas/pendiente", timeout=30, params={
                            "nodo_id": nid_f, "ts": ts_b2,
                            "firma": firmar(seed_f, f"pendiente:{nid_f}:{ts_b2}")})
                        check("un nodo castigado no puede pedir trabajo",
                              quiza.status_code == 429 and "castigado" in quiza.text,
                              quiza.text[:100])
                        _perdonar(nid_f)
                    else:
                        check("el nodo B recibe la replica de la segunda tarea", False,
                              str(pb2.get("tarea") or pb2.get("motivo"))[:100])

                    # --- (c) el primario miente y la replica acierta: cobra B
                    rt_c = s.post(f"{base}/tareas/crear", headers=admin, timeout=30, json={
                        "tipo": "matriz", "params": {"n": 800, "seed": 13}, "cola": "A",
                        "prioridad": 1e9, "campana": "pruebas-replica-buena"})
                    tid_c = rt_c.json().get("tarea_id") if rt_c.status_code == 200 else None
                    valor_c = _matriz_valor(800, 13)
                    _pedir(nid, seed, int(time.time()))
                    pc = _pedir(nid_f, seed_f, int(time.time()), solo=False)
                    if pc.get("replica") is True and (pc.get("tarea") or {}).get("id") == tid_c:
                        s.post(f"{base}/tareas/replica", timeout=60, json={
                            "tid": tid_c, "nodo_id": nid_f, "metrica": valor_c,
                            "firma": firmar(seed_f, f"{tid_c}:{valor_c}"),
                            "device": "cpu", "duracion_ms": 40.0})
                        mala = "1"
                        rc = s.post(f"{base}/tareas/resultado_json", timeout=120, json={
                            "tid": tid_c, "nodo_id": nid, "metrica": mala,
                            "firma": firmar(seed, f"{tid_c}:{mala}"),
                            "device": "cpu", "duracion_ms": 45.0})
                        cc = rc.json() if rc.status_code == 200 else {}
                        check("si el primario miente, se le rechaza",
                              rc.status_code == 200 and cc.get("ok") is False,
                              rc.text[:100])
                        con_r = sqlite3.connect(str(tmp / "kukia.db"), timeout=10)
                        est = con_r.execute("SELECT estado FROM tareas WHERE id=?", (tid_c,)).fetchone()
                        con_r.close()
                        check("y la tarea se cierra buena con el resultado del verificador",
                              bool(est) and est[0] == "ok", f"estado={est[0] if est else '?'}")
                        # El primario mentiroso queda bloqueado; se le perdona
                        # aqui para poder comprobar el cobro del castigo abajo.
                        ts_a2 = int(time.time())
                        qa = s.get(f"{base}/tareas/pendiente", timeout=30, params={
                            "nodo_id": nid, "ts": ts_a2,
                            "firma": firmar(seed, f"pendiente:{nid}:{ts_a2}")})
                        check("el primario mentiroso tampoco puede pedir trabajo",
                              qa.status_code == 429 and "castigado" in qa.text,
                              qa.text[:100])
                        _perdonar(nid)
                    else:
                        check("el verificador honesto recibe la replica de la tercera tarea", False,
                              str(pc)[:160])

                    # --- (d) una tarea CARA de las que genera la red (difusion).
                    # Es el caso que de verdad quita trabajo al Core: si esto
                    # pagara con recalculo, la red seguiria sin escalar.
                    params_d = {"n": 250, "pasos": 1907, "seed": 21}
                    codigo_d = (
                        "import sys\n"
                        f"sys.path.insert(0, r'{RAIZ / 'worker'}')\n"
                        "import kukia_worker as w\n"
                        f"print(w.trabajo_local('difusion_calor', {params_d!r}, 'cpu'))\n")
                    valor_d = subprocess.run([sys.executable, "-c", codigo_d],
                                             capture_output=True, text=True,
                                             timeout=300).stdout.strip()
                    rt_d = s.post(f"{base}/tareas/crear", headers=admin, timeout=30, json={
                        "tipo": "difusion_calor", "params": params_d, "cola": "A",
                        "prioridad": 1e9, "campana": "pruebas-replica-difusion"})
                    tid_d = rt_d.json().get("tarea_id") if rt_d.status_code == 200 else None
                    # 24-10-2026: las escenas anteriores entregan resultados con
                    # device="cpu" y el Core marca al nodo como CPU. Por diseno
                    # (medido el 23-10: 17,3 s en CPU contra 0,8 s en GPU) una
                    # CPU no recibe difusiones, asi que A se quedaba sin tarea y
                    # la prueba fallaba por el entorno, no por el mecanismo.
                    # Aqui A vuelve a declararse GPU, que es lo que se quiere
                    # probar: la replica de una difusion entre dos GPUs.
                    con_g = sqlite3.connect(str(tmp / "kukia.db"), timeout=10)
                    con_g.execute("UPDATE nodos SET device='cuda:0' WHERE id=?", (nid,))
                    con_g.commit()
                    con_g.close()
                    pa_d = _pedir(nid, seed, int(time.time()))
                    pd = _pedir(nid_f, seed_f, int(time.time()), solo=False)
                    check("una difusion cara (1,2 GFLOP) tambien se replica",
                          bool(tid_d) and (pa_d.get("tarea") or {}).get("id") == tid_d
                          and pd.get("replica") is True
                          and (pd.get("tarea") or {}).get("id") == tid_d,
                          f"primario={str(pa_d)[:120]} replica={str(pd)[:100]}")
                    if pd.get("replica") is True and (pd.get("tarea") or {}).get("id") == tid_d:
                        rd = s.post(f"{base}/tareas/replica", timeout=60, json={
                            "tid": tid_d, "nodo_id": nid_f, "metrica": valor_d,
                            "firma": firmar(seed_f, f"{tid_d}:{valor_d}"),
                            "device": "cpu", "duracion_ms": 900.0})
                        check("la replica de la difusion se acepta",
                              rd.status_code == 200, rd.text[:120])
                        rrd = s.post(f"{base}/tareas/resultado_json", timeout=300, json={
                            "tid": tid_d, "nodo_id": nid, "metrica": valor_d,
                            "firma": firmar(seed, f"{tid_d}:{valor_d}"),
                            "device": "cpu", "duracion_ms": 950.0})
                        cd = rrd.json() if rrd.status_code == 200 else {}
                        pd_i = cd.get("pago") or {}
                        check("difusion verificada por dos nodos: el Core NO recalcula",
                              rrd.status_code == 200 and cd.get("ok") is True
                              and pd_i.get("verificado_por") in ("replica", "auditoria"),
                              f"verificado_por={pd_i.get('verificado_por')} "
                              f"parte={pd_i.get('parte_replica')}")

                    # --- (e) MUESTREO: 1 de cada 10 tareas replicadas se
                    # recalcula igualmente. Sin esto, dos nodos coludidos con
                    # el mismo resultado falso no se detectarian nunca.
                    def _en_muestra(tid_e_):
                        return int(hashlib.sha256(str(tid_e_).encode())
                                   .hexdigest()[:8], 16) % 10 == 0

                    tid_e = None
                    for _ in range(60):
                        rte = s.post(f"{base}/tareas/crear", headers=admin, timeout=30, json={
                            "tipo": "matriz", "params": {"n": 800, "seed": 17}, "cola": "A",
                            "prioridad": 1e9, "campana": "pruebas-muestra"})
                        cand = rte.json().get("tarea_id") if rte.status_code == 200 else None
                        if cand and _en_muestra(cand):
                            tid_e = cand
                            break
                        if cand:
                            con_x = sqlite3.connect(str(tmp / "kukia.db"), timeout=10)
                            con_x.execute("DELETE FROM tareas WHERE id=? AND estado='queued'",
                                          (cand,))
                            con_x.commit()
                            con_x.close()
                    check("el muestreo del 10 % tiene tareas en las que cae",
                          bool(tid_e), "60 ids sin caer en la muestra")
                    if tid_e:
                        valor_e = _matriz_valor(800, 17)
                        _pedir(nid, seed, int(time.time()))
                        pe = _pedir(nid_f, seed_f, int(time.time()), solo=False)
                        if pe.get("replica") is True \
                                and (pe.get("tarea") or {}).get("id") == tid_e:
                            s.post(f"{base}/tareas/replica", timeout=60, json={
                                "tid": tid_e, "nodo_id": nid_f, "metrica": valor_e,
                                "firma": firmar(seed_f, f"{tid_e}:{valor_e}"),
                                "device": "cpu", "duracion_ms": 40.0})
                            re_ = s.post(f"{base}/tareas/resultado_json", timeout=120, json={
                                "tid": tid_e, "nodo_id": nid, "metrica": valor_e,
                                "firma": firmar(seed, f"{tid_e}:{valor_e}"),
                                "device": "cpu", "duracion_ms": 45.0})
                            ce = re_.json() if re_.status_code == 200 else {}
                            pe_i = ce.get("pago") or {}
                            check("la tarea muestreada la recalcula el Core igualmente",
                                  re_.status_code == 200 and ce.get("ok") is True
                                  and pe_i.get("verificado_por") == "auditoria",
                                  f"verificado_por={pe_i.get('verificado_por')}")
                            check("y aun asi el verificador cobra su parte",
                                  float(pe_i.get("parte_replica") or 0) > 0,
                                  f"parte={pe_i.get('parte_replica')}")
                        else:
                            check("la tarea muestreada la recalcula el Core igualmente",
                                  False, str(pe)[:120])

                # POTENCIA DEMOSTRADA (21-10-2026): el tier salia de `vram_gb`,
                # que lo declara el propio minero. Una CPU diciendo 24 GB
                # cobraba la prima x1,6 sin demostrar nada. Esta prueba comprueba
                # las dos caras: sin pruebas se respeta el tier, y con pruebas de
                # que no da la talla la prima se anula.
                print("\n== POTENCIA DEMOSTRADA: la prima por tier se gana ==")
                check("un nodo que declara 24 GB entra con tier 1.6",
                      reg_f.status_code == 200 and
                      abs(float(reg_f.json().get("tier") or 0) - 1.6) < 1e-9,
                      reg_f.text[:120])
                if nid_f:
                    # 50 muestras a 1 GFLOPS: no es una GPU de 24 GB ni de lejos.
                    con_f = sqlite3.connect(str(tmp / "kukia.db"), timeout=10)
                    con_f.execute("UPDATE nodos SET gflops_medido=1.0, potencia_muestras=50 "
                                  "WHERE id=?", (nid_f,))
                    con_f.commit()
                    con_f.close()
                    datos_f = "kukia-potencia-" + secrets.token_hex(8)
                    rt_f = s.post(f"{base}/tareas/crear", headers=admin, timeout=30, json={
                        "tipo": "checksum", "params": {"data": datos_f}, "cola": "A",
                        "prioridad": 1e9, "campana": "pruebas-potencia", "vram_min": 24})
                    tid_f = rt_f.json().get("tarea_id") if rt_f.status_code == 200 else None
                    ts_f = int(time.time())
                    pend_f = s.get(f"{base}/tareas/pendiente", timeout=30, params={
                        "nodo_id": nid_f, "colas": "A", "ts": ts_f, "solo_primaria": 1,
                        "firma": firmar(seed_f, f"pendiente:{nid_f}:{ts_f}")}).json()
                    if (pend_f.get("tarea") or {}).get("id") == tid_f:
                        met_f = hashlib.sha256(datos_f.encode()).hexdigest()
                        rr_f = s.post(f"{base}/tareas/resultado_json", timeout=120, json={
                            "tid": tid_f, "nodo_id": nid_f, "metrica": met_f,
                            "firma": firmar(seed_f, f"{tid_f}:{met_f}"),
                            "device": "cpu", "duracion_ms": 5.0})
                        cf = rr_f.json() if rr_f.status_code == 200 else {}
                        pf = cf.get("pago") or {}
                        check("resultado correcto pero potencia no demostrada: paga tier 1.0",
                              rr_f.status_code == 200 and cf.get("ok") is True
                              and abs(float(pf.get("tier_pagado") or 0) - 1.0) < 1e-9
                              and abs(float(pf.get("tier_declarado") or 0) - 1.6) < 1e-9,
                              f"pagado={pf.get('tier_pagado')} declarado={pf.get('tier_declarado')}")
                    else:
                        check("el nodo falso recibe su tarea de 24 GB", False,
                              f"{(pend_f.get('tarea') or {}).get('id')}")

                # AUDITORIA 2026-10-20: el castigo se cobraba al id del nodo,
                # pero el dinero vive en su direccion de cobro -> nunca se
                # aplicaba. Esta prueba lo habria detectado.
                saldo_antes_castigo = float(s.get(f"{base}/saldo/{origen}", timeout=30).json()["saldo"])
                datos_mal = "kukia-castigo-" + secrets.token_hex(8)
                rt_mal = s.post(f"{base}/tareas/crear", headers=admin, timeout=30, json={
                    "tipo": "checksum", "params": {"data": datos_mal}, "cola": "A",
                    "prioridad": 1e9, "campana": "pruebas-castigo"})
                tid_mal = rt_mal.json().get("tarea_id") if rt_mal.status_code == 200 else None
                if tid_mal:
                    ts_p = int(time.time())
                    nz = secrets.token_hex(8)
                    pm = s.get(f"{base}/tareas/pendiente", timeout=30, params={
                        "nodo_id": nid, "colas": "A", "ts": ts_p, "nonce": nz,
                        "solo_primaria": 1,
                        "firma": firmar(seed, f"pendiente:{nid}:{ts_p}:{nz}")}).json()
                    if (pm.get("tarea") or {}).get("id") == tid_mal:
                        mentira = "0" * 64
                        # Entrega por CUERPO JSON (la ruta nueva); la query
                        # string sigue viva para mineros antiguos.
                        rm = s.post(f"{base}/tareas/resultado_json", timeout=120, json={
                            "tid": tid_mal, "nodo_id": nid, "metrica": mentira,
                            "firma": firmar(seed, f"{tid_mal}:{mentira}"),
                            "device": "cpu", "duracion_ms": 5.0})
                        cuerpo_m = rm.json() if rm.status_code == 200 else {}
                        check("resultado incorrecto por JSON verificado y rechazado",
                              rm.status_code == 200 and cuerpo_m.get("ok") is False,
                              rm.text[:120])
                        saldo_despues = float(s.get(f"{base}/saldo/{origen}", timeout=30).json()["saldo"])
                        castigos = [x for x in s.get(f"{base}/ledger", timeout=30).json()
                                    if str(x.get("motivo") or "").startswith("penalizacion")
                                    and x.get("nodo_id") == origen]
                        check("el castigo se cobra a la cuenta de cobro (no al id del nodo)",
                              bool(castigos) and saldo_despues < saldo_antes_castigo,
                              f"saldo {saldo_antes_castigo:.6f} -> {saldo_despues:.6f}, "
                              f"asientos {len(castigos)}")
                    else:
                        check("resultado incorrecto verificado y rechazado", False,
                              "el nodo no recibio la tarea del castigo")
                else:
                    check("resultado incorrecto por JSON verificado y rechazado", False,
                          rt_mal.text[:120])

        # AUDITORIA 2026-10-20: tope de tareas por nodo. Un nodo SIN historial
        # solo puede 1 y vence a los 2 min; con historial, hasta 4.
        seed_b = secrets.token_hex(32)
        pub_b = pub_de(seed_b)
        dir_b = dir_de(pub_b)
        r_reto_b = s.post(f"{base}/nodos/reto", timeout=30, json={"clave_publica": pub_b})
        rb = r_reto_b.json() if r_reto_b.status_code == 200 else {}
        reg_b = s.post(f"{base}/nodos/registrar", timeout=30, json={
            "nombre": "prueba-tope-cola", "vram_gb": 8, "tops": 1,
            "clave_publica": pub_b, "direccion_cobro": dir_b,
            "reto": rb.get("nonce", ""),
            "firma_reto": firmar(seed_b, rb.get("mensaje") or "")})
        nid_b = reg_b.json().get("id") if reg_b.status_code == 200 else None
        if nid_b:
            def pedir_b():
                ts_b = int(time.time())
                qb = s.get(f"{base}/tareas/pendiente", timeout=30, params={
                    "nodo_id": nid_b, "ts": ts_b, "solo_primaria": 1,
                    "firma": firmar(seed_b, f"pendiente:{nid_b}:{ts_b}")})
                return (qb.json().get("tarea") or {}) if qb.status_code == 200 else {}

            nuevas = sum(1 for _ in range(3) if pedir_b().get("id"))
            check("un nodo sin historial solo puede 1 tarea",
                  nuevas == 1, f"{nuevas} tareas asignadas")

            # Simula que ya completo trabajo: ahora el tope sube a 4.
            con_b = sqlite3.connect(str(tmp / "kukia.db"), timeout=10)
            con_b.execute("UPDATE nodos SET tareas_ok=1 WHERE id=?", (nid_b,))
            con_b.commit()
            con_b.close()
            mas = sum(1 for _ in range(4) if pedir_b().get("id"))
            check("un nodo con historial no puede acaparar (max 4)",
                  mas == 3, f"{mas} tareas mas (1 previa + 3 = 4)")

            libre = s.post(f"{base}/admin/liberar_atascadas", headers=admin, timeout=30)
            despues = pedir_b()
            check("liberar atascadas devuelve la cola y el cupo",
                  libre.status_code == 200 and bool(despues.get("id")),
                  f"liberadas={libre.json().get('devueltas_a_la_cola') if libre.status_code == 200 else libre.text[:60]}")
        else:
            check("un nodo sin historial solo puede 1 tarea", False, reg_b.text[:120])
            check("un nodo con historial no puede acaparar (max 4)", False, "sin nodo")
            check("liberar atascadas devuelve la cola y el cupo", False, "sin nodo")

        print("\n== VALIDADORES INDEPENDIENTES ==")
        # 1) Firma falsa: un atacante no puede inventar una conformidad.
        seed_falsa = secrets.token_bytes(32)
        k_falsa = Ed25519PrivateKey.from_private_bytes(seed_falsa)
        pub_falsa = k_falsa.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
        datos = {"validador": "VAL_FALSO", "clave_publica": pub_falsa,
                 "desde": 0.0, "hasta": 0.0, "revisadas": 1, "ok": True,
                 "discrepancias": [], "huella": "", "ts": int(time.time())}
        datos["firma"] = k_falsa.sign(b"esto no es la atestacion").hex()
        r = s.post(f"{base}/validador/atestacion", json=datos, timeout=30)
        check("atestacion con firma invalida rechazada", r.status_code == 401,
              r.text[:120])

        # 2) Validador honesto de verdad: proceso aparte, recalcula y firma.
        ident = tmp / "ident_validador.json"
        codigo = 99
        salida = ""
        for _ in range(5):
            v = subprocess.run(
                [sys.executable, str(RAIZ / "validator" / "kukia_validator.py"),
                 "--core", base, "--nombre", "VAL_TEST",
                 "--identidad", str(ident), "--lote", "5",
                 "--desde", str(time.time() - 180), "--una-pasada"],
                capture_output=True, text=True, timeout=240)
            codigo = v.returncode
            salida = (v.stdout or "") + (v.stderr or "")
            if codigo == 0:
                break
            time.sleep(2)
        check("validador independiente recomputa y atestigua", codigo == 0,
              salida.strip()[-240:])
        est = s.get(f"{base}/validador/estado", timeout=30).json()
        nombres = [x.get("validador") for x in (est.get("validadores") or [])]
        check("el Core registra al validador", "VAL_TEST" in nombres,
              f"activos={est.get('activos')} nombres={nombres[:5]}")
        check("el validador honesto no encuentra discrepancias",
              est.get("discrepancias_24h") == 0,
              f"discrepancias={est.get('discrepancias_24h')}")

        # 3) Una discrepancia firmada se registra (sirve de alarma, no bloquea).
        seed_liar = secrets.token_bytes(32)
        k_liar = Ed25519PrivateKey.from_private_bytes(seed_liar)
        pub_liar = k_liar.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
        datos = {"validador": "VAL_LIAR", "clave_publica": pub_liar,
                 "desde": 0.0, "hasta": 0.0, "revisadas": 1, "ok": False,
                 "discrepancias": [{"id": "tarea-x", "esperado": "1", "recibido": "2"}],
                 "huella": "", "ts": int(time.time())}
        texto = json.dumps(datos, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
        datos["firma"] = k_liar.sign(texto.encode()).hex()
        r = s.post(f"{base}/validador/atestacion", json=datos, timeout=30)
        check("una discrepancia firmada se acepta como aviso", r.status_code == 200,
              r.text[:120])
        est = s.get(f"{base}/validador/estado", timeout=30).json()
        check("el Core cuenta la discrepancia", est.get("discrepancias_24h", 0) >= 1,
              f"discrepancias={est.get('discrepancias_24h')}")

        print("\n== TOPES DE COSTE (DoS) ==")
        rd = s.post(f"{base}/tareas/crear", headers=admin, timeout=30, json={
            "tipo": "difusion_calor",
            "params": {"n": 1024, "pasos": 20000, "seed": 7}, "cola": "C",
            "campana": "pruebas-seguridad"})
        p = (rd.json() or {}).get("params") or {}
        tope = max(1, 2_000_000_000 // (1024 * 1024))
        check("difusion al limite recortada al presupuesto",
              int(p.get("pasos") or 0) <= tope, f"pasos={p.get('pasos')} (tope {tope})")

        print("\n== CUPO DE REGISTROS (anti-Sybil) ==")
        # El Core aislado arranca con cupo 3/hora. Se limpia el contador para
        # que la prueba no dependa de cuantos nodos se registraron antes en la
        # misma ejecucion: entran exactamente 3 y el resto recibe 429.
        con_c = sqlite3.connect(str(tmp / "kukia.db"), timeout=10)
        con_c.execute("DELETE FROM registros_nodo")
        con_c.commit()
        con_c.close()
        cupo = []
        for i in range(4):
            seed_c = secrets.token_hex(32)
            pub_c = pub_de(seed_c)
            rc = s.post(f"{base}/nodos/reto", timeout=30,
                        json={"clave_publica": pub_c}).json()
            cupo.append(s.post(f"{base}/nodos/registrar", timeout=30, json={
                "nombre": f"cupo-{i}", "vram_gb": 8, "tops": 1,
                "clave_publica": pub_c, "direccion_cobro": dir_de(pub_c),
                "reto": rc.get("nonce", ""),
                "firma_reto": firmar(seed_c, rc.get("mensaje") or "")}).status_code)
        check("el cupo por IP corta los registros en rafaga",
              cupo.count(200) == 3 and cupo.count(429) == 1,
              f"codigos={cupo}")

        supply_final = float(s.get(f"{base}/stats", timeout=30).json()["supply_emitido"])
        rec = s.post(f"{base}/admin/recalcular_resumen", headers=admin, timeout=60)
        check("recalculo de totales desde el ledger",
              rec.status_code == 200 and abs(float(rec.json().get("supply") or 0) - supply_final) < 1e-6,
              rec.text[:120])

        print("\n== INTEGRIDAD FINAL ==")
        fin = s.get(f"{base}/ledger/integridad", timeout=60).json()
        check("cadena sigue intacta tras todo", fin.get("cadena_ok") is True)
        check("sellos siguen cuadrando", fin.get("sellos_ok") is True)
        check("pagos no bloqueados", fin.get("pagos_bloqueados") is False)
        ver = s.get(f"{base}/ledger/verificar", timeout=60).json()
        check("/ledger/verificar coincide", ver.get("cadena_ok") is True)
        serie = s.get(f"{base}/red/serie?minutos=60", timeout=60).json()
        check("/red/serie devuelve 60 puntos", len(serie.get("puntos") or []) == 60)
        # METRICAS GRAFICABLES (21-10-2026): potencia y latencia por GPU con
        # lo que el minero midio en cada tarea, no con medias declaradas.
        sg = s.get(f"{base}/red/serie_gpu?minutos=60", timeout=60).json()
        pts_g = sg.get("puntos") or []
        ok_puntos = len(pts_g) == 60 and all("gpus" in p and "hora" in p for p in pts_g)
        con_dato = [p for p in pts_g if p.get("gpus")]
        # gflops puede ser null (ese minuto no hubo tareas de computo): se
        # dibuja un hueco, no un cero que parezca una GPU parada por averia.
        campo_ok = all(
            isinstance(g.get("tareas"), int)
            and (g.get("gflops") is None or isinstance(g.get("gflops"), (int, float)))
            and (g.get("ms") is None or isinstance(g.get("ms"), (int, float)))
            for p in con_dato for g in p["gpus"].values())
        con_gflops = [g for p in con_dato for g in p["gpus"].values()
                      if isinstance(g.get("gflops"), (int, float)) and g["gflops"] > 0]
        check("/red/serie_gpu devuelve 60 puntos con gflops y ms por GPU",
              ok_puntos and campo_ok and bool(con_gflops)
              and isinstance(sg.get("tipos"), dict) and isinstance(sg.get("devices"), list),
              f"puntos={len(pts_g)} con_dato={len(con_dato)} "
              f"con_gflops={len(con_gflops)} devices={sg.get('devices')}")
        stats = s.get(f"{base}/stats", timeout=30).json()
        # 24-10-2026: esta prueba pedia la version "0.4.2" a mano y se quedo
        # vieja en cuanto el Core subio a 0.4.4 (daba rojo con el codigo bien).
        # Ahora se compara con la VERSION que declara el propio coordinator, asi
        # que no hay que tocarla cada vez que se sube la version.
        import re as _re
        _ver_core = _re.search(r'FastAPI\(title="KUKIA Core", version="([^"]+)"',
                               (COORD / "kukia_core.py").read_text(encoding="utf-8"))
        _ver_esperada = _ver_core.group(1) if _ver_core else None
        check("/stats incluye integridad y version",
              bool(stats.get("integridad")) and bool(_ver_esperada)
              and stats.get("version") == _ver_esperada,
              f"version={stats.get('version')} esperada={_ver_esperada}")

        # ESQUEMA CONTRA INSERT (27-10-2026): P-1 fue una tabla que declaraba
        # menos columnas de las que escribe su INSERT (6 contra 7) y estuvo a la
        # vista sin que lo viera nadie: el CREATE y su INSERT viven en sitios
        # distintos del mismo fichero. Aqui se revisa TODO el codigo de producto
        # en cada vuelta, para que el siguiente no llegue a produccion.
        print("\n== ESQUEMA DE TABLAS CONTRA SUS INSERT ==")
        _esq = subprocess.run([sys.executable, str(RAIZ / "tools" / "esquema_vs_insert.py"),
                               "--json"], capture_output=True, text=True, timeout=120)
        try:
            _datos = json.loads(_esq.stdout)
        except Exception:
            _datos = {}
        check("ningun esquema declara menos columnas de las que escribe su INSERT",
              _esq.returncode == 0 and not _datos.get("cortas"),
              "; ".join(f"{c['tabla']} sin {c['faltan']} ({c['fichero']})"
                        for c in (_datos.get("cortas") or []))[:200]
              or (_esq.stderr or "")[:200])
        _auto = subprocess.run([sys.executable, str(RAIZ / "tools" / "esquema_vs_insert.py"),
                                "--autoprueba"], capture_output=True, text=True, timeout=120)
        check("el revisor de esquemas detecta el fallo de P-1 (su autoprueba)",
              _auto.returncode == 0,
              (_auto.stdout.strip().splitlines() or [""])[-1][:160])

        print("\n== RESUMEN ==")
        fails = [r for r in resultados if not r[1]]
        print(f"{len(resultados) - len(fails)}/{len(resultados)} pruebas OK")
        if fails:
            for nombre, _, detalle in fails:
                print(f"  FALLO: {nombre} {detalle}")
        return 0 if not fails else 1

    except Exception:
        # Antes esto salia como un traceback crudo y la carpeta temporal se
        # borraba con el log dentro: imposible saber si el Core murio o si fue
        # un corte de red. Ahora se dice cual de las dos cosas paso.
        vivo = proc.poll() is None
        print(f"\nERROR: la suite se corto por {type(sys.exc_info()[1]).__name__}. "
              f"Core aislado {'SIGUE VIVO' if vivo else 'MUERTO (codigo ' + str(proc.poll()) + ')'}")
        try:
            texto = (tmp / "core.log").read_text(encoding="utf-8", errors="replace").splitlines()
            print("--- ultimas lineas del Core aislado ---")
            print("\n".join(texto[-25:]))
        except Exception:
            pass
        raise

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=15)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        log.close()
        if args.dejar:
            print(f"\ncarpeta conservada: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
