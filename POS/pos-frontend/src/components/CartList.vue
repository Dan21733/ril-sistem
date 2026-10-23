<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { createUuid } from '../services/id'
import { compressProofImage } from '../services/image/compressProofImage'
import {
  deletePaymentProof,
  getPaymentProof,
  savePaymentProof,
} from '../services/storage/paymentProofRepository'
import { findWorkerNameByPin } from '../services/workers/workerDirectory'
import { DEFAULT_SUGGESTION_COUNT, suggestCashAmounts } from '../utils/cashSuggestion'
import { shouldCloseCheckoutModal } from '../utils/checkoutModal'
import { PRICE_RANGE_PROBLEM, priceRangeProblem, resolveItemPrices } from '../utils/priceRange'
import vModalFocus from '../directives/modalFocus'
import {
  digitsOnly,
  formatRupiahInput,
  preventNonNumericInput,
} from '../utils/rupiahInput'
import {
  FLOOR_CODE_LEGEND,
  FLOOR_CODE_MNEMONIC,
  floorCodeFor,
  isRoundedFloorCode,
} from '../utils/floorCode'

const props = defineProps({
  cartItems: {
    type: Array,
    required: true,
  },
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
  pendingReceipt: {
    type: Object,
    default: null,
  },
  retryAfterSeconds: {
    type: Number,
    default: 0,
  },
  /* Status bukti transfer, terpisah dari status notanya (bagian 7.2). */
  proofSyncMessage: {
    type: String,
    default: '',
  },
  /*
   * Antrean multi-nota dan hasil batch tidak lagi diterima di sini: sejak
   * 2026-10-09 notifikasi dan aksinya tinggal di modal Riwayat Nota supaya
   * kolom keranjang tidak kehilangan tinggi (permintaan pemilik). Kontraknya
   * sama, hanya pemilik tampilannya yang pindah.
   */
})

const emit = defineEmits([
  'increase',
  'decrease',
  'remove',
  'clear',
  'update-quantity',
  'update-price',
  'complete-transaction',
  'checkout-finished',
])

/*
 * Sisa versi lama: nama kasir dulu diketik dan disimpan di localStorage.
 * Dibersihkan supaya tidak ada nama basi yang mengesankan kasir masih terisi.
 */
/* Lebar yang sama dengan satu-kolom checkout (`@media (max-width: 900px)` di
   bawah): di layar kecil hanya tiga saran yang muat rapi (permintaan pemilik,
   2026-10-14). */
const NARROW_CASH_MEDIA_QUERY = '(max-width: 900px)'
const NARROW_CASH_SUGGESTION_COUNT = 3

const isNarrowCashLayout = ref(false)
let cashMediaQueryList = null

function syncCashLayout() {
  isNarrowCashLayout.value = Boolean(cashMediaQueryList?.matches)
}

onMounted(() => {
  try {
    localStorage.removeItem('cashierName')
  } catch {
    /* Penyimpanan diblokir: tidak ada yang perlu dibersihkan. */
  }
  if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
    cashMediaQueryList = window.matchMedia(NARROW_CASH_MEDIA_QUERY)
    syncCashLayout()
    if (typeof cashMediaQueryList.addEventListener === 'function') {
      cashMediaQueryList.addEventListener('change', syncCashLayout)
    } else if (typeof cashMediaQueryList.addListener === 'function') {
      cashMediaQueryList.addListener(syncCashLayout)
    }
  }
})

/* ========================================
   CHECKOUT STATE
======================================== */

/*
 * Tiga langkah saja sejak permintaan pemilik 2026-09-29:
 *
 *   1. Konfirmasi Pesanan
 *   2. Pengambilan + metode bayar + pembayaran (satu layar)
 *   3. PIN Kasir
 */
const STEP_ORDER = 1
const STEP_PAYMENT = 2
const STEP_PIN = 3

const showConfirmation = ref(false)

const checkoutStep = ref(STEP_ORDER)

const transitionDirection = ref('forward')

const deliveryMethod = ref('')

const shippingCost = ref('')

/*
 * Data pelanggan per nota (keputusan pemilik 2026-10-14, CUSTOMER_DESIGN.md):
 * nama wajib diisi untuk penjualan baru, No. HP opsional. Pola HP sama
 * dengan yang ditegakkan serializer backend supaya pesan kasir konsisten.
 */
const customerName = ref('')
const customerPhone = ref('')
const CUSTOMER_PHONE_PATTERN = /^\+?[0-9][0-9\s()-]*$/
const CUSTOMER_PHONE_MAX_LENGTH = 20

const isCustomerPhoneValid = computed(() => {
  const phone = customerPhone.value.trim()
  return !phone
    || (phone.length <= CUSTOMER_PHONE_MAX_LENGTH && CUSTOMER_PHONE_PATTERN.test(phone))
})

const paymentMethod = ref('')

const cashReceived = ref('')

const cashierPin = ref('')
const completing = ref(false)
const isRetry = computed(() => ['retryable', 'rate_limited'].includes(props.checkoutStatus))

/*
 * Blok hasil transaksi yang sukses, nomor nota, status cetak, dan tombol cetak
 * ulang sudah pindah ke Riwayat Nota (permintaan pemilik, 2026-10-10). Yang
 * tinggal di kolom keranjang hanya pesan yang menghalangi penjualan berikutnya
 * plus status bukti transfer nota terakhir yang belum terkirim.
 */
const cartFeedbackVisible = computed(() => Boolean(
  props.proofSyncMessage
  || (props.checkoutStatus !== 'success' && props.checkoutMessage),
))

const cartFeedbackIsProofOnly = computed(() => (
  props.checkoutStatus === 'success' || !props.checkoutMessage
))

/* ========================================
   NAMA KASIR DARI PIN

   Nama kasir tidak lagi diketik (KEPUTUSAN-10): ia diambil dari worker yang
   memiliki PIN, lewat daftar kasir yang disimpan tablet (services/workers/).
   Selama PIN belum lengkap atau belum cocok, nama dibiarkan kosong — nota yang
   dicetak tanpa nama lebih baik daripada nota yang mencetak nama yang salah.
======================================== */

const resolvedCashierName = ref('')
const pendingReceiptName = computed(() => props.pendingReceipt?.cashier_name || '')

/*
 * Hasil pencocokan bcrypt, terpisah dari namanya: inilah bukti bahwa perangkat
 * ini sendiri mengenali PIN-nya. Dipakai `App.vue` untuk memutuskan nota boleh
 * dicetak sekarang (PIN sudah membuktikan transaksinya final) atau harus
 * menunggu server menerima transaksinya (permintaan pemilik, 2026-09-30).
 */
const cashierPinMatched = ref(false)

/* Ketikan yang lebih baru membatalkan hasil pencarian yang lama. */
let cashierLookupToken = 0

async function resolveCashier(pin) {
  const submitted = String(pin ?? '')
  const token = (cashierLookupToken += 1)

  if (!/^\d{6}$/.test(submitted)) {
    resolvedCashierName.value = ''
    cashierPinMatched.value = false

    return { name: '', matched: false }
  }

  let name

  try {
    name = (await findWorkerNameByPin(submitted)) || ''
  } catch {
    name = ''
  }

  if (token !== cashierLookupToken) {
    return { name: resolvedCashierName.value, matched: cashierPinMatched.value }
  }

  resolvedCashierName.value = name
  cashierPinMatched.value = Boolean(name)

  return { name, matched: Boolean(name) }
}

/*
 * Pencarian dijalankan begitu 6 digit terisi, bukan saat tombol ditekan: kasir
 * melihat namanya lebih dulu dan bisa mengganti PIN sebelum nota tercetak.
 */
watch(cashierPin, (pin) => {
  void resolveCashier(pin)
})

/* Perangkat ini belum mengenal PIN tersebut (daftar kasir kosong atau basi). */
const cashierPinUnknown = computed(
  () => cashierPin.value.length === 6 && !cashierPinMatched.value,
)

/* ========================================
   KODE HARGA FLOOR
======================================== */

/*
 * Kasir melihat harga floor sebagai kode, bukan angka, karena layar ini juga
 * terlihat pelanggan (utils/floorCode.js).
 */
function floorCode(item) {
  return floorCodeFor(item.floor_price)
}

/*
 * Hanya sebagian harga yang perlu dibulatkan (misalnya 1.405.700); catatan
 * tanda "~" baru muncul kalau di keranjang memang ada kode seperti itu.
 */
const hasRoundedFloorCode = computed(() =>
  props.cartItems.some((item) => isRoundedFloorCode(floorCode(item))),
)

/*
 * Bukti transfer disimpan di Dexie sejak foto dipilih (KEPUTUSAN-4, bagian
 * 7.1), jadi yang dipegang komponen ini adalah `proof_id`, bukan berkas di
 * memori. `transferProofPreview` hanya URL pratinjau dari blob tersimpan.
 */
const transferProofId = ref(null)

const transferProofPreview = ref('')

const transferProofBusy = ref(false)

const transferProofError = ref('')

let transferProofPreviewUrl = ''

/* ========================================
   TOTAL PRICE
======================================== */

const totalPrice = computed(() => {
  return props.cartItems.reduce((total, item) => {
    return total + Number(item.unit_price ?? item.price) * Number(item.quantity)
  }, 0)
})

const totalQuantity = computed(() => {
  return props.cartItems.reduce((total, item) => total + Number(item.quantity), 0)
})

const hasValidCashierPin = computed(() => cashierPin.value.length === 6)

/*
 * Harga di bawah floor (keputusan pemilik 2026-10-17): boleh, asal disetujui.
 * Pilihannya dua: PIN penyetuju (karyawan yang ditandai pemilik di master),
 * atau "tidak ada penyetuju" sehingga nota dikirim untuk ACC pemilik di master
 * (Nota perlu ACC). PIN penyetuju diperiksa server, bukan di tablet.
 */
const approverPin = ref('')
const mintaAcc = ref(false)

const needsFloorApproval = computed(() => props.cartItems.some((item) => {
  const resolved = resolveItemPrices(item)
  return priceRangeProblem(item.unit_price ?? resolved.price, resolved.floor_price, resolved.price)
    === PRICE_RANGE_PROBLEM.BELOW
}))

