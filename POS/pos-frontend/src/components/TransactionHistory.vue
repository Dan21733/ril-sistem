<script setup>
import { computed, ref, watch } from 'vue'

import {
  createRefundEvent,
  fetchTransactionDetail,
  fetchTransactionHistory,
} from '../services/api/transactionApi'
import {
  flushPaymentProofs,
  resubmitPaymentProofAsNew,
  retryPaymentProofNow,
} from '../services/paymentProof/uploadQueue'
import {
  reprintReceiptFromHistory,
  summarizePrintSync,
} from '../services/printer/printQueue'
import { createUuid } from '../services/id'
import deviceRepository from '../services/storage/deviceRepository'
import {
  getPaymentProofsBySyncIds,
  listOutstandingPaymentProofs,
} from '../services/storage/paymentProofRepository'
import { getTransaction } from '../services/storage/transactionRepository'
import { listQueuedTransactions } from '../services/storage/transactionQueue'
import { getPrintJobsBySyncIds } from '../services/storage/printJobRepository'
import {
  readDismissedPrintSignature,
  readDismissedQueueSignature,
  saveDismissedPrintSignature,
  saveDismissedQueueSignature,
} from '../utils/dismissedNotice'
import { visibleBannerProofs } from '../utils/bannerProofs'
import {
  digitsOnly,
  formatRupiahInput,
  preventNonNumericInput,
} from '../utils/rupiahInput'

import LoadingSpinner from './LoadingSpinner.vue'
import vModalFocus from '../directives/modalFocus'

/*
 * Riwayat nota (keputusan visibilitas 2026-09-26 + refund 2026-10-05/10-06):
 * bawaan hanya nota device/tab ini; `scope=all` membuka semua device
 * tetapi wajib PIN kasir. Void/retur/refund dicatat dari panel detail —
 * hanya kasir nota asal yang boleh menyetujui (keputusan pemilik: retur
 * hanya transaksi sendiri + ACC master prosedural); approver-nya tercatat
 * di event.
 *
 * Istilah yang dipakai form (ARCHITECTURE.md):
 *   VOID   = batalkan nota; hanya nota yang belum punya retur/refund.
 *   RETUR  = barang kembali; uang kembali opsional (0 = tukar barang /
 *            pelanggan mengambil pengganti lewat nota baru).
 *   REFUND = uang kembali tanpa barang (mis. ongkir yang tidak jadi dikirim).
 *
 * Sejak permintaan pemilik 2026-09-29, klik nota TIDAK lagi mengganti isi
 * modal: daftar tetap di kiri dan detailnya terbuka di panel kanan, supaya
 * kasir bisa membandingkan beberapa nota tanpa pindah halaman.
 *
 * Sejak permintaan pemilik 2026-09-30 daftarnya **per hari**: hari ini dulu,
 * hari lain dicari lewat pemilih kalender. Nota satu hari bisa panjang, jadi
 * satu hari per permintaan lebih cepat dan lebih mudah dibaca daripada
 * menggulir riwayat berbulan-bulan.
 */

const props = defineProps({
  visible: {
    type: Boolean,
    default: false,
  },
  /*
   * Antrean perangkat + status kirim batch, dipasok App.vue (permintaan pemilik,
   * 2026-10-09). Dulu blok ini duduk di kolom keranjang dan memakan tinggi layar
   * kasir; sekarang notifikasi dan aksinya tinggal di riwayat, sementara tombol
   * "Riwayat Nota" memakai jumlah yang sama sebagai lencana.
   */
  queuedNotices: {
    type: Array,
    default: () => [],
  },
  batchSending: {
    type: Boolean,
    default: false,
  },
  batchMessage: {
    type: String,
    default: '',
  },
  /* Hasil checkout sementara; bukan status nota yang dipilih/filter riwayat. */
  checkoutStatus: {
    type: String,
    default: 'idle',
  },
  checkoutMessage: {
    type: String,
    default: '',
  },
  checkoutResult: {
    type: Object,
    default: null,
  },
  /* Ringkasan dan retry mencakup antrean cetak perangkat, bukan satu nota. */
  printSyncMessage: {
    type: String,
    default: '',
  },
  printJobId: {
    type: String,
    default: null,
  },
  /* Nota yang dibuat hari ini dan belum tercetak, dari `App.vue`. */
  unprintedCount: {
    type: Number,
    default: 0,
  },
  /* Semua job yang masih belum tercetak, untuk menutup warning + aksi bersama. */
  printAlertKey: {
    type: String,
    default: '',
  },
})

const emit = defineEmits(['close', 'resend-queued', 'resend-batch', 'retry-print'])

const showCheckoutResult = computed(() => props.checkoutStatus === 'success' && Boolean(props.checkoutMessage))

/*
 * Tombol tutup di band notifikasi (permintaan pemilik, 2026-10-10).
 *
 * Ini **state tampilan**, bukan perubahan state antrean: `App.vue` tetap pemilik
 * `checkoutResult`, `printSyncMessage`, dan `outstandingPrintJobId`. Menutup pesan
 * karena itu tidak membuang pekerjaan cetak, tidak mematikan kirim ulang
 * otomatis (antrean tetap dicoba berkala dengan backoff), dan tidak mengubah data
 * apa pun — hanya berhenti menampilkan pesan itu.
 *
 * Yang disimpan adalah himpunan job yang belum tercetak. Menutup cetak
 * menyembunyikan satu panel utuh (peringatan dan tombol ulang); antreannya tetap
 * ada dan retry otomatis tidak berubah. Pekerjaan baru membuat panel muncul lagi.
 */
const dismissedResult = ref('')
const dismissedPrint = ref('')

/*
 * Penutupan pesan cetak bertahan sampai halaman dimuat ulang (permintaan
 * pemilik, 2026-10-10). Sebelum ini kasir menutup "Cetak tertunda", me-refresh
 * tablet, dan pesannya kembali karena `App.vue` menyusun ulang pesan yang sama
 * dari antrean cetak yang memang masih tersimpan di perangkat.
 *
 * Dua batas yang disengaja:
 * - Penolakan permanen penghubung selalu muncul kembali setelah muat ulang,
 *   karena keadaan itu butuh teknisi.
 * - X menutup seluruh pasangan visual, termasuk tombol "Cetak ulang". Ini hanya
 *   state tampilan: pekerjaan cetaknya tetap ada dan retry otomatis berlanjut.
 */
const persistedDismissedPrint = ref(readDismissedPrintSignature())

const resultSignature = computed(() => `${props.checkoutResult?.transaction_id ?? ''}|${props.checkoutMessage}`)

/*
 * Identitas panel cetak adalah himpunan semua job yang belum tercetak. Kunci
 * berbasis job stabil saat teks status disusun ulang; pekerjaan baru atau selesai
 * mencetak mengubah set dan menampilkan status lagi.
 */
const printNoticeKey = computed(() => (
  props.printAlertKey
    ? `jobs:${props.printAlertKey}`
    : `msg:${props.printJobId ?? ''}|${props.printSyncMessage}`
))

/*
 * Sumber chip cetak: antrean cetak perangkat yang belum keluar, atau pesan
 * hasil siklus cetak terakhir (selesai / ditolak / dikirim ulang). Sejak 2026-10-10
 * yang menentukan chipnya bermakna "tertunda" adalah `printAlertKey` — himpunan
 * pekerjaan yang belum tercetak — sedangkan angkanya hanya menghitung nota
 * yang dibuat hari ini. Keduanya dibaca `App.vue` dari daftar yang sama.
 */
const printNoticeActive = computed(() => (
  Boolean(props.printSyncMessage) || Boolean(props.printJobId)
))

/* Penolakan permanen: pesannya tidak pernah dianggap "sudah dibaca". */
const printRejected = computed(() => /menolak/.test(props.printSyncMessage))

const showPrintNotice = computed(() => (
  printNoticeActive.value && !printRejected.value
    ? dismissedPrint.value !== printNoticeKey.value
      && persistedDismissedPrint.value !== printNoticeKey.value
    : printNoticeActive.value
))

const printActionsVisible = computed(() => showPrintNotice.value && Boolean(props.printJobId))

const visibleCheckoutResult = computed(() => (
  showCheckoutResult.value && dismissedResult.value !== resultSignature.value
))

const showHeaderNotices = computed(() => (
  visibleCheckoutResult.value
  || showPrintNotice.value
  || showQueueNotice.value
  || batchChipVisible.value
))

function dismissResult() {
  dismissedResult.value = resultSignature.value
}

function dismissPrint() {
  const key = printNoticeKey.value

  dismissedPrint.value = key

  /* Hanya simpan penutupan panel untuk pekerjaan yang masih tercatat. */
  if (printRejected.value || !props.printAlertKey) {
    return
  }

  persistedDismissedPrint.value = key
  saveDismissedPrintSignature(key)
}

/*
 * Hasil kirim batch juga berita, bukan pekerjaan tertunda: pesannya boleh
 * ditutup, dan kembali lagi saat hasil berikutnya berbeda.
 */
const dismissedBatch = ref('')

/*
 * Chip-nya muncul berdasarkan **label yang tersisa**, bukan mentahnya pesan:
 * keadaan "perlu diperiksa" sengaja tidak punya label (2026-10-11), jadi pesan
 * jenis itu tidak memunculkan chip sama sekali.
 */
const batchChipVisible = computed(() => (
  Boolean(batchChipLabel.value) && dismissedBatch.value !== props.batchMessage
))

function dismissBatch() {
  dismissedBatch.value = props.batchMessage
}

/*
 * Chip hanya memuat status singkat (permintaan pemilik, 2026-10-10): kalimat
 * lengkapnya tetap dibawa `title`/`aria-label`, jadi artinya tidak hilang dan
 * pembaca layar tetap menerima pesan penuhnya.
 */
const resultChipLabel = computed(() => (
  props.checkoutResult?.transaction_id
    ? `Tersimpan · ${props.checkoutResult.transaction_id}`
    : 'Tersimpan'
))

const printChipLabel = computed(() => {
  const message = props.printSyncMessage

  /* Job yang belum keluar adalah alasan paling kuat, apa pun pesan terakhirnya. */
  if (props.printJobId) {
    return 'Cetak tertunda'
  }

  if (/menolak/.test(message)) {
    return 'Cetak ditolak'
  }

  if (/terkirim ke printer/.test(message)) {
    return 'Cetak selesai'
  }

  if (/kirim ulang/.test(message)) {
    return 'Cetak dikirim ulang'
  }

  if (/tertunda|belum menjawab/.test(message)) {
    return 'Cetak tertunda'
  }

  return message ? 'Status cetak' : ''
})

/*
 * Teks yang benar-benar dibaca kasir (permintaan pemilik, 2026-10-10): keadaan
 * "cetak tertunda" membawa **jumlah notanya**, sama seperti chip antrean yang
 * berbunyi "N belum tersinkron" — angka itu yang membuat pasangan peringatan +
 * tombol solusi bisa dibaca sekilas. Keadaan lain tetap satu kata; kalimat
 * penuhnya tetap di `title`/`aria-label`.
 */
const printChipText = computed(() => (
  printChipLabel.value === 'Cetak tertunda' && props.unprintedCount > 0
    ? `${props.unprintedCount} cetak tertunda`
    : printChipLabel.value
))

/*
 * Warna chip cetak mengikuti artinya, bukan sekadar ada-tidaknya pekerjaan
 * (permintaan pemilik, 2026-10-10): kertas belum keluar = amber, penghubung
 * menolak permanen = merah (butuh teknisi), sudah tercetak = hijau, dan berita
 * netral (dikirim ulang / status tak dikenal) tetap netral. Sebelumnya
 * "Cetak ditolak" tanpa `printJobId` sempat berwarna hijau karena pemetaannya
 * hanya melihat ada-tidaknya job.
 */
const printChipTone = computed(() => {
  if (printChipLabel.value === 'Cetak selesai') {
    return 'is-ok'
  }

  if (printChipLabel.value === 'Cetak ditolak') {
    return 'is-danger'
  }

  if (printChipLabel.value === 'Cetak tertunda') {
    return 'is-wait'
  }

  return 'is-info'
})

/*
 * Kalimat untuk kiriman yang berhasil (permintaan pemilik, 2026-10-11: "tombol
 * 'kirim selesai' diganti menjadi 'Nota berhasil dikirim'"). Ditulis sekali
 * karena dipakai **dua** tempat: teks chipnya dan penentuan warna kotaknya —
 * dua hal yang harus selalu sama tidak boleh ditulis dua kali.
 */
const BATCH_SENT_LABEL = 'Nota berhasil dikirim'

/*
 * Hasil kirim batch juga diringkas (permintaan pemilik, 2026-10-10): pesannya
 * panjang dan berbeda tiap kejadian, sedangkan yang dibaca cepat di baris judul
 * cukup hasilnya. Kalimat lengkap tetap di `title`/`aria-label`, jadi artinya
 * tidak hilang; urutan pemeriksaan dipilih dari yang paling khusus, karena
 * pesan nyata sering memuat dua sinyal sekaligus ("2 nota terkirim, 1 ditolak").
 */
