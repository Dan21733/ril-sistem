/*
 * Antrean cetak nota (PRINT_PATH_DESIGN.md §5).
 *
 * Urutan yang mengikat saat finalisasi:
 *   PIN -> tulis baris antrean -> tampilkan nota -> cetak.
 * Karena itu job disimpan ke Dexie **sebelum** dikirim: cetak tidak pernah
 * memblokir penyimpanan transaksi, dan gagal cetak tidak pernah membatalkan
 * nota yang sudah sah.
 *
 * Kapan baris antrean itu ditulis ditentukan pemanggil (`App.vue`): setelah PIN
 * dibuktikan perangkat sendiri, atau setelah server menerima transaksinya.
 * Modul ini tidak pernah memutuskan sendiri, dan tidak pernah mencetak apa pun
 * yang belum punya baris antrean.
 *
 * Dua hal yang sengaja TIDAK dilakukan di sini:
 * - Tidak memeriksa `navigator.onLine`. Bridge dan printer ada di LAN; internet
 *   mati bukan alasan untuk menahan kertas. Yang menentukan hanyalah apakah
 *   bridge menjawab.
 * - Tidak menyusun ulang isi nota saat percobaan ulang. Byte dan `job_id`
 *   disimpan apa adanya supaya kiriman kembar bisa dikenali bridge dan printer
 *   tidak mengeluarkan nota dua kali.
 */

import { createUuid } from '../id'
import { buildPrintJob } from './escpos'
import { PrintBridgeError, sendPrintJob } from './printBridge'
import { buildDeliverySlipLines, buildReceiptLines, receiptWidthIssues } from './receipt'
import { getStoreProfile } from './storeProfile'
import {
  PRINT_STATUS,
  getLatestOutstandingPrintJob,
  getPrintJob,
  listOutstandingPrintJobs,
  listPrintJobsToPrint,
  markPrintJobFailure,
  markPrintJobPrinted,
  markPrintJobPrinting,
  promoteStuckPrintJobs,
  prunePrintedPrintJobs,
  savePrintJob,
} from '../storage/printJobRepository'

/* Satu siklus dibatasi supaya nota yang menumpuk tidak memblok UI kasir. */
const MAX_PRINTS_PER_CYCLE = 3
const BASE_BACKOFF_MS = 10000
const MAX_BACKOFF_MS = 5 * 60 * 1000

/* Single-flight: mount, event `online`, dan finalisasi bisa saling menyusul. */
let flushInFlight = null

let lastSummary = { printed: 0, skipped: 0, failed: 0, blocked: 0 }

export function backoffDelayMs(attempts) {
  const attempt = Math.max(1, Number(attempts) || 1)

  return Math.min(BASE_BACKOFF_MS * 2 ** (attempt - 1), MAX_BACKOFF_MS)
}

export function getLastPrintFlushSummary() {
  return lastSummary
}

/*
 * Ringkasan nota yang dipakai UI sebagai pesan. Dipisah dari status nota supaya
 * kasir bisa membedakan "nota sudah tercatat" dari "kertaskah sudah keluar".
 */
export function summarizePrintSync(summary = lastSummary) {
  if (summary.blocked > 0) {
    return 'Penghubung cetak menolak nota ini. Nota tetap sah; hubungi teknisi sebelum mencetak ulang.'
  }

  if (summary.failed > 0) {
    return 'Printer belum menjawab. Nota tetap sah dan akan dicoba cetak lagi otomatis.'
  }

  if (summary.printed > 0) {
    return summary.printed === 1
      ? 'Nota terkirim ke printer.'
      : `${summary.printed} nota terkirim ke printer.`
  }

  return ''
}

/*
 * Baris nota -> satu baris antrean cetak. Dipisah supaya nota dan surat jalan
 * memakai jalur antrean yang sama persis: satu pekerjaan = satu kali printer
 * bekerja.
 */
