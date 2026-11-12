import assert from 'node:assert/strict'
import test from 'node:test'

/*
 * Cetak langsung lewat Ril (2026-11-12): halaman kasir di dalam Ril menyerahkan byte struk ke
 * RilTablet; jawabannya datang lewat window.__rilJawab supaya layar tidak membeku.
 */
function pasangRil({ status, cetak }) {
  const dipanggil = []
  globalThis.RilTablet = {
    bisaCetak: () => true,
    statusNanti: (id) => setTimeout(() => globalThis.__rilJawab(id, status), 5),
    cetakNanti: (id, jobId, b64) => {
      dipanggil.push({ jobId, b64 })
      setTimeout(() => globalThis.__rilJawab(id, cetak), 5)
    },
  }
  return dipanggil
}

test('di dalam Ril: cetak tidak lewat penghubung, status dari Ril', async () => {
  const { sendPrintJob, checkPrintBridgeHealth, PRINTER_HEALTH_STATE } = await import('../src/services/printer/printBridge.js')
  const dipanggil = pasangRil({
    status: { printer_status: 'ok', printer: '192.168.1.50:9100', printer_name: 'TM-U220B (langsung dari Ril)', printer_detail: 'Printer pindah alamat' },
    cetak: { ok: true, duplicate: false },
  })
  const fetchImpl = () => { throw new Error('tidak boleh lewat fetch') }
  const hasil = await sendPrintJob({ jobId: 'J1', bytes: new Uint8Array([27, 64, 65]), fetchImpl })
  assert.deepEqual(hasil, { ok: true, status: 200, duplicate: false })
  assert.deepEqual(dipanggil, [{ jobId: 'J1', b64: 'G0BB' }])
  const s = await checkPrintBridgeHealth({ fetchImpl })
  assert.equal(s.state, PRINTER_HEALTH_STATE.CONNECTED)
  assert.equal(s.printerAddress, '192.168.1.50:9100')

  pasangRil({ status: { printer_status: 'unreachable', printer_detail: 'Printer tidak ditemukan.' },
    cetak: { ok: false, pesan: 'Printer tidak ditemukan.', retryable: true } })
  await assert.rejects(sendPrintJob({ jobId: 'J2', bytes: [1], fetchImpl }), (e) => e.retryable === true)
  assert.equal((await checkPrintBridgeHealth({ fetchImpl })).state, PRINTER_HEALTH_STATE.PRINTER_UNREACHABLE)
  delete globalThis.RilTablet
})
