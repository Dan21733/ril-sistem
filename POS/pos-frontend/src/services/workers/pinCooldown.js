import { getPinAttempts, savePinAttempts } from '../storage/pinAttemptRepository'
import {
  isPinCooldownBlocked,
  normalizePinCooldown,
  pinCooldownRemainingSeconds,
  registerPinCooldownFailure,
  resetPinCooldown,
} from '../../utils/pinCooldown'

/*
 * Cooldown percobaan PIN di perangkat (keputusan pemilik 2026-10-08).
 *
 * Saat perangkat offline, throttle server tidak menjangkau tebakan PIN: satu-
 * satunya penghambat adalah hitungan lokal ini. Ia sengaja tidak menyimpan PIN
 * atau hash-nya, dan kegagalan penyimpanan tidak pernah memblokir penjualan —
 * ini penjaga UX, bukan batas keamanan (OFFLINE_SYNC_DESIGN.md §6). Server
 * tetap memverifikasi PIN dan tetap memiliki throttle-nya sendiri.
 */

const SCOPE = 'device'

const UNBLOCKED = { blocked: false, remainingSeconds: 0, failedCount: 0 }

function summary(state, now) {
  return {
    blocked: isPinCooldownBlocked(state, now),
    remainingSeconds: pinCooldownRemainingSeconds(state, now),
    failedCount: Number(state?.failed_count) || 0,
  }
}

async function readState(now) {
  return normalizePinCooldown(await getPinAttempts(SCOPE), SCOPE, now)
}

export async function getPinCooldownState(now = Date.now()) {
  try {
    return summary(await readState(now), now)
  } catch {
    /* Penyimpanan bermasalah: jangan menahan penjualan karena penghitung. */
    return { ...UNBLOCKED, storageUnavailable: true }
  }
}

export async function registerPinFailure(now = Date.now()) {
  try {
    const next = registerPinCooldownFailure(await readState(now), now)
    await savePinAttempts(next)
    return summary(next, now)
  } catch {
    return { ...UNBLOCKED, storageUnavailable: true }
  }
}

export async function registerPinSuccess(now = Date.now()) {
  try {
    const next = resetPinCooldown(await readState(now), SCOPE, now)
    await savePinAttempts(next)
    return summary(next, now)
  } catch {
    return { ...UNBLOCKED, storageUnavailable: true }
  }
}

export default {
  getPinCooldownState,
  registerPinFailure,
  registerPinSuccess,
}
