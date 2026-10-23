"""Memoria de KUKIA META: guarda el rastro y calcula el escalon epistemico.

Nada de lo que entra aqui se cree por la cara. `estado_de()` mira las pruebas
guardadas y deduce el escalon; el que escribe NO puede decir "esto esta
validado", tiene que haber ejecuciones y verificaciones que lo demuestren.

Estados y su prueba (todo deducido, no declarado):

    hipotesis    sin ejecucion correcta
    calculado    1 ejecucion correcta
    reproducido  2 ejecuciones correctas del mismo motor, mismo numero
    validado     + 1 verificacion concordante de OTRO motor
    confirmado   + 2 verificaciones concordantes de motores distintos
    refutado     alguna verificacion discrepa (y se conserva el fallo)
    indeterminado se intento y no se pudo calcular

Contradicciones: dos conocimientos numericos de la misma pregunta que no pueden
ser ciertos a la vez se marcan, y la pregunta pasa a `en_disputa`. No se borra
ninguno de los dos: se enseña el choque.
"""
from __future__ import annotations

import json
import time

from . import esquema

TOL_REPRODUCIBLE = 0.0      # mismo motor: tiene que dar exactamente lo mismo
TOL_REFUTA = 1e-9           # discrepancia relativa que ya se considera fallo


def _ahora() -> float:
    return time.time()


# --------------------------------------------------------------------------
#  Alta de piezas
# --------------------------------------------------------------------------
def nuevo_problema(con, titulo: str, enunciado: str, dominio: str,
                   autor: str = "kuki", notas: str = "") -> int:
    cur = con.execute(
        "INSERT INTO problemas(titulo,enunciado,dominio,autor,estado,notas,creado)"
        " VALUES(?,?,?,?,'abierto',?,?)",
        (titulo, enunciado, dominio, autor, notas, _ahora()))
    con.commit()
    return int(cur.lastrowid)


def nueva_pregunta(con, problema_id: int, texto: str, criterio: str,
                   metodo: str) -> int:
    cur = con.execute(
        "INSERT INTO preguntas(problema_id,texto,criterio,metodo,estado,creado)"
        " VALUES(?,?,?,?,'abierta',?)",
        (problema_id, texto, criterio, metodo, _ahora()))
    con.commit()
    return int(cur.lastrowid)


def nueva_hipotesis(con, pregunta_id: int, afirmacion: str, autor: str = "kuki",
                    valor: float | None = None, unidad: str = "",
                    tolerancia: float = 1e-6) -> int:
    cur = con.execute(
        "INSERT INTO hipotesis(pregunta_id,afirmacion,autor,valor,unidad,"
        "tolerancia,estado,creado) VALUES(?,?,?,?,?,?,'hipotesis',?)",
        (pregunta_id, afirmacion, autor, valor, unidad, tolerancia, _ahora()))
    con.commit()
    return int(cur.lastrowid)


def nueva_subtarea(con, pregunta_id: int, paso: str, params: dict,
                   hipotesis_id: int | None = None) -> int:
    cur = con.execute(
        "INSERT INTO subtareas(pregunta_id,hipotesis_id,paso,params,estado,creado)"
        " VALUES(?,?,?,?,'pendiente',?)",
        (pregunta_id, hipotesis_id, paso,
         json.dumps(params, ensure_ascii=False), _ahora()))
    con.commit()
    return int(cur.lastrowid)


