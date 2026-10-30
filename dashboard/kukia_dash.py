"""KUKIA Dashboard - sala de operaciones de la red (puerto 8301).

Lee del Core (API), nunca de la base de datos: el Core es el unico escritor y
abrir una segunda conexion solo provoca bloqueos. Si el Core no responde, el
panel sigue mostrando los ultimos datos y avisa en rojo.

Graficas medidas, no maquilladas:
  - emision acumulada      -> /stats + /red/serie (ledger)
  - tareas por minuto      -> tareas verificadas
  - GFLOPS                 -> FLOPs de cada tarea / segundos reales de GPU
  - nodos y ultimos asientos

Arranque:
    cd dashboard && python -m uvicorn kukia_dash:app --port 8301
    (o ARRANCAR_DASHBOARD.cmd)

Variables:
    KUKIA_CORE   URL del Core (defecto http://127.0.0.1:8300)
"""
import json
import os
import threading
import time
from pathlib import Path

import requests
from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

CORE = (os.environ.get("KUKIA_CORE") or "http://127.0.0.1:8300").rstrip("/")
TIMEOUT = max(1.0, float(os.environ.get("KUKIA_DASH_TIMEOUT", "4")))
CACHE_SEG = max(0.5, float(os.environ.get("KUKIA_DASH_CACHE", "2")))
CACHE_INTEGRIDAD = max(5.0, float(os.environ.get("KUKIA_DASH_INTEGRIDAD", "60")))

app = FastAPI(title="KUKIA Dashboard", version="1.0.0")
_sesion = requests.Session()
_lock = threading.Lock()
_cache = {"ts": 0.0, "datos": None}
_int_cache = {"ts": 0.0, "datos": None}
_ultimo_bueno = {"ts": 0.0, "datos": None}


def _get(ruta: str, defecto=None, timeout: float = None):
    try:
        r = _sesion.get(f"{CORE}{ruta}", timeout=timeout or TIMEOUT)
        if r.status_code != 200:
            return defecto
        return r.json()
    except Exception:
        return defecto


def _integridad():
    """La verificacion completa de la cadena es cara: se cachea 60 s."""
    ahora = time.time()
    if _int_cache["datos"] is not None and ahora - _int_cache["ts"] < CACHE_INTEGRIDAD:
        return _int_cache["datos"]
    d = _get("/ledger/integridad", None, timeout=max(TIMEOUT, 15.0))
    if d is not None:
        _int_cache.update({"ts": ahora, "datos": d})
    return d if d is not None else _int_cache["datos"] or {}


def _potencia_real(telemetria, nodos, serie) -> dict:
    """Potencia REAL de las GPUs: lo que dio el benchmark del propio minero.

    Hasta ahora la unica cifra de potencia del panel salia de dividir los FLOPs
    entre los milisegundos que el minero DECIA haber tardado (`duracion_ms`),
    un dato que el minero puede escribir a mano. El minero ya mide su GPU al
    arrancar con las mismas cuentas que las tareas (tools/benchmark_gpu.py) y
    manda el resultado en la telemetria (`gflops_base`): esto lo muestra tal
    cual, por GPU, y deja al lado lo que el Core ha verificado.
    """
    por_gpu = {}
    for t in telemetria or []:
        try:
            m = json.loads(t.get("metricas") or "{}")
        except Exception:
            continue
        clave = (str(t.get("nodo_id") or ""), str(t.get("device") or ""))
        if clave in por_gpu:
            continue            # la primera es la mas reciente
        try:
            gf = float(m.get("gflops_base") or 0.0)
        except Exception:
            gf = 0.0
        if gf <= 0:
            continue
        por_gpu[clave] = {
            "nodo_id": clave[0], "device": clave[1], "gflops": round(gf, 2),
            "vram_gb": m.get("vram_gb"),
            "tarea_ms": m.get("tarea_ms"),
        }
    gpus = sorted(por_gpu.values(), key=lambda g: -g["gflops"])
    # Lo que el Core ha podido comprobar: FLOPs de tareas verificadas / tiempo
    # que el propio Core cronometro. Incluye la espera de la red, asi que es
    # SIEMPRE menor que el benchmark: no son lo mismo y no se mezclan.
    verificado = float((serie or {}).get("gflops_ventana") or 0.0)
    return {
        "gflops_medidos": round(sum(g["gflops"] for g in gpus), 2) if gpus else None,
        "gflops_verificados": round(verificado, 2),
        "gpus": gpus,
        "fuente": "benchmark del minero al arrancar (mismas cuentas que las tareas)",
    }


