/*
 * Kompresi foto bukti transfer di sisi klien
 * (PAYMENT_PROOF_DESIGN.md bagian 7.5, KEPUTUSAN-8).
 *
 * Prinsipnya: jangan mengompres lebih dari yang perlu.
 *
 * 1. Berkas yang sudah memenuhi syarat server (JPEG/PNG/WEBP dan tidak melebihi
 *    target ukuran) diteruskan apa adanya, tanpa re-encode. Re-encode selalu
 *    menurunkan mutu, jadi screenshot bukti transfer yang berukuran ratusan KB
 *    tidak perlu disentuh sama sekali — dan PNG screenshot yang diteruskan utuh
 *    justru lebih tajam daripada hasil konversi ke JPEG.
 * 2. Yang terlalu besar atau berformat yang tidak diterima server (mis. HEIC
 *    dari galeri iPhone) di-encode ulang ke JPEG pada resolusi aslinya, dengan
 *    kualitas setinggi mungkin yang masih di bawah target. Resolusi hanya
 *    diturunkan kalau kualitas minimum pun belum cukup — jadi hasilnya berada
 *    sedekat mungkin dengan batas server, bukan sekecil mungkin.
 *
 * Batas keras server tetap 5 MB (`MAX_PROOF_BYTES` di
 * pos/payment_proof_storage.py) dan klien sengaja berhenti sedikit di bawahnya.
 *
 * Tidak ada hash di sini: `crypto.subtle` hanya tersedia pada secure context,
 * sedangkan POS dibuka dari http pada IP LAN (bagian 7.11). `sha256` yang
 * otoritatif dihitung server.
 */

/* Batas keras server. */
export const MAX_PROOF_BYTES = 5 * 1024 * 1024

/*
 * Target yang dituju saat encode ulang: 4,5 MB, yaitu 90% batas keras. Sisanya
 * jadi margin supaya berkas tidak jatuh tepat di ambang batas — pada batas
 * persis, pembulatan multipart atau constraint database bisa menolaknya.
 * Ini satu-satunya angka yang perlu diubah kalau unggahan terasa terlalu berat.
 */
export const TARGET_PROOF_BYTES = Math.round(4.5 * 1024 * 1024)

/*
 * Format yang diterima server, ditentukan dari magic bytes-nya (lihat
 * `MAGIC_SIGNATURES` di pos/payment_proof_storage.py). SVG sengaja tidak ada di
 * sana karena dapat memuat skrip.
 */
const PASS_THROUGH_CONTENT_TYPES = ['image/jpeg', 'image/png', 'image/webp']

/* Kualitas tertinggi yang dicoba. Di atas ini ukuran membengkak tanpa guna. */
const QUALITY_MAX = 0.95

/* Kualitas terendah yang masih dianggap layak untuk bukti transfer. */
const QUALITY_MIN = 0.5

/* Jumlah langkah pencarian kualitas di antara QUALITY_MIN dan QUALITY_MAX. */
const QUALITY_SEARCH_STEPS = 3

/* Resolusi cadangan bila kualitas minimum masih melebihi target. */
const FALLBACK_LONG_SIDES = [2560, 1600]

export class ProofCompressionError extends Error {
  constructor(message) {
    super(message)
    this.name = 'ProofCompressionError'
  }
}

/*
 * `createImageBitmap` tidak ada di semua browser (mis. Safari lama); jalur
 * cadangannya elemen `<img>` dengan object URL yang wajib di-revoke.
 */
async function decodeFile(file) {
  if (typeof createImageBitmap === 'function') {
    try {
      return { source: await createImageBitmap(file), release: () => {} }
    } catch {
      /* Diteruskan ke jalur `<img>` di bawah. */
    }
  }

  if (typeof document === 'undefined') {
    throw new ProofCompressionError('Foto ini tidak dapat dibaca di perangkat ini.')
  }

  const url = URL.createObjectURL(file)

  try {
    const image = await new Promise((resolve, reject) => {
      const element = new Image()

      element.onload = () => resolve(element)
      element.onerror = () => reject(new ProofCompressionError('Foto ini tidak dapat dibaca.'))
      element.src = url
    })

    return { source: image, release: () => URL.revokeObjectURL(url) }
  } catch (error) {
    URL.revokeObjectURL(url)

    throw error
  }
}

function sourceDimensions(source) {
  /* `<img>` memakai naturalWidth/naturalHeight; ImageBitmap memakai width/height. */
  return {
    width: source.naturalWidth || source.width || 0,
    height: source.naturalHeight || source.height || 0,
  }
}

/* Hitung ukuran hasil untuk sisi terpanjang `longSide`; tidak pernah memperbesar. */
function fitWithin(width, height, longSide) {
  const scale = Math.min(1, longSide / Math.max(width, height))

  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  }
}

async function encodeJpeg(source, width, height, quality) {
  let canvas

  if (typeof OffscreenCanvas === 'function') {
    canvas = new OffscreenCanvas(width, height)
  } else {
    canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
  }

  const context = canvas.getContext('2d')

  if (!context) {
    throw new ProofCompressionError('Foto ini tidak dapat diproses di perangkat ini.')
  }

  /* Menurunkan resolusi dengan penyaring termahal supaya teks nota tetap tajam. */
  context.imageSmoothingEnabled = true
  context.imageSmoothingQuality = 'high'

  context.drawImage(source, 0, 0, width, height)

  const blob =
    typeof canvas.convertToBlob === 'function'
      ? await canvas.convertToBlob({ type: 'image/jpeg', quality })
      : await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality))

  if (!blob) {
    throw new ProofCompressionError('Foto ini tidak dapat dikompres.')
  }

  return blob
}

