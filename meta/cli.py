"""Consola de KUKIA META.

Uso:
    python -m meta.cli banco                  pasa el banco entero y mide
    python -m meta.cli banco --sin-sabotaje  solo aciertos, sin prueba de errores
    python -m meta.cli estado                 que hay guardado y en que escalon
    python -m meta.cli demo --paso cinematica.mrua --params '{"v0":20,"a":-4,"t":3}'
    python -m meta.cli pasos                  registro cientifico disponible
    python -m meta.cli dominios               disciplinas con pasos cargados

Codigo de salida: 0 si todo lo que se probo se valido y acerto, 1 si algo fallo.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import esquema, evaluacion, memoria, pasos, proceso


def _consola() -> None:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def cmd_banco(a, con) -> int:
    print(f"BANCO {a.banco or evaluacion.BANCO_POR_DEFECTO}")
    r = evaluacion.evaluar(con, a.banco, sabotear=not a.sin_sabotaje, ver=not a.json)
    if a.json:
        print(json.dumps({k: v for k, v in r.items() if k != "detalle"}, indent=2,
                         ensure_ascii=False))
        return 0 if r["fallos"] == 0 and r["aciertos"] == r["validados"] else 1

    print()
    print(f"  problemas probados      {r['problemas']}")
    print(f"  validados               {r['validados']}")
    print(f"  aciertos (valor exacto) {r['aciertos']}")
    print(f"  fallos                  {r['fallos']}")
    print(f"  indeterminados          {r['indeterminados']}")
    if r["sabotajes"]:
        print(f"  errores del 5 % cazados {r['sabotajes_detectados']}/{r['sabotajes']}")
    print(f"  tiempo                  {r['segundos']} s")
    print()
    print("  por disciplina:")
    for dom, d in sorted(evaluacion.por_dominio(r).items()):
        print(f"    {dom:26s} {d['aciertos']}/{d['problemas']} validados y correctos")
    return 0 if r["fallos"] == 0 and r["aciertos"] == r["validados"] else 1


def cmd_estado(a, con) -> int:
    r = memoria.resumen(con)
    if a.json:
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return 0
    print("MEMORIA DE KUKIA META")
    for clave, valor in r.items():
        if clave == "por_estado":
            continue
        print(f"  {clave:16s} {valor}")
    print("  conocimiento por escalon:")
    for estado, n in r["por_estado"].items():
        print(f"    {estado:14s} {n}")
    disputas = con.execute("SELECT COUNT(*) c FROM contradicciones").fetchone()["c"]
    if disputas:
        print(f"  contradicciones abiertas: {disputas}")
    return 0


def cmd_demo(a, con) -> int:
    try:
        params = json.loads(a.params)
    except json.JSONDecodeError as e:
        print(f"[ERROR] --params no es JSON valido: {e}")
        return 2
    pid = memoria.nuevo_problema(con, a.titulo, a.titulo, a.dominio or "sin_dominio")
    r = proceso.resolver(con, pid, a.paso, params)
    print(json.dumps({k: v for k, v in r.items() if k != "plan"}, indent=2,
                     ensure_ascii=False, default=str))
    return 0 if r["estado"] in ("validado", "confirmado") else 1


def cmd_pasos(a, con) -> int:
    for nombre in sorted(pasos.PASOS):
        f = pasos.ficha(nombre)
        print(f"  {nombre:34s} {f['dominio']:24s} "
              f"{' + '.join(m[0] for m in f['motores'])}")
    print(f"\n  {len(pasos.PASOS)} pasos · huella {pasos.huella_pasos()}")
    return 0


def cmd_dominios(a, con) -> int:
    cuenta: dict[str, int] = {}
    for nombre in pasos.PASOS:
        cuenta[pasos.ficha(nombre)["dominio"]] = \
            cuenta.get(pasos.ficha(nombre)["dominio"], 0) + 1
    for dom, n in sorted(cuenta.items()):
        print(f"  {dom:26s} {n}")
    return 0


def main(argv=None) -> int:
    _consola()
    ap = argparse.ArgumentParser(prog="meta", description="KUKIA META (prototipo)")
    ap.add_argument("--db", default=None, help="ruta de la base (por defecto meta/estado/meta.db)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("banco", help="pasa el banco de problemas y mide")
    p.add_argument("--banco", default=None)
    p.add_argument("--json", action="store_true")
    p.add_argument("--sin-sabotaje", action="store_true")
    p.set_defaults(fn=cmd_banco)

    p = sub.add_parser("estado", help="resumen de la memoria")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_estado)

    p = sub.add_parser("demo", help="resuelve un paso suelto")
    p.add_argument("--paso", required=True)
    p.add_argument("--params", required=True)
    p.add_argument("--titulo", default="prueba manual")
    p.add_argument("--dominio", default=None)
    p.set_defaults(fn=cmd_demo)

    sub.add_parser("pasos", help="registro cientifico").set_defaults(fn=cmd_pasos)
    sub.add_parser("dominios", help="disciplinas cargadas").set_defaults(fn=cmd_dominios)

    a = ap.parse_args(argv)
    con = esquema.conexion(a.db)
    return a.fn(a, con)


if __name__ == "__main__":
    raise SystemExit(main())
