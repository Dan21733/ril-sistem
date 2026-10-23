import db from './db'

/*
 * Penghitung percobaan PIN per perangkat (Dexie `pin_attempts`).
 *
 * Satu baris saja — `scope` = 'device' — karena penghitung per worker
 * membutuhkan penanda PIN, dan penanda apa pun dari ruang enam digit adalah
 * alat bantu penebak, bukan penghambat (utils/pinCooldown.js).
 */

export async function getPinAttempts(scope) {
  return db.pin_attempts.get(scope)
}

export async function savePinAttempts(record) {
  return db.pin_attempts.put({ ...record })
}

export async function clearPinAttempts(scope) {
  return db.pin_attempts.delete(scope)
}

export default {
  clearPinAttempts,
  getPinAttempts,
  savePinAttempts,
}
