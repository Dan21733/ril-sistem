/*
 * Saran nominal uang tunai untuk layar pembayaran cash.
 *
 * Kenapa ada: kasir harus mengetik jumlah uang yang diterima pada tablet. Untuk
 * transaksi kecil, menebak nominal yang paling mungkin diserahkan pelanggan lalu
 * mengetiknya angka per angka lebih lambat daripada satu tap. Modul ini menyusun
 * kandidat nominal yang benar-benar mungkin diserahkan pelanggan.
 *
 * Algoritma (exhaustive, merujuk PDF pemilik
 * "algoritma_pembayaran_tunai_minimum_Rp500_v2.pdf"): untuk total T, yang dicari
 * adalah semua nilai M(S) yang berbeda — M(S) = jumlah subset terkecil dari
 * kumpulan uang S yang mencapai atau melewati T. Begitu uang yang diserahkan
 * mencapai T, proses HARUS berhenti; pecahan tambahan tidak boleh ikut dihitung
 * (contoh PDF: 3×100rb sudah cukup untuk 300rb, sisa 20rb disimpan).
 *
 * Implementasi mengikuti pseudocode pemilik persis apa adanya:
 *
 *   For every possible combination of banknotes:
 *       total = sum of all banknotes in the combination
 *       if total < TARGET: continue
 *       is_minimum = true
 *       for each banknote denomination D in the combination:
 *           remaining = total - D
 *           if remaining >= TARGET: is_minimum = false; break
 *       if is_minimum: add total to results
 *   return sorted(results)
 *
 * `isMinimumCombination` di bawah adalah baris `is_minimum` tersebut, dan
 * dipakai sebagai gerbang penerimaan setiap kandidat — bukan hanya di tes.
 * Pencarian kandidatnya memakai DP yang ekuivalen (tanpa batas jumlah lembar):
 * sebuah nominal v lolos tepat ketika v dapat dibentuk dari pecahan yang
 * SEMUANYA lebih besar daripada selisihnya ke total (v - T). Bukti dua arah:
 * (1) Jika T* subset pencapai yang inclusion-minimal dengan jumlah v, maka
 * melepas pecahan terkecilnya pun jatuh di bawah T, jadi pecahan terkecil >
 * v - T. (2) Sebaliknya, jika v terbentuk dari pecahan yang semuanya > v - T,
 * maka setiap subset sejati jumlahnya <= v - pecahan terkecil < T, sehingga v
 * adalah minimum atas semua subsetnya (M(S) = v untuk S = kombinasi itu
 * sendiri). Brute force atas pseudocode (maks 12 lembar) menghasilkan himpunan
 * yang identik untuk 417000, 400000, 300000, 135000, 417500, dan 25000.
 *
 * Batas enumerasi (PDF syarat 10, dinyatakan eksplisit): jumlah lembar per
 * pecahan TIDAK dibatasi, tetapi himpunannya tetap finite karena selisih
 * v - T harus lebih kecil daripada pecahan terbesar (Rp100.000) — kalau tidak,
 * tidak ada pecahan yang bisa melebihinya. Jadi v hanya dicari pada rentang
 * [T, T + 100.000). Contoh: 350.000 dan 400.000 untuk total 300.000 BUKAN
 * pembayaran minimum yang mungkin (350rb tidak habis dibagi 100rb; 400rb butuh
 * pecahan di atas 100rb yang tidak beredar), sedangkan 301.000 valid lewat
 * 2×100rb + 50rb + 2×20rb + 5rb + 3×2rb, dan 310.000 valid lewat
 * 2×100rb + 50rb + 3×20rb.
 *
 * Batas yang mengikat (POS/pos-frontend/AGENTS.md):
 * - Modul ini pengaburan kecepatan, bukan aturan bisnis. Server memvalidasi
 *   transaksi; pembulatan ke atas tetap kelipatan 500 — saran di sini tidak
 *   pernah menurunkan total yang harus dibayar, hanya menawarkan jumlah uang
 *   yang mungkin diserahkan.
 * - Semua saran wajib >= total (tepatnya >= uang pas kelipatan 500), dan wajib
 *   benar-benar bisa dibentuk dari nominal rupiah yang ada.
 */

/*
 * Nominal rupiah yang beredar, kecil ke besar (sama dengan PDF §1/§7).
 * Ini juga yang dipakai menghitung "kombinasi uang" pada `cashBreakdown`.
 */
export const CASH_DENOMINATIONS = [500, 1000, 2000, 5000, 10000, 20000, 50000, 100000]

/* Satuan terkecil yang bisa dibayar tunai: koin Rp500. */
export const CASH_UNIT = 500

export const DEFAULT_SUGGESTION_COUNT = 4

export function roundUpTo(value, step) {
  if (!step || step <= 0) {
    return Math.round(Number(value) || 0)
  }

  return Math.ceil((Number(value) || 0) / step) * step
}

/*
 * Kelipatan `step` berikutnya yang **lebih besar** daripada nilainya, jadi 135.000
 * dengan tangga 5.000 menghasilkan 140.000, bukan 135.000.
 */
