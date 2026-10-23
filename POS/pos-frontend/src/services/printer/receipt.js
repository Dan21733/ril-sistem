/*
 * Penyusun baris nota untuk printer TM-U220B.
 *
 * Tata letaknya bukan desain baru: ini terjemahan baris-per-baris dari desain
 * yang sudah disetujui di POS/docs/desain_nota_75x65.pdf. PDF itu artefak beku
 * sejak generator `generate_invoice_pdf.py` dihapus, jadi kalau tata letaknya
 * berubah, PDF dan modul ini harus diubah bersama secara manual; batas 40 kolom
 * dijaga `receiptWidthIssues`.
 *
 * Bentuk "left right" (permintaan pemilik, 2026-09-30): label di kiri, nilainya
 * rata kanan pada kolom yang sama, dan baris barang memuat **jumlah** + **jumlah
 * akhir** (bukan harga satuan lalu hasil kalkulasi) supaya satu baris terbaca
 * sekali pandang: "1 Kunci Bulat x2          34.000".
 *
 * Aturan isi yang mengikat (POS/pos-frontend/AGENTS.md bagian 5 & 7):
 * - Hanya field yang memang tampil untuk kasir/pelanggan yang boleh masuk ke
 *   kertas. `assertPrintableSale` menolak payload yang membawa margin/HPP/harga
 *   floor, karena field itu tidak boleh keluar dari server ke tab kasir.
 * - Nama kasir di kertas berasal dari pekerja yang diatribusikan, bukan kolom
 *   ketikan bebas. Sampai daftar pekerja offline tersambung, nilai yang
 *   tersimpan di nota dipakai apa adanya.
 * - Nota antaran (surat jalan) adalah **dokumen kedua**, bukan baris tambahan di
 *   nota: satu kali perintah cetak, satu lembar, untuk transaksi yang diantar.
 *
 * Kenapa dua lembar nota berisi teks identik: printer ini satu kepala cetak,
 * satu kali jalan (PRINT_PATH_DESIGN.md §2). Label per-salinan tidak mungkin,
 * jadi tidak ada percabangan "lembar kedua" di sini.
 */

import { COLUMNS, toPrinterText } from './escpos'

export const RULE_EQ = '='.repeat(COLUMNS)
export const RULE_DASH = '-'.repeat(COLUMNS)

/* Lebar kolom uang di sisi kanan nota; 40 - 9 = 31 kolom untuk label/nama. */
export const MONEY_WIDTH = 9

/* Lebar terkecil yang masih layak untuk nama barang sebelum baris dipatahkan. */
export const MIN_NAME_WIDTH = 12

/* Label kolom kiri nota — sama dengan desain PDF. */
const LABEL_NOTA = 'No. Nota'
const LABEL_DATE = 'Tanggal'
const LABEL_CASHIER = 'Kasir'
const LABEL_CUSTOMER = 'Pelanggan'
const LABEL_CUSTOMER_PHONE = 'No. HP'
const LABEL_FULFILLMENT = 'Layanan'

/*
 * Jarak kepala cetak ke autocutter sekitar 13 mm, dan pada Font B kertas 76 mm
 * satu baris hanya ~3-4 mm. Karena itu baris terakhir nota baru melewati pisau
 * setelah beberapa baris umpan — kalau umpan terlalu sedikit, "Terima kasih /
 * Barang tidak dapat ditukar" ikut terpotong (lihat DEFAULT_FEED_LINES di
 * escpos.js).
 */

const PAYMENT_LABELS = {
  cash: 'CASH',
  edc: 'EDC',
  bank_transfer: 'TRANSFER',
}

const FULFILLMENT_LABELS = {
  pickup: 'AMBIL SENDIRI',
  delivery: 'DIANTAR',
}

/*
 * Field yang dilarang muncul di jalur cetak. Daftarnya sengaja eksplisit, bukan
 * pola: kalau suatu hari ada field baru dari server, gagal-loud di sini lebih
 * baik daripada diam-diam mencetak angka keuangan di nota pelanggan.
 */
