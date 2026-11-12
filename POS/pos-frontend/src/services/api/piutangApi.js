import { API_BASE_URL, deviceAuthHeader } from './deviceApi'

/*
 * Piutang pelanggan di tablet (DP fase 2). Daftar dan detail terlihat semua
 * kasir setelah PIN (header X-Approver-PIN, tidak pernah disimpan di sini).
 * Aksi (bayar / barang diambil) membawa PIN di badan permintaan seperti nota,
 * idempoten per `aksi_id` sehingga antrean offline boleh mengirim ulang.
 */

async function request(path, deviceToken, { pin = '', method = 'GET', body = null } = {}) {
  const headers = deviceAuthHeader(deviceToken)

  if (pin) {
    headers['X-Approver-PIN'] = String(pin)
  }
  if (body) {
    headers['Content-Type'] = 'application/json'
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    ...(body ? { body: JSON.stringify(body) } : {}),
  })

  let data
  try {
    data = await response.json()
  } catch {
    data = {}
  }

  if (!response.ok) {
    const error = new Error(data.detail || `Permintaan gagal (HTTP ${response.status}).`)
    error.status = response.status
    throw error
  }

  return data
}

export function fetchDpRules(deviceToken) {
  return request('/api/piutang/aturan/', deviceToken)
}

export function fetchPiutangList(deviceToken, pin, tab = 'proses', query = '') {
  const params = new URLSearchParams({ tab })

  if (query) {
    params.set('q', query)
  }

  return request(`/api/piutang/?${params.toString()}`, deviceToken, { pin })
}

export function fetchPiutangDetail(deviceToken, pin, kunci) {
  return request(`/api/piutang/${encodeURIComponent(kunci)}/`, deviceToken, { pin })
}

export function postPiutangAction(deviceToken, action) {
  return request('/api/piutang/aksi/', deviceToken, { method: 'POST', body: action })
}

export default { fetchDpRules, fetchPiutangList, fetchPiutangDetail, postPiutangAction }