const floorApprovalReady = computed(() => !needsFloorApproval.value
  || isRetry.value || approverPin.value.length === 6 || mintaAcc.value)

function updateApproverPin(event) {
  approverPin.value = event.target.value.replace(/\D/g, '').slice(0, 6)
  if (approverPin.value) {
    mintaAcc.value = false
  }
}

const isEditingLocked = computed(() => completing.value ||
  ['restoring', 'submitting', 'conflict', 'review']
    .includes(props.checkoutStatus)
  /*
   * Cooldown PIN lokal (App.vue) hanya menahan selama hitungannya berjalan:
   * begitu sisa waktunya nol, kolom dan tombolnya terbuka lagi tanpa perlu
   * status baru dari induk.
   */
  || (props.checkoutStatus === 'pin_cooldown' && props.retryAfterSeconds > 0),
)

/* ========================================
   FINAL TOTAL
======================================== */

const finalTotal = computed(() => {
  const shipping = deliveryMethod.value === 'delivery' ? Number(shippingCost.value || 0) : 0

  return totalPrice.value + shipping
})

/* ========================================
   CHANGE
======================================== */

const changeAmount = computed(() => {
  const received = Number(cashReceived.value || 0)

  if (received < finalTotal.value) {
    return 0
  }

  return received - finalTotal.value
})

/* ========================================
   SARAN NOMINAL UANG TUNAI

   Satu tap mengisi kolom "Uang Diterima". Saran disusun dari kombinasi uang
   rupiah yang beredar (utils/cashSuggestion.js); semua saran selalu >= total,
   jadi tombol ini tidak pernah membuat transaksi gagal validasi.

   Empat tombol di layar lebar (permintaan pemilik, 2026-10-09), yaitu bawaan
   modul (`DEFAULT_SUGGESTION_COUNT`); tiga tombol di layar kecil
   (permintaan pemilik, 2026-10-14) supaya muat tanpa memadatkan baris.
   Sebelumnya komponen meminta 8, dan untuk total 307.000 itu menampilkan
   keenam nominal yang mungkin sekaligus.
======================================== */

const suggestedCashAmounts = computed(() => {
  if (paymentMethod.value !== 'cash' || finalTotal.value <= 0) {
    return []
  }

  return suggestCashAmounts(finalTotal.value, {
    max: isNarrowCashLayout.value
      ? NARROW_CASH_SUGGESTION_COUNT
      : DEFAULT_SUGGESTION_COUNT,
  })
})

function fillCashReceived(amount) {
  if (isEditingLocked.value) {
    return
  }

  cashReceived.value = String(amount)
}

/* ========================================
   FORMAT PRICE
======================================== */

function formatPrice(price) {
  return new Intl.NumberFormat('id-ID', {
    style: 'currency',
    currency: 'IDR',
    maximumFractionDigits: 0,
  }).format(Number(price))
}

/* ========================================
   QUANTITY
======================================== */

function increaseQuantity(item) {
  emit('increase', item)
}

function decreaseQuantity(item) {
  emit('decrease', item)
}

function updateQuantity(item, event) {
  emit('update-quantity', {
    item,
    quantity: event.target.value,
  })
}

/*
 * Kolom harga satuan menampilkan angka bertitik ribuan seperti kolom uang lain
 * (audit tampilan, 2026-10-09): "95000" dan "950000" hanya beda satu nol, dan
 * itu uang. Nilai yang dikirim tetap digit polos lewat `digitsOnly`.
 */
function formatCartPriceInput(event) {
  event.target.value = formatRupiahInput(event.target.value)
}

function updatePrice(item, event) {
  const input = event.target
  const price = Number(digitsOnly(input.value))
  const floorPrice = Number(item.floor_price)
  const ceilingPrice = Number(item.price)

  /*
   * Di bawah floor sekarang boleh diketik (ditandai merah dan butuh
   * persetujuan saat bayar); yang tetap ditolak hanya di atas pricelist dan
   * barang yang rentang harganya belum ada.
   */
  if (
    item.floor_price == null ||
    item.price == null ||
    !Number.isSafeInteger(price) ||
    !Number.isSafeInteger(floorPrice) ||
    !Number.isSafeInteger(ceilingPrice) ||
    price < 0 ||
    price > ceilingPrice
  ) {
    /*
     * Pesan tidak boleh memuat angka floor/ceiling: layar kasir terlihat
     * pelanggan, jadi batas harga cukup disebut tanpa angka.
     */
    input.setCustomValidity(
      item.floor_price == null || item.price == null
        ? 'Rentang harga belum tersedia; gunakan harga pricelist.'
        : 'Harga di atas harga pricelist.',
    )
    input.reportValidity()
    input.value = formatRupiahInput(item.unit_price ?? item.price ?? '')
    return
  }

  input.setCustomValidity('')
  emit('update-price', { item, price })
}

function openRetryCheckout() {
  retryTransaction()
}

function retryTransaction() {
  if (props.checkoutStatus !== 'retryable' && props.checkoutStatus !== 'rate_limited') {
    return
  }

  if (props.checkoutStatus === 'rate_limited' && props.retryAfterSeconds > 0) {
    return
  }

  emit('complete-transaction', {})
}

watch(() => props.pendingReceipt, (sale) => {
  if (!sale) {
    return
  }
  deliveryMethod.value = sale.fulfillment
  shippingCost.value = String(sale.shipping_amount || 0)
  customerName.value = sale.cust_name || ''
  customerPhone.value = sale.phone_no || ''
  paymentMethod.value = sale.payment_method
  cashReceived.value = String(sale.payment_amount)
  resolvedCashierName.value = sale.cashier_name || ''
}, { immediate: true })

watch(() => props.checkoutStatus, (status, previousStatus) => {
  if (status === 'success') {
    cashierPin.value = ''
    /*
     * Nama pelanggan tidak boleh bocor ke penjualan berikutnya: nota baru
     * berarti pelanggan baru, dan nama basi yang tercetak salah lebih
     * berbahaya daripada mengetik ulang (CUSTOMER_DESIGN.md).
     */
    customerName.value = ''
    customerPhone.value = ''
    /*
     * Bukti sudah dikaitkan ke nota oleh App.vue, jadi fungsi ini hanya
     * mereset tampilan — record-nya tetap menunggu giliran diunggah.
     */
    void discardUnusedTransferProof()
    showConfirmation.value = false
  } else if (status !== 'submitting') {
    if (status === 'pending' || status === 'unknown' || status === 'syncing') {
      return
    }
    cashierPin.value = ''
    /*
     * Keputusan menutup modal ada di satu tempat (`utils/checkoutModal.js`) —
     * termasuk jalur offline: nota sudah masuk antrean, status induk kembali
     * `idle`, dan modal harus keluar supaya kasir bisa mulai nota berikutnya.
     */
    if (shouldCloseCheckoutModal(status, previousStatus)) {
      showConfirmation.value = false
    }
  }
})

function preventInvalidQuantity(event) {
  if (
    event.key === 'e' ||
    event.key === 'E' ||
    event.key === '+' ||
    event.key === '-' ||
    event.key === '.'
  ) {
    event.preventDefault()
  }
}

/* ========================================
   CLEAR CART
======================================== */

function clearCart() {
  if (isEditingLocked.value) {
    return
  }

  emit('clear')

  /* Cart kosong berarti bukti yang sudah dipilih tidak punya nota lagi. */
  void discardUnusedTransferProof()
}

/* ========================================
   OPEN CHECKOUT
======================================== */

function openConfirmation() {
  if (
    props.cartItems.length === 0 || isEditingLocked.value || props.pendingReceipt
  ) {
    return
  }

  /*
   * Selesaikan status sukses/error sebelumnya agar checkout baru mulai bersih.
   */
  if (['success', 'error'].includes(props.checkoutStatus)) {
    emit('checkout-finished')
  }

  checkoutStep.value = STEP_ORDER
  transitionDirection.value = 'forward'

  showConfirmation.value = true
}

/* ========================================
   LANGKAH 1 → LANGKAH 2
======================================== */

function confirmOrder() {
  transitionDirection.value = 'forward'

  checkoutStep.value = STEP_PAYMENT
}

/* ========================================
   KELENGKAPAN LANGKAH 2
======================================== */

/*
 * Semua syarat langkah gabungan (pengambilan + metode bayar + pembayaran)
 * dicek sekaligus karena kasir melihatnya dalam satu layar. Server tetap
 * memvalidasi ulang; ini hanya menjaga tombol konfirmasi tidak aktif sia-sia.
 */
const isPaymentStepComplete = computed(() => {
  if (!deliveryMethod.value) {
    return false
  }

  if (deliveryMethod.value === 'delivery' && !shippingCost.value) {
    return false
  }

  if (!customerName.value.trim()) {
    return false
  }

  if (!isCustomerPhoneValid.value) {
    return false
  }

  if (!paymentMethod.value) {
    return false
  }

  if (paymentMethod.value === 'cash' && Number(cashReceived.value) < finalTotal.value) {
    return false
  }

  if (paymentMethod.value === 'bank_transfer' && !transferProofId.value) {
    return false
  }

  return true
})

/* ========================================
   LANGKAH 2 → LANGKAH 3 (PIN)
======================================== */

function confirmPayment() {
  if (!isPaymentStepComplete.value) {
    return
  }

  transitionDirection.value = 'forward'

  checkoutStep.value = STEP_PIN
}

/* ========================================
   BACK
======================================== */

function goBack() {
  if (isEditingLocked.value || checkoutStep.value <= STEP_ORDER) {
    return
  }

  transitionDirection.value = 'back'

  checkoutStep.value = checkoutStep.value === STEP_PIN ? STEP_PAYMENT : STEP_ORDER
}

/* ========================================
   CLOSE CHECKOUT
======================================== */

function closeConfirmation() {
  if (!completing.value && props.checkoutStatus !== 'submitting' && !props.pendingReceipt) {
    showConfirmation.value = false
  }
}

/* ========================================
   SHIPPING INPUT
======================================== */

function updateShippingCost(event) {
  if (isEditingLocked.value) {
    return
  }
  shippingCost.value = event.target.value.replace(/\D/g, '')
}

