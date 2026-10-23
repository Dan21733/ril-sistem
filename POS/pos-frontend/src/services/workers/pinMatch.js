import { compareSync } from 'bcryptjs'

/*
 * Pencocokan PIN terhadap daftar worker yang disimpan tablet (KEPUTUSAN-9/10).
 *
 * Modul ini sengaja tidak menyentuh Dexie maupun jaringan: isinya hanya
 * keputusan "PIN ini milik siapa", sehingga bisa diuji sebagai fungsi murni.
 *
 * Yang disimpan tablet adalah nilai PIN apa adanya dari tabel `worker`, dan di
 * tabel itu bentuknya hash bcrypt dari sistem lama. Jadi pencocokan dilakukan
 * dengan bcrypt yang sama, bukan dengan membandingkan teks: PIN asli tidak
 * pernah tersimpan di tablet, dan menebaknya dari tablet yang dicuri butuh
 * waktu berjam-jam per worker — bukan sekejap seperti pada daftar PIN terbuka
 * atau sidik jari sha256 dari ruang 6 digit.
 */

export const WORKER_PIN_FORMAT = {
  BCRYPT: 'bcrypt',
  PLAINTEXT: 'plaintext',
  /*
   * Hash berformat Django (mis. `pbkdf2_sha256$…`). Browser tidak punya hasher
   * itu, jadi baris seperti ini diabaikan — lebih baik tidak mencocokkan
   * daripada mencocokkan dengan asumsi.
   */
  OTHER: 'other',
}

const PIN_PATTERN = /^\d{6}$/

/* Perbandingan panjang tetap untuk jalur teks biasa; murah dan tanpa cabang. */
function equalStrings(left, right) {
  if (left.length !== right.length) {
    return false
  }

  let difference = 0

  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index)
  }

  return difference === 0
}

function storedPinMatches(submitted, storedPin, format) {
  if (!storedPin) {
    return false
  }

  if (format === WORKER_PIN_FORMAT.PLAINTEXT) {
    return equalStrings(String(storedPin), submitted)
  }

  if (format === WORKER_PIN_FORMAT.BCRYPT) {
    try {
      return compareSync(submitted, String(storedPin))
    } catch {
      /* Hash rusak (salt/hash tidak sah) diperlakukan sebagai tidak cocok. */
      return false
    }
  }

  return false
}

/*
 * Cari worker yang memiliki PIN ini. `null` kalau tidak ada yang cocok — dan itu
 * jawaban yang sah, bukan kesalahan: token kasir bisa saja sudah berubah di
 * server sementara daftar di tablet belum sempat disegarkan.
 */
export function matchWorkerPin(pin, workers) {
  const submitted = String(pin ?? '')

  if (!PIN_PATTERN.test(submitted)) {
    return null
  }

  for (const worker of workers || []) {
    if (!storedPinMatches(submitted, worker?.pin_stored, worker?.pin_format)) {
      continue
    }

    return {
      name: String(worker.name ?? '').trim(),
      format: worker.pin_format,
    }
  }

  return null
}

/* Berapa baris yang tidak bisa dipakai tablet; ditampilkan sebagai peringatan. */
export function countUnsupportedWorkers(workers) {
  return (workers || []).filter(
    (worker) => worker?.pin_format !== WORKER_PIN_FORMAT.BCRYPT
      && worker?.pin_format !== WORKER_PIN_FORMAT.PLAINTEXT,
  ).length
}

export default {
  WORKER_PIN_FORMAT,
  matchWorkerPin,
  countUnsupportedWorkers,
}
