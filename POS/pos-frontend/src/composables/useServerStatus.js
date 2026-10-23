import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import {
  probeServerReachability,
  serverReachability,
} from '../services/api/serverReachability.js'

/*
 * Pil status server di header POS (permintaan pemilik, 2026-10-09).
 *
 * Kasir harus tahu tanpa menekan apa pun apakah POS sedang online atau
 * offline, supaya antrean multi-nota tidak dikirim membabi buta dan PIN tak
 * dikenal tidak dicoba saat server mati. Satu-satunya bukti yang dipakai
 * adalah modul `serverReachability` yang sama dengan gerbang PIN: respons
 * 2xx = hidup, sisanya sunyi (KEPUTUSAN-14). `navigator.onLine` tidak dipakai
 * sebagai jawaban, hanya sebagai pemicu pemeriksaan ulang.
 *
 * Dua hal yang sengaja dipilih (sama seperti status printer):
 * - **Jarang bertanya.** Bukti segar dari aktivitas yang memang sudah jalan
 *   (checkout, katalog) dipakai ulang tanpa request tambahan; pemeriksaan
 *   terjadwal hanya tiap 30 detik dan hanya saat tab terlihat.
 * - **Pil ini indikator saja.** Kembali online tidak mengirim antrean
 *   otomatis; tombol "Kirim semua" tetap satu-satunya jalan sync.
 */

const DEFAULT_INTERVAL_MS = 30000

/*
 * Pemeriksaan berturut-turut dalam jendela ini dilayani hasil terakhir.
 * Event `online`, kembalinya tab, dan selesainya checkout bisa datang hampir
 * bersamaan; modul probe sendiri sudah single-flight + cache 5 detik, ini
 * lapisan kedua supaya pil tidak berkedip.
 */
const MIN_GAP_BETWEEN_CHECKS_MS = 3000

export const SERVER_STATUS = {
  ONLINE: 'online',
  OFFLINE: 'offline',
  UNKNOWN: 'unknown',
}

const LABELS = {
  [SERVER_STATUS.ONLINE]: 'Online',
  [SERVER_STATUS.OFFLINE]: 'Offline',
  [SERVER_STATUS.UNKNOWN]: 'Memeriksa…',
}

export function useServerStatus({ intervalMs = DEFAULT_INTERVAL_MS } = {}) {
  const state = ref(SERVER_STATUS.UNKNOWN)
  const checking = ref(false)
  const checkedAt = ref('')

  let timer = null
  let inFlight = null
  let lastResult = null
  let lastCheckedMs = 0

  function apply(reachable, at) {
    state.value = reachable ? SERVER_STATUS.ONLINE : SERVER_STATUS.OFFLINE
    checkedAt.value = at || ''

    lastResult = reachable

    return reachable
  }

  /*
   * Single-flight + jarak minimum. Pemanggil cukup memanggil ini kapan saja
   * (mount, event `online`, selesai checkout) tanpa mengatur jadwal. Bukti
   * segar dari modul dipakai ulang: hanya bila tidak ada bukti (atau bukti
   * terakhir sunyi) selidik benar-benar menyapa server.
   */
  function refresh({ force = false } = {}) {
    if (inFlight) {
      return inFlight
    }

    if (!force && lastResult !== null && Date.now() - lastCheckedMs < MIN_GAP_BETWEEN_CHECKS_MS) {
      return Promise.resolve(lastResult)
    }

    const cached = force ? null : serverReachability()

    if (cached === true) {
      lastCheckedMs = Date.now()
      return Promise.resolve(apply(true, new Date(lastCheckedMs).toISOString()))
    }

    checking.value = true

    inFlight = probeServerReachability()
      .then((reachable) => {
        lastCheckedMs = Date.now()

        return apply(reachable, new Date(lastCheckedMs).toISOString())
      })
      .finally(() => {
        checking.value = false
        inFlight = null
      })

    return inFlight
  }

  function startPolling() {
    stopPolling()

    timer = setInterval(() => {
      if (typeof document === 'undefined' || document.visibilityState === 'visible') {
        void refresh()
      }
    }, Math.max(5000, Number(intervalMs) || DEFAULT_INTERVAL_MS))
  }

  function stopPolling() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  function onVisibilityChange() {
    /* Tab kembali dilihat: langsung perbarui, jangan tunggu jadwal berikutnya. */
    if (document.visibilityState === 'visible') {
      void refresh({ force: true })
    }
  }

  function onConnectivityChange() {
    void refresh({ force: true })
  }

  onMounted(() => {
    void refresh({ force: true })
    startPolling()

    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', onVisibilityChange)
    }

    if (typeof window !== 'undefined') {
      window.addEventListener('online', onConnectivityChange)
      window.addEventListener('offline', onConnectivityChange)
    }
  })

  onBeforeUnmount(() => {
    stopPolling()

    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }

    if (typeof window !== 'undefined') {
      window.removeEventListener('online', onConnectivityChange)
      window.removeEventListener('offline', onConnectivityChange)
    }
  })

  const label = computed(() => LABELS[state.value] || LABELS[SERVER_STATUS.UNKNOWN])

  const tone = computed(() => {
    if (state.value === SERVER_STATUS.ONLINE) {
      return 'ok'
    }

    return state.value === SERVER_STATUS.UNKNOWN ? 'unknown' : 'offline'
  })

  function formatCheckedAt(value) {
    if (!value) {
      return ''
    }

    const date = new Date(value)

    if (Number.isNaN(date.getTime())) {
      return ''
    }

    return date.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  }

  const title = computed(() => {
    const time = formatCheckedAt(checkedAt.value)
    const suffix = time ? ` Diperiksa pukul ${time}.` : ''

    if (state.value === SERVER_STATUS.ONLINE) {
      return `Server menjawab.${suffix} Antrean bisa dikirim dengan tombol "Kirim semua".`
    }

    if (state.value === SERVER_STATUS.OFFLINE) {
      return `Server tidak menjawab.${suffix} Penjualan tetap jalan dan antre di perangkat; kirim lagi setelah online.`
    }

    return 'Status server belum diperiksa. Ketuk untuk memeriksa sekarang.'
  })

  return {
    state,
    tone,
    label,
    title,
    checking,
    checkedAt,
    refresh,
    startPolling,
    stopPolling,
  }
}

export default useServerStatus
