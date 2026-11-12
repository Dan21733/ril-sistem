<script setup>
import { computed, ref, watch } from 'vue'

import { fetchPiutangDetail, fetchPiutangList } from '../services/api/piutangApi'
import { createUuid } from '../services/id'
import { enqueuePiutangPrint, flushPrintJobs } from '../services/printer/printQueue'
import deviceRepository from '../services/storage/deviceRepository'
import { enqueuePiutangAction, flushPiutangActions } from '../services/storage/piutangQueue'
import { STATUS_BARANG_LABEL } from '../utils/piutang'
import { digitsOnly, formatRupiahInput, preventNonNumericInput } from '../utils/rupiahInput'

import LoadingSpinner from './LoadingSpinner.vue'

/*
 * Piutang pelanggan di menu Transaksi (DP fase 2, 2026-11-02).
 *
 * Nota diproses (12 Nov 2026): tab "Belum selesai" (pesanan, titipan, COD;
 * semua kasir boleh melanjutkan, tiap baris menyebut kasir pembuatnya) dan
 * "Invoice" (pelanggan tempo). COD: "Uang COD diterima" = pelunasan sebesar
 * sisa; "Barang dibawa pulang" membuka retur nota itu (kasir mana pun).
 *
 * Tab "Belum lunas" dan "Ambil nanti / Pesanan" terlihat untuk semua kasir
 * setelah PIN. Kasir boleh mencatat pelunasan dan menandai barang diambil;
 * keduanya masuk antrean perangkat dulu (jalan saat offline) lalu dikirim,
 * dan master yang menerapkannya ke piutang. ACC tetap di master/HP pemilik.
 */

const props = defineProps({
  tab: { type: String, default: 'proses' },
  pin: { type: String, default: '' },
  cashierName: { type: String, default: '' },
})

const emit = defineEmits(['pin-invalid', 'counts', 'retur-cod'])

const JENIS_LABEL = { pesanan: 'Pesanan', titipan: 'Titipan', cod: 'COD', invoice: 'Invoice' }
const SUB_FILTER = [
  { value: '', label: 'Semua' },
  { value: 'pesanan', label: 'Pesanan' },
  { value: 'titipan', label: 'Titipan' },
  { value: 'cod', label: 'COD' },
]
const subFilter = ref('')

const rows = ref([])
const loading = ref(false)
const errorMessage = ref('')
const query = ref('')
const selected = ref(null)
const detail = ref(null)
const detailLoading = ref(false)
const detailError = ref('')

const payAmount = ref('')
const payMethod = ref('cash')
const payNote = ref('')
const busy = ref(false)
const actionMessage = ref('')
const actionError = ref('')
const confirmHandover = ref(false)

const CARA = [
  { value: 'cash', label: 'Cash' },
  { value: 'edc', label: 'EDC' },
  { value: 'bank_transfer', label: 'Transfer' },
]

function formatPrice(value) {
  return `Rp ${new Intl.NumberFormat('id-ID').format(Math.round(Number(value) || 0))}`
}

function formatDate(value) {
  if (!value) {
    return '-'
  }
  const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (!match) {
    return String(value)
  }
  const bulan = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des']
  return `${Number(match[3])} ${bulan[Number(match[2]) - 1]} ${match[1]}`
}

function isLate(row) {
  if (!row?.jatuh_tempo || row.sisa <= 0) {
    return false
  }
  const today = new Date()
  const pad2 = (n) => String(n).padStart(2, '0')
  const iso = `${today.getFullYear()}-${pad2(today.getMonth() + 1)}-${pad2(today.getDate())}`
  return row.jatuh_tempo < iso
}

async function token() {
  const device = await deviceRepository.getDevice()
  return device?.device_token || ''
}

let loadSequence = 0

async function load() {
  const sequence = ++loadSequence
  loading.value = true
  errorMessage.value = ''
  try {
    const data = await fetchPiutangList(await token(), props.pin, props.tab, query.value.trim())
    if (sequence !== loadSequence) {
      return
    }
    rows.value = data.hasil || []
    emit('counts', data.hitungan || {})
    if (selected.value && !rows.value.some((r) => r.kunci === selected.value.kunci)) {
      selected.value = null
      detail.value = null
    }
  } catch (error) {
    if (sequence !== loadSequence) {
      return
    }
    if (error.status === 400) {
      emit('pin-invalid')
      return
    }
    errorMessage.value = error.status
      ? error.message
      : 'Server tidak terjangkau. Daftar piutang butuh koneksi; pelunasan yang dicatat tetap masuk antrean.'
  } finally {
    if (sequence === loadSequence) {
      loading.value = false
    }
  }
}

