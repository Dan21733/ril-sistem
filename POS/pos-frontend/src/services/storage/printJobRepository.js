import db from './db'

/*
 * Antrean cetak nota (PRINT_PATH_DESIGN.md §5).
 *
 * Cetak adalah upaya terbaik: nota yang sudah sah tidak pernah dibatalkan karena
 * printer mati. Karena itu job disimpan lebih dulu, lalu dikirim; kegagalan
 * hanya memundurkan jadwal percobaan berikutnya.
 *
 * Status:
 * - `pending`   menunggu giliran (baru dibuat atau dijadwalkan ulang)
 * - `printing`  sedang dikirim ke bridge (dipulihkan ke `retryable` saat dibuka)
 * - `printed`   bridge sudah meneruskan byte ke printer
 * - `retryable` gagal sementara (printer mati, bridge mati, batas waktu)
 * - `blocked`   bridge menolak permanen; butuh manusia, tidak dicoba lagi
 *
 * Status terpisah dari `transactions.sync_status`: nota bisa sudah di server
 * sementara kertasnya belum keluar, dan sebaliknya saat offline.
 */

export const PRINT_STATUS = {
  PENDING: 'pending',
  PRINTING: 'printing',
  PRINTED: 'printed',
  RETRYABLE: 'retryable',
  BLOCKED: 'blocked',
}

/* Status yang boleh dicoba lagi otomatis oleh antrean cetak. */
export const PRINT_QUEUE_STATUSES = [PRINT_STATUS.PENDING, PRINT_STATUS.RETRYABLE]

export async function getPrintJob(jobId) {
  return db.print_jobs.get(jobId)
}

export async function getPrintJobsBySyncId(syncId) {
  return db.print_jobs.where('sync_id').equals(syncId).toArray()
}

export async function getPrintJobsBySyncIds(syncIds) {
  if (!syncIds?.length) {
    return []
  }

  return db.print_jobs.where('sync_id').anyOf(syncIds).toArray()
}

export async function savePrintJob(job) {
  return db.print_jobs.put({ ...job })
}

export async function deletePrintJob(jobId) {
  return db.print_jobs.delete(jobId)
}

export async function clearPrintJobs() {
  return db.print_jobs.clear()
}

export async function markPrintJobPrinting(jobId) {
  return db.print_jobs.update(jobId, {
    status: PRINT_STATUS.PRINTING,
    last_attempt_at: new Date().toISOString(),
  })
}

export async function markPrintJobPrinted(jobId) {
  return db.print_jobs.update(jobId, {
    status: PRINT_STATUS.PRINTED,
    detail: null,
    next_attempt_at: null,
    printed_at: new Date().toISOString(),
  })
}

export async function markPrintJobFailure(
  jobId,
  status,
  { detail = '', attempts = 0, nextAttemptAt = null } = {},
) {
  return db.print_jobs.update(jobId, {
    status,
    detail: detail || null,
    attempts,
    next_attempt_at: nextAttemptAt,
  })
}

/*
 * Kandidat antrean: hanya `pending`/`retryable` yang jadwalnya sudah tiba,
 * diurutkan paling tua dulu supaya nota tertua tidak tertinggal di belakang.
 */
export async function listPrintJobsToPrint(limit, now = new Date()) {
  const jobs = await db.print_jobs.where('status').anyOf(PRINT_QUEUE_STATUSES).toArray()
  const nowIso = now.toISOString()

  return jobs
    .filter((job) => !job.next_attempt_at || job.next_attempt_at <= nowIso)
    .sort((left, right) =>
      String(left.created_at || '').localeCompare(String(right.created_at || '')),
    )
    .slice(0, Math.max(1, limit))
}

/* Semua job yang belum keluar dari printer, untuk penanda di UI. */
export async function listOutstandingPrintJobs() {
  const jobs = await db.print_jobs.toArray()

  return jobs
    .filter((job) => job.status !== PRINT_STATUS.PRINTED)
    .sort((left, right) =>
      String(left.created_at || '').localeCompare(String(right.created_at || '')),
    )
}

export async function getLatestOutstandingPrintJob() {
  const jobs = await listOutstandingPrintJobs()

  return jobs.length > 0 ? jobs[jobs.length - 1] : null
}

export async function countOutstandingPrintJobs() {
  const jobs = await listOutstandingPrintJobs()

  return jobs.length
}

/*
 * Status `printing` bersifat sementara, persis seperti `syncing` pada nota:
 * kalau aplikasi mati saat pengiriman, status itu menggantung dan tidak akan
 * pernah dicoba lagi tanpa dipulihkan di sini.
 */
export async function promoteStuckPrintJobs() {
  const stuck = await db.print_jobs.where('status').equals(PRINT_STATUS.PRINTING).toArray()

  await Promise.all(
    stuck.map((job) =>
      markPrintJobFailure(job.job_id, PRINT_STATUS.RETRYABLE, {
        detail: 'Pengiriman cetak terputus; akan dicoba lagi.',
        attempts: job.attempts || 0,
        nextAttemptAt: null,
      }),
    ),
  )

  return stuck.length
}

/*
 * Semua job untuk nota yang dibuang sebelum pernah dikonfirmasi server tidak
 * punya tujuan lagi. Yang sudah tercetak tidak disentuh: kertas yang sudah keluar
 * tidak bisa ditarik kembali.
 */
export async function deleteOutstandingPrintJobsBySyncId(syncId) {
  const jobs = await getPrintJobsBySyncId(syncId)

  await Promise.all(
    jobs
      .filter((job) => job.status !== PRINT_STATUS.PRINTED)
      .map((job) => deletePrintJob(job.job_id)),
  )

  return jobs.length
}

/*
 * Retensi. Batasnya masih [USULAN] di PRINT_PATH_DESIGN.md §7, jadi dipilih
 * konservatif: riwayat cetak yang sudah berhasil tidak dibutuhkan lama (riwayat
 * nota tetap ada di server), sementara job yang belum keluar tidak pernah
 * dibuang — menyimpannya jauh lebih murah daripada nota yang hilang.
 */
export const PRINT_JOB_RETENTION_DAYS = 14

export async function prunePrintedPrintJobs(now = new Date()) {
  const cutoff = new Date(now.getTime() - PRINT_JOB_RETENTION_DAYS * 24 * 60 * 60 * 1000)
  const printed = await db.print_jobs.where('status').equals(PRINT_STATUS.PRINTED).toArray()
  const expired = printed.filter((job) => {
    const stamp = job.printed_at || job.created_at

    return stamp ? stamp < cutoff.toISOString() : true
  })

  await Promise.all(expired.map((job) => deletePrintJob(job.job_id)))

  return expired.length
}

export default {
  PRINT_STATUS,
  PRINT_QUEUE_STATUSES,
  PRINT_JOB_RETENTION_DAYS,
  getPrintJob,
  getPrintJobsBySyncId,
  getPrintJobsBySyncIds,
  savePrintJob,
  deletePrintJob,
  clearPrintJobs,
  markPrintJobPrinting,
  markPrintJobPrinted,
  markPrintJobFailure,
  listPrintJobsToPrint,
  listOutstandingPrintJobs,
  getLatestOutstandingPrintJob,
  countOutstandingPrintJobs,
  promoteStuckPrintJobs,
  deleteOutstandingPrintJobsBySyncId,
  prunePrintedPrintJobs,
}
