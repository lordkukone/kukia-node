"""KUKIA export_stats — genera docs/stats.json desde el Core local (SOLO LECTURA).

Uso:
    python scripts/export_stats.py [--core http://127.0.0.1:8300] [--out docs/stats.json]

Reglas:
- Consulta UNICAMENTE GET /stats (endpoint publico verificado del Core).
- No inicia el Core, no abre puertos, no toca la BD, el ledger ni ninguna config.
- No incluye correos, IPs, claves publicas/privadas, tokens ni datos personales.
- tareas_ejecutando y tareas_fallidas NO los expone GET /stats -> se marcan
  como "no_disponible" (null) en lugar de inventar cifras.
- Si el Core no responde: informa del error y NO sustituye un snapshot valido
  existente. Si no existe ninguno, escribe uno con estado "no_disponible".
"""
import argparse
import datetime
import json
import os
import sys
import urllib.request

CORE_DEFAULT = "http://127.0.0.1:8300"
TIMEOUT_SEG = 5

# Lista blanca explicita: unico origen es GET /stats del Core local.
CAMPOS_APROBADOS = {
    "supply_emitido", "max_supply", "nodos", "tareas_ok", "tareas_pend",
    "recompensa_base_actual", "emision",
}

NO_DISPONIBLES = ["tareas_ejecutando", "tareas_fallidas"]


