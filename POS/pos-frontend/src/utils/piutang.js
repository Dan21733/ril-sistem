/*
 * DP & piutang di kasir (rancangan DP 2026-10-31, fase 2).
 *
 * Aturan dari master: setiap nota yang belum lunas wajib DP minimal (bawaan
 * 50% dari harga barang; barang tertentu bisa lebih tinggi). Di bawah minimal
 * hanya untuk pelanggan tempo dan tetap menunggu ACC pemilik. Server kasir
 * menerima nota apa adanya; master yang memutuskan ACC. Tablet menjaga supaya
 * kasir tidak bisa membuat DP kurang tanpa memilih pelanggan tempo.
 *
 * Aturan (persen bawaan, lama jatuh tempo, daftar pelanggan tempo) diambil
 * dari server dan disimpan di perangkat supaya checkout DP tetap jalan offline.
 */

const STORAGE_KEY = 'pos.aturanDp'
const BAWAAN = { dp_minimal_persen: 50, jatuh_tempo_hari: 14, pelanggan: [] }

export const STATUS_BARANG = [
  { value: 'sekarang', label: 'Dibawa sekarang' },
  { value: 'ambil', label: 'Titip di toko' },
  { value: 'pesan', label: 'Pesan dulu' },
]

export const STATUS_BARANG_LABEL = {
  bawa: 'Dibawa',
  antar: 'Diantar',
  ambil: 'Titip di toko',
  pesan: 'Pesan dulu',
}

function storage() {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null
  }
}

export function dpRules() {
  try {
    const data = JSON.parse(storage()?.getItem(STORAGE_KEY) || 'null')

    if (data && typeof data === 'object') {
      return {
        dp_minimal_persen: Number(data.dp_minimal_persen) || BAWAAN.dp_minimal_persen,
        jatuh_tempo_hari: Number(data.jatuh_tempo_hari) || BAWAAN.jatuh_tempo_hari,
        pelanggan: Array.isArray(data.pelanggan) ? data.pelanggan : [],
        toko_titipan: Array.isArray(data.toko_titipan) ? data.toko_titipan : [],
        metode_bayar: metodeBayarRapi(data.metode_bayar),
        daftar_hitam: Array.isArray(data.daftar_hitam) ? data.daftar_hitam.map(String) : [],
      }
    }
  } catch {
    /* Salinan rusak: pakai bawaan. */
  }

  return { ...BAWAAN, pelanggan: [], toko_titipan: [], metode_bayar: [...METODE_BAYAR], daftar_hitam: [] }
}

/* Cara bayar yang ditampilkan kasir (Aturan kasir di master, 2026-11-12). Salinan lama tanpa daftar = semua. */
export const METODE_BAYAR = ['cash', 'bank_transfer', 'edc', 'campur']

export function metodeBayarRapi(daftar) {
  if (!Array.isArray(daftar)) {
    return [...METODE_BAYAR]
  }
  const pilih = METODE_BAYAR.filter((m) => daftar.includes(m))
  return pilih.length ? pilih : ['cash']
}

export function noteDpRules(data) {
  if (!data || typeof data !== 'object') {
    return
  }

  try {
    storage()?.setItem(STORAGE_KEY, JSON.stringify({
      dp_minimal_persen: data.dp_minimal_persen,
      jatuh_tempo_hari: data.jatuh_tempo_hari,
      pelanggan: (Array.isArray(data.pelanggan) ? data.pelanggan : []).map((c) => ({
        id: c.id,
        nama: String(c.nama || ''),
        telepon: String(c.telepon || ''),
      })),
      /* Kop struk toko titipan (brand) untuk nota yang dipecah. */
      toko_titipan: (Array.isArray(data.toko_titipan) ? data.toko_titipan : []).map((t) => ({
        id: t.id,
        nama: String(t.nama || ''),
        alamat: String(t.alamat || ''),
        telepon: String(t.telepon || ''),
      })),
      metode_bayar: metodeBayarRapi(data.metode_bayar),
      /* No. HP yang tidak boleh COD lagi (pernah menolak bayar saat diantar). */
      daftar_hitam: (Array.isArray(data.daftar_hitam) ? data.daftar_hitam : []).map(String),
    }))
  } catch {
    /* Penyimpanan penuh: aturan lama tetap dipakai. */
  }
}

/*
 * Minimal DP satu nota = jumlah (subtotal barang x persen minimal barangnya),
 * dibulatkan ke atas. Ongkir tidak ikut (sama dengan master).
 */
export function minimalDp(items, persenBawaan = dpRules().dp_minimal_persen) {
  let total = 0

  for (const item of Array.isArray(items) ? items : []) {
    const harga = Number(item.unit_price ?? item.price ?? 0)
    const qty = Number(item.quantity ?? 0)
    const persen = Number(item.min_dp) > 0 ? Number(item.min_dp) : Number(persenBawaan)

    total += (harga * qty * persen) / 100
  }

  return Math.max(0, Math.ceil(total - 1e-9)) || 0
}

