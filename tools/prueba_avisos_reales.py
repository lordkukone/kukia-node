#!/usr/bin/env python3
"""Prueba de punta a punta de los avisos: caida de verdad y vuelta de verdad.

A diferencia de `prueba_vigilante_servicios.py` (que comprueba la logica con
todo simulado), esta usa el canal REAL: la notificacion de Windows y, si esta
configurado, Telegram. Para provocar la caida sin tocar nada del proyecto se
apunta el Core local a un puerto donde no hay nadie (127.0.0.1:9).

Que hace:
  1. Una pasada con el Core "caido" -> no avisa (hacen falta dos seguidas).
  2. Otra pasada caida -> avisa de la caida (escritorio + Telegram).
  3. Una pasada con el Core bueno -> avisa de la vuelta.

Usa un estado temporal: el vigilante de verdad (y su tarea de 5 minutos) no se
entera de nada. Los unicos mensajes que salen son los de la prueba.

USO
    python tools/prueba_avisos_reales.py
    python tools/prueba_avisos_reales.py --sin-telegram   solo escritorio
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))

import avisos  # noqa: E402
import vigilante_servicios as v  # noqa: E402

MUERTO = "http://127.0.0.1:9"      # puerto cerrado: conexion rechazada al instante


def main() -> int:
    ap = argparse.ArgumentParser(description="Prueba real de los avisos del vigilante")
    ap.add_argument("--sin-telegram", action="store_true",
                    help="no intentar Telegram (solo la notificacion de Windows)")
    args = ap.parse_args()

    temporal = Path(tempfile.mkdtemp(prefix="kukia_avisos_"))
    v.LOGS, v.LOG, v.ESTADO, v.RESUMEN = temporal, temporal / "log.txt", \
        temporal / "estado.json", temporal / "resumen.txt"
    # Solo el Core local: las webs y la copia de claves no pintan nada aqui.
    v.SITIOS = {}
    v.estado_copia_claves = lambda base=None: {"nombre": "copia-claves",
                                               "ok": True, "detalle": "(no se mira)"}

    # Se cuenta lo que de verdad se manda por cada canal.
    enviados = []
    escritorio = []
    original_avisa = v.avisa

    def avisa_de_verdad(titulo, texto):
        r = original_avisa(titulo, texto)
        escritorio.append((titulo, r))
        return r

    v.avisa = avisa_de_verdad
    original = avisos.enviar
    if args.sin_telegram:
        avisos.enviar = lambda titulo, texto="": False
    else:
        def telegram(titulo, texto=""):
            r = original(titulo, texto)
            enviados.append((titulo, r))
            return r
        avisos.enviar = telegram

    print(f"Telegram: {'configurado' if avisos.configurado() else 'SIN CONFIGURAR'}"
          + ("" if args.sin_telegram else ""))
    print(f"Core de prueba: {MUERTO} (ahi no escucha nadie)\n")

    print("pasada 1: Core caido (no debe avisar todavia)")
    v.una_vez(avisar=True, core_local=MUERTO)
    print("\npasada 2: sigue caido (aqui debe avisar)")
    v.una_vez(avisar=True, core_local=MUERTO)
    print("\npasada 3: Core bueno (aqui debe avisar de que volvio)")
    v.una_vez(avisar=True, core_local=v.CORE_LOCAL)

    print("\n--- resultado ---")
    lineas = (temporal / "log.txt").read_text(encoding="utf-8", errors="replace")
    avisos_dados = [l for l in lineas.splitlines() if "AVISO:" in l]
    for a in avisos_dados:
        print("  " + a.split("] ", 1)[-1])
    if len(avisos_dados) != 2:
        print(f"  MAL: salieron {len(avisos_dados)} avisos, deberian ser 2")
        return 1
    ok_esc = sum(1 for _t, r in escritorio if r)
    print(f"  escritorio: {ok_esc} de {len(escritorio)} entregados")
    if args.sin_telegram:
        return 0 if ok_esc == len(escritorio) else 1
    ok_tel = sum(1 for _t, r in enviados if r)
    print(f"  telegram: {ok_tel} de {len(enviados)} entregados")
    if not avisos.configurado():
        print("  telegram sin configurar -> python tools/avisos.py --configurar")
        return 1
    if ok_tel != len(enviados):
        print("  telegram configurado pero algun envio fallo: mira --estado")
        return 1
    print("  TODO BIEN: caida y vuelta avisadas por escritorio y por Telegram")
    return 0


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
