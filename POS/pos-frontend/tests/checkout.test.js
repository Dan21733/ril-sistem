import assert from 'node:assert/strict'
import { webcrypto } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

import { searchCatalog as searchCatalogRows } from '../src/utils/localCatalogSearch.js'
import { formatRupiahInput } from '../src/utils/rupiahInput.js'

/*
 * Modul util dimuat apa adanya (bukan di-stub) supaya perilakunya ikut diuji:
 * aturan harga (`resolveItemPrices`) dan format kolom uang (`rupiahInput`) adalah
 * bagian dari alur checkout/refund, bukan detail tampilan yang boleh berbeda
 * antara komponen dan tes.
 */
async function loadRealModule(path, context) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const module = new vm.SourceTextModule(source, { context })
  await module.link(() => { throw new Error('unexpected import') })
  await module.evaluate()
  return module
}

// No browser, database, printer or real credentials: these are script-level tests.
async function harness({ records = [], savedCart = null, storageFailure = false, busyTab = false, catalog = null } = {}) {
  let rows = structuredClone(records)
  const proofs = []
  const printed = []
  const requests = []
  /* Pekerjaan cetak yang belum keluar (nota yang kertasnya belum tercetak). */
  let outstandingPrintJobs = []
  let response = async () => { throw new Error('lost response') }
  let failStatus = null
  // Katalog uji (GET /api/items/) terpisah dari `respond`: permintaan nota
  // memakai body JSON, permintaan item tidak.
  const itemPage = async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      results: structuredClone(catalog ?? []),
      count: (catalog ?? []).length,
      next: null,
    }),
  })
  const table = (getRows, key) => ({
    async toArray() { return structuredClone(getRows()) },
    where(index) {
      return {
        equals(value) {
          const matching = () => getRows().filter((row) => row[index] === value)
          return {
            async toArray() { return structuredClone(matching()) },
            async first() { return structuredClone(matching()[0]) },
            async count() { return matching().length },
          }
        },
      }
    },
    async get(id) { return structuredClone(getRows().find((row) => row[key] === id)) },
    async add(row) {
      if (storageFailure) { throw new Error('storage unavailable') }
      getRows().push(structuredClone(row))
      return row[key]
    },
    async update(id, changes) {
      if (changes.sync_status === failStatus) { throw new Error('write failed') }
      const row = getRows().find((entry) => entry[key] === id)
      if (!row) { return 0 }
      Object.assign(row, structuredClone(changes))
      return 1
    },
    async delete(id) {
      const index = getRows().findIndex((row) => row[key] === id)
      if (index >= 0) { getRows().splice(index, 1) }
    },
  })
  const db = {
    transactions: table(() => rows, 'sync_id'),
    payment_proofs: table(() => proofs, 'proof_id'),
    async transaction(_mode, ...args) {
      const before = structuredClone(rows)
      const beforeProofs = structuredClone(proofs)
      try { return await args.at(-1)() } catch (error) {
        rows = before
        proofs.splice(0, proofs.length, ...beforeProofs)
        throw error
      }
    },
  }
  const local = new Map()
  let lockActive = busyTab
  const props = { cartItems: [], checkoutStatus: 'idle', retryAfterSeconds: 0, pendingReceipt: null, proofSyncMessage: '' }
  const emitted = []
  let workerLookup = async () => 'Original cashier'
  let localStorageFailure = false
  /* Sakelar untuk gerbang PIN tak dikenal (§3): server hidup/mati, dan
   * penghitung cooldown lokal yang bisa disetel tes. */
  let serverUp = true
  let sequenceConsumed = 0
  let rangeState = { next_sequence: 1, end_sequence: 1000 }
  /* Salinan katalog di perangkat + cap waktunya (jalur offline, §3.3). */
  let cachedCatalog = []
  let cachedSyncedAt = null
  let cachedAgeMs = 0
  let cooldown = { blocked: false, remainingSeconds: 0, failedCount: 0 }
  const pinFailures = []
  const catalogSyncCalls = []
  let pinResets = 0
  const context = vm.createContext({
    console,
    setTimeout: (...args) => { const timer = setTimeout(...args); timer.unref(); return timer },
    clearTimeout,
    setInterval: (...args) => { const timer = setInterval(...args); timer.unref(); return timer },
    clearInterval,
    AbortController,
    navigator: { onLine: true, locks: {
      async request(name, options, callback) {
        if (lockActive) { return callback(null) }
        lockActive = true
        try { return await callback({ name }) } finally { lockActive = false }
      },
    } },
    localStorage: {
      getItem: (key) => local.get(key) || null,
      setItem: (key, value) => {
        if (localStorageFailure) { throw new Error('storage unavailable') }
        local.set(key, value)
      },
      removeItem: (key) => local.delete(key),
    },
    window: { addEventListener() {}, removeEventListener() {} },
    URL, URLSearchParams, Intl,
    defineProps: () => props,
    defineEmits: () => (...args) => emitted.push(args),
    fetch: async (url, options) => {
      /* Tanpa server, penyelidikan dan pengiriman nota sama-sama tidak punya jawaban. */
      if (!serverUp) { throw new Error('network down') }
      if (String(url).includes('/api/items/')) { return itemPage() }
      requests.push(JSON.parse(options.body))
      return response(requests.at(-1))
    },
  })
  /*
   * Runtime Vue tidak jalan di VM ini, jadi watcher hanya dicatat. Tes yang
   * butuh efeknya (mis. modal checkout yang harus menutup sendiri) memicunya
   * lewat `notifyStatusChange` dengan transisi yang sama seperti induk.
   */
  const watchers = []
  const vue = {
    ref: (value) => ({ value }),
    computed: (callback) => ({ get value() { return callback() } }),
    watch: (source, callback) => { watchers.push({ source, callback }) },
    onMounted() {}, onBeforeUnmount() {},
  }
  const modules = new Map()
  const mock = (values) => new vm.SyntheticModule(Object.keys(values), function () {
    for (const [name, value] of Object.entries(values)) { this.setExport(name, value) }
  }, { context })
  const repoSource = await readFile(new URL('../src/services/storage/transactionRepository.js', import.meta.url), 'utf8')
  const repo = new vm.SourceTextModule(repoSource, { context })
  await repo.link(() => mock({ default: db }))
  await repo.evaluate()
  modules.set('./services/storage/transactionRepository', repo)
  /* Antrean dibaca dari modul nyata supaya pemetaan baris ikut diuji. */
  const queueSource = await readFile(new URL('../src/services/storage/transactionQueue.js', import.meta.url), 'utf8')
  const queue = new vm.SourceTextModule(queueSource, { context })
  await queue.link((specifier) => {
    if (specifier === './transactionRepository') { return repo }
    throw new Error(`unexpected import ${specifier}`)
  })
  await queue.evaluate()
  modules.set('./services/storage/transactionQueue', queue)
  /*
   * Aturan harga diuji sungguhan, bukan di-stub: `App.vue` memakai
   * `resolveItemPrices` saat memetakan katalog dan saat merekonsiliasi
   * keranjang lama, jadi perilaku harga adalah bagian dari alur checkout
   * (mis. `max_price` = 0 jangan menghasilkan nota Rp 0).
   */
  const priceRange = await loadRealModule('../src/utils/priceRange.js', context)
  modules.set('./utils/priceRange', priceRange)
  /* CartList memakai util yang sama untuk persetujuan harga di bawah floor. */
  modules.set('../utils/priceRange', priceRange)
  /*
   * Pemetaan item katalog diuji sungguhan juga: `mapApiItem` memakai aturan
   * harga yang sama dengan katalog tersimpan, jadi jalur online dan salinan
   * offline tidak bisa berbeda soal `max_price` = 0.
   */
  const catalogItemSource = await readFile(new URL('../src/utils/catalogItem.js', import.meta.url), 'utf8')
  const catalogItem = new vm.SourceTextModule(catalogItemSource, { context })
  await catalogItem.link((specifier) => (specifier === './priceRange' ? priceRange : mock({})))
  await catalogItem.evaluate()
  modules.set('./utils/catalogItem', catalogItem)
  modules.set('./utils/localCatalogSearch', await loadRealModule('../src/utils/localCatalogSearch.js', context))
  /*
   * Penanda di tombol "Riwayat Nota" diuji sungguhan juga: aturan "berita vs
   * pekerjaan" dan hitungan nota-vs-kertas ada di situ, bukan di komponennya.
   */
  modules.set('./utils/historyButtonNotice', await loadRealModule('../src/utils/historyButtonNotice.js', context))
  /*
   * Aturan "cetak tertunda" (hitung nota-vs-kertas + kunci himpunan pekerjaan)
   * diuji sungguhan juga: `App.vue` memakainya untuk angka di tombol riwayat
   * dan kunci penutupan pesan cetak, jadi ambang "sudah pernah dicoba" adalah
   * bagian dari alur cetak, bukan detail tampilan yang boleh berbeda.
   */
  modules.set('./utils/printDelay', await loadRealModule('../src/utils/printDelay.js', context))
  /*
   * Kolom uang (uang diterima, ongkir) memakai util yang sama; CartList ada di
   * `src/components/` jadi specifier-nya `../utils/...`.
   */
  const rupiahInput = await loadRealModule('../src/utils/rupiahInput.js', context)
  modules.set('./utils/rupiahInput', rupiahInput)
  modules.set('../utils/rupiahInput', rupiahInput)
  /* Diskon persen di keranjang (31 Okt 2026): util sungguhan, bukan tiruan. */
  modules.set('../utils/discount', await loadRealModule('../src/utils/discount.js', context))
  /* Aturan tutup-modal dipakai sungguhan (bukan di-stub): inilah yang
   * menentukan jalur offline tidak meninggalkan modal di langkah PIN. */
  const checkoutModal = await loadRealModule('../src/utils/checkoutModal.js', context)
  modules.set('../utils/checkoutModal', checkoutModal)
  modules.set('../directives/modalFocus', await loadRealModule('../src/directives/modalFocus.js', context))
  /* Generator nyata tidak pernah kembar; mock konstan akan menabrak penjaga
   * duplikat sync_id begitu antrean menampung lebih dari satu nota. */
  let uuidCounter = 0
  const exportsByPath = {
    vue,
    './services/id': { createUuid: () => `12345678-1234-4234-8234-${String(uuidCounter += 1).padStart(12, '0')}` },
    './services/api/deviceApi': {
      API_BASE_URL: '', deviceAuthHeader: () => ({}),
      registerDevice: async () => null, registerOrRenewDevice: async () => null,
      allocateSequenceRange: async () => null,
    },
    './services/storage/deviceRepository': { default: {
      getDevice: async () => ({ device_id: 'test', device_code: 'T01', device_token: 'test-only' }),
    } },
    './services/storage/sequenceRepository': {
      SequenceRangeExhaustedError: Error,
      getActiveRange: async () => ({ ...rangeState }),
      consumeNextSequence: async () => { sequenceConsumed += 1; return sequenceConsumed },
      saveActiveRange: async () => {},
    },
    './services/api/transactionApi': {
      /* Tiruan setia `syncTransactionBatch` asli: HTTP + parse + throw berstatus
       * sama persis, supaya pemetaan hasil di App.vue yang diuji, bukan tiruannya. */
      syncTransactionBatch: async (deviceToken, members) => {
        const response = await context.fetch('/api/transactions/sync/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ transactions: members }),
        })
        let body
        try { body = await response.json() } catch { body = {} }
        if (response.status < 200 || response.status >= 300) {
          const error = new Error(body.detail || `Gagal mengirim antrean (HTTP ${response.status}).`)
          error.status = response.status
          error.body = body
          error.headers = response.headers
          throw error
        }
        return body
      },
    },
    './services/api/serverReachability': {
      noteServerAnswer: () => {}, noteServerSilence: () => {},
      serverReachability: () => serverUp, probeServerReachability: async () => serverUp,
      clockSkewMs: () => null,
    },
    './services/workers/pinSeal': { sealApproverPin: async () => null },
    './services/catalog/catalogCache': {
      getCatalogState: async () => ({
        count: cachedCatalog.length,
        syncedAt: cachedSyncedAt,
        ageMs: cachedAgeMs,
        stale: cachedAgeMs > 7 * 24 * 60 * 60 * 1000,
        fresh: cachedAgeMs <= 7 * 24 * 60 * 60 * 1000,
      }),
      searchCachedCatalog: async (query, { page = 1 } = {}) => searchCatalogRows(cachedCatalog, query, { page }),
      ensureCatalogFresh: async () => {
        catalogSyncCalls.push('check')
        return { synced: false, count: cachedCatalog.length }
      },
      syncCatalog: async () => {
        catalogSyncCalls.push(1)
        return { count: cachedCatalog.length, syncedAt: '2026-10-08T00:00:00.000Z' }
      },
    },
    './services/workers/pinCooldown': {
      getPinCooldownState: async () => ({ ...cooldown }),
      registerPinFailure: async () => { pinFailures.push(1); return { ...cooldown } },
      registerPinSuccess: async () => { pinResets += 1; cooldown = { blocked: false, remainingSeconds: 0, failedCount: 0 }; return { ...cooldown } },
    },
    './services/paymentProof/uploadQueue': {
      flushPaymentProofs: async () => ({}), summarizeProofSync: () => '',
    },
    './services/printer/printQueue': {
      enqueueReceiptPrint: async ({ sale }) => { printed.push(structuredClone(sale)) },
      flushPrintJobs: async () => ({}),
      retryPrintJobNow: async () => ({}), summarizePrintSync: () => '',
    },
    './services/storage/printJobRepository': {
      getPrintJobsBySyncId: async (syncId) => printed.filter((sale) => sale.sync_id === syncId),
      listOutstandingPrintJobs: async () => structuredClone(outstandingPrintJobs),
    },
    './services/workers/workerDirectory': { refreshWorkerDirectory: async () => {} },
    './composables/useTheme': { useTheme: () => ({}) },
    './composables/usePrinterStatus': { usePrinterStatus: () => ({ refresh: async () => {} }) },
    './composables/useServerStatus': { useServerStatus: () => ({ refresh: async () => {} }) },
  }
  async function loadComponent(path, exposed, extra = {}) {
    const text = await readFile(new URL(path, import.meta.url), 'utf8')
    const script = text.match(/<script setup>([\s\S]*?)<\/script>/)[1]
    const module = new vm.SourceTextModule(`${script}\nexport { ${exposed} }`, { context })
    await module.link((specifier) => {
      if (modules.has(specifier)) { return modules.get(specifier) }
      if (extra[specifier]) { return mock(extra[specifier]) }
      if (exportsByPath[specifier]) { return mock(exportsByPath[specifier]) }
      return mock({ default: {} })
    })
    await module.evaluate()
    return module.namespace
  }
  const app = await loadComponent('../src/App.vue',
    'completeTransaction, restorePendingCheckout, finishCheckout, handleSharedCartChange, resendQueuedTransaction, resendQueuedBatch, batchSyncState, batchSyncMessage, queuedNotices, cart, checkoutStatus, checkoutMessage, retryAfterSeconds, isCartLocked, loadItems, items, searchQuery, catalogSource, catalogNotice, catalogUnavailable, catalogStale, totalItems, hasMoreItems, outstandingPrintCount, outstandingPrintJobId, outstandingPrintKey, historyNotice, refreshOutstandingPrintJob, retryPrint')
  /* Batas watcher milik CartList: watcher App tidak ikut dipicu tes. */
  const watchersBeforeCart = watchers.length
  const cartUi = await loadComponent('../src/components/CartList.vue',
    'completeTransaction, retryTransaction, cashierPin, paymentMethod, deliveryMethod, cashReceived, customerName, customerPhone, isPaymentStepComplete, completing, showConfirmation, openConfirmation, cartFeedbackVisible, cartFeedbackIsProofOnly', {
      '../services/workers/workerDirectory': { findWorkerNameByPin: (pin) => workerLookup(pin) },
      '../utils/floorCode': { FLOOR_CODE_LEGEND: '', FLOOR_CODE_MNEMONIC: '', floorCodeFor() {}, isRoundedFloorCode() {} },
      '../utils/cashSuggestion': { DEFAULT_SUGGESTION_COUNT: 4, cashBreakdown() {}, suggestCashAmounts() {} },
      '../services/id': { createUuid() {} },
      '../services/image/compressProofImage': { compressProofImage() {} },
      '../services/storage/paymentProofRepository': { deletePaymentProof() {}, getPaymentProof() {}, savePaymentProof() {} },
    })
  if (savedCart) {
    app.cart.value = structuredClone(savedCart)
    local.set('posCart', JSON.stringify(savedCart))
  }
  await app.restorePendingCheckout()
  if (!savedCart && app.checkoutStatus.value === 'idle') {
    app.cart.value = [{ item_name: 'Item', variant: 'Default', quantity: 1, unit_price: 100, price: 100 }]
    local.set('posCart', JSON.stringify(app.cart.value))
  }
  return {
    app, repo: repo.namespace, cartUi, props, emitted, requests, printed, local, proofs,
    rows: () => rows,
    respond: (callback) => { response = callback },
    failStatus: (status) => { failStatus = status },
    lookup: (callback) => { workerLookup = callback },
    failLocalStorage: () => { localStorageFailure = true },
    setServerUp: (value) => { serverUp = value },
    setCachedCatalog: (rows, { syncedAt = '2026-10-08T00:00:00.000Z', ageMs = 0 } = {}) => {
      cachedCatalog = structuredClone(rows)
      cachedSyncedAt = syncedAt
      cachedAgeMs = ageMs
    },
    setCooldown: (state) => { cooldown = { blocked: false, remainingSeconds: 0, failedCount: 0, ...state } },
    setOutstandingPrintJobs: (jobs) => { outstandingPrintJobs = structuredClone(jobs) },
    sequenceConsumed: () => sequenceConsumed,
    setSequenceRange: (patch) => { rangeState = { ...rangeState, ...patch } },
    pinFailures: () => pinFailures.length,
    pinResets: () => pinResets,
    catalogSyncs: () => catalogSyncCalls.length,
    catalogChecks: () => catalogSyncCalls.filter((entry) => entry === 'check').length,
    catalogForcedSyncs: () => catalogSyncCalls.filter((entry) => entry === 1).length,
    /*
     * Memicu watcher CartList yang sumbernya bernilai `status` dengan transisi
     * (status, previous) — bentuk yang Vue berikan pada runtime.
     */
    notifyStatusChange: (status, previous) => {
      for (const { source, callback } of watchers.slice(watchersBeforeCart)) {
        if (typeof source === 'function' && source() === status) {
          callback(status, previous)
        }
      }
    },
  }
}