/* Tanggal lokal yyyy-mm-dd, `hari` hari dari `dari`. */
export function dueDateFrom(hari, dari = new Date()) {
  const tanggal = new Date(dari.getFullYear(), dari.getMonth(), dari.getDate() + Number(hari || 0))
  const pad2 = (n) => String(n).padStart(2, '0')

  return `${tanggal.getFullYear()}-${pad2(tanggal.getMonth() + 1)}-${pad2(tanggal.getDate())}`
}

/*
 * Data `piutang` untuk payload nota, atau null untuk nota biasa (lunas dan
 * barang dibawa/diantar sekarang).
 */
export function buildPiutangPayload({ total, dpMode, dp, statusBarang, delivery, pelangganId, jatuhTempo }) {
  const dibayar = dpMode ? Math.max(0, Math.min(Number(dp || 0), Number(total))) : Number(total)
  const status = statusBarang === 'ambil' || statusBarang === 'pesan'
    ? statusBarang
    : (delivery ? 'antar' : 'bawa')

  if (dibayar >= Number(total) && (status === 'bawa' || status === 'antar')) {
    return null
  }

  return {
    dibayar,
    status_barang: status,
    /* COD tidak punya jatuh tempo: sisanya ditagih hari itu juga saat diantar. */
    jatuh_tempo: dibayar < Number(total) && jatuhTempo && status !== 'antar' ? jatuhTempo : null,
    pelanggan_id: pelangganId ? Number(pelangganId) : null,
  }
}

/* Nomor HP untuk dicocokkan dengan daftar hitam: angka saja, awalan 62 jadi 0 (sama dengan master). */
export function teleponBaku(telepon) {
  let angka = String(telepon || '').replace(/\D/g, '')
  if (angka.startsWith('62')) {
    angka = `0${angka.slice(2)}`
  }
  return angka.length >= 8 ? angka : ''
}

/*
 * Status barang nota: bawa / antar / ambil (titip di toko) / pesan.
 * 'sekarang' di layar kasir = dibawa atau diantar, tergantung pengambilan.
 */
export function statusNota(statusBarang, delivery) {
  if (statusBarang === 'ambil' || statusBarang === 'pesan') {
    return statusBarang
  }
  return delivery ? 'antar' : 'bawa'
}

/*
 * Aturan bayar per jenis nota (SOP pemilik 12 Nov 2026). Hasil: '' kalau
 * boleh, atau pesan yang ditampilkan ke kasir. Master memeriksa ulang; nota
 * yang lolos dari tablet lama tetap wajib ACC pemilik.
 *
 * - bawa  : lunas; bayar sebagian hanya pelanggan tempo (invoice).
 * - antar : COD, bayar 0 sampai lunas; wajib No. HP; nomor di daftar hitam wajib lunas.
 * - ambil : titip di toko, wajib lunas.
 * - pesan : DP minimal; kurang dari itu hanya pelanggan tempo.
 */
export function masalahBayar({ status, dpMode, dp, total, minimal, pelangganTempo, telepon, daftarHitam = [] }) {
  if (!dpMode) {
    return ''
  }
  if (status === 'ambil') {
    return 'Titip di toko wajib lunas.'
  }
  if (dp === '' || dp === null || dp === undefined) {
    return status === 'antar' ? 'Isi uang yang dibayar di toko (boleh 0).' : 'Isi jumlah DP.'
  }
  const dibayar = Number(dp)
  if (dibayar >= Number(total)) {
    return 'Jumlahnya sama dengan total: pilih Lunas.'
  }
  if (status === 'antar') {
    const baku = teleponBaku(telepon)
    if (!baku) {
      return 'COD wajib No. HP pembeli.'
    }
    if (daftarHitam.includes(baku)) {
      return 'No. HP ini ada di daftar hitam COD (pernah menolak bayar). Wajib lunas di toko.'
    }
    return ''
  }
  if (status === 'pesan' && dibayar >= Number(minimal)) {
    return ''
  }
  if (!pelangganTempo) {
    return status === 'pesan'
      ? `DP pesanan minimal Rp${Number(minimal).toLocaleString('id-ID')}. Kurang dari itu hanya untuk pelanggan tempo.`
      : 'Barang dibawa wajib lunas. Bayar sebagian hanya untuk pelanggan tempo (invoice).'
  }
  return ''
}

/* Perlu memilih pelanggan tempo (nota jadi invoice)? */
export function perluTempo({ status, dpMode, dp, minimal }) {
  if (!dpMode || dp === '' || dp === null || dp === undefined) {
    return false
  }
  if (status === 'bawa') {
    return true
  }
  return status === 'pesan' && Number(dp) < Number(minimal)
}

export default {
  STATUS_BARANG,
  STATUS_BARANG_LABEL,
  dpRules,
  noteDpRules,
  minimalDp,
  dueDateFrom,
  buildPiutangPayload,
  teleponBaku,
  statusNota,
  masalahBayar,
  perluTempo,
}
