"""KUKIA Core - coordinador: nodos, tareas, verificacion REAL, ledger (SQLite).

VERIFICACION (corregida): el Core NO confia en el hash del minero.
El Core recalcula la tarea el mismo (trustless) y compara el resultado
exacto. Tareas con aritmetica determinista (enteros exactos via float64
con magnitudes acotadas) para que CPU y GPU den el MISMO numero.
"""
import hashlib, json, secrets, sqlite3, threading, uuid, time, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    import ia_onnx_cpu as ONNXCPU  # referencia CPU (tareas embeddings)
except Exception:
    ONNXCPU = None
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel

# Lock global: SQLite NO es seguro para uso concurrente en una sola conexion.
# Todo pago pasa por este lock + BEGIN IMMEDIATE (doble proteccion).
_pago_lock = threading.Lock()

DB = Path(__file__).parent / "ledger" / "kukia.db"
DB.parent.mkdir(parents=True, exist_ok=True)

# --- Token de ADMIN: solo Kuki maneja el Core ---
# Se genera UNA vez y se guarda en un fichero local (nadie mas lo tiene).
ADMIN_TOKEN_FILE = Path(__file__).parent / "admin_token.txt"
def admin_token() -> str:
    if ADMIN_TOKEN_FILE.exists():
        return ADMIN_TOKEN_FILE.read_text(encoding="utf-8").strip()
    tok = secrets.token_hex(32)
    ADMIN_TOKEN_FILE.write_text(tok, encoding="utf-8")
    return tok

ADMIN_TOKEN = admin_token()

def exigir_admin(x_admin_token: str = Header(default="", alias="X-Admin-Token")):
    """Solo el fundador (con el token) puede administrar el Core."""
    if not secrets.compare_digest(x_admin_token or "", ADMIN_TOKEN):
        raise HTTPException(403, "solo el administrador del Core")

MAX_SUPPLY = 100_000_000          # techo fijo 100M
REWARD_BASE = 10.0                # KUKI por tarea base
# --- Bloque genesis fundacional (una sola vez) ---
GENESIS_DIR = "KUK77D72508BD940C9BA969B0402A3CF1F9679BE3"   # wallet fundador (Kuki)
GENESIS_MONTO = 15_000_000.0      # 15M reservados al fundador dentro del techo de 100M
TIER_MULT = {12: 1.0, 16: 1.3, 24: 1.6}   # Rent-Tier por VRAM (mejora 1)
REF_BONUS = 50.0                  # bonus referido al completar 10 tareas (mejora 2)
BOUNTY = 1000.0                   # bounty anti-trampa (mejora 3)
# --- Politica de firmas (migracion) ---
# False = nodos viejos sin clave siguen cobrando (compatibilidad, fase pruebas).
# True  = TODOS los nodos deben traer firma (rechaza sin clave_publica).
# Pasar a True al abrir la red publica.
EXIGIR_FIRMA_GLOBAL = True

# Tareas 'ejecutando' sin resultado tras este tiempo vuelven a 'queued'
# (recuperacion oportunista en /tareas/pendiente, misma transaccion).
ABANDONO_SEG = 1800.0

con = sqlite3.connect(str(DB), check_same_thread=False)
con.row_factory = sqlite3.Row