/* ========================================
   CASH INPUT
======================================== */

function updateCashReceived(event) {
  if (isEditingLocked.value) {
    return
  }
  cashReceived.value = digitsOnly(event.target.value)
}

/* ========================================
   CASHIER PIN
======================================== */

function updateCashierPin(event) {
  cashierPin.value = event.target.value.replace(/\D/g, '').slice(0, 6)
}

/* ========================================
   TRANSFER PROOF
======================================== */

function setTransferProofPreview(blob) {
  if (transferProofPreviewUrl) {
    URL.revokeObjectURL(transferProofPreviewUrl)
  }

  transferProofPreviewUrl = URL.createObjectURL(blob)
  transferProofPreview.value = transferProofPreviewUrl
}

function resetTransferProofUi() {
  if (transferProofPreviewUrl) {
    URL.revokeObjectURL(transferProofPreviewUrl)
    transferProofPreviewUrl = ''
  }

  transferProofId.value = null
  transferProofPreview.value = ''
  transferProofError.value = ''
  transferProofBusy.value = false
}

/*
 * Membuang bukti yang belum terpakai (notanya belum ada). Bukti yang sudah
 * menempel pada nota tidak dihapus dari sini: antrean unggah yang mengurusnya,
 * dan bukti yang sudah diterima server tidak pernah dihapus (append-only).
 */
async function discardUnusedTransferProof() {
  const proofId = transferProofId.value

  try {
    if (proofId) {
      const proof = await getPaymentProof(proofId)

      if (proof && !proof.sync_id) {
        await deletePaymentProof(proofId)
      }
    }
  } catch {
    /*
     * Kegagalan membersihkan pratinjau lama tidak boleh menghalangi checkout.
     */
  }

  resetTransferProofUi()
}

async function updateTransferProof(event) {
  if (isEditingLocked.value) {
    return
  }
  const [file] = event.target.files || []

  /* Direset supaya memilih berkas yang sama dua kali tetap memicu `change`. */
  event.target.value = ''

  if (!file) {
    return
  }

  transferProofBusy.value = true
  transferProofError.value = ''

  try {
    const compressed = await compressProofImage(file)
    const previous = transferProofId.value
      ? await getPaymentProof(transferProofId.value)
      : null

    /*
     * Bukti yang sudah diterima server tidak pernah ditimpa: foto baru berarti
     * `proof_id` baru. Selama belum terunggah, mengganti foto cukup memperbarui
     * record yang sama supaya satu nota tidak punya dua bukti kembar.
     */
    const proofId =
      previous && previous.upload_status !== 'uploaded' ? previous.proof_id : createUuid()
    const now = new Date().toISOString()

    await savePaymentProof({
      ...previous,
      proof_id: proofId,
      sync_id: previous?.sync_id || null,
      transaction_id: previous?.transaction_id || null,
      blob: compressed.blob,
      content_type: compressed.contentType,
      byte_size: compressed.byteSize,
      width: compressed.width,
      height: compressed.height,
      /* `false` = berkas diteruskan apa adanya (KEPUTUSAN-8). */
      recompressed: compressed.reencoded,
      captured_at: now,
      created_at: previous?.created_at || now,
      upload_status: previous?.sync_id ? 'pending' : 'captured',
      upload_attempts: 0,
      last_attempt_at: null,
      next_attempt_at: null,
      server_sha256: null,
      uploaded_at: null,
      detail: null,
    })

    transferProofId.value = proofId
    setTransferProofPreview(compressed.blob)
  } catch (error) {
    transferProofError.value =
      error.message || 'Foto bukti tidak dapat diproses. Ambil ulang foto.'
  } finally {
    transferProofBusy.value = false
  }
}

onBeforeUnmount(() => {
  if (transferProofPreviewUrl) {
    URL.revokeObjectURL(transferProofPreviewUrl)
  }
  if (cashMediaQueryList) {
    if (typeof cashMediaQueryList.removeEventListener === 'function') {
      cashMediaQueryList.removeEventListener('change', syncCashLayout)
    } else if (typeof cashMediaQueryList.removeListener === 'function') {
      cashMediaQueryList.removeListener(syncCashLayout)
    }
    cashMediaQueryList = null
  }
})

/* ========================================
   COMPLETE TRANSACTION
======================================== */

async function completeTransaction() {
  if (isEditingLocked.value || transferProofBusy.value
    || (props.checkoutStatus === 'rate_limited' && props.retryAfterSeconds > 0)
    || !isPaymentStepComplete.value || !hasValidCashierPin.value || !floorApprovalReady.value) {
    return
  }

  // Claim the click before bcrypt/IndexedDB awaits, not only after the parent emits.
  completing.value = true
  const pin = cashierPin.value
  const snapshot = {
    paymentMethod: paymentMethod.value,
    deliveryMethod: deliveryMethod.value,
    shippingCost: deliveryMethod.value === 'delivery' ? Number(shippingCost.value) : 0,
    customerName: customerName.value.trim(),
    customerPhone: customerPhone.value.trim(),
    cashReceived: paymentMethod.value === 'cash' ? Number(cashReceived.value) : 0,
    change: paymentMethod.value === 'cash' ? changeAmount.value : 0,
    total: finalTotal.value,
    proofId: transferProofId.value,
  }
  try {
    const { name: cashierName, matched: pinVerifiedLocally } = await resolveCashier(pin)
    if (cashierPin.value !== pin || !hasValidCashierPin.value
      || ['restoring', 'submitting', 'review', 'conflict'].includes(props.checkoutStatus)) {
      return
    }
    emit('complete-transaction', {
      ...snapshot,
      cashierName,
      pin,
      pinVerifiedLocally,
      approverPin: needsFloorApproval.value && approverPin.value.length === 6 ? approverPin.value : null,
      mintaAcc: needsFloorApproval.value && !approverPin.value && mintaAcc.value,
    })
    cashierPin.value = ''
    approverPin.value = ''
    mintaAcc.value = false
  } finally {
    completing.value = false
  }
}

</script>

