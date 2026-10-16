"""KUKIA Worker - minero multi-GPU: se registra, pide trabajo, ejecuta en GPU.

VERIFICACION REAL: el worker calcula el resultado (no un hash propio) y el
Core lo recalcula y compara. El worker NO puede falsificar: si el numero no
coincide exactamente con el recalculado por el Core, no cobra.

MULTI-GPU: usa TODAS las GPUs disponibles a la vez (una cola por GPU).
"""
import hashlib, json, sys, time, threading, socket
import requests
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "ledger"))
import kukia_wallet as WALLET

CORE = "http://127.0.0.1:8300"

NOMBRE_VISIBLE = "KUKIAMINER V1.0 BETA"

def _limpia(texto):
    """Quita IPs y rutas personales de lo que se muestra en pantalla.
    Los datos tecnicos internos (ids de tarea/nodo) se conservan."""
    import os as _os
    import re as _re
    t = str(texto)
    t = _re.sub(r"\b\d{1,3}(\.\d{1,3}){3}\b", "***.***.***.***", t)
    try:
        perfil = _os.path.dirname(_os.path.dirname(_os.environ.get("APPDATA", ""))) + "\\"
        if len(perfil) > 5:
            t = t.replace(perfil, "~\\")
    except Exception:
        pass
    return t

def _difusion_torch(n, pasos, seed, device):
    """Ecuacion del calor 2D en GPU. La condicion inicial se genera con numpy
    sembrado (identica en CPU y GPU); el bucle corre en el device pedido."""
    import torch
    import numpy as _np
    x0 = _np.random.default_rng(int(seed)).random((int(n), int(n)))
    x = torch.from_numpy(x0).to(dtype=torch.float64).to(device)
    for _ in range(int(pasos)):
        x[1:-1, 1:-1] = x[1:-1, 1:-1] + 0.2 * (
            x[:-2, 1:-1] + x[2:, 1:-1] + x[1:-1, :-2] + x[1:-1, 2:] - 4 * x[1:-1, 1:-1])
    return float(x.sum().item())

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
        n = int(params.get("n", 256))
        pasos = int(params.get("pasos", 200))
        seed = int(params.get("seed", 42))
        return str(_difusion_torch(n, pasos, seed, device))
    if tipo in ("embeddings", "lote_embeddings"):
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import ia_onnx_cpu
        ruta = _modelo_local(params)
        clave = params["input"] if tipo == "embeddings" else params["inputs"]
        h = ia_onnx_cpu.config_hash(ruta, clave)
        if "model_hash" in params and str(params["model_hash"]) != h:
            return "hash_modelo_no_coincide"
        if tipo == "embeddings":
            return json.dumps(ia_onnx_cpu.ejecutar_tarea(ruta, params["input"]))
        return json.dumps([ia_onnx_cpu.ejecutar_tarea(ruta, e) for e in params["inputs"]])
    return "tarea_no_soportada"

def _modelo_local(params):
    """Punto 5: usa el modelo local o lo baja del Core (/modelos) si falta."""
    ruta = Path(str(params["model_path"]))
    if ruta.is_file():
        return str(ruta)
    dest = Path(__file__).parent.parent / "modelos" / ruta.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(f"{CORE}/modelos/{ruta.name}", timeout=600)
    if r.status_code != 200:
        raise RuntimeError("modelo no disponible ni local ni en el Core")
    dest.write_bytes(r.content)
    return str(dest)

# --- PASO 3: controles del propietario (se aplican AQUI, en el minero) ---
# LIMITACION HONESTA: max_vram_fraction limita la memoria CUDA de este proceso
# (torch), y espera_entre_tareas_seg dosifica el uso en el tiempo. NINGUNO de
# los dos garantiza por si solo un limite exacto de potencia (W) ni de
# temperatura: eso exige control del driver (nvidia-smi -pl, con permisos).
CONFIG_PATH = Path(__file__).parent / "minero_config.json"
DEFAULT_CONFIG = {"max_vram_fraction": 0.8, "horarios": [["00:00", "24:00"]],
                  "pausa": False, "excluir_campanas": [], "colas": ["A", "B", "C"],
                  "espera_entre_tareas_seg": 0, "espera_sin_tareas_seg": 10}

