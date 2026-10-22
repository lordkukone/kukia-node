"""KUKIA Worker - minero multi-GPU: se registra, pide trabajo, ejecuta en GPU.

VERIFICACION REAL: el worker calcula el resultado (no un hash propio) y el
Core lo recalcula y compara. El worker NO puede falsificar: si el numero no
coincide exactamente con el recalculado por el Core, no cobra.

MULTI-GPU: usa TODAS las GPUs disponibles a la vez (una cola por GPU).

AUDITORIA 2026-10-18:
  - Implementado 'difusion_calor' (antes devolvia el literal
    "tarea_no_soportada": habia 97 tareas de ese tipo que era imposible
    cobrar y cada intento costaba minutos de CPU al Core).
  - /tareas/pendiente exige firma del nodo ("pendiente:<id>:<ts>").
  - Se leen los codigos 401/403/429 del Core y se informa en claro
    (antes un 429 reventaba con KeyError y solo ponia "err").
  - El resultado de difusion va como numero con toda la precision
    (float64), que es lo que el Core verifica con tolerancia relativa.

AUDITORIA 2026-10-21 (por que el minero se quedaba sin minar):
  - El Core pierde de vista un nodo (o una tarea) unos instantes y responde
    404 "nodo no registrado". Antes eso dejaba al minero pidiendo trabajo cada
    10 s PARA SIEMPRE con la GPU encendida (233 veces en el log). Ahora se
    comprueba el mismo id y, si de verdad no existe, se registra otra vez.
  - Las tareas que quedan 'ejecutando' a nombre del nodo le ocupan el cupo (4)
    y el Core le responde "limite de tareas simultaneas" durante 30 minutos.
    Ahora un vigia pide el reinicio del propio nodo (firmado) si lleva 2 min
    sin cobrar con el cupo lleno, y reinicia el proceso si a los 5 min sigue
    igual con el Core en pie.
  - La entrega tenia timeout de 900 s: una respuesta colgada dejaba la GPU
    parada 15 minutos. Ahora son 90 s y, si falla la red, se reintenta una vez
    (un 409 confirma que ya estaba pagada: no hay doble pago).
  - Cada minuto se escribe un resumen (tareas/min, KIA/min, fallos) para que
    "esta minando" sea un dato y no una suposicion.
"""
import hashlib, json, os, queue, secrets, subprocess, sys, time, threading, socket, urllib.parse
import requests
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "ledger"))
import kukia_wallet as WALLET

# Se puede apuntar a otro Core para pruebas: KUKIA_CORE=http://127.0.0.1:8403
#
# La direccion del Core puede venir de tres sitios, por este orden:
#   1. KUKIA_CORE (lo que pone ARRANCAR_MINERO.cmd), que puede ser una URL o
#      una indireccion: "auto https://kukia-network.surge.sh/core_actual.json".
#   2. core_minero.txt del directorio de arriba (si se lanza a mano).
#   3. el Core de este mismo PC.
# La indireccion existe porque el tunel publico cambia de nombre: el fichero
# del espejo dice cual esta vivo. Ver tools/core_destino.py.
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
try:
    import core_destino as _DESTINO
except Exception as _e:  # sin el modulo se mina igual, con la linea tal cual
    _DESTINO = None
    print(f"[worker] AVISO: sin core_destino ({_e})", flush=True)


def _leer_core() -> str:
    """Primera linea util de core_minero.txt (o el Core local si no hay)."""
    f = Path(__file__).resolve().parent.parent / "core_minero.txt"
    try:
        for linea in f.read_text(encoding="utf-8", errors="replace").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#"):
                return linea
    except Exception:
        pass
    return "http://127.0.0.1:8300"


CORE_CRUDO = (os.environ.get("KUKIA_CORE", "") or "").strip() or _leer_core()


def _resolver_core(valor: str, ttl: float = 0) -> str:
    if _DESTINO is None:
        return valor
    try:
        return _DESTINO.resolver(valor, ttl=ttl)
    except Exception as e:
        print(f"[worker] AVISO: no pude resolver la direccion del Core ({e}); "
              f"uso '{valor}'", flush=True)
        return valor


CORE = _resolver_core(CORE_CRUDO)

# AUDITORIA 2026-10-20: la entrega tenia timeout=3600. Si el Core se colgaba
# (o tardaba en verificar una tarea enorme), el hilo de la GPU se quedaba
# bloqueado una hora sin minar y sin liberar el trabajo. Ahora es acotado y,
# al agotarse, se reintenta: la tarea vence sola en el Core y vuelve a la cola.
# 900 s era demasiado: una entrega colgada dejaba la GPU sin minar 15 minutos
# (medido: cuda:1 a 0 tareas/min con dos tareas 'ejecutando' en el Core).
TIMEOUT_ENTREGA = max(30.0, float(os.environ.get("KUKIA_TIMEOUT_ENTREGA", "90")))
TIMEOUT_API = max(5.0, float(os.environ.get("KUKIA_TIMEOUT_API", "30")))

# ARREGLO 2026-10-21 (el minero minaba a la mitad de su capacidad): el bucle
# pedia trabajo, calculaba y entregaba EN SERIE. Medido en esta maquina, pedir
# trabajo tarda ~260 ms de mediana (p90 455 ms) y calcular un checksum 0 ms, asi
# que la GPU se pasaba el rato esperando al Core. Con un hilo que mantiene la
# siguiente tarea ya pedida y otro que entrega, la GPU calcula sin huecos.
# El Core permite 4 tareas en vuelo por nodo: 1 calculando + 1 en cola + hasta 2
# entregando. Subirlo mas solo provoca "limite de tareas simultaneas".
PROFUNDIDAD = max(1, min(2, int(os.environ.get("KUKIA_PROFUNDIDAD", "1"))))
RESUMEN_SEG = max(10.0, float(os.environ.get("KUKIA_RESUMEN_SEG", "60")))

# AUDITORIA 2026-10-20 (seguridad del minero): el worker NO confia en el Core.
# Antes hacia int(params['n']) y range(pasos) con lo que le llegara: un Core
# comprometido (o un MITM) podia mandar n=100000/pasos=1000000000 y dejar la
# GPU sin memoria o colgada. Ahora valida el trabajo con los mismos topes
# duros del Core antes de calcular nada.
TIPOS_SOPORTADOS = {"checksum", "matriz", "inferencia", "difusion_calor"}
LIMITE_N = 4096
LIMITE_PASOS = 20000
LIMITE_PARAMS_BYTES = 65536
MAX_DATA = 4096
MAX_OPS_DIFUSION = max(1_000_000, int(os.environ.get("KUKIA_MAX_OPS_DIFUSION", "2000000000")))
# 2*n^3: con 2e11 cabe hasta n=4096 (1.37e11). Es el techo del Core.
MAX_OPS_MATRIZ = max(1_000_000, int(os.environ.get("KUKIA_MAX_OPS_MATRIZ", "200000000000")))

def _get(url, **kw):
    """GET sin redirecciones: una redireccion podria llevarse la firma a otro host."""
    kw.setdefault("allow_redirects", False)
    return requests.get(url, **kw)

def _post(url, **kw):
    kw.setdefault("allow_redirects", False)
    return requests.post(url, **kw)

def _core_seguro(core: str) -> tuple:
    try:
        u = urllib.parse.urlsplit(core or "")
    except Exception:
        return False, "URL del Core invalida"
    if u.scheme not in ("http", "https") or not u.hostname:
        return False, "la URL del Core debe empezar por http:// o https://"
    if u.username or u.password:
        return False, "la URL del Core no debe llevar usuario/contrasena"
    if u.scheme == "http" and u.hostname.lower() not in ("127.0.0.1", "localhost", "::1"):
        if os.environ.get("KUKIA_CORE_INSEGURO", "0") != "1":
            return False, ("Core remoto por HTTP SIN cifrar: cualquiera en la red puede leer "
                           "las firmas y alterar el trabajo. Usa https:// (tunel) o, si es "
                           "una prueba consciente, arranca con KUKIA_CORE_INSEGURO=1")
    return True, ""

