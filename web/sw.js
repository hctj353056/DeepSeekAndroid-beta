"use strict";

const CACHE = "deepseek-termux-v0.0.6-diagnostics";
const ASSETS = ["/styles.css", "/response-renderer.js", "/vendor/highlight.min.js", "/app.js", "/diagnostics.js", "/manifest.webmanifest", "/icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

function networkFirst(request, fallbackKey = "") {
  return fetch(request)
    .then((response) => {
      if (response.ok) {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(fallbackKey || request, copy));
      }
      return response;
    })
    .catch(() => caches.match(fallbackKey || request));
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;

  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request, "/index.html"));
    return;
  }

  // 本地开发以拿到新源码为准；网络不可用才回退到离线缓存。
  event.respondWith(networkFirst(request));
});
