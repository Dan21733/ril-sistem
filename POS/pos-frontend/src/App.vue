<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import {
  API_BASE_URL,
  allocateSequenceRange as fetchSequenceRange,
  deviceAuthHeader,
  registerOrRenewDevice,
} from './services/api/deviceApi'
import {
  clockSkewMs,
  noteServerAnswer,
  noteServerSilence,
  probeServerReachability,
  serverReachability,
} from './services/api/serverReachability'
import { sealApproverPin } from './services/workers/pinSeal'
import { syncTransactionBatch } from './services/api/transactionApi'
import {
  ensureCatalogFresh,
  getCatalogState,
  searchCachedCatalog,
  syncCatalog,
} from './services/catalog/catalogCache'
import { createUuid } from './services/id'
import {
  flushPaymentProofs,
  summarizeProofSync,
} from './services/paymentProof/uploadQueue'
import {
  enqueueReceiptPrint,
  flushPrintJobs,
  retryPrintJobNow,
  summarizePrintSync,
} from './services/printer/printQueue'
import deviceRepository from './services/storage/deviceRepository'
import {
  getPrintJobsBySyncId,
  listOutstandingPrintJobs,
} from './services/storage/printJobRepository'
import {
  getPinCooldownState,
  registerPinFailure,
  registerPinSuccess,
} from './services/workers/pinCooldown'
import { refreshWorkerDirectory } from './services/workers/workerDirectory'
import {
  SequenceRangeExhaustedError,
  consumeNextSequence,
  getActiveRange,
  saveActiveRange,
} from './services/storage/sequenceRepository'
import {
  beginTransactionSubmission,
  getRejectedTransactions,
  getTransaction,
  getUnresolvedTransactions,
  recordDefiniteAttemptFailure,
  saveTransaction,
  updateTransactionStatus,
} from './services/storage/transactionRepository'
import { listQueuedTransactions } from './services/storage/transactionQueue'

import { mapApiItem } from './utils/catalogItem'
import { historyButtonNotice } from './utils/historyButtonNotice'
import { describeCatalogAge } from './utils/localCatalogSearch'
import { countUnprintedNotasToday, unprintedPrintKey } from './utils/printDelay'
import { PRICE_RANGE_PROBLEM, findCartPriceProblem, itemLabel } from './utils/priceRange'

import ProductList from './components/ProductList.vue'
import SearchBar from './components/SearchBar.vue'
import CartList from './components/CartList.vue'
import DeviceRegistrationModal from './components/DeviceRegistrationModal.vue'
import PrinterStatusPanel from './components/PrinterStatusPanel.vue'
import ServerStatusPill from './components/ServerStatusPill.vue'
import TransactionHistory from './components/TransactionHistory.vue'
import { usePrinterStatus } from './composables/usePrinterStatus'
import { useServerStatus } from './composables/useServerStatus'
import { useTheme } from './composables/useTheme'

/*
 * Mode gelap/terang untuk layar kasir (permintaan pemilik, 2026-09-29).
 */
const { isDark, toggleTheme } = useTheme()

/*   * Status printer di header (permintaan pemilik, 2026-09-30). Penghubung cetak
   * dan printer

 * ada di LAN, jadi statusnya tidak boleh diambil dari `navigator.onLine`: satu-
 * satunya bukti adalah jawaban bridge atas pemeriksaan status.
 */
const {
  tone: printerTone,
  label: printerLabel,
  /* Bentuk pendek untuk header sempit (audit tampilan, 2026-10-09). */
  shortLabel: printerShortLabel,
  title: printerTitle,
  detail: printerDetail,
  printerName,
  printerAddress,
  printerStatusLabel,
  checkedAtLabel,
  nextSteps: printerNextSteps,
  checking: printerChecking,
  refresh: refreshPrinterStatus,
} = usePrinterStatus()

/*
 * Pil status server di header (permintaan pemilik, 2026-10-09): indikator
 * saja, tanpa auto-kirim. Keadaan dibaca dari modul `serverReachability` yang
 * sama dengan gerbang PIN, jadi checkout yang memang sudah menyelidik ikut
 * menyegarkan pil tanpa request tambahan.
 */
const {
  tone: serverTone,
  label: serverLabel,
  title: serverTitle,
  checking: serverChecking,
  refresh: refreshServerStatus,
} = useServerStatus()

/* ========================================
   PRODUCT DATA
======================================== */

const items = ref([])

const searchQuery = ref('')

/* ========================================
   API PAGINATION
======================================== */

const currentPage = ref(1)

const hasMoreItems = ref(true)

const loadingItems = ref(false)

const totalItems = ref(0)

/*
 * Asal daftar yang sedang tampil (permintaan pemilik 2026-10-08):
 * `server` = jawaban `/api/items/`, `cache` = salinan di perangkat karena
 * server tidak menjawab. Kasir berhak tahu bedanya: harga dan stok pada
 * salinan adalah snapshot terakhir, bukan angka saat ini.
 */
const catalogSource = ref('server')

const catalogSyncedAt = ref(null)

const catalogAgeMs = ref(null)

/* Salinan ada tetapi sudah lebih tua dari ambang; tetap bisa dipakai. */
const catalogStale = ref(false)

/* Server tidak menjawab dan perangkat belum punya salinan sama sekali. */
const catalogUnavailable = ref(false)

const catalogNotice = computed(() => {
  /*
   * "Tidak ada salinan" diperiksa **sebelum** "dari salinan": keadaan itu bisa
   * terjadi setelah salinan pernah dipakai — misalnya penyimpanan perangkat
   * dibersihkan browser saat aplikasi masih terbuka — dan banner yang tetap
   * mengaku ada salinan akan membuat kasir mengira harganya masih bisa
   * dipercaya. Ditemukan lewat uji browser 2026-10-08.
   */
  if (catalogUnavailable.value) {
    return 'Server tidak terjangkau dan katalog perangkat belum tersimpan. Sambungkan ke server agar salinan pertama dibuat.'
  }

  if (catalogSource.value === 'cache') {
    const age = describeCatalogAge(catalogAgeMs.value)

    return `Mode offline: daftar dari katalog tersimpan (disinkronkan ${age}). Harga dan stok adalah salinan terakhir, bukan angka saat ini.`
  }

  if (catalogStale.value && catalogSyncedAt.value) {
    return `Katalog tersimpan berumur ${describeCatalogAge(catalogAgeMs.value)}. Sambungkan ke server agar salinan offline diperbarui.`
  }

  return ''
})

/*
 * Digunakan untuk membatalkan request
 * sebelumnya jika user mengganti search
 * sebelum request selesai.
 */
let abortController = null

/* ========================================
   CART
======================================== */

const cart = ref([])

const checkoutStatus = ref('restoring')

const checkoutMessage = ref('')

const checkoutResult = ref(null)

const retryAfterSeconds = ref(0)

const historyVisible = ref(false)

const deviceRegistered = ref(true)

const registrationDismissible = ref(true)

const registrationState = ref('idle')

const registrationError = ref('')

/*
 * Status bukti transfer dipisah dari status nota: nota bisa sudah tersimpan di
 * server sementara buktinya masih tertunda (PAYMENT_PROOF_DESIGN.md bagian 7.2).
 */
const proofSyncMessage = ref('')

/*
 * Status cetak dipisah dari status nota dan status bukti: nota bisa sudah di
 * server sementara kertasnya belum keluar dari printer (PRINT_PATH_DESIGN.md §5).
 */
const printSyncMessage = ref('')

/* Job cetak terakhir yang belum keluar — sumber tombol "Cetak ulang". */
const outstandingPrintJobId = ref(null)

/*
 * Berapa nota yang dibuat hari ini dan belum tercetak: sumber angka di tombol
 * "Riwayat Nota" dan chip modal. Semua job yang belum tercetak tetap bisa
 * dicoba ulang, termasuk job dari hari sebelumnya; tanggal hanya membatasi
 * angka notifikasinya.
 */
const outstandingPrintCount = ref(0)

/*
 * Identitas semua pekerjaan yang belum tercetak, dibaca dari antrean yang sama.
 * Menutup panel menutup peringatan dan tombol ulang sebagai satu pasangan,
 * sekaligus tetap memunculkan panel lagi saat ada pekerjaan yang baru.
 */
const outstandingPrintKey = ref('')

let pendingTransaction = null
const pendingReceipt = ref(null)

/*
 * Antrean multi-nota offline (opsi A slice 1): nota yang belum selesai tidak
 * menahan penjualan berikutnya. Daftar baca-saja, tertua dulu; pengiriman
 * tetap per nota dengan PIN asli tersimpan.
 */
const queuedNotices = ref([])

async function refreshQueue() {
  try {
    queuedNotices.value = await listQueuedTransactions()
  } catch {
    queuedNotices.value = []
  }
}

/*
 * Kirim antrean otomatis (QA 18 Okt 2026). Dulu nota offline hanya terkirim
 * lewat tombol "Kirim semua"; kalau kasir lupa, nota tertahan sampai Sabtu.
 * Fungsi yang dipanggil sama persis dengan tombol itu (resendQueuedBatch),
 * jadi semua pengamannya ikut: server menolak kiriman dobel lewat sync_id,
 * nota final yang ditolak menjadi `review`, jeda 429 dihormati. Hanya jalan
 * kalau ada antrean, server terbukti menjawab, dan kasir tidak sedang bayar.
 */
const AUTO_SEND_STATUSES = ['draft', 'pending', 'unknown']

async function autoSendQueue() {
  if (batchSyncState.value !== 'idle' || retryAfterSeconds.value > 0
    || ['restoring', 'submitting', 'conflict', 'review'].includes(checkoutStatus.value)) {
    return
  }
  let waiting = []
  try {
    waiting = (await getUnresolvedTransactions())
      .filter((record) => AUTO_SEND_STATUSES.includes(record.sync_status) && record.original_pin)
  } catch {
    return
  }
  if (waiting.length === 0 || !await probeServerReachability()) {
    return
  }
  await resendQueuedBatch()
}

/*
 * Jam tablet salah (QA 18 Okt 2026): nota memakai jam tablet, dan server
 * menahan nota yang lebih dari 10 menit di depan jamnya. Peringatan muncul
 * sebelum masalahnya menumpuk.
 */
const CLOCK_WARNING_MS = 5 * 60_000
const clockWarning = ref('')

function refreshClockWarning() {
  const skew = clockSkewMs()
  if (skew === null || Math.abs(skew) < CLOCK_WARNING_MS) {
    clockWarning.value = ''
    return
  }
  const minutes = Math.round(Math.abs(skew) / 60_000)
  const amount = minutes >= 120 ? `${Math.round(minutes / 60)} jam` : `${minutes} menit`
  clockWarning.value = `Jam tablet ${skew > 0 ? 'lebih cepat' : 'lebih lambat'} ${amount} dari server. `
    + 'Betulkan jam tablet (Pengaturan > Tanggal dan waktu > otomatis) supaya nota tercatat di tanggal yang benar.'
}

/*
 * Satu keputusan tampilan untuk tombol "Riwayat Nota" (utils/historyButtonNotice):
 * angka antrean, angka cetak tertunda, dan kalimat penuh untuk
 * `title`/`aria-label`.
 */
const historyNotice = computed(() => historyButtonNotice({
  queuedCount: queuedNotices.value.length,
  unprintedCount: outstandingPrintCount.value,
}))
let retryTimer = null
let recoveryTimer = null
const SUBMISSION_RECOVERY_GRACE_MS = 45_000
let searchDebounceTimer = null
const SEARCH_DEBOUNCE_MS = 500
let queueFlushTimer = null
const QUEUE_FLUSH_INTERVAL_MS = 30_000

/*
 * Salinan katalog offline disegarkan berkala, bukan setiap kali dibuka:
 * mengunduh seluruh katalog (~2900 varian) bukan pekerjaan yang perlu diulang
 * tiap tablet bangun. `ensureCatalogFresh()` yang memutuskan perlu-tidaknya.
 */
let catalogRefreshTimer = null
const CATALOG_REFRESH_INTERVAL_MS = 15 * 60_000

/* ========================================
   PRODUCT KEY
======================================== */

function getProductKey(item) {
  return JSON.stringify([item.item_name, item.variant || 'N/A'])
}

/* ========================================
   LOAD ITEMS FROM API
======================================== */

