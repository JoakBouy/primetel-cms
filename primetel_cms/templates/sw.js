// Bumping the cache name on every release purges old assets and forces the
// browser to fetch a fresh copy of every cacheable resource. If you're ever
// debugging "I only see the old version", bump v3 → v4 etc.
const CACHE_NAME = 'primetel-cms-v4';
const ASSETS_TO_CACHE = [
    '/static/css/custom.css',
    '/static/img/logo.png',
    '/static/vendor/tailwind-3.4.cdn.js',
    '/static/vendor/htmx-1.9.12.min.js',
    '/static/vendor/alpine-3.14.3.min.js',
    '/static/vendor/chart-4.4.4.min.js'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS_TO_CACHE))
    );
    self.skipWaiting();
});

self.addEventListener('fetch', (event) => {
    const request = event.request;
    const url = new URL(request.url);

    // Never intercept non-GET, cross-origin, or anything outside /static/.
    // This is critical: HTML pages, HTMX swaps, and POSTs MUST go straight to
    // the network so authentication cookies and CSRF tokens are honored.
    if (request.method !== 'GET'
        || url.origin !== self.location.origin
        || !url.pathname.startsWith('/static/')) {
        return;
    }

    event.respondWith(
        caches.match(request).then((cached) => {
            if (cached) return cached;
            return fetch(request).then((networkResponse) => {
                if (!networkResponse || networkResponse.status !== 200 || networkResponse.type !== 'basic') {
                    return networkResponse;
                }
                const clone = networkResponse.clone();
                caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
                return networkResponse;
            });
        })
    );
});

self.addEventListener('activate', (event) => {
    const allowList = [CACHE_NAME];
    event.waitUntil(
        caches.keys().then((names) =>
            Promise.all(
                names.map((n) => (allowList.indexOf(n) === -1 ? caches.delete(n) : null))
            )
        )
    );
    self.clients.claim();
});