<template>
  <aside class="cart">
    <!-- ==================================
         CART HEADER
    =================================== -->

    <div class="cart-header">
      <h2>Keranjang</h2>

      <p v-if="hasRoundedFloorCode" class="floor-legend is-note">
        Tanda ~ pada kode berarti harga floor dibulatkan.
      </p>
    </div>

    <!-- ==================================
         CART ITEMS
    =================================== -->

    <div class="cart-items">
      <div v-for="item in cartItems" :key="item.cartId" class="cart-item">
        <div class="cart-item-top">
          <h3>
            {{ item.item_name }}

            <span v-if="item.variant && item.variant !== 'N/A'">
              -
              {{ item.variant }}
            </span>
          </h3>

          <strong class="cart-item-total">
            {{ formatPrice(Number(item.unit_price ?? item.price) * Number(item.quantity)) }}
          </strong>
        </div>

        <div class="cart-item-price-row">
          <label class="cart-price-input">
            <!--
              "Harga" saja, bukan "Harga satuan": label yang lebih panjang
              mendorong kode floor turun ke baris kedua pada kode 7 huruf
              (audit tampilan, 2026-10-09). Arti lengkapnya tetap ada di
              `aria-label` kolom ini.
            -->
            Harga
            <!--
              Teks, bukan `type="number"`: batas harga sudah dicek di JavaScript
              (pesannya sengaja tidak menyebut angka floor), dan angka bertitik
              ribuan tidak sah di kolom number.
            -->
            <input
              type="text"
              inputmode="numeric"
              autocomplete="off"
              :value="formatRupiahInput(item.unit_price ?? item.price ?? '')"
              :disabled="isEditingLocked"
              :class="{ 'price-input-invalid': item.floor_price != null && item.price != null && (Number(item.unit_price ?? item.price) < Number(item.floor_price) || Number(item.unit_price ?? item.price) > Number(item.price)) }"
              :aria-label="`Harga ${item.item_name}`"
              @keydown="preventNonNumericInput"
              @input="formatCartPriceInput"
              @change="updatePrice(item, $event)"
            />
          </label>

          <!--
            Harga floor hanya tampil sebagai kode supaya tidak terbaca langsung
            oleh pelanggan di depan kasir.
          -->

          <span
            v-if="floorCode(item)"
            class="floor-code"
            :title="`Kode harga floor. Kata kunci ${FLOOR_CODE_MNEMONIC}: ${FLOOR_CODE_LEGEND}. Tanda ~ berarti harga dibulatkan.`"
          >
            Floor
            <strong>{{ floorCode(item) }}</strong>
          </span>
        </div>

        <!-- Quantity -->

        <div class="cart-item-controls">
          <button
            type="button"
            :disabled="Number(item.quantity) <= 1 || isEditingLocked"
            aria-label="Kurangi jumlah"
            @click="decreaseQuantity(item)"
          >
            −
          </button>

          <input
            class="quantity-input"
            type="number"
            :value="item.quantity"
            :disabled="isEditingLocked"
            min="1"
            inputmode="numeric"
            aria-label="Jumlah"
            @keydown="preventInvalidQuantity"
            @change="updateQuantity(item, $event)"
          />

          <button
            type="button"
            :disabled="isEditingLocked"
            aria-label="Tambah jumlah"
            @click="increaseQuantity(item)"
          >
            +
          </button>

          <button
            type="button"
            class="remove-button"
            :disabled="isEditingLocked"
            aria-label="Hapus item"
            @click="emit('remove', item)"
          >
            ×
          </button>
        </div>
      </div>

      <p v-if="cartItems.length === 0" class="empty-cart">Keranjang kosong</p>
    </div>

    <!-- ==================================
         CART FOOTER
    =================================== -->

    <div class="cart-footer">
      <div
        v-if="cartFeedbackVisible"
        class="checkout-feedback"
        :class="[`is-${checkoutStatus}`, { 'is-proof-only': cartFeedbackIsProofOnly }]"
        role="alert"
      >
        <span v-if="checkoutStatus !== 'success' && checkoutMessage">
          {{ checkoutMessage }}
        </span>
        <span v-if="checkoutStatus === 'conflict' || checkoutStatus === 'review'
          || (checkoutStatus === 'error' && pendingReceipt)">
          Jangan kirim ulang. Nota dan bukti tetap tersimpan; minta penanggung jawab memeriksa sebelum membuat transaksi baru.
        </span>
        <span v-if="checkoutStatus !== 'success' && checkoutResult?.transaction_id">
          Nota {{ checkoutResult.transaction_id }}
        </span>
        <span v-if="pendingReceipt">
          Kasir {{ pendingReceipt.cashier_name || 'belum dikenal lokal' }} ·
          {{ pendingReceipt.payment_method }} · Total {{ formatPrice(pendingReceipt.total_amount) }}
        </span>
        <span v-if="proofSyncMessage">
          {{ proofSyncMessage }}
        </span>
        <span v-if="checkoutStatus === 'rate_limited'">
          Tunggu {{ retryAfterSeconds }} detik sebelum mencoba lagi.
        </span>
        <button
          v-if="checkoutStatus === 'error' && cartItems.length === 0"
          type="button"
          @click="emit('checkout-finished')"
        >
          Tutup pesan
        </button>
        <template v-else-if="checkoutStatus === 'retryable' || (checkoutStatus === 'rate_limited' && retryAfterSeconds === 0)">
          <button
            type="button"
            :disabled="checkoutStatus === 'rate_limited' && retryAfterSeconds > 0"
            @click="openRetryCheckout"
          >
            Kirim ulang nota yang sama
          </button>
        </template>
      </div>

      <!--
        Antrean multi-nota tidak lagi ditampilkan di kolom ini (permintaan
        pemilik, 2026-10-09): banernya memakan tinggi layar kasir. Notifikasi,
        "Kirim semua", dan kirim ulang per nota sekarang ada di modal Riwayat
        Nota, dengan lencana jumlah di tombol "Riwayat Nota" di header.
      -->

      <div class="total-row">
        <div class="total">
          <span> Total </span>

          <strong>
            {{ formatPrice(totalPrice) }}
          </strong>
        </div>

        <button
          class="clear-cart-button"
          :disabled="cartItems.length === 0 || isEditingLocked"
          title="Kosongkan keranjang"
          aria-label="Kosongkan keranjang"
          @click="clearCart"
        >
          <img src="/trash-can-10417.svg" alt="" />
        </button>
      </div>

      <button
        class="pay-button"
        :disabled="cartItems.length === 0 || isEditingLocked"
        @click="openConfirmation"
      >
        BAYAR
      </button>
    </div>

    <!-- ==================================
         CHECKOUT MODAL
    =================================== -->

    <div
      v-if="showConfirmation"
      v-modal-focus="{ onEscape: closeConfirmation, step: checkoutStep }"
      class="modal-overlay"
      @click.self="checkoutStatus !== 'submitting' && closeConfirmation()"
    >
      <div
        class="confirmation-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="checkout-title"
        tabindex="-1"
      >
        <ol class="checkout-progress" aria-label="Langkah pembayaran">
          <li
            v-for="(label, index) in ['Pesanan', 'Pembayaran', 'PIN kasir']"
            :key="label"
            :aria-current="checkoutStep === index + 1 ? 'step' : undefined"
            :class="{ 'is-complete': checkoutStep > index + 1 }"
          >
            <span aria-hidden="true">{{ index + 1 }}</span>
            {{ label }}
          </li>
        </ol>
        <Transition
          :name="transitionDirection === 'forward' ? 'slide-forward' : 'slide-back'"
          mode="out-in"
        >
          <!-- ==================================
               LANGKAH 1: KONFIRMASI PESANAN
          =================================== -->

          <div v-if="checkoutStep === STEP_ORDER" key="step-1" class="checkout-step">
            <div class="modal-header">
              <h2 id="checkout-title">Konfirmasi Pesanan</h2>
            </div>

            <div class="modal-body">
              <div class="checkout-columns">
                <div class="checkout-pane">
                  <p class="pane-title">Daftar barang</p>

                  <p class="step-description">Periksa kembali pesanan sebelum melanjutkan.</p>

                  <div class="confirmation-list">
                    <div
                      v-for="item in cartItems"
                      :key="item.cartId"
                      class="confirmation-item"
                    >
                      <div class="confirmation-item-info">
                        <strong>
                          {{ item.item_name }}

                          <span v-if="item.variant && item.variant !== 'N/A'">
                            -
                            {{ item.variant }}
                          </span>
                        </strong>

                        <span>
                          {{ item.quantity }}
                          ×
                          {{ formatPrice(item.unit_price ?? item.price) }}
                        </span>
                      </div>

                      <strong class="confirmation-item-total">
                        {{ formatPrice(Number(item.unit_price ?? item.price) * Number(item.quantity)) }}
                      </strong>
                    </div>
                  </div>
                </div>

                <div class="checkout-pane summary-pane">
                  <p class="pane-title">Ringkasan</p>

                  <div class="summary-row">
                    <span>Jumlah baris</span>
                    <strong>{{ cartItems.length }}</strong>
                  </div>

                  <div class="summary-row">
                    <span>Total barang</span>
                    <strong>{{ formatPrice(totalPrice) }}</strong>
                  </div>

                  <div class="summary-row summary-total">
                    <span>Total</span>
                    <strong>{{ formatPrice(totalPrice) }}</strong>
                  </div>
                </div>
              </div>
            </div>

            <div class="confirmation-footer">
              <div class="confirmation-total">
                <span>Total</span>

                <strong>
                  {{ formatPrice(totalPrice) }}
                </strong>
              </div>

              <div class="confirmation-actions">
                <button type="button" class="cancel-button" @click="closeConfirmation">
                  BATAL
                </button>

                <button type="button" class="confirm-button" @click="confirmOrder">
                  KONFIRMASI
                </button>
              </div>
            </div>
          </div>

          <!-- ==================================
               LANGKAH 2: PENGAMBILAN + PEMBAYARAN
          =================================== -->

          <div v-else-if="checkoutStep === STEP_PAYMENT" key="step-2" class="checkout-step">
            <div class="modal-header step-header">
              <button type="button" class="back-button" aria-label="Kembali" title="Kembali" @click="goBack">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m12 5-7 7 7 7M5 12h14" /></svg>
              </button>

              <div>
                <h2 id="checkout-title">Pengambilan &amp; Pembayaran</h2>
              </div>
            </div>

            <div class="modal-body">
              <div class="checkout-columns">
                <!-- Pengambilan -->

                <div class="checkout-pane">
                  <p class="pane-title">Pengambilan pesanan</p>

                  <label
                    class="option-card"
                    :class="{ selected: deliveryMethod === 'pickup' }"
                  >
                    <input v-model="deliveryMethod" type="radio" value="pickup" />

                    <span> Ambil Sendiri </span>
                  </label>

                  <label
                    class="option-card"
                    :class="{ selected: deliveryMethod === 'delivery' }"
                  >
                    <input v-model="deliveryMethod" type="radio" value="delivery" />

                    <span> Antar </span>
                  </label>

                  <div v-if="deliveryMethod === 'delivery'" class="field-block">
                    <label for="shipping"> Ongkos Kirim </label>

                    <input
                      id="shipping"
                      :value="shippingCost ? `Rp ${formatRupiahInput(shippingCost)}` : ''"
                      type="text"
                      inputmode="numeric"
                      pattern="[0-9]*"
                      autocomplete="off"
                      placeholder="Rp 0"
                      @keydown="preventNonNumericInput"
                      @input="updateShippingCost"
                    />
                  </div>

                  <!--
                    Data pelanggan (keputusan pemilik 2026-10-14): nama wajib,
                    No. HP opsional. Selalu tampil untuk ambil maupun antar
                    karena nama tercetak di setiap struk.
                  -->
                  <div class="field-block">
                    <label for="customer-name"> Nama Pelanggan </label>

                    <input
                      id="customer-name"
                      v-model.trim="customerName"
                      type="text"
                      autocomplete="off"
                      placeholder="Wajib diisi"
                      maxlength="120"
                      :disabled="isEditingLocked"
                    />
                  </div>

                  <div class="field-block">
                    <label for="customer-phone"> No. HP (opsional) </label>

                    <input
                      id="customer-phone"
                      v-model.trim="customerPhone"
                      type="tel"
                      autocomplete="off"
                      placeholder="Opsional"
                      maxlength="20"
                      :disabled="isEditingLocked"
                    />
                  </div>
                </div>

                <!-- Metode bayar + pembayaran -->

                <div class="checkout-pane">
                  <p class="pane-title">Metode pembayaran</p>

                  <div class="payment-options">
                    <label
                      class="option-card"
                      :class="{ selected: paymentMethod === 'cash' }"
                    >
                      <input v-model="paymentMethod" type="radio" value="cash" />

                      <span> Cash </span>
                    </label>

                    <label
                      class="option-card"
                      :class="{ selected: paymentMethod === 'edc' }"
                    >
                      <input v-model="paymentMethod" type="radio" value="edc" />

                      <span> EDC </span>
                    </label>

                    <label
                      class="option-card"
                      :class="{ selected: paymentMethod === 'bank_transfer' }"
                    >
                      <input v-model="paymentMethod" type="radio" value="bank_transfer" />

                      <span> Bank Transfer </span>
                    </label>
                  </div>

                  <!-- Cash -->

                  <div v-if="paymentMethod === 'cash'" class="field-block">
                    <label for="cash-received"> Uang Diterima </label>

                    <input
                      id="cash-received"
                      :value="cashReceived ? `Rp ${formatRupiahInput(cashReceived)}` : ''"
                      type="text"
                      inputmode="numeric"
                      pattern="[0-9]*"
                      autocomplete="off"
                      placeholder="Rp 0"
                      @keydown="preventNonNumericInput"
                      @input="updateCashReceived"
                    />

                    <div v-if="suggestedCashAmounts.length > 0" class="cash-suggestions">
                      <p class="cash-suggestions-hint">Nominal uang diterima</p>

                      <div class="cash-suggestion-list">
                        <button
                          v-for="suggestion in suggestedCashAmounts"
                          :key="suggestion.amount"
                          type="button"
                          class="cash-suggestion"
                          :class="{ selected: Number(cashReceived) === suggestion.amount }"
                          :disabled="isEditingLocked"
                          :title="
                            suggestion.exact
                              ? 'Uang pas, tanpa kembalian'
                              : `Kembalian ${formatPrice(suggestion.change)}`
                          "
                          @click="fillCashReceived(suggestion.amount)"
                        >
                          {{ formatPrice(suggestion.amount) }}
                        </button>
                      </div>

                    </div>

                    <div v-if="Number(cashReceived) >= finalTotal" class="change-row">
                      <span> Kembalian </span>

                      <strong>
                        {{ formatPrice(changeAmount) }}
                      </strong>
                    </div>
                  </div>

                  <!-- EDC -->

                  <p v-else-if="paymentMethod === 'edc'" class="step-description">
                    Pastikan pembayaran melalui mesin EDC telah berhasil diselesaikan sebelum
                    mengonfirmasi transaksi.
                  </p>

                  <!-- Bukti transfer -->

                  <div v-else-if="paymentMethod === 'bank_transfer'" class="transfer-proof">
                    <p class="step-description">
                      Ambil foto bukti transfer dengan kamera atau pilih foto yang sudah tersimpan.
                    </p>

                    <input
                      id="transfer-proof-camera"
                      class="transfer-proof-input"
                      type="file"
                      accept="image/*"
                      capture="environment"
                      @change="updateTransferProof"
                    />

                    <input
                      id="transfer-proof-gallery"
                      class="transfer-proof-input"
                      type="file"
                      accept="image/*"
                      @change="updateTransferProof"
                    />

                    <div class="proof-actions">
                      <label for="transfer-proof-camera" class="proof-select-button">
                        {{ transferProofId ? 'AMBIL FOTO BARU' : 'AMBIL FOTO' }}
                      </label>

                      <label for="transfer-proof-gallery" class="proof-select-button">
                        PILIH DARI GALERI
                      </label>
                    </div>

                    <p v-if="transferProofBusy" class="step-description">
                      Memproses foto...
                    </p>

                    <p v-if="transferProofError" class="checkout-inline-message" role="alert">
                      {{ transferProofError }}
                    </p>

                    <div v-if="transferProofPreview" class="transfer-proof-preview">
                      <img :src="transferProofPreview" alt="Pratinjau bukti transfer" />
                    </div>
                  </div>

                  <div class="payment-total">
                    <span> Total Pembayaran </span>

                    <strong>
                      {{ formatPrice(finalTotal) }}
                    </strong>
                  </div>

                  <!--
                    Tombol konfirmasi tinggal di kolom pembayaran (permintaan
                    pemilik 2026-10-14): kolom pengambilan memakai penuh
                    ruangnya, dan aksi ada tepat di bawah total yang dibayar.
                  -->
                  <button
                    type="button"
                    class="confirm-button payment-confirm"
                    :disabled="!isPaymentStepComplete"
                    @click="confirmPayment"
                  >
                    KONFIRMASI PEMBAYARAN
                  </button>
                </div>
              </div>
            </div>
          </div>

          <!-- ==================================
               LANGKAH 3: PIN KASIR
          =================================== -->

          <div v-else-if="checkoutStep === STEP_PIN" key="step-3" class="checkout-step">
            <div class="modal-header step-header">
              <button type="button" class="back-button" aria-label="Kembali" title="Kembali" @click="goBack">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m12 5-7 7 7 7M5 12h14" /></svg>
              </button>

              <div>
                <h2 id="checkout-title">PIN Kasir</h2>
              </div>
            </div>

            <div class="modal-body">
              <div class="checkout-columns">
                <div class="checkout-pane summary-pane">
                  <p class="pane-title">Ringkasan transaksi</p>

                  <div class="summary-row">
                    <span>Jumlah barang</span>
                    <strong>{{ totalQuantity }}</strong>
                  </div>

                  <div class="summary-row">
                    <span>Pengambilan</span>
                    <strong>
                      {{ { pickup: 'Ambil Sendiri', delivery: 'Antar' }[deliveryMethod] || '-' }}
                    </strong>
                  </div>

                  <div class="summary-row">
                    <span>Metode bayar</span>
                    <strong>
                      {{
                        { cash: 'Cash', edc: 'EDC', bank_transfer: 'Bank Transfer' }[
                          paymentMethod
                        ] || '-'
                      }}
                    </strong>
                  </div>

                  <div class="summary-row summary-total">
                    <span>Total</span>
                    <strong>{{ formatPrice(finalTotal) }}</strong>
                  </div>
                </div>

                <div class="checkout-pane">
                  <p class="pane-title">Masukkan PIN</p>

                  <p class="step-description">
                    {{ isRetry ? 'Mengirim ulang nota tersimpan atas nama kasir yang sama.' : 'Masukkan PIN kasir 6 digit untuk menyelesaikan transaksi.' }}
                  </p>
                  <p v-if="isRetry && pendingReceipt" class="step-description">
                    Nota {{ props.checkoutResult?.transaction_id || '' }} · Kasir {{ pendingReceiptName || 'nama belum dikenal lokal' }}
                  </p>

                  <p v-if="checkoutMessage" class="checkout-inline-message" role="alert">
                    {{ checkoutMessage }}
                    <span v-if="['rate_limited', 'pin_cooldown'].includes(checkoutStatus)">
                      Tunggu {{ retryAfterSeconds }} detik.
                    </span>
                  </p>

                  <div class="cashier-pin-input">
                    <label for="cashier-pin">{{ isRetry ? 'PIN asli nota (tidak diganti)' : 'PIN Kasir' }}</label>

                    <input
                      id="cashier-pin"
                      :value="cashierPin"
                      :disabled="isEditingLocked"
                      type="password"
                      inputmode="numeric"
                      pattern="[0-9]*"
                      maxlength="6"
                      autocomplete="off"
                      placeholder="••••••"
                      aria-describedby="cashier-pin-hint"
                      @keydown="preventNonNumericInput"
                      @input="updateCashierPin"
                    />

                    <p id="cashier-pin-hint">{{ cashierPin.length }}/6 digit</p>

                    <!--
                      Nama kasir datang dari daftar kasir di perangkat. Kalau PIN
                      yang diketik belum dikenal di sini, server tetap memverifikasi
                      saat nota dikirim — jadi peringatan ini menjelaskan kenapa
                      nama di atas masih kosong, bukan menolak transaksi.
                    -->
                    <p v-if="cashierPinUnknown" class="pin-unknown" role="status">
                      PIN ini belum ada di daftar kasir perangkat ini, jadi nama kasir
                      pada nota belum terisi. Nota hanya dapat diselesaikan kalau server
                      terjangkau; saat perangkat offline, PIN yang belum dikenal tidak
                      boleh menyelesaikan penjualan.
                    </p>
                  </div>

                  <div v-if="needsFloorApproval && !isRetry" class="floor-approval" role="group" aria-labelledby="floor-approval-title">
                    <p id="floor-approval-title" class="floor-approval-title">Ada harga di bawah batas floor</p>
                    <label for="approver-pin">PIN penyetuju</label>
                    <input
                      id="approver-pin"
                      :value="approverPin"
                      :disabled="isEditingLocked || mintaAcc"
                      type="password"
                      inputmode="numeric"
                      pattern="[0-9]*"
                      maxlength="6"
                      autocomplete="off"
                      placeholder="••••••"
                      @keydown="preventNonNumericInput"
                      @input="updateApproverPin"
                    />
                    <label class="floor-approval-alt">
                      <input v-model="mintaAcc" type="checkbox" :disabled="isEditingLocked || approverPin.length > 0" />
                      Tidak ada penyetuju: kirim nota untuk ACC pemilik
                    </label>
                  </div>
                </div>
              </div>
            </div>

            <div class="modal-footer">
              <button
                v-if="!['conflict', 'review', 'restoring'].includes(checkoutStatus)"
                type="button"
                class="confirm-button"
                :disabled="isRetry || isEditingLocked || transferProofBusy || !hasValidCashierPin
                  || !floorApprovalReady
                  || (checkoutStatus === 'rate_limited' && retryAfterSeconds > 0)"
                @click="completeTransaction"
              >
                {{ checkoutStatus === 'submitting' ? 'MENGIRIM...' : isRetry ? 'KIRIM ULANG NOTA YANG SAMA' : 'SELESAIKAN TRANSAKSI' }}
              </button>
              <div v-else class="checkout-conflict-actions">
                <p class="checkout-inline-message" role="alert">
                  Nota ditahan. Jangan kirim ulang atau membuat transaksi pengganti; minta penanggung jawab memeriksa.
                </p>
                <button type="button" class="cancel-button" @click="closeConfirmation">
                  TUTUP
                </button>
              </div>
            </div>
          </div>
        </Transition>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.floor-approval {
  margin-top: 14px;
  text-align: left;
  padding: 12px;
  border: 1px solid var(--pos-danger, #d93a3a);
  border-radius: 10px;
  background: rgba(217, 58, 58, 0.06);
}

.floor-approval label[for='approver-pin'] {
  display: block;
  margin-bottom: 6px;
  color: var(--pos-text-muted);
  font-size: 14px;
  font-weight: 600;
}

#approver-pin {
  width: 100%;
  height: 56px;
  padding: 0 16px;
  box-sizing: border-box;
  border: 1px solid var(--pos-border);
  border-radius: 10px;
  background: var(--pos-field-bg);
  color: var(--pos-text);
  font-size: 26px;
  letter-spacing: 0.45em;
  text-align: center;
  outline: none;
}

