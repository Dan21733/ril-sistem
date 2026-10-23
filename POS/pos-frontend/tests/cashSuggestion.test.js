import assert from 'node:assert/strict'
import test from 'node:test'

import {
  CASH_DENOMINATIONS,
  CASH_UNIT,
  isMinimumCombination,
  minimalCashPayments,
  suggestCashAmounts,
} from '../src/utils/cashSuggestion.js'

const TARGET = 417000

/* Pseudocode pemilik, ditulis ulang sebagai brute force independen (maks
   `maxPieces` lembar agar enumerasinya finite). */
function bruteForceMinimums(target, maxPieces) {
  const found = new Set()
  const combo = []
  const walk = (start, sum) => {
    if (sum >= target) {
      let minimum = true
      for (const piece of combo) {
        if (sum - piece >= target) {
          minimum = false
          break
        }
      }
      if (minimum) {
        found.add(sum)
      }
    }
    if (combo.length >= maxPieces) {
      return
    }
    for (let i = start; i < CASH_DENOMINATIONS.length; i += 1) {
      combo.push(CASH_DENOMINATIONS[i])
      walk(i, sum + CASH_DENOMINATIONS[i])
      combo.pop()
    }
  }
  walk(0, 0)
  return [...found].sort((a, b) => a - b)
}

test('isMinimumCombination mirrors the owner pseudocode check', () => {
  assert.equal(isMinimumCombination(TARGET, [100000, 100000, 100000, 100000, 10000, 5000, 2000]), true)
  // 4x100rb + 1000 mencapai target, tetapi tanpa 1000 pun sudah sampai.
  assert.equal(isMinimumCombination(400000, [100000, 100000, 100000, 100000, 1000]), false)
  assert.equal(isMinimumCombination(TARGET, []), false)
  assert.equal(isMinimumCombination(TARGET, [100000, 100000]), false)
})

test('minimalCashPayments(417000) equals the brute-forced pseudocode set', () => {
  const fromCode = minimalCashPayments(TARGET).map((row) => row.amount)
  assert.deepEqual(fromCode, bruteForceMinimums(TARGET, 12))
  assert.ok(fromCode.length > 0)
})

test('every returned combo is a verified minimum (no cheaper subset)', () => {
  for (const target of [TARGET, 400000, 300000, 135000]) {
    for (const { amount, combo } of minimalCashPayments(target)) {
      assert.equal(isMinimumCombination(target, combo), true)
      assert.equal(
        combo.reduce((sum, piece) => sum + piece, 0),
        amount,
      )
      for (const piece of combo) {
        assert.ok(CASH_DENOMINATIONS.includes(piece))
        assert.equal(piece % CASH_UNIT, 0)
      }
    }
  }
})

test('suggestCashAmounts(300000) is exactly 300000, 301000, 310000', () => {
  assert.deepEqual(
    suggestCashAmounts(300000).map((row) => row.amount),
    [300000, 301000, 310000],
  )
})

test('350000 and 400000 are impossible minimums for a 300000 total', () => {
  const amounts = suggestCashAmounts(300000, { max: 50 }).map((row) => row.amount)
  assert.ok(!amounts.includes(350000))
  assert.ok(!amounts.includes(400000))
})

test('suggestCashAmounts(417000) keeps uang pas + jangkar 50rb/100rb', () => {
  assert.deepEqual(
    suggestCashAmounts(TARGET).map((row) => row.amount),
    [417000, 420000, 450000, 500000],
  )
})

test('slot sisa diisi nominal paling bulat, bukan yang terkecil', () => {
  /*
   * Kasus pemilik (2026-10-09, max = 4): semesta 307.000 memang enam nominal.
   * Empat yang tampil = uang pas, kedua jangkar, lalu 310.000 (paling bulat) —
   * bukan 308.000 yang hanya 1.000 di atas uang pas alias tombol kembar.
   */
  assert.deepEqual(
    minimalCashPayments(307000).map((row) => row.amount),
    [307000, 308000, 310000, 320000, 350000, 400000],
  )
  assert.deepEqual(
    suggestCashAmounts(307000).map((row) => row.amount),
    [307000, 310000, 350000, 400000],
  )
})

test('max 3 untuk layar kecil mempertahankan uang pas + kedua jangkar', () => {
  /*
   * Ambang responsif komponen (`max-width: 900px`, permintaan pemilik
   * 2026-10-14): tiga slot tepat menampung uang pas + jangkar 50rb/100rb,
   * jadi slot "paling bulat" yang gugur lebih dulu.
   */
  assert.deepEqual(
    suggestCashAmounts(417000, { max: 3 }).map((row) => row.amount),
    [417000, 450000, 500000],
  )
  assert.deepEqual(
    suggestCashAmounts(307000, { max: 3 }).map((row) => row.amount),
    [307000, 350000, 400000],
  )
})

test('anchors survive the max cap for other totals too', () => {
  assert.deepEqual(
    suggestCashAmounts(25000).map((row) => row.amount),
    [25000, 30000, 50000, 100000],
  )
  assert.deepEqual(
    suggestCashAmounts(101000).map((row) => row.amount),
    [101000, 110000, 150000, 200000],
  )
})

test('suggested amounts are always a capped subset of the exhaustive minimums', () => {
  for (const target of [TARGET, 417500, 135000, 25000, 101000, 87000, 300000]) {
    const exhaustive = minimalCashPayments(target).map((row) => row.amount)
    const shown = suggestCashAmounts(target).map((row) => row.amount)
    assert.ok(shown.length <= 4)
    assert.deepEqual([...shown].sort((a, b) => a - b), shown)
    assert.ok(shown.includes(exhaustive[0]))
    for (const amount of shown) {
      assert.ok(exhaustive.includes(amount))
      assert.ok(amount >= target)
    }
    const anchor50 = exhaustive.find((amount) => amount % 50000 === 0)
    const anchor100 = exhaustive.find((amount) => amount % 100000 === 0)
    assert.ok(shown.includes(anchor50))
    assert.ok(shown.includes(anchor100))
  }
})
