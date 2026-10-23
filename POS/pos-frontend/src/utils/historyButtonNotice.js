/*
 * Penanda notifikasi di tombol "Riwayat Nota" (permintaan pemilik, 2026-10-10).
 *
 * Dua hal berbeda bisa menunggu di riwayat, dan kasir perlu bisa membedakannya
 * tanpa membuka modal:
 * - **nota belum tersinkron ke server** — pekerjaan server, dan jumlahnya
 *   penting karena pengiriman ulang dilakukan per nota. Karena itu bentuknya
 *   lencana angka.
 * - **nota hari ini belum tercetak** — kertas transaksi yang dibuat hari ini
 *   belum keluar dari printer, termasuk yang belum dicoba. Nota kemarin tidak
 *   menambah lencana hari ini, namun tetap tersedia untuk dicetak ulang di
 *   riwayat. Karena itu bentuknya lencana angka, sama seperti antrean.
 *
 * Walaupun bentuknya sama-sama angka, keduanya tidak diwakili oleh warna saja:
 * tiap lencana membawa glyph-nya sendiri (panah-kirim untuk server, printer
 * untuk kertas). Arti lengkapnya tetap tidak bergantung pada lencananya:
 * `title`/`aria-label` tombol yang membawa kalimatnya, sedangkan lencananya
 * dekoratif (`aria-hidden`).
 *
 * Teks di sini masuk ke layar kasir dan ke pembaca layar, jadi bahasanya untuk
 * orang toko: "belum tersinkron ke server", "belum tercetak hari ini".
 */

/* Angka dari pemanggil tidak pernah dipercaya apa adanya (bisa NaN/negatif). */
function toCount(value) {
  const number = Math.floor(Number(value))

  return Number.isFinite(number) && number > 0 ? number : 0
}

function noticeTitle(queued, unprinted) {
  if (queued > 0 && unprinted > 0) {
    return `${queued} nota belum tersinkron ke server; ${unprinted} nota hari ini belum tercetak. Buka riwayat untuk memeriksa.`
  }

  if (queued > 0) {
    return `${queued} nota belum tersinkron ke server. Buka riwayat untuk mengirim ulang.`
  }

  if (unprinted > 0) {
    return `${unprinted} nota hari ini belum tercetak di printer. Buka riwayat untuk mencetak ulang.`
  }

  return 'Riwayat Nota'
}

function noticeAriaLabel(queued, unprinted) {
  const parts = []

  if (queued > 0) {
    parts.push(`${queued} nota belum tersinkron`)
  }

  if (unprinted > 0) {
    parts.push(`${unprinted} nota hari ini belum tercetak`)
  }

  return parts.length > 0 ? `Riwayat Nota, ${parts.join(', ')}` : 'Riwayat Nota'
}

/*
 * Satu keputusan tampilan untuk tombol "Riwayat Nota": berapa yang ditampilkan,
 * apa yang dibaca pembaca layar, dan lencana mana yang muncul.
 */
export function historyButtonNotice({ queuedCount = 0, unprintedCount = 0 } = {}) {
  const queued = toCount(queuedCount)
  const unprinted = toCount(unprintedCount)
  const hasQueued = queued > 0
  const hasPrintAlert = unprinted > 0

  return {
    queued,
    unprinted,
    hasQueued,
    hasPrintAlert,
    showStatuses: hasQueued || hasPrintAlert,
    title: noticeTitle(queued, unprinted),
    ariaLabel: noticeAriaLabel(queued, unprinted),
  }
}

export default {
  historyButtonNotice,
}
