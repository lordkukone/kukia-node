"""Mensaje canonico de KUKIA v2: lo que firman TODOS los algoritmos.

Regla de oro de una firma hibrida: las dos firmas tienen que ir sobre los
MISMOS bytes. Si Ed25519 firma un JSON y ML-DSA firma otro con el mismo
contenido pero distinto espaciado, un atacante puede hacer que cada
verificador mire una cosa distinta. Aqui se construye una sola vez.

Tres cosas que van DENTRO del mensaje firmado, y por que:

  1. El dominio ("KUKIA-SIG-v2|<red>|"). Sin esto, la misma ristra de bytes
     firmada para "transferir" vale como "sellar" en otra red: una firma
     robada se recicla. Con esto, no.
  2. La lista de algoritmos (`alg`). Si se pudiera quitar la firma poscuantica
     y dejar solo la Ed25519, el atacante habria hecho un downgrade. Como
     `alg` va firmado, quitar la firma poscuantica deja el campo mintiendo y
     el verificador lo rechaza; cambiar el campo invalida las dos firmas.
  3. La version del protocolo (`v`). Permite que un Core viejo rechace lo que
     no entiende en vez de interpretarlo a la ligera.

Los importes van como TEXTO decimal canonico, no como float. Es el mismo
motivo del hallazgo D1 de la auditoria del 25-10: un float puede redondear
entre lo que se firma y lo que se mueve, y entonces la firma no cubre el
importe real.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

VERSION = 2
RED = "kukia-mainnet"
PREFIJO = "KUKIA-SIG-v2"

# Operaciones que hoy existen en el Core y que se firman.
OPERACIONES = {
    "transferir",      # billetera -> billetera / nodo
    "cliente",         # prepago de una tarea de cliente
    "pendiente",       # pedir la cola de tareas
    "resultado",       # entrega de un resultado de tarea
    "telemetria",      # latido de GPU
    "reinicio",        # el worker pide reiniciar su propio nodo
    "cobro",           # el worker pide cobrar a su billetera
    "sello",           # sello del ledger
    "vincular",        # migracion de una billetera v1 a una v2
}

# Tope de decimales del importe: el mismo que acepta el Core hoy (4). El suelo
# transferible es 0,0001, asi que nadie legitimo pierde nada.
DECIMALES = 4
SUMINISTRO_MAX = Decimal("100000000")

_RE_DECIMAL = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]{1,4})?$")


class MensajeInvalido(ValueError):
    """El contenido no se puede firmar: mejor decirlo que firmar basura."""


def importe(valor) -> str:
    """Importe canonico como texto. Acepta str, int, Decimal o float.

    Un float SOLO se acepta si su representacion decimal corta cuadra; si no,
    se lanza. Asi no se cuela un 0.1 + 0.2 = 0.30000000000000004 en una firma.
    """
    if isinstance(valor, bool):
        raise MensajeInvalido("importe booleano")
    if isinstance(valor, float):
        if valor != valor or valor in (float("inf"), float("-inf")):
            raise MensajeInvalido("importe no finito")
        valor = Decimal(repr(valor))
    if isinstance(valor, Decimal):
        d = valor
    else:
        try:
            d = Decimal(str(valor))
        except (InvalidOperation, ValueError):
            raise MensajeInvalido(f"importe no numerico: {valor!r}")
    if d != d or d.is_infinite():
        raise MensajeInvalido("importe no finito")
    if d <= 0:
        raise MensajeInvalido("importe debe ser positivo")
    if d > SUMINISTRO_MAX:
        raise MensajeInvalido("importe mayor que el suministro total")
    if -d.as_tuple().exponent > DECIMALES:
        raise MensajeInvalido(f"importe con mas de {DECIMALES} decimales")
    texto = format(d.normalize(), "f")
    if not _RE_DECIMAL.match(texto):
        raise MensajeInvalido(f"importe no canonico: {texto}")
    return texto


def _texto(valor, nombre: str, maximo: int = 200) -> str:
    if valor is None:
        raise MensajeInvalido(f"falta {nombre}")
    s = str(valor).strip()
    if not s:
        raise MensajeInvalido(f"{nombre} vacio")
    if len(s) > maximo:
        raise MensajeInvalido(f"{nombre} demasiado largo ({len(s)} > {maximo})")
    if any(ord(c) < 32 for c in s):
        raise MensajeInvalido(f"{nombre} con caracteres de control")
    return s


def canonico(d: dict) -> bytes:
    """JSON estable: mismas claves -> mismos bytes, siempre."""
    return json.dumps(d, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def cuerpo(tipo: str, alg: list[str], red: str = RED, **campos) -> dict:
    """El objeto que se firma, con las claves ordenadas y los importes en texto.

    `alg` es la lista de algoritmos que firman, en orden fijo. El orden se
    normaliza (sorted) para que dos implementaciones distintas produzcan los
    mismos bytes y no se peleen por "ed25519+mldsa65" vs "mldsa65+ed25519".
    """
    if tipo not in OPERACIONES:
        raise MensajeInvalido(f"operacion desconocida: {tipo!r}")
    if not isinstance(alg, (list, tuple)) or not alg:
        raise MensajeInvalido("alg vacio")
    algoritmos = sorted(str(a).strip().lower() for a in alg)
    if len(set(algoritmos)) != len(algoritmos):
        raise MensajeInvalido("algoritmo repetido en alg")

    d = {"v": VERSION, "red": _texto(red, "red", 40), "tipo": tipo,
         "alg": algoritmos}
    for k, v in campos.items():
        if k in d:
            raise MensajeInvalido(f"campo reservado: {k}")
        if k == "monto":
            d[k] = importe(v)
        elif k == "nonce":
            d[k] = _texto(v, "nonce", 64)
        elif isinstance(v, bool):
            raise MensajeInvalido(f"{k} booleano (no se firman booleanos sueltos)")
        elif isinstance(v, (int, float, Decimal)):
            raise MensajeInvalido(f"{k} numerico: usa texto canonico")
        else:
            d[k] = _texto(v, k)
    return d


def construir(tipo: str, alg: list[str], red: str = RED, **campos) -> bytes:
    """Devuelve los bytes exactos del cuerpo que hay que firmar."""
    return canonico(cuerpo(tipo, alg, red, **campos))


def cabecera(tipo: str, alg: list[str], red: str = RED) -> bytes:
    """Prefijo de dominio, para poder rechazar rapido sin parsear el cuerpo."""
    algoritmos = ",".join(sorted(a.strip().lower() for a in alg))
    return f"{PREFIJO}|{red}|{tipo}|{algoritmos}|".encode("utf-8")


def mensaje(tipo: str, alg: list[str], red: str = RED, **campos) -> bytes:
    """Dominio + cuerpo canonico. Esto es lo que se pasa a cada firmador."""
    return cabecera(tipo, alg, red) + construir(tipo, alg, red, **campos)
