import { API_BASE_URL, deviceAuthHeader } from './deviceApi'

/*
 * Unggah bukti transfer: `POST /api/payment-proofs/`, multipart, header
 * `Authorization: Device <token>` (PAYMENT_PROOF_DESIGN.md bagian 6).
 *
 * Berbeda dari modul API lain yang melempar error, fungsi ini mengembalikan
 * `{ status, body }` apa adanya. Alasannya: pemanggilnya (uploadQueue) harus
 * membedakan 401, 404, 409, dan 413 untuk menentukan status antrean lokal —
 * melempar error akan membuang informasi itu.
 *
 * `proof_id` dibuat klien dan menjadi kunci idempotensi: mengirim ulang payload
 * yang sama tidak pernah menghasilkan bukti ganda.
 */
/*
 * Nama berkas kiriman hanya untuk keterbacaan log; server tidak memakainya.
 * Ekstensi diambil dari tipe blob supaya cocok dengan isinya: screenshot yang
 * diteruskan apa adanya tetap `.png`, bukan `.jpg` (KEPUTUSAN-8).
 */
const EXTENSION_BY_CONTENT_TYPE = {
  'image/jpeg': 'jpg',
  'image/png': 'png',
  'image/webp': 'webp',
}

export async function uploadPaymentProof({
  deviceToken,
  proofId,
  syncId,
  capturedAt = null,
  blob,
}) {
  const form = new FormData()

  form.append('proof_id', proofId)
  form.append('sync_id', syncId)

  if (capturedAt) {
    form.append('captured_at', capturedAt)
  }

  const extension = EXTENSION_BY_CONTENT_TYPE[String(blob?.type || '').toLowerCase()] || 'jpg'

  form.append('file', blob, `${proofId}.${extension}`)

  const response = await fetch(`${API_BASE_URL}/api/payment-proofs/`, {
    method: 'POST',
    headers: deviceAuthHeader(deviceToken),
    body: form,
  })

  let body = {}

  try {
    const parsed = await response.json()

    body = parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    /* Balasan tanpa JSON (mis. halaman error dari proxy) dianggap kosong. */
  }

  return { status: response.status, body }
}

export default {
  uploadPaymentProof,
}