const FORBIDDEN_KEYS = new Set([
  'margin',
  'margins',
  'modal',
  'modal_rata',
  'cost',
  'cost_amount',
  'cost_price',
  'hpp',
  'min_price',
  'floor_price',
  'purchase_price',
  'buy_price',
  'supplier',
  'net_sales',
  'profit',
  'stock_value',
])

/*
 * Baris tanda tangan surat jalan memakai dua kolom selebar 20 karakter supaya
 * "Penerima," dan "Hormat Kami," duduk di kiri dan kanan kertas, sama seperti
 * formulir toko.
 */
const HALF_WIDTH = Math.floor(COLUMNS / 2)

/* Teks kosong yang harus diisi tangan: digaris bawahi sampai ujung kanan. */
function handwriting(label) {
  const prefix = `${label} `

  return `${prefix}${'_'.repeat(Math.max(0, COLUMNS - prefix.length))}`
}

function collectKeys(value, keys, depth = 0) {
  if (depth > 6 || value === null || typeof value !== 'object') {
    return keys
  }

  if (Array.isArray(value)) {
    for (const entry of value) {
      collectKeys(entry, keys, depth + 1)
    }

    return keys
  }

  for (const [key, entry] of Object.entries(value)) {
    keys.add(String(key).toLocaleLowerCase())

    collectKeys(entry, keys, depth + 1)
  }

  return keys
}

/*
 * Dipanggil sebelum byte disusun. Melempar, bukan memperingatkan: nota yang
 * salah cetak tidak bisa ditarik kembali dari tangan pelanggan.
 */
export function assertPrintableSale(sale) {
  const keys = collectKeys(sale, new Set())
  const offending = [...keys].filter((key) => FORBIDDEN_KEYS.has(key))

  if (offending.length > 0) {
    throw new Error(`Payload cetak memuat field terlarang: ${offending.join(', ')}.`)
  }

  return sale
}

/*
 * Pemisah ribuan gaya nota: titik, tanpa "Rp" (desain memakai kolom sempit).
 */
export function rupiah(value) {
  const amount = Math.round(Number(value) || 0)

  return new Intl.NumberFormat('id-ID', { maximumFractionDigits: 0 }).format(amount)
}

function pad(value, width) {
  return String(value).padEnd(width).slice(0, width)
}

function alignRight(value, width) {
  const text = String(value)

  return text.length >= width ? text : text.padStart(width)
}

function center(text, width) {
  const value = String(text).slice(0, width)
  const left = Math.max(0, Math.floor((width - value.length) / 2))

  return pad(' '.repeat(left) + value, width)
}

/* Dua sel bersebelahan; dipakai baris tanda tangan surat jalan. */
function pairCells(left, right) {
  return `${center(left, HALF_WIDTH)}${center(right, COLUMNS - HALF_WIDTH)}`
}

/*
 * dd/mm/yy hh:mm supaya hemat kolom, diambil dari waktu lokal perangkat —
 * jam nota harus sama dengan jam yang dilihat kasir, bukan UTC.
 */
export function formatReceiptDate(value) {
  const date = value instanceof Date ? value : new Date(value)

  if (Number.isNaN(date.getTime())) {
    return ''
  }

  const pad2 = (number) => String(number).padStart(2, '0')

  return (
    `${pad2(date.getDate())}/${pad2(date.getMonth() + 1)}/${String(date.getFullYear()).slice(-2)} ` +
    `${pad2(date.getHours())}:${pad2(date.getMinutes())}`
  )
}

/*
 * yyyy-mm-dd hh:mm:ss untuk surat jalan. Formulir pengiriman memakai tanggal
 * yang bisa dibaca apa adanya (dan bisa ditulis tangan di jalan), jadi tidak
 * dipendekkan seperti nota.
 */
export function formatSlipDate(value) {
  const date = value instanceof Date ? value : new Date(value)

  if (Number.isNaN(date.getTime())) {
    return ''
  }

  const pad2 = (number) => String(number).padStart(2, '0')

  return (
    `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ` +
    `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`
  )
}

/*
 * Nomor surat jalan ikut nomor nota supaya dua dokumen itu bisa dicocokkan
 * tanpa bertanya: T01-000123 -> SJ000123. Kalau notanya belum bernomor (jatah
 * nomor device habis, KEPUTUSAN-8), nomornya jatuh ke cap waktu lokal supaya
 * tetap unik per lembar.
 */
