import assert from 'node:assert/strict'
import test from 'node:test'

import { historyButtonNotice } from '../src/utils/historyButtonNotice.js'

/*
 * Tombol "Riwayat Nota" membawa dua penanda tunggu: angka nota belum
 * tersinkron ke server dan angka nota yang dijual hari ini, belum tercetak. Yang diuji di sini
 * adalah keputusan tampilannya — berapa yang ditampilkan, kalimat penuh untuk
 * `title`/`aria-label`, dan penanda mana yang muncul — bukan render komponennya.
 *
 * Aturan "pekerjaan mana yang dihitung tertunda" tinggal di
 * `utils/printDelay.js` dan diuji di `tests/printDelay.test.js`, bukan di sini.
 */

test('tombol riwayat menyebut kanal yang benar saat keduanya menunggu', () => {
  const notice = historyButtonNotice({ queuedCount: 2, unprintedCount: 3 })

  assert.equal(notice.queued, 2)
  assert.equal(notice.unprinted, 3)
  assert.equal(notice.hasQueued, true)
  assert.equal(notice.hasPrintAlert, true)
  assert.equal(notice.showStatuses, true)
  assert.equal(
    notice.title,
    '2 nota belum tersinkron ke server; 3 nota hari ini belum tercetak. Buka riwayat untuk memeriksa.',
  )
  assert.equal(notice.ariaLabel, 'Riwayat Nota, 2 nota belum tersinkron, 3 nota hari ini belum tercetak')
})

test('antrean saja tetap memakai kalimat lama, tanpa menambah berita cetak', () => {
  const notice = historyButtonNotice({ queuedCount: 1 })

  assert.equal(notice.hasPrintAlert, false)
  assert.equal(notice.hasQueued, true)
  assert.equal(
    notice.title,
    '1 nota belum tersinkron ke server. Buka riwayat untuk mengirim ulang.',
  )
  assert.equal(notice.ariaLabel, 'Riwayat Nota, 1 nota belum tersinkron')
})

test('cetak saja tetap memberitahu tanpa lencana antrean', () => {
  const notice = historyButtonNotice({ unprintedCount: 1 })

  assert.equal(notice.hasQueued, false)
  assert.equal(notice.hasPrintAlert, true)
  assert.equal(notice.showStatuses, true)
  assert.equal(
    notice.title,
    '1 nota hari ini belum tercetak di printer. Buka riwayat untuk mencetak ulang.',
  )
  assert.equal(notice.ariaLabel, 'Riwayat Nota, 1 nota hari ini belum tercetak')
})

test('tanpa pekerjaan tertunda tombolnya kembali jadi tombol biasa', () => {
  const notice = historyButtonNotice({})

  assert.equal(notice.queued, 0)
  assert.equal(notice.unprinted, 0)
  assert.equal(notice.showStatuses, false)
  assert.equal(notice.hasPrintAlert, false)
  assert.equal(notice.title, 'Riwayat Nota')
  assert.equal(notice.ariaLabel, 'Riwayat Nota')
})

test('angka yang tidak masuk akal tidak pernah jadi pemberitahuan palsu', () => {
  for (const value of [undefined, null, NaN, -3, 'bukan angka', 0]) {
    const notice = historyButtonNotice({ queuedCount: value, unprintedCount: value })

    assert.equal(notice.queued, 0, String(value))
    assert.equal(notice.unprinted, 0, String(value))
    assert.equal(notice.showStatuses, false, String(value))
    assert.equal(notice.title, 'Riwayat Nota', String(value))
  }

  /* Bilangan pecahan dibulatkan ke bawah, dan angka berbentuk teks tetap jalan. */
  assert.equal(historyButtonNotice({ unprintedCount: 2.9 }).unprinted, 2)
  assert.equal(historyButtonNotice({ queuedCount: '4' }).queued, 4)
})

