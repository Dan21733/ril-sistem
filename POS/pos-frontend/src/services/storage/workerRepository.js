import db from './db'
import { WORKER_PIN_FORMAT } from '../workers/pinMatch'

/*
 * Daftar worker yang dipakai POS saat PIN harus dicocokkan tanpa server
 * (KEPUTUSAN-9, OFFLINE_SYNC_DESIGN.md §7).
 *
 * Satu baris = satu worker: `name` + `pin_stored` apa adanya dari tabel
 * `worker`. Daftar selalu **diganti utuh**, tidak ditambal per baris: sumber
 * kebenarannya tabel di server, jadi kasir yang sudah dihapus di sana harus
 * hilang di tablet juga — kalau tidak, PIN lamanya masih bisa membuat nota.
 *
 * Retensi tidak berlaku di sini: daftar ini kecil (hitungan baris) dan lebih
 * berbahaya kedaluwarsa sendiri daripada sedikit basi. Yang menjaga kesegarannya
 * adalah `fetched_at` pada tiap baris.
 */

function normalizeWorker(worker, fetchedAt) {
  return {
    name: String(worker?.name ?? '').trim(),
    pin_stored: String(worker?.pin_stored ?? ''),
    pin_format: worker?.pin_format || WORKER_PIN_FORMAT.OTHER,
    fetched_at: fetchedAt,
  }
}

export async function replaceWorkers(workers, fetchedAt = new Date().toISOString()) {
  const rows = (workers || [])
    .map((worker) => normalizeWorker(worker, fetchedAt))
    .filter((row) => row.name && row.pin_stored)

  await db.transaction('rw', db.workers, async () => {
    await db.workers.clear()

    if (rows.length > 0) {
      await db.workers.bulkAdd(rows)
    }
  })

  return rows.length
}

export async function listWorkers() {
  return db.workers.toArray()
}

export async function countWorkers() {
  return db.workers.count()
}

export async function clearWorkers() {
  return db.workers.clear()
}

export async function getWorkerDirectoryRefreshedAt() {
  const newest = await db.workers.orderBy('fetched_at').last()

  return newest?.fetched_at ?? null
}

export default {
  replaceWorkers,
  listWorkers,
  countWorkers,
  clearWorkers,
  getWorkerDirectoryRefreshedAt,
}
