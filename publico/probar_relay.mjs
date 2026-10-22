// Prueba del rele sin red ni Wrangler: se llama a su fetch() con un R2 en memoria.
// Lo importante: el ancla publicada conserva TODO lo que va dentro de la firma
// (incluido `legado`) y descarta cualquier campo que no corresponda.
import assert from 'node:assert/strict';
import relay from './relay.js';

const store = new Map();
const env = {
  KUKIA_CLAVE: 'clave-de-prueba',
  FILES: {
    async get(k) {
      if (!store.has(k)) return null;
      const txt = store.get(k);
      return { async text() { return txt; } };
    },
    async put(k, v) { store.set(k, v); },
  },
};
const ctx = { waitUntil() {} };

async function pedir(metodo, ruta, cuerpo, clave) {
  const headers = {};
  const txt = cuerpo === undefined ? undefined : JSON.stringify(cuerpo);
  if (txt !== undefined) {
    headers['content-type'] = 'application/json';
    // fetch() lo pone solo; aqui construimos el Request a mano.
    headers['content-length'] = String(new TextEncoder().encode(txt).length);
  }
  if (clave) headers.authorization = 'Bearer ' + clave;
  const req = new Request('https://relay.test' + ruta, {
    method: metodo,
    headers,
    body: txt,
  });
  return relay.fetch(req, env, ctx);
}

const sello = {
  v: 2,
  hasta: 88151,
  asientos: 87976,
  suma: 15083452.1061,
  ts: 1792492621,
  hash: 'c13bc5d670cb83632fe200569bad5f874b57bdf9b6a2cf8629a30b134a22bfbf',
  prev: '08643ce04559ead5186381524cbcb858aef07608466ffbf3d5a1636b6bde6cd1',
  firma: 'f524c29af5c63eea3de6320f6d749aa7e0630b0dac6348a142abb66ffb8fada' +
         '3029a7cce6a306dfc04bf254dcd688e728189e6bad148429f96a97db5b85d1508',
  clave_publica: '2013664e12b4ac93f5a266ae5e1a4a071f51c68112e1209fae5d501ecb2f8d1c',
  legado: { hash: '49dd4c6ed1a5b62e2bda158ab1ddb1b487ba97ee6b022576d68391e2851efb98',
            hasta: 175, n: 175, suma: 15000802.2563 },
  basura: 'no debe publicarse',
};

// 1) Sin clave no se publica.
let r = await pedir('POST', '/api/publicar', { stats: { nodos: 1 } });
assert.equal(r.status, 401, 'POST sin clave debe ser 401');

// 2) Publicacion correcta.
r = await pedir('POST', '/api/publicar', {
  stats: { supply_emitido: 15083452.106094, nodos: 23 },
  saldos: { ABC: 12.5 },
  ancla: sello,
}, 'clave-de-prueba');
assert.equal(r.status, 200);
const acuse = await r.json();
assert.equal(acuse.ancla, 88151, 'el acuse debe traer el ultimo sello');

// 3) El ancla conserva la parte firmada y tira lo demas.
r = await pedir('GET', '/api/metricas');
assert.equal(r.status, 200);
const d = await r.json();
assert.equal(d.ancla.legado.hasta, 175, 'legado.hasta debe sobrevivir');
assert.equal(d.ancla.legado.hash, sello.legado.hash, 'legado.hash debe sobrevivir');
assert.equal(d.ancla.firma, sello.firma, 'la firma debe publicarse intacta');
assert.equal(d.ancla.clave_publica, sello.clave_publica);
assert.ok(!('basura' in d.ancla), 'campos desconocidos no se publican');

// 4) Saldos y direccion.
r = await pedir('GET', '/api/saldo/abc');
const s = await r.json();
assert.equal(s.saldo, 12.5);
assert.equal(s.conocido, true);

// 5) Un ancla sin firma no se publica como si fuera valida.
r = await pedir('POST', '/api/publicar', { stats: {}, ancla: { hasta: 1 } }, 'clave-de-prueba');
assert.equal(r.status, 200);
r = await pedir('GET', '/api/metricas');
assert.equal((await r.json()).ancla, null, 'ancla incompleta debe quedar en null');

// 6) Serie por GPU medida: se publica acotada y sin basura.
r = await pedir('POST', '/api/publicar', {
  stats: { supply_emitido: 1 },
  gpu_serie: {
    minutos: 60,
    devices: ['cuda:0', 'cuda:1', 'EVIL <script>', 'cuda:0'],
    tipos: { matriz: 10, difusion_calor: 4, '<img onerror=x>': 2, malo: 'no-numero' },
    puntos: [
      { t: 1000, gpus: {
          'cuda:0': { gflops: 21.234, ms: 336.66, tareas: 81 },
          'cuda:1': { gflops: null, ms: null },
          'x;DROP': { gflops: 5 } } },
      { t: 0, gpus: { 'cuda:0': { gflops: 1 } } },        // t invalido: fuera
      { t: 1060, gpus: { 'cuda:0': { gflops: -3 } } },    // valor imposible: fuera
    ],
  },
}, 'clave-de-prueba');
assert.equal(r.status, 200, 'publicar con gpu_serie debe aceptarse');
r = await pedir('GET', '/api/metricas');
const gs = (await r.json()).gpu_serie;
assert.ok(gs && Array.isArray(gs.puntos), 'gpu_serie debe publicarse');
assert.equal(gs.puntos.length, 1, 'solo el punto con datos validos');
assert.deepEqual(Object.keys(gs.puntos[0].gpus), ['cuda:0'], 'solo GPUs con dato');
assert.equal(gs.puntos[0].gpus['cuda:0'].gflops, 21.23, 'GFLOPS redondeados a 2 decimales');
assert.equal(gs.puntos[0].gpus['cuda:0'].ms, 336.7, 'latencia redondeada a 1 decimal');
assert.deepEqual(gs.devices, ['cuda:0', 'cuda:1'], 'devices validos, sin repetir y sin HTML');
assert.deepEqual(Object.keys(gs.tipos), ['matriz', 'difusion_calor'], 'solo tipos numericos y con nombre simple');

// 7) Mas de 60 puntos se recortan (la web no puede recibir historia infinita).
r = await pedir('POST', '/api/publicar', {
  stats: { supply_emitido: 1 },
  gpu_serie: { minutos: 60, devices: ['cuda:0'], tipos: { matriz: 1 },
    puntos: Array.from({ length: 65 }, (_, i) => ({
      t: 2000 + i * 60, gpus: { 'cuda:0': { gflops: 10, ms: 100, tareas: 3 } } })) },
}, 'clave-de-prueba');
assert.equal(r.status, 200);
r = await pedir('GET', '/api/metricas');
assert.equal((await r.json()).gpu_serie.puntos.length, 60, 'maximo 60 puntos');

console.log('relay OK: ancla firmada con legado, 401 sin clave, saldo por direccion, serie por GPU saneada');
