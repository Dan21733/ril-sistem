/*
 * Tanda tangan pesan cetak yang sudah ditutup kasir (permintaan pemilik,
 * 2026-10-10): menutup "Cetak tertunda" harus tetap berlaku setelah halaman
 * dimuat ulang, bukan hanya sampai tabletnya di-refresh.
 *
 * Kenapa di `localStorage`, bukan di Dexie: ini **keadaan tampilan**, bukan
 * data bisnis. Yang disimpan hanya kunci himpunan pekerjaan cetak yang gagal
 * (`job_id` belum tercetak, diurutkan), tanpa nama nota, jumlah, atau waktu.
 * Kalau penyimpanan tidak tersedia (mode privat, kuota penuh), fungsi ini
 * diam-diam mengembalikan tanda tangan kosong — perilakunya kembali seperti
 * sebelum ada penyimpanan.
 *
 * Modul ini tidak memutuskan kapan penutupan itu kedaluwarsa: itu urusan
 * `TransactionHistory.vue`, yang membatalkan penutupan begitu set pekerjaan
 * berubah (job baru atau job selesai dicetak).
 */
export const DISMISSED_PRINT_STORAGE_KEY = 'posDismissedPrintNotice'

/*
 * Tanda tangan himpunan nota yang belum tersinkron (permintaan pemilik,
 * 2026-10-11): panel "N belum tersinkron" kini punya lencana tutup seperti
 * panel cetak, dan penutupannya juga harus bertahan setelah halaman dimuat
 * ulang — alasan yang sama dengan panel cetak: pesannya disusun ulang dari
 * antrean perangkat yang memang masih tersimpan.
 *
 * Kuncinya **himpunan `sync_id`** yang masih antre, bukan jumlahnya: nota baru
 * yang masuk antrean harus memunculkan peringatannya lagi, sedangkan antrean
 * yang sama tidak boleh muncul kembali hanya karena halaman di-refresh.
 */
export const DISMISSED_QUEUE_STORAGE_KEY = 'posDismissedQueueNotice'

export function readDismissedPrintSignature() {
  try {
    return localStorage.getItem(DISMISSED_PRINT_STORAGE_KEY) || ''
  } catch {
    return ''
  }
}

export function saveDismissedPrintSignature(signature) {
  try {
    if (signature) {
      localStorage.setItem(DISMISSED_PRINT_STORAGE_KEY, String(signature))
    } else {
      localStorage.removeItem(DISMISSED_PRINT_STORAGE_KEY)
    }

    return true
  } catch {
    return false
  }
}

export function readDismissedQueueSignature() {
  try {
    return localStorage.getItem(DISMISSED_QUEUE_STORAGE_KEY) || ''
  } catch {
    return ''
  }
}

export function saveDismissedQueueSignature(signature) {
  try {
    if (signature) {
      localStorage.setItem(DISMISSED_QUEUE_STORAGE_KEY, String(signature))
    } else {
      localStorage.removeItem(DISMISSED_QUEUE_STORAGE_KEY)
    }

    return true
  } catch {
    return false
  }
}

export default {
  DISMISSED_PRINT_STORAGE_KEY,
  DISMISSED_QUEUE_STORAGE_KEY,
  readDismissedPrintSignature,
  saveDismissedPrintSignature,
  readDismissedQueueSignature,
  saveDismissedQueueSignature,
}