#approver-pin:focus {
  border-color: var(--pos-accent);
  box-shadow: 0 0 0 3px var(--pos-accent-soft);
}

#approver-pin:disabled {
  opacity: 0.5;
}

.floor-approval-title {
  margin: 0 0 8px;
  font-weight: 600;
  color: var(--pos-danger, #d93a3a);
}

.floor-approval-alt input[type='checkbox'] {
  width: 20px;
  height: 20px;
  margin: 0;
}

.floor-approval-alt {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 10px;
  font-weight: 400;
}

/* ========================================
   CART CONTAINER

   Keranjang sengaja tidak lagi berbentuk kartu penuh: hanya dibatasi garis
   di sisi kirinya supaya tidak memakan layar kanan (permintaan pemilik).
======================================== */

.cart {
  position: sticky;

  top: 0;

  display: flex;

  flex-direction: column;

  width: 100%;

  height: 100%;

  padding-left: 16px;

  border-left: 1px solid var(--pos-border);

  box-sizing: border-box;
}

/* ========================================
   CART HEADER
======================================== */

.cart-header {
  flex-shrink: 0;

  padding: 0 0 14px;
}

.cart-header h2 {
  margin: 0 0 12px;

  color: var(--pos-text);

  font-size: 26px;

  font-weight: 700;
}

.floor-legend {
  margin: 10px 0 0;

  color: var(--pos-text-muted);

  font-size: 12px;

  line-height: 1.4;
}

.floor-legend.is-note {
  margin-top: 4px;
}

/* ========================================
   CART ITEMS
======================================== */

.cart-items {
  flex: 1;

  min-height: 0;

  margin-right: -6px;

  padding-right: 6px;

  overflow-y: auto;
}

/*
 * Baris keranjang dirapatkan supaya lebih banyak barang terlihat tanpa
 * menggulung (audit tampilan, 2026-10-09). Tinggi sebelumnya 165px per barang:
 * di layar kasir 601px hanya dua setengah baris yang kelihatan. Yang dirapatkan
 * hanya jarak dan tata letak label harga — ukuran tombol jari tetap 44px.
 */

.cart-item {
  padding: 8px 10px;

  margin-bottom: 6px;

  background: var(--pos-surface-alt);

  border: 1px solid var(--pos-border);

  border-radius: 10px;
}

.cart-item-top {
  display: flex;

  align-items: baseline;

  gap: 10px;
}

.cart-item-top h3 {
  flex: 1;

  min-width: 0;

  margin: 0;

  color: var(--pos-text);

  font-size: 15px;

  font-weight: 600;

  line-height: 1.35;
}

/* ========================================
   KODE HARGA FLOOR
======================================== */

.cart-item-price-row {
  display: flex;

  /*
   * Kalau kolom keranjang mengecil ke batas bawahnya (300px), kode floor turun
   * ke baris kedua dengan sengaja: label harga di kiri, kode di kanan.
   */
  flex-wrap: wrap;

  align-items: center;

  gap: 6px 10px;

  margin-top: 6px;
}

.floor-code {
  flex-shrink: 0;

  margin-left: auto;

  padding: 4px 10px;

  border: 1px dashed var(--pos-border);

  border-radius: 8px;

  background: var(--pos-surface);

  color: var(--pos-text-muted);

  font-size: 11px;

  letter-spacing: 0.02em;

  white-space: nowrap;
}

.floor-code strong {
  margin-left: 5px;

  color: var(--pos-text);

  font-family: 'SFMono-Regular', ui-monospace, 'Roboto Mono', Menlo, monospace;

  font-size: 14px;

  font-weight: 700;

  letter-spacing: 0.08em;
}

/* ========================================
   CART PRICE INPUT
======================================== */

/*
 * Label dan kolomnya sebaris: label di atas kolom menambah satu baris teks per
 * barang di layar yang tingginya paling berharga.
 */

.cart-price-input {
  display: flex;

  align-items: center;

  gap: 8px;

  color: var(--pos-text-muted);

  font-size: 12px;

  white-space: nowrap;
}

.cart-price-input input {
  width: 96px;
  height: 44px;
  padding: 0 10px;
  border: 1px solid var(--pos-border);
  border-radius: 8px;
  background: var(--pos-field-bg);
  color: var(--pos-text);
  font-size: 15px;
}

.cart-price-input input:focus {
  border-color: var(--pos-accent);
  outline: none;
}

.cart-price-input input.price-input-invalid {
  border-color: var(--pos-danger);
  background: var(--pos-outstock-bg);
  color: var(--pos-outstock-text);
}

/* ========================================
   QUANTITY CONTROLS
======================================== */

.cart-item-controls {
  display: flex;

  align-items: center;

  gap: 8px;

  margin-top: 8px;
}

/*
 * Tombol jari: minimal 44px supaya nyaman ditekan di tablet.
 */

.cart-item-controls button {
  display: flex;

  justify-content: center;

  align-items: center;

  width: 44px;

  height: 44px;

  padding: 0;

  border: 1px solid var(--pos-border);

  border-radius: 8px;

  background: var(--pos-surface);

  color: var(--pos-text);

  font-size: 22px;

  line-height: 1;

  cursor: pointer;

  transition:
    background-color 0.15s ease,
    border-color 0.15s ease,
    opacity 0.15s ease;
}

.cart-item-controls button:hover:not(:disabled) {
  background: var(--pos-hover);

  border-color: var(--pos-accent);
}

.cart-item-controls button:disabled {
  opacity: 0.35;

  cursor: not-allowed;
}

/* ========================================
   QUANTITY INPUT
======================================== */

.quantity-input {
  width: 56px;

  height: 44px;

  padding: 0;

  box-sizing: border-box;

  border: 1px solid var(--pos-border);

  border-radius: 8px;

  background: var(--pos-field-bg);

  color: var(--pos-text);

  font-size: 18px;

  font-weight: 700;

  text-align: center;

  outline: none;

  cursor: text;
}

.quantity-input::-webkit-inner-spin-button,
.quantity-input::-webkit-outer-spin-button {
  margin: 0;

  -webkit-appearance: none;
}

.quantity-input[type='number'] {
  appearance: textfield;
}

.quantity-input:hover {
  border-color: var(--pos-border);
}

.quantity-input:focus {
  border-color: var(--pos-accent);
}

/* ========================================
   REMOVE
======================================== */

.cart-item-controls .remove-button {
  margin-left: auto;

  color: var(--pos-danger);
}

.cart-item-controls .remove-button:hover {
  background: var(--pos-outstock-bg);

  border-color: var(--pos-danger);
}

/* ========================================
   ITEM TOTAL
======================================== */

.cart-item-total {
  flex-shrink: 0;

  color: var(--pos-text);

  font-size: 16px;

  font-weight: 700;
}

/* ========================================
   EMPTY CART
======================================== */

.empty-cart {
  margin-top: 40px;

  color: var(--pos-text-muted);

  font-size: 15px;

  text-align: center;
}

/* ========================================
   CHECKOUT FEEDBACK
======================================== */

.checkout-feedback,
.checkout-inline-message {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  margin: 0 0 10px;
  border: 1px solid var(--pos-border);
  border-radius: 8px;
  color: var(--pos-text);
  font-size: 14px;
}

.checkout-feedback.is-error,
.checkout-feedback.is-conflict,
.checkout-feedback.is-review,
.checkout-feedback.is-retryable,
.checkout-feedback.is-rate_limited,
.checkout-inline-message {
  border-color: var(--pos-danger);
  background: var(--pos-outstock-bg);
  color: var(--pos-outstock-text);
}

/*
 * Bukti transfer yang belum terkirim bukan penolakan: blok ini satu-satunya
 * isinya, jadi warnanya memakai token peringatan, bukan bahaya. Dipisah dari
 * `is-${checkoutStatus}` supaya nota sukses tidak lagi dicat hijau di keranjang
 * sementara kesalahan nyata tetap merah.
 */
.checkout-feedback.is-proof-only {
  border-color: var(--pos-warning-border);
  background: var(--pos-warning-bg);
  color: var(--pos-warning-text);
}

.checkout-feedback button {
  align-self: flex-start;

  min-height: 44px;

  padding: 0 14px;

  border: 1px solid var(--pos-border);

  border-radius: 8px;

  background: var(--pos-surface);

  color: var(--pos-text);

  font-size: 14px;

  font-weight: 600;

  cursor: pointer;
}

.checkout-feedback .discard-button {
  border-color: var(--pos-danger);
  color: var(--pos-danger);
}

/*
 * Gaya baner antrean dihapus bersama banernya (2026-10-09): notifikasi antrean
 * sekarang hidup di `TransactionHistory.vue` dan memakai token peringatan POS.
 */

/* ========================================
   CART FOOTER
======================================== */

.checkout-conflict-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.checkout-conflict-actions .cancel-button {
  align-self: flex-end;
}

.cart-footer {
  flex-shrink: 0;

  padding: 14px 0 0;

  border-top: 1px solid var(--pos-border);
}

.total-row {
  display: flex;

  align-items: center;

  width: 100%;

  margin-bottom: 12px;
}

.total {
  display: flex;

  align-items: center;

  gap: 10px;

  color: var(--pos-text-muted);

  font-size: 16px;
}

.total strong {
  color: var(--pos-text);

  font-size: 26px;

  font-weight: 700;
}

/* ========================================
   CLEAR CART
======================================== */

.clear-cart-button {
  display: flex;

  justify-content: center;

  align-items: center;

  width: 48px;

  height: 48px;

  margin-left: auto;

  padding: 12px;

  border: 1px solid var(--pos-border);

  border-radius: 10px;

  background: var(--pos-surface);

  cursor: pointer;

  transition:
    background-color 0.15s ease,
    border-color 0.15s ease,
    opacity 0.15s ease;
}

.clear-cart-button img {
  width: 20px;

  height: 20px;

  object-fit: contain;
}

.clear-cart-button:hover:not(:disabled) {
  background: var(--pos-hover);

  border-color: var(--pos-danger);
}

.clear-cart-button:disabled {
  opacity: 0.35;

  cursor: not-allowed;
}

/* ========================================
   PAY BUTTON

   Satu-satunya tombol aksen teal bersama tombol konfirmasi di modal.
======================================== */

.pay-button {
  width: 100%;

  height: 64px;

  border: none;

  border-radius: 10px;

  background: var(--pos-accent);

  color: var(--pos-accent-contrast);

  font-size: 20px;

  font-weight: 700;

  letter-spacing: 0.04em;

  cursor: pointer;

  transition:
    background-color 0.15s ease,
    opacity 0.15s ease;
}

.pay-button:hover:not(:disabled) {
  background: var(--pos-accent-hover);
}

.pay-button:disabled {
  opacity: 0.4;

  cursor: not-allowed;
}

/* ========================================
   MODAL OVERLAY
======================================== */

.modal-overlay {
  position: fixed;

  inset: 0;

  z-index: 1000;

  display: flex;

  justify-content: center;

  align-items: center;

  padding: 16px;

  background: var(--pos-overlay);
}

/* ========================================
   MODAL

   Hampir memenuhi layar tablet: keputusan pemilik 2026-09-29 supaya
   pengambilan + metode bayar + pembayaran bisa berdampingan.
======================================== */

.confirmation-modal {
  display: flex;

  flex-direction: column;

  width: min(1240px, 96vw);

  height: min(780px, 92vh);

  background: var(--pos-surface);

  border: 1px solid var(--pos-border);

  border-radius: 12px;

  box-shadow: var(--pos-shadow);

  overflow: hidden;

  box-sizing: border-box;
}

/* ========================================
   CHECKOUT STEP
======================================== */

.checkout-progress {
  display: flex;
  flex-shrink: 0;
  gap: 20px;
  margin: 0;
  padding: 12px 22px 0;
  list-style: none;
  color: var(--pos-text-muted);
  font-size: 13px;
}

.checkout-progress li {
  display: flex;
  align-items: center;
  gap: 7px;
}

.checkout-progress li > span {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border: 1px solid var(--pos-border);
  border-radius: 50%;
  font-variant-numeric: tabular-nums;
}

.checkout-progress [aria-current='step'] {
  color: var(--pos-text);
  font-weight: 700;
}

.checkout-progress [aria-current='step'] > span {
  background: var(--pos-text);
  border-color: var(--pos-text);
  color: var(--pos-surface);
}

.checkout-progress .is-complete > span {
  background: var(--pos-surface-alt);
}

.checkout-step {
  display: flex;

  flex-direction: column;

  width: 100%;

  flex: 1;
  min-height: 0;
}

/* ========================================
   MODAL HEADER
======================================== */

.modal-header {
  flex-shrink: 0;

  padding: 18px 22px;

  border-bottom: 1px solid var(--pos-border);
}

.modal-header h2 {
  margin: 0;

  color: var(--pos-text);

  font-size: 24px;

  font-weight: 700;
}

/* ========================================
   STEP HEADER
======================================== */

.step-header {
  display: flex;

  align-items: center;

  gap: 12px;
}

/* ========================================
   BACK BUTTON
======================================== */

.back-button {
  display: flex;

  justify-content: center;

  align-items: center;

  width: 44px;

  height: 44px;

  flex-shrink: 0;

  padding: 0;

  border: 1px solid var(--pos-border);

  border-radius: 10px;

  background: var(--pos-surface);

  color: var(--pos-text);

  font-size: 22px;

  line-height: 1;

  cursor: pointer;
}

.back-button:hover {
  background: var(--pos-hover);

  border-color: var(--pos-accent);
}

/* ========================================
   MODAL BODY
======================================== */

.modal-body {
  flex: 1;

  min-height: 0;

  padding: 18px 22px;

  overflow-y: auto;
}

/*
 * Dua kolom di layar lebar, satu kolom di layar sempit. Modal yang lebar
 * membuat pengambilan dan pembayaran bisa dibaca bersamaan.
 */

.checkout-columns {
  display: grid;

  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);

  gap: 22px;

  height: 100%;

  min-height: 0;
}