def _proteger(ruta) -> None:
    """Permisos solo para el dueno del fichero.

    POSIX: chmod 600. Windows: se corta la herencia con `icacls` para que otra
    cuenta del mismo PC no pueda leer la recuperacion del minero (tiene la
    contrasena y las 20 palabras: es la llave entera).
    """
    try:
        os.chmod(ruta, 0o600)
    except Exception:
        pass
    if os.name != "nt":
        return
    try:
        usuario = os.environ.get("USERNAME") or ""
        if usuario:
            subprocess.run(["icacls", str(ruta), "/inheritance:r",
                            "/grant:r", f"{usuario}:(R,W)"],
                           capture_output=True, timeout=10,
                           creationflags=0x08000000)
    except Exception:
        pass

def _indice_gpu(device) -> int:
    s = str(device or "")
    if ":" in s:
        try:
            return int(s.split(":", 1)[1])
        except Exception:
            return 0
    return 0

def _gpu_stats(device):
    """(utilizacion %, VRAM usada MB) reales, o (None, None) si no se puede.

    nvidia-smi es lo mas fiable y viene con el driver; torch.cuda.utilization
    necesita nvidia-ml-py, que no siempre esta instalado. Se intenta primero
    nvidia-smi y se cae a torch sin romper el minero.
    """
    idx = _indice_gpu(device)
    # FALLO REAL (21-10-2026): esto se llama desde la telemetria cada 3 s y
    # nvidia-smi es un programa de consola: sin esta bandera, Windows abria una
    # ventana negra cada 3 s en la cara del usuario, todo el dia. Con
    # CREATE_NO_WINDOW se ejecuta igual y no se ve nada.
    extra = {}
    if os.name == "nt":
        extra["creationflags"] = 0x08000000   # CREATE_NO_WINDOW
    try:
        salida = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
             "--format=csv,noheader,nounits", "-i", str(idx)],
            capture_output=True, text=True, timeout=4, **extra)
        if salida.returncode == 0 and salida.stdout.strip():
            partes = [x.strip() for x in salida.stdout.strip().splitlines()[0].split(",")]
            if len(partes) >= 2:
                return int(float(partes[0])), int(float(partes[1]))
    except Exception:
        pass
    try:
        import torch
        if torch.cuda.is_available():
            util = int(torch.cuda.utilization(idx))
            libre, total = torch.cuda.mem_get_info(idx)
            return util, int((total - libre) / 1048576)
    except Exception:
        pass
    return None, None

def _bucle_telemetria(nid: str, device: str, vram_gb: int, tops: float,
                      privkey: str, estado: dict) -> None:
    """Latido firmado cada 3 s: el panel ve la GPU en vivo, no solo al pagar."""
    avisado = False
    while True:
        try:
            util, mem = _gpu_stats(device)
            datos = {"device": str(device), "vram_gb": int(vram_gb),
                     "tops_declarados": float(tops),
                     "gflops_base": _GFLOPS_BASE.get(str(device), 0.0),
                     "gpu_util_pct": int(util) if util is not None else -1,
                     "gpu_mem_mb": int(mem) if mem is not None else -1,
                     "tarea_ms": round(float(estado.get("ms") or 0.0), 1)}
            metricas = json.dumps(datos, separators=(",", ":"), ensure_ascii=False)
            ts = int(time.time())
            huella = hashlib.sha256(metricas.encode()).hexdigest()
            firma = WALLET.firmar(f"telemetria:{nid}:{ts}:{huella}", privkey)
            r = _post(f"{CORE}/nodos/telemetria",
                      json={"nodo_id": nid, "device": str(device),
                            "metricas": metricas, "ts": ts, "firma": firma},
                      timeout=10)
            if r.status_code in (401, 403, 404):
                if not avisado:
                    print(f"[KUKIA] telemetria en vivo no disponible "
                          f"({r.status_code}); actualiza el Core", flush=True)
                    avisado = True
                return
        except Exception:
            pass
        time.sleep(3)

# ARREGLO 2026-10-19: antes cada arranque registraba un nodo NUEVO (uuid) y los
# KIA ganados se quedaban en ese id, no en la billetera. Ahora:
#  - el nodo se registra con direccion_cobro = direccion de la billetera,
#  - el id se guarda en identidad_minero.json y se reutiliza al rearrancar,
#  - al arrancar se barren a la billetera los saldos viejos de nodos que
#    firmaban con la misma clave (recupera lo que quedo huerfano).
IDENTIDAD = Path(__file__).parent / "identidad_minero.json"

# BENCHMARK DE ARRANQUE (21-10-2026): antes de minar se mide la GPU con las
# mismas cuentas que las tareas de la red. Sirve para detectar una GPU mal
# configurada antes de perder horas minando, y para publicar en la telemetria
# lo que esta maquina da de verdad (no lo que dice el driver).
BENCHMARK = Path(__file__).parent / "benchmark.json"
_GFLOPS_BASE = {}


def _benchmark_arranque() -> dict:
    """Mide la GPU (o reutiliza una medida de los ultimos 7 dias).

    Nunca lanza: si falla, el minero arranca igual sin benchmark. Una medida
    vieja se repite porque drivers, relojes y temperatura cambian el resultado.
    """
    global _GFLOPS_BASE
    edad = 1e18
    try:
        d = json.loads(BENCHMARK.read_text(encoding="utf-8"))
        edad = time.time() - time.mktime(time.strptime(d.get("fecha", ""),
                                                      "%Y-%m-%d %H:%M:%S"))
    except Exception:
        d = None
    if d is None or edad > 7 * 86400:
        script = Path(__file__).resolve().parent.parent / "tools" / "benchmark_gpu.py"
        if not script.exists():
            return {}
        print("[KUKIA] midiendo la GPU antes de minar (unos segundos)...", flush=True)
        extra = {}
        if os.name == "nt":
            extra["creationflags"] = 0x08000000   # CREATE_NO_WINDOW
        try:
            subprocess.run([sys.executable, str(script), "--json"],
                           cwd=str(script.parent.parent), timeout=300, **extra)
            d = json.loads(BENCHMARK.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[KUKIA] benchmark no disponible ({type(e).__name__}); se mina igual",
                  flush=True)
            return {}
    piezas = []
    for g in (d or {}).get("gpus") or []:
        idx = int(g.get("indice", -1))
        clave = "cpu" if idx < 0 else f"cuda:{idx}"
        _GFLOPS_BASE[clave] = float(g.get("gflops") or 0.0)
        # Nombre real del adaptador (lo pone el driver) para que la cifra no
        # sea un "cuda:0" anonimo: RTX 5070 -> 42,2 GFLOPS medidos.
        nombre = str(g.get("nombre") or clave).strip() or clave
        piezas.append(f"{nombre} ({clave}) {_GFLOPS_BASE[clave]:.1f} GFLOPS")
    if _GFLOPS_BASE:
        print("[KUKIA] potencia medida al arrancar: " + " | ".join(piezas), flush=True)
    return dict(_GFLOPS_BASE)
BARRIDO_SEG = 300.0        # cada cuanto se revisan saldos huerfanos
_barrido_lock = threading.Lock()
_ultimo_barrido = 0.0