const batchChipLabel = computed(() => {
  const message = props.batchMessage

  if (!message) {
    return ''
  }

  /* Pesannya kalimat penuh, jadi huruf besar di awal tidak boleh menyamarkan sinyalnya. */
  if (/belum terdaftar/i.test(message)) {
    return 'Belum terdaftar'
  }

  if (/tidak ada nota/i.test(message)) {
    return 'Tidak ada nota'
  }

  /*
   * Sebagian terkirim tapi ada yang ditolak/ditahan: hasilnya butuh mata
   * manusia, dan notifikasi modalnya **dihapus** (permintaan pemilik,
   * 2026-10-11: "Notif 'perlu diperiksa' hilangkan saja"). Yang dikembalikan
   * bukan label lain melainkan label kosong: kalau dibiarkan jatuh ke cabang
   * berikutnya, kalimat "2 nota terkirim, 1 ditolak" akan terbaca "Kirim
   * selesai" — lebih buruk daripada tidak ada chip. Keadaannya sendiri tidak
   * hilang: barisnya menjadi `review`/`conflict` di Dexie, dan saat halaman
   * dimuat ulang `restorePendingCheckout` mengunci penjualan baru sambil
   * menampilkan pesan pemeriksaan di kolom keranjang.
   */
  if (/ditolak|konflik|belum jelas|belum diketahui/i.test(message)) {
    return ''
  }

  if (/gagal|tidak dapat|tidak bisa/i.test(message)) {
    return 'Kirim gagal'
  }

  if (/terkirim|dikirim/i.test(message)) {
    return BATCH_SENT_LABEL
  }

  return 'Hasil kirim'
})

/*
 * Warna kotak chip hasil kirim mengikuti artinya (permintaan pemilik,
 * 2026-10-11): kiriman yang berhasil memakai nada sukses yang **sama** dengan
 * chip "Tersimpan" saat nota masuk online. Keadaan lain tetap netral seperti
 * sebelumnya — hijau hanya untuk kabar yang memang sudah beres, karena warna
 * itu yang membuat kasir berhenti memeriksa.
 */
const batchChipTone = computed(() => (
  batchChipLabel.value === BATCH_SENT_LABEL ? 'is-ok' : 'is-info'
))

function retryPrint() {
  if (!props.printJobId || props.checkoutStatus === 'submitting') {
    return
  }
  emit('retry-print')
}

const notas = ref([])

const currentPage = ref(1)

const hasMore = ref(false)

const totalNotas = ref(0)

const loading = ref(false)

const errorMessage = ref('')

const selectedNota = ref(null)

const selectedItems = ref([])

const loadingDetail = ref(false)

const detailError = ref('')

/*
 * Tanggal yang sedang ditampilkan. "Hari ini" dihitung ulang setiap modal
 * dibuka, jadi riwayat tidak tertinggal di hari kemarin kalau tabletnya
 * dibiarkan menyala semalam.
 */
const selectedDate = ref(todayIsoDate())

/*
 * Cakupan riwayat: 'self' = device ini saja (bawaan); 'all' = semua device
 * untuk kemudahan refund, wajib PIN kasir (header X-Approver-PIN).
 */
const historyScope = ref('self')

/* PIN kasir untuk scope=all; hidup selama modal terbuka, dibersihkan saat
   modal ditutup — tidak pernah disimpan persisten. */
const personPin = ref('')

/*
 * Filter status aksi untuk peninjauan harian (mis. semua void hari ini).
 * '' = semua status; nilai lain divalidasi server (none/void/returned/
 * refunded) dan nilai tak dikenal dibalas 400, bukan diabaikan diam-diam.
 */
const historyStatusFilter = ref('')

/* Event refund nota yang sedang dibuka (dari detail server). */
const selectedRefunds = ref([])

/* Form void/retur/refund: null = tidak dibuka. */
const refundMode = ref(null)
const refundReason = ref('')
const refundMethod = ref('cash')
const refundAmount = ref('')
const refundPin = ref('')
const refundQuantities = ref({})
const refundBusy = ref(false)
const refundMessage = ref('')

/* Pesan sukses yang bertahan setelah form ditutup. */
const refundNotice = ref('')

const reprintBusy = ref(false)

const reprintMessage = ref('')

/*
 * Bukti transfer yang belum diterima server (PAYMENT_PROOF_DESIGN.md bagian
 * 7.2): ditandai di halaman riwayat, bukan di layar terpisah.
 */
const outstandingProofs = ref([])

const proofActionBusy = ref(null)

/* `sync_id` yang sedang dikirim "Kirim semua"; dasar penyaring di bawah. */
const queuedProofSyncIds = computed(
  () =>
    new Set((props.queuedNotices || []).map((row) => row.sync_id).filter(Boolean)),
)

/*
 * Peringatan bukti yang pengirimannya baru saja dimulai hilang seketika
 * (permintaan pemilik, 2026-10-11: "Kirim semua" tidak membuat peringatan
 * bukti langsung hilang). Sebabnya baris bukti menunggu notanya lebih dulu,
 * jadi selama nota itu sedang naik, barisnya tidak perlu menggantung di
 * layar. Isinya dibaca ulang begitu pengiriman selesai: kalau unggahannya
 * berhasil ia tetap hilang, kalau gagal ia kembali dengan statusnya sendiri.
 */
const bannerProofs = computed(() =>
  visibleBannerProofs(outstandingProofs.value, {
    batchSending: props.batchSending,
    queuedSyncIds: queuedProofSyncIds.value,
  }),
)

const proofError = ref('')

/*
 * Tiga jalur kirim satu nota dibaca dari tabelnya masing-masing (bukan dari
 * pesan terakhir yang lewat): server = baris ini ada di daftar server;
 * bukti = payment_proofs per sync_id; cetak = print_jobs per sync_id.
 * Kertas keluar tidak berarti server menyimpan, dan sebaliknya.
 */
const notaChannelMap = ref({})

function summarizeChannels({ proofs = [], jobs = [] } = {}) {
  let proof = 'none'

  if (proofs.length > 0) {
    proof = proofs.some((item) => item.upload_status === 'uploaded')
      ? 'uploaded'
      : proofs.some((item) => item.upload_status === 'conflict' || item.upload_status === 'blocked')
        ? 'attention'
        : 'waiting'
  }

  let print = 'none'

  if (jobs.length > 0) {
    print = jobs.some(
      (job) => job.status === 'pending' || job.status === 'printing' || job.status === 'retryable',
    )
      ? 'queued'
      : jobs.some((job) => job.status === 'blocked')
        ? 'blocked'
        : 'printed'
  }

  return { proof, print }
}

function channelFor(syncId) {
  return notaChannelMap.value[syncId] || { proof: 'none', print: 'none' }
}

const PROOF_CHIP_TEXT = { uploaded: 'Bukti ✓', waiting: 'Bukti …', attention: 'Bukti !' }
const PRINT_CHIP_TEXT = { printed: 'Cetak ✓', queued: 'Cetak …', blocked: 'Cetak !' }

function channelChipClass(state) {
  if (state === 'uploaded' || state === 'printed') {
    return 'is-ok'
  }

  if (state === 'attention' || state === 'blocked') {
    return 'is-bad'
  }

  return 'is-wait'
}

async function loadNotaChannels() {
  const syncIds = [...new Set(notas.value.map((nota) => nota.sync_id).filter(Boolean))]

  if (syncIds.length === 0) {
    notaChannelMap.value = {}
    return
  }

  try {
    const [proofs, jobs] = await Promise.all([
      getPaymentProofsBySyncIds(syncIds),
      getPrintJobsBySyncIds(syncIds),
    ])
    const next = {}

    for (const syncId of syncIds) {
      next[syncId] = summarizeChannels({
        proofs: proofs.filter((proof) => proof.sync_id === syncId),
        jobs: jobs.filter((job) => job.sync_id === syncId),
      })
    }

    notaChannelMap.value = next
  } catch {
    /* Indikator tidak boleh memblokir daftar nota. */
  }
}

/*
 * Tanggal lokal perangkat, bukan UTC: "hari ini" yang dimaksud kasir adalah
 * hari di toko, sama dengan hari yang dipakai backend saat memfilter.
 */
function todayIsoDate() {
  const now = new Date()
  const pad2 = (number) => String(number).padStart(2, '0')

  return `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`
}

const isToday = computed(() => selectedDate.value === todayIsoDate())

const dateLabel = computed(() => {
  const date = new Date(`${selectedDate.value}T00:00:00`)

  if (Number.isNaN(date.getTime())) {
    return selectedDate.value
  }

  return date.toLocaleDateString('id-ID', {
    weekday: 'long',
    day: '2-digit',
    month: 'long',
    year: 'numeric',
  })
})

function formatPrice(price) {
  return new Intl.NumberFormat('id-ID', {
    style: 'currency',
    currency: 'IDR',
    maximumFractionDigits: 0,
  }).format(Number(price))
}

