"""Prueba del aviso por disponibilidad del dia (sin red, sin toasts, sin Telegram).

Comprueba lo que importa:
  - un servicio por debajo del 99 % avisa UNA vez (no cada 5 minutos),
  - un servicio al 100 % no avisa,
  - un servicio con muy pocos minutos medidos no avisa (aun no dice nada),
  - dos servicios malos se cuentan por separado.

Uso:
    python tools/prueba_vigilante_disponibilidad.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))
import vigilante_servicios as vig  # noqa: E402

fallos = 0


def comprobar(nombre: str, cond: bool, detalle="") -> None:
    global fallos
    if cond:
        print(f"  OK    {nombre}")
    else:
        fallos += 1
        print(f"  FALLO {nombre} -> {detalle}")


def pasadas(servicio: str, inicio: datetime, minutos: int, cada: int = 5,
            fallos_en: tuple = ()) -> list:
    """[(cuando, servicio, ok, motivo)] con fallos en los minutos indicados."""
    fuera = []
    for m in range(0, minutos + 1, cada):
        cuando = inicio + timedelta(minutes=m)
        malo = m in fallos_en
        fuera.append((cuando, servicio, not malo, "fallo" if malo else ""))
    return fuera


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="kukia_vig_disp_"))
    vig.AVISADAS = tmp / "disp.json"
    # Nada de escribir en el log de verdad durante la prueba.
    vig.log = lambda *a, **k: None
    ahora = datetime(2026, 10, 22, 12, 0)
    base = datetime(2026, 10, 22, 10, 0)

    eventos = []
    # surge: 2 pasadas malas seguidas (10:40 y 10:45) = 10 min de 60 mirados
    # -> 83,3 %.
    eventos += pasadas("surge", base, 60, fallos_en=(40, 45))
    # github: perfecto.
    eventos += pasadas("github", base, 60)
    # core-local: un solo parpadeo de 5 min en 60 -> 91,7 %, tambien avisa.
    eventos += pasadas("core-local", base, 60, fallos_en=(30,))

    avisos = vig.revisa_disponibilidad(ahora=ahora, eventos=eventos, avisar=True)
    comprobar("hay aviso", len(avisos) == 1, avisos)
    titulo, texto = avisos[0]
    comprobar("el peor primero es surge", "surge" in titulo, titulo)
    comprobar("dice el porcentaje", "83,3 %" in titulo, titulo)
    comprobar("el texto dice el minimo", "99 %" in texto, texto)
    comprobar("el texto cuenta los dos malos", "surge" in texto and "core-local" in texto, texto)
    comprobar("no menciona el que va bien", "github" not in texto, texto)

    otra = vig.revisa_disponibilidad(ahora=ahora, eventos=eventos, avisar=True)
    comprobar("no repite el aviso el mismo dia", otra == [], otra)
    marcas = (tmp / "disp.json").read_text(encoding="utf-8")
    comprobar("quedan apuntados los dos", "surge" in marcas and "core-local" in marcas, marcas)

    # Muy pocos minutos medidos: no se puede juzgar el dia.
    poco = pasadas("copia-claves", datetime(2026, 10, 22, 11, 50), 10, fallos_en=(5,))
    comprobar("con 10 min no avisa",
              vig.revisa_disponibilidad(ahora=ahora, eventos=poco, avisar=True) == [], poco)

    # Una pasada con --sin-aviso NO debe gastar el aviso del dia: avisa (devuelve
    # la lista) pero no apunta nada, asi la pasada de verdad si avisara.
    caido_todo = pasadas("espejo", base, 60, fallos_en=tuple(range(0, 61, 5)))
    seco = vig.revisa_disponibilidad(ahora=ahora, eventos=caido_todo, avisar=False)
    comprobar("--sin-aviso devuelve el aviso", len(seco) == 1, seco)
    de_verdad = vig.revisa_disponibilidad(ahora=ahora, eventos=caido_todo, avisar=True)
    comprobar("--sin-aviso no gasta la marca del dia", len(de_verdad) == 1, de_verdad)

    # Un dia entero perfecto: cero avisos.
    vig.AVISADAS = tmp / "disp2.json"
    perfecto = pasadas("surge", base, 60)
    comprobar("100 % no avisa",
              vig.revisa_disponibilidad(ahora=ahora, eventos=perfecto, avisar=True) == [], "")

    # Al dia siguiente el mismo servicio vuelve a poder avisar.
    manana = datetime(2026, 10, 23, 12, 0)
    eventos_manana = pasadas("surge", base + timedelta(days=1), 60, fallos_en=(20, 25))
    avisos2 = vig.revisa_disponibilidad(ahora=manana, eventos=eventos_manana, avisar=True)
    comprobar("al dia siguiente avisa otra vez", len(avisos2) == 1, avisos2)

    # El fichero de estado lo deja escrito (ES_TADO_WEBS se rellena con esto).
    vig.AVISADAS = tmp / "disp3.json"
    disp = vig.disponibilidad_hoy(ahora=ahora,
                                  eventos=pasadas("surge", base, 60, fallos_en=(40, 45)))
    linea = vig.linea_disponibilidad(disp)
    comprobar("la linea lleva el porcentaje", "surge 83,3 %" in linea, linea)
    texto_resumen = vig.resumen_texto({"servicios": {}}, disp)
    comprobar("el resumen lo incluye", "Disponibilidad de hoy" in texto_resumen, texto_resumen)
    comprobar("y marca el que va bajo", "por debajo del minimo" in texto_resumen, texto_resumen)

    print("\nPRUEBA " + ("BIEN" if fallos == 0 else f"MAL ({fallos} fallos)"))
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