def pedir_stats(core: str) -> dict:
    """GET /stats del Core local. Solo lectura. Timeout corto."""
    url = core.rstrip("/") + "/stats"
    req = urllib.request.Request(url, headers={"User-Agent": "kukia-export-stats/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEG) as r:
        datos = json.loads(r.read().decode("utf-8"))
    # Ignorar campos que no estan en la lista blanca aprobada.
    return {k: v for k, v in datos.items() if k in CAMPOS_APROBADOS}


def construir_snapshot(st: dict, ahora: datetime.datetime) -> dict:
    em = st.get("emision") or {}
    return {
        "estado": "ok",
        "testnet": True,
        "fecha_actualizacion": ahora.isoformat(timespec="seconds"),
        "etiqueta_fecha": ahora.strftime("%d/%m/%Y %H:%M"),
        "metricas": {
            "nodos_registrados": st.get("nodos"),
            "tareas_verificadas": st.get("tareas_ok"),
            "tareas_en_cola": st.get("tareas_pend"),
            "tareas_ejecutando": None,
            "tareas_fallidas": None,
            "suministro_total_kia": st.get("supply_emitido"),
            "suministro_maximo_kia": st.get("max_supply"),
            "recompensa_base_actual_kia": st.get("recompensa_base_actual"),
            "era_actual": em.get("era_actual"),
            "proximo_halving": em.get("proximo_halving"),
            "fin_emision": em.get("fin_emision"),
        },
        "no_disponibles": list(NO_DISPONIBLES),
        "nota": ("Instantanea generada por scripts/export_stats.py desde el Core local. "
                 "TESTNET: no son metricas en tiempo real."),
    }


def construir_no_disponible(ahora: datetime.datetime, detalle: str) -> dict:
    return {
        "estado": "no_disponible",
        "testnet": True,
        "fecha_actualizacion": ahora.isoformat(timespec="seconds"),
        "etiqueta_fecha": ahora.strftime("%d/%m/%Y %H:%M"),
        "detalle": detalle,
        "metricas": {
            "nodos_registrados": None,
            "tareas_verificadas": None,
            "tareas_en_cola": None,
            "tareas_ejecutando": None,
            "tareas_fallidas": None,
            "suministro_total_kia": None,
            "suministro_maximo_kia": None,
            "recompensa_base_actual_kia": None,
            "era_actual": None,
            "proximo_halving": None,
            "fin_emision": None,
        },
        "no_disponibles": list(NO_DISPONIBLES),
        "nota": "Core local sin respuesta en el momento de la generacion. TESTNET.",
    }


def es_numero(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def snapshot_valido_existente(out: str) -> bool:
    try:
        with open(out, encoding="utf-8") as f:
            return json.load(f).get("estado") == "ok"
    except Exception:
        return False


def saldos_valido_existente(out: str) -> bool:
    try:
        with open(out, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("estado") == "ok" and isinstance(d.get("saldos"), dict)
    except Exception:
        return False


def construir_saldos(saldos: dict, ahora: datetime.datetime) -> dict:
    return {
        "estado": "ok",
        "testnet": True,
        "fecha_actualizacion": ahora.isoformat(timespec="seconds"),
        "etiqueta_fecha": ahora.strftime("%d/%m/%Y %H:%M"),
        "saldos": saldos.get("saldos", {}),
        "direcciones": saldos.get("direcciones", 0),
        "nota": ("Mapa publico direccion->saldo desde el Core local. "
                 "TESTNET: instantanea, no tiempo real."),
    }


def construir_saldos_no_disponible(ahora: datetime.datetime, detalle: str) -> dict:
    return {
        "estado": "no_disponible",
        "testnet": True,
        "fecha_actualizacion": ahora.isoformat(timespec="seconds"),
        "etiqueta_fecha": ahora.strftime("%d/%m/%Y %H:%M"),
        "detalle": detalle,
        "saldos": {},
        "direcciones": 0,
        "nota": "Core local sin respuesta en el momento de la generacion. TESTNET.",
    }


def exportar_saldos(core: str, out: str, ahora: datetime.datetime) -> bool:
    """Genera el mapa publico de saldos. True si OK. Nunca inventa cifras."""
    try:
        req = urllib.request.Request(core.rstrip("/") + "/saldos",
                                     headers={"User-Agent": "kukia-export-stats/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEG) as r:
            datos = json.loads(r.read().decode("utf-8"))
        mapa = datos.get("saldos")
        if not isinstance(mapa, dict):
            raise ValueError("respuesta sin mapa 'saldos'")
        for k, v in mapa.items():
            if not isinstance(k, str) or not es_numero(v):
                raise ValueError("entrada de saldo invalida")
    except Exception as e:
        detalle = f"Core local sin respuesta o datos invalidos ({e.__class__.__name__})"
        if saldos_valido_existente(out):
            print(f"[export_stats] ERROR saldos: {detalle}. Se conserva el fichero.",
                  file=sys.stderr)
        else:
            escribir(out, construir_saldos_no_disponible(ahora, detalle))
            print(f"[export_stats] ERROR saldos: {detalle}. Marcador escrito.",
                  file=sys.stderr)
        return False
    escribir(out, construir_saldos(datos, ahora))
    print(f"[export_stats] OK saldos: {out} (direcciones={len(mapa)})")
    return True


def escribir(out: str, data: dict) -> None:
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, out)


def historial_valido_existente(out: str) -> bool:
    try:
        with open(out, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("estado") == "ok" and isinstance(d.get("movimientos"), list)
    except Exception:
        return False


def construir_historial(rows: list, ahora: datetime.datetime) -> dict:
    return {
        "estado": "ok",
        "testnet": True,
        "fecha_actualizacion": ahora.isoformat(timespec="seconds"),
        "etiqueta_fecha": ahora.strftime("%d/%m/%Y %H:%M"),
        "movimientos": [{"ts": r["ts"], "id": r["nodo_id"], "k": r["kuki"],
                         "m": r["motivo"], "tid": r["tarea_id"]} for r in rows],
        "cuenta": len(rows),
        "nota": ("Movimientos reales del ledger (maximo 200 por respuesta). "
                 "TESTNET: instantanea, no tiempo real."),
    }


def construir_historial_no_disponible(ahora: datetime.datetime, detalle: str) -> dict:
    return {
        "estado": "no_disponible",
        "testnet": True,
        "fecha_actualizacion": ahora.isoformat(timespec="seconds"),
        "etiqueta_fecha": ahora.strftime("%d/%m/%Y %H:%M"),
        "detalle": detalle,
        "movimientos": [],
        "cuenta": 0,
        "nota": "Core local sin respuesta en el momento de la generacion. TESTNET.",
    }


def exportar_historial(core: str, out: str, ahora: datetime.datetime) -> bool:
    """Genera el historial publico de movimientos. True si OK. Sin inventos."""
    try:
        req = urllib.request.Request(core.rstrip("/") + "/ledger",
                                     headers={"User-Agent": "kukia-export-stats/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEG) as r:
            rows = json.loads(r.read().decode("utf-8"))
        if not isinstance(rows, list):
            raise ValueError("respuesta /ledger no es lista")
        for i, r in enumerate(rows):
            if not isinstance(r, dict):
                raise ValueError(f"fila {i} no es objeto")
            if not isinstance(r.get("ts"), str) or not isinstance(r.get("motivo"), str) \
                    or not isinstance(r.get("tarea_id"), str) or not isinstance(r.get("nodo_id"), str):
                raise ValueError(f"fila {i} con campos invalidos")
            if not es_numero(r.get("kuki")):
                raise ValueError(f"fila {i} con kuki no numerico")
    except Exception as e:
        detalle = f"Core local sin respuesta o datos invalidos ({e.__class__.__name__})"
        if historial_valido_existente(out):
            print(f"[export_stats] ERROR historial: {detalle}. Se conserva el fichero.",
                  file=sys.stderr)
        else:
            escribir(out, construir_historial_no_disponible(ahora, detalle))
            print(f"[export_stats] ERROR historial: {detalle}. Marcador escrito.",
                  file=sys.stderr)
        return False
    escribir(out, construir_historial(rows, ahora))
    print(f"[export_stats] OK historial: {out} (movimientos={len(rows)})")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--core", default=CORE_DEFAULT, help="URL del Core (GET /stats)")
    ap.add_argument("--out", default=None,
                    help="Ruta del stats.json (por defecto: docs/stats.json del repositorio)")
    ap.add_argument("--con-saldos", default=None, metavar="RUTA",
                    help="Generar tambien el mapa publico direccion->saldo en RUTA "
                         "(por defecto no se genera)")
    ap.add_argument("--con-historial", default=None, metavar="RUTA",
                    help="Generar tambien el historial publico de movimientos en RUTA "
                         "(por defecto no se genera)")
    args = ap.parse_args()

    if args.out:
        out = os.path.abspath(args.out)
    else:
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = os.path.join(raiz, "docs", "stats.json")

    ahora = datetime.datetime.now()
    try:
        st = pedir_stats(args.core)
    except Exception as e:
        detalle = f"Core local sin respuesta en {args.core} ({e.__class__.__name__})"
        if snapshot_valido_existente(out):
            print(f"[export_stats] ERROR: {detalle}. "
                  "Se conserva el snapshot valido existente: " + out, file=sys.stderr)
            sys.exit(1)
        escribir(out, construir_no_disponible(ahora, detalle))
        print(f"[export_stats] ERROR: {detalle}. Snapshot 'no_disponible' escrito: " + out,
              file=sys.stderr)
        sys.exit(1)

    escribir(out, construir_snapshot(st, ahora))
    print(f"[export_stats] OK: {out} "
          f"(nodos={st.get('nodos')}, tareas_ok={st.get('tareas_ok')}, "
          f"en_cola={st.get('tareas_pend')}, supply={st.get('supply_emitido')})")
    if args.con_saldos:
        if not exportar_saldos(args.core, os.path.abspath(args.con_saldos), ahora):
            sys.exit(1)
    if args.con_historial:
        if not exportar_historial(args.core, os.path.abspath(args.con_historial), ahora):
            sys.exit(1)


if __name__ == "__main__":
    main()
