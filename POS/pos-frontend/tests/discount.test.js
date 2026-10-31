import assert from 'node:assert/strict'
import test from 'node:test'

import { discountedPrice, parsePercent, percentOff } from '../src/utils/discount.js'

test('persen dibaca dari ketikan kasir', () => {
  assert.equal(parsePercent('10'), 10)
  assert.equal(parsePercent('10%'), 10)
  assert.equal(parsePercent(' 2,5 % '), 2.5)
  assert.equal(parsePercent(''), null)
  assert.equal(parsePercent('abc'), null)
  assert.equal(parsePercent('120'), null)
})

test('harga diskon dibulatkan ke atas ke kelipatan pengaturan', () => {
  assert.equal(discountedPrice(67500, 10, 100), 60800)   // 60.750 -> 60.800
  assert.equal(discountedPrice(67500, 10, 1000), 61000)
  assert.equal(discountedPrice(67500, 10, 1), 60750)
  assert.equal(discountedPrice(100000, 25, 100), 75000)  // pas, tidak naik
  assert.equal(discountedPrice(89000, 2.5, 100), 86800)  // 86.775 -> 86.800
  assert.equal(discountedPrice(89000, 0, 100), 89000)
  assert.equal(discountedPrice(99, 0, 100), 99)          // tidak pernah di atas pricelist
  assert.equal(discountedPrice(null, 10, 100), null)
})

test('label persen dari harga sekarang', () => {
  assert.equal(percentOff(100000, 90000), 10)
  assert.equal(percentOff(67500, 60800), 9.9)
  assert.equal(percentOff(100000, 100000), 0)
})
