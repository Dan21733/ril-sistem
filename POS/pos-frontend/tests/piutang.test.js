import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

import { buildPiutangPayload, dueDateFrom, minimalDp } from '../src/utils/piutang.js'

/* DP fase 2 (2 Nov 2026): aturan DP di tablet dan cetakan struk DP/pelunasan. */

test('DP minimal ikut persen per barang, dibulatkan ke atas, tanpa ongkir', () => {
  const items = [
    { unit_price: 1500, quantity: 3 },
    { unit_price: 2501, quantity: 1, min_dp: 80 },
  ]
  assert.equal(minimalDp(items, 50), Math.ceil(2250 + 2000.8))
  assert.equal(minimalDp([], 50), 0)
})

test('payload piutang hanya untuk nota DP atau barang belum dibawa', () => {
  assert.equal(buildPiutangPayload({ total: 10000, dpMode: false, statusBarang: 'sekarang' }), null)
  assert.equal(buildPiutangPayload({ total: 10000, dpMode: false, statusBarang: 'sekarang', delivery: true }), null)
  assert.deepEqual(buildPiutangPayload({ total: 10000, dpMode: false, statusBarang: 'ambil' }), {
    dibayar: 10000, status_barang: 'ambil', jatuh_tempo: null, pelanggan_id: null,
  })
  assert.deepEqual(buildPiutangPayload({
    total: 10000, dpMode: true, dp: '6000', statusBarang: 'sekarang', delivery: true, pelangganId: '4',
    jatuhTempo: '2026-11-16',
  }), { dibayar: 6000, status_barang: 'antar', jatuh_tempo: '2026-11-16', pelanggan_id: 4 })
})

test('jatuh tempo dihitung dari tanggal lokal', () => {
  assert.equal(dueDateFrom(14, new Date(2026, 10, 2, 23, 30)), '2026-11-16')
  assert.equal(dueDateFrom(30, new Date(2026, 11, 15)), '2027-01-14')
})

const context = vm.createContext({ console, Intl })
let receipt = null

async function loadReceipt() {
  if (receipt) {
    return receipt
  }
  const escpos = new vm.SourceTextModule(
    await readFile(new URL('../src/services/printer/escpos.js', import.meta.url), 'utf8'), { context })
  await escpos.link(() => { throw new Error('unexpected import') })
  await escpos.evaluate()
  receipt = new vm.SourceTextModule(
    await readFile(new URL('../src/services/printer/receipt.js', import.meta.url), 'utf8'), { context })
  await receipt.link((specifier) => {
    if (specifier === './escpos') {
      return escpos
    }
    throw new Error(`unexpected import ${specifier}`)
  })
  await receipt.evaluate()
  return receipt
}

function teks(lines) {
  return lines.map((line) => line.text)
}

test('nota DP mencetak DP, kembalian dari DP, sisa tagihan, dan jatuh tempo', async () => {
  const { buildReceiptLines, receiptWidthIssues } = (await loadReceipt()).namespace
  const lines = buildReceiptLines({
    transaction_id: 'T01-000012',
    transaction_time: '2026-11-02T10:00:00+07:00',
    cashier_name: 'Rusdi',
    fulfillment: 'pickup',
    items: [{ item_name: 'Besi TI', variant: '10', quantity: 20, unit_price: 86000, discount: 0, subtotal: 1720000 }],
    item_amount: 1720000,
    shipping_amount: 0,
    total_amount: 1720000,
    payment_method: 'cash',
    payment_amount: 1000000,
    change_amount: 100000,
    cust_name: 'Pak Budi',
    piutang: { dibayar: 900000, status_barang: 'ambil', jatuh_tempo: '2026-11-16' },
  }, { name: 'TOKO' })
  const isi = teks(lines)
  assert.ok(isi.some((t) => t.includes('NOTA DP - BELUM LUNAS')))
  assert.ok(isi.some((t) => t.startsWith('DP CASH') && t.trim().endsWith('900.000')))
  assert.ok(isi.some((t) => t.startsWith('Kembali') && t.trim().endsWith('100.000')))
  assert.ok(isi.some((t) => t.startsWith('SISA TAGIHAN') && t.trim().endsWith('820.000')))
  assert.ok(isi.some((t) => t.startsWith('Jatuh tempo') && t.endsWith('16/11/26')))
  assert.ok(isi.some((t) => t.includes('AMBIL NANTI DI TOKO')))
  assert.equal(receiptWidthIssues(lines).length, 0)
})

test('nota lunas biasa tidak berubah', async () => {
  const { buildReceiptLines } = (await loadReceipt()).namespace
  const isi = teks(buildReceiptLines({
    transaction_id: 'T01-000013', transaction_time: '2026-11-02T10:00:00+07:00', fulfillment: 'pickup',
    items: [{ item_name: 'Palu', variant: 'N/A', quantity: 1, unit_price: 5000, discount: 0, subtotal: 5000 }],
    total_amount: 5000, payment_method: 'cash', payment_amount: 10000, change_amount: 5000,
  }, {}))
  assert.ok(!isi.some((t) => t.includes('SISA') || t.includes('DP')))
  assert.ok(isi.some((t) => t.startsWith('Kembali') && t.trim().endsWith('5.000')))
})

test('struk pelunasan dan tagihan', async () => {
  const { buildPiutangLines, receiptWidthIssues } = (await loadReceipt()).namespace
  const lunas = teks(buildPiutangLines({
    jenis: 'bayar', nomor: 'T01-000012', nama: 'Pak Budi', total: 1720000, dibayar_sebelum: 900000,
    jumlah: 820000, cara: 'bank_transfer', kasir: 'Rusdi', waktu: '2026-11-05T09:00:00+07:00',
  }, { name: 'TOKO' }))
  assert.ok(lunas.some((t) => t.includes('BUKTI PELUNASAN')))
  assert.ok(lunas.some((t) => t.startsWith('Bayar TRANSFER') && t.trim().endsWith('820.000')))
  assert.ok(lunas.some((t) => t.trim() === 'LUNAS'))
  const tagihanLines = buildPiutangLines({
    jenis: 'tagihan', nomor: 'T01-000012', nama: 'Pak Budi', total: 1720000, dibayar_sebelum: 900000,
    jatuh_tempo: '2026-11-16',
  }, { name: 'TOKO' })
  const tagihan = teks(tagihanLines)
  assert.ok(tagihan.some((t) => t.trim() === 'TAGIHAN'))
  assert.ok(tagihan.some((t) => t.startsWith('SISA TAGIHAN') && t.trim().endsWith('820.000')))
  assert.equal(receiptWidthIssues(tagihanLines).length, 0)
})