const checkout = {
  pin: '123456', cashierName: 'Original cashier', pinVerifiedLocally: true,
  deliveryMethod: 'delivery', shippingCost: 10, paymentMethod: 'cash', cashReceived: 110,
  customerName: 'Budi Santoso', customerPhone: '081234567890',
}
const reply = (status, body) => ({ status, headers: { get: () => '60' }, json: async () => body })
const success = (request) => reply(200, {
  sync_id: request.transaction.sync_id, transaction_id: request.transaction.transaction_id, status: 'idempotent',
})

test('draft is durable before networking and cannot be discarded once submission begins', async () => {
  const h = await harness()
  let resolveResponse
  h.respond(() => new Promise((resolve) => { resolveResponse = resolve }))
  const pending = h.app.completeTransaction(checkout)
  await new Promise((resolve) => setTimeout(resolve, 10))
  assert.equal(h.rows()[0].sync_status, 'syncing')
  await assert.rejects(h.repo.deleteTransaction(h.rows()[0].sync_id))
  resolveResponse(success(h.requests[0]))
  await pending
})

test('first definite 400 still saves audit facts after retry response', async () => {
  const h = await harness()
  h.respond(() => reply(400, { detail: 'Definite domain rejection' }))
  await h.app.completeTransaction({ ...checkout, pinVerifiedLocally: false })
  assert.equal(h.rows()[0].sync_status, 'rejected')
  assert.equal(h.rows()[0].original_pin, null)
})

/*
 * Gerbang PIN sebelum segel (keputusan pemilik 2026-10-08,
 * OFFLINE_SYNC_DESIGN.md §3): nomor nota dan baris lokal tidak boleh lahir
 * sebelum PIN terbukti — cocok lokal, atau server memang bisa menjawab.
 */
test('unknown PIN without a reachable server seals nothing and spends no number', async () => {
  const h = await harness()
  h.setServerUp(false)
  await h.app.completeTransaction({ ...checkout, pinVerifiedLocally: false, cashierName: '' })
  assert.equal(h.rows().length, 0)
  assert.equal(h.requests.length, 0)
  assert.equal(h.printed.length, 0)
  assert.equal(h.sequenceConsumed(), 0)
  assert.equal(h.app.checkoutStatus.value, 'error')
  assert.equal(h.pinFailures(), 1)
  /* Keranjang tetap utuh supaya kasir bisa mencoba lagi setelah tersambung. */
  assert.equal(h.app.cart.value.length, 1)
})

test('matched PIN still seals and prints while the server is unreachable', async () => {
  const h = await harness()
  h.setServerUp(false)
  await h.app.completeTransaction(checkout)
  assert.equal(h.rows().length, 1)
  assert.equal(h.rows()[0].local_final, true)
  assert.equal(h.sequenceConsumed(), 1)
  /* Nota final lokal dicetak tanpa menunggu jaringan. */
  assert.equal(h.printed.length, 1)
  assert.equal(h.pinResets(), 1)
})

test('local PIN cooldown holds the next attempt before numbering or storage', async () => {
  const h = await harness()
  h.setCooldown({ blocked: true, remainingSeconds: 12, failedCount: 5 })
  await h.app.completeTransaction({ ...checkout, pinVerifiedLocally: false, cashierName: '' })
  assert.equal(h.rows().length, 0)
  assert.equal(h.sequenceConsumed(), 0)
  assert.equal(h.app.checkoutStatus.value, 'pin_cooldown')
  assert.equal(h.app.retryAfterSeconds.value, 12)
  assert.equal(h.pinFailures(), 0)
  /* Hitungan detiknya hanya milik komponen: pesan statis tidak boleh
   * menyebut angka yang langsung basi, apalagi dua kali. */
  assert.equal(
    h.app.checkoutMessage.value,
    'PIN belum dikenal perangkat ini; percobaan berikutnya ditahan agar PIN tidak bisa ditebak.',
  )
})

test('stored nota retry is never blocked by the local PIN cooldown', async () => {
  const first = await harness()
  await first.app.completeTransaction(checkout)
  const h = await harness({ records: first.rows() })
  h.setCooldown({ blocked: true, remainingSeconds: 300, failedCount: 5 })
  h.respond(success)
  await h.app.resendQueuedTransaction(h.rows()[0].sync_id)
  /* Kirim ulang memakai PIN asli tersimpan: bukan tebakan, jadi tidak ditahan. */
  assert.equal(h.app.checkoutStatus.value, 'success')
  assert.equal(h.rows()[0].sync_status, 'synced')
})

test('lost response queues the nota and frees the cart for the next sale', async () => {
  const h = await harness()
  await h.app.completeTransaction(checkout)
  assert.equal(h.rows()[0].sync_status, 'unknown')
  assert.equal(h.rows()[0].outcome_unknown, true)
  /* Antrean multi-nota: keranjang bersih, kasir boleh jualan lagi. */
  assert.equal(h.app.isCartLocked(), false)
  assert.equal(h.app.cart.value.length, 0)
  assert.equal(h.app.checkoutStatus.value, 'idle')
  assert.equal(h.app.queuedNotices.value.length, 1)
  assert.equal(h.rows().length, 1)
  h.respond(success)
  await h.app.resendQueuedTransaction(h.rows()[0].sync_id)
  assert.deepEqual(h.requests[1], h.requests[0])
  assert.equal(h.printed.length, 1)
  assert.equal(h.printed[0].cashier_name, checkout.cashierName)
  assert.equal(h.printed[0].fulfillment, 'delivery')
  assert.equal(h.rows()[0].original_pin, null)
  assert.equal(h.app.checkoutStatus.value, 'success')
  assert.equal(h.app.cart.value.length, 0)
  assert.equal(h.app.queuedNotices.value.length, 0)
})

/*
 * Skenario B (permintaan pemilik 2026-10-12, council 2026-10-12:
 * only-if-stale): transaksi online yang sukses memicu *pengecekan* salinan di
 * latar, bukan unduh paksa — unduh penuh ±98 halaman (±4 menit terukur) setiap
 * nota adalah pemborosan karena isinya sama dengan yang timer ambil. Nota
 * yang masuk antrean offline tidak memicu apa pun: tidak ada bukti server
 * hidup di situ.
 */
test('transaksi online yang sukses mengecek salinan tanpa unduh paksa', async () => {
  const h = await harness()
  h.respond(success)
  await h.app.completeTransaction(checkout)
  assert.equal(h.app.checkoutStatus.value, 'success')
  assert.equal(h.catalogChecks(), 1)
  assert.equal(h.catalogForcedSyncs(), 0)
})

test('nota antrean offline tidak menyentuh salinan katalog', async () => {
  const h = await harness()
  h.setServerUp(false)
  await h.app.completeTransaction(checkout)
  assert.equal(h.app.queuedNotices.value.length, 1)
  assert.equal(h.catalogSyncs(), 0)
})

/*
 * Permintaan pemilik 2026-10-09: setelah SELESAIKAN TRANSAKSI modal tidak boleh
 * tertinggal di layar. Yang paling mudah rusak adalah jalur offline — nota
 * masuk antrean lalu induk mengembalikan status ke `idle` — karena `idle` juga
 * status saat modal baru dibuka.
 */
test('nota offline yang masuk antrean menutup modal checkout', async () => {
  const h = await harness()
  /* Kasir sedang di langkah PIN: modalnya terbuka. */
  h.cartUi.showConfirmation.value = true
  await h.app.completeTransaction(checkout)

  assert.equal(h.app.checkoutStatus.value, 'idle')
  assert.equal(h.app.queuedNotices.value.length, 1)

  /* Transisi yang sama seperti runtime Vue: `submitting` -> `idle`. */
  h.notifyStatusChange('idle', 'submitting')
  assert.equal(h.cartUi.showConfirmation.value, false)
})

test('idle di luar checkout tidak menutup modal yang baru dibuka', async () => {
  const h = await harness()
  h.cartUi.showConfirmation.value = true
  /* `openConfirmation()` menutup checkout lama (`success`) tepat sebelum modal
     baru dibuka: urutan itu tidak boleh ikut menutup modal tersebut. */
  h.notifyStatusChange('idle', 'success')
  assert.equal(h.cartUi.showConfirmation.value, true)
})

test('reload restores the queue and resends without a UI PIN', async () => {
  const first = await harness()
  await first.app.completeTransaction(checkout)
  const h = await harness({ records: first.rows() })
  assert.equal(h.app.checkoutStatus.value, 'idle')
  assert.equal(h.app.queuedNotices.value.length, 1)
  /* Keranjang bersih untuk jualan baru; kirim ulang lewat antrean. */
  assert.equal(h.app.isCartLocked(), false)
  h.respond(success)
  await h.app.resendQueuedTransaction(h.rows()[0].sync_id)
  assert.deepEqual(h.requests[0], first.requests[0])
})

test('crash after synced write cannot restore sold cart as a new draft', async () => {
  const first = await harness()
  await first.app.completeTransaction(checkout)
  const savedCart = JSON.parse(first.local.get('posCart'))
  first.respond(success)
  await first.app.completeTransaction({})
  const h = await harness({ records: first.rows(), savedCart })
  assert.equal(h.app.cart.value.length, 0)
  assert.equal(h.app.checkoutStatus.value, 'idle')
})

test('recent in-flight record waits before becoming retryable after reload', async () => {
  const first = await harness()
  await first.app.completeTransaction(checkout)
  const record = structuredClone(first.rows()[0])
  record.sync_status = 'syncing'
  record.submission_started_at = new Date().toISOString()
  const h = await harness({ records: [record] })
  assert.equal(h.rows()[0].sync_status, 'syncing')
  assert.equal(h.app.checkoutStatus.value, 'review')
  assert.equal(h.app.isCartLocked(), true)
  assert.equal(h.requests.length, 0)
})

test('expired in-flight record is conservatively restored as unknown', async () => {
  const first = await harness()
  await first.app.completeTransaction(checkout)
  const record = structuredClone(first.rows()[0])
  record.sync_status = 'syncing'
  record.submission_started_at = new Date(Date.now() - 46_000).toISOString()
  const h = await harness({ records: [record] })
  assert.equal(h.rows()[0].sync_status, 'unknown')
  assert.equal(h.app.checkoutStatus.value, 'idle')
  assert.equal(h.app.queuedNotices.value.length, 1)
})

test('later 400 cannot discard an earlier unknown attempt or its print', async () => {
  const h = await harness()
  await h.app.completeTransaction(checkout)
  h.respond(() => reply(400, { detail: 'Rejected by server' }))
  await h.app.resendQueuedTransaction(h.rows()[0].sync_id)
  assert.equal(h.rows()[0].sync_status, 'review')
  assert.equal(h.rows()[0].outcome_unknown, true)
  assert.equal(h.printed.length, 1)
  h.app.finishCheckout()
  assert.equal(h.app.checkoutStatus.value, 'review')
})

test('first 400 on locally final nota requires review, never deletion', async () => {
  const h = await harness()
  h.respond(() => reply(400, { pin: ['Not accepted'] }))
  await h.app.completeTransaction(checkout)
  assert.equal(h.rows()[0].sync_status, 'review')
  await assert.rejects(h.repo.deleteTransaction(h.rows()[0].sync_id))
})

test('first definite rejection of nonfinal nota is retained and unlocks draft', async () => {
  const h = await harness()
  h.respond(() => reply(400, { detail: 'Rejected' }))
  await h.app.completeTransaction({ ...checkout, pinVerifiedLocally: false })
  assert.equal(h.rows()[0].sync_status, 'rejected')
  assert.equal(h.rows()[0].outcome_unknown, false)
  assert.equal(h.rows()[0].original_pin, null)
  assert.equal(h.printed.length, 0)
  const proof = await harness()
  proof.proofs.push({ proof_id: 'proof', sync_id: null })
  proof.respond(() => reply(400, { detail: 'Rejected' }))
  await proof.app.completeTransaction({ ...checkout, pinVerifiedLocally: false, paymentMethod: 'bank_transfer', proofId: 'proof' })
  assert.equal(proof.proofs[0].sync_id, proof.rows()[0].sync_id)
  assert.equal(proof.proofs[0].upload_status, 'pending')
  assert.equal(proof.rows()[0].sync_status, 'rejected')
  h.app.finishCheckout()
  assert.equal(h.app.isCartLocked(), false)
  await assert.rejects(h.repo.deleteTransaction(h.rows()[0].sync_id))
})

for (const status of [401, 429]) {
  test(`later ${status} preserves uncertainty and original sale`, async () => {
    const h = await harness()
    await h.app.completeTransaction(checkout)
    h.respond(() => reply(status, { detail: 'Temporarily refused' }))
    await h.app.resendQueuedTransaction(h.rows()[0].sync_id)
    assert.equal(h.rows()[0].outcome_unknown, true)
    assert.equal(h.rows()[0].sync_status, 'unknown')
    assert.equal(h.rows()[0].original_pin, checkout.pin)
    assert.equal(h.app.isCartLocked(), false)
    if (status === 429) {
      assert.ok(h.rows()[0].retry_after_until)
      const restored = await harness({ records: h.rows() })
      assert.equal(restored.app.checkoutStatus.value, 'idle')
      assert.equal(restored.app.queuedNotices.value.length, 1)
    }
    const unresolved = await h.repo.getTransaction(h.rows()[0].sync_id)
    await assert.rejects(h.repo.recordDefiniteAttemptFailure(unresolved.sync_id, 'rejected'))
    await assert.rejects(h.repo.updateTransactionStatus(unresolved.sync_id, 'pending'))
  })
}

test('invalid success response is held for review without clearing the cart', async () => {
  const h = await harness()
  h.respond(() => reply(201, {}))
  await h.app.completeTransaction(checkout)
  assert.equal(h.rows()[0].sync_status, 'review')
  assert.equal(h.rows()[0].outcome_unknown, true)
  assert.equal(h.app.cart.value.length, 1)
  await h.app.completeTransaction({})
  assert.equal(h.requests.length, 1)
})

