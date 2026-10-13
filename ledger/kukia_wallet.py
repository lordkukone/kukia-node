"""KUKIA Wallet - billetera local del nodo: claves, saldo, historial, firma."""
import json, hashlib, os, sqlite3, time
from pathlib import Path

WALLET_DIR = Path(__file__).parent.parent / "ledger" / "wallet"
WALLET_DIR.mkdir(parents=True, exist_ok=True)

def crear_billetera(nombre):
    """Crea o devuelve la billetera del nodo (clave + saldo local)."""
    f = WALLET_DIR / f"{nombre}.json"
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        return d
    # clave de firma (simplificada con hash; en production: ed25519/PQC)
    clave_secreta = hashlib.sha256(f"{nombre}:{time.time()}".encode()).hexdigest()
    direccion = "KUK" + hashlib.sha256(clave_secreta.encode()).hexdigest()[:38].upper()
    d = {"nombre": nombre, "direccion": direccion, "clave": clave_secreta,
         "saldo": 0.0, "creada": time.ctime()}
    f.write_text(json.dumps(d, indent=2), encoding="utf-8")
    return d

def firmar(datos, clave):
    """Firma un mensaje con la clave del nodo (hash HMAC simple; en prod PQC)."""
    return hashlib.sha256(f"{datos}:{clave}".encode()).hexdigest()

def ver_saldo(nombre):
    d = crear_billetera(nombre)
    return d["saldo"]

def abonar(nombre, monto, motivo):
    d = crear_billetera(nombre)
    d["saldo"] = round(d["saldo"] + monto, 4)
    f = WALLET_DIR / f"{nombre}.json"
    f.write_text(json.dumps(d, indent=2), encoding="utf-8")
    # historial local
    hist = WALLET_DIR / f"{nombre}_hist.json"
    h = json.loads(hist.read_text(encoding="utf-8")) if hist.exists() else []
    h.append({"ts": time.ctime(), "monto": monto, "motivo": motivo})
    hist.write_text(json.dumps(h[-200:], indent=2), encoding="utf-8")
    return d["saldo"]

if __name__ == "__main__":
    import sys
    nombre = sys.argv[1] if len(sys.argv) > 1 else "nodo_fundador"
    w = crear_billetera(nombre)
    print(f"[KUKIA Wallet] {w['direccion']}")
    print(f"  saldo: {w['saldo']} KUKI inicial")