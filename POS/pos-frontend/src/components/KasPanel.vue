<script setup>
import { computed, onMounted, ref } from 'vue'

import { fetchKasStatus, postKasBuka, postKasTutup } from '../services/api/kasApi'
import { createUuid } from '../services/id'
import deviceRepository from '../services/storage/deviceRepository'
import { enqueueKasGerak, flushKasGerak, listPendingKasGerak, listRecentKasGerak } from '../services/storage/kasQueue'
import { listPendingPiutangActions } from '../services/storage/piutangQueue'
import {
  KATEGORI_BAWAAN, gerakProblem, mergeGerak, readKasStatus, rememberKasStatus, tutupHariIni, tutupProblem,
} from '../utils/kas'
import { digitsOnly, formatRupiahInput, preventNonNumericInput } from '../utils/rupiahInput'

import LoadingSpinner from './LoadingSpinner.vue'

/*
 * Kas laci di menu Transaksi (rancangan 2026-11-03).
 *
 * Semua tablet boleh mencatat uang keluar (masuk antrean, jalan offline).
 * Tablet kas (diizinkan pemilik di master) juga membuka kas pagi, mencatat
 * tambah modal, dan menutup kas sore. Hitungan tutup kas buta: tablet tidak
 * pernah melihat angka seharusnya/selisih; pemilik yang memeriksa di HP.
 */

const props = defineProps({
  pin: { type: String, default: '' },
  cashierName: { type: String, default: '' },
  queuedCount: { type: Number, default: 0 },
})

const emit = defineEmits(['pin-invalid', 'status'])

const status = ref(readKasStatus())
const online = ref(false)
const loading = ref(false)
const loadError = ref('')
const localRows = ref([])
const localPending = ref(0)
const busy = ref(false)
const message = ref('')
const errorMessage = ref('')

const modal = ref('')

const jenis = ref('keluar')
const kategori = ref('')
const jumlah = ref('')
const keterangan = ref('')

const diambil = ref('')
const uangSisa = ref('')
const catatanTutup = ref('')
const confirmTutup = ref(false)

const kategoriList = computed(() => status.value?.kategori?.length ? status.value.kategori : KATEGORI_BAWAAN)
const sesi = computed(() => status.value?.sesi || null)
const sudahTutup = computed(() => tutupHariIni(status.value))
const actionPane = ref(null)
const bolehKas = computed(() => Boolean(status.value?.boleh_kas))
const gerak = computed(() => mergeGerak(sesi.value?.gerak || [], localRows.value))
const totalKeluar = computed(() => gerak.value.filter((g) => g.jenis === 'keluar').reduce((n, g) => n + Number(g.jumlah || 0), 0))
const totalDihitung = computed(() => Number(digitsOnly(diambil.value) || 0) + Number(digitsOnly(uangSisa.value) || 0))
const pendingTotal = computed(() => props.queuedCount + localPending.value)
const gerakError = computed(() => gerakProblem({ jenis: jenis.value, jumlah: jumlah.value, kategori: kategori.value, keterangan: keterangan.value }))
const tutupError = computed(() => tutupProblem({ status: status.value, online: online.value, localPending: pendingTotal.value, uangSisa: uangSisa.value, diambil: diambil.value }))

function formatPrice(value) {
  return `Rp ${new Intl.NumberFormat('id-ID').format(Math.round(Number(value) || 0))}`
}

function formatTime(value) {
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) {
    return ''
  }
  return d.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' })
}

function formatDateTime(value) {
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) {
    return ''
  }
  return d.toLocaleString('id-ID', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

async function token() {
  const device = await deviceRepository.getDevice()
  return device?.device_token || ''
}

async function refreshLocal() {
  const [pendingKas, pendingPiutang, recent] = await Promise.all([
    listPendingKasGerak(), listPendingPiutangActions(), listRecentKasGerak(),
  ])
  localPending.value = pendingKas.length + pendingPiutang.length
  localRows.value = recent
}

function applyStatus(data) {
  status.value = data
  online.value = true
  rememberKasStatus(data)
  emit('status', data)
  if (!data.sesi) {
    modal.value = String(data.modal_saran || 0)
  }
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    await flushKasGerak().catch(() => {})
    await refreshLocal()
    applyStatus(await fetchKasStatus(await token(), pendingTotal.value))
  } catch (error) {
    online.value = false
    loadError.value = error.status
      ? error.message
      : 'Server tidak terjangkau. Uang keluar tetap bisa dicatat; buka & tutup kas butuh koneksi.'
  } finally {
    loading.value = false
  }
}

function scrollTop() {
  actionPane.value?.scrollTo?.({ top: 0, behavior: 'smooth' })
}

