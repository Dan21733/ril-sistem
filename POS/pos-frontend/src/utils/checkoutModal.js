/*
 * Kapan modal checkout boleh menutup sendiri (permintaan pemilik, 2026-10-09:
 * "setelah tombol SELESAIKAN TRANSAKSI halaman ini tidak keluar sendiri").
 *
 * Modal hidup di `CartList.vue`, tetapi yang tahu checkout sudah selesai adalah
 * `App.vue` lewat `checkoutStatus`. Aturan ini satu-satunya tempat keputusan itu
 * diambil, supaya pertanyaan "masih perlukah kasir melihat modal?" tidak
 * tersebar di beberapa daftar status.
 *
 * Ditutup ketika:
 * - `success`: nota diterima server (baru atau idempoten) — kasir lanjut ke
 *   pembeli berikutnya.
 * - `retryable` / `rate_limited`: gagal terkirim; pesannya ada di panel
 *   keranjang, jadi modal tidak perlu menahan layar.
 * - `review` / `conflict` / `restoring`: butuh manusia atau sedang dipulihkan;
 *   pemberitahuannya juga ada di panel keranjang.
 * - `idle` yang datang SETELAH checkout berjalan (`submitting` / `restoring`):
 *   inilah jalur offline. Nota tersimpan ke antrean, lalu induk melepas
 *   keranjang dan mengembalikan status ke `idle` supaya kasir bisa jualan lagi.
 *   Tanpa aturan ini modal tertinggal di langkah PIN dengan kolom kosong dan
 *   penjualan berikutnya tidak bisa dimulai (dilaporkan pemilik 2026-10-09,
 *   direproduksi di browser sebelum diperbaiki).
 *
 * `idle` dari keadaan lain sengaja TIDAK menutup: `openConfirmation()` justru
 * membuka modal saat status masih `idle` — kalau checkout lama (`success` /
 * `error`) ditutup tepat sebelumnya — jadi menutup pada semua `idle` akan
 * menutup modal yang baru saja dibuka.
 */
const TERMINAL_STATUSES = [
  'success',
  'retryable',
  'rate_limited',
  'review',
  'conflict',
  'restoring',
]

/* Status yang berarti checkout sedang berjalan, bukan selesai. */
const IN_FLIGHT_STATUSES = ['submitting', 'restoring']

export function shouldCloseCheckoutModal(status, previousStatus = '') {
  if (TERMINAL_STATUSES.includes(status)) {
    return true
  }

  return status === 'idle' && IN_FLIGHT_STATUSES.includes(previousStatus)
}

export default { shouldCloseCheckoutModal }
