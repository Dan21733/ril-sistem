/*
 * Klien print bridge (POS/pos-backend/PRINT_PATH_DESIGN.md §4).
 *
 * POS tidak pernah menyentuh printer: halaman web tidak bisa membuka TCP mentah
 * ke port 9100, dan panggilan ke http:// di LAN diblokir sebagai mixed content
 * kalau POS disajikan lewat HTTPS. Yang dikirim ke bridge hanyalah byte ESC/POS
 * yang sudah jadi; bridge meneruskannya apa adanya dan tidak tahu apa itu nota.
 *
 * Alamat bridge default `/print` (same-origin di belakang reverse proxy yang
 * sama dengan POS) karena itu satu-satunya pilihan yang bebas mixed content
 * tanpa sertifikat lokal. Kalau toko memilih jalur lain (LAN HTTP, atau bridge
 * bersertifikat), cukup isi VITE_PRINT_BRIDGE_URL dengan alamat lengkapnya.
 */

export const PRINT_JOB_HEADER = 'X-Print-Job'

export const DEFAULT_PRINT_BRIDGE_URL = '/print'

/* Bridge tidak menunggu operator; kalau printer tidak menjawab, kita yang mundur. */
export const PRINT_TIMEOUT_MS = 8000

/*
 * Pemeriksaan status jauh lebih pendek dari percobaan cetak: kasir melihat
 * hasilnya di header, jadi menunggu 8 detik hanya membuat indikatornya
 * menggantung. Bridge sendiri membatasi sentuhan ke printer (PRINT_HEALTH_TIMEOUT).
 */
export const PRINT_HEALTH_TIMEOUT_MS = 4000

/*
 * Tiga keadaan yang harus dibedakan kasir, karena cara memperbaikinya berbeda:
 *
 * - `connected`           : printer menjawab, cetak seharusnya jalan.
 * - `printer_unreachable` : bridge hidup tetapi printer mati/kabelnya lepas.
 * - `bridge_unreachable`  : bridge (atau jalurnya) yang tidak ada; batas ini
 *                           yang paling sering terjadi setelah instalasi baru.
 */
export const PRINTER_HEALTH_STATE = {
  UNKNOWN: 'unknown',
  CONNECTED: 'connected',
  PRINTER_UNREACHABLE: 'printer_unreachable',
  BRIDGE_UNREACHABLE: 'bridge_unreachable',
}

/*
 * Error bergaya status HTTP: pemanggil (antrean cetak) hanya perlu tahu apakah
 * percobaan berikutnya masih masuk akal.
 */
export class PrintBridgeError extends Error {
  constructor(message, { status = 0, retryable = true } = {}) {
    super(message)
    this.name = 'PrintBridgeError'
    this.status = status
    this.retryable = retryable
  }
}

/*
 * Token bridge opsional. Berbeda dengan DEVICE_REGISTRATION_TOKEN, nilai ini
 * memang ikut ke bundle POS (variabel VITE_*), jadi sifatnya pagar terhadap
 * klien asing di LAN — bukan autentikasi kuat. Bridge yang tidak memakai token
 * tidak mengirim header ini sama sekali.
 */
export function getPrintBridgeToken() {
  try {
    return String(import.meta.env?.VITE_PRINT_TOKEN || '').trim()
  } catch {
    return ''
  }
}

export function getPrintBridgeUrl() {
  let configured

  try {
    configured = import.meta.env?.VITE_PRINT_BRIDGE_URL || ''
  } catch {
    /* Diuji lewat Node: import.meta.env tidak ada. */
    configured = ''
  }

  return String(configured || DEFAULT_PRINT_BRIDGE_URL).replace(/\/+$/, '')
}

/*
 * Alamat pemeriksaan status = alamat bridge + `/health`. Bridge menjawab di
 * `/health` maupun `/print/health`, jadi alamat bridge boleh berupa root host
 * (mis. http://10.0.0.5:9101) atau alamat same-origin berprefix (`/print`).
 */