function handleError(error) {
  if (error.status === 400 && /PIN/i.test(error.message)) {
    emit('pin-invalid')
    return
  }
  errorMessage.value = error.status ? error.message : 'Server tidak terjangkau. Coba lagi saat tersambung.'
}

async function buka() {
  if (busy.value) {
    return
  }
  busy.value = true
  message.value = ''
  errorMessage.value = ''
  try {
    applyStatus(await postKasBuka(await token(), {
      pin: props.pin,
      modal: Number(digitsOnly(modal.value) || 0),
    }))
    message.value = 'Kas dibuka.'
  } catch (error) {
    handleError(error)
  } finally {
    busy.value = false
    scrollTop()
  }
}

async function simpanGerak() {
  if (busy.value || gerakError.value) {
    return
  }
  busy.value = true
  message.value = ''
  errorMessage.value = ''
  try {
    await enqueueKasGerak({
      gerak_id: createUuid(),
      pin: props.pin,
      oleh: props.cashierName,
      jenis: jenis.value,
      jumlah: Number(digitsOnly(jumlah.value)),
      kategori: jenis.value === 'keluar' ? kategori.value : '',
      keterangan: keterangan.value.trim(),
    })
    const hasil = await flushKasGerak().catch(() => ({ pending: 1, failed: 0 }))
    await refreshLocal()
    const ditolak = localRows.value.find((g) => g.status === 'rejected' && g.detail)
    if (hasil.failed && ditolak) {
      errorMessage.value = `Ditolak server: ${ditolak.detail}`
    } else {
      message.value = hasil.pending ? 'Tersimpan di tablet; dikirim otomatis saat tersambung.' : 'Tercatat.'
    }
    jumlah.value = ''
    keterangan.value = ''
    kategori.value = ''
    if (!hasil.pending) {
      await load()
    }
  } finally {
    busy.value = false
  }
}

async function tutup() {
  if (busy.value || tutupError.value) {
    return
  }
  if (!confirmTutup.value) {
    confirmTutup.value = true
    return
  }
  busy.value = true
  message.value = ''
  errorMessage.value = ''
  try {
    applyStatus(await postKasTutup(await token(), {
      pin: props.pin,
      sesi_id: sesi.value.id,
      diambil: Number(digitsOnly(diambil.value) || 0),
      uang_sisa: Number(digitsOnly(uangSisa.value) || 0),
      catatan: catatanTutup.value.trim(),
      antrean: pendingTotal.value,
    }))
    diambil.value = ''
    uangSisa.value = ''
    catatanTutup.value = ''
    message.value = 'Kas ditutup. Hitungan dikirim ke pemilik untuk ACC.'
  } catch (error) {
    handleError(error)
  } finally {
    busy.value = false
    confirmTutup.value = false
    scrollTop()
  }
}

onMounted(load)

defineExpose({ load })
</script>

