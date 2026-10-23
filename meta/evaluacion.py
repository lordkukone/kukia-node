"""Evaluacion: ¿esto sirve para algo? Se mide, no se opina.

Tres numeros que se calculan de verdad:

  1. Aciertos. Cada problema del banco tiene respuesta conocida, calculada fuera
     de nuestro codigo. Si el resultado validado no coincide, es un fallo.
  2. Deteccion de errores. Se sabotea el calculo (+5 %) y se mira si el
     verificador independiente lo caza. Un sistema que no detecta un 5 % de
     error no verifica nada.
  3. Estado epistemico. Cuantos quedan en `validado`/`confirmado`, cuantos
     `refutado` y cuantos `indeterminado`. Un sistema que dice "validado" sin
     pruebas se ve aqui.

Nada de esto mejora por guardar conversaciones: mejora si sube el numero de
aciertos y baja el de refutados sin tocar el banco.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import memoria, proceso

RAIZ = Path(__file__).resolve().parent.parent
BANCO_POR_DEFECTO = RAIZ / "meta" / "bancos" / "base.json"
VALIDOS = ("validado", "confirmado")


def cargar(ruta=None) -> dict:
    ruta = Path(ruta) if ruta else BANCO_POR_DEFECTO
    return json.loads(ruta.read_text(encoding="utf-8"))


def _dif(valor: float, esperado: float) -> float:
    base = abs(esperado) if esperado != 0 else 1.0
    return abs(valor - esperado) / base


def evaluar(con, ruta_banco=None, autor: str = "meta",
            sabotear: bool = True, ver: bool = True) -> dict:
    banco = cargar(ruta_banco)
    t0 = time.perf_counter()
    filas = []

    for p in banco["problemas"]:
        pid = memoria.nuevo_problema(con, p["titulo"], p["enunciado"], p["dominio"],
                                     autor=autor,
                                     notas=f"banco {banco['banco']} v{banco['version']}")
        r = proceso.resolver(con, pid, p["paso"], p["params"], autor=autor,
                             fuentes=[{"tipo": "formula", "referencia": p["fuente"]}])

        correcto = (r["valor"] is not None
                    and _dif(r["valor"], p["valor_esperado"]) <= p["tolerancia"] * 10)
        fila = {
            "id": p["id"], "dominio": p["dominio"], "paso": p["paso"],
            "estado": r["estado"], "valor": r["valor"],
            "esperado": p["valor_esperado"],
            "dif_relativa": None if r["valor"] is None else _dif(r["valor"], p["valor_esperado"]),
            "correcto": correcto,
            "segundos": round(r["segundos"], 4),
            "motores": r.get("motores"),
        }
        if sabotear:
            det = _probar_sabotaje(con, pid, p, autor)
            fila["sabotaje_detectado"] = det["detectado"]
            fila["sabotaje_estado"] = det["estado"]
        filas.append(fila)
        if ver:
            marca = "OK " if correcto and fila["estado"] in VALIDOS else "MAL"
            print(f"  [{marca}] {p['id']:24s} {fila['estado']:<13} "
                  f"valor={r['valor']!r}"[:110])

    segundos = time.perf_counter() - t0
    validos = [f for f in filas if f["estado"] in VALIDOS]
    aciertos = [f for f in validos if f["correcto"]]
    fallos = [f for f in filas if f["estado"] == "refutado" or (f["valor"] is not None and not f["correcto"])]
    indeterminados = [f for f in filas if f["estado"] == "indeterminado"]
    sab = [f for f in filas if f.get("sabotaje_detectado") is not None]
    cazados = [f for f in sab if f["sabotaje_detectado"]]

    resumen = {
        "banco": banco["banco"],
        "version": banco["version"],
        "problemas": len(filas),
        "validados": len(validos),
        "aciertos": len(aciertos),
        "fallos": len(fallos),
        "indeterminados": len(indeterminados),
        "sabotajes": len(sab),
        "sabotajes_detectados": len(cazados),
        "segundos": round(segundos, 2),
        "detalle": filas,
    }
    con.execute(
        "INSERT INTO evaluaciones(banco,version,aciertos,fallos,indeterminados,"
        "segundos,detalle,creado) VALUES(?,?,?,?,?,?,?,?)",
        (resumen["banco"], resumen["version"], resumen["aciertos"], resumen["fallos"],
         resumen["indeterminados"], resumen["segundos"],
         json.dumps(filas, ensure_ascii=False), time.time()))
    con.commit()
    return resumen


def _probar_sabotaje(con, problema_id: int, p: dict, autor: str) -> dict:
    """Repite el problema con el calculo desviado un 5 % y mira si se caza."""
    r = proceso.resolver(con, problema_id, p["paso"], p["params"], autor=autor,
                         sabotaje=0.05, limitaciones="prueba de deteccion de errores")
    discrepa = any(v["veredicto"] == "discrepa" for v in r["verificaciones"])
    return {"detectado": bool(r["estado"] == "refutado" or discrepa),
            "estado": r["estado"]}


def por_dominio(resumen: dict) -> dict:
    salida: dict[str, dict] = {}
    for f in resumen["detalle"]:
        d = salida.setdefault(f["dominio"], {"problemas": 0, "validados": 0,
                                             "aciertos": 0})
        d["problemas"] += 1
        if f["estado"] in VALIDOS:
            d["validados"] += 1
        if f["correcto"] and f["estado"] in VALIDOS:
            d["aciertos"] += 1
    return salida