/*
 * Header token tablet yang tersimpan, atau kosong kalau belum terdaftar.
 * `/api/items/` wajib token sejak 17 Okt 2026; tanpa token jawabannya 401,
 * lalu daftar dimuat ulang begitu pendaftaran otomatis selesai.
 */
async function storedDeviceHeader() {
  try {
    const device = await deviceRepository.getDevice()
    return device?.device_token ? deviceAuthHeader(device.device_token) : {}
  } catch {
    return {}
  }
}

async function loadItems(page = 1) {
  const isFirstPage = page === 1

  /*
   * Batalkan request sebelumnya jika ada.
   */
  if (abortController) {
    abortController.abort()
  }

  abortController = new AbortController()

  loadingItems.value = true

  try {
    const params = new URLSearchParams()

    params.set('page', page)

    const query = searchQuery.value.trim()

    if (query) {
      params.set('search', query)
    }

    const response = await fetch(
      `/api/items/?${params.toString()}`,
      {
        signal: abortController.signal,
        headers: await storedDeviceHeader(),
      },
    )

    if (!response.ok) {
      throw new Error(`HTTP error: ${response.status}`)
    }

    /*
     * Katalog adalah panggilan yang hampir selalu terjadi lebih dulu: jawabannya
     * menjadi bukti server hidup untuk gerbang PIN tak dikenal (§3), supaya nota
     * berikutnya tidak perlu menyelidik ulang.
     */
    noteServerAnswer()

    const data = await response.json()
    /*
     * Bentuk barisnya datang dari `mapApiItem()` yang sama dengan jalur salinan
     * offline, supaya item yang sama tidak bisa tampil beda antar-jalur.
     */
    const newItems = (data.results || []).map(mapApiItem)

    if (isFirstPage) {
      items.value = newItems
    } else {
      items.value.push(...newItems)
    }

    currentPage.value = page

    hasMoreItems.value = data.next !== null

    totalItems.value = Number(data.count)

    /* Daftar ini dari server; peringatan salinan offline tidak berlaku. */
    catalogSource.value = 'server'
    catalogUnavailable.value = false

    /* Umur salinan offline dibaca ulang untuk keterangan di bawah jumlah item. */
    await refreshCatalogStatus()

    reconcileCartPrices()
  } catch (error) {
    if (error.name === 'AbortError') {
      return
    }

    /*
     * Server tidak menjawab. Sebelum menyerah, coba salinan katalog di
     * perangkat: kalau ada, kasir tetap bisa mencari dan menjual
     * (OFFLINE_SYNC_DESIGN.md §3.3).
     */
    const servedFromCache = await loadItemsFromCache(page)

    if (!servedFromCache) {
      console.error('Failed to load items from API:', error)
    }
  } finally {
    loadingItems.value = false
  }
}

/*
 * Halaman katalog dari salinan di perangkat. Bentuk hasilnya sengaja sama
 * dengan jawaban server (`results`/`count`/`next`) supaya sisa layar tidak
 * perlu tahu dari mana barisnya datang.
 */
async function loadItemsFromCache(page) {
  try {
    const state = await getCatalogState()

    if (state.count === 0) {
      catalogUnavailable.value = true

      return false
    }

    const result = await searchCachedCatalog(searchQuery.value, { page })

    items.value = page === 1 ? result.results : [...items.value, ...result.results]
    currentPage.value = page
    hasMoreItems.value = result.next !== null
    totalItems.value = result.count
    catalogSource.value = 'cache'
    catalogSyncedAt.value = state.syncedAt
    catalogAgeMs.value = state.ageMs
    catalogStale.value = state.stale
    catalogUnavailable.value = false

    /* Keranjang lama tetap dicocokkan dengan harga salinan yang tersedia. */
    reconcileCartPrices()

    return true
  } catch {
    return false
  }
}

/*
 * Sinkron katalog di latar belakang (permintaan pemilik 2026-10-08: disimpan
 * seluruhnya dan disegarkan berkala). Tidak pernah memblok kasir dan tidak
 * pernah menyusun ulang baris yang sedang dilihat: cap waktunya saja yang
 * diperbarui, baris berikutnya memakai salinan terbaru.
 */
async function refreshCatalogQuietly({ force = false } = {}) {
  const headers = await storedDeviceHeader()
  const result = force
    ? await syncCatalog({ headers }).then((fresh) => ({ synced: true, ...fresh })).catch(() => null)
    : await ensureCatalogFresh({ headers })

  /*
   * Daftar di layar berasal dari salinan yang lebih lama, dan sengaja tidak
   * disusun ulang di bawah jari kasir: keterangan umurnya tetap milik baris
   * yang sedang dilihat. Salinan di penyimpanan tetap diperbarui sinkron ini.
   */
  if (catalogSource.value === 'cache') {
    return
  }

  if (result?.synced) {
    catalogSyncedAt.value = result.syncedAt
    catalogAgeMs.value = 0
    catalogStale.value = false

    return
  }

  await refreshCatalogStatus()
}

/*
 * Umur salinan dibaca ulang tanpa menyentuh daftar yang sedang tampil. Dipanggil
 * setelah jawaban server yang normal: salinan yang sudah tua tetap layak
 * dipakai offline, tetapi kasir perlu tahu sebelum internet putus.
 */
async function refreshCatalogStatus() {
  const state = await getCatalogState()

  catalogSyncedAt.value = state.syncedAt
  catalogAgeMs.value = state.ageMs
  catalogStale.value = state.stale
}

/* ========================================
   LOAD MORE
======================================== */

function loadMoreItems() {
  if (loadingItems.value || !hasMoreItems.value) {
    return
  }

  loadItems(currentPage.value + 1)
}

function showPendingRecord(record) {
  pendingTransaction = record.payload
  pendingReceipt.value = record.receipt_sale || null
  // LocalStorage may still contain an edited/stale cart: the sealed nota wins.
  cart.value = record.payload.items.map((item) => ({
    ...item,
    price: item.unit_price,
    floor_price: null,
    cartId: getProductKey(item),
    checkout_sync_id: record.sync_id,
  }))
  // Persist the cart's origin before HTTP so a crash after acceptance cannot
  // restore the sold cart as an unrelated draft on the next launch.
  try {
    localStorage.setItem('posCart', JSON.stringify(cart.value))
  } catch {
    checkoutStatus.value = 'review'
    throw new Error('Asal keranjang tidak dapat disimpan dengan aman.')
  }
  checkoutResult.value = {
    sync_id: record.sync_id,
    transaction_id: record.transaction_id,
  }
}

async function restorePendingCheckout({ lockHeld = false } = {}) {
  clearTimeout(recoveryTimer)
  recoveryTimer = null
  checkoutStatus.value = 'restoring'
  if (navigator.locks && !lockHeld) {
    return navigator.locks.request('pos-checkout', { ifAvailable: true }, async (lock) => {
      if (!lock) {
        checkoutStatus.value = 'review'
        checkoutMessage.value = 'Checkout sedang berjalan di tab lain. POS akan memeriksa lagi; jangan membuat nota pengganti.'
        clearTimeout(recoveryTimer)
        recoveryTimer = setTimeout(() => {
          recoveryTimer = null
          void restorePendingCheckout()
        }, 5000)
        return
      }
      await restorePendingCheckout({ lockHeld: true })
    })
  }
  try {
    const unresolved = await getUnresolvedTransactions()
    if (unresolved.length === 0) {
      const definiteRejection = (await getRejectedTransactions())
        .filter((record) => record.checkout_version === 1
          && !record.local_final && !record.outcome_unknown)
        .sort((left, right) => String(right.created_at).localeCompare(String(left.created_at)))[0]
      if (definiteRejection) {
        checkoutStatus.value = 'error'
        checkoutMessage.value = definiteRejection.rejection_detail
          || 'Transaksi sebelumnya ditolak pasti; catatan tetap tersimpan.'
      }
      const soldCartId = cart.value[0]?.checkout_sync_id
      if (soldCartId) {
        const origin = await getTransaction(soldCartId)
        if (origin?.sync_status === 'synced') {
          cart.value = []
        } else if (origin?.sync_status === 'rejected' && origin.checkout_version === 1
          && !origin.local_final && !origin.outcome_unknown) {
          cart.value = cart.value.filter((item) => item.checkout_sync_id !== soldCartId)
          checkoutStatus.value = 'error'
          checkoutMessage.value = origin.rejection_detail || 'Transaksi sebelumnya ditolak pasti; catatan tetap tersimpan.'
        } else if (origin?.sync_status === 'rejected' && origin.checkout_version === 1) {
          cart.value = []
          checkoutStatus.value = 'review'
          checkoutMessage.value = origin.rejection_detail || 'Nota final ditolak server; transaksi baru ditahan untuk pemeriksaan.'
          return
        } else {
          throw new Error('Asal keranjang tidak dapat dipastikan.')
        }
      }
      pendingTransaction = null
      pendingReceipt.value = null
      if (checkoutStatus.value !== 'error') {
        checkoutStatus.value = 'idle'
      }
      return
    }
    // Antrean multi-nota (opsi A slice 1): N nota boleh antre; penjualan baru
    // tidak ditahan. Yang menahan: baris rusak/legacy (PIN tak pasti) dan
    // nota review/conflict (butuh manusia).
    const invalid = unresolved.filter((record) => !record.payload?.sync_id
      || record.payload.sync_id !== record.sync_id
      || !record.payload.items?.length || record.checkout_version !== 1
      || !record.receipt_sale || !/^\d{6}$/.test(record.original_pin || ''))
    if (invalid.length > 0) {
      checkoutStatus.value = 'review'
      checkoutMessage.value = 'Nota lama atau data pemulihan tidak lengkap. PIN asli tidak dapat dipastikan; minta pemeriksaan, jangan buat nota pengganti.'
      return
    }
    const held = unresolved.filter(
      (record) => ['review', 'conflict'].includes(record.sync_status))
    if (held.length > 0) {
      checkoutStatus.value = held[0].sync_status === 'conflict' ? 'conflict' : 'review'
      checkoutMessage.value = held[0].rejection_detail
        || 'Nota ditahan untuk pemeriksaan. Jangan hapus atau buat pengganti sampai diperiksa.'
      return
    }
    let freshInFlightMs = 0
    for (const record of unresolved) {
      if (record.sync_status !== 'syncing') { continue }
      const startedAt = Date.parse(record.submission_started_at)
      const elapsed = Number.isFinite(startedAt) ? Date.now() - startedAt : 0
      const remaining = SUBMISSION_RECOVERY_GRACE_MS - elapsed
      if (remaining > 0) {
        freshInFlightMs = Math.max(freshInFlightMs, remaining)
      } else {
        await updateTransactionStatus(record.sync_id, 'unknown')
      }
    }
    if (freshInFlightMs > 0) {
      checkoutStatus.value = 'review'
      checkoutMessage.value = `Pengiriman mungkin masih berjalan di tab lain. Tunggu ${Math.ceil(freshInFlightMs / 1000)} detik, lalu POS akan memeriksa lagi. Jangan buat nota pengganti.`
      clearTimeout(recoveryTimer)
      recoveryTimer = setTimeout(() => {
        recoveryTimer = null
        void restorePendingCheckout()
      }, freshInFlightMs + 250)
      return
    }
    /*
     * Baris tersegel milik antrean bukan draft belanja: keluarkan dari
     * keranjang (aman — payload utuh di Dexie) supaya kasir bisa jualan lagi.
     */
    const queuedIds = new Set(unresolved.map((record) => record.sync_id))
    if (cart.value.some((item) => item.checkout_sync_id && queuedIds.has(item.checkout_sync_id))) {
      cart.value = cart.value.filter(
        (item) => !item.checkout_sync_id || !queuedIds.has(item.checkout_sync_id))
      try {
        localStorage.setItem('posCart', JSON.stringify(cart.value))
      } catch {
        checkoutStatus.value = 'review'
        checkoutMessage.value = 'Antrean lokal tidak dapat dibaca. Checkout diblokir agar nota tidak tergandakan.'
        return
      }
    }
    pendingTransaction = null
    pendingReceipt.value = null
    checkoutStatus.value = 'idle'
    checkoutMessage.value = ''
    await refreshQueue()
  } catch {
    checkoutStatus.value = 'review'
    checkoutMessage.value = 'Antrean lokal tidak dapat dibaca. Checkout diblokir agar nota tidak tergandakan.'
  }
}

