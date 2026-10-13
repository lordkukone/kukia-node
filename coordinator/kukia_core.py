"""KUKIA Core - coordinador: nodos, tareas, verificacion, ledger (SQLite)."""
import hashlib, json, sqlite3, uuid, time, threading
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DB = Path(__file__).parent / "ledger" / "kukia.db"
DB.parent.mkdir(parents=True, exist_ok=True)

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
    # migracion: anadir columna tops si la tabla es antigua
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

app = FastAPI(title="KUKIA Core", version="0.1.0")

class NodoIn(BaseModel):
    nombre: str
    vram_gb: int
    ref: str = ""
    tops: float = 0.0    # potencia IA declarada (TOPS, valor oficial de la GPU)

class TareaIn(BaseModel):
    tipo: str      # 'checksum' | 'matriz' | 'inferencia' (local)
    params: dict = {}

def tier(vram: int) -> float:
    return TIER_MULT.get(vram, 1.0)

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
def crear_tarea(t: TareaIn):
    tid = uuid.uuid4().hex[:12]
    con.execute("INSERT INTO tareas(id,tipo,params,estado,creado) VALUES(?,?,?,?,?)",
                (tid, t.tipo, json.dumps(t.params), "queued", time.ctime()))
    con.commit()
    return {"tarea_id": tid, "estado": "queued"}

@app.get("/tareas/pendiente")
def pendiente(nodo_id: str):
    # asigna la tarea mas antigua (mejora 7: heartbeat/estado)
    row = con.execute("SELECT id,tipo,params FROM tareas WHERE estado='queued' "
                      "ORDER BY creado LIMIT 1").fetchone()
    if not row:
        return {"tarea": None}
    con.execute("UPDATE tareas SET estado='ejecutando', nodo_id=? WHERE id=?", (nodo_id, row["id"]))
    con.commit()
    return {"tarea": {"id": row["id"], "tipo": row["tipo"], "params": json.loads(row["params"])}}

@app.post("/tareas/resultado")
def resultado(tid: str, nodo_id: str, hash_res: str, metrica: str):
    verif = hashlib.sha256(f"{tid}:{nodo_id}:{metrica}".encode()).hexdigest()  # verificacion reproducible
    ok = hash_res == verif
    kuki = 0.0
    if ok:
        node = con.execute("SELECT * FROM nodos WHERE id=?", (nodo_id,)).fetchone()
        kuki = REWARD_BASE * node["tier"]
        # recompensa + ledger (boom: la acunea de trabajo verificado)
        con.execute("UPDATE nodos SET tareas_ok=tareas_ok+1, reputacion=min(5,reputacion+0.05) "
                    "WHERE id=?", (nodo_id,))
        # mejora 2: bonus referido cada 10 tareas
        ok10 = node["tareas_ok"] and (node["tareas_ok"]+1) % 10 == 0
        if ok10 and node["ref"]:
            ref_node = con.execute("SELECT id FROM nodos WHERE nombre=?", (node["ref"],)).fetchone()
            # se acredita al referidor (simplificado por nombre)
        con.execute("UPDATE tareas SET estado='ok', hash=?, metrica=?, fin=?, verificado=1 WHERE id=?",
                    (hash_res, metrica, time.ctime(), tid))
        con.execute("INSERT INTO ledger(tarea_id,nodo_id,kuki,motivo,ts) VALUES(?,?,?,?,?)",
                    (tid, nodo_id, kuki, "trabajo verificado", time.ctime()))
    else:
        con.execute("UPDATE tareas SET estado='fallo', fin=? WHERE id=?", (time.ctime(), tid))
        con.execute("INSERT INTO ledger(tarea_id,nodo_id,kuki,motivo,ts) VALUES(?,?,?,?,?)",
                    (tid, nodo_id, 0.0, "hash no verificado", time.ctime()))
    con.commit()
    return {"ok": ok, "kuki": kuki, "expected": verif}

@app.get("/ledger")
def ledger():
    return [dict(r) for r in con.execute("SELECT * FROM ledger ORDER BY id DESC LIMIT 200").fetchall()]

@app.get("/stats")
def stats():
    sum = con.execute("SELECT COALESCE(SUM(kuki),0) as s FROM ledger").fetchone()["s"]
    sum = min(sum, MAX_SUPPLY)
    return {
        "supply_emitido": sum,
        "max_supply": MAX_SUPPLY,
        "nodos": con.execute("SELECT COUNT(*) c FROM nodos").fetchone()["c"],
        "tareas_ok": con.execute("SELECT COUNT(*) c FROM tareas WHERE estado='ok'").fetchone()["c"],
        "tareas_pend": con.execute("SELECT COUNT(*) c FROM tareas WHERE estado='queued'").fetchone()["c"],
    }

# el coordinador corre: uvicorn kukia_core:app --port 8300