def cargar_config():
    try:
        cfg = dict(DEFAULT_CONFIG)
        if CONFIG_PATH.is_file():
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        return cfg
    except Exception:
        return dict(DEFAULT_CONFIG)

LOCK_PATH = Path(__file__).parent / "minero.lock"

def _candado_instancia():
    """Anti-doble-instancia: un solo minero por carpeta. Si el PID del
    candado sigue vivo, salimos; si murio, heredamos el candado."""
    import os
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        pass
    except Exception:
        return True
    try:
        pid = int(LOCK_PATH.read_text(encoding="utf-8").strip().split()[0])
    except Exception:
        pid = -1
    vivo = False
    if pid > 0:
        try:
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            vivo = bool(h)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
        except Exception:
            try:
                os.kill(pid, 0)
                vivo = True
            except Exception:
                vivo = False
    if vivo:
        return False
    try:
        LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except Exception:
        return True

def _en_horario(cfg):
    try:
        from datetime import datetime as _dt
        ahora = _dt.now().strftime("%H:%M")
        for ini, fin in cfg.get("horarios", [["00:00", "24:00"]]):
            if ini <= ahora <= fin or (fin < ini and (ahora >= ini or ahora <= fin)):
                return True
        return False
    except Exception:
        return True

_energia_wh = {}
_t_ultimo = {}

def energia_sesion(device):
    """Energia ESTIMADA acumulada de la sesion (potencia instantanea x tiempo
    entre envios). Es una estimacion, no una medida integrada: va etiquetada."""
    import time as _t
    ahora = _t.time()
    wh = _energia_wh.get(device, 0.0)
    if device in _t_ultimo:
        try:
            pot = 0.0
            for g in metricas_gpu():
                if f"cuda:{g['gpu']}" == str(device):
                    pot = float(g["potencia_w"])
                    break
            wh += pot * max(0.0, ahora - _t_ultimo[device]) / 3600.0
        except Exception:
            pass
    _energia_wh[device] = wh
    _t_ultimo[device] = ahora
    return round(wh, 4)

def _pausado(cfg):
    try:
        if cfg.get("pausa"):
            return True
        return (Path(__file__).parent / "PAUSA").is_file()
    except Exception:
        return False

def metricas_gpu():
    """Potencia (W), temperatura (C), carga (%) y VRAM por GPU via nvidia-smi.
    Solo lectura. Si falla, devuelve lista vacia (no rompe el minero)."""
    import subprocess
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,temperature.gpu,power.draw,"
             "utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20)
        res = []
        for lin in (out.stdout or "").strip().split("\n"):
            p = [x.strip() for x in lin.split(",")]
            if len(p) == 7:
                res.append({"gpu": int(p[0]), "nombre": p[1], "temp_c": float(p[2]),
                            "potencia_w": float(p[3]), "carga_pct": float(p[4]),
                            "vram_usada_mb": float(p[5]), "vram_total_mb": float(p[6])})
        return res
    except Exception:
        return []

def prueba_gpu():
    """Punto 3: atestado GPU declarativo (NO criptografico: el minero podria
    mentir; sirve como telemetria y filtro inicial, no como prueba)."""
    import time as _t
    try:
        import torch
        if torch.cuda.is_available():
            i = torch.cuda.current_device()
            p = torch.cuda.get_device_properties(i)
            a = torch.randn(2048, 2048, device=f"cuda:{i}")
            t0 = _t.time(); c = (a @ a.T).sum().item(); dt = _t.time() - t0
            return f"{p.name}|cc{p.major}.{p.minor}|vram{p.total_memory//1024**3}GB|kernel{dt:.3f}s"
    except Exception as e:
        return f"sin_gpu:{e}"
    return "sin_gpu"