def schema():
    con.execute("CREATE TABLE IF NOT EXISTS nodos(id TEXT PRIMARY KEY, nombre TEXT, vram_gb INT, "
                "tier REAL, reputacion REAL DEFAULT 1.0, ref TEXT, tareas_ok INT DEFAULT 0, "
                "creado TEXT, tops REAL DEFAULT 0)")
    con.execute("CREATE TABLE IF NOT EXISTS tareas(id TEXT PRIMARY KEY, tipo TEXT, params TEXT, "
                "estado TEXT DEFAULT 'queued', nodo_id TEXT, resultado TEXT, hash TEXT, "
                "metrica TEXT, creado TEXT, fin TEXT, verificado INT DEFAULT 0, doble TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "tarea_id TEXT, nodo_id TEXT, kuki REAL, motivo TEXT, ts TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS registros(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "nombre TEXT, email TEXT, gpu TEXT, tops TEXT, ceder TEXT, ip TEXT, ts TEXT)")
    try:
        con.execute("ALTER TABLE nodos ADD COLUMN tops REAL DEFAULT 0")
    except Exception:
        pass
    try:
        con.execute("ALTER TABLE nodos ADD COLUMN clave_publica TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        con.execute("ALTER TABLE tareas ADD COLUMN asignado REAL DEFAULT NULL")
    except Exception:
        pass
    for _sql in (
        "ALTER TABLE tareas ADD COLUMN cola TEXT DEFAULT 'B'",
        "ALTER TABLE tareas ADD COLUMN campana TEXT DEFAULT ''",
        "ALTER TABLE tareas ADD COLUMN prioridad REAL DEFAULT 0",
        "ALTER TABLE tareas ADD COLUMN vram_min INT DEFAULT 0",
        "ALTER TABLE tareas ADD COLUMN device TEXT DEFAULT ''",
        "ALTER TABLE tareas ADD COLUMN duracion_ms REAL DEFAULT 0",
    ):
        try:
            con.execute(_sql)
        except Exception:
            pass
    con.execute("CREATE TABLE IF NOT EXISTS planificador_log(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "tarea_id TEXT, nodo_id TEXT, cola TEXT, motivo TEXT, ts TEXT)")
    con.commit()

schema()

def genesis():
    """Asignacion fundacional: se ejecuta UNA sola vez (idempotente)."""
    ya = con.execute("SELECT COUNT(*) c FROM ledger WHERE motivo='asignacion fundacional'").fetchone()["c"]
    if not ya:
        con.execute("INSERT INTO ledger(tarea_id,nodo_id,kuki,motivo,ts) VALUES(?,?,?,?,?)",
                    ("genesis", GENESIS_DIR, GENESIS_MONTO, "asignacion fundacional", time.ctime()))
        con.commit()

genesis()

# ============================================================
#  VERIFICACION REAL (trustless): el Core recalcula la tarea
# ============================================================

def gen_matriz(n: int, seed: int) -> np.ndarray:
    """Matriz determinista de enteros acotados (exacta en float64)."""
    idx = np.arange(n, dtype=np.float64)
    return (idx[:, None] * 31 + idx[None, :] * 17 + seed) % 97 - 48

def computar_tarea(tipo: str, params: dict):
    """Recalcula el resultado esperado en el propio Core (numpy, CPU)."""
    if tipo == "checksum":
        data = str(params.get("data", "kukia"))
        return hashlib.sha256(data.encode()).hexdigest()
    if tipo == "matriz":
        n = int(params.get("n", 256))
        seed = int(params.get("seed", 42))
        a = gen_matriz(n, seed)
        b = gen_matriz(n, seed + 1)
        return str(int(round(float((a @ b).sum()))))
    if tipo == "inferencia":
        n = int(params.get("n", 64))
        seed = int(params.get("seed", 7))
        x = gen_matriz(n, seed)
        k = np.array([[1.0, 2.0, 1.0], [2.0, 4.0, 2.0], [1.0, 2.0, 1.0]])
        out = 0.0
        for i in range(3):
            for j in range(3):
                out += float((x[i:n-2+i, j:n-2+j] * k[i, j]).sum())
        return str(int(round(out)))
    return None

# --- Modelos de referencia para tareas IA (punto 5: distribucion) ---
MODELOS_DIR = Path(__file__).parent / "modelos"
MODELOS_DIR.mkdir(parents=True, exist_ok=True)

def _verificar_difusion(params, metrica):
    """Recomputa la difusion en CPU (numpy float64) y compara con tolerancia
    relativa (GPU y CPU redondean distinto tras cientos de pasos)."""
    try:
        n = int(params.get("n", 256))
        pasos = int(params.get("pasos", 200))
        seed = int(params.get("seed", 42))
        x = np.random.default_rng(seed).random((n, n))
        for _ in range(pasos):
            x[1:-1, 1:-1] = x[1:-1, 1:-1] + 0.2 * (
                x[:-2, 1:-1] + x[2:, 1:-1] + x[1:-1, :-2] + x[1:-1, 2:] - 4 * x[1:-1, 1:-1])
        ref = float(x.sum())
        val = float(metrica)
        tol = float(params.get("tolerancia_rel", 1e-9))
        return abs(val - ref) <= tol * max(1.0, abs(ref))
    except Exception:
        return False

def _verificar_embeddings(tipo, params, metrica):
    """Puntos 1+2: hash del modelo + recomputo CPU con tolerancia (lote: spot-check)."""
    try:
        modelo = params["model_path"]
        vecs = json.loads(metrica)
        if not isinstance(vecs, list):
            return False
        h = ONNXCPU.config_hash(modelo, params["input"] if tipo == "embeddings" else params["inputs"])
        if str(params.get("model_hash", h)) != h:
            return False
        tol = float(params.get("tolerancia", 1e-5))
        if tipo == "embeddings":
            pares = [(params["input"], vecs)]
        else:
            entradas = list(params["inputs"])
            if len(vecs) != len(entradas):
                return False
            if len(entradas) > 3:
                import random
                idx = random.Random(int(params.get("seed", 0))).sample(range(len(entradas)), 3)
                pares = [(entradas[i], vecs[i]) for i in idx]
            else:
                pares = list(zip(entradas, vecs))
        for ent, rec in pares:
            ref = ONNXCPU.ejecutar_tarea(modelo, ent)
            if not ONNXCPU.comparar_con_tolerancia(rec, ref, tol):
                return False
        return True
    except Exception:
        return False

app = FastAPI(title="KUKIA Core", version="0.2.0")

# ============================================================
#  RATE LIMIT (anti-spam: max 30 peticiones/min por IP en rutas publicas)
# ============================================================
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarRequest

RUTAS_LIMITADAS = {"/registro", "/nodos/registrar", "/tareas/pendiente", "/tareas/resultado", "/transferir"}
RATE_MAX = 30
RATE_WINDOW = 60.0
_rate_hist = {}
_rate_lock = threading.Lock()

class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarRequest, call_next):
        if request.url.path in RUTAS_LIMITADAS:
            ip = request.client.host if request.client else "?"
            ahora = time.time()
            with _rate_lock:
                hist = [t for t in _rate_hist.get(ip, []) if ahora - t < RATE_WINDOW]
                if len(hist) >= RATE_MAX:
                    from fastapi.responses import JSONResponse
                    return JSONResponse({"detail": "demasiadas peticiones, espera un minuto"}, status_code=429)
                hist.append(ahora)
                _rate_hist[ip] = hist
        return await call_next(request)

app.add_middleware(RateLimitMiddleware)

class NodoIn(BaseModel):
    nombre: str
    vram_gb: int
    ref: str = ""
    tops: float = 0.0    # potencia IA declarada (TOPS, valor oficial de la GPU)
    clave_publica: str = ""  # Ed25519 hex (64 bytes) para autenticar resultados
    prueba_gpu: str = ""  # atestado GPU declarativo (punto 3, no criptografico)
    tops_estimado: bool = True  # punto 1: los TOPS son estimados por VRAM, no medidos

class TareaIn(BaseModel):
    tipo: str      # 'checksum' | 'matriz' | 'inferencia' | 'embeddings' | 'lote_embeddings' | 'difusion_calor'
    params: dict = {}
    cola: str = "B"       # A=comercial, B=investigacion KUKIA, C=voluntaria/experimental
    campana: str = ""     # identificador de campana (para exclusion y trazabilidad)
    prioridad: float = 0.0  # dentro de la cola, mayor antes
    vram_min: int = 0     # VRAM minima (GB) que exige la tarea

def tier(vram: int) -> float:
    return TIER_MULT.get(vram, 1.0)

# --- Calendario de emision: la ultima moneda sale el 13-04-2150 ---
# 6 halvings en 124,28 anos -> era de 20,71 anos. Tras esa fecha, emision = 0.
from datetime import date, datetime
EMISSION_START = date(2026, 1, 1)
EMISSION_END = date(2150, 4, 13)
HALVING_YEARS = (EMISSION_END - EMISSION_START).days / 365.25 / 6  # ~20,71
def emission_mult() -> float:
    """Multiplicador de recompensa por era: 1.0, 0.5, 0.25... 0 tras el 13-04-2150."""
    hoy = date.today()
    if hoy >= EMISSION_END:
        return 0.0
    elapsed = (hoy - EMISSION_START).days / 365.25
    era = max(0, int(elapsed // HALVING_YEARS))
    return 0.5 ** era

def emission_info() -> dict:
    from datetime import timedelta
    hoy = date.today()
    if hoy >= EMISSION_END:
        return {"era_actual": 6, "multiplicador": 0.0,
                "proximo_halving": None, "fin_emision": "2150-04-13",
                "halving_years": round(HALVING_YEARS, 2)}
    elapsed = (hoy - EMISSION_START).days / 365.25
    era = max(0, int(elapsed // HALVING_YEARS))
    prox = EMISSION_START + timedelta(days=int((era + 1) * HALVING_YEARS * 365.25))
    return {
        "era_actual": era,
        "multiplicador": 0.5 ** era,
        "proximo_halving": prox.isoformat(),
        "fin_emision": "2150-04-13",
        "halving_years": round(HALVING_YEARS, 2),
    }

@app.post("/nodos/registrar")
def registrar(n: NodoIn):
    nid = uuid.uuid4().hex[:12]
    con.execute("CREATE TABLE IF NOT EXISTS gpu_pruebas(nodo_id TEXT, prueba TEXT, creado TEXT)")
    try:
        con.execute("ALTER TABLE nodos ADD COLUMN tops_estimado INT DEFAULT 1")
    except Exception:
        pass
    if n.prueba_gpu:
        con.execute("INSERT INTO gpu_pruebas(nodo_id,prueba,creado) VALUES(?,?,?)",
                    (nid, n.prueba_gpu[:2000], time.ctime()))
    con.execute("INSERT INTO nodos(id,nombre,vram_gb,tier,reputacion,ref,creado,tops,clave_publica,tops_estimado) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (nid, n.nombre, n.vram_gb, tier(n.vram_gb), 1.0, n.ref, time.ctime(), n.tops, n.clave_publica, 1 if n.tops_estimado else 0))
    con.commit()
    return {"id": nid, "tier": tier(n.vram_gb), "max_supply": MAX_SUPPLY, "tops_registrados": n.tops,
            "autenticado": bool(n.clave_publica)}

@app.get("/nodos")
def nodos():
    return [dict(r) for r in con.execute("SELECT * FROM nodos").fetchall()]

@app.get("/modelos/{nombre}")
def descargar_modelo(nombre: str):
    """Punto 5: el Core sirve el modelo de referencia; el minero lo baja si le falta."""
    seguro = Path(nombre).name
    ruta = MODELOS_DIR / seguro
    if not ruta.is_file():
        raise HTTPException(404, "modelo no disponible en el Core")
    from fastapi.responses import FileResponse
    return FileResponse(str(ruta), filename=seguro)

@app.post("/tareas/crear")
def crear_tarea(t: TareaIn, _ok: None = Depends(exigir_admin)):
    tid = uuid.uuid4().hex[:12]
    # los params llevan seed -> la tarea es reproducible/verificable
    cola = t.cola if t.cola in ("A", "B", "C") else "B"
    con.execute("INSERT INTO tareas(id,tipo,params,estado,creado,cola,campana,prioridad,vram_min) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (tid, t.tipo, json.dumps(t.params), "queued", time.ctime(),
                 cola, t.campana[:64], float(t.prioridad), int(t.vram_min)))
    con.commit()
    return {"tarea_id": tid, "estado": "queued"}

@app.get("/tareas/pendiente")
def pendiente(nodo_id: str, excluir: str = "", colas: str = ""):
    """Asignacion ATOMICA: SELECT + UPDATE en una transaccion con lock.

    Dos workers simultaneos NO pueden llevarse la misma tarea: el segundo
    espera al lock y re-verifica que siga en 'queued' antes de asignarla.

    POLITICA DE PRIORIDAD (PASO 2): cola A > B > C; dentro de cola, mayor
    prioridad antes; a igual, mas antigua antes. Solo tareas con
    vram_min <= VRAM del nodo. 'excluir' = campanas separadas por comas que
    el minero no quiere. SIN desalojo: lo que ya ejecuta no se interrumpe.
    Cada asignacion queda en planificador_log (auditable).

    Recuperacion oportunista: antes de asignar, las tareas 'ejecutando'
    sin resultado tras ABANDONO_SEG vuelven a 'queued' en la misma
    transaccion. No duplica pagos: el pago exige estado='ejecutando' con
    el nodo asignado actual (ver /tareas/resultado).
    """
    with _pago_lock:
        try:
            con.execute("BEGIN IMMEDIATE")
        except Exception:
            raise HTTPException(503, "Core ocupado, reintentar")
        try:
            con.execute("UPDATE tareas SET estado='queued', nodo_id=NULL, asignado=NULL "
                        "WHERE estado='ejecutando' AND asignado IS NOT NULL "
                        "AND asignado < ?", (time.time() - ABANDONO_SEG,))
            nodo = con.execute("SELECT vram_gb FROM nodos WHERE id=?", (nodo_id,)).fetchone()
            vram = int(nodo["vram_gb"] or 0) if nodo else 0
            excl = [e.strip()[:64] for e in (excluir or "").split(",") if e.strip()]
            _q = ("SELECT id,tipo,params,cola,campana FROM tareas WHERE estado='queued' "
                  "AND COALESCE(vram_min,0) <= ? ")
            _args = [vram]
            if excl:
                _q += "AND COALESCE(campana,'') NOT IN (%s) " % ",".join("?" * len(excl))
                _args += excl
            _colas = [c.strip().upper() for c in (colas or "").split(",")
                      if c.strip().upper() in ("A", "B", "C")]
            if _colas:
                _q += "AND COALESCE(cola,'B') IN (%s) " % ",".join("?" * len(_colas))
                _args += _colas
            _q += ("ORDER BY CASE COALESCE(cola,'B') WHEN 'A' THEN 0 WHEN 'B' THEN 1 ELSE 2 END, "
                   "COALESCE(prioridad,0) DESC, creado LIMIT 1")
            row = con.execute(_q, _args).fetchone()
            if not row:
                con.execute("ROLLBACK")
                return {"tarea": None}
            # Re-verificar DENTRO del lock que nadie la cogio antes
            chk = con.execute("SELECT estado FROM tareas WHERE id=?", (row["id"],)).fetchone()
            if not chk or chk["estado"] != "queued":
                con.execute("ROLLBACK")
                return {"tarea": None}
            con.execute("UPDATE tareas SET estado='ejecutando', nodo_id=?, asignado=? WHERE id=?",
                        (nodo_id, time.time(), row["id"]))
            try:
                _cola = row["cola"] or "B"
            except Exception:
                _cola = "B"
            con.execute("INSERT INTO planificador_log(tarea_id,nodo_id,cola,motivo,ts) VALUES(?,?,?,?,?)",
                        (row["id"], nodo_id, _cola,
                         f"A>B>C,prioridad,vram_min_ok(vram={vram},excl={len(excl)})", time.ctime()))
            con.commit()
            return {"tarea": {"id": row["id"], "tipo": row["tipo"], "params": json.loads(row["params"])}}
        except HTTPException:
            raise
        except Exception:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
            raise HTTPException(500, "error interno asignando tarea")

@app.post("/tareas/resultado")
def resultado(tid: str, nodo_id: str, metrica: str, hash_res: str = "", firma: str = "",
              device: str = "", duracion_ms: float = 0.0, telemetria: str = ""):
    """El Core recalcula la tarea y compara el RESULTADO EXACTO (trustless).

    AUTENTICACION: si el nodo registro clave_publica, el envio debe traer
    firma Ed25519 de "tid:metrica". Sin firma valida -> 401. Asi nadie puede
    suplantar a otro nodo enviando su nodo_id.
    """
    params = None
    tipo = None
    # 1. Lectura inicial (sin lock: solo para validar existencia y tipo)
    task = con.execute("SELECT * FROM tareas WHERE id=?", (tid,)).fetchone()
    if not task:
        raise HTTPException(404, "tarea no encontrada")
    params = json.loads(task["params"])
    tipo = task["tipo"]
    esperado = computar_tarea(tipo, params)
    _tol = (tipo in ("embeddings", "lote_embeddings") and ONNXCPU is not None) or tipo == "difusion_calor"
    if esperado is None and not _tol:
        raise HTTPException(400, "tipo de tarea no soportado")
    if tipo in ("embeddings", "lote_embeddings") and ONNXCPU is not None:
        ok = _verificar_embeddings(tipo, params, metrica)
    elif tipo == "difusion_calor":
        ok = _verificar_difusion(params, metrica)
    else:
        ok = str(metrica).strip() == str(esperado).strip()

    # 2. TRANSACCION ATOMICA con bloqueo de escritura inmediato.
    # Lock de hilos (la conexion SQLite es compartida) + BEGIN IMMEDIATE
    # (bloqueo a nivel de fichero BD contra otros procesos).
    with _pago_lock:
        try:
            con.execute("BEGIN IMMEDIATE")
        except Exception:
            raise HTTPException(503, "Core ocupado, reintentar")
        try:
            # Releer DENTRO del lock (el estado pudo cambiar entre 1 y 2)
            task2 = con.execute("SELECT * FROM tareas WHERE id=?", (tid,)).fetchone()
            if task2["estado"] == "ok":
                con.execute("ROLLBACK")
                raise HTTPException(409, "tarea ya pagada (doble pago bloqueado)")
            if task2["estado"] == "fallo":
                con.execute("ROLLBACK")
                raise HTTPException(409, "tarea ya cerrada con fallo (no reintentable)")
            if task2["estado"] != "ejecutando":
                con.execute("ROLLBACK")
                raise HTTPException(409, f"tarea en estado '{task2['estado']}', no pagable")
            if task2["nodo_id"] and task2["nodo_id"] != nodo_id:
                con.execute("ROLLBACK")
                raise HTTPException(409, "tarea asignada a otro nodo")

            # AUTENTICACION PRIMERO (vale para correctos E incorrectos):
            # nadie sin firma puede cerrar la tarea de otro (ni como fallo).
            node = con.execute("SELECT * FROM nodos WHERE id=?", (nodo_id,)).fetchone()
            if not node:
                con.execute("ROLLBACK")
                raise HTTPException(404, "nodo no registrado")
            pub = None
            try:
                pub = node["clave_publica"]
            except Exception:
                pub = None
            # Politica de migracion: si EXIGIR_FIRMA_GLOBAL, sin clave no hay cobro.
            if EXIGIR_FIRMA_GLOBAL and not pub:
                con.execute("ROLLBACK")
                raise HTTPException(401, "nodo sin clave registrada (firma obligatoria)")
            if pub:
                if not firma:
                    con.execute("ROLLBACK")
                    raise HTTPException(401, "falta firma del nodo (suplantacion bloqueada)")
                try:
                    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
                    Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(
                        bytes.fromhex(firma), f"{tid}:{metrica}".encode())
                except Exception:
                    con.execute("ROLLBACK")
                    raise HTTPException(401, "firma invalida (suplantacion bloqueada)")

            kuki = 0.0
            info_pago = {}
            if ok:
                # Tope calculado DENTRO de la transaccion (nadie mas puede mover el supply)
                kuki, info_pago = recompensa_pago(node["tier"])
                con.execute("UPDATE nodos SET tareas_ok=tareas_ok+1, reputacion=min(5,reputacion+0.05) "
                            "WHERE id=?", (nodo_id,))
                h = hashlib.sha256(f"{tid}:{nodo_id}:{metrica}".encode()).hexdigest()
                con.execute("UPDATE tareas SET estado='ok', hash=?, metrica=?, fin=?, verificado=1, device=?, duracion_ms=? WHERE id=?",
                            (h, metrica, time.ctime(), str(device)[:16], float(duracion_ms or 0.0), tid))
                try:
                    con.execute("CREATE TABLE IF NOT EXISTS gpu_telemetria(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                                "nodo_id TEXT, device TEXT, metricas TEXT, ts TEXT, origen TEXT)")
                    if telemetria:
                        con.execute("INSERT INTO gpu_telemetria(nodo_id,device,metricas,ts,origen) VALUES(?,?,?,?,?)",
                                    (nodo_id, str(device)[:16], telemetria[:2000], time.ctime(), "worker"))
                except Exception:
                    pass
                con.execute("INSERT INTO ledger(tarea_id,nodo_id,kuki,motivo,ts) VALUES(?,?,?,?,?)",
                            (tid, nodo_id, kuki, "trabajo verificado", time.ctime()))
            else:
                con.execute("UPDATE tareas SET estado='fallo', fin=?, device=?, duracion_ms=? WHERE id=?",
                            (time.ctime(), str(device)[:16], float(duracion_ms or 0.0), tid))
                con.execute("INSERT INTO ledger(tarea_id,nodo_id,kuki,motivo,ts) VALUES(?,?,?,?,?)",
                            (tid, nodo_id, 0.0, "resultado incorrecto (recalculado por el Core)", time.ctime()))
            con.commit()
        except HTTPException:
            raise
        except Exception:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
            raise HTTPException(500, "error interno en el pago")
    return {"ok": ok, "kuki": kuki, "esperado": esperado, "pago": info_pago}

@app.get("/ledger")
def ledger():
    return [dict(r) for r in con.execute("SELECT * FROM ledger ORDER BY id DESC LIMIT 200").fetchall()]

# ============================================================
#  DIFICULTAD ADAPTATIVA (potencia de LA RED KUKIA, no mundial)
#  + RECOMPENSA DINAMICA (garantiza agotar justo el 13-04-2150)
# ============================================================
REFERENCIA_TOPS = 1747.0   # red inicial (5070 + 5060 Ti): punto de calibracion
BASE_MATRIX_N = 256
BASE_INFER_N = 64

def tops_red() -> float:
    """Potencia total de la red = suma de TOPS declarados por los nodos."""
    row = con.execute("SELECT COALESCE(SUM(tops),0) s FROM nodos").fetchone()
    return float(row["s"] or 0.0)

def dificultad() -> dict:
    """Tamano de tarea segun la potencia de la red (tarea ~constante en tiempo)."""
    tops = tops_red()
    factor = 1.0 if tops <= 0 else min(4.0, max(0.5, (tops / REFERENCIA_TOPS) ** 0.5))
    return {
        "tops_red": round(tops, 1),
        "tops_referencia": REFERENCIA_TOPS,
        "factor": round(factor, 3),
        "matriz_n": int(BASE_MATRIX_N * factor),
        "inferencia_n": int(BASE_INFER_N * factor),
    }

def supply_emitido() -> float:
    return float(con.execute("SELECT COALESCE(SUM(kuki),0) s FROM ledger").fetchone()["s"] or 0.0)

def _epoch(ts: str) -> float:
    try:
        return time.mktime(time.strptime(ts))
    except Exception:
        return time.time()

def tasa_tareas_seg() -> float:
    """Ritmo medio real de la red (tareas OK / segundo). Minimo: 1 tarea/dia."""
    n = con.execute("SELECT COUNT(*) c FROM tareas WHERE estado='ok'").fetchone()["c"]
    if n == 0:
        return 1.0 / 86400.0
    primera = con.execute("SELECT MIN(fin) m FROM tareas WHERE estado='ok' AND fin IS NOT NULL").fetchone()["m"]
    if not primera:
        return 1.0 / 86400.0
    elapsed = max(3600.0, time.time() - _epoch(primera))
    return max(1.0 / 86400.0, n / elapsed)

def recompensa_pago(tier: float) -> tuple:
    """Pago que garantiza: (1) nunca pasar de 100M, (2) agotar justo el 13-04-2150.

    - objetivo = restante / tareas_restantes_estimadas (persigue la fecha)
    - techo/suelo = bandas del halving x10 / x0.1 (predecible, anti-spikes)
    - pago final = min(base x tier, restante) -> el ledger JAMAS supera 100M
    """
    emit = supply_emitido()
    restante = MAX_SUPPLY - emit
    if restante <= 0:
        return 0.0, {"agotado": True}
    fin_dt = datetime(EMISSION_END.year, EMISSION_END.month, EMISSION_END.day)
    seg_rest = max(1.0, (fin_dt - datetime.now()).total_seconds())
    tasa = tasa_tareas_seg()
    tareas_rest = max(1.0, tasa * seg_rest)
    objetivo = restante / tareas_rest
    techo = REWARD_BASE * emission_mult()
    base = min(max(objetivo, techo * 0.1), techo * 10.0)
    pago = round(min(base * tier, restante), 4)
    return pago, {"objetivo": round(objetivo, 4), "techo_halving": round(techo, 4),
                  "tasa_dia": round(tasa * 86400, 2), "restante": round(restante, 2)}

@app.get("/stats")
def stats():
    sum = con.execute("SELECT COALESCE(SUM(kuki),0) as s FROM ledger").fetchone()["s"]
    sum = min(sum, MAX_SUPPLY)
    pago1, info1 = recompensa_pago(1.0)
    return {
        "supply_emitido": sum,
        "max_supply": MAX_SUPPLY,
        "nodos": con.execute("SELECT COUNT(*) c FROM nodos").fetchone()["c"],
        "tareas_ok": con.execute("SELECT COUNT(*) c FROM tareas WHERE estado='ok'").fetchone()["c"],
        "tareas_pend": con.execute("SELECT COUNT(*) c FROM tareas WHERE estado='queued'").fetchone()["c"],
        "registros": con.execute("SELECT COUNT(*) c FROM registros").fetchone()["c"],
        "emision": emission_info(),
        "dificultad": dificultad(),
        "recompensa_base_actual": pago1,
        "detalle_pago": info1,
    }

@app.post("/tareas/crear_auto")
def crear_auto(tipo: str = "matriz", _ok: None = Depends(exigir_admin)):
    """Crea una tarea con la dificultad ACTUAL de la red (tamano segun TOPS)."""
    import secrets as _secrets
    dif = dificultad()
    if tipo == "matriz":
        params = {"n": dif["matriz_n"], "seed": _secrets.randbelow(100000)}
    elif tipo == "inferencia":
        params = {"n": dif["inferencia_n"], "seed": _secrets.randbelow(100000)}
    elif tipo == "checksum":
        params = {"data": "kukia-" + _secrets.token_hex(8)}
    else:
        raise HTTPException(400, "tipo no soportado (matriz|inferencia|checksum)")
    tid = uuid.uuid4().hex[:12]
    con.execute("INSERT INTO tareas(id,tipo,params,estado,creado) VALUES(?,?,?,?,?)",
                (tid, tipo, json.dumps(params), "queued", time.ctime()))
    con.commit()
    return {"tarea_id": tid, "estado": "queued", "dificultad": dif}

class RegistroIn(BaseModel):
    nombre: str
    email: str
    gpu: str = ""
    tops: str = ""
    ceder_potencia: str = ""
    ref: str = ""

@app.post("/registro")
def registro(r: RegistroIn):
    """Registro publico de la lista de espera (desde la web)."""
    con.execute("INSERT INTO registros(nombre,email,gpu,tops,ceder,ip,ts) VALUES(?,?,?,?,?,?,?)",
                (r.nombre, r.email, r.gpu, r.tops, r.ceder_potencia, r.ref, time.ctime()))
    con.commit()
    return {"ok": True, "mensaje": "Registrado. Te avisaremos al lanzamiento.", "posicion": con.execute("SELECT COUNT(*) c FROM registros").fetchone()["c"]}

@app.get("/registros")
def registros(_ok: None = Depends(exigir_admin)):
    return [dict(x) for x in con.execute("SELECT * FROM registros ORDER BY id DESC LIMIT 500").fetchall()]

@app.get("/saldo/{direccion}")
def saldo(direccion: str):
    """Saldo publico de una direccion (cualquiera puede consultarlo)."""
    s = con.execute("SELECT COALESCE(SUM(kuki),0) as s FROM ledger WHERE nodo_id=?",
                    (direccion,)).fetchone()["s"]
    # tambien sumar pagos recibidos como nodo minero
    return {"direccion": direccion, "saldo": s}

@app.get("/saldos")
def saldos():
    """Mapa publico direccion -> saldo (solo lectura, agregados del ledger).

    Permite a la web mostrar saldos sin exponer el Core: el exportador
    guarda este mapa en un JSON estatico. No incluye emails, claves ni
    datos personales (el ledger solo guarda ids y cantidades).
    """
    filas = con.execute("SELECT nodo_id, COALESCE(SUM(kuki),0) as s FROM ledger "
                        "GROUP BY nodo_id").fetchall()
    return {"saldos": {r["nodo_id"]: r["s"] for r in filas},
            "direcciones": len(filas)}

class TransferIn(BaseModel):
    de_nodo: str
    a_direccion: str
    monto: float
    nonce: str = ""
    firma: str = ""

@app.post("/transferir")
def transferir(t: TransferIn):
    """Transfiere KIA entre direcciones con firma Ed25519 y anti-repeticion.

    La firma es sobre "transferir:de:a:monto:nonce" con la clave registrada
    del nodo emisor. El nonce solo vale una vez (tabla transferencias con
    UNIQUE). Todo ocurre en una transaccion: sin saldo no hay debito ni
    credito (anti-doble-gasto). No crea moneda: debito y credito se compensan.
    """
    if not t.a_direccion or len(t.a_direccion) > 128:
        raise HTTPException(400, "destino invalido")
    if t.a_direccion == t.de_nodo:
        raise HTTPException(400, "origen y destino iguales")
    try:
        monto = round(float(t.monto), 4)
    except Exception:
        raise HTTPException(400, "monto invalido")
    if monto <= 0:
        raise HTTPException(400, "monto debe ser positivo")
    if not t.nonce or len(t.nonce) > 64:
        raise HTTPException(400, "nonce invalido")
    with _pago_lock:
        try:
            con.execute("BEGIN IMMEDIATE")
        except Exception:
            raise HTTPException(503, "Core ocupado, reintentar")
        try:
            con.execute("CREATE TABLE IF NOT EXISTS transferencias(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                        "de_nodo TEXT, a_direccion TEXT, monto REAL, nonce TEXT, ts TEXT, "
                        "UNIQUE(de_nodo, nonce))")
            node = con.execute("SELECT clave_publica FROM nodos WHERE id=?", (t.de_nodo,)).fetchone()
            pub = (node["clave_publica"] if node else "") or ""
            if not pub:
                con.execute("ROLLBACK")
                raise HTTPException(401, "emisor sin clave registrada")
            if not t.firma:
                con.execute("ROLLBACK")
                raise HTTPException(401, "falta firma")
            try:
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
                Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(
                    bytes.fromhex(t.firma),
                    f"transferir:{t.de_nodo}:{t.a_direccion}:{monto}:{t.nonce}".encode())
            except Exception:
                con.execute("ROLLBACK")
                raise HTTPException(401, "firma invalida")
            saldo = float(con.execute("SELECT COALESCE(SUM(kuki),0) s FROM ledger WHERE nodo_id=?",
                                      (t.de_nodo,)).fetchone()["s"] or 0.0)
            if saldo < monto:
                con.execute("ROLLBACK")
                raise HTTPException(409, "saldo insuficiente")
            try:
                con.execute("INSERT INTO transferencias(de_nodo,a_direccion,monto,nonce,ts) VALUES(?,?,?,?,?)",
                            (t.de_nodo, t.a_direccion, monto, t.nonce, time.ctime()))
            except Exception:
                con.execute("ROLLBACK")
                raise HTTPException(409, "nonce ya usado (repeticion bloqueada)")
            ts = time.ctime()
            con.execute("INSERT INTO ledger(tarea_id,nodo_id,kuki,motivo,ts) VALUES(?,?,?,?,?)",
                        (f"tx-{t.nonce[:12]}", t.de_nodo, -monto, "transferencia salida", ts))
            con.execute("INSERT INTO ledger(tarea_id,nodo_id,kuki,motivo,ts) VALUES(?,?,?,?,?)",
                        (f"tx-{t.nonce[:12]}", t.a_direccion, monto, "transferencia entrada", ts))
            con.commit()
            return {"ok": True, "de": t.de_nodo, "a": t.a_direccion, "monto": monto}
        except HTTPException:
            raise
        except Exception:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
            raise HTTPException(500, "error interno en transferencia")

@app.get("/red/telemetria")
def red_telemetria():
    """Ultima telemetria por nodo/device (solo lectura, para el panel).
    Cada fila lleva fecha (ts) y origen. La energia es ESTIMADA."""
    try:
        filas = con.execute("SELECT nodo_id,device,metricas,ts,origen FROM gpu_telemetria "
                            "ORDER BY id DESC LIMIT 200").fetchall()
        return {"telemetria": [dict(r) for r in filas]}
    except Exception:
        return {"telemetria": []}

# el coordinador corre: uvicorn kukia_core:app --port 8300
