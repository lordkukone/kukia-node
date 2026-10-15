/* KUKIA — Gráficas TESTNET con datos reales (dev).
 * Lee 'datos-graficas.json' (instantánea de desarrollo). SVG nativo, sin dependencias.
 * Si faltan datos o hay error, muestra un estado informativo: NUNCA inventa valores.
 */
(function(){
"use strict";

var MESES = {Jan:0,Feb:1,Mar:2,Apr:3,May:4,Jun:5,Jul:6,Aug:7,Sep:8,Oct:9,Nov:10,Dec:11};
var MOT_VERIFICADO = "trabajo verificado";
var MOT_FALLOS = ["resultado incorrecto (recalculado por el Core)", "hash no verificado"];

/* ---------- utilidades puras (testeables) ---------- */
function parseTS(s){
  try {
    var p = String(s).split(" ");
    if(p.length < 5 || MESES[p[1]] === undefined) return null;
    var h = p[3].split(":");
    var t = new Date(+p[4], MESES[p[1]], +p[2], +h[0], +h[1], +(h[2] || 0)).getTime();
    return isFinite(t) ? t : null;
  } catch(e){ return null; }
}

function fmtNum(n, dec){
  if(typeof n !== "number" || !isFinite(n)) return "n/d";
  return n.toLocaleString("es-ES", {maximumFractionDigits: dec === undefined ? 1 : dec,
                                    minimumFractionDigits: 0});
}

function fmtCompacto(n){
  if(typeof n !== "number" || !isFinite(n)) return "n/d";
  if(Math.abs(n) >= 1000000)
    return (n / 1000000).toLocaleString("es-ES", {maximumFractionDigits: 1, minimumFractionDigits: 1}) + "M";
  return n.toLocaleString("es-ES", {maximumFractionDigits: 0});
}

function fmtFecha(ms){
  if(ms === null || ms === undefined) return "fecha desconocida";
  var d = new Date(ms), p = function(x){ return (x < 10 ? "0" : "") + x; };
  return p(d.getDate()) + "/" + p(d.getMonth() + 1) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
}

function fmtGenerado(iso){
  try {
    var p = String(iso).split("T"), f = p[0].split("-");
    if(p.length !== 2 || f.length !== 3) return null;
    var y = +f[0], m = +f[1], d = +f[2];
    if(!isFinite(y) || !isFinite(m) || !isFinite(d) || m < 1 || m > 12 || d < 1 || d > 31) return null;
    return f[2] + "/" + f[1] + "/" + f[0] + " " + (p[1] || "").slice(0, 5);
  } catch(e){ return null; }
}

/* Suministro acumulado: suma k en orden cronológico, excluyendo movimientos
 * internos de migración (se compensan entre sí, neto 0; no son emisión). */
function serieSuministro(rows){
  var validos = [], usados = 0;
  rows.forEach(function(r, i){
    if(typeof r.m === "string" && r.m.indexOf("migracion") === 0) return;
    var t = parseTS(r.t), k = +r.k;
    if(t === null || !isFinite(k)) return;
    usados++;
    validos.push({t: t, k: k, i: i});
  });
  validos.sort(function(a, b){ return (a.t - b.t) || (a.i - b.i); });
  var cum = 0;
  var pts = validos.map(function(p){ cum += p.k; return {t: p.t, v: cum}; });
  return {puntos: pts, usados: usados};
}

/* Tareas acumuladas por motivo exacto, sin contar dos veces la misma tarea. */
function serieTareas(rows, motivos){
  var vistos = {}, pts = [], cum = 0;
  var lista = rows.map(function(r, i){ return {t: parseTS(r.t), m: r.m, id: r.id, i: i}; })
    .filter(function(r){ return r.t !== null && motivos.indexOf(r.m) >= 0 && !vistos[r.id + "|" + r.m] && (vistos[r.id + "|" + r.m] = 1); })
    .sort(function(a, b){ return (a.t - b.t) || (a.i - b.i); });
  lista.forEach(function(r){ cum++; pts.push({t: r.t, v: cum}); });
  return pts;
}

function altasNodos(nodes){
  var lista = nodes.map(function(n){ return {t: parseTS(n.c)}; })
    .filter(function(r){ return r.t !== null; })
    .sort(function(a, b){ return a.t - b.t; });
  var pts = [], cum = 0;
  lista.forEach(function(r){ cum++; pts.push({t: r.t, v: cum}); });
  return pts;
}

function distribucionVRAM(nodes){
  var m = {};
  nodes.forEach(function(n){
    var v = +n.v;
    if(!isFinite(v)) return;
    m[v] = (m[v] || 0) + 1;
  });
  return Object.keys(m).map(Number).sort(function(a, b){ return a - b; })
    .map(function(v){ return {vram: v, n: m[v]}; });
}

/* Potencia total DECLARADA (suma de TOPS que cada nodo declara).
 * No es potencia medida: la precisión no la indica cada nodo y no se verifica. */
function potenciaDeclarada(nodes){
  var total = 0, n = 0;
  nodes.forEach(function(nodo){
    var t = +nodo.tops;
    if(!isFinite(t)) return;
    n++;
    total += t;
  });
  return {total: total, nodos: n};
}

/* Recompensas pagadas por trabajos verificados (del ledger, valores reales). */
function recompensas(rows){
  var pagos = [];
  var vistos = {};
  rows.forEach(function(r){
    if(r.m !== MOT_VERIFICADO) return;
    if(vistos[r.id]) return;
    vistos[r.id] = 1;
    var t = parseTS(r.t), k = +r.k;
    if(t === null || !isFinite(k)) return;
    pagos.push({t: t, id: String(r.id).slice(0, 12), k: k});
  });
  pagos.sort(function(a, b){ return a.t - b.t; });
  var total = pagos.reduce(function(a, p){ return a + p.k; }, 0);
  return {total: total, pagos: pagos};
}

/* ---------- dibujo SVG ---------- */
var W = 640, H = 300, ML = 64, MR = 14, MT = 14, MB = 34;

function escalaLineal(min, max, pix){
  if(!(max > min)) { min -= 1; max += 1; }
  return function(v){ return pix - (v - min) / (max - min) * pix; };
}

function ejeY(min, max, unidad){
  var g = "", sy = escalaLineal(min, max, H - MT - MB);
  for(var i = 0; i <= 3; i++){
    var v = min + (max - min) * i / 3, y = MT + sy(v);
    g += '<line x1="' + ML + '" y1="' + y.toFixed(1) + '" x2="' + (W - MR) + '" y2="' + y.toFixed(1) +
         '" stroke="rgba(123,92,255,.25)" stroke-width="1"/>' +
         '<text x="' + (ML - 6) + '" y="' + (y + 4).toFixed(1) + '" text-anchor="end" font-size="11" fill="#9db6dd">' +
         fmtCompacto(v) + (unidad ? " " + unidad : "") + "</text>";
  }
  return g;
}

function ejeX(t0, t1){
  var x = function(t){ return ML + (t1 > t0 ? (t - t0) / (t1 - t0) : 0.5) * (W - ML - MR); };
  var fmt = function(t){ return fmtFecha(t); };
  return '<text x="' + ML + '" y="' + (H - 10) + '" font-size="11" fill="#9db6dd">' + fmt(t0) + "</text>" +
    '<text x="' + (W - MR) + '" y="' + (H - 10) + '" text-anchor="end" font-size="11" fill="#9db6dd">' + fmt(t1) + "</text>";
}

function dibujarSerie(el, pts, color, unidad, titulo){
  if(!pts.length){
    el.innerHTML = '<p class="gload">Sin datos suficientes para esta gráfica.</p>';
    return null;
  }
  var ts = pts.map(function(p){ return p.t; }), vs = pts.map(function(p){ return p.v; });
  var t0 = Math.min.apply(null, ts), t1 = Math.max.apply(null, ts);
  var v0 = Math.min.apply(null, vs), v1 = Math.max.apply(null, vs);
  var X = function(t){ return ML + (t1 > t0 ? (t - t0) / (t1 - t0) : 0.5) * (W - ML - MR); };
  var Y = escalaLineal(v0, v1, H - MT - MB);
  var d = pts.map(function(p, i){ return (i ? "L" : "M") + X(p.t).toFixed(1) + "," + (MT + Y(p.v)).toFixed(1); }).join(" ");
  var area = d + "L" + X(t1).toFixed(1) + "," + (H - MB) + "L" + X(t0).toFixed(1) + "," + (H - MB) + "Z";
  var dots = pts.map(function(p){
    return '<circle cx="' + X(p.t).toFixed(1) + '" cy="' + (MT + Y(p.v)).toFixed(1) + '" r="3.5" fill="' + color + '">' +
      "<title>" + titulo + ": " + fmtNum(p.v) + (unidad ? " " + unidad : "") + " — " + fmtFecha(p.t) + "</title></circle>";
  }).join("");
  el.innerHTML = '<svg viewBox="0 0 ' + W + " " + H + '" style="width:100%;height:auto;display:block;" role="img">' +
    ejeY(v0, v1, unidad) + ejeX(t0, t1) +
    '<path d="' + area + '" fill="' + color + '" opacity="0.18"/>' +
    '<path d="' + d + '" fill="none" stroke="' + color + '" stroke-width="2"/>' + dots + "</svg>";
  return pts[pts.length - 1];
}

function dibujarBarras(el, items, color){
  if(!items.length){
    el.innerHTML = '<p class="gload">Sin datos suficientes para esta gráfica.</p>';
    return;
  }
  var max = Math.max.apply(null, items.map(function(i){ return i.n; }));
  var bw = (W - ML - MR) / items.length;
  var bars = items.map(function(it, i){
    var h = max ? (it.n / max) * (H - MT - MB - 20) : 0;
    var x = ML + i * bw + bw * 0.2, y = H - MB - h;
    return '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + (bw * 0.6).toFixed(1) + '" height="' + h.toFixed(1) +
      '" rx="4" fill="' + color + '"><title>' + it.n + " nodo(s) con " + it.vram + " GB</title></rect>" +
      '<text x="' + (x + bw * 0.3).toFixed(1) + '" y="' + (H - MB + 18) + '" text-anchor="middle" font-size="11" fill="#9db6dd">' +
      it.vram + " GB</text>" +
      '<text x="' + (x + bw * 0.3).toFixed(1) + '" y="' + (y - 6).toFixed(1) + '" text-anchor="middle" font-size="12" fill="#fff">' +
      it.n + "</text>";
  }).join("");
  el.innerHTML = '<svg viewBox="0 0 ' + W + " " + H + '" style="width:100%;height:auto;display:block;" role="img">' + bars + "</svg>";
}

function tablaNodos(el, nodes){
  if(!nodes.length){
    el.innerHTML = '<p class="gload">Sin datos suficientes.</p>';
    return;
  }
  var rows = nodes.slice().sort(function(a, b){ return (b.ok - a.ok) || (b.r - a.r); });
  var html = '<table style="width:100%;border-collapse:collapse;font-size:.9rem;"><thead><tr>' +
    '<th style="text-align:left;padding:.4rem;color:#9db6dd;">Nodo</th>' +
    '<th style="text-align:right;padding:.4rem;color:#9db6dd;">Tareas</th>' +
    '<th style="text-align:right;padding:.4rem;color:#9db6dd;">Reputación</th>' +
    '<th style="text-align:right;padding:.4rem;color:#9db6dd;">VRAM</th></tr></thead><tbody>';
  rows.forEach(function(n){
    html += "<tr><td style=\"padding:.4rem;border-top:1px solid rgba(123,92,255,.2);color:#fff;\">nodo-" + n.id + "</td>" +
      '<td style="padding:.4rem;border-top:1px solid rgba(123,92,255,.2);text-align:right;color:#fff;">' + n.ok + "</td>" +
      '<td style="padding:.4rem;border-top:1px solid rgba(123,92,255,.2);text-align:right;color:#fff;">' + fmtNum(n.r, 2) + "</td>" +
      '<td style="padding:.4rem;border-top:1px solid rgba(123,92,255,.2);text-align:right;color:#fff;">' + n.v + " GB</td>";
  });
  el.innerHTML = html + "</tbody></table>";
}

function estadoError(msg){
  ["g-supply", "g-tasks", "g-nodes", "g-vram", "g-board", "g-pow", "g-rew", "g-tasa"].forEach(function(id){
    var el = document.getElementById(id);
    if(el) el.innerHTML = '<p class="gload">' + msg + "</p>";
  });
}

function nota(id, txt){
  var el = document.getElementById(id);
  if(el) el.textContent = txt;
}

/* ---------- carga y render ---------- */
function iniciar(){
  if(!document.getElementById("g-supply")) return;
  fetch("datos-graficas.json", {cache: "no-store"}).then(function(r){
    if(!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }).then(function(d){
    if(!d || !Array.isArray(d.ledger)){
      estadoError("Datos no disponibles: el fichero no tiene el formato esperado.");
      return;
    }
    var rows = d.ledger, nodes = Array.isArray(d.nodos) ? d.nodos : [];

    var s = serieSuministro(rows);
    var ult = dibujarSerie(document.getElementById("g-supply"), s.puntos, "#00e5ff", "KIA", "Suministro");
    nota("n-supply", "Serie con " + s.usados + " operaciones disponibles. Movimientos internos de migración excluidos (neto 0); el inicio es la asignación fundacional." +
      (ult ? " Último: " + fmtNum(ult.v) + " KIA." : ""));

    var ver = serieTareas(rows, [MOT_VERIFICADO]);
    var fal = serieTareas(rows, MOT_FALLOS);
    var elT = document.getElementById("g-tasks");
    dibujarSerie(elT, ver, "#00ff88", "tareas", "Verificadas");
    if(fal.length){
      var svg = elT.querySelector("svg");
      var ts = fal.map(function(p){ return p.t; });
      var t0 = Math.min.apply(null, ts), t1 = Math.max.apply(null, ts);
      var X = function(t){ return ML + (t1 > t0 ? (t - t0) / (t1 - t0) : 0.5) * (W - ML - MR); };
      var Y = escalaLineal(0, Math.max.apply(null, fal.map(function(p){ return p.v; })), H - MT - MB);
      var dfal = fal.map(function(p, i){ return (i ? "L" : "M") + X(p.t).toFixed(1) + "," + (MT + Y(p.v)).toFixed(1); }).join(" ");
      var dots = fal.map(function(p){
        return '<circle cx="' + X(p.t).toFixed(1) + '" cy="' + (MT + Y(p.v)).toFixed(1) +
          '" r="3.5" fill="#ff7b7b"><title>Fallidas: ' + p.v + " — " + fmtFecha(p.t) + "</title></circle>";
      }).join("");
      svg.innerHTML += '<path d="' + dfal + '" fill="none" stroke="#ff7b7b" stroke-width="2" stroke-dasharray="5,4"/>' + dots;
    }
    nota("n-tasks", "Verificadas en verde, fallidas en rojo discontinuo. " +
      (ver.length ? "Último: " + ver[ver.length - 1].v + " verificadas." : "Sin verificadas.") +
      " Las pendientes/en curso no quedan registradas en el ledger.");

    var altas = altasNodos(nodes);
    dibujarSerie(document.getElementById("g-nodes"), altas, "#7b5cff", "nodos", "Nodos");
    nota("n-nodes", "Nodos registrados acumulados (no implica conexión en tiempo real)." +
      (altas.length ? " Último: " + altas[altas.length - 1].v + " nodos." : ""));

    dibujarBarras(document.getElementById("g-vram"), distribucionVRAM(nodes), "#00e5ff");
    var vtot = nodes.reduce(function(a, n){ return a + ((isFinite(+n.v) && +n.v > 0) ? +n.v : 0); }, 0);
    nota("n-vram", "Reparto de nodos por memoria GPU declarada. Total: " + fmtNum(vtot) + " GB en " +
      nodes.length + " nodos.");
    tablaNodos(document.getElementById("g-board"), nodes);

    var pot = potenciaDeclarada(nodes);
    var conTops = nodes.filter(function(n){ return isFinite(+n.tops); }).length;
    var elP = document.getElementById("g-pow");
    if(elP){
      if(conTops > 0){
        elP.innerHTML = '<p style="font-size:2.1rem;color:#fff;margin:.4rem 0;">' + fmtNum(pot.total) +
          ' <span style="font-size:1rem;color:#9db6dd;">TOPS</span></p>';
      } else {
        elP.innerHTML = '<p class="gload">No disponible: el fichero de datos actual no incluye ' +
          "la potencia declarada por nodo. Se añadirá al procedimiento de exportación.</p>";
      }
    }
    nota("n-pow", "Suma de TOPS declarados por cada nodo (precisión no indicada por los nodos; " +
      "sin verificar). No es potencia medida ni trabajo realizado." +
      (conTops > 0 ? "" : " Sin datos de potencia en esta instantánea."));

    var rew = recompensas(rows);
    var elR = document.getElementById("g-rew");
    if(elR){
      if(rew.pagos.length){
        var html = '<p style="font-size:2.1rem;color:#fff;margin:.4rem 0;">' + fmtNum(rew.total) +
          ' <span style="font-size:1rem;color:#9db6dd;">KIA</span></p>' +
          '<table style="width:100%;border-collapse:collapse;font-size:.85rem;"><tbody>';
        rew.pagos.slice(-8).reverse().forEach(function(pg){
          html += "<tr><td style=\"padding:.3rem;border-top:1px solid rgba(123,92,255,.2);color:#9db6dd;\">" +
            fmtFecha(pg.t) + "</td>" +
            '<td style="padding:.3rem;border-top:1px solid rgba(123,92,255,.2);color:#fff;">' + pg.id + "</td>" +
            '<td style="padding:.3rem;border-top:1px solid rgba(123,92,255,.2);text-align:right;color:#00ff88;">+' +
            fmtNum(pg.k) + "</td>";
        });
        elR.innerHTML = html + "</tbody></table>";
      } else {
        elR.innerHTML = '<p class="gload">No disponible: sin pagos verificados en esta instantánea.</p>';
      }
    }
    nota("n-rew", "Pagos reales por trabajos verificados (del ledger). " +
      (rew.pagos.length ? "Total: " + fmtNum(rew.total) + " KIA en " + rew.pagos.length + " pagos." : ""));

    var tot = ver.length + fal.length;
    var elTasa = document.getElementById("g-tasa");
    if(elTasa){
      if(tot > 0){
        elTasa.innerHTML = '<p style="font-size:2.1rem;color:#fff;margin:.4rem 0;">' +
          (ver.length / tot * 100).toLocaleString("es-ES", {maximumFractionDigits: 1, minimumFractionDigits: 1}) + " %</p>";
      } else {
        elTasa.innerHTML = '<p class="gload">No disponible: sin tareas en esta instantánea.</p>';
      }
    }
    nota("n-tasa", "Verificadas sobre decididas en la ventana disponible" +
      (tot > 0 ? " (" + ver.length + "/" + tot + "). No es histórico completo." : ". Sin datos."));

    var elMeta = document.getElementById("g-meta");
    if(elMeta){
      var fg = fmtGenerado(d.generado);
      elMeta.textContent = "Última actualización: " + (fg || "n/d") +
        " · Fuente: instantánea local del Core (TESTNET) · Cobertura: últimas " + rows.length +
        " operaciones (máximo 200 por respuesta).";
    }

    fetch("stats.json", {cache: "no-store"}).then(function(r){
      if(!r.ok) throw 0;
      return r.json();
    }).then(function(s){
      if(!s || !s.metricas) return;
      var elN = document.getElementById("n-supply");
      if(elN) elN.textContent += " Era " + s.metricas.era_actual +
        " · Próximo halving: " + (s.metricas.proximo_halving || "n/d") +
        " · Fin de emisión: " + (s.metricas.fin_emision || "n/d") + ".";
    }).catch(function(){});
  }).catch(function(){
    estadoError("No se pudieron cargar los datos de la TESTNET.");
  });
}

if(document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciar);
else iniciar();

/* exposición para pruebas */
window.__kukiaGraficas = {parseTS: parseTS, fmtNum: fmtNum, fmtCompacto: fmtCompacto,
  fmtGenerado: fmtGenerado,
  fmtFecha: fmtFecha, serieSuministro: serieSuministro, serieTareas: serieTareas,
  altasNodos: altasNodos, distribucionVRAM: distribucionVRAM, potenciaDeclarada: potenciaDeclarada,
  recompensas: recompensas, MOT_VERIFICADO: MOT_VERIFICADO,
  MOT_FALLOS: MOT_FALLOS};
})();
