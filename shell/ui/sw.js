// MBUDDY phone app: keeps the app's own files so it opens fast and shows something when the connection is
// slow. It never keeps anything from the PC (the /remote calls and pairing always go to the network).
const CACHE = "mbuddy-shell-10";
// The phone tools page and its files are kept at once, so it opens with no connection to the PC at all.
const KEEP = ["phone", "css/phone.css", "js/phone-actions.js", "js/phone-page.js", "js/native-shim.js", "assets/fonts/spacegrotesk.woff2", "assets/fonts/doto.woff2", "assets/pwa/icon-192.png"];
self.addEventListener("install", (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then((cache) => Promise.all(KEEP.map((u) => cache.add(u).catch(() => {})))));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/remote") || url.pathname === "/pair" || url.pathname === "/health") return;
  e.respondWith(caches.open(CACHE).then(async (cache) => {
    const hit = await cache.match(e.request);
    const fresh = fetch(e.request).then((res) => { if (res.ok) cache.put(e.request, res.clone()); return res; }).catch(() => hit);
    return hit || fresh;
  }));
});