async function enqueueLinesJob({ lines, sale, jobId = null, kind = 'nota' }) {
  /*
   * Batas 40 kolom dijaga `buildReceiptLines`; ini jaring pengaman terakhir
   * kalau data dari server (nama toko, nama barang) lebih panjang dari dugaan.
   * Encoder tetap mematahkan barisnya, jadi yang tercetak masih bisa dibaca —
   * peringatannya untuk pengembang, bukan alasan menahan nota.
   */
  const issues = receiptWidthIssues(lines)

  if (issues.length > 0 && typeof console !== 'undefined') {
    console.warn(
      'Baris nota melebihi 40 kolom dan akan dipatahkan di kertas:',
      issues.map((line) => line.text),
    )
  }

  const job = buildPrintJob(lines, { jobId: jobId || createUuid() })
  const now = new Date().toISOString()

  return savePrintJob({
    job_id: job.job_id,
    sync_id: sale.sync_id ?? null,
    transaction_id: sale.transaction_id ?? null,
    created_at: now,
    /* Tanggal nota tidak boleh tertukar dengan kapan job masuk antrean. */
    transaction_time: sale.transaction_time ?? null,
    status: PRINT_STATUS.PENDING,
    attempts: 0,
    last_attempt_at: null,
    next_attempt_at: null,
    printed_at: null,
    detail: null,
    /* Disimpan untuk pemeriksaan manusia & tampilan riwayat cetak. */
    kind,
    lines: job.lines,
    bytes: job.bytes,
    /* Ringkasan aman-tampil; bukan payload server. */
    total_amount: sale.total_amount ?? null,
    payment_method: sale.payment_method ?? null,
  })
}

/*
 * Buat baris antrean cetak untuk satu nota. Dipanggil begitu nota tersimpan
 * lokal: sebelum dikirim ke server kalau PIN-nya sudah dibuktikan perangkat ini
 * (agar kasir bisa langsung menyobek nota meski server tidak terjangkau), atau
 * setelah server menerimanya kalau PIN itu belum dikenal perangkat.
 *
 * Transaksi DIANTAR menghasilkan **dua** pekerjaan: nota (2 rangkap) dan surat
 * jalan (1 lembar, `buildDeliverySlipLines`). Keduanya punya `job_id` sendiri
 * supaya bridge tidak melihat surat jalan sebagai kiriman kembar nota, tetapi
 * memakai `sync_id` yang sama supaya membuang nota ikut membuang surat jalannya.
 */
export async function enqueueReceiptPrint({ sale, store = getStoreProfile(), jobId = null } = {}) {
  if (!sale) {
    throw new Error('Pekerjaan cetak tanpa data nota.')
  }

  const receiptJob = await enqueueLinesJob({
    lines: buildReceiptLines(sale, store),
    sale,
    jobId,
  })

  if (sale.fulfillment === 'delivery') {
    await enqueueLinesJob({
      lines: buildDeliverySlipLines(sale, store),
      sale,
      kind: 'nota-antaran',
    })
  }

  return receiptJob
}

export async function flushPrintJobs() {
  if (flushInFlight) {
    return flushInFlight
  }

  flushInFlight = runFlush()
    .catch((error) => {
      lastSummary = { printed: 0, skipped: 0, failed: 1, blocked: 0, error }

      return lastSummary
    })
    .finally(() => {
      flushInFlight = null
    })

  return flushInFlight
}

async function runFlush() {
  const summary = { printed: 0, skipped: 0, failed: 0, blocked: 0 }

  /* Pemulihan sebelum pencarian kandidat, agar job yang menggantung ikut dicoba. */
  await promoteStuckPrintJobs()
  await prunePrintedPrintJobs()

  const candidates = await listPrintJobsToPrint(MAX_PRINTS_PER_CYCLE)

  for (const job of candidates) {
    const outcome = await sendJob(job)

    if (outcome === 'printed') {
      summary.printed += 1
    } else if (outcome === 'skipped') {
      summary.skipped += 1
    } else if (outcome === 'blocked') {
      summary.blocked += 1
    } else {
      summary.failed += 1
    }
  }

  lastSummary = summary

  return summary
}

async function sendJob(job) {
  await markPrintJobPrinting(job.job_id)

  const attempts = (job.attempts || 0) + 1

  try {
    await sendPrintJob({ jobId: job.job_id, bytes: job.bytes })
  } catch (error) {
    const retryable = error instanceof PrintBridgeError ? error.retryable : true

    await markPrintJobFailure(job.job_id, retryable ? PRINT_STATUS.RETRYABLE : PRINT_STATUS.BLOCKED, {
      detail: error.message || 'Pekerjaan cetak gagal dikirim.',
      attempts,
      nextAttemptAt: retryable
        ? new Date(Date.now() + backoffDelayMs(attempts)).toISOString()
        : null,
    })

    return retryable ? 'failed' : 'blocked'
  }

  await markPrintJobPrinted(job.job_id)

  return 'printed'
}