def _resumen():
    ahora = time.time()
    with _lock:
        if _cache["datos"] is not None and ahora - _cache["ts"] < CACHE_SEG:
            return _cache["datos"]
        errores = []
        stats = _get("/stats", None)
        if stats is None:
            errores.append("Core no responde en /stats")
            datos = {"ok": False, "ts": int(time.time()), "core": CORE,
                     "errores": errores,
                     "ultimo_bueno": _ultimo_bueno["ts"] or None}
            if _ultimo_bueno["datos"]:
                datos.update({"stats": _ultimo_bueno["datos"].get("stats", {}),
                              "nodos": _ultimo_bueno["datos"].get("nodos", []),
                              "ledger": _ultimo_bueno["datos"].get("ledger", []),
                              "serie": _ultimo_bueno["datos"].get("serie", {}),
                              "serie_gpu": _ultimo_bueno["datos"].get("serie_gpu", {}),
                              "integridad": _ultimo_bueno["datos"].get("integridad", {}),
                              "telemetria": _ultimo_bueno["datos"].get("telemetria", []),
                              "potencia": _ultimo_bueno["datos"].get("potencia", {}),
                              "validadores": _ultimo_bueno["datos"].get("validadores", {})})
            _cache.update({"ts": ahora, "datos": datos})
            return datos

        # La lista esta acotada en el Core; el total real viene de /stats.
        nodos = _get("/nodos?limite=500", []) or []
        serie = _get("/red/serie?minutos=60", {}) or {}
        # Potencia y latencia MEDIDAS en cada GPU (no la media del Core): es lo
        # que el minero manda en cada tarea.
        serie_gpu = _get("/red/serie_gpu?minutos=60", {}) or {}
        integridad = _integridad()
        led = _get("/ledger", []) or []
        tele = _get("/red/telemetria", {}) or {}
        validadores = _get("/validador/estado", {}) or {}
        datos = {
            "ok": True,
            "ts": int(time.time()),
            "core": CORE,
            "stats": stats,
            "nodos": nodos,
            "serie": serie,
            "serie_gpu": serie_gpu,
            "integridad": integridad,
            "ledger": led[:80],
            "telemetria": tele.get("telemetria", [])[:120],
            "potencia": _potencia_real(tele.get("telemetria", [])[:120], nodos, serie),
            "validadores": validadores,
            "errores": errores,
        }
        _ultimo_bueno.update({"ts": ahora, "datos": datos})
        _cache.update({"ts": ahora, "datos": datos})
        return datos


@app.get("/api/resumen")
def api_resumen():
    return JSONResponse(_resumen())


@app.get("/")
def index():
    html = (Path(__file__).parent / "panel.html")
    if html.exists():
        return Response(html.read_text(encoding="utf-8"), media_type="text/html")
    return Response(HTML, media_type="text/html")


HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- AUDITORIA 2026-10-20: el panel solo habla con su propio origen (Core
     mediante /api/resumen). La CSP evita que un fallo de escapado pueda
     cargar scripts o mandar datos a un dominio externo. -->
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'">
<title>KUKIA · Network Operations</title>
<style>
:root{
  --bg:#050d1c; --bg2:#081427; --panel:#0b1a31; --panel2:#0e2140;
  --line:#1b355c; --gold:#d4af37; --gold2:#f2d675; --green:#35e08a;
  --red:#ff6b6b; --amber:#ffb84d; --text:#d3e0f5; --muted:#7f96bb;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;background:
  radial-gradient(1200px 600px at 50% -10%,#12294c 0%,var(--bg) 55%);
  color:var(--text);min-height:100vh;padding:1.2rem}
.wrap{max-width:1380px;margin:0 auto}
header{display:flex;align-items:center;justify-content:space-between;gap:1rem;
  flex-wrap:wrap;padding:.4rem 0 1rem;border-bottom:1px solid var(--line);margin-bottom:1.2rem}
.brand{display:flex;align-items:baseline;gap:.8rem}
.brand h1{color:var(--gold);letter-spacing:.18em;font-size:1.55rem;font-weight:800}
.brand span{color:var(--muted);font-size:.68rem;letter-spacing:.28em;text-transform:uppercase}
.live{display:flex;align-items:center;gap:.7rem;font-size:.78rem;color:var(--muted);flex-wrap:wrap}
.dot{width:9px;height:9px;border-radius:50%;background:var(--green);
  box-shadow:0 0 10px var(--green);display:inline-block}
.dot.off{background:var(--red);box-shadow:0 0 10px var(--red)}
.pill{border:1px solid var(--line);border-radius:999px;padding:.32rem .7rem;
  background:rgba(11,26,49,.8);font-variant-numeric:tabular-nums}
.grid{display:grid;grid-template-columns:repeat(6,1fr);gap:.9rem}
@media (max-width:1150px){.grid{grid-template-columns:repeat(3,1fr)}}
@media (max-width:640px){.grid{grid-template-columns:repeat(2,1fr)}}
.card{background:linear-gradient(160deg,var(--panel2),var(--panel));
  border:1px solid var(--line);border-radius:14px;padding:1rem 1.05rem;
  box-shadow:0 10px 30px rgba(0,0,0,.28)}
.k{font-size:.66rem;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
.v{font-size:1.55rem;font-weight:800;color:#fff;margin:.30rem 0 .16rem;
  font-variant-numeric:tabular-nums;line-height:1.15}
.v.gold{color:var(--gold)}
.sub{font-size:.72rem;color:var(--muted)}
.bar{height:5px;border-radius:4px;background:#122843;overflow:hidden;margin-top:.55rem}
.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--gold),var(--gold2));width:0}
.charts{display:grid;grid-template-columns:1fr 1fr;gap:.9rem;margin-top:1rem}
@media (max-width:900px){.charts{grid-template-columns:1fr}}
.chart{padding:.9rem 1rem .6rem}
.chart .cab{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:.35rem}
.chart .cab b{font-size:.78rem;letter-spacing:.06em;color:var(--text)}
.chart .cab span{font-size:.72rem;color:var(--muted);font-variant-numeric:tabular-nums}
.chart canvas{width:100%;height:150px;display:block}
.wide{grid-column:1/-1}
.two{display:grid;grid-template-columns:1.25fr 1fr;gap:.9rem;margin-top:1rem}
@media (max-width:980px){.two{grid-template-columns:1fr}}
h2{font-size:.72rem;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);
  margin-bottom:.7rem;display:flex;justify-content:space-between;align-items:center}
