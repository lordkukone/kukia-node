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
// v6 (2026-10-26): dos arreglos de "la pagina no carga":
//   1) si no habia red, CUALQUIER navegacion caia en wallet.html, asi que a
//      quien le fallara la portada (por ejemplo mientras se publicaba) le
//      aparecia la billetera, que se ve como si la web estuviera rota. Ahora
//      cae en la copia de ESA misma pagina y, si no hay, en un aviso.
//   2) el install usaba addAll (todo o nada): un solo fichero que faltara
//      dejaba el worker viejo para siempre. Ahora entra igual y la cache se
//      completa con lo que haya.
var CACHE = 'kukia-wallet-v6';
var ASSETS = [
  'wallet.html',
  'manifest.json',
  'moneda.jpg',
  'nacl.min.js',
  'qrcode.min.js',
  'wordlist.txt'
];

// Aviso minimo para cuando NO hay red y esa pagina no esta en la cache.
var AVISO_SIN_RED = '<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">' +
  '<meta name="viewport" content="width=device-width, initial-scale=1">' +
  '<title>KUKIA | sin conexion</title></head>' +
  '<body style="font-family:system-ui,sans-serif;background:#050d0a;color:#e8f2ec;' +
  'margin:0;padding:2rem;text-align:center">' +
  '<h1 style="color:#e6bd4b">Sin conexion</h1>' +
  '<p>No hay red y esta pagina no estaba guardada en tu navegador.</p>' +
  '<p><a href="" style="color:#22e08a">Reintentar</a></p></body></html>';

self.addEventListener('install', function(e) {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then(function(c) {
    return Promise.all(ASSETS.map(function(u) {
      return c.add(u).catch(function() { /* lo que falte no bloquea al worker nuevo */ });
    }));
  }));
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
        if (r) return r;
        if (/wallet/i.test(url.pathname)) {
          return caches.match('wallet.html').then(function(w) {
            return w || new Response(AVISO_SIN_RED, {status: 200,
              headers: {'content-type': 'text/html; charset=utf-8'}});
          });
        }
        return new Response(AVISO_SIN_RED, {status: 200,
          headers: {'content-type': 'text/html; charset=utf-8'}});
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
