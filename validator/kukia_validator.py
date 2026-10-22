"""KUKIA Validator - Core de verificacion independiente.

Que hace:
  1. Pide al Core un manifiesto firmado con trabajo YA verificado (/red/witness).
  2. Comprueba la firma del manifiesto.
  3. RECALCULA cada tarea por su cuenta (numpy, CPU; no usa el resultado del
     minero ni el del Core para calcularlo).
  4. Compara y firma una atestacion con su PROPIA clave Ed25519:
       - ok=true  -> todo coincide
       - ok=false -> lista de discrepancias (id, esperado, recibido)

Lo que NO hace (a proposito):
  - No paga, no mueve saldos, no escribe en el ledger ni en las billeteras.
  - No sustituye al Core: el Core de pagos sigue siendo uno. Dos Cores que
    pagasen a la vez serian un doble gasto, no mas seguridad.
  - No necesita abrir puertos: solo sale hacia el Core.

El valor esta en que cada validador corre en una maquina distinta y con su
propia clave. Si dos validadores independientes recalculan lo mismo y uno
discrepa, el panel lo ensena. Hoy, en el mismo PC, es la base tecnica; la
independencia real llega al ejecutarlo en otros equipos.

Uso:
    python validator/kukia_validator.py --core http://127.0.0.1:8300
    python validator/kukia_validator.py --nombre VALIDADOR_2 --intervalo 30
    python validator/kukia_validator.py --una-pasada     # una vuelta y sale

Variables de entorno equivalentes:
    KUKIA_CORE, KUKIA_VALIDADOR_NOMBRE, KUKIA_VALIDADOR_LOTE,
    KUKIA_VALIDADOR_INTERVALO, KUKIA_VALIDADOR_MAX_MS,
    KUKIA_VALIDADOR_IDENTIDAD
"""
import argparse
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path

# La direccion del Core puede ser una indireccion ("auto https://.../core_actual.json")
# cuando el tunel publico cambia de nombre: se resuelve al arrancar. Ver
# tools/core_destino.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
try:
    import core_destino as _DESTINO
except Exception as _e:  # aqui se valida igual con la linea tal cual
    _DESTINO = None
    print(f"[validador] AVISO: sin core_destino ({_e})", flush=True)

import numpy as np
import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey)

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "ledger"))
import sellos as SELLOS  # noqa: E402  (JSON canonico, misma regla que el Core)

IDENTIDAD_POR_DEFECTO = Path(__file__).parent / "identidad_validador.json"
TIPOS_SOPORTADOS = {"checksum", "matriz", "inferencia", "difusion_calor"}


# ------------------------------------------------------------------ identidad
def cargar_identidad(ruta: Path, nombre: str) -> dict:
    if ruta.exists():
        try:
            d = json.loads(ruta.read_text(encoding="utf-8"))
            if len(bytes.fromhex(d.get("clave_privada") or "")) == 32:
                d["nombre"] = nombre or d.get("nombre") or "validador"
                return d
        except Exception:
            pass
    priv = secrets.token_bytes(32).hex()
    d = {"nombre": nombre or "validador", "clave_privada": priv,
         "clave_publica": SELLOS.publica_de(priv), "creado": time.ctime()}
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[VALIDADOR] identidad nueva en {ruta}", flush=True)
    print(f"[VALIDADOR] clave publica: {d['clave_publica']}", flush=True)
    return d


def firmar(priv_hex: str, texto: str) -> str:
    return Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(priv_hex)).sign(texto.encode()).hex()


# ------------------------------------------------------------------ calculo
def gen_matriz(n: int, seed: int) -> np.ndarray:
    idx = np.arange(n, dtype=np.float64)
    return (idx[:, None] * 31 + idx[None, :] * 17 + seed) % 97 - 48