def _leer_identidad() -> dict:
    try:
        return json.loads(IDENTIDAD.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _guardar_identidad(device: str, nid: str, pubkey: str):
    d = _leer_identidad()
    d[device] = {"id": nid, "clave_publica": pubkey}
    try:
        IDENTIDAD.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
        _proteger(IDENTIDAD)
    except Exception as e:
        print(f"[KUKIA] no pude guardar la identidad: {e}", flush=True)

def _prueba_registro(pubkey: str, privkey: str):
    """Reto firmado de registro (anti-Sybil).

    El Core nuevo exige demostrar que se tiene la clave privada antes de dar
    un id de nodo. Si el Core es antiguo (404/405) se devuelve None y el
    registro sigue por el camino clasico, para no romper una red a medias.
    """
    if not (pubkey and privkey):
        return None
    try:
        r = _post(f"{CORE}/nodos/reto", json={"clave_publica": pubkey},
                  timeout=TIMEOUT_API)
    except Exception:
        return None
    if r.status_code in (404, 405):
        return None
    if r.status_code != 200:
        return None
    try:
        d = r.json()
        msg = str(d.get("mensaje") or "")
        nonce = str(d.get("nonce") or "")
        if not msg or not nonce:
            return None
        return {"reto": nonce, "firma_reto": WALLET.firmar(msg, privkey)}
    except Exception:
        return None


def _registrar_o_reusar(etiqueta: str, device: str, vram_gb: int, tops: float,
                        pubkey: str, direccion: str, privkey: str):
    """Reutiliza el nodo de un arranque anterior si sigue existiendo en el Core."""
    guardado = _leer_identidad().get(device) or {}
    nid = guardado.get("id")
    if nid and guardado.get("clave_publica") == pubkey:
        try:
            # AUDITORIA 2026-10-20: se pregunta por ESE nodo, no por la lista
            # entera (que ahora esta acotada y puede no incluirlo).
            r = _get(f"{CORE}/nodos/{nid}", timeout=30)
            if r.status_code == 200 and (r.json() or {}).get("id") == nid:
                print(f"[KUKIA] nodo {nid} ({etiqueta}) identidad reutilizada", flush=True)
                return nid
        except Exception:
            pass
    carga = {"nombre": etiqueta, "vram_gb": vram_gb, "tops": tops,
             "clave_publica": pubkey, "direccion_cobro": direccion}
    prueba = _prueba_registro(pubkey, privkey)
    if prueba:
        carga.update(prueba)
    r = _post(f"{CORE}/nodos/registrar", json=carga, timeout=30)
    r.raise_for_status()
    nid = r.json()["id"]
    _guardar_identidad(device, nid, pubkey)
    print(f"[KUKIA] nodo {nid} ({etiqueta}) vram={vram_gb}GB listo (cobra en {direccion})", flush=True)
    return nid

def _avisar_reinicio(nid: str, etiqueta: str, privkey: str) -> None:
    """Suelta las tareas que dejo el proceso anterior.

    Sin esto, relanzar el minero (o que lo relance la guardia) lo dejaba sin
    minar hasta 30 minutos: el Core seguia contandole las tareas 'ejecutando'
    de un proceso que ya no existe, asi que respondia "limite de tareas
    simultaneas" a todas las peticiones.
    """
    if not privkey:
        return
    try:
        ts = int(time.time())
        firma = WALLET.firmar(f"reinicio:{nid}:{ts}", privkey)
        r = _post(f"{CORE}/nodos/{nid}/reinicio",
                  json={"ts": ts, "firma": firma}, timeout=TIMEOUT_API)
        if r.status_code == 200:
            n = int((r.json() or {}).get("liberadas") or 0)
            if n:
                print(f"[KUKIA] {etiqueta} {n} tarea(s) del proceso anterior a la cola",
                      flush=True)
    except Exception:
        pass


def _asegurar_cobro(nid: str, etiqueta: str, direccion: str, privkey: str):
    """Deja el nodo pagando a la billetera (tambien si se reutilizo uno viejo)."""
    if not (direccion and privkey):
        return
    try:
        rn = _get(f"{CORE}/nodos/{nid}", timeout=30)
        actual = rn.json() if rn.status_code == 200 else None
        if not actual:
            return
        if (actual.get("direccion_cobro") or "").strip().upper() == direccion.upper():
            return
        ts = int(time.time())
        firma = WALLET.firmar(f"cobro:{nid}:{direccion}:{ts}", privkey)
        r = _post(f"{CORE}/nodos/{nid}/cobro",
                  json={"direccion": direccion, "ts": ts, "firma": firma}, timeout=30)
        if r.status_code == 200:
            print(f"[KUKIA] {etiqueta} cobro actualizado -> {direccion}", flush=True)
        else:
            print(f"[KUKIA] {etiqueta} no pude fijar el cobro: {r.status_code} {r.text[:120]}", flush=True)
    except Exception as e:
        print(f"[KUKIA] {etiqueta} error fijando cobro: {e}", flush=True)

def barrer_saldos(etiqueta: str, wallet: dict, privkey: str, forzar: bool = False):
    """Mueve a la billetera el saldo que quede en nodos firmados con su clave.

    Idempotente y sin privilegios: firma cada transferencia con la clave del
    nodo. Si el Core ya paga directo a la direccion, no hay nada que barrer.
    """
    global _ultimo_barrido
    direccion = (wallet.get("direccion") or "").strip().upper()
    pubkey = wallet.get("clave_publica") or ""
    if not (direccion and pubkey and privkey):
        return
    if not forzar and time.time() - _ultimo_barrido < BARRIDO_SEG:
        return
    if not _barrido_lock.acquire(blocking=False):
        return
    try:
        _ultimo_barrido = time.time()
        nodos = _get(f"{CORE}/nodos?limite=1000", timeout=30).json()
        for n in nodos:
            if (n.get("clave_publica") or "") != pubkey:
                continue
            try:
                s = _get(f"{CORE}/saldo/{n['id']}", timeout=30).json().get("saldo", 0)
                monto = round(float(s), 4)
                if monto <= 0:
                    continue
                nonce = f"barrido-{n['id']}-{int(time.time() * 1000)}"
                mensaje = f"transferir:{n['id']}:{direccion}:{monto}:{nonce}"
                firma = WALLET.firmar(mensaje, privkey)
                r = _post(f"{CORE}/transferir",
                          json={"de_nodo": n["id"], "a_direccion": direccion,
                                "monto": monto, "nonce": nonce, "firma": firma},
                          timeout=60)
                if r.status_code == 200:
                    print(f"[KUKIA] {etiqueta} barrido: {monto} KIA de {n['id']} -> {direccion}", flush=True)
                else:
                    print(f"[KUKIA] {etiqueta} barrido rechazado en {n['id']}: "
                          f"{r.status_code} {r.text[:120]}", flush=True)
            except Exception as e:
                print(f"[KUKIA] {etiqueta} barrido err en {n['id']}: {e}", flush=True)
            time.sleep(0.2)
    except Exception as e:
        print(f"[KUKIA] {etiqueta} barrido err: {e}", flush=True)
    finally:
        _barrido_lock.release()

def validar_tarea(tipo, params) -> tuple:
    """Rechaza trabajo imposible ANTES de tocar la GPU.

    Devuelve (ok, motivo, params_normalizados). Los topes son los mismos que
    aplica el Core al crear la tarea; si un Core comprometido manda algo fuera
    de rango, el worker no se cuelga ni se queda sin VRAM: descarta la tarea y
    espera a que venza.
    """
    if tipo not in TIPOS_SOPORTADOS:
        return False, f"tipo no soportado: {tipo!r}", {}
    if not isinstance(params, dict):
        return False, "params no es un objeto", {}
    try:
        crudo = json.dumps(params, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        return False, "params no serializable", {}
    if len(crudo) > LIMITE_PARAMS_BYTES:
        return False, f"params demasiado grande ({len(crudo)} bytes)", {}
    p = dict(params)
    try:
        n = int(p.get("n", 256))
    except Exception:
        return False, "n invalido", {}
    if not (1 <= n <= LIMITE_N):
        return False, f"n fuera de rango (1..{LIMITE_N})", {}
    p["n"] = n
    try:
        seed = int(p.get("seed", 42))
    except Exception:
        return False, "seed invalido", {}
    if abs(seed) > (1 << 63):
        return False, "seed fuera de rango", {}
    p["seed"] = seed
    if tipo == "checksum":
        data = str(p.get("data", "kukia"))
        if len(data) > MAX_DATA:
            return False, f"data demasiado larga (max {MAX_DATA})", {}
        p["data"] = data
        return True, "", p
    if tipo == "difusion_calor":
        if n < 3:
            return False, "n demasiado pequeno para difusion", {}
        try:
            pasos = int(p.get("pasos", 200))
        except Exception:
            return False, "pasos invalido", {}
        if not (1 <= pasos <= LIMITE_PASOS):
            return False, f"pasos fuera de rango (1..{LIMITE_PASOS})", {}
        # Mismo presupuesto que el Core: n*n*pasos (no 10x). Si se comparara
        # con el coste real de 10 ops/celda, se rechazarian tareas legitimas
        # que el Core ya acepto (p.ej. n=1024/pasos=1907).
        if float(n * n * pasos) > MAX_OPS_DIFUSION:
            return False, (f"difusion demasiado cara ({n*n*pasos} celdas-paso, "
                           f"max {MAX_OPS_DIFUSION})"), {}
        p["pasos"] = pasos
        return True, "", p
    if tipo == "matriz":
        if 2.0 * n ** 3 > MAX_OPS_MATRIZ:
            return False, (f"matriz demasiado cara ({2.0*n**3:.0f} ops, "
                           f"max {MAX_OPS_MATRIZ})"), {}
        return True, "", p
    if tipo == "inferencia":
        if n < 3:
            return False, "n demasiado pequeno para inferencia", {}
        return True, "", p
    return False, "tipo no soportado", {}

def flops_tarea(tipo: str, params: dict) -> float:
    """FLOPs de una tarea, con la MISMA formula que el Core (`_flops_tarea`).

    Se manda en la telemetria para que el panel pueda dibujar la potencia de
    cada GPU con lo que esa GPU acaba de hacer de verdad: FLOPs de la tarea /
    milisegundos cronometrados en la propia maquina. Si la formula cambia en el
    Core, cambia aqui: son la misma cuenta.
    """
    try:
        n = float((params or {}).get("n") or 0.0)
        if tipo == "matriz":
            return 2.0 * n ** 3
        if tipo == "inferencia":
            return 18.0 * n ** 2
        if tipo == "difusion_calor":
            return 10.0 * n ** 2 * float((params or {}).get("pasos") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return 0.0


def trabajo_local(tipo: str, params: dict, device: str = "cuda:0") -> str:
    """Calcula la tarea EXACTAMENTE como el Core (determinista)."""
    if tipo == "matriz":
        import torch
        n = int(params.get("n", 256))
        seed = int(params.get("seed", 42))
        idx = torch.arange(n, device=device, dtype=torch.float64)
        a = ((idx[:, None] * 31 + idx[None, :] * 17 + seed) % 97) - 48
        b = ((idx[:, None] * 31 + idx[None, :] * 17 + (seed + 1)) % 97) - 48
        c = float((a @ b).sum().item())
        return str(int(round(c)))
    if tipo == "checksum":
        data = str(params.get("data", "kukia"))
        return hashlib.sha256(data.encode()).hexdigest()
    if tipo == "inferencia":
        import torch
        n = int(params.get("n", 64))
        seed = int(params.get("seed", 7))
        idx = torch.arange(n, device=device, dtype=torch.float64)
        x = ((idx[:, None] * 31 + idx[None, :] * 17 + seed) % 97) - 48
        k = torch.tensor([[1.0, 2.0, 1.0], [2.0, 4.0, 1.0 * 2], [1.0, 2.0, 1.0]],
                         device=device, dtype=torch.float64)
        out = 0.0
        for i in range(3):
            for j in range(3):
                out += float((x[i:n-2+i, j:n-2+j] * k[i, j]).sum().item())
        return str(int(round(out)))
    if tipo == "difusion_calor":
        # AUDITORIA: el tipo que faltaba. La rejilla inicial tiene que ser
        # IDENTICA a la del Core (numpy default_rng), asi que se genera con
        # numpy en CPU y se pasa a la GPU para iterar en float64.
        import numpy as np
        import torch
        n = int(params.get("n", 256))
        pasos = int(params.get("pasos", 200))
        seed = int(params.get("seed", 42))
        rejilla = np.random.default_rng(seed).random((n, n))
        x = torch.from_numpy(rejilla).to(device=device, dtype=torch.float64)
        for _ in range(pasos):
            x[1:-1, 1:-1] = x[1:-1, 1:-1] + 0.2 * (
                x[:-2, 1:-1] + x[2:, 1:-1] + x[1:-1, :-2] + x[1:-1, 2:] - 4 * x[1:-1, 1:-1])
        return repr(float(x.sum().item()))
    return "tarea_no_soportada"

def _codigo(r):
    return r.status_code if r is not None else 0

# AUDITORIA 2026-10-19: con 2 GPUs los dos hilos creaban la MISMA billetera a la
# vez (mismo nombre) y el segundo fichero pisaba al primero. Un lock evita que
# se generen dos claves distintas para el mismo nodo.
_wallet_lock = threading.Lock()

def _guardar_recuperacion(w: dict, pwd: str) -> None:
    """Deja la recuperacion en un fichero al lado de la billetera.

    La consola se llena, se cierra o el proceso muere antes de que el minero
    apunte nada, y entonces la billetera nueva ya no se puede recuperar en otro
    PC (la contrasena se genera al azar y no se guardaba). El .json de la
    billetera ya lleva la clave privada en claro, asi que este fichero no
    anade un riesgo que no existiera: solo evita perder la recuperacion.
    """
    try:
        ruta = Path(WALLET.WALLET_DIR) / f"RECUPERACION_{w.get('nombre', 'minero')}.txt"
        ruta.write_text(
            "KUKIA - recuperacion de la billetera del minero\n"
            "=================================================\n"
            f"nombre:      {w.get('nombre')}\n"
            f"direccion:   {w.get('direccion')}\n"
            f"contrasena:  {pwd}\n"
            f"20 palabras: {' '.join(w.get('mnemonico') or [])}\n\n"
            "Con la contrasena + las 20 palabras se recupera ESTA MISMA billetera\n"
            "en otro PC (ledger/kukia_wallet.py -> recuperar_billetera).\n"
            "Guardalo en un sitio seguro y no lo compartas: quien lo tenga puede\n"
            "firmar transferencias con tu clave.\n",
            encoding="utf-8")
        _proteger(ruta)
        print(f"[KUKIA] recuperacion guardada en {ruta}", flush=True)
    except Exception as e:
        print(f"[KUKIA] no pude guardar la recuperacion: {e}", flush=True)

def _anunciar_billetera_nueva(w: dict, pwd: str) -> None:
    """Avisa de la billetera nueva SIN dejar secretos escritos en los logs.

    AUDITORIA: ARRANCAR_MINERO.cmd manda la salida a worker/minero_ok.out.log,
    asi que imprimir la contrasena y las 20 palabras las dejaba en disco para
    siempre (y ese log se copia/pega facilmente). Se guardan en el fichero de
    recuperacion y solo se muestran por pantalla si hay una consola de verdad
    (isatty); si la salida va a un fichero, se imprime solo la ruta.
    """
    _guardar_recuperacion(w, pwd)
    print("=" * 64, flush=True)
    print("[KUKIA] BILLETERA NUEVA - APUNTA ESTO ANTES DE SEGUIR", flush=True)
    print(f"[KUKIA]   direccion:  {w.get('direccion')}", flush=True)
    if sys.stdout.isatty():
        print(f"[KUKIA]   contrasena: {pwd}", flush=True)
        print(f"[KUKIA]   20 palabras: {' '.join(w.get('mnemonico') or [])}", flush=True)
    else:
        print("[KUKIA]   contrasena y 20 palabras: NO se escriben en el log.", flush=True)
        print("[KUKIA]   estan en el fichero RECUPERACION_*.txt indicado arriba.", flush=True)
    print("[KUKIA] Sin contrasena + palabras NO se recupera el saldo en otro PC.", flush=True)
    print("=" * 64, flush=True)

# ============================================================
#  Pipeline de minado (21-10-2026)
# ============================================================
_registro_lock = threading.Lock()


def _re_registrar(ctx: dict, etiqueta: str, device: str, vram_gb: int, tops: float,
                  pubkey: str, direccion: str, privkey: str) -> bool:
    """Registra el nodo otra vez cuando el Core ya no lo conoce.

    Antes, un 404 "nodo no registrado" dejaba al minero pidiendo trabajo cada
    10 s PARA SIEMPRE (paso 233 veces en el log): la GPU seguia encendida y no
    cobraba nada hasta reiniciar el proceso a mano. Ahora se vuelve a registrar
    con la misma clave (la billetera no cambia) y sigue minando.
    """
    with _registro_lock:
        try:
            nuevo = _registrar_o_reusar(etiqueta, device, vram_gb, tops,
                                        pubkey, direccion, privkey)
        except Exception as e:
            print(f"[KUKIA] {etiqueta} no pude volver a registrar el nodo: {e}", flush=True)
            return False
        if nuevo != ctx.get("nid"):
            print(f"[KUKIA] {etiqueta} el Core no reconocia el nodo {ctx.get('nid')}; "
                  f"registrado de nuevo como {nuevo}", flush=True)
            ctx["nid"] = nuevo
            _asegurar_cobro(nuevo, etiqueta, direccion, privkey)
            _avisar_reinicio(nuevo, etiqueta, privkey)
        return True


def _hilo_pedir(ctx: dict, etiqueta: str, device: str, vram_gb: int, tops: float,
                pubkey: str, direccion: str, privkey: str, cola, alto) -> None:
    """Mantiene la cola de trabajo llena: la GPU nunca espera a que llegue."""
    espera = 5.0
    while not alto.is_set():
        try:
            if cola.full():
                time.sleep(0.02)
                continue
            nid = ctx["nid"]
            ts = int(time.time())
            nonce = secrets.token_hex(16)
            firma = WALLET.firmar(f"pendiente:{nid}:{ts}:{nonce}", privkey) if privkey else ""
            params = {"nodo_id": nid, "ts": ts, "nonce": nonce, "firma": firma}
            if ctx.get("solo_primaria"):
                params["solo_primaria"] = "1"
            rp = _get(f"{CORE}/tareas/pendiente", params=params, timeout=TIMEOUT_API)
            if rp.status_code == 200:
                cuerpo = rp.json() or {}
                espera = 5.0
                if cuerpo.get("tarea"):
                    cola.put(cuerpo)
                else:
                    # Cola vacia o cupo del nodo lleno: se reintenta en seguida,
                    # no en 3 s como antes (eso era tiempo perdido por tarea).
                    time.sleep(0.2)
                continue
            detalle = (rp.text or "")[:160]
            if rp.status_code in (401, 404) and "nodo no registrado" in detalle:
                print(f"[KUKIA] {etiqueta} el Core ya no tiene el nodo; lo registro otra vez",
                      flush=True)
                if _re_registrar(ctx, etiqueta, device, vram_gb, tops,
                                 pubkey, direccion, privkey):
                    continue
                time.sleep(15)
                continue
            if rp.status_code == 429:
                print(f"[KUKIA] {etiqueta} limitado por el Core (429): {detalle}", flush=True)
                time.sleep(20)
                continue
            print(f"[KUKIA] {etiqueta} el Core rechaza la peticion de trabajo: "
                  f"{rp.status_code} {detalle}", flush=True)
            if rp.status_code == 401 and not ctx.get("avisado_firma"):
                print("[KUKIA] si el Core es anterior al 20-10-2026, actualizalo: "
                      "el worker nuevo firma el trabajo con nonce.", flush=True)
                ctx["avisado_firma"] = True
            time.sleep(espera)
            espera = min(60.0, espera * 1.6)
        except Exception as e:
            print(f"[KUKIA] {etiqueta} pidiendo trabajo: {e} (reintento en {espera:.0f} s)",
                  flush=True)
            time.sleep(espera)
            espera = min(60.0, espera * 1.6)


def _hilo_entregar(ctx: dict, etiqueta: str, device: str, privkey: str, cola,
                   cuenta: dict, cuenta_lock, alto) -> None:
    """Entrega los resultados mientras la GPU ya calcula el siguiente."""
    while not alto.is_set():
        try:
            trabajo = cola.get(timeout=5)
        except queue.Empty:
            continue
        pid, tipo, metrica, duracion_ms, es_replica, telemetria = trabajo
        hecho = False
        for intento in (1, 2):
            nid = ctx["nid"]
            firma = WALLET.firmar(f"{pid}:{metrica}", privkey) if privkey else ""
            try:
                if es_replica:
                    r = _post(f"{CORE}/tareas/replica",
                              json={"tid": pid, "nodo_id": nid, "metrica": metrica,
                                    "firma": firma, "device": str(device),
                                    "duracion_ms": duracion_ms},
                              timeout=TIMEOUT_ENTREGA)
                else:
                    entrega = {"tid": pid, "nodo_id": nid, "metrica": metrica,
                               "firma": firma, "device": str(device),
                               "duracion_ms": duracion_ms, "telemetria": telemetria}
                    r = _post(f"{CORE}/tareas/resultado_json", json=entrega,
                              timeout=TIMEOUT_ENTREGA)
                    if r.status_code == 404 and "Not Found" in (r.text or ""):
                        r = _post(f"{CORE}/tareas/resultado", params=entrega,
                                  timeout=TIMEOUT_ENTREGA)
            except requests.exceptions.RequestException as e:
                # Un timeout NO significa que el Core no lo haya cobrado. Se
                # reintenta UNA vez: si ya estaba pagada, el segundo intento
                # devuelve 409 y no hay doble pago.
                if intento == 1:
                    print(f"[KUKIA] {etiqueta} entrega de {pid[:8]} sin respuesta "
                          f"({type(e).__name__}); reintento", flush=True)
                    continue
                print(f"[KUKIA] {etiqueta} entrega de {pid[:8]} perdida: {e}", flush=True)
                with cuenta_lock:
                    cuenta["err"] += 1
                hecho = True
                break
            if es_replica:
                if r.status_code == 200:
                    print(f"[KUKIA] {etiqueta} replica {pid[:8]} {tipo} verificada "
                          f"({duracion_ms:.0f} ms)", flush=True)
                elif r.status_code == 404:
                    # El Core no tiene la ruta de replicas. Se deja de pedirlas
                    # (solo_primaria): si no, cada oferta ocupa un turno del
                    # nodo hasta que caduca y el minero se queda sin trabajo.
                    ctx["solo_primaria"] = True
                    print(f"[KUKIA] {etiqueta} el Core no admite replicas: pido solo "
                          f"trabajo propio", flush=True)
                elif r.status_code == 409:
                    print(f"[KUKIA] {etiqueta} replica {pid[:8]} llego tarde "
                          f"(la tarea ya estaba pagada)", flush=True)
                else:
                    print(f"[KUKIA] {etiqueta} replica {pid[:8]} rechazada: "
                          f"{r.status_code} {(r.text or '')[:120]}", flush=True)
                    with cuenta_lock:
                        cuenta["err"] += 1
                hecho = True
                break
            if r.status_code == 200:
                res = r.json() or {}
                if res.get("ok"):
                    kuki = float(res.get("kuki") or 0.0)
                    with cuenta_lock:
                        cuenta["ok"] += 1
                        cuenta["kuki"] += kuki
                    print(f"[KUKIA] {etiqueta} tarea {pid[:8]} {tipo} -> OK +{res.get('kuki')} KIA "
                          f"({duracion_ms:.0f} ms)", flush=True)
                else:
                    print(f"[KUKIA] {etiqueta} tarea {pid[:8]} {tipo} -> RECHAZADO "
                          f"(esperado {res.get('esperado')}, enviado {metrica[:40]})", flush=True)
                    with cuenta_lock:
                        cuenta["err"] += 1
            elif r.status_code == 409:
                print(f"[KUKIA] {etiqueta} tarea {pid[:8]} ya cerrada (409): "
                      f"{(r.text or '')[:100]}", flush=True)
            elif r.status_code == 503:
                # El Core no tenia hueco para verificar: la tarea vuelve a la cola.
                print(f"[KUKIA] {etiqueta} Core ocupado verificando; {pid[:8]} vuelve a la cola",
                      flush=True)
            elif r.status_code == 429:
                print(f"[KUKIA] {etiqueta} Core saturado (429) al entregar {pid[:8]}", flush=True)
                with cuenta_lock:
                    cuenta["err"] += 1
            else:
                print(f"[KUKIA] {etiqueta} entrega rechazada: {r.status_code} "
                      f"{(r.text or '')[:140]}", flush=True)
                with cuenta_lock:
                    cuenta["err"] += 1
            hecho = True
            break
        if not hecho:
            with cuenta_lock:
                cuenta["err"] += 1


def _hilo_resumen(etiqueta: str, cuenta: dict, cuenta_lock) -> None:
    """Una linea por minuto: asi se ve si el minero rinde o se ha atascado."""
    ultimo = time.time()
    ult_ok, ult_kuki, ult_err = 0, 0.0, 0
    while True:
        time.sleep(RESUMEN_SEG)
        with cuenta_lock:
            ok, kuki, err = cuenta["ok"], cuenta["kuki"], cuenta["err"]
        dt = max(0.001, time.time() - ultimo)
        print(f"[KUKIA] {etiqueta} resumen: {(ok - ult_ok) / dt * 60:.0f} tareas/min, "
              f"{(kuki - ult_kuki) / dt * 60:.3f} KIA/min, {err - ult_err} fallos, "
              f"total {ok} tareas / {kuki:.3f} KIA", flush=True)
        ultimo = time.time()
        ult_ok, ult_kuki, ult_err = ok, kuki, err


# ============================================================
#  Vigilancia y resumen (21-10-2026)
# ============================================================
_registro_lock = threading.Lock()
RESUMEN_SEG = max(10.0, float(os.environ.get("KUKIA_RESUMEN_SEG", "60")))


def _re_registrar(nid: str, etiqueta: str, device: str, vram_gb: int, tops: float,
                  pubkey: str, direccion: str, privkey: str):
    """Devuelve un id de nodo que el Core conozca (el mismo si sigue vivo).

    El 404 del Core es casi siempre transitorio: medio segundo despues el mismo
    id vuelve a existir. Se comprueba varias veces antes de registrar otro,
    porque un nodo nuevo arranca con cupo 1 y sin historial, y cada re-registro
    dejaba tareas colgadas a nombre del viejo.
    """
    with _registro_lock:
        for espera in (0.0, 0.3, 0.8, 1.5):
            if espera:
                time.sleep(espera)
            try:
                r = _get(f"{CORE}/nodos/{nid}", timeout=TIMEOUT_API) if nid else None
                if r is not None and r.status_code == 200 and (r.json() or {}).get("id") == nid:
                    return nid
            except Exception:
                pass
        try:
            nuevo = _registrar_o_reusar(etiqueta, device, vram_gb, tops,
                                        pubkey, direccion, privkey)
        except Exception as e:
            print(f"[KUKIA] {etiqueta} no pude registrar el nodo: {e}", flush=True)
            return nid
        if nuevo != nid:
            print(f"[KUKIA] {etiqueta} el Core no conocia el nodo {nid}: ahora es {nuevo}",
                  flush=True)
            _asegurar_cobro(nuevo, etiqueta, direccion, privkey)
            _avisar_reinicio(nuevo, etiqueta, privkey)
            if nid:
                _avisar_reinicio(nid, etiqueta, privkey)
        return nuevo


def _hilo_resumen(etiqueta: str, cuenta: dict, cuenta_lock) -> None:
    """Una linea por minuto: asi se ve si el minero rinde o se ha atascado."""
    ultimo = time.time()
    ult_ok, ult_kuki, ult_err = 0, 0.0, 0
    while True:
        time.sleep(RESUMEN_SEG)
        with cuenta_lock:
            ok, kuki, err = cuenta["ok"], cuenta["kuki"], cuenta["err"]
        dt = max(0.001, time.time() - ultimo)
        print(f"[KUKIA] {etiqueta} resumen: {(ok - ult_ok) / dt * 60:.0f} tareas/min, "
              f"{(kuki - ult_kuki) / dt * 60:.3f} KIA/min, {err - ult_err} fallos, "
              f"total {ok} tareas / {kuki:.3f} KIA", flush=True)
        ultimo = time.time()
        ult_ok, ult_kuki, ult_err = ok, kuki, err


def _hilo_vigia(etiqueta: str, ctx: dict, privkey: str, cuenta: dict, cuenta_lock) -> None:
    """Suelta las tareas colgadas si el nodo se queda a tope y sin cobrar.

    FALLO REAL (21-10-2026, el mismo que documenta el Core en
    /nodos/{id}/reinicio): las tareas que quedan 'ejecutando' a nombre del nodo
    le ocupan el cupo (4) y el Core contesta "limite de tareas simultaneas"
    durante ABANDONO_SEG (30 min) aunque la GPU este lista. Medido: 8 tareas
    colgadas y cero pagos. Si a los 2 min sigue igual, se pide el reinicio
    (firmado) del propio nodo y las tareas vuelven a la cola en segundos.
    """
    ultimo_release = 0.0
    while True:
        time.sleep(30)
        with cuenta_lock:
            sin_cobrar = time.time() - float(cuenta["ultimo_ok"] or 0.0)
        if sin_cobrar <= 120:
            ultimo_release = 0.0
            continue
        tope_reciente = time.time() - float(cuenta.get("ultimo_limite") or 0.0) < 40
        if tope_reciente and time.time() - ultimo_release > 60:
            print(f"[KUKIA] {etiqueta} {sin_cobrar:.0f} s sin cobrar con el cupo lleno: "
                  f"suelto las tareas colgadas de {ctx['nid']}", flush=True)
            _avisar_reinicio(ctx["nid"], etiqueta, privkey)
            ultimo_release = time.time()
        if sin_cobrar > 300 and time.time() - float(cuenta.get("ultimo_core_ok") or 0.0) < 60:
            print(f"[KUKIA] {etiqueta} {sin_cobrar:.0f} s sin cobrar con el Core en pie: "
                  f"reinicio el minero para que arranque limpio", flush=True)
            os._exit(3)


def ciclo_worker(nombre: str, device: str, vram_gb: int, tops: float):
    """Un hilo por GPU: registra su nodo y mina en bucle (resultados FIRMADOS)."""
    etiqueta = f"{nombre}[{device}]"
    # Billetera propia para firmar envios (nadie puede suplantar este nodo)
    try:
        with _wallet_lock:
            res = WALLET.crear_billetera("worker_" + nombre.replace("[", "_")
                                         .replace("]", "_").replace(":", ""))
    except Exception as e:
        # Billetera cifrada que no se puede descifrar en esta cuenta de Windows
        # (por ejemplo, copiada de otro PC). Sin clave no se puede firmar: se
        # avisa en claro y se espera; no se martillea el Core con un nodo mudo.
        while True:
            print(f"[KUKIA] {etiqueta} la billetera no se puede abrir: {e}", flush=True)
            print("[KUKIA] recuperala con la contrasena y las 20 palabras "
                  "(RECUPERACION_*.txt) antes de minar en este PC.", flush=True)
            time.sleep(300)
    if isinstance(res, tuple):
        # Billetera recien creada. La contrasena se genera al azar y no se
        # guardaba en ningun sitio: quien minaba desde fuera se quedaba sin
        # recuperacion y no lo sabia hasta perder el PC. Se ensena UNA vez.
        w, pwd_nueva = res
        _anunciar_billetera_nueva(w, pwd_nueva)
    else:
        w = res
    pubkey = w.get("clave_publica", "")
    privkey = w.get("clave_privada", "")
    direccion = w.get("direccion", "")
    # AUDITORIA 2026-10-20: antes, si el Core estaba arrancando (o caido) al
    # iniciar el minero, el hilo hacia 'return' y esa GPU se quedaba muerta
    # para siempre. Ahora reintenta hasta que el Core responda.
    while True:
        try:
            nid = _registrar_o_reusar(etiqueta, device, vram_gb, tops, pubkey, direccion, privkey)
            break
        except Exception as e:
            print(f"[KUKIA] {etiqueta} Core no disponible en el registro ({e}); "
                  f"reintento en 15 s", flush=True)
            time.sleep(15)
    _asegurar_cobro(nid, etiqueta, direccion, privkey)
    _avisar_reinicio(nid, etiqueta, privkey)
    barrer_saldos(etiqueta, w, privkey, forzar=True)
    estado_tarea = {"ms": 0.0}
    if privkey and str(device).startswith("cuda"):
        threading.Thread(target=_bucle_telemetria,
                         args=(nid, device, vram_gb, tops, privkey, estado_tarea),
                         daemon=True).start()
    # El id puede cambiar si el Core olvida el nodo; todo lo que va por detras
    # (vigia) lee la misma caja, no una copia.
    ctx = {"nid": nid}
    cuenta = {"ok": 0, "kuki": 0.0, "err": 0, "ultimo_ok": time.time(),
              "ultimo_limite": 0.0, "ultimo_core_ok": time.time()}
    cuenta_lock = threading.Lock()
    threading.Thread(target=_hilo_resumen,
                     args=(etiqueta, cuenta, cuenta_lock), daemon=True).start()
    threading.Thread(target=_hilo_vigia,
                     args=(etiqueta, ctx, privkey, cuenta, cuenta_lock), daemon=True).start()
    bloqueado_hasta = 0.0
    espera_error = 5.0   # backoff exponencial (5 s -> 60 s)
    while True:
        try:
            ahora = time.time()
            nid = ctx["nid"]
            # Barrido periodico (throttled a BARRIDO_SEG): si el Core paga
            # directo a la direccion no hace nada; si quedo saldo viejo en
            # algun nodo, lo mueve a la billetera.
            barrer_saldos(etiqueta, w, privkey)
            if ahora < bloqueado_hasta:
                time.sleep(min(5.0, bloqueado_hasta - ahora))
                continue
            # AUDITORIA 2026-10-20: peticion FIRMADA con nonce aleatorio.
            # Sin nonce, una peticion capturada se podia repetir 5 minutos y
            # robar la cola del nodo. El Core nuevo exige la firma con nonce;
            # hay que actualizar Core y worker a la vez (los mineros viejos
            # siguen funcionando porque mandan el mensaje antiguo sin nonce).
            ts = int(time.time())
            nonce = secrets.token_hex(16)
            firma_p = WALLET.firmar(f"pendiente:{nid}:{ts}:{nonce}", privkey) if privkey else ""
            rp = _get(f"{CORE}/tareas/pendiente",
                      params={"nodo_id": nid, "ts": ts, "nonce": nonce, "firma": firma_p},
                      timeout=TIMEOUT_API)
            if rp.status_code == 429:
                print(f"[KUKIA] {etiqueta} limitado por el Core (429), espero 20 s", flush=True)
                bloqueado_hasta = time.time() + 20
                continue
            if rp.status_code != 200:
                detalle = (rp.text or "")[:160]
                if rp.status_code in (401, 404) and "nodo no registrado" in detalle:
                    print(f"[KUKIA] {etiqueta} el Core no ve el nodo {nid}: lo compruebo "
                          f"antes de seguir", flush=True)
                    ctx["nid"] = nid = _re_registrar(nid, etiqueta, device, vram_gb, tops,
                                                      pubkey, direccion, privkey)
                    cuenta["ultimo_core_ok"] = time.time()
                    continue
                print(f"[KUKIA] {etiqueta} el Core rechaza la peticion de trabajo: "
                      f"{rp.status_code} {detalle}", flush=True)
                if rp.status_code == 401:
                    print("[KUKIA] si el Core es anterior al 20-10-2026, actualizalo: "
                          "el worker nuevo firma el trabajo con nonce.", flush=True)
                time.sleep(10)
                continue
            _cuerpo = rp.json()
            cuenta["ultimo_core_ok"] = time.time()
            pend = _cuerpo.get("tarea")
            # EJECUCION REDUNDANTE (21-10-2026): el Core puede dar una tarea
            # que otro nodo ya esta resolviendo. Verificar tambien se cobra
            # (30 % del premio de esa tarea) y no hace falta recalcular nada
            # en el Core, que era el cuello de botella de la red.
            es_replica = bool(_cuerpo.get("replica"))
            if not pend:
                motivo = str(_cuerpo.get("motivo") or "")
                if "limite" in motivo:
                    # El cupo del nodo esta lleno (tareas colgadas de antes): el
                    # vigia lo suelta, aqui solo se espera sin martillear.
                    cuenta["ultimo_limite"] = time.time()
                    time.sleep(1.0)
                else:
                    time.sleep(0.5)
                continue
            pid = str(pend.get("id") or "")
            tipo = pend.get("tipo")
            ok_t, motivo_t, params = validar_tarea(tipo, pend.get("params"))
            if not pid or not ok_t:
                print(f"[KUKIA] {etiqueta} tarea {pid[:8] or '?'} rechazada localmente: "
                      f"{motivo_t or 'id vacio'}", flush=True)
                time.sleep(3)
                continue
            t0 = time.time()
            metrica = trabajo_local(tipo, params, device)
            duracion_ms = (time.time() - t0) * 1000.0
            estado_tarea["ms"] = round(duracion_ms, 1)
            if metrica == "tarea_no_soportada":
                print(f"[KUKIA] {etiqueta} tipo '{tipo}' no implementado en este worker", flush=True)
                time.sleep(5)
                continue
            firma = WALLET.firmar(f"{pid}:{metrica}", privkey) if privkey else ""
            # Telemetria REAL del nodo: tiempo de la tarea, FLOPs que acaba de
            # hacer y GFLOPS medidos en ESTA tarea. Con eso el panel dibuja la
            # potencia y la latencia de cada GPU con datos, no con una media
            # declarada. No influye en el pago.
            _fl = flops_tarea(tipo, params)
            _ms = max(0.001, float(duracion_ms))
            _vram_usada = 0.0
            try:
                if str(device).startswith("cuda"):
                    import torch
                    _vram_usada = round(torch.cuda.memory_reserved(device) / (1024 * 1024), 1)
            except Exception:
                _vram_usada = 0.0
            telemetria = json.dumps({"device": str(device), "vram_gb": vram_gb,
                                     "tops_declarados": tops,
                                     "gflops_base": _GFLOPS_BASE.get(str(device), 0.0),
                                     "tarea_ms": round(duracion_ms, 1),
                                     "tipo": str(tipo),
                                     "flops": round(_fl, 1),
                                     "gflops_tarea": round(_fl / (_ms / 1000.0) / 1e9, 3),
                                     "vram_usada_mb": _vram_usada},
                                    separators=(",", ":"))
            if es_replica:
                # Entrega de verificacion: no paga ahora; el Core la guarda y
                # la cruza con el primario. La firma es la misma.
                try:
                    rv = _post(f"{CORE}/tareas/replica",
                               json={"tid": pid, "nodo_id": nid, "metrica": metrica,
                                     "firma": firma, "device": str(device),
                                     "duracion_ms": duracion_ms},
                               timeout=TIMEOUT_ENTREGA)
                    if rv.status_code == 200:
                        print(f"[KUKIA] {etiqueta} replica {pid[:8]} {tipo} verificada "
                              f"({duracion_ms:.0f} ms)", flush=True)
                    elif rv.status_code == 404:
                        det = (rv.text or "")[:100]
                        if "Not Found" in det:
                            print(f"[KUKIA] {etiqueta} el Core no admite replicas "
                                  f"(actualiza el Core); sigo", flush=True)
                        else:
                            # Normal: la tarea se cerro o la replica ya no estaba
                            # reclamada. No es un fallo del minero.
                            print(f"[KUKIA] {etiqueta} replica {pid[:8]} sin reclamar: {det}",
                                  flush=True)
                    elif rv.status_code == 409:
                        # La tarea se pago antes de que llegara la verificacion:
                        # el Core ya la cerro y esta replica no se cobra. Es
                        # normal en tareas cortas; solo se avisa.
                        print(f"[KUKIA] {etiqueta} replica {pid[:8]} llego tarde "
                              f"(la tarea ya estaba pagada)", flush=True)
                    espera_error = 5.0
                except Exception as e:
                    print(f"[KUKIA] {etiqueta} replica {pid[:8]} no entregada: {e}", flush=True)
                continue
            entrega = {"tid": pid, "nodo_id": nid, "metrica": metrica,
                       "firma": firma, "device": str(device),
                       "duracion_ms": duracion_ms, "telemetria": telemetria}
            rr = None
            for _intento in (1, 2):
                try:
                    # AUDITORIA 2026-10-20: cuerpo JSON (sin limite de URL). Si el
                    # Core es antiguo y la ruta no existe, se usa la query string.
                    rr = _post(f"{CORE}/tareas/resultado_json", json=entrega,
                               timeout=TIMEOUT_ENTREGA)
                    if rr.status_code == 404 and "Not Found" in (rr.text or ""):
                        rr = _post(f"{CORE}/tareas/resultado", params=entrega,
                                   timeout=TIMEOUT_ENTREGA)
                except requests.exceptions.RequestException as e:
                    # Un timeout no significa que no se haya cobrado: se
                    # reintenta UNA vez. Si ya estaba pagada, el segundo intento
                    # devuelve 409 y no hay doble pago.
                    if _intento == 1:
                        print(f"[KUKIA] {etiqueta} entrega de {pid[:8]} sin respuesta "
                              f"({type(e).__name__}); reintento", flush=True)
                        continue
                    print(f"[KUKIA] {etiqueta} entrega de {pid[:8]} perdida: {e}; "
                          f"la tarea volvera a la cola", flush=True)
                    rr = None
                    break
                # El Core ha perdido la fila del nodo un instante (404/401
                # "nodo"): se comprueba/registra otra vez y se reintenta. Es la
                # unica forma de no perder un trabajo ya calculado.
                if (_intento == 1 and rr.status_code in (401, 404)
                        and "nodo" in (rr.text or "")):
                    print(f"[KUKIA] {etiqueta} el Core no ve el nodo {nid} al entregar "
                          f"{pid[:8]}; lo compruebo y reintento", flush=True)
                    nuevo = _re_registrar(nid, etiqueta, device, vram_gb, tops,
                                          pubkey, direccion, privkey)
                    if nuevo and nuevo != nid:
                        ctx["nid"] = nid = nuevo
                        entrega["nodo_id"] = nid
                        entrega["firma"] = WALLET.firmar(f"{pid}:{metrica}", privkey)
                    continue
                break
            if rr is None:
                espera_error = 5.0
                continue
            if rr.status_code == 429:
                print(f"[KUKIA] {etiqueta} Core saturado (429) al entregar, espero 20 s", flush=True)
                bloqueado_hasta = time.time() + 20
                continue
            if rr.status_code != 200:
                print(f"[KUKIA] {etiqueta} entrega rechazada: {rr.status_code} {rr.text[:140]}", flush=True)
                if rr.status_code in (429, 403):
                    bloqueado_hasta = time.time() + 30
                continue
            res = rr.json()
            if res.get("ok"):
                with cuenta_lock:
                    cuenta["ok"] += 1
                    cuenta["kuki"] += float(res.get("kuki") or 0.0)
                    cuenta["ultimo_ok"] = time.time()
                print(f"[KUKIA] {etiqueta} tarea {pid[:8]} {tipo} -> OK +{res.get('kuki')} KIA "
                      f"({duracion_ms:.0f} ms)", flush=True)
                espera_error = 5.0
            else:
                with cuenta_lock:
                    cuenta["err"] += 1
                print(f"[KUKIA] {etiqueta} tarea {pid[:8]} {tipo} -> RECHAZADO "
                      f"(esperado {res.get('esperado')}, enviado {metrica[:40]})", flush=True)
                espera_error = 5.0
        except Exception as e:
            # Backoff: un Core caido no debe hacer que 2 GPUs lo martilleen
            # en bucle ni llenar el log a 1000 lineas por minuto.
            print(f"[KUKIA] {etiqueta} err: {e} (reintento en {espera_error:.0f} s)", flush=True)
            time.sleep(espera_error)
            espera_error = min(60.0, espera_error * 1.6)

def _vigilar_direccion(seg: float = 300.0) -> None:
    """Si el Core se muda (tunel nuevo), el minero se entera sin reiniciar.

    Solo hace falta con la indireccion ("auto ..."): con una URL fija no hay
    nada que vigilar. Se reescribe el global CORE, que es lo que leen los
    f-strings de todos los hilos, asi que el cambio es inmediato y no corta
    el trabajo que se esta calculando.
    """
    if _DESTINO is None or not _DESTINO.es_indirecta(CORE_CRUDO):
        return

    def bucle():
        global CORE
        while True:
            time.sleep(seg)
            try:
                nuevo = _DESTINO.resolver(CORE_CRUDO, ttl=0)
            except Exception:
                continue
            if nuevo and nuevo != CORE:
                print(f"[KUKIA] el Core se movio: {CORE} -> {nuevo}", flush=True)
                CORE = nuevo

    threading.Thread(target=bucle, daemon=True, name="direccion-core").start()


def main():
    # AUDITORIA 2026-10-19: la lista de palabras es la BIP39 en espanol y lleva
    # tildes. En Windows la consola es cp1252 y print() reventaba con
    # UnicodeEncodeError justo despues de crear la billetera: el minero moria
    # en el primer arranque. UTF-8 + errores sustituidos = nunca peta.
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    _vigilar_direccion()
    ok_core, motivo_core = _core_seguro(CORE)
    if not ok_core:
        print(f"[KUKIA] ERROR: {motivo_core}", flush=True)
        return 2
    nombre = sys.argv[1] if len(sys.argv) > 1 else "worker_" + socket.gethostname()
    try:
        import torch
        n_gpus = torch.cuda.device_count()
    except Exception:
        n_gpus = 0

    if n_gpus == 0:
        print("[KUKIA] Sin GPUs CUDA - usando CPU (mas lento)", flush=True)
        _benchmark_arranque()
        ciclo_worker(nombre, "cpu", 0, 0.0)
        return

    print(f"[KUKIA] {n_gpus} GPU(s) detectada(s) - minando en TODAS a la vez", flush=True)
    _benchmark_arranque()
    hilos = []
    for g in range(n_gpus):
        try:
            props = torch.cuda.get_device_properties(g)
            # AUDITORIA 2026-10-19: era // (division entera), asi que una GPU
            # de 12 GB declaraba 11 y se quedaba en el tier de 100 TOPS. Hay que
            # redondear para que case con la clase real de la GPU.
            vram = int(round(props.total_memory / (1024**3)))
            # TOPS aproximados por clase de GPU (referencia NVIDIA)
            tops_est = {8: 60.0, 12: 300.0, 16: 760.0, 24: 1300.0}.get(int(vram), 100.0)
            print(f"[KUKIA] GPU{g}: {props.name} ({vram} GB)", flush=True)
        except Exception:
            vram, tops_est = 0, 0.0
        t = threading.Thread(target=ciclo_worker, args=(nombre, f"cuda:{g}", vram, tops_est), daemon=True)
        t.start()
        hilos.append(t)
    for t in hilos:
        t.join()

if __name__ == "__main__":
    raise SystemExit(main())
