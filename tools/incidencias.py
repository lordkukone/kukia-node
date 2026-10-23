"""Historico de incidencias por dia: caidas, cuanto duraron y disponibilidad %.

PARA QUE
    Los vigilantes ya apuntan cada pasada en `logs/`, pero hasta ahora solo se
    veia lo ultimo (`ESTADO_WEBS.txt`). Esto lee esos logs y saca el historico:
    por dia y por servicio, cuantas caidas hubo, cuanto tiempo estuvo caido y
    que porcentaje del tiempo observable estuvo disponible.

DE DONDE SALEN LOS DATOS
    logs/vigilante_servicios.log   cada 5 min: surge, freebuff, github,
                                   core-local, copia-claves, copia-integra
    logs/vigilante_web.log         cada 10 min: el estado del sitio canonico
                                   (listo / caido / sin_publicar / sin_clave)
    No se inventa nada ni se pregunta al Core: es lo que los vigilantes fueron
    dejando escrito. Si un log no existe, simplemente no aparece.

QUE CUENTA Y QUE NO
    - Dos pasadas malas seguidas son UNA incidencia, no dos.
    - La caida dura hasta la primera pasada buena (no hasta la ultima mala).
    - Un hueco sin pasadas (PC apagado, tarea parada) NO cuenta ni como caida
      ni como tiempo disponible: sale de la cuenta en vez de ensuciarla.
    - Una caida que sigue abierta ahora mismo se muestra como `sigue`.

USO
    python tools/incidencias.py                    ultimos 7 dias
    python tools/incidencias.py --dias 30          un mes
    python tools/incidencias.py --dias 0           todo lo que haya en los logs
    python tools/incidencias.py --detalle          ademas, cada caida con hora
    python tools/incidencias.py --servicio surge    solo uno
    python tools/incidencias.py --json             para scripts

    Doble clic: VER_INCIDENCIAS.cmd
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
LOGS = RAIZ / "logs"
LOG_SERVICIOS = LOGS / "vigilante_servicios.log"
LOG_WEB = LOGS / "vigilante_web.log"
# Si entre dos pasadas pasa mas que esto, se supone que el vigilante no estaba
# mirando (PC apagado) y ese tramo no se cuenta. Se queda con el mayor de este
# minimo y tres veces la cadencia real de ese servicio.
HUECO_MINUTOS = 15
CORE_PRIMERO = ("core-local",)


def hhmm(segundos) -> str:
    """Segundos -> '2 h 03 min' / '12 min 30 s' / '40 s'. None = 'sigue'."""
    if segundos is None:
        return "sigue"
    segundos = int(segundos)
    h, resto = divmod(segundos, 3600)
    m, s = divmod(resto, 60)
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {s:02d} s"
    return f"{s} s"


def pct(valor: float) -> str:
    """Porcentaje con coma decimal, como se escribe aqui."""
    return f"{valor:.1f}".replace(".", ",") + " %"


LINEA = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+(.*)$")
PASADA = re.compile(r"^pasada:\s*(.+)$")
PAR = re.compile(r"([A-Za-z0-9_.\-]+)=(ok|fallo\(\d+\))")
WEB = re.compile(r"^(https?://\S+)\s+->\s+(\S+)(?:\s+\((.*)\))?$")
ESTADOS_WEB = ("listo", "caido", "sin_publicar", "sin_clave")


def _host(url: str) -> str:
    return re.sub(r"^https?://", "", url).split("/")[0].lower()


def lee(ruta) -> list:
    """[(cuando, servicio, ok, motivo)] que haya en un log de vigilante."""
    eventos = []
    try:
        texto = Path(ruta).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return eventos
    for cruda in texto.splitlines():
        m = LINEA.match(cruda.strip())
        if not m:
            continue
        try:
            cuando = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        resto = m.group(2).strip()

        p = PASADA.match(resto)
        if p:
            for nombre, estado in PAR.findall(p.group(1)):
                ok = estado.lower() == "ok"
                eventos.append((cuando, nombre.lower(), ok, "" if ok else "fallo"))
            continue

        w = WEB.match(resto)
        if w and w.group(2) in ESTADOS_WEB:
            # sin_publicar / sin_clave responden: el host esta vivo aunque sirva
            # otra cosa. Solo `caido` es caida.
            ok = w.group(2) != "caido"
            motivo = w.group(3) or ("" if ok else w.group(2))
            eventos.append((cuando, "web " + _host(w.group(1)), ok, motivo))
    return eventos


def incidencias(pasadas: list) -> list:
    """Agrupa fallos seguidos y devuelve una entrada por caida.

    `pasadas` = [(cuando, ok, motivo)] ordenado. La ultima entrada puede no
    tener fin (`segundos` None): es una caida que sigue abierta.
    """
    salida = []
    desde = None
    motivo = ""
    for cuando, ok, detalle in pasadas:
        if not ok:
            if desde is None:
                desde, motivo = cuando, detalle
            motivo = detalle or motivo
        elif desde is not None:
            salida.append({"desde": desde, "hasta": cuando, "motivo": motivo,
                           "segundos": int((cuando - desde).total_seconds())})
            desde = None
    if desde is not None:
        salida.append({"desde": desde, "hasta": None, "motivo": motivo,
                       "segundos": None})
    return salida


def _limite_hueco(pasadas: list) -> float:
    """Segundos a partir de los cuales se supone que el vigilante no miraba."""
    if len(pasadas) < 3:
        return HUECO_MINUTOS * 60
    huecos = [(b[0] - a[0]).total_seconds() for a, b in zip(pasadas, pasadas[1:])]
    huecos = [h for h in huecos if h > 0]
    if not huecos:
        return HUECO_MINUTOS * 60
    return max(HUECO_MINUTOS * 60, 3 * statistics.median(huecos))


def resumen(eventos: list, desde: datetime, hasta: datetime) -> dict:
    """{dia: {servicio: {caidas, caido, observado, disponibilidad, detalle}}}."""
    por_servicio = {}
    for cuando, servicio, ok, motivo in eventos:
        if desde <= cuando <= hasta:
            por_servicio.setdefault(servicio, []).append((cuando, ok, motivo))

    dias = {}
    for servicio, pasadas in por_servicio.items():
        pasadas.sort()
        limite = _limite_hueco(pasadas)

        def hueco(dia: str) -> dict:
            return dias.setdefault(dia, {}).setdefault(
                servicio, {"caidas": 0, "caido": 0.0, "observado": 0.0,
                           "disponibilidad": None, "detalle": []})

        # Tramos: [pasada_i, pasada_i+1). Manda el estado de la primera.
        for (cuando, ok, _), (siguiente, _, _) in zip(pasadas, pasadas[1:]):
            tramo = (siguiente - cuando).total_seconds()
            if tramo <= 0 or tramo > limite:
                continue
            s = hueco(cuando.strftime("%Y-%m-%d"))
            s["observado"] += tramo
            if not ok:
                s["caido"] += tramo

        for inc in incidencias(pasadas):
            dia = inc["desde"].strftime("%Y-%m-%d")
            s = dias.get(dia, {}).get(servicio)
            if s is None:
                continue  # caida entera dentro de un hueco: no hay tramo medido
            s["caidas"] += 1
            s["detalle"].append({
                "desde": inc["desde"].strftime("%H:%M:%S"),
                "hasta": inc["hasta"].strftime("%H:%M:%S") if inc["hasta"] else "sigue",
                "segundos": inc["segundos"],
                "motivo": inc["motivo"],
            })

    for dia in dias.values():
        for s in dia.values():
            if s["observado"] > 0:
                s["disponibilidad"] = 100.0 * (1 - s["caido"] / s["observado"])
    return dias


def _orden(servicio: str) -> tuple:
    return (0 if servicio in CORE_PRIMERO else 1, servicio)


def _nombre(servicio: str, ancho: int = 28) -> str:
    """El nombre cabe en la columna (el host largo se corta, no rompe la tabla)."""
    return servicio if len(servicio) <= ancho else servicio[:ancho - 3] + "..."


def texto(dias: dict, con_detalle: bool) -> str:
    """Tabla legible. Devuelve '' si no hay nada."""
    if not dias:
        return ""
    lineas = [f"{'dia':<12}{'servicio':<28}{'caidas':>7}{'caido':>12}"
              f"{'disponible':>12}"]
    totales = {}
    for dia in sorted(dias, reverse=True):
        for servicio in sorted(dias[dia], key=_orden):
            s = dias[dia][servicio]
            if s["observado"] <= 0:
                continue
            disp = pct(s["disponibilidad"] or 0.0)
            caido = "-" if s["caido"] <= 0 else hhmm(s["caido"])
            lineas.append(f"{dia:<12}{_nombre(servicio):<28}{s['caidas']:>7}{caido:>12}{disp:>12}")
            t = totales.setdefault(servicio, {"caido": 0.0, "observado": 0.0,
                                              "caidas": 0, "detalle": []})
            t["caido"] += s["caido"]
            t["observado"] += s["observado"]
            t["caidas"] += s["caidas"]
            if con_detalle:
                for d in s["detalle"]:
                    lineas.append(f"   {'':<8}{d['desde']} -> {d['hasta']:<9}"
                                  f"{hhmm(d['segundos']):>12}"
                                  f"   {d['motivo']}".rstrip())
        lineas.append("")
    if totales:
        lineas.append("total del periodo")
        for servicio in sorted(totales, key=_orden):
            t = totales[servicio]
            disp = pct(100.0 * (1 - t["caido"] / t["observado"])) if t["observado"] else "-"
            caido = "-" if t["caido"] <= 0 else hhmm(t["caido"])
            lineas.append(f"{'':<12}{_nombre(servicio):<28}{t['caidas']:>7}{caido:>12}{disp:>12}")
    return "\n".join(lineas)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Historico de caidas y disponibilidad por dia, leido de los logs "
                    "de los vigilantes.")
    ap.add_argument("--dias", type=int, default=7,
                    help="cuantos dias mirar hacia atras (0 = todo el historico)")
    ap.add_argument("--servicio", default="",
                    help="solo este (core-local, surge, github, freebuff, web ...)")
    ap.add_argument("--detalle", action="store_true", help="cada caida con hora y motivo")
    ap.add_argument("--json", action="store_true", help="salida para scripts")
    a = ap.parse_args()

    eventos = lee(LOG_SERVICIOS) + lee(LOG_WEB)
    if a.servicio:
        quiero = a.servicio.lower().strip()
        eventos = [e for e in eventos if quiero in e[1]]

    ahora = datetime.now()
    if a.dias > 0:
        desde = datetime.combine((ahora - timedelta(days=a.dias - 1)).date(),
                                 datetime.min.time())
    else:
        desde = datetime.min
    hasta = datetime.combine(ahora.date(), datetime.max.time())
    dias = resumen(eventos, desde, hasta)

    if a.json:
        print(json.dumps({"hasta": hasta.strftime("%Y-%m-%d %H:%M:%S"),
                          "dias": {d: {s: v for s, v in x.items()}
                                   for d, x in sorted(dias.items())}},
                         indent=2, ensure_ascii=False, default=str))
        return 0

    if not dias:
        print("todavia no hay pasadas en los logs de los vigilantes.")
        print(f"  mirados: {LOG_SERVICIOS.name} y {LOG_WEB.name}")
        print("  si los vigilantes estan instalados, espera unos minutos; si no:")
        print("  python tools/vigilante_servicios.py --instalar")
        return 1

    print("HISTORICO DE INCIDENCIAS - KUKIA")
    print(f"fuente: {LOG_SERVICIOS.relative_to(RAIZ)} (cada 5 min) "
          f"+ {LOG_WEB.relative_to(RAIZ)} (cada 10 min)")
    print(f"periodo: {'todo lo que hay' if a.dias <= 0 else f'ultimos {a.dias} dias'}"
          .replace("todo lo que hay", "todo lo que hay en los logs"))
    print("(un hueco sin pasadas no cuenta: PC apagado o tarea parada)\n")
    print(texto(dias, a.detalle))
    return 0


if __name__ == "__main__":
    sys.exit(main())