test('malformed JSON response remains unknown', async () => {
  const h = await harness()
  h.respond(() => ({ status: 201, json: async () => { throw new Error('truncated') } }))
  await h.app.completeTransaction(checkout)
  assert.equal(h.rows()[0].sync_status, 'unknown')
})

test('401 before any lost response is pending rather than unknown', async () => {
  const h = await harness()
  h.respond(() => reply(401, { detail: 'Device rejected' }))
  await h.app.completeTransaction(checkout)
  assert.equal(h.rows()[0].sync_status, 'pending')
  assert.equal(h.rows()[0].outcome_unknown, false)
  assert.equal(h.rows()[0].local_final, true)
})

test('409 retains nota and blocks further submission', async () => {
  const h = await harness()
  h.respond(() => reply(409, { detail: 'Different cashier or sale facts' }))
  await h.app.completeTransaction(checkout)
  assert.equal(h.rows()[0].sync_status, 'conflict')
  await h.app.completeTransaction({})
  assert.equal(h.requests.length, 1)
  assert.equal(h.rows().length, 1)
})

test('storage failure prevents network, printing and replacement checkout', async () => {
  const h = await harness({ storageFailure: true })
  await h.app.completeTransaction(checkout)
  assert.equal(h.requests.length, 0)
  assert.equal(h.printed.length, 0)
  assert.equal(h.app.checkoutStatus.value, 'review')
})

test('cart-origin storage failure does not send or print a persisted nota', async () => {
  const h = await harness()
  h.failLocalStorage()
  await h.app.completeTransaction(checkout)
  assert.equal(h.rows().length, 1)
  assert.equal(h.app.checkoutStatus.value, 'review')
  assert.equal(h.requests.length, 0)
  assert.equal(h.printed.length, 0)
})

test('failed synced write preserves unknown identity for reload', async () => {
  const h = await harness()
  h.failStatus('synced')
  h.respond(success)
  await h.app.completeTransaction(checkout)
  assert.equal(h.rows()[0].sync_status, 'unknown')
  assert.equal(h.app.checkoutStatus.value, 'idle')
  assert.equal(h.app.queuedNotices.value.length, 1)
})

test('double click sends only one HTTP request', async () => {
  const h = await harness()
  await Promise.all([h.app.completeTransaction(checkout), h.app.completeTransaction(checkout)])
  assert.equal(h.requests.length, 1)
  assert.equal(h.rows().length, 1)
})

test('stale tab cannot sell a shared cart already confirmed by another tab', async () => {
  const first = await harness()
  first.respond(success)
  await first.app.completeTransaction(checkout)
  const sharedCart = first.local.get('posCart')
  const h = await harness({ records: first.rows() })
  h.local.set('posCart', sharedCart)
  await h.app.completeTransaction(checkout)
  assert.equal(h.requests.length, 0)
  assert.equal(h.rows().length, 1)
  assert.equal(h.app.cart.value.length, 0)
})

test('shared storage change immediately locks a stale cart', async () => {
  const h = await harness()
  h.app.handleSharedCartChange({ key: 'posCart' })
  assert.equal(h.app.isCartLocked(), true)
  await h.app.completeTransaction(checkout)
  assert.equal(h.requests.length, 0)
})

test('another tab holding checkout lock blocks restoration and new sale', async () => {
  const h = await harness({ busyTab: true })
  await h.app.completeTransaction(checkout)
  assert.equal(h.app.checkoutStatus.value, 'review')
  assert.equal(h.requests.length, 0)
})

test('legacy pending/rejected records require review rather than replacement PIN', async () => {
  for (const status of ['pending', 'rejected']) {
    const h = await harness({ records: [{ sync_id: 'legacy', sync_status: status, payload: { sync_id: 'legacy' } }] })
    assert.equal(h.app.checkoutStatus.value, 'review')
    assert.equal(h.requests.length, 0)
  }
})

test('repository refuses changed sealed facts, overwrite and deletion', async () => {
  const h = await harness()
  await h.app.completeTransaction(checkout)
  const row = h.rows()[0]
  await assert.rejects(h.repo.updateTransactionStatus(row.sync_id, 'pending', { payload: {} }))
  await assert.rejects(h.repo.saveTransaction(row))
  await assert.rejects(h.repo.deleteTransaction(row.sync_id))
})

test('transfer proof association and nota write succeed or fail together', async () => {
  const h = await harness()
  h.proofs.push({ proof_id: 'proof', sync_id: null })
  await h.app.completeTransaction({ ...checkout, paymentMethod: 'bank_transfer', proofId: 'proof' })
  assert.equal(h.proofs[0].sync_id, h.rows()[0].sync_id)
  const missing = await harness()
  await missing.app.completeTransaction({ ...checkout, paymentMethod: 'bank_transfer', proofId: 'missing' })
  assert.equal(missing.rows().length, 0)
  assert.equal(missing.requests.length, 0)
})

// Renewal device: frontend mencoba body { device_id, device_token } dulu
// (kode tidak berubah) sebelum registrasi baru. Modul deviceApi tidak punya
// import, jadi cukup dimuat dengan fetch double di context VM baru.
async function deviceApiHarness() {
  const calls = []
  let queue = []
  const context = vm.createContext({
    fetch: async (url, options) => {
      calls.push({ url, body: options?.body ? JSON.parse(options.body) : null })
      const next = queue.shift() ?? { status: 500, body: {} }
      return {
        ok: next.status >= 200 && next.status < 300,
        status: next.status,
        json: async () => next.body,
      }
    },
  })
  const source = await readFile(new URL('../src/services/api/deviceApi.js', import.meta.url), 'utf8')
  const module = new vm.SourceTextModule(source, { context })
  await module.link(() => { throw new Error('unexpected import') })
  await module.evaluate()
  return { api: module.namespace, calls, reply: (entries) => { queue = [...entries] } }
}

test('registration without stored device sends no renewal body', async () => {
  const h = await deviceApiHarness()
  h.reply([{ status: 201, body: { device_id: 'new', device_code: 'T02', device_token: 'tok' } }])
  const device = await h.api.registerOrRenewDevice(null)
  assert.equal(device.device_code, 'T02')
  assert.equal(h.calls.length, 1)
  assert.equal(h.calls[0].body, null)
})

test('stored device renews with old id and token, keeping its code', async () => {
  const h = await deviceApiHarness()
  h.reply([{ status: 200, body: { device_id: 'old', device_code: 'T01', device_token: 'fresh' } }])
  const device = await h.api.registerOrRenewDevice({ device_id: 'old', device_code: 'T01', device_token: 'expired' })
  assert.equal(device.device_id, 'old')
  assert.equal(device.device_code, 'T01')
  assert.equal(h.calls.length, 1)
  assert.deepEqual(h.calls[0].body, { device_id: 'old', device_token: 'expired' })
})

test('rejected renewal falls back once to a fresh registration', async () => {
  const h = await deviceApiHarness()
  h.reply([
    { status: 401, body: { detail: 'Device token is outside the renewal window.' } },
    { status: 201, body: { device_id: 'new', device_code: 'T02', device_token: 'tok' } },
  ])
  const device = await h.api.registerOrRenewDevice({ device_id: 'old', device_code: 'T01', device_token: 'expired' })
  assert.equal(device.device_code, 'T02')
  assert.equal(h.calls.length, 2)
  assert.deepEqual(h.calls[0].body, { device_id: 'old', device_token: 'expired' })
  assert.equal(h.calls[1].body, null)
})

test('double 401 surfaces instead of hiding a proxy token problem', async () => {
  const h = await deviceApiHarness()
  h.reply([
    { status: 401, body: { detail: 'renewal rejected' } },
    { status: 401, body: { detail: 'Invalid device registration token.' } },
  ])
  await assert.rejects(h.api.registerOrRenewDevice({ device_id: 'old', device_token: 'expired' }), /Invalid device registration token/)
  assert.equal(h.calls.length, 2)
})

test('non-401 renewal failure never falls back silently', async () => {
  const h = await deviceApiHarness()
  h.reply([{ status: 429, body: { detail: 'throttled' } }])
  await assert.rejects(h.api.registerOrRenewDevice({ device_id: 'old', device_token: 'expired' }))
  assert.equal(h.calls.length, 1)
})

// Perlindungan bukti: bukti yang sudah menempel pada nota tidak boleh dihapus
// dari jalur mana pun (blob foto tidak bisa dibuat ulang seperti byte cetak).
async function proofRepositoryHarness(records = []) {
  const rows = structuredClone(records)
  const matches = (index, values) => rows.filter((row) => values.includes(row[index]))
  const table = {
    async get(id) { return structuredClone(rows.find((row) => row.proof_id === id)) },
    async put(row) {
      const at = rows.findIndex((entry) => entry.proof_id === row.proof_id)
      if (at >= 0) { rows[at] = structuredClone(row) } else { rows.push(structuredClone(row)) }
    },
    async delete(id) {
      const at = rows.findIndex((row) => row.proof_id === id)
      if (at >= 0) { rows.splice(at, 1) }
    },
    where(index) {
      return {
        equals(value) { return { async toArray() { return structuredClone(matches(index, [value])) } } },
        anyOf(values) { return { async toArray() { return structuredClone(matches(index, values)) } } },
      }
    },
  }
  const context = vm.createContext({})
  const source = await readFile(new URL('../src/services/storage/paymentProofRepository.js', import.meta.url), 'utf8')
  const module = new vm.SourceTextModule(source, { context })
  await module.link((specifier) => {
    assert.equal(specifier, './db')
    return new vm.SyntheticModule(['default'], function () { this.setExport('default', { payment_proofs: table }) }, { context })
  })
  await module.evaluate()
  return { repo: module.namespace, rows: () => rows }
}

test('unlinked proof can still be discarded before its nota exists', async () => {
  const h = await proofRepositoryHarness([{ proof_id: 'p1', sync_id: null, upload_status: 'captured' }])
  await h.repo.deletePaymentProof('p1')
  assert.equal(h.rows().length, 0)
})

test('proof linked to an ambiguous nota cannot be deleted', async () => {
  const h = await proofRepositoryHarness([{ proof_id: 'p1', sync_id: 'nota-ambigu', upload_status: 'pending' }])
  await assert.rejects(h.repo.deletePaymentProof('p1'), /menempel pada nota/)
  assert.equal(h.rows().length, 1)
})

test('uploaded proof cannot be deleted either', async () => {
  const h = await proofRepositoryHarness([{ proof_id: 'p1', sync_id: 'nota-synced', upload_status: 'uploaded' }])
  await assert.rejects(h.repo.deletePaymentProof('p1'))
  assert.equal(h.rows().length, 1)
})

test('batch proof lookup groups rows per nota', async () => {
  const h = await proofRepositoryHarness([
    { proof_id: 'p1', sync_id: 'a', upload_status: 'uploaded' },
    { proof_id: 'p2', sync_id: 'b', upload_status: 'pending' },
  ])
  // Lewat JSON karena baris lahir di realm VM yang berbeda.
  const found = JSON.parse(JSON.stringify(await h.repo.getPaymentProofsBySyncIds(['b'])))
  assert.deepEqual(found.map((row) => row.proof_id), ['p2'])
  assert.deepEqual(JSON.parse(JSON.stringify(await h.repo.getPaymentProofsBySyncIds([]))), [])
})

// Indikator tiga jalur per nota di riwayat: server/bukti/cetak dari tabelnya
// masing-masing. Kertas keluar bukan bukti server menyimpan.
async function historyHarness({
  proofs = [], jobs = [], queue = [], historyRows = [], historyError = null,
  queuedNotices = [], batchSending = false, batchMessage = '',
  checkoutStatus = 'idle', checkoutMessage = '', checkoutResult = null,
  printSyncMessage = '', printJobId = null, unprintedCount = 0, printAlertKey = '',
  /*
   * `Map` bersama antar dua pemanggilan = dua muat-ulang halaman yang memakai
   * `localStorage` yang sama; untuk menguji penutupan "Cetak tertunda" bertahan
   * setelah refresh (permintaan pemilik, 2026-10-10).
   */
  sharedStorage = null,
} = {}) {
  const bySync = (rows) => async (syncIds) => rows.filter((row) => syncIds.includes(row.sync_id))
  const refundCalls = []
  const detailCalls = []
  /* Pendengar event modal riwayat (permintaan pemilik 2026-10-09: notifikasi
     antrean + tombol kirim pindah ke sini dari kolom keranjang). */
  const historyEmitted = []
  /* Prop dari App.vue; tidak reaktif di VM, jadi tes menulisnya langsung. */
  const historyProps = {
    queuedNotices: structuredClone(queuedNotices),
    batchSending,
    batchMessage,
    checkoutStatus,
    checkoutMessage,
    checkoutResult,
    printSyncMessage,
    printJobId,
    unprintedCount,
    printAlertKey,
  }
  const vue = {
    ref: (value) => ({ value }),
    computed: (callback) => ({ get value() { return callback() } }),
    watch() {},
  }
  /* `localStorage` per panggilan, kecuali tes meminta yang bersama. */
  const noticeStore = sharedStorage ?? new Map()
  const context = vm.createContext({
    defineProps: () => historyProps,
    defineEmits: () => (...args) => historyEmitted.push(args),
    /* Host API tidak ada di realm VM baru; form refund memakai createUuid
       (crypto.getRandomValues) supaya jalan di http://IP-LAN. */
    crypto: {
      randomUUID: () => 'refund-uuid',
      getRandomValues: (array) => webcrypto.getRandomValues(array),
    },
    localStorage: {
      getItem: (key) => (noticeStore.has(key) ? noticeStore.get(key) : null),
      setItem: (key, value) => { noticeStore.set(key, String(value)) },
      removeItem: (key) => { noticeStore.delete(key) },
    },
    Number, String, Array, Object, JSON, Math, Date,
  })
  /* Baris antrean perangkat dibaca dari modul nyata lewat repo nyata. */
  const txRows = structuredClone(queue)
  const txTable = {
    async toArray() { return structuredClone(txRows) },
    async get(id) { return structuredClone(txRows.find((row) => row.sync_id === id)) },
  }
  const repoSource = await readFile(new URL('../src/services/storage/transactionRepository.js', import.meta.url), 'utf8')
  const fake = (values) => new vm.SyntheticModule(Object.keys(values), function () {
    for (const [name, value] of Object.entries(values)) { this.setExport(name, value) }
  }, { context })
  const repoModule = new vm.SourceTextModule(repoSource, { context })
  await repoModule.link((specifier) => {
    assert.equal(specifier, './db')
    return fake({ default: { transactions: txTable } })
  })
  await repoModule.evaluate()
  const queueSource = await readFile(new URL('../src/services/storage/transactionQueue.js', import.meta.url), 'utf8')
  const queueModule = new vm.SourceTextModule(queueSource, { context })
  await queueModule.link((specifier) => {
    assert.equal(specifier, './transactionRepository')
    return repoModule
  })
  await queueModule.evaluate()
  const doubles = {
    vue,
    '../services/api/transactionApi': {
      fetchTransactionDetail: async (...args) => { detailCalls.push(args); return {} },
      fetchTransactionHistory: async () => {
        if (historyError) { throw historyError }
        return { results: historyRows, next: null, count: historyRows.length }
      },
      createRefundEvent: async (token, payload) => { refundCalls.push(payload); return {} },
    },
    '../services/paymentProof/uploadQueue': {
      flushPaymentProofs: async () => ({}), resubmitPaymentProofAsNew: async () => null,
      retryPaymentProofNow: async () => ({}),
    },
    '../services/printer/printQueue': {
      reprintReceiptFromHistory: async () => ({}), summarizePrintSync: () => '',
    },
    '../services/storage/deviceRepository': {
      default: { getDevice: async () => ({ device_id: 'test', device_code: 'T01', device_token: 'test-only' }) },
    },
    '../services/storage/paymentProofRepository': {
      getPaymentProofsBySyncIds: bySync(proofs), listOutstandingPaymentProofs: async () => [],
    },
    '../services/storage/printJobRepository': { getPrintJobsBySyncIds: bySync(jobs) },
    './LoadingSpinner.vue': { default: {} },
  }
  const realModules = {
    '../directives/modalFocus': await loadRealModule('../src/directives/modalFocus.js', context),
    '../utils/rupiahInput': await loadRealModule('../src/utils/rupiahInput.js', context),
    /* Penutupan "Cetak tertunda" disimpan di modul nyata supaya bertahan-
       setelah-refresh ikut diuji, bukan di-stub. */
    '../utils/dismissedNotice': await loadRealModule('../src/utils/dismissedNotice.js', context),
    /* Penyaring baris bukti juga modul nyata: perilaku "hilang seketika"
       yang diuji harus perilaku yang benar-benar dipakai komponennya. */
    '../utils/bannerProofs': await loadRealModule('../src/utils/bannerProofs.js', context),
    /* Modul ID asli: createUuid hanya memakai crypto.getRandomValues yang ada di
       semua context (termasuk http://IP-LAN tanpa secure context). */
    '../services/id': await loadRealModule('../src/services/id.js', context),
    '../services/storage/transactionRepository': repoModule,
    '../services/storage/transactionQueue': queueModule,
  }
  const text = await readFile(new URL('../src/components/TransactionHistory.vue', import.meta.url), 'utf8')
  const script = text.match(/<script setup>([\s\S]*?)<\/script>/)[1]
  const module = new vm.SourceTextModule(
    `${script}\nexport { summarizeChannels, loadNotaChannels, notaChannelMap, notas, refundAmount, updateRefundAmount, openRefundForm, submitRefund, selectedNota, refundPin, refundReason, refundMethod, loadPage, openNota, loadLocalQueueRows, selectedItems, errorMessage, totalNotas, detailError, queuedCount, resendBatch, resendQueued, showCheckoutResult, retryPrint, visibleCheckoutResult, showPrintNotice, printActionsVisible, showHeaderNotices, dismissResult, dismissPrint, batchChipVisible, dismissBatch, resultChipLabel, printChipLabel, printChipText, printNoticeKey, batchChipLabel, batchChipTone, queueSignature, showQueueNotice, dismissQueue, queueNoticeKey }`,
    { context },
  )
  await module.link((specifier) => {
    if (realModules[specifier]) { return realModules[specifier] }
    const values = doubles[specifier] || {}
    return new vm.SyntheticModule(Object.keys(values), function () {
      for (const [name, value] of Object.entries(values)) { this.setExport(name, value) }
    }, { context })
  })
  await module.evaluate()
  return {
    ...module.namespace,
    refundCalls,
    detailCalls,
    emitted: historyEmitted,
    props: historyProps,
  }
}

