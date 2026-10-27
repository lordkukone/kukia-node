"""Claves y direcciones de la billetera v2 (Ed25519 + ML-DSA-65).

Como se deriva todo, y por que asi:

  material  = PBKDF2-HMAC-SHA512(20 palabras + contrasena, sal, 200.000)   64 B
  semilla_ed = SHA-256("kukia/v2/ed25519|" + material)                    32 B
  semilla_pq = SHA-256("kukia/v2/mldsa65|" + material)                    32 B

Las dos semillas son de 32 bytes porque los dos algoritmos usan semilla de 32
bytes (Ed25519 y la semilla xi de FIPS 204). La separacion con etiquetas
distintas es deliberada: aunque no conozco un ataque publicado por reutilizar
la misma semilla entre Ed25519 y ML-DSA, reutilizar material secreto entre dos
esquemas no compra nada y complica el razonamiento. Dos etiquetas, dos claves
independientes, y las 20 palabras siguen recuperando todo en cualquier equipo.

Lo que NO se hace: re-derivar la clave Ed25519 de una billetera v1 con la
etiqueta nueva. Eso cambiaria su direccion y su saldo quedaria huerfano. Una
billetera v1 conserva su clave vieja solo para UNA cosa: firmar el vinculo
hacia su direccion nueva. Los fondos se mueven despues, a mano y firmados con
la clave nueva.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, mldsa
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

PBKDF2_ITER = 200_000
KDF_V2 = 2

# Contexto de FIPS 204: ata la firma ML-DSA a este protocolo y a esta version.
CONTEXTO_PQ = b"kukia-transfer-v2"

# Direcciones v1 (heredadas): KUK + SHA256(pub_ed25519)[:38] en hex MAYUSCULA.
PREFIJO_V1 = "KUK"
# Direcciones v2: KQ1 + base32(SHA256("kukia/addr/v2|" + pub_ed + pub_pq)[:20]).
PREFIJO_V2 = "KQ1"
_B32 = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # sin 0/O/1/I: se dicta por telefono


class ClaveInvalida(ValueError):
    pass


def _salt(mnemonico: list[str], version: int) -> bytes:
    if version >= 2:
        material = ("kukia-wallet-v2|" + " ".join(mnemonico)).encode("utf-8")
        return hashlib.sha256(material).digest()
    return b"kukia-wallet-v1"


def material(palabras: list[str], password: str, version: int = KDF_V2) -> bytes:
    """64 bytes de PBKDF2. Es lo unico que hay que guardar para recuperar."""
    if isinstance(palabras, str):
        palabras = palabras.split()
    if len(palabras) != 20:
        raise ClaveInvalida("se necesitan las 20 palabras")
    if any(not p.strip() for p in palabras):
        raise ClaveInvalida("palabra vacia")
    kdf = PBKDF2HMAC(algorithm=hashes.SHA512(), length=64,
                     salt=_salt(palabras, version), iterations=PBKDF2_ITER)
    return kdf.derive((" ".join(palabras) + "|" + password).encode("utf-8"))


def semilla(m: bytes, etiqueta: str) -> bytes:
    """32 bytes independientes por algoritmo, del mismo material."""
    if etiqueta not in ("ed25519", "mldsa65"):
        raise ClaveInvalida(f"etiqueta desconocida: {etiqueta}")
    return hashlib.sha256(f"kukia/v2/{etiqueta}|".encode("utf-8") + m).digest()


def base32(datos: bytes) -> str:
    bits, salida, buf, n = 0, [], 0, 0
    for byte in datos:
        buf = (buf << 8) | byte
        n += 8
        while n >= 5:
            n -= 5
            salida.append(_B32[(buf >> n) & 31])
    if n:
        salida.append(_B32[(buf << (5 - n)) & 31])
    return "".join(salida)


def direccion_v1(pub_ed_hex: str) -> str:
    """La de siempre. Se mantiene para reconocer y no confundir con la v2."""
    return PREFIJO_V1 + hashlib.sha256(bytes.fromhex(pub_ed_hex)).hexdigest()[:38].upper()


def direccion_v2(pub_ed_hex: str, pub_pq_hex: str) -> str:
    """Compromete las DOS claves. Si alguien cambia una, la direccion no cuadra."""
    ed = bytes.fromhex(pub_ed_hex)
    pq = bytes.fromhex(pub_pq_hex)
    if len(ed) != 32:
        raise ClaveInvalida("clave Ed25519 de 32 bytes")
    if len(pq) != 1952:
        raise ClaveInvalida("clave publica ML-DSA-65 de 1952 bytes")
    h = hashlib.sha256(b"kukia/addr/v2|" + ed + pq).digest()[:20]
    return PREFIJO_V2 + base32(h)


def es_direccion_v2(d: str) -> bool:
    d = (d or "").strip().upper()
    return (len(d) == 35 and d.startswith(PREFIJO_V2)
            and all(c in _B32 for c in d[3:]))


def es_direccion_v1(d: str) -> bool:
    d = (d or "").strip().upper()
    return (len(d) == 41 and d.startswith(PREFIJO_V1)
            and all(c in "0123456789ABCDEF" for c in d[3:]))


def familia(d: str) -> str:
    """'v1', 'v2' o '' — para que el Core nunca confunda una con otra."""
    if es_direccion_v2(d):
        return "v2"
    if es_direccion_v1(d):
        return "v1"
    return ""


class Billetera:
    """Par de claves en memoria. Nada de esto se escribe a disco en el prototipo."""

    def __init__(self, ed_priv: bytes, pq_priv: bytes):
        if len(ed_priv) != 32:
            raise ClaveInvalida("semilla Ed25519 de 32 bytes")
        if len(pq_priv) != 32:
            raise ClaveInvalida("semilla ML-DSA de 32 bytes")
        self._ed = ed25519.Ed25519PrivateKey.from_private_bytes(ed_priv)
        # FIPS 204 acepta la semilla de 32 bytes (xi) y expande la clave. Es lo
        # que permite que 20 palabras sigan recuperando la billetera entera.
        self._pq = mldsa.MLDSA65PrivateKey.from_seed_bytes(pq_priv)
        self.pub_ed = self._ed.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
        self.pub_pq = self._pq.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
        self.direccion = direccion_v2(self.pub_ed, self.pub_pq)

    # --- firmas sueltas (las usa el modulo hibrido) ---
    def firmar_ed(self, datos: bytes) -> bytes:
        return self._ed.sign(datos)

    def firmar_pq(self, datos: bytes, contexto: bytes = CONTEXTO_PQ) -> bytes:
        """FIPS 204 con contexto de dominio propio de KUKIA.

        El contexto va DENTRO del algoritmo (no solo en el mensaje), asi que
        una firma hecha para otro protocolo con la misma clave no verifica
        aqui aunque el mensaje coincida. Es la separacion de dominio que
        recomienda el propio estandar.
        """
        return self._pq.sign(datos, context=contexto)

    def privadas(self) -> tuple[bytes, bytes]:
        """Solo para pruebas: en produccion la clave no sale del equipo."""
        return self._ed.private_bytes_raw(), self._pq.private_bytes_raw()


def crear(palabras: list[str] | None = None, password: str | None = None,
          version: int = KDF_V2) -> tuple[Billetera, list[str], str]:
    """Crea (o re-deriva) una billetera v2. Devuelve (billetera, palabras, pwd)."""
    if palabras is None:
        raise ClaveInvalida("las palabras las pone quien llama (no hay wordlist aqui)")
    if password is None:
        password = secrets.token_hex(16)
    m = material(palabras, password, version)
    return Billetera(semilla(m, "ed25519"), semilla(m, "mldsa65")), palabras, password


def llave_v1(ruta: Path) -> ed25519.Ed25519PrivateKey:
    """Lee la clave Ed25519 de una billetera v1 REAL, sin modificarla.

    Solo se usa en el ensayo de migracion, y sobre una COPIA: el prototipo no
    escribe en la billetera ni descifra nada que no este ya en claro.
    """
    d = json.loads(Path(ruta).read_text(encoding="utf-8"))
    priv = (d.get("clave_privada") or "").strip()
    if not priv:
        raise ClaveInvalida("billetera cifrada con DPAPI: el prototipo no la descifra")
    return ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv))
