import assert from 'node:assert/strict'
import test from 'node:test'

import {
  REACHABILITY_PROBE_PATH,
  noteServerAnswer,
  noteServerSilence,
  probeServerReachability,
  resetServerReachability,
  serverReachability,
} from '../src/services/api/serverReachability.js'

/*
 * Bukti keterjangkauan hanya berasal dari respons 2xx (keputusan pemilik
 * 2026-10-09). Tes ini memakai jam nyata (bukan waktu palsu) karena cache
 * selidik dihitung dari `Date.now()` di dalam modul.
 */

const answered = () => ({ ok: true, status: 200 })

test('reachability is unknown until something answers', () => {
  resetServerReachability()

  assert.equal(serverReachability({ now: 1000 }), null)

  noteServerAnswer({ now: 1000 })
  assert.equal(serverReachability({ now: 1000 }), true)
  /* Bukti yang sudah lama tidak dianggap bukti. */
  assert.equal(serverReachability({ now: 1000 + 60_000 }), null)

  noteServerSilence({ now: 2000 })
  assert.equal(serverReachability({ now: 2000 }), false)

  noteServerAnswer({ now: 3000 })
  assert.equal(serverReachability({ now: 3000 }), true)
})

test('probe reports reachable only on an actual HTTP answer', async () => {
  resetServerReachability()
  const calls = []
  const original = globalThis.fetch

  try {
    globalThis.fetch = async (url) => { calls.push(String(url)); return answered() }

    assert.equal(await probeServerReachability({ attempts: 1 }), true)
    assert.deepEqual(calls, [REACHABILITY_PROBE_PATH])
    assert.equal(serverReachability(), true)
  } finally {
    globalThis.fetch = original
  }
})

test('a 502 from a dead backend counts as silence, not proof', async () => {
  resetServerReachability()
  let attempts = 0
  const original = globalThis.fetch

  try {
    /*
     * Inilah yang dikembalikan proxy dev saat backend mati: respons HTTP yang
     * sah, tetapi bukan bukti backend hidup. Menghitungnya sebagai jawaban
     * pernah membuka gerbang PIN untuk server yang mati (2026-10-09).
     */
    globalThis.fetch = async () => { attempts += 1; return { ok: false, status: 502 } }

    assert.equal(await probeServerReachability(), false)
    assert.equal(attempts, 2)
    assert.equal(serverReachability(), false)
  } finally {
    globalThis.fetch = original
  }
})

test('probe retries once and reports unreachable when nothing answers', async () => {
  resetServerReachability()
  let attempts = 0
  const original = globalThis.fetch

  try {
    globalThis.fetch = async () => { attempts += 1; throw new Error('no server') }

    assert.equal(await probeServerReachability(), false)
    assert.equal(attempts, 2)
    assert.equal(serverReachability(), false)
  } finally {
    globalThis.fetch = original
  }
})

test('probe result is cached briefly and shared while in flight', async () => {
  resetServerReachability()
  const start = Date.now()
  let calls = 0
  const original = globalThis.fetch

  try {
    globalThis.fetch = async () => { calls += 1; return answered() }

    const [first, second] = await Promise.all([
      probeServerReachability({ now: start }),
      probeServerReachability({ now: start }),
    ])

    assert.equal(first, true)
    assert.equal(second, true)
    assert.equal(calls, 1)

    await probeServerReachability({ now: start + 3000 })
    assert.equal(calls, 1)

    await probeServerReachability({ now: start + 6000 })
    assert.equal(calls, 2)
  } finally {
    globalThis.fetch = original
  }
})

test('a browser reporting offline skips the probe entirely', async () => {
  resetServerReachability()
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  let calls = 0
  const original = globalThis.fetch

  try {
    globalThis.fetch = async () => { calls += 1; return answered() }
    Object.defineProperty(globalThis, 'navigator', {
      value: { onLine: false }, configurable: true,
    })

    assert.equal(await probeServerReachability(), false)
    assert.equal(calls, 0)
    assert.equal(serverReachability(), false)
  } finally {
    globalThis.fetch = original
    if (descriptor) {
      Object.defineProperty(globalThis, 'navigator', descriptor)
    } else {
      delete globalThis.navigator
    }
  }
})
