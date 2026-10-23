import assert from 'node:assert/strict'
import test from 'node:test'

import {
  digitsOnly,
  formatRupiahInput,
  preventNonNumericInput,
} from '../src/utils/rupiahInput.js'

/*
 * Kolom uang: yang tampil bertitik ribuan, yang disimpan digit polos.
 * Dipakai kolom uang diterima + ongkir di keranjang dan jumlah uang kembali di
 * form void/retur/refund; payload ke server tidak boleh pernah berisi "70.000".
 */

test('digits are grouped by thousands', () => {
  assert.equal(formatRupiahInput('70000'), '70.000')
  assert.equal(formatRupiahInput('700'), '700')
  assert.equal(formatRupiahInput('1234567'), '1.234.567')
  assert.equal(formatRupiahInput(245000), '245.000')
})

test('already formatted and prefixed input stays parseable', () => {
  // Tempel dari struk/kalkulator: "Rp 70.000" tetap berarti 70000.
  assert.equal(digitsOnly('Rp 70.000'), '70000')
  assert.equal(formatRupiahInput('Rp 70.000'), '70.000')
  assert.equal(formatRupiahInput('1.234.567'), '1.234.567')
  assert.equal(formatRupiahInput('12a3'), '123')
})

test('empty and non-numeric input never invents a zero', () => {
  assert.equal(formatRupiahInput(''), '')
  assert.equal(formatRupiahInput(null), '')
  assert.equal(formatRupiahInput(undefined), '')
  assert.equal(formatRupiahInput('abc'), '')
  assert.equal(digitsOnly(null), '')
})

test('keyboard guard blocks non-digits but keeps editing keys', () => {
  const keydown = (key) => {
    let prevented = false
    preventNonNumericInput({ key, preventDefault: () => { prevented = true } })
    return prevented
  }

  assert.equal(keydown('7'), false)
  assert.equal(keydown('a'), true)
  assert.equal(keydown('.'), true)
  assert.equal(keydown('-'), true)
  assert.equal(keydown('Backspace'), false)
  assert.equal(keydown('ArrowLeft'), false)
  assert.equal(keydown('Tab'), false)
})