export function nextMultipleAbove(value, step) {
  const rounded = roundUpTo(value, step)

  return rounded > value ? rounded : rounded + step
}

/*
 * Seberapa "bulat" sebuah nominal: anak tangga terbesar yang membaginya habis.
 * Dipakai sebagai info tampilan, bukan untuk menolak kandidat.
 */
export function cashRoundness(amount, steps = [5000, 10000, 50000, 100000, 500000, 1000000]) {
  const value = Math.abs(Math.round(Number(amount) || 0))
  const ladder = [...steps].sort((left, right) => right - left)

  for (const step of ladder) {
    if (value % step === 0) {
      return step
    }
  }

  return CASH_UNIT
}

/*
 * Jumlah tunai terkecil yang bisa dibayar tanpa menyerahkan uang lebih kecil
 * daripada koin Rp500 — inilah "uang pas" untuk sebuah total.
 */
export function payableCashAmount(total) {
  const value = Math.max(0, Math.round(Number(total) || 0))

  return value === 0 ? 0 : roundUpTo(value, CASH_UNIT)
}

/*
 * Kombinasi uang untuk satu nominal. Greedy dari nominal terbesar sudah optimal
 * untuk daftar rupiah ini (100rb, 50rb, 20rb, 10rb, 5rb, 2rb, 1rb, 500).
 * `pieces` dipakai sebagai informasi "siapkan berapa lembar" di layar kasir.
 */
export function cashBreakdown(amount, denominations = CASH_DENOMINATIONS) {
  const pieces = []
  let remaining = Math.max(0, Math.round(Number(amount) || 0))

  for (const denomination of [...denominations].sort((left, right) => right - left)) {
    const count = Math.floor(remaining / denomination)

    if (count > 0) {
      pieces.push({ denomination, count })
      remaining -= count * denomination
    }
  }

  return {
    pieces,
    pieces_count: pieces.reduce((total, piece) => total + piece.count, 0),
    remainder: remaining,
  }
}

/*
 * Cek `is_minimum` dari pseudocode pemilik untuk satu kombinasi: jumlahnya
 * mencapai TARGET, dan melepas SATU lembar berapa pun membuatnya jatuh di
 * bawah TARGET (melepas lebih banyak hanya membuatnya lebih kecil lagi,
 * sehingga cek satu lembar sudah mencakup semua subset sejati).
 */
export function isMinimumCombination(target, combo) {
  const total = combo.reduce((sum, piece) => sum + piece, 0)

  if (combo.length === 0 || total < target) {
    return false
  }

  for (const piece of combo) {
    const remaining = total - piece

    if (remaining >= target) {
      return false
    }
  }

  return true
}

/*
 * Jumlah-jumlah (dalam rupiah) di bawah `exclusiveLimit` yang bisa dibentuk dari
 * `pieces` (unbounded knapsack), beserta satu contoh kombinasi tiap jumlah.
 * Dihitung dalam satuan CASH_UNIT agar array-nya kecil.
 */
function reachableSums(exclusiveLimit, pieces) {
  const size = Math.max(0, Math.ceil(exclusiveLimit / CASH_UNIT))
  const reach = new Uint8Array(size)
  const parent = new Int32Array(size).fill(-1)
  const usedPiece = new Int32Array(size).fill(0)
  const units = pieces.map((piece) => piece / CASH_UNIT)
  reach[0] = 1

  for (let index = 0; index < size; index += 1) {
    if (!reach[index]) {
      continue
    }

    for (const step of units) {
      const next = index + step

      if (next < size && !reach[next]) {
        reach[next] = 1
        parent[next] = index
        usedPiece[next] = step * CASH_UNIT
      }
    }
  }

  return { reach, parent, usedPiece }
}

function comboFor(index, parent, usedPiece) {
  const combo = []
  let current = index

  while (current > 0) {
    combo.push(usedPiece[current])
    current = parent[current]
  }

  return combo
}

/*
 * Semua pembayaran minimum yang mungkin untuk satu total (PDF §3 + §7),
 * menaik: `[{ amount, combo }]` dengan `combo` contoh kombinasi pecahan yang
 * terbukti minimal (PDF syarat 4–6: melepas pecahan apa pun dari combo membuat
 * jumlahnya jatuh di bawah total, jadi tidak ada subset yang mencapai total
 * dengan nominal lebih rendah).
 */
