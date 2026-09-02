// Service worker mínimo: no cachea nada, solo existe para que el navegador
// permita "instalar" Relacionai como aplicación de escritorio (PWA).
self.addEventListener("install", function (event) {
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", function (event) {
  event.respondWith(fetch(event.request));
});
