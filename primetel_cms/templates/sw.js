const CACHE_NAME = 'primetel-cms-v2';
const ASSETS_TO_CACHE = [
    '/',
    '/static/css/custom.css',
    '/static/img/logo.png',
    '/static/vendor/tailwind-3.4.cdn.js',
    '/static/vendor/htmx-1.9.12.min.js',
    '/static/vendor/alpine-3.14.3.min.js',
    '/static/vendor/chart-4.4.4.min.js'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            console.log('Opened cache');
            return cache.addAll(ASSETS_TO_CACHE);
        })
    );
});

self.addEventListener('fetch', (event) => {
    event.respondWith(
        caches.match(event.request).then((response) => {
            // Return cached response if found
            if (response) {
                return response;
            }
            
            // Otherwise try to fetch from network
            return fetch(event.request).then((networkResponse) => {
                // If the fetch fails or is not a valid response, just return it
                if (!networkResponse || networkResponse.status !== 200 || networkResponse.type !== 'basic') {
                    return networkResponse;
                }
                
                // Clone the response because it's a stream
                const responseToCache = networkResponse.clone();
                
                // Cache the new resource for future
                caches.open(CACHE_NAME).then((cache) => {
                    if (event.request.method === 'GET' && !event.request.url.includes('/api/')) {
                        cache.put(event.request, responseToCache);
                    }
                });
                
                return networkResponse;
            }).catch(() => {
                // Return offline fallback if we had one
                // return caches.match('/offline.html');
            });
        })
    );
});

self.addEventListener('activate', (event) => {
    const cacheAllowlist = [CACHE_NAME];
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cacheName) => {
                    if (cacheAllowlist.indexOf(cacheName) === -1) {
                        return caches.delete(cacheName);
                    }
                })
            );
        })
    );
});
