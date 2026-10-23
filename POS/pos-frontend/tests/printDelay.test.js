import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  DELAYED_PRINT_STATUSES,
  countDelayedNotas,
  countUnprintedNotasToday,
  delayedPrintKey,
  unprintedPrintKey,
  isDelayedPrintJob,
} from '../src/utils/printDelay.js'

/*
 * "Cetak tertunda" hanya untuk pekerjaan yang percobaannya memang sudah gagal
 * (permintaan pemilik, 2026-10-10): pekerjaan baru yang belum pernah dicoba
 * bukan tertunda — normalnya kertas keluar dalam sekejap, jadi menghitungnya
 * membuat pemberitahuan berkedip di setiap penjualan.
 */
test('only jobs with a failed attempt count as delayed', () => {
  /* Status gagal dihitung walau catatannya belum sempat menulis attempts. */
  assert.equal(isDelayedPrintJob({ status: 'retryable', attempts: 0 }), true)
  assert.equal(isDelayedPrintJob({ status: 'blocked', attempts: 0 }), true)

  /* Kiriman yang sedang berjalan/jadwal-ulang: statusnya bisa `pending` lagi,
     tetapi percobaannya sudah tercatat. */
  assert.equal(isDelayedPrintJob({ status: 'pending', attempts: 1 }), true)
  assert.equal(isDelayedPrintJob({ status: 'printing', attempts: 3 }), true)

  /* Pekerjaan baru yang belum pernah dicoba bukan tertunda. */
  assert.equal(isDelayedPrintJob({ status: 'pending', attempts: 0 }), false)
  assert.equal(isDelayedPrintJob({ status: 'printing', attempts: 0 }), false)

  /* Yang sudah keluar tidak pernah tertunda, berapa pun attempts-nya. */
  assert.equal(isDelayedPrintJob({ status: 'printed', attempts: 0 }), false)
  assert.equal(isDelayedPrintJob({ status: 'printed', attempts: 2 }), false)

  assert.equal(isDelayedPrintJob(null), false)
  assert.equal(isDelayedPrintJob(undefined), false)
  assert.equal(isDelayedPrintJob({}), false)
})

/*
 * Inti perhitungannya: satu nota diantar menghasilkan dua pekerjaan cetak — nota
 * dan surat jalan — jadi menghitung baris antrean akan melaporkan "2 nota"
 * untuk satu penjualan. Yang dilaporkan ke kasir harus jumlah nota.
 */
test('jumlah yang dilaporkan adalah nota, bukan pekerjaan cetak', () => {
  const failed = { status: 'retryable', attempts: 1 }

  assert.equal(
    countDelayedNotas([
      { job_id: 'a', sync_id: 'sale-1', ...failed },
      { job_id: 'b', sync_id: 'sale-1', ...failed },
    ]),
    1,
  )

  assert.equal(
    countDelayedNotas([
      { job_id: 'a', sync_id: 'sale-1', ...failed },
      { job_id: 'b', sync_id: 'sale-1', ...failed },
      { job_id: 'c', sync_id: 'sale-2', ...failed },
    ]),
    2,
  )

  /* Nota yang sama dilaporkan sekali walau urutannya terbalik. */
  assert.equal(
    countDelayedNotas([
      { job_id: 'a', sync_id: 'sale-2', ...failed },
      { job_id: 'b', sync_id: 'sale-1', ...failed },
      { job_id: 'c', sync_id: 'sale-2', ...failed },
    ]),
    2,
  )

  /* Pekerjaan tanpa nota tidak bisa disatukan, jadi dihitung sendiri-sendiri. */
  assert.equal(
    countDelayedNotas([
      { job_id: 'a', sync_id: null, ...failed },
      { job_id: 'b', sync_id: null, ...failed },
      { job_id: 'c', sync_id: 'sale-1', ...failed },
    ]),
    3,
  )

  /* Pekerjaan baru yang belum pernah dicoba tidak ikut dihitung. */
  assert.equal(
    countDelayedNotas([
      { job_id: 'a', sync_id: 'sale-1', ...failed },
      { job_id: 'b', sync_id: 'sale-2', status: 'pending', attempts: 0 },
    ]),
    1,
  )

  assert.equal(countDelayedNotas([]), 0)
  assert.equal(countDelayedNotas(null), 0)
  assert.equal(countDelayedNotas([null, undefined]), 0)
})

