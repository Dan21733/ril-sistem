import { API_BASE_URL, deviceAuthHeader } from './deviceApi'

/*
 * Riwayat nota milik device ini (keputusan visibilitas 2026-09-26):
 * backend hanya mengembalikan nota dengan prefix Txx- milik device
 * pemanggil. Header wajib Authorization: Device <token> — tanpa itu
 * backend membalas 401.
 */

async function requestHistory(path, deviceToken, personPin = '') {
  const headers = deviceAuthHeader(deviceToken)

  /* PIN kasir untuk scope=all dan baca lintas device (keputusan 2026-10-05).
     PIN tidak pernah disimpan di sini; hanya diteruskan sebagai header. */
  if (personPin) {
    headers['X-Approver-PIN'] = String(personPin)
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers,
  })

  let body
  try {
    body = await response.json()
  } catch {
    body = {}
  }

  if (!response.ok) {
    const error = new Error(
      body.detail || `Gagal memuat riwayat nota (HTTP ${response.status}).`,
    )
    error.status = response.status
    throw error
  }

  return body
}

/*
 * Daftar ringkas nota device, terbaru dulu, terpaginasi.
 *
 * `date` (YYYY-MM-DD, hari lokal toko) membatasi ke satu hari: riwayat default
 * adalah hari ini, dan hari lain dicari lewat pencarian kalender (permintaan
 * pemilik, 2026-09-30). Tanggal kosong berarti semua hari — hanya dipakai kalau
 * pemanggil memang menginginkan itu.
 */
export function fetchTransactionHistory(
  deviceToken,
  page = 1,
  date = null,
  scope = 'self',
  personPin = '',
  refundStatus = '',
) {
  const params = new URLSearchParams()

  params.set('page', String(page))

  if (date) {
    params.set('date', String(date))
  }

  if (scope === 'all') {
    params.set('scope', 'all')
  }

  /* Filter status aksi (none/void/returned/refunded) untuk peninjauan harian;
     server menolak nilai tak dikenal dengan 400, bukan mengabaikannya. */
  if (refundStatus) {
    params.set('refund_status', String(refundStatus))
  }

  return requestHistory(
    `/api/transactions/history/?${params.toString()}`,
    deviceToken,
    personPin,
  )
}

/* Satu nota beserta daftar barangnya (dan event refund bila ada). */
export function fetchTransactionDetail(deviceToken, transactionId, personPin = '') {
  return requestHistory(
    `/api/transactions/history/${encodeURIComponent(transactionId)}/`,
    deviceToken,
    personPin,
  )
}

/*
 * Kirim antrean offline sebagai satu batch (slice 2 PIN-per-nota):
 * `members` = [{ ...payload transaction, pin }], PIN tiap nota dari
 * `original_pin` tersimpan. Respons bentuk baru `{ results: [...] }`
 * tanpa `worker_id` top-level; hasil per-nota dipetakan pemanggil ke
 * status lokal (App.vue). PIN tidak dicatat ke mana pun di sini.
 */
export async function syncTransactionBatch(deviceToken, members) {
  const response = await fetch(`${API_BASE_URL}/api/transactions/sync/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...deviceAuthHeader(deviceToken),
    },
    body: JSON.stringify({ transactions: members }),
  })

  let body
  try {
    body = await response.json()
  } catch {
    body = {}
  }

  if (response.status < 200 || response.status >= 300) {
    const error = new Error(
      body.detail || `Gagal mengirim antrean (HTTP ${response.status}).`,
    )
    error.status = response.status
    error.body = body
    error.headers = response.headers
    throw error
  }

  return body
}

/*
 * Catat satu event void/return/refund. `payload` = { refund_id, sync_id,
 * approver_pin, event_type, reason, refund_method, refund_amount, items }.
 * PIN hanya diteruskan ke server, tidak disimpan di perangkat.
 */
export async function createRefundEvent(deviceToken, payload) {
  const response = await fetch(`${API_BASE_URL}/api/refunds/`, {
    method: 'POST',
    headers: {
      ...deviceAuthHeader(deviceToken),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })

  let body
  try {
    body = await response.json()
  } catch {
    body = {}
  }

  if (!response.ok) {
    /*
     * 403 = PIN sah tapi bukan kasir nota asal (keputusan pemilik: retur
     * hanya transaksi sendiri + ACC master). Kalimatnya dipatok di sini,
     * bukan mengikuti `detail` server, supaya kasir selalu membaca pesan yang
     * sama walau versi backend berbeda.
     */
    const detail = response.status === 403
      ? 'Hanya kasir nota ini yang boleh void/retur/refund. Minta ACC master bila kasirnya tidak ada.'
      : body.detail || `Gagal mencatat refund (HTTP ${response.status}).`
    const error = new Error(detail)
    error.status = response.status
    throw error
  }

  return body
}
