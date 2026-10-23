import { uploadPaymentProof } from '../api/paymentProofApi'
import { compressProofImage } from '../image/compressProofImage'
import { createUuid } from '../id'
import deviceRepository from '../storage/deviceRepository'
import {
  PROOF_STATUS,
  getPaymentProof,
  linkPaymentProofToTransaction,
  listPaymentProofsToUpload,
  markPaymentProofFailure,
  markPaymentProofUploaded,
  markPaymentProofUploading,
  promoteStuckPaymentProofs,
  resetPaymentProofForRetry,
  savePaymentProof,
} from '../storage/paymentProofRepository'
import { getTransaction } from '../storage/transactionRepository'

/*
 * Antrean unggah bukti transfer (PAYMENT_PROOF_DESIGN.md bagian 7.9).
 *
 * Aturan yang mengikat modul ini:
 * - Nota dulu, bukti menyusul. Bukti hanya dikirim untuk nota yang sudah
 *   `synced`; nota yang belum terkirim bukan kegagalan, hanya menunggu.
 * - Satu siklus dibatasi beberapa berkas supaya unggah foto tidak memblok UI.
 * - Kegagalan sementara memakai backoff; yang permanen (404/400/409) berhenti
 *   dan menunggu manusia.
 */

const MAX_UPLOADS_PER_CYCLE = 3
const BASE_BACKOFF_MS = 5000
const MAX_BACKOFF_MS = 5 * 60 * 1000

/*
 * Target encode ulang setelah 413: jauh di bawah batas server, supaya berkas
 * hasil percobaan kedua tidak ditolak lagi dengan alasan yang sama.
 */
const RETRY_TARGET_BYTES = 2 * 1024 * 1024

/* Single-flight: mount, event `online`, dan checkout bisa memicu hampir bersamaan. */
let flushInFlight = null

let lastSummary = { uploaded: 0, skipped: 0, failed: 0, unauthorized: false }

export function backoffDelayMs(attempts) {
  const attempt = Math.max(1, Number(attempts) || 1)

  return Math.min(BASE_BACKOFF_MS * 2 ** (attempt - 1), MAX_BACKOFF_MS)
}

export function getLastProofFlushSummary() {
  return lastSummary
}

/*
 * Dipanggil `completeTransaction()` tepat setelah nota disimpan lokal, sebelum
 * nota dikirim: kalau aplikasi mati di tengah pengiriman, buktinya sudah
 * terhubung ke `sync_id` dan sisa urusannya bisa dilanjutkan saat dibuka lagi.
 */
export async function attachProofToTransaction(syncId, proofId, { transactionId = null } = {}) {
  if (!syncId || !proofId) {
    return null
  }

  const proof = await getPaymentProof(proofId)

  if (!proof) {
    return null
  }

  return linkPaymentProofToTransaction(proofId, {
    syncId,
    transactionId,
    status:
      proof.upload_status === PROOF_STATUS.UPLOADED ? PROOF_STATUS.UPLOADED : PROOF_STATUS.PENDING,
  })
}

export async function flushPaymentProofs({ deviceToken = null } = {}) {
  if (flushInFlight) {
    return flushInFlight
  }

  flushInFlight = runFlush(deviceToken)
    .catch((error) => {
      lastSummary = { uploaded: 0, skipped: 0, failed: 1, unauthorized: false, error }

      return lastSummary
    })
    .finally(() => {
      flushInFlight = null
    })

  return flushInFlight
}

async function runFlush(deviceToken) {
  const summary = { uploaded: 0, skipped: 0, failed: 0, unauthorized: false }

  if (typeof navigator !== 'undefined' && navigator.onLine === false) {
    lastSummary = summary

    return summary
  }

  /*
   * Pemulihan status menggantung dijalankan sebelum mencari kandidat, supaya
   * bukti yang tertinggal pada status `uploading` ikut dicoba lagi.
   */
  await promoteStuckPaymentProofs()

  const token = deviceToken || (await deviceRepository.getDevice())?.device_token

  if (!token) {
    lastSummary = summary

    return summary
  }

  const candidates = await listPaymentProofsToUpload(MAX_UPLOADS_PER_CYCLE)

  for (const proof of candidates) {
    const nota = proof.sync_id ? await getTransaction(proof.sync_id) : null

    if (!nota || nota.sync_status !== 'synced') {
      /* Menunggu notanya, bukan gagal. */
      summary.skipped += 1

      continue
    }

    const outcome = await sendProof(proof, token)

    if (outcome === 'uploaded') {
      summary.uploaded += 1
    } else if (outcome === 'skipped') {
      summary.skipped += 1
    } else if (outcome === 'unauthorized') {
      summary.unauthorized = true
      summary.failed += 1
    } else {
      summary.failed += 1
    }
  }

  lastSummary = summary

  return summary
}

