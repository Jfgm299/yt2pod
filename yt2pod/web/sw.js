// yt2pod service worker: the app shell works offline; API, RSS and audio always go to the network.
const VERSION = "yt2pod-v0.5.3";
const SHELL = ["/", "/styles.css", "/app.js", "/lib.js", "/store.js", "/i18n.js", "/vendor/htm-preact.js",
  "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon.svg"];
const IMAGES = "yt2pod-images";

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((k) => k !== VERSION && k !== IMAGES).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;

  // Thumbnails and avatars from YouTube: cache-first, so lists render instantly and offline.
  if (/(^|\.)ytimg\.com$|ggpht\.com$|googleusercontent\.com$/.test(url.hostname)) {
    e.respondWith(caches.open(IMAGES).then(async (c) => {
      const hit = await c.match(e.request);
      if (hit) return hit;
      const res = await fetch(e.request);
      if (res.ok || res.type === "opaque") c.put(e.request, res.clone()).then(() => trim(c, 600));
      return res;
    }));
    return;
  }
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/rss/") || url.pathname === "/healthz") return;

  // App shell: network first so updates land at once; the cached copy opens the app offline
  // or when the server is slow. Client-side routes (/canal/…, /buscar) all get the shell.
  const isPage = e.request.mode === "navigate";
  const key = isPage ? "/" : url.pathname;
  e.respondWith(caches.open(VERSION).then(async (c) => {
    const net = fetch(isPage ? "/" : e.request, { cache: "no-cache" }).then((res) => { if (res.ok) c.put(key, res.clone()); return res; });
    const slow = new Promise((r) => setTimeout(r, 2500));
    const first = await Promise.race([net.catch(() => null), slow]);
    if (first) return first;
    return (await c.match(key)) || net;
  }));
});

async function trim(cache, max) {
  const keys = await cache.keys();
  for (const k of keys.slice(0, Math.max(0, keys.length - max))) await cache.delete(k);
}
