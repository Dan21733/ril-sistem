import db from './db'

function normalizeCashierName(name) {
  return String(name ?? '').trim().toLocaleLowerCase()
}

export function isUnresolvedTransaction(record) {
  if (record.sync_status === 'synced') {
    return false
  }

  // Old rejected rows do not prove that an earlier attempt or local print failed.
  if (record.sync_status === 'rejected') {
    return record.checkout_version !== 1 || record.local_final || record.outcome_unknown
  }
  return true
}

export async function getTransaction(syncId) {
  return db.transactions.get(syncId)
}

export async function getTransactionByTransactionId(transactionId) {
  return db.transactions.where('transaction_id').equals(transactionId).first()
}

export async function getUnresolvedTransactions() {
  return (await db.transactions.toArray()).filter(isUnresolvedTransaction)
}

export async function saveTransaction(transaction) {
  // Antrean multi-nota (opsi A slice 1): nota baru boleh lahir walau antrean
  // belum kosong. Duplikat sync_id dan bukti terpakai tetap ditolak di sini;
  // perlindungan tab-ganda dijaga kunci checkout di App.vue.
  return db.transaction('rw', db.transactions, db.payment_proofs, async () => {
    if (await getTransaction(transaction.sync_id)) {
      throw new Error('Nota tersimpan tidak boleh ditimpa.')
    }

    if (transaction.proof_id) {
      const proof = await db.payment_proofs.get(transaction.proof_id)
      if (!proof || (proof.sync_id && proof.sync_id !== transaction.sync_id)) {
        throw new Error('Bukti transfer tidak tersedia atau sudah dipakai nota lain.')
      }
      await db.payment_proofs.update(proof.proof_id, {
        sync_id: transaction.sync_id,
        transaction_id: transaction.transaction_id,
        upload_status: proof.upload_status === 'uploaded' ? 'uploaded' : 'pending',
        updated_at: new Date().toISOString(),
      })
    } else if (transaction.payload.payment_method === 'bank_transfer') {
      throw new Error('Bukti transfer wajib tersimpan sebelum nota dibuat.')
    }

    return db.transactions.add({
      ...transaction,
      cashier_name_normalized: normalizeCashierName(transaction.cashier_name),
    })
  })
}

export async function updateTransactionStatus(syncId, syncStatus, details = {}) {
  if (!['pending', 'unknown', 'synced', 'rejected', 'review', 'conflict'].includes(syncStatus)) {
    throw new Error('Status nota tidak sah.')
  }
  const allowedDetails = [
    'server_status', 'rejection_detail', 'last_http_status', 'retry_after_until',
  ]
  if (Object.keys(details).some((key) => !allowedDetails.includes(key))) {
    throw new Error('Isi nota yang sudah disimpan tidak boleh diubah.')
  }
  if (syncStatus === 'synced' && !['new', 'idempotent'].includes(details.server_status)) {
    throw new Error('Konfirmasi final server tidak lengkap.')
  }

  return db.transaction('rw', db.transactions, async () => {
    const record = await getTransaction(syncId)
    const allowedTransitions = {
      draft: ['syncing', 'rejected'],
      pending: ['unknown', 'synced', 'rejected', 'conflict'],
      unknown: ['syncing', 'unknown', 'review', 'conflict'],
      syncing: ['unknown', 'synced', 'review', 'conflict'],
    }
    if (!record || !allowedTransitions[record.sync_status]?.includes(syncStatus)) {
      throw new Error('Perubahan status nota tidak sah.')
    }
    if (syncStatus === 'synced'
      && (details.server_status !== 'new' && details.server_status !== 'idempotent')) {
      throw new Error('Hanya konfirmasi server yang dapat menandai nota synced.')
    }

    // A later rejection cannot rule out a commit during an earlier lost response.
    if (syncStatus === 'rejected' && (record.local_final || record.outcome_unknown)) {
      throw new Error('Nota final atau ambigu wajib diperiksa, bukan ditolak pasti.')
    }

    await db.transactions.update(syncId, {
      ...details,
      sync_status: syncStatus,
      ...(syncStatus === 'synced' ? {
        outcome_unknown: false,
        server_final: true,
        original_pin: null,
        approver_pin: null,
      } : {}),
      ...(syncStatus === 'rejected' ? { original_pin: null, approver_pin: null } : {}),
    })
  })
}

