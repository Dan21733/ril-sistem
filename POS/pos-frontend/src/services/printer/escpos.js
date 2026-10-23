/*
 * Encoder ESC/POS untuk Epson TM-U220B (POS/pos-backend/PRINT_PATH_DESIGN.md).
 *
 * Kenapa ada: browser tidak bisa membuka TCP mentah ke port 9100 printer. Yang
 * dikirim POS ke print bridge adalah byte ESC/POS jadi, dan bridge hanya
 * meneruskannya. Karena itu byte harus disusun di sini, bukan di bridge.
 *
 * Batas perangkat yang mengikat (bukan pilihan kita, lihat dokumen §1):
 * - Font B pada kertas 76 mm = 40 kolom per baris. Semua baris dipotong/patah
 *   di 40 karakter supaya tidak ada teks yang hilang di kertas.
 * - Satu kepala cetak, satu kali jalan: dua lembar (asli + karbon) berisi teks
 *   identik. Tidak ada perintah "khusus lembar kedua".
 * - Autocutter ada di Type B, tidak ada di Type D. Karena itu nota diakhiri
 *   umpan beberapa baris lalu perintah potong `GS V`; pada unit Type D perintah
 *   itu diabaikan dan kasir menyobek manual — desainnya tetap sama.
 *
 * Encoder ini tidak tahu apa itu transaksi: masuknya daftar baris, keluarnya
 * byte. Penyusunan isi nota ada di `receipt.js`.
 */

/* Font B kertas 76 mm: 40 kolom. Batas keras, bukan preferensi. */
export const COLUMNS = 40

/*
 * Halaman kode printer disetel ke PC437 (bawaan pabrik TM-U220), jadi yang
 * dikirim hanya byte ASCII 0x20-0x7E. Karakter di luar itu diterjemahkan di
 * `toPrinterText` supaya nama barang tidak berubah jadi sampah di kertas.
 */
export const CODEPAGE = 'PC437'

export const STYLE = {
  LEFT: 'left',
  CENTER: 'center',
  MONEY: 'money',
  BOLD: 'bold',
}

/*
 * Umpan akhir sebelum perintah potong.
 *
 * Jarak kepala cetak ke autocutter TM-U220 sekitar 13 mm, sementara Font B di
 * kertas 76 mm hanya memakai 3-4 mm per baris. Umpan 4 baris (nilai lama) karena
 * itu tidak selalu cukup: baris penutup nota — "Terima kasih" dan "Barang tidak
 * dapat ditukar" — terpotong sebelum sempat lewat pisau (laporan pemilik,
 * 2026-09-30). Delapan baris memberi margin aman di kedua tipe unit; kalau
 * kertasnya ternyata terlalu panjang, angka ini satu-satunya yang perlu diubah.
 */
export const DEFAULT_FEED_LINES = 8

const CMD = {
  /* ESC @ — reset printer ke kondisi awal. */
  INIT: [0x1b, 0x40],
  /* ESC M 1 — pilih Font B. */
  FONT_B: [0x1b, 0x4d, 0x01],
  /* ESC a n — perataan. */
  ALIGN_LEFT: [0x1b, 0x61, 0x00],
  ALIGN_CENTER: [0x1b, 0x61, 0x01],
  /* ESC E n — tebal. */
  BOLD_ON: [0x1b, 0x45, 0x01],
  BOLD_OFF: [0x1b, 0x45, 0x00],
  LF: [0x0a],
}

/*
 * Pemetaan karakter yang sering muncul dari input manusia ke padanan ASCII-nya.
 * Sumbernya data nama barang/variant yang diketik di backoffice, jadi tanda
 * kutip melengkung dan tanda pisah panjang lebih sering muncul daripada aksen.
 */
const CHAR_FALLBACKS = new Map([
  ['\u00a0', ' '],
  ['\u2018', "'"],
  ['\u2019', "'"],
  ['\u201c', '"'],
  ['\u201d', '"'],
  ['\u2013', '-'],
  ['\u2014', '-'],
  ['\u2026', '...'],
  ['\u00b7', '.'],
  ['\u00d7', 'x'],
  ['\u2212', '-'],
  ['\u2022', '-'],
  /*
   * Pecahan diberi spasi di depan supaya "1½" menjadi "1 1/2", bukan "11/2" —
   * yang terbaca sebagai sebelas per dua, bukan satu setengah.
   */
  ['\u00bd', ' 1/2'],
  ['\u00bc', ' 1/4'],
  ['\u00be', ' 3/4'],
])

/*
 * Satu-satunya tempat teks diubah jadi byte. Alasannya bukan kerapian: byte
 * yang tidak dikenal printer bisa keluar sebagai karakter acak atau, lebih
 * buruk, sebagai perintah yang tidak diminta. Jadi semua yang di luar
 * 0x20-0x7E dibuang setelah aksennya dilepas.
 */
