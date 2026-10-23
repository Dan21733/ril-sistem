import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { PRINTER_HEALTH_STATE, checkPrintBridgeHealth } from '../services/printer/printBridge'

/*
 * Status printer untuk header POS (permintaan pemilik, 2026-09-30).
 *
 * Kasir harus tahu sebelum menyobek nota apakah kertas akan keluar, jadi
 * indikatornya memakai pemeriksaan termurah yang tersedia: bridge menjawab
 * `GET /health`, dan bridge sendiri menyentuh port printer tanpa mengirim byte.
 *
 * Dua hal yang sengaja dipilih:
 * - **Jarang bertanya** (30 detik). Printer di toko tidak berubah keadaan tiap
 *   detik, dan tablet kasir dipakai seharian; pemeriksaan per detik hanya
 *   membebani jaringan tanpa menambah informasi.
 * - **Tidak memakai `navigator.onLine` sebagai jawaban.** Printer dan penghubung
 *   cetak ada di LAN: internet mati bukan berarti printer mati. Yang menentukan
 *   hanyalah apakah penghubung cetak menjawab. Event `online`/`offline` tetap
 *   dipakai, tapi hanya sebagai pemicu pemeriksaan ulang.
 *
 * Teks yang keluar dari sini masuk ke layar kasir, jadi bahasanya untuk orang
 * toko: "penghubung cetak", bukan "bridge", dan tanpa versi/jumlah job. Nama
 * modul tetap teknis; teks pemakai tidak.
 */

const DEFAULT_INTERVAL_MS = 30000

/*
 * Pemeriksaan berturut-turut dalam jendela ini dilayani hasil terakhir. Mount,
 * pemulihan antrean cetak, dan klik kasir bisa datang hampir bersamaan; tanpa
 * ini printer disapa tiga kali dalam satu detik.
 */
const MIN_GAP_BETWEEN_CHECKS_MS = 3000

const LABELS = {
  [PRINTER_HEALTH_STATE.CONNECTED]: 'Printer tersambung',
  [PRINTER_HEALTH_STATE.PRINTER_UNREACHABLE]: 'Printer tidak tersambung',
  [PRINTER_HEALTH_STATE.BRIDGE_UNREACHABLE]: 'Printer tidak tersambung',
  [PRINTER_HEALTH_STATE.UNKNOWN]: 'Memeriksa printer...',
}

/*
 * Bentuk pendek untuk tombol header di layar sempit (audit tampilan,
 * 2026-10-09). Di tablet kasir 1007px, tombol header berbagi lebar dengan pil
 * server, tombol tema, dan Riwayat Nota: label panjang pecah jadi dua baris di
 * dalam tombol 44px sehingga teksnya berdesakan. Bentuk panjangnya tetap hidup
 * di `label`, `title`, dan panel detail yang dibuka dari tombol ini.
 */
const SHORT_LABELS = {
  [PRINTER_HEALTH_STATE.CONNECTED]: 'Printer siap',
  [PRINTER_HEALTH_STATE.PRINTER_UNREACHABLE]: 'Printer offline',
  [PRINTER_HEALTH_STATE.BRIDGE_UNREACHABLE]: 'Printer offline',
  [PRINTER_HEALTH_STATE.UNKNOWN]: 'Memeriksa...',
}

/*
 * Langkah pemeriksaan yang ditampilkan di panel detail. Disusun dari yang paling
 * sering terjadi di toko: colokan/kabel dulu, baru konfigurasi. Dipisah dari
 * komponen supaya teks perbaikan hanya punya satu tempat.
 */
const NEXT_STEPS = {
  [PRINTER_HEALTH_STATE.PRINTER_UNREACHABLE]: [
    'Pastikan printer menyala dan lampu galatnya tidak menyala.',
    'Periksa kabel LAN printer (atau kabel ke adaptor jaringannya).',
    'Pastikan IP printer tidak berubah; beri DHCP reservation di router.',
    'Tekan "Periksa lagi" setelah selesai.',
  ],
  [PRINTER_HEALTH_STATE.BRIDGE_UNREACHABLE]: [
    'Pastikan penghubung cetak di toko hidup: komputer/mini PC yang menyala selama jam jualan.',
    'Periksa kabel jaringan komputer itu, terutama kalau baru dinyalakan ulang.',
    'Kalau belum tahu komputer mana itu, buka panduan printer bagian pemasangan.',
    'Tekan "Periksa lagi" setelah penghubung cetak hidup lagi.',
  ],
  [PRINTER_HEALTH_STATE.UNKNOWN]: [
    'Pemeriksaan pertama belum selesai; tekan "Periksa lagi".',
  ],
}

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

