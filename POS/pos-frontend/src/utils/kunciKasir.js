/*
 * Kasir dikunci pemilik dari master (mis. saat stock opname, 2026-11-05).
 *
 * Server menjawab 423 untuk katalog, nota baru, riwayat, dll., dan status kas
 * membawa `dikunci`. Keadaannya disimpan di perangkat supaya layar tetap
 * tertutup walau tablet lalu offline: salinan katalog (stok, harga) tidak boleh
 * jadi jalan belakang selama dikunci.
 */

const KUNCI_KEY = 'pos.kunci'

export function readKunci(storage = globalThis.localStorage) {
  try {
    const raw = storage?.getItem(KUNCI_KEY)
    return raw ? JSON.parse(raw) : { dikunci: false, alasan: '' }
  } catch {
    return { dikunci: false, alasan: '' }
  }
}

export function rememberKunci(kunci, storage = globalThis.localStorage) {
  try {
    storage?.setItem(KUNCI_KEY, JSON.stringify(kunci))
  } catch {
    /* Penyimpanan diblokir: keadaan hanya hidup di memori. */
  }
}

/* Keadaan kunci dari status kas (`/api/kas/`), atau null kalau server lama tanpa kolom ini. */
export function kunciDariStatus(status) {
  if (!status || typeof status.dikunci !== 'boolean') {
    return null
  }
  return { dikunci: status.dikunci, alasan: status.kunci_alasan || '' }
}

/* Jawaban 423 dari server: badan `{kode: 'kasir_dikunci', alasan}`. */
export function kunciDariJawaban(status, body) {
  if (status !== 423) {
    return null
  }
  return { dikunci: true, alasan: body?.alasan || '' }
}