export function getPrintBridgeHealthUrl(url = getPrintBridgeUrl()) {
  return `${String(url).replace(/\/+$/, '')}/health`
}

function failedHealth(state, detail, extra = {}) {
  return {
    state,
    printerStatus: null,
    printerAddress: '',
    printerName: '',
    bridgeVersion: '',
    jobsSeen: 0,
    detail,
    ...extra,
  }
}

/*
 * Tanya status bridge + printer. Fungsi ini **tidak pernah melempar**: pemanggil
 * hanya menggambar hasilnya (indikator header), dan kegagalan jaringan di situ
 * adalah jawaban yang sah — bukan kesalahan yang perlu ditangani terpisah.
 *
 * Yang diukur hanya "printer menerima koneksi"; kertas habis atau pita kering
 * tetap tidak terlihat dan baru ketahuan saat mencetak.
 */
export async function checkPrintBridgeHealth({
  url = getPrintBridgeUrl(),
  timeoutMs = PRINT_HEALTH_TIMEOUT_MS,
  fetchImpl = globalThis.fetch,
  checkedAt = new Date().toISOString(),
} = {}) {
  const healthUrl = getPrintBridgeHealthUrl(url)

  if (typeof fetchImpl !== 'function') {
    return failedHealth(
      PRINTER_HEALTH_STATE.BRIDGE_UNREACHABLE,
      'Perangkat ini tidak bisa memeriksa status printer.',
      { checkedAt, url: healthUrl },
    )
  }

  const token = getPrintBridgeToken()
  const controller = typeof AbortController === 'function' ? new AbortController() : null
  const timer = controller
    ? setTimeout(
        () => controller.abort(),
        Math.max(1000, Number(timeoutMs) || PRINT_HEALTH_TIMEOUT_MS),
      )
    : null

  let response

  try {
    response = await fetchImpl(healthUrl, {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        ...(token ? { 'X-Print-Token': token } : {}),
      },
      signal: controller?.signal,
    })
  } catch (error) {
    return failedHealth(
      PRINTER_HEALTH_STATE.BRIDGE_UNREACHABLE,
      error?.name === 'AbortError'
        ? 'Penghubung cetak di toko tidak menjawab dalam batas waktu.'
        : 'Penghubung cetak di toko tidak bisa dihubungi.',
      { checkedAt, url: healthUrl },
    )
  } finally {
    if (timer) {
      clearTimeout(timer)
    }
  }

  if (!response.ok) {
    return failedHealth(
      PRINTER_HEALTH_STATE.BRIDGE_UNREACHABLE,
      `Penghubung cetak menjawab dengan kode ${response.status}.`,
      { checkedAt, url: healthUrl },
    )
  }

  let body

  try {
    body = await response.json()
  } catch {
    return failedHealth(
      PRINTER_HEALTH_STATE.BRIDGE_UNREACHABLE,
      'Jawaban penghubung cetak tidak bisa dibaca.',
      { checkedAt, url: healthUrl },
    )
  }

  const printerStatus = body?.printer_status
  const base = {
    printerStatus: printerStatus ?? null,
    printerAddress: body?.printer ?? '',
    /* Label dari `PRINTER_NAME` di bridge, bukan hasil deteksi model. */
    printerName: body?.printer_name ?? '',
    bridgeVersion: body?.version ?? '',
    jobsSeen: Number(body?.jobs_seen ?? 0),
    checkedAt,
    url: healthUrl,
  }

  if (printerStatus === 'ok') {
    return { ...base, state: PRINTER_HEALTH_STATE.CONNECTED, detail: '' }
  }

  if (printerStatus === 'unreachable') {
    return {
      ...base,
      state: PRINTER_HEALTH_STATE.PRINTER_UNREACHABLE,
      detail: body?.printer_detail || 'Printer tidak menjawab.',
    }
  }

  /*
   * Bridge versi lama belum melaporkan `printer_status`. Bridge hidup berarti
   * nota masih bisa dicetak, jadi jangan ditampilkan sebagai gangguan; cukup
   * catat bahwa status printer belum bisa dipastikan.
   */
  return {
    ...base,
    state: PRINTER_HEALTH_STATE.CONNECTED,
    detail: 'Penghubung cetak ini belum melaporkan status printer, jadi nota tetap dianggap bisa keluar.',
  }
}

