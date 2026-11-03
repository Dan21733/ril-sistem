import { API_BASE_URL, deviceAuthHeader } from './deviceApi'

/*
 * Kas laci toko (rancangan 2026-11-03). Status boleh dibaca tanpa PIN; buka,
 * tutup, dan uang keluar membawa PIN kasir di badan permintaan (tidak pernah
 * disimpan di sini). Tablet tidak pernah menerima angka "seharusnya" atau
 * selisih: hitungan kasir buta, pemilik yang melihat selisihnya di HP/master.
 */

async function request(path, deviceToken, { method = 'GET', body = null } = {}) {
  const headers = deviceAuthHeader(deviceToken)

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
    error.data = data
    throw error
  }

  return data
}

/* `antrean` = nota + catatan kas + aksi piutang tablet ini yang belum terkirim. */
export function fetchKasStatus(deviceToken, antrean = null) {
  const query = antrean === null ? '' : `?antrean=${encodeURIComponent(antrean)}`
  return request(`/api/kas/${query}`, deviceToken)
}

export function postKasBuka(deviceToken, body) {
  return request('/api/kas/buka/', deviceToken, { method: 'POST', body })
}

export function postKasTutup(deviceToken, body) {
  return request('/api/kas/tutup/', deviceToken, { method: 'POST', body })
}

export function postKasGerak(deviceToken, body) {
  return request('/api/kas/gerak/', deviceToken, { method: 'POST', body })
}

export default { fetchKasStatus, postKasBuka, postKasTutup, postKasGerak }
