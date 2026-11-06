import test from 'node:test'
import assert from 'node:assert/strict'
import { angkaJumlah, bacaJumlah, hargaPerSatuan, hargaTersimpan, langkahJumlah, teksJumlah } from '../src/utils/satuan.js'

const fiber = { satuan: 'm', kelipatan: 10 }
const paku = { satuan: 'kg', kelipatan: 10 }
const biasa = {}

test('jumlah tersimpan tampil dalam satuan asli', () => {
  assert.equal(teksJumlah(15, fiber), '1,5 m')
  assert.equal(teksJumlah(10, fiber), '1 m')
  assert.equal(teksJumlah(8, fiber), '0,8 m')
  assert.equal(teksJumlah(25, paku), '2,5 kg')
  assert.equal(teksJumlah(3, biasa), '3')
  assert.equal(angkaJumlah(7, { satuan: 'm', kelipatan: 100 }), '0,07')
})

test('ketikan kasir dibaca jadi jumlah tersimpan bulat', () => {
  assert.equal(bacaJumlah('1,5', fiber), 15)
  assert.equal(bacaJumlah('1.5', fiber), 15)
  assert.equal(bacaJumlah('0,8', fiber), 8)
  assert.equal(bacaJumlah('2', fiber), 20)
  assert.equal(bacaJumlah('0,04', fiber), 1, 'minimal satu kelipatan')
  assert.equal(bacaJumlah('abc', fiber), null)
  assert.equal(bacaJumlah('3', biasa), 3)
})

test('harga per satuan <-> tersimpan', () => {
  assert.equal(hargaPerSatuan(2500, fiber), 25000)
  assert.equal(hargaTersimpan(25000, fiber), 2500)
  assert.equal(hargaTersimpan(24500, fiber), 2450)
  assert.equal(hargaPerSatuan(95000, biasa), 95000)
  assert.equal(langkahJumlah(fiber), 10)
  assert.equal(langkahJumlah(biasa), 1)
})

test('contoh pemilik: 1,5 m x Rp 25.000/m = Rp 37.500 dari angka tersimpan', () => {
  const jumlah = bacaJumlah('1,5', fiber)
  const harga = hargaTersimpan(25000, fiber)
  assert.equal(jumlah * harga, 37500)
})
