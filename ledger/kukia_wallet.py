"""KUKIA Wallet - billetera con criptografia REAL (Ed25519).

- Clave privada Ed25519 (256 bits) generada con secrets (CSPRNG del sistema)
- Direccion derivada de la clave PUBLICA (no de la privada)
- Firma digital Ed25519 verificable (no HMAC simplificado)
- La clave privada nunca sale del equipo del usuario
"""
import json, hashlib, os, secrets, time
from pathlib import Path

WALLET_DIR = Path(__file__).parent.parent / "ledger" / "wallet"
WALLET_DIR.mkdir(parents=True, exist_ok=True)

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
    from cryptography.hazmat.primitives import serialization
    CRYPTO_REAL = True
except ImportError:
    CRYPTO_REAL = False

def crear_billetera(nombre):
    """Crea o devuelve la billetera del nodo (Ed25519 + saldo local)."""
    f = WALLET_DIR / f"{nombre}.json"
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        return d

    if CRYPTO_REAL:
        # clave privada Ed25519 real (CSPRNG del sistema)
        priv = Ed25519PrivateKey.generate()
        priv_bytes = priv.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption())
        pub_bytes = priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        clave_secreta = priv_bytes.hex()
        clave_publica = pub_bytes.hex()
        # direccion = hash de la clave PUBLICA (estandar cripto)
        direccion = "KUK" + hashlib.sha256(pub_bytes).hexdigest()[:38].upper()
        esquema = "ed25519"
    else:
        # fallback (no deberia ocurrir: cryptography esta instalado)
        clave_secreta = secrets.token_hex(32)
        clave_publica = ""
        direccion = "KUK" + hashlib.sha256(clave_secreta.encode()).hexdigest()[:38].upper()
        esquema = "sha256-fallback"

    d = {"nombre": nombre, "direccion": direccion, "esquema": esquema,
         "clave_privada": clave_secreta, "clave_publica": clave_publica,
         "saldo": 0.0, "creada": time.ctime()}
    f.write_text(json.dumps(d, indent=2), encoding="utf-8")
    return d

def firmar(datos, clave_privada_hex):
    """Firma un mensaje con la clave privada Ed25519 (firma digital real)."""
    if not CRYPTO_REAL:
        return hashlib.sha256(f"{datos}:{clave_privada_hex}".encode()).hexdigest()
    priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(clave_privada_hex))
    return priv.sign(datos.encode()).hex()

def verificar_firma(datos, firma_hex, clave_publica_hex):
    """Verifica una firma Ed25519 contra la clave publica (True/False)."""
    if not CRYPTO_REAL:
        return False
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(clave_publica_hex))
        pub.verify(bytes.fromhex(firma_hex), datos.encode())
        return True
    except Exception:
        return False

def ver_saldo(nombre):
    d = crear_billetera(nombre)
    return d["saldo"]

def abonar(nombre, monto, motivo):
    d = crear_billetera(nombre)
    d["saldo"] = round(d["saldo"] + monto, 4)
    f = WALLET_DIR / f"{nombre}.json"
    f.write_text(json.dumps(d, indent=2), encoding="utf-8")
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
    print(f"  esquema: {w['esquema']}")
    print(f"  saldo: {w['saldo']} KIA")
    # prueba de firma/verificacion
    if CRYPTO_REAL:
        firma = firmar("prueba-kukia", w["clave_privada"])
        ok = verificar_firma("prueba-kukia", firma, w["clave_publica"])
        print(f"  firma Ed25519 verificada: {ok}")
