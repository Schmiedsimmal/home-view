// Minimal service worker for PWA installability.
// No caching: all data is live and must never be served stale.
self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  // Passthrough: network only, no cache.
  event.respondWith(fetch(event.request));
});
