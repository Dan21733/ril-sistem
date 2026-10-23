/*
 * Keterjangkauan server: kapan perangkat boleh menganggap server POS bisa
 * dijawab. Dipakai gerbang PIN tak dikenal (OFFLINE_SYNC_DESIGN.md §3).
 *
 * `navigator.onLine` tidak dipakai sebagai bukti: status internet browser tidak
 * mengatakan apa pun tentang server POS (proxy LAN, backend mati, captive
 * portal). Yang dihitung sebagai bukti hanya respons **2xx** dari server itu
 * sendiri (keputusan pemilik 2026-10-09: respons 4xx/5xx — termasuk 502 dari
 * proxy dev saat backend mati — membuktikan ada yang menjawab HTTP, bukan
 * bahwa backend hidup; menghitungnya sebagai "hidup" pernah membuka gerbang
 * PIN untuk backend yang mati). `navigator.onLine === false` tetap dipakai
 * sebagai jalan pintas negatif supaya perangkat yang memang offline tidak
 * menunggu timeout.
 *
 * Modul ini sengaja tidak mengimpor apa pun: ia harus bisa dipakai dan diuji
 * tanpa Dexie, tanpa store, dan tanpa konfigurasi API.
 */

/* Selama ini, jawaban server terakhir masih dianggap bukti yang sah. */
const FRESH_MS = 20000

/* Batas tunggu satu percobaan selidik. */
const PROBE_TIMEOUT_MS = 4000

/* Dua percobaan: server yang lambat sekali tidak boleh menolak nota. */
const PROBE_ATTEMPTS = 2

/* Hasil selidik dipakai ulang sebentar supaya tidak menghajar katalog. */
const PROBE_CACHE_MS = 5000

/* Halaman pertama katalog: kecil, tanpa efek samping, dan sama-origin. */
/*
 * `/api/health/` (17 Okt 2026): jawaban server tanpa data, tidak butuh token
 * tablet. Dulu memakai `/api/items/`, yang sekarang wajib token sehingga
 * tablet yang belum terdaftar akan salah dianggap "server mati".
 */
export const REACHABILITY_PROBE_PATH = '/api/health/'

let lastAnswerAt = 0

let lastSilenceAt = 0

let cachedProbe = null

let inFlightProbe = null

function recordAnswer(reachable, at) {
  if (reachable) {
    lastAnswerAt = at
  } else {
    lastSilenceAt = at
  }

  cachedProbe = { at, reachable }

  return reachable
}

/*
 * Bukti terakhir: `true` (server menjawab baru-baru ini), `false` (kegagalan
 * jaringan terakhir lebih baru), atau `null` (belum ada bukti sejak aplikasi
 * dibuka — pemanggil yang memutuskan perlu selidik atau tidak).
 */
export function serverReachability({ now = Date.now(), freshMs = FRESH_MS } = {}) {
  if (lastAnswerAt > lastSilenceAt && now - lastAnswerAt <= freshMs) {
    return true
  }

  if (lastSilenceAt > lastAnswerAt) {
    return false
  }

  return null
}

/*
 * Hanya untuk respons 2xx. Pemanggil wajib memeriksa `response.ok` dulu:
 * 4xx/5xx dicatat sebagai sunyi lewat `noteServerSilence()`, bukan di sini.
 * Kegagalan jaringan dicatat terpisah oleh pemanggil.
 */
export function noteServerAnswer({ now = Date.now() } = {}) {
  return recordAnswer(true, now)
}

export function noteServerSilence({ now = Date.now() } = {}) {
  return recordAnswer(false, now)
}

async function attemptProbe(timeoutMs) {
  const controller = typeof AbortController === 'function' ? new AbortController() : null

  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null

  try {
    const response = await fetch(REACHABILITY_PROBE_PATH, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      cache: 'no-store',
      signal: controller?.signal,
    })

    /*
     * `ok` berarti 2xx. 502 dari proxy dev (backend mati) dan 4xx lain
     * bukanlah bukti backend hidup — lihat keputusan di komentar modul.
     */
    if (response?.ok) {
      try {
        noteServerTime((await response.json())?.server_time)
      } catch {
        /* Jawaban tanpa jam server tetap bukti server hidup. */
      }
    }
    return Boolean(response?.ok)
  } catch {
    /* Timeout, abort, dan kegagalan jaringan sama artinya di sini. */
    return false
  } finally {
    if (timer) {
      clearTimeout(timer)
    }
  }
}

/*
 * Selisih jam tablet terhadap jam server (QA 18 Okt 2026). Nota memakai jam
 * tablet; jam yang salah membuat nota tercatat di hari lain, dan server menahan
 * nota yang lebih dari 10 menit di depan jamnya. Positif = jam tablet maju.
 */
let clockSkew = null

export function noteServerTime(serverTime, { now = Date.now() } = {}) {
  const at = Date.parse(serverTime ?? '')
  if (Number.isFinite(at)) {
    clockSkew = now - at
  }
  return clockSkew
}

export function clockSkewMs() {
  return clockSkew
}

/*
 * Selidik hanya ketika belum ada bukti yang cukup. Hasilnya disimpan sebentar,
 * dan selidik yang sedang berjalan dipakai bersama oleh semua pemanggil.
 */
export async function probeServerReachability({
  timeoutMs = PROBE_TIMEOUT_MS,
  attempts = PROBE_ATTEMPTS,
  now = Date.now(),
} = {}) {
  if (cachedProbe && now - cachedProbe.at <= PROBE_CACHE_MS) {
    return cachedProbe.reachable
  }

  if (inFlightProbe) {
    return inFlightProbe
  }

  inFlightProbe = (async () => {
    if (typeof navigator !== 'undefined' && navigator?.onLine === false) {
      return recordAnswer(false, Date.now())
    }

    const tries = Math.max(1, Number(attempts) || 1)

    for (let attempt = 0; attempt < tries; attempt += 1) {
      if (await attemptProbe(timeoutMs)) {
        return recordAnswer(true, Date.now())
      }
    }

    return recordAnswer(false, Date.now())
  })().finally(() => {
    inFlightProbe = null
  })

  return inFlightProbe
}

/* Untuk pengujian: buang semua bukti yang tersimpan di memori. */
export function resetServerReachability() {
  lastAnswerAt = 0
  lastSilenceAt = 0
  cachedProbe = null
  inFlightProbe = null
  clockSkew = null
}

export default {
  REACHABILITY_PROBE_PATH,
  noteServerAnswer,
  noteServerSilence,
  probeServerReachability,
  resetServerReachability,
  serverReachability,
}
