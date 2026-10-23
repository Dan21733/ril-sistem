import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

/*
 * `replaceCatalog()` — ganti-utuh salinan katalog di perangkat
 * (OFFLINE_SYNC_DESIGN.md §3.3), diuji terhadap **modul aslinya** dengan `db`
 * palsu yang menirukan bentuk API Dexie 4 apa adanya.
 *
 * Kenapa dobelnya dibuat sedetail itu: versi pertama fungsi ini memanggil
 * `db.items.primaryKeys()`, padahal di Dexie 4 metode itu milik **Collection**.
 * Kesalahannya tidak terlihat di tes jalur layanan (di sana repositori di-stub)
 * dan tidak terlihat di layar: 2.917 baris terunduh seluruhnya, lalu seluruh
 * transaksi gagal dan salinan offline tidak pernah ada. Karena itu tabel palsu
 * di bawah **tidak** punya `primaryKeys()` — sama seperti Dexie sungguhan —
 * sehingga panggilan yang salah bentuk langsung gagal di sini, bukan di tablet.
 */

async function loadRepository() {
  const rows = new Map()
  const meta = new Map()
  const transactions = []

  const keyOf = (row) => JSON.stringify([row.item_name, row.variant])

  const items = {
    __name: 'items',
    toArray: async () => [...rows.values()],
    count: async () => rows.size,
    get: async ([itemName, variant]) => rows.get(JSON.stringify([itemName, variant])),
    put: async (row) => {
      rows.set(keyOf(row), { ...row })
      return [row.item_name, row.variant]
    },
    bulkPut: async (list) => {
      for (const row of list) {
        rows.set(keyOf(row), { ...row })
      }
    },
    bulkDelete: async (keys) => {
      for (const key of keys) {
        rows.delete(JSON.stringify(key))
      }
    },
    clear: async () => rows.clear(),
    toCollection: () => ({
      primaryKeys: async () => [...rows.keys()].map((key) => JSON.parse(key)),
    }),
    /* SENGAJA tanpa `primaryKeys` — di Dexie 4 itu metode Collection. */
  }

  const catalogMeta = {
    __name: 'catalog_meta',
    get: async (key) => meta.get(key),
    put: async (row) => {
      meta.set(row.key, { ...row })
    },
  }

  const db = {
    items,
    catalog_meta: catalogMeta,
    transaction: async (mode, ...rest) => {
      const run = rest.pop()
      transactions.push({ mode, stores: rest.map((table) => table.__name) })
      return run()
    },
  }

  const context = vm.createContext({ console })
  const source = await readFile(new URL('../src/services/storage/itemRepository.js', import.meta.url), 'utf8')
  const module = new vm.SourceTextModule(source, { context })
  await module.link((specifier) => {
    if (specifier !== './db') {
      throw new Error(`unexpected import: ${specifier}`)
    }

    return new vm.SyntheticModule(['db', 'default'], function () {
      this.setExport('db', db)
      this.setExport('default', db)
    }, { context })
  })
  await module.evaluate()

  return { repo: module.namespace, rows, meta, transactions, db }
}

const item = (itemName, variant, extra = {}) => ({ item_name: itemName, variant, ...extra })

test('replaceCatalog replaces the copy wholly and stamps it in the same transaction', async () => {
  const h = await loadRepository()

  await h.repo.saveItem(item('Hapus Nanti', 'N/A', { price: 1000 }))
  await h.repo.replaceCatalog(
    [item('Kunci Bulat', 'Besar', { price: 95000 }), item('Solasi', 'Hitam', { price: 20000 })],
    { syncedAt: '2026-10-08T03:00:00.000Z' },
  )

  const names = (await h.repo.getAllItems()).map((row) => row.item_name).sort()
  assert.deepEqual(names, ['Kunci Bulat', 'Solasi'])

  const meta = await h.repo.getCatalogMeta()
  assert.deepEqual(meta, { key: 'catalog', synced_at: '2026-10-08T03:00:00.000Z', count: 2 })

  /* Item lama dihapus dan item baru ditulis dalam satu transaksi yang sama
   * dengan cap waktunya — bukan dua transaksi terpisah. */
  assert.deepEqual(h.transactions.at(-1), { mode: 'rw', stores: ['items', 'catalog_meta'] })
})

test('a copy without a timestamp is reported as never synced', async () => {
  const h = await loadRepository()

  assert.equal(await h.repo.getCatalogMeta(), undefined)

  await h.repo.replaceCatalog([item('Solasi', 'Hitam', { price: 20000 })], { syncedAt: null })

  const meta = await h.repo.getCatalogMeta()
  assert.equal(meta.synced_at, null)
  assert.equal(await h.repo.countItems(), 1)
})

test('an empty replacement clears the copy instead of leaving stale rows', async () => {
  const h = await loadRepository()

  await h.repo.bulkSaveItems([item('Solasi', 'Hitam', { price: 20000 })])

  await h.repo.replaceCatalog([], { syncedAt: '2026-10-08T03:00:00.000Z' })

  assert.equal(await h.repo.countItems(), 0)
  assert.equal((await h.repo.getCatalogMeta()).count, 0)
})

test('the same item name with two variants stays two rows', async () => {
  const h = await loadRepository()

  await h.repo.replaceCatalog(
    [item('Aries Pintu', 'Abu', { price: 245000 }), item('Aries Pintu', 'Putih', { price: 250000 })],
    { syncedAt: '2026-10-08T03:00:00.000Z' },
  )

  assert.equal(await h.repo.countItems(), 2)
  assert.equal((await h.repo.getItem('Aries Pintu', 'Putih')).price, 250000)
  assert.equal((await h.repo.getItem('Aries Pintu', 'Hitam')), undefined)
})
