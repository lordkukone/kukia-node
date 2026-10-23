"""Planificador: decide como se ataca una pregunta y lo deja por escrito.

Solo admite pasos que esten registrados en `meta/pasos.py` y parametros que
encajen con su ficha (nombres y tipos). No hay ejecucion de texto libre: ni
`eval`, ni `exec`, ni scripts que lleguen de fuera. Un plan que no se puede
comprobar no se acepta, se devuelve el motivo.
"""
from __future__ import annotations

from . import pasos

TIPOS = (int, float, str, list)


def _valida_params(paso: str, params: dict) -> str:
    if not pasos.existe(paso):
        return f"paso desconocido en el registro: {paso!r}"
    ficha = pasos.ficha(paso)
    faltan = [p for p in ficha["parametros"] if p not in params]
    if faltan:
        return f"faltan parametros: {', '.join(faltan)}"
    sobran = [p for p in params if p not in ficha["parametros"]]
    if sobran:
        return f"parametros que ese paso no usa: {', '.join(sobran)}"
    for nombre, valor in params.items():
        if isinstance(valor, bool) or not isinstance(valor, TIPOS):
            return f"parametro {nombre} de tipo no admitido ({type(valor).__name__})"
        if isinstance(valor, (int, float)) and not isinstance(valor, bool):
            if valor != valor or valor in (float("inf"), float("-inf")):
                return f"parametro {nombre} no es un numero finito"
    return ""


def planificar(pregunta: dict, propuesta: dict | None = None) -> dict:
    """Devuelve {aceptado, paso, params, motivo}.

    `propuesta` es opcional: lo que sugiere un modelo ({'paso':..., 'params':...}).
    Si la propuesta no es valida, NO se cae a otra cosa por la cara: se rechaza y
    se explica, para que quede en el registro quien propuso que.
    """
    origen = "meta"
    if propuesta is not None:
        origen = "modelo"
        paso = propuesta.get("paso") or ""
        if not pasos.existe(paso):
            return {"aceptado": False, "paso": paso, "params": {},
                    "motivo": f"el modelo propuso un paso que no existe: {paso!r}",
                    "origen": origen}
        params = propuesta.get("params") or {}
    else:
        paso, params = pregunta["paso"], pregunta["params"]

    problema = _valida_params(paso, params)
    if problema:
        return {"aceptado": False, "paso": paso, "params": params,
                "motivo": problema, "origen": origen}
    return {"aceptado": True, "paso": paso, "params": params, "motivo": "",
            "origen": origen}
