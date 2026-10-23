import db from './db'

/*
 * Repository bukti transfer (PAYMENT_PROOF_DESIGN.md bagian 7.7).
 *
 * Yang disimpan di sini adalah blob hasil kompres beserta status unggahnya.
 * Urutan pengiriman ke server diatur `services/paymentProof/uploadQueue.js`,
 * bukan modul ini.
 *
 * Status:
 * - `captured`  foto sudah dikompres & tersimpan, nota belum dibuat
 * - `pending`   nota sudah ada di server, bukti siap diunggah
 * - `uploading` sedang dikirim (dipulihkan ke `retryable` saat aplikasi dibuka)
 * - `uploaded`  diterima server
 * - `retryable` gagal sementara, akan dicoba lagi otomatis
 * - `conflict`  409: butuh tindakan manusia
 * - `blocked`   notanya belum/tidak ada di server
 */

export const PROOF_STATUS = {
  CAPTURED: 'captured',
  PENDING: 'pending',
  UPLOADING: 'uploading',
  UPLOADED: 'uploaded',
  RETRYABLE: 'retryable',
  CONFLICT: 'conflict',
  BLOCKED: 'blocked',
}

/* Status yang boleh dicoba ulang otomatis oleh antrean unggah. */
export const PROOF_QUEUE_STATUSES = [PROOF_STATUS.PENDING, PROOF_STATUS.RETRYABLE]

export async function getPaymentProof(proofId) {
  return db.payment_proofs.get(proofId)
}

export async function getPaymentProofsBySyncId(syncId) {
  return db.payment_proofs.where('sync_id').equals(syncId).toArray()
}

export async function getPaymentProofsBySyncIds(syncIds) {
  if (!syncIds?.length) {
    return []
  }

  return db.payment_proofs.where('sync_id').anyOf(syncIds).toArray()
}

export async function savePaymentProof(proof) {
  return db.payment_proofs.put({ ...proof })
}

/*
 * Menghapus bukti yang belum terpakai (difoto sebelum notanya ada, lalu
 * dibatalkan). Bukti yang sudah menempel pada nota — apalagi yang status
 * notanya ambigu — TIDAK boleh dihapus dari jalur mana pun: blob foto tidak
 * bisa dibuat ulang seperti byte cetakan. Alur conflict memakai
 * resubmitPaymentProofAsNew (menandai BLOCKED + membuat proof_id baru),
 * bukan menghapus.
 */
export async function deletePaymentProof(proofId) {
  const proof = await getPaymentProof(proofId)

  if (proof?.sync_id) {
    throw new Error('Bukti yang sudah menempel pada nota tidak boleh dihapus.')
  }

  return db.payment_proofs.delete(proofId)
}

export async function deletePaymentProofsBySyncId(syncId) {
  const proofs = await getPaymentProofsBySyncId(syncId)

  await Promise.all(proofs.map((proof) => deletePaymentProof(proof.proof_id)))

  return proofs.length
}

export async function clearPaymentProofs() {
  return db.payment_proofs.clear()
}

/*
 * Mengaitkan bukti ke nota yang baru dibuat. `sync_id` sengaja nullable: foto
 * dipilih sebelum notanya ada (KEPUTUSAN-4, bagian 7.1).
 */
export async function linkPaymentProofToTransaction(
  proofId,
  { syncId, transactionId = null, status = PROOF_STATUS.PENDING },
) {
  return db.payment_proofs.update(proofId, {
    sync_id: syncId,
    transaction_id: transactionId,
    upload_status: status,
    updated_at: new Date().toISOString(),
  })
}

export async function markPaymentProofUploading(proofId) {
  return db.payment_proofs.update(proofId, {
    upload_status: PROOF_STATUS.UPLOADING,
    last_attempt_at: new Date().toISOString(),
  })
}

export async function markPaymentProofUploaded(
  proofId,
  { serverSha256 = null, byteSize = null } = {},
) {
  const patch = {
    upload_status: PROOF_STATUS.UPLOADED,
    server_sha256: serverSha256,
    detail: null,
    next_attempt_at: null,
    uploaded_at: new Date().toISOString(),
  }

  /* Ukuran final hanya diketahui server; jangan menimpa dengan null. */
  if (byteSize != null) {
    patch.byte_size = byteSize
  }

  return db.payment_proofs.update(proofId, patch)
}

