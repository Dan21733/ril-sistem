/*
 * Service worker kasir (QA 18 Okt 2026): aplikasi tetap terbuka walau tablet
 * dimuat ulang (ditutup, refresh, restart) saat server atau internet putus.
 * Katalog dan nota sudah tersimpan di IndexedDB; yang hilang dulu hanya
 * berkas aplikasinya.
 *
 * - Halaman (navigasi): jaringan dulu, salinan terakhir kalau gagal. Jadi saat
 *   online tablet selalu memakai versi terbaru setelah update.
 * - /assets/* (nama ber-hash, tidak pernah berubah): salinan dulu.
 * - /api/* dan /print*: tidak pernah disentuh; nota dan katalog punya jalur
 *   offline sendiri.
 * Butuh HTTPS (tailscale serve) atau localhost.
 */
const CACHE = 'kasir-aplikasi-v1'
const STATIS = ['/', '/logo.jpeg', '/favicon.ico', '/trash-can-10417.svg']

function berkasHalaman(html) {
  const hasil = new Set()
  for (const m of html.matchAll(/(?:src|href)="(\/[^"]+)"/g)) {
    if (!m[1].startsWith('/api/') && !m[1].startsWith('/print')) {
      hasil.add(m[1])
    }
  }
  return [...hasil]
}

async function simpanHalaman(cache, response) {
  const html = await response.clone().text()
  await cache.put('/', response.clone())
  const perlu = berkasHalaman(html)
  await Promise.all(perlu.map(async (url) => {
    if (!(await cache.match(url))) {
      try {
        const r = await fetch(url, { cache: 'no-cache' })
        if (r.ok) {
          await cache.put(url, r)
        }
      } catch {
        /* Dicoba lagi di navigasi berikutnya. */
      }
    }
  }))
  /* Berkas versi lama yang tidak dipakai halaman terbaru dibuang. */
  const dipakai = new Set([...STATIS, ...perlu].map((u) => new URL(u, self.location.origin).href))
  for (const req of await cache.keys()) {
    if (new URL(req.url).pathname.startsWith('/assets/') && !dipakai.has(req.url)) {
      await cache.delete(req)
    }
  }
}

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE)
    try {
      const r = await fetch('/', { cache: 'no-cache' })
      if (r.ok) {
        await simpanHalaman(cache, r)
      }
    } catch {
      /* Dipasang saat online berikutnya. */
    }
    await Promise.all(STATIS.slice(1).map((u) => cache.add(u).catch(() => {})))
    await self.skipWaiting()
  })())
})

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    for (const nama of await caches.keys()) {
      if (nama !== CACHE) {
        await caches.delete(nama)
      }
    }
    await self.clients.claim()
  })())
})

self.addEventListener('fetch', (event) => {
  const req = event.request
  const url = new URL(req.url)
  if (req.method !== 'GET' || url.origin !== self.location.origin
    || url.pathname.startsWith('/api/') || url.pathname.startsWith('/print')) {
    return
  }

  if (req.mode === 'navigate') {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE)
      try {
        const r = await fetch(req)
        if (r.ok) {
          event.waitUntil(simpanHalaman(cache, r.clone()))
        }
        return r
      } catch {
        return (await cache.match('/')) || Response.error()
      }
    })())
    return
  }

  if (url.pathname.startsWith('/assets/') || STATIS.includes(url.pathname)) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE)
      const ada = await cache.match(req)
      if (ada) {
        return ada
      }
      const r = await fetch(req)
      if (r.ok) {
        await cache.put(req, r.clone())
      }
      return r
    })())
  }
})
