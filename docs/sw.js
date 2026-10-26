// KUKIA Wallet - service worker
// v2 (2026-10-19): antes era cache-first para wallet.html, asi que quien ya
// habia entrado seguia recibiendo la version ANTIGUA (la que guardaba la
// clave privada en localStorage) aunque el servidor ya tuviera la buena.
// Ahora el HTML va a RED primero y la cache solo se usa si no hay conexion.
// v4 (2026-10-20): sube la version para que todo el que ya tenia la cache
// vieja la tire al activarse el worker nuevo (la web se quedaba antigua).
// v5 (2026-10-26): los .json de datos (metricas, metricas_snapshot, version,
// core_actual) NO se cachean. Estaban cayendo en la rama cache-first de los
// estaticos: quien ya habia entrado una vez seguia viendo las cifras —y el
// precio— del primer dia, aunque el servidor ya tuviera las nuevas. Se sube la
// version para tirar las caches v4 que ya tienen esos JSON dentro.
var CACHE = 'kukia-wallet-v5';
var ASSETS = [
  'wallet.html',
  'manifest.json',
  'moneda.jpg',
  'nacl.min.js',
  'qrcode.min.js',
  'wordlist.txt'
];

self.addEventListener('install', function(e) {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then(function(c) { return c.addAll(ASSETS); }));
});

self.addEventListener('activate', function(e) {
  // Borra caches de versiones anteriores (kukia-wallet-v1 incluida).
  e.waitUntil(caches.keys().then(function(keys) {
    return Promise.all(keys.filter(function(k) { return k !== CACHE; })
      .map(function(k) { return caches.delete(k); }));
  }).then(function() { return self.clients.claim(); }));
});

self.addEventListener('fetch', function(e) {
  if (e.request.method !== 'GET') return;
  var url;
  try { url = new URL(e.request.url); } catch (err) { return; }
  if (url.origin !== self.location.origin) return;

  var esHtml = e.request.mode === 'navigate' || /\.html$/i.test(url.pathname);
  if (esHtml) {
    e.respondWith(fetch(e.request).then(function(r) {
      var copia = r.clone();
      caches.open(CACHE).then(function(c) { c.put(e.request, copia); });
      return r;
    }).catch(function() {
      return caches.match(e.request).then(function(r) {
        return r || caches.match('wallet.html');
      });
    }));
    return;
  }

  // Datos que cambian (metricas.json, metricas_snapshot.json, version.json,
  // core_actual.json...): SIEMPRE red. Un JSON cacheado es una cifra vieja que
  // se queda pegada, y aqui una cifra vieja es un precio viejo.
  if (/\.json$/i.test(url.pathname)) return;

  // Estaticos (imagenes, scripts, video): cache primero, y se refresca en segundo plano.
  e.respondWith(caches.match(e.request).then(function(r) {
    if (r) return r;
    return fetch(e.request).then(function(res) {
      // El video se pide por trozos (Range: 206). La Cache API NO acepta
      // respuestas parciales: intentar guardarlas lanza un TypeError y ensucia
      // la consola en cada reproduccion. Solo se guarda lo que viene entero.
      if (res.status === 200 && !e.request.headers.get('range')) {
        var copia = res.clone();
        caches.open(CACHE).then(function(c) { return c.put(e.request, copia); })
          .catch(function() {});
      }
      return res;
    });
  }));
});