/*
 * Kunci penutupan pesan cetak: himpunan pekerjaan yang gagal, terurut supaya
 * urutan bacanya tidak memengaruhi. Stabil lintas muat ulang selama
 * pekerjaannya sama; berubah begitu ada nota yang gagal menyusul.
 */
test('dismissal key follows the delayed job set, not the message text', () => {
  const failed = { status: 'retryable', attempts: 1 }

  assert.equal(
    delayedPrintKey([
      { job_id: 'job-2', sync_id: 'sale-1', ...failed },
      { job_id: 'job-1', sync_id: 'sale-1', ...failed },
    ]),
    'job-1|job-2',
  )

  /* Pekerjaan yang belum gagal tidak masuk kunci. */
  assert.equal(
    delayedPrintKey([
      { job_id: 'job-1', sync_id: 'sale-1', ...failed },
      { job_id: 'job-9', sync_id: 'sale-2', status: 'pending', attempts: 0 },
    ]),
    'job-1',
  )

  assert.equal(delayedPrintKey([]), '')
  assert.equal(delayedPrintKey(null), '')
})

test('today count includes unprinted notas sold on the local day, including new jobs', () => {
  const today = new Date(2026, 4, 3, 10, 0, 0)
  const todayIso = today.toISOString()
  const yesterdayIso = new Date(2026, 4, 2, 10, 0, 0).toISOString()

  assert.equal(countUnprintedNotasToday([
    { job_id: 'a', sync_id: 'sale-today-delivery', status: 'pending', attempts: 0, transaction_time: todayIso, created_at: todayIso },
    { job_id: 'b', sync_id: 'sale-today-delivery', status: 'retryable', attempts: 1, transaction_time: todayIso, created_at: todayIso },
    { job_id: 'c', sync_id: 'sale-yesterday', status: 'retryable', attempts: 2, transaction_time: yesterdayIso, created_at: todayIso },
    { job_id: 'd', sync_id: 'sale-created-yesterday', status: 'retryable', attempts: 1, transaction_time: yesterdayIso, created_at: todayIso },
    { job_id: 'e', sync_id: 'sale-created-today', status: 'pending', attempts: 0, transaction_time: todayIso, created_at: yesterdayIso },
    { job_id: 'f', sync_id: 'sale-printed', status: 'printed', attempts: 1, transaction_time: todayIso, created_at: todayIso },
  ], today), 2)

  assert.equal(countUnprintedNotasToday([
    { job_id: 'old', sync_id: 'old-sale', status: 'retryable', attempts: 3, created_at: yesterdayIso },
  ], today), 0)
  assert.equal(countUnprintedNotasToday([
    { job_id: 'old-job-today-sale', sync_id: 'sale', status: 'retryable', attempts: 1,
      transaction_time: todayIso, created_at: yesterdayIso },
  ], today), 1)
  assert.equal(countUnprintedNotasToday([], today), 0)
})

test('dismiss key includes all unprinted jobs, not only failed jobs', () => {
  assert.equal(unprintedPrintKey([
    { job_id: 'failed', status: 'retryable' },
    { job_id: 'new', status: 'pending', attempts: 0 },
    { job_id: 'done', status: 'printed' },
  ]), 'failed|new')
})

/*
 * `DELAYED_PRINT_STATUSES` menduplikasi sebagian `PRINT_STATUS` milik
 * `printJobRepository.js`; keduanya tidak boleh menyimpang diam-diam. Repository
 * tidak bisa dimuat langsung di sini (menarik Dexie + IndexedDB), jadi yang
 * diperiksa teks sumbernya: setiap status "tertunda" harus terdaftar sebagai
 * nilai di `PRINT_STATUS`.
 */
test('delayed statuses stay registered in the print job repository', async () => {
  const source = await readFile(new URL('../src/services/storage/printJobRepository.js', import.meta.url), 'utf8')
  const block = source.match(/PRINT_STATUS\s*=\s*\{([\s\S]*?)\}/)
  assert.ok(block, 'blok PRINT_STATUS harus ada di printJobRepository.js')
  const registered = [...block[1].matchAll(/:\s*'([^']+)'/g)].map((match) => match[1])

  for (const status of DELAYED_PRINT_STATUSES) {
    assert.ok(registered.includes(status), `status tertunda '${status}' harus terdaftar di PRINT_STATUS`)
  }
})
