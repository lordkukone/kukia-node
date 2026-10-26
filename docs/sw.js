// KUKIA - el service worker SE RETIRA. 26-10-2026 (2)
//
// Historico: este worker existia para que la billetera funcionase sin conexion.
// El problema real que trajo: cada navegador que ya habia entrado se quedaba
// con SU copia de la web y seguia sirviendo esa, aunque el servidor tuviera la
// version nueva. En el PC de Kuki (Chrome y Edge) y en su movil la portada se
// quedaba EN BLANCO con una copia vieja; en una ventana recien estrenada se veia
// entera. Encima el navegador puede tardar hasta 24 h en revisar este fichero,
// asi que el fallo se quedaba pegado dias.
//
// La web es estatica y no necesita cache offline. Este fichero, ahora:
//   1) se activa enseguida (skipWaiting),
//   2) BORRA todas las caches del dominio,
//   3) SE DESREGISTRA a si mismo,
//   4) NO intercepta ninguna peticion (no hay handler de fetch).
// El index.html hace lo mismo por su parte, asi que basta con que el navegador
// abra la web una vez para soltar la copia vieja para siempre.
//
// Si algun dia se quiere volver a tener la billetera sin conexion, que sea un
// worker SOLO para wallet.html y con la version de cache en el nombre, nunca
// sirviendo HTML ya visto.

self.addEventListener('install', function() {
  // Nada que precachear: este worker solo viene a desmontar el anterior.
  self.skipWaiting();
});

self.addEventListener('activate', function(e) {
  e.waitUntil((function() {
    var tareas = [];
    if (self.caches && caches.keys) {
      tareas.push(caches.keys().then(function(claves) {
        return Promise.all(claves.map(function(k) { return caches.delete(k); }));
      }));
    }
    tareas.push(self.registration.unregister());
    if (self.clients && self.clients.claim) tareas.push(self.clients.claim());
    return Promise.all(tareas).catch(function() {});
  })());
});

// Sin handler de 'fetch': no intercepta nada. Todas las peticiones van directas
// a la red, que es lo que se quiere en una web de datos en vivo.
