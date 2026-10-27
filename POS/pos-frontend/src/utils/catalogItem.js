import { resolveItemPrices } from './priceRange'

/*
 * Pemetaan satu item `GET /api/items/` ke bentuk yang dipakai layar kasir.
 *
 * Ada dua jalur yang harus menghasilkan bentuk yang sama persis: katalog online
 * di `App.vue` dan katalog tersimpan untuk offline
 * (`services/catalog/catalogCache.js`). Kalau keduanya memetakan sendiri-sendiri,
 * item yang sama bisa tampil dengan harga atau stok yang berbeda hanya karena
 * koneksi saat itu — dan bedanya baru ketahuan saat nota ditolak.
 *
 * Aturan harganya tetap di `resolveItemPrices()` (pricelist 0 berarti belum
 * diisi, bukan Rp 0); modul ini hanya menyatukan bentuk hasilnya.
 */
export function mapApiItem(item = {}) {
  const stockAlert = item.stock_alert

  return {
    item_name: item.item_name,
    variant: item.variant,
    ...resolveItemPrices({
      price: item.price,
      floor_price: item.floor_price,
    }),
    /* null = stok sedang ditutup karena ada tugas hitung stok (hitung buta), bukan 0. */
    stock: item.stock === null || item.stock === undefined ? null : Number(item.stock),
    stock_alert: stockAlert === null || stockAlert === undefined ? null : Number(stockAlert),
    /* Kode rak dari master; kunci hanya ada kalau rak diisi. */
    ...(item.rak ? { rak: String(item.rak) } : {}),
    /* Alias pencarian dari master, dipakai pencarian offline; hanya kalau ada. */
    ...(item.alias ? { alias: String(item.alias) } : {}),
  }
}

export default { mapApiItem }
