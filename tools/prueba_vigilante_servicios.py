"""Prueba de la logica de avisos de `vigilante_servicios.py`, sin tocar nada real.

Simula un servicio que se cae y vuelve, y comprueba lo que importa: que un
fallo de una sola pasada NO avise, que una caida sostenida avise UNA vez (no
en cada vuelta) y que la vuelta avise UNA vez.

Uso:
    python tools/prueba_vigilante_servicios.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))
import vigilante_servicios as v  # noqa: E402

fallos = 0


def comprobar(nombre: str, cond: bool, detalle="") -> None:
    global fallos
    if cond:
        print(f"  OK    {nombre}")
    else:
        fallos += 1
        print(f"  FALLO {nombre} -> {detalle}")


def main() -> int:
    temporal = Path(tempfile.mkdtemp(prefix="kukia_vigilante_"))
    v.LOGS = temporal
    v.LOG = temporal / "log.txt"
    v.ESTADO = temporal / "estado.json"
    v.RESUMEN = temporal / "resumen.txt"
    # Solo el servicio falso: ni webs ni Core de verdad.
    v.SITIOS = {}

    avisos: list = []
    v.avisa = lambda titulo, texto: (avisos.append(titulo), True)[1]

    vivo = {"si": True}
    v.comprueba = lambda *a, **k: [
        {"nombre": "falso", "ok": vivo["si"],
         "detalle": "200" if vivo["si"] else "sin respuesta"}]

    print("1) un fallo suelto no avisa")
    vivo["si"] = False
    v.una_vez(avisar=True)
    comprobar("caida de 1 pasada: sin aviso", avisos == [], avisos)
    vivo["si"] = True
    v.una_vez(avisar=True)
    comprobar("vuelve sin haber avisado caida: sin aviso", avisos == [], avisos)

    print("2) una caida sostenida avisa una vez")
    vivo["si"] = False
    v.una_vez(avisar=True)
    comprobar("1a pasada mala: calla (hacen falta 2)", avisos == [], avisos)
    v.una_vez(avisar=True)
    comprobar("2a pasada mala: avisa", avisos == ["KUKIA: falso CAIDO"], avisos)
    v.una_vez(avisar=True)
    comprobar("3a pasada mala: no repite", len(avisos) == 1, avisos)

    print("3) cuando vuelve, avisa una vez")
    vivo["si"] = True
    v.una_vez(avisar=True)
    comprobar("vuelve: avisa", avisos[-1] == "KUKIA: falso ha vuelto", avisos)
    v.una_vez(avisar=True)
    comprobar("sigue bien: no repite", len(avisos) == 2, avisos)

    print("4) codigo de salida y ficheros")
    comprobar("0 con todo bien", v.una_vez(avisar=False) == 0)
    vivo["si"] = False
    v.una_vez(avisar=False)
    comprobar("1 con algo caido", v.una_vez(avisar=False) == 1)
    d = json.loads((temporal / "estado.json").read_text(encoding="utf-8"))
    comprobar("estado en json", "falso" in d.get("servicios", {}), d)
    comprobar("resumen escrito",
              "CAIDO" in (temporal / "resumen.txt").read_text(encoding="utf-8"))

    print("5) la copia de claves en otro disco")
    # (sin copias) -> falla; reciente -> ok; vieja -> falla
    comprobar("sin carpeta: falla", not v.estado_copia_claves(temporal / "no_existe")["ok"])
    fresca = temporal / "copias_ok" / "2026-10-22"
    fresca.mkdir(parents=True)
    (fresca / "MANIFIESTO.json").write_text(
        '{"copiado": "' + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + '"}',
        encoding="utf-8")
    e = v.estado_copia_claves(temporal / "copias_ok")
    comprobar("copia de hoy: ok", e["ok"], e)
    vieja = temporal / "copias_viejas" / "2020-01-01"
    vieja.mkdir(parents=True)
    (vieja / "MANIFIESTO.json").write_text('{"copiado": "2020-01-01 04:20:00"}',
                                            encoding="utf-8")
    e = v.estado_copia_claves(temporal / "copias_viejas")
    comprobar("copia de hace anos: falla", not e["ok"], e)
    comprobar("y lo dice en el detalle", "2020" in e["detalle"] or "anos" in e["detalle"], e)
    justa = temporal / "copias_justas" / "2026-10-16"
    justa.mkdir(parents=True)
    (justa / "MANIFIESTO.json").write_text(
        '{"copiado": "' + (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S") + '"}',
        encoding="utf-8")
    comprobar("6 dias: todavia vale", v.estado_copia_claves(temporal / "copias_justas")["ok"])
    pasada = temporal / "copias_pasadas" / "2026-10-10"
    pasada.mkdir(parents=True)
    (pasada / "MANIFIESTO.json").write_text(
        '{"copiado": "' + (datetime.now() - timedelta(days=9)).strftime("%Y-%m-%d %H:%M:%S") + '"}',
        encoding="utf-8")
    comprobar("9 dias: falla", not v.estado_copia_claves(temporal / "copias_pasadas")["ok"])

    print("6) la copia se revalida de verdad (hashes + clave)")
    v.CACHE_INTEGRA = temporal / "copia_integra.json"
    import hashlib
    copia = temporal / "integra" / "2026-10-22"
    (copia / "wallet").mkdir(parents=True)
    (copia / "wallet" / "una.json").write_text('{"a": 1}', encoding="utf-8")
    # A proposito SIN sello_key.txt: la comparacion de la clave se prueba aparte,
    # en `copia_claves.py`, contra la clave viva de este PC.
    man = {}
    for f in (copia / "wallet" / "una.json",):
        rel = str(f.relative_to(copia)).replace("\\", "/")
        man[rel] = {"bytes": f.stat().st_size,
                     "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
    (copia / "MANIFIESTO.json").write_text(json.dumps(
        {"copiado": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ficheros": man}),
        encoding="utf-8")

    e = v.estado_copia_integra(temporal / "integra", forzar=True)
    comprobar("copia intacta: ok", e["ok"], e)

    # Cache: se toca un fichero y, sin forzar, todavia se cree el veredicto bueno.
    (copia / "wallet" / "una.json").write_text('{"a": 2}', encoding="utf-8")
    e = v.estado_copia_integra(temporal / "integra")
    comprobar("cache del dia: no relee aun", e["ok"], e)
    e = v.estado_copia_integra(temporal / "integra", forzar=True)
    comprobar("forzando: detecta el cambio", not e["ok"], e)
    comprobar("y dice que fichero", "una.json" in e["detalle"], e)

    # Un veredicto malo NO se cachea: al arreglarlo, la siguiente pasada vuelve a OK.
    e = v.estado_copia_integra(temporal / "integra")
    comprobar("malo no se cachea: sigue fallando sin forzar", not e["ok"], e)
    (copia / "wallet" / "una.json").write_text('{"a": 1}', encoding="utf-8")
    e = v.estado_copia_integra(temporal / "integra")
    comprobar("arreglado: vuelve a ok sin forzar", e["ok"], e)

    # Un fichero que falta tiene que cantar.
    (copia / "wallet" / "una.json").unlink()
    e = v.estado_copia_integra(temporal / "integra", forzar=True)
    comprobar("fichero borrado: falla", not e["ok"], e)
    comprobar("y lo nombra", "una.json" in e["detalle"], e)

    print("\nPRUEBA " + ("BIEN" if fallos == 0 else f"MAL ({fallos} fallos)"))
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
