import assert from 'node:assert/strict'
import test from 'node:test'

import {
  PIN_COOLDOWN_POLICY,
  isPinCooldownBlocked,
  normalizePinCooldown,
  pinCooldownRemainingSeconds,
  registerPinCooldownFailure,
  resetPinCooldown,
} from '../src/utils/pinCooldown.js'

/* Satu titik waktu tetap supaya hitungannya bisa dibaca langsung. */
const T0 = 1_700_000_000_000

/* Baris tersimpan: kegagalan terakhir baru saja terjadi pada `at`. */
const seeded = (count, at = T0) => ({
  scope: 'device',
  failed_count: count,
  last_failure_at: at,
  blocked_until: 0,
  last_seen_at: at,
})

test('block only starts at the fifth failure', () => {
  let state = normalizePinCooldown(undefined, 'device', T0)

  for (let attempt = 1; attempt < PIN_COOLDOWN_POLICY.threshold; attempt += 1) {
    const now = T0 + attempt * 1000

    state = registerPinCooldownFailure(state, now)

    assert.equal(state.failed_count, attempt)
    assert.equal(isPinCooldownBlocked(state, now), false)
  }

  state = registerPinCooldownFailure(state, T0 + 5000)

  assert.equal(state.failed_count, PIN_COOLDOWN_POLICY.threshold)
  assert.equal(pinCooldownRemainingSeconds(state, T0 + 5000), PIN_COOLDOWN_POLICY.baseSeconds)
})

test('each further failure doubles the block up to the cap', () => {
  let state = seeded(PIN_COOLDOWN_POLICY.threshold - 1)
  const waits = []

  for (let failure = PIN_COOLDOWN_POLICY.threshold; failure <= 12; failure += 1) {
    const now = T0 + failure * 1000

    state = registerPinCooldownFailure(state, now)

    waits.push(pinCooldownRemainingSeconds(state, now))
  }

  assert.deepEqual(waits.slice(0, 5), [30, 60, 120, 240, 480])
  assert.equal(waits.at(-1), PIN_COOLDOWN_POLICY.maxSeconds)
})

test('a rolled-back clock cannot clear a running block', () => {
  const blocked = registerPinCooldownFailure(seeded(PIN_COOLDOWN_POLICY.threshold - 1), T0)
  const rolledBack = T0 - 6 * 60 * 60_000

  assert.equal(isPinCooldownBlocked(blocked, rolledBack), true)
  assert.equal(pinCooldownRemainingSeconds(blocked, rolledBack), PIN_COOLDOWN_POLICY.baseSeconds)
})

test('an expired block and an old failure do not pile up', () => {
  const blocked = registerPinCooldownFailure(seeded(PIN_COOLDOWN_POLICY.threshold - 1), T0)
  const longAfter = T0 + PIN_COOLDOWN_POLICY.failureWindowMs + 1000

  assert.equal(isPinCooldownBlocked(blocked, longAfter), false)

  const next = registerPinCooldownFailure(blocked, longAfter)

  assert.equal(next.failed_count, 1)
  assert.equal(isPinCooldownBlocked(next, longAfter), false)
})

test('failures inside the window still count, and success clears them', () => {
  const blocked = registerPinCooldownFailure(seeded(PIN_COOLDOWN_POLICY.threshold - 1), T0)
  const soon = T0 + 31_000

  const again = registerPinCooldownFailure(blocked, soon)

  assert.equal(again.failed_count, PIN_COOLDOWN_POLICY.threshold + 1)
  assert.equal(pinCooldownRemainingSeconds(again, soon), 60)

  const cleared = resetPinCooldown(again, 'device', soon)

  assert.equal(cleared.failed_count, 0)
  assert.equal(cleared.blocked_until, 0)
  assert.equal(isPinCooldownBlocked(cleared, soon), false)
})
