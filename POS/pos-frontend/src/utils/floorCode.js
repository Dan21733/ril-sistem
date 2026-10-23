/*
 * Kode harga floor untuk keranjang.
 *
 * Kenapa ada: layar kasir terlihat pelanggan. Menampilkan "Floor Rp 85.000"
 * sebagai angka membuat batas bawah harga langsung terbaca pelanggan, padahal
 * aturan produk hanya membolehkan kasir tahu harga floor. Karena itu harga
 * floor ditampilkan sebagai kode pendek: kasir bisa membukanya, pelanggan
 * hanya melihat rangkaian huruf-angka.
 *
 * Kode ini PENGABURAN, bukan keamanan: siapa pun yang tahu skemanya bisa
 * membalikkannya. Harga floor tetap tidak boleh dianggap rahasia teknis.
 *
 * Skema (deterministik, harga sama -> kode sama supaya kasir cepat hafal):
 * 1. Harga floor diwakili angka + satuan bulat terbesarnya, jadi 85.000 cukup
 *    ditulis "85" + penanda ribuan (G).
 * 2. Satuan dibaca dengan kata kunci CANGKIR, dari besar ke kecil:
 *    C juta, A ratus ribu, N puluh ribu, G ribu, K ratus, I puluh, R rupiah.
 * 3. Kalau angkanya lebih dari 4 digit (misalnya 1.405.700 yang hanya bulat
 *    dibagi 100), harga dibulatkan ke satuan terkecil yang masih muat 4 digit
 *    dan kode diberi awalan "~" supaya tidak terlihat lebih presisi daripada
 *    datanya.
 * 4. Huruf pengisi dikumpulkan di depan, lalu angka ditulis berdampingan
 *    diikuti langsung huruf penanda satuan (mis. "3A" = 3 x 100rb), lalu
 *    dua huruf pemeriksa di belakang. Angka + penanda yang bersebelahan
 *    ini membuat kasir membaca nilai tanpa mengurai huruf selingan.
 *
 * Contoh: harga floor 85.000 -> "85" ribuan -> penanda G.
 */

/*
 * Huruf pengisi tidak memakai I dan O karena mudah tertukar dengan 1 dan 0.
 * I tetap boleh muncul sebagai penanda satuan (I = puluh) karena angka dan
 * huruf tidak pernah tertukar saat dibaca: digit selalu membentuk satu blok
 * dan penanda adalah huruf pertama tepat setelah blok digit.
 */
const CODE_LETTERS = 'ABCDEFGHJKLMNPQRSTUVWXYZ'

/*
 * Harga dibulatkan ke satuan terkecil yang masih muat 4 digit; 4 digit adalah
 * batas supaya kode masih nyaman dibaca di kolom keranjang.
 */
const MAX_CODE_DIGITS = 4

/*
 * Kata kunci satuan, urut dari terbesar ke terkecil.
 */
const FLOOR_UNITS = [
  { divisor: 1000000, marker: 'C', label: '×1jt' },
  { divisor: 100000, marker: 'A', label: '×100rb' },
  { divisor: 10000, marker: 'N', label: '×10rb' },
  { divisor: 1000, marker: 'G', label: '×1rb' },
  { divisor: 100, marker: 'K', label: '×100' },
  { divisor: 10, marker: 'I', label: '×10' },
  { divisor: 1, marker: 'R', label: '×1' },
]

/*
 * Huruf pengisi dihitung dari angkanya, bukan dari waktu/acak, supaya kode
 * untuk harga yang sama selalu sama di semua perangkat. Posisi pengisi
 * dipertahankan dari skema lama (pengisi ke-i tetap dari seed + i), sehingga
 * kode lama dan baru hanya bertukar letak penanda dengan pengisi di
 * sebelahnya: E3XAH lama menjadi E3AXH.
 */
