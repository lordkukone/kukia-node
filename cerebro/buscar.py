"""Busqueda semantica sobre el cerebro. Solo lee.

Uso:
    python -m cerebro.buscar "por que no se rota la clave del sello" -k 8
    python -m cerebro.buscar "como se ancla el ledger" --json
"""
from __future__ import annotations

import argparse
import json
import sys
import textwrap

from . import indice


def recortar(texto: str, limite: int = 240) -> str:
    plano = " ".join(texto.split())
    return plano if len(plano) <= limite else plano[:limite] + " ..."


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cerebro.buscar", description="Busca en el cerebro KUKIA.")
    ap.add_argument("consulta", nargs="+", help="que quieres encontrar")
    ap.add_argument("-k", type=int, default=8, help="cuantos fragmentos (por defecto 8)")
    ap.add_argument("--json", action="store_true", help="salida para maquinas")
    args = ap.parse_args(argv)

    consulta = " ".join(args.consulta)
    try:
        resultados = indice.buscar(consulta, args.k)
    except RuntimeError as fallo:
        print(f"ERROR: {fallo}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(resultados, ensure_ascii=False, indent=2))
        return 0

    print(f'consulta: "{consulta}"  ({len(resultados)} fragmentos)')
    for i, r in enumerate(resultados, 1):
        print(f"{i}. {r['puntos']:.3f}  {r['rel']}:{r['linea_ini']}-{r['linea_fin']}")
        print(textwrap.fill(recortar(r["texto"]), width=100,
                            initial_indent="   ", subsequent_indent="   "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
