"""Mide el cerebro contra un banco de preguntas con respuesta conocida.

Sin medir, decir "mejora" no significa nada. Uso:
    python -m cerebro.evaluar            # recall@8 por defecto
    python -m cerebro.evaluar -k 5
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config, indice

BANCO = config.PAQUETE / "bancos" / "preguntas.json"


def coincide(rel: str, esperados) -> bool:
    """El fichero esperado puede venir con o sin carpeta; da igual el prefijo."""
    return any(rel == e or rel.endswith("/" + e) for e in esperados)


def evaluar(k: int = 8, banco=None, db=None) -> dict:
    casos = json.loads(Path(banco or BANCO).read_text(encoding="utf-8"))
    aciertos = 0
    mrr = 0.0
    detalle = []
    for caso in casos:
        resultados = indice.buscar(caso["pregunta"], k, db=db)
        posicion = 0
        for i, r in enumerate(resultados, 1):
            if coincide(r["rel"], caso["esperado"]):
                posicion = i
                break
        if posicion:
            aciertos += 1
            mrr += 1.0 / posicion
        detalle.append({"pregunta": caso["pregunta"], "posicion": posicion,
                        "primero": resultados[0]["rel"] if resultados else "-"})
    n = len(casos) or 1
    return {"n": len(casos), "aciertos": aciertos, "recall": aciertos / n,
            "mrr": mrr / n, "detalle": detalle}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cerebro.evaluar", description="Banco del cerebro KUKIA.")
    ap.add_argument("-k", type=int, default=8)
    ap.add_argument("--banco", default=None)
    ap.add_argument("--detalle", action="store_true", help="una linea por pregunta")
    args = ap.parse_args(argv)

    r = evaluar(args.k, args.banco)
    if args.detalle:
        for d in r["detalle"]:
            marca = f"#{d['posicion']}" if d["posicion"] else "FALLO"
            print(f"  {marca:<6} {d['pregunta'][:70]:<72} -> {d['primero']}")
    print(f"recall@{args.k} = {r['aciertos']}/{r['n']} = {r['recall']:.2f} | MRR = {r['mrr']:.3f}")
    if r["recall"] < config.UMBRAL_RECALL:
        print(f"POR DEBAJO DEL UMBRAL ({config.UMBRAL_RECALL:.2f}): no cuenta como mejora")
        return 1
    print(f"CUMPLE el umbral ({config.UMBRAL_RECALL:.2f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