def computar(tipo: str, params: dict, max_ops: float):
    """Recalculo INDEPENDIENTE (misma especificacion que el Core, otro proceso)."""
    if not isinstance(params, dict):
        return None
    try:
        if tipo == "checksum":
            data = str(params.get("data", "kukia"))[:4096]
            return hashlib.sha256(data.encode()).hexdigest()
        n = int(params.get("n", 256))
        if not (1 <= n <= 4096):
            return None
        if tipo == "matriz":
            if 2.0 * n ** 3 > max_ops:
                return None
            a = gen_matriz(n, int(params.get("seed", 42)))
            b = gen_matriz(n, int(params.get("seed", 42)) + 1)
            return str(int(round(float((a @ b).sum()))))
        if tipo == "inferencia":
            if 18.0 * n ** 2 > max_ops:
                return None
            x = gen_matriz(n, int(params.get("seed", 7)))
            k = np.array([[1.0, 2.0, 1.0], [2.0, 4.0, 2.0], [1.0, 2.0, 1.0]])
            out = 0.0
            for i in range(3):
                for j in range(3):
                    out += float((x[i:n - 2 + i, j:n - 2 + j] * k[i, j]).sum())
            return str(int(round(out)))
        if tipo == "difusion_calor":
            pasos = int(params.get("pasos", 200))
            if not (1 <= pasos <= 20000) or 10.0 * n ** 2 * pasos > max_ops:
                return None
            x = np.random.default_rng(int(params.get("seed", 42))).random((n, n))
            for _ in range(pasos):
                x[1:-1, 1:-1] = x[1:-1, 1:-1] + 0.2 * (
                    x[:-2, 1:-1] + x[2:, 1:-1] + x[1:-1, :-2] + x[1:-1, 2:]
                    - 4 * x[1:-1, 1:-1])
            return repr(float(x.sum()))
    except Exception:
        return None
    return None


def coincide(tipo: str, params: dict, esperado, recibido) -> bool:
    try:
        if tipo == "difusion_calor":
            a, b = float(esperado), float(recibido)
            if not (np.isfinite(a) and np.isfinite(b)):
                return False
            tol = float(params.get("tolerancia_rel", 1e-9))
            return abs(a - b) <= tol * max(1.0, abs(a))
        return str(esperado).strip() == str(recibido).strip()
    except Exception:
        return False


# ------------------------------------------------------------------ Core
def pedir_witness(core: str, desde: float, lote: int) -> dict:
    r = requests.get(f"{core}/red/witness", params={"desde": desde, "limite": lote},
                     timeout=30)
    r.raise_for_status()
    w = r.json()
    man = w.get("manifiesto") or ""
    firma = w.get("firma") or ""
    pub = w.get("clave_publica") or ""
    if not man or not firma or not pub:
        raise RuntimeError("el Core no firma el manifiesto (sellos desactivados?)")
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(
        bytes.fromhex(firma), man.encode())
    datos = json.loads(man)
    if datos.get("v") != 1 or not isinstance(datos.get("items"), list):
        raise RuntimeError("manifiesto con formato desconocido")
    return datos


def revisar(items: list, max_ops: float) -> tuple:
    """Devuelve (revisadas, discrepancias, saltadas, hasta_max)."""
    revisadas = 0
    discrepancias = []
    saltadas = 0
    hasta = 0.0
    for it in items:
        try:
            tid = str(it.get("id") or "")
            tipo = str(it.get("tipo") or "")
            params = it.get("params") or {}
            metrica = it.get("metrica")
            fin = float(it.get("fin") or 0.0)
        except Exception:
            saltadas += 1
            continue
        hasta = max(hasta, fin)
        if not tid or tipo not in TIPOS_SOPORTADOS or metrica is None:
            saltadas += 1
            continue
        esperado = computar(tipo, params, max_ops)
        if esperado is None:
            saltadas += 1
            continue
        revisadas += 1
        if not coincide(tipo, params, esperado, metrica):
            discrepancias.append({
                "id": tid, "tipo": tipo,
                "esperado": str(esperado)[:200],
                "recibido": str(metrica)[:200]})
    return revisadas, discrepancias, saltadas, hasta


def atestiguar(core: str, ident: dict, desde: float, hasta: float,
               revisadas: int, discrepancias: list, huella: str) -> bool:
    datos = {
        "validador": ident["nombre"],
        "clave_publica": ident["clave_publica"].lower(),
        "desde": round(float(desde), 3),
        "hasta": round(float(hasta), 3),
        "revisadas": int(revisadas),
        "ok": not discrepancias,
        "discrepancias": discrepancias[:20],
        "huella": (huella or "")[:64],
        "ts": int(time.time()),
    }
    texto = SELLOS.canonico(datos)
    datos["firma"] = firmar(ident["clave_privada"], texto)
    r = requests.post(f"{core}/validador/atestacion", json=datos, timeout=30)
    if r.status_code == 200:
        print(f"[VALIDADOR] atestacion aceptada: {revisadas} tareas, "
              f"{len(discrepancias)} discrepancias", flush=True)
        return True
    print(f"[VALIDADOR] atestacion rechazada: {r.status_code} {r.text[:200]}", flush=True)
    return False