async function open(row, { keepMessage = false } = {}) {
  selected.value = row
  detail.value = null
  detailError.value = ''
  if (!keepMessage) {
    actionMessage.value = ''
    actionError.value = ''
  }
  confirmHandover.value = false
  payAmount.value = row.sisa > 0 ? String(row.sisa) : ''
  payMethod.value = 'cash'
  payNote.value = ''
  detailLoading.value = true
  try {
    detail.value = await fetchPiutangDetail(await token(), props.pin, row.kunci)
  } catch (error) {
    detail.value = { ...row, barang: [], pembayaran: [] }
    detailError.value = error.status === 404 ? 'Nota tidak ditemukan.' : 'Rincian belum bisa dimuat (offline).'
  } finally {
    detailLoading.value = false
  }
}

const current = computed(() => detail.value || selected.value)

const shownRows = computed(() => (props.tab === 'proses' && subFilter.value
  ? rows.value.filter((row) => row.jenis === subFilter.value)
  : rows.value))

function subCount(jenis) {
  return jenis ? rows.value.filter((row) => row.jenis === jenis).length : rows.value.length
}

const isCod = computed(() => current.value?.jenis === 'cod')

/* Pesan ACC per jenis: invoice, titipan, atau pelanggaran SOP (alasan dari master). */
function accText(r) {
  if (r.jenis_acc === 'sop') {
    return `Melanggar SOP${r.acc_catatan ? `: ${r.acc_catatan}` : ''} Menunggu ACC pemilik.`
  }
  return `${r.jenis_acc === 'invoice' ? 'Invoice' : 'Titipan'} menunggu ACC pemilik.`
}

const pending = computed(() => (current.value?.menunggu || []).length > 0)

const canPay = computed(() => current.value && current.value.sisa > 0 && current.value.acc_status !== 'ditolak')

/* COD: barang dibawa pulang lewat retur nota kasir (butuh nomor nota kasir). */
const canReturCod = computed(() => isCod.value && current.value.sisa > 0 && Boolean(current.value.kasir_sync_id)
  && current.value.acc_status !== 'ditolak')

const canHandover = computed(() => {
  const r = current.value
  if (!r || r.barang_selesai || !['ambil', 'pesan'].includes(r.status_barang)) {
    return false
  }
  return r.acc_status !== 'menunggu' && r.acc_status !== 'ditolak'
})

const handoverBlocked = computed(() => {
  const r = current.value
  if (!canHandover.value) {
    return ''
  }
  if (r.sisa > 0 && !(r.jenis_acc === 'invoice' && r.acc_status === 'disetujui')) {
    return `Masih kurang ${formatPrice(r.sisa)}. Catat pelunasannya dulu.`
  }
  return ''
})

const payProblem = computed(() => {
  const jumlah = Number(payAmount.value || 0)
  if (!jumlah) {
    return 'Isi jumlah yang dibayar.'
  }
  if (current.value && jumlah > current.value.sisa) {
    return `Lebih dari sisa tagihan (${formatPrice(current.value.sisa)}).`
  }
  /* COD tidak boleh jadi utang: uang yang dibawa sopir harus pas sisa. Kurang = barang dibawa pulang dulu. */
  if (isCod.value && jumlah !== current.value.sisa) {
    return `COD harus dibayar penuh (${formatPrice(current.value.sisa)}). Kalau kurang, catat dulu barang yang dibawa pulang.`
  }
  return ''
})

function printData(jenis, extra = {}) {
  const r = current.value
  return {
    jenis,
    sync_id: r.kasir_sync_id || null,
    nomor: r.nomor,
    nama: r.nama,
    telepon: r.telepon,
    tanggal_nota: r.tanggal,
    total: r.total,
    dibayar_sebelum: r.dibayar,
    jatuh_tempo: r.jatuh_tempo,
    status_barang: r.status_barang,
    kasir: props.cashierName,
    waktu: new Date().toISOString(),
    barang: (detail.value?.barang || []).map((b) => ({ nama: b.nama, varian: b.varian, jumlah: b.jumlah })),
    ...extra,
  }
}

