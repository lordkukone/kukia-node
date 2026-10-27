"""Puente entre el Core y las firmas hibridas (Ed25519 + ML-DSA-65). Fase C.

Este modulo NO toca la base de datos ni el dinero: solo verifica sobres y
traduce el resultado a algo que el Core pueda usar. Se ha separado a proposito
para que la parte criptografica se pueda probar sin arrancar el Core y para que
el parche del Core sea pequeño y revisable.

Tres decisiones que conviene tener por escrito:

1. **Apagado por defecto.** `KUKIA_PQC` vale "0" si nadie dice lo contrario.
   Sin la variable, el Core se comporta exactamente como antes y los endpoints
   nuevos solo contestan "desactivado". Una rama experimental no debe cambiar
   el comportamiento de un Core que ya esta en la calle.

2. **El camino v1 no se toca.** `/transferir` (firma Ed25519 suelta sobre una
   cadena de texto) sigue siendo el mismo codigo, byte a byte. Lo nuevo es un
   endpoint paralelo. Asi una regresion en la integracion no puede romper las
   transferencias que hoy funcionan.

3. **El importe se comprueba dos veces.** La firma cubre un decimal canonico
   en texto; el ledger necesita un float. Se verifica que el float, vuelto a
   texto, es EXACTAMENTE el decimal firmado. Es el hallazgo D1 de la auditoria
   del 25-10: si el float deriva, la firma deja de cubrir el importe real.
"""

from __future__ import annotations

import hashlib
import json
import os
from decimal import Decimal

from . import firma_hibrida, llaves, mensaje

HIBRIDA = list(firma_hibrida.HIBRIDA)
MAX_SOBRE = firma_hibrida.MAX_SOBRE
RED = mensaje.RED

_FALSO = ("", "0", "no", "false", "off")

ACTIVO = os.environ.get("KUKIA_PQC", "0").strip().lower() not in _FALSO

# Politica por operacion. 'transferir' exige las dos firmas SIEMPRE: es
# dinero. Las operaciones heredadas (telemetria, pendiente) siguen valiendo
# con Ed25519 sola durante la transicion, y el resultado lo dice ('heredada')
# para que quede en el log: permitido no es lo mismo que silencioso.
POLITICA: dict = {"transferir": HIBRIDA}
if os.environ.get("KUKIA_PQC_EXIGIR_TODO", "0").strip() == "1":
    POLITICA["exigir_hibrida_en_todo"] = True


class Rechazo(Exception):
    """Sobre rechazado. El motivo va escrito: quien lo manda sabe que corregir."""


def estado() -> dict:
    """Lo que un cliente necesita para saber si puede usar el camino v2."""
    return {
        "activo": ACTIVO,
        "version_mensaje": mensaje.VERSION,
        "red": RED,
        "algoritmos": HIBRIDA,
        "hibrida_obligatoria_en": sorted(k for k, v in POLITICA.items() if isinstance(v, list)),
        "exigir_hibrida_en_todo": bool(POLITICA.get("exigir_hibrida_en_todo")),
        "direcciones_v2": True,
        "max_sobre_bytes": MAX_SOBRE,
        "motivo": "" if ACTIVO else "desactivado: arranca el Core con KUKIA_PQC=1",
    }


def es_direccion_v2(d) -> bool:
    return llaves.es_direccion_v2(d)