function formatTime(value) {
  const date = new Date(value)

  if (Number.isNaN(date.getTime())) {
    return value
  }

  return date.toLocaleString('id-ID', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function paymentLabel(method) {
  return { cash: 'Cash', edc: 'EDC', bank_transfer: 'Bank Transfer' }[method] || method
}

/*
 * Antrean perangkat sebagai baris riwayat (opsi A slice 1): nota lokal yang
 * belum sync tampil satu daftar dengan nota server, berlabel jelas, dan
 * tidak pernah diklaim sebagai bukti sah server. Bentuk barisnya disamakan
 * dengan baris server supaya template yang sama bisa dipakai.
 */
async function loadLocalQueueRows() {
  try {
    const queued = await listQueuedTransactions()
    return queued
      .filter((row) => !selectedDate.value
        || String(row.created_at).slice(0, 10) === selectedDate.value)
      .sort((left, right) => String(right.created_at).localeCompare(String(left.created_at)))
      .map((row) => ({
        sync_id: row.sync_id,
        transaction_id: row.transaction_id || 'Tanpa nomor',
        device_code: null,
        transaction_time: row.created_at,
        payment_method: row.payment_method,
        total_amount: row.total_amount,
        refund_status: 'none',
        local_queue: true,
        queue_label: row.queue_label,
      }))
  } catch {
    return []
  }
}

/*
 * Notifikasi antrean (permintaan pemilik, 2026-10-09). Komponen ini tidak
 * mengirim apa pun sendiri: permintaan diteruskan ke App.vue, yang memegang
 * gerbang (checkout terkunci, cooldown PIN) dan memakai PIN asli tiap nota.
 */
const queuedCount = computed(() => (Array.isArray(props.queuedNotices) ? props.queuedNotices.length : 0))

/*
 * Panel "N belum tersinkron" kini punya lencana tutup (permintaan pemilik,
 * 2026-10-11) dengan aturan yang sama seperti panel cetak: yang ditutup adalah
 * **tampilannya**, bukan pekerjaannya.
 *
 * Identitasnya **himpunan `sync_id`** yang masih antre, bukan jumlahnya:
 * penutupan bertahan setelah muat ulang selama antreannya sama, tetapi nota
 * baru yang masuk antrean memunculkan peringatannya lagi (peringatan yang bisa
 * ditutup permanen akan menyembunyikan antrean yang justru bertambah).
 * Penutupan tidak mengirim ulang apa pun, tidak mengubah data, dan tidak
 * mematikan lencana angka di tombol "Riwayat Nota" milik `App.vue`.
 */
const queueNoticeKey = computed(() => (
  Array.isArray(props.queuedNotices)
    ? props.queuedNotices.map((nota) => String(nota?.sync_id ?? '')).sort().join('|')
    : ''
))

const dismissedQueue = ref('')
const persistedDismissedQueue = ref(readDismissedQueueSignature())

const showQueueNotice = computed(() => (
  queuedCount.value > 0
    && dismissedQueue.value !== queueNoticeKey.value
    && persistedDismissedQueue.value !== queueNoticeKey.value
))

function dismissQueue() {
  const key = queueNoticeKey.value

  dismissedQueue.value = key
  persistedDismissedQueue.value = key
  saveDismissedQueueSignature(key)
}

function resendBatch() {
  if (props.batchSending) {
    return
  }

  emit('resend-batch')
}

function resendQueued(syncId) {
  if (!syncId || props.batchSending) {
    return
  }

  emit('resend-queued', syncId)
}

/*
 * Penanda urutan pemuatan. Dua pemuatan halaman 1 bisa bertumpang (ganti
 * tanggal/filter, atau daftar yang disegarkan karena ada nota baru selesai
 * dikirim); yang boleh menulis ke layar hanya yang **terakhir diminta**, sebab
 * jawaban yang lebih tua bisa datang belakangan dan menaruh kembali baris lama.
 * Itu persis gejala yang dilaporkan pemilik 2026-10-11: "nota yang baru saja
 * tersinkron tidak langsung berubah statusnya, harus di-refresh dulu".
 */
let loadSequence = 0

async function loadPage(page) {
  const sequence = ++loadSequence

  loading.value = true
  errorMessage.value = ''

  try {
    const device = await deviceRepository.getDevice()

    if (!device?.device_token) {
      errorMessage.value =
        'Device belum terdaftar; riwayat nota butuh koneksi ke server.'
      return
    }

    let serverRows = []
    let serverFailed = false
    /* `null` = jawaban server tidak dipakai, jadi jumlah notanya tidak ditulis. */
    let serverTotal = null
    let serverHasMore = false
    try {
      const data = await fetchTransactionHistory(
        device.device_token,
        page,
        selectedDate.value,
        historyScope.value,
        historyScope.value === 'all' ? personPin.value : '',
        historyStatusFilter.value,
      )

      serverRows = data.results || []
      serverTotal = Number(data.count || 0)
      serverHasMore = data.next !== null
    } catch (error) {
      serverFailed = true
      if (error.status === 401) {
        errorMessage.value =
          'Device tidak terautentikasi. Daftarkan ulang device lalu coba lagi.'
      } else if (error.status === 400 && historyScope.value === 'all') {
        errorMessage.value =
          'Isi PIN kasir 6 digit untuk melihat nota semua perangkat.'
      } else {
        errorMessage.value = error.message || 'Riwayat nota tidak dapat dimuat.'
      }
    }

    const localRows = page === 1 ? await loadLocalQueueRows() : []

    /*
     * Jawaban basi: ada pemuatan yang lebih baru sejak ini dimulai. Semua
     * penulisan state ditunda sampai titik ini — bukan hanya daftar barisnya —
     * supaya jawaban lama tidak menaruh kembali jumlah nota, halaman, atau
     * penanda "masih ada lagi" milik jawaban yang lebih baru.
     */
    if (sequence !== loadSequence) {
      return
    }

    currentPage.value = page
    hasMore.value = serverFailed ? false : serverHasMore
    if (serverTotal !== null) {
      totalNotas.value = serverTotal
    }

    notas.value = page === 1 ? [...localRows, ...serverRows] : [...notas.value, ...serverRows]
    if (page === 1) {
      totalNotas.value = Number(totalNotas.value || 0) + localRows.length
    }
    if (serverFailed && page === 1 && localRows.length > 0) {
      errorMessage.value = `${errorMessage.value} Menampilkan ${localRows.length} nota antrean perangkat.`
    }
    await loadNotaChannels()
  } finally {
    if (sequence === loadSequence) {
      loading.value = false
    }
  }
}

function loadMore() {
  if (!loading.value && hasMore.value) {
    loadPage(currentPage.value + 1)
  }
}

function proofStatusLabel(status) {
  return (
    {
      captured: 'Menunggu nota tersimpan',
      pending: 'Menunggu dikirim',
      uploading: 'Sedang dikirim',
      retryable: 'Gagal sementara, akan dicoba lagi',
      conflict: 'Konflik isi bukti',
      blocked: 'Perlu tindak lanjut',
    }[status] || status
  )
}

/* `blocked`/`captured` tidak bisa dibereskan dengan mengirim ulang. */
function canRetryProof(proof) {
  return ['pending', 'uploading', 'retryable', 'conflict'].includes(proof.upload_status)
}

async function loadOutstandingProofs() {
  try {
    outstandingProofs.value = await listOutstandingPaymentProofs()
  } catch {
    outstandingProofs.value = []
  }
}

async function retryProof(proof) {
  proofActionBusy.value = proof.proof_id
  proofError.value = ''

  try {
    if (proof.upload_status === 'conflict') {
      /*
       * 409: `proof_id` ini sudah ada di server dengan isi berbeda. Bukti
       * bersifat append-only, jadi jalan keluarnya mengirim salinan dengan
       * `proof_id` baru (bagian 6).
       */
      await resubmitPaymentProofAsNew(proof.proof_id)
      await flushPaymentProofs()
    } else {
      await retryPaymentProofNow(proof.proof_id)
    }
  } catch {
    proofError.value = 'Bukti belum dapat dikirim. Periksa koneksi lalu coba lagi.'
  } finally {
    proofActionBusy.value = null
    await loadOutstandingProofs()
    await loadNotaChannels()
  }
}

/*
 * Membuka riwayat juga menjadi kesempatan mengirim bukti tertunda
 * (bagian 7.9).
 */
async function refreshOutstandingProofs() {
  await loadOutstandingProofs()

  try {
    await flushPaymentProofs()
  } catch {
    /* Antrean bukti tidak boleh memblokir riwayat nota. */
  }

  await loadOutstandingProofs()
  await loadNotaChannels()
}

/*
 * Cetak ulang struk dari riwayat.
 *
 * Yang dicetak dirakit ulang dari jawaban server (bukan dari byte di perangkat),
 * karena byte lama sudah dibuang retensi — lihat `reprintReceiptFromHistory`.
 * Untuk nota yang diantar, surat jalannya ikut dicetak lagi.
 *
 * Cetak tetap upaya terbaik: gagal kirim tidak mengubah apa pun pada nota, dan
 * pekerjaannya tetap di antrean sampai printer hidup lagi.
 */
async function reprintNota() {
  if (!selectedNota.value || reprintBusy.value) {
    return
  }

  reprintBusy.value = true
  reprintMessage.value = ''

  try {
    const { summary } = await reprintReceiptFromHistory({
      ...selectedNota.value,
      items: selectedItems.value,
    })

    reprintMessage.value =
      summarizePrintSync(summary) || 'Struk dikirim ulang ke penghubung cetak.'
  } catch (error) {
    reprintMessage.value = error.message || 'Struk belum bisa dicetak ulang.'
  } finally {
    reprintBusy.value = false
  }
}

async function openNota(nota) {
  selectedNota.value = nota
  selectedItems.value = []
  selectedRefunds.value = []
  detailError.value = ''
  reprintMessage.value = ''
  refundNotice.value = ''
  closeRefundForm()
  loadingDetail.value = true

  /* Baris antrean lokal: rinciannya dari payload Dexie, bukan server. */
  if (nota.local_queue) {
    try {
      const record = await getTransaction(nota.sync_id)
      const payload = record?.payload || {}
      const items = (payload.items || []).map((item) => ({
        ...item,
        subtotal: Number(item.quantity || 0) * Number(item.unit_price || 0)
          - Number(item.discount || 0),
      }))
      selectedItems.value = items

      /*
       * Baris antrean tidak mewarisi `item_amount` dari server: angkanya
       * dihitung dari payload yang tersimpan. Tanpa ini "Total barang" tampil
       * RpNaN pada nota yang belum tersinkron. `item_amount` payload dipakai
       * kalau ada supaya nota lama tetap menampilkan angka tersimpannya.
       */
      const itemAmount = payload.item_amount === undefined
        ? items.reduce((sum, item) => sum + item.subtotal, 0)
        : Number(payload.item_amount)

      selectedNota.value = {
        ...nota,
        refund_status: 'none',
        item_amount: itemAmount,
        shipping_amount: Number(payload.shipping_amount || 0),
      }
    } catch {
      detailError.value = 'Rincian nota antrean tidak dapat dibaca dari perangkat.'
    } finally {
      loadingDetail.value = false
    }
    return
  }

  try {
    const device = await deviceRepository.getDevice()
    const detail = await fetchTransactionDetail(
      device.device_token,
      nota.transaction_id,
      historyScope.value === 'all' ? personPin.value : '',
    )

    selectedItems.value = detail.items || []
    selectedRefunds.value = detail.refunds || []
    selectedNota.value = {
      ...nota,
      device_code: detail.device_code ?? nota.device_code ?? null,
      refund_status: detail.refund_status ?? nota.refund_status ?? 'none',
    }
  } catch (error) {
    detailError.value = error.message || 'Detail nota tidak dapat dimuat.'
  } finally {
    loadingDetail.value = false
  }
}

/* ========================================
   VOID / RETUR / REFUND

   Aturan server (keputusan pemilik, revisi 2026-10-06): hanya PIN kasir
   nota asal yang boleh menyetujui — PIN kasir lain ditolak 403 — dan nama
   penyetujunya tercatat di event. PIN hanya diteruskan ke server dan
   langsung dibersihkan dari memori.
======================================== */

const REFUND_MODE_LABEL = { void: 'Void', return: 'Retur', refund: 'Refund' }

const REFUND_STATUS_LABEL = {
  void: 'Void',
  returned: 'Retur',
  refunded: 'Refund',
}

function refundStatusChip(status) {
  return REFUND_STATUS_LABEL[status] || ''
}

function openRefundForm(mode) {
  refundMode.value = mode
  refundReason.value = ''
  refundMethod.value = 'cash'
  refundNotice.value = ''

  /*
   * REFUND = uang saja, jadi jumlah penuh nota adalah titik awal yang wajar.
   * VOID   = nota batal; kalau dibayar tunai, seluruh total biasanya memang
   *          kembali ke pelanggan (kasir boleh mengubahnya ke 0 kalau uang
   *          dibalik lewat bank, atau ke jumlah lain).
   * RETUR  = uang mengikuti barang yang dicentang, jadi dibiarkan kosong dan
   *          ada saran nilainya di bawah kolom jumlah.
   */
  if (mode === 'refund') {
    refundAmount.value = String(selectedNota.value?.total_amount ?? '')
  } else if (mode === 'void') {
    refundAmount.value =
      selectedNota.value?.payment_method === 'cash'
        ? String(selectedNota.value?.total_amount ?? '')
        : ''
  } else {
    refundAmount.value = ''
  }

  refundPin.value = ''
  refundMessage.value = ''
  const quantities = {}
  for (const item of selectedItems.value) {
    quantities[`${item.item_name}||${item.variant}`] = item.quantity
  }
  refundQuantities.value = quantities
}

function closeRefundForm() {
  refundMode.value = null
  refundReason.value = ''
  refundAmount.value = ''
  refundPin.value = ''
  refundQuantities.value = {}
  refundMessage.value = ''
  refundBusy.value = false
}

function refundItemKey(item) {
  return `${item.item_name}||${item.variant}`
}

/*
 * Nilai barang yang dicentang di form retur. Server tetap yang memutuskan
 * (plafon uang kumulatif = total nota, dan nilai retur per baris tidak boleh
 * melebihi fakta bayar baris itu); ini hanya angka bantu supaya kasir tahu
 * nilai barangnya sebelum menekan CATAT.
 */
const refundReturnedValue = computed(() =>
  selectedItems.value.reduce((sum, item) => {
    const quantity = Number(refundQuantities.value[refundItemKey(item)] || 0)
    return sum + quantity * Number(item.unit_price || 0)
  }, 0),
)

function fillRefundAmountFromGoods() {
  refundAmount.value = String(refundReturnedValue.value)
}

/*
 * Kolom jumlah uang kembali hanya menyimpan digit; titik ribuan dibuat saat
 * menampilkan (utils/rupiahInput.js). `Number(refundAmount.value || 0)` di
 * submitRefund karena itu tetap angka yang benar.
 */
function updateRefundAmount(event) {
  refundAmount.value = digitsOnly(event.target.value)
}

async function submitRefund() {
  if (!selectedNota.value || refundBusy.value) {
    return
  }

  refundBusy.value = true
  refundMessage.value = ''

  try {
    const device = await deviceRepository.getDevice()
    const mode = refundMode.value

    /*
     * Barang kembali hanya dikirim untuk RETUR; REFUND murni uang dan VOID
     * tidak menggerakkan barang (server menolak kombinasi lain — 400).
     */
    const items =
      mode !== 'return'
        ? []
        : selectedItems.value
          .map((item) => ({
            item_name: item.item_name,
            variant: item.variant,
            quantity: Number(refundQuantities.value[refundItemKey(item)] || 0),
            unit_price: Number(item.unit_price),
            subtotal:
              Number(refundQuantities.value[refundItemKey(item)] || 0) *
              Number(item.unit_price),
          }))
          .filter((item) => item.quantity > 0)

    await createRefundEvent(device.device_token, {
      /* createUuid, bukan crypto.randomUUID: tablet kasir dibuka dari
       * http://<IP-LAN> (bukan secure context) sehingga crypto.randomUUID
       * undefined dan refund dari tablet selalu error. */
      refund_id: createUuid(),
      sync_id: selectedNota.value.sync_id,
      approver_pin: refundPin.value,
      event_type: mode,
      reason: refundReason.value.trim(),
      refund_method: refundMethod.value,
      refund_amount: Number(refundAmount.value || 0),
      items,
    })

    /*
     * Pesan sukses ditaruh di luar form (`refundNotice`), bukan di
     * `refundMessage`: form ditutup setelah berhasil, jadi pesan di dalamnya
     * ikut hilang dan kasir tidak pernah melihat konfirmasinya.
     */
    /* self_approval selalu true: server menolak approver selain kasir nota. */
    refundNotice.value = `${REFUND_MODE_LABEL[mode]} tercatat.`

    closeRefundForm()
    /* Muat ulang detail (status + daftar event) dan baris daftar. */
    await openNota(selectedNota.value)
    await loadPage(1)
  } catch (error) {
    refundMessage.value = error.message || 'Refund belum bisa dicatat.'
  } finally {
    refundBusy.value = false
  }
}

function isSelected(nota) {
  return selectedNota.value?.sync_id === nota.sync_id
}

watch(
  () => props.visible,
  (visible) => {
    if (!visible) {
      /* PIN kasir tidak boleh menetap setelah modal ditutup. */
      personPin.value = ''
      historyScope.value = 'self'
      closeRefundForm()
      return
    }

    selectedNota.value = null
    selectedItems.value = []
    reprintMessage.value = ''

    /*
     * Tanggal kembali ke hari ini setiap modal dibuka. Pemuatannya dilakukan
     * salah satu pihak saja: kalau tanggalnya memang berubah, watcher tanggal
     * di bawah yang memuat — kalau tidak, tidak ada watcher yang jalan dan
     * daftarnya dimuat dari sini. Dua pemuatan sekaligus hanya membuat jawaban
     * yang lebih lambat menimpa yang lebih baru.
     */
    if (selectedDate.value === todayIsoDate()) {
      loadPage(1)
    } else {
      selectedDate.value = todayIsoDate()
    }

    refreshOutstandingProofs()
  },
)

/*
 * Selesainya pengiriman batch adalah saat keadaan bukti berubah: nota yang
 * baru masuk server membuat buktinya bisa diunggah, dan nota yang gagal
 * membuat barisnya harus muncul lagi. Karena itu daftarnya dibaca ulang
 * (sekaligus menjalankan antrean bukti) di sini, bukan menunggu modal
 * dibuka ulang.
 */
watch(
  () => props.batchSending,
  (sending) => {
    if (!props.visible || sending) {
      return
    }

    refreshOutstandingProofs()
  },
)

/* Pindah tanggal selalu mulai dari halaman pertama daftar hari itu. */
watch(selectedDate, () => {
  if (!props.visible) {
    return
  }

  selectedNota.value = null
  selectedItems.value = []
  reprintMessage.value = ''
  loadPage(1)
})

/* Ganti cakupan juga memuat ulang dari halaman pertama. */
watch(historyScope, () => {
  if (!props.visible) {
    return
  }

  selectedNota.value = null
  selectedItems.value = []
  reprintMessage.value = ''
  loadPage(1)
})

/* Ganti filter status juga memuat ulang dari halaman pertama. */
watch(historyStatusFilter, () => {
  if (!props.visible) {
    return
  }

  selectedNota.value = null
  loadPage(1)
})

/*
 * Daftar nota ikut berubah begitu ada nota yang selesai dikirim (laporan
 * pemilik, 2026-10-11: "nota 000029 yang baru saja tersinkron tidak langsung
 * berubah statusnya, harus di-refresh terlebih dahulu").
 *
 * Sumber kebenarannya daftar antrean perangkat di `App.vue`: nota yang keluar
 * dari antrean berarti barisnya sudah ada di server, jadi saat itulah daftar
 * perlu dibaca ulang. Sebelum ini satu-satunya pemicu muat ulang adalah membuka
 * modal dan mengganti tanggal/cakupan/filter, sehingga barisnya tetap terbaca
 * "Hasil belum diketahui" walau kirimannya sudah berhasil.
 *
 * Kuncinya **isi** antreannya (id + label statusnya), bukan objeknya: `App.vue`
 * menyusun ulang array yang sama setiap kali antrean dibaca, dan memuat ulang
 * karena array baru yang isinya sama hanya membuat layar berkedip.
 */
const queueSignature = computed(() => (
  (Array.isArray(props.queuedNotices) ? props.queuedNotices : [])
    .map((notice) => `${notice.sync_id}:${notice.queue_label || ''}`)
    .join('|')
))

watch(queueSignature, async () => {
  if (!props.visible) {
    return
  }

  const selected = selectedNota.value

  await loadPage(1)

  /*
   * Detail yang sedang terbuka juga ikut diperbarui, tetapi hanya kalau
   * barisnya benar-benar berpindah jenis (antrean perangkat → baris server).
   * Kalau tidak, membuka ulang detail hanya memindahkan fokus dan berkedip.
   */
  if (!selected?.local_queue) {
    return
  }

  const refreshed = notas.value.find((nota) => (
    nota.sync_id === selected.sync_id && !nota.local_queue
  ))

  if (refreshed) {
    await openNota(refreshed)
  }
})
</script>

<template>
  <div
    v-if="props.visible"
    v-modal-focus="{ onEscape: () => emit('close') }"
    class="modal-overlay"
    @click.self="emit('close')"
  >
    <div class="history-modal" role="dialog" aria-modal="true" aria-labelledby="history-title" tabindex="-1">
      <div class="modal-header">
        <div class="modal-title">
          <h2 id="history-title">{{ isToday ? 'Riwayat Nota Hari Ini' : 'Riwayat Nota' }}</h2>

          <p>
            {{ totalNotas }} nota
            {{ historyScope === 'all' ? 'semua perangkat' : 'dari perangkat ini' }}
            · {{ dateLabel }}
          </p>
        </div>

        <!--
          Status ringkas di baris judul (permintaan pemilik, 2026-10-10): pesan
          tidak lagi memakai band di bawah judul supaya daftar nota tetap lega.
          Kalimat lengkapnya tetap dibawa `title`/`aria-label`, jadi yang
          diringkas hanya tampilannya, bukan artinya.

          Urutannya pekerjaan dulu, berita kemudian: di layar sempit baris ini
          bergeser, jadi yang harus terlihat tanpa digeser adalah antrean yang
          belum selesai beserta aksinya — bukan pesan sukses yang bisa menunggu.
        -->
        <div
          v-if="showHeaderNotices"
          class="header-notices"
        >
          <!--
            Pasangan 1: antrean belum-tersinkron + aksinya (permintaan pemilik,
            2026-10-10). Peringatan dan tombol solusinya dibungkus satu panel
            supaya terbaca sebagai sepasang, dengan warna keluarga bahaya.

            Lencana tutupnya ditambahkan 2026-10-11 atas permintaan pemilik —
            menyamakan panel ini dengan panel cetak. Yang ditutup tetap hanya
            tampilannya: pekerjaan antreannya utuh, pengiriman ulang per nota
            tetap jalan, dan lencana angka di tombol "Riwayat Nota" tidak ikut
            hilang.
          -->
          <div v-if="showQueueNotice" class="notice-pair is-danger has-dismiss">
            <button
              type="button"
              class="notice-dismiss"
              :aria-label="`Tutup peringatan antrean: ${queuedCount} belum tersinkron`"
              title="Tutup peringatan antrean"
              @click="dismissQueue"
            >
              <span class="notice-dismiss-badge" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6" /></svg>
              </span>
            </button>

            <span
              class="notice-pair-text"
              title="Nota masih antre di perangkat; kirim ulang yang tertua dulu."
              role="status"
            >
              {{ queuedCount }} belum tersinkron
            </span>

            <!--
              Angka di tombol ini dihapus (permintaan pemilik, 2026-10-11:
              "(1) setelah 'Kirim semua' hilangkan saja"). Jumlahnya sudah ada
              di pesan panel ini ("N belum tersinkron") dan di lencana tombol
              "Riwayat Nota", jadi mengulanginya di tombol hanya menambah
              lebar yang harus digeser di layar sempit.
            -->
            <button
              type="button"
              class="chip-action"
              :disabled="props.batchSending"
              @click="resendBatch"
            >
              {{ props.batchSending ? 'Mengirim…' : 'Kirim semua' }}
            </button>
          </div>

          <button
            v-if="visibleCheckoutResult"
            type="button"
            class="notice-chip is-ok"
            :title="`${props.checkoutMessage} Tersimpan di server.`"
            :aria-label="`Tutup pesan hasil transaksi terakhir: ${resultChipLabel}`"
            @click="dismissResult"
          >
            <span class="notice-chip-text" role="status">{{ resultChipLabel }}</span>
            <svg class="notice-chip-close" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6" /></svg>
          </button>

          <!--
            Pasangan 2: status cetak dan aksi ulang berada di satu panel
            (permintaan pemilik, 2026-10-11): lencana tutup bulat di sudut kiri
            atas, pesan peringatan di kiri, dan tombol solusinya berupa pil
            terisi di kanan. X menutup seluruh panel tanpa menghapus pekerjaan
            cetak.
          -->
          <div
            v-if="showPrintNotice"
            class="notice-pair is-print has-dismiss"
            :class="printChipTone"
          >
            <button
              type="button"
              class="notice-dismiss"
              :aria-label="`Tutup peringatan cetak: ${printChipText}`"
              title="Tutup peringatan cetak"
              @click="dismissPrint"
            >
              <span class="notice-dismiss-badge" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6" /></svg>
              </span>
            </button>

            <span class="notice-pair-text" :title="props.printSyncMessage" role="status">
              {{ printChipText }}
            </span>

            <!-- Semua job yang belum tercetak dicoba ulang memakai ID yang sama. -->
            <button
              v-if="printActionsVisible"
              type="button"
              class="chip-action"
              :disabled="props.checkoutStatus === 'submitting'"
              @click="retryPrint"
            >
              Cetak ulang
            </button>
          </div>

          <button
            v-if="batchChipVisible"
            type="button"
            class="notice-chip"
            :class="batchChipTone"
            :title="props.batchMessage"
            :aria-label="`Tutup hasil pengiriman: ${props.batchMessage}`"
            @click="dismissBatch"
          >
            <span class="notice-chip-text" role="status">{{ batchChipLabel }}</span>
            <svg class="notice-chip-close" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6" /></svg>
          </button>
        </div>

        <button type="button" class="close-button" aria-label="Tutup riwayat" title="Tutup" @click="emit('close')">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6" /></svg>
        </button>
      </div>

      <!-- ==================================
           DAFTAR + DETAIL BERDAMPINGAN
      =================================== -->

      <div class="history-body">
        <!-- ==================================
             PANEL KIRI: DAFTAR NOTA
        =================================== -->

        <div class="history-list-pane">
          <!-- ==================================
               FILTER TANGGAL (PENCARIAN KALENDER)
          =================================== -->

          <div class="history-filter">
            <label>Cakupan nota</label>

            <!--
              Filter "Status aksi" duduk sebaris dengan tombol cakupan, di ruang
              kosong sebelah kanannya (permintaan pemilik, 2026-10-07), supaya
              panel kiri tidak memakai satu baris sendiri untuk dropdown.
              Labelnya tetap ada agar kasir tahu baris itu filter apa; di panel
              sempit kelompoknya turun utuh ke baris berikutnya.
            -->

            <div class="history-filter-row scope-row">
              <button
                type="button"
                class="today-button"
                :disabled="historyScope === 'self'"
                @click="historyScope = 'self'"
              >
                PERANGKAT INI
              </button>

              <button
                type="button"
                class="today-button"
                :disabled="historyScope === 'all'"
                @click="historyScope = 'all'"
              >
                SEMUA PERANGKAT
              </button>

              <span class="scope-status-group">
                <label for="history-status">Status aksi</label>

                <select id="history-status" v-model="historyStatusFilter">
                  <option value="">Semua status</option>
                  <option value="none">Belum ada aksi</option>
                  <option value="returned">Retur</option>
                  <option value="refunded">Refund</option>
                  <option value="void">Void</option>
                </select>
              </span>
            </div>

            <div v-if="historyScope === 'all'" class="history-filter-row scope-pin-row">
              <input
                v-model="personPin"
                type="password"
                inputmode="numeric"
                maxlength="6"
                autocomplete="off"
                placeholder="PIN kasir 6 digit"
                aria-label="PIN kasir"
                @change="loadPage(1)"
              />
            </div>

            <p v-if="historyScope === 'all'" class="history-filter-hint">
              Melihat nota semua perangkat wajib PIN kasir.
            </p>

            <label for="history-date">Tanggal nota</label>

            <div class="history-filter-row">
              <input
                id="history-date"
                v-model="selectedDate"
                type="date"
                :max="todayIsoDate()"
              />

              <button
                type="button"
                class="today-button"
                :disabled="isToday"
                @click="selectedDate = todayIsoDate()"
              >
                HARI INI
              </button>
            </div>

            <p class="history-filter-hint">
              {{ isToday ? 'Menampilkan nota hari ini.' : `Nota hari ${dateLabel}.` }}
            </p>
          </div>

          <!-- ==================================
               BUKTI TRANSFER TERTUNDA
          =================================== -->

          <div v-if="bannerProofs.length > 0" class="proof-banner">
            <p class="proof-banner-title">
              {{ bannerProofs.length }} bukti transfer belum terkirim ke server
            </p>

            <p v-if="proofError" class="proof-banner-error" role="alert">
              {{ proofError }}
            </p>

            <div v-for="proof in bannerProofs" :key="proof.proof_id" class="proof-row">
              <div class="proof-info">
                <strong>
                  {{ proof.transaction_id || 'Nota belum bernomor' }}
                </strong>

                <span>
                  {{ proofStatusLabel(proof.upload_status) }}

                  <template v-if="proof.detail">· {{ proof.detail }}</template>
                </span>
              </div>

              <button
                v-if="canRetryProof(proof)"
                type="button"
                class="proof-action-button"
                :disabled="proofActionBusy === proof.proof_id"
                @click="retryProof(proof)"
              >
                {{
                  proof.upload_status === 'conflict' ? 'UNGGAH SEBAGAI BUKTI BARU' : 'KIRIM ULANG'
                }}
              </button>
            </div>
          </div>

          <!--
            Pesan galat dan daftarnya sengaja TIDAK dalam satu rantai v-if:
            saat server mati (502) pesannya berbunyi "Menampilkan N nota antrean
            perangkat", tetapi rangkaian `v-else` lama menyembunyikan baris-baris
            itu — tepat di kondisi di mana nota antrean paling perlu terlihat
            (ditemukan 2026-10-09 saat memindahkan notifikasi antrean ke sini).
          -->
          <p v-if="errorMessage" class="history-error" role="alert">
            {{ errorMessage }}
          </p>

          <p v-if="loading && notas.length === 0" class="history-empty">
            <LoadingSpinner label="Memuat riwayat nota..." />
          </p>

          <p v-else-if="notas.length === 0" class="history-empty">
            {{ isToday ? 'Belum ada nota hari ini.' : `Belum ada nota pada ${dateLabel}.` }}
          </p>

          <button
            v-for="nota in notas"
            v-else
            :key="nota.sync_id"
            type="button"
            class="nota-row"
            :class="{ selected: isSelected(nota) }"
            :aria-pressed="isSelected(nota)"
            @click="openNota(nota)"
          >
            <span class="nota-id">
              {{ nota.transaction_id }}

              <span v-if="nota.device_code" class="nota-device">
                {{ nota.device_code }}
              </span>

              <span class="nota-channels">
                <span
                  v-if="nota.local_queue"
                  class="channel-chip is-wait"
                  title="Nota masih antre di perangkat; belum tersimpan di server."
                >
                  {{ nota.queue_label }}
                </span>

                <span v-else class="channel-chip is-ok" title="Nota sudah tersimpan di server.">
                  Server ✓
                </span>

                <span
                  v-if="channelFor(nota.sync_id).proof !== 'none'"
                  class="channel-chip"
                  :class="channelChipClass(channelFor(nota.sync_id).proof)"
                  title="Status foto bukti transfer di perangkat ini."
                >
                  {{ PROOF_CHIP_TEXT[channelFor(nota.sync_id).proof] }}
                </span>

                <span
                  v-if="channelFor(nota.sync_id).print !== 'none'"
                  class="channel-chip"
                  :class="channelChipClass(channelFor(nota.sync_id).print)"
                  title="Status byte nota ke printer di perangkat ini."
                >
                  {{ PRINT_CHIP_TEXT[channelFor(nota.sync_id).print] }}
                </span>

                <span
                  v-if="refundStatusChip(nota.refund_status)"
                  class="channel-chip is-bad"
                  title="Nota ini sudah punya event void/retur/refund."
                >
                  {{ refundStatusChip(nota.refund_status) }}
                </span>
              </span>
            </span>

            <span class="nota-time">{{ formatTime(nota.transaction_time) }}</span>

            <span class="nota-method">{{ paymentLabel(nota.payment_method) }}</span>

            <span class="nota-total">{{ formatPrice(nota.total_amount) }}</span>

            <span class="nota-chevron" aria-hidden="true">›</span>
          </button>

          <button
            v-if="hasMore"
            type="button"
            class="load-more-button"
            :disabled="loading"
            @click="loadMore"
          >
            <LoadingSpinner v-if="loading" compact />

            <span v-else>Muat lebih banyak</span>
          </button>
        </div>

        <!-- ==================================
             PANEL KANAN: DETAIL NOTA
        =================================== -->

        <div class="history-detail-pane">
          <p v-if="!selectedNota" class="history-empty">
            Pilih satu nota di kiri untuk melihat barangnya di sini.
          </p>

          <template v-else>
            <div class="detail-summary">
              <div class="detail-summary-head">
                <div class="detail-summary-text">
                  <h3>{{ selectedNota.transaction_id }}</h3>

                  <p>{{ formatTime(selectedNota.transaction_time) }}</p>

                  <p>
                    {{ paymentLabel(selectedNota.payment_method) }}
                    ·
                    {{ selectedNota.worker_name }}
                    <template v-if="selectedNota.device_code">
                      · Device {{ selectedNota.device_code }}
                    </template>
                    <template v-if="refundStatusChip(selectedNota.refund_status)">
                      · {{ refundStatusChip(selectedNota.refund_status) }}
                    </template>
                  </p>
                </div>

                <!--
                  Kirim ulang pindah ke kanan atas kartu nota (permintaan
                  pemilik, 2026-10-11): dulu tombolnya duduk di bawah paragraf
                  penjelasan, jauh dari nomor notanya. Yang berubah tempat dan
                  bobotnya; aksinya tetap `resendQueued` dengan PIN asli nota,
                  dan hanya nota yang masih antre di perangkat yang punya
                  tombol ini. Kalau barisnya tidak cukup (layar sempit), flexbox
                  menurunkannya utuh ke baris kedua di bawah teksnya.
                -->
                <button
                  v-if="selectedNota.local_queue"
                  type="button"
                  class="resend-nota-button"
                  :disabled="props.batchSending"
                  title="Kirim ulang nota ini memakai PIN asli nota."
                  @click="resendQueued(selectedNota.sync_id)"
                >
                  <svg
                    viewBox="0 0 24 24"
                    width="16"
                    height="16"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2.2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    aria-hidden="true"
                  >
                    <path d="M3 12a9 9 0 1 0 3-6.7" />
                    <path d="M3 4v5h5" />
                  </svg>

                  KIRIM ULANG NOTA INI
                </button>
              </div>
            </div>

            <!-- ==================================
                 EVENT VOID / RETUR / REFUND
            =================================== -->

            <div v-if="selectedRefunds.length > 0" class="refund-history">
              <p class="pane-title">Riwayat void / retur / refund</p>

              <div
                v-for="event in selectedRefunds"
                :key="event.refund_id"
                class="refund-event"
              >
                <strong>
                  {{ REFUND_MODE_LABEL[event.event_type] || event.event_type }}
                  · {{ formatPrice(event.refund_amount) }}
                  ({{ event.refund_method }})
                </strong>

                <span>
                  {{ event.reason }} · {{ event.approver_name }}

                  <template v-if="event.cross_device">
                    · dari device {{ event.device_code }}
                  </template>

                  <template v-if="event.self_approval">
                    · approval kasir nota ini
                  </template>
                </span>
              </div>
            </div>

            <!--
              Nota antrean lokal belum ada di server: void/retur/refund butuh
              nota server, jadi aksinya disembunyikan sampai nota terkirim.
            -->
            <template v-if="!selectedNota?.local_queue">
              <div class="refund-actions">
                <button
                  v-for="mode in ['void', 'return', 'refund']"
                  :key="mode"
                  type="button"
                  class="proof-action-button"
                  :disabled="loadingDetail"
                  @click="openRefundForm(mode)"
                >
                  {{ REFUND_MODE_LABEL[mode].toUpperCase() }}
                </button>
              </div>
            </template>

            <div v-else class="queue-detail-actions">
              <p class="history-filter-hint">
                Nota ini masih antre di perangkat dan belum tersimpan di server.
                Kirim ulang dari tombol di kanan atas memakai PIN asli nota, jadi
                nota tidak lahir dua kali.
              </p>
            </div>

            <p v-if="refundNotice" class="reprint-message" role="status">
              {{ refundNotice }}
            </p>

            <div v-if="refundMode" class="refund-form">
              <p class="pane-title">
                {{ REFUND_MODE_LABEL[refundMode] }} nota {{ selectedNota.transaction_id }}
              </p>

              <div v-if="refundMode === 'return'" class="refund-items">
                <label
                  v-for="item in selectedItems"
                  :key="`${item.item_name}-${item.variant}`"
                  class="refund-item-row"
                >
                  <span>{{ item.item_name }} - {{ item.variant }}</span>

                  <input
                    v-model.number="refundQuantities[`${item.item_name}||${item.variant}`]"
                    type="number"
                    min="0"
                    :max="item.quantity"
                    aria-label="Jumlah retur"
                  />
                </label>
              </div>

              <!--
                Jumlah uang kembali tampil bertitik ribuan seperti kolom uang
                lain (permintaan pemilik, 2026-10-07): "70000" terlalu mudah
                salah dibaca sebagai "700000". Yang disimpan di
                `refundAmount` tetap digit polos, jadi payload ke server tidak
                pernah berisi "70.000".
              -->

              <label class="refund-field">
                {{ refundMode === 'void' ? 'Uang dikembalikan ke pelanggan (Rp)' : 'Jumlah uang kembali (Rp)' }}
                <input
                  :value="formatRupiahInput(refundAmount)"
                  type="text"
                  inputmode="numeric"
                  autocomplete="off"
                  placeholder="0"
                  @keydown="preventNonNumericInput"
                  @input="updateRefundAmount"
                />
              </label>

              <p v-if="refundMode === 'return'" class="history-filter-hint">
                Nilai barang dicentang: {{ formatPrice(refundReturnedValue) }}.

                <button
                  type="button"
                  class="today-button"
                  @click="fillRefundAmountFromGoods"
                >
                  ISI NILAI INI
                </button>
              </p>

              <p v-if="refundMode === 'void'" class="history-filter-hint">
                Isi 0 bila uang tidak keluar dari kas (mis. dibalik lewat bank).
              </p>

              <label class="refund-field">
                Metode kembali
                <select v-model="refundMethod">
                  <option value="cash">Cash</option>
                  <option value="edc">EDC</option>
                  <option value="bank_transfer">Bank Transfer</option>
                </select>
              </label>

              <label class="refund-field">
                Alasan (wajib)
                <input
                  v-model="refundReason"
                  type="text"
                  autocomplete="off"
                  placeholder="Mis. barang cacat produksi"
                />
              </label>

              <label class="refund-field">
                PIN kasir penyetuju (6 digit)
                <input
                  v-model="refundPin"
                  type="password"
                  inputmode="numeric"
                  maxlength="6"
                  autocomplete="off"
                  placeholder="••••••"
                />
              </label>

              <p v-if="refundMessage" class="reprint-message" role="status">
                {{ refundMessage }}
              </p>

              <div class="refund-form-actions">
                <button
                  type="button"
                  class="proof-action-button"
                  :disabled="refundBusy"
                  @click="submitRefund"
                >
                  {{ refundBusy ? 'MENCATAT...' : 'CATAT' }}
                </button>

                <button
                  type="button"
                  class="proof-action-button"
                  :disabled="refundBusy"
                  @click="closeRefundForm"
                >
                  BATAL
                </button>
              </div>

              <p class="history-filter-hint">
                Hanya PIN kasir nota ini yang boleh menyetujui; cantumkan
                ACC master di kolom alasan bila diminta. Nama penyetuju
                tercatat di riwayat.
              </p>
            </div>

            <p v-if="detailError" class="history-error" role="alert">
              {{ detailError }}
            </p>

            <p v-else-if="loadingDetail" class="history-empty">
              <LoadingSpinner label="Memuat barang..." />
            </p>

            <div v-else class="detail-items">
              <div
                v-for="item in selectedItems"
                :key="`${item.item_name}-${item.variant}`"
                class="detail-item"
              >
                <div class="detail-item-info">
                  <strong>
                    {{ item.item_name }}

                    <span v-if="item.variant && item.variant !== 'N/A'">
                      - {{ item.variant }}
                    </span>
                  </strong>

                  <span>
                    {{ item.quantity }} × {{ formatPrice(item.unit_price) }}

                    <template v-if="item.discount > 0">
                      (diskon {{ formatPrice(item.discount) }})
                    </template>
                  </span>
                </div>

                <strong class="detail-item-total">
                  {{ formatPrice(item.subtotal) }}
                </strong>
              </div>
            </div>

            <div class="detail-totals">
              <div class="detail-row">
                <span>Total barang</span>

                <strong>{{ formatPrice(selectedNota.item_amount) }}</strong>
              </div>

              <div v-if="selectedNota.shipping_amount" class="detail-row">
                <span>Ongkos kirim</span>

                <strong>{{ formatPrice(selectedNota.shipping_amount) }}</strong>
              </div>

              <div v-if="selectedNota.cust_name" class="detail-row">
                <span>Pelanggan</span>

                <strong>{{ selectedNota.cust_name }}</strong>
              </div>

              <div v-if="selectedNota.phone_no" class="detail-row">
                <span>No. HP</span>

                <strong>{{ selectedNota.phone_no }}</strong>
              </div>

              <div class="detail-row detail-grand-total">
                <span>Total</span>

                <strong>{{ formatPrice(selectedNota.total_amount) }}</strong>
              </div>
            </div>

            <!--
              Cetak ulang: nota lama tetap bisa dicetak dengan bentuk yang
              sama seperti saat keluar pertama kali, termasuk surat jalannya.
            -->
            <div class="reprint-block">
              <button
                type="button"
                class="reprint-button"
                :disabled="reprintBusy || loadingDetail || selectedItems.length === 0"
                @click="reprintNota"
              >
                {{ reprintBusy ? 'MENGIRIM...' : 'CETAK ULANG STRUK' }}
              </button>

              <p v-if="reprintMessage" class="reprint-message" role="status">
                {{ reprintMessage }}
              </p>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-overlay {
  position: fixed;

  inset: 0;

  z-index: 1500;

  display: flex;

  justify-content: center;

  align-items: center;

  padding: 16px;

  background: var(--pos-overlay);
}

/*
 * Modal lebar supaya daftar nota dan detailnya bisa tampil berdampingan.
 */

.history-modal {
  display: flex;

  flex-direction: column;

  width: min(1180px, 96vw);

  height: min(760px, 92vh);

  background: var(--pos-surface);

  border: 1px solid var(--pos-border);

  border-radius: 12px;

  box-shadow: var(--pos-shadow);

  overflow: hidden;

  box-sizing: border-box;
}

.modal-header {
  display: flex;

  align-items: center;

  gap: 12px;

  flex-shrink: 0;

  padding: 18px 22px;

  border-bottom: 1px solid var(--pos-border);
}

/*
 * Judul **boleh menyusut** sejak 2026-10-11 (dulu `flex-shrink: 0`): pemilik
 * melaporkan panel peringatan yang ukurannya sudah diperkecil tetap "pindah ke
 * bawah" di tablet karena judulnya tidak mau mengecil sedikit pun. Sekarang
 * judul dan baris panelnya berbagi satu baris: judulnya menyusut sampai
 * subjudulnya membungkus, panelnya tetap di sebelahnya. Di layar sempit baris
 * panel tetap pindah ke barisnya sendiri (`max-width: 900px` di bawah), karena
 * di lebar itu tidak ada lagi ruang untuk dibagi.
 */
.modal-title {
  min-width: 0;

  flex-shrink: 1;
}

.modal-title h2 {
  margin: 0;

  color: var(--pos-text);

  font-size: 24px;

  font-weight: 700;
}

.modal-title p {
  margin: 4px 0 0;

  color: var(--pos-text-muted);

  font-size: 14px;
}

.close-button {
  display: flex;

  justify-content: center;

  align-items: center;

  width: 44px;

  height: 44px;

  flex-shrink: 0;

  margin-left: auto;

  padding: 0;

  border: 1px solid var(--pos-border);

  border-radius: 10px;

  background: var(--pos-surface);

  color: var(--pos-text);

  font-size: 24px;

  line-height: 1;

  cursor: pointer;
}

.close-button:hover {
  background: var(--pos-hover);

  border-color: var(--pos-accent);
}

/* ========================================
   DUA PANEL
======================================== */

.history-body {
  display: grid;

  grid-template-columns: minmax(0, 1fr) minmax(0, 1.05fr);

  flex: 1;

  min-height: 0;
}

.history-list-pane,
.history-detail-pane {
  min-height: 0;

  padding: 16px 18px;

  overflow-y: auto;
}

.history-detail-pane {
  border-left: 1px solid var(--pos-border);

  background: var(--pos-surface-alt);
}

/* ========================================
   DAFTAR
======================================== */

.history-empty {
  margin: 40px 0;

  color: var(--pos-text-muted);

  font-size: 15px;

  text-align: center;
}

.history-error {
  margin: 0 0 12px;

  padding: 10px 12px;

  border: 1px solid var(--pos-danger);

  border-radius: 10px;

  background: var(--pos-outstock-bg);

  color: var(--pos-outstock-text);

  font-size: 14px;
}

.nota-row {
  display: grid;

  grid-template-columns: 1fr auto auto auto 16px;

  gap: 12px;

  align-items: center;

  width: 100%;

  min-height: 56px;

  padding: 10px 14px;

  margin-bottom: 8px;

  border: 1px solid var(--pos-border);

  border-radius: 10px;

  background: var(--pos-surface);

  color: var(--pos-text);

  font-size: 15px;

  text-align: left;

  cursor: pointer;

  transition:
    background-color 0.15s ease,
    border-color 0.15s ease;
}

.nota-row:hover {
  background: var(--pos-hover);

  border-color: var(--pos-accent);
}

/* Nota yang sedang dibuka di panel kanan. */
.nota-row.selected {
  border-color: var(--pos-accent);

  background: var(--pos-accent-soft);
}

.nota-id {
  font-weight: 700;
}

.nota-device {
  display: inline-block;

  margin-left: 6px;
  padding: 1px 7px;

  border: 1px solid var(--pos-border);
  border-radius: 999px;

  background: var(--pos-surface-alt);

  font-size: 11px;
  font-weight: 700;
}

.scope-pin-row {
  margin-top: 10px;
}

.scope-pin-row input,
.refund-field input,
.refund-field select {
  flex: 1;

  min-width: 0;
  min-height: 44px;

  padding: 0 12px;

  border: 1px solid var(--pos-border);
  border-radius: 10px;

  background: var(--pos-surface);
  color: var(--pos-text);

  font-size: 15px;
}

.refund-history {
  margin-bottom: 14px;
  padding: 12px 14px;

  border: 1px solid var(--pos-border);
  border-radius: 10px;

  background: var(--pos-surface);
}

.refund-event {
  display: flex;
  flex-direction: column;
  gap: 2px;

  padding: 8px 0;

  border-top: 1px solid var(--pos-border);

  font-size: 14px;
}

.refund-event strong {
  color: var(--pos-text);
}

.refund-event span {
  color: var(--pos-text-muted);
}

.refund-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;

  margin: 14px 0;
}

.refund-form {
  margin-bottom: 14px;
  padding: 12px 14px;

  border: 1px solid var(--pos-border);
  border-radius: 10px;

  background: var(--pos-surface);
}

.refund-items {
  display: flex;
  flex-direction: column;
  gap: 8px;

  margin-bottom: 10px;
}

.refund-item-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;

  font-size: 14px;
}