async function printQuietly(data) {
  try {
    await enqueuePiutangPrint({ data })
    void flushPrintJobs()
    return true
  } catch {
    return false
  }
}

async function record(jenis, extra = {}) {
  const r = current.value
  busy.value = true
  actionError.value = ''
  actionMessage.value = ''
  try {
    await enqueuePiutangAction({
      aksi_id: createUuid(),
      jenis,
      kunci: r.kunci,
      pin: props.pin,
      jumlah: extra.jumlah || 0,
      cara: extra.cara || '',
      catatan: extra.catatan || '',
    })
    const hasil = await flushPiutangActions()
    const printed = await printQuietly(printData(jenis, extra))
    const offline = hasil.pending > 0
    actionMessage.value = [
      jenis !== 'bayar' ? 'Barang ditandai sudah diambil.' : (isCod.value ? 'Uang COD dicatat; nota selesai.' : 'Pembayaran dicatat.'),
      offline ? 'Server belum terjangkau: tersimpan di antrean tablet, dikirim otomatis.' : 'Pemilik melihatnya di master.',
      printed ? 'Struk masuk antrean cetak.' : 'Struk belum bisa disiapkan untuk printer.',
    ].join(' ')
    if (hasil.failed > 0) {
      actionError.value = 'Server menolak aksi ini. Periksa lagi rinciannya.'
    }
    await load()
    const again = rows.value.find((row) => row.kunci === r.kunci)
    if (again) {
      await open(again, { keepMessage: true })
    } else {
      detail.value = { ...detail.value, menunggu: [{ jenis }] }
    }
  } catch (error) {
    actionError.value = error.message || 'Aksi tidak dapat disimpan di tablet.'
  } finally {
    busy.value = false
    confirmHandover.value = false
  }
}

function submitPayment() {
  if (payProblem.value || busy.value) {
    return
  }
  void record('bayar', { jumlah: Number(payAmount.value), cara: payMethod.value, catatan: payNote.value.trim() })
}

function submitHandover() {
  if (handoverBlocked.value || busy.value) {
    return
  }
  if (!confirmHandover.value) {
    confirmHandover.value = true
    return
  }
  void record('serahkan')
}

async function printBill() {
  actionMessage.value = (await printQuietly(printData('tagihan'))) ? 'Tagihan masuk antrean cetak.' : 'Tagihan belum bisa disiapkan untuk printer.'
}

let searchTimer = null
watch(query, () => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(load, 300)
})

watch(() => [props.tab, props.pin], () => {
  subFilter.value = ''
  selected.value = null
  detail.value = null
  if (props.pin) {
    void load()
  }
}, { immediate: true })

const emptyText = computed(() => ({
  proses: 'Tidak ada pesanan, titipan, atau COD yang belum selesai.',
  invoice: 'Tidak ada invoice yang belum lunas.',
  ambil: 'Tidak ada barang pelanggan yang menunggu diambil atau dipesan.',
}[props.tab] || 'Tidak ada nota yang belum lunas.'))

defineExpose({ load })
</script>