/*
 * Rekonsiliasi harga cart lama dengan data item terbaru dari server: cart yang
 * tersimpan sebelum fitur floor tidak punya floor_price. Sesuai pedoman,
 * rentang yang hilang jangan memblok penjualan — cukup isi ulang datanya.
 *
 * Sejak harga `max_price` = 0 dipakai sebagai harga jual, harga lama yang
 * mentok 0 juga ikut diperbaiki di sini: `posCart` yang menyimpan
 * `price: 0, unit_price: 0` dari pricelist yang belum diisi tidak boleh lagi
 * menghasilkan penjualan Rp 0 setelah master harganya tersedia.
 */
function reconcileCartPrices() {
  if (isCartLocked() || cart.value.length === 0 || items.value.length === 0) {
    return
  }

  const itemIndex = new Map(items.value.map((item) => [getProductKey(item), item]))

  cart.value.forEach((cartItem) => {
    const freshItem = itemIndex.get(getProductKey(cartItem))

    if (!freshItem) {
      return
    }

    const staleCeiling = cartItem.price == null || Number(cartItem.price) === 0

    if (staleCeiling) {
      cartItem.price = freshItem.price
      cartItem.floor_price = freshItem.floor_price

      if (freshItem.price != null && !cartItem.unit_price) {
        cartItem.unit_price = freshItem.price
      }

      return
    }

    if (cartItem.floor_price == null && freshItem.floor_price != null) {
      cartItem.floor_price = freshItem.floor_price
    }
  })
}

/* ========================================
   DEVICE REGISTRATION (OTOMATIS)
======================================== */

/*
 * Guard agar tidak ada dua registrasi paralel (mount + online + checkout
 * bisa nyaris bersamaan).
 */
let registrationInFlight = null

async function runAutoRegistration() {
  if (registrationInFlight) {
    return registrationInFlight
  }

  registrationInFlight = (async () => {
    registrationState.value = 'registering'
    registrationError.value = ''
    registrationDismissible.value = false
    deviceRegistered.value = false

    try {
      /*
       * Device tersimpan mencoba renewal dulu (kode tidak berubah) sebelum
       * registrasi baru. Token lama hanya dipakai di request ini.
       */
      let stored = null
      try {
        stored = await deviceRepository.getDevice()
      } catch {
        stored = null
      }
      const device = await registerOrRenewDevice(stored)

      if (!device?.device_id || !device?.device_code || !device?.device_token) {
        throw new Error('Respons registrasi tidak lengkap.')
      }

      await deviceRepository.saveDevice({
        device_id: device.device_id,
        device_code: device.device_code,
        device_token: device.device_token,
      })

      deviceRegistered.value = true
      registrationState.value = 'idle'
      registrationDismissible.value = true

      /* Daftar barang yang tadi ditolak (belum ada token) dimuat ulang. */
      void loadItems(1)
      void refreshCatalogQuietly({ force: true })

      /*
       * Daftar kasir juga (QA 18 Okt 2026): saat aplikasi dibuka, unduhan
       * pertama berjalan sebelum tablet punya token dan gagal diam-diam, jadi
       * tablet baru tidak bisa jualan offline sampai ada penjualan online.
       */
      void refreshWorkerDirectoryQuietly()

      /* Token baru juga membuka jalan bagi bukti yang tadinya ditolak 401. */
      void flushProofs({ quiet: true })

      /*
       * Nota yang tadinya gagal karena belum terdaftar langsung bisa
       * dikirim ulang dari tombol yang sudah ada di keranjang.
       */
      if (stored?.device_code && stored.device_code !== device.device_code) {
        checkoutMessage.value = `Device terdaftar ulang dengan kode baru ${device.device_code} (renewal tidak memungkinkan). Nota tertunda berkode ${stored.device_code} tetap memakai nota dan PIN aslinya; kirim ulang nota yang sama, jangan membuat pengganti.`
      } else {
        checkoutMessage.value = 'Device terdaftar. Jika ada nota tertunda, kirim ulang nota yang sama; jangan membuat pengganti.'
      }
      return device
    } catch (error) {
      registrationState.value = 'failed'
      registrationDismissible.value = true
      registrationError.value = error.status === 401
        ? 'Server belum mengenali koneksi ini. Pastikan DEVICE_REGISTRATION_TOKEN terisi di file .env frontend, lalu jalankan ulang dev server.'
        : error.message || 'Registrasi otomatis gagal. Periksa koneksi ke server.'
      return null
    } finally {
      registrationInFlight = null
    }
  })()

  return registrationInFlight
}

async function ensureDeviceRegistered() {
  try {
    const device = await deviceRepository.getDevice()
    deviceRegistered.value = Boolean(device?.device_id && device?.device_token)
  } catch {
    deviceRegistered.value = false
  }

  if (!deviceRegistered.value && navigator.onLine) {
    /*
     * Jaringan toko di-whitelist pemilik: pendaftaran berjalan sendiri tanpa
     * input manusia. Modal hanya muncul kalau otomatis gagal.
     */
    await runAutoRegistration()
  } else if (!deviceRegistered.value) {
    registrationDismissible.value = true
  }
}

function handleRegistrationDismissed() {
  deviceRegistered.value = true
  registrationState.value = 'idle'
  checkoutMessage.value = 'Device belum terdaftar; transaksi online tidak bisa dikirim sampai pendaftaran berhasil.'
}

async function sealedApprover(pin) {
  if (!pin) {
    return { approver_pin: null }
  }
  const sealed = await sealApproverPin(pin)
  return sealed ? { approver_pin: null, approver_pin_sandi: sealed } : { approver_pin: pin }
}

async function refreshWorkerDirectoryQuietly() {
  try {
    await refreshWorkerDirectory()
  } catch {
    /*
     * Daftar kasir tidak pernah memblok penjualan: kalau gagal disegarkan,
     * daftar lama tetap dipakai dan PIN yang tidak dikenal cuma membuat nota
     * tanpa nama kasir.
     */
  }
}

function handleSharedCartChange(event) {
  if (event.key === 'posCart' && !['submitting', 'restoring'].includes(checkoutStatus.value)) {
    try {
      const savedCart = JSON.parse(event.newValue || '[]')
      if (savedCart[0]?.checkout_sync_id) {
        cart.value = savedCart
        void restorePendingCheckout()
        return
      }
      const priorCart = JSON.parse(event.oldValue || '[]')
      if (priorCart[0]?.checkout_sync_id && savedCart.length === 0) {
        cart.value = []
        void restorePendingCheckout()
        return
      }
    } catch {
      // A malformed shared cart is a fail-closed condition below.
    }
    // One shared device cart must not be sold a second time by a stale tab.
    checkoutStatus.value = 'review'
    checkoutMessage.value = 'Keranjang berubah di tab lain. Buka ulang POS untuk membaca nota terbaru; jangan jual ulang keranjang lama.'
  }
}

function handleOnline() {
  if (!deviceRegistered.value && navigator.onLine) {
    void runAutoRegistration()
  }

  /*
   * Pemicu 3 dari KEPUTUSAN-9 (offline -> online): daftar kasir disegarkan
   * begitu koneksi kembali, supaya PIN yang berubah saat offline tidak
   * tertinggal lama di tablet.
   */
  void refreshWorkerDirectoryQuietly()

  /*
   * Kembali online: kesempatan pertama menyegarkan salinan katalog offline
   * sebelum internet putus lagi.
   */
  void refreshCatalogQuietly()

  /* Kembali online: bukti tertunda ikut dicoba tanpa menunggu kasir. */
  void flushProofs({ quiet: true })

  /*
   * Antrean cetak tidak menunggu internet: bridge dan printer ada di LAN, jadi
   * event ini hanya kesempatan tambahan, bukan syarat.
   */
  void flushPrints({ quiet: true })
}

/* ========================================
   INITIAL LOAD
======================================== */

onMounted(() => {
  /*
   * Restore cart
   */
  let savedCart
  try {
    savedCart = localStorage.getItem('posCart')
  } catch {
    checkoutStatus.value = 'review'
    checkoutMessage.value = 'Keranjang tersimpan tidak dapat dibaca. Jangan lanjutkan checkout sebelum penyimpanan diperiksa.'
    window.addEventListener('online', handleOnline)
    window.addEventListener('storage', handleSharedCartChange)
    return
  }

  if (savedCart) {
    try {
      const parsedCart = JSON.parse(savedCart)

      cart.value = parsedCart.map((item) => ({
        ...item,
        unit_price: Number(item.unit_price ?? item.price),
        floor_price: item.floor_price == null ? null : Number(item.floor_price),
        cartId: item.cartId || getProductKey(item),
      }))
    } catch {
      checkoutStatus.value = 'review'
      checkoutMessage.value = 'Keranjang tersimpan rusak atau tidak terbaca. Jangan membuat transaksi pengganti sebelum data diperiksa.'
      window.addEventListener('online', handleOnline)
      window.addEventListener('storage', handleSharedCartChange)
      return
    }
  }

  /*
   * Load page pertama.
   *
   * Hanya 30 item.
   */

  loadItems(1)
  void restorePendingCheckout().finally(() => {
    if (checkoutStatus.value === 'review' && cart.value.length === 0) {
      void loadItems(1)
    }
  })
  void ensureDeviceRegistered()

  /*
   * Pemicu 1 dari KEPUTUSAN-9 (POS dibuka selagi terhubung): daftar kasir
   * diambil lebih dulu supaya layar PIN sudah punya nama untuk dicocokkan.
   */
  void refreshWorkerDirectoryQuietly()

  /*
   * Salinan katalog untuk offline (permintaan pemilik 2026-10-08). Sinkronnya
   * berjalan di latar: kasir tidak menunggu, dan kalau gagal salinan lama tetap
   * dipakai.
   */
  void refreshCatalogQuietly()

  /* Bukti tertunda dari sesi sebelumnya langsung dicoba lagi. */
  void flushProofs({ quiet: true })

  /*
   * Nota yang kertasnya belum keluar dicoba lagi saat aplikasi dibuka — inilah
   * yang membuat cetak ulang bertahan setelah printer mati atau tablet di-restart.
   */
  void flushPrints({ quiet: true })

  /*
   * Retry terjadwal selama aplikasi terbuka: antrean bukti dan cetak tidak
   * lagi menunggu dibuka ulang, event online, checkout, atau tombol manual.
   * Kedua flush aman dipanggil berkala (single-flight + backoff per item +
   * no-op saat antrean kosong) dan memakai mode quiet supaya pesan yang
   * sedang dibaca kasir tidak tertimpa siklus tanpa hasil.
   */
  clearInterval(queueFlushTimer)
  queueFlushTimer = setInterval(() => {
    void flushProofs({ quiet: true })
    void flushPrints({ quiet: true })
    void autoSendQueue().finally(refreshClockWarning)
  }, QUEUE_FLUSH_INTERVAL_MS)
  void probeServerReachability().finally(refreshClockWarning)

  clearInterval(catalogRefreshTimer)
  catalogRefreshTimer = setInterval(() => {
    void refreshCatalogQuietly()
  }, CATALOG_REFRESH_INTERVAL_MS)

  window.addEventListener('online', handleOnline)
  window.addEventListener('storage', handleSharedCartChange)
})

onBeforeUnmount(() => {
  window.removeEventListener('online', handleOnline)
  window.removeEventListener('storage', handleSharedCartChange)
  clearInterval(retryTimer)
  clearInterval(queueFlushTimer)
  clearInterval(catalogRefreshTimer)
  clearTimeout(searchDebounceTimer)
})

/* ========================================
   SAVE CART
======================================== */

watch(
  cart,
  (newCart) => {
    try {
      localStorage.setItem('posCart', JSON.stringify(newCart))
    } catch {
      checkoutStatus.value = 'review'
      checkoutMessage.value = 'Keranjang tidak dapat disimpan. Checkout ditahan agar nota tidak hilang atau tergandakan.'
    }
  },
  {
    deep: true,
  },
)

/* ========================================
   SEARCH
======================================== */

/*
 * Debounce 500ms: ketikan beruntun hanya memicu satu request GET setelah
 * kasir berhenti mengetik, supaya backend tidak dibanjiri satu request per
 * huruf ("Debouncer untuk minim resource"). Request yang telanjur jalan
 * tetap aman dibatalkan karena loadItems() memakai AbortController.
 */
