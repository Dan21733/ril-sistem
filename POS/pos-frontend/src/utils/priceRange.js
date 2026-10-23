/*
 * Rentang harga nego: `floor_price <= unit_price <= price` (pricelist = ceiling).
 *
 * Kenapa diperiksa juga di perangkat, padahal server sudah menegakkannya
 * (pos/transaction_service.py `_validate_item_price`): baris antrean cetak
 * ditulis **sebelum** nota dikirim ke server — itu yang membuat nota tetap
 * keluar saat internet mati. Akibatnya nota yang akan ditolak server sudah
 * kertasnya lebih dulu (laporan pemilik, 2026-09-30: "error harga di bawah
 * floor, tetapi print tetap mencetak struk"). Rentang harga sudah diketahui
 * perangkat dari `GET /api/items/`, jadi pemeriksaan ini bisa menahan nota
 * sebelum satu byte pun sampai ke printer.
 *
 * Server tetap penentu akhir: kalau rentangnya belum tersedia perangkat (item
 * tidak ada di halaman katalog yang termuat), pemeriksaan ini tidak memblok apa
 * pun dan keputusan server yang berlaku.
 */

export const PRICE_RANGE_PROBLEM = {
  BELOW: 'below',
  ABOVE: 'above',
}

/*
 * `null` berarti tidak ada alasan untuk menahan: nilai hilang atau bukan angka
 * tidak pernah menghasilkan blokir, karena data yang tidak lengkap bukan bukti
 * harga salah.
 */
export function priceRangeProblem(unitPrice, floorPrice, ceilingPrice) {
  /*
   * Nilai yang belum ada diperiksa lebih dulu, bukan diserahkan ke `Number()`:
   * `Number(null)` adalah 0, dan 0 akan terbaca sebagai "di bawah floor" —
   * nota tanpa harga malah diblokir dengan alasan yang salah.
   */
  if (unitPrice === null || unitPrice === undefined || unitPrice === '') {
    return null
  }

  const unit = Number(unitPrice)

  if (!Number.isFinite(unit)) {
    return null
  }

  const floor = floorPrice == null ? null : Number(floorPrice)
  const ceiling = ceilingPrice == null ? null : Number(ceilingPrice)

  if (floor != null && Number.isFinite(floor) && unit < floor) {
    return PRICE_RANGE_PROBLEM.BELOW
  }

  if (ceiling != null && Number.isFinite(ceiling) && unit > ceiling) {
    return PRICE_RANGE_PROBLEM.ABOVE
  }

  return null
}

/*
 * Satu tempat yang memutuskan harga jual + floor sebuah item katalog.
 *
 * Server mempublikasikan `max_price` (pricelist = harga atap) sebagai `price`
 * dan `min_price` sebagai `floor_price`. Nilai **`max_price` = 0** berarti
 * pricelist-nya belum diisi, bukan harga jual Rp 0 (data nyata 2026-10-07:
 * "Aries Pintu - Abu" min 245.000 / max 0 tampil Rp 0 di katalog). Untuk item
 * seperti itu harga jualnya `min_price`, dan karena harga itu sudah duduk di
 * batas bawah, item itu **tidak punya floor lagi**: `floor_price` dijadikan
 * null supaya pil kode floor di keranjang hilang dengan sendirinya
 * (`encodeFloorCode` mengembalikan '' untuk nilai kosong), bukan disembunyikan.
 *
 * `price` yang null (`max_price` NULL) tetap berarti master data belum
 * lengkap: harga tidak dikarang dari floor, katalog menampilkan "Harga tidak
 * tersedia".
 *
 * Harga pas (`min_price` == `max_price`, permintaan pemilik 2026-10-12) juga
 * tidak punya floor: tidak ada ruang nego sehingga pil Floor tidak ditampilkan
 * dan tidak ada blokir "di bawah floor". Batas atas tetap ada — menjual di
 * atas pricelist tetap ditahan — dan server tetap penentu akhir kalau kasir
 * menjual di bawah harga pas saat offline.
 *
 * Dipakai saat memetakan `GET /api/items/` dan saat memperbarui ulang data
 * keranjang lama, supaya katalog, keranjang, dan pemeriksaan rentang tidak
 * pernah memakai aturan harga yang berbeda.
 */
export function resolveItemPrices(item = {}) {
  const ceiling = item.price == null ? null : Number(item.price)
  const floor = item.floor_price == null ? null : Number(item.floor_price)

  if (ceiling === 0) {
    return { price: floor, floor_price: null }
  }

  if (floor !== null && ceiling !== null && floor === ceiling) {
    return { price: ceiling, floor_price: null }
  }

  return { price: ceiling, floor_price: floor }
}

/* Label barang untuk pesan ke kasir; bentuknya sama dengan yang di keranjang. */
export function itemLabel(item) {
  const variant = item?.variant && item.variant !== 'N/A' ? ` - ${item.variant}` : ''

  return `${item?.item_name ?? 'Barang'}${variant}`
}

/*
 * Baris pertama keranjang yang harganya keluar rentang, atau `null` kalau semua
 * baris aman.
 */
export function findCartPriceProblem(cartItems = []) {
  for (const item of cartItems) {
    /*
     * Batasnya diambil lewat `resolveItemPrices()` lagi, bukan dari field
     * mentah item keranjang: keranjang lama (localStorage) masih bisa menyimpan
     * harga atap 0 dari pricelist yang sekarang sudah diisi, dan itu akan
     * terbaca sebagai "di bawah floor" padahal yang salah harga simpanannya.
     */
    const resolved = resolveItemPrices(item)
    const problem = priceRangeProblem(
      item?.unit_price ?? resolved.price,
      resolved.floor_price,
      resolved.price,
    )

    if (problem) {
      return { item, problem }
    }
  }

  return null
}

export default {
  PRICE_RANGE_PROBLEM,
  priceRangeProblem,
  resolveItemPrices,
  itemLabel,
  findCartPriceProblem,
}
