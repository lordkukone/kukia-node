"""Sellos del ledger con firmas poscuanticas, sin romper los sellos v1.

Un sello es "aqui esta el estado del ledger hasta el asiento N, firmado". Los
sellos ya emitidos (v1, Ed25519 sobre `sha256(canonico)`) NO se tocan: seguir
valiendo para siempre es justo lo que hace que un sello sirva de algo.

Lo que anade un sello v2:

  - `alg` DENTRO del cuerpo: la version va firmada, no al lado.
  - Firma Ed25519 + ML-DSA-65 sobre los mismos bytes (hibrida, ver firma_hibrida).
  - Opcion de una tercera firma SLH-DSA (FIPS 205) con fundamento matematico
    distinto (hashes, no reticulos). Se hace con el binario `openssl`, porque
    `cryptography` 49 todavia no expone SLH-DSA; el coste esta medido en el
    informe y solo se paga al sellar (raro), no al verificar tareas.

Lo que NO se hace: re-hashear la cadena. El hash sigue siendo SHA-256, que no
esta en el plan de retirada de NIST (NIST IR 8547) y con Grover conserva ~128
bits. Cambiarlo obligaria a recalcular toda la cadena y a invalidar los sellos
firmados: se estudia aparte, con su plan, no de paso.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ed25519, mldsa

from . import llaves, mensaje

FORMATO_V2 = 2
_CONTEXTO_SELLO = b"kukia-sello-v2"


def canonico(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def huella(sello: dict) -> str:
    """Hash del sello SIN las firmas: lo que encadena con el anterior."""
    cuerpo = {k: v for k, v in sello.items() if k != "firmas" and k != "firma"}
    return hashlib.sha256(canonico(cuerpo)).hexdigest()


def _cuerpo(estado: dict, anterior: dict | None) -> dict:
    sello = {
        "v": FORMATO_V2,
        "alg": ["ed25519", "mldsa65"],
        "hasta": int(estado["hasta"]),
        "hash": str(estado["hash"]),
        "asientos": int(estado["asientos"]),
        "suma": str(estado["suma"]),          # texto: ni un float en un sello
        "ts": int(estado.get("ts") or time.time()),
        "prev": huella(anterior) if anterior else "",
    }
    if estado.get("legado"):
        sello["legado"] = estado["legado"]
    return sello


def sellar(estado: dict, billetera: llaves.Billetera, anterior: dict | None = None,
           con_slhdsa: str | None = None) -> dict:
    """Crea el sello v2 firmado. `con_slhdsa` = ruta a una clave SLH-DSA privada."""
    sello = _cuerpo(estado, anterior)
    datos = canonico(sello)
    firmas = {
        "ed25519": billetera.firmar_ed(datos).hex(),
        "mldsa65": billetera.firmar_pq(datos, _CONTEXTO_SELLO).hex(),
    }
    if con_slhdsa:
        firmas["slhdsa_sha2_128s"] = _firmar_slhdsa(datos, Path(con_slhdsa))
    sello["firmas"] = firmas
    return sello


def verificar(sello: dict, claves: dict, anterior: dict | None = None) -> tuple[bool, str]:
    """Verifica un sello v2 y su encadenado. Devuelve (ok, motivo)."""
    if not isinstance(sello, dict):
        return False, "sello no es un objeto"
    if sello.get("v") != FORMATO_V2:
        return False, f"sello v{sello.get('v')} no es v2 (usa verificar_v1)"
    firmas = sello.get("firmas")
    if not isinstance(firmas, dict):
        return False, "sello sin firmas"
    alg = sello.get("alg")
    if alg != ["ed25519", "mldsa65"]:
        return False, f"alg inesperado: {alg!r}"
    if anterior is not None and sello.get("prev") != huella(anterior):
        return False, "el sello no encadena con el anterior"
    if anterior is None and sello.get("prev"):
        return False, "primer sello con prev no vacio"

    datos = canonico({k: v for k, v in sello.items() if k != "firmas"})
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(claves["ed25519"])).verify(
            bytes.fromhex(str(firmas.get("ed25519", ""))), datos)
    except Exception:
        return False, "firma Ed25519 del sello invalida"
    try:
        mldsa.MLDSA65PublicKey.from_public_bytes(
            bytes.fromhex(claves["mldsa65"])).verify(
            bytes.fromhex(str(firmas.get("mldsa65", ""))), datos,
            context=_CONTEXTO_SELLO)
    except Exception:
        return False, "firma ML-DSA-65 del sello invalida"
    if "slhdsa_sha2_128s" in firmas:
        ok, motivo = _verificar_slhdsa(datos, firmas["slhdsa_sha2_128s"],
                                       Path(claves["slhdsa_pub"]))
        if not ok:
            return False, f"firma SLH-DSA del sello: {motivo}"
    return True, ""


def verificar_v1(sello: dict, publica_hex: str) -> tuple[bool, str]:
    """Verifica un sello v1 TAL CUAL (formato viejo, `firma` en hex).

    Esto existe para demostrar que los sellos ya emitidos siguen valiendo: una
    migracion que invalida el historial no es una migracion, es un borrado.
    """
    if not isinstance(sello, dict) or "firma" not in sello:
        return False, "no es un sello v1"
    cuerpo = {k: v for k, v in sello.items() if k != "firma"}
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(publica_hex)).verify(
            bytes.fromhex(sello["firma"]), canonico(cuerpo))
        return True, ""
    except Exception:
        return False, "firma Ed25519 v1 invalida"


# ------------------------------------------------------------------
#  SLH-DSA por el binario openssl (cryptography 49 no lo expone todavia)
# ------------------------------------------------------------------

ALG_SLHDSA = "SLH-DSA-SHA2-128s"


def _openssl() -> str:
    ruta = shutil.which("openssl")
    if not ruta:
        raise RuntimeError("no hay openssl en el PATH")
    return ruta


def _firmar_slhdsa(datos: bytes, clave_priv: Path) -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        f.write(datos)
        ruta = f.name
    try:
        out = subprocess.run([_openssl(), "pkeyutl", "-sign", "-inkey", str(clave_priv),
                              "-in", ruta, "-rawin"], capture_output=True, timeout=60)
        if out.returncode:
            raise RuntimeError(f"openssl fallo: {out.stderr.decode()[:120]}")
        return out.stdout.hex()
    finally:
        Path(ruta).unlink(missing_ok=True)


def _verificar_slhdsa(datos: bytes, firma_hex: str, clave_pub: Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        m, s = Path(tmp) / "m.bin", Path(tmp) / "s.bin"
        m.write_bytes(datos)
        s.write_bytes(bytes.fromhex(firma_hex))
        out = subprocess.run([_openssl(), "pkeyutl", "-verify", "-pubin",
                              "-inkey", str(clave_pub), "-in", str(m),
                              "-sigfile", str(s), "-rawin"],
                             capture_output=True, timeout=60)
        if out.returncode == 0:
            return True, ""
        return False, (out.stderr.decode()[:120] or "verify != 0")


def crear_clave_slhdsa(carpeta: Path) -> tuple[Path, Path]:
    """Genera un par SLH-DSA de PRUEBA en `carpeta`. Devuelve (priv, pub)."""
    carpeta.mkdir(parents=True, exist_ok=True)
    priv, pub = carpeta / "sello_slhdsa.key", carpeta / "sello_slhdsa.pub"
    subprocess.run([_openssl(), "genpkey", "-algorithm", ALG_SLHDSA,
                    "-out", str(priv)], check=True, capture_output=True, timeout=120)
    subprocess.run([_openssl(), "pkey", "-in", str(priv), "-pubout",
                    "-out", str(pub)], check=True, capture_output=True, timeout=60)
    return priv, pub
