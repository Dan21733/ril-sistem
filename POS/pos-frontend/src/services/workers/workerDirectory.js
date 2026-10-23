import { fetchOfflineWorkers } from '../api/workerApi'
import deviceRepository from '../storage/deviceRepository'
import {
  getWorkerDirectoryRefreshedAt,
  listWorkers,
  replaceWorkers,
} from '../storage/workerRepository'
import { countUnsupportedWorkers, matchWorkerPin } from './pinMatch'

/*
 * Direktori kasir di perangkat (KEPUTUSAN-9/10, OFFLINE_SYNC_DESIGN.md §7).
 *
 * Inilah yang menggantikan kolom nama kasir yang dulu diketik: nama pada nota
 * berasal dari worker yang memiliki PIN-nya. Modul ini menyatukan tiga hal yang
 * tidak boleh tersebar di komponen:
 *
 * 1. mengambil daftar dari server (butuh device token),
 * 2. mencocokkan PIN yang diketik terhadap daftar itu (bcrypt, di modul
 *    `pinMatch.js` yang bebas dari Dexie),
 * 3. mengingat hasil pencocokan selama daftar belum berubah — bcrypt sengaja
 *    lambat, dan kasir mengetik PIN berkali-kali.
 *
 * Penyegaran tidak pernah memblok penjualan: kalau gagal, daftar lama tetap
 * dipakai dan pesannya cukup dicatat pemanggil.
 */

/* pin -> nama (string kosong = sudah dicoba, tidak ada yang cocok). */
const matchCache = new Map()

let inFlightRefresh = null

async function runRefresh() {
  const device = await deviceRepository.getDevice()

  if (!device?.device_token) {
    return { refreshed: false, reason: 'unregistered', count: 0, unusable: 0 }
  }

  const workers = await fetchOfflineWorkers(device.device_token)
  const count = await replaceWorkers(workers)

  /*
   * Daftar berubah berarti hasil cocok yang lama tidak berlaku lagi: PIN bisa
   * sudah dipindahkan ke worker lain. Cache dibuang, bukan ditambal.
   */
  matchCache.clear()

  return {
    refreshed: true,
    reason: '',
    count,
    unusable: countUnsupportedWorkers(workers),
  }
}

export async function refreshWorkerDirectory() {
  if (inFlightRefresh) {
    return inFlightRefresh
  }

  inFlightRefresh = runRefresh().finally(() => {
    inFlightRefresh = null
  })

  return inFlightRefresh
}

/*
 * Nama kasir untuk satu PIN. Dipanggil saat kasir selesai mengetik 6 digit dan
 * sekali lagi sebelum nota difinalisasi; hasil kedua dilayani cache, jadi tidak
 * ada dua kali bcrypt untuk PIN yang sama.
 */
export async function findWorkerNameByPin(pin) {
  const submitted = String(pin ?? '')

  if (!/^\d{6}$/.test(submitted)) {
    return ''
  }

  if (matchCache.has(submitted)) {
    return matchCache.get(submitted)
  }

  const match = matchWorkerPin(submitted, await listWorkers())
  const name = match?.name || ''

  matchCache.set(submitted, name)

  return name
}

/* Ringkasan untuk UI: berapa kasir yang dikenal tablet ini, dan sejak kapan. */
export async function getWorkerDirectoryState() {
  const workers = await listWorkers()

  return {
    count: workers.length,
    unusable: countUnsupportedWorkers(workers),
    refreshedAt: await getWorkerDirectoryRefreshedAt(),
  }
}

export default {
  refreshWorkerDirectory,
  findWorkerNameByPin,
  getWorkerDirectoryState,
}
