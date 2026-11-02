/*
 * Keranjang campur barang toko + barang titipan (brand) — titipan fase 2,
 * keputusan pemilik 2026-10-31 / 2026-11-02:
 * - dibayar sekali, lalu dipecah menjadi satu nota per pemilik barang;
 * - nomor nota ikut urutan tablet yang sama;
 * - keranjang campur tidak boleh DP (harus lunas, barang dibawa/diantar).
 *
 * Modul ini murni (tanpa Vue/Dexie) supaya aturan pemecahan bisa diuji.
 */

/* Pemilik barang di keranjang: null = toko sendiri, angka = id toko titipan. */
export function ownerOf(item) {
  return Number.isInteger(item?.pemilik_id) ? item.pemilik_id : null
}

/* Kelompok pemilik, toko sendiri dulu, lalu urutan pertama muncul di keranjang. */
export function groupByOwner(items) {
  const groups = new Map()

  for (const item of Array.isArray(items) ? items : []) {
    const owner = ownerOf(item)

    if (!groups.has(owner)) {
      groups.set(owner, { owner, nama: item.pemilik_nama || '', items: [] })
    }
    groups.get(owner).items.push(item)
  }

  return [...groups.values()].sort((a, b) => (a.owner === null ? -1 : 0) - (b.owner === null ? -1 : 0))
}

export function isMixedCart(items) {
  return groupByOwner(items).length > 1
}

/*
 * Bagi satu pembayaran ke beberapa nota. Total tiap nota = barangnya (+ ongkir
 * hanya di nota pertama). Nota pertama menampung seluruh kelebihan uang tunai
 * (kembalian); nota lain dibayar pas. Non-tunai: semua nota dibayar pas.
 *
 * `totals`: total tiap nota, urut sesuai nota. Hasil: [{ payment, change }].
 */
export function splitPayment(totals, { method, cashReceived }) {
  const sum = totals.reduce((a, b) => a + b, 0)

  return totals.map((total, index) => {
    if (method === 'cash' && index === 0) {
      const payment = Number(cashReceived) - (sum - total)
      return { payment, change: payment - total }
    }
    return { payment: total, change: 0 }
  })
}

export default { ownerOf, groupByOwner, isMixedCart, splitPayment }