export function buildSlipNumber(sale) {
  const transactionId = String(sale?.transaction_id || '')
  const match = transactionId.match(/-(\d+)$/)

  if (match) {
    return `SJ${match[1].padStart(6, '0')}`
  }

  const stamp = formatSlipDate(sale?.transaction_time || new Date())

  return `SJ${stamp.replace(/[-: ]/g, '')}`
}

/*
 * Nomor dokumen di kepala surat jalan. Bentuknya mengikuti formulir toko
 * (kode toko + cap waktu). Kode toko datang dari `VITE_STORE_CODE`; kalau belum
 * diisi, yang tercetak hanya cap waktunya — lebih baik daripada kode karangan.
 */
export function buildSlipDocumentNumber(sale, store = {}) {
  const code = toPrinterText(store.code || '').trim()
  const stamp = formatSlipDate(sale?.transaction_time || new Date()).replace(/[-: ]/g, '')

  return `${code}${stamp}`
}

/*
 * Bungkus teks pada lebar tertentu; kata yang lebih panjang daripada lebar kolom
 * dipotong paksa supaya tidak ada karakter yang hilang.
 */
export function wrapText(text, width) {
  const words = toPrinterText(text).split(/\s+/).filter(Boolean)
  const lines = []
  let current = ''

  for (const word of words) {
    const candidate = current ? `${current} ${word}` : word

    if (candidate.length <= width) {
      current = candidate
      continue
    }

    if (current) {
      lines.push(current)
    }

    let remainder = word

    while (remainder.length > width) {
      lines.push(remainder.slice(0, width))
      remainder = remainder.slice(width)
    }

    current = remainder
  }

  if (current) {
    lines.push(current)
  }

  return lines.length > 0 ? lines : ['']
}

function pushLine(lines, text, { align = 'left', bold = false } = {}) {
  lines.push({ text: String(text).slice(0, COLUMNS), align, bold })
}

/* Baris kosong; dipakai pemisah blok di surat jalan. */
function pushBlank(lines) {
  lines.push({ text: '', align: 'left', bold: false })
}

function pushCentered(lines, text, { bold = false } = {}) {
  for (const line of wrapText(text, COLUMNS)) {
    pushLine(lines, line, { align: 'center', bold })
  }
}

/*
 * Baris "label kiri, nilai kanan" — bentuk left-right permintaan pemilik. Kalau
 * label + nilainya tidak muat satu baris, nilainya turun ke baris berikutnya dan
 * tetap rata kanan: nilai yang terpotong lebih berbahaya daripada tata letak
 * yang kurang rapi.
 */
function pushPair(lines, label, value, { bold = false } = {}) {
  const text = toPrinterText(value ?? '').trim()

  if (!text) {
    return
  }

  const labelText = toPrinterText(label)
  const valueText = text.length <= COLUMNS ? text : text.slice(0, COLUMNS)

  if (labelText.length + 1 + valueText.length <= COLUMNS) {
    pushLine(lines, `${labelText}${' '.repeat(COLUMNS - labelText.length - valueText.length)}${valueText}`, {
      bold,
    })

    return
  }

  pushLine(lines, labelText, { bold })

  for (const line of wrapText(valueText, COLUMNS)) {
    pushLine(lines, alignRight(line, COLUMNS))
  }
}

/*
 * Baris uang: label kiri, angka rata kanan pada 9 kolom. Kalau angkanya lebih
 * panjang daripada kolomnya (nota ratusan juta ke atas), label pindah ke baris
 * sendiri dan angkanya tetap utuh — angka yang salah lebih berbahaya daripada
 * tata letak yang kurang rapi.
 */
function pushMoney(lines, label, value) {
  const text = rupiah(value)

  if (text.length <= MONEY_WIDTH) {
    pushLine(lines, `${pad(label, COLUMNS - MONEY_WIDTH)}${alignRight(text, MONEY_WIDTH)}`)

    return
  }

  pushLine(lines, label)

  if (text.length <= COLUMNS) {
    pushLine(lines, alignRight(text, COLUMNS))
    return
  }

  /* Nilai di luar dugaan: biarkan encoder mematahkannya per 40 kolom. */
  pushLine(lines, text)
}

