/*
 * Semua request API lewat path relatif: dev server mem-proxy /api ke backend
 * (lihat vite.config.js) sehingga same-origin dan bebas dari daftar CORS.
 * Header X-Device-Registration-Token untuk registrasi otomatis disuntikkan
 * proxy dari .env.local — tidak pernah lewat kode browser.
 */
export const API_BASE_URL = ''

/*
 * Header auth device. Token didapat dari registrasi device dan kedaluwarsa
 * setelah 30 hari; tanpa header ini endpoint transaksi membalas 401.
 */
export function deviceAuthHeader(deviceToken) {
  return { Authorization: `Device ${deviceToken}` }
}

/*
 * Registrasi device. Token registrasi tidak dikirim dari sini — proxy dev
 * server yang menyuntikkannya. Jika proxy belum punya token, backend membalas
 * 401 dan UI meminta pemilik mengisi .env.local lalu restart dev server.
 *
 * `renewal` opsional: `{ device_id, device_token }` lama untuk memperbarui
 * token device yang sama (backend membalas 200). Tanpa renewal, backend
 * mengalokasikan device dan kode baru (201). Token lama tidak pernah dicetak
 * atau dicatat ke history/log; ia hanya hidup di body request ini.
 */
export async function registerDevice(renewal) {
  const response = await fetch(`${API_BASE_URL}/api/devices/register/`, {
    method: 'POST',
    ...(renewal
      ? {
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            device_id: renewal.device_id,
            device_token: renewal.device_token,
          }),
        }
      : {}),
  })

  let body
  try {
    body = await response.json()
  } catch {
    body = {}
  }

  if (!response.ok) {
    const error = new Error(body.detail || `Registrasi device gagal (HTTP ${response.status}).`)
    error.status = response.status
    throw error
  }

  return body
}

/*
 * Pendaftaran dengan renewal: device yang sudah tersimpan mencoba memperbarui
 * token lamanya dulu (200, device_id dan kode tidak berubah) sebelum meminta
 * kode baru (201). Renewal yang ditolak 401 — token di luar window 60 hari,
 * device tak dikenal server, atau pasangan id/token tak cocok — jatuh ke
 * registrasi baru satu kali. Status lain (409 kode habis, 429, 5xx, jaringan)
 * diteruskan apa adanya supaya tidak menutupi masalah yang sebenarnya.
 */
export async function registerOrRenewDevice(stored) {
  if (stored?.device_id && stored?.device_token) {
    try {
      return await registerDevice({
        device_id: stored.device_id,
        device_token: stored.device_token,
      })
    } catch (error) {
      if (error.status !== 401) {
        throw error
      }
    }
  }

  return registerDevice(null)
}

/*
 * Minta rentang nomor nota baru untuk device. Wajib memakai device token;
 * tanpa token backend membalas 401.
 */
export async function allocateSequenceRange(device) {
  const response = await fetch(
    `${API_BASE_URL}/api/devices/${encodeURIComponent(device.device_id)}/transaction-sequence/`,
    {
      method: 'POST',
      headers: deviceAuthHeader(device.device_token),
    },
  )

  if (!response.ok) {
    const error = new Error(`Alokasi nomor nota gagal (HTTP ${response.status}).`)
    error.status = response.status
    throw error
  }

  return response.json()
}