<template>
  <div class="piutang-body">
    <div class="piutang-list-pane">
      <div class="piutang-search">
        <input v-model="query" type="search" placeholder="Cari nama, No. HP, nomor nota" aria-label="Cari piutang" />
      </div>

      <div v-if="tab === 'proses'" class="piutang-chips piutang-sub" role="tablist" aria-label="Jenis nota">
        <button
          v-for="opsi in SUB_FILTER"
          :key="opsi.value"
          type="button"
          class="piutang-chip"
          :class="{ selected: subFilter === opsi.value }"
          @click="subFilter = opsi.value"
        >{{ opsi.label }}<template v-if="subCount(opsi.value)"> · {{ subCount(opsi.value) }}</template></button>
      </div>

      <p v-if="errorMessage" class="piutang-error" role="alert">{{ errorMessage }}</p>

      <div v-if="loading && rows.length === 0" class="piutang-empty"><LoadingSpinner /></div>
      <p v-else-if="shownRows.length === 0 && !errorMessage" class="piutang-empty">{{ emptyText }}</p>

      <button
        v-for="row in shownRows"
        :key="row.kunci"
        type="button"
        class="piutang-row"
        :class="{ selected: selected?.kunci === row.kunci, late: isLate(row) }"
        @click="open(row)"
      >
        <span class="piutang-row-main">
          <strong>{{ row.nama || 'Tanpa nama' }}</strong>
          <span class="piutang-row-sub">
            {{ row.nomor }} · {{ formatDate(row.tanggal) }}<template v-if="row.pembuat"> · oleh {{ row.pembuat }}</template>
          </span>
          <span class="piutang-tags">
            <span v-if="JENIS_LABEL[row.jenis]" class="piutang-tag" :class="{ cod: row.jenis === 'cod' }">{{ JENIS_LABEL[row.jenis] }}</span>
            <span v-if="row.acc_status === 'ditolak'" class="piutang-tag danger">Ditolak: batalkan nota</span>
            <span v-else-if="row.acc_status === 'menunggu'" class="piutang-tag warn">{{ row.jenis_acc === 'sop' ? 'Melanggar SOP · ACC' : 'Menunggu ACC' }}</span>
            <span v-if="isLate(row)" class="piutang-tag danger">Lewat tempo</span>
            <span v-if="row.menunggu?.length" class="piutang-tag">Belum terkirim ke master</span>
          </span>
        </span>
        <span class="piutang-row-amount">
          <template v-if="row.sisa > 0">
            <strong>{{ formatPrice(row.sisa) }}</strong>
            <span class="piutang-row-sub">dari {{ formatPrice(row.total) }}</span>
          </template>
          <strong v-else>Lunas</strong>
        </span>
      </button>
    </div>

    <div class="piutang-detail-pane">
      <p v-if="!current" class="piutang-empty">Pilih satu nota di kiri.</p>

      <template v-else>
        <div class="piutang-detail-head">
          <div>
            <h3>{{ current.nama || 'Tanpa nama' }}</h3>
            <p class="piutang-row-sub">
              {{ current.nomor }} · {{ formatDate(current.tanggal) }}<template v-if="current.telepon"> · {{ current.telepon }}</template>
            </p>
          </div>
          <button v-if="current.sisa > 0" type="button" class="piutang-secondary" :disabled="busy" @click="printBill">CETAK TAGIHAN</button>
        </div>

        <div class="piutang-figures">
          <div>
            <span>Total{{ JENIS_LABEL[current.jenis] ? ` · ${JENIS_LABEL[current.jenis]}` : '' }}</span>
            <strong>{{ formatPrice(current.total) }}</strong>
            <small v-if="current.potongan_retur">barang kembali −{{ formatPrice(current.potongan_retur) }}</small>
          </div>
          <div><span>Dibayar</span><strong>{{ formatPrice(current.dibayar) }}</strong></div>
          <div :class="{ late: isLate(current) }">
            <span>{{ isCod ? 'Tagih di tempat' : 'Sisa' }}</span>
            <strong>{{ current.sisa > 0 ? formatPrice(current.sisa) : 'Lunas' }}</strong>
            <small v-if="current.jatuh_tempo && current.sisa > 0 && !isCod">tempo {{ formatDate(current.jatuh_tempo) }}</small>
          </div>
          <div>
            <span>Barang</span>
            <strong>{{ STATUS_BARANG_LABEL[current.status_barang] || current.status_barang }}</strong>
            <small v-if="current.barang_selesai">sudah diambil</small>
          </div>
        </div>

        <p v-if="current.pembuat" class="piutang-row-sub">Dibuat oleh {{ current.pembuat }}</p>
        <p v-if="current.acc_status === 'menunggu'" class="piutang-note warn">{{ accText(current) }}</p>
        <p v-if="current.acc_status === 'ditolak'" class="piutang-error" role="alert">
          Ditolak pemilik. Kasir pembuat wajib membatalkan (void) nota ini dari Riwayat, lalu jelaskan ke pembeli.
        </p>
        <p v-if="pending" class="piutang-note">Ada aksi kasir yang belum diproses master (biasanya kurang dari 1 menit).</p>
        <p v-if="detailError" class="piutang-note">{{ detailError }}</p>
        <p v-if="actionMessage" class="piutang-note ok" role="status">{{ actionMessage }}</p>
        <p v-if="actionError" class="piutang-error" role="alert">{{ actionError }}</p>

        <div v-if="canPay" class="piutang-action">
          <p class="piutang-action-title">{{ isCod ? 'Uang COD diterima' : 'Catat pembayaran' }}</p>
          <div class="piutang-pay-row">
            <input
              :value="payAmount ? `Rp ${formatRupiahInput(payAmount)}` : ''"
              type="text"
              inputmode="numeric"
              autocomplete="off"
              placeholder="Rp 0"
              aria-label="Jumlah dibayar"
              @keydown="preventNonNumericInput"
              @input="payAmount = digitsOnly($event.target.value)"
            />
            <div class="piutang-chips">
              <button
                v-for="opsi in CARA"
                :key="opsi.value"
                type="button"
                class="piutang-chip"
                :class="{ selected: payMethod === opsi.value }"
                @click="payMethod = opsi.value"
              >{{ opsi.label }}</button>
            </div>
          </div>
          <input v-model="payNote" type="text" maxlength="120" placeholder="Catatan (opsional), mis. transfer BCA" aria-label="Catatan pembayaran" />
          <p v-if="payProblem && payAmount" class="piutang-error">{{ payProblem }}</p>
          <button type="button" class="piutang-primary" :disabled="Boolean(payProblem) || busy" @click="submitPayment">
            {{ isCod ? 'SIMPAN UANG COD & CETAK' : Number(payAmount) === current.sisa ? 'SIMPAN PELUNASAN & CETAK' : 'SIMPAN PEMBAYARAN & CETAK' }}
          </button>
        </div>

        <div v-if="canReturCod" class="piutang-action">
          <p class="piutang-action-title">Pembeli tidak membayar penuh?</p>
          <p class="piutang-row-sub">
            Barang yang tidak dibayar dibawa pulang: catat sebagai retur (boleh sebagian). No. HP pembeli masuk daftar hitam COD.
          </p>
          <button type="button" class="piutang-secondary" :disabled="busy" @click="emit('retur-cod', current)">BARANG DIBAWA PULANG</button>
        </div>

        <div v-if="canHandover" class="piutang-action">
          <p class="piutang-action-title">Barang {{ current.status_barang === 'pesan' ? 'pesanan' : 'di toko' }}</p>
          <p v-if="handoverBlocked" class="piutang-note">{{ handoverBlocked }}</p>
          <button type="button" class="piutang-primary" :class="{ confirm: confirmHandover }" :disabled="Boolean(handoverBlocked) || busy" @click="submitHandover">
            {{ confirmHandover ? 'YA, BARANG SUDAH DIAMBIL' : 'BARANG SUDAH DIAMBIL' }}
          </button>
        </div>

        <div v-if="detailLoading" class="piutang-empty"><LoadingSpinner /></div>
        <template v-else-if="detail">
          <p v-if="detail.barang?.length" class="piutang-action-title">Barang</p>
          <div v-for="(b, i) in detail.barang || []" :key="`b${i}`" class="piutang-line">
            <span>{{ b.nama }}<template v-if="b.varian && b.varian !== 'N/A'"> / {{ b.varian }}</template>
              <small> {{ b.jumlah }} × {{ formatPrice(b.harga) }}</small></span>
            <span>{{ formatPrice(b.subtotal) }}</span>
          </div>
          <p v-if="detail.pembayaran?.length" class="piutang-action-title">Pembayaran</p>
          <div v-for="(p, i) in detail.pembayaran || []" :key="`p${i}`" class="piutang-line">
            <span>{{ formatDate(p.waktu) }} · {{ { cash: 'Cash', edc: 'EDC', bank_transfer: 'Transfer' }[p.cara] || p.cara }}
              <small v-if="p.oleh || p.catatan"> {{ [p.oleh, p.catatan].filter(Boolean).join(' · ') }}</small></span>
            <span>{{ formatPrice(p.jumlah) }}</span>
          </div>
        </template>
      </template>
    </div>
  </div>
