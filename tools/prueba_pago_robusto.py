#!/usr/bin/env python3
"""Reproduce el 500 esporadico de /tareas/resultado con la tarea ya borrada.

Hipotesis (auditoria 21-10): el pago lee la tarea otra vez dentro de la
transaccion (`task2`). Si entre la primera lectura y esa transaccion la fila
desaparece (la recuperacion la cierra como 'fallo' por abandono y el archivador
la borra por antigua), `task2` es None y `task2["estado"]` lanza
TypeError -> 500 "error interno en el pago".

Este script lo fuerza a proposito sobre una COPIA del ledger: crea una tarea,
la asigna, deja una replica en curso (que hace al Core esperar 6 s antes de
pagar) y BORRA la fila de la tarea durante esa espera. Nunca toca la base real.

Uso: python tools/repro_pago_500.py
Sale 0 si el Core responde 409 (controlado), 1 si responde 500 (el fallo).
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

RAIZ = Path(__file__).resolve().parent.parent
COORD = RAIZ / "coordinator"
sys.path.insert(0, str(RAIZ / "ledger"))
sys.path.insert(0, str(RAIZ / "tools"))
import pruebas_seguridad as P        # noqa: E402  (helpers de la suite)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="kukia_repro500_"))
    P.copiar_estado(tmp)
    token = "repro-" + str(int(time.time()))
    (tmp / "admin_token.txt").write_text(token, encoding="utf-8")

    puerto = P.puerto_libre()
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
        # Ventana clara para borrar la fila mientras el Core verifica.
        "KUKIA_REPLICA_ESPERA": "6",
        "PYTHONUNBUFFERED": "1",
    })
    log = open(tmp / "core.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "kukia_core:app",
         "--host", "127.0.0.1", "--port", str(puerto)],
        cwd=str(COORD), env=entorno, stdout=log, stderr=subprocess.STDOUT)
    try:
        t0 = time.time()
        while time.time() - t0 < 120:
            try:
                if requests.get(f"{base}/stats", timeout=3).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(1)
        else:
            print("el Core no arranco")
            return 2

        # Nodo nuevo, registrado con reto firmado (mismo camino que la suite).
        clave = Ed25519PrivateKey.generate()
        seed = clave.private_bytes_raw().hex()
        pub = P.pub_de(seed)
        direccion = P.dir_de(pub)
        reto = requests.post(f"{base}/nodos/reto", json={"clave_publica": pub},
                             timeout=30).json()
        reg = requests.post(f"{base}/nodos/registrar", timeout=30, json={
            "nombre": "repro-500", "vram_gb": 12, "tops": 300,
            "clave_publica": pub, "direccion_cobro": direccion,
            "reto": reto.get("nonce", ""),
            "firma_reto": P.firmar(seed, reto.get("mensaje") or "")})
        nid = reg.json().get("id")
        print(f"nodo: {nid}")

        creada = requests.post(f"{base}/tareas/crear", timeout=30,
                               headers={"X-Admin-Token": token},
                               json={"tipo": "difusion_calor", "params": {"n": 64, "pasos": 40},
                                     "campana": "repro500"}).json()
        tid = creada.get("tarea_id") or creada.get("id")
        print(f"tarea: {tid}")

        ts = int(time.time())
        nonce = "n%d" % ts
        firma = P.firmar(seed, f"pendiente:{nid}:{ts}:{nonce}")
        pedida = requests.get(f"{base}/tareas/pendiente", timeout=30, params={
            "nodo_id": nid, "ts": ts, "nonce": nonce, "firma": firma}).json()
        # La copia trae cola pendiente: se trabaja con la tarea que el Core
        # asigne de verdad (da igual cual; lo que importa es borrarla a mitad).
        asignada = (pedida.get("tarea") or {}).get("id")
        if not asignada:
            print(f"no se asigno ninguna tarea: {pedida}")
            return 2
        tid = asignada
        print(f"asignada: {tid}")

        # Replica en curso: el Core esperara KUKIA_REPLICA_ESPERA antes de pagar.
        con = sqlite3.connect(str(tmp / "kukia.db"))
        con.execute("INSERT OR REPLACE INTO tareas_replica(tarea_id,nodo_id,estado,asignado) "
                    "VALUES(?,?, 'reclamando', ?)", (tid, nid, time.time()))
        con.commit()
        con.close()

        caja = {}

        def entregar():
            caja["r"] = requests.post(
                f"{base}/tareas/resultado_json", timeout=120,
                json={"tid": tid, "nodo_id": nid, "metrica": "0",
                      "hash_res": "", "firma": P.firmar(seed, f"{tid}:0"),
                      "device": "repro", "duracion_ms": 1.0})

        hilo = threading.Thread(target=entregar, daemon=True)
        hilo.start()
        time.sleep(1.5)          # ya paso la primera lectura, ahora espera la replica
        con = sqlite3.connect(str(tmp / "kukia.db"), timeout=10)
        borradas = con.execute("DELETE FROM tareas WHERE id=?", (tid,)).rowcount
        con.commit()
        con.close()
        print(f"fila borrada durante la espera: {borradas}")
        hilo.join(120)

        r = caja.get("r")
        if r is None:
            print("la entrega no respondio")
            return 2
        print(f"respuesta: {r.status_code} {r.text[:160]}")
        fallo = 0
        if r.status_code == 500:
            print("FALLO: 500 por la tarea borrada a mitad de pago")
            fallo = 1
        elif r.status_code == 409:
            print("OK: 409 (la tarea ya no existe), sin 500")
        else:
            print("FALLO: respuesta inesperada en la entrega")
            fallo = 2

        # La otra cara del mismo borrado: el verificador entrega su replica
        # cuando la fila ya no esta. No es culpa suya y no debe parecer error.
        ts2 = int(time.time())
        rep = requests.post(f"{base}/tareas/replica", timeout=30,
                            json={"tid": tid, "nodo_id": nid, "metrica": "123",
                                  "firma": P.firmar(seed, f"{tid}:123"),
                                  "device": "repro", "duracion_ms": 1.0})
        print(f"replica tardia: {rep.status_code} {rep.text[:120]}")
        if rep.status_code == 200 and rep.json().get("guardada") is False:
            print("OK: replica tardia informativa, sin 404")
        else:
            print("FALLO: la replica tardia sigue devolviendo error")
            fallo = fallo or 3
        return fallo
    finally:
        proc.kill()
        log.close()
        lineas = (tmp / "core.log").read_text(encoding="utf-8", errors="replace").splitlines()
        errores = [l for l in lineas if "ERROR en el pago" in l or "Traceback" in l]
        if errores:
            print("--- log del Core ---")
            print("\n".join(errores[-6:]))


if __name__ == "__main__":
    raise SystemExit(main())