/*
 * Cari kualitas tertinggi pada satu resolusi yang hasilnya masih di bawah
 * `targetBytes`. Mengembalikan `null` kalau bahkan QUALITY_MIN tidak muat —
 * pemanggil yang memutuskan untuk menurunkan resolusi.
 */
async function highestQualityWithinTarget(source, width, height, targetBytes) {
  const atMax = await encodeJpeg(source, width, height, QUALITY_MAX)

  if (atMax.size <= targetBytes) {
    return { blob: atMax, quality: QUALITY_MAX }
  }

  const atMin = await encodeJpeg(source, width, height, QUALITY_MIN)

  if (atMin.size > targetBytes) {
    return null
  }

  /*
   * Titik tengah: kualitas tertinggi yang terbukti muat (batas bawah) dan yang
   * terbukti tidak muat (batas atas), lalu dipersempit beberapa langkah.
   */
  let best = { blob: atMin, quality: QUALITY_MIN }
  let low = QUALITY_MIN
  let high = QUALITY_MAX

  for (let step = 0; step < QUALITY_SEARCH_STEPS; step += 1) {
    const middle = (low + high) / 2
    const blob = await encodeJpeg(source, width, height, middle)

    if (blob.size <= targetBytes) {
      best = { blob, quality: middle }
      low = middle
    } else {
      high = middle
    }
  }

  return best
}

async function encodeWithinTarget(source, { width, height }, targetBytes) {
  const sourceLongSide = Math.max(width, height)

  /*
   * Resolusi asli dulu, baru cadangan yang benar-benar lebih kecil darinya.
   * Tanpa penyaringan ini, foto kecil akan diuji ulang pada resolusi yang sama
   * dan hanya membuang waktu encode. Tidak pernah memperbesar foto.
   */
  const candidates = [sourceLongSide, ...FALLBACK_LONG_SIDES].filter(
    (longSide, index, list) =>
      list.indexOf(longSide) === index && (index === 0 || longSide < sourceLongSide),
  )

  for (const longSide of candidates) {
    const size = fitWithin(width, height, longSide)
    const found = await highestQualityWithinTarget(source, size.width, size.height, targetBytes)

    if (found) {
      return { ...found, ...size }
    }
  }

  return null
}

/*
 * Mengembalikan `{ blob, contentType, width, height, byteSize, reencoded, quality }`.
 * `reencoded` bernilai `false` bila berkas diteruskan apa adanya.
 *
 * Melempar `ProofCompressionError` berpesan bahasa Indonesia supaya kasir tahu
 * harus berbuat apa — bukan gagal diam-diam.
 */
export async function compressProofImage(
  file,
  { targetBytes = TARGET_PROOF_BYTES, forceRecompress = false } = {},
) {
  if (!file || !String(file.type || '').startsWith('image/')) {
    throw new ProofCompressionError('Berkas yang dipilih bukan gambar.')
  }

  const { source, release } = await decodeFile(file)

  try {
    const { width, height } = sourceDimensions(source)

    if (!width || !height) {
      throw new ProofCompressionError('Ukuran foto tidak dapat dibaca.')
    }

    const contentType = String(file.type || '').toLowerCase()
    const acceptable = PASS_THROUGH_CONTENT_TYPES.includes(contentType)

    /*
     * Jalur hemat: sudah JPEG/PNG/WEBP dan tidak melebihi target, jadi bukti
     * dikirim apa adanya. Resolusi asli dipertahankan supaya teks nota tetap
     * terbaca; ukurannya pun tidak akan membengkak karena target 4,5 MB jauh di
     * bawah batas keras server.
     */
    if (!forceRecompress && acceptable && file.size <= targetBytes) {
      return {
        blob: file,
        contentType,
        width,
        height,
        byteSize: file.size,
        reencoded: false,
        quality: null,
      }
    }

    const encoded = await encodeWithinTarget(source, { width, height }, targetBytes)

    if (!encoded) {
      throw new ProofCompressionError(
        'Foto masih terlalu besar setelah dikompres. Ambil ulang dari jarak yang lebih dekat.',
      )
    }

    /* Pengaman terakhir: apa pun yang terjadi, berkas tidak boleh melewati batas server. */
    if (encoded.blob.size > MAX_PROOF_BYTES) {
      throw new ProofCompressionError(
        'Foto masih terlalu besar setelah dikompres. Ambil ulang dari jarak yang lebih dekat.',
      )
    }

    return {
      blob: encoded.blob,
      contentType: 'image/jpeg',
      width: encoded.width,
      height: encoded.height,
      byteSize: encoded.blob.size,
      reencoded: true,
      quality: encoded.quality,
    }
  } finally {
    release()

    if (typeof source.close === 'function') {
      source.close()
    }
  }
}

export default {
  compressProofImage,
  MAX_PROOF_BYTES,
  TARGET_PROOF_BYTES,
  ProofCompressionError,
}