.checkout-pane {
  display: flex;

  flex-direction: column;

  min-width: 0;

  min-height: 0;
}

.pane-title {
  margin: 0 0 10px;

  color: var(--pos-text-muted);

  font-size: 13px;

  font-weight: 700;

  letter-spacing: 0.06em;

  text-transform: uppercase;
}

.step-description {
  margin: 0 0 14px;

  color: var(--pos-text-muted);

  font-size: 15px;

  line-height: 1.5;
}

/* ========================================
   DAFTAR BARANG (LANGKAH 1)
======================================== */

.confirmation-list {
  min-height: 0;

  overflow-y: auto;
}

.confirmation-item {
  display: flex;

  justify-content: space-between;

  gap: 16px;

  padding: 12px 14px;

  margin-bottom: 8px;

  background: var(--pos-surface-alt);

  border: 1px solid var(--pos-border);

  border-radius: 10px;
}

.confirmation-item-info {
  display: flex;

  flex-direction: column;

  gap: 4px;

  min-width: 0;
}

.confirmation-item-info strong {
  color: var(--pos-text);

  font-size: 16px;

  line-height: 1.4;
}

.confirmation-item-info span {
  color: var(--pos-text-muted);

  font-size: 14px;
}

.confirmation-item-total {
  flex-shrink: 0;

  align-self: center;

  color: var(--pos-text);

  font-size: 16px;
}