function itemAmount(item) {
  const quantity = Number(item.quantity) || 0
  const unitPrice = Number(item.unit_price ?? item.price) || 0

  return item.subtotal != null ? Number(item.subtotal) : unitPrice * quantity
}

function itemIdentity(item) {
  return [item.item_name, item.variant && item.variant !== 'N/A' ? `- ${item.variant}` : '']
    .filter(Boolean)
    .join(' ')
}

/*
 * Satu baris barang: "1 Kunci Bulat x2" di kiri, jumlah akhirnya rata kanan.
 * Harga satuan sengaja tidak dicetak lagi (permintaan pemilik, 2026-09-30):
 * yang dibutuhkan pelanggan adalah berapa banyak dan berapa bayarnya.
 */
function pushItem(lines, number, item) {
  const quantity = Number(item.quantity) || 0
  const amount = itemAmount(item)
  const qtyTag = ` x${quantity}`
  const prefix = `${number} `

  const nameWidth = Math.max(
    MIN_NAME_WIDTH,
    COLUMNS - MONEY_WIDTH - qtyTag.length - prefix.length,
  )

  const chunks = wrapText(itemIdentity(item), nameWidth)
  const head = `${prefix}${chunks[0]}${qtyTag}`
  const money = alignRight(rupiah(amount), MONEY_WIDTH)

  if (head.length + 1 + MONEY_WIDTH <= COLUMNS) {
    pushLine(lines, `${pad(head, COLUMNS - MONEY_WIDTH)}${money}`)
  } else {
    pushLine(lines, head)
    pushLine(lines, money)
  }

  /* Sisa nama dicetak dengan indentasi dua spasi; jumlah hanya di baris pertama. */
  const remainder = chunks.slice(1).join(' ')

  if (remainder) {
    for (const line of wrapText(remainder, COLUMNS - 2)) {
      pushLine(lines, `  ${line}`)
    }
  }
}

/* Total nota; dipakai nota dan bisa dipakai pemeriksa lebar baris. */
function totalsOf(sale) {
  const items = Array.isArray(sale.items) ? sale.items : []
  const itemAmountValue = items.reduce((sum, item) => sum + itemAmount(item), 0)
  const discount = Number(sale.discount_amount ?? 0)
  const shipping = Number(sale.shipping_amount ?? 0)
  const total =
    sale.total_amount != null ? Number(sale.total_amount) : itemAmountValue + shipping - discount
  const paymentAmount =
    sale.payment_amount != null ? Number(sale.payment_amount) : itemAmountValue + shipping - discount

  return { itemAmount: itemAmountValue, discount, shipping, total, paymentAmount }
}

/*
 * Kata kunci status aksi di kertas cetak ulang. Sengaja huruf besar dan
 * dibingkai garis supaya kasir tidak salah membaca lembar lama sebagai nota
 * sah saat pelanggan membawanya kembali.
 */
const ACTION_BANNERS = {
  void: ['*** NOTA VOID / DIBATALKAN ***'],
  returned: ['*** SUDAH ADA RETUR BARANG ***'],
  refunded: ['*** SUDAH ADA REFUND UANG ***'],
}

/*
 * Nota lengkap satu transaksi. `sale` adalah snapshot yang dibuat saat
 * finalisasi, `store` identitas toko (storeProfile.js).
 */
