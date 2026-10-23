import assert from 'node:assert/strict'
import test from 'node:test'

import {
  REACHABILITY_PROBE_PATH,
  resetServerReachability,
} from '../src/services/api/serverReachability.js'
import { SERVER_STATUS, useServerStatus } from '../src/composables/useServerStatus.js'

/*
 * Pil status server adalah indikator saja (permintaan pemilik 2026-10-09):
 * ia membaca bukti `serverReachability` yang sama dengan gerbang PIN dan
 * tidak pernah mengirim antrean. Tes ini memakai fetch gantian seperti
 * `serverReachability.test.js`; lifecycle Vue (`onMounted`) tidak berjalan di
 * luar komponen, jadi `refresh()` dipanggil langsung.
 *
 * `useServerStatus()` diimpor dari `vue` di node_modules; itu disengaja —
 * logika yang diuji adalah pemetaan bukti ke label, bukan render.
 */

const answered = () => ({ ok: true, status: 200 })

async function withFetch(stub, run) {
  const original = globalThis.fetch

  try {
    globalThis.fetch = stub
    await run()
  } finally {
    globalThis.fetch = original
  }
}

test('status awal unknown sampai ada bukti', () => {
  resetServerReachability()

  const pill = useServerStatus()

  assert.equal(pill.state.value, SERVER_STATUS.UNKNOWN)
  assert.equal(pill.label.value, 'Memeriksa…')
  assert.equal(pill.tone.value, 'unknown')
})

test('jawaban 2xx menampilkan Online dan menyapa jalur selidik', async () => {
  resetServerReachability()
  const calls = []

  await withFetch(async (url) => { calls.push(String(url)); return answered() }, async () => {
    const pill = useServerStatus()

    assert.equal(await pill.refresh({ force: true }), true)
    assert.equal(pill.state.value, SERVER_STATUS.ONLINE)
    assert.equal(pill.label.value, 'Online')
    assert.equal(pill.tone.value, 'ok')
    assert.deepEqual(calls, [REACHABILITY_PROBE_PATH])
  })
})

test('502 proxy saat backend mati menampilkan Offline', async () => {
  resetServerReachability()
  let attempts = 0

  await withFetch(async () => { attempts += 1; return { ok: false, status: 502 } }, async () => {
    const pill = useServerStatus()

    assert.equal(await pill.refresh({ force: true }), false)
    assert.equal(pill.state.value, SERVER_STATUS.OFFLINE)
    assert.equal(pill.label.value, 'Offline')
    assert.equal(pill.tone.value, 'offline')
    assert.equal(attempts, 2)
  })
})

test('bukti segar dari checkout dipakai ulang tanpa request tambahan', async () => {
  resetServerReachability()
  let calls = 0

  await withFetch(async () => { calls += 1; return answered() }, async () => {
    const pill = useServerStatus()

    assert.equal(await pill.refresh({ force: true }), true)
    assert.equal(calls, 1)

    /* Bukti masih segar: baca ulang tidak menyapa server lagi. */
    const second = useServerStatus()

    assert.equal(await second.refresh(), true)
    assert.equal(second.state.value, SERVER_STATUS.ONLINE)
    assert.equal(calls, 1)
  })
})
