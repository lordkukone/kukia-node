// KUKIA Wallet - service worker
// v2 (2026-10-19): antes era cache-first para wallet.html, asi que quien ya
// habia entrado seguia recibiendo la version ANTIGUA (la que guardaba la
// clave privada en localStorage) aunque el servidor ya tuviera la buena.
// Ahora el HTML va a RED primero y la cache solo se usa si no hay conexion.
var CACHE = 'kukia-wallet-v3';
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

  // Estaticos: cache primero, y se refresca en segundo plano.
  e.respondWith(caches.match(e.request).then(function(r) {
    if (r) return r;
    return fetch(e.request).then(function(res) {
      var copia = res.clone();
      caches.open(CACHE).then(function(c) { c.put(e.request, copia); });
      return res;
    });
  }));
});