export function usePrinterStatus({ intervalMs = DEFAULT_INTERVAL_MS } = {}) {
  const state = ref(PRINTER_HEALTH_STATE.UNKNOWN)
  const detail = ref('')
  const printerAddress = ref('')
  const printerName = ref('')
  const printerStatus = ref(null)
  const checkedAt = ref('')
  const checking = ref(false)

  let timer = null
  let inFlight = null
  let lastResult = null
  let lastCheckedMs = 0

  function apply(result) {
    state.value = result.state
    detail.value = result.detail || ''
    printerAddress.value = result.printerAddress || ''
    printerName.value = result.printerName || ''
    printerStatus.value = result.printerStatus ?? null
    checkedAt.value = result.checkedAt || ''

    return result
  }

  /*
   * Single-flight + jarak minimum. Pemanggil cukup memanggil ini kapan saja
   * (mount, event `online`, selesai cetak, klik kasir) tanpa mengatur jadwal.
   */
  function refresh({ force = false } = {}) {
    if (inFlight) {
      return inFlight
    }

    if (!force && lastResult && Date.now() - lastCheckedMs < MIN_GAP_BETWEEN_CHECKS_MS) {
      return Promise.resolve(lastResult)
    }

    checking.value = true

    inFlight = checkPrintBridgeHealth()
      .then((result) => {
        lastResult = result
        lastCheckedMs = Date.now()

        return apply(result)
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

  const label = computed(() => LABELS[state.value] || LABELS[PRINTER_HEALTH_STATE.UNKNOWN])

  const shortLabel = computed(
    () => SHORT_LABELS[state.value] || SHORT_LABELS[PRINTER_HEALTH_STATE.UNKNOWN],
  )

  const tone = computed(() => {
    if (state.value === PRINTER_HEALTH_STATE.CONNECTED) {
      return 'ok'
    }

    return state.value === PRINTER_HEALTH_STATE.UNKNOWN ? 'unknown' : 'offline'
  })

  const title = computed(() => {
    const time = formatCheckedAt(checkedAt.value)
    const suffix = time ? ` Diperiksa pukul ${time}.` : ''

    if (state.value === PRINTER_HEALTH_STATE.CONNECTED) {
      return `Nota akan tercetak: printer ${printerName.value || printerAddress.value || 'tujuan'} (${printerAddress.value || 'alamat belum dilaporkan'}) menjawab dan siap.${suffix} Ketuk untuk membuka detail.`
    }

    if (state.value === PRINTER_HEALTH_STATE.PRINTER_UNREACHABLE) {
      return `Printer ${printerName.value || printerAddress.value || 'tujuan'} tidak menjawab.${detail.value ? ` ${detail.value}` : ''}${suffix} Nota tetap sah dan akan dicoba cetak lagi otomatis. Ketuk untuk membuka detail.`
    }

    if (state.value === PRINTER_HEALTH_STATE.BRIDGE_UNREACHABLE) {
      return `Penghubung cetak di toko tidak bisa dihubungi.${detail.value ? ` ${detail.value}` : ''}${suffix} Nota tetap sah dan akan dicoba cetak lagi otomatis. Ketuk untuk membuka detail.`
    }

    return 'Status printer belum diperiksa. Ketuk untuk memeriksa sekarang.'
  })

  /*
   * "Online"/"Tidak menjawab": status printernya sendiri, bukan status alat
   * penghubungnya. Kalau penghubung cetaknya tidak menjawab, status printer
   * memang belum terbaca — panel tidak boleh menulis "Belum diketahui"
   * berdampingan dengan judul "Printer tidak tersambung", karena dua kalimat itu
   * terdengar bertentangan.
   */
  const printerStatusLabel = computed(() => {
    if (printerStatus.value === 'ok') {
      return 'Online'
    }

    if (printerStatus.value === 'unreachable') {
      return 'Tidak menjawab'
    }

    return state.value === PRINTER_HEALTH_STATE.BRIDGE_UNREACHABLE
      ? 'Belum terbaca \u2014 penghubung cetak tidak menjawab'
      : 'Belum diketahui'
  })

  const checkedAtLabel = computed(() => formatCheckedAt(checkedAt.value) || 'belum pernah')

  const nextSteps = computed(() => NEXT_STEPS[state.value] || [])

  return {
    state,
    tone,
    label,
    shortLabel,
    title,
    detail,
    printerName,
    printerAddress,
    printerStatus,
    printerStatusLabel,
    checkedAt,
    checkedAtLabel,
    nextSteps,
    checking,
    refresh,
  }
}

export default usePrinterStatus