watch(searchQuery, () => {
  clearTimeout(searchDebounceTimer)
  searchDebounceTimer = setTimeout(() => {
    searchDebounceTimer = null
    /*
     * Search baru harus kembali
     * ke page pertama.
     */

    currentPage.value = 1

    hasMoreItems.value = true

    loadItems(1)
  }, SEARCH_DEBOUNCE_MS)
})

/* ========================================
   ADD TO CART
======================================== */

// Antrean multi-nota (opsi A slice 1): nota yang belum pasti hasilnya tidak
// mengunci keranjang — kasir boleh jualan terus, antrean dikirim per nota.
// Yang mengunci hanya status yang butuh manusia atau pengiriman berjalan.
function isCartLocked() {
  return ['restoring', 'submitting', 'conflict', 'review']
    .includes(checkoutStatus.value)
}

function addToCart(item) {
  if (isCartLocked() || item.price == null || !Number.isFinite(Number(item.price))) {
    return
  }

  /*
   * Cari apakah item sudah ada.
   */

  const existingItem = cart.value.find(
    (cartItem) => getProductKey(cartItem) === getProductKey(item),
  )

  /*
   * Jika sudah ada,
   * tambah quantity.
   */

  if (existingItem) {
    existingItem.quantity += 1
    return
  }

  /*
   * Jika belum ada,
   * tambahkan item baru.
   */

  cart.value.push({
    ...item,
    unit_price: Number(item.price),
    cartId: getProductKey(item),
    quantity: 1,
  })
}

/* ========================================
   MANUAL QUANTITY
======================================== */

function updateCartQuantity({ item, quantity }) {
  if (isCartLocked()) {
    return
  }

  const cartItem = cart.value.find((cartItem) => getProductKey(cartItem) === getProductKey(item))

  if (!cartItem) {
    return
  }

  let newQuantity = Number(quantity)

  /*
   * Invalid input
   */

  if (!Number.isFinite(newQuantity)) {
    newQuantity = 1
  }

  /*
   * Integer only
   */

  newQuantity = Math.floor(newQuantity)

  /*
   * Minimum = 1
   */

  if (newQuantity < 1) {
    newQuantity = 1
  }

  cartItem.quantity = newQuantity
}

function updateCartPrice({ item, price }) {
  if (isCartLocked()) {
    return
  }

  const cartItem = cart.value.find((entry) => getProductKey(entry) === getProductKey(item))

  if (!cartItem) {
    return
  }

  const newPrice = Number(price)
  const floorPrice = cartItem.floor_price
  const ceilingPrice = cartItem.price

  /*
   * Pedoman AGENTS.md: kalau rentang harga hilang, jangan blok penjualan —
   * terima harga pricelist apa adanya; validasi final tetap di server.
   */
  if (floorPrice == null || ceilingPrice == null) {
    if (newPrice === Number(cartItem.unit_price ?? cartItem.price)) {
      cartItem.unit_price = newPrice
    }
    return
  }

  if (
    !Number.isSafeInteger(newPrice) ||
    !Number.isSafeInteger(Number(floorPrice)) ||
    !Number.isSafeInteger(Number(ceilingPrice)) ||
    // Di bawah floor boleh (butuh persetujuan saat bayar, 2026-10-17);
    // di atas pricelist tetap tidak.
    newPrice < 0 ||
    newPrice > Number(ceilingPrice)
  ) {
    return
  }

  cartItem.unit_price = newPrice
}

/* ========================================
   INCREASE QUANTITY
======================================== */

function increaseQuantity(item) {
  if (!isCartLocked()) {
    item.quantity += 1
  }
}

/* ========================================
   DECREASE QUANTITY
======================================== */

function decreaseQuantity(item) {
  if (!isCartLocked() && Number(item.quantity) > 1) {
    item.quantity -= 1
  }
}

/* ========================================
   REMOVE ITEM
======================================== */

function removeFromCart(item) {
  if (isCartLocked()) {
    return
  }

  cart.value = cart.value.filter((cartItem) => getProductKey(cartItem) !== getProductKey(item))
}

/* ========================================
   CLEAR CART
======================================== */

function clearCart() {
  if (!isCartLocked()) {
    cart.value = []
  }
}

/*
 * `sync_id` nota dan `proof_id` bukti memakai generator yang sama
 * (services/id.js) supaya syarat keamanannya konsisten dan tidak bergantung
 * pada secure context.
 */
function createSyncId() {
  return createUuid()
}

async function allocateSequenceRange(device) {
  let range

  try {
    range = await fetchSequenceRange(device)
  } catch {
    /*
     * Alokasi nomor gagal (jaringan/401). Nota boleh tetap dibuat tanpa
     * nomor final; backend menyimpan transaction_id null.
     */
    return false
  }
  if (!range) {
    return false
  }
  const start = Number(range.start_sequence)
  const end = Number(range.end_sequence)

  if (
    !Number.isInteger(start) ||
    !Number.isInteger(end) ||
    start < 1 ||
    end < start ||
    end > 999999
  ) {
    return false
  }

  await saveActiveRange({
    device_id: device.device_id,
    next_sequence: start,
    end_sequence: end,
  })

  return true
}

async function nextTransactionId() {
  const device = await deviceRepository.getDevice()

  if (!device?.device_id || !/^T\d{2}$/.test(device.device_code || '')) {
    return null
  }

  try {
    let range = await getActiveRange(device.device_id)

    if ((!range || range.next_sequence > range.end_sequence) && navigator.onLine) {
      if (await allocateSequenceRange(device)) {
        range = await getActiveRange(device.device_id)
      }
    }

    if (!range || range.next_sequence > range.end_sequence) {
      return null
    }

    const sequence = await consumeNextSequence(device.device_id)
    if (!Number.isInteger(sequence) || sequence < 1 || sequence > 999999) {
      return null
    }

    return `${device.device_code}-${String(sequence).padStart(6, '0')}`
  } catch (error) {
    if (error instanceof SequenceRangeExhaustedError) {
      return null
    }

    throw error
  }
}

function buildTransaction(data) {
  const items = cart.value.map((item) => {
    const unitPrice = Number(item.unit_price ?? item.price)
    const quantity = Number(item.quantity)
    const discount = 0

    return {
      item_name: item.item_name,
      variant: item.variant,
      quantity,
      unit_price: unitPrice,
      discount,
      subtotal: unitPrice * quantity - discount,
    }
  })

  const itemAmount = items.reduce((sum, item) => sum + item.subtotal, 0)
  const shippingAmount = data.deliveryMethod === 'delivery' ? Number(data.shippingCost) : 0
  const totalAmount = itemAmount + shippingAmount
  const paymentAmount = data.paymentMethod === 'cash' ? Number(data.cashReceived) : totalAmount

  return {
    sync_id: createSyncId(),
    transaction_id: null,
    transaction_time: new Date().toISOString(),
    cust_name: String(data.customerName || '').trim() || null,
    phone_no: String(data.customerPhone || '').trim() || null,
    item_amount: itemAmount,
    shipping_amount: shippingAmount,
    total_amount: totalAmount,
    payment_method: data.paymentMethod,
    payment_amount: paymentAmount,
    change_amount: paymentAmount - totalAmount,
    items,
  }
}

function getResponseMessage(body) {
  const detail = body?.detail ?? body?.message ?? body

  if (typeof detail === 'string') {
    return detail
  }

  if (Array.isArray(detail)) {
    return detail
      .map((entry) => typeof entry === 'string' ? entry : JSON.stringify(entry))
      .join(' ')
  }

  if (detail && typeof detail === 'object') {
    return Object.entries(detail)
      .map(([field, value]) => {
        const message = Array.isArray(value)
          ? value.map((entry) => typeof entry === 'string' ? entry : JSON.stringify(entry)).join(', ')
          : typeof value === 'string' ? value : JSON.stringify(value)
        return `${field}: ${message}`
      })
      .join(' ')
  }

  return ''
}

function getRetryDelay(response, body) {
  const retryAfterHeader = response.headers.get('Retry-After')
  const retryAfter = retryAfterHeader === null ? NaN : Number(retryAfterHeader)
  if (Number.isFinite(retryAfter) && retryAfter >= 0) {
    return Math.ceil(retryAfter)
  }

  const waitValue =
    body?.retry_after_seconds ?? body?.retry_after ?? body?.retryAfter ?? body?.wait
  if (waitValue !== undefined && waitValue !== null && waitValue !== '') {
    const seconds = Number(waitValue)
    if (Number.isFinite(seconds) && seconds >= 0) {
      return Math.ceil(seconds)
    }
  }

  const message = getResponseMessage(body)
  const match = message.match(/(?:in|wait|tunggu)\s+(\d+)\s*(?:seconds?|secs?|detik)/i)
  return match ? Number(match[1]) : 0
}

function startRetryCountdown(seconds) {
  clearInterval(retryTimer)
  retryAfterSeconds.value = seconds

  if (seconds <= 0) {
    return
  }

  retryTimer = setInterval(() => {
    retryAfterSeconds.value = Math.max(0, retryAfterSeconds.value - 1)

    if (retryAfterSeconds.value === 0) {
      clearInterval(retryTimer)
      retryTimer = null
    }
  }, 1000)
}

/*
 * Bukti transfer dikirim setelah notanya diterima server. Kegagalan unggah
 * tidak pernah membatalkan nota: nota sudah sah, buktinya yang menyusul
 * (PAYMENT_PROOF_DESIGN.md bagian 7.9).
 */
async function flushProofs({ quiet = false } = {}) {
  try {
    const summary = await flushPaymentProofs()

    if (!quiet || summary.uploaded > 0 || summary.failed > 0) {
      proofSyncMessage.value = summarizeProofSync(summary)
    }
  } catch {
    proofSyncMessage.value = 'Bukti transfer tertunda; akan dicoba lagi otomatis.'
  }
}

/*
 * Snapshot nota untuk kertas. Dipisah dari payload server karena dua alasan:
 * (1) `fulfillment` dan `cashier_name` bukan bagian dari kontrak
 * `POST /api/transactions/`, jadi tidak boleh ikut terkirim; (2) isi nota harus
 * dibekukan saat finalisasi, sebab cart dikosongkan begitu server menerima
 * transaksi dan printer bisa menyusul jauh setelah itu.
 */
function buildReceiptSale(transaction, data) {
  return {
    sync_id: transaction.sync_id,
    transaction_id: transaction.transaction_id,
    transaction_time: transaction.transaction_time,
    cashier_name: data.cashierName,
    cust_name: transaction.cust_name ?? null,
    phone_no: transaction.phone_no ?? null,
    fulfillment: data.deliveryMethod,
    items: transaction.items,
    item_amount: transaction.item_amount,
    shipping_amount: transaction.shipping_amount,
    discount_amount: transaction.items.reduce(
      (sum, item) => sum + (Number(item.discount) || 0),
      0,
    ),
    total_amount: transaction.total_amount,
    payment_method: transaction.payment_method,
    payment_amount: transaction.payment_amount,
    change_amount: transaction.change_amount,
  }
}

/*
 * Cetak adalah upaya terbaik: baris antrean disimpan lebih dulu supaya nota
 * tetap bisa disobek kasir walau server (atau printer) sedang tidak terjangkau.
 * Kegagalan menyiapkan pekerjaan cetak tidak pernah membatalkan penjualan.
 */
async function queueReceiptPrint(sale) {
  try {
    await enqueueReceiptPrint({ sale })
    outstandingPrintJobId.value = null
    printSyncMessage.value = 'Nota masuk antrean cetak.'
  } catch {
    printSyncMessage.value = 'Nota tidak dapat disiapkan untuk printer.'
  }

  await refreshOutstandingPrintJob()
  return (await getPrintJobsBySyncId(sale.sync_id)
    .catch(() => [])).some((job) => job.status !== 'printed')
}

/*
 * Pastikan nota ini punya baris antrean cetak, sekali saja.
 *
 * Dipanggil dua tempat dengan sengaja: sebelum nota dikirim (kalau PIN sudah
 * dibuktikan perangkat) dan lagi setelah server menerima. Pemeriksaan
 * `getPrintJobsBySyncId` menghindari job baru selama job lama masih tersimpan.
 * Ini bukan jaminan exactly-once fisik setelah retensi atau restart penghubung.
 */
