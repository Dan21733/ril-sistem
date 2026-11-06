import test from 'node:test'
import assert from 'node:assert/strict'
import { letakPapan, papanAktif, tekanTombol } from '../src/utils/numpad.js'

test('angka pertama mengganti isi lama, berikutnya menambah', () => {
  assert.equal(tekanTombol('Rp 86.000', '9', { segar: true }), '9')
  assert.equal(tekanTombol('9', '0'), '90')
  assert.equal(tekanTombol('Rp 300.000', '000'), 'Rp 300.000000')
})

test('hapus: satu karakter, atau kosongkan kalau isian baru dibuka', () => {
  assert.equal(tekanTombol('Rp 300.000', 'hapus'), 'Rp 300.00')
  assert.equal(tekanTombol('12', 'hapus', { segar: true }), '')
  assert.equal(tekanTombol('', 'hapus'), '')
})

test('PIN dibatasi enam digit', () => {
  assert.equal(tekanTombol('12345', '6', { maks: 6 }), '123456')
  assert.equal(tekanTombol('123456', '7', { maks: 6 }), '123456')
  assert.equal(tekanTombol('Rp 1.000', '5', { maks: 5 }), 'Rp 1.0005')
  assert.equal(tekanTombol('Rp 1.000', '55', { maks: 4 }), 'Rp 1.000')
})

test('koma hanya untuk isian desimal dan hanya sekali', () => {
  assert.equal(tekanTombol('12', ',', { desimal: true }), '12,')
  assert.equal(tekanTombol('12,5', ',', { desimal: true }), '12,5')
  assert.equal(tekanTombol('12', ','), '12')
})

const layar = { lebar: 1007, tinggi: 601 }
const ukuran = { lebar: 252, tinggi: 322 }

test('tablet mendatar: papan di kiri isian (menutup kolom yang sedang tidak dipakai)', () => {
  const r = letakPapan({ left: 520, top: 400, bottom: 446, height: 46 }, ukuran, layar)
  assert.equal(r.sisi, 'kiri')
  assert.equal(r.left, 520 - 12 - 252)
  assert.ok(r.top >= 8 && r.top + 322 <= 601 - 8)
})

test('tablet tegak: isian di tepi kiri, papan di atas bila isian di separuh bawah, di bawah bila di separuh atas', () => {
  const tegak = { lebar: 601, tinggi: 1007 }
  assert.equal(letakPapan({ left: 35, top: 300, bottom: 346, height: 46 }, ukuran, tegak).sisi, 'bawah')
  assert.equal(letakPapan({ left: 35, top: 600, bottom: 646, height: 46 }, ukuran, tegak).sisi, 'atas')
  const atas = letakPapan({ left: 35, top: 850, bottom: 896, height: 46 }, ukuran, tegak)
  assert.equal(atas.sisi, 'atas')
  assert.equal(atas.top, 850 - 12 - 322)
})

test('papan aktif di layar sentuh atau dipaksa lewat localStorage', () => {
  const win = (kasar, simpan) => ({
    matchMedia: () => ({ matches: kasar }),
    localStorage: { getItem: () => simpan },
  })
  assert.equal(papanAktif(win(true, null)), true)
  assert.equal(papanAktif(win(false, null)), false)
  assert.equal(papanAktif(win(false, '1')), true)
  assert.equal(papanAktif(win(true, '0')), false)
})