.refund-item-row input {
  width: 72px;
  min-height: 44px;

  padding: 0 10px;

  border: 1px solid var(--pos-border);
  border-radius: 10px;

  background: var(--pos-surface-alt);
  color: var(--pos-text);

  font-size: 15px;
}

.refund-field {
  display: flex;
  flex-direction: column;
  gap: 6px;

  margin-bottom: 10px;

  font-size: 13px;
  font-weight: 600;
  color: var(--pos-text-muted);
}

.refund-form-actions {
  display: flex;
  gap: 8px;

  margin-top: 10px;
}

.nota-time,
.nota-method {
  color: var(--pos-text-muted);

  font-size: 13px;
}

.nota-total {
  font-weight: 700;

  text-align: right;
}

.nota-chevron {
  color: var(--pos-text-muted);

  font-size: 20px;

  line-height: 1;
}

.load-more-button {
  width: 100%;

  min-height: 52px;

  padding: 10px;

  margin-top: 6px;

  border: 1px solid var(--pos-border);

  border-radius: 10px;

  background: var(--pos-surface);

  color: var(--pos-text);

  font-size: 15px;

  font-weight: 600;

  cursor: pointer;
}

.load-more-button:disabled {
  opacity: 0.4;

  cursor: not-allowed;
}

/* ========================================
   DETAIL
======================================== */

