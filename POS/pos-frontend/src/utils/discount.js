/*
 * Diskon persen di keranjang (permintaan pemilik, 31 Okt 2026).
 *
 * Kasir mengetik "10" (atau "10%", "2,5") dan harga satuan menjadi harga
 * pricelist dikurangi persen itu, DIBULATKAN KE ATAS ke kelipatan yang diatur
 * pemilik di master (menu Toko, bawaan Rp100). Diskon tidak mengubah aturan
 * floor: hasil di bawah floor tetap ditandai merah dan butuh persetujuan.
 *
 * Kelipatan pembulatan datang dari jawaban /api/health/ dan disimpan di
 * perangkat supaya tetap dipakai saat offline.
 */

const STORAGE_KEY = 'pos.pembulatanDiskon'
const BAWAAN = 100
const PILIHAN = [1, 100, 500, 1000]

function storage() {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null
  }
}

export function discountRounding() {
  const nilai = Number(storage()?.getItem(STORAGE_KEY))
  return PILIHAN.includes(nilai) ? nilai : BAWAAN
}

export function noteDiscountRounding(nilai) {
  const angka = Number(nilai)
  if (!PILIHAN.includes(angka)) {
    return
  }
  try {
    storage()?.setItem(STORAGE_KEY, String(angka))
  } catch {
    /* Penyimpanan penuh/terkunci: pakai nilai lama. */
  }
}

/* "10", "10%", " 2,5 % " -> 10 / 2.5; selain angka 0..100 -> null. */
export function parsePercent(teks) {
  const bersih = String(teks ?? '').replace('%', '').replace(',', '.').trim()
  if (bersih === '' || !/^\d+(\.\d+)?$/.test(bersih)) {
    return null
  }
  const persen = Number(bersih)
  return persen >= 0 && persen <= 100 ? persen : null
}

/*
 * Harga setelah diskon, dibulatkan ke atas ke kelipatan `rounding`, tidak
 * pernah di atas pricelist. Dihitung dalam basis-poin supaya 2,5% tidak
 * meleset karena pecahan biner.
 */
export function discountedPrice(pricelist, persen, rounding = discountRounding()) {
  const harga = Number(pricelist)
  if (pricelist == null || pricelist === '' || !Number.isSafeInteger(harga) || harga < 0 || persen == null) {
    return null
  }
  const satuan = Math.max(1, Number(rounding) || 1)
  const basisPoin = Math.round(Number(persen) * 100)
  const mentah = (harga * (10000 - basisPoin)) / 10000
  const bulat = Math.ceil(mentah / satuan - 1e-9) * satuan
  return Math.min(harga, Math.max(0, bulat))
}

/* Persen diskon yang terlihat dari harga sekarang (untuk label "−10%"). */
export function percentOff(pricelist, unitPrice) {
  const harga = Number(pricelist)
  const jual = Number(unitPrice)
  if (!harga || !Number.isFinite(jual) || jual >= harga) {
    return 0
  }
  return Math.round((1 - jual / harga) * 1000) / 10
}

export const QUICK_DISCOUNTS = [5, 10, 15, 20, 25]
