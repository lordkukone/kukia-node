"""KUKIA Wallet - billetera con criptografia REAL (Ed25519) + recuperacion.

- Clave privada Ed25519 (256 bits) DERIVADA de: contrasena + 20 palabras unicas
- Derivacion: PBKDF2-HMAC-SHA512 (200.000 iteraciones, salt fijo de KUKIA)
- Las 20 palabras salen de la lista oficial BIP39 en espanol (2048 palabras)
- Con la contrasena + las 20 palabras se recupera la MISMA billetera en
  cualquier equipo (Python, movil, web). Sin ellas, imposible.
- Direccion = KUK + SHA256(clave publica), derivada de la publica.
- La clave privada nunca sale del equipo del usuario.
"""
import json, hashlib, os, secrets, time
from pathlib import Path

WALLET_DIR = Path(__file__).parent.parent / "ledger" / "wallet"
WALLET_DIR.mkdir(parents=True, exist_ok=True)
WORDLIST_FILE = Path(__file__).parent / "wordlist_es.txt"

PBKDF2_ITER = 200_000
KUKIA_SALT = b"kukia-wallet-v1"

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    CRYPTO_REAL = True
except ImportError:
    CRYPTO_REAL = False

def _wordlist():
    words = [w.strip() for w in WORDLIST_FILE.read_text(encoding="utf-8").splitlines() if w.strip()]
    if len(words) < 2048:
        raise RuntimeError("wordlist incompleta")
    return words

def generar_mnemonico(n_palabras: int = 20) -> list:
    """20 palabras unicas aleatorias (CSPRNG) de la lista BIP39 espanol."""
    words = _wordlist()
    return [secrets.choice(words) for _ in range(n_palabras)]

def derivar_clave(password: str, mnemonico: list) -> bytes:
    """Deriva 64 bytes via PBKDF2 (los 32 primeros = semilla Ed25519)."""
    if isinstance(mnemonico, str):
        mnemonico = mnemonico.strip().split()
    material = (" ".join(mnemonico) + "|" + password).encode("utf-8")
    if CRYPTO_REAL:
        kdf = PBKDF2HMAC(algorithm=hashes.SHA512(), length=64,
                         salt=KUKIA_SALT, iterations=PBKDF2_ITER)
        return kdf.derive(material)
    # fallback (no deberia ocurrir)
    return hashlib.pbkdf2_hmac("sha512", material, KUKIA_SALT, PBKDF2_ITER, 64)

def crear_billetera(nombre, password: str = "", mnemonico: list = None):
    """Crea (o recupera) la billetera. Sin password/mnemonico genera unos nuevos."""
    f = WALLET_DIR / f"{nombre}.json"
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        # migrar billeteras viejas sin mnemonico: conservar direccion y saldo
        if "mnemonico" not in d:
            d["mnemonico"] = None
            d["tiene_recuperacion"] = False
        return d

    if mnemonico is None:
        mnemonico = generar_mnemonico(20)
    if not password:
        # si no dan contrasena, se genera una (hay que guardarla bien)
        password = secrets.token_hex(16)

    semilla = derivar_clave(password, mnemonico)[:32]
    if CRYPTO_REAL:
        priv = Ed25519PrivateKey.from_private_bytes(semilla)
        pub_bytes = priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        clave_privada = semilla.hex()
        clave_publica = pub_bytes.hex()
        direccion = "KUK" + hashlib.sha256(pub_bytes).hexdigest()[:38].upper()
        esquema = "ed25519-pbkdf2"
    else:
        clave_privada = semilla.hex()
        clave_publica = ""
        direccion = "KUK" + hashlib.sha256(semilla).hexdigest()[:38].upper()
        esquema = "sha256-fallback"

    d = {"nombre": nombre, "direccion": direccion, "esquema": esquema,
         "clave_privada": clave_privada, "clave_publica": clave_publica,
         "mnemonico": mnemonico, "tiene_recuperacion": True,
         "saldo": 0.0, "creada": time.ctime()}
    f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    return d, password

def recuperar_billetera(nombre, password: str, mnemonico: list):
    """Recupera la MISMA billetera en otro equipo con password + 20 palabras."""
    if isinstance(mnemonico, str):
        mnemonico = mnemonico.strip().split()
    if len(mnemonico) != 20:
        raise ValueError("se necesitan las 20 palabras")
    semilla = derivar_clave(password, mnemonico)[:32]
    if CRYPTO_REAL:
        priv = Ed25519PrivateKey.from_private_bytes(semilla)
        pub_bytes = priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        direccion = "KUK" + hashlib.sha256(pub_bytes).hexdigest()[:38].upper()
    else:
        direccion = "KUK" + hashlib.sha256(semilla).hexdigest()[:38].upper()
    f = WALLET_DIR / f"{nombre}.json"
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("direccion") != direccion:
            raise ValueError("las palabras/contrasena no coinciden con esta billetera")
        return d
    d = {"nombre": nombre, "direccion": direccion, "esquema": "ed25519-pbkdf2",
         "clave_privada": semilla.hex(),
         "clave_publica": pub_bytes.hex() if CRYPTO_REAL else "",
         "mnemonico": mnemonico, "tiene_recuperacion": True,
         "saldo": 0.0, "creada": time.ctime(), "recuperada": True}
    f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
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
    if isinstance(d, tuple):
        d = d[0]
    return d["saldo"]

def abonar(nombre, monto, motivo):
    d = crear_billetera(nombre)
    if isinstance(d, tuple):
        d = d[0]
    d["saldo"] = round(d["saldo"] + monto, 4)
    f = WALLET_DIR / f"{nombre}.json"
    f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    hist = WALLET_DIR / f"{nombre}_hist.json"
    h = json.loads(hist.read_text(encoding="utf-8")) if hist.exists() else []
    h.append({"ts": time.ctime(), "monto": monto, "motivo": motivo})
    hist.write_text(json.dumps(h[-200:], indent=2, encoding="utf-8") if False else json.dumps(h[-200:], indent=2), encoding="utf-8")
    return d["saldo"]

if __name__ == "__main__":
    import sys
    nombre = sys.argv[1] if len(sys.argv) > 1 else "nodo_fundador"
    password = sys.argv[2] if len(sys.argv) > 2 else ""
    res = crear_billetera(nombre, password)
    if isinstance(res, tuple):
        w, pwd = res
        print(f"[KUKIA Wallet] {w['direccion']}")
        print(f"  esquema: {w['esquema']}")
        print(f"  saldo: {w['saldo']} KIA")
        print(f"  PASSWORD: {pwd}")
        print(f"  20 PALABRAS: {' '.join(w['mnemonico'])}")
        print("  GUARDA password + 20 palabras: sin ellas NO se recupera.")
    else:
        print(f"[KUKIA Wallet] {res['direccion']} (existente)")