export function toPrinterText(value) {
  const text = String(value ?? '')

  const normalized = text
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .split('')
    .map((character) => CHAR_FALLBACKS.get(character) ?? character)
    .join('')

  let output = ''

  for (const character of normalized) {
    const code = character.codePointAt(0)

    if (code === 0x0a) {
      output += '\n'
      continue
    }

    if (code >= 0x20 && code <= 0x7e) {
      output += character
      continue
    }

    /*
     * `?` lebih jujur daripada spasi kosong: kasir langsung tahu ada karakter
     * yang tidak bisa dicetak, bukan melihat nama barang yang tampak benar.
     */
    output += '?'
  }

  return output
}

/*
 * Patahkan teks panjang supaya tidak ada baris melebihi lebar kertas. Ini jaring
 * pengaman terakhir: `receipt.js` sudah membungkus nama barang, tapi nilai yang
 * datang dari data server (nama toko, nama kasir) tidak boleh membuat nota
 * kehilangan kolom.
 */
export function hardWrap(text, columns = COLUMNS) {
  const width = Math.max(1, Number(columns) || COLUMNS)
  const lines = []

  for (const rawLine of String(text ?? '').split('\n')) {
    if (rawLine.length <= width) {
      lines.push(rawLine)
      continue
    }

    for (let start = 0; start < rawLine.length; start += width) {
      lines.push(rawLine.slice(start, start + width))
    }
  }

  return lines
}

/*
 * Normalisasi satu baris nota: teks printer + perataan + gaya. Dipakai
 * `receipt.js` dan antrean cetak supaya bentuk barisnya satu definisi saja.
 */
export function normalizeLine(line) {
  /*
   * Transliterasi dilakukan di sini, bukan hanya di `receipt.js`: encoder ini
   * satu-satunya yang mengubah teks jadi byte, jadi ia yang harus menjamin
   * tidak ada karakter di luar PC437 yang lolos — termasuk untuk baris yang
   * dikirim sebagai string biasa.
   */
  if (typeof line === 'string') {
    return { text: toPrinterText(line), align: 'left', bold: false }
  }

  return {
    text: toPrinterText(line?.text ?? ''),
    align: line?.align === 'center' ? 'center' : 'left',
    bold: Boolean(line?.bold),
  }
}

function pushBytes(target, bytes) {
  for (const byte of bytes) {
    target.push(byte & 0xff)
  }
}

/*
 * Susun satu pekerjaan cetak penuh: reset, Font B, baris nota, umpan akhir, lalu
 * potong. `feedLines` wajib lebih besar daripada jarak kepala cetak ke pisau
 * (DEFAULT_FEED_LINES) supaya baris penutup tidak terpotong; kalau unitnya Type D
 * (tanpa cutter) baris kosong ini tetap berguna sebagai batas sobek.
 */
export function encodeReceipt(lines, { feedLines = DEFAULT_FEED_LINES, cut = true } = {}) {
  const bytes = []

  pushBytes(bytes, CMD.INIT)
  pushBytes(bytes, CMD.FONT_B)
  pushBytes(bytes, CMD.ALIGN_LEFT)

  let bold = false

  for (const rawLine of lines) {
    const line = normalizeLine(rawLine)

    if (line.bold !== bold) {
      pushBytes(bytes, line.bold ? CMD.BOLD_ON : CMD.BOLD_OFF)
      bold = line.bold
    }

    pushBytes(bytes, line.align === 'center' ? CMD.ALIGN_CENTER : CMD.ALIGN_LEFT)

    for (const textLine of hardWrap(line.text)) {
      for (const character of textLine) {
        pushBytes(bytes, [character.charCodeAt(0)])
      }

      pushBytes(bytes, CMD.LF)
    }
  }

  if (bold) {
    pushBytes(bytes, CMD.BOLD_OFF)
  }

  pushBytes(bytes, CMD.ALIGN_LEFT)

  for (let index = 0; index < Math.max(0, feedLines); index += 1) {
    pushBytes(bytes, CMD.LF)
  }

  if (cut) {
    /*
     * GS V 0 — potong penuh. Pada unit tanpa autocutter (Type D) perintah ini
     * tidak berefek; jangan sampai jalur cetak bergantung padanya.
     */
    pushBytes(bytes, [0x1d, 0x56, 0x00])
  }

  return Uint8Array.from(bytes)
}

/*
 * Baris nota -> { jobId, bytes }. `jobId` wajib sama untuk percobaan ulang job
 * yang sama supaya bridge bisa mengenali kiriman kembar (dokumen §5).
 */
export function buildPrintJob(lines, { jobId, feedLines = DEFAULT_FEED_LINES, cut = true } = {}) {
  return {
    job_id: jobId,
    lines: lines.map((line) => normalizeLine(line)),
    bytes: encodeReceipt(lines, { feedLines, cut }),
  }
}

export default {
  COLUMNS,
  CODEPAGE,
  STYLE,
  DEFAULT_FEED_LINES,
  toPrinterText,
  hardWrap,
  normalizeLine,
  encodeReceipt,
  buildPrintJob,
}