<template>
  <div class="kas-body">
    <div class="kas-status-pane">
      <div class="kas-status-card" :class="{ open: sesi }">
        <p class="kas-label">Kas toko</p>
        <p class="kas-state">{{ sesi ? 'Dibuka' : sudahTutup ? 'Sudah ditutup' : 'Belum dibuka' }}</p>
        <p v-if="sesi" class="kas-sub">{{ formatDateTime(sesi.dibuka_waktu) }} · {{ sesi.dibuka_oleh }} · modal {{ formatPrice(sesi.modal_awal) }}</p>
        <p v-else-if="sudahTutup" class="kas-sub">Ditutup {{ formatTime(status.tutup_terakhir) }}. Sisa {{ formatPrice(status.modal_saran) }} jadi modal besok.</p>
        <p v-else-if="status?.tutup_terakhir" class="kas-sub">Tutup terakhir {{ formatDateTime(status.tutup_terakhir) }}</p>
        <p v-if="status?.mode_pemilik" class="kas-sub">Kas dibuka &amp; ditutup pemilik dari HP.</p>
        <p v-else-if="!bolehKas" class="kas-sub">Kas dibuka &amp; ditutup dari tablet kas. Tablet ini mencatat uang keluar.</p>
      </div>

      <p v-if="loading" class="kas-empty"><LoadingSpinner /></p>
      <p v-if="loadError" class="kas-note warn">{{ loadError }}</p>

      <p class="kas-section-title">
        Uang keluar &amp; modal
        <span v-if="totalKeluar" class="kas-muted">· keluar {{ formatPrice(totalKeluar) }}</span>
      </p>
      <p v-if="!gerak.length" class="kas-empty">Belum ada catatan.</p>
      <div v-for="g in gerak" :key="g.gerak_id" class="kas-line">
        <span>
          {{ g.jenis === 'tambah_modal' ? 'Tambah modal' : g.kategori }}
          <small>{{ formatTime(g.waktu) }} · {{ g.oleh || cashierName }}<template v-if="g.keterangan"> · {{ g.keterangan }}</template></small>
          <small v-if="g.local && g.status === 'pending'" class="kas-pending">belum terkirim</small>
          <small v-else-if="g.local && g.status === 'rejected'" class="kas-rejected">ditolak: {{ g.detail }}</small>
        </span>
        <strong>{{ formatPrice(g.jumlah) }}</strong>
      </div>
    </div>

    <div ref="actionPane" class="kas-action-pane">
      <p v-if="message" class="kas-note ok" role="status">{{ message }}</p>
      <p v-if="errorMessage" class="kas-error" role="alert">{{ errorMessage }}</p>

      <div v-if="!sesi && bolehKas" class="kas-action">
        <p class="kas-action-title">{{ sudahTutup ? 'Buka kas lagi' : 'Buka kas' }}</p>
        <label class="kas-field">
          <span>Modal di laci (hitung dulu)</span>
          <input :value="modal ? `Rp ${formatRupiahInput(modal)}` : ''" type="text" inputmode="numeric" placeholder="Rp 0" aria-label="Modal di laci"
            @keydown="preventNonNumericInput" @input="modal = digitsOnly($event.target.value)" />
          <small>Sisa tutup kas terakhir {{ formatPrice(status?.modal_saran) }}. Kalau beda, isi yang benar-benar ada.</small>
        </label>
        <button type="button" class="kas-primary" :disabled="busy || !online" @click="buka">{{ busy ? 'MEMBUKA…' : 'BUKA KAS' }}</button>
        <p v-if="!online" class="kas-muted">Buka kas butuh koneksi ke server.</p>
      </div>

      <div class="kas-action">
        <p class="kas-action-title">Catat {{ jenis === 'tambah_modal' ? 'tambah modal' : 'uang keluar' }}</p>
        <div v-if="bolehKas" class="kas-chips">
          <button type="button" class="kas-chip" :class="{ selected: jenis === 'keluar' }" @click="jenis = 'keluar'">Uang keluar</button>
          <button type="button" class="kas-chip" :class="{ selected: jenis === 'tambah_modal' }" @click="jenis = 'tambah_modal'">Tambah modal</button>
        </div>
        <div v-if="jenis === 'keluar'" class="kas-chips wrap">
          <button v-for="k in kategoriList" :key="k" type="button" class="kas-chip" :class="{ selected: kategori === k }" @click="kategori = k">{{ k }}</button>
        </div>
        <input :value="jumlah ? `Rp ${formatRupiahInput(jumlah)}` : ''" type="text" inputmode="numeric" placeholder="Rp 0" aria-label="Jumlah"
          @keydown="preventNonNumericInput" @input="jumlah = digitsOnly($event.target.value)" />
        <input v-model="keterangan" type="text" maxlength="200" :placeholder="jenis === 'keluar' ? 'Keterangan, mis. bensin antar Pak Budi' : 'Keterangan (opsional)'" aria-label="Keterangan" />
        <p v-if="gerakError && jumlah" class="kas-error">{{ gerakError }}</p>
        <button type="button" class="kas-primary" :disabled="Boolean(gerakError) || busy" @click="simpanGerak">SIMPAN</button>
      </div>

      <div v-if="sesi && bolehKas" class="kas-action">
        <p class="kas-action-title">Tutup kas</p>
        <p class="kas-muted">Hitung semua uang di laci, lalu isi yang diambil dan yang ditinggal.</p>
        <div class="kas-two">
          <label class="kas-field">
            <span>Uang diambil</span>
            <input :value="diambil ? `Rp ${formatRupiahInput(diambil)}` : ''" type="text" inputmode="numeric" placeholder="Rp 0" aria-label="Uang diambil"
              @keydown="preventNonNumericInput" @input="diambil = digitsOnly($event.target.value); confirmTutup = false" />
          </label>
          <label class="kas-field">
            <span>Uang sisa (modal besok)</span>
            <input :value="uangSisa !== '' ? `Rp ${formatRupiahInput(uangSisa) || 0}` : ''" type="text" inputmode="numeric" :placeholder="`min Rp ${formatRupiahInput(status?.sisa_minimal ?? 0) || 0}`" aria-label="Uang sisa di laci"
              @keydown="preventNonNumericInput" @input="uangSisa = digitsOnly($event.target.value); confirmTutup = false" />
          </label>
        </div>
        <p class="kas-muted">Total dihitung {{ formatPrice(totalDihitung) }} · sisa minimal {{ formatPrice(status?.sisa_minimal) }} untuk keperluan pagi.</p>
        <input v-model="catatanTutup" type="text" maxlength="200" placeholder="Catatan (opsional)" aria-label="Catatan tutup kas" />
        <p v-if="tutupError && (uangSisa !== '' || pendingTotal || !online)" class="kas-note warn">{{ tutupError }}</p>
        <button type="button" class="kas-primary" :class="{ confirm: confirmTutup }" :disabled="Boolean(tutupError) || busy" @click="tutup">
          {{ busy ? 'MENUTUP…' : confirmTutup ? 'YA, TUTUP KAS' : 'TUTUP KAS' }}
        </button>
        <p class="kas-muted">Setelah ditutup, hitungan dikirim ke pemilik untuk ACC.</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.kas-body {
  display: grid;
  grid-template-columns: minmax(280px, 380px) minmax(0, 1fr);
  min-height: 0;
  flex: 1;
}