def una_pasada(core: str, ident: dict, lote: int, max_ops: float,
               desde: float) -> int:
    try:
        datos = pedir_witness(core, desde, lote)
    except Exception as e:
        print(f"[VALIDADOR] no pude leer /red/witness: {e}", flush=True)
        return 2
    items = datos.get("items") or []
    revisadas, dis, saltadas, hasta = revisar(items, max_ops)
    huella = hashlib.sha256(
        json.dumps(datos, sort_keys=True,
                   separators=(",", ":")).encode()).hexdigest()
    if revisadas == 0:
        print(f"[VALIDADOR] sin tareas nuevas (items={len(items)}, saltadas={saltadas})",
              flush=True)
        return 3
    ok = atestiguar(core, ident, float(datos.get("desde") or desde),
                    hasta or float(datos.get("hasta") or desde), revisadas, dis, huella)
    print(json.dumps({"ok": ok, "revisadas": revisadas,
                      "discrepancias": len(dis), "hasta": hasta}), flush=True)
    return 0 if ok else 1


def bucle(core: str, ident: dict, lote: int, intervalo: float,
          max_ops: float, desde: float) -> int:
    vistos = set()
    espera = 2.0
    print(f"[VALIDADOR] {ident['nombre']} verificando {core} "
          f"(lote={lote}, cada {intervalo:.0f}s)", flush=True)
    while True:
        try:
            datos = pedir_witness(core, desde, lote)
            items = [i for i in (datos.get("items") or [])
                     if str(i.get("id")) not in vistos]
            if items:
                t0 = time.time()
                revisadas, dis, saltadas, hasta = revisar(items, max_ops)
                for i in items:
                    vistos.add(str(i.get("id")))
                if len(vistos) > 20000:
                    vistos = set(list(vistos)[-10000:])
                if revisadas:
                    huella = hashlib.sha256(
                        json.dumps(datos, sort_keys=True,
                                   separators=(",", ":")).encode()).hexdigest()
                    atestiguar(core, ident, float(datos.get("desde") or desde),
                               hasta or float(datos.get("hasta") or desde),
                               revisadas, dis, huella)
                if hasta > 0:
                    desde = max(desde, hasta - 2.0)   # solape de 2 s + dedupe
                print(f"[VALIDADOR] vuelta: {revisadas} revisadas, {len(dis)} discrepancias, "
                      f"{len(items) - revisadas - saltadas} repetidas, "
                      f"{(time.time() - t0):.1f}s", flush=True)
            espera = 2.0
        except KeyboardInterrupt:
            print("\n[VALIDADOR] parado", flush=True)
            return 0
        except Exception as e:
            print(f"[VALIDADOR] error: {e} (reintento en {espera:.0f}s)", flush=True)
            time.sleep(espera)
            espera = min(60.0, espera * 1.7)
            continue
        time.sleep(intervalo)


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Validador independiente de KUKIA")
    ap.add_argument("--core", default=os.environ.get("KUKIA_CORE", "http://127.0.0.1:8300"))
    ap.add_argument("--nombre", default=os.environ.get(
        "KUKIA_VALIDADOR_NOMBRE", "VALIDADOR_" + os.environ.get("COMPUTERNAME", "PC")))
    ap.add_argument("--lote", type=int, default=int(os.environ.get("KUKIA_VALIDADOR_LOTE", "10")))
    ap.add_argument("--intervalo", type=float, default=float(
        os.environ.get("KUKIA_VALIDADOR_INTERVALO", "30")))
    ap.add_argument("--max-ms", type=int, default=int(
        os.environ.get("KUKIA_VALIDADOR_MAX_MS", "45000")),
        help="presupuesto de calculo por vuelta (ms)")
    ap.add_argument("--identidad", default=os.environ.get(
        "KUKIA_VALIDADOR_IDENTIDAD", str(IDENTIDAD_POR_DEFECTO)))
    ap.add_argument("--una-pasada", action="store_true",
                    help="procesa una vuelta y sale (para pruebas)")
    ap.add_argument("--desde", type=float, default=0.0,
                    help="epoch desde el que mirar (0 = ultimos 5 minutos)")
    args = ap.parse_args()

    core = args.core.rstrip("/")
    if _DESTINO is not None:
        try:
            core = _DESTINO.resolver(core)
        except Exception as e:
            print(f"[validador] AVISO: no pude resolver la direccion del Core ({e})",
                  flush=True)
    ident = cargar_identidad(Path(args.identidad), args.nombre)
    ident["nombre"] = (args.nombre or ident["nombre"]).strip()[:64]
    lote = max(1, min(50, args.lote))
    intervalo = max(5.0, args.intervalo)
    desde = args.desde if args.desde > 0 else time.time() - 300.0
    max_ops = 2_000_000_000.0

    if args.una_pasada:
        return una_pasada(core, ident, lote, max_ops, desde)
    try:
        return bucle(core, ident, lote, intervalo, max_ops, desde)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
