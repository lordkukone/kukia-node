/**
 * KUKIA publico - rele de metricas.
 *
 * El Core vive en un PC sin IP publica, asi que NO se expone: un script local
 * (publicar_metricas.py) lee /stats y /saldos cada pocos segundos y los empuja
 * aqui con una clave. La web publica solo puede LEER.
 *
 *   GET  /api/metricas        -> {ts, stats:{...}, salud:{...}, saldos:{...}}
 *   GET  /api/saldo/{dir}     -> {direccion, saldo, ts}
 *   POST /api/publicar        -> escribe el estado (Authorization: Bearer <clave>)
 *
 * Binding R2: FILES (objeto estado.json).
 */

const OBJETO = 'estado.json';
const MAX_BYTES = 256 * 1024;
const MAX_SALDOS = 2000;

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, OPTIONS',
  'Access-Control-Allow-Headers': '*',
  'Access-Control-Max-Age': '600',
};

function json(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
      ...CORS,
      ...extra,
    },
  });
}

function num(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

// Potencia medida por GPU: {"cuda:0": 12.3, "cuda:1": 15.1}
function limpiaPot(v) {
  const out = {};
  if (!v || typeof v !== 'object' || Array.isArray(v)) return null;
  let n = 0;
  for (const [k, val] of Object.entries(v)) {
    if (n >= 4) break;
    if (typeof k !== 'string' || !/^(cuda|cpu):?\d*$/.test(k)) continue;
    const x = num(val);
    if (x === null || x < 0 || x > 1e6) continue;
    out[k] = Math.round(x * 100) / 100;
    n++;
  }
  return n ? out : null;
}

function limpiaGpusPot(v) {
  if (!Array.isArray(v)) return [];
  const out = [];
  for (const x of v.slice(0, 4)) {
    const d = x && typeof x.d === 'string' ? x.d.trim() : '';
    const g = num(x && x.gflops);
    if (!/^(cuda|cpu):?\d*$/.test(d) || g === null || g < 0 || g > 1e6) continue;
    out.push({ d: d, gflops: Math.round(g * 100) / 100 });
  }
  return out;
}

function limpiaSerie(v) {
  if (!Array.isArray(v)) return [];
  return v.slice(-200).map(function (x) {
    return {
      t: num(x && x.t),
      supply: num(x && x.supply),
      tareas: num(x && x.tareas),
      rate: num(x && x.rate),
      gpus: num(x && x.gpus),
      pot: limpiaPot(x && x.pot),
    };
  }).filter(function (x) {
    return x.t !== null && x.supply !== null;
  });
}

// Serie por GPU medida (GFLOPS y latencia de cada tarjeta, minuto a minuto).
// Se acota TODO: numero de puntos, de GPUs y de tipos de tarea, y cada valor
// tiene que ser un numero razonable. Un publicador con la clave comprometida
// no puede convertir el rele en un vertedero ni colar texto en la web.
function limpiaGpuSerie(v) {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return null;
  const puntos = [];
  const entrada = Array.isArray(v.puntos) ? v.puntos.slice(-60) : [];
  for (const p of entrada) {
    const t = num(p && p.t);
    if (t === null || t <= 0) continue;
    const gpus = {};
    let n = 0;
    const gp = (p.gpus && typeof p.gpus === 'object' && !Array.isArray(p.gpus)) ? p.gpus : {};
    for (const [k, x] of Object.entries(gp)) {
      if (n >= 4) break;
      if (typeof k !== 'string' || !/^(cuda|cpu):?\d*$/.test(k)) continue;
      if (!x || typeof x !== 'object' || Array.isArray(x)) continue;
      const item = {};
      // OJO: num(null) es 0, y aqui null significa "ese minuto no hubo tarea
      // de computo". Si se convirtiera en 0, la web dibujaria una GPU
      // rindiendo cero cuando en realidad no habia nada que medir.
      const estricto = function (w) {
        return (w === null || w === undefined || w === '') ? null : num(w);
      };
      const g = estricto(x.gflops), ms = estricto(x.ms), ta = estricto(x.tareas);
      if (g !== null) {
        if (g < 0 || g > 1e6) continue;
        item.gflops = Math.round(g * 100) / 100;
      }
      if (ms !== null) {
        if (ms < 0 || ms > 1e7) continue;
        item.ms = Math.round(ms * 10) / 10;
      }
      if (ta !== null) {
        if (ta < 0 || ta > 1e9) continue;
        item.tareas = Math.round(ta);
      }
      if (!Object.keys(item).length) continue;
      gpus[k] = item;
      n++;
    }
    if (n) puntos.push({ t: Math.round(t), gpus });
  }
  const devices = [];
  for (const d of (Array.isArray(v.devices) ? v.devices.slice(0, 4) : [])) {
    const s = typeof d === 'string' ? d.trim() : '';
    if (/^(cuda|cpu):?\d*$/.test(s) && devices.indexOf(s) < 0) devices.push(s);
  }
  const tipos = {};
  let nt = 0;
  const tv = (v.tipos && typeof v.tipos === 'object' && !Array.isArray(v.tipos)) ? v.tipos : {};
  for (const [k, x] of Object.entries(tv)) {
    if (nt >= 8) break;
    // Solo nombres de tarea simples: nada de HTML ni de texto libre.
    const nombre = (typeof k === 'string' && /^[a-z_]{1,24}$/i.test(k)) ? k : '';
    const c = num(x);
    if (!nombre || c === null || c < 0 || c > 1e9) continue;
    tipos[nombre] = Math.round(c);
    nt++;
  }
  if (!puntos.length) return null;
  const minutos = num(v.minutos);
  return {
    minutos: Math.min(180, Math.max(5, minutos === null ? 60 : Math.round(minutos))),
    devices: devices,
    puntos: puntos,
    tipos: tipos,
  };
}

function limpiaSaldos(v) {
  const entradas = [];
  if (!v || typeof v !== 'object') return { saldos: {}, total: 0, truncado: false };
  for (const [k, val] of Object.entries(v)) {
    if (typeof k !== 'string' || !k || k.length > 64) continue;
    const x = num(val);
    if (x === null) continue;
    entradas.push([k.toUpperCase(), Math.round(x * 1e6) / 1e6]);
  }
  const total = entradas.length;
  const truncado = total > MAX_SALDOS;
  const out = {};
  for (const [k, x] of entradas.slice(0, MAX_SALDOS)) out[k] = x;
  return { saldos: out, total, truncado };
}

// Tramo "legado": los primeros asientos de la cadena. Forma parte del cuerpo
// firmado del sello v2; si se recorta, la firma publicada NO se puede verificar.
function limpiaLegado(v) {
  if (!v || typeof v !== 'object') return null;
  const hasta = num(v.hasta);
  const n = num(v.n);
  const suma = num(v.suma);
  const hash = typeof v.hash === 'string' ? v.hash.slice(0, 64) : '';
  if (hasta === null || n === null || !hash) return null;
  const out = { hasta, n, hash };
  if (suma !== null) out.suma = suma;
  return out;
}

// Ancla del ledger: ultimo sello firmado por el Core. La web publica puede guardarla
// para poder demostrar que un historial reescrito no encadena con lo ya visto.
function limpiaAncla(v) {
  if (!v || typeof v !== 'object') return null;
  const hasta = num(v.hasta);
  const asientos = num(v.asientos);
  const suma = num(v.suma);
  const ts = num(v.ts);
  const hash = typeof v.hash === 'string' ? v.hash : '';
  const firma = typeof v.firma === 'string' ? v.firma : '';
  const pub = typeof v.clave_publica === 'string' ? v.clave_publica : '';
  if (hasta === null || asientos === null || !hash || !firma || !pub) return null;
  const out = {
    v: num(v.v) || 2,
    hasta: hasta,
    asientos: asientos,
    suma: suma,
    ts: ts,
    hash: hash.slice(0, 64),
    prev: String(v.prev || '').slice(0, 64),
    firma: firma.slice(0, 128),
    clave_publica: pub.slice(0, 64),
  };
  // Sin esto la firma del ancla no cuadra: `legado` se firmo con el sello.
  const legado = limpiaLegado(v.legado);
  if (legado) out.legado = legado;
  return out;
}

async function leer(env) {
  const o = await env.FILES.get(OBJETO);
  if (!o) return null;
  try {
    return JSON.parse(await o.text());
  } catch (e) {
    return null;
  }
}

function autorizado(req, env) {
  if (!env.KUKIA_CLAVE) return false;
  const dado = req.headers.get('authorization') || '';
  const esperado = 'Bearer ' + env.KUKIA_CLAVE;
  if (dado.length !== esperado.length) return false;
  let dif = 0;
  for (let i = 0; i < esperado.length; i++) {
    dif |= dado.charCodeAt(i) ^ esperado.charCodeAt(i);
  }
  return dif === 0;
}

// Cache de borde (3 s): la pagina refresca cada 5 s y con esto un bot que
// martillee /api/metricas no genera una lectura de R2 por peticion. La clave
// ignora el ?_= de la pagina, que si no anularia la cache.
async function cacheado(ctx, clave, ttl, crear) {
  // La cache es una optimizacion, NUNCA un requisito: si el entorno no la
  // soporta o falla, se responde igual desde R2.
  let cache = null;
  try { cache = caches.default; } catch (e) { cache = null; }
  if (cache) {
    try {
      const hit = await cache.match(clave);
      if (hit) return hit;
    } catch (e) { /* seguimos sin cache */ }
  }
  const res = await crear();
  if (cache && ctx && res.status === 200) {
    try {
      const copia = res.clone();
      const cabeceras = new Headers(copia.headers);
      cabeceras.set('cache-control', `public, max-age=${ttl}`);
      const guardable = new Response(copia.body, { status: 200, headers: cabeceras });
      ctx.waitUntil(cache.put(clave, guardable).catch(function () {}));
    } catch (e) { /* sin cache */ }
  }
  return res;
}

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);

    if (req.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS });
    }

    if (req.method === 'POST' && url.pathname === '/api/publicar') {
      if (!autorizado(req, env)) return json({ error: 'no autorizado' }, 401);
      // AUDITORIA 2026-10-20: comprobar el tamano ANTES de leer el cuerpo.
      // Antes se hacia req.text() y luego se miraba: un cuerpo enorme se
      // cargaba en memoria del Worker antes de rechazarlo.
      const cl = req.headers.get('content-length');
      if (!cl || !/^\d+$/.test(cl)) return json({ error: 'content-length requerido' }, 411);
      if (Number(cl) > MAX_BYTES) return json({ error: 'demasiado grande' }, 413);
      const txt = await req.text();
      if (new TextEncoder().encode(txt).length > MAX_BYTES) {
        return json({ error: 'demasiado grande' }, 413);
      }
      let d;
      try {
        d = JSON.parse(txt);
      } catch (e) {
        return json({ error: 'json invalido' }, 400);
      }
      if (!d || typeof d !== 'object' || !d.stats || typeof d.stats !== 'object') {
        return json({ error: 'faltan stats' }, 400);
      }
      const s = d.stats;
      const saldosInfo = limpiaSaldos(d.saldos);
      const estado = {
        v: 1,
        ts: Math.floor(Date.now() / 1000),
        ancla: limpiaAncla(d.ancla),
        stats: {
          supply_emitido: num(s.supply_emitido),
          max_supply: num(s.max_supply),
          nodos: num(s.nodos),
          tareas_ok: num(s.tareas_ok),
          tareas_pend: num(s.tareas_pend),
          // Precio del trabajo y tope del minuto (26-10-2026): la portada los
          // pinta en la calculadora leyendolos de aqui, no llevandolos escritos.
          // Se publican con todos los decimales que da /stats: el precio es
          // 1,45e-06 KIA/GFLOP y redondearlo a 6 decimales daria cero.
          precio: {
            gflop_kia: num(s.pago_por_potencia && s.pago_por_potencia.precio_gflop),
            gflop_divisor: num(s.bolsa_emision && s.bolsa_emision.gflop_divisor),
            tope_min_kia: num(s.bolsa_emision && s.bolsa_emision.presupuesto_kia_min),
            gpu_ref_gflops: num(s.bolsa_emision && s.bolsa_emision.gpu_ref_gflops),
          },
        },
        serie: limpiaSerie(d.serie),
        // Serie por GPU medida: sin esto la web no tiene latencia ni mezcla.
        gpu_serie: limpiaGpuSerie(d.gpu_serie),
        salud: {
          gpus: num(d.salud && d.salud.gpus),
          tareas_por_min: num(d.salud && d.salud.tareas_por_min),
          emision_por_min: num(d.salud && d.salud.emision_por_min),
          tops: num(d.salud && d.salud.tops),
          ultima_tarea_hace: num(d.salud && d.salud.ultima_tarea_hace),
          // Potencia medida en GFLOPS (FLOPs verificados / segundo).
          gflops: num(d.salud && d.salud.gflops),
          gflops_gpus: limpiaGpusPot(d.salud && d.salud.gflops_gpus),
        },
        saldos: saldosInfo.saldos,
        saldos_total: saldosInfo.total,
        saldos_truncado: saldosInfo.truncado,
      };
      await env.FILES.put(OBJETO, JSON.stringify(estado));
      return json({ ok: true, ts: estado.ts, saldos: Object.keys(estado.saldos).length,
                    ancla: estado.ancla ? estado.ancla.hasta : null });
    }

    if (req.method === 'GET' && url.pathname === '/api/metricas') {
      return cacheado(ctx, new Request(url.origin + '/api/metricas'), 3, async () => {
        const d = await leer(env);
        if (!d) return json({ error: 'sin datos todavia' }, 503);
        return json(d);
      });
    }

    if (req.method === 'GET' && url.pathname.startsWith('/api/saldo/')) {
      const dir = decodeURIComponent(url.pathname.slice('/api/saldo/'.length)).trim().toUpperCase();
      if (!dir) return json({ error: 'falta la direccion' }, 400);
      return cacheado(ctx, new Request(url.origin + '/api/saldo/' + encodeURIComponent(dir)), 3, async () => {
        const d = await leer(env);
        if (!d) return json({ error: 'sin datos todavia' }, 503);
        const saldo = d.saldos ? d.saldos[dir] : undefined;
        return json({
          direccion: dir,
          saldo: typeof saldo === 'number' ? saldo : 0,
          conocido: typeof saldo === 'number',
          // Si la lista publicada esta recortada, un 0 no significa "sin saldo".
          truncado: !!d.saldos_truncado,
          ts: d.ts,
        });
      });
    }

    if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/api')) {
      return json({
        servicio: 'kukia-metricas',
        nota: 'solo lectura: el Core no esta expuesto a internet',
        endpoints: ['/api/metricas', '/api/saldo/{direccion}'],
        ancla: 'campo ancla de /api/metricas (ultimo sello del Core)',
      });
    }

    return json({ error: 'no encontrado' }, 404);
  },
};
