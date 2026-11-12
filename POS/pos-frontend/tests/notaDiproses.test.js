import assert from 'node:assert/strict'
import test from 'node:test'

import { buildPiutangPayload, masalahBayar, perluTempo, statusNota, teleponBaku } from '../src/utils/piutang.js'

/* Aturan bayar per jenis nota di tablet (SOP pemilik 12 Nov 2026). */

const dasar = { dpMode: true, total: 10000, minimal: 5000, pelangganTempo: '', telepon: '', daftarHitam: [] }

test('status nota dari pilihan barang dan pengambilan', () => {
  assert.equal(statusNota('sekarang', false), 'bawa')
  assert.equal(statusNota('sekarang', true), 'antar')
  assert.equal(statusNota('ambil', true), 'ambil')
  assert.equal(statusNota('pesan', false), 'pesan')
})

test('lunas selalu boleh', () => {
  for (const status of ['bawa', 'antar', 'ambil', 'pesan']) {
    assert.equal(masalahBayar({ ...dasar, status, dpMode: false }), '')
  }
})

test('barang dibawa: bayar sebagian hanya pelanggan tempo', () => {
  assert.match(masalahBayar({ ...dasar, status: 'bawa', dp: '9000' }), /wajib lunas/)
  assert.equal(masalahBayar({ ...dasar, status: 'bawa', dp: '0', pelangganTempo: '3' }), '')
  assert.equal(perluTempo({ status: 'bawa', dpMode: true, dp: '9000', minimal: 5000 }), true)
})

test('titip di toko wajib lunas', () => {
  assert.match(masalahBayar({ ...dasar, status: 'ambil', dp: '9000' }), /Titip di toko wajib lunas/)
})

test('pesanan: DP minimal, kurang dari itu hanya pelanggan tempo', () => {
  assert.equal(masalahBayar({ ...dasar, status: 'pesan', dp: '5000' }), '')
  assert.match(masalahBayar({ ...dasar, status: 'pesan', dp: '4000' }), /DP pesanan minimal Rp5\.000/)
  assert.equal(masalahBayar({ ...dasar, status: 'pesan', dp: '4000', pelangganTempo: '3' }), '')
  assert.equal(perluTempo({ status: 'pesan', dpMode: true, dp: '5000', minimal: 5000 }), false)
})

test('COD: boleh 0, wajib No. HP, nomor daftar hitam wajib lunas', () => {
  assert.match(masalahBayar({ ...dasar, status: 'antar', dp: '0' }), /COD wajib No\. HP/)
  assert.equal(masalahBayar({ ...dasar, status: 'antar', dp: '0', telepon: '0812-3456-789' }), '')
  const hitam = { ...dasar, status: 'antar', dp: '2000', telepon: '+62 812 3456 789', daftarHitam: ['08123456789'] }
  assert.match(masalahBayar(hitam), /daftar hitam COD/)
  assert.equal(masalahBayar({ ...hitam, dpMode: false }), '')
  /* COD tidak pernah menawarkan pelanggan tempo: tidak boleh jadi utang. */
  assert.equal(perluTempo({ status: 'antar', dpMode: true, dp: '0', minimal: 5000 }), false)
})

test('nomor HP dibakukan seperti di master', () => {
  assert.equal(teleponBaku('+62 812-3456-789'), '08123456789')
  assert.equal(teleponBaku('123'), '')
})

test('COD tidak mengirim jatuh tempo', () => {
  const cod = buildPiutangPayload({ total: 10000, dpMode: true, dp: '0', statusBarang: 'sekarang', delivery: true, jatuhTempo: '2026-11-26' })
  assert.equal(cod.status_barang, 'antar')
  assert.equal(cod.jatuh_tempo, null)
  const pesan = buildPiutangPayload({ total: 10000, dpMode: true, dp: '5000', statusBarang: 'pesan', delivery: false, jatuhTempo: '2026-11-26' })
  assert.equal(pesan.jatuh_tempo, '2026-11-26')
})