export function buildReceiptLines(sale, store = {}) {
  assertPrintableSale(sale)

  const lines = []

  pushCentered(lines, store.name || 'TOKO', { bold: true })

  for (const detail of [store.address, store.phone]) {
    const text = toPrinterText(detail || '').trim()

    if (text) {
      pushCentered(lines, text)
    }
  }

  pushLine(lines, RULE_EQ)

  /*
   * Status aksi wajib terlihat saat nota dicetak ulang dari riwayat: nota yang
   * sudah dibatalkan atau sudah diretur/direfund tidak boleh beredar seolah
   * penjualan yang masih berlaku. Nota baru tidak membawa `refund_status`
   * (undefined), jadi kertas transaksi normal tidak berubah sama sekali.
   */
  const actionBanner = ACTION_BANNERS[sale.refund_status]

  if (actionBanner) {
    for (const text of actionBanner) {
      pushLine(lines, text, { align: 'center', bold: true })
    }

    pushLine(lines, RULE_EQ)
  }

  /*
   * Nota tanpa nomor berarti jatah nomor device habis (KEPUTUSAN-8). Kertasnya
   * tetap harus memberi tahu kasir bahwa nomor aslinya belum ada, karena nomor
   * itu yang dipakai untuk nota tulis tangan.
   */
  pushPair(lines, LABEL_NOTA, sale.transaction_id || '(belum bernomor)')

  const stamp = formatReceiptDate(sale.transaction_time || new Date())

  if (stamp) {
    pushPair(lines, LABEL_DATE, stamp)
  }

  const cashier = toPrinterText(sale.cashier_name || '').trim()

  if (cashier) {
    pushPair(lines, LABEL_CASHIER, cashier)
  }

  /*
   * Nama pelanggan wajib untuk nota baru (CUSTOMER_DESIGN.md); nota lama
   * (cust_name NULL) mencetak tanpa baris ini seperti dulu. No. HP hanya
   * bila dicatat.
   */
  const customer = toPrinterText(sale.cust_name || '').trim()

  if (customer) {
    pushPair(lines, LABEL_CUSTOMER, customer)
  }

  const customerPhone = toPrinterText(sale.phone_no || '').trim()

  if (customerPhone) {
    pushPair(lines, LABEL_CUSTOMER_PHONE, customerPhone)
  }

  /*
   * Status pengambilan wajib ada di setiap nota ("tidak boleh kosong"), jadi
   * kalau nilainya tidak dikenal pun tetap dicetak apa adanya.
   */
  const fulfillment = FULFILLMENT_LABELS[sale.fulfillment] || sale.fulfillment || ''

  pushPair(lines, LABEL_FULFILLMENT, fulfillment)

  pushLine(lines, RULE_DASH)

  const items = Array.isArray(sale.items) ? sale.items : []

  items.forEach((item, index) => {
    pushItem(lines, index + 1, item)
  })

  pushLine(lines, RULE_DASH)

  const { itemAmount: itemTotal, discount, shipping, total, paymentAmount } = totalsOf(sale)

  pushMoney(lines, 'Subtotal', itemTotal)

  if (discount > 0) {
    pushMoney(lines, 'Diskon', -discount)
  }

  /* Ongkir jadi baris tersendiri di nota (AGENTS.md bagian 3). */
  if (shipping > 0) {
    pushMoney(lines, 'Ongkir', shipping)
  }

  pushMoney(lines, 'TOTAL', total)

  const paymentLabel = PAYMENT_LABELS[sale.payment_method] || String(sale.payment_method || '').toUpperCase()

  pushMoney(lines, `Bayar ${paymentLabel}`.trim(), paymentAmount)

  if (sale.payment_method === 'cash') {
    pushMoney(lines, 'Kembali', paymentAmount - total)
  }

  pushLine(lines, RULE_DASH)
  pushLine(lines, 'Terima kasih', { align: 'center' })
  pushLine(lines, 'Barang tidak dapat ditukar', { align: 'center' })

  return lines
}

/*
 * Nota antaran (surat jalan) untuk transaksi berstatus DIANTAR.
 *
 * Dipakai sebagai dokumen kedua yang menemani barang keluar toko: satu lembar
 * per transaksi, bukan rangkap, dan tanpa satu pun harga — yang dibawa kurir
 * cukup jenis dan jumlah barangnya.
 *
 * Yang sengaja dibiarkan kosong untuk diisi tangan: nama penerima (Kepala YTH),
 * nomor HP, alamat tujuan, kendaraan, dan nomor polisi. POS tidak punya field
 * untuk semuanya, dan menebaknya di atas kertas jauh lebih berbahaya daripada
 * menyediakan garis isian.
 */