.detail-summary {
  margin-bottom: 14px;
}

.detail-summary h3 {
  margin: 0 0 6px;

  color: var(--pos-text);

  font-size: 20px;

  font-weight: 700;
}

.detail-summary p {
  margin: 2px 0;

  color: var(--pos-text-muted);

  font-size: 14px;
}

/*
 * Kartu nota: nomor + waktu + kasir di kiri, tombol kirim ulang di kanan atas
 * (permintaan pemilik, 2026-10-11). `flex-wrap` tanpa ambang jendela: cukup =
 * sebaris, tidak cukup = tombol turun utuh ke baris berikutnya, jadi tidak ada
 * teks yang tertimpa di tablet 360px.
 */
.detail-summary-head {
  display: flex;

  flex-wrap: wrap;

  align-items: flex-start;

  justify-content: space-between;

  gap: 12px;
}

/*
 * Dasar 120px, bukan selebar isi teksnya: keputusan "tombol pindah baris atau
 * tidak" memakai angka ini, jadi dengan dasar kecil tombolnya tetap duduk di
 * kanan atas selama ruangnya cukup untuk tombolnya sendiri — yang menyusut
 * adalah kolom teksnya, bukan tombol aksinya.
 */
.detail-summary-text {
  flex: 1 1 120px;

  min-width: 0;
}