/*
 * Tombol "Cetak ulang": buang jadwal backoff lalu kirim lagi dengan `job_id`
 * yang sama, sehingga bridge mengenalinya sebagai pekerjaan yang sama.
 *
 * Tanpa `jobId`, semua pekerjaan yang belum keluar dicoba — bukan hanya yang
 * terakhir. Satu transaksi diantar menghasilkan dua pekerjaan (nota dan surat
 * jalan), dan kasir yang menekan tombol ini ingin kertasnya keluar, bukan
 * setengahnya.
 */
export async function retryPrintJobNow(jobId = null) {
  const jobs = jobId
    ? [(await getPrintJob(jobId))].filter(Boolean)
    : await listOutstandingPrintJobs()

  if (jobs.length === 0) {
    return { printed: 0, skipped: 0, failed: 0, blocked: 0 }
  }

  for (const job of jobs) {
    await savePrintJob({
      ...job,
      status: PRINT_STATUS.PENDING,
      next_attempt_at: null,
      detail: null,
    })
  }

  return flushPrintJobs()
}

/* Dipakai UI untuk menampilkan tombol cetak ulang hanya saat memang perlu. */
export async function findOutstandingPrintJob() {
  return getLatestOutstandingPrintJob()
}

/*
 * Susun ulang snapshot nota dari jawaban riwayat server, lalu antrekan cetak
 * ulang (tombol "Cetak ulang" di riwayat nota).
 *
 * Kenapa dari jawaban server, bukan dari byte di perangkat: nota lama hidup di
 * server sedangkan byte ESC/POS di perangkat dibuang retensi
 * (`prunePrintedPrintJobs`), jadi nota yang paling perlu dicetak ulang justru
 * yang byte-nya sudah tidak ada.
 *
 * `fulfillment` tidak tersimpan di tabel `transactions` (skema itu dikelola di
 * luar Django — `managed = False`), jadi satu-satunya jejak transaksi diantar
 * yang tersisa adalah ongkir > 0, dan ongkir memang wajib diisi untuk
 * pengantaran di layar kasir. Nota lama dari sebelum aturan itu tercetak
 * sebagai AMBIL SENDIRI tanpa surat jalan: itu pilihan sadar, karena menebak
 * "diantar" untuk nota yang salah lebih berbahaya daripada surat jalan yang
 * harus ditulis tangan sekali.
 */
export function saleFromHistoryDetail(detail = {}) {
  const items = Array.isArray(detail.items) ? detail.items : []
  const shipping = Number(detail.shipping_amount ?? 0)

  return {
    sync_id: detail.sync_id ?? null,
    transaction_id: detail.transaction_id ?? null,
    transaction_time: detail.transaction_time ?? null,
    cashier_name: detail.worker_name ?? '',
    cust_name: detail.cust_name ?? null,
    phone_no: detail.phone_no ?? null,
    /* Diteruskan supaya cetak ulang menandai nota void/retur/refund. */
    refund_status: detail.refund_status ?? 'none',
    fulfillment: shipping > 0 ? 'delivery' : 'pickup',
    items,
    item_amount: Number(detail.item_amount ?? 0),
    shipping_amount: shipping,
    discount_amount: items.reduce((sum, item) => sum + (Number(item.discount) || 0), 0),
    total_amount: Number(detail.total_amount ?? 0),
    payment_method: detail.payment_method ?? '',
    payment_amount: Number(detail.payment_amount ?? 0),
    change_amount: Number(detail.change_amount ?? 0),
  }
}

/*
 * `job_id` sengaja baru: mencetak ulang berarti memang ingin kertas keluar
 * lagi, jadi bridge tidak boleh mengenalinya sebagai kiriman kembar job lama.
 */
export async function reprintReceiptFromHistory(detail, { store = getStoreProfile() } = {}) {
  const job = await enqueueReceiptPrint({ sale: saleFromHistoryDetail(detail), store })
  const summary = await flushPrintJobs()

  return { job, summary }
}

export default {
  backoffDelayMs,
  enqueueReceiptPrint,
  flushPrintJobs,
  retryPrintJobNow,
  findOutstandingPrintJob,
  getLastPrintFlushSummary,
  summarizePrintSync,
  saleFromHistoryDetail,
  reprintReceiptFromHistory,
}