export function buildDeliverySlipLines(sale, store = {}) {
  const lines = []

  pushCentered(lines, store.name || 'TOKO', { bold: true })

  for (const detail of [store.address, store.phone]) {
    const text = toPrinterText(detail || '').trim()

    if (text) {
      pushCentered(lines, text)
    }
  }

  pushCentered(lines, buildSlipDocumentNumber(sale, store))

  pushLine(lines, RULE_DASH)
  pushLine(lines, 'SURAT JALAN', { bold: true })

  const stamp = formatSlipDate(sale.transaction_time || new Date())

  if (stamp) {
    pushLine(lines, `Tanggal : ${stamp}`)
  }

  pushLine(lines, `Nomor Surat Jalan : ${buildSlipNumber(sale)}`)

  pushBlank(lines)

  /*
   * Nama + No. HP pelanggan sudah terisi otomatis dari nota
   * (CUSTOMER_DESIGN.md); nota lama tanpa data pelanggan kembali ke garis
   * tulis tangan seperti dulu. Alamat tetap tulis tangan: kebutuhan
   * alamat/zona yang dipersist belum didesain.
   */
  const slipCustomer = toPrinterText(sale.cust_name || '').trim()
  const slipPhone = toPrinterText(sale.phone_no || '').trim()

  if (slipCustomer) {
    pushLine(lines, `Kepala YTH : ${slipCustomer}`.slice(0, COLUMNS))
  } else {
    pushLine(lines, handwriting('Kepala YTH :'))
  }
  pushBlank(lines)
  if (slipPhone) {
    pushLine(lines, `No Hp : ${slipPhone}`.slice(0, COLUMNS))
  } else {
    pushLine(lines, handwriting('No Hp :'))
  }
  pushBlank(lines)
  pushLine(lines, handwriting('Alamat :'))
  pushBlank(lines)
  pushLine(lines, '_'.repeat(COLUMNS))

  pushBlank(lines)

  for (const line of wrapText(
    'Kami kirimkan barang - barang tersebut dibawah ini dengan',
    COLUMNS,
  )) {
    pushLine(lines, line)
  }

  pushBlank(lines)
  pushLine(lines, handwriting('Kendaraan:'))
  pushBlank(lines)
  pushLine(lines, handwriting('No.Polisi:'))

  pushBlank(lines)

  const items = Array.isArray(sale.items) ? sale.items : []

  items.forEach((item) => {
    const quantity = Number(item.quantity) || 0
    const prefix = `${quantity}  `
    const chunks = wrapText(itemIdentity(item), COLUMNS - prefix.length)

    pushLine(lines, `${prefix}${chunks[0]}`)

    for (const line of chunks.slice(1)) {
      pushLine(lines, `   ${line}`)
    }
  })

  pushLine(lines, RULE_DASH)

  /* Bagian tanda tangan: nama penerima dan nama toko, keduanya ditulis tangan. */
  pushLine(lines, pairCells('Penerima,', 'Hormat Kami,'))
  pushBlank(lines)
  pushBlank(lines)
  pushBlank(lines)
  pushLine(lines, pairCells('(...........)', '(...........)'))

  return lines
}

/*
 * Baris yang melewati lebar kertas. Dijadikan fungsi terpisah supaya bisa
 * diperiksa tanpa mencetak (lihat POS/docs/… dan pemeriksaan node di repo).
 */
export function receiptWidthIssues(lines, columns = COLUMNS) {
  return lines.filter((line) => String(line?.text ?? line).length > columns)
}

export function assertReceiptWidth(lines, columns = COLUMNS) {
  const issues = receiptWidthIssues(lines, columns)

  if (issues.length > 0) {
    throw new Error(
      `Baris nota melebihi ${columns} kolom: ${issues.map((line) => line.text).join(' | ')}`,
    )
  }

  return lines
}

export default {
  RULE_EQ,
  RULE_DASH,
  MONEY_WIDTH,
  MIN_NAME_WIDTH,
  assertPrintableSale,
  assertReceiptWidth,
  receiptWidthIssues,
  formatReceiptDate,
  formatSlipDate,
  buildSlipNumber,
  buildSlipDocumentNumber,
  rupiah,
  wrapText,
  buildReceiptLines,
  buildDeliverySlipLines,
}