async function ensureReceiptPrint(sale) {
  let alreadyQueued

  try {
    alreadyQueued = (await getPrintJobsBySyncId(sale.sync_id)).length > 0
  } catch {
    printSyncMessage.value = 'Antrean cetak tidak dapat diperiksa; jangan membuat pekerjaan cetak pengganti.'
    return false
  }

  if (alreadyQueued) {
    await refreshOutstandingPrintJob()
    return true
  }

  return queueReceiptPrint(sale)
}

/*
 * Satu pembacaan untuk tiga hal yang harus sepakat: job terakhir yang belum
 * keluar (tombol "Cetak ulang"), jumlah nota hari ini (angka di tombol
 * "Riwayat Nota"), dan identitas semua job yang belum tercetak (kunci dismiss).
 * Job terakhir =
 * job terbaru dari daftar yang sudah diurutkan `created_at`, sama seperti
 * `getLatestOutstandingPrintJob` dulu.
 */
async function refreshOutstandingPrintJob() {
  try {
    const jobs = await listOutstandingPrintJobs()
    const job = jobs.length > 0 ? jobs[jobs.length - 1] : null

    outstandingPrintJobId.value = job?.job_id ?? null
    outstandingPrintCount.value = countUnprintedNotasToday(jobs)
    outstandingPrintKey.value = unprintedPrintKey(jobs)
  } catch {
    outstandingPrintJobId.value = null
    outstandingPrintCount.value = 0
    outstandingPrintKey.value = ''
  }
}

async function flushPrints({ quiet = false } = {}) {
  try {
    const summary = await flushPrintJobs()
    const message = summarizePrintSync(summary)

    /*
     * Ringkasan kosong berarti tidak ada nota yang selesai dicetak pada siklus
     * ini. Jangan menimpanya dengan string kosong: bisa jadi siklus ini hanya
     * melanjutkan antrean lama, sementara pesan sebelumnya masih berlaku.
     */
    if (message && (!quiet || summary.printed > 0 || summary.failed > 0 || summary.blocked > 0)) {
      printSyncMessage.value = message
    }
  } catch {
    printSyncMessage.value = 'Pekerjaan cetak tertunda; akan dicoba lagi otomatis.'
  }

  await refreshOutstandingPrintJob()

  /*
   * Percobaan cetak adalah bukti paling nyata soal printer, jadi indikator di
   * header diperbarui dari situ — kalau gagal, kasir langsung tahu sebabnya
   * (printer mati atau bridge-nya).
   */
  void refreshPrinterStatus({ force: true })
}

/*
 * Tombol "Cetak ulang" saat printer sudah hidup lagi. `job_id` lama dipertahankan
 * supaya bridge mengenali kiriman ini sebagai pekerjaan yang sama, bukan nota baru.
 */
async function retryPrint() {
  try {
    /*
     * Semua pekerjaan yang belum keluar dicoba, bukan hanya yang terakhir:
     * transaksi diantar punya dua kertas (nota dan surat jalan).
     */
    const summary = await retryPrintJobNow()

    printSyncMessage.value = summarizePrintSync(summary) || 'Pekerjaan cetak dikirim ulang.'
  } catch {
    printSyncMessage.value = 'Pekerjaan cetak belum bisa dikirim ulang.'
  }

  await refreshOutstandingPrintJob()
  void refreshPrinterStatus({ force: true })
}

async function completeTransaction(data = {}) {
  if (['restoring', 'submitting', 'conflict', 'review'].includes(checkoutStatus.value)) {
    return
  }
  if (retryAfterSeconds.value > 0) {
    return
  }
  if (!pendingTransaction) {
    const priceProblem = findCartPriceProblem(cart.value)
    /*
     * Di bawah floor boleh kalau disetujui: PIN penyetuju (diperiksa server)
     * atau dikirim untuk ACC pemilik. Di atas pricelist tetap ditolak.
     */
    const approved = priceProblem?.problem === PRICE_RANGE_PROBLEM.BELOW
      && (/^\d{6}$/.test(data.approverPin || '') || data.mintaAcc === true)
    if (priceProblem && !approved) {
      checkoutStatus.value = 'error'
      checkoutMessage.value = priceProblem.problem === PRICE_RANGE_PROBLEM.BELOW
        ? `Harga ${itemLabel(priceProblem.item)} di bawah batas floor. Masukkan PIN penyetuju, atau pilih kirim untuk ACC pemilik.`
        : `Harga ${itemLabel(priceProblem.item)} di atas harga pricelist. Perbaiki harga di keranjang, lalu coba lagi.`
      return
    }
  }
  if (!pendingTransaction && !/^\d{6}$/.test(data.pin || '')) {
    return
  }

  // Lock synchronously, before numbering, storage or network awaits.
  checkoutStatus.value = 'submitting'
  checkoutMessage.value = ''
  try {
    if (navigator.locks) {
      await navigator.locks.request('pos-checkout', { ifAvailable: true }, async (lock) => {
        if (!lock) {
          checkoutStatus.value = 'review'
          checkoutMessage.value = 'Checkout sedang berjalan di tab lain. Jangan membuat transaksi pengganti; buka ulang setelah tab itu selesai.'
          return
        }
        await submitSealedTransaction(data)
      })
    } else {
      await submitSealedTransaction(data)
    }
  } catch {
    // Never reopen the cart on a storage error; even the last status write may fail.
    checkoutStatus.value = 'review'
    checkoutMessage.value = 'Status nota tidak dapat disimpan. Jangan ulangi sebagai penjualan baru; periksa penyimpanan perangkat.'
  }
}

/*
 * PIN yang belum dikenal perangkat menahan percobaan berikutnya, bukan
 * penjualan yang sudah sah: nota tersimpan tetap dikirim lewat jalur kirim
 * ulang yang tidak melewati gerbang ini.
 */
function holdForPinCooldown(seconds, reason = '') {
  const wait = Math.max(1, Math.ceil(Number(seconds) || 0))

  checkoutStatus.value = 'pin_cooldown'
  /*
   * Sisa waktunya sengaja tidak ditulis di sini: hitungan berjalan yang
   * ditampilkan komponen (status `pin_cooldown` + `retryAfterSeconds`) supaya
   * angkanya tidak basi dan tidak muncul dua kali.
   */
  checkoutMessage.value = reason
    || 'PIN belum dikenal perangkat ini; percobaan berikutnya ditahan agar PIN tidak bisa ditebak.'
  startRetryCountdown(wait)
}

/*
 * Gerbang PIN sebelum nota disegel (keputusan pemilik 2026-10-08,
 * OFFLINE_SYNC_DESIGN.md §3).
 *
 * Nomor nota dan baris lokal tidak boleh lahir sebelum PIN terbukti. PIN yang
 * cocok di perangkat sudah cukup — itulah yang membuat jualan tetap jalan saat
 * server tidak terjangkau — sedangkan PIN yang tidak dikenal hanya boleh dikirim
 * kalau server memang bisa menjawab. Karena itu pemeriksaan ini dijalankan
 * sebelum `nextTransactionId()` dan `saveTransaction()`, bukan sesudahnya.
 */
async function evaluateSealGate(data) {
  if (data.pinVerifiedLocally) {
    await registerPinSuccess()

    return { allowed: true }
  }

  const cooldown = await getPinCooldownState()

  if (cooldown.blocked) {
    holdForPinCooldown(cooldown.remainingSeconds)

    return { allowed: false }
  }

  /*
   * Bukti yang masih segar dipakai apa adanya; selidik hanya kalau perangkat
   * belum punya bukti sejak dibuka atau server terakhir gagal menjawab.
   */
  const reachable = serverReachability() === true || await probeServerReachability()

  /* Selidik barusan memperbarui bukti modul; pil header ikut dibaca ulang. */
  void refreshServerStatus()

  const after = await registerPinFailure()

  if (!reachable) {
    const offline = 'PIN ini tidak ada di daftar kasir perangkat, dan server tidak terjangkau. Nota tidak dibuat; sambungkan kembali lalu coba lagi, atau minta penanggung jawab menyegarkan daftar kasir.'

    if (after.blocked) {
      holdForPinCooldown(after.remainingSeconds, offline)
    } else {
      checkoutStatus.value = 'error'
      checkoutMessage.value = offline
    }

    return { allowed: false }
  }

  return { allowed: true }
}

