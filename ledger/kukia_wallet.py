"""KUKIA Wallet - billetera con criptografia REAL (Ed25519) + recuperacion.

- Clave privada Ed25519 (256 bits) DERIVADA de: contrasena + 20 palabras unicas
- Derivacion: PBKDF2-HMAC-SHA512 (200.000 iteraciones)
- Las 20 palabras salen de la lista oficial BIP39 en espanol (2048 palabras)
- Con la contrasena + las 20 palabras se recupera la MISMA billetera en
  cualquier equipo (Python, movil, web). Sin ellas, imposible.
- Direccion = KUK + SHA256(clave publica), derivada de la publica.
- La clave privada nunca sale del equipo del usuario.

AUDITORIA 2026-10-18:
  - Antes TODAS las billeteras usaban la misma sal fija ("kukia-wallet-v1"),
    lo que permite tablas precalculadas entre usuarios. Las billeteras
    NUEVAS usan v2: sal derivada de las propias 20 palabras (unica por
    billetera y sin tener que guardarla, asi que la recuperacion sigue
    funcionando en cualquier equipo).
  - Las billeteras ya creadas son v1 y NO cambian: si se re-derivaran,
    cambiaria su direccion y perderian el saldo.
  - `saldo_real()` consulta el saldo al Core: el campo 'saldo' del JSON es
    solo una copia local y no coincidia con el ledger (decia 0 mientras el
    Core tenia 15M).
"""
import json, hashlib, os, secrets, time, urllib.request, urllib.error, urllib.parse
from pathlib import Path

WALLET_DIR = Path(__file__).parent.parent / "ledger" / "wallet"
WALLET_DIR.mkdir(parents=True, exist_ok=True)

def _proteger(ruta) -> None:
    """Claves solo para el dueno (POSIX; en Windows el perfil ya es por usuario)."""
    try:
        os.chmod(ruta, 0o600)
    except Exception:
        pass
WORDLIST_FILE = Path(__file__).parent / "wordlist_es.txt"

PBKDF2_ITER = 200_000
KUKIA_SALT = b"kukia-wallet-v1"     # v1 (compatibilidad con billeteras antiguas)
KDF_ACTUAL = 2                      # billeteras nuevas
CORE = "http://127.0.0.1:8300"

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
    """20 palabras aleatorias (CSPRNG) de la lista BIP39 espanol."""
    words = _wordlist()
    return [secrets.choice(words) for _ in range(n_palabras)]

def _salt_de(mnemonico: list, version: int) -> bytes:
    """v1 = sal fija historica. v2 = sal unica por billetera, derivada de las
    palabras (determinista: se puede recalcular en cualquier equipo)."""
    if version >= 2:
        material = ("kukia-wallet-v2|" + " ".join(mnemonico)).encode("utf-8")
        return hashlib.sha256(material).digest()
    return KUKIA_SALT

def derivar_clave(password: str, mnemonico: list, version: int = KDF_ACTUAL) -> bytes:
    """Deriva 64 bytes via PBKDF2 (los 32 primeros = semilla Ed25519)."""
    if isinstance(mnemonico, str):
        mnemonico = mnemonico.strip().split()
    material = (" ".join(mnemonico) + "|" + password).encode("utf-8")
    salt = _salt_de(mnemonico, version)
    if CRYPTO_REAL:
        kdf = PBKDF2HMAC(algorithm=hashes.SHA512(), length=64,
                         salt=salt, iterations=PBKDF2_ITER)
        return kdf.derive(material)
    return hashlib.pbkdf2_hmac("sha512", material, salt, PBKDF2_ITER, 64)

def _direccion_y_claves(semilla: bytes):
    if CRYPTO_REAL:
        priv = Ed25519PrivateKey.from_private_bytes(semilla)
        pub_bytes = priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        return pub_bytes.hex(), "KUK" + hashlib.sha256(pub_bytes).hexdigest()[:38].upper(), "ed25519-pbkdf2"
    return "", "KUK" + hashlib.sha256(semilla).hexdigest()[:38].upper(), "sha256-fallback"

def crear_billetera(nombre, password: str = "", mnemonico: list = None):
    """Crea (o recupera) la billetera. Sin password/mnemonico genera unos nuevos.

    Devuelve el diccionario, o (diccionario, password) si la ha creado ahora.
    """
    f = WALLET_DIR / f"{nombre}.json"
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        if "mnemonico" not in d:
            d["mnemonico"] = None
            d["tiene_recuperacion"] = False
        d.setdefault("kdf", 1)   # AUDITORIA: las antiguas son v1
        return d

    if mnemonico is None:
        mnemonico = generar_mnemonico(20)
    if not password:
        password = secrets.token_hex(16)

    semilla = derivar_clave(password, mnemonico, KDF_ACTUAL)[:32]
    clave_publica, direccion, esquema = _direccion_y_claves(semilla)

    d = {"nombre": nombre, "direccion": direccion, "esquema": esquema,
         "kdf": KDF_ACTUAL,
         "clave_privada": semilla.hex(), "clave_publica": clave_publica,
         "mnemonico": mnemonico, "tiene_recuperacion": True,
         "saldo": 0.0, "saldo_nota": "copia local; el saldo real lo manda el Core",
         "creada": time.ctime()}
    f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    _proteger(f)
    return d, password

