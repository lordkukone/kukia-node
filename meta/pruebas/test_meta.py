"""Pruebas de KUKIA META. Sin pytest: se ejecuta y devuelve 0 o 1.

    python meta/pruebas/test_meta.py

Lo que se comprueba no es "que arranca", es que NO se puede mentir:

  - el escalon no se puede saltar (sin pruebas no hay validado)
  - un valor equivocado queda refutado, no aprobado
  - el verificador no puede usar el mismo motor que calculo
  - el planificador rechaza pasos y parametros inventados
  - dos verdades incompatibles se marcan como contradiccion
  - el banco entero acierta y el 5 % de error se caza
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RAIZ))

from meta import (ejecutor, esquema, evaluacion, memoria, pasos,  # noqa: E402
                  planificador, proceso, verificador)

fallos: list[str] = []


def check(cond: bool, texto: str) -> None:
    print(f"[{'OK' if cond else 'FALLO'}] {texto}")
    if not cond:
        fallos.append(texto)


def _con(tmp: Path):
    return esquema.conexion(tmp / "meta_test.db")


def prueba_escalones(con) -> None:
    pid = memoria.nuevo_problema(con, "escalones", "x", "fisica")
    preg = memoria.nueva_pregunta(con, pid, "¿?", "criterio", "metodo")
    hid = memoria.nueva_hipotesis(con, preg, "afirmacion")
    check(memoria.estado_de(con, hid) == "hipotesis", "sin ejecuciones -> hipotesis")

    sid = memoria.nueva_subtarea(con, preg, "cinematica.mrua",
                                {"v0": 1, "a": 0, "t": 1}, hipotesis_id=hid)
    memoria.registrar_ejecucion(con, sid, "directo", 1.0, 0.001, "env")
    check(memoria.estado_de(con, hid) == "calculado", "una ejecucion -> calculado")

    e2 = memoria.registrar_ejecucion(con, sid, "directo", 1.0, 0.001, "env")
    check(memoria.estado_de(con, hid) == "reproducido", "dos ejecuciones iguales -> reproducido")

    memoria.registrar_verificacion(con, e2, "edo", 1.0, 1e-6, "concuerda")
    check(memoria.estado_de(con, hid) == "validado", "una verificacion de otro motor -> validado")

    memoria.registrar_verificacion(con, e2, "tercero", 1.0, 1e-6, "concuerda")
    check(memoria.estado_de(con, hid) == "confirmado",
          "dos verificaciones de motores distintos -> confirmado")

    memoria.registrar_verificacion(con, e2, "cuarto", 1.5, 1e-6, "discrepa")
    check(memoria.estado_de(con, hid) == "refutado", "una discrepancia manda: refutado")

    # el estado escrito en conocimiento es el deducido, no el que se pida
    kid = memoria.anotar_conocimiento(con, hid)
    fila = memoria.fila(con, "conocimiento", kid)
    check(fila["estado"] == "refutado", "conocimiento guarda el estado deducido, no el deseado")


def prueba_verificador(con) -> None:
    params = {"v0": 1, "a": 0, "t": 1}
    ficha = pasos.ficha("cinematica.mrua")
    calc = ficha["motores"][0][0]
    check(all(m[0] != calc for m in ficha["motores"][1:]),
          "ningun paso usa para comprobar el mismo motor que calcula")
    r = verificador.comprobar("cinematica.mrua", params, 2.0)
    check(all(v["veredicto"] == "discrepa" for v in r),
          "un valor falso no pasa la comprobacion")

    # Guardia: si el motor de comprobacion se llamara igual que el de calculo,
    # no puede aprobar nada (se comprueba con un paso de mentira).
    pasos.registro("prueba.guardia", "prueba", "paso de prueba", "u",
                   ["x"], [("mismo", lambda p: 1.0), ("mismo", lambda p: 1.0)])
    try:
        g = verificador.comprobar("prueba.guardia", {"x": 1}, 1.0,
                                  motor_calculado="mismo")
        check(all(v["veredicto"] == "indeterminado" for v in g),
              "mismo motor para calcular y comprobar -> indeterminado, no concuerda")
    finally:
        pasos.PASOS.pop("prueba.guardia", None)


def prueba_planificador() -> None:
    preg = {"paso": "cinematica.mrua", "params": {"v0": 1, "a": 0, "t": 1}}
    check(planificador.planificar(preg)["aceptado"], "plan valido aceptado")
    check(not planificador.planificar({"paso": "fisica.magia", "params": {}})["aceptado"],
          "paso inventado rechazado")
    malo = {"paso": "cinematica.mrua", "params": {"v0": 1, "a": 0}}
    check(not planificador.planificar(malo)["aceptado"], "faltan parametros -> rechazado")
    sobra = {"paso": "cinematica.mrua", "params": {"v0": 1, "a": 0, "t": 1, "z": 2}}
    check(not planificador.planificar(sobra)["aceptado"], "parametros de mas -> rechazado")
    prop = {"paso": "quimica.ph_acido_debil", "params": {"Ka": 1e-5, "c": 0.1}}
    r = planificador.planificar(preg, prop)
    check(r["aceptado"] and r["origen"] == "modelo", "propuesta valida del modelo se acepta")
    prop2 = {"paso": "inventado.total", "params": {}}
    check(not planificador.planificar(preg, prop2)["aceptado"],
          "propuesta inventada del modelo se rechaza y se explica")


def prueba_sabotaje(con) -> None:
    pid = memoria.nuevo_problema(con, "sabotaje", "x", "fisica")
    r = proceso.resolver(con, pid, "cinematica.mrua", {"v0": 20, "a": -4, "t": 3},
                         sabotaje=0.05)
    check(r["estado"] == "refutado", "resultado desviado un 5 % -> refutado")
    check(any(v["veredicto"] == "discrepa" for v in r["verificaciones"]),
          "y la verificacion dice exactamente que discrepa")


def prueba_contradiccion(con) -> None:
    pid = memoria.nuevo_problema(con, "contradiccion", "x", "fisica")
    preg = memoria.nueva_pregunta(con, pid, "¿?", "criterio", "metodo")
    valores = []
    for v in (1.0, 1.3):
        hid = memoria.nueva_hipotesis(con, preg, f"vale {v}", valor=v)
        sid = memoria.nueva_subtarea(con, preg, "cinematica.mrua",
                                     {"v0": v, "a": 0, "t": 1}, hipotesis_id=hid)
        e1 = memoria.registrar_ejecucion(con, sid, "directo", v, 0.001, "env")
        memoria.registrar_ejecucion(con, sid, "directo", v, 0.001, "env")
        memoria.registrar_verificacion(con, e1, "edo", v, 1e-9, "concuerda")
        valores.append(memoria.anotar_conocimiento(con, hid))
    memoria.detectar_contradicciones(con)
    n = con.execute("SELECT COUNT(*) c FROM contradicciones").fetchone()["c"]
    check(n >= 1, "dos valores validados incompatibles -> contradiccion guardada")
    check(memoria.fila(con, "preguntas", preg)["estado"] == "en_disputa",
          "la pregunta queda en disputa")
    check(len({valores[0], valores[1]}) == 2, "no se borra ninguno de los dos numeros")


def prueba_banco(con, tmp: Path) -> None:
    r = evaluacion.evaluar(con, sabotear=True, ver=False)
    check(r["problemas"] == 13, "el banco tiene 13 problemas")
    check(r["aciertos"] == r["problemas"], f"aciertos {r['aciertos']}/{r['problemas']}")
    check(r["fallos"] == 0, "sin fallos")
    check(r["sabotajes_detectados"] == r["sabotajes"],
          f"errores del 5 % cazados {r['sabotajes_detectados']}/{r['sabotajes']}")


def prueba_sin_codigo_arbitrario() -> None:
    """Nada de eval/exec sobre texto: lo unico que se ejecuta son pasos registrados."""
    malos = []
    for f in (RAIZ / "meta").glob("*.py"):
        for i, linea in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            limpia = linea.strip()
            if limpia.startswith("#"):
                continue
            if re.match(r"^(eval|exec)\s*\(", limpia) or "__import__(" in limpia:
                malos.append(f"{f.name}:{i}")
    check(not malos, f"sin eval/exec dinamico ({', '.join(malos) or 'limpio'})")


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    with tempfile.TemporaryDirectory() as tmp:
        con = _con(Path(tmp))
        print("== escalones ==")
        prueba_escalones(con)
        print("== verificador ==")
        prueba_verificador(con)
        print("== planificador ==")
        prueba_planificador()
        print("== sabotaje ==")
        prueba_sabotaje(con)
        print("== contradicciones ==")
        prueba_contradiccion(con)
        print("== seguridad del ejecutor ==")
        prueba_sin_codigo_arbitrario()
        print("== banco completo ==")
        prueba_banco(con, Path(tmp))
        # En Windows el fichero no se puede borrar con la conexion abierta.
        con.close()

    print()
    if fallos:
        print(f"[prueba] {len(fallos)} FALLO(S)")
        return 1
    print("[prueba] todo OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