</template>

<style scoped>
.piutang-body {
  display: grid;
  grid-template-columns: minmax(280px, 380px) minmax(0, 1fr);
  min-height: 0;
  flex: 1;
}

.piutang-list-pane {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px;
  overflow-y: auto;
  border-right: 1px solid var(--pos-divider, var(--pos-border));
}

.piutang-detail-pane {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 20px;
  overflow-y: auto;
  background: var(--pos-surface-alt);
}

.piutang-search input,
.piutang-action input {
  width: 100%;
  height: 46px;
  padding: 0 12px;
  box-sizing: border-box;
  border: 1px solid var(--pos-border);
  border-radius: 10px;
  background: var(--pos-field-bg);
  color: var(--pos-text);
  font-size: 16px;
}

.piutang-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding: 12px;
  border: 1px solid var(--pos-border);
  border-radius: 12px;
  background: var(--pos-surface);
  color: var(--pos-text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.piutang-row.selected {
  border-color: var(--pos-accent);
  background: var(--pos-accent-soft);
}

.piutang-row.late {
  border-left: 4px solid var(--pos-danger);
}

.piutang-row-main,
.piutang-row-amount {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.piutang-row-amount {
  align-items: flex-end;
  white-space: nowrap;
}

.piutang-line small {
  display: block;
}

.piutang-row-sub,
.piutang-line small,
.piutang-figures small {
  color: var(--pos-text-muted);
  font-size: 0.82rem;
}

.piutang-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 2px;
}

.piutang-tag {
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--pos-hover);
  font-size: 0.75rem;
  font-weight: 600;
}

