/*
 * Kebijakan cooldown PIN lokal (keputusan pemilik 2026-10-08).
 *
 * Tanpa cooldown, PIN enam digit bisa ditebak tanpa batas saat perangkat
 * offline: throttle server (HTTP 429) hanya berlaku kalau servernya bisa
 * dihubungi. Isi modul ini murni hitungan — tanpa Dexie, tanpa jaringan —
 * supaya bisa diuji sebagai fungsi dan tidak menyimpan apa pun tentang PIN.
 *
 * Yang TIDAK disimpan: PIN, potongannya, atau hash-nya. Ruang enam digit hanya
 * 10^6, jadi berkas hash apa pun di perangkat adalah alat bantu penebak, bukan
 * penghambat (OFFLINE_SYNC_DESIGN.md §6). Yang dihitung hanya jumlah kegagalan
 * per perangkat beserta waktu blokirnya.
 */

export const PIN_COOLDOWN_POLICY = {
  /* Kegagalan ke-5 menutup percobaan berikutnya. */
  threshold: 5,

  /* Blokir pertama, lalu berlipat dua setiap kegagalan berikutnya. */
  baseSeconds: 30,

  /* Batas atas: menahan tebakan tanpa mengunci till berjam-jam. */
  maxSeconds: 600,

  /*
   * Kegagalan lama tidak ditumpuk: salah ketik kemarin tidak boleh membuat
   * kasir hari ini terkunci.
   */
  failureWindowMs: 15 * 60_000,
}

/*
 * Jam mundur tidak boleh membuka blokir: `last_seen_at` adalah waktu tertinggi
 * yang pernah terlihat, jadi memundurkan jam tablet hanya membuat hitungan
 * berhenti, bukan blokirnya hilang.
 */
function effectiveNow(now, state) {
  const current = Number.isFinite(now) ? now : Date.now()
  const seen = Number(state?.last_seen_at)

  return Number.isFinite(seen) && seen > current ? seen : current
}

export function createPinCooldownState(scope, now = Date.now()) {
  return {
    scope,
    failed_count: 0,
    last_failure_at: 0,
    blocked_until: 0,
    last_seen_at: effectiveNow(now, null),
  }
}

/* Baris tersimpan (atau baris hilang) dijadikan bentuk yang bisa dihitung. */
export function normalizePinCooldown(state, scope, now = Date.now()) {
  const current = effectiveNow(now, state)

  if (!state) {
    return createPinCooldownState(scope, current)
  }

  return {
    scope: state.scope || scope,
    failed_count: Math.max(0, Number(state.failed_count) || 0),
    last_failure_at: Number(state.last_failure_at) || 0,
    blocked_until: Number(state.blocked_until) || 0,
    last_seen_at: current,
  }
}

export function pinCooldownRemainingSeconds(state, now = Date.now()) {
  const current = effectiveNow(now, state)
  const until = Number(state?.blocked_until) || 0

  if (until <= current) {
    return 0
  }

  return Math.ceil((until - current) / 1000)
}

export function isPinCooldownBlocked(state, now = Date.now()) {
  return pinCooldownRemainingSeconds(state, now) > 0
}

export function registerPinCooldownFailure(state, now = Date.now()) {
  const normalized = normalizePinCooldown(state, state?.scope, now)
  const current = normalized.last_seen_at

  const quiet = !isPinCooldownBlocked(normalized, current)
    && current - normalized.last_failure_at > PIN_COOLDOWN_POLICY.failureWindowMs

  const failed = (quiet ? 0 : normalized.failed_count) + 1

  let blockedUntil = normalized.blocked_until

  if (failed >= PIN_COOLDOWN_POLICY.threshold) {
    const step = failed - PIN_COOLDOWN_POLICY.threshold

    const seconds = Math.min(
      PIN_COOLDOWN_POLICY.baseSeconds * 2 ** step,
      PIN_COOLDOWN_POLICY.maxSeconds,
    )

    blockedUntil = current + seconds * 1000
  }

  return {
    scope: normalized.scope,
    failed_count: failed,
    last_failure_at: current,
    blocked_until: blockedUntil,
    last_seen_at: current,
  }
}

export function resetPinCooldown(state, scope, now = Date.now()) {
  return createPinCooldownState(scope || state?.scope, now)
}

export default {
  PIN_COOLDOWN_POLICY,
  createPinCooldownState,
  isPinCooldownBlocked,
  normalizePinCooldown,
  pinCooldownRemainingSeconds,
  registerPinCooldownFailure,
  resetPinCooldown,
}
