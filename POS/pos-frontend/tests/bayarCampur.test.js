import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

import { splitCampur, splitPayment } from '../src/utils/pecahNota.js'
import { metodeBayarRapi } from '../src/utils/piutang.js'

/*
 * Bayar campur (keputusan pemilik 2026-11-12): tunai + transfer (+ EDC) dalam
 * satu nota, kembalian selalu tunai (boleh dari transfer berlebih), struk
 * menulis rincian per metode, dan cara bayar yang tampil diatur pemilik.
 */

test('nota pecah: nota ke-2 dibayar pas dari non-tunai dulu, kembalian di nota pertama', () => {
  const hasil = splitCampur([30000, 20000], [
    { method: 'cash', amount: 40000 }, { method: 'bank_transfer', amount: 15000 },
  ])
  assert.deepEqual(hasil[1], { payment: 20000, change: 0, payments: [
    { method: 'bank_transfer', amount: 15000 }, { method: 'cash', amount: 5000 }] })
  assert.deepEqual(hasil[0], { payment: 35000, change: 5000, payments: [{ method: 'cash', amount: 35000 }] })
  const total = hasil.reduce((a, h) => a + h.payment - h.change, 0)
  assert.equal(total, 50000)
  assert.deepEqual(splitPayment([10000], { method: 'campur', payments: [{ method: 'bank_transfer', amount: 12000 }] }),
    [{ payment: 12000, change: 2000, payments: [{ method: 'bank_transfer', amount: 12000 }] }])
})

test('daftar cara bayar: salinan lama = semua, kosong = tunai saja, urutan tetap', () => {
  assert.deepEqual(metodeBayarRapi(undefined), ['cash', 'bank_transfer', 'edc', 'campur'])
  assert.deepEqual(metodeBayarRapi(['campur', 'cash']), ['cash', 'campur'])
  assert.deepEqual(metodeBayarRapi([]), ['cash'])
})

async function loadReceipt() {
  const context = vm.createContext({ console, Intl })
  const escpos = new vm.SourceTextModule(
    await readFile(new URL('../src/services/printer/escpos.js', import.meta.url), 'utf8'), { context })
  await escpos.link(() => { throw new Error('unexpected import') })
  await escpos.evaluate()
  const receipt = new vm.SourceTextModule(
    await readFile(new URL('../src/services/printer/receipt.js', import.meta.url), 'utf8'), { context })
  await receipt.link((s) => { if (s === './escpos') return escpos; throw new Error(s) })
  await receipt.evaluate()
  return receipt.namespace
}

test('struk bayar campur menulis rincian per metode dan kembalian tunai', async () => {
  const { buildReceiptLines, receiptWidthIssues } = await loadReceipt()
  const lines = buildReceiptLines({
    transaction_id: 'T01-000009', transaction_time: '2026-11-12T10:00:00+07:00', cashier_name: 'Rusdi',
    fulfillment: 'pickup',
    items: [{ item_name: 'Semen', variant: '50kg', quantity: 10, unit_price: 70000, discount: 0, subtotal: 700000 }],
    item_amount: 700000, shipping_amount: 0, total_amount: 700000,
    payment_method: 'campur', payment_amount: 710000, change_amount: 10000,
    payments: [{ method: 'bank_transfer', amount: 500000 }, { method: 'cash', amount: 210000 }],
  }, {})
  const text = lines.map((l) => l.text).join('\n')
  assert.match(text, /Bayar TRANSFER\s+500\.000/)
  assert.match(text, /Bayar CASH\s+210\.000/)
  assert.match(text, /Kembali\s+10\.000/)
  assert.equal(receiptWidthIssues(lines).length, 0)
})
