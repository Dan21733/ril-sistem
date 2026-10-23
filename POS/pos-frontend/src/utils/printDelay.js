/*
 * Hitungan notifikasi cetak untuk tombol Riwayat Nota dan modal.
 *
 * `countDelayedNotas` mengukur kegagalan percobaan untuk diagnostik.
 * Lencana UI memakai `countUnprintedNotasToday`: nota yang transaksinya terjadi
 * hari ini dan belum tercetak, termasuk pekerjaan yang belum dicoba.
 *
 * Satu nota diantar dapat memiliki dua print job (nota + surat jalan), maka
 * seluruh hitungan disatukan berdasarkan `sync_id`.
 */
export const DELAYED_PRINT_STATUSES = Object.freeze(['retryable', 'blocked'])

export function isDelayedPrintJob(job) {
  if (!job) {
    return false
  }

  /* Yang sudah keluar tidak pernah tertunda, berapa pun attempts-nya. */
  if (job.status === 'printed') {
    return false
  }

  if (DELAYED_PRINT_STATUSES.includes(job.status)) {
    return true
  }

  return Number(job.attempts) > 0
}

/*
 * Jumlah **nota** (bukan pekerjaan cetak) yang cetaknya tertunda.
 *
 * Satu nota diantar menghasilkan dua pekerjaan cetak — nota dan surat jalan
 * (`enqueueReceiptPrint` di services/printer/printQueue.js) — sehingga
 * menghitung baris antrean akan melaporkan "2 nota" untuk satu penjualan.
 * Karena itu yang dihitung adalah `sync_id` yang unik. Pekerjaan tanpa
 * `sync_id` dihitung sendiri-sendiri karena tidak ada yang menyatukannya
 * dengan nota mana pun.
 */
export function countDelayedNotas(jobs = []) {
  const notas = new Set()
  let looseJobs = 0

  /* `null` dari pemanggil diperlakukan seperti daftar kosong. */
  for (const job of jobs ?? []) {
    if (!isDelayedPrintJob(job)) {
      continue
    }

    if (job.sync_id) {
      notas.add(String(job.sync_id))
    } else {
      looseJobs += 1
    }
  }

  return notas.size + looseJobs
}

function localDateKey(value) {
  const date = value instanceof Date ? value : new Date(value)

  if (Number.isNaN(date.getTime())) {
    return ''
  }

  const pad2 = (number) => String(number).padStart(2, '0')

  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`
}

/*
 * Jumlah **nota yang belum tercetak hari ini**, termasuk pekerjaan baru yang
 * belum dicoba. Baris pekerjaan (nota + surat jalan) dikelompokkan per
 * `sync_id`; tanggal mengikuti waktu transaksi `transaction_time` di zona
 * waktu lokal kasir. Job lama tanpa timestamp transaksi memakai `created_at`.
 */
export function countUnprintedNotasToday(jobs = [], now = new Date()) {
  const today = localDateKey(now)
  const notas = new Set()
  let looseJobs = 0

  if (!today) {
    return 0
  }

  for (const job of jobs ?? []) {
    if (!job) {
      continue
    }

    const notaDate = job.transaction_time || job.created_at

    if (job.status === 'printed' || localDateKey(notaDate) !== today) {
      continue
    }

    if (job.sync_id) {
      notas.add(String(job.sync_id))
    } else {
      looseJobs += 1
    }
  }

  return notas.size + looseJobs
}

/*
 * Kunci stabil untuk satu set seluruh pekerjaan yang belum tercetak. Ini
 * menutup berita dan aksi bersama-sama, termasuk pekerjaan dari tanggal lama.
 */
export function unprintedPrintKey(jobs = []) {
  const ids = new Set()

  for (const job of jobs ?? []) {
    if (!job || job.status === 'printed') {
      continue
    }

    ids.add(String(job.job_id ?? job.sync_id ?? 'tanpa-id'))
  }

  return [...ids].sort().join('|')
}

/*
 * Identitas **himpunan pekerjaan cetak yang tertunda**: dipakai untuk menyimpan
 * penutupan pemberitahuan cetak (permintaan pemilik, 2026-10-10).
 *
 * Kenapa bukan teks pesannya: pesan cetak disusun ulang tiap siklus
 * ("Pekerjaan cetak tertunda; akan dicoba lagi otomatis." vs "1 nota gagal
 * dicetak…"), dan setelah tablet di-refresh pesannya mulai dari kosong. Kunci
 * yang berbasis teks karena itu tidak pernah cocok lagi — persis keluhan pemilik
 * saat backend dimatikan: sudah diklik ×, di-refresh, pemberitahuannya kembali.
 * Himpunan `job_id` tertunda stabil lintas muat ulang selama pekerjaannya sama,
 * dan berubah (→ pemberitahuan muncul lagi) begitu ada nota yang gagal menyusul.
 */
export function delayedPrintKey(jobs = []) {
  const ids = new Set()

  for (const job of jobs ?? []) {
    if (!isDelayedPrintJob(job)) {
      continue
    }

    /* `job_id` selalu ada di antrean nyata; `sync_id` cuma jaring pengaman. */
    ids.add(String(job.job_id ?? job.sync_id ?? 'tanpa-id'))
  }

  return [...ids].sort().join('|')
}

export default {
  DELAYED_PRINT_STATUSES,
  isDelayedPrintJob,
  countDelayedNotas,
  countUnprintedNotasToday,
  unprintedPrintKey,
  delayedPrintKey,
}
