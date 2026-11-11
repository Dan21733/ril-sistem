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
export function splitPayment(totals, { method, cashReceived, payments }) {
  const sum = totals.reduce((a, b) => a + b, 0)

  if (method === 'campur') {
    return splitCampur(totals, payments)
  }

  return totals.map((total, index) => {
    if (method === 'cash' && index === 0) {
      const payment = Number(cashReceived) - (sum - total)
      return { payment, change: payment - total }
    }
    return { payment: total, change: 0 }
  })
}

/*
 * Bayar campur dipecah per nota (2026-11-12): nota ke-2 dst. dibayar pas, diambil
 * dari non-tunai lebih dulu lalu tunai; sisa semua uang (termasuk kelebihan)
 * jatuh ke nota pertama, jadi kembalian ada di nota pertama seperti tunai biasa.
 */
export function splitCampur(totals, payments) {
  const sisa = [...(payments || [])]
    .filter((p) => Number(p.amount) > 0)
    .map((p) => ({ method: p.method, amount: Number(p.amount) }))
    .sort((a, b) => (a.method === 'cash') - (b.method === 'cash'))
  const hasil = totals.map(() => null)

  for (let i = totals.length - 1; i >= 1; i -= 1) {
    let kurang = totals[i]
    const bagian = []
    for (const p of sisa) {
      if (kurang <= 0) break
      const ambil = Math.min(p.amount, kurang)
      if (ambil > 0) {
        bagian.push({ method: p.method, amount: ambil })
        p.amount -= ambil
        kurang -= ambil
      }
    }
    hasil[i] = { payment: totals[i] - kurang, change: 0, payments: bagian }
  }

  const pertama = sisa.filter((p) => p.amount > 0)
  const bayar = pertama.reduce((a, p) => a + p.amount, 0)
  hasil[0] = { payment: bayar, change: bayar - totals[0], payments: pertama }
  return hasil
}

export default { ownerOf, groupByOwner, isMixedCart, splitPayment, splitCampur }
