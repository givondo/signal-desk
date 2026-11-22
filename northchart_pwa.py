"""Installable desktop app (PWA) assets for Northcharts, standard library only.

Serves the web app manifest, a small service worker and the app icons, so
Chrome and Edge offer "Install Northcharts" and it opens in its own window
from the Start menu, dock or taskbar. Icons are drawn here as PNGs (no image
files to ship or deploy).
"""

from __future__ import annotations

import functools
import json
import struct
import zlib

BG = (11, 13, 16)
GOLD = (228, 192, 74)

PUBLIC_PATHS = ("/manifest.webmanifest", "/sw.js", "/icons/icon-192.png", "/icons/icon-512.png",
                "/icons/maskable-512.png", "/icons/apple-touch-icon.png")


def manifest() -> bytes:
    return json.dumps({
        "name": "Northcharts",
        "short_name": "Northcharts",
        "description": "Charts, signals and an agent desk for gold, FX, crypto, indices and oil.",
        "id": "/",
        "start_url": "/?source=desktop",
        "scope": "/",
        "display": "standalone",
        "display_override": ["window-controls-overlay", "standalone"],
        "background_color": "#0b0d10",
        "theme_color": "#0b0d10",
        "icons": [
            {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/icons/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Agent desk", "url": "/?source=desktop#agents"},
            {"name": "Journal", "url": "/journal"},
        ],
    }).encode()


SERVICE_WORKER = b"""// Northcharts service worker: network first, so prices are never served stale.
// If the network is down, the last copy of the app shell is shown instead.
const SHELL = 'northchart-shell-v1';
self.addEventListener('install', e => { self.skipWaiting(); });
self.addEventListener('activate', e => { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', e => {
  const r = e.request;
  if (r.method !== 'GET' || r.mode !== 'navigate') return;   // APIs and streams go straight to the network
  e.respondWith(fetch(r).then(res => {
    if (res.ok) { const copy = res.clone(); caches.open(SHELL).then(c => c.put('/', copy)); }
    return res;
  }).catch(() => caches.match('/')));
});
"""


def _png(w, h, rows):
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


@functools.lru_cache(maxsize=8)
def icon(size: int, maskable: bool = False) -> bytes:
    """Dark tile with a gold N. Maskable icons fill the square and keep the
    letter inside the safe zone; 'any' icons have rounded corners."""
    s = float(size)
    radius = 0 if maskable else s * 0.22
    scale = 0.52 if maskable else 0.62        # letter size relative to the tile
    lw = s * scale * 0.2                     # stroke width
    x0, x1 = s * (1 - scale) / 2, s * (1 + scale) / 2
    y0, y1 = s * (1 - scale) / 2, s * (1 + scale) / 2
    # diagonal from top-left stem to bottom-right stem
    dx, dy = (x1 - lw) - x0, y1 - y0
    norm = (dx * dx + dy * dy) ** 0.5

    def inside_tile(x, y):
        if radius == 0:
            return True
        cx = min(max(x, radius), s - radius)
        cy = min(max(y, radius), s - radius)
        return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2

    def inside_n(x, y):
        if not (y0 <= y <= y1):
            return False
        if x0 <= x <= x0 + lw or x1 - lw <= x <= x1:
            return True
        # distance from the diagonal centre line, clipped between the stems
        t = ((x - x0) * dx + (y - y0) * dy) / (norm * norm)
        if 0 <= t <= 1:
            d = abs((x - (x0 + lw / 2)) * dy - (y - y0) * dx) / norm
            return d <= lw * 0.62
        return False

    sub = ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75))
    rows = []
    for j in range(size):
        row = []
        for i in range(size):
            tile = gold = 0
            for ox, oy in sub:
                x, y = i + ox, j + oy
                if inside_tile(x, y):
                    tile += 1
                    if inside_n(x, y):
                        gold += 1
            a = tile * 255 // 4
            if tile:
                g = gold / tile
                c = [round(BG[k] * (1 - g) + GOLD[k] * g) for k in range(3)]
            else:
                c = [0, 0, 0]
            row.extend((c[0], c[1], c[2], a))
        rows.append(row)
    return _png(size, size, rows)


def serve(path: str):
    """Return (content_type, body) for a PWA path, or None."""
    if path == "/manifest.webmanifest":
        return "application/manifest+json", manifest()
    if path == "/sw.js":
        return "text/javascript; charset=utf-8", SERVICE_WORKER
    if path == "/icons/icon-192.png":
        return "image/png", icon(192)
    if path == "/icons/icon-512.png":
        return "image/png", icon(512)
    if path == "/icons/maskable-512.png":
        return "image/png", icon(512, True)
    if path == "/icons/apple-touch-icon.png":
        return "image/png", icon(180, True)
    return None
