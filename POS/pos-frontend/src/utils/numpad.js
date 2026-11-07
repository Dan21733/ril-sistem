/**
 * Papan angka di layar kasir (permintaan pemilik, 2026-11-06).
 *
 * Keyboard Samsung menutup hampir separuh layar tablet (mendatar tinggal ~330px,
 * dialog bayar jadi tidak terbaca). Untuk isian angka, tablet memakai papan
 * angka sendiri yang muncul di samping isian; keyboard sistem tidak dibuka
 * (`inputmode="none"`). Isian teks biasa (nama pelanggan, catatan) tetap memakai
 * keyboard sistem.
 *
 * Logika di sini murni (tanpa DOM) supaya bisa diuji; komponen `NumPad.vue`
 * yang menempelkannya ke isian yang sedang fokus.
 */

/** Isian yang memakai papan angka: semua isian bertanda inputmode angka. */
export const PEMILIH_ISIAN_ANGKA = 'input[inputmode="numeric"], input[inputmode="decimal"], input[data-numpad]'
/** Isian yang sudah ditandai (inputmode-nya sudah diganti "none"). */
export const PEMILIH_ISIAN_TERTANDA = `${PEMILIH_ISIAN_ANGKA}, input[data-numpad-mode]`

/**
 * Nilai isian setelah satu tombol ditekan.
 *
 * - `segar`: isian baru saja dibuka; angka pertama MENGGANTI isi lama
 *   (mengetik harga baru tidak perlu menghapus harga lama dulu).
 * - `maks`: batas panjang (PIN 6 digit); angka berlebih diabaikan.
 * - Tombol `,` hanya untuk isian desimal (diskon persen).
 * Nilai yang sudah diformat ("Rp 300.000") cukup ditambah/dikurangi di ujung;
 * penangan `input` milik tiap isian yang membaca ulang digitnya.
 */
export function tekanTombol(nilai, tombol, { segar = false, maks = 0, desimal = false } = {}) {
  const sekarang = String(nilai ?? '')
  if (tombol === 'hapus') return segar ? '' : sekarang.slice(0, -1)
  if (tombol === ',' && !desimal) return sekarang
  if (tombol === ',' && !segar && sekarang.includes(',')) return sekarang
  const dasar = segar ? '' : sekarang
  const hasil = dasar + tombol
  if (maks > 0) {
    // Panjang dihitung dari digit saja: nilai berformat "Rp 300.000" punya titik/awalan.
    const digit = hasil.replace(/\D/g, '')
    if (digit.length > maks) return sekarang
  }
  return hasil
}

/**
 * Letak papan (dalam px viewport) terhadap kotak isian.
 *
 * Urutan pilihan: di KIRI isian bila muat (tablet mendatar: keranjang dan
 * kolom bayar ada di kanan, jadi papan menutupi daftar barang / kolom
 * pengambilan yang sedang tidak dipakai); kalau tidak, di ATAS untuk isian di
 * separuh bawah layar dan di BAWAH untuk isian di separuh atas.
 * Selalu dijepit di dalam layar dengan jarak `tepi`.
 */
export function letakPapan(kotak, ukuran, layar, { jarak = 12, tepi = 8 } = {}) {
  const jepit = (n, min, max) => Math.max(min, Math.min(n, max))
  const maksKiri = layar.lebar - ukuran.lebar - tepi
  const maksAtas = layar.tinggi - ukuran.tinggi - tepi
  if (kotak.left - jarak - ukuran.lebar >= tepi) {
    return {
      sisi: 'kiri',
      left: kotak.left - jarak - ukuran.lebar,
      top: jepit(kotak.top + kotak.height / 2 - ukuran.tinggi / 2, tepi, maksAtas),
    }
  }
  const left = jepit(kotak.left, tepi, maksKiri)
  // Isian di separuh bawah layar (Uang Diterima di tablet tegak): papan di atasnya,
  // supaya saran nominal dan tombol Konfirmasi di bawah isian tetap terlihat.
  const muatAtas = kotak.top - jarak - ukuran.tinggi >= tepi
  if (muatAtas && kotak.top > layar.tinggi / 2) {
    return { sisi: 'atas', left, top: kotak.top - jarak - ukuran.tinggi }
  }
  if (kotak.bottom + jarak + ukuran.tinggi <= layar.tinggi - tepi) {
    return { sisi: 'bawah', left, top: kotak.bottom + jarak }
  }
  return { sisi: 'atas', left, top: jepit(kotak.top - jarak - ukuran.tinggi, tepi, maksAtas) }
}

/**
 * Apakah lepas fokus isian disebabkan ketukan di papan sendiri (bug tablet 2026-11-07).
 *
 * Di Android, ketukan jari bisa memindah fokus ke tombol papan walau
 * `pointerdown` sudah dicegah; dulu itu dianggap "pindah ke tempat lain", papan
 * tertutup setiap satu angka, dan angka berikutnya (isian dibuka ulang = segar)
 * menimpa angka sebelumnya. PIN 6 digit jadi tidak pernah bisa diketik.
 * `fokusBaru`: elemen tujuan fokus ada di dalam papan; `msSejakTekan`: jarak
 * waktu sejak papan terakhir disentuh.
 */
export function lepasKarenaPapan({ fokusBaruDiPapan = false, msSejakTekan = Infinity } = {}, batasMs = 600) {
  return Boolean(fokusBaruDiPapan) || msSejakTekan < batasMs
}

/**
 * Papan angka dipakai di layar sentuh, atau dipaksa lewat localStorage
 * `pos.numpad` = '1' (pengujian) / '0' (matikan).
 */
export function papanAktif(win = globalThis) {
  try {
    const paksa = win.localStorage?.getItem('pos.numpad')
    if (paksa === '1') return true
    if (paksa === '0') return false
  } catch {
    // localStorage bisa ditolak (mode privat): ikut deteksi layar sentuh.
  }
  return Boolean(win.matchMedia?.('(pointer: coarse)').matches || win.matchMedia?.('(any-pointer: coarse)').matches)
}