/*
 * Tombol kirim ulang nota antre adalah aksi terpenting di kartu ini, jadi
 * bentuknya pil terisi bergaris 2px dari keluarga bahaya — bukan tombol netral
 * seperti aksi lain di panel detail. Teksnya tetap `--pos-text` di atas gradien
 * `--pos-danger-*`, seperti pil solusi panel antrean, jadi kontrasnya tetap
 * >=4,5:1 di kedua tema; tebalnya yang dinaikkan supaya lebih mencolok.
 */
.resend-nota-button {
  display: inline-flex;

  align-items: center;

  gap: 8px;

  flex-shrink: 0;

  min-height: 44px;

  padding: 0 16px;

  border: 2px solid var(--pos-danger);

  border-radius: 999px;

  background: linear-gradient(135deg, var(--pos-danger-bg), var(--pos-danger-deep));

  color: var(--pos-text);

  font-size: 13px;

  font-weight: 800;

  letter-spacing: 0.04em;

  cursor: pointer;

  transition: background 0.15s ease, transform 0.1s ease;
}

.resend-nota-button:hover {
  background: linear-gradient(135deg, var(--pos-danger-deep), var(--pos-danger-edge));
}

.resend-nota-button:active {
  transform: scale(0.98);
}

.resend-nota-button:disabled {
  opacity: 0.5;

  cursor: not-allowed;
}

.detail-item {
  display: flex;

  justify-content: space-between;

  gap: 16px;

  padding: 12px 14px;

  margin-bottom: 8px;

  background: var(--pos-surface);

  border: 1px solid var(--pos-border);

  border-radius: 10px;
}

.detail-item-info {
  display: flex;

  flex-direction: column;

  gap: 4px;

  min-width: 0;
}

.detail-item-info strong {
  color: var(--pos-text);

  font-size: 16px;

  line-height: 1.4;
}

.detail-item-info span {
  color: var(--pos-text-muted);

  font-size: 14px;
}

.detail-item-total {
  flex-shrink: 0;

  align-self: center;

  color: var(--pos-text);

  font-size: 16px;
}

.detail-totals {
  margin-top: 14px;
}

.detail-row {
  display: flex;

  justify-content: space-between;

  padding: 6px 0;

  color: var(--pos-text-muted);

  font-size: 15px;
}

.detail-row strong {
  color: var(--pos-text);
}

.detail-grand-total {
  padding-top: 10px;

  margin-top: 6px;

  border-top: 1px solid var(--pos-border);

  color: var(--pos-text);

  font-size: 17px;

  font-weight: 700;
}

.detail-grand-total strong {
  font-size: 22px;

  font-weight: 700;
}

/* ========================================
   FILTER TANGGAL
======================================== */

.history-filter {
  margin-bottom: 14px;

  padding: 12px 14px;

  border: 1px solid var(--pos-border);

  border-radius: 10px;

  background: var(--pos-surface-alt);
}

.history-filter label {
  display: block;

  margin-bottom: 6px;

  color: var(--pos-text-muted);

  font-size: 13px;

  font-weight: 600;
}

.history-filter-row {
  display: flex;

  gap: 10px;

  align-items: center;
}

/*
 * Baris cakupan nota: tombol cakupan dan kelompok "Status aksi". Kelompoknya
 * adalah satu anak flex, jadi ia membungkus sebagai satu kesatuan -- label dan
 * dropdown-nya tidak pernah terpisah.
 *
 * Sebelumnya baris ini dipaksa turun oleh media query berbasis **lebar
 * jendela** (`max-width: 1365px`), dan itu yang dilaporkan pemilik pada
 * 2026-10-11: di layar lebar pun "Status aksi" bisa turun ke bawah padahal
 * ruangnya ada, karena yang menentukan ruang sebenarnya adalah lebar panel,
 * bukan lebar jendela. Sekarang keputusannya diserahkan pada flexbox: cukup =
 * sebaris, tidak cukup = turun sendiri, tanpa ambang jendela.
 */
.scope-row {
  /*
   * Jarak kolom 8px (bukan 10px bawaan baris filter): ruang barisnya dipakai
   * untuk memuat ketiga kontrolnya sebaris di panel sempit. Jarak baris tetap
   * 8px agar tetap rapi saat benar-benar harus turun.
   */
  column-gap: 8px;

  flex-wrap: wrap;

  row-gap: 8px;
}

/*
 * `flex: 1 1 auto` + `min-width: 0`: saat sebaris, sisa ruang barisnya dipakai
 * dropdown-nya (tidak ada separuh baris kosong di kanan); saat turun, kelompok
 * ini mengisi penuh barisnya -- label di kiri, select melebar seperti kolom
 * tanggal di bawahnya, persis tampilan tablet yang diminta 2026-10-07.
 */
.scope-status-group {
  display: flex;

  /* 6px, bukan 8px: di panel 601px barisnya lewat 1px dari batas dan jadi
     membungkus; selisih kecil ini membuatnya tetap sebaris tanpa mengubah
     tampilan lain. */
  gap: 6px;

  align-items: center;

  flex: 1 1 auto;

  min-width: 0;

  margin-left: auto;
}

/*
 * Label ini sebaris dengan kontrolnya, bukan label blok seperti label filter
 * lain di panel ini.
 */
.scope-status-group label {
  display: inline;

  margin: 0;

  white-space: nowrap;
}

/*
 * Select bawaan browser tampil putih di mode gelap; token POS dipakai supaya
 * tingginya 44px (sama dengan tombol di sebelahnya) dan warnanya ikut tema.
 */
/*
 * Select melebar mengisi sisa kelompoknya. `min-width: 0` mencegah lebar
 * minimum bawaan select memaksa barisnya meluber di panel sempit.
 */
.scope-status-group select {
  flex: 1;

  min-width: 0;

  min-height: 44px;

  padding: 0 10px;

  border: 1px solid var(--pos-border);

  border-radius: 10px;

  background: var(--pos-surface);

  color: var(--pos-text);

  font-size: 14px;
}

.history-filter input[type='date'] {
  flex: 1;

  min-width: 0;

  min-height: 44px;

  padding: 0 12px;

  border: 1px solid var(--pos-border);

  border-radius: 10px;

  background: var(--pos-surface);

  color: var(--pos-text);

  font-size: 15px;
}

.today-button {
  flex-shrink: 0;

  min-height: 44px;

  padding: 0 14px;

  border: 1px solid var(--pos-border);

  border-radius: 10px;

  background: var(--pos-surface);

  color: var(--pos-text);

  font-size: 13px;

  font-weight: 600;

  cursor: pointer;
}