def ciclo_worker(nombre: str, device: str, vram_gb: int, tops: float, billetera: str = ""):
    """Un hilo por GPU: registra su nodo y mina en bucle (resultados FIRMADOS).
    Si se indica billetera, firma con esa (p.ej. la del fundador, bajo su orden);
    si no, crea/usa una propia worker_<nombre>."""
    etiqueta = f"{nombre}[{device}]"
    # Billetera para firmar envios (nadie puede suplantar este nodo)
    _wname = billetera if billetera else "worker_" + nombre.replace("[", "_").replace("]", "_").replace(":", "")
    w = WALLET.crear_billetera(_wname)
    if isinstance(w, tuple):
        w = w[0]
    pubkey = w.get("clave_publica", "")
    privkey = w.get("clave_privada", "")
    _espera = 10
    # IDENTIDAD PERSISTENTE: reutiliza el nodo ya registrado (evita duplicados).
    sys.path.insert(0, str(Path(__file__).parent))
    import identidad as _ID
    _info = _ID.get_nodo(etiqueta)
    nid = None
    if _info.get("nodo_id"):
        try:
            _r = requests.get(f"{CORE}/tareas/pendiente",
                              params={"nodo_id": _info["nodo_id"]}, timeout=15)
            if _r.status_code in (200, 429):
                nid = _info["nodo_id"]
                print(f"[KUKIAMINER V1.0 BETA] {etiqueta} identidad reutilizada {nid}")
        except Exception:
            nid = None
    if nid:
        pass
    else:
        print(f"[KUKIAMINER V1.0 BETA] {etiqueta} REGISTRANDO")
    while not nid:
        try:
            r = requests.post(f"{CORE}/nodos/registrar",
                              json={"nombre": etiqueta, "vram_gb": vram_gb, "tops": tops,
                                    "clave_publica": pubkey, "tops_estimado": True,
                                    "prueba_gpu": prueba_gpu()}, timeout=30)
            if r.status_code == 429:
                print(f"[KUKIAMINER V1.0 BETA] {etiqueta} Core saturado (429), esperando 60s")
                time.sleep(60)
                continue
            nid = r.json().get("id")
            if not nid:
                print(f"[KUKIAMINER V1.0 BETA] {etiqueta} registro sin id, reintento en {_espera}s")
                time.sleep(_espera)
                _espera = min(60, _espera + 10)
                continue
            break
        except Exception as e:
            print(f"[KUKIAMINER V1.0 BETA] {etiqueta} sin conexion, reintentando solo en {_espera}s: {_limpia(e)}")
            time.sleep(_espera)
            _espera = min(60, _espera + 10)
    _ID.set_nodo(etiqueta, nid, pubkey)
    print(f"[KUKIAMINER V1.0 BETA] nodo {nid} ({etiqueta}) vram={vram_gb}GB listo")
    try:
        import torch as _tc
        _dev = int(str(device).split(":")[1]) if ":" in str(device) else 0
        _tc.cuda.set_per_process_memory_fraction(
            max(0.1, min(1.0, float(cargar_config().get("max_vram_fraction", 0.8)))), _dev)
    except Exception:
        pass
    _ult = {"estado": ""}

    def _loge(msg):
        if msg != _ult["estado"]:
            print(f"[KUKIAMINER V1.0 BETA] {etiqueta} {msg}")
            _ult["estado"] = msg

    _loge("CONECTANDO")
    while True:
        try:
            cfg = cargar_config()
            if not _en_horario(cfg) or _pausado(cfg):
                _loge("en pausa (config/horario/fichero PAUSA), esperando sin minar")
                time.sleep(30)
                continue
            _loge("CONECTANDO")
            try:
                r = requests.get(f"{CORE}/tareas/pendiente",
                                 params={"nodo_id": nid,
                                         "excluir": ",".join(cfg.get("excluir_campanas", [])),
                                         "colas": ",".join(cfg.get("colas", ["A", "B", "C"]))},
                                 timeout=30)
            except Exception:
                _loge("ERROR sin conexion al Core, reintentando solo")
                time.sleep(10)
                continue
            if r.status_code == 429:
                _loge("ERROR Core saturado (429), esperando 60s")
                time.sleep(60)
                continue
            try:
                pend = r.json().get("tarea")
            except Exception:
                pend = None
                _loge("ERROR respuesta invalida del Core, esperando")
            if not pend:
                _loge("ESPERANDO TAREA CIENTIFICA")
                time.sleep(int(cfg.get("espera_sin_tareas_seg", 10) or 10))
                continue
            _loge(f"EJECUTANDO {pend['id'][:8]} ({pend['tipo']})")
            _t0 = time.time()
            metrica = trabajo_local(pend["tipo"], pend["params"], device)
            if metrica == "tarea_no_soportada":
                _loge(f"tarea incompatible con este minero ({pend['tipo']}), se deja para otro")
                time.sleep(30)
                continue
            _dur_ms = round((time.time() - _t0) * 1000.0, 2)
            _loge(f"ENVIANDO RESULTADO {pend['id'][:8]}")
            firma = WALLET.firmar(f"{pend['id']}:{metrica}", privkey) if privkey else ""
            res = requests.post(f"{CORE}/tareas/resultado",
                                params={"tid": pend["id"], "nodo_id": nid, "metrica": metrica,
                                        "firma": firma, "device": device,
                                        "duracion_ms": _dur_ms,
                                        "telemetria": json.dumps(
                                            {"gpu": [g for g in metricas_gpu()
                                                      if f"cuda:{g['gpu']}" == str(device)],
                                             "wh_sesion_estimada": energia_sesion(device)})},
                                timeout=60).json()
            estado = f"OK +{res['kuki']} KIA" if res.get("ok") else f"RECHAZADO (esperado {res.get('esperado')})"
            print(f"[KUKIAMINER V1.0 BETA] {etiqueta} tarea {pend['id'][:8]} -> {estado}")
            _esp = float(cargar_config().get("espera_entre_tareas_seg", 0) or 0)
            if _esp > 0:
                time.sleep(_esp)
        except Exception as e:
            print(f"[KUKIAMINER V1.0 BETA] {etiqueta} err: {_limpia(e)}")
            time.sleep(5)