def recuperar_billetera(nombre, password: str, mnemonico: list):
    """Recupera la MISMA billetera en otro equipo con password + 20 palabras."""
    if isinstance(mnemonico, str):
        mnemonico = mnemonico.strip().split()
    if len(mnemonico) != 20:
        raise ValueError("se necesitan las 20 palabras")
    f = WALLET_DIR / f"{nombre}.json"
    version = KDF_ACTUAL
    if f.exists():
        version = int(json.loads(f.read_text(encoding="utf-8")).get("kdf", 1))
    semilla = derivar_clave(password, mnemonico, version)[:32]
    clave_publica, direccion, esquema = _direccion_y_claves(semilla)
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("direccion") != direccion:
            raise ValueError("las palabras/contrasena no coinciden con esta billetera")
        return d
    d = {"nombre": nombre, "direccion": direccion, "esquema": esquema, "kdf": version,
         "clave_privada": semilla.hex(), "clave_publica": clave_publica,
         "mnemonico": mnemonico, "tiene_recuperacion": True,
         "saldo": 0.0, "saldo_nota": "copia local; el saldo real lo manda el Core",
         "creada": time.ctime(), "recuperada": True}
    f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    _proteger(f)
    return d

def firmar(datos, clave_privada_hex):
    """Firma un mensaje con la clave privada Ed25519 (firma digital real)."""
    if not CRYPTO_REAL:
        raise RuntimeError("falta 'cryptography': una firma falsa no cobra en el Core")
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
    """Saldo LOCAL (copia en el JSON). Para el de verdad, usa saldo_real()."""
    d = crear_billetera(nombre)
    if isinstance(d, tuple):
        d = d[0]
    return d.get("saldo", 0.0)

def saldo_real(direccion: str, core: str = CORE, timeout: float = 5.0):
    """AUDITORIA: saldo de verdad, preguntando al Core. Devuelve None si el
    Core no responde (asi no se confunde 'sin red' con 'saldo cero')."""
    try:
        url = f"{core.rstrip('/')}/saldo/{urllib.parse.quote(direccion)}"
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode()).get("saldo")
    except Exception:
        return None

def abonar(nombre, monto, motivo):
    """Anota un movimiento en la copia local (el Core es la autoridad)."""
    d = crear_billetera(nombre)
    if isinstance(d, tuple):
        d = d[0]
    d["saldo"] = round(d.get("saldo", 0.0) + monto, 4)
    f = WALLET_DIR / f"{nombre}.json"
    f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    _proteger(f)
    hist = WALLET_DIR / f"{nombre}_hist.json"
    h = json.loads(hist.read_text(encoding="utf-8")) if hist.exists() else []
    h.append({"ts": time.ctime(), "monto": monto, "motivo": motivo})
    hist.write_text(json.dumps(h[-200:], indent=2, ensure_ascii=False), encoding="utf-8")
    _proteger(hist)
    return d["saldo"]

if __name__ == "__main__":
    import sys
    import urllib.parse
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    nombre = sys.argv[1] if len(sys.argv) > 1 else "nodo_fundador"
    password = sys.argv[2] if len(sys.argv) > 2 else ""
    res = crear_billetera(nombre, password)
    if isinstance(res, tuple):
        w, pwd = res
        print(f"[KUKIA Wallet] {w['direccion']}")
        print(f"  esquema: {w['esquema']} (kdf v{w.get('kdf', 1)})")
        print(f"  PASSWORD: {pwd}")
        print(f"  20 PALABRAS: {' '.join(w['mnemonico'])}")
        print("  GUARDA password + 20 palabras: sin ellas NO se recupera.")
    else:
        w = res
        print(f"[KUKIA Wallet] {w['direccion']} (existente, kdf v{w.get('kdf', 1)})")
    real = saldo_real(w["direccion"])
    if real is None:
        print(f"  saldo: {w.get('saldo', 0.0)} KIA (copia local; el Core no responde)")
    else:
        print(f"  saldo REAL en el Core: {real} KIA")
        if abs(float(real) - float(w.get("saldo", 0.0))) > 1e-9:
            print(f"  (la copia local decia {w.get('saldo', 0.0)}: el valido es el del Core)")