function toBody(bytes) {
  if (bytes instanceof Uint8Array) {
    return bytes
  }

  if (bytes instanceof ArrayBuffer) {
    return new Uint8Array(bytes)
  }

  if (Array.isArray(bytes)) {
    return Uint8Array.from(bytes)
  }

  throw new PrintBridgeError('Isi pekerjaan cetak bukan byte ESC/POS.', {
    status: 0,
    retryable: false,
  })
}

/*
 * Kirim satu pekerjaan cetak. `jobId` wajib sama pada percobaan ulang job yang
 * sama: bridge memakainya untuk mengenali kiriman kembar supaya printer tidak
 * mengeluarkan nota dua kali (dokumen §5).
 */
export async function sendPrintJob({
  jobId,
  bytes,
  url = getPrintBridgeUrl(),
  timeoutMs = PRINT_TIMEOUT_MS,
  fetchImpl = globalThis.fetch,
} = {}) {
  if (!jobId) {
    throw new PrintBridgeError('Pekerjaan cetak tanpa ID tidak boleh dikirim.', {
      status: 0,
      retryable: false,
    })
  }

  if (typeof fetchImpl !== 'function') {
    throw new PrintBridgeError('Perangkat ini tidak bisa mengirim pekerjaan cetak.', {
      status: 0,
      retryable: false,
    })
  }

  const body = toBody(bytes)
  const token = getPrintBridgeToken()
  const controller = typeof AbortController === 'function' ? new AbortController() : null
  const timer = controller
    ? setTimeout(() => controller.abort(), Math.max(1000, Number(timeoutMs) || PRINT_TIMEOUT_MS))
    : null

  let response

  try {
    response = await fetchImpl(`${url}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/octet-stream',
        [PRINT_JOB_HEADER]: String(jobId),
        ...(token ? { 'X-Print-Token': token } : {}),
      },
      body,
      signal: controller?.signal,
    })
  } catch (error) {
    throw new PrintBridgeError(
      error?.name === 'AbortError'
        ? 'Printer atau penghubung cetak tidak menjawab dalam batas waktu.'
        : 'Penghubung cetak di toko tidak bisa dihubungi.',
      { status: 0, retryable: true },
    )
  } finally {
    if (timer) {
      clearTimeout(timer)
    }
  }

  const status = response.status

  if (response.ok) {
    return {
      ok: true,
      status,
      /* Bridge membalas 200 tanpa mencetak kalau job ini memang sudah pernah masuk. */
      duplicate: response.headers?.get?.('X-Print-Duplicate') === '1',
    }
  }

  if (status === 503 || status === 504 || status === 429 || status >= 500) {
    throw new PrintBridgeError('Printer tidak terjangkau dari penghubung cetak.', {
      status,
      retryable: true,
    })
  }

  throw new PrintBridgeError(
    `Penghubung cetak menolak kiriman ini (kode ${status}). Periksa pengaturan cetak.`,
    { status, retryable: false },
  )
}

export default {
  PRINT_JOB_HEADER,
  DEFAULT_PRINT_BRIDGE_URL,
  PRINT_TIMEOUT_MS,
  PRINT_HEALTH_TIMEOUT_MS,
  PRINTER_HEALTH_STATE,
  PrintBridgeError,
  getPrintBridgeUrl,
  getPrintBridgeToken,
  getPrintBridgeHealthUrl,
  checkPrintBridgeHealth,
  sendPrintJob,
}