def abrir(sobre):
    """JSON -> dict, con el tope de tamaño ANTES de parsear."""
    if isinstance(sobre, (bytes, bytearray)):
        if len(sobre) > MAX_SOBRE:
            raise Rechazo(f"sobre demasiado grande ({len(sobre)} B > {MAX_SOBRE})")
        crudo = bytes(sobre)
    elif isinstance(sobre, str):
        crudo = sobre.encode("utf-8")
        if len(crudo) > MAX_SOBRE:
            raise Rechazo(f"sobre demasiado grande ({len(crudo)} B > {MAX_SOBRE})")
    else:
        crudo = b""
    if crudo:
        try:
            sobre = json.loads(crudo.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise Rechazo(f"el sobre no es JSON: {e}")
    if not isinstance(sobre, dict):
        raise Rechazo("el sobre tiene que ser un objeto JSON")
    return sobre


def sobre_hash(sobre: dict) -> str:
    """Huella del sobre completo, para poder auditarlo despues sin guardarlo.

    Se guarda el hash y no el sobre: el sobre lleva las firmas (3,4 KB por
    transferencia) y el ledger no es un archivo de adjuntos. Con el hash, el
    cliente que conserve su copia puede demostrar que ESTE es su sobre.
    """
    return "sha256:" + hashlib.sha256(mensaje.canonico(sobre)).hexdigest()


def monto(float_o_texto):
    """Decimal firmado -> float del ledger, sin deriva.

    `mensaje.importe` ya normaliza; aqui se comprueba el viaje de vuelta. Un
    valor que no vuelve identico se rechaza en vez de escribirse redondeado:
    mas vale una transferencia rechazada que un asiento que no cuadra con su
    firma.
    """
    try:
        canon = mensaje.importe(float_o_texto)
    except mensaje.MensajeInvalido as e:
        raise Rechazo(f"importe invalido: {e}")
    valor = round(float(Decimal(canon)), 4)
    try:
        vuelta = mensaje.importe(valor)
    except mensaje.MensajeInvalido as e:
        raise Rechazo(f"el importe {canon} no se puede llevar al ledger: {e}")
    if vuelta != canon:
        raise Rechazo(f"deriva numerica: se firmo {canon} y se escribira {vuelta}")
    return valor


def transferencia(sobre, pub_ed: str = "", pub_pq: str = "") -> dict:
    """Verifica un sobre 'transferir' hibrido y devuelve el movimiento limpio.

    Lanza `Rechazo` con el motivo concreto. No escribe nada.
    """
    if not ACTIVO:
        raise Rechazo(estado()["motivo"])
    sobre = abrir(sobre)
    tipo = sobre.get("tipo")
    if tipo != "transferir":
        raise Rechazo(f"este endpoint solo acepta 'transferir', no {tipo!r}")

    cuerpo = sobre.get("cuerpo")
    if not isinstance(cuerpo, dict):
        raise Rechazo("el cuerpo del sobre no es un objeto")
    de = str(cuerpo.get("de") or "").strip().upper()
    if not de:
        raise Rechazo("el cuerpo firmado no dice el origen (de)")
    # Una direccion KUK... es una billetera v1 sin clave poscuantica: su sitio
    # es /transferir. Mezclarlas dejaria un camino donde la PQ se puede saltar.
    if not llaves.es_direccion_v2(de):
        raise Rechazo(f"el origen {de} no es una direccion KQ1... v2 "
                      f"(una billetera v1 transfiere por /transferir)")

    claves = {"ed25519": (pub_ed or "").strip().lower(),
              "mldsa65": (pub_pq or "").strip().lower()}
    r = firma_hibrida.verificar(sobre, claves, direccion_origen=de, politica=POLITICA)
    if not r.ok:
        raise Rechazo(r.motivo)

    cuerpo = r.cuerpo
    a = str(cuerpo.get("a") or "").strip()
    if not a:
        raise Rechazo("el cuerpo firmado no dice el destino (a)")
    if a.upper() == de:
        raise Rechazo("origen y destino iguales")
    if len(a) > 128:
        raise Rechazo("destino demasiado largo")
    nonce = str(cuerpo.get("nonce") or "").strip()
    if not nonce:
        raise Rechazo("falta el nonce")
    # Las direcciones se guardan en mayusculas en el ledger; los ids de nodo
    # son hex en minusculas. Se normaliza solo lo que es una direccion.
    if llaves.familia(a):
        a = a.upper()
    return {"de": de, "a": a, "monto": monto(cuerpo.get("monto")),
            "monto_texto": cuerpo.get("monto"), "nonce": nonce,
            "modo": r.modo, "sobre_hash": sobre_hash(sobre),
            "familia_destino": llaves.familia(a) or "nodo"}
