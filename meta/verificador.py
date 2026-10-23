"""Verificador: comprueba un resultado por un camino distinto al que lo calculo.

Nunca usa el resultado como punto de partida de la cuenta: recalcula por su
propio motor y compara al final. Si no hay ningun motor independiente disponible
(el paso estuviera mal registrado), el veredicto es `indeterminado`, no
"concuerda": en la duda no se aprueba nada.

Regla: un motor que ya se uso para calcular no vale para verificar, aunque este
en la lista. La independencia se exige por nombre y por codigo.
"""
from __future__ import annotations

import time

from . import pasos


def _dif_relativa(valor: float, ref: float) -> float:
    base = abs(ref) if ref != 0 else 1.0
    return abs(valor - ref) / base


def comprobar(paso: str, params: dict, valor_ref: float,
              motor_calculado: str | None = None,
              tolerancia: float | None = None) -> list[dict]:
    """Corre todos los motores de comprobacion y devuelve un veredicto por motor."""
    if not pasos.existe(paso):
        raise KeyError(f"paso desconocido: {paso}")
    ficha = pasos.ficha(paso)
    tol = float(tolerancia if tolerancia is not None else ficha["tolerancia"])
    salida = []

    for nombre, fn in ficha["motores"][1:]:
        if nombre == motor_calculado:
            # No se puede "verificar" con el mismo camino: se anota y se sigue.
            salida.append({"motor": nombre, "valor": None, "diferencia": None,
                           "veredicto": "indeterminado",
                           "detalle": "mismo motor que calculo: no es independiente"})
            continue
        t0 = time.perf_counter()
        try:
            valor = float(fn(params))
            dif = _dif_relativa(valor, valor_ref)
            if dif <= tol:
                veredicto = "concuerda"
                detalle = f"diferencia relativa {dif:.3g} <= {tol:g}"
            else:
                veredicto = "discrepa"
                detalle = f"diferencia relativa {dif:.3g} > {tol:g}"
        except Exception as e:                     # noqa: BLE001
            valor, dif = None, None
            veredicto = "indeterminado"
            detalle = f"{type(e).__name__}: {e}"
        salida.append({"motor": nombre, "valor": valor, "diferencia": dif,
                       "veredicto": veredicto, "detalle": detalle,
                       "segundos": time.perf_counter() - t0})
    return salida