.piutang-tag.warn {
  background: var(--pos-warning-bg);
  color: var(--pos-warning-text);
}

.piutang-tag.cod {
  background: var(--pos-accent);
  color: var(--pos-on-accent, #fff);
}

.piutang-sub {
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.piutang-tag.danger {
  background: var(--pos-outstock-bg);
  color: var(--pos-outstock-text);
}

.piutang-empty {
  padding: 24px 8px;
  color: var(--pos-text-muted);
  text-align: center;
}

.piutang-error {
  margin: 0;
  color: var(--pos-danger);
  font-size: 0.9rem;
}

.piutang-detail-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.piutang-detail-head h3 {
  margin: 0;
  font-size: 1.25rem;
}

.piutang-figures {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.piutang-figures > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--pos-surface);
}

.piutang-figures span {
  color: var(--pos-text-muted);
  font-size: 0.8rem;
}

.piutang-figures strong {
  font-size: 1.05rem;
}

.piutang-figures .late strong {
  color: var(--pos-danger);
}

.piutang-note {
  margin: 0;
  padding: 8px 12px;
  border-radius: 10px;
  background: var(--pos-surface);
  font-size: 0.9rem;
}

.piutang-note.warn {
  background: var(--pos-warning-bg);
  color: var(--pos-warning-text);
}

.piutang-note.ok {
  background: var(--pos-accent-soft);
}

.piutang-action {
  display: grid;
  gap: 8px;
  padding: 12px;
  border-radius: 12px;
  background: var(--pos-surface);
}

.piutang-action-title {
  margin: 4px 0 0;
  font-weight: 700;
}

.piutang-pay-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
}

.piutang-chips {
  display: flex;
  gap: 6px;
}

.piutang-chip,
.piutang-secondary {
  min-height: 46px;
  padding: 0 14px;
  border: 1px solid var(--pos-border);
  border-radius: 10px;
  background: var(--pos-field-bg);
  color: var(--pos-text);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}

.piutang-chip.selected {
  border-color: var(--pos-accent);
  background: var(--pos-accent-soft);
}

.piutang-primary {
  min-height: 50px;
  border: 0;
  border-radius: 12px;
  background: var(--pos-accent);
  color: var(--pos-accent-contrast);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}

.piutang-primary.confirm {
  background: var(--pos-danger);
}

.piutang-primary:disabled,
.piutang-secondary:disabled {
  opacity: .5;
  cursor: default;
}

.piutang-line {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--pos-divider, var(--pos-border));
}

@media (max-width: 900px) {
  .piutang-body {
    grid-template-columns: minmax(0, 1fr);
  }

  .piutang-figures {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
