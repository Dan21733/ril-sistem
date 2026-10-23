import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

/*
 * Data pelanggan di kertas (keputusan pemilik 2026-10-14,
 * CUSTOMER_DESIGN.md): struk memuat baris Pelanggan + No. HP, surat jalan
 * terisi otomatis, dan nota lama (cust_name NULL) mencetak seperti dulu —
 * tanpa baris pelanggan dan surat jalan tulis tangan.
 *
 * `receipt.js` memakai impor tanpa ekstensi (`./escpos`) yang hanya
 * dimengerti Vite, jadi modul nyata dimuat lewat VM seperti
 * `checkout.test.js`, bukan impor langsung.
 */
const context = vm.createContext({ console, Intl })
let receipt = null

async function loadReceipt() {
  if (receipt) {
    return receipt
  }
  const escposSource = await readFile(
    new URL('../src/services/printer/escpos.js', import.meta.url), 'utf8',
  )
  const escpos = new vm.SourceTextModule(escposSource, { context })
  await escpos.link(() => { throw new Error('unexpected import') })
  await escpos.evaluate()
  const receiptSource = await readFile(
    new URL('../src/services/printer/receipt.js', import.meta.url), 'utf8',
  )
  receipt = new vm.SourceTextModule(receiptSource, { context })
  await receipt.link((specifier) => {
    if (specifier === './escpos') {
      return escpos
    }
    throw new Error(`unexpected import ${specifier}`)
  })
  await receipt.evaluate()
  return receipt
}

function sale(overrides = {}) {
  return {
    transaction_id: 'T01-000001',
    transaction_time: '2026-10-14T10:00:00+07:00',
    cashier_name: 'Rusdi',
    fulfillment: 'delivery',
    items: [{
      item_name: 'Kunci Bulat', variant: 'Default', quantity: 2,
      unit_price: 17000, discount: 0, subtotal: 34000,
    }],
    item_amount: 34000,
    shipping_amount: 10000,
    total_amount: 44000,
    payment_method: 'cash',
    payment_amount: 50000,
    change_amount: 6000,
    cust_name: 'Budi Santoso',
    phone_no: '081234567890',
    ...overrides,
  }
}

test('struk memuat nama + No. HP pelanggan dalam 40 kolom', async () => {
  const { buildReceiptLines, receiptWidthIssues } = (await loadReceipt()).namespace
  const lines = buildReceiptLines(sale(), {})
  const text = lines.map((line) => line.text)
  assert.ok(text.some((row) => row.includes('Budi Santoso')), 'baris Pelanggan ada')
  assert.ok(text.some((row) => row.includes('081234567890')), 'baris No. HP ada')
  assert.equal(receiptWidthIssues(lines).length, 0, 'tanpa baris melebihi 40 kolom')
})

test('struk nota lama tanpa pelanggan tidak berubah', async () => {
  const { buildReceiptLines } = (await loadReceipt()).namespace
  const text = buildReceiptLines(sale({ cust_name: null, phone_no: null }), {})
    .map((line) => line.text)
  assert.ok(!text.some((row) => row.includes('Pelanggan')), 'tanpa baris Pelanggan')
  assert.ok(!text.some((row) => row.includes('No. HP')), 'tanpa baris No. HP')
})

test('surat jalan terisi nama + No. HP otomatis', async () => {
  const { buildDeliverySlipLines } = (await loadReceipt()).namespace
  const text = buildDeliverySlipLines(sale(), {}).map((line) => line.text)
  assert.ok(
    text.some((row) => row.startsWith('Kepala YTH : Budi Santoso')),
    'nama terisi',
  )
  assert.ok(
    text.some((row) => row.startsWith('No Hp : 081234567890')),
    'No. HP terisi',
  )
})

test('surat jalan nota lama kembali ke garis tulis tangan', async () => {
  const { buildDeliverySlipLines } = (await loadReceipt()).namespace
  const text = buildDeliverySlipLines(sale({ cust_name: null, phone_no: null }), {})
    .map((line) => line.text)
    .join('\n')
  assert.match(text, /Kepala YTH : _+/, 'nama tulis tangan')
  assert.match(text, /No Hp : _+/, 'No. HP tulis tangan')
})