/* ========================================
   RINGKASAN
======================================== */

.summary-pane {
  align-self: start;

  padding: 16px;

  background: var(--pos-surface-alt);

  border: 1px solid var(--pos-border);

  border-radius: 10px;
}

.summary-row {
  display: flex;

  justify-content: space-between;

  align-items: center;

  gap: 12px;

  padding: 8px 0;

  color: var(--pos-text-muted);

  font-size: 15px;
}

.summary-row strong {
  color: var(--pos-text);

  font-size: 16px;
}

.summary-total {
  margin-top: 6px;

  padding-top: 12px;

  border-top: 1px solid var(--pos-border);

  color: var(--pos-text);

  font-weight: 600;
}

.summary-total strong {
  font-size: 22px;

  font-weight: 700;
}

/* ========================================
   CONFIRMATION FOOTER
======================================== */

.confirmation-footer {
  flex-shrink: 0;

  padding: 16px 22px;

  border-top: 1px solid var(--pos-border);

  background: var(--pos-surface);
}

.confirmation-total {
  display: flex;

  justify-content: space-between;

  align-items: center;

  margin-bottom: 14px;

  color: var(--pos-text-muted);

  font-size: 15px;
}

.confirmation-total strong {
  color: var(--pos-text);

  font-size: 24px;

  font-weight: 700;
}

/* ========================================
   CONFIRMATION ACTIONS
======================================== */

.confirmation-actions {
  display: flex;

  gap: 12px;

  width: 100%;

  max-width: 460px;

  margin-left: auto;
}

/* ========================================
   OPTION CARD (PENGAMBILAN / METODE BAYAR)
======================================== */

.option-card {
  display: flex;

  align-items: center;

  gap: 12px;

  width: 100%;

  box-sizing: border-box;

  min-height: 56px;

  padding: 14px 16px;

  margin-bottom: 10px;

  border: 1px solid var(--pos-border);

  border-radius: 10px;

  background: var(--pos-surface);

  color: var(--pos-text);

  font-size: 16px;

  cursor: pointer;

  transition:
    background-color 0.15s ease,
    border-color 0.15s ease;
}

.option-card:hover {
  background: var(--pos-hover);

  border-color: var(--pos-accent);
}

/* Pilihan aktif memakai aksen teal yang sama dengan tombol Bayar. */
.option-card.selected {
  border-color: var(--pos-accent);

  background: var(--pos-accent-soft);

  font-weight: 600;
}

.option-card input {
  width: 20px;

  height: 20px;

  margin: 0;

  accent-color: var(--pos-accent);
}

/* ========================================
   METODE PEMBAYARAN SEBARIS

   Permintaan pemilik 2026-10-08: tiga pilihan pembayaran berdampingan, bukan
   bertumpuk. Kartu di dalam baris membagi lebar pane sama rata, jadi tingginya
   ikut sama walau labelnya dua baris.

   Ambang `min-width` diukur, bukan ditebak: kata terpanjang di sini
   "Transfer" ≈ 65px pada huruf 16px, ditambah radio 20px, jarak 12px, dan
   padding terkecil 2x14px (aturan layar pendek) = 125px. Ambangnya dipasang
   136px supaya perbedaan huruf antar perangkat tidak memecah tata letak.
   Di bawah jumlah itu barisnya pecah sendiri (di panel tersempit ketiganya
   kembali bertumpuk seperti sebelumnya), jadi tidak ada label yang perlu
   dipotong dan tidak ada kontrol yang menggantung separuh baris.
======================================== */

.payment-options {
  display: flex;

  flex-wrap: wrap;

  gap: 10px;
}

.payment-options .option-card {
  flex: 1 1 0;

  width: auto;

  min-width: 136px;

  margin-bottom: 0;

  justify-content: center;

  text-align: center;
}

/*
 * Di dalam baris ini huruf pilihan aktif sengaja tidak ditebalkan. Diukur pada
 * 601x1007: "Bank Transfer" 94px saat biasa dan ~103px saat tebal, sedangkan
 * ruang teksnya 98px — menebalkan saja cukup untuk memaksa baris kedua, jadi
 * tinggi kartu berubah (56px -> 72px) tepat saat kasir memilih. Penanda
 * aktifnya tetap tiga: border aksen, latar aksen lembut, dan titik radio yang
 * terisi (`.option-card.selected` di atas tetap berlaku untuk pilihan lain).
 */
.payment-options .option-card.selected {
  font-weight: 400;
}

/* ========================================
   KOLOM ISIAN
======================================== */

.field-block {
  margin-top: 14px;
}

.field-block label {
  display: block;

  margin-bottom: 6px;

  color: var(--pos-text-muted);

  font-size: 14px;

  font-weight: 600;
}

.field-block input {
  width: 100%;

  height: 52px;

  padding: 0 14px;

  box-sizing: border-box;

  border: 1px solid var(--pos-border);

  border-radius: 10px;

  background: var(--pos-field-bg);

  color: var(--pos-text);

  font-size: 18px;

  outline: none;
}

.field-block input:focus {
  border-color: var(--pos-accent);
  box-shadow: 0 0 0 3px var(--pos-accent-soft);
}

/* ========================================
   SARAN NOMINAL UANG TUNAI

   Tombol jari minimal 44px seperti kontrol lain; warnanya tetap dari token,
   tanpa aksen teal karena aksen itu hanya untuk Bayar, Konfirmasi, dan fokus
   kotak pencarian.
======================================== */

.cash-suggestions {
  margin-top: 12px;
}

.cash-suggestions-hint {
  margin: 0 0 6px;

  color: var(--pos-text-muted);

  font-size: 12px;
}

