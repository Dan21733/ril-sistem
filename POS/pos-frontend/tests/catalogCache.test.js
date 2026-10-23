import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

/*
 * Sinking katalog offline (OFFLINE_SYNC_DESIGN.md §3.3). Yang diuji perilakunya
 * dengan dobel: seluruh halaman diambil berurutan, salinan ditulis sekali
 * sebagai ganti-utuh bersama cap waktunya, jawaban kosong/gagal tidak menghapus
 * salinan lama, dan dua pemanggil tidak mengunduh katalog dua kali.
 */

const HOUR = 60 * 60 * 1000
const DAY = 24 * HOUR

async function loadRealModule(path, context) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const module = new vm.SourceTextModule(source, { context })
  await module.link(() => { throw new Error('unexpected import') })
  await module.evaluate()
  return module
}

async function harness({ pages = [[], null], failOnPage = null } = {}) {
  const fetched = []
  const fetchedUrls = []
  const writes = []
  const itemRows = []
  const metaRows = []
  const context = vm.createContext({
    console,
    setTimeout,
    clearTimeout,
    fetch: async (url) => {
      const page = Number(String(url).match(/page=(\d+)/)?.[1] || 1)
      fetched.push(page)
      fetchedUrls.push(String(url))
      if (failOnPage === page) {
        throw new Error('network down')
      }
      const [results, next] = pages[page] || [[], null]
      return {
        ok: true,
        status: 200,
        json: async () => ({ results, count: results.length, next }),
      }
    },
  })

  const itemRepository = {
    countItems: async () => itemRows.length,
    getCatalogMeta: async () => metaRows.at(-1),
    getAllItems: async () => itemRows,
    replaceCatalog: async (rows, { syncedAt }) => {
      writes.push({ count: rows.length, syncedAt, first: rows[0] })
      itemRows.splice(0, itemRows.length, ...rows)
      metaRows.push({ key: 'catalog', synced_at: syncedAt, count: rows.length })
    },
  }

  const priceRange = await loadRealModule('../src/utils/priceRange.js', context)
  const catalogItemSource = await readFile(new URL('../src/utils/catalogItem.js', import.meta.url), 'utf8')
  const catalogItem = new vm.SourceTextModule(catalogItemSource, { context })
  await catalogItem.link((specifier) => (specifier === './priceRange'
    ? priceRange
    : new vm.SyntheticModule([], function () {}, { context })))
  await catalogItem.evaluate()

  const modules = new Map()
  modules.set('../api/deviceApi', new vm.SyntheticModule(['API_BASE_URL'], function () {
    this.setExport('API_BASE_URL', '')
  }, { context }))
  modules.set('../storage/itemRepository', new vm.SyntheticModule(
    Object.keys(itemRepository), function () {
      for (const [name, value] of Object.entries(itemRepository)) { this.setExport(name, value) }
    }, { context },
  ))
  modules.set('../../utils/catalogItem', catalogItem)
  modules.set('../../utils/localCatalogSearch', await loadRealModule('../src/utils/localCatalogSearch.js', context))

  const source = await readFile(new URL('../src/services/catalog/catalogCache.js', import.meta.url), 'utf8')
  const module = new vm.SourceTextModule(source, { context })
  await module.link((specifier) => modules.get(specifier) || new vm.SyntheticModule([], function () {}, { context }))
  await module.evaluate()

  return { cache: module.namespace, fetched, fetchedUrls, writes, itemRows }
}

const twoPages = {
  1: [[{ item_name: 'Kunci Bulat', variant: 'Besar', price: 95000, floor_price: 90000, stock: 13, stock_alert: null }], 2],
  2: [[{ item_name: 'Solasi', variant: 'Hitam', price: 20000, floor_price: null, stock: 4, stock_alert: 2 }], null],
}

test('every page is fetched and the copy is replaced once with its timestamp', async () => {
  const h = await harness({ pages: twoPages })
  const now = Date.UTC(2026, 9, 8, 3, 0, 0)
  const result = await h.cache.syncCatalog({ now })

  assert.deepEqual(h.fetched, [1, 2])
  assert.equal(h.writes.length, 1)
  assert.equal(h.writes[0].count, 2)
  assert.equal(h.writes[0].syncedAt, new Date(now).toISOString())
  assert.equal(result.count, 2)
  /* Baris yang disimpan sudah lewat aturan harga/angka yang sama. */
  assert.deepEqual(JSON.parse(JSON.stringify(h.writes[0].first)), {
    item_name: 'Kunci Bulat',
    variant: 'Besar',
    price: 95000,
    floor_price: 90000,
    stock: 13,
    stock_alert: null,
  })
})

test('an empty catalog never replaces the existing copy', async () => {
  const h = await harness({ pages: { 1: [[], null] } })

  await assert.rejects(() => h.cache.syncCatalog(), /kosong/)
  assert.equal(h.writes.length, 0)
})

test('a failing page keeps the previous copy untouched', async () => {
  const h = await harness({ pages: twoPages, failOnPage: 2 })

  await assert.rejects(() => h.cache.syncCatalog(), /network down/)
  assert.deepEqual(h.fetched, [1, 2])
  assert.equal(h.writes.length, 0)
})

test('concurrent callers share one download', async () => {
  const h = await harness({ pages: twoPages })

  await Promise.all([h.cache.syncCatalog(), h.cache.syncCatalog()])

  assert.deepEqual(h.fetched, [1, 2])
  assert.equal(h.writes.length, 1)
})

test('every page is requested with the large page size for a fast sync', async () => {
  const h = await harness({ pages: twoPages })

  await h.cache.syncCatalog()

  assert.equal(h.fetchedUrls.length, 2)
  for (const url of h.fetchedUrls) {
    assert.match(url, /page_size=200/)
  }
})

test('state is fresh only inside the staleness window', async () => {
  const h = await harness({ pages: twoPages })
  const now = Date.UTC(2026, 9, 8, 3, 0, 0)

  await h.cache.syncCatalog({ now })

  const fresh = await h.cache.getCatalogState({ now: now + 20 * HOUR })
  assert.equal(fresh.fresh, true)
  assert.equal(fresh.stale, false)
  assert.equal(fresh.count, 2)

  const stale = await h.cache.getCatalogState({ now: now + 2 * DAY })
  assert.equal(stale.fresh, false)
  assert.equal(stale.stale, true)
  assert.equal(stale.ageMs, 2 * DAY)
})

test('an empty copy counts as stale so the first sync runs by itself', async () => {
  const h = await harness({ pages: twoPages })

  const state = await h.cache.getCatalogState()

  assert.equal(state.count, 0)
  assert.equal(state.syncedAt, null)
  assert.equal(state.stale, true)

  const result = await h.cache.ensureCatalogFresh()

  assert.equal(result.synced, true)
  assert.equal(result.count, 2)

  /* Sudah segar: sinkron berikutnya tidak mengunduh ulang. */
  const again = await h.cache.ensureCatalogFresh()

  assert.equal(again.synced, false)
  assert.deepEqual(h.fetched, [1, 2])
})

test('a failed background sync is swallowed, not reported as a sale failure', async () => {
  const h = await harness({ pages: twoPages, failOnPage: 1 })

  const result = await h.cache.ensureCatalogFresh()

  assert.equal(result.synced, false)
  assert.ok(result.error)
  assert.equal(h.writes.length, 0)
})
