"""El proceso completo: pregunta -> plan -> calculo -> comprobacion -> memoria.

Es el corazon de KUKIA META y cabe en una idea: **ningun numero entra en la
memoria sin una comprobacion independiente, y ninguno entra con mas escalon del
que sus pruebas permiten**.

    ingesta    escribe la pregunta y el criterio de exito
    plan       valida que el paso existe y que los parametros encajan
    ejecuta    corre el calculo dos veces (reproducibilidad)
    comprueba  otro motor recalcula por otra via
    memoria    deduce el escalon y guarda la afirmacion con su rastro
    disputa    si choca con algo ya validado, se marca la contradiccion
"""
from __future__ import annotations

import time

from . import ejecutor, ingesta, memoria, pasos, planificador, verificador


def resolver(con, problema_id: int, paso: str, params: dict,
             autor: str = "meta", propuesta: dict | None = None,
             sabotaje: float | None = None, limitaciones: str = "",
             fuentes: list | None = None) -> dict:
    """Resuelve una pregunta de principio a fin y devuelve todo lo ocurrido."""
    t0 = time.perf_counter()
    prob = memoria.fila(con, "problemas", problema_id)
    if prob is None:
        raise ValueError(f"no existe el problema {problema_id}")

    pregunta = ingesta.preguntas_de(paso, params, autor=autor)
    pid = memoria.nueva_pregunta(con, problema_id, pregunta["texto"],
                                 pregunta["criterio"], pregunta["metodo"])

    plan = planificador.planificar(pregunta, propuesta)
    if not plan["aceptado"]:
        hid = memoria.nueva_hipotesis(con, pid, f"sin resolver: {plan['motivo']}",
                                      autor=plan.get("origen", autor))
        memoria.anotar_conocimiento(
            con, hid, limitaciones="el plan no se pudo ejecutar",
            fuentes=fuentes or [])
        return {"pregunta_id": pid, "hipotesis_id": hid, "estado": "indeterminado",
                "valor": None, "motivo": plan["motivo"], "sabotaje": sabotaje,
                "segundos": time.perf_counter() - t0, "plan": plan}

    hid = memoria.nueva_hipotesis(
        con, pid, f"{pasos.ficha(paso)['descripcion']} con "
        f"{plan['params']}", autor=autor, tolerancia=pasos.ficha(paso)["tolerancia"])
    sid = memoria.nueva_subtarea(con, pid, paso, plan["params"], hipotesis_id=hid)

    fichas = ejecutor.ejecutar(paso, plan["params"], sabotaje=sabotaje)
    ids_ejec = [memoria.registrar_ejecucion(
        con, sid, f["motor"], f["salida"], f["segundos"], f["entorno"],
        ok=f["ok"], error=f["error"]) for f in fichas]

    buenas = [f for f in fichas if f["ok"]]
    if not buenas:
        kid = memoria.anotar_conocimiento(
            con, hid, limitaciones=f"fallo el calculo: {fichas[0]['error']}",
            fuentes=fuentes or [])
        return {"pregunta_id": pid, "hipotesis_id": hid, "estado": "indeterminado",
                "valor": None, "motivo": fichas[0]["error"], "sabotaje": sabotaje,
                "segundos": time.perf_counter() - t0, "plan": plan}

    valor = float(buenas[0]["salida"])
    veredictos = verificador.comprobar(paso, plan["params"], valor,
                                       motor_calculado=buenas[0]["motor"])
    for v in veredictos:
        memoria.registrar_verificacion(
            con, ids_ejec[0], v["motor"], v["valor"],
            pasos.ficha(paso)["tolerancia"], v["veredicto"], v["detalle"])

    con.execute("UPDATE hipotesis SET valor=?, unidad=?, afirmacion=? WHERE id=?",
                (valor, pasos.ficha(paso)["unidad"],
                 f"{pasos.ficha(paso)['descripcion']} con {plan['params']} "
                 f"= {valor:.12g} {pasos.ficha(paso)['unidad']}", hid))
    con.commit()

    kid = memoria.anotar_conocimiento(con, hid, limitaciones=limitaciones,
                                      fuentes=fuentes or [])
    fila = memoria.fila(con, "conocimiento", kid)
    rep, dif_rep = ejecutor.reproducibilidad(fichas)

    return {
        "problema_id": problema_id,
        "pregunta_id": pid,
        "hipotesis_id": hid,
        "conocimiento_id": kid,
        "paso": paso,
        "params": plan["params"],
        "valor": valor,
        "unidad": pasos.ficha(paso)["unidad"],
        "estado": fila["estado"],
        "motores": [m[0] for m in pasos.ficha(paso)["motores"]],
        "verificaciones": veredictos,
        "reproducible": rep,
        "dif_reproducibilidad": dif_rep,
        "sabotaje": sabotaje,
        "segundos": time.perf_counter() - t0,
        "plan": plan,
    }
