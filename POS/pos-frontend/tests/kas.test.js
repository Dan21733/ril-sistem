import assert from 'node:assert/strict'
import test from 'node:test'

import { kunciDariJawaban, kunciDariStatus, readKunci, rememberKunci } from '../src/utils/kunciKasir.js'
import { gerakProblem, kasBanner, mergeGerak, readKasStatus, rememberKasStatus, tutupHariIni, tutupProblem } from '../src/utils/kas.js'

/* Kas laci di tablet (3 Nov 2026). */

test('pengingat buka kas hanya di tablet kas yang kasnya belum dibuka', () => {
  assert.equal(kasBanner(null), null)
  assert.equal(kasBanner({ boleh_kas: false, sesi: null }), null)
  assert.equal(kasBanner({ boleh_kas: true, sesi: { id: 'x' } }), null)
  assert.equal(kasBanner({ boleh_kas: true, sesi: null }).action, 'Buka kas')
  /* Sudah ditutup sore ini: tidak diingatkan buka lagi sampai besok. */
  const sore = new Date(2026, 10, 3, 17, 0)
  const tutup = { boleh_kas: true, sesi: null, tutup_terakhir: new Date(2026, 10, 3, 16, 30).toISOString() }
  assert.equal(kasBanner(tutup, sore), null)
  assert.equal(tutupHariIni(tutup, sore), true)
  assert.equal(kasBanner(tutup, new Date(2026, 10, 4, 7, 0)).action, 'Buka kas')
})

test('tutup kas ditahan selama offline atau ada antrean di tablet ini', () => {
  const status = { boleh_kas: true, sesi: { id: 's' } }
  assert.match(tutupProblem({ status: { ...status, sesi: null }, online: true, localPending: 0, uangSisa: '0' }), /belum dibuka/)
  assert.match(tutupProblem({ status: { ...status, boleh_kas: false }, online: true, localPending: 0, uangSisa: '0' }), /tablet kas/)
  assert.match(tutupProblem({ status, online: false, localPending: 0, uangSisa: '0' }), /koneksi/)
  assert.match(tutupProblem({ status, online: true, localPending: 2, uangSisa: '0' }), /2 catatan/)
  assert.match(tutupProblem({ status, online: true, localPending: 0, uangSisa: '' }), /uang sisa/)
  assert.equal(tutupProblem({ status, online: true, localPending: 0, uangSisa: '0' }), '')
  /* Sisa minimal per toko: tidak boleh mengambil sampai sisa di bawah minimal. */
  const minimal = { ...status, sisa_minimal: 400000 }
  assert.match(tutupProblem({ status: minimal, online: true, localPending: 0, uangSisa: '300000', diambil: '500000' }), /minimal/)
  assert.equal(tutupProblem({ status: minimal, online: true, localPending: 0, uangSisa: '400000', diambil: '500000' }), '')
  /* Uang di laci memang kurang dari minimal: semuanya ditinggal, boleh. */
  assert.equal(tutupProblem({ status: minimal, online: true, localPending: 0, uangSisa: '250000', diambil: '' }), '')
})

test('uang keluar wajib kategori; "Lainnya" wajib keterangan; tambah modal tanpa kategori', () => {
  assert.match(gerakProblem({ jenis: 'keluar', jumlah: '', kategori: 'Makan & minum' }), /jumlah/)
  assert.match(gerakProblem({ jenis: 'keluar', jumlah: '5000', kategori: '' }), /Pilih/)
  assert.match(gerakProblem({ jenis: 'keluar', jumlah: '5000', kategori: 'Lainnya', keterangan: ' ' }), /keterangan/)
  assert.equal(gerakProblem({ jenis: 'keluar', jumlah: '5000', kategori: 'Lainnya', keterangan: 'tali' }), '')
  assert.equal(gerakProblem({ jenis: 'tambah_modal', jumlah: '50000', kategori: '' }), '')
})

test('daftar uang keluar menggabung server dan antrean tanpa dobel', () => {
  const server = [{ gerak_id: 'a', waktu: '2026-11-03T09:00:00Z', jumlah: 1 }]
  const lokal = [
    { gerak_id: 'a', created_at: '2026-11-03T09:00:00Z', status: 'sent' },
    { gerak_id: 'b', created_at: '2026-11-03T08:00:00Z', status: 'pending', jumlah: 2 },
    { gerak_id: 'c', created_at: '2026-11-03T10:00:00Z', status: 'sent' },
  ]
  const hasil = mergeGerak(server, lokal)
  assert.equal(hasil.length, 2)
  assert.equal(hasil[0].gerak_id, 'b')
  assert.equal(hasil[0].local, true)
})

test('status kas disalin untuk dipakai offline', () => {
  const isi = new Map()
  const storage = { setItem: (k, v) => isi.set(k, v), getItem: (k) => isi.get(k) ?? null }
  rememberKasStatus({ boleh_kas: true, sesi: null }, storage)
  assert.equal(readKasStatus(storage).boleh_kas, true)
  assert.equal(readKasStatus({ getItem: () => '{rusak' }), null)
})

test('kunci kasir dari status kas / jawaban 423, diingat untuk offline', () => {
  assert.equal(kunciDariStatus({ boleh_kas: true }), null)
  assert.deepEqual(kunciDariStatus({ dikunci: true, kunci_alasan: 'stock opname' }), { dikunci: true, alasan: 'stock opname' })
  assert.deepEqual(kunciDariStatus({ dikunci: false }), { dikunci: false, alasan: '' })
  assert.equal(kunciDariJawaban(500, {}), null)
  assert.equal(kunciDariJawaban(423, { alasan: 'opname' }).dikunci, true)
  const isi = new Map()
  const storage = { setItem: (k, v) => isi.set(k, v), getItem: (k) => isi.get(k) ?? null }
  assert.equal(readKunci(storage).dikunci, false)
  rememberKunci({ dikunci: true, alasan: 'x' }, storage)
  assert.equal(readKunci(storage).dikunci, true)
})