async function submitSealedTransaction(data) {
  let timer = null
  try {
    if (!pendingTransaction) {
      let sharedCart
      try {
        sharedCart = JSON.parse(localStorage.getItem('posCart') || '[]')
      } catch {
        checkoutStatus.value = 'review'
        checkoutMessage.value = 'Keranjang bersama tidak dapat dibaca. Checkout ditahan.'
        return
      }
      if (sharedCart[0]?.checkout_sync_id
        && sharedCart[0].checkout_sync_id !== cart.value[0]?.checkout_sync_id) {
        cart.value = sharedCart
        await restorePendingCheckout({ lockHeld: true })
        return
      }
      const gate = await evaluateSealGate(data)
      if (!gate.allowed) {
        return
      }
      const transaction = buildTransaction(data)
      transaction.transaction_id = await nextTransactionId()
      if (!transaction.transaction_id && await isSequenceRangeExhausted()) {
        checkoutStatus.value = 'error'
        checkoutMessage.value = 'Jatah nomor nota habis. Sambungkan kembali agar perangkat mendapat jatah baru; keranjang dan antrean tetap aman.'
        return
      }
      const record = {
        checkout_version: 1,
        sync_id: transaction.sync_id,
        transaction_id: transaction.transaction_id,
        payload: transaction,
        receipt_sale: buildReceiptSale(transaction, data),
        cashier_name: data.cashierName,
        original_pin: data.pin,
        /*
         * Persetujuan harga di bawah floor; ikut dikirim bersama nota. PIN
         * penyetuju dikunci kunci publik server (QA 18 Okt 2026) supaya tidak
         * tersimpan sebagai teks biasa selama nota menunggu terkirim. Tanpa
         * kunci (tablet belum pernah mengunduh daftar kasir), teks biasa.
         */
        ...(await sealedApprover(data.approverPin)),
        minta_acc: data.mintaAcc === true,
        proof_id: data.proofId || null,
        local_final: Boolean(data.pinVerifiedLocally),
        outcome_unknown: false,
        submission_attempts: 0,
        sync_status: 'draft',
        created_at: transaction.transaction_time,
      }
      // Persist nota + proof association atomically before final display/printing.
      await saveTransaction(record)
      showPendingRecord(record)
    }

    const stored = await getTransaction(pendingTransaction.sync_id)
    if (!stored || ['synced', 'rejected', 'review', 'conflict'].includes(stored.sync_status)) {
      await restorePendingCheckout({ lockHeld: true })
      return
    }
    showPendingRecord(stored)
    if (stored.local_final) {
      // Printing is best effort; a failed spool must not hold up a finalized sale.
      await ensureReceiptPrint(stored.receipt_sale)
      void flushPrints({ quiet: true })
    }

    let device = await deviceRepository.getDevice()
    if (!device?.device_token) {
      if (!await runAutoRegistration()) {
        checkoutStatus.value = 'retryable'
        checkoutMessage.value = 'Nota tersimpan. Perangkat belum terdaftar; jangan membuat nota pengganti.'
        return
      }
      device = await deviceRepository.getDevice()
    }

    const attempt = await beginTransactionSubmission(stored.sync_id)
    const controller = new AbortController()
    timer = setTimeout(() => controller.abort(), 30000)
    let response

    try {
      response = await fetch(`${API_BASE_URL}/api/transactions/`, {
        method: 'POST',
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
          ...deviceAuthHeader(device.device_token),
        },
        // Retry UI is not a source of sale facts or cashier credentials.
        /*
         * `final_lokal`: nota ini sudah final di tablet (PIN cocok lokal,
         * sudah dicetak). Server tidak menolaknya mentah-mentah; kalau gagal
         * diperiksa, nota ditahan untuk ACC pemilik di master.
         */
        body: JSON.stringify({
          pin: attempt.original_pin,
          transaction: attempt.payload,
          final_lokal: Boolean(attempt.local_final),
          ...(attempt.approver_pin ? { approver_pin: attempt.approver_pin } : {}),
          ...(attempt.approver_pin_sandi ? { approver_pin_sandi: attempt.approver_pin_sandi } : {}),
          ...(attempt.minta_acc ? { minta_acc: true } : {}),
        }),
      })
    } catch (error) {
      /* Tidak ada jawaban sama sekali — bukti untuk gerbang PIN berikutnya. */
      noteServerSilence()
      throw error
    }
    /*
     * Bukti hidup hanya untuk 2xx (aturan probe, `serverReachability.js`):
     * 502 dari proxy dev berarti backend mati, bukan hidup. Mencatatnya
     * sebagai jawaban pernah membuka gerbang PIN untuk server yang mati.
     */
    if (response.ok) {
      noteServerAnswer()
    } else {
      noteServerSilence()
    }
    const body = await response.json()
    clearTimeout(timer)
    timer = null

    if ([200, 201].includes(response.status)) {
      const responseMatches = body.sync_id === attempt.sync_id
        && body.transaction_id === attempt.transaction_id
        && body.status === (response.status === 201 ? 'new' : 'idempotent')
      if (!responseMatches) {
        // Persist contradictory/malformed success as review; do not mutate or resend blindly.
        await updateTransactionStatus(attempt.sync_id, 'review', {
          rejection_detail: 'Respons sukses tidak cocok dengan identitas nota.',
          last_http_status: response.status,
        })
        checkoutStatus.value = 'review'
        checkoutMessage.value = 'Server mengirim konfirmasi yang tidak cocok dengan nota lokal. Minta pemeriksaan; jangan buat nota pengganti.'
        return
      }
      /*
       * Server menerima PIN ini, jadi hitungan kegagalan lokal dihapus: PIN
       * yang sah tidak boleh mewarisi blokir dari salah ketik sebelumnya.
       */
      await registerPinSuccess()
      // Server acceptance finalizes the sale even when the best-effort print queue is unavailable.
      await ensureReceiptPrint(attempt.receipt_sale)
      await updateTransactionStatus(attempt.sync_id, 'synced', {
        server_status: body.status,
        rejection_detail: null,
      })
      cart.value = []
      pendingTransaction = null
      pendingReceipt.value = null
      checkoutResult.value = body
      await refreshQueue()
      checkoutStatus.value = 'success'
      if (body.ditahan) {
        checkoutMessage.value = `${body.detail || 'Nota terkirim dan menunggu ACC pemilik.'} Nota tetap sah di tablet; tidak perlu dibuat ulang.`
      } else {
        checkoutMessage.value = response.status === 201
          ? 'Transaksi berhasil disimpan.' : 'Nota yang sama sudah tercatat; stok tidak dipotong lagi.'
      }
      void flushProofs()
      void refreshWorkerDirectoryQuietly()
      void flushPrints()
      /*
       * Skenario B (permintaan pemilik 2026-10-12, council 2026-10-12:
       * only-if-stale, bukan paksa): transaksi online yang sukses adalah bukti
       * server hidup, jadi salinan katalog ikut dicek di latar — hanya diunduh
       * ulang kalau sudah basi (ambang 1 hari). `force: true` berarti unduh
       * penuh ±98 halaman (±4 menit) setiap nota, padahal isinya sama dengan
       * yang timer 15 menit ambil; bukti terukur di laporan council. Tidak
       * menahan kasir (fire-and-forget, single-flight), dan tidak menyusun
       * ulang baris di layar: kalau daftar yang tampil masih dari salinan,
       * hanya penyimpanan yang diperbarui. Gagal sync tidak mengubah status
       * sukses.
       */
      void refreshCatalogQuietly()
      return
    }

    const message = getResponseMessage(body)
    const definiteReply = body && typeof body === 'object' && !Array.isArray(body)
      && (typeof body.detail === 'string' || body.pin || body.transaction)
    if (!definiteReply) {
      throw new Error('Jawaban server tidak lengkap.')
    }
    const details = {
      rejection_detail: message || `Permintaan ditolak server (HTTP ${response.status}).`,
      last_http_status: response.status,
    }
    const earlierUnknown = attempt.outcome_unknown

    if (response.status === 409 || response.status === 400) {
      const needsReview = response.status === 409 || earlierUnknown || attempt.local_final
      if (needsReview) {
        const status = response.status === 409 ? 'conflict' : 'review'
        await updateTransactionStatus(attempt.sync_id, status, details)
        checkoutStatus.value = status
        checkoutMessage.value = `${message} Nota final/ambigu ditahan untuk pemeriksaan. Jangan hapus atau buat pengganti.`
      } else {
        await recordDefiniteAttemptFailure(attempt.sync_id, 'rejected', details, {
          previousOutcomeUnknown: earlierUnknown,
        })
        pendingTransaction = null
        pendingReceipt.value = null
        checkoutResult.value = null
        checkoutStatus.value = 'error'
        checkoutMessage.value = `${message} Ditolak pasti pada pengiriman pertama; catatan dan bukti tetap disimpan.`
      }
      return
    }

    if ([401, 429].includes(response.status)) {
      const delay = response.status === 429 ? getRetryDelay(response, body) : 0
      details.retry_after_until = delay > 0 ? new Date(Date.now() + delay * 1000).toISOString() : null
      if (earlierUnknown) {
        await updateTransactionStatus(attempt.sync_id, 'unknown', details)
      } else {
        await recordDefiniteAttemptFailure(attempt.sync_id, 'pending', details, {
          previousOutcomeUnknown: earlierUnknown,
        })
      }
      checkoutStatus.value = response.status === 429 ? 'rate_limited' : 'retryable'
      checkoutMessage.value = `${message} Nota dan PIN asli tetap disimpan; jawaban ini tidak membatalkan kemungkinan percobaan sebelumnya sudah tercatat.`
      if (response.status === 401) {
        deviceRegistered.value = false
      }
      startRetryCountdown(delay)
      return
    }

    throw new Error('Hasil pengiriman belum diketahui.')
  } catch {
    if (checkoutStatus.value === 'review') {
      checkoutMessage.value = 'Penyimpanan keranjang bermasalah. Nota tetap ditahan; jangan membuat transaksi pengganti.'
      return
    }
    if (pendingTransaction) {
      const record = await getTransaction(pendingTransaction.sync_id)
      if (record?.sync_status === 'syncing') {
        await updateTransactionStatus(record.sync_id, 'unknown')
      } else if (record?.sync_status === 'draft' && record.outcome_unknown) {
        await updateTransactionStatus(record.sync_id, 'syncing')
        await updateTransactionStatus(record.sync_id, 'unknown')
      } else if (!record || ['review', 'conflict', 'synced', 'rejected'].includes(record.sync_status)) {
        await restorePendingCheckout({ lockHeld: true })
        return
      }
      // Antrean multi-nota (opsi A slice 1): nota yang gagal terkirim masuk
      // antrean, keranjang dibersihkan untuk penjualan berikutnya. Kasir
      // mengirim ulang per nota dari banner antrean.
      await refreshQueue()
      cart.value = cart.value.filter(
        (item) => item.checkout_sync_id !== pendingTransaction.sync_id)
      try {
        localStorage.setItem('posCart', JSON.stringify(cart.value))
      } catch {
        checkoutStatus.value = 'review'
        checkoutMessage.value = 'Penyimpanan keranjang bermasalah. Nota tetap ditahan; jangan membuat transaksi pengganti.'
        return
      }
      pendingTransaction = null
      pendingReceipt.value = null
      checkoutResult.value = null
      checkoutStatus.value = 'idle'
      checkoutMessage.value = ''
      return
    }
    // Failed local writes/numbering must be checked before allowing another sale.
    checkoutStatus.value = 'review'
    checkoutMessage.value = 'Nota tidak dapat disiapkan dengan aman. Buka ulang dan periksa antrean lokal; jangan membuat pengganti.'
  } finally {
    clearTimeout(timer)
  }
}

/*
 * Jatah nomor habis yang sudah pasti (opsi A slice 1, §2.4): rentang aktif
 * ada tetapi nomor berikutnya melewati batasnya. Alokasi yang gagal karena
 * jaringan/401 bukan bukti habis — jalurnya tetap legacy (nota null).
 */
async function isSequenceRangeExhausted() {
  try {
    const device = await deviceRepository.getDevice()
    if (!device?.device_id) { return false }
    const range = await getActiveRange(device.device_id)
    return Boolean(range) && Number(range.next_sequence) > Number(range.end_sequence)
  } catch {
    return false
  }
}

/*
 * Kirim seluruh antrean sebagai satu batch (slice 2 PIN-per-nota): tiap nota
 * memakai payload dan PIN asli tersimpan; keranjang draft tidak disentuh.
 * Berbeda dari kirim per-nota, status checkout tidak dipakai — penjualan boleh
 * jalan terus selagi batch terbang; tombol dikunci lewat `batchSyncState`.
 *
 * Pemetaan hasil mengikuti mesin status lokal (`transactionRepository.js`):
 * `syncing` tidak boleh langsung `rejected`, dan nota final/ambigu tidak boleh
 * ditolak pasti — karena itu `rejected` server untuk nota final/ambigu menjadi
 * `review`, dan hanya penolakan pertama nota biasa yang menjadi `rejected`.
 */
const batchSyncState = ref('idle')
const batchSyncMessage = ref('')

async function resendQueuedBatch() {
  if (batchSyncState.value !== 'idle') {
    return
  }
  if (['restoring', 'submitting', 'conflict', 'review'].includes(checkoutStatus.value)
    || retryAfterSeconds.value > 0) {
    return
  }
  batchSyncState.value = 'sending'
  batchSyncMessage.value = ''

  try {
    let queued
    try {
      queued = (await getUnresolvedTransactions())
        .filter((record) => ['draft', 'pending', 'unknown'].includes(record.sync_status))
        .sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || '')))
        .slice(0, 200)
    } catch {
      batchSyncMessage.value = 'Antrean tidak dapat dibaca. Periksa penyimpanan perangkat; antrean tetap aman.'
      return
    }

    if (queued.length === 0) {
      await refreshQueue()
      return
    }

    let device = await deviceRepository.getDevice()
    if (!device?.device_token) {
      if (!await runAutoRegistration()) {
        batchSyncMessage.value = 'Perangkat belum terdaftar; antrean tetap aman, kirim lagi setelah terdaftar.'
        return
      }
      device = await deviceRepository.getDevice()
    }

    /* Tandai syncing dulu seperti jalur satuan: tablet mati kapan pun sesudah
     * ini berarti ambigu, bukan berarti belum dicoba. Nota tanpa PIN tersimpan
     * (atau status yang berubah) dilewati — tetap di antrean. */
    const attempts = []
    for (const record of queued) {
      try {
        attempts.push(await beginTransactionSubmission(record.sync_id))
      } catch {
        /* Dilewati; bukan kegagalan batch. */
      }
    }

    if (attempts.length === 0) {
      await refreshQueue()
      batchSyncMessage.value = 'Tidak ada nota yang siap dikirim; periksa antrean.'
      return
    }

    let body
    try {
      body = await syncTransactionBatch(
        device.device_token,
        attempts.map((attempt) => ({
          ...attempt.payload,
          pin: attempt.original_pin,
          final_lokal: Boolean(attempt.local_final),
          ...(attempt.approver_pin ? { approver_pin: attempt.approver_pin } : {}),
          ...(attempt.approver_pin_sandi ? { approver_pin_sandi: attempt.approver_pin_sandi } : {}),
          ...(attempt.minta_acc ? { minta_acc: true } : {}),
        })),
      )
      noteServerAnswer()
    } catch (error) {
      noteServerSilence()
      await handleBatchRequestError(error, attempts)
      return
    }

    await applyBatchResults(body, attempts)
  } finally {
    batchSyncState.value = 'idle'
  }
}

/* Seluruh request batch ditolak (401/429) atau jawabannya tidak bisa dipakai:
 * catat status per nota supaya jejaknya sama seperti jalur satuan. */
