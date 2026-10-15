var CACHE = 'kukia-wallet-v2';
var FILES = [
  'wallet.html',
  'manifest.json',
  'moneda.jpg',
  'nacl.min.js',
  'qrcode.min.js',
  'wordlist.txt'
];
self.addEventListener('install', function(e) {
  e.waitUntil(caches.open(CACHE).then(function(c) { return c.addAll(FILES); }));
});
self.addEventListener('fetch', function(e) {
  e.respondWith(caches.match(e.request).then(function(r) { return r || fetch(e.request); }));
});