/* Baris antrean perangkat untuk uji riwayat gabungan (created_at hari ini). */
const queuedRecord = (overrides = {}) => {
  const today = new Date().toISOString().slice(0, 10)
  return {
    sync_id: 'local-1',
    transaction_id: 'T01-000014',
    sync_status: 'unknown',
    checkout_version: 1,
    local_final: true,
    outcome_unknown: true,
    created_at: `${today}T10:00:00.000Z`,
    cashier_name: 'Rusdi',
    original_pin: '123456',
    receipt_sale: { payment_method: 'edc', total_amount: 3860500, cashier_name: 'Rusdi' },
    payload: {
      payment_method: 'edc',
      total_amount: 3860500,
      items: [{
        item_name: 'Aries Pintu', variant: 'Abu', quantity: 1, unit_price: 245000, discount: 0,
      }],
    },
    ...overrides,
  }
}

test('queued notas appear above server rows with honest labels', async () => {
  const today = new Date().toISOString().slice(0, 10)
  const h = await historyHarness({
    queue: [
      queuedRecord({ sync_id: 'local-1' }),
      queuedRecord({
        sync_id: 'local-2', transaction_id: 'T01-000015', sync_status: 'draft',
        outcome_unknown: false, created_at: `${today}T11:00:00.000Z`,
      }),
    ],
    historyRows: [{
      sync_id: 'srv-1', transaction_id: 'T01-000013', transaction_time: `${today}T09:00:00.000Z`,
      payment_method: 'cash', total_amount: 100000,
    }],
  })
  await h.loadPage(1)
  assert.equal(h.notas.value.length, 3)
  /* Terbaru dulu: antrean lokal di atas, server di bawah. */
  assert.equal(h.notas.value[0].sync_id, 'local-2')
  assert.equal(h.notas.value[0].local_queue, true)
  assert.equal(h.notas.value[0].queue_label, 'Belum terkirim')
  assert.equal(h.notas.value[1].sync_id, 'local-1')
  assert.equal(h.notas.value[1].queue_label, 'Hasil belum diketahui')
  assert.equal(h.notas.value[2].sync_id, 'srv-1')
  assert.equal(h.notas.value[2].local_queue, undefined)
  assert.equal(h.totalNotas.value, 3)
})

/*
 * Notifikasi antrean pindah dari kolom keranjang ke modal riwayat (permintaan
 * pemilik, 2026-10-09). Komponen riwayat tidak mengirim sendiri: ia meneruskan
 * permintaan ke App.vue, yang memegang gerbang status dan PIN asli tiap nota.
 */
test('riwayat menghitung antrean dari induk dan meneruskan permintaan kirim', async () => {
  const h = await historyHarness({
    queue: [queuedRecord()],
    queuedNotices: [queuedRecord(), queuedRecord({ sync_id: 'local-2' })],
  })

  assert.equal(h.queuedCount.value, 2)

  h.resendBatch()
  assert.deepEqual(JSON.parse(JSON.stringify(h.emitted.at(-1))), ['resend-batch'])

  h.resendQueued('local-2')
  assert.deepEqual(JSON.parse(JSON.stringify(h.emitted.at(-1))), ['resend-queued', 'local-2'])

  /* Selagi batch terbang, komponen tidak menambah kiriman kedua. */
  h.props.batchSending = true
  h.resendBatch()
  h.resendQueued('local-1')
  assert.equal(h.emitted.length, 2)
})

test('history shows only a successful checkout result, independently of printer feedback', async () => {
  const h = await historyHarness({
    checkoutStatus: 'success',
    checkoutMessage: 'Transaksi berhasil disimpan.',
    checkoutResult: { transaction_id: 'T01-000018' },
    printSyncMessage: 'Printer belum menjawab.',
    printJobId: 'existing-job',
  })
  assert.equal(h.showCheckoutResult.value, true)
  for (const status of ['error', 'review', 'conflict', 'retryable', 'rate_limited', 'submitting', 'idle']) {
    h.props.checkoutStatus = status
    assert.equal(h.showCheckoutResult.value, false, status)
  }
  h.props.checkoutStatus = 'success'
  h.props.checkoutMessage = ''
  assert.equal(h.showCheckoutResult.value, false)
})

/*
 * Tombol tutup di band notifikasi (permintaan pemilik, 2026-10-10). Yang ditutup
 * adalah tampilannya, bukan state antrean: `App.vue` tetap pemilik pekerjaan
 * cetak, dan pesannya kembali begitu isinya berubah.
 */
test('dismissing a history notice hides only that message and only until its content changes', async () => {
  const h = await historyHarness({
    checkoutStatus: 'success',
    checkoutMessage: 'Transaksi berhasil disimpan.',
    checkoutResult: { transaction_id: 'T01-000020' },
    printSyncMessage: 'Printer belum menjawab. Nota tetap sah dan akan dicoba cetak lagi otomatis.',
    printJobId: 'job-20',
  })

  assert.equal(h.visibleCheckoutResult.value, true)
  assert.equal(h.showPrintNotice.value, true)

  h.dismissResult()
  assert.equal(h.visibleCheckoutResult.value, false)
  /* Menutup satu pesan tidak menyembunyikan pesan lain. */
  assert.equal(h.showPrintNotice.value, true)

  /* Nota berikutnya membawa pesan barunya sendiri. */
  h.props.checkoutResult = { transaction_id: 'T01-000021' }
  assert.equal(h.visibleCheckoutResult.value, true)

  h.dismissPrint()
  assert.equal(h.showPrintNotice.value, false)
  /* Status cetak baru (job baru / hasil cetak baru) memunculkannya lagi. */
  h.props.printJobId = 'job-21'
  assert.equal(h.showPrintNotice.value, true)
  h.props.printJobId = 'job-20'
  assert.equal(h.showPrintNotice.value, false)
  h.props.printSyncMessage = 'Nota terkirim ke printer.'
  assert.equal(h.showPrintNotice.value, true)

  /* Menutup pesan tidak menyentuh state antrean milik induk. */
  h.props.printSyncMessage = 'Printer belum menjawab. Nota tetap sah dan akan dicoba cetak lagi otomatis.'
  h.props.printJobId = 'job-20'
  h.dismissResult(); h.dismissPrint()
  assert.equal(h.props.printJobId, 'job-20')
})

/*
 * Status ringkas di baris judul (permintaan pemilik, 2026-10-10). Band di bawah
 * judul dihapus supaya daftar nota tidak kehilangan tinggi; yang bisa ditutup
 * adalah tampilan pesannya (hasil, cetak, antrean), bukan pekerjaannya — nota
 * yang belum tersinkron tetap menunggu di antrean perangkat.
 */
test('header status chips replace the band and every panel is dismissible', async () => {
  const source = await readFile(new URL('../src/components/TransactionHistory.vue', import.meta.url), 'utf8')

  /* Band lama tidak boleh kembali: itu yang memakan ruang daftar nota. */
  assert.doesNotMatch(source, /class="history-notices"/)
  assert.doesNotMatch(source, /class="queue-notice"/)
  assert.match(source, /class="notice-dismiss"/)

  const headerStart = source.indexOf('class="header-notices"')
  assert.ok(headerStart > -1, 'baris chip header tidak ada')

  /*
   * Wadah chip harus memakai computed yang sama dengan isinya. Versi pertama
   * memakai prop mentah, sehingga setelah pesan ditutup tersisa wadah kosong
   * yang masih masuk urutan fokus keyboard.
   */
  const headerTag = source.slice(source.lastIndexOf('<div', headerStart))
  assert.match(headerTag, /v-if="showHeaderNotices"/)
  /*
   * `showHeaderNotices` hanya menyisakan elemen yang masih terlihat; X menutup
   * seluruh pasangan cetak termasuk tombol solusinya.
   */
  const computedBlock = source.slice(source.indexOf('showHeaderNotices'), source.indexOf('showHeaderNotices') + 400)
  for (const part of ['visibleCheckoutResult', 'showPrintNotice', 'showQueueNotice', 'batchChipVisible']) {
    assert.ok(computedBlock.includes(part), part)
  }

  const header = source.slice(headerStart, source.indexOf('class="close-button"'))

  /* Tombol tutup dan aksi cetak berbagi satu panel. */
  const printPairStart = header.indexOf('class="notice-pair is-print has-dismiss"')
  const printPair = header.slice(printPairStart, header.indexOf('</div>', printPairStart))
  assert.ok(printPairStart >= 0)
  assert.match(printPair, /class="notice-dismiss"[\s\S]*?@click="dismissPrint"/)
  assert.match(printPair, /class="chip-action"[\s\S]*?@click="retryPrint"[\s\S]*?Cetak ulang/)

  /*
   * Panel antrean juga bisa ditutup (permintaan pemilik, 2026-10-11): lencana
   * bulat di sudut kiri atas yang menutup seluruh panel — termasuk tombol
   * aksi "Kirim semua" — bukan hanya teksnya, sama seperti panel cetak.
   */
  const queuePairStart = header.indexOf('class="notice-pair is-danger has-dismiss"')
  assert.ok(queuePairStart > -1, 'panel antrean tidak memakai kelas has-dismiss')
  const queueChip = header.slice(queuePairStart, header.indexOf('</div>', queuePairStart))
  assert.match(queueChip, /class="notice-dismiss"[^>]*aria-label="`Tutup peringatan antrean:/)
  assert.match(queueChip, /class="notice-dismiss-badge"/)
  assert.match(queueChip, /<span[^>]*class="notice-pair-text"[\s\S]*?role="status"/)
  assert.match(queueChip, /@click="resendBatch"/)
  assert.match(header, /v-if="showQueueNotice"/)
  /* Lencana angka lama (`v-if="queuedCount > 0"`) adalah versi sebelum panel
     bisa ditutup; kalau kembali, X-nya hilang tanpa terlihat di tes lain. */
  assert.doesNotMatch(header, /v-if="queuedCount > 0"/)

  /* Pengiriman batch tetap menjadi aksi tombol terpisah. */
  assert.match(header, /@click="resendBatch"/)

  /*
   * Pekerjaan dulu, berita kemudian: di layar sempit baris chip bergeser, jadi
   * yang harus terbaca tanpa digeser adalah antrean yang belum selesai.
   */
  assert.ok(
    header.indexOf('belum tersinkron') < header.indexOf('Tersimpan'),
    'chip antrean harus mendahului chip hasil transaksi',
  )

  /*
   * Tombol X: lencana bulat **tepat di sudut kiri atas** panel cetak (permintaan
   * pemilik, 2026-10-11: "tombol X nya bergeser jadi tidak tepat berada pada
   * ujung kiri atas"). Kotaknya 32px dengan lencana 20px, jadi pusat lencananya
   * jatuh di (3,3) — titik terluar lengkung sudut 10px — bukan menggeser ke
   * dalam panel. Kotak tombolnya **adalah** daerah kliknya (40x44px), jadi
   * tidak perlu `::after`: ia sengaja melebar ke kanan dan ke bawah 38px, bukan
   * ke kiri, karena 16px di sebelah kiri ada panel tetangga dan di x = 30px
   * sudah ada huruf pertama pesannya.
   */
  assert.match(printPair, /class="notice-dismiss"[^>]*aria-label="`Tutup peringatan cetak:/)
  assert.match(printPair, /class="notice-dismiss-badge"/)
  assert.match(source, /\.notice-dismiss\s*\{[^}]*position: absolute;[^}]*top: -8px;[^}]*left: -15px;/s)
  assert.match(source, /\.notice-dismiss\s*\{[^}]*width: 40px;[^}]*height: 44px;/s)
  assert.doesNotMatch(source, /\.notice-dismiss::after/)
  assert.match(
    source,
    /\.notice-dismiss-badge\s*\{[^}]*position: absolute;[^}]*left: 6px;[^}]*top: -1px;[^}]*width: 20px;[^}]*height: 20px;[^}]*border: 2px solid var\(--notice-border\);/s,
  )
  /* Teksnya digeser supaya huruf pertama tidak berada di dalam daerah klik X. */
  assert.match(source, /\.notice-pair\.has-dismiss\s*\{[^}]*padding-left: 28px;/s)
  /*
   * Barisnya membungkus, bukan menggeser (permintaan pemilik, 2026-10-11: di
   * layar tab, dua peringatan memaksa menggeser ke kanan dan ke kiri).
   */
  assert.match(source, /\.header-notices\s*\{[^}]*flex-wrap: wrap;[^}]*gap: 16px;[^}]*row-gap: 14px;/s)
  assert.match(source, /\.header-notices\s*\{[^}]*padding: 0 2px;/s)
  assert.doesNotMatch(source, /\.header-notices\s*\{[^}]*overflow-x: auto;/s)
  assert.doesNotMatch(source, /scrollbar-width/)
  /*
   * Panelnya diperkecil 2026-10-11 dalam tiga langkah ("box warning terlihat
   * sangat besar", "masih terlalu besar", lalu "bukan hanya fontnya yang
   * dikecilkan tetapi box nya juga"): tinggi 60px → 52px → 48px → 36px, sudut
   * 14px → 12px → 10px, pesan 15px → 13px → 12px/700. Hurufnya tidak ikut
   * mengecil lagi di langkah terakhir; yang dipotong ruang kosongnya (isi 32px =
   * 16px baris + 8px bantalan atas-bawah). Target sentuh 44px dijaga terpisah
   * lewat `::after` di wilayah aksi, lencana tutup, dan chip.
   * Warnanya tetap dari keluarga token POS lewat alias `--notice-*`.
   */
  assert.match(source, /\.notice-pair\s*\{[^}]*--notice-bg: var\(--pos-danger-bg\);[^}]*min-height: 36px;[^}]*border: 2px solid var\(--notice-border\);[^}]*border-radius: 10px;[^}]*linear-gradient\(135deg, var\(--notice-bg\), var\(--notice-deep\)\);/s)
  assert.match(source, /\.notice-pair-text\s*\{[^}]*line-height: 16px;[^}]*font-size: 12px;[^}]*font-weight: 700;/s)
  /* Chip di baris yang sama ikut tinggi barunya, dan daerah kliknya tetap 44px. */
  assert.match(source, /\.notice-chip\s*\{[^}]*min-height: 36px;[^}]*border-radius: 18px;/s)
  assert.match(source, /\.notice-chip::after\s*\{[^}]*position: absolute;[^}]*inset: -4px 0;/s)
  /*
   * Wilayah solusi, bukan kotaknya sendiri (permintaan pemilik, 2026-10-11):
   * tanpa garis keliling, hanya `border-left` 2px sebagai pemisah dari pesan,
   * latar memakai warna kotak pilnya dulu, dan sudut kanannya mengikuti
   * lengkung panel (10px luar - 2px garis = 8px). Tingginya 32px mengikuti isi
   * panel, dan daerah klik 44px-nya dibuat `::after`.
   */
  assert.match(
    source,
    /\.notice-pair \.chip-action\s*\{[^}]*border: 0;[^}]*border-left: 2px solid var\(--notice-border\);[^}]*border-radius: 0 8px 8px 0;[^}]*linear-gradient\(135deg, var\(--notice-deep\), var\(--notice-edge\)\);[^}]*color: var\(--pos-text\);/s,
  )
  assert.match(source, /\.notice-pair \.chip-action\s*\{[^}]*align-self: stretch;[^}]*min-height: 32px;/s)
  assert.match(source, /\.notice-pair \.chip-action::after\s*\{[^}]*inset: -6px 0;/s)
  assert.doesNotMatch(source, /\.notice-pair \.chip-action\s*\{[^}]*border-radius: 999px;/s)
  assert.match(
    source,
    /\.notice-pair\.is-print,\s*\n\.notice-pair\.is-wait\s*\{[^}]*--notice-bg: var\(--pos-warning-bg\);[^}]*--notice-border: var\(--pos-warning-border\);/,
  )
  /* Nada yang lebih khusus ditulis sesudahnya, supaya ia yang menang. */
  assert.ok(
    source.indexOf('.notice-pair.is-ok {') > source.indexOf('.notice-pair.is-print,'),
    'nada panel cetak harus ditulis setelah keluarga is-print',
  )
  /* Nada panel cetak dipakai di template, bukan cuma dihitung. */
  assert.match(printPair, /:class="printChipTone"/)
  /*
   * Teks chip tetap menjadi live region (pengganti band lama): perubahan status
   * cetak/antrean selagi modal terbuka masih diumumkan pembaca layar.
   */
  assert.match(printPair, /class="notice-pair-text"[^>]*role="status"/)
  assert.match(queueChip, /role="status"/)

  /* Chip hasil kirim batch memakai status singkat, bukan kalimat panjangnya. */
  assert.match(header, /<span class="notice-chip-text" role="status">\{\{ batchChipLabel \}\}<\/span>/)
  assert.doesNotMatch(
    header.slice(header.indexOf(':class="batchChipTone"')),
    /notice-chip-text"[^>]*>\{\{ props\.batchMessage \}\}/,
  )
  assert.match(header, /:title="`\$\{props\.checkoutMessage\} Tersimpan di server\.`"/)
  assert.match(header, /:title="props\.printSyncMessage"/)
  assert.match(header, /:title="props\.batchMessage"/)
})

