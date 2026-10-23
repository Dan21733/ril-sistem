import { API_BASE_URL } from '../api/deviceApi'
import {
  countItems,
  getCatalogMeta,
  getAllItems,
  replaceCatalog,
} from '../storage/itemRepository'
import { mapApiItem } from '../../utils/catalogItem'
import { searchCatalog } from '../../utils/localCatalogSearch'

/*
 * Salinan katalog di perangkat (keputusan pemilik 2026-10-08).
 *
 * Tujuannya satu: kasir bisa **memulai** penjualan saat server tidak terjangkau
 * — mencari barang, melihat harga pricelist dan stok terakhir, lalu menutup
 * nota dengan PIN. Yang disimpan adalah hasil halaman katalog apa adanya,
 * dipetakan lewat `mapApiItem()` yang sama dengan jalur online, supaya harga
 * dan stok tidak bisa berbeda antara dua jalur itu.
 *
 * Yang tidak disimpan dan tidak dikarang: alias (`item_alias` tidak dikirim
 * `/api/items/`), modal/margin, dan riwayat. Pencarian lokal karena itu hanya
 * substring nama+varian — lihat `utils/localCatalogSearch.js`.
 *
 * Seluruh katalog diambil berurutan sampai `next` null. Server mengirim 30
 * baris per halaman; `MAX_PAGES` hanya pagar supaya satu bug server tidak
 * membuat tablet mengunduh tanpa henti.
 */

/*
 * Umur salinan yang masih dianggap layak dipakai tanpa peringatan: 1 hari
 * (permintaan pemilik 2026-10-12; dulu 7 hari). Salinan 3 hari terbukti basi
 * tanpa terasa karena search selalu ke server saat online — ambang pendek
 * membuat timer 15 menit, event online, dan buka aplikasi benar-benar
 * menyegarkan salinan tiap hari, bukan tiap minggu.
 */
export const CATALOG_STALE_AFTER_MS = 24 * 60 * 60 * 1000

/* Pagar pengaman loop: 200 halaman @ 200 baris, jauh di atas katalog (~2900 varian, ~15 halaman). */
const MAX_PAGES = 200

let inFlightSync = null

/*
 * Halaman besar: 2924 baris selesai dalam ~15 request (±2,3 dtk @ 153 ms),
 * bukan 98 request (34 dtk). Server cap di 200 via max_page_size; tanpa
 * param default tetap 30 (kontrak lama utuh). Bentuk respons DRF identik
 * (results/next), jadi loop while(next) tidak berubah.
 */
/*
 * `headers` = header token tablet dari pemanggil: sejak 17 Okt 2026
 * `/api/items/` hanya menjawab tablet terdaftar.
 */
async function fetchCatalogPage(page, headers = {}) {
  const response = await fetch(`${API_BASE_URL}/api/items/?page=${page}&page_size=200`, {
    headers: { Accept: 'application/json', ...headers },
  })

  if (!response.ok) {
    throw new Error(`HTTP error: ${response.status}`)
  }

  return response.json()
}

async function runSync({ now = Date.now(), headers = {} } = {}) {
  const rows = []
  let page = 1
  let more = true

  while (more && page <= MAX_PAGES) {
    const data = await fetchCatalogPage(page, headers)

    for (const item of data.results || []) {
      rows.push(mapApiItem(item))
    }

    more = Boolean(data.next)
    page += 1
  }

  /*
   * Katalog kosong hampir pasti berarti jawaban yang tidak seperti biasanya,
   * bukan toko tanpa barang. Salinan lama dipertahankan daripada dihapus.
   */
  if (rows.length === 0) {
    throw new Error('Katalog server kosong; salinan lama dipertahankan.')
  }

  const syncedAt = new Date(now).toISOString()

  await replaceCatalog(rows, { syncedAt })

  return { count: rows.length, syncedAt }
}

/* Satu sinkron dalam satu waktu; pemanggil berikutnya ikut menunggu yang sama. */
export async function syncCatalog(options = {}) {
  if (inFlightSync) {
    return inFlightSync
  }

  inFlightSync = runSync(options).finally(() => {
    inFlightSync = null
  })

  return inFlightSync
}

export async function getCatalogState({ now = Date.now() } = {}) {
  try {
    const [meta, count] = await Promise.all([getCatalogMeta(), countItems()])

    if (!meta?.synced_at || count === 0) {
      return { count, syncedAt: null, ageMs: null, stale: true, fresh: false }
    }

    const syncedTime = Date.parse(meta.synced_at)
    const ageMs = Number.isFinite(syncedTime) ? Math.max(0, now - syncedTime) : null

    return {
      count,
      syncedAt: meta.synced_at,
      ageMs,
      stale: ageMs === null || ageMs > CATALOG_STALE_AFTER_MS,
      fresh: ageMs !== null && ageMs <= CATALOG_STALE_AFTER_MS,
    }
  } catch {
    /* Penyimpanan bermasalah bukan alasan menahan penjualan. */
    return { count: 0, syncedAt: null, ageMs: null, stale: true, fresh: false, storageUnavailable: true }
  }
}

/*
 * Sinkron kalau salinannya belum ada atau sudah basi. Kegagalannya tidak
 * pernah dilaporkan sebagai kegagalan penjualan: jalur online tetap jalan, dan
 * salinan lama tetap dipakai.
 */
export async function ensureCatalogFresh(options = {}) {
  const state = await getCatalogState(options)

  if (state.fresh) {
    return { synced: false, ...state }
  }

  try {
    const result = await syncCatalog(options)

    return { synced: true, ...result }
  } catch (error) {
    return { synced: false, error, ...state }
  }
}

/* Pencarian pada salinan di perangkat; bentuknya sama dengan jawaban server. */
export async function searchCachedCatalog(query, { page = 1, perPage } = {}) {
  return searchCatalog(await getAllItems(), query, { page, perPage })
}

export default {
  CATALOG_STALE_AFTER_MS,
  ensureCatalogFresh,
  getCatalogState,
  searchCachedCatalog,
  syncCatalog,
}