.today-button:disabled {
  opacity: 0.5;

  cursor: not-allowed;
}

.history-filter-hint {
  margin: 8px 0 0;

  color: var(--pos-text-muted);

  font-size: 13px;
}

/* ========================================
   CETAK ULANG STRUK
======================================== */

.reprint-block {
  margin-top: 16px;

  padding-top: 14px;

  border-top: 1px solid var(--pos-border);
}

.reprint-button {
  width: 100%;

  min-height: 52px;

  padding: 0 16px;

  border: 1px solid var(--pos-border);

  border-radius: 10px;

  background: var(--pos-surface);

  color: var(--pos-text);

  font-size: 14px;

  font-weight: 700;

  cursor: pointer;
}

.reprint-button:hover:not(:disabled) {
  background: var(--pos-hover);

  border-color: var(--pos-accent);
}

.reprint-button:disabled {
  opacity: 0.5;

  cursor: not-allowed;
}

.reprint-message {
  margin: 10px 0 0;

  color: var(--pos-text-muted);

  font-size: 13px;
}

/* ========================================
   BUKTI TRANSFER TERTUNDA
======================================== */

.proof-banner {
  margin-bottom: 14px;

  padding: 12px 14px;

  border: 1px solid var(--pos-warning-border);

  border-radius: 10px;

  background: var(--pos-warning-bg);
}

.proof-banner-title {
  margin: 0 0 8px;

  color: var(--pos-warning-text);

  font-size: 14px;

  font-weight: 700;
}

.proof-banner-error {
  margin: 0 0 8px;

  color: var(--pos-danger);

  font-size: 14px;
}

.proof-row {
  display: flex;

  justify-content: space-between;

  align-items: center;

  gap: 10px;

  padding: 8px 0;

  border-top: 1px solid var(--pos-warning-border);
}

.proof-info {
  display: flex;

  flex-direction: column;

  gap: 2px;

  min-width: 0;
}

.proof-info strong {
  color: var(--pos-text);

  font-size: 14px;
}

.proof-info span {
  color: var(--pos-warning-text);

  font-size: 13px;
}

.proof-action-button {
  flex-shrink: 0;

  min-height: 44px;

  padding: 0 14px;

  border: 1px solid var(--pos-border);

  border-radius: 10px;

  background: var(--pos-surface);

  color: var(--pos-text);

  font-size: 13px;

  font-weight: 600;

  cursor: pointer;
}

/*
 * Tiga chip jalur kirim per nota: Server (dari daftar server), Bukti
 * (payment_proofs), Cetak (print_jobs). Kertas keluar bukan bukti server
 * menyimpan — chipnya dibaca dari tabel masing-masing.
 */
.nota-channels {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;

  margin-top: 2px;
}

.channel-chip {
  display: inline-block;

  padding: 1px 7px;

  border: 1px solid var(--pos-border);
  border-radius: 999px;

  background: var(--pos-surface-alt);

  font-size: 11px;
  font-weight: 600;
  line-height: 1.6;
  white-space: nowrap;
}

.channel-chip.is-ok {
  border-color: transparent;

  background: var(--pos-success-bg);

  color: var(--pos-success-text);
}

.channel-chip.is-wait {
  border-color: var(--pos-warning-border);

  background: var(--pos-warning-bg);

  color: var(--pos-warning-text);
}

.channel-chip.is-bad {
  border-color: transparent;

  background: var(--pos-outstock-bg);

  color: var(--pos-outstock-text);
}

.proof-action-button:disabled {
  opacity: 0.5;

  cursor: not-allowed;
}

/* ========================================
   LAYAR SEMPIT: DETAIL DI BAWAH DAFTAR
======================================== */

@media (max-width: 900px) {
  .history-body {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(0, 1fr) minmax(0, 1fr);
  }

  .history-detail-pane {
    border-left: none;

    border-top: 1px solid var(--pos-border);
  }

  .nota-row {
    grid-template-columns: 1fr auto auto;
  }

  .nota-time,
  .nota-chevron {
    display: none;
  }
}

/*
 * Status ringkas di baris judul (permintaan pemilik, 2026-10-10).
 *
 * Status dan aksi yang berhubungan dicetak dalam panel bersama. Baris tetap
 * satu baris yang bisa digeser pada lebar sempit; panel tidak mengecil di bawah
 * ukuran kontrol. Berita hasil transaksi/batch tetap berupa chip terpisah.
 */
/*
 * Jarak antar panel 20px: cukup untuk memisahkan, dan menyisakan lebar supaya
 * dua peringatan masih muat sebaris.
 *
 * Dua perubahan 2026-10-11 dari keluhan pemilik ("box warning terlihat sangat
 * besar", dan "kalau ada 2 warning di layar tab sampai scroll ke kanan dan
 * kiri"):
 * - **Barisnya membungkus, bukan menggeser.** Keputusan 2026-10-10 (satu baris
 *   `nowrap` + `overflow-x: auto`) menahan tinggi header, tetapi menyembunyikan
 *   panel di balik geseran kiri-kanan — di tablet itu justru yang membuat
 *   peringatan kedua tidak terlihat. Sekarang `flex-wrap: wrap` + `row-gap`: dua
 *   peringatan yang tidak muat sebaris turun ke baris berikutnya, tidak ada lagi
 *   isi yang harus digeser.
 * - **Bantalan klip dihapus.** Bantalan 16px tadi hanya ada karena `overflow`
 *   barisnya memotong lencana tutup yang menjorok; tanpa `overflow` itu,
 *   ruangnya tidak dibutuhkan lagi — dan tingginya ikut turun.
 */
.header-notices {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;

  /*
   * 14px antar baris, bukan 8px: kotak-kotaknya memang lebih pendek, tetapi
   * daerah kliknya kini melebar keluar kotaknya (lencana 6px ke atas, chip dan
   * wilayah aksi 4px ke atas-bawah). 6 + 4 = 10px adalah tonjolan terbesar yang
   * bisa bertemu, jadi 14px menyisakan 4px supaya dua sasaran sentuh di baris
   * bersebelahan tidak pernah bertumpuk.
   */
  row-gap: 14px;

  min-width: 0;
  max-width: 100%;

  /* 2px kiri-kanan = ruang cincin fokus chip di ujung baris. */
  padding: 0 2px;
  margin-inline: -2px;
  margin-left: auto;
}

.notice-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;

  position: relative;

  max-width: 240px;

  /*
   * 36px, sama dengan panel di barisnya (permintaan pemilik 2026-10-11: "box
   * nya juga"): satu baris dengan tinggi berbeda-beda terbaca bergerigi.
   * Tinggi itu hanya yang terlihat — daerah kliknya tetap 44px lewat `::after`.
   */
  min-height: 36px;

  flex-shrink: 0;
  padding: 0 12px;

  border: 1px solid var(--pos-border);
  border-radius: 18px;

  background: var(--pos-surface-alt);
  color: var(--pos-text);

  font-size: 13px;
  font-weight: 600;

  cursor: pointer;

  transition: background-color 0.15s ease;
}

.notice-chip:hover {
  background: var(--pos-hover);
}

/* Daerah klik 44px untuk chip 36px; `inset` mendatar 0, jadi tidak menutupi
   tetangganya kiri-kanan. */
.notice-chip::after {
  content: '';
  position: absolute;
  inset: -4px 0;
}

.notice-chip.is-ok {
  border-color: var(--pos-success-text);
  background: var(--pos-success-bg);
  color: var(--pos-success-text);
}

.notice-chip.is-wait {
  border-color: var(--pos-warning-border);
  background: var(--pos-warning-bg);
  color: var(--pos-warning-text);
}

/*
 * Merah = ada yang belum beres dan menunggu tindakan (pilihan pemilik,
 * 2026-10-10): nota belum tersinkron ke server, dan penghubung cetak yang
 * menolak nota secara permanen. Teksnya `--pos-danger` di atas
 * `--pos-danger-bg` (5,1:1 terang / 7,3:1 gelap).
 */
.notice-chip.is-danger {
  border-color: var(--pos-danger);
  background: var(--pos-danger-bg);
  color: var(--pos-danger);
}

/*
 * Panel "peringatan + solusi" (permintaan pemilik, 2026-10-11 — dari gambar
 * acuan): kotak bergaris 2px dengan gradien warna keluarganya, pesan tebal di
 * kiri, wilayah aksi di kanan yang dipisahkan **garis pemisah** setinggi
 * panelnya, dan lencana tutup bulat yang menjorok di sudut kiri atas. Jarak
 * antar panel 24px (16px jarak baris + 8px tambahan khusus di layar
 * >=901px) memisahkannya dari panel tetangganya.
 *
 * Warnanya tetap milik keluarga token POS, bukan warna baru: alias lokal di
 * bawah hanya menunjuk ke variabel main.css, supaya satu blok bentuk ini bisa
 * melayani empat nada (bahaya, peringatan, selesai, netral) tanpa mengulang
 * seluruh deklarasi empat kali.
 */
.notice-pair {
  --notice-bg: var(--pos-danger-bg);
  --notice-deep: var(--pos-danger-deep);
  --notice-edge: var(--pos-danger-edge);
  --notice-border: var(--pos-danger);
  --notice-ink: var(--pos-danger);

  position: relative;
  display: inline-flex;

  /*
   * `stretch`, bukan `center`: wilayah aksinya harus setinggi panel supaya
   * garis pemisahnya benar-benar membentang dari tepi atas ke tepi bawah.
   */
  align-items: stretch;
  gap: 0;

  /*
   * Tinggi 36px (60px → 52px → 48px → 36px; permintaan pemilik 2026-10-11:
   * "masih terlalu besar", lalu "bukan hanya fontnya yang dikecilkan tetapi box
   * nya juga"). 36px = 2x2px garis + 32px isi, dan 32px itu pas untuk pesan
   * 12px dengan tinggi baris 16px plus bantalan 8px di atas dan bawah — yang
   * turun ruang kosongnya, bukan hurufnya (hurufnya tetap 12px).
   *
   * Target sentuh 44px tetap ada walau kotaknya 36px: wilayah aksi dan lencana
   * tutup masing-masing melebarkan daerah kliknya lewat `::after`, jadi
   * "kotak lebih kecil" tidak berarti "sasaran jari lebih kecil".
   */
  min-height: 36px;
  flex-shrink: 0;
  max-width: 100%;

  /* Bantalan kanan milik wilayah aksinya; teksnya membawa bantalannya sendiri. */
  padding: 0 0 0 10px;

  border: 2px solid var(--notice-border);
  border-radius: 10px;
  background: linear-gradient(135deg, var(--notice-bg), var(--notice-deep));
  color: var(--notice-ink);

  /*
   * Bayangan lembut dari token POS + sorotan atas tipis: itu yang membuat
   * panelnya terbaca sebagai bidang bertepi, bukan kotak warna datar.
   */
  box-shadow:
    var(--pos-shadow-soft),
    inset 0 1px 0 rgb(255 255 255 / 0.22);
}

/*
 * Nada panel cetak mengikuti artinya, bukan sekadar ada-tidaknya pekerjaan
 * (keputusan pemilik, 2026-10-10; bentuknya dipakai ulang di sini): kertas
 * belum keluar = amber, penghubung menolak permanen = merah (butuh teknisi),
 * sudah tercetak = hijau, berita netral = tanpa warna status. Kelas nada
 * ditulis setelah `is-print` supaya nada yang lebih khusus menang.
 *
 * Nada amber memakai pasangan amber milik palet pemilik, sama dengan
 * keluarga "stok tipis": `--pos-warning-bg` (#FDEBC8) +
 * `--pos-warning-text` (#8A4B08), dan garisnya tinta itu sendiri
 * (#8A4B08) — resep yang sama dengan panel bahaya, yang juga bergaris
 * tinta (`--pos-danger`). Sebelumnya latarnya krem #FDF3E0 dan
 * garisnya mustard #D9A441 yang tidak ada di palet, dan mustard itu
 * hanya 2,04:1 terhadap latarnya sehingga tepi panelnya nyaris tak
 * terlihat (permintaan pemilik, 2026-10-11: "warna kedua warning tidak
 * cocok ketika light mode", dengan gambar palet sebagai acuan).
 *
 * Wilayah aksinya memakai `--pos-warning-edge`, bukan `--notice-border`:
 * selama garisnya masih mustard, "warna garis" dan "ujung gradien aksi"
 * boleh satu nilai; begitu garisnya menjadi tinta gelap, keduanya harus
 * dipisah — kalau tidak, gradient aksinya ikut menjadi coklat gelap.
 * `-edge` adalah `-deep` yang dicampur tinta keluarganya ~20%, resep
 * yang sama dengan `--pos-danger-edge`.
 */
.notice-pair.is-print,
.notice-pair.is-wait {
  --notice-bg: var(--pos-warning-bg);
  --notice-deep: var(--pos-warning-deep);
  --notice-edge: var(--pos-warning-edge);
  --notice-border: var(--pos-warning-border);
  --notice-ink: var(--pos-warning-text);
}

.notice-pair.is-ok {
  --notice-bg: var(--pos-success-bg);
  --notice-deep: var(--pos-success-bg);
  --notice-edge: var(--pos-success-bg);
  --notice-border: var(--pos-success-text);
  --notice-ink: var(--pos-success-text);
}