export function minimalCashPayments(total, { denominations = CASH_DENOMINATIONS } = {}) {
  const value = Math.max(0, Math.round(Number(total) || 0))

  if (value <= 0) {
    return []
  }

  const valid = [...new Set(denominations)].filter(
    (piece) => Number.isInteger(piece) && piece > 0 && piece % CASH_UNIT === 0,
  ).sort((left, right) => left - right)

  if (valid.length === 0) {
    return []
  }

  /* amount -> combo; bila ujung yang sama tercapai dua cara, simpan yang
     lembamya paling sedikit (lebih mungkin diserahkan pelanggan). */
  const found = new Map()

  for (const smallest of valid) {
    const usable = valid.filter((piece) => piece >= smallest)
    const { reach, parent, usedPiece } = reachableSums(value, usable)
    /* Sisa s = v - smallest harus di [value - smallest, value). */
    const from = Math.max(0, Math.ceil((value - smallest) / CASH_UNIT))
    const to = Math.ceil(value / CASH_UNIT)

    for (let index = from; index < to; index += 1) {
      if (!reach[index] || index * CASH_UNIT >= value) {
        continue
      }

      const amount = index * CASH_UNIT + smallest
      const combo = [...comboFor(index, parent, usedPiece), smallest]

      /* Gerbang penerimaan = baris `is_minimum` pseudocode pemilik. */
      if (!isMinimumCombination(value, combo)) {
        continue
      }

      const previous = found.get(amount)

      if (!previous || combo.length < previous.length) {
        found.set(amount, combo)
      }
    }
  }

  return [...found.entries()]
    .sort((left, right) => left[0] - right[0])
    .map(([amount, combo]) => ({ amount, combo }))
}

/*
 * Saran nominal untuk satu total. Mengembalikan array menaik:
 * `{ amount, change, exact, roundness, pieces }`.
 * `max` membatasi jumlah tombol di layar (bawaan 4); inti daftarnya exhaustive.
 *
 * Kenapa tidak `slice(0, max)` saja: kandidat paling bulat (kelipatan 50rb /
 * 100rb — pelanggan yang hanya membawa pecahan besar) selalu menjadi 1–2 nilai
 * TERBESAR di daftar exhaustive (mis. 450rb & 500rb untuk total 417rb), jadi
 * slice naif justru memotong tepat yang paling dibutuhkan. Seleksinya:
 * wajib ada uang pas + jangkar 50rb/100rb terkecil (dari dalam daftar
 * exhaustive), sisanya diisi nominal **paling bulat** lebih dulu sampai `max`.
 *
 * Pengisi slot = paling bulat (keputusan 2026-10-09, saat pemilik menetapkan
 * `max = 4`): dengan satu slot bebas, "terkecil lebih dulu" menghasilkan
 * tombol kembar — 307.000 melahirkan 308.000, hanya 1.000 di atas uang pas,
 * yang jarang diserahkan pelanggan. Nominal bulat berikutnya (310.000 pada
 * contoh itu, 140.000 untuk 135.000) lebih mungkin benar-benar ada di dompet.
 * Seri diputus oleh nominal terkecil supaya urutannya deterministik.
 */
export function suggestCashAmounts(
  total,
  {
    max = DEFAULT_SUGGESTION_COUNT,
    denominations = CASH_DENOMINATIONS,
  } = {},
) {
  const value = Math.max(0, Math.round(Number(total) || 0))

  if (value <= 0) {
    return []
  }

  const exact = payableCashAmount(value)
  const all = minimalCashPayments(value, { denominations })

  if (!(Number.isFinite(max) && max >= 0)) {
    return all.map(({ amount, combo }) => ({
      amount,
      change: amount - value,
      exact: amount === exact,
      roundness: cashRoundness(amount),
      pieces: combo.length,
    }))
  }

  const limit = Math.floor(max)
  const byAmount = new Map(all.map((row) => [row.amount, row]))
  /* Jangkar: kelipatan 50rb / 100rb terkecil yang tetap pembayaran minimum.
     Dengan pecahan default ini selalu ada (ceil murni 50rb/100rb terbukti
     minimal); dengan daftar pecahan kustom bisa tidak ada -> abaikan. */
  const anchor50 = all.find((row) => row.amount % 50000 === 0)?.amount
  const anchor100 = all.find((row) => row.amount % 100000 === 0)?.amount
  const required = []

  for (const amount of [exact, anchor50, anchor100]) {
    if (amount != null && byAmount.has(amount) && !required.includes(amount)) {
      required.push(amount)
    }
  }

  let shown

  if (required.length >= limit) {
    shown = required.slice(0, limit)
  } else {
    /* Slot sisa: paling bulat lebih dulu, seri diputus nominal terkecil. */
    const rest = all
      .filter(({ amount }) => !required.includes(amount))
      .sort(
        (left, right) =>
          cashRoundness(right.amount) - cashRoundness(left.amount) || left.amount - right.amount,
      )

    shown = [...required, ...rest.slice(0, limit - required.length).map(({ amount }) => amount)]
  }

  shown.sort((left, right) => left - right)

  return shown.map((amount) => {
    const { combo } = byAmount.get(amount)

    return {
      amount,
      change: amount - value,
      exact: amount === exact,
      roundness: cashRoundness(amount),
      pieces: combo.length,
    }
  })
}

export default {
  CASH_DENOMINATIONS,
  CASH_UNIT,
  DEFAULT_SUGGESTION_COUNT,
  roundUpTo,
  nextMultipleAbove,
  cashRoundness,
  payableCashAmount,
  cashBreakdown,
  isMinimumCombination,
  minimalCashPayments,
  suggestCashAmounts,
}