export async function markPaymentProofFailure(
  proofId,
  status,
  { detail = '', attempts = 0, nextAttemptAt = null } = {},
) {
  return db.payment_proofs.update(proofId, {
    upload_status: status,
    detail: detail || null,
    upload_attempts: attempts,
    next_attempt_at: nextAttemptAt,
  })
}

/*
 * Kandidat antrean: hanya `pending`/`retryable` yang waktunya sudah tiba,
 * diurutkan paling tua dulu supaya bukti terlama tidak tertinggal di belakang.
 */
export async function listPaymentProofsToUpload(limit, now = new Date()) {
  const proofs = await db.payment_proofs
    .where('upload_status')
    .anyOf(PROOF_QUEUE_STATUSES)
    .toArray()
  const nowIso = now.toISOString()

  return proofs
    .filter((proof) => !proof.next_attempt_at || proof.next_attempt_at <= nowIso)
    .sort((left, right) =>
      String(left.captured_at || '').localeCompare(String(right.captured_at || '')),
    )
    .slice(0, Math.max(1, limit))
}

/* Semua bukti yang belum diterima server, untuk penanda di riwayat nota. */
export async function listOutstandingPaymentProofs() {
  const proofs = await db.payment_proofs.toArray()

  return proofs
    .filter((proof) => proof.upload_status !== PROOF_STATUS.UPLOADED)
    .sort((left, right) =>
      String(left.captured_at || '').localeCompare(String(right.captured_at || '')),
    )
}

export async function countOutstandingPaymentProofs() {
  const proofs = await listOutstandingPaymentProofs()

  return proofs.length
}

/*
 * Status `uploading` bersifat sementara, seperti `syncing` pada nota: kalau
 * aplikasi mati saat pengiriman berjalan, status itu menggantung dan tidak akan
 * pernah dicoba lagi tanpa dipulihkan di sini.
 */
export async function promoteStuckPaymentProofs() {
  const stuck = await db.payment_proofs
    .where('upload_status')
    .equals(PROOF_STATUS.UPLOADING)
    .toArray()

  await Promise.all(
    stuck.map((proof) =>
      markPaymentProofFailure(proof.proof_id, PROOF_STATUS.RETRYABLE, {
        detail: proof.detail,
        attempts: proof.upload_attempts || 0,
        nextAttemptAt: null,
      }),
    ),
  )

  return stuck.length
}

/* Nota yang ditolak server tidak akan pernah ada, jadi buktinya ditahan. */
export async function markPaymentProofsBlockedBySyncId(syncId, detail) {
  const proofs = await getPaymentProofsBySyncId(syncId)

  await Promise.all(
    proofs
      .filter((proof) => proof.upload_status !== PROOF_STATUS.UPLOADED)
      .map((proof) =>
        markPaymentProofFailure(proof.proof_id, PROOF_STATUS.BLOCKED, {
          detail,
          attempts: proof.upload_attempts || 0,
          nextAttemptAt: null,
        }),
      ),
  )

  return proofs.length
}

/* Tombol "Kirim ulang" di riwayat nota: buang jadwal backoff lalu coba lagi. */
export async function resetPaymentProofForRetry(proofId) {
  const proof = await getPaymentProof(proofId)

  if (!proof) {
    return null
  }

  return savePaymentProof({
    ...proof,
    upload_status: proof.sync_id ? PROOF_STATUS.PENDING : PROOF_STATUS.CAPTURED,
    next_attempt_at: null,
  })
}

export default {
  PROOF_STATUS,
  PROOF_QUEUE_STATUSES,
  getPaymentProof,
  getPaymentProofsBySyncId,
  getPaymentProofsBySyncIds,
  savePaymentProof,
  deletePaymentProof,
  deletePaymentProofsBySyncId,
  clearPaymentProofs,
  linkPaymentProofToTransaction,
  markPaymentProofUploading,
  markPaymentProofUploaded,
  markPaymentProofFailure,
  listPaymentProofsToUpload,
  listOutstandingPaymentProofs,
  countOutstandingPaymentProofs,
  promoteStuckPaymentProofs,
  markPaymentProofsBlockedBySyncId,
  resetPaymentProofForRetry,
}
