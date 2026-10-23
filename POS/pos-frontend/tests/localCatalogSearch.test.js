import assert from 'node:assert/strict'
import test from 'node:test'

import {
  LOCAL_PAGE_SIZE,
  compareCatalogRows,
  describeCatalogAge,
  matchCatalogQuery,
  normalizeCatalogText,
  searchCatalog,
} from '../src/utils/localCatalogSearch.js'

/* 35 baris: cukup untuk menguji dua halaman pada ukuran halaman server (30). */
const rows = Array.from({ length: 35 }, (_, index) => ({
  item_name: `Item ${String(index + 1).padStart(2, '0')}`,
  variant: index % 2 === 0 ? 'Besar' : 'Kecil',
}))

test('an empty query returns every row, ordered by name then variant', () => {
  const result = searchCatalog([...rows].reverse(), '')

  assert.equal(result.count, 35)
  assert.equal(result.results.length, LOCAL_PAGE_SIZE)
  assert.equal(result.next, 2)
  assert.deepEqual(result.results.slice(0, 2).map((row) => row.item_name), ['Item 01', 'Item 02'])
})

test('the last page reports no next page', () => {
  const result = searchCatalog(rows, '', { page: 2 })

  assert.equal(result.results.length, 5)
  assert.equal(result.next, null)
  assert.equal(result.results.at(-1).item_name, 'Item 35')
})

test('query matches name and variant, ignoring case and padding', () => {
  assert.equal(matchCatalogQuery({ item_name: 'Kunci Bulat', variant: 'Besar' }, 'kunci'), true)
  assert.equal(matchCatalogQuery({ item_name: 'Kunci Bulat', variant: 'Besar' }, '  BESAR '), true)
  assert.equal(matchCatalogQuery({ item_name: 'Kunci Bulat', variant: 'Besar' }, 'solasi'), false)
  /* Nama/varian hilang bukan alasan melempar error saat kasir mengetik. */
  assert.equal(matchCatalogQuery({}, 'kunci'), false)
  assert.equal(normalizeCatalogText(undefined), '')
})

test('ordering is a stable code-unit comparison, not locale-dependent', () => {
  const left = { item_name: 'A', variant: 'B' }
  const right = { item_name: 'A', variant: 'C' }

  assert.equal(compareCatalogRows(left, right), -1)
  assert.equal(compareCatalogRows(left, left), 0)
  assert.equal(compareCatalogRows(right, left), 1)
})

test('catalog age is described in words the cashier can read', () => {
  assert.equal(describeCatalogAge(30 * 1000), 'baru saja')
  assert.equal(describeCatalogAge(5 * 60 * 1000), '5 menit lalu')
  assert.equal(describeCatalogAge(3 * 60 * 60 * 1000), '3 jam lalu')
  assert.equal(describeCatalogAge(4 * 24 * 60 * 60 * 1000), '4 hari lalu')
  assert.equal(describeCatalogAge(Number.NaN), 'belum diketahui')
})

test('urutan alami: angka dibaca sebagai angka, sama dengan server dan master', () => {
  const items = ['10 MM', '2 MM', '8 MM', '12 mm'].map((variant) => ({ item_name: 'Mata Bor', variant }))
  const { results } = searchCatalog(items, '')
  assert.deepEqual(results.map((row) => row.variant), ['2 MM', '8 MM', '10 MM', '12 mm'])
  assert.deepEqual(
    searchCatalog([{ item_name: 'CW 415', variant: 'A' }, { item_name: 'Cw 53', variant: 'A' }], '').results
      .map((row) => row.item_name),
    ['Cw 53', 'CW 415'],
  )
})

test('pencarian offline per kata: nama + varian, urutan bebas, dan alias (QA 18 Okt 2026)', () => {
  const items = [
    { item_name: 'AM 152 151', variant: 'Black', alias: 'cat batu alam pelapis batu' },
    { item_name: 'AM 152 151', variant: 'Clear Golssy', alias: 'cat batu alam pelapis batu' },
    { item_name: 'Palu', variant: 'Besar', alias: 'martil penokok' },
    { item_name: 'Pelapis Batu Premium', variant: 'N/A' },
  ]
  assert.deepEqual(searchCatalog(items, 'AM 152 151 black').results.map((row) => row.variant), ['Black'])
  assert.deepEqual(searchCatalog(items, 'black am').results.map((row) => row.variant), ['Black'])
  assert.deepEqual(searchCatalog(items, 'martil').results.map((row) => row.item_name), ['Palu'])
  /* Cocok lewat nama tampil sebelum yang cocok lewat alias. */
  assert.deepEqual(searchCatalog(items, 'pelapis batu').results.map((row) => row.item_name),
    ['Pelapis Batu Premium', 'AM 152 151', 'AM 152 151'])
  assert.equal(matchCatalogQuery(items[2], 'martil besar'), true)
  assert.equal(matchCatalogQuery(items[2], 'martil kecil'), false)
})