table{width:100%;border-collapse:collapse;font-size:.78rem}
th{color:var(--muted);font-weight:600;text-align:left;padding:.45rem .4rem;
  border-bottom:1px solid var(--line);font-size:.66rem;letter-spacing:.08em;text-transform:uppercase}
td{padding:.45rem .4rem;border-bottom:1px solid #12294a;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
.mono{font-family:'Cascadia Code',Consolas,monospace;font-size:.72rem}
.green{color:var(--green)}.red{color:var(--red)}.amber{color:var(--amber)}
.muted{color:var(--muted)}
.tag{display:inline-block;padding:.1rem .45rem;border-radius:6px;font-size:.62rem;
  border:1px solid var(--line);background:#0a1a30;color:var(--muted)}
.tag.on{color:var(--green);border-color:#1d5c41;background:#09251a}
.tag.off{color:var(--amber);border-color:#5c4a1d;background:#241d09}
.seg{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:.9rem;margin-top:1rem}
@media (max-width:900px){.seg{grid-template-columns:repeat(2,1fr)}}
.seg .v{font-size:1.05rem}
.hash{word-break:break-all;line-height:1.35}
.banner{display:none;margin:0 0 1rem;padding:.7rem 1rem;border-radius:10px;
  border:1px solid #5c1d1d;background:#2a0f0f;color:#ffb3b3;font-size:.82rem}
.banner.on{display:block}
footer{margin:1.4rem 0 .4rem;color:var(--muted);font-size:.7rem;line-height:1.6;
  border-top:1px solid var(--line);padding-top:.8rem}
.scroll{max-height:330px;overflow:auto}
.scroll::-webkit-scrollbar{width:7px;height:7px}
.scroll::-webkit-scrollbar-thumb{background:#1e3c66;border-radius:6px}
.gpus{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.6rem;margin-top:.6rem}
.gpu{border:1px solid var(--line);border-radius:10px;padding:.6rem .7rem;background:#09182e;font-size:.74rem}
.gpu b{color:var(--gold);display:block;font-size:.78rem;margin-bottom:.15rem}
.gpu .util{display:flex;align-items:center;gap:.45rem;margin-top:.4rem}
.gpu .bar{flex:1;height:5px;border-radius:4px;background:#122a4a;overflow:hidden}
.gpu .bar span{display:block;height:100%;background:linear-gradient(90deg,#35e08a,#d4af37)}
.gpu .pct{font-weight:700;color:#eaf2ff;font-variant-numeric:tabular-nums}
.gpu .sub{color:var(--muted);font-size:.68rem;margin-top:.2rem;display:block}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="brand"><h1>KUKIA</h1><span>Network Operations</span></div>
  <div class="live">
    <span class="pill"><span id="dot" class="dot off"></span> <span id="estado">Conectando…</span></span>
    <span class="pill" id="reloj">—</span>
    <span class="pill">Core <span class="mono" id="coreUrl">—</span></span>
  </div>
</header>

<div id="banner" class="banner"></div>

<div class="grid">
  <div class="card">
    <div class="k">Emitido</div>
    <div class="v gold" id="kSupply">—</div>
    <div class="sub" id="kSupplySub">de — KIA</div>
    <div class="bar"><i id="kSupplyBar"></i></div>
  </div>
  <div class="card">
    <div class="k">Tareas verificadas</div>
    <div class="v" id="kTasks">—</div>
    <div class="sub" id="kTasksSub">— por minuto</div>
    <!-- Desglose de lo archivado: el total mezclaba 'ok' y 'fallo' y la suma
         no cuadraba. Aqui se ve de donde sale cada numero. -->
    <div class="sub" id="kTasksArch">— en tabla · — archivadas (— ok · — fallo)</div>
  </div>
  <div class="card">
    <div class="k">Nodos activos</div>
    <div class="v" id="kNodes">—</div>
    <div class="sub" id="kNodesSub">— registrados</div>
  </div>
  <div class="card">
    <div class="k">Cola de trabajo</div>
    <div class="v" id="kQueue">—</div>
    <div class="sub">tareas esperando GPU</div>
  </div>
  <div class="card">
    <div class="k">Pago por tarea</div>
    <div class="v" id="kPay">—</div>
    <div class="sub" id="kPaySub">KIA · comisión 0</div>
  </div>
  <div class="card">
    <div class="k">Potencia medida</div>
    <div class="v" id="kFlops">—</div>
    <div class="sub" id="kFlopsSub">GFLOPS medidos en la GPU</div>
  </div>
</div>

<div class="charts">
  <div class="card chart">
    <div class="cab"><b>Emisión acumulada</b><span id="cSupplyInfo">últimos 60 min</span></div>
    <canvas id="cSupply"></canvas>
  </div>
  <div class="card chart">
    <div class="cab"><b>Tareas verificadas / minuto</b><span id="cRateInfo">—</span></div>
    <canvas id="cRate"></canvas>
  </div>
  <div class="card chart wide">
    <div class="cab"><b>Potencia medida (GFLOPS)</b>
      <span id="cFlopsInfo">FLOPs de tareas verificadas ÷ segundos reales</span></div>
    <canvas id="cFlops"></canvas>
  </div>
  <div class="card chart">
    <div class="cab"><b>GFLOPS por GPU</b>
      <span id="cGpuFlopsInfo">medidos en la tarjeta, por tarea</span></div>
    <canvas id="cGpuFlops"></canvas>
  </div>
  <div class="card chart">
    <div class="cab"><b>Latencia por GPU</b>
      <span id="cGpuMsInfo">ms por tarea (medidos en la GPU)</span></div>
    <canvas id="cGpuMs"></canvas>
  </div>
  <div class="card chart wide">
    <div class="cab"><b>Mezcla de trabajo real</b>
      <span id="cTiposInfo">tareas ejecutadas en la ventana</span></div>
    <div id="tiposMix" style="display:flex;flex-wrap:wrap;gap:10px;padding:10px 2px"></div>
  </div>
</div>

<div class="seg">
  <div class="card">
    <div class="k">Cadena del ledger</div>
    <div class="v" id="sChain">—</div>
    <div class="sub" id="sChainSub">—</div>
  </div>
  <div class="card">
    <div class="k">Sellos firmados</div>
    <div class="v" id="sSeals">—</div>
    <div class="sub" id="sSealsSub">Ed25519</div>
  </div>
  <div class="card">
    <div class="k">Pagos</div>
    <div class="v" id="sPay">—</div>
    <div class="sub" id="sPaySub">—</div>
  </div>
  <div class="card">
    <div class="k">Último sello</div>
    <div class="v mono hash" id="sHash" style="font-size:.7rem">—</div>
    <div class="sub" id="sHashSub">—</div>
  </div>
  <div class="card">
    <div class="k">Validadores independientes</div>
    <div class="v" id="sVals">—</div>
    <div class="sub" id="sValsSub">—</div>
  </div>
</div>

<div class="two">
  <div class="card">
    <h2>Nodos <span class="muted" id="nodesInfo">—</span></h2>
    <div class="scroll">
      <table>
        <thead><tr><th>Nodo</th><th>VRAM</th><th title="FLOPs de sus tareas verificadas ÷ tiempo cronometrado por el Core (incluye la red: siempre menor que el benchmark de la GPU)">GFLOPS Core</th><th>Tareas</th><th>Rep.</th><th>Visto</th></tr></thead>
        <tbody id="nodesBody"><tr><td colspan="6" class="muted">Cargando…</td></tr></tbody>
      </table>
    </div>
    <div class="gpus" id="gpus"></div>
  </div>
  <div class="card">
    <h2>Últimos asientos <span class="muted">ledger en vivo</span></h2>
    <div class="scroll">
      <table>
        <thead><tr><th>Hora</th><th>Cuenta</th><th>KIA</th><th>Motivo</th></tr></thead>
        <tbody id="ledgerBody"><tr><td colspan="4" class="muted">Cargando…</td></tr></tbody>
      </table>
    </div>
  </div>
</div>

<footer>
  Panel de operaciones de KUKIA. Las gráficas se calculan con trabajo <b>verificado</b> por el Core
  (no con lo que declara el nodo). La emisión es fija hacia 2150 y el techo es 100.000.000 KIA.
  Si el Core no responde, este panel lo dice y muestra la última foto buena: nunca inventa datos.
</footer>
</div>

<script>
var ultimo = null;
var ultimoOk = 0;

function n(x, d){
  if(x === null || x === undefined || isNaN(Number(x))) return '—';
  return Number(x).toLocaleString('es-ES', {maximumFractionDigits: d === undefined ? 2 : d});
}
function corto(s, a, b){
  s = String(s || '');
  if(!s) return '—';
  return s.length > (a + b + 1) ? s.slice(0, a) + '…' + s.slice(-b) : s;
}
// AUDITORIA 2026-10-20: el nombre del nodo y su telemetria los manda quien se
// registra, y se pintaban con innerHTML. Un nombre como <img onerror=...>
// ejecutaba codigo en el panel del administrador (XSS almacenado). Todo texto
// que venga del Core pasa por aqui antes de entrar en el HTML.
function esc(v){
  return String(v === null || v === undefined ? '' : v).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}
function hace(ts){
  if(!ts) return '—';
  var s = Date.now() / 1000 - Number(ts);
  if(s < 0) s = 0;
  if(s < 60) return 'ahora';
  if(s < 3600) return Math.floor(s / 60) + ' min';
  if(s < 86400) return Math.floor(s / 3600) + ' h';
  return Math.floor(s / 86400) + ' d';
}
function setEstado(ok, txt){
  document.getElementById('dot').className = 'dot' + (ok ? '' : ' off');
  document.getElementById('estado').textContent = txt;
}

function lienzo(id){
  var cv = document.getElementById(id);
  var dpr = window.devicePixelRatio || 1;
  var w = cv.clientWidth || 400, h = cv.clientHeight || 150;
  cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
  var ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return {ctx: ctx, w: w, h: h};
}
function rejilla(ctx, w, h, min, max){
  ctx.strokeStyle = 'rgba(120,150,200,.13)';
  ctx.lineWidth = 1;
  for(var i = 0; i <= 3; i++){
    var y = 10 + (h - 26) * i / 3;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }
  ctx.fillStyle = 'rgba(150,175,215,.6)';
  ctx.font = '10px Segoe UI,system-ui,sans-serif';
  ctx.fillText(n(max, max >= 1000 ? 0 : 2), 4, 9);
  ctx.fillText(n(min, min >= 1000 ? 0 : 2), 4, h - 4);
}
function linea(id, valores, color){
  var c = lienzo(id), ctx = c.ctx, w = c.w, h = c.h;
  var vals = (valores || []).map(Number).filter(function(v){ return isFinite(v); });
  if(vals.length < 2){ ctx.fillStyle = 'rgba(150,175,215,.5)';
    ctx.font = '12px Segoe UI,system-ui,sans-serif'; ctx.fillText('Sin datos suficientes', 12, h / 2); return; }
  var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
  if(max === min){ max = min + 1; }
  rejilla(ctx, w, h, min, max);
  var pad = 10, alto = h - 26;
  function x(i){ return (i / (vals.length - 1)) * w; }
  function y(v){ return pad + alto - ((v - min) / (max - min)) * alto; }
  var grad = ctx.createLinearGradient(0, pad, 0, pad + alto);
  grad.addColorStop(0, color + '55'); grad.addColorStop(1, color + '00');
  ctx.beginPath(); ctx.moveTo(0, pad + alto);
  for(var i = 0; i < vals.length; i++){ ctx.lineTo(x(i), y(vals[i])); }
  ctx.lineTo(w, pad + alto); ctx.closePath();
  ctx.fillStyle = grad; ctx.fill();
  ctx.beginPath();
  for(i = 0; i < vals.length; i++){ if(i === 0) ctx.moveTo(x(i), y(vals[i])); else ctx.lineTo(x(i), y(vals[i])); }
  ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.lineJoin = 'round'; ctx.stroke();
  ctx.beginPath(); ctx.arc(x(vals.length - 1), y(vals[vals.length - 1]), 3, 0, 6.283);
  ctx.fillStyle = color; ctx.fill();
}
// Varias lineas en el mismo lienzo (una por GPU). Misma escala para todas:
// comparar tarjetas solo tiene sentido asi.
function lineas(id, series){
  var c = lienzo(id), ctx = c.ctx, w = c.w, h = c.h;
  series = (series || []).filter(function(s){ return (s.valores || []).length >= 2; });
  if(!series.length){
    ctx.fillStyle = 'rgba(150,175,215,.5)';
    ctx.font = '12px Segoe UI,system-ui,sans-serif';
    ctx.fillText('Sin datos del minero todavia', 12, h / 2);
    return;
  }
  var vals = [];
  series.forEach(function(s){ s.valores.forEach(function(v){
    if(v !== null && v !== undefined && isFinite(v)) vals.push(Number(v));
  }); });
  var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
  if(max === min){ max = min + 1; }
  rejilla(ctx, w, h, min, max);
  var pad = 10, alto = h - 26;
  var len = Math.max.apply(null, series.map(function(s){ return s.valores.length; }));
  function X(i){ return (i / (len - 1)) * w; }
  function Y(v){ return pad + alto - ((v - min) / (max - min)) * alto; }
  series.forEach(function(s){
    ctx.beginPath();
    ctx.strokeStyle = s.color; ctx.lineWidth = 2; ctx.lineJoin = 'round';
    var pen = false;
    for(var i = 0; i < s.valores.length; i++){
      var v = Number(s.valores[i]);
      if(!isFinite(v)){ pen = false; continue; }   // hueco: no hubo dato
      if(!pen){ ctx.moveTo(X(i), Y(v)); pen = true; }
      else { ctx.lineTo(X(i), Y(v)); }
    }
    ctx.stroke();
    for(var j = s.valores.length - 1; j >= 0; j--){
      var u = Number(s.valores[j]);
      if(!isFinite(u)) continue;
      ctx.beginPath(); ctx.arc(X(j), Y(u), 3, 0, 6.283);
      ctx.fillStyle = s.color; ctx.fill();
      break;
    }
  });
  var lx = 6, ly = h - 6;
  ctx.font = '10px Segoe UI,system-ui,sans-serif';
  series.forEach(function(s){
    ctx.fillStyle = s.color; ctx.fillRect(lx, ly - 7, 8, 8);
    ctx.fillStyle = 'rgba(200,215,235,.85)';
    ctx.fillText(s.nombre, lx + 12, ly);
    lx += 14 + ctx.measureText(s.nombre).width + 10;
  });
}
function barras(id, valores, color){
  var c = lienzo(id), ctx = c.ctx, w = c.w, h = c.h;
  var vals = (valores || []).map(Number).filter(function(v){ return isFinite(v); });
  if(vals.length < 1){ ctx.fillStyle = 'rgba(150,175,215,.5)';
    ctx.font = '12px Segoe UI,system-ui,sans-serif'; ctx.fillText('Sin datos suficientes', 12, h / 2); return; }
  var max = Math.max.apply(null, vals.concat([1]));
  rejilla(ctx, w, h, 0, max);
  var pad = 10, alto = h - 26, bw = Math.max(2, (w / vals.length) * 0.72);
  var grad = ctx.createLinearGradient(0, pad, 0, pad + alto);
  grad.addColorStop(0, color); grad.addColorStop(1, color + '55');
  for(var i = 0; i < vals.length; i++){
    var bh = Math.max(vals[i] > 0 ? 2 : 0, (vals[i] / max) * alto);
    var xx = (i / vals.length) * w + (w / vals.length - bw) / 2;
    ctx.fillStyle = grad;
    ctx.fillRect(xx, pad + alto - bh, bw, bh);
  }
}

function pintar(d){
  var st = d.stats || {}, nodos = d.nodos || [], serie = d.serie || {}, it = d.integridad || {};
  document.getElementById('coreUrl').textContent = d.core || '—';
  var supply = Number(st.supply_emitido || 0), max = Number(st.max_supply || 100000000);
  document.getElementById('kSupply').textContent = n(supply, 0);
  document.getElementById('kSupplySub').textContent = 'de ' + n(max, 0) + ' KIA · ' + n(supply / max * 100, 2) + '%';
  document.getElementById('kSupplyBar').style.width = Math.min(100, supply / max * 100) + '%';
  document.getElementById('kTasks').textContent = n(st.tareas_ok, 0);
  var pts = (serie.puntos || []);
  var ult5 = pts.slice(-5);
  var rate = ult5.length ? ult5.reduce(function(a, p){ return a + Number(p.tareas || 0); }, 0) / ult5.length : 0;
  document.getElementById('kTasksSub').textContent = n(rate, 1) + ' por minuto (5 min)';
  document.getElementById('kTasksArch').textContent =
    n(st.tareas_ok_en_tabla, 0) + ' en tabla · ' + n(st.tareas_archivadas, 0) +
    ' archivadas (' + n(st.tareas_ok_archivadas, 0) + ' ok · ' +
    n(st.tareas_fallo_archivadas, 0) + ' fallo)';
  var ahora = Date.now() / 1000;
  var activos = nodos.filter(function(x){ return x.visto && (ahora - Number(x.visto)) < 86400; });
  document.getElementById('kNodes').textContent = n(activos.length, 0);
  document.getElementById('kNodesSub').textContent = n(st.nodos, 0) + ' registrados';
  document.getElementById('kQueue').textContent = n(st.tareas_pend, 0);
  document.getElementById('kPay').textContent = n(st.recompensa_base_actual, 6);
  // POTENCIA REAL: lo que cada GPU dio en el benchmark del minero (mismas
  // cuentas que las tareas). El trabajo verificado por el Core va aparte
  // porque incluye el viaje de red y siempre es menor: no son lo mismo.
  var pot = d.potencia || {};
  var medidos = (pot.gflops_medidos === null || pot.gflops_medidos === undefined)
    ? null : Number(pot.gflops_medidos);
  document.getElementById('kFlops').textContent = n(medidos === null ? serie.gflops_ventana : medidos, 2);
  document.getElementById('kFlopsSub').textContent = medidos === null
    ? 'GFLOPS verificados por el Core'
    : 'GFLOPS en ' + n((pot.gpus || []).length, 0) + ' GPU(s) · ' + n(pot.gflops_verificados, 2) + ' verificados por el Core';

  linea('cSupply', pts.map(function(p){ return p.supply; }), '#d4af37');
  barras('cRate', pts.map(function(p){ return p.tareas; }), '#35e08a');
  linea('cFlops', pts.map(function(p){ return p.gflops; }), '#4aa8ff');

  // POTENCIA Y LATENCIA MEDIDAS EN CADA GPU (telemetria del minero, una
  // muestra por tarea). Nada de medias declaradas ni de duracion_ms.
  var sg = d.serie_gpu || {}, sgp = sg.puntos || [], devs = sg.devices || [];
  var paleta = ['#4aa8ff', '#d4af37', '#35e08a', '#ff7a59', '#b58cff', '#5fd0d8'];
  function serieDe(campo){
    return devs.map(function(dev, i){
      return {nombre: dev, color: paleta[i % paleta.length],
        valores: sgp.map(function(p){
          var g = (p.gpus || {})[dev];
          // null = ese minuto no tuvo tareas de computo: se dibuja hueco, no 0.
          return (g && g[campo] !== null && g[campo] !== undefined) ? Number(g[campo]) : null;
        })};
    });
  }
  lineas('cGpuFlops', serieDe('gflops'));
  lineas('cGpuMs', serieDe('ms'));
  document.getElementById('cGpuFlopsInfo').textContent = devs.length
    ? 'solo tareas de cómputo · ' + devs.join(' / ') : 'sin datos del minero';
  document.getElementById('cGpuMsInfo').textContent = devs.length
    ? 'media de las tareas de cómputo · ' + devs.join(' / ') : 'sin datos del minero';
  var tipos = sg.tipos || {}, claves = Object.keys(tipos), total = 0;
  claves.forEach(function(k){ total += Number(tipos[k]) || 0; });
  var coloresT = {'matriz':'#4aa8ff','checksum':'#8fa6c8','difusion_calor':'#d4af37','inferencia':'#35e08a'};
  document.getElementById('tiposMix').innerHTML = (!claves.length || !total)
    ? '<span class="sub">Sin tareas en la ventana</span>'
    : claves.map(function(k){
        var c = Number(tipos[k]) || 0, pct = Math.round(c / total * 100);
        var col = coloresT[k] || '#b58cff';
        return '<div style="min-width:150px"><div style="display:flex;justify-content:space-between;gap:8px">' +
          '<b style="color:' + col + '">' + esc(k) + '</b><span class="sub">' + n(c, 0) + ' · ' + pct + '%</span></div>' +
          '<div style="height:6px;background:rgba(120,150,200,.15);border-radius:3px;margin-top:4px">' +
          '<div style="height:6px;width:' + pct + '%;background:' + col + ';border-radius:3px"></div></div></div>';
      }).join('');
  document.getElementById('cTiposInfo').textContent = total
    ? n(total, 0) + ' tareas en ' + n(sg.minutos || 60, 0) + ' min' : 'sin tareas';
  var tot = pts.reduce(function(a, p){ return a + Number(p.tareas || 0); }, 0);
  document.getElementById('cRateInfo').textContent = n(tot, 0) + ' en ' + n(serie.minutos || 60, 0) + ' min';
  document.getElementById('cSupplyInfo').textContent = 'ahora ' + n(supply, 0) + ' KIA';

  var cadena = it.cadena_ok === true;
  document.getElementById('sChain').innerHTML = it.cadena_ok === undefined ? '—' :
    (cadena ? '<span class="green">OK</span>' : '<span class="red">ALTERADA</span>');
  document.getElementById('sChainSub').textContent = it.asientos_vivos ?
    n(it.asientos_vivos, 0) + ' asientos vivos' : 'sin datos';
  document.getElementById('sSeals').textContent = n(it.sellos, 0);
  document.getElementById('sSealsSub').textContent = it.sellos_ok === undefined ? '—' :
    (it.sellos_ok ? 'firmas verificadas' : 'revisar: no cuadran');
  var bloqueado = it.pagos_bloqueados === true;
  document.getElementById('sPay').innerHTML = it.pagos_bloqueados === undefined ? '—' :
    (bloqueado ? '<span class="red">BLOQUEADOS</span>' : '<span class="green">ACTIVOS</span>');
  document.getElementById('sPaySub').textContent = bloqueado ?
    'historial alterado: revisar /ledger/integridad' : 'el Core paga trabajo verificado';
  var sello = it.ultimo_sello || null;
  document.getElementById('sHash').textContent = sello ? corto(sello.hash, 10, 12) : '—';
  document.getElementById('sHashSub').textContent = sello ?
    ('hasta asiento ' + n(sello.hasta, 0) + ' · ' + new Date(Number(sello.ts || 0) * 1000).toLocaleTimeString('es-ES')) : '—';

  var val = d.validadores || {};
  var activeVals = (val.activos === undefined) ? null : Number(val.activos);
  var nVals = document.getElementById('sVals');
  if(activeVals === null){
    nVals.textContent = '—';
    document.getElementById('sValsSub').textContent = 'sin datos';
  } else {
    nVals.innerHTML = activeVals > 0
      ? '<span class="green">' + activeVals + '</span>'
      : '<span class="amber">0</span>';
    var d24 = Number(val.discrepancias_24h || 0);
    document.getElementById('sValsSub').innerHTML =
      (activeVals > 0
        ? (n(val.revisadas_24h || 0, 0) + ' tareas revalidadas · ')
        : 'ninguno conectado: ARRANCAR_VALIDADOR.cmd') +
      (d24 > 0 ? '<span class="red">' + d24 + ' discrepancias</span>' :
                 (activeVals > 0 ? '<span class="green">0 discrepancias</span>' : ''));
    nVals.title = (val.validadores || []).map(function(v){ return v.validador; }).slice(0, 8).join(', ');
  }

  var tbody = document.getElementById('nodesBody');
  if(!nodos.length){
    tbody.innerHTML = '<tr><td colspan="6" class="muted">Sin nodos registrados</td></tr>';
  } else {
    var orden = nodos.slice().sort(function(a, b){ return Number(b.visto || 0) - Number(a.visto || 0); }).slice(0, 40);
    tbody.innerHTML = orden.map(function(x){
      var on = x.visto && (ahora - Number(x.visto)) < 86400;
      // GFLOPS medidos por el Core: FLOPs de sus tareas verificadas entre el
      // tiempo que el propio Core cronometro. Es distinto (y menor) que el
      // benchmark de la GPU, porque incluye el viaje de red.
      var gf = Number(x.gflops_medido) || 0;
      var nm = Number(x.potencia_muestras) || 0;
      var celdaGf = gf > 0 ? '<span title="' + n(nm, 0) + ' muestras cronometradas por el Core">' +
        n(gf, 1) + '</span>' : '<span class="muted">—</span>';
      return '<tr><td class="mono">' + esc(corto(x.nombre || x.id, 14, 4)) + '</td>' +
        '<td>' + (x.vram_gb ? x.vram_gb + ' GB' : '—') + '</td>' +
        '<td>' + celdaGf + '</td>' +
        '<td>' + n(x.tareas_ok, 0) + '</td>' +
        '<td>' + n(x.reputacion, 2) + '</td>' +
        '<td><span class="tag ' + (on ? 'on' : 'off') + '">' + hace(x.visto) + '</span></td></tr>';
    }).join('');
  }
  document.getElementById('nodesInfo').textContent = n(activos.length, 0) + ' activos';

  var tel = d.telemetria || [];
  var porGpu = {};
  tel.forEach(function(t){
    var k = (t.nodo_id || '') + '|' + (t.device || '');
    if(!porGpu[k]) porGpu[k] = t;
  });
  var gpus = Object.keys(porGpu).slice(0, 8).map(function(k){
    var t = porGpu[k], m = {};
    try { m = JSON.parse(t.metricas || '{}'); } catch(e){}
    var util = Number(m.gpu_util_pct);
    var hayUtil = isFinite(util) && util >= 0;
    var pct = hayUtil ? Math.max(0, Math.min(100, Math.round(util))) : null;
    var mem = Number(m.gpu_mem_mb);
    var gf = Number(m.gflops_base) || 0;
    var barra = pct === null ? '<span class="sub">sin dato de GPU</span>' :
      '<div class="util"><div class="bar"><span style="width:' + pct + '%"></span></div>' +
      '<span class="pct">' + pct + '%</span></div>';
    return '<div class="gpu"><b>' + esc(t.device || 'GPU') + '</b>' +
      'nodo ' + esc(corto(t.nodo_id, 8, 3)) + '<br>' +
      (gf > 0 ? '<b class="green">' + n(gf, 1) + ' GFLOPS</b> ' +
               '<span class="sub">medidos</span><br>' : '') +
      (Number(m.vram_gb) > 0 ? n(m.vram_gb, 0) + ' GB · ' : '') +
      (m.tarea_ms !== undefined && m.tarea_ms !== null ? n(m.tarea_ms, 0) + ' ms/tarea' : 'sin muestra') +
      barra +
      (mem > 0 ? '<span class="sub">' + n(mem / 1024, 1) + ' GB usados</span>' : '') +
      '</div>';
  }).join('');
  document.getElementById('gpus').innerHTML = gpus;

  var lb = document.getElementById('ledgerBody');
  var led = d.ledger || [];
  if(!led.length){
    lb.innerHTML = '<tr><td colspan="4" class="muted">Sin asientos</td></tr>';
  } else {
    lb.innerHTML = led.slice(0, 60).map(function(r){
      var k = Number(r.kuki || 0);
      var clase = k > 0 ? 'green' : (k < 0 ? 'red' : 'muted');
      var hora = String(r.ts || '').slice(11, 19);
      return '<tr><td class="mono muted">' + (hora || '—') + '</td>' +
        '<td class="mono">' + esc(corto(r.nodo_id, 10, 4)) + '</td>' +
        '<td class="' + clase + '">' + (k > 0 ? '+' : '') + n(k, 6) + '</td>' +
        '<td class="muted">' + esc(r.motivo || '') + '</td></tr>';
    }).join('');
  }
}

function cargar(){
  fetch('/api/resumen?_=' + Date.now(), {cache: 'no-store'})
    .then(function(r){ return r.json(); })
    .then(function(d){
      ultimo = d;
      if(d.ok){
        ultimoOk = Date.now();
        setEstado(true, 'EN VIVO');
        document.getElementById('banner').className = 'banner';
        pintar(d);
      } else {
        setEstado(false, 'CORE SIN RESPUESTA');
        document.getElementById('banner').className = 'banner on';
        document.getElementById('banner').textContent =
          'El Core no responde (' + (d.errores && d.errores[0] || d.core) +
          '). Se muestra la última foto buena de ' +
          (d.ultimo_bueno ? new Date(d.ultimo_bueno * 1000).toLocaleTimeString('es-ES') : '—') +
          '. No se inventan datos.';
        if(d.stats) pintar(d);
      }
      document.getElementById('reloj').textContent =
        new Date().toLocaleTimeString('es-ES');
    })
    .catch(function(e){
      setEstado(false, 'SIN CONEXION');
      var b = document.getElementById('banner');
      b.className = 'banner on';
      b.textContent = 'No se pudo cargar el panel: ' + e.message;
    });
}
cargar();
setInterval(function(){ if(!document.hidden) cargar(); }, 4000);
window.addEventListener('resize', function(){ if(ultimo && ultimo.stats) pintar(ultimo); });
</script>
</body>
</html>
"""

# corre: uvicorn kukia_dash:app --port 8301
