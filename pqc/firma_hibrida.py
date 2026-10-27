"""Firma hibrida versionada Ed25519 + ML-DSA-65.

La regla que hace que esto sirva de algo: **una operacion de dinero no vale si
falla cualquiera de las dos firmas**. No es "vale la que mejor pase". Durante
la transicion, mientras Ed25519 siga siendo seguro, las dos; cuando haya que
retirar Ed25519, se cambia la politica y ML-DSA sigue sola sin tocar el
formato del mensaje.

Lo que este modulo comprueba, en este orden (barato primero):

  1. Tamano del sobre (corta un ataque de agotamiento antes de parsear JSON).
  2. Forma del sobre: version, red, operacion, algoritmos.
  3. Politica: que combinacion de algoritmos vale para ESA operacion.
  4. Coherencia cabecera/cuerpo (que nadie firme una cosa y declare otra).
  5. Longitud exacta de claves y firmas (sin pasarle basura a la libreria).
  6. Direccion derivada de las dos claves publicas == origen declarado.
  7. Las DOS firmas sobre los MISMOS bytes.

Todo lo que se rechaza se rechaza con un motivo escrito, porque "firma
invalida" a secas fue justo lo que dejo ciego al Core en el hallazgo A2.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519, mldsa

from . import llaves, mensaje

MAX_SOBRE = 32_768          # un sobre legitimo no llega a 12 KB; 32 KB da aire
LARGO = {"ed25519": (32, 64), "mldsa65": (1952, 3309)}

# Operaciones donde el dinero se mueve. Aqui NUNCA se acepta solo Ed25519.
DINERO = {"transferir", "cliente", "cobro", "vincular"}

# Operaciones de bajo riesgo que un nodo antiguo puede seguir firmando solo con
# Ed25519 durante la transicion. Se registra en el resultado ('heredada') para
# que quede en el log: permitido no es lo mismo que silencioso.
HEREDADAS_OK = {"telemetria", "pendiente"}

HIBRIDA = ["ed25519", "mldsa65"]


@dataclass
class Resultado:
    ok: bool
    motivo: str = ""
    modo: str = ""            # "hibrida" | "heredada"
    cuerpo: dict = field(default_factory=dict)


def _bytes_del_sobre(sobre: dict) -> bytes:
    """Reconstruye los bytes firmados desde el sobre, sin fiarse de nada.

    Si el sobre dice otra cosa que lo que se firmo, la reconstruccion no
    coincide y las firmas fallan. Por eso el sobre no lleva "mensaje" aparte:
    no hay dos verdades que comparar.
    """
    return mensaje.cabecera(sobre["tipo"], sobre["alg"], sobre["red"]) + \
        mensaje.canonico(sobre["cuerpo"])


def firmar(tipo: str, billetera: llaves.Billetera, red: str = mensaje.RED,
           **campos) -> dict:
    """Construye el sobre y firma con las dos claves."""
    alg = list(HIBRIDA)
    cuerpo = mensaje.cuerpo(tipo, alg, red, **campos)
    sobre = {"v": mensaje.VERSION, "red": red, "tipo": tipo, "alg": alg,
             "cuerpo": cuerpo, "firmas": {}}
    datos = _bytes_del_sobre(sobre)
    sobre["firmas"]["ed25519"] = billetera.firmar_ed(datos).hex()
    sobre["firmas"]["mldsa65"] = billetera.firmar_pq(datos, llaves.CONTEXTO_PQ).hex()
    return sobre


def _tam(valor) -> int:
    if isinstance(valor, (bytes, bytearray)):
        return len(valor)
    if isinstance(valor, str):
        return len(valor.encode("utf-8"))
    try:
        return len(mensaje.canonico(valor))
    except Exception:
        return MAX_SOBRE + 1


def verificar(sobre, claves: dict, direccion_origen: str = "",
              politica: dict | None = None) -> Resultado:
    """Verifica un sobre. `claves` = {'ed25519': hex_pub, 'mldsa65': hex_pub}."""
    # 1. Tamano antes de parsear: un JSON de 50 MB no llega al parser.
    tam = _tam(sobre)
    if tam > MAX_SOBRE:
        return Resultado(False, f"sobre demasiado grande ({tam} B > {MAX_SOBRE})")
    if isinstance(sobre, (bytes, str)):
        try:
            sobre = json.loads(sobre)
        except Exception:
            return Resultado(False, "sobre no es JSON")
    if not isinstance(sobre, dict):
        return Resultado(False, "sobre no es un objeto JSON")

    # 2. Forma.
    for k in ("v", "red", "tipo", "alg", "cuerpo", "firmas"):
        if k not in sobre:
            return Resultado(False, f"falta campo {k}")
    if sobre["v"] != mensaje.VERSION:
        return Resultado(False, f"version no soportada: {sobre['v']!r}")
    tipo = sobre["tipo"]
    if tipo not in mensaje.OPERACIONES:
        return Resultado(False, f"operacion desconocida: {tipo!r}")
    if not isinstance(sobre["alg"], list) or not sobre["alg"]:
        return Resultado(False, "alg vacio")
    try:
        alg = sorted(str(a).strip().lower() for a in sobre["alg"])
    except Exception:
        return Resultado(False, "alg mal formado")
    if len(set(alg)) != len(alg):
        return Resultado(False, "algoritmo repetido")
    if not isinstance(sobre["cuerpo"], dict):
        return Resultado(False, "cuerpo no es objeto")
    if not isinstance(sobre["firmas"], dict):
        return Resultado(False, "firmas no es objeto")

    # 3. Politica por operacion (aqui se corta el downgrade).
    pol = politica or {}
    requeridos = sorted(pol.get(tipo) or HIBRIDA)
    if alg == requeridos:
        modo = "hibrida"
    elif (tipo in HEREDADAS_OK and alg == ["ed25519"]
          and not pol.get("exigir_hibrida_en_todo")):
        modo = "heredada"
    else:
        return Resultado(False, f"algoritmos {alg} no permitidos para '{tipo}' "
                                f"(se exige {requeridos})")

    # 4. Coherencia: los campos firmados tienen que ser los declarados.
    if sobre["cuerpo"].get("alg") != alg:
        return Resultado(False, "alg del cuerpo no coincide con el del sobre")
    if sobre["cuerpo"].get("tipo") != tipo:
        return Resultado(False, "tipo del cuerpo no coincide con el del sobre")
    if sobre["cuerpo"].get("red") != sobre["red"]:
        return Resultado(False, "red del cuerpo no coincide con la del sobre")
    if sobre["cuerpo"].get("v") != mensaje.VERSION:
        return Resultado(False, "version del cuerpo no coincide")
    # El importe tiene que ir en su forma canonica. Si no, "1.5" y "1.50"
    # serian dos firmas validas del mismo dinero y la contabilidad tendria dos
    # textos para el mismo numero.
    if "monto" in sobre["cuerpo"]:
        try:
            canon = mensaje.importe(sobre["cuerpo"]["monto"])
        except mensaje.MensajeInvalido as e:
            return Resultado(False, f"importe invalido en el cuerpo: {e}")
        if canon != sobre["cuerpo"]["monto"]:
            return Resultado(False, "importe no canonico (p.ej. '1.50' en vez de '1.5')")

    # 5. Longitudes exactas antes de tocar criptografia.
    for nombre in alg:
        if nombre not in LARGO:
            return Resultado(False, f"algoritmo no soportado: {nombre}")
        hex_pub = str(claves.get(nombre) or "").strip()
        hex_sig = str(sobre["firmas"].get(nombre) or "").strip()
        if not hex_pub:
            return Resultado(False, f"falta clave publica {nombre}")
        lp, ls = LARGO[nombre]
        try:
            bpub = bytes.fromhex(hex_pub)
            bfirma = bytes.fromhex(hex_sig)
        except (ValueError, TypeError):
            return Resultado(False, f"{nombre}: clave o firma no es hex")
        if len(bpub) != lp:
            return Resultado(False, f"{nombre}: clave de {len(bpub)} B (se esperan {lp})")
        if len(bfirma) != ls:
            return Resultado(False, f"{nombre}: firma de {len(bfirma)} B (se esperan {ls})")

    # 6. Las claves tienen que ser las de la direccion de origen.
    if direccion_origen:
        if not llaves.es_direccion_v2(direccion_origen):
            return Resultado(False, "origen no es una direccion v2")
        if "mldsa65" not in claves or "ed25519" not in claves:
            return Resultado(False, "faltan claves para derivar la direccion")
        try:
            esperada = llaves.direccion_v2(claves["ed25519"], claves["mldsa65"])
        except llaves.ClaveInvalida as e:
            return Resultado(False, f"claves mal formadas: {e}")
        if esperada != direccion_origen.strip().upper():
            return Resultado(False, "las claves no son las duenas de la direccion")

    # 7. Las dos firmas, sobre los mismos bytes.
    datos = _bytes_del_sobre(sobre)
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(claves["ed25519"])).verify(
            bytes.fromhex(sobre["firmas"]["ed25519"]), datos)
    except (InvalidSignature, ValueError):
        return Resultado(False, "firma Ed25519 invalida")
    if "mldsa65" in alg:
        try:
            mldsa.MLDSA65PublicKey.from_public_bytes(
                bytes.fromhex(claves["mldsa65"])).verify(
                bytes.fromhex(sobre["firmas"]["mldsa65"]), datos,
                context=llaves.CONTEXTO_PQ)
        except (InvalidSignature, ValueError):
            return Resultado(False, "firma ML-DSA-65 invalida")
    return Resultado(True, "", modo, sobre["cuerpo"])


class AlmacenNonces:
    """Un nonce vale una vez. En el Core esto ya es UNIQUE(de_nodo, nonce)."""

    def __init__(self):
        self._vistos: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    def consumir(self, de: str, nonce: str) -> bool:
        clave = (de.strip().upper(), nonce.strip())
        with self._lock:              # atomico: no hay ventana entre mirar y meter
            if clave in self._vistos:
                return False
            self._vistos.add(clave)
            return True
