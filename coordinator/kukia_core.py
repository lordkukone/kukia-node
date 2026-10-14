"""KUKIA Core - coordinador: nodos, tareas, verificacion REAL, ledger (SQLite).

VERIFICACION (corregida): el Core NO confia en el hash del minero.
El Core recalcula la tarea el mismo (trustless) y compara el resultado
exacto. Tareas con aritmetica determinista (enteros exactos via float64
con magnitudes acotadas) para que CPU y GPU den el MISMO numero.
"""
import hashlib, json, secrets, sqlite3, uuid, time
from pathlib import Path
import numpy as np
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel

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

app = FastAPI(title="KUKIA Core", version="0.2.0")

class NodoIn(BaseModel):
    nombre: str
    vram_gb: int
    ref: str = ""
    tops: float = 0.0    # potencia IA declarada (TOPS, valor oficial de la GPU)

class TareaIn(BaseModel):
    tipo: str      # 'checksum' | 'matriz' | 'inferencia'
    params: dict = {}

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
    con.execute("INSERT INTO nodos(id,nombre,vram_gb,tier,reputacion,ref,creado,tops) VALUES(?,?,?,?,?,?,?,?)",
                (nid, n.nombre, n.vram_gb, tier(n.vram_gb), 1.0, n.ref, time.ctime(), n.tops))
    con.commit()
    return {"id": nid, "tier": tier(n.vram_gb), "max_supply": MAX_SUPPLY, "tops_registrados": n.tops}

@app.get("/nodos")
def nodos():
    return [dict(r) for r in con.execute("SELECT * FROM nodos").fetchall()]

@app.post("/tareas/crear")
def crear_tarea(t: TareaIn, _ok: None = Depends(exigir_admin)):
    tid = uuid.uuid4().hex[:12]
    # los params llevan seed -> la tarea es reproducible/verificable
    con.execute("INSERT INTO tareas(id,tipo,params,estado,creado) VALUES(?,?,?,?,?)",
                (tid, t.tipo, json.dumps(t.params), "queued", time.ctime()))
    con.commit()
    return {"tarea_id": tid, "estado": "queued"}

@app.get("/tareas/pendiente")
def pendiente(nodo_id: str):
    row = con.execute("SELECT id,tipo,params FROM tareas WHERE estado='queued' "
                      "ORDER BY creado LIMIT 1").fetchone()
    if not row:
        return {"tarea": None}
    con.execute("UPDATE tareas SET estado='ejecutando', nodo_id=? WHERE id=?", (nodo_id, row["id"]))
    con.commit()
    return {"tarea": {"id": row["id"], "tipo": row["tipo"], "params": json.loads(row["params"])}}

@app.post("/tareas/resultado")
def resultado(tid: str, nodo_id: str, metrica: str, hash_res: str = ""):
    """El Core recalcula la tarea y compara el RESULTADO EXACTO (trustless)."""
    task = con.execute("SELECT * FROM tareas WHERE id=?", (tid,)).fetchone()
    if not task:
        raise HTTPException(404, "tarea no encontrada")
    if task["estado"] == "ok":
        raise HTTPException(409, "tarea ya pagada (doble pago bloqueado)")
    if task["nodo_id"] and task["nodo_id"] != nodo_id:
        raise HTTPException(409, "tarea asignada a otro nodo")
    params = json.loads(task["params"])
    esperado = computar_tarea(task["tipo"], params)
    if esperado is None:
        raise HTTPException(400, "tipo de tarea no soportado")
    ok = str(metrica).strip() == str(esperado).strip()
    kuki = 0.0
    info_pago = {}
    if ok:
        node = con.execute("SELECT * FROM nodos WHERE id=?", (nodo_id,)).fetchone()
        if not node:
            raise HTTPException(404, "nodo no registrado")
        kuki, info_pago = recompensa_pago(node["tier"])
        con.execute("UPDATE nodos SET tareas_ok=tareas_ok+1, reputacion=min(5,reputacion+0.05) "
                    "WHERE id=?", (nodo_id,))
        h = hashlib.sha256(f"{tid}:{nodo_id}:{metrica}".encode()).hexdigest()
        con.execute("UPDATE tareas SET estado='ok', hash=?, metrica=?, fin=?, verificado=1 WHERE id=?",
                    (h, metrica, time.ctime(), tid))
        con.execute("INSERT INTO ledger(tarea_id,nodo_id,kuki,motivo,ts) VALUES(?,?,?,?,?)",
                    (tid, nodo_id, kuki, "trabajo verificado", time.ctime()))
    else:
        con.execute("UPDATE tareas SET estado='fallo', fin=? WHERE id=?", (time.ctime(), tid))
        con.execute("INSERT INTO ledger(tarea_id,nodo_id,kuki,motivo,ts) VALUES(?,?,?,?,?)",
                    (tid, nodo_id, 0.0, "resultado incorrecto (recalculado por el Core)", time.ctime()))
    con.commit()
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

# el coordinador corre: uvicorn kukia_core:app --port 8300
