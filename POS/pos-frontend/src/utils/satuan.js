/**
 * Jual per satuan dengan kelipatan (keputusan pemilik, 2026-11-06).
 *
 * Barang meteran/kiloan (fiber, seng plat roll, kawat loket, amplas, paku, karpet
 * talang) diberi `satuan` ("m", "kg") dan `kelipatan` di master. Kelipatan 10 berarti
 * jumlah dan harga di keranjang, nota, dan server disimpan per 0,1 satuan sebagai
 * bilangan bulat: 1,5 m = jumlah 15, Rp 25.000/m = harga 2.500. Layar kasir dan nota
 * kertas menampilkan satuan aslinya. Barang biasa: kelipatan 1, tidak ada yang berubah.
 */

export function kelipatanOf(item) {
  const k = Number(item?.kelipatan)
  return Number.isInteger(k) && k > 1 ? k : 1
}

export function pakaiSatuan(item) {
  return Boolean(item?.satuan) && kelipatanOf(item) > 1
}

/** Akhiran harga, mis. "/m". Kosong untuk barang biasa. */
export function akhiranSatuan(item) {
  return item?.satuan ? `/${item.satuan}` : ''
}

/** Jumlah tersimpan -> angka tampilan: 15 (k=10) -> "1,5". */
export function angkaJumlah(jumlah, item) {
  const k = kelipatanOf(item)
  const n = Number(jumlah)
  if (!Number.isFinite(n)) return ''
  if (k === 1) return String(n)
  const desimal = String(k).length - 1
  return (n / k).toFixed(desimal).replace(/\.?0+$/, '').replace('.', ',')
}

/** Jumlah tersimpan -> teks lengkap: 15 (m, k=10) -> "1,5 m"; 3 (biasa) -> "3". */
export function teksJumlah(jumlah, item) {
  const angka = angkaJumlah(jumlah, item)
  return item?.satuan ? `${angka} ${item.satuan}` : angka
}

/**
 * Teks ketikan kasir ("1,5", "1.5", "0,8") -> jumlah tersimpan (bulat, minimal 1).
 * Barang biasa: angka bulat seperti sebelumnya. Hasil null = ketikan tidak sah.
 */
export function bacaJumlah(teks, item) {
  const k = kelipatanOf(item)
  const bersih = String(teks ?? '').trim().replace(',', '.')
  if (!/^\d*\.?\d*$/.test(bersih) || bersih === '' || bersih === '.') return null
  const nilai = Math.round(Number(bersih) * k)
  if (!Number.isFinite(nilai)) return null
  return Math.max(1, nilai)
}

/** Harga tersimpan (per kelipatan) -> harga per satuan. */
export function hargaPerSatuan(harga, item) {
  if (harga === null || harga === undefined || harga === '') return harga
  return Number(harga) * kelipatanOf(item)
}

/** Harga per satuan ketikan kasir -> harga tersimpan (dibulatkan ke rupiah). */
export function hargaTersimpan(hargaSatuan, item) {
  return Math.round(Number(hargaSatuan) / kelipatanOf(item))
}

/** Langkah tombol +/−: satu satuan penuh (1 m = 10 untuk kelipatan 10). */
export function langkahJumlah(item) {
  return kelipatanOf(item)
}

/*
 * Jual per rupiah (keputusan pemilik 9 Nov 2026): pembeli bilang "paku 5 ribu".
 * Kasir mengetik rupiahnya; jumlah = rupiah : harga pricelist, DIBULATKAN KE ATAS ke
 * kelipatan (pembeli tidak pernah dapat kurang dari yang dibayar), lalu harga satuan
 * diturunkan supaya total pas. Total bisa kurang beberapa rupiah dari ketikan (tidak
 * pernah lebih) karena harga tersimpan bilangan bulat. Turunnya harga ikut aturan floor.
 * Hasil null: rupiah tidak sah / pricelist tidak ada.
 */
export function jualPerRupiah(rupiah, item) {
  const r = Number(rupiah)
  const p = Number(item?.price)
  if (!Number.isSafeInteger(r) || r <= 0 || !Number.isSafeInteger(p) || p <= 0) return null
  const quantity = Math.max(1, Math.ceil(r / p))
  const unit_price = Math.floor(r / quantity)
  return { quantity, unit_price, total: unit_price * quantity }
}

/* Tawar total pas: jumlah tetap, harga satuan = rupiah : jumlah (dibulatkan ke bawah). */
export function totalPas(rupiah, item) {
  const r = Number(rupiah)
  const q = Number(item?.quantity)
  const p = Number(item?.price)
  if (!Number.isSafeInteger(r) || r <= 0 || !Number.isSafeInteger(q) || q < 1) return null
  const unit_price = Math.floor(r / q)
  if (Number.isSafeInteger(p) && unit_price > p) return null
  return { quantity: q, unit_price, total: unit_price * q }
}