def registrar_ejecucion(con, subtarea_id: int, motor: str, salida: float | None,
                        segundos: float, entorno: str, ok: bool = True,
                        error: str = "") -> int:
    cur = con.execute(
        "INSERT INTO ejecuciones(subtarea_id,motor,salida,segundos,entorno,ok,error,creado)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (subtarea_id, motor, salida, segundos, entorno, 1 if ok else 0, error, _ahora()))
    con.execute("UPDATE subtareas SET estado=? WHERE id=?",
                ("ejecutada" if ok else "fallida", subtarea_id))
    con.commit()
    return int(cur.lastrowid)


def registrar_verificacion(con, ejecucion_id: int, motor: str, valor: float | None,
                           tolerancia: float, veredicto: str,
                           detalle: str = "") -> int:
    if veredicto not in ("concuerda", "discrepa", "indeterminado"):
        raise ValueError(f"veredicto no valido: {veredicto}")
    fila = con.execute("SELECT salida FROM ejecuciones WHERE id=?",
                       (ejecucion_id,)).fetchone()
    dif = None
    if valor is not None and fila is not None and fila["salida"] is not None:
        ref = abs(float(fila["salida"])) or 1.0
        dif = abs(float(valor) - float(fila["salida"])) / ref
    cur = con.execute(
        "INSERT INTO verificaciones(ejecucion_id,motor,valor,diferencia,tolerancia,"
        "veredicto,detalle,creado) VALUES(?,?,?,?,?,?,?,?)",
        (ejecucion_id, motor, valor, dif, tolerancia, veredicto, detalle, _ahora()))
    con.commit()
    return int(cur.lastrowid)


# --------------------------------------------------------------------------
#  El escalon, deducido de las pruebas
# --------------------------------------------------------------------------
def _filas_de(con, hipotesis_id: int):
    """Ejecuciones y verificaciones de las subtareas de UNA hipotesis.

    Cada afirmacion tiene sus propias pruebas: si dos hipotesis de la misma
    pregunta se mezclaran, una refutaria a la otra sin querer y no se podria
    distinguir cual falla.
    """
    sub = con.execute(
        "SELECT id FROM subtareas WHERE hipotesis_id=?", (hipotesis_id,)).fetchall()
    if not sub:
        return [], []
    ids = [r["id"] for r in sub]
    marcas = ",".join("?" * len(ids))
    eje = con.execute(
        f"SELECT * FROM ejecuciones WHERE subtarea_id IN ({marcas}) ORDER BY id",
        ids).fetchall()
    ver = con.execute(
        f"SELECT * FROM verificaciones WHERE ejecucion_id IN"
        f" (SELECT id FROM ejecuciones WHERE subtarea_id IN ({marcas})) ORDER BY id",
        ids).fetchall()
    return eje, ver


def estado_de(con, hipotesis_id: int) -> str:
    eje, ver = _filas_de(con, hipotesis_id)
    buenas = [e for e in eje if e["ok"] and e["salida"] is not None]
    if not eje:
        return "hipotesis"
    if any(v["veredicto"] == "discrepa" for v in ver):
        return "refutado"
    if not buenas:
        return "indeterminado"

    # reproducido: dos ejecuciones del mismo motor con el mismo numero
    por_motor: dict[str, list[float]] = {}
    for e in buenas:
        por_motor.setdefault(e["motor"], []).append(float(e["salida"]))
    reproducible = any(
        len(v) >= 2 and max(v) - min(v) <= TOL_REPRODUCIBLE + 1e-12 for v in por_motor.values())
    if not reproducible:
        return "calculado"

    motores_eje = {e["motor"] for e in buenas}
    concordantes = [v for v in ver
                    if v["veredicto"] == "concuerda" and v["motor"] not in motores_eje]
    distintos = {v["motor"] for v in concordantes}
    if len(distintos) >= 2:
        return "confirmado"
    if len(distintos) >= 1:
        return "validado"
    return "reproducido"


def estado_de_pregunta(con, pregunta_id: int) -> str:
    """El peor caso manda: si algo se refuto, la pregunta no esta resuelta."""
    filas = con.execute("SELECT id FROM hipotesis WHERE pregunta_id=?",
                        (pregunta_id,)).fetchall()
    if not filas:
        return "abierta"
    estados = [estado_de(con, r["id"]) for r in filas]
    for malo in ("refutado", "indeterminado"):
        if malo in estados:
            return malo
    if all(e == "confirmado" for e in estados):
        return "confirmado"
    if all(e in ("validado", "confirmado") for e in estados):
        return "validado"
    return "en_curso"


# --------------------------------------------------------------------------
#  Conocimiento y contradicciones
# --------------------------------------------------------------------------
def anotar_conocimiento(con, hipotesis_id: int, limitaciones: str = "",
                        fuentes: list | None = None) -> int:
    """Guarda la afirmacion con el escalon QUE SE DEDUCE de las pruebas."""
    h = con.execute("SELECT * FROM hipotesis WHERE id=?", (hipotesis_id,)).fetchone()
    if h is None:
        raise ValueError(f"no existe la hipotesis {hipotesis_id}")
    estado = estado_de(con, hipotesis_id)
    con.execute("UPDATE hipotesis SET estado=? WHERE id=?", (estado, hipotesis_id))
    eje, ver = _filas_de(con, hipotesis_id)
    usados = sorted({e["motor"] for e in eje if e["ok"]}
                    | {v["motor"] for v in ver if v["veredicto"] == "concuerda"})
    cur = con.execute(
        "INSERT INTO conocimiento(pregunta_id,afirmacion,valor,unidad,estado,motores,"
        "limitaciones,fuentes,creado) VALUES(?,?,?,?,?,?,?,?,?)",
        (h["pregunta_id"], h["afirmacion"], h["valor"], h["unidad"], estado,
         json.dumps(usados), limitaciones,
         json.dumps(fuentes or [], ensure_ascii=False), _ahora()))
    con.commit()
    detectar_contradicciones(con)
    return int(cur.lastrowid)


def detectar_contradicciones(con) -> list[dict]:
    """Dos numeros validados de la misma pregunta que no caben juntos."""
    filas = con.execute(
        "SELECT * FROM conocimiento WHERE valor IS NOT NULL"
        " AND estado IN ('validado','confirmado') ORDER BY pregunta_id, id").fetchall()
    por_pregunta: dict[int, list] = {}
    for f in filas:
        por_pregunta.setdefault(f["pregunta_id"], []).append(f)

    halladas = []
    for pregunta_id, grupo in por_pregunta.items():
        for i in range(len(grupo)):
            for j in range(i + 1, len(grupo)):
                a, b = grupo[i], grupo[j]
                ref = max(abs(float(a["valor"])), abs(float(b["valor"])), 1e-300)
                dif = abs(float(a["valor"]) - float(b["valor"])) / ref
                if dif <= 1e-9:
                    continue
                ya = con.execute(
                    "SELECT id FROM contradicciones WHERE conocimiento_a=? AND"
                    " conocimiento_b=?", (a["id"], b["id"])).fetchone()
                if ya:
                    continue
                motivo = (f"misma pregunta, valores incompatibles: "
                          f"{a['valor']!r} ({a['estado']}) vs {b['valor']!r} "
                          f"({b['estado']}), diferencia relativa {dif:.3g}")
                con.execute(
                    "INSERT INTO contradicciones(pregunta_id,conocimiento_a,"
                    "conocimiento_b,motivo,estado,creado) VALUES(?,?,?,?,'abierta',?)",
                    (pregunta_id, a["id"], b["id"], motivo, _ahora()))
                halladas.append({"pregunta_id": pregunta_id, "a": a["id"],
                                 "b": b["id"], "motivo": motivo})
    if halladas:
        con.execute(
            "UPDATE preguntas SET estado='en_disputa' WHERE id IN (%s)"
            % ",".join("?" * len({h["pregunta_id"] for h in halladas})),
            sorted({h["pregunta_id"] for h in halladas}))
    con.commit()
    return halladas


def fila(con, tabla: str, ident: int):
    return con.execute(f"SELECT * FROM {tabla} WHERE id=?", (ident,)).fetchone()


def resumen(con) -> dict:
    def cuenta(sql: str) -> int:
        return int(con.execute(sql).fetchone()[0])

    return {
        "problemas": cuenta("SELECT COUNT(*) FROM problemas"),
        "preguntas": cuenta("SELECT COUNT(*) FROM preguntas"),
        "hipotesis": cuenta("SELECT COUNT(*) FROM hipotesis"),
        "ejecuciones": cuenta("SELECT COUNT(*) FROM ejecuciones"),
        "verificaciones": cuenta("SELECT COUNT(*) FROM verificaciones"),
        "conocimiento": cuenta("SELECT COUNT(*) FROM conocimiento"),
        "contradicciones": cuenta("SELECT COUNT(*) FROM contradicciones"),
        "por_estado": {e: cuenta(f"SELECT COUNT(*) FROM conocimiento WHERE estado='{e}'")
                       for e in esquema.TODOS},
    }
