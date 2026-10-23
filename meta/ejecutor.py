"""Ejecutor: corre el motor de calculo y deja constancia de como se corrio.

Cada paso se ejecuta DOS veces seguidas. No es por gusto: un resultado que no
sale igual dos veces no es reproducible, y sin reproducibilidad no se puede ni
plantear como resultado. La comparacion se hace aqui mismo y se guarda la
diferencia; el escalon lo decide `memoria.estado_de()` leyendo esas pruebas.

El ejecutor no verifica y no decide: solo calcula y anota. Verificar es otro
modulo (`verificador.py`) y con otro camino.
"""
from __future__ import annotations

import importlib
import platform
import sys
import time

from . import pasos


def _entorno() -> str:
    partes = [f"python {platform.python_version()}"]
    for nombre in ("numpy", "scipy", "sympy"):
        try:
            mod = importlib.import_module(nombre)
            partes.append(f"{nombre} {mod.__version__}")
        except Exception:
            partes.append(f"{nombre} -")
    partes.append(f"pasos {pasos.huella_pasos()}")
    return " | ".join(partes)


def ejecutar(paso: str, params: dict, repeticiones: int = 2,
             sabotaje: float | None = None) -> list[dict]:
    """Corre el motor 0 las veces pedidas y devuelve una ficha por ejecucion.

    `sabotaje` solo lo usa la prueba de deteccion de errores: desvia el
    resultado un tanto por uno para comprobar que el verificador lo caza.
    """
    if not pasos.existe(paso):
        raise KeyError(f"paso desconocido: {paso}")
    ficha = pasos.ficha(paso)
    nombre, fn = ficha["motores"][0]
    entorno = _entorno()
    salidas = []

    for _ in range(max(1, repeticiones)):
        t0 = time.perf_counter()
        try:
            valor = float(fn(params))
            if sabotaje:
                valor *= (1.0 + sabotaje)
            segundos = time.perf_counter() - t0
            salidas.append({"motor": nombre, "salida": valor, "segundos": segundos,
                            "entorno": entorno, "ok": True, "error": ""})
        except Exception as e:                     # noqa: BLE001 - se guarda tal cual
            segundos = time.perf_counter() - t0
            salidas.append({"motor": nombre, "salida": None, "segundos": segundos,
                            "entorno": entorno, "ok": False,
                            "error": f"{type(e).__name__}: {e}"})
    return salidas


def reproducibilidad(fichas: list[dict]) -> tuple[bool, float]:
    """¿Dan lo mismo todas las ejecuciones correctas? Devuelve (si, diferencia)."""
    buenas = [f["salida"] for f in fichas if f["ok"] and f["salida"] is not None]
    if len(buenas) < 2:
        return False, float("inf")
    ref = abs(buenas[0]) or 1.0
    dif = max(abs(v - buenas[0]) for v in buenas) / ref
    return dif <= 1e-12, dif


def python_exe() -> str:
    return sys.executable