async function sendProof(proof, deviceToken) {
  await markPaymentProofUploading(proof.proof_id)

  const attempts = (proof.upload_attempts || 0) + 1
  let response

  try {
    response = await uploadPaymentProof({
      deviceToken,
      proofId: proof.proof_id,
      syncId: proof.sync_id,
      capturedAt: proof.captured_at,
      blob: proof.blob,
    })
  } catch {
    await markPaymentProofFailure(proof.proof_id, PROOF_STATUS.RETRYABLE, {
      detail: 'Koneksi ke server gagal saat mengirim bukti.',
      attempts,
      nextAttemptAt: new Date(Date.now() + backoffDelayMs(attempts)).toISOString(),
    })

    return 'failed'
  }

  const { status, body } = response
  const detail = typeof body?.detail === 'string' ? body.detail : ''

  if (status === 201 || status === 200) {
    await markPaymentProofUploaded(proof.proof_id, {
      serverSha256: body?.sha256 ?? null,
      byteSize: body?.byte_size ?? null,
    })

    return 'uploaded'
  }

  if (status === 401) {
    /* Token device ditolak; App.vue yang tahu cara mendaftarkan ulang. */
    await markPaymentProofFailure(proof.proof_id, PROOF_STATUS.RETRYABLE, {
      detail: detail || 'Perangkat tidak terautentikasi.',
      attempts,
      nextAttemptAt: new Date(Date.now() + backoffDelayMs(attempts)).toISOString(),
    })

    return 'unauthorized'
  }

  if (status === 413) {
    return recompressAndRetry(proof, attempts)
  }

  if (status === 409) {
    await markPaymentProofFailure(proof.proof_id, PROOF_STATUS.CONFLICT, {
      detail: detail || 'Bukti dengan ID ini sudah ada dengan isi berbeda.',
      attempts,
      nextAttemptAt: null,
    })

    return 'failed'
  }

  if (status === 400 || status === 404) {
    await markPaymentProofFailure(proof.proof_id, PROOF_STATUS.BLOCKED, {
      detail: detail || 'Bukti belum bisa diunggah untuk nota ini.',
      attempts,
      nextAttemptAt: null,
    })

    return 'failed'
  }

  await markPaymentProofFailure(proof.proof_id, PROOF_STATUS.RETRYABLE, {
    detail: detail || `Server belum menerima bukti (HTTP ${status}).`,
    attempts,
    nextAttemptAt: new Date(Date.now() + backoffDelayMs(attempts)).toISOString(),
  })

  return 'failed'
}

/*
 * 413 bisa benar-benar terjadi sekarang: klien sengaja berhenti dekat batas
 * server (KEPUTUSAN-8), jadi berkas yang lolos di klien bisa saja ditolak kalau
 * ada lapisan lain yang menghitung ukuran berbeda. Kompres ulang sekali dengan
 * target jauh lebih kecil, lalu serahkan ke siklus berikutnya (bagian 7.10).
 */
async function recompressAndRetry(proof, attempts) {
  const smaller = await compressProofImage(proof.blob, {
    targetBytes: RETRY_TARGET_BYTES,
    forceRecompress: true,
  }).catch(() => null)

  if (smaller && smaller.byteSize < proof.byte_size) {
    await savePaymentProof({
      ...proof,
      blob: smaller.blob,
      byte_size: smaller.byteSize,
      width: smaller.width,
      height: smaller.height,
      recompressed: true,
      upload_status: PROOF_STATUS.RETRYABLE,
      detail: 'Foto diperkecil otomatis karena melebihi batas server.',
      upload_attempts: attempts,
      next_attempt_at: new Date(Date.now() + backoffDelayMs(attempts)).toISOString(),
    })

    return 'skipped'
  }

  await markPaymentProofFailure(proof.proof_id, PROOF_STATUS.BLOCKED, {
    detail: 'Foto terlalu besar untuk server. Ambil ulang foto dari jarak lebih dekat.',
    attempts,
    nextAttemptAt: null,
  })

  return 'failed'
}

/* Tombol "Kirim ulang" di riwayat nota: buang jadwal backoff, lalu jalankan antrean. */
export async function retryPaymentProofNow(proofId) {
  await resetPaymentProofForRetry(proofId)

  return flushPaymentProofs()
}

/*
 * 409: server sudah punya `proof_id` ini dengan isi berbeda. Aturan bukti
 * bersifat append-only, jadi jalan keluarnya adalah mengirim sebagai bukti baru
 * dengan `proof_id` baru, bukan menimpa (bagian 6).
 */
export async function resubmitPaymentProofAsNew(proofId) {
  const proof = await getPaymentProof(proofId)

  if (!proof) {
    return null
  }

  const replacement = {
    ...proof,
    proof_id: createUuid(),
    created_at: new Date().toISOString(),
    upload_status: proof.sync_id ? PROOF_STATUS.PENDING : PROOF_STATUS.CAPTURED,
    upload_attempts: 0,
    last_attempt_at: null,
    next_attempt_at: null,
    server_sha256: null,
    uploaded_at: null,
    detail: null,
  }

  await savePaymentProof(replacement)
  await markPaymentProofFailure(proofId, PROOF_STATUS.BLOCKED, {
    detail: 'Digantikan oleh bukti baru.',
    attempts: proof.upload_attempts || 0,
    nextAttemptAt: null,
  })

  return replacement
}

export function summarizeProofSync(summary = lastSummary) {
  if (summary.unauthorized) {
    return 'Bukti transfer belum terkirim: perangkat tidak terautentikasi.'
  }

  if (summary.failed > 0) {
    return `${summary.failed} bukti transfer belum terkirim; akan dicoba lagi otomatis.`
  }

  if (summary.uploaded > 0) {
    return `${summary.uploaded} bukti transfer tersimpan di server.`
  }

  return ''
}

export default {
  attachProofToTransaction,
  flushPaymentProofs,
  retryPaymentProofNow,
  resubmitPaymentProofAsNew,
  getLastProofFlushSummary,
  summarizeProofSync,
  backoffDelayMs,
}
