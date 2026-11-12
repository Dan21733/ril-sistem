import './assets/main.css'

import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'

/* Warna tablet dari Ril (menu pemilik). Di luar Ril: warna bawaan. */
try {
  const warna = globalThis.RilTablet?.tema?.()
  if (warna) {
    document.documentElement.dataset.warna = String(warna)
  }
} catch {
  /* bukan di dalam Ril */
}

const app = createApp(App)

app.use(createPinia())

app.mount('#app')

/*
 * Service worker (QA 18 Okt 2026): aplikasi kasir tetap terbuka saat tablet
 * dimuat ulang tanpa server. Hanya build produksi; dev server Vite tidak.
 */
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      /* Tanpa HTTPS (http://IP) service worker tidak tersedia; aplikasi tetap jalan. */
    })
  })
}
