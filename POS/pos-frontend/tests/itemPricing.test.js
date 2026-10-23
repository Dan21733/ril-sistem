import assert from 'node:assert/strict'
import test from 'node:test'

import {
  findCartPriceProblem,
  resolveItemPrices,
} from '../src/utils/priceRange.js'
import { floorCodeFor } from '../src/utils/floorCode.js'

/*
 * Aturan harga pemilik (2026-10-07): `max_price` = 0 di master barang berarti
 * pricelist-nya belum diisi, jadi harga jual item itu = `min_price` dan item
 * itu tidak punya floor lagi. Data nyatanya "Aries Pintu - Abu" (min 245.000,
 * max 0) yang sebelumnya tampil Rp 0 di katalog POS.
 *
 * Uji ini menahan satu tempat keputusan saja (resolveItemPrices), karena
 * katalog, keranjang, dan pemeriksaan rentang sama-sama membacanya.
 */

/* Bentuk baris dari GET /api/items/: price = max_price, floor_price = min_price. */
const aries = { item_name: 'Aries Pintu', variant: 'Abu', price: 0, floor_price: 245000 }

test('pricelist yang terisi tetap jadi harga jual dan min_price tetap floor', () => {
  assert.deepEqual(
    resolveItemPrices({ price: 300000, floor_price: 200000 }),
    { price: 300000, floor_price: 200000 },
  )
})

test('max_price 0 menjual di min_price tanpa floor', () => {
  assert.deepEqual(resolveItemPrices(aries), { price: 245000, floor_price: null })
})

test('max_price 0 tidak menyisakan kode floor di keranjang', () => {
  const resolved = resolveItemPrices(aries)

  // floor_price null -> encodeFloorCode mengembalikan '' -> pil Floor hilang.
  assert.equal(resolveItemPrices(aries).floor_price, null)
  assert.equal(floorCodeFor(resolved.floor_price), '')
  assert.equal(floorCodeFor(resolveItemPrices({ price: 0, floor_price: null }).floor_price), '')
})

test('harga pas (min == max) menjual tanpa floor dan tanpa pil Floor', () => {
  // Aries Pintu - Garis Biru: min 240.000 / max 240.000, tidak ada ruang nego.
  const resolved = resolveItemPrices({ price: 240000, floor_price: 240000 })

  assert.deepEqual(resolved, { price: 240000, floor_price: null })
  assert.equal(floorCodeFor(resolved.floor_price), '')

  // Harga pas tidak bisa "di bawah floor"; di atas pricelist tetap ditahan.
  assert.equal(
    findCartPriceProblem([{ price: 240000, floor_price: 240000, unit_price: 240000 }]),
    null,
  )
  assert.deepEqual(
    findCartPriceProblem([{ price: 240000, floor_price: 240000, unit_price: 250000 }]).problem,
    'above',
  )
})

test('max_price NULL tetap berarti master harga belum lengkap', () => {
  // Harga tidak dikarang dari floor; floor-nya sendiri tetap dipublikasikan.
  assert.deepEqual(
    resolveItemPrices({ price: null, floor_price: 200000 }),
    { price: null, floor_price: 200000 },
  )
})

test('keranjang lama berharga 0 tidak lagi dituduh di bawah floor', () => {
  // Baris tersimpan sebelum master harga diisi: harga atap 0, harga jual 0.
  assert.equal(
    findCartPriceProblem([{ item_name: 'Aries Pintu', variant: 'Abu', price: 0, floor_price: 245000, unit_price: 0 }]),
    null,
  )

  // Harga yang benar-benar keluar rentang tetap dilaporkan.
  assert.deepEqual(
    findCartPriceProblem([{ price: 300000, floor_price: 200000, unit_price: 150000 }]).problem,
    'below',
  )
  assert.deepEqual(
    findCartPriceProblem([{ price: 300000, floor_price: 200000, unit_price: 350000 }]).problem,
    'above',
  )
})