/*
 * Chip hanya boleh memuat status singkat; kalimat lengkapnya tetap di title dan
 * aria-label. Labelnya diuji langsung karena pemetaan teks panjang ke bentuk
 * singkat itulah yang bisa salah tanpa terlihat di layar.
 */
test('print chip label stays short without losing the full sentence', async () => {
  const h = await historyHarness({})
  const label = () => h.printChipLabel.value

  h.props.printJobId = 'job-1'
  h.props.printSyncMessage = 'Printer belum menjawab. Nota tetap sah dan akan dicoba cetak lagi otomatis.'
  assert.equal(label(), 'Cetak tertunda')

  h.props.printJobId = null
  assert.equal(label(), 'Cetak tertunda')
  h.props.printSyncMessage = 'Nota terkirim ke printer.'
  assert.equal(label(), 'Cetak selesai')
  h.props.printSyncMessage = 'Penghubung cetak menolak nota ini. Nota tetap sah; hubungi teknisi sebelum mencetak ulang.'
  assert.equal(label(), 'Cetak ditolak')
  h.props.printSyncMessage = 'Pekerjaan cetak dikirim ulang.'
  assert.equal(label(), 'Cetak dikirim ulang')
  h.props.printSyncMessage = 'Pesan cetak yang belum dikenal.'
  assert.equal(label(), 'Status cetak')
  h.props.printSyncMessage = ''
  assert.equal(label(), '')

  /* Chip hasil transaksi membawa nomor nota, dan tetap ringkas tanpa nomor. */
  h.props.checkoutResult = { transaction_id: 'T01-000020' }
  assert.equal(h.resultChipLabel.value, 'Tersimpan · T01-000020')
  h.props.checkoutResult = null
  assert.equal(h.resultChipLabel.value, 'Tersimpan')
})

/*
 * Chip "cetak tertunda" membawa jumlah notanya (permintaan pemilik, 2026-10-10),
 * sama seperti chip antrean yang berbunyi "N belum tersinkron" — bukan sekadar
 * kata "tertunda" tanpa angka.
 */
test('print chip text carries the number of unprinted notas from today', async () => {
  const h = await historyHarness({
    printSyncMessage: 'Pekerjaan cetak tertunda; akan dicoba lagi otomatis.',
    printJobId: 'job-2',
    unprintedCount: 2,
    printAlertKey: 'job-1|job-2',
  })

  assert.equal(h.printChipLabel.value, 'Cetak tertunda')
  assert.equal(h.printChipText.value, '2 cetak tertunda')

  /* Semua pekerjaan hari ini dihitung, bukan hanya yang sudah gagal. */
  h.props.unprintedCount = 3
  assert.equal(h.printChipText.value, '3 cetak tertunda')

  /* Keadaan lain tidak ikut diberi angka. */
  h.props.printJobId = null
  h.props.printSyncMessage = 'Nota terkirim ke printer.'
  assert.equal(h.printChipText.value, 'Cetak selesai')
})

/*
 * Penutupan "Cetak tertunda" bertahan setelah halaman dimuat ulang (permintaan
 * pemilik, 2026-10-10): kuncinya himpunan pekerjaan yang gagal, bukan teks
 * status yang disusun ulang. Dua muat-ulang disimulasikan dengan dua harness
 * yang berbagi `localStorage` yang sama.
 */
test('dismissed print notice stays hidden across reload until the job set changes', async () => {
  const sharedStorage = new Map()
  const failedJobs = {
    printSyncMessage: 'Pekerjaan cetak tertunda; akan dicoba lagi otomatis.',
    printJobId: 'job-2',
    unprintedCount: 1,
    printAlertKey: 'job-1|job-2',
    sharedStorage,
  }

  const first = await historyHarness(failedJobs)
  assert.equal(first.showPrintNotice.value, true)
  first.dismissPrint()
  assert.equal(first.showPrintNotice.value, false)

  /* Muat ulang dengan pekerjaan yang sama: pesannya boleh beda teks (pesan
     disusun ulang tiap siklus), chipnya tetap tidak muncul. */
  const reloaded = await historyHarness({
    ...failedJobs,
    printSyncMessage: '1 nota gagal dicetak; akan dicoba lagi otomatis.',
  })
  assert.equal(reloaded.showPrintNotice.value, false)
  /* X menutup seluruh panel termasuk tombol cetak ulang. */
  assert.equal(reloaded.props.printJobId, 'job-2')
  assert.equal(reloaded.printActionsVisible.value, false)
  assert.equal(reloaded.showHeaderNotices.value, false)

  /* Nota lain ikut gagal = himpunan pekerjaan berubah = berita baru muncul. */
  const newer = await historyHarness({
    ...failedJobs,
    printAlertKey: 'job-1|job-2|job-3',
    unprintedCount: 2,
  })
  assert.equal(newer.showPrintNotice.value, true)
})

/*
 * Penutupan "N belum tersinkron" (permintaan pemilik, 2026-10-11) memakai
 * aturan yang sama dengan panel cetak: kuncinya himpunan `sync_id` yang masih
 * antre, sehingga penutupan bertahan setelah halaman dimuat ulang tetapi nota
 * baru yang masuk antrean memunculkan peringatannya lagi. Yang ditutup hanya
 * tampilannya — jumlah antrean dan aksi kirimnya tetap utuh.
 */
test('dismissed queue notice stays hidden across reload until the queue set changes', async () => {
  const sharedStorage = new Map()
  const queuedNotices = [queuedRecord(), queuedRecord({ sync_id: 'local-2' })]
  const queued = { queue: [queuedRecord()], queuedNotices, sharedStorage }

  const first = await historyHarness(queued)
  assert.equal(first.showQueueNotice.value, true)
  first.dismissQueue()
  assert.equal(first.showQueueNotice.value, false)

  /* Pekerjaannya tidak disentuh: masih 2 nota menunggu dan tombolnya ada. */
  assert.equal(first.queuedCount.value, 2)
  assert.equal(first.props.queuedNotices.length, 2)
  assert.equal(first.showHeaderNotices.value, false)

  /* Muat ulang dengan antrean yang sama: pesannya tetap tertutup. */
  const reloaded = await historyHarness(queued)
  assert.equal(reloaded.showQueueNotice.value, false)
  assert.equal(reloaded.queuedCount.value, 2)

  /* Nota baru masuk antrean = himpunan berubah = berita baru muncul. */
  const newer = await historyHarness({
    ...queued,
    queuedNotices: [...queuedNotices, queuedRecord({ sync_id: 'local-3' })],
  })
  assert.equal(newer.showQueueNotice.value, true)
})

/*
 * Dua permintaan pemilik 2026-10-11 di panel riwayat, diuji dari sumbernya:
 * (1) "Status aksi" tidak boleh turun ke bawah selama ruangnya ada di layar
 * desktop — keputusannya milik flexbox, bukan ambang lebar jendela;
 * (2) tombol kirim ulang nota antre pindah ke kanan atas kartu nota dan dibuat
 * lebih mencolok.
 */
/*
 * Dua peringatan harus terbaca tanpa digeser ke kanan-kiri **dan** tanpa turun
 * ke barisnya sendiri di tablet (permintaan pemilik, 2026-10-11): yang menjaga
 * keduanya bukan angka di CSS, melainkan pembagian ruangnya — baris panel tetap
 * di sebelah judul selama ruangnya ada (judulnya yang menyusut), dan baru pindah
 * ke barisnya sendiri di bawah 900px, tempat tidak ada ruang untuk dibagi lagi.
 */
/*
 * Keluarga amber harus berasal dari palet pemilik (permintaan pemilik,
 * 2026-10-11: "warna kedua warning tidak cocok ketika light mode", dengan
 * gambar palet sebagai acuan). Yang dijaga di sini nilainya, bukan bentuk
 * panelnya: latar dan tinta memakai pasangan amber palet (#FDEBC8/#8A4B08)
 * yang sama dengan keluarga "stok tipis", garisnya tinta itu sendiri seperti
 * panel bahaya, dan mustard #D9A441 yang lama (2,04:1 terhadap latarnya)
 * tidak boleh kembali. Di mode gelap nilai `-edge` sengaja tetap #7a5a1c —
 * warna garis lama — supaya tampilan gelap tidak ikut berubah.
 *
 * Jaraknya juga dijaga: dua panel yang sebaris berjarak 24px, sedangkan di
 * layar di bawah ambang barisnya (`max-width: 900px`) tidak ada yang berubah
 * (permintaan pemilik: "Jika di layar yang lebih kecil sudah cukup berjarak
 * tanpa membuat warning nya bisa di scroll maka biarkan").
 */
