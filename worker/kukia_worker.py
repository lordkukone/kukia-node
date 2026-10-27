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
from datetime import date, datetime
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
# --- CPU como minero mas (23-10-2026) --------------------------------------
# Hasta hoy, con GPU presente la CPU no minaba nunca: el worker abria un hilo
# por GPU y punto. KUKIA_USAR_CPU anade la CPU como un nodo mas, A LA VEZ que
# las GPU.
#
# Medido en este PC el 23-10-2026 (tools/medir_potencia.py, tareas de la red):
#   i9-10900K  matriz 149 GFLOP/s (88 % de la 5060 Ti) | difusion 1,5 (44x menos)
#   RTX 5070   matriz 234           | difusion 82,7
# Por eso viene APAGADO: en un PC con GPU, la CPU coge difusiones que la GPU
# haria en 0,24 s y las arrastra 13 s, y encima se come nucleos del Core si
# vive en esta misma maquina. Donde si suma: maquinas sin GPU, o con la GPU
# ya saturada y CPU libre.
# Valores: 0/no (apagado, defecto) | 1/si/auto = mitad de los nucleos (entre 2
# y 4 hilos) | un numero = esos hilos exactos.
def _ajuste_cpu() -> tuple:
    crudo = (os.environ.get("KUKIA_USAR_CPU", "0") or "0").strip().lower()
    if crudo in ("0", "no", "false", "off", ""):
        return False, 0
    nucleos = os.cpu_count() or 2
    # "1" es el interruptor (como 1/si/auto), no "un hilo": si no, el uso
    # normal (KUKIA_USAR_CPU=1) dejaria la CPU a un solo hilo. Para pedir un
    # numero concreto de hilos se pone 2 o mas.
    if crudo.isdigit() and int(crudo) > 1:
        return True, max(1, min(nucleos, int(crudo)))
    return True, max(2, min(4, nucleos // 2))

# Cuantas ENTREGAS pueden estar en vuelo a la vez (23-10-2026): con 1 el
# dispositivo se queda esperando la respuesta del Core antes de pedir la
# siguiente tarea; con 2 calcula mientras la anterior se entrega. El Core
# acepta 4 tareas en vuelo por nodo (1 calculando + hasta 2 entregando), asi
# que 2 es el maximo seguro.
# Tareas que el hilo pedidor deja preparadas por dispositivo.
#
# MEDIDO EL 23-10-2026 (medidas/ensayos/gpu_100pct_*): subirlo a 6 y poner dos
# ranuras de calculo lleva la GPU del 28 % al 60 % de uso, pero NO da mas KIA
# (la emision va por presupuesto por minuto) y sube los vatios un 36 %, asi que
# KIA/kWh BAJA de 1.135 a 825. Por eso el defecto sigue siendo el de antes: la
# mejora de ocupacion se enciende a mano (KUKIA_PROFUNDIDAD, KUKIA_HILOS_GPU,
# KUKIA_EN_VUELO=8 y KUKIA_MAX_TAREAS_NODO=8 en el Core).
PROFUNDIDAD = max(1, min(10, int(os.environ.get("KUKIA_PROFUNDIDAD", "2"))))
# Aviso de parada (Ctrl+C). Lo pone main() y lo miran el hilo peticionero y el
# bucle de calculo, para terminar las entregas en vuelo antes de salir.
PARAR = threading.Event()
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
# Cerradura de instancia unica (22-10-2026): el 22-10 se llego a abrir una
# segunda ventana del minero sin querer; dos mineros se reparten el cupo de
# tareas del nodo (4) y rinden la mitad, sin que se vea ningun error.
LOCK = Path(__file__).parent / "minero.lock"

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
    # 'device' va en el registro (23-10-2026): el Core necesita saber si este
    # nodo es CPU para no mandarle difusiones, que en CPU cuestan ~20x mas.
    # Los Core antiguos ignoran el campo de mas (NodoIn lo acepta y lo tira).
    carga = {"nombre": etiqueta, "vram_gb": vram_gb, "tops": tops,
             "device": str(device), "clave_publica": pubkey,
             "direccion_cobro": direccion}
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
        #
        # BUCLE RAPIDO (22-10-2026): antes cada paso eran ~7 operaciones de
        # torch (siete lanzamientos de kernel); ahora es UNA convolucion 3x3
        # (el laplaciano de 5 puntos) mas una suma en sitio. Medido: 1907 pasos
        # 0,66 s -> 0,30 s y 20.000 pasos 7,00 s -> 3,37 s, con resultado
        # BIT A BIT identico al bucle viejo (mismo orden de redondeo), asi que
        # el Core sigue verificando igual sin tocar nada. Ver
        # tools/prueba_difusion_rapida.py.
        import numpy as np
        import torch
        import torch.nn.functional as F
        n = int(params.get("n", 256))
        pasos = int(params.get("pasos", 200))
        seed = int(params.get("seed", 42))
        rejilla = np.random.default_rng(seed).random((n, n))
        x = torch.from_numpy(rejilla).to(device=device, dtype=torch.float64)
        if str(device).startswith("cuda"):
            # En GPU la convolucion gana (1,7x a 5x). En CPU es MAS LENTA que
            # las operaciones sueltas (medido: 0,6x), asi que ahi se queda el
            # bucle de siempre: el resultado es el mismo por los dos caminos.
            lap_k = torch.tensor([[0.0, 1.0, 0.0],
                                  [1.0, -4.0, 1.0],
                                  [0.0, 1.0, 0.0]],
                                 dtype=torch.float64, device=device).view(1, 1, 3, 3)
            for _ in range(pasos):
                lap = F.conv2d(x.view(1, 1, n, n), lap_k)
                x[1:-1, 1:-1] += 0.2 * lap.view(n - 2, n - 2)
        else:
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
    """Una linea por minuto: asi se ve si el minero rinde o se ha atascado.

    Se enseña en KIA/min, KIA/h y KIA/dia, y ademas cuanto de eso da cada
    GFLOPS de potencia medida: es la forma de comparar dos GPUs sin discutir
    sobre lo que declara el driver.
    """
    ultimo = time.time()
    ult_ok, ult_kuki, ult_err = 0, 0.0, 0
    while True:
        time.sleep(RESUMEN_SEG)
        with cuenta_lock:
            ok, kuki, err = cuenta["ok"], cuenta["kuki"], cuenta["err"]
            gflops = float(cuenta.get("gflops") or 0.0)
        dt = max(0.001, time.time() - ultimo)
        tareas_min = (ok - ult_ok) / dt * 60
        kia_min = (kuki - ult_kuki) / dt * 60
        potencia = f" {gflops:.1f} GFLOPS |" if gflops > 0 else ""
        por_gf = f" ({kia_min * 60 / gflops:.2f} KIA/h por GFLOPS)" if gflops > 0 else ""
        print(f"[KUKIA] {etiqueta}{potencia} {tareas_min:.0f} tareas/min | "
              f"{kia_min:.3f} KIA/min = {kia_min * 60:.1f} KIA/h = {kia_min * 1440:.0f} KIA/dia"
              f"{por_gf} | {err - ult_err} fallos, total {ok} tareas / {kuki:.3f} KIA",
              flush=True)
        ultimo = time.time()
        ult_ok, ult_kuki, ult_err = ok, kuki, err


def _anunciar_emision() -> None:
    """Al arrancar: cuanto paga cada tarea y cuando se acaba la moneda.

    Solo lee /stats; si el Core no contesta, el minero arranca igual (esto es
    informacion, no requisito para minar).
    """
    try:
        d = _get(f"{CORE}/stats", timeout=TIMEOUT_API).json()
    except Exception:
        return
    e = d.get("emision") or {}
    pago = float(d.get("pago_por_tarea") or 0.0)
    techo = int(d.get("max_supply") or 0)
    bolsa = d.get("bolsa_emision") or {}
    precio_gf = bolsa.get("precio_minuto")
    if d.get("modo_pago") == "potencia" and precio_gf:
        # 23-10-2026: con pago por trabajo, "KIA por tarea" ya no significa
        # nada (cada tarea cobra sus GFLOP x precio). Antes imprimia 0.000000
        # KIA por tarea y parecia que minar no pagaba.
        suelo = float((d.get("pago_por_potencia") or {}).get("gflop_min_facturable") or 0.0)
        como_paga = (f"{float(precio_gf):.9f} KIA por GFLOP verificado"
                     + (f" (suelo {suelo:g} GFLOP/tarea)" if suelo > 0 else ""))
    else:
        como_paga = f"{pago:.6f} KIA por tarea"
    print(f"[KUKIA] emision: era {e.get('era_actual')} (x{e.get('multiplicador')}), "
          f"{como_paga}, {d.get('emision_por_min')} KIA/min en toda la red, "
          f"techo {techo:,} KIA".replace(",", "."), flush=True)
    fin = str(e.get("fin_emision") or "")
    try:
        anos = (datetime.strptime(fin, "%Y-%m-%d").date() - date.today()).days / 365.25
    except Exception:
        return
    # El calendario ya no se mide "desde hoy" (eso cambiaba solo con el paso
    # del tiempo y empujo la fecha al 2152). Lo que se comprueba es el largo
    # del calendario: 6 eras x halving_years, que con el fin en 2150 son 124.
    try:
        calendario = 6.0 * float(e.get("halving_years") or 0.0)
    except Exception:
        calendario = 0.0
    veredicto = (f"calendario de {calendario:.1f} anos" if calendario >= 124
                 else f"REVISAR: calendario corto ({calendario:.1f} anos)")
    print(f"[KUKIA] ultima moneda: {fin} -> quedan {anos:.1f} anos ({veredicto})",
          flush=True)


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
    # LATIDO TAMBIEN EN CPU (23-10-2026): antes solo latia la GPU, asi que un
    # nodo CPU no volvia a decir nunca con que dispositivo trabaja. Ahora late
    # siempre: el Core aprende el dispositivo en cada conexion y deja de
    # adivinarlo por VRAM/TOPS (que castigaba a los nodos antiguos sin
    # difusiones).
    if privkey:
        threading.Thread(target=_bucle_telemetria,
                         args=(nid, device, vram_gb, tops, privkey, estado_tarea),
                         daemon=True).start()
    # El id puede cambiar si el Core olvida el nodo; todo lo que va por detras
    # (vigia) lee la misma caja, no una copia.
    ctx = {"nid": nid}
    cuenta = {"ok": 0, "kuki": 0.0, "err": 0, "ultimo_ok": time.time(),
              "ultimo_limite": 0.0, "ultimo_core_ok": time.time(),
              # Potencia medida en ESTA GPU (GFLOPS). El resumen la usa para
              # enseñar los KIA por hora que rinde cada GFLOPS.
              "gflops": float(_GFLOPS_BASE.get(device) or 0.0)}
    cuenta_lock = threading.Lock()
    threading.Thread(target=_hilo_resumen,
                     args=(etiqueta, cuenta, cuenta_lock), daemon=True).start()
    threading.Thread(target=_hilo_vigia,
                     args=(etiqueta, ctx, privkey, cuenta, cuenta_lock), daemon=True).start()
    # `bloqueado` en dict y no en variable suelta: lo escribe tambien el hilo
    # de entrega (un 429 o un 403 del Core al entregar frena las peticiones).
    bloqueado = {"hasta": 0.0}
    espera_error = 5.0   # backoff exponencial (5 s -> 60 s)

    # --- CALCULAR SIN ESPERAR A LA RED (23-10-2026) --------------------------
    # Antes: pedir -> calcular -> entregar -> pedir, todo en el mismo hilo. El
    # dispositivo se quedaba mirando la respuesta del Core con la GPU parada, y
    # esas respuestas no son rapidas: medido en este Core, /tareas/pendiente
    # tarda 353 ms de mediana (p90 555 ms) y la entrega 152 ms (p90 500, hasta
    # 2,8 s esperando a la verificacion redundante). Resultado: el Core le
    # atribuia 18,9 GFLOP/s a una RTX 5070 que en el banco da 84,8
    # (tools/medir_potencia.py). El tiempo no se perdia calculando.
    # Ahora: un hilo pide (y deja la tarea en cola), este bucle calcula, y la
    # entrega se manda desde un tercer hilo. El dispositivo solo hace lo suyo.
    # Tareas EN VUELO por nodo. El Core corta a 4 ("limite de tareas
    # simultaneas"), asi que 4 es el cupo maximo util: se coge antes de pedir
    # y se suelta cuando la entrega termina o la tarea se descarta.
    # Con dos ranuras de calculo por tarjeta hacen falta mas tareas en vuelo
    # para tenerlas a las dos ocupadas (cada pedido tarda ~350 ms y cada tarea
    # ~120 ms). El Core tiene que permitirlo: KUKIA_MAX_TAREAS_NODO=8.
    # Defecto 4 = el de siempre; ver el comentario de PROFUNDIDAD arriba.
    cupo_vuelo = threading.Semaphore(max(2, min(12, int(os.environ.get("KUKIA_EN_VUELO", "4")))))
    cola_tareas = queue.Queue(maxsize=PROFUNDIDAD)
    en_vuelo = {"n": 0}
    en_vuelo_lock = threading.Lock()

    def _soltar():
        """Suelta UNA tarea en vuelo. Unico camino de liberacion.

        POR QUE UN SOLO SITIO (23-10-2026): el cupo se coge al pedir y se
        suelta al entregar, pero hay seis salidas distintas (429, error HTTP,
        cola vacia, tarea invalida, tipo no implementado, excepcion al
        calcular). Si una se olvida, el cupo se pierde para siempre: al cuarto
        fallo el nodo deja de minar y no lo dice. Aqui se suelta siempre por
        el mismo sitio, y se lleva la cuenta para poder esperar al parar.
        """
        try:
            cupo_vuelo.release()
        except Exception:
            pass
        with en_vuelo_lock:
            en_vuelo["n"] = max(0, en_vuelo["n"] - 1)

    def _entregar(pid, tipo, metrica, duracion_ms, telemetria, es_replica, firma):
        """Manda la entrega (o la verificacion) sin bloquear al dispositivo."""
        try:
            nid_e = ctx["nid"]
            if es_replica:
                # Verificacion: no paga ahora; el Core la cruza con el primario.
                try:
                    rv = _post(f"{CORE}/tareas/replica",
                               json={"tid": pid, "nodo_id": nid_e, "metrica": metrica,
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
                            print(f"[KUKIA] {etiqueta} replica {pid[:8]} sin reclamar: {det}",
                                  flush=True)
                    elif rv.status_code == 409:
                        # La tarea se pago antes de que llegara la verificacion.
                        print(f"[KUKIA] {etiqueta} replica {pid[:8]} llego tarde "
                              f"(la tarea ya estaba pagada)", flush=True)
                except Exception as e:
                    print(f"[KUKIA] {etiqueta} replica {pid[:8]} no entregada: {e}", flush=True)
                return
            entrega = {"tid": pid, "nodo_id": nid_e, "metrica": metrica,
                       "firma": firma, "device": str(device),
                       "duracion_ms": duracion_ms, "telemetria": telemetria}
            rr = None
            for _intento in (1, 2):
                try:
                    rr = _post(f"{CORE}/tareas/resultado_json", json=entrega,
                               timeout=TIMEOUT_ENTREGA)
                    if rr.status_code == 404 and "Not Found" in (rr.text or ""):
                        rr = _post(f"{CORE}/tareas/resultado", params=entrega,
                                   timeout=TIMEOUT_ENTREGA)
                except requests.exceptions.RequestException as e:
                    # Un timeout no significa que no se haya cobrado: se
                    # reintenta UNA vez (si ya estaba pagada, el segundo
                    # intento devuelve 409 y no hay doble pago).
                    if _intento == 1:
                        print(f"[KUKIA] {etiqueta} entrega de {pid[:8]} sin respuesta "
                              f"({type(e).__name__}); reintento", flush=True)
                        continue
                    print(f"[KUKIA] {etiqueta} entrega de {pid[:8]} perdida: {e}; "
                          f"la tarea volvera a la cola", flush=True)
                    rr = None
                    break
                if (_intento == 1 and rr.status_code in (401, 404)
                        and "nodo" in (rr.text or "")):
                    print(f"[KUKIA] {etiqueta} el Core no ve el nodo {nid_e} al entregar "
                          f"{pid[:8]}; lo compruebo y reintento", flush=True)
                    nuevo = _re_registrar(nid_e, etiqueta, device, vram_gb, tops,
                                          pubkey, direccion, privkey)
                    if nuevo and nuevo != nid_e:
                        ctx["nid"] = nid_e = nuevo
                        entrega["nodo_id"] = nid_e
                        entrega["firma"] = WALLET.firmar(f"{pid}:{metrica}", privkey)
                    continue
                break
            if rr is None:
                return
            if rr.status_code == 429:
                print(f"[KUKIA] {etiqueta} Core saturado (429) al entregar, espero 20 s",
                      flush=True)
                bloqueado["hasta"] = time.time() + 20
                return
            if rr.status_code != 200:
                print(f"[KUKIA] {etiqueta} entrega rechazada: {rr.status_code} "
                      f"{rr.text[:140]}", flush=True)
                if rr.status_code in (429, 403):
                    bloqueado["hasta"] = time.time() + 30
                return
            res = rr.json()
            if res.get("ok"):
                with cuenta_lock:
                    cuenta["ok"] += 1
                    cuenta["kuki"] += float(res.get("kuki") or 0.0)
                    cuenta["ultimo_ok"] = time.time()
                print(f"[KUKIA] {etiqueta} tarea {pid[:8]} {tipo} -> OK +{res.get('kuki')} KIA "
                      f"({duracion_ms:.0f} ms)", flush=True)
            else:
                with cuenta_lock:
                    cuenta["err"] += 1
                print(f"[KUKIA] {etiqueta} tarea {pid[:8]} {tipo} -> RECHAZADO "
                      f"(esperado {res.get('esperado')}, enviado {metrica[:40]})", flush=True)
        except Exception as e:
            print(f"[KUKIA] {etiqueta} entrega de {pid[:8]} fallida: "
                  f"{type(e).__name__}: {e}", flush=True)
        finally:
            # Se suelta SIEMPRE, con o sin exito: si no, una entrega que falle
            # deja el cupo cogido y el nodo pierde capacidad para siempre.
            _soltar()

    def _pedir():
        """Hilo dedicado a PEDIR trabajo; el bucle de abajo solo calcula.

        POR QUE (medido el 23-10-2026 en este Core): /tareas/pendiente tarda
        353 ms de mediana (p90 555 ms). Calculado dentro del mismo hilo, la
        tarjeta se pasaba mas tiempo esperando la tarea siguiente que
        calculandola: 18,9 GFLOP/s atribuidos a una GPU que da 84,8 en el
        banco. Ahora la peticionero va por delante y deja la tarea en cola.
        """
        espera = 2.0
        while not PARAR.is_set():
            tengo_cupo = False
            try:
                ahora = time.time()
                if ahora < bloqueado["hasta"]:
                    time.sleep(min(5.0, bloqueado["hasta"] - ahora))
                    continue
                # Barrido periodico (throttled a BARRIDO_SEG): si el Core paga
                # directo a la direccion no hace nada; si quedo saldo viejo en
                # algun nodo, lo mueve a la billetera.
                barrer_saldos(etiqueta, w, privkey)
                if not cupo_vuelo.acquire(timeout=1.0):
                    continue
                tengo_cupo = True
                with en_vuelo_lock:
                    en_vuelo["n"] += 1
                nid_p = ctx["nid"]
                # AUDITORIA 2026-10-20: peticion FIRMADA con nonce aleatorio.
                # Sin nonce, una peticion capturada se podia repetir 5 minutos
                # y robar la cola del nodo. El Core nuevo exige la firma con
                # nonce; los mineros viejos siguen con el mensaje antiguo.
                ts = int(time.time())
                nonce = secrets.token_hex(16)
                firma_p = WALLET.firmar(f"pendiente:{nid_p}:{ts}:{nonce}",
                                        privkey) if privkey else ""
                rp = _get(f"{CORE}/tareas/pendiente",
                          params={"nodo_id": nid_p, "ts": ts, "nonce": nonce,
                                  "firma": firma_p},
                          timeout=TIMEOUT_API)
                if rp.status_code == 429:
                    print(f"[KUKIA] {etiqueta} limitado por el Core (429), espero 20 s",
                          flush=True)
                    bloqueado["hasta"] = time.time() + 20
                    _soltar()
                    continue
                if rp.status_code != 200:
                    detalle = (rp.text or "")[:160]
                    _soltar()
                    if rp.status_code in (401, 404) and "nodo no registrado" in detalle:
                        print(f"[KUKIA] {etiqueta} el Core no ve el nodo {nid_p}: lo compruebo "
                              f"antes de seguir", flush=True)
                        ctx["nid"] = _re_registrar(nid_p, etiqueta, device, vram_gb, tops,
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
                if not _cuerpo.get("tarea"):
                    motivo = str(_cuerpo.get("motivo") or "")
                    _soltar()
                    if "limite" in motivo:
                        # Cupo del nodo lleno (tareas colgadas de antes, o un
                        # nodo nuevo que solo puede tener 1). Se espera medio
                        # segundo: con 1 s el arranque de un nodo nuevo tarda el
                        # doble de lo necesario.
                        cuenta["ultimo_limite"] = time.time()
                        time.sleep(0.5)
                    else:
                        time.sleep(0.2)
                    continue
                # El cupo de esta tarea lo suelta quien la entregue.
                tengo_cupo = False
                cola_tareas.put(_cuerpo)
            except Exception as e:
                print(f"[KUKIA] {etiqueta} pidiendo trabajo: {type(e).__name__}: {e} "
                      f"(reintento en {espera:.0f} s)", flush=True)
                time.sleep(espera)
                espera = min(60.0, espera * 1.6)
            else:
                espera = 2.0      # la ultima peticion fue bien: sin backoff
            finally:
                if tengo_cupo:
                    _soltar()

    threading.Thread(target=_pedir, daemon=True,
                     name=f"pedir-{str(device).replace(':', '')}").start()

    def _calcular():
        """Una ranura de calculo del dispositivo (puede haber varias).

        POR QUE MAS DE UNA (23-10-2026): con una sola ranura, entre tarea y
        tarea la tarjeta esperaba al Python (pedir, firmar, montar la
        telemetria). Medido en el ensayo de dos GPU: 28 % de uso medio y
        49,5 GFLOP/s, con tareas de 8 ms a 400 ms. Dos ranuras solapan la
        preparacion de una con los kernels de la otra sin tocar el Core ni
        el pago.
        """
        espera_error = 5.0
        while not PARAR.is_set():
            en_mano = False      # hay un cupo cogido por la tarea que llevamos
            try:
                # El trabajo lo trae el hilo _pedir; aqui solo se calcula.
                try:
                    _cuerpo = cola_tareas.get(timeout=2.0)
                except queue.Empty:
                    continue
                en_mano = True
                nid = ctx["nid"]
                pend = _cuerpo.get("tarea")
                # EJECUCION REDUNDANTE (21-10-2026): el Core puede dar una tarea
                # que otro nodo ya esta resolviendo. Verificar tambien se cobra
                # (30 % del premio de esa tarea) y no hace falta recalcular nada
                # en el Core, que era el cuello de botella de la red.
                es_replica = bool(_cuerpo.get("replica"))
                pid = str(pend.get("id") or "")
                tipo = pend.get("tipo")
                ok_t, motivo_t, params = validar_tarea(tipo, pend.get("params"))
                if not pid or not ok_t:
                    print(f"[KUKIA] {etiqueta} tarea {pid[:8] or '?'} rechazada localmente: "
                          f"{motivo_t or 'id vacio'}", flush=True)
                    _soltar()
                    en_mano = False
                    time.sleep(3)
                    continue
                t0 = time.time()
                try:
                    metrica = trabajo_local(tipo, params, device)
                except Exception as e:
                    # FALLO AL CALCULAR (23-10-2026). Dos cosas:
                    #
                    # 1. Se REINTENTA una vez tras vaciar la cache de CUDA. Casi
                    #    todas las faltas de memoria son fragmentacion, no tamaño:
                    #    el segundo intento con la cache limpia suele entrar.
                    # 2. Si vuelve a fallar, se suelta el cupo (antes se quedaba
                    #    cogido para siempre: cuatro fallos y el nodo dejaba de
                    #    minar en silencio) y se sigue con otra tarea.
                    # La tarea se queda asignada en el Core hasta que vence
                    # (ABANDONO_SEG)... eso ocupa un hueco del cupo del Core, no
                    # del nuestro. Para eso hace falta un /tareas/soltar en el
                    # Core: propuesto, no aplicado (toca protocolo).
                    print(f"[KUKIA] {etiqueta} error calculando {pid[:8]} "
                          f"({type(e).__name__}: {e}); limpio VRAM y reintento", flush=True)
                    try:
                        if str(device).startswith("cuda"):
                            import torch
                            torch.cuda.empty_cache()
                    except Exception:
                        pass
                    try:
                        metrica = trabajo_local(tipo, params, device)
                    except Exception as e2:
                        print(f"[KUKIA] {etiqueta} tampoco a la segunda {pid[:8]} "
                              f"({type(e2).__name__}: {e2}); sigo con otra tarea",
                              flush=True)
                        _soltar()
                        en_mano = False
                        time.sleep(3.0)
                        continue
                duracion_ms = (time.time() - t0) * 1000.0
                estado_tarea["ms"] = round(duracion_ms, 1)
                if metrica == "tarea_no_soportada":
                    print(f"[KUKIA] {etiqueta} tipo '{tipo}' no implementado en este worker", flush=True)
                    _soltar()
                    en_mano = False
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
                # El dispositivo ya tiene su resultado: la entrega se manda desde
                # otro hilo y este bucle VUELVE A PEDIR TRABAJO en el acto. Es toda
                # la optimizacion: el calculo de la siguiente tarea se solapa con
                # la espera del Core (que puede ser de hasta 3 s por la replica).
                espera_error = 5.0
                en_mano = False      # el cupo lo suelta el hilo de entrega
                try:
                    threading.Thread(target=_entregar,
                                     args=(pid, tipo, metrica, duracion_ms, telemetria,
                                           es_replica, firma),
                                     daemon=True).start()
                except Exception:
                    en_mano = True   # no llego a nacer: el cupo vuelve a nosotros
                    raise
            except Exception as e:
                if en_mano:
                    _soltar()
                    en_mano = False
                # Backoff: un Core caido no debe hacer que 2 GPUs lo martilleen
                # en bucle ni llenar el log a 1000 lineas por minuto.
                print(f"[KUKIA] {etiqueta} err: {e} (reintento en {espera_error:.0f} s)", flush=True)
                time.sleep(espera_error)
                espera_error = min(60.0, espera_error * 1.6)

    # Ranuras de calculo por dispositivo. 1 = comportamiento anterior.
    # El tope real lo pone el Core (cupo de tareas en vuelo por nodo).
    hilos_calc = max(1, min(4, int(os.environ.get("KUKIA_HILOS_GPU", "1"))))
    for _h in range(hilos_calc):
        threading.Thread(target=_calcular, daemon=True,
                         name=f"calc-{str(device).replace(':', '')}-{_h}").start()
    while not PARAR.is_set():
        time.sleep(0.5)

    # --- SALIDA LIMPIA ------------------------------------------------------
    # Al parar (Ctrl+C o PARAR), se dejan terminar las entregas en vuelo. Si el
    # proceso muere con tareas asignadas sin entregar, el Core no las devuelve a
    # la cola hasta pasados 30 min (ABANDONO_SEG) y ese calculo se pierde.
    limite = time.time() + 15.0
    while time.time() < limite:
        with en_vuelo_lock:
            if en_vuelo["n"] == 0:
                break
        time.sleep(0.2)
    with en_vuelo_lock:
        quedan = en_vuelo["n"]
    print(f"[KUKIA] {etiqueta} parado" +
          (f" ({quedan} sin entregar)" if quedan else " limpiamente"), flush=True)

def _proceso_vivo(pid: int) -> bool:
    """¿Sigue vivo ese PID? (Windows sin psutil: OpenProcess + GetExitCode)."""
    try:
        pid = int(pid)
    except Exception:
        return False
    if pid <= 0 or pid == os.getpid():
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == 259             # STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    except Exception:
        return False


def _tomar_lock() -> bool:
    """True si esta ventana puede minar. False si ya hay otro minero vivo.

    Si el fichero tiene un PID muerto (se cerro la ventana a lo bruto) se
    sobrescribe: la cerradura nunca deja el minero clavado.
    """
    try:
        if LOCK.exists():
            otro = (LOCK.read_text(encoding="utf-8") or "").strip()
            otro = int(otro) if otro.isdigit() else 0
            if otro and _proceso_vivo(otro):
                print(f"[KUKIA] ya hay un minero vivo (PID {otro}): esta ventana NO mina",
                      flush=True)
                print("[KUKIA] dos mineros en la misma GPU se reparten el cupo y rinden "
                      "la mitad.", flush=True)
                print("[KUKIA] cierra esa ventana (o ese proceso) y vuelve a abrir esta.",
                      flush=True)
                return False
    except Exception:
        pass
    try:
        LOCK.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass
    return True


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


# --- Deteccion de GPU con reintento (26-10-2026) ---------------------------
# FALLO REAL: al iniciar sesion el driver de NVIDIA tarda unos segundos en
# estar listo (por eso la guardia del .vbs espera 40 s antes de arrancar nada).
# Si el minero llegaba antes, `torch.cuda.device_count()` devolvia 0 y el
# minero se quedaba TODA la sesion minando en CPU: GPU encendida, cero potencia
# medida y tareas mucho mas lentas, sin ningun error en el log. Ahora se
# reintenta un rato antes de darse por vencido; solo si al final no aparece
# ninguna GPU se mina en CPU.
CUDA_INTENTOS = max(1, int(os.environ.get("KUKIA_CUDA_INTENTOS", "6")))
CUDA_ESPERA_SEG = max(1.0, float(os.environ.get("KUKIA_CUDA_ESPERA_SEG", "10")))


def _gpus_nvidia() -> list:
    """Nombres de las GPU NVIDIA del equipo segun el driver (nvidia-smi).

    Sirve para distinguir "este PC no tiene GPU" de "tiene GPU pero CUDA no la
    ve": en el log las dos cosas se veian exactamente igual.
    """
    extra = {}
    if os.name == "nt":
        extra["creationflags"] = 0x08000000   # CREATE_NO_WINDOW
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name",
                            "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=8, **extra)
        if r.returncode == 0:
            return [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
    except Exception:
        pass
    return []


def _detectar_gpus() -> tuple:
    """(numero de GPUs CUDA, motivo si son 0). Reintenta al arrancar.

    No hay cache que valga: cada intento vuelve a preguntar a torch. Se sale en
    cuanto hay una GPU. Tampoco bloquea el arranque para siempre: como mucho
    espera KUKIA_CUDA_INTENTOS x KUKIA_CUDA_ESPERA_SEG (por defecto ~50 s) y
    entonces mina en CPU, que es lo que hacia antes siempre.
    """
    motivo = "sin probar"
    for intento in range(1, CUDA_INTENTOS + 1):
        try:
            import torch
        except ImportError as e:
            # Definitivo: reintentar no instala torch. Se dice claro y se sigue.
            return 0, f"torch no esta instalado ({e})"
        except Exception as e:
            motivo = f"no se pudo importar torch ({type(e).__name__})"
        else:
            try:
                n = int(torch.cuda.device_count() or 0)
                motivo = "CUDA no ve ninguna GPU" if n == 0 else ""
            except Exception as e:
                n, motivo = 0, f"CUDA no inicializa ({type(e).__name__}: {e})"
            if n > 0:
                if intento > 1:
                    print(f"[KUKIA] GPU disponible en el intento {intento}/{CUDA_INTENTOS}: "
                          f"{n} - se mina en GPU", flush=True)
                return n, ""
        if intento < CUDA_INTENTOS:
            print(f"[KUKIA] aun no hay GPU ({motivo}); intento {intento}/{CUDA_INTENTOS}, "
                  f"reintento en {CUDA_ESPERA_SEG:.0f} s", flush=True)
            time.sleep(CUDA_ESPERA_SEG)
    return 0, motivo


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
    if not _tomar_lock():
        return 0
    _vigilar_direccion()
    ok_core, motivo_core = _core_seguro(CORE)
    if not ok_core:
        print(f"[KUKIA] ERROR: {motivo_core}", flush=True)
        return 2
    nombre = sys.argv[1] if len(sys.argv) > 1 else "worker_" + socket.gethostname()
    n_gpus, motivo_gpu = _detectar_gpus()

    def _esperar(hilos):
        """Espera a los hilos y, si llega un Ctrl+C, para ordenadamente."""
        try:
            for t in hilos:
                t.join()
        except KeyboardInterrupt:
            # Ctrl+C: se avisa a los hilos y se les da tiempo a entregar lo que
            # llevan encima antes de salir. Sin esto, el pipeline deja hasta 4
            # tareas colgadas media hora en el Core.
            PARAR.set()
            print("[KUKIA] parando: termino lo que esta en vuelo y salgo...", flush=True)
            for t in hilos:
                t.join(timeout=20)
            print("[KUKIA] minero detenido", flush=True)

    if n_gpus == 0:
        print(f"[KUKIA] Sin GPUs CUDA ({motivo_gpu}) - usando CPU (mas lento)", flush=True)
        nombres = _gpus_nvidia()
        if nombres:
            # La GPU esta, CUDA no la ve: es un problema de driver, no de
            # hardware, y asi queda escrito para no confundirlo con "este PC no
            # tiene GPU".
            print(f"[KUKIA] OJO: el equipo SI tiene GPU NVIDIA ({', '.join(nombres)}) "
                  f"pero CUDA no la ve: se mina en CPU. Comprueba 'nvidia-smi' y "
                  f"reinicia el minero cuando responda.", flush=True)
        _benchmark_arranque()
        _anunciar_emision()
        # En un hilo y no en el principal: asi el Ctrl+C pasa por la misma
        # parada ordenada que el resto de modos (entrega lo que tenga en vuelo).
        t = threading.Thread(target=ciclo_worker, args=(nombre, "cpu", 0, 0.0), daemon=True)
        t.start()
        _esperar([t])
        return

    print(f"[KUKIA] {n_gpus} GPU(s) detectada(s) - minando en TODAS a la vez", flush=True)
    # Una linea para saber QUE version corre: con el pipeline, "pide, calcula y
    # entrega" van en hilos distintos y conviene verlo en el arranque.
    print(f"[KUKIA] pipeline: {PROFUNDIDAD} tarea(s) en cola por dispositivo, "
          f"{os.environ.get('KUKIA_EN_VUELO', '4')} en vuelo por nodo", flush=True)
    _benchmark_arranque()
    _anunciar_emision()
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

    # CPU como un nodo mas, si se ha pedido (KUKIA_USAR_CPU). Ver _ajuste_cpu.
    usar_cpu, hilos_cpu = _ajuste_cpu()
    if usar_cpu:
        try:
            import torch
            torch.set_num_threads(hilos_cpu)   # no ahogar a los hilos de las GPU
        except Exception:
            pass
        print(f"[KUKIA] CPU tambien mina: {hilos_cpu} hilos "
              f"(KUKIA_USAR_CPU=0 lo apaga)", flush=True)
        t = threading.Thread(target=ciclo_worker, args=(nombre, "cpu", 0, 0.0), daemon=True)
        t.start()
        hilos.append(t)
    else:
        print("[KUKIA] CPU en reposo (KUKIA_USAR_CPU=1 para que mine tambien)", flush=True)

    _esperar(hilos)

if __name__ == "__main__":
    raise SystemExit(main())
