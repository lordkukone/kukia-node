"""Identidad persistente del minero KUKIAMINER V1.0 BETA (rama).
Evita crear un nodo nuevo en cada arranque: guarda nodo_id + clave y los
reutiliza. Si el Core pierde el nodo, se re-registra con la MISMA clave.
"""
import json
from pathlib import Path

RUTA = Path(__file__).parent / "identidad_minero.json"


def cargar():
    try:
        return json.loads(RUTA.read_text(encoding="utf-8"))
    except Exception:
        return {}


def guardar(datos: dict):
    try:
        RUTA.write_text(json.dumps(datos, indent=1), encoding="utf-8")
    except Exception:
        pass


def set_nodo(nombre_worker: str, nodo_id: str, clave_publica: str):
    d = cargar()
    d[nombre_worker] = {"nodo_id": nodo_id, "clave_publica": clave_publica}
    guardar(d)


def get_nodo(nombre_worker: str):
    return cargar().get(nombre_worker, {})
