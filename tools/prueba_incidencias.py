"""Prueba del historico de incidencias con logs de mentira (fechas y horas fijas).

Comprueba lo que importa: que dos fallos seguidos sean UNA incidencia, que la
duracion y la disponibilidad salgan como deben, y que un hueco sin pasadas
(PC apagado) no se cuente ni como caida ni como tiempo disponible.

Uso:
    python tools/prueba_incidencias.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))
import incidencias as inc  # noqa: E402

fallos = 0


def comprobar(nombre: str, cond: bool, detalle="") -> None:
    global fallos
    if cond:
        print(f"  OK    {nombre}")
    else:
        fallos += 1
        print(f"  FALLO {nombre} -> {detalle}")


def escribe(ruta: Path, lineas: list) -> None:
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="kukia_inc_"))
    log_web = tmp / "vigilante_web.log"
    log_serv = tmp / "vigilante_servicios.log"

    # Servicios: cada 5 min de 10:00 a 10:30. core-local cae en 10:10 y 10:15
    # y vuelve en 10:20 -> UNA incidencia de 10 minutos.
    lineas = []
    for minuto in range(0, 31, 5):
        hh = f"10:{minuto:02d}:00"
        core = "fallo(2)" if minuto in (10, 15) else "ok"
        lineas.append(f"[2026-10-20 {hh}] pasada: surge=ok, core-local={core}, copia-claves=ok")
    escribe(log_serv, lineas)

    # Web: cada 10 min de 11:00 a 11:30, caida de 11:10 a 11:30 (20 min, 503).
    escribe(log_web, [
        "[2026-10-20 11:00:00] https://sitio -> listo",
        "[2026-10-20 11:10:00] https://sitio -> caido (HTTP 503)",
        "[2026-10-20 11:20:00] https://sitio -> caido (HTTP 503)",
        "[2026-10-20 11:30:00] https://sitio -> listo",
        # hueco de 3 horas: el PC estaba apagado, no es una caida
        "[2026-10-20 14:30:00] https://sitio -> listo",
        "[2026-10-20 14:40:00] https://sitio -> listo",
    ])

    eventos = inc.lee(log_web) + inc.lee(log_serv)
    comprobar("lee las pasadas de los dos logs", len(eventos) == 7 * 3 + 6, len(eventos))

    desde = datetime(2026, 10, 20, 0, 0)
    hasta = datetime(2026, 10, 20, 23, 59)
    dias = inc.resumen(eventos, desde, hasta)

    d = dias["2026-10-20"]
    core = d["core-local"]
    comprobar("dos fallos seguidos = una caida", core["caidas"] == 1, core)
    comprobar("tiempo caido 10 min (600 s)", int(core["caido"]) == 600, core)
    comprobar("disponibilidad 66,67 %", abs(core["observado"] - 1800) < 1, core)
    comprobar("detalle con hora", core["detalle"][0]["desde"] == "10:10:00", core["detalle"])
    comprobar("detalle hasta", core["detalle"][0]["hasta"] == "10:20:00", core["detalle"])

    surge = d["surge"]
    comprobar("lo que no falla: 0 caidas", surge["caidas"] == 0, surge)
    comprobar("y 100 % de disponibilidad", surge["caido"] == 0, surge)

    web = d["web sitio"]
    comprobar("web: una sola caida", web["caidas"] == 1, web)
    comprobar("web: 20 min caida", int(web["caido"]) == 1200, web)
    # 5 intervalos, el de 3 h (11:30 -> 14:30) se descarta: quedan 4 x 600 s.
    comprobar("web: el hueco de 3 h no cuenta", int(web["observado"]) == 2400, web)
    comprobar("web: disponibilidad 50 %", web["disponibilidad"] == 50.0, web)
    comprobar("web: motivo del fallo", web["detalle"][0]["motivo"] == "HTTP 503", web["detalle"])

    # Una caida que sigue abierta ahora mismo.
    abierta = inc.incidencias([
        (datetime(2026, 10, 20, 12, 0), True, ""),
        (datetime(2026, 10, 20, 12, 5), False, "sin respuesta"),
    ])
    comprobar("caida abierta: sin fin", len(abierta) == 1 and abierta[0]["hasta"] is None, abierta)
    comprobar("se muestra como 'sigue'", inc.hhmm(abierta[0]["segundos"]) == "sigue")

    print("\nPRUEBA " + ("BIEN" if fallos == 0 else f"MAL ({fallos} fallos)"))
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
