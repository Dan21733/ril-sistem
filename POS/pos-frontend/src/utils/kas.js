/*
 * Kas laci di tablet (rancangan 2026-11-03): keputusan kecil yang tidak butuh
 * layar, supaya bisa diuji tanpa komponen.
 *
 * - Satu sesi kas per toko. Tablet "kas" (diizinkan pemilik di master) boleh
 *   buka & tutup; tablet lain hanya mencatat uang keluar.
 * - Tidak ada tablet kas = mode pemilik: kas dibuka/ditutup dari HP/master.
 * - Penjualan tidak pernah diblokir walau kas belum dibuka.
 */

const STATUS_KEY = 'pos.kasStatus'

export const KATEGORI_BAWAAN = [
  'Diambil pemilik',
  'Belanja keperluan toko',
  'Ongkir / bensin',
  'Makan & minum',
  'Bayar supplier / barang',
  'Lainnya',
]

/* Salinan status terakhir dari server, untuk ditampilkan saat offline. */
export function rememberKasStatus(status, storage = globalThis.localStorage) {
  try {
    storage?.setItem(STATUS_KEY, JSON.stringify({ ...status, disimpan: new Date().toISOString() }))
  } catch {
    /* Penyimpanan penuh / diblokir: status hanya hidup di memori. */
  }
}

export function readKasStatus(storage = globalThis.localStorage) {
  try {
    const raw = storage?.getItem(STATUS_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function sameLocalDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
}

/* Kas sudah ditutup hari ini (dan belum dibuka lagi)? */
export function tutupHariIni(status, now = new Date()) {
  if (!status || status.sesi || !status.tutup_terakhir) {
    return false
  }
  const tutup = new Date(status.tutup_terakhir)
  return !Number.isNaN(tutup.getTime()) && sameLocalDay(tutup, now)
}

/* Pengingat di atas daftar barang: hanya di tablet kas yang kasnya belum dibuka hari ini. */
export function kasBanner(status, now = new Date()) {
  if (!status || !status.boleh_kas || status.sesi || tutupHariIni(status, now)) {
    return null
  }
  return { text: 'Kas belum dibuka. Penjualan tetap bisa jalan.', action: 'Buka kas' }
}

export function diambilTotal(lembar100, lembar50) {
  return (Math.max(0, Number(lembar100) || 0) * 100000) + (Math.max(0, Number(lembar50) || 0) * 50000)
}

/* Alasan tombol tutup kas tidak bisa dipakai, atau '' kalau boleh. */
export function tutupProblem({ status, online, localPending, uangSisa }) {
  if (!status?.sesi) {
    return 'Kas belum dibuka.'
  }
  if (!status.boleh_kas) {
    return 'Kas ditutup dari tablet kas atau oleh pemilik.'
  }
  if (!online) {
    return 'Tutup kas butuh koneksi ke server.'
  }
  if (localPending > 0) {
    return `Masih ada ${localPending} catatan di tablet ini yang belum terkirim. Kirim dulu (menu Transaksi > Kirim semua).`
  }
  if (uangSisa === '' || uangSisa === null || uangSisa === undefined) {
    return 'Isi uang sisa di laci.'
  }
  return ''
}

/* Alasan catatan uang keluar / tambah modal belum bisa disimpan, atau ''. */
export function gerakProblem({ jenis, jumlah, kategori, keterangan }) {
  if (!(Number(jumlah) > 0)) {
    return 'Isi jumlahnya.'
  }
  if (jenis === 'keluar' && !kategori) {
    return 'Pilih untuk apa uangnya keluar.'
  }
  if (jenis === 'keluar' && kategori === 'Lainnya' && !String(keterangan || '').trim()) {
    return 'Tulis keterangan untuk "Lainnya".'
  }
  return ''
}

/* Uang keluar sesi ini: catatan dari server + yang masih antre di tablet (tanpa dobel). */
export function mergeGerak(serverRows = [], localRows = []) {
  const known = new Set(serverRows.map((g) => g.gerak_id))
  const pending = localRows
    .filter((g) => g.status !== 'sent' && !known.has(g.gerak_id))
    .map((g) => ({ ...g, waktu: g.created_at, local: true }))
  return [...serverRows, ...pending].sort((a, b) => String(a.waktu).localeCompare(String(b.waktu)))
}
