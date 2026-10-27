"""Migracion de una billetera v1 (solo Ed25519) a una v2 (Ed25519 + ML-DSA-65).

La regla que protege los fondos: **nadie mueve un saldo sin la clave que hoy
manda en esa direccion**. Asi que la migracion es un acto firmado por las DOS
partes en el mismo documento:

  - la clave VIEJA firma "soy el dueno de KUKxxxx", y
  - la clave NUEVA firma "esta KQ1yyyy es mia".

Un solo registro, dos firmas, cero ambiguedad. Ni el Core ni un atacante pueden
inventarse el vinculo: sin la clave vieja no hay vinculo, y sin la clave nueva
tampoco. Y el vinculo NO mueve dinero: solo apunta que las dos cuentas son de
la misma persona. El traslado del saldo es un paso aparte, firmado con la clave
NUEVA y decidido por el usuario, para que nadie se encuentre los fondos donde
no los dejo.

Lo que la clave vieja NO puede hacer despues: gastar desde la direccion nueva.
Y la clave nueva no puede reclamar la vieja: la direccion v1 deriva de la
publica Ed25519 vieja, asi que un vinculo con otra clave no cuadra.
"""

from __future__ import annotations

import json
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, mldsa

from . import llaves, mensaje

_CONTEXTO_VINCULO = b"kukia-vinculo-v2"


def _bytes(vinculo: dict) -> bytes:
    cuerpo = {k: v for k, v in vinculo.items() if k != "firmas"}
    return (f"KUKIA-VINCULO-v2|{mensaje.RED}|".encode("utf-8")
            + mensaje.canonico(cuerpo))


def crear(priv_v1: ed25519.Ed25519PrivateKey, nueva: llaves.Billetera,
          nonce: str | None = None, ts: int | None = None) -> dict:
    """Construye el vinculo firmado por la clave vieja y la nueva."""
    pub_v1 = priv_v1.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    vinculo = {
        "v": 2,
        "red": mensaje.RED,
        "origen_v1": llaves.direccion_v1(pub_v1),
        "pub_v1": pub_v1,
        "destino_v2": nueva.direccion,
        "pub_ed_v2": nueva.pub_ed,
        "pub_pq_v2": nueva.pub_pq,
        "nonce": nonce or "vinc-0001",
        "ts": int(ts or time.time()),
    }
    datos = _bytes(vinculo)
    vinculo["firmas"] = {
        "v1_ed25519": priv_v1.sign(datos).hex(),
        "ed25519": nueva.firmar_ed(datos).hex(),
        "mldsa65": nueva.firmar_pq(datos, _CONTEXTO_VINCULO).hex(),
    }
    return vinculo


def verificar(vinculo, ya_vinculadas: dict[str, str] | None = None) -> tuple[bool, str]:
    """Comprueba un vinculo. `ya_vinculadas` = {origen_v1: destino_v2} del registro."""
    if isinstance(vinculo, (bytes, str)):
        if len(vinculo) > 32_768:
            return False, "vinculo demasiado grande"
        try:
            vinculo = json.loads(vinculo)
        except Exception:
            return False, "vinculo no es JSON"
    if not isinstance(vinculo, dict):
        return False, "vinculo no es un objeto"
    for k in ("v", "red", "origen_v1", "pub_v1", "destino_v2", "pub_ed_v2",
              "pub_pq_v2", "firmas"):
        if k not in vinculo:
            return False, f"falta campo {k}"
    if vinculo["v"] != 2 or vinculo["red"] != mensaje.RED:
        return False, "version o red no soportada"

    # Las direcciones tienen que derivar de las claves que van en el papel.
    try:
        if llaves.direccion_v1(vinculo["pub_v1"]) != vinculo["origen_v1"]:
            return False, "la direccion v1 no deriva de pub_v1"
        if llaves.direccion_v2(vinculo["pub_ed_v2"], vinculo["pub_pq_v2"]) != vinculo["destino_v2"]:
            return False, "la direccion v2 no deriva de las claves nuevas"
    except (llaves.ClaveInvalida, ValueError):
        return False, "claves mal formadas en el vinculo"

    if ya_vinculadas:
        previo = ya_vinculadas.get(vinculo["origen_v1"])
        if previo and previo != vinculo["destino_v2"]:
            return False, "esa direccion v1 ya esta vinculada a otra v2 (no se re-vincula)"

    datos = _bytes(vinculo)
    firmas = vinculo["firmas"]
    if not isinstance(firmas, dict):
        return False, "firmas mal formadas"
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(vinculo["pub_v1"])).verify(
            bytes.fromhex(str(firmas.get("v1_ed25519", ""))), datos)
    except (InvalidSignature, ValueError):
        return False, "la clave vieja no firma el vinculo"
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(vinculo["pub_ed_v2"])).verify(
            bytes.fromhex(str(firmas.get("ed25519", ""))), datos)
        mldsa.MLDSA65PublicKey.from_public_bytes(
            bytes.fromhex(vinculo["pub_pq_v2"])).verify(
            bytes.fromhex(str(firmas.get("mldsa65", ""))), datos,
            context=_CONTEXTO_VINCULO)
    except (InvalidSignature, ValueError):
        return False, "la clave nueva no firma el vinculo (o le falta la poscuantica)"
    return True, ""