async function handleBatchRequestError(error, attempts) {
  const message = getResponseMessage(error?.body) || error?.message || 'Pengiriman antrean gagal.'

  if (error && (error.status === 401 || error.status === 429)) {
    const delay = error.status === 429
      ? getRetryDelay({ headers: error.headers }, error.body)
      : 0
    const details = {
      rejection_detail: `${message} Nota dan PIN asli tetap disimpan.`,
      last_http_status: error.status,
      ...(delay > 0
        ? { retry_after_until: new Date(Date.now() + delay * 1000).toISOString() }
        : {}),
    }
    for (const attempt of attempts) {
      try {
        if (attempt.outcome_unknown) {
          await updateTransactionStatus(attempt.sync_id, 'unknown', details)
        } else {
          await recordDefiniteAttemptFailure(attempt.sync_id, 'pending', details, {
            previousOutcomeUnknown: attempt.outcome_unknown,
          })
        }
      } catch {
        /* Satu baris macet tidak boleh menahan baris lain. */
      }
    }
    if (error.status === 401) {
      deviceRegistered.value = false
    }
    startRetryCountdown(delay)
    batchSyncMessage.value = `${message} Antrean tetap aman; coba lagi setelah jeda.`
    await refreshQueue()
    return
  }

  for (const attempt of attempts) {
    try {
      await updateTransactionStatus(attempt.sync_id, 'unknown', {
        rejection_detail: `${message} Hasil pengiriman belum diketahui; jangan membuat pengganti.`,
        ...(typeof error?.status === 'number' ? { last_http_status: error.status } : {}),
      })
    } catch {
      /* Satu baris macet tidak boleh menahan baris lain. */
    }
  }
  batchSyncMessage.value = `${message} Hasil belum diketahui; antrean ditahan, jangan membuat pengganti.`
  await refreshQueue()
}

/* Petakan hasil per-nota ke status lokal; satu baris macet tidak menahan lain.
 * Keranjang tidak disentuh: batch hanya mengurus antrean. */
async function applyBatchResults(body, attempts) {
  const results = Array.isArray(body?.results) ? body.results : []
  const bySyncId = new Map(results.map((result) => [result?.sync_id, result]))
  const syncedRecords = []
  let sent = 0
  let rejected = 0
  let conflicted = 0
  let unclear = 0

  for (const attempt of attempts) {
    const result = bySyncId.get(attempt.sync_id)
    try {
      if (!result || !['new', 'idempotent', 'rejected', 'conflict'].includes(result.status)) {
        await updateTransactionStatus(attempt.sync_id, 'unknown', {
          rejection_detail: 'Jawaban server untuk nota ini tidak lengkap; jangan membuat pengganti.',
          last_http_status: 200,
        })
        unclear += 1
      } else if (result.status === 'new' || result.status === 'idempotent') {
        await updateTransactionStatus(attempt.sync_id, 'synced', {
          server_status: result.status,
          rejection_detail: null,
          last_http_status: 200,
        })
        sent += 1
        syncedRecords.push(attempt)
      } else if (result.status === 'conflict') {
        await updateTransactionStatus(attempt.sync_id, 'conflict', {
          rejection_detail: result.detail || 'Konflik identitas; butuh pemeriksaan.',
          last_http_status: 200,
        })
        conflicted += 1
      } else if (attempt.local_final || attempt.outcome_unknown) {
        await updateTransactionStatus(attempt.sync_id, 'review', {
          rejection_detail: result.detail || 'Ditolak server; nota final/ambigu ditahan untuk pemeriksaan.',
          last_http_status: 200,
        })
        rejected += 1
      } else {
        await recordDefiniteAttemptFailure(attempt.sync_id, 'rejected', {
          rejection_detail: result.detail || 'Ditolak server pada pengiriman pertama; catatan tetap disimpan.',
          last_http_status: 200,
        }, { previousOutcomeUnknown: attempt.outcome_unknown })
        rejected += 1
      }
    } catch {
      unclear += 1
    }
  }

  for (const record of syncedRecords) {
    await ensureReceiptPrint(record.receipt_sale)
  }

  if (sent > 0) {
    await registerPinSuccess()
    void flushProofs()
    void refreshWorkerDirectoryQuietly()
    void flushPrints()
    /* Skenario B juga berlaku untuk batch: ada nota yang baru terbukti
     * sampai ke server, jadi salinan katalog dicek di latar (only-if-stale,
     * bukan paksa — lihat catatan di jalur sukses tunggal di atas). */
    void refreshCatalogQuietly()
  }

  await refreshQueue()

  const parts = [`${sent} nota terkirim`]
  if (rejected > 0) { parts.push(`${rejected} ditolak`) }
  if (conflicted > 0) { parts.push(`${conflicted} konflik`) }
  if (unclear > 0) { parts.push(`${unclear} belum jelas`) }
  batchSyncMessage.value = rejected + conflicted + unclear > 0
    ? `${parts.join(', ')}. Nota yang ditolak/konflik/belum jelas butuh pemeriksaan; jangan membuat pengganti.`
    : `${parts.join(', ')}.`
}

/*
 * Kirim ulang satu nota antrean (opsi A slice 1): memakai payload dan PIN
 * asli tersimpan; keranjang draft tidak disentuh. Nota review/conflict tidak
 * boleh dikirim ulang dari sini — butuh manusia.
 */
async function resendQueuedTransaction(syncId) {
  if (!syncId || ['restoring', 'submitting', 'conflict', 'review'].includes(checkoutStatus.value)) {
    return
  }
  const record = await getTransaction(syncId)
  if (!record || !['draft', 'pending', 'unknown'].includes(record.sync_status)) {
    await refreshQueue()
    return
  }
  pendingTransaction = record.payload
  pendingReceipt.value = record.receipt_sale || null
  checkoutResult.value = { sync_id: record.sync_id, transaction_id: record.transaction_id }
  await submitSealedTransaction({})
}

async function handleDeviceRegistered() {
  deviceRegistered.value = true
  checkoutMessage.value = 'Device terdaftar. Jika ada nota tertunda, kirim ulang nota yang sama; jangan membuat pengganti.'
}

function finishCheckout() {
  if (pendingTransaction || isCartLocked()) {
    return
  }

  checkoutStatus.value = 'idle'
  checkoutMessage.value = ''
  checkoutResult.value = null
  proofSyncMessage.value = ''
  printSyncMessage.value = ''
  outstandingPrintJobId.value = null
  retryAfterSeconds.value = 0
  clearInterval(retryTimer)
  retryTimer = null
}

onBeforeUnmount(() => {
  window.removeEventListener('online', handleOnline)
  window.removeEventListener('storage', handleSharedCartChange)
  clearInterval(retryTimer)
  clearInterval(queueFlushTimer)
  clearTimeout(recoveryTimer)
  clearTimeout(searchDebounceTimer)
})
</script>

<template>
  <main class="pos-page">
    <div class="pos-layout">
      <!-- ==================================
           PRODUCT SECTION
      =================================== -->

      <section class="product-section">
        <header class="page-header">
          <h1>POS</h1>

          <div class="header-actions">
            <!--
              Status printer (permintaan pemilik, 2026-09-30): tombol yang
              melebar ke bawah berisi detail koneksi, supaya kasir bisa
              membedakan printer mati dari penghubung cetak yang mati tanpa
              membuka mesin. Bahasanya sengaja untuk orang toko.
            -->
            <!--
              Pil status server (permintaan pemilik, 2026-10-09): hijau/merah
              otomatis dari bukti keterjangkauan terakhir; ketuk untuk memeriksa
              ulang. Tidak mengirim antrean — itu tetap tugas tombol
              "Kirim semua".
            -->
            <ServerStatusPill
              :tone="serverTone"
              :label="serverLabel"
              :title="serverTitle"
              :checking="serverChecking"
              @refresh="refreshServerStatus({ force: true })"
            />

            <PrinterStatusPanel
              :tone="printerTone"
              :label="printerLabel"
              :short-label="printerShortLabel"
              :title="printerTitle"
              :detail="printerDetail"
              :printer-name="printerName"
              :printer-address="printerAddress"
              :printer-status-label="printerStatusLabel"
              :checked-at-label="checkedAtLabel"
              :next-steps="printerNextSteps"
              :checking="printerChecking"
              @refresh="refreshPrinterStatus({ force: true })"
            />

            <button
              type="button"
              class="theme-toggle"
              :aria-label="isDark ? 'Aktifkan mode terang' : 'Aktifkan mode gelap'"
              :title="isDark ? 'Mode terang' : 'Mode gelap'"
              @click="toggleTheme"
            >
              <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <template v-if="isDark"><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" /></template>
                <path v-else d="M20.5 13A8.5 8.5 0 0 1 11 3.5 8.5 8.5 0 1 0 20.5 13Z" />
              </svg>
            </button>

          </div>

          <!--
            Riwayat Nota sengaja tidak sebaris dengan tombol status di atas
            (audit tampilan, 2026-10-09): di jendela <=480px empat tombol tidak
            pernah muat, dan sebagai saudara `.header-actions` tombol ini bisa
            mengambil satu baris penuh sendiri — label di kiri, lencana di
            kanan — tanpa memaksa tombol lain diperas di bawah ukuran jari.
          -->
          <button
            type="button"
            class="history-button"
            :class="{ 'has-queue': historyNotice.hasQueued, 'has-print-alert': historyNotice.hasPrintAlert }"
            :title="historyNotice.title"
            :aria-label="historyNotice.ariaLabel"
            @click="historyVisible = true"
          >
            Riwayat Nota

            <!--
              Penanda tunggu di tombol ini (permintaan pemilik, 2026-10-09 dan
              2026-10-10): jumlah nota yang belum tersinkron ke server dan
              jumlah nota yang cetaknya tertunda. Keduanya dalam satu wadah
              karena di layar <=480px tombol memakai `justify-content:
              space-between`: kalau keduanya jadi anak langsung tombol,
              penandanya tersebar di tengah tombol dan bukan berkelompok di
              kanan.

              Artinya tidak boleh bergantung pada warna saja, jadi tiap
              hitungan membawa glyph-nya sendiri: panah-kirim untuk server,
              printer untuk kertas. Angkanya dekoratif - kalimat penuhnya dibawa
              `title`/`aria-label` tombol ini supaya pembaca layar tidak
              mendengar angka telanjang.
            -->
            <span v-if="historyNotice.showStatuses" class="history-statuses">
              <span
                v-if="historyNotice.hasQueued"
                class="history-count is-sync"
                aria-hidden="true"
              >
                <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5" /><path d="m5 12 7-7 7 7" /></svg>
                {{ historyNotice.queued }}
              </span>

              <span
                v-if="historyNotice.hasPrintAlert"
                class="history-count is-print"
                aria-hidden="true"
              >
                <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="9" width="14" height="7" rx="1.5" /><path d="M8 9V4h8v5" /><path d="M8 16v4h8v-4" /></svg>
                {{ historyNotice.unprinted }}
              </span>
            </span>
          </button>
        </header>

        <!-- SEARCH -->

        <SearchBar v-model="searchQuery" />

        <!-- PRODUCT COUNT -->

        <p class="product-count">
          {{ totalItems }}
          varian siap
        </p>

        <!--
          Asal katalog: dari server, atau salinan perangkat saat server tidak
          menjawab (OFFLINE_SYNC_DESIGN.md §3.3). Kasir perlu tahu bedanya
          karena harga/stok salinan adalah snapshot terakhir.
        -->

        <p v-if="catalogNotice" class="catalog-notice" role="status">
          {{ catalogNotice }}
        </p>

        <p v-if="clockWarning" class="clock-warning" role="alert">
          {{ clockWarning }}
        </p>

        <!-- PRODUCT LIST -->

        <ProductList
          :items="items"
          :loading="loadingItems"
          :has-more="hasMoreItems"
          :selection-disabled="isCartLocked()"
          @add-to-cart="addToCart"
          @load-more="loadMoreItems"
        />
      </section>

      <!-- ==================================
           CART SECTION
      =================================== -->

      <section class="cart-section">
        <CartList
          :cart-items="cart"
          @increase="increaseQuantity"
          @decrease="decreaseQuantity"
          @remove="removeFromCart"
          @clear="clearCart"
          :checkout-status="checkoutStatus"
          :checkout-message="checkoutMessage"
          :checkout-result="checkoutResult"
          :retry-after-seconds="retryAfterSeconds"
          :proof-sync-message="proofSyncMessage"
          @update-quantity="updateCartQuantity"
          @update-price="updateCartPrice"
          @complete-transaction="completeTransaction"
          @checkout-finished="finishCheckout"
          :pending-receipt="pendingReceipt"
        />
      </section>
    </div>

    <!--
      Notifikasi antrean + aksi kirim pindah ke sini (permintaan pemilik,
      2026-10-09): kontraknya sama seperti yang dulu dipakai CartList — App
      tetap pemilik gerbang status dan PIN asli tiap nota.
    -->
    <TransactionHistory
      :visible="historyVisible"
      :queued-notices="queuedNotices"
      :unprinted-count="outstandingPrintCount"
      :print-alert-key="outstandingPrintKey"
      :batch-sending="batchSyncState !== 'idle'"
      :batch-message="batchSyncMessage"
      :checkout-status="checkoutStatus"
      :checkout-message="checkoutMessage"
      :checkout-result="checkoutResult"
      :print-sync-message="printSyncMessage"
      :print-job-id="outstandingPrintJobId"
      @retry-print="retryPrint"
      @resend-queued="resendQueuedTransaction"
      @resend-batch="resendQueuedBatch"
      @close="historyVisible = false"
    />

    <DeviceRegistrationModal
      :visible="!deviceRegistered"
      :cancellable="registrationDismissible"
      :registration-state="registrationState"
      :error-message="registrationError"
      @registered="handleDeviceRegistered"
      @dismiss="handleRegistrationDismissed"
    />
  </main>