.kas-status-pane {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px;
  overflow-y: auto;
  border-right: 1px solid var(--pos-divider, var(--pos-border));
}

.kas-action-pane {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 20px;
  overflow-y: auto;
  background: var(--pos-surface-alt);
}

.kas-status-card {
  padding: 14px;
  border: 1px solid var(--pos-border);
  border-radius: 12px;
  background: var(--pos-surface);
}

.kas-status-card.open {
  border-color: var(--pos-accent);
  background: var(--pos-accent-soft);
}

.kas-label,
.kas-state,
.kas-sub,
.kas-section-title,
.kas-action-title,
.kas-muted {
  margin: 0;
}

.kas-label {
  color: var(--pos-text-muted);
  font-size: 0.85rem;
}

.kas-state {
  font-size: 1.4rem;
  font-weight: 700;
}

.kas-sub,
.kas-muted,
.kas-line small {
  color: var(--pos-text-muted);
  font-size: 0.85rem;
}

.kas-section-title,
.kas-action-title {
  margin-top: 6px;
  font-weight: 700;
}

.kas-line {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--pos-divider, var(--pos-border));
}

.kas-line small {
  display: block;
}

.kas-line .kas-pending {
  color: var(--pos-warning-text);
  font-weight: 600;
}

.kas-line .kas-rejected {
  color: var(--pos-danger);
}

.kas-empty {
  padding: 16px 8px;
  margin: 0;
  color: var(--pos-text-muted);
  text-align: center;
}

.kas-note {
  margin: 0;
  padding: 8px 12px;
  border-radius: 10px;
  background: var(--pos-surface);
  font-size: 0.9rem;
}

.kas-note.warn {
  background: var(--pos-warning-bg);
  color: var(--pos-warning-text);
}

.kas-note.ok {
  background: var(--pos-accent-soft);
}

.kas-error {
  margin: 0;
  color: var(--pos-danger);
  font-size: 0.9rem;
}

.kas-action {
  display: grid;
  gap: 8px;
  padding: 12px;
  border-radius: 12px;
  background: var(--pos-surface);
}

.kas-action input[type='text'] {
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

.kas-field {
  display: grid;
  gap: 4px;
}

.kas-field > span {
  font-size: 0.9rem;
  font-weight: 600;
}

.kas-field small {
  color: var(--pos-text-muted);
  font-size: 0.82rem;
}

.kas-check {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 44px;
}

.kas-check input {
  width: 22px;
  height: 22px;
}

.kas-chips {
  display: flex;
  gap: 6px;
}

.kas-chips.wrap {
  flex-wrap: wrap;
}

.kas-chip,
.kas-stepper button {
  min-height: 44px;
  padding: 0 14px;
  border: 1px solid var(--pos-border);
  border-radius: 10px;
  background: var(--pos-field-bg);
  color: var(--pos-text);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}

.kas-chip.selected {
  border-color: var(--pos-accent);
  background: var(--pos-accent-soft);
}

.kas-two {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.kas-steppers {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.kas-stepper {
  display: grid;
  gap: 6px;
  font-size: 0.9rem;
  font-weight: 600;
}

.kas-stepper > div {
  display: flex;
  align-items: center;
  gap: 10px;
}

.kas-stepper strong {
  min-width: 36px;
  font-size: 1.3rem;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.kas-stepper button {
  width: 48px;
  padding: 0;
  font-size: 1.3rem;
}

.kas-primary {
  min-height: 50px;
  border: 0;
  border-radius: 12px;
  background: var(--pos-accent);
  color: var(--pos-accent-contrast);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}

.kas-primary.confirm {
  background: var(--pos-danger);
}

.kas-primary:disabled {
  opacity: .5;
  cursor: default;
}

@media (max-width: 900px) {
  .kas-body {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