def main():
    if "--probar-onnx" in sys.argv:
        # Autotest aislado de la referencia CPU (no mina, no toca red/BD).
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import ia_onnx_cpu
        sys.exit(0 if ia_onnx_cpu.probar_onnx() else 1)
    if not _candado_instancia():
        print("[KUKIAMINER V1.0 BETA] Ya hay otro minero corriendo (candado activo). Saliendo.")
        sys.exit(3)
    try:
        import os as _os
        if _os.name == "nt":
            _os.system("title KUKIAMINER V1.0 BETA")
    except Exception:
        pass
    _args = [a for a in sys.argv[1:] if a != "--probar-onnx"]
    _bil = ""
    if "--billetera" in _args:
        _i = _args.index("--billetera")
        _bil = _args[_i + 1] if _i + 1 < len(_args) else ""
        del _args[_i:_i + 2]
    nombre = _args[0] if _args and not _args[0].startswith("--") else "worker_" + socket.gethostname()
    try:
        import torch
        n_gpus = torch.cuda.device_count()
    except Exception:
        n_gpus = 0

    if n_gpus == 0:
        print("[KUKIAMINER V1.0 BETA] Sin GPUs CUDA - usando CPU (mas lento)")
        ciclo_worker(nombre, "cpu", 0, 0.0, _bil)
        return

    print(f"[KUKIAMINER V1.0 BETA] {n_gpus} GPU(s) detectada(s) - minando en TODAS a la vez")
    hilos = []
    for g in range(n_gpus):
        try:
            props = torch.cuda.get_device_properties(g)
            vram = props.total_memory // (1024**3)
            # TOPS aproximados por clase de GPU (referencia NVIDIA)
            # ETIQUETA HONESTA (punto 1): son ESTIMADOS por VRAM, jamas medidos.
            tops_est = {8: 60.0, 12: 300.0, 16: 760.0, 24: 1300.0}.get(int(vram), 100.0)
        except Exception:
            vram, tops_est = 0, 0.0
        t = threading.Thread(target=ciclo_worker, args=(nombre, f"cuda:{g}", vram, tops_est, _bil), daemon=True)
        t.start()
        hilos.append(t)
    for t in hilos:
        t.join()

if __name__ == "__main__":
    main()
