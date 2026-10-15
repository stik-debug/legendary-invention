const CACHE = "chamapay-static-v9";
const OFFLINE_URL = "/static/offline.html";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then(cache => cache.addAll([OFFLINE_URL, "/static/css/app.css", "/static/js/app.js", "/static/manifest.webmanifest", "/static/logo.jpg", "/static/icons/icon-192.png", "/static/icons/icon-512.png"]))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(req).then(cached => {
        if (cached) return cached;
        return fetch(req).then(response => {
          if (response.ok) caches.open(CACHE).then(cache => cache.put(req, response.clone()));
          return response;
        });
      })
    );
    return;
  }

  // Keep authenticated data out of the cache. If navigation fails,
  // provide the dedicated offline shell with the last saved Chama summary.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() => caches.match(OFFLINE_URL))
    );
  }
});


// Background notifications are displayed by the browser/OS even when ChamaPay is not open.
self.addEventListener('push', function(event) {
  var payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch (_) { payload = {body: event.data ? event.data.text() : 'You have a new ChamaPay update.'}; }
  var title = payload.title || 'ChamaPay';
  var options = { body: payload.body || 'You have a new update.', icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png', tag: 'chamapay-' + (payload.notification_id || Date.now()),
    // Ask supported mobile browsers to keep the notification visible and use vibration.
    // The operating system still controls whether a banner is shown or silently delivered.
    requireInteraction: true, silent: false, vibrate: [200, 100, 200], renotify: true,
    data: { url: (payload.url && payload.url.charAt(0) === '/' && payload.url.indexOf('//') !== 0) ? payload.url : '/notifications' } };
  event.waitUntil(self.registration.showNotification(title, options));
});
self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  var path = event.notification && event.notification.data && event.notification.data.url || '/notifications';
  if (path.charAt(0) !== '/' || path.indexOf('//') === 0) path = '/notifications';
  event.waitUntil(clients.matchAll({type: 'window', includeUncontrolled: true}).then(function(list) {
    for (var i = 0; i < list.length; i++) {
      var client = list[i];
      if ('focus' in client) { client.navigate(path); return client.focus(); }
    }
    return clients.openWindow(path);
  }));
});
