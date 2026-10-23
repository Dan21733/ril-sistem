import { API_BASE_URL, deviceAuthHeader } from './deviceApi'
import { saveSealKey } from '../workers/pinSeal'

/*
 * Daftar worker untuk pencocokan PIN di perangkat (OFFLINE_SYNC_DESIGN.md §7).
 *
 * Respons memuat nilai PIN tersimpan apa adanya (di tabel saat ini: hash bcrypt
 * mentah). Bukan PIN terbuka: yang bisa dilakukan tablet adalah **menguji** PIN
 * yang diketik, bukan membaca PIN kasir. Karena itu device token wajib — tanpa
 * token backend membalas 401, dan tidak satu pun baris terkirim.
 *
 * `worker_id` tidak pernah ada di respons: identitas resmi tetap ditentukan
 * server saat nota dikirim/disinkronkan. Yang dipakai POS dari daftar ini hanya
 * nama untuk nota.
 */

export async function fetchOfflineWorkers(deviceToken) {
  const response = await fetch(`${API_BASE_URL}/api/workers/offline-list/`, {
    headers: deviceAuthHeader(deviceToken),
  })

  let body
  try {
    body = await response.json()
  } catch {
    body = {}
  }

  if (!response.ok) {
    const error = new Error(
      body.detail || `Gagal memuat daftar kasir (HTTP ${response.status}).`,
    )
    error.status = response.status
    throw error
  }

  /* Kunci publik untuk mengunci PIN penyetuju di antrean (pinSeal.js). */
  saveSealKey(body.kunci_pin)

  return Array.isArray(body.workers) ? body.workers : []
}

export default {
  fetchOfflineWorkers,
}
