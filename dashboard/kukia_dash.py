"""KUKIA Dashboard - estado de la red en vivo (heartbeat, mejora 7)."""
import sqlite3
from pathlib import Path

DB = Path(__file__).parent.parent / "coordinator" / "ledger" / "kukia.db"

HTML = """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<title>KUKIA Network - Dashboard</title>
<style>
body{font-family:Segoe UI,sans-serif;background:#0a1f44;color:#fff;margin:0;padding:2rem}
h1{color:#d4af37}h2{color:#9db6dd}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem;margin:1rem 0}
.card{background:#12305e;border-radius:12px;padding:1rem}
.card .num{font-size:2rem;color:#d4af37;font-weight:bold}
table{width:100%;border-collapse:collapse;margin-top:1rem}
th,td{border:1px solid #2c4a7a;padding:.5rem;text-align:left;font-size:.9rem}
.ok{color:#5ee08a}.pend{color:#e0c05e}
.foot{margin-top:2rem;color:#789;font-size:.8rem}
</style></head><body>
<h1>KUKIA <span style="color:#d4af37">&#10085;</span> Network Dashboard</h1>
<p>Every GPU. One intelligence network.</p>
<div class="grid">
  <div class="card"><div class="num">__SUPPLY__</div>KUKI emitidos (verificados)</div>
  <div class="card"><div class="num">__MAX__</div>Suministro maximo (100M)</div>
  <div class="card"><div class="num">__NODOS__</div>Nodos conectados</div>
  <div class="card"><div class="num">__TAREAS_OK__</div>Tareas verificadas</div>
  <div class="card"><div class="num">__PEND__</div>Tareas en cola</div>
</div>
<h2>Nodos</h2>
<section id="nodes"></section>
<h2>Ledger (ultimas 100)</h2>
<section id="ledger"></section>
<div class="foot">KUKIA es un prototipo local. La moneda NO tiene precio hasta que la red demuestre movimiento real sostenido. Ver whitepaper.</div>
<script>
async function load(){
  const s=await fetch('/api/stats');const st=await s.json();
  document.getElementById('nodes').innerHTML=st.nodes.join('');
  document.getElementById('ledger').innerHTML=st.ledger.join('');
}
load();setInterval(load,5000);
</script></body></html>"""

import json
from fastapi import FastAPI, Response

app = FastAPI()

def q(sql, *a):
    con = sqlite3.connect(str(DB)); con.row_factory = sqlite3.Row
    r = con.execute(sql, *a).fetchall(); con.close(); return r

def nodo_html(r):
    return f'<table><tr><td>{r["nombre"]}</td><td>{r["vram_gb"]}GB</td>' \
           f'<td>tier {r["tier"]:.1f}</td><td class="ok">{r["tareas_ok"]} tareas</td>' \
           f'<td>reputacion {r["reputacion"]:.2f}</td></tr></table>'

@app.get("/")
def index():
    return Response(HTML, media_type="text/html")

@app.get("/api/stats")
def api_stats():
    s = q("SELECT COALESCE(SUM(kuki),0) su FROM ledger")[0]["su"]
    nodes = q("SELECT * FROM nodos")
    led = q("SELECT * FROM ledger ORDER BY id DESC LIMIT 100")
    html = HTML
    html = html.replace("__SUPPLY__", f"{s:,.0f}")
    html = html.replace("__MAX__", "100,000,000")
    html = html.replace("__NODOS__", str(len(nodes)))
    html = html.replace("__TAREAS_OK__", str(q("SELECT COUNT(*) c FROM tareas WHERE estado='ok'")[0]["c"]))
    html = html.replace("__PEND__", str(q("SELECT COUNT(*) c FROM tareas WHERE estado='queued'")[0]["c"]))
    return {"nodes": [nodo_html(n) for n in nodes],
            "ledger": ["".join([f"<tr><td>{l['tarea_id'][:8]}</td><td>{l['nodo_id'][:8]}</td>",
                                f"<td class=ok>+{l['kuki']:.1f}</td><td>{l['motivo']}</td><td>{l['ts']}</td></tr>"]) for l in led]}

# corre: uvicorn kukia_dash:app --port 8301