export async function beginTransactionSubmission(syncId) {
  return db.transaction('rw', db.transactions, async () => {
    const record = await getTransaction(syncId)
    if (!record || !['draft', 'pending', 'unknown'].includes(record.sync_status)
      || !/^\d{6}$/.test(record.original_pin || '')) {
      throw new Error('Nota tidak siap dikirim. Periksa antrean sebelum melanjutkan.')
    }

    // Write BEFORE fetch: closing the tablet at any later point is ambiguous.
    await db.transactions.update(syncId, {
      sync_status: 'syncing',
      outcome_unknown: true,
      submission_started_at: new Date().toISOString(),
      submission_attempts: (record.submission_attempts || 0) + 1,
    })
    return record
  })
}

export async function recordDefiniteAttemptFailure(
  syncId,
  syncStatus,
  details = {},
  { previousOutcomeUnknown = false } = {},
) {
  // A complete response resolves only this attempt; it cannot resolve an earlier lost reply.
  if (!['pending', 'rejected'].includes(syncStatus)) {
    throw new Error('Hasil percobaan tidak dapat ditutup dengan status ini.')
  }
  const allowedDetails = [
    'server_status', 'rejection_detail', 'last_http_status', 'retry_after_until',
  ]
  if (Object.keys(details).some((key) => !allowedDetails.includes(key))) {
    throw new Error('Detail status nota tidak sah.')
  }
  if (syncStatus === 'rejected'
    && (!details.rejection_detail || typeof details.rejection_detail !== 'string')) {
    throw new Error('Penolakan harus menyimpan alasan server.')
  }

  return db.transaction('rw', db.transactions, async () => {
    const record = await getTransaction(syncId)
    if (!record || record.sync_status !== 'syncing' || !record.outcome_unknown
      || previousOutcomeUnknown || (syncStatus === 'rejected' && record.local_final)) {
      throw new Error('Hasil percobaan sebelumnya belum diketahui.')
    }
    await db.transactions.update(syncId, {
      ...details,
      sync_status: syncStatus,
      outcome_unknown: false,
      ...(syncStatus === 'rejected' ? { original_pin: null, approver_pin: null } : {}),
    })
  })
}

export async function getPendingTransactions() {
  return db.transactions.where('sync_status').equals('pending').toArray()
}

export async function getTransactionsByCashierName(cashierName) {
  return db.transactions.where('cashier_name_normalized')
    .equals(normalizeCashierName(cashierName)).toArray()
}

export async function getSyncingTransactions() {
  return db.transactions.where('sync_status').equals('syncing').toArray()
}

export async function getConflictTransactions() {
  return db.transactions.where('sync_status').equals('conflict').toArray()
}

export async function getRejectedTransactions() {
  return db.transactions.where('sync_status').equals('rejected').toArray()
}

export async function getFailedTransactions() {
  return db.transactions.where('sync_status').equals('failed').toArray()
}

export async function countTransactionsByStatus(syncStatus) {
  return db.transactions.where('sync_status').equals(syncStatus).count()
}

export async function deleteTransaction(syncId) {
  return db.transaction('rw', db.transactions, async () => {
    const record = await getTransaction(syncId)
    if (record && (record.sync_status !== 'draft' || record.local_final
      || record.outcome_unknown || record.submission_attempts)) {
      throw new Error('Hanya draft yang belum final dan belum dikirim boleh dibuang.')
    }
    return db.transactions.delete(syncId)
  })
}

export default {
  getTransaction,
  getTransactionByTransactionId,
  getUnresolvedTransactions,
  saveTransaction,
  updateTransactionStatus,
  beginTransactionSubmission,
  recordDefiniteAttemptFailure,
  getPendingTransactions,
  getTransactionsByCashierName,
  getSyncingTransactions,
  getConflictTransactions,
  getRejectedTransactions,
  getFailedTransactions,
  countTransactionsByStatus,
  deleteTransaction,
}
