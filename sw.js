const CACHE = "p34-pwa-v6";

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (
    url.pathname.startsWith("/sync") ||
    url.pathname.startsWith("/auth") ||
    url.pathname.startsWith("/health") ||
    url.pathname.startsWith("/ocr")
  ) {
    return;
  }
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res && res.ok && (url.pathname === "/" || url.pathname.endsWith(".html") || url.pathname.endsWith(".png") || url.pathname === "/manifest.json")) {
          const copy = res.clone();
          caches.open(CACHE).then((cache) => cache.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req))
  );
});
