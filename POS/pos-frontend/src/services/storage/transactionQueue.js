import { getUnresolvedTransactions } from './transactionRepository'

/*
 * Antrean multi-nota offline (opsi A slice 1): daftar baca-saja nota yang
 * belum selesai, tertua dulu. Tidak menulis apa pun — pengiriman tetap lewat
 * jalur submit per nota dengan PIN asli tersimpan.
 *
 * Status UI `retryable` bukan nilai Dexie; ia dipetakan dari baris-baris ini.
 */
const QUEUED_STATUSES = ['draft', 'pending', 'syncing', 'unknown']

function queueLabel(record) {
  if (record.sync_status === 'unknown' || record.sync_status === 'syncing') {
    return 'Hasil belum diketahui'
  }
  return 'Belum terkirim'
}

export function toQueuedRow(record) {
  const receipt = record.receipt_sale || {}
  const payload = record.payload || {}
  return {
    sync_id: record.sync_id,
    transaction_id: record.transaction_id || null,
    cashier_name: record.cashier_name || receipt.cashier_name || '',
    /* Nama pelanggan ikut ke baris antrean supaya detail + cetak ulang nota
       yang belum tersinkron tetap membawa datanya (CUSTOMER_DESIGN.md). */
    cust_name: receipt.cust_name ?? payload.cust_name ?? null,
    phone_no: receipt.phone_no ?? payload.phone_no ?? null,
    payment_method: receipt.payment_method || record.payload?.payment_method || '',
    total_amount: Number(receipt.total_amount ?? record.payload?.total_amount ?? 0),
    sync_status: record.sync_status,
    created_at: record.created_at || '',
    queue_label: queueLabel(record),
  }
}

export async function listQueuedTransactions() {
  const unresolved = await getUnresolvedTransactions()
  return unresolved
    .filter((record) => QUEUED_STATUSES.includes(record.sync_status))
    .map(toQueuedRow)
    .sort((left, right) => String(left.created_at).localeCompare(String(right.created_at)))
}

export default {
  listQueuedTransactions,
  toQueuedRow,
}
