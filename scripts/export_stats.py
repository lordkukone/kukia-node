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


def snapshot_valido_existente(out: str) -> bool:
    try:
        with open(out, encoding="utf-8") as f:
            return json.load(f).get("estado") == "ok"
    except Exception:
        return False


def escribir(out: str, data: dict) -> None:
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--core", default=CORE_DEFAULT, help="URL del Core (GET /stats)")
    ap.add_argument("--out", default=None,
                    help="Ruta del stats.json (por defecto: docs/stats.json del repositorio)")
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


if __name__ == "__main__":
    main()