test('amber alert family follows the owner palette and leaves room between panels', async () => {
  const css = await readFile(new URL('../src/assets/main.css', import.meta.url), 'utf8')
  const history = await readFile(new URL('../src/components/TransactionHistory.vue', import.meta.url), 'utf8')

  /* Pasangan amber palet, nilai yang sama dengan keluarga "stok tipis". */
  assert.match(css, /--pos-lowstock-bg: #fdebc8;/)
  assert.match(css, /--pos-warning-bg: #fdebc8;/)
  assert.match(css, /--pos-warning-text: #8a4b08;/)
  /* Garis = tinta, resep yang sama dengan panel bahaya (`--pos-danger`). */
  assert.match(css, /--pos-warning-border: #8a4b08;/)
  assert.doesNotMatch(css, /#d9a441/)
  assert.doesNotMatch(css, /#fdf3e0/)
  /* Ujung gradien wilayah aksi punya token sendiri, tidak meminjam warna garis. */
  assert.match(css, /--pos-warning-edge: #e3c693;/)
  assert.match(css, /\[data-theme='dark'\]\s*\{[\s\S]*?--pos-warning-edge: #7a5a1c;/)
  assert.match(history, /--notice-edge: var\(--pos-warning-edge\);/)
  assert.doesNotMatch(history, /--notice-edge: var\(--pos-warning-border\);/);

  /* Jarak lebih antar dua panel, hanya saat keduanya duduk sebaris. */
  /*
   * Jarak lebih antar dua panel, hanya saat keduanya duduk sebaris.
   * Ambangnya dicari sebagai blok, bukan lewat regex satu baris: barisnya
   * memang multi-baris, jadi aturan media query-nya dibaca sebagai potongan
   * teks dan isinya diperiksa dengan pola yang tidak memuat baris baru.
   */
  const wideRule = history.slice(history.indexOf('@media (min-width: 901px)'))
  assert.notEqual(wideRule, '', 'aturan jarak layar lebar harus ada')
  assert.match(
    wideRule,
    /@media \(min-width: 901px\) \{\s*\.header-notices \{[^}]*column-gap: 24px;/,
  )
  /*
   * Ditambahkan sebagai jarak baris, bukan margin pada panel: di 901-1006px
   * barisnya masih bisa membungkus (terukur di 901x800), dan margin akan
   * membuat panel yang turun ke baris kedua menjorok dari panel pertama.
   */
  assert.doesNotMatch(wideRule, /margin-left: 8px/)
  assert.match(history, /\.header-notices\s*\{[^}]*gap: 16px;[^}]*row-gap: 14px;/s)
})
/*
 * Peringatan bukti transfer tidak langsung hilang saat "Kirim semua" ditekan
 * (laporan pemilik, 2026-10-11). Sebabnya baris bukti menunggu notanya lebih
 * dulu; selama nota itu sedang naik, barisnya tidak perlu menggantung di
 * layar. Yang dijaga di sini dua sisi sekaligus: ia hilang **seketika**
 * (penyaring membaca `batchSending`, bukan menunggu unggahan selesai), dan ia
 * **kembali** kalau pengirimannya gagal (daftar dibaca ulang saat batch
 * selesai) — jadi tidak ada keadaan yang disembunyikan secara permanen.
 *
 * Cakupannya sengaja sempit: hanya bukti yang `sync_id`-nya ada di antrean
 * yang sedang dikirim. Bukti milik nota yang sudah ada di server tidak ikut
 * hilang, karena mengirim antrean tidak menyentuh unggahannya.
 */
test('bukti yang sedang dikirim hilang seketika dan kembali kalau gagal', async () => {
  const source = await readFile(new URL('../src/components/TransactionHistory.vue', import.meta.url), 'utf8')
  const { visibleBannerProofs } = await import('../src/utils/bannerProofs.js')

  /* Panelnya memakai daftar tersaring: judul, v-if, dan barisnya. */
  assert.match(source, /<div v-if="bannerProofs\.length > 0" class="proof-banner">/)
  assert.match(source, /v-for="proof in bannerProofs"/)
  assert.match(source, /\{\{ bannerProofs\.length \}\} bukti transfer belum terkirim ke server/)
  assert.doesNotMatch(source, /outstandingProofs\.length > 0" class="proof-banner"/)

  /* Hilang seketika: pemicunya status pengiriman batch, bukan hasil unggahan. */
  assert.match(source, /const bannerProofs = computed\(\(\) =>[\s\S]*?batchSending: props\.batchSending,/)
  assert.match(source, /const queuedProofSyncIds = computed\([\s\S]*?props\.queuedNotices/)
  /* Dan dibaca ulang saat pengiriman selesai, bukan saat modal dibuka lagi. */
  assert.match(
    source,
    /watch\([\s\S]*?\(\) => props\.batchSending,[\s\S]*?refreshOutstandingProofs\(\)/,
  )

  const proofs = [
    { proof_id: 'a', sync_id: 'q-1', upload_status: 'pending' },
    { proof_id: 'b', sync_id: 's-9', upload_status: 'retryable' },
    { proof_id: 'c', upload_status: 'captured' },
  ]
  const queued = new Set(['q-1'])
  const ids = (list) => list.map((proof) => proof.proof_id)

  /* Sebelum apa-apa: ketiganya tampil. */
  assert.deepEqual(ids(visibleBannerProofs(proofs, { queuedSyncIds: queued })), ['a', 'b', 'c'])
  /* "Kirim semua" berjalan: hanya bukti yang notanya sedang dikirim yang hilang. */
  assert.deepEqual(
    ids(visibleBannerProofs(proofs, { batchSending: true, queuedSyncIds: queued })),
    ['b', 'c'],
  )
  /* Pengiriman gagal: barisnya kembali seperti semula. */
  assert.deepEqual(ids(visibleBannerProofs(proofs, { queuedSyncIds: queued })), ['a', 'b', 'c'])
  /* Daftar kosong/null tidak pernah membuat panelnya galat. */
  assert.deepEqual(visibleBannerProofs(null, { batchSending: true, queuedSyncIds: queued }), [])
})
test('two warnings wrap instead of forcing a sideways scroll', async () => {
  const source = await readFile(new URL('../src/components/TransactionHistory.vue', import.meta.url), 'utf8')

  /* Tidak ada lagi baris geser: tidak ada overflow-x pada baris panelnya. */
  assert.doesNotMatch(source, /\.header-notices\s*\{[^}]*overflow-x/s)
  assert.match(source, /\.header-notices\s*\{[^}]*flex-wrap: wrap;/s)
  assert.match(source, /\.header-notices\s*\{[^}]*max-width: 100%;/s)

  /*
   * Judul boleh menyusut (`flex-shrink: 1`, dulu `0`) supaya baris panel tidak
   * dipaksa turun di tablet — inilah yang membuat versi "baris penuh di bawah
   * 1120px" dikembalikan.
   */
  assert.match(source, /\.modal-title\s*\{[^}]*min-width: 0;[^}]*flex-shrink: 1;/s)
  assert.doesNotMatch(source, /\.modal-title\s*\{[^}]*flex-shrink: 0;/s)

  /*
   * Ambang baris-penuh tetap 900px (bukan 1120px): 1120px sempat dipakai
   * 2026-10-11, tetapi di tablet panelnya lalu selalu turun ke bawah judul.
   */
  assert.match(source, /@media \(max-width: 900px\) \{\s*\.modal-header \{\s*flex-wrap: wrap;/s)
  /*
   * Blok 900px yang terakhir: yang menangani header, bukan yang menumpuk panel.
   * Potongannya berhenti di media query berikutnya (jarak antar panel di
   * `min-width: 901px`) — bukan di `</style>` — supaya yang diperiksa tetap
   * isi blok 900px ini sendiri.
   */
  const wideStart = source.lastIndexOf('@media (max-width: 900px)')
  const wideEnd = source.indexOf('@media (min-width: 901px)', wideStart)
  const wide = source.slice(wideStart, wideEnd === -1 ? source.indexOf('</style>') : wideEnd)
  assert.match(wide, /\.header-notices\s*\{[^}]*order: 1;[^}]*width: 100%;/s)
  assert.doesNotMatch(wide, /\.notice-pair\s*\{/)
  assert.doesNotMatch(source, /@media \(max-width: 1120px\)/)
})

test('filter row wraps by its own width and the resend button sits top-right of the nota card', async () => {
  const source = await readFile(new URL('../src/components/TransactionHistory.vue', import.meta.url), 'utf8')

  /*
   * Baris cakupan: satu-satunya keputusan "turun atau tidak" adalah lebar baris
   * itu sendiri. Ambang berbasis lebar jendela (`max-width: 1365px`) dihapus
   * 2026-10-11 karena di layar lebar pun dropdownnya turun di bawah padahal
   * ruangnya ada — panelnya yang sempit, bukan jendelanya.
   */
  assert.doesNotMatch(source, /@media[^{]*max-width: 1365px/)
  assert.match(source, /\.scope-row\s*\{[^}]*flex-wrap: wrap;[^}]*row-gap: 8px;/s)
  /* Label + dropdown adalah satu anak flex: membungkus sebagai satu kesatuan. */
  assert.match(source, /\.scope-status-group\s*\{[^}]*flex: 1 1 auto;[^}]*margin-left: auto;/s)

  /*
   * Tombol kirim ulang: di dalam kartu nota (`detail-summary-head`), bukan lagi
   * di bawah paragraf penjelasan. Aksinya tetap milik induk lewat `resendQueued`
   * dan hanya untuk nota yang masih antre di perangkat.
   */
  const headStart = source.indexOf('class="detail-summary-head"')
  assert.ok(headStart > -1, 'kartu nota tidak memakai baris dua kolom')
  const head = source.slice(headStart, source.indexOf('</div>', source.indexOf('KIRIM ULANG NOTA INI', headStart)))
  assert.match(head, /v-if="selectedNota\.local_queue"/)
  assert.match(head, /class="resend-nota-button"/)
  assert.match(head, /@click="resendQueued\(selectedNota\.sync_id\)"/)

  /* Bentuk lamanya tidak boleh tersisa di blok penjelasan di bawah. */
  const hintStart = source.indexOf('class="queue-detail-actions"')
  const hint = source.slice(hintStart, source.indexOf('</div>', hintStart))
  assert.ok(hintStart > -1, 'blok penjelasan nota antre tidak ada')
  assert.doesNotMatch(hint, /KIRIM ULANG NOTA INI/)
  assert.match(hint, /tombol di kanan atas/)

  /* Mencolok: pil terisi bergaris 2px dari keluarga bahaya, teks 800. */
  assert.match(
    source,
    /\.detail-summary-head\s*\{[^}]*display: flex;[^}]*flex-wrap: wrap;[^}]*justify-content: space-between;/s,
  )
  assert.match(
    source,
    /\.resend-nota-button\s*\{[^}]*border: 2px solid var\(--pos-danger\);[^}]*border-radius: 999px;[^}]*linear-gradient\(135deg, var\(--pos-danger-bg\), var\(--pos-danger-deep\)\);[^}]*font-weight: 800;/s,
  )
  assert.match(source, /\.resend-nota-button\s*\{[^}]*min-height: 44px;/s)
})

/*
 * Hasil kirim batch juga hanya status singkat di chip; kalimat penuhnya tetap
 * di title/aria-label. Pesan nyatanya bisa memuat dua sinyal sekaligus
 * ("2 nota terkirim, 1 ditolak"), jadi urutan pemeriksaan label diuji langsung.
 */
test('batch chip label stays short without losing the full sentence', async () => {
  const h = await historyHarness({ batchMessage: '' })
  const label = () => h.batchChipLabel.value

  assert.equal(label(), '')

  h.props.batchMessage = '3 nota terkirim.'
  assert.equal(label(), 'Nota berhasil dikirim')

  /*
   * Keadaan "perlu diperiksa" tidak punya chip sama sekali (permintaan
   * pemilik, 2026-10-11). Yang penting: labelnya **kosong**, bukan jatuh ke
   * label kiriman berhasil karena kalimatnya memuat kata "terkirim".
   */
  h.props.batchMessage =
    '2 nota terkirim, 1 ditolak. Nota yang ditolak/konflik/belum jelas butuh pemeriksaan; jangan membuat pengganti.'
  assert.equal(label(), '')
  assert.equal(h.batchChipVisible.value, false)

  h.props.batchMessage = 'Antrean tidak dapat dibaca. Periksa penyimpanan perangkat; antrean tetap aman.'
  assert.equal(label(), 'Kirim gagal')

  h.props.batchMessage = 'Hasil belum diketahui; antrean ditahan, jangan membuat pengganti.'
  assert.equal(label(), '')

  /* Keadaan lain tetap punya chip-nya. */
  h.props.batchMessage = '1 nota terkirim, 1 konflik. Butuh pemeriksaan.'
  assert.equal(label(), '')

  h.props.batchMessage = 'Perangkat belum terdaftar; antrean tetap aman, kirim lagi setelah terdaftar.'
  assert.equal(label(), 'Belum terdaftar')

  h.props.batchMessage = 'Tidak ada nota yang siap dikirim; periksa antrean.'
  assert.equal(label(), 'Tidak ada nota')

  h.props.batchMessage = 'Pesan batch yang belum dikenal.'
  assert.equal(label(), 'Hasil kirim')
})

/*
 * Permintaan pemilik 2026-10-11: chip hasil kirim batch berbunyi "Nota berhasil
 * dikirim" dengan kotak hijau yang sama seperti chip "Tersimpan" saat nota
 * masuk online. Label dan nadanya diuji bersama karena satu konstanta yang
 * menentukan keduanya — kalau labelnya bergeser sendiri, warnanya ikut salah.
 */
test('batch success chip uses the online-save wording and tone', async () => {
  const h = await historyHarness({ batchMessage: '' })
  const source = await readFile(new URL('../src/components/TransactionHistory.vue', import.meta.url), 'utf8')

  h.props.batchMessage = '3 nota terkirim.'
  assert.equal(h.batchChipLabel.value, 'Nota berhasil dikirim')
  assert.equal(h.batchChipTone.value, 'is-ok')

  /* Kabar yang belum beres tidak boleh ikut hijau. */
  h.props.batchMessage = 'Antrean tidak dapat dibaca. Periksa penyimpanan perangkat; antrean tetap aman.'
  assert.equal(h.batchChipLabel.value, 'Kirim gagal')
  assert.equal(h.batchChipTone.value, 'is-info')

  h.props.batchMessage = 'Perangkat belum terdaftar; antrean tetap aman, kirim lagi setelah terdaftar.'
  assert.equal(h.batchChipLabel.value, 'Belum terdaftar')
  assert.equal(h.batchChipTone.value, 'is-info')

  /* Kotak hijaunya chip sukses milik token POS yang sama dengan "Tersimpan". */
  assert.match(
    source,
    /\.notice-chip\.is-ok\s*\{[^}]*border-color: var\(--pos-success-text\);[^}]*background: var\(--pos-success-bg\);[^}]*color: var\(--pos-success-text\);/s,
  )
  assert.match(source, /class="notice-chip is-ok"/)
  assert.match(source, /:class="batchChipTone"/)
})

/*
 * Regresi yang dilaporkan pemilik 2026-10-11: "nota 000029 yang baru saja
 * tersinkron tidak langsung berubah statusnya, harus di-refresh terlebih
 * dahulu". Pemicu muat ulangnya harus daftar antrean perangkat — nota yang
 * keluar dari antrean berarti barisnya sudah ada di server — dan pemicu itu
 * harus terbaca tanpa melihat DOM, karena itulah yang diuji di sini.
 */
test('queue signature drives the history reload when a nota leaves the queue', async () => {
  const h = await historyHarness({
    queuedNotices: [
      { sync_id: 'q-1', queue_label: 'Belum terkirim' },
      { sync_id: 'q-2', queue_label: 'Hasil belum diketahui' },
    ],
  })
  const source = await readFile(new URL('../src/components/TransactionHistory.vue', import.meta.url), 'utf8')

  const before = h.queueSignature.value
  assert.match(before, /q-2/)

  /* Array baru dengan isi yang sama bukan perubahan: layar tidak boleh berkedip. */
  h.props.queuedNotices = [
    { sync_id: 'q-1', queue_label: 'Belum terkirim' },
    { sync_id: 'q-2', queue_label: 'Hasil belum diketahui' },
  ]
  assert.equal(h.queueSignature.value, before)

  /* Status satu nota berubah (mis. pending → hasil belum diketahui) tetap terbaca. */
  h.props.queuedNotices = [
    { sync_id: 'q-1', queue_label: 'Belum terkirim' },
    { sync_id: 'q-2', queue_label: 'Belum terkirim' },
  ]
  assert.notEqual(h.queueSignature.value, before)

  /* Nota yang selesai dikirim keluar dari antrean: itu pemicunya. */
  h.props.queuedNotices = [{ sync_id: 'q-1', queue_label: 'Belum terkirim' }]
  assert.notEqual(h.queueSignature.value, before)
  assert.doesNotMatch(h.queueSignature.value, /q-2/)

  /* Pemicunya benar-benar terpasang, dan jawaban basi tidak menimpa yang baru. */
  assert.match(source, /watch\(queueSignature, async \(\) => \{/)
  assert.match(source, /if \(sequence !== loadSequence\) \{\s*\n\s*return/)
  assert.match(source, /if \(sequence === loadSequence\) \{\s*\n\s*loading\.value = false/)
})

test('history print retry delegates the existing queue action, never the selected nota reprint', async () => {
  const h = await historyHarness({ printJobId: 'existing-job' })
  h.selectedNota.value = { transaction_id: 'T01-other' }
  h.retryPrint()
  assert.deepEqual(JSON.parse(JSON.stringify(h.emitted)), [['retry-print']])

  h.props.checkoutStatus = 'submitting'
  h.retryPrint()
  h.props.checkoutStatus = 'idle'
  h.props.printJobId = null
  h.retryPrint()
  assert.equal(h.emitted.length, 1)
})

test('feedback ownership keeps successful checkout and printer retry out of cart but critical alerts in it', async () => {
  const cart = await readFile(new URL('../src/components/CartList.vue', import.meta.url), 'utf8')
  const app = await readFile(new URL('../src/App.vue', import.meta.url), 'utf8')
  const history = await readFile(new URL('../src/components/TransactionHistory.vue', import.meta.url), 'utf8')
  assert.match(cart, /v-if="cartFeedbackVisible"/)
  assert.match(cart, /checkoutStatus === 'conflict' \|\| checkoutStatus === 'review'/)
  /* Bukti transfer tetap milik keranjang; tidak boleh muncul di dua tempat. */
  assert.match(cart, /cartFeedbackIsProofOnly/)
  assert.doesNotMatch(cart, /printSyncMessage|printJobId|retry-print|is-success/)
  const cartBinding = app.match(/<CartList[\s\S]*?\/>/)[0]
  const historyBinding = app.match(/<TransactionHistory[\s\S]*?\/>/)[0]
  assert.doesNotMatch(cartBinding, /print-sync-message|print-job-id|retry-print/)
  assert.match(cartBinding, /:proof-sync-message="proofSyncMessage"/)
  assert.doesNotMatch(historyBinding, /proof-sync-message/)
  for (const prop of ['checkout-status', 'checkout-message', 'checkout-result', 'print-sync-message', 'print-job-id', 'unprinted-count', 'print-alert-key']) {
    assert.ok(historyBinding.includes(`:${prop}=`), prop)
  }
  assert.match(historyBinding, /@retry-print="retryPrint"/)
  assert.match(history, /class="header-notices"/)
  assert.match(history, /Cetak ulang/)
})

/*
 * Tombol "Riwayat Nota" memberi tahu dua hal yang berbeda tanpa membuka modal
 * (permintaan pemilik, 2026-10-10): lencana angka untuk nota yang belum
 * tersinkron ke server, dan lencana angka untuk nota yang kertasnya belum
 * keluar dari printer. Yang diuji di sini adalah sambungannya ke sumber data
 * nyata — jumlah notanya dibaca dari antrean cetak perangkat, bukan disimpulkan
 * dari pesan terakhir — dan bentuk penandanya di template.
 */
test('history button marks unprinted notas from the device print queue', async () => {
  const h = await harness()
  const app = await readFile(new URL('../src/App.vue', import.meta.url), 'utf8')

  /* Tanpa pekerjaan cetak, tombolnya tombol biasa. */
  assert.equal(h.app.outstandingPrintCount.value, 0)
  assert.equal(h.app.outstandingPrintKey.value, '')
  assert.equal(h.app.historyNotice.value.hasPrintAlert, false)
  assert.equal(h.app.historyNotice.value.title, 'Riwayat Nota')

  /*
   * Satu nota diantar = dua pekerjaan (nota + surat jalan), tetapi satu nota.
   * Tanggal penjualan berasal dari transaction_time, bukan waktu job dicatat.
   */
  const createdToday = new Date().toISOString()
  h.setOutstandingPrintJobs([
    { job_id: 'job-1', sync_id: 'sale-1', status: 'retryable', attempts: 2, transaction_time: createdToday, created_at: createdToday },
    { job_id: 'job-2', sync_id: 'sale-1', status: 'retryable', attempts: 2, transaction_time: createdToday, created_at: createdToday },
  ])
  await h.app.refreshOutstandingPrintJob()
  assert.equal(h.app.outstandingPrintCount.value, 1)
  assert.equal(h.app.outstandingPrintJobId.value, 'job-2')
  assert.equal(h.app.outstandingPrintKey.value, 'job-1|job-2')
  assert.equal(h.app.historyNotice.value.hasPrintAlert, true)
  assert.equal(h.app.historyNotice.value.unprinted, 1)
  assert.match(h.app.historyNotice.value.title, /1 nota hari ini belum tercetak/)

  /* Nota hari ini yang baru masuk antrean ikut dihitung, nota lama tidak. */
  const today = new Date()
  const localDayStart = new Date(today.getFullYear(), today.getMonth(), today.getDate()).toISOString()
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1).toISOString()
  h.setOutstandingPrintJobs([
    { job_id: 'job-1', sync_id: 'sale-1', status: 'retryable', attempts: 2, transaction_time: localDayStart, created_at: localDayStart },
    { job_id: 'job-2', sync_id: 'sale-1', status: 'retryable', attempts: 2, transaction_time: localDayStart, created_at: localDayStart },
    { job_id: 'job-3', sync_id: 'sale-2', status: 'pending', attempts: 0, transaction_time: localDayStart, created_at: localDayStart },
    { job_id: 'job-4', sync_id: 'sale-old', status: 'retryable', attempts: 1, transaction_time: yesterday, created_at: localDayStart },
  ])
  await h.app.refreshOutstandingPrintJob()
  assert.equal(h.app.outstandingPrintCount.value, 2)
  assert.equal(h.app.outstandingPrintKey.value, 'job-1|job-2|job-3|job-4')

  /* Kertas keluar: penandanya hilang sendiri setelah antrean dibaca ulang. */
  h.setOutstandingPrintJobs([])
  await h.app.refreshOutstandingPrintJob()
  assert.equal(h.app.outstandingPrintCount.value, 0)
  assert.equal(h.app.outstandingPrintJobId.value, null)

  /*
   * Kedua angkanya dekoratif — artinya dibawa `title`/`aria-label` tombol —
   * dan glyph-nya membedakan kanal tanpa bersandar pada warna saja. Lencana
   * hanya muncul dari keadaan antrean, bukan dari sisa perhitungan lama.
   */
  assert.match(app, /:title="historyNotice\.title"/)
  assert.match(app, /:aria-label="historyNotice\.ariaLabel"/)
  assert.match(
    app,
    /v-if="historyNotice\.hasPrintAlert"[\s\S]{0,200}?class="history-count is-print"[\s\S]{0,400}?\{\{ historyNotice\.unprinted \}\}/,
  )
  assert.match(
    app,
    /v-if="historyNotice\.hasQueued"[\s\S]{0,200}?class="history-count is-sync"[\s\S]{0,400}?\{\{ historyNotice\.queued \}\}/,
  )
  assert.doesNotMatch(app, /history-print-dot/)
  assert.doesNotMatch(app, /v-if="queuedNotices\.length > 0" class="history-badge"/)
})

/*
 * Regresi yang tertangkap di browser sebelum perbaikan: dengan syarat hanya
 * "checkoutMessage || proofSyncMessage", nota sukses tetap tampil di keranjang
 * sehingga notanya terlihat di dua tempat sekaligus.
 */
/*
 * Chip hasil kirim hanya ada kalau ada labelnya: keadaan "perlu diperiksa"
 * sengaja kosong (permintaan pemilik, 2026-10-11), jadi chipnya tidak muncul.
 * Diuji lewat computed yang dipakai template, bukan hanya lewat pemetaan label.
 */
test('batch chip hides itself when the label is empty', async () => {
  const h = await historyHarness({ batchMessage: '' })

  assert.equal(h.batchChipVisible.value, false)

  h.props.batchMessage = '2 nota terkirim, 1 ditolak. Butuh pemeriksaan; jangan membuat pengganti.'
  assert.equal(h.batchChipLabel.value, '')
  assert.equal(h.batchChipVisible.value, false)

  h.props.batchMessage = '3 nota terkirim.'
  assert.equal(h.batchChipLabel.value, 'Nota berhasil dikirim')
  assert.equal(h.batchChipVisible.value, true)

  /* Ditutup tetap berlaku, dan hanya sampai pesannya berubah. */
  h.dismissBatch()
  assert.equal(h.batchChipVisible.value, false)
  h.props.batchMessage = '1 nota terkirim.'
  assert.equal(h.batchChipVisible.value, true)
})

/*
 * Jumlah di tombol "Kirim semua" dihapus (permintaan pemilik, 2026-10-11);
 * jumlahnya tetap ada di pesan panel dan di lencana tombol "Riwayat Nota".
 */
test('queue action label no longer repeats the queue count', async () => {
  const source = await readFile(new URL('../src/components/TransactionHistory.vue', import.meta.url), 'utf8')

  assert.match(source, /props\.batchSending \? 'Mengirim…' : 'Kirim semua' \}\}/)
  assert.doesNotMatch(source, /Kirim semua \(\$\{queuedCount\}\)/)
  /* Angkanya tetap di pesannya. */
  assert.match(source, /\{\{ queuedCount \}\} belum tersinkron/)
})

test('cart feedback membership is decided by blocking failures and pending proof alone', async () => {
  const h = await harness()

  /* Nota sukses tidak lagi tampil di keranjang, walau hasilnya masih tersimpan. */
  h.props.checkoutStatus = 'success'
  h.props.checkoutMessage = 'Transaksi berhasil disimpan.'
  h.props.proofSyncMessage = ''
  assert.equal(h.cartUi.cartFeedbackVisible.value, false)

  /* Pesan yang menghalangi penjualan berikutnya tetap di keranjang. */
  for (const status of ['review', 'error', 'conflict', 'retryable', 'rate_limited', 'pin_cooldown']) {
    h.props.checkoutStatus = status
    assert.equal(h.cartUi.cartFeedbackVisible.value, true, status)
  }

  /* Bukti transfer yang belum terkirim tetap diumumkan sesudah nota sukses. */
  h.props.checkoutStatus = 'success'
  h.props.proofSyncMessage = '1 bukti transfer belum terkirim; akan dicoba lagi otomatis.'
  assert.equal(h.cartUi.cartFeedbackVisible.value, true)
  assert.equal(h.cartUi.cartFeedbackIsProofOnly.value, true)

  /* Kesalahan nyata tidak boleh ikut dicat sebagai peringatan bukti saja. */
  h.props.checkoutStatus = 'review'
  assert.equal(h.cartUi.cartFeedbackIsProofOnly.value, false)
})

/*
 * Kontrak gaya fokus kotak pencarian (laporan pemilik, 2026-10-10): kolom teks
 * selalu cocok `:focus-visible` begitu diklik, jadi aturan fokus global di
 * `main.css` sempat menggambar persegi netral di dalam kolom padahal pembungkus
 * `.search-bar` sudah menjadi penanda fokusnya. Pengecualian itu hanya sah
 * selama cincin pembungkusnya masih ada dan aturan globalnya tetap berlaku
 * untuk kontrol lain.
 */
test('search focus ring stays a single wrapper indicator', async () => {
  const css = await readFile(new URL('../src/assets/main.css', import.meta.url), 'utf8')
  const searchBar = await readFile(new URL('../src/components/SearchBar.vue', import.meta.url), 'utf8')

  /* Aturan global tidak boleh hilang: kontrol lain masih butuh outline netral. */
  assert.match(css, /#app :is\(button, input, select, textarea, \[tabindex\]\):focus-visible \{\s*\n\s*outline: 2px solid var\(--pos-focus\);/)
  /* Pengecualian hanya untuk kolom pencarian, bukan seluruh input POS. */
  assert.match(css, /#app \.search-bar input:focus-visible \{\s*\n\s*outline: none;/)
  /* Penanda fokus penggantinya wajib tetap ada, kalau tidak ini jadi bug aksesibilitas. */
  assert.match(searchBar, /\.search-bar:focus-within \{[^}]*border-color: var\(--pos-accent\);/)
  assert.match(searchBar, /\.search-bar:focus-within \{[^}]*box-shadow: 0 0 0 3px var\(--pos-accent-soft\);/)
  /* Kolomnya sendiri tetap tanpa border/background supaya tidak ada kotak kedua. */
  assert.match(searchBar, /border: none;\s*\n\s*outline: none;/)
})

test('starting a new checkout still clears previous success via the existing finish event', async () => {
  const h = await harness()
  h.props.checkoutStatus = 'success'
  h.props.cartItems = [{ item_name: 'Next item', quantity: 1, unit_price: 100 }]
  h.cartUi.openConfirmation()
  assert.deepEqual(JSON.parse(JSON.stringify(h.emitted.at(-1))), ['checkout-finished'])
  assert.equal(h.cartUi.showConfirmation.value, true)
})

test('local row detail opens from the device copy without a server call', async () => {
  const h = await historyHarness({ queue: [queuedRecord()] })
  await h.loadPage(1)
  assert.equal(h.notas.value.length, 1)
  await h.openNota(h.notas.value[0])
  assert.equal(h.detailCalls.length, 0)
  assert.equal(h.selectedItems.value.length, 1)
  assert.equal(h.selectedItems.value[0].subtotal, 245000)
  /* Total barang dihitung dari payload, bukan dibiarkan RpNaN. */
  assert.equal(h.selectedNota.value.item_amount, 245000)
  assert.equal(h.selectedNota.value.shipping_amount, 0)
  assert.equal(h.detailError.value, '')
})

test('local row detail keeps stored payload amounts when present', async () => {
  const h = await historyHarness({
    queue: [queuedRecord({
      payload: {
        payment_method: 'delivery',
        item_amount: 200000,
        shipping_amount: 15000,
        total_amount: 215000,
        items: [{
          item_name: 'Aries Pintu', variant: 'Abu', quantity: 1, unit_price: 245000, discount: 45000,
        }],
      },
    })],
  })
  await h.loadPage(1)
  await h.openNota(h.notas.value[0])
  assert.equal(h.selectedNota.value.item_amount, 200000)
  assert.equal(h.selectedNota.value.shipping_amount, 15000)
  assert.equal(h.selectedItems.value[0].subtotal, 200000)
})

test('server failure still shows the queue instead of an empty error', async () => {
  const h = await historyHarness({
    queue: [queuedRecord()],
    historyError: Object.assign(new Error('Failed to fetch'), { status: undefined }),
  })
  await h.loadPage(1)
  assert.equal(h.notas.value.length, 1)
  assert.equal(h.notas.value[0].local_queue, true)
  assert.match(h.errorMessage.value, /antrean/)
})

test('channel summary separates server, proof, and print facts', async () => {
  const h = await historyHarness()
  // Lewat JSON karena hasil lahir di realm VM yang berbeda.
  const channels = (args) => JSON.parse(JSON.stringify(h.summarizeChannels(args)))
  assert.deepEqual(
    channels({ proofs: [{ upload_status: 'uploaded' }], jobs: [{ status: 'printed' }] }),
    { proof: 'uploaded', print: 'printed' },
  )
  assert.deepEqual(
    channels({ proofs: [{ upload_status: 'retryable' }], jobs: [{ status: 'pending' }] }),
    { proof: 'waiting', print: 'queued' },
  )
  assert.deepEqual(
    channels({ proofs: [{ upload_status: 'conflict' }], jobs: [{ status: 'blocked' }] }),
    { proof: 'attention', print: 'blocked' },
  )
  assert.deepEqual(channels({ proofs: [], jobs: [] }), { proof: 'none', print: 'none' })
})

test('history rows map each nota to its own proof and print state', async () => {
  const h = await historyHarness({
    proofs: [{ proof_id: 'p1', sync_id: 'a', upload_status: 'uploaded' }],
    jobs: [
      { job_id: 'j1', sync_id: 'a', status: 'printed' },
      { job_id: 'j2', sync_id: 'b', status: 'retryable' },
    ],
  })
  h.notas.value = [{ sync_id: 'a' }, { sync_id: 'b' }, { sync_id: 'c' }]
  await h.loadNotaChannels()
  // Lewat JSON karena objek peta lahir di realm VM yang berbeda.
  assert.deepEqual(JSON.parse(JSON.stringify(h.notaChannelMap.value)), {
    a: { proof: 'uploaded', print: 'printed' },
    b: { proof: 'none', print: 'queued' },
    c: { proof: 'none', print: 'none' },
  })
})

// Aturan harga pemilik 2026-10-07: `max_price` 0 di master barang berarti
// pricelist-nya belum diisi, bukan harga jual Rp 0. Data nyatanya
// "Aries Pintu - Abu" (min 245.000, max 0) yang dulu tampil Rp 0.
const ariesCatalog = [
  { item_name: 'Aries Pintu', variant: 'Abu', price: 0, floor_price: 245000, stock: 3, stock_alert: null },
]

test('catalog item with an unfinished pricelist sells at the floor price without a floor', async () => {
  const h = await harness({ catalog: ariesCatalog })
  await h.app.loadItems(1)
  assert.equal(h.app.items.value[0].price, 245000)
  assert.equal(h.app.items.value[0].floor_price, null)
})

test('saved cart holding Rp 0 is repriced from the catalog instead of selling at zero', async () => {
  const h = await harness({
    catalog: ariesCatalog,
    savedCart: [{
      item_name: 'Aries Pintu', variant: 'Abu', quantity: 1,
      unit_price: 0, price: 0, floor_price: 245000, stock: 3,
    }],
  })
  assert.equal(h.app.cart.value[0].unit_price, 0)
  await h.app.loadItems(1)
  assert.equal(h.app.cart.value[0].price, 245000)
  assert.equal(h.app.cart.value[0].unit_price, 245000)
  assert.equal(h.app.cart.value[0].floor_price, null)
})

// Kolom uang form refund (permintaan pemilik, 2026-10-07): tampil bertitik
// ribuan, tetapi nilai yang dikirim ke server tetap angka.
test('refund amount stores digits while the field shows thousands separators', async () => {
  const h = await historyHarness()
  h.refundAmount.value = '70000'
  assert.equal(formatRupiahInput(h.refundAmount.value), '70.000')

  // Tempel nilai berformat: titik dan "Rp" tidak boleh ikut tersimpan.
  h.updateRefundAmount({ target: { value: 'Rp 1.234.567' } })
  assert.equal(h.refundAmount.value, '1234567')
  assert.equal(formatRupiahInput(h.refundAmount.value), '1.234.567')

  h.updateRefundAmount({ target: { value: '12a3' } })
  assert.equal(h.refundAmount.value, '123')
})

test('refund submission sends the amount as a number, not the formatted text', async () => {
  const h = await historyHarness()
  h.selectedNota.value = {
    sync_id: 'nota-1', transaction_id: 'T01-000001', total_amount: 70000, payment_method: 'cash',
  }
  h.openRefundForm('refund')
  assert.equal(h.refundAmount.value, '70000')
  assert.equal(formatRupiahInput(h.refundAmount.value), '70.000')

  h.updateRefundAmount({ target: { value: '70.000' } })
  h.refundPin.value = '123456'
  h.refundReason.value = 'salah hitung'
  await h.submitRefund()

  assert.equal(h.refundCalls.length, 1)
  assert.equal(h.refundCalls[0].refund_amount, 70000)
  assert.equal(typeof h.refundCalls[0].refund_amount, 'number')
})

/*
 * Katalog offline (permintaan pemilik 2026-10-08): seluruh item disimpan di
 * perangkat dan dipakai saat server tidak menjawab. Yang diuji di sini adalah
 * keputusan jalur di `App.vue`; pencariannya sendiri adalah modul nyata
 * (`utils/localCatalogSearch.js`) yang dipanggil lewat double layanan.
 */
const cachedRows = [
  { item_name: 'Kunci Bulat', variant: 'Besar', price: 95000, floor_price: null, stock: 13, stock_alert: null },
  { item_name: 'Solasi Kunci', variant: 'Hitam', price: 20000, floor_price: null, stock: 4, stock_alert: null },
]

test('device copy serves the catalog when the server does not answer', async () => {
  const h = await harness()
  h.setServerUp(false)
  h.setCachedCatalog(cachedRows)
  await h.app.loadItems(1)
  assert.equal(h.app.items.value.length, 2)
  assert.equal(h.app.catalogSource.value, 'cache')
  assert.equal(h.app.totalItems.value, 2)
  assert.match(h.app.catalogNotice.value, /Mode offline: daftar dari katalog tersimpan/)
  /* Harga salinan tetap melewati aturan harga yang sama. */
  assert.deepEqual(
    h.app.items.value.map((item) => [item.item_name, item.price]),
    [['Kunci Bulat', 95000], ['Solasi Kunci', 20000]],
  )
})

test('offline search filters the device copy by name and variant', async () => {
  const h = await harness()
  h.setServerUp(false)
  h.setCachedCatalog(cachedRows)
  h.app.searchQuery.value = 'kunci'
  await h.app.loadItems(1)
  assert.deepEqual(h.app.items.value.map((item) => item.item_name), ['Kunci Bulat', 'Solasi Kunci'])
  h.app.searchQuery.value = 'hitam'
  await h.app.loadItems(1)
  assert.deepEqual(h.app.items.value.map((item) => item.variant), ['Hitam'])
  assert.equal(h.app.totalItems.value, 1)
})

test('a server answer wins and clears the offline notice', async () => {
  const h = await harness({ catalog: [cachedRows[0]] })
  h.setCachedCatalog(cachedRows)
  await h.app.loadItems(1)
  assert.equal(h.app.catalogSource.value, 'server')
  assert.equal(h.app.items.value.length, 1)
  assert.equal(h.app.catalogNotice.value, '')
})

test('a stale device copy is announced while the server still answers', async () => {
  const h = await harness({ catalog: [cachedRows[0]] })
  h.setCachedCatalog(cachedRows, { ageMs: 10 * 24 * 60 * 60 * 1000 })
  await h.app.loadItems(1)
  assert.equal(h.app.catalogSource.value, 'server')
  assert.equal(h.app.catalogStale.value, true)
  assert.match(h.app.catalogNotice.value, /Katalog tersimpan berumur 10 hari lalu/)
})

test('without a device copy the cashier is told the copy is missing', async () => {
  const h = await harness()
  h.setServerUp(false)
  await h.app.loadItems(1)
  assert.equal(h.app.items.value.length, 0)
  assert.equal(h.app.catalogUnavailable.value, true)
  assert.match(h.app.catalogNotice.value, /katalog perangkat belum tersimpan/)
})

test('a copy that vanishes mid-session stops being announced as available', async () => {
  const h = await harness()
  h.setServerUp(false)
  h.setCachedCatalog(cachedRows)
  await h.app.loadItems(1)
  assert.equal(h.app.catalogSource.value, 'cache')

  /*
   * Penyimpanan perangkat bisa kosong saat aplikasi masih terbuka (pembersihan
   * browser, atau hapus data). Banner yang tetap berbunyi "daftar dari katalog
   * tersimpan" akan membuat kasir mengira harganya masih bisa dipercaya.
   * Ditemukan lewat uji browser 2026-10-08.
   */
  h.setCachedCatalog([])
  await h.app.loadItems(1)

  assert.equal(h.app.catalogUnavailable.value, true)
  assert.match(h.app.catalogNotice.value, /katalog perangkat belum tersimpan/)
  assert.doesNotMatch(h.app.catalogNotice.value, /daftar dari katalog tersimpan/)
})

/*
 * Antrean multi-nota (opsi A slice 1): server mati bukan alasan berhenti
 * jualan. Tiga nota beruntun masuk antrean dengan identitas unik, keranjang
 * bersih setiap selesai, dan jatah nomor terpakai satu per nota.
 */
test('three offline sales queue while the server is down', async () => {
  const h = await harness()
  h.setServerUp(false)
  const refill = () => {
    h.app.cart.value = [{
      item_name: 'Item', variant: 'Default', quantity: 1, unit_price: 100, price: 100,
    }]
  }
  for (let sale = 0; sale < 3; sale += 1) {
    refill()
    await h.app.completeTransaction(checkout)
  }
  assert.equal(h.rows().length, 3)
  assert.deepEqual(h.rows().map((row) => row.sync_status), ['unknown', 'unknown', 'unknown'])
  assert.equal(new Set(h.rows().map((row) => row.sync_id)).size, 3)
  assert.equal(h.app.cart.value.length, 0)
  assert.equal(h.app.checkoutStatus.value, 'idle')
  assert.equal(h.app.isCartLocked(), false)
  assert.equal(h.app.queuedNotices.value.length, 3)
  assert.equal(h.sequenceConsumed(), 3)
  assert.equal(h.printed.length, 3)
})

test('queue resends one nota at a time and clears each settled row', async () => {
  const h = await harness()
  h.setServerUp(false)
  for (let sale = 0; sale < 2; sale += 1) {
    h.app.cart.value = [{
      item_name: 'Item', variant: 'Default', quantity: 1, unit_price: 100, price: 100,
    }]
    await h.app.completeTransaction(checkout)
  }
  h.setServerUp(true)
  h.respond(success)
  await h.app.resendQueuedTransaction(h.rows()[0].sync_id)
  assert.equal(h.rows()[0].sync_status, 'synced')
  assert.equal(h.rows()[1].sync_status, 'unknown')
  assert.equal(h.app.queuedNotices.value.length, 1)
})

test('exhausted number range holds the sale with the cart intact', async () => {
  const h = await harness()
  h.setSequenceRange({ next_sequence: 1001, end_sequence: 1000 })
  await h.app.completeTransaction(checkout)
  assert.equal(h.rows().length, 0)
  assert.equal(h.sequenceConsumed(), 0)
  assert.equal(h.app.checkoutStatus.value, 'error')
  assert.match(h.app.checkoutMessage.value, /Jatah nomor nota habis/)
  assert.equal(h.app.cart.value.length, 1)
})

test('CartList claims click before async cashier lookup and retry emits no replacement PIN', async () => {
  const h = await harness()
  let resolve
  h.lookup(() => new Promise((done) => { resolve = done }))
  h.cartUi.cashierPin.value = checkout.pin
  h.cartUi.paymentMethod.value = 'cash'
  h.cartUi.deliveryMethod.value = 'pickup'
  h.cartUi.cashReceived.value = '100'
  h.cartUi.customerName.value = 'Budi Santoso'
  const first = h.cartUi.completeTransaction()
  await h.cartUi.completeTransaction()
  assert.equal(h.cartUi.completing.value, true)
  resolve('Original cashier')
  await first
  assert.equal(h.emitted.length, 1)
  h.props.checkoutStatus = 'retryable'
  h.cartUi.cashierPin.value = '654321'
  h.cartUi.retryTransaction()
  assert.deepEqual(Object.keys(h.emitted[1][1]), [])
})

/*
 * Data pelanggan (keputusan pemilik 2026-10-14, CUSTOMER_DESIGN.md): nama
 * wajib diisi, No. HP opsional berpola. Langkah pembayaran tidak lengkap
 * tanpa nama, dan emit tidak boleh terjadi.
 */
test('CartList meminta nama pelanggan dan memvalidasi No. HP', async () => {
  const h = await harness()
  h.cartUi.paymentMethod.value = 'cash'
  h.cartUi.deliveryMethod.value = 'pickup'
  h.cartUi.cashReceived.value = '100'
  h.cartUi.customerName.value = ''
  h.cartUi.customerPhone.value = ''
  assert.equal(h.cartUi.isPaymentStepComplete.value, false)
  h.cartUi.cashierPin.value = checkout.pin
  await h.cartUi.completeTransaction()
  assert.equal(h.emitted.length, 0)

  h.cartUi.customerName.value = 'Budi Santoso'
  h.cartUi.customerPhone.value = '08AB12'
  assert.equal(h.cartUi.isPaymentStepComplete.value, false)
  await h.cartUi.completeTransaction()
  assert.equal(h.emitted.length, 0)

  h.cartUi.customerPhone.value = '0812-3456-7890'
  assert.equal(h.cartUi.isPaymentStepComplete.value, true)
})

test('nota tersimpan membawa nama + No. HP ke server dan ke kertas', async () => {
  const h = await harness()
  h.respond(success)
  await h.app.completeTransaction(checkout)
  assert.equal(h.requests[0].transaction.cust_name, 'Budi Santoso')
  assert.equal(h.requests[0].transaction.phone_no, '081234567890')
  assert.equal(h.printed[0].cust_name, 'Budi Santoso')
  assert.equal(h.printed[0].phone_no, '081234567890')
})

/*
 * Batch PIN-per-nota (slice 2): satu kiriman untuk seluruh antrean, tiap nota
 * memakai PIN aslinya; hasil campuran dipetakan ke status lokal tanpa
 * menyentuh keranjang draft.
 */
async function sealThreeOffline(h) {
  h.setServerUp(false)
  for (let index = 0; index < 3; index += 1) {
    h.app.cart.value = [{ item_name: 'Item', variant: 'Default', quantity: 1, unit_price: 100, price: 100 }]
    h.local.set('posCart', JSON.stringify(h.app.cart.value))
    await h.app.completeTransaction(checkout)
  }
  assert.equal(h.rows().length, 3)
  assert.equal(h.app.queuedNotices.value.length, 3)
  h.setServerUp(true)
}

const batchReply = (request) => reply(200, {
  results: request.transactions.map((member, index) => ({
    sync_id: member.sync_id,
    transaction_id: member.transaction_id,
    status: ['new', 'rejected', 'conflict'][index],
    detail: index === 0 ? null : (index === 1 ? 'Item is out of stock.' : 'Transaction identity conflict.'),
  })),
})

test('batch maps mixed per-nota results and leaves the draft cart intact', async () => {
  const h = await harness()
  await sealThreeOffline(h)
  /* Keranjang draft jualan baru: batch tidak boleh menyentuhnya. */
  h.app.cart.value = [{ item_name: 'New sale', variant: 'Default', quantity: 2, unit_price: 50, price: 50 }]
  h.respond(batchReply)
  await h.app.resendQueuedBatch()

  assert.equal(h.app.batchSyncState.value, 'idle')
  /* Satu request, tiga anggota, tiap anggota membawa PIN aslinya. */
  assert.equal(h.requests.length, 1)
  assert.equal(h.requests[0].transactions.length, 3)
  assert.deepEqual(
    h.requests[0].transactions.map((member) => member.pin),
    ['123456', '123456', '123456'],
  )
  assert.ok(!('pin' in h.requests[0]))
  /* new → synced (PIN dibersihkan); nota final yang ditolak → review, bukan
   * rejected pasti; konflik → conflict. Antrean kosong, keranjang utuh. */
  assert.deepEqual(
    h.rows().map((row) => row.sync_status),
    ['synced', 'review', 'conflict'],
  )
  assert.equal(h.rows()[0].original_pin, null)
  assert.match(h.rows()[1].rejection_detail, /out of stock/)
  assert.equal(h.app.queuedNotices.value.length, 0)
  assert.equal(h.app.cart.value.length, 1)
  assert.equal(h.app.cart.value[0].item_name, 'New sale')
  assert.match(h.app.batchSyncMessage.value, /1 nota terkirim, 1 ditolak, 1 konflik/)
})

test('batch resends nothing once no nota is sendable', async () => {
  const h = await harness()
  await sealThreeOffline(h)
  h.respond(batchReply)
  await h.app.resendQueuedBatch()
  assert.equal(h.requests.length, 1)
  assert.match(h.app.batchSyncMessage.value, /1 nota terkirim/)
  /* Tidak ada yang draft/pending/unknown lagi: batch kedua tidak fetch. */
  await h.app.resendQueuedBatch()
  assert.equal(h.requests.length, 1)
  assert.deepEqual(
    h.rows().map((row) => row.sync_status),
    ['synced', 'review', 'conflict'],
  )
})

/*
 * Gerbang kirim ulang ada di `App.vue`, bukan di tombol: selagi checkout
 * terkunci (butuh manusia) atau cooldown PIN berjalan, batch tidak boleh menyapa
 * server — kiriman yang salah waktu bisa menggandakan nota.
 */
test('batch kirim ulang ditahan selagi checkout terkunci atau cooldown berjalan', async () => {
  const h = await harness()
  await sealThreeOffline(h)

  h.app.checkoutStatus.value = 'review'
  await h.app.resendQueuedBatch()
  assert.equal(h.requests.length, 0)
  assert.equal(h.app.batchSyncState.value, 'idle')

  h.app.checkoutStatus.value = 'idle'
  h.app.retryAfterSeconds.value = 30
  await h.app.resendQueuedBatch()
  assert.equal(h.requests.length, 0)

  /* Antrean tetap utuh: tidak ada nota yang ditandai `syncing`. */
  assert.deepEqual(
    h.rows().map((row) => row.sync_status),
    ['unknown', 'unknown', 'unknown'],
  )
})

/*
 * Tablet mendatar: Riwayat Nota sebaris pil status (permintaan pemilik,
 * 2026-10-12). Di 1007x601 kolom daftar 599px sedangkan sebaris penuh butuh
 * ~616px, jadi ambang `min-width: 901px and max-height: 720px` merapatkan
 * jarak SAJA (bukan mengecilkan tombol/huruf): kolom header 12px→8px,
 * antar-tombol 10px→8px, padding tombol 18px→14px, jarak dalam 8px/6px→6px/4px.
 * `flex-wrap` tidak dimatikan — kalau ada keadaan tak terduga, tombol turun
 * baris seperti dulu, bukan terpotong.
 */
test('riwayat nota sebaris di tablet mendatar dengan merapatkan jarak saja', async () => {
  const app = await readFile(new URL('../src/App.vue', import.meta.url), 'utf8')
  const block = app.match(
    /@media \(min-width: 901px\) and \(max-height: 720px\) \{[\s\S]*?\.history-statuses \{\s*gap: 4px;\s*\}\s*\}/,
  )[0]
  assert.match(block, /\.page-header \{\s*column-gap: 8px;\s*\}/)
  assert.match(block, /\.header-actions \{\s*gap: 8px;\s*\}/)
  assert.match(block, /\.history-button \{\s*gap: 6px;\s*padding: 0 14px;\s*\}/)
  /* Tidak ada yang mengecilkan tombol atau huruf di ambang ini. */
  assert.doesNotMatch(block, /font-size|min-height: 44px|height: 44px/)
})

/*
 * Harga di bawah floor (keputusan pemilik 2026-10-17): boleh dengan PIN
 * penyetuju, atau dikirim untuk ACC pemilik; tanpa keduanya tetap ditahan.
 */
const belowFloorCart = [{ item_name: 'Item', variant: 'Default', quantity: 1, unit_price: 80, price: 100, floor_price: 90 }]

test('harga di bawah floor tanpa persetujuan tidak dikirim', async () => {
  const h = await harness({ savedCart: belowFloorCart })
  await h.app.completeTransaction(checkout)
  assert.equal(h.requests.length, 0)
  assert.equal(h.app.checkoutStatus.value, 'error')
  assert.match(h.app.checkoutMessage.value, /PIN penyetuju/)
})

test('PIN penyetuju ikut dikirim bersama nota dan tidak tersimpan setelah sukses', async () => {
  const h = await harness({ savedCart: belowFloorCart })
  h.respond((request) => reply(201, {
    sync_id: request.transaction.sync_id, transaction_id: request.transaction.transaction_id, status: 'new',
  }))
  await h.app.completeTransaction({ ...checkout, approverPin: '654321' })
  assert.equal(h.requests.length, 1)
  assert.equal(h.requests[0].approver_pin, '654321')
  assert.equal(h.requests[0].final_lokal, true)
  assert.equal(h.rows()[0].sync_status, 'synced')
  assert.equal(h.rows()[0].approver_pin, null)
})

test('tanpa penyetuju: nota dikirim untuk ACC dan kasir diberi tahu', async () => {
  const h = await harness({ savedCart: belowFloorCart })
  h.respond((request) => reply(201, {
    sync_id: request.transaction.sync_id, transaction_id: request.transaction.transaction_id, status: 'new',
    ditahan: true, detail: 'Nota diterima server dan menunggu ACC pemilik: harga di bawah floor',
  }))
  await h.app.completeTransaction({ ...checkout, mintaAcc: true })
  assert.equal(h.requests[0].minta_acc, true)
  assert.equal(h.requests[0].approver_pin, undefined)
  assert.equal(h.app.checkoutStatus.value, 'success')
  assert.match(h.app.checkoutMessage.value, /menunggu ACC pemilik/)
})