function fillerLetter(seed, position, avoid) {
  const letter = CODE_LETTERS[(seed * 31 + position * 17 + 7) % CODE_LETTERS.length]

  /*
   * Huruf pengisi tidak boleh sama dengan penanda satuan: "P3GKK" terbaca
   * seperti salah ketik, bukan kode.
   */
  if (avoid && letter === avoid) {
    return CODE_LETTERS[(CODE_LETTERS.indexOf(letter) + 1) % CODE_LETTERS.length]
  }

  return letter
}

function seedFrom(digits, unitIndex) {
  let seed = unitIndex * 7 + digits.length

  for (const digit of digits) {
    seed = (seed * 9 + Number(digit)) % 997
  }

  return seed
}

/*
 * Bagi harga dengan satuan yang dipilih; kembalikan { unitIndex, digits,
 * rounded }.
 */
function pickRepresentation(price) {
  /*
   * Sebagian besar harga floor toko bulat (ribuan/ratusan), jadi coba dulu
   * pembagian persis dengan satuan terbesar.
   */
  const exactIndex = FLOOR_UNITS.findIndex((unit) => price % unit.divisor === 0)
  const exactDigits = String(price / FLOOR_UNITS[exactIndex].divisor)

  if (exactDigits.length <= MAX_CODE_DIGITS) {
    return { unitIndex: exactIndex, digits: exactDigits, rounded: false }
  }

  /*
   * Harga "tidak bulat" (misal 1.405.700): ambil satuan terkecil yang hasil
   * pembulatannya masih muat 4 digit, supaya galatnya sekecil mungkin dan
   * tetap diberi tanda "~".
   */
  for (let index = FLOOR_UNITS.length - 1; index >= 0; index -= 1) {
    const digits = String(Math.round(price / FLOOR_UNITS[index].divisor))

    if (digits.length <= MAX_CODE_DIGITS) {
      return { unitIndex: index, digits, rounded: true }
    }
  }

  /*
   * Harga di atas puluhan juta: pakai satuan terbesar apa adanya; kode boleh
   * lebih panjang daripada melewatkan angkanya.
   */
  return {
    unitIndex: 0,
    digits: String(Math.round(price / FLOOR_UNITS[0].divisor)),
    rounded: true,
  }
}

/*
 * Kembalikan kode untuk satu harga floor, atau string kosong kalau harga
 * floor-nya tidak tersedia / tidak masuk akal.
 */
export function encodeFloorCode(floorPrice) {
  const value = Number(floorPrice)

  if (!Number.isFinite(value) || value <= 0) {
    return ''
  }

  const representation = pickRepresentation(Math.round(value))
  const unit = FLOOR_UNITS[representation.unitIndex]
  const { digits } = representation
  const seed = seedFrom(digits, representation.unitIndex)

  let code = ''

  for (let index = 0; index < digits.length; index += 1) {
    code += fillerLetter(seed, index)
  }

  code += digits
  code += unit.marker
  code += fillerLetter(seed, digits.length, unit.marker)
  code += fillerLetter(seed, digits.length + 1 + representation.unitIndex, unit.marker)

  return representation.rounded ? `~${code}` : code
}

/*
 * Kode dengan awalan "~" berarti harga floor dibulatkan (lihat
 * pickRepresentation).
 */
export function isRoundedFloorCode(code) {
  return typeof code === 'string' && code.startsWith('~')
}

/*
 * Kata kunci yang dihafal kasir: huruf penanda dibaca berurutan dari juta ke
 * rupiah.
 */
export const FLOOR_CODE_MNEMONIC = 'CANGKIR'

/*
 * Legenda singkat untuk ditampilkan di keranjang.
 */
export const FLOOR_CODE_LEGEND = FLOOR_UNITS.map((unit) => `${unit.marker} ${unit.label}`).join(
  ' · ',
)

/*
 * Kode siap tampil untuk satu item keranjang.
 */
export function floorCodeFor(floorPrice) {
  return encodeFloorCode(floorPrice)
}