</template>

<style scoped>
/* ========================================
   POS PAGE
======================================== */

.pos-page {
  width: 100%;
  height: 100vh;

  padding: 18px 22px;

  box-sizing: border-box;

  background: var(--pos-bg);
  color: var(--pos-text);

  overflow: hidden;
}

/* ========================================
   MAIN LAYOUT
======================================== */

.pos-layout {
  display: grid;

  /*
   * Keranjang dibatasi lebarnya supaya tidak memakan seluruh sisi kanan
   * layar tablet (permintaan pemilik, 2026-09-29). Pemisahnya cukup garis di
   * sisi kiri panel keranjang.
   */

  grid-template-columns:
    minmax(0, 1fr)
    minmax(300px, 360px);

  gap: 20px;

  width: 100%;
  height: 100%;

  max-width: 1800px;

  margin: 0 auto;

  min-height: 0;
}

/* ========================================
   PRODUCT SECTION
======================================== */

.product-section {
  display: flex;
  flex-direction: column;

  width: 100%;
  height: 100%;

  min-width: 0;
  min-height: 0;

  overflow: hidden;
}

/* ========================================
   CART SECTION
======================================== */

.cart-section {
  width: 100%;
  height: 100%;

  min-width: 0;
  min-height: 0;
}

/* ========================================
   PAGE HEADER
======================================== */

.page-header {
  display: flex;

  align-items: center;

  /*
   * Judul didorong ke kiri oleh `margin-right: auto`, sisanya menempel ke kanan
   * dengan jarak tetap — sama seperti susunan sebelumnya, tetapi tombol Riwayat
   * Nota tidak lagi terkunci di dalam kelompok tombol status.
   */
  justify-content: flex-end;

  /*
   * Judul dan tombol status berbagi satu baris selama muat; kalau tidak, tombol
   * pindah ke baris kedua dengan sengaja (audit tampilan, 2026-10-09). Tanpa
   * ini, di jendela 390px baris tombol melewati batas header dan bagian kanan
   * "Riwayat Nota" beserta lencananya terpotong `overflow: hidden` milik kolom
   * daftar item.
   */
  flex-wrap: wrap;

  gap: 10px 12px;

  flex-shrink: 0;

  margin-bottom: 16px;
}

/* ========================================
   HEADER ACTIONS
======================================== */

.header-actions {
  display: flex;

  align-items: center;

  /*
   * Sama seperti header: tombol tidak pernah diperas di bawah ukuran jari,
   * mereka pindah baris. Flexbox bawaan menekan tombol tema 48px menjadi 38px
   * pada jendela sempit.
   */
  flex-wrap: wrap;

  gap: 10px;
}

/*
 * Tombol status printer beserta panel detailnya tinggal di
 * `components/PrinterStatusPanel.vue` supaya ukuran, warna, dan aturan layar
 * sempitnya ada di satu berkas.
 */

/* ========================================
   THEME TOGGLE
======================================== */

.theme-toggle {
  display: flex;

  justify-content: center;

  align-items: center;

  flex-shrink: 0;

  width: 48px;
  height: 48px;

  padding: 0;

  border: 1px solid var(--pos-border);
  border-radius: 8px;

  background: var(--pos-surface);

  color: var(--pos-text);

  font-size: 22px;
  line-height: 1;

  cursor: pointer;

  transition:
    background-color 0.15s ease,
    border-color 0.15s ease;
}

.theme-toggle:hover {
  background: var(--pos-hover);

  border-color: var(--pos-accent);
}

/* ========================================
   HISTORY BUTTON
======================================== */

.history-button {
  display: inline-flex;

  align-items: center;

  justify-content: center;

  flex-shrink: 0;

  /*
   * Label + lencana selalu sebaris (audit tampilan, 2026-10-09): sebagai
   * `display: block`, lencana antrean turun ke baris kedua di dalam tombol
   * 44px sehingga isinya melewati kotaknya sendiri dan tombolnya terlihat
   * bergerigi dibanding tombol sebelahnya.
   */
  gap: 8px;

  min-height: 48px;

  padding: 0 18px;

  border: 1px solid var(--pos-border);

  border-radius: 8px;

  background: var(--pos-surface);

  color: var(--pos-text);

  font-size: 15px;

  font-weight: 600;

  cursor: pointer;

  transition:
    background-color 0.15s ease,
    border-color 0.15s ease;
}

.history-button:hover {
  background: var(--pos-hover);

  border-color: var(--pos-accent);
}

/*
 * Dua hitungan tunggu di tombol "Riwayat Nota" (permintaan pemilik, 2026-10-10).
 * Tombolnya tetap tombol biasa; penandanya yang menarik perhatian kasir tanpa
 * memakan satu baris layar seperti baner di keranjang dulu. Batasnya sama: ini
 * pemberitahuan, bukan penghalang — penjualan berikutnya tetap bisa dimulai.
 *
 * Urutan dua aturan border ini disengaja: kalau kedua keadaan terjadi
 * bersamaan, garis merah (belum tersinkron) yang menang karena itu yang paling
 * mendesak.
 */

.history-button.has-print-alert {
  border-color: var(--pos-warning-border);
}

.history-button.has-queue {
  border-color: var(--pos-danger);
}

/*
 * Wadah penanda tunggu. Di layar <=480px inilah yang berkelompok di kanan saat
 * tombol memakai `justify-content: space-between`.
 */
.history-statuses {
  display: inline-flex;

  align-items: center;

  gap: 6px;

  flex-shrink: 0;
}

/*
 * Satu hitungan: glyph + angka, di dalam pil kecil. Ukurannya bukan sasaran
 * sentuh (tombolnya yang jadi sasaran, >= 44px) tetapi tetap harus terbaca
 * sebagai bentuk — karena itu tiap keadaan punya glyph-nya sendiri, bukan
 * hanya warna.
 */
.history-count {
  display: inline-flex;

  align-items: center;

  gap: 4px;

  min-width: 24px;
  height: 22px;

  padding: 0 7px;

  border-radius: 11px;

  font-size: 12px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

/*
 * Belum tersinkron ke server: keluarga merah (`--pos-danger-bg` +
 * `--pos-danger`, terukur 5,1:1 di terang dan 7,3:1 di gelap). Kasir boleh
 * mengirim ulang sendiri, tetapi catatannya belum sampai ke server — karena itu
 * yang paling mendesak dari dua keadaan ini (pilihan pemilik, 2026-10-10).
 */
.history-count.is-sync {
  background: var(--pos-danger-bg);
  color: var(--pos-danger);
}

/*
 * Cetak tertunda: keluarga amber yang sama dengan chip "Cetak tertunda" di
 * modal (`--pos-warning-text`: 5,9:1 di terang, 10,6:1 di gelap). Ambang
 * "tertunda"-nya di `utils/printDelay.js`: pekerjaan yang sudah pernah dicoba,
 * jadi angkanya tidak berkedip di tiap penjualan.
 */
.history-count.is-print {
  background: var(--pos-warning-bg);
  color: var(--pos-warning-text);
}

.history-count svg {
  flex-shrink: 0;
}

.page-header h1 {
  margin: 0 auto 0 0;

  color: var(--pos-text);

  font-size: 34px;

  font-weight: 700;
}

/* ========================================
   PRODUCT COUNT
======================================== */

.product-count {
  flex-shrink: 0;

  margin: 10px 0;

  color: var(--pos-text-muted);

  font-size: 15px;
}

/* Baris status katalog tersimpan: hanya muncul saat salinan offline dipakai
   atau saat salinannya sudah basi. Warnanya token yang sama dengan teks
   sekunder — ini keterangan, bukan peringatan yang menghalangi penjualan. */
.clock-warning {
  flex-shrink: 0;

  margin: 0 0 10px;
  padding: 8px 12px;

  border: 1px solid var(--pos-warning-border);
  border-radius: 8px;

  background: var(--pos-warning-bg);
  color: var(--pos-warning-text);

  font-size: 14px;
}

.catalog-notice {
  flex-shrink: 0;

  margin: 0 0 10px;

  color: var(--pos-text-muted);

  font-size: 14px;
}

/* ========================================
   TABLET & LAYAR SEMPIT
======================================== */

/*
 * Ambang 900px (bukan 1024px): tablet Galaxy Tab A7 Lite melaporkan viewport
 * 1007x601 pada posisi mendatar, dan pada lebar itu keranjang harus tetap di
 * sisi kanan daftar item. Yang perlu ditumpuk hanya layar yang benar-benar
 * sempit, misalnya tablet posisi tegak (601px).
 */

@media (max-width: 900px) {
  .pos-page {
    height: auto;
    min-height: 100vh;

    overflow: visible;
  }

  .pos-layout {
    display: flex;

    flex-direction: column;

    height: auto;
  }

  .product-section {
    height: 62vh;
    min-height: 380px;
  }

  .cart-section {
    height: auto;
  }
}

/*
 * Jendela paling sempit (audit tampilan, 2026-10-09). Diukur: empat tombol
 * header butuh 334px termasuk jarak (pil server 44 + printer 53 + tema 48 +
 * Riwayat Nota 159 + tiga jarak 30). Pada jendela 360px, ruang isinya hanya
 * ~299px, jadi baris tombol tidak pernah muat. Alih-alih diperas (tombol tema
 * pernah menciut ke 38px dan lencana antrean terpotong), tombol Riwayat Nota
 * diberi satu baris penuh sendiri: label di kiri, lencana di kanan. Sisa tiga
 * tombol status duduk bersama judul dan tetap muat (~279px dari ~299px).
 */

@media (max-width: 480px) {
  .history-button {
    flex: 1 1 100%;

    justify-content: space-between;
  }
}

/*
 * Layar pendek (tablet mendatar hanya sekitar 600px tinggi): rapatkan jarak
 * luar saja agar daftar item tetap luas. Ukuran font dan tombol tidak ikut
 * mengecil supaya tetap nyaman disentuh.
 */

@media (max-height: 720px) {
  .pos-page {
    padding: 10px 14px;
  }

  .page-header {
    margin-bottom: 10px;
  }

  .page-header h1 {
    font-size: 28px;
  }

  .theme-toggle,
  .history-button {
    min-height: 44px;
    height: 44px;
  }

  .product-count {
    margin: 6px 0;
  }
}

/*
 * Tablet mendatar: Riwayat Nota sebaris dengan pil status (permintaan pemilik,
 * 2026-10-12). Diukur di 1007x601: kolom daftar 599px, sedangkan judul 54 +
 * server 86 + printer pendek 153 + tema 48 + Riwayat dua lencana ~231 + jarak
 * 44 = ~616px — 17px kelebihan yang menurunkannya ke baris kedua. Di ambang
 * ini saja jarak dirapatkan (kolom header 12px→8px, antar-tombol 10px→8px,
 * padding tombol 18px→14px, jarak dalam tombol 8px/6px→6px/4px): hemat ~24px
 * sehingga sebaris dengan sisa ~7px. Ukuran tombol dan huruf tidak berubah,
 * target sentuh >=44px terjaga. `flex-wrap` sengaja dipertahankan sebagai
 * pengaman: kalau satu keadaan tak terduga melebarkan tombol, yang terjadi
 * adalah kembali ke perilaku lama (turun baris), bukan terpotong.
 */

@media (min-width: 901px) and (max-height: 720px) {
  .page-header {
    column-gap: 8px;
  }

  .header-actions {
    gap: 8px;
  }

  .history-button {
    gap: 6px;

    padding: 0 14px;
  }

  .history-statuses {
    gap: 4px;
  }
}
</style>
