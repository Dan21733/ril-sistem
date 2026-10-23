/*
 * Pencarian katalog dari salinan di perangkat (permintaan pemilik 2026-10-08:
 * seluruh item disimpan, disegarkan berkala).
 *
 * Ini **bukan** pengganti peringkat pencarian server: server memakai trigram
 * plus alias (`item_alias`), sedangkan salinan di perangkat tidak memuat alias
 * sama sekali — `/api/items/` tidak mengirimkannya. Karena itu pencarian lokal
 * hanya mencocokkan nama dan varian sebagai substring, lalu mengurutkannya
 * menurut nama+varian supaya hasilnya bisa diprediksi kasir. Tambahan alias di
 * perangkat menuntut perubahan kontrak item, bukan sesuatu yang boleh
 * dikarang di sisi kasir.
 *
 * Isi modul ini murni: tanpa Dexie, tanpa jaringan, supaya bisa diuji sebagai
 * fungsi dan dipakai jalur offline maupun jalur katalog tersimpan.
 */

/* Sama dengan pagination server supaya perilaku "Muat lebih banyak" konsisten. */
export const LOCAL_PAGE_SIZE = 30

export function normalizeCatalogText(value) {
  return String(value ?? '')
    .trim()
    .toLowerCase()
}

/*
 * Urutan alami (17 Okt 2026), sama dengan server dan master: huruf besar-kecil
 * dianggap sama, spasi diabaikan, dan angka dibandingkan sebagai angka
 * ("2 MM" sebelum "10 MM"). Tetap tanpa `localeCompare`: urutannya harus sama
 * persis di browser kasir dan di tes, tidak bergantung tabel ICU.
 */
export function naturalKey(value) {
  const compact = String(value ?? '').toLowerCase().replace(/\s+/g, '')
  return compact.split(/(\d+)/).map((part, index) => (index % 2 === 1 ? Number(part) : part))
}

function compareNatural(left, right) {
  const a = naturalKey(left)
  const b = naturalKey(right)
  const length = Math.min(a.length, b.length)

  for (let i = 0; i < length; i += 1) {
    if (a[i] !== b[i]) {
      return a[i] < b[i] ? -1 : 1
    }
  }

  return a.length - b.length
}

export function compareCatalogRows(left, right) {
  return compareNatural(left.item_name, right.item_name)
    || compareNatural(left.variant, right.variant)
    || (left.item_name < right.item_name ? -1 : left.item_name > right.item_name ? 1 : 0)
    || (left.variant < right.variant ? -1 : left.variant > right.variant ? 1 : 0)
}

/*
 * Pencarian offline per kata (QA 18 Okt 2026): setiap kata yang diketik harus
 * ada di gabungan nama + varian + alias, urutan kata bebas. Dulu satu frasa
 * utuh dicocokkan ke nama ATAU varian saja, jadi "AM 152 151 black" (nama +
 * varian) dan alias seperti "martil" tidak menemukan apa pun saat offline,
 * padahal online ketemu.
 *
 * Nilai kembali: 0 = cocok lewat nama/varian, 1 = baru cocok kalau alias ikut
 * dihitung, -1 = tidak cocok. `matchCatalogQuery` tetap boolean.
 */
function queryWords(query) {
  return normalizeCatalogText(query).split(/\s+/).filter(Boolean)
}

export function matchRank(item, words) {
  if (!words.length) {
    return 0
  }

  const nameVariant = `${normalizeCatalogText(item?.item_name)} ${normalizeCatalogText(item?.variant)}`

  if (words.every((word) => nameVariant.includes(word))) {
    return 0
  }

  const withAlias = `${nameVariant} ${normalizeCatalogText(item?.alias)}`

  return words.every((word) => withAlias.includes(word)) ? 1 : -1
}

export function matchCatalogQuery(item, query) {
  return matchRank(item, queryWords(query)) >= 0
}

/*
 * Hasil berbentuk sama dengan jawaban server (`results`, `count`, `next`) supaya
 * `App.vue` tidak perlu dua jalur tampilan: `next` null berarti tidak ada
 * halaman berikutnya, dan `count` adalah jumlah hasil yang cocok — bukan jumlah
 * baris yang dikirim.
 */
export function searchCatalog(items = [], query = '', { page = 1, perPage = LOCAL_PAGE_SIZE } = {}) {
  const words = queryWords(query)
  /* Cocok lewat nama/varian tampil lebih dulu, lalu yang cocok lewat alias. */
  const rows = (items || [])
    .filter(Boolean)
    .map((item) => ({ item, rank: matchRank(item, words) }))
    .filter((row) => row.rank >= 0)
    .sort((left, right) => left.rank - right.rank || compareCatalogRows(left.item, right.item))
    .map((row) => row.item)

  const size = Math.max(1, Number(perPage) || LOCAL_PAGE_SIZE)
  const current = Math.max(1, Number(page) || 1)
  const start = (current - 1) * size
  const results = rows.slice(start, start + size)

  return {
    results,
    count: rows.length,
    next: start + size < rows.length ? current + 1 : null,
  }
}

/* Umur katalog dalam bahasa yang bisa dibaca kasir, bukan milidetik. */
export function describeCatalogAge(ageMs) {
  const minutes = Math.floor(Number(ageMs) / 60000)

  if (!Number.isFinite(minutes) || minutes < 0) {
    return 'belum diketahui'
  }

  if (minutes < 1) {
    return 'baru saja'
  }

  if (minutes < 60) {
    return `${minutes} menit lalu`
  }

  const hours = Math.floor(minutes / 60)

  if (hours < 24) {
    return `${hours} jam lalu`
  }

  return `${Math.floor(hours / 24)} hari lalu`
}

export default {
  LOCAL_PAGE_SIZE,
  compareCatalogRows,
  describeCatalogAge,
  matchCatalogQuery,
  normalizeCatalogText,
  searchCatalog,
}