/*
 * Jarak antar chip nominal (permintaan pemilik, 2026-10-07): 8px membuat
 * empat chip bertumpuk terlihat seperti satu balok, terutama chip yang
 * terpilih dengan border tebal. 12px memberi jarak yang jelas tanpa membuat
 * barisnya patah lebih awal di keranjang sempit.
 */
.cash-suggestion-list {
  display: flex;

  flex-wrap: wrap;

  justify-content: center;

  gap: 12px;
}

.cash-suggestion {
  min-height: 44px;

  padding: 0 14px;

  border: 1px solid var(--pos-border);

  border-radius: 8px;

  background: var(--pos-surface-alt);

  color: var(--pos-text);

  font-size: 15px;

  font-weight: 600;

  cursor: pointer;

  transition:
    background-color 0.15s ease,
    border-color 0.15s ease,
    opacity 0.15s ease;
}

.cash-suggestion:hover:not(:disabled) {
  background: var(--pos-hover);
}

.cash-suggestion.selected {
  border-color: var(--pos-text);
  background: var(--pos-field-bg);
}

.cash-suggestion:disabled {
  opacity: 0.4;

  cursor: not-allowed;
}

/* ========================================
   CHANGE
======================================== */

.change-row {
  display: flex;

  justify-content: space-between;

  align-items: center;

  margin-top: 14px;

  padding-top: 12px;

  border-top: 1px solid var(--pos-border);

  color: var(--pos-text-muted);

  font-size: 15px;
}

.change-row strong {
  color: var(--pos-text);

  font-size: 22px;

  font-weight: 700;
}

/* ========================================
   PAYMENT TOTAL
======================================== */

.payment-total {
  display: flex;

  justify-content: space-between;

  align-items: center;

  margin-top: 18px;

  padding: 14px 16px;

  border: 1px solid var(--pos-border);

  border-radius: 10px;

  background: var(--pos-surface-alt);

  color: var(--pos-text-muted);

  font-size: 15px;
}

.payment-total strong {
  color: var(--pos-text);

  font-size: 24px;

  font-weight: 700;
}

/* ========================================
   CASHIER PIN
======================================== */

.cashier-pin-input label {
  display: block;

  margin-bottom: 8px;

  color: var(--pos-text-muted);

  font-size: 14px;

  font-weight: 600;
}

.cashier-pin-input input {
  width: 100%;

  height: 72px;

  padding: 0 16px;

  box-sizing: border-box;

  border: 1px solid var(--pos-border);

  border-radius: 10px;

  background: var(--pos-field-bg);

  color: var(--pos-text);

  font-size: 32px;

  letter-spacing: 0.45em;

  text-align: center;

  outline: none;
}

.cashier-pin-input input:focus {
  border-color: var(--pos-accent);
  box-shadow: 0 0 0 3px var(--pos-accent-soft);
}

.cashier-pin-input p {
  margin: 8px 0 0;

  color: var(--pos-text-muted);

  font-size: 14px;

  text-align: right;
}

/*
 * Peringatan "PIN belum dikenal" rata kiri: isinya kalimat, bukan angka.
 */
.cashier-pin-input .pin-unknown {
  padding: 8px 10px;

  border: 1px solid var(--pos-border);
  border-radius: 8px;

  background: var(--pos-surface-alt);

  color: var(--pos-text-muted);

  font-size: 13px;
  line-height: 1.45;

  text-align: left;
}

/* ========================================
   TRANSFER PROOF
======================================== */

.transfer-proof-input {
  position: absolute;

  width: 1px;

  height: 1px;

  overflow: hidden;

  clip: rect(0, 0, 0, 0);

  white-space: nowrap;

  clip-path: inset(50%);
}

.proof-select-button {
  display: flex;

  justify-content: center;

  align-items: center;

  min-height: 54px;

  padding: 0 16px;

  box-sizing: border-box;

  border: 1px solid var(--pos-border);

  border-radius: 10px;

  background: var(--pos-surface);

  color: var(--pos-text);

  font-size: 15px;

  font-weight: 700;

  cursor: pointer;
}

.proof-actions {
  display: grid;

  grid-template-columns: repeat(2, minmax(0, 1fr));

  gap: 10px;
}

.proof-select-button:hover {
  background: var(--pos-hover);

  border-color: var(--pos-accent);
}

.transfer-proof-preview {
  display: flex;

  justify-content: center;

  margin-top: 14px;

  padding: 8px;

  border: 1px solid var(--pos-border);

  border-radius: 10px;

  background: var(--pos-surface-alt);
}

.transfer-proof-preview img {
  display: block;

  width: 100%;

  max-height: 260px;

  object-fit: contain;

  border-radius: 6px;
}

/* ========================================
   MODAL FOOTER
======================================== */

.modal-footer {
  display: flex;

  align-items: center;

  gap: 16px;

  flex-shrink: 0;

  padding: 14px 22px;

  border-top: 1px solid var(--pos-border);
}

/* ========================================
   MODAL BUTTONS
======================================== */

.cancel-button,
.confirm-button {
  flex: 1;

  min-height: 56px;

  padding: 0 20px;

  border-radius: 10px;

  font-size: 16px;

  font-weight: 700;

  cursor: pointer;
}

.cancel-button {
  background: var(--pos-surface);

  color: var(--pos-text);

  border: 1px solid var(--pos-border);
}

.cancel-button:hover {
  background: var(--pos-hover);

  border-color: var(--pos-accent);
}

/* Tombol aksen teal: konfirmasi pembayaran dan penyelesaian transaksi. */
.confirm-button {
  margin-left: auto;

  min-width: 260px;

  background: var(--pos-accent);

  color: var(--pos-accent-contrast);

  border: 1px solid var(--pos-accent);
}

.confirm-button:hover:not(:disabled) {
  background: var(--pos-accent-hover);
}

/*
 * Tombol konfirmasi di kolom pembayaran: selebar kolom, menempel ke dasar
 * pane (`margin-top: auto`) supaya sejajar dengan dasar kolom pengambilan
 * yang lebih tinggi; `margin-bottom` total di bawah menjaga jarak minimum
 * 14px saat pane memendek (satu kolom di layar sempit).
 */
.payment-confirm {
  /* `flex: none` membatalkan `flex: 1` era footer (tombol memenuhi baris)
     yang di kolom vertikal malah membuat tombol setinggi sisa pane. */
  flex: none;
  width: 100%;
  min-width: 0;
  margin: auto 0 0;
}

/* Total satu-satunya pemakai kelas ini adalah langkah pembayaran, yang kini
   selalu diikuti tombol `.payment-confirm`: jarak minimum 14px bahkan saat
   pane memendek dan `margin-top: auto` tombol menjadi nol. */
.payment-total {
  margin-bottom: 14px;
}

/* Umpan balik tekan (bukan gerak otomatis): tombol terasa ditekan kasir. */
.confirm-button:active:not(:disabled) {
  transform: translateY(1px);
}

.confirm-button:disabled {
  opacity: 0.4;

  cursor: not-allowed;
}

/* ========================================
   CHECKOUT STEP TRANSITION
======================================== */

.slide-forward-enter-active,
.slide-forward-leave-active {
  transition:
    transform 0.25s ease,
    opacity 0.25s ease;
}

.slide-forward-enter-from {
  opacity: 0;

  transform: translateX(35px);
}

.slide-forward-leave-to {
  opacity: 0;

  transform: translateX(-35px);
}

.slide-back-enter-active,
.slide-back-leave-active {
  transition:
    transform 0.25s ease,
    opacity 0.25s ease;
}

.slide-back-enter-from {
  opacity: 0;

  transform: translateX(-35px);
}

.slide-back-leave-to {
  opacity: 0;

  transform: translateX(35px);
}

/* ========================================
   TABLET TEGAK: KERANJANG DI BAWAH DAFTAR

   Ambang 900px mengikuti App.vue: Galaxy Tab A7 Lite (1007x601) tetap dua
   kolom, jadi tumpukan hanya untuk layar yang benar-benar sempit.
======================================== */

@media (max-width: 900px) {
  .cart {
    position: static;

    padding-left: 0;
    padding-top: 16px;

    border-left: none;
    border-top: 1px solid var(--pos-border);
  }
}

/* ========================================
   LAYAR PENDEK: MODAL TETAP MUAT

   Tablet mendatar hanya sekitar 600px tinggi, sementara modal checkout
   memuat pengambilan + metode bayar + pembayaran sekaligus. Yang dirapatkan
   hanya jarak dan tinggi kolom isian; ukuran huruf dan tombol tetap besar.
======================================== */

@media (max-height: 720px) {
  .confirmation-modal {
    height: min(780px, 94vh);
  }

  .modal-header {
    padding: 12px 18px;
  }

  .modal-header h2 {
    font-size: 21px;
  }

  .modal-body {
    padding: 12px 18px;
  }

  .pane-title {
    margin-bottom: 8px;
  }

  .step-description {
    margin-bottom: 10px;
    font-size: 14px;
  }

  .option-card {
    min-height: 48px;

    padding: 10px 14px;
    margin-bottom: 8px;
  }

  .field-block input {
    height: 46px;
  }

  .payment-total {
    margin-top: 12px;

    padding: 10px 14px;
  }

  .payment-total strong {
    font-size: 20px;
  }

  .summary-pane {
    padding: 12px;
  }

  .summary-row {
    padding: 5px 0;
  }

  .cashier-pin-input input {
    height: 60px;

    font-size: 26px;
  }

  .modal-footer {
    padding: 10px 18px;
  }

  .cancel-button,
  .confirm-button {
    min-height: 50px;
  }
}

/* ========================================
   LAYAR SEMPIT: SATU KOLOM
======================================== */

@media (max-width: 900px) {
  .checkout-progress {
    justify-content: space-between;
    gap: 8px;
    padding: 12px 16px 0;
    font-size: 12px;
  }

  .checkout-progress li {
    gap: 5px;
  }

  .checkout-columns {
    grid-template-columns: minmax(0, 1fr);
  }

  .confirmation-actions {
    max-width: none;
  }

  .confirm-button {
    min-width: 0;
    width: 100%;
  }

  .modal-footer {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .slide-forward-enter-active,
  .slide-forward-leave-active,
  .slide-back-enter-active,
  .slide-back-leave-active {
    transition: none;
  }
}
</style>