.notice-pair.is-danger {
  --notice-bg: var(--pos-danger-bg);
  --notice-deep: var(--pos-danger-deep);
  --notice-edge: var(--pos-danger-edge);
  --notice-border: var(--pos-danger);
  --notice-ink: var(--pos-danger);
}

.notice-pair.is-info {
  --notice-bg: var(--pos-surface);
  --notice-deep: var(--pos-surface-alt);
  --notice-edge: var(--pos-surface-alt);
  --notice-border: var(--pos-border);
  --notice-ink: var(--pos-text);
}

/*
 * Lencana tutup duduk di sudut kiri atas, jadi teksnya digeser ke kanannya.
 * 28px bukan angka asal: daerah klik lencananya (40px) berhenti di x = 27px,
 * sedangkan huruf pertama pesannya mulai di x = 30px (2px garis + 28px) — jadi
 * menyentuh pesannya tidak pernah ikut menutup panelnya.
 */
.notice-pair.has-dismiss {
  padding-left: 28px;
}

/*
 * Wilayah solusi, bukan kotaknya sendiri (permintaan pemilik, 2026-10-11:
 * "tombol solusi tidak perlu berada di dalam kotaknya sendiri, cukup
 * dipisahkan dari tombol warning dengan garis merah seperti itu"). Aksi dan
 * peringatannya tetap satu panel, tetapi aksinya kini **wilayah** di ujung
 * kanan panel: tanpa sudut sendiri di kiri dan tanpa garis keliling — hanya
 * `border-left` 2px setinggi panel sebagai garis pemisah dari pesannya — dan
 * latarnya memakai warna yang dulu menjadi kotak pilnya (`--notice-deep` →
 * `--notice-edge`). Jadi tetap satu kotak, bukan dua kotak bertumpuk.
 *
 * Sudut kanannya mengikuti lengkung panel (16px luar - 2px garis = 14px)
 * supaya latarnya berhenti rapi di dalam bingkai, dan tingginya mengikuti
 * panel (`stretch`, minimal 44px) supaya sasaran jarinya tidak pernah lebih
 * kecil dari tombol lain di baris ini. Teksnya tetap `--pos-text` (>=6,9:1 di
 * seluruh gradiennya).
 */
.notice-pair .chip-action {
  position: relative;
  align-self: stretch;

  /*
   * 32px = tinggi isi panel (36px - 2x2px garis), jadi yang terlihat memang
   * kecil — tetapi daerah kliknya tetap 44px lewat `::after` di bawah ini.
   */
  min-height: 32px;
  margin: 0;
  padding: 0 12px;

  border: 0;
  border-left: 2px solid var(--notice-border);
  border-radius: 0 8px 8px 0;
  background: linear-gradient(135deg, var(--notice-deep), var(--notice-edge));
  color: var(--pos-text);

  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.01em;
  white-space: nowrap;

  /*
   * Kurva pegas, bukan `ease` bawaan: sorotannya melunak seperti benda yang
   * benar-benar ditekan, dan gerakannya hanya menyentuh bayangan/transform
   * sehingga tidak memicu reflow.
   */
  transition: box-shadow 0.35s cubic-bezier(0.32, 0.72, 0, 1);
}

/*
 * Daerah klik 44px tanpa menambah tinggi yang terlihat: kotaknya 32px, melebar
 * 6px ke atas dan ke bawah (4px di antaranya keluar dari kotak panel). Celah
 * antar baris lencana/panel 12px > 4px, jadi dua baris panel yang bertumpuk
 * tidak pernah berebut sentuhan yang sama.
 */
.notice-pair .chip-action::after {
  content: '';
  position: absolute;
  inset: -6px 0;
}

/*
 * Sorotan putih tipis, bukan `brightness()`: di mode gelap `brightness(0.96)`
 * justru menenggelamkan tombolnya, sedangkan selubung putih terbaca sebagai
 * "sedang disorot" di kedua tema.
 */
.notice-pair .chip-action:hover:not(:disabled) {
  box-shadow: inset 0 0 0 999px rgb(255 255 255 / 0.14);
}

.notice-pair .chip-action:active:not(:disabled) {
  /* Tekanan fisik: turun 1px dan mengerut 1% — tetap di jalur transform. */
  transform: translateY(1px) scale(0.99);
}

.notice-pair .chip-action:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

/*
 * Ukuran pesan 13px/700 (sempat 15px/700, lalu diperkecil 2026-10-11 atas
 * permintaan pemilik: panelnya terlihat terlalu besar). Peringatannya tetap
 * dibedakan dari teks biasa lewat ketebalan 700 dan warnanya, bukan lewat
 * ukuran huruf — tinggi dan lebarnya yang turun, bukan keterbacaannya.
 */
.notice-pair-text {
  display: inline-flex;
  align-items: center;

  /*
   * Bantalan kanan 12px = jarak pesan ke garis pemisah; bantalan itu juga
   * yang menjaga teks tidak menempel garis panel saat panelnya tidak punya
   * aksi sama sekali (mis. panel cetak tanpa pekerjaan tertunda).
   */
  padding: 8px 10px 8px 0;

  /*
   * Tinggi baris dipatok 16px supaya tinggi panelnya pasti 32px isi dan tidak
   * bergantung pada `line-height` bawaan (normal ≈ 1,4x = 17px, yang membuat
   * panelnya 1px lebih tinggi dari kotaknya sendiri).
   */
  line-height: 16px;

  min-width: 0;
  overflow: hidden;

  white-space: nowrap;
  font-size: 12px;
  font-weight: 700;
}

/*
 * Tombol tutup: lencananya duduk **tepat di titik sudut kiri atas panel**
 * (permintaan pemilik, 2026-10-11: "tombol X nya bergeser jadi tidak tepat
 * berada pada ujung kiri atas"). Pada sudut melengkung 10px titik terluar
 * lengkungnya ("puncak" sudut) ada di 10 - 10/√2 = 2,93px dari tepi luar panel,
 * dan pusat lencananya dipasang di sana.
 *
 * Kotaknya **adalah** daerah kliknya: 40x44px, jadi tidak perlu `::after`
 * tambahan. Bentuk itu dipilih dari ruang yang benar-benar tersedia, bukan
 * angka bulat:
 * - ke kiri hanya boleh 13px (di 16px sebelah kiri ada panel tetangga; kalau
 *   lebih, sentuhan di ujung kanan tombol "Kirim semua" panel itu malah
 *   menutup peringatan ini), dan ke kanan berhenti di x = 27px, tiga px sebelum
 *   huruf pertama pesannya (x = 30px);
 * - ke atas hanya 6px, karena di atasnya bisa ada chip/panel lain yang jaraknya
 *   14px (celah antar baris) — sisa 38px-nya melebar ke bawah, menutupi badan
 *   panel yang toh tidak punya kendali lain di kolom itu.
 *
 * Jadi: 2px garis + (-13px) = -15px dari tepi dalam, dan (-6px) → -8px.
 */
.notice-dismiss {
  position: absolute;
  top: -8px;
  left: -15px;

  width: 40px;
  height: 44px;
  padding: 0;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

/*
 * Lencananya memakai permukaan + garis nada panelnya: di tema terang jadi
 * lingkaran krem bercincin amber dengan X coklat (persis acuan), di tema gelap
 * cincinnya tetap amber dan X-nya tetap terbaca di atas permukaan gelap.
 */
.notice-dismiss-badge {
  /*
   * Ditempatkan sendiri, tidak lagi dipusatkan flexbox: kotak tombolnya tinggi
   * (44px untuk daerah klik), jadi pusatnya tidak sama dengan pusat kotaknya.
   * (6, -1) di dalam kotak = pusat (3,3) dari tepi luar panel, yaitu puncak
   * lengkung sudutnya.
   */
  position: absolute;
  left: 6px;
  top: -1px;

  display: inline-flex;
  align-items: center;
  justify-content: center;

  /* 20px (30px → 26px → 24px → 20px) mengikuti panelnya yang diperkecil 2026-10-11. */
  width: 20px;
  height: 20px;

  border: 2px solid var(--notice-border);
  border-radius: 999px;
  background: var(--pos-surface);
  color: var(--notice-ink);

  transition:
    background-color 0.35s cubic-bezier(0.32, 0.72, 0, 1),
    transform 0.35s cubic-bezier(0.32, 0.72, 0, 1);
}

.notice-dismiss:hover .notice-dismiss-badge {
  background: var(--pos-hover);
}

.notice-dismiss:active .notice-dismiss-badge {
  transform: scale(0.94);
}

/*
 * Aturan `prefers-reduced-motion` di main.css hanya menjangkau `button` dan
 * `input`; lencananya `span`, jadi transisinya dimatikan di sini juga.
 */
@media (prefers-reduced-motion: reduce) {
  .notice-dismiss-badge,
  .notice-pair .chip-action {
    transition: none;
  }
}

.notice-chip.is-info {
  border-color: var(--pos-border);
  background: var(--pos-surface);
  color: var(--pos-text-muted);
}

/*
 * Teks chip dipotong dengan elipsis; kalimat lengkapnya tetap ada di title dan
 * aria-label, jadi yang diringkas hanya tampilannya. Tiap teks chip juga
 * `role="status"` — pengganti live region band lama: saat status cetak atau
 * antrean berubah selagi modal terbuka, perubahannya tetap diumumkan.
 */
.notice-chip-text {
  overflow: hidden;

  white-space: nowrap;
  text-overflow: ellipsis;
}

/* X hasil transaksi/batch tetap menjadi affordance pada chip kecil. */
.notice-chip-close {
  flex-shrink: 0;
}

.chip-action {
  min-height: 44px;
  flex-shrink: 0;
  padding: 0 14px;

  border: 1px solid var(--pos-control-border);
  border-radius: 22px;

  background: var(--pos-surface);
  color: var(--pos-text);

  font-size: 13px;
  font-weight: 600;

  cursor: pointer;
}

.chip-action:hover:not(:disabled) {
  background: var(--pos-hover);
}

.chip-action:disabled {
  opacity: 0.5;

  cursor: not-allowed;
}

/*
 * Ambangnya dulu 900px (lebar modal pada tablet tegak). Sejak 2026-10-11
 * ambangnya 1120px dan barisnya membungkus: percobaan 2026-10-10 yang menahan
 * satu baris geser (`nowrap` + `overflow-x`) memang menjaga tinggi header, tapi
 * di tablet peringatan kedua jadi tersembunyi di balik geseran — dan itu yang
 * dilaporkan pemilik. Angka lama "membungkus = 378px" berlaku untuk enam chip
 * lama; dengan panel yang sudah diperkecil (52px) baris kedua berharga ~60px dan
 * hanya muncul kalau memang tidak muat.
 */
/*
 * Di bawah 900px jendela baris panelnya pindah ke barisnya sendiri selebar
 * header, karena di lebar itu tidak ada lagi ruang untuk dibagi dengan judul.
 * Ambang ini sempat dinaikkan ke 1120px 2026-10-11, tetapi pemilik melaporkan
 * panelnya lalu "malah pindah ke bawah" di tablet — jadi layout satu-baris itu
 * dikembalikan: judul yang mengecil (lihat `.modal-title`), bukan panelnya yang
 * turun.
 */
@media (max-width: 900px) {
  .modal-header {
    flex-wrap: wrap;
  }

  /*
   * Di baris yang sudah sempit, judul justru harus boleh mengecil — dan ruang
   * tombol tutupnya disisakan lebih dulu. Tanpa `max-width` ini tombol × selalu
   * memilih turun ke barisnya sendiri (52px) daripada mendesak judulnya.
   */
  .modal-title {
    max-width: calc(100% - 56px);

    flex-shrink: 1;
  }

  .header-notices {
    /*
     * `order: 1` menaruh strip panel di baris kedua, sehingga tombol tutup tetap
     * sebaris dengan judul. Tanpa itu strip selebar penuh memaksa tombol ×
     * turun ke baris ketiganya sendiri — 52px ekstra hanya untuk satu tombol.
     * Urutan DOM tidak berubah; yang bergeser hanya tampilannya.
     */
    order: 1;

    width: 100%;
    margin-left: 0;
  }

  .notice-chip {
    max-width: 200px;
  }
}
/*
 * Dua panel peringatan yang bersebelahan mendapat 8px tambahan — jadi 24px —
 * hanya di layar lebar (permintaan pemilik, 2026-10-11: "berikan jarak lebih
 * antar warning. Jika di layar yang lebih kecil sudah cukup berjarak tanpa
 * membuat warning nya bisa di scroll maka biarkan"). Ambang 901px adalah
 * pelengkap ambang tata letak barisnya (`max-width: 900px`), jadi di bawah itu
 * jaraknya tetap 16px — di 601-900px kedua panel memang masih duduk sebaris
 * tanpa perlu digeser, dan pemilik memilih membiarkannya.
 *
 * Jaraknya ditambahkan lewat `column-gap` barisnya, bukan `margin` pada panel
 * kedua: antara 901px dan ~1006px barisnya masih bisa membungkus (terukur di
 * 901x800, tinggi header 123px), dan `margin-left` akan menggeser panel yang
 * turun ke baris kedua sehingga tampak menjorok 8px dari panel pertama.
 */
@media (min-width: 901px) {
  .header-notices {
    /* 24px = 16px jarak dasar baris + 8px tambahan. */
    column-gap: 24px;
  }
}
</style>
