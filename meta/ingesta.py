"""Ingesta: convierte un problema en preguntas investigables.

Lo que hace y lo que NO hace, porque aqui esta el peligro de creerse listo:

  - Formulacion determinista (plantilla): toma el paso cientifico y sus
    parametros, y escribe la pregunta, el criterio de exito y el metodo. Siempre
    esta disponible y nunca inventa nada.
  - Formulacion con modelo de lenguaje (opcional): puede reescribir la pregunta
    para que se entienda mejor. Se le permite **proponer texto y que paso
    cree conveniente**, jamas dar el valor numerico. Si propone un paso que no
    existe en el registro, se descarta y se apunta el motivo.

Una pregunta sin criterio de exito no entra: "¿cuanto sale?" no es investigable;
"¿cuanto sale, con que tolerancia y comprobado como?" si.
"""
from __future__ import annotations

import json

from . import pasos


def criterio_de(paso: str) -> str:
    ficha = pasos.ficha(paso)
    motor_calc = ficha["motores"][0][0]
    motor_ver = ficha["motores"][1][0]
    return (f"el valor calculado con `{motor_calc}` tiene que coincidir con el de "
            f"`{motor_ver}` con tolerancia relativa {ficha['tolerancia']:g}")


def metodo_de(paso: str) -> str:
    ficha = pasos.ficha(paso)
    nombres = ", ".join(m[0] for m in ficha["motores"])
    return (f"calcular {ficha['descripcion'].lower()} con los motores declarados "
            f"({nombres}); comprobar por un camino distinto al usado para calcular")


def preguntas_de(paso: str, params: dict, autor: str = "meta") -> dict:
    """La pregunta, su criterio y su metodo, en forma determinista."""
    if not pasos.existe(paso):
        raise KeyError(f"paso desconocido: {paso}")
    ficha = pasos.ficha(paso)
    texto = f"{ficha['descripcion']} con {json.dumps(params, ensure_ascii=False)}"
    return {
        "texto": texto,
        "criterio": criterio_de(paso),
        "metodo": metodo_de(paso),
        "paso": paso,
        "params": params,
        "autor": autor,
    }


def reescribir_con_modelo(pregunta: dict, proveedor) -> dict:
    """Mejora la redaccion si hay modelo; si no, devuelve la misma.

    El proveedor solo puede devolver texto (y, como mucho, otro nombre de paso).
    Cualquier intento de colar un valor numerico se ignora: el valor lo pone el
    ejecutor, no el modelo.
    """
    if proveedor is None:
        return pregunta
    try:
        propuesta = proveedor.reescribir(pregunta["texto"])
    except Exception:
        return pregunta
    if not isinstance(propuesta, str) or not propuesta.strip():
        return pregunta
    nueva = dict(pregunta)
    nueva["texto"] = propuesta.strip()[:500]
    nueva["autor"] = getattr(proveedor, "nombre", "modelo")
    return nueva
