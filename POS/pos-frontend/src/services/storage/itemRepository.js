import db from './db'

export async function getItem(itemName, variant) {
  return db.items.get([itemName, variant])
}

export async function saveItem(item) {
  return db.items.put({ ...item })
}

export async function bulkSaveItems(items) {
  return db.items.bulkPut(items.map((item) => ({ ...item })))
}

export async function getAllItems() {
  return db.items.toArray()
}

export async function clearItems() {
  return db.items.clear()
}

/*
 * Ganti seluruh salinan katalog dalam satu transaksi (OFFLINE_SYNC_DESIGN.md
 * §3.3). "Ganti utuh", bukan tambal: item yang sudah dihapus dari master ikut
 * hilang dari tablet, sama seperti direktori kasir (`replaceWorkers`).
 *
 * Item dan cap waktunya ditulis bersama: kalau tablet mati di tengah jalan,
 * yang tersisa adalah keadaan sebelum sinkron berikutnya — bukan salinan
 * setengah jadi yang mengaku baru.
 */
export async function replaceCatalog(rows, { syncedAt } = {}) {
  const items = rows.map((item) => ({ ...item }))

  return db.transaction('rw', db.items, db.catalog_meta, async () => {
    /*
     * `primaryKeys()` ada di **Collection**, bukan di Table (Dexie 4):
     * `db.items.primaryKeys()` melempar TypeError dan membuat seluruh sinkron
     * gagal tanpa jejak di layar. Kesalahannya sudah pernah terjadi di sini
     * (ditemukan 2026-10-08 saat uji browser: katalog 2.917 baris terunduh
     * seluruhnya lalu dibuang), karena itu bentuk pemanggilannya dikunci oleh
     * `tests/itemRepository.test.js` yang menirukan API Dexie apa adanya.
     */
    const keys = await db.items.toCollection().primaryKeys()

    const keep = new Set(items.map((item) => JSON.stringify([item.item_name, item.variant])))

    const stale = keys.filter((key) => !keep.has(JSON.stringify(key)))

    if (stale.length > 0) {
      await db.items.bulkDelete(stale)
    }

    if (items.length > 0) {
      await db.items.bulkPut(items)
    }

    await db.catalog_meta.put({ key: 'catalog', synced_at: syncedAt, count: items.length })
  })
}

/* Cap waktu + jumlah baris sinkron terakhir; `undefined` kalau belum pernah. */
export async function getCatalogMeta() {
  return db.catalog_meta.get('catalog')
}

export async function countItems() {
  return db.items.count()
}

export default {
  getItem,
  saveItem,
  bulkSaveItems,
  getAllItems,
  clearItems,
  countItems,
  replaceCatalog,
  getCatalogMeta,
}
