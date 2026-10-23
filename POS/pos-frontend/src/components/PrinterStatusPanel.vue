<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

/*
 * Tombol status printer di header POS + panel detail yang melebar ke bawah
 * (permintaan pemilik, 2026-09-30).
 *
 * Komponen ini sengaja "bodoh": seluruh keadaan (status, alamat, langkah
 * perbaikan) datang sebagai prop dari `usePrinterStatus()`, dan satu-satunya
 * keluaran adalah permintaan memeriksa ulang. Alasannya, hanya App.vue yang tahu
 * kapan pemeriksaan perlu diulang (setelah percobaan cetak, setelah kembali
 * online), jadi polling tidak digandakan di dua tempat.
 *
 * Yang ditampilkan saat tersambung bukan sekadar "aman", tetapi bukti yang bisa
 * dibaca kasir: nama printer, alamatnya, dan pukul berapa terakhir diperiksa —
 * supaya "tersambung" bisa dibedakan dari "sudah lama tidak diperiksa".
 *
 * **Bahasa panel ini untuk orang toko, bukan pengembang (permintaan pemilik,
 * 2026-09-30).** Kata "bridge", nama versi, dan jumlah job yang diingat bridge
 * sengaja tidak ditampilkan: yang dibutuhkan kasir/admin adalah nama perangkat
 * yang bermasalah dan langkah berikutnya. Istilah teknisnya tetap hidup di kode
 * (`printBridge.js`) dan di dokumen pengembang (`PRINT_PATH_DESIGN.md`), jadi
 * jangan menyamakan bahasa panel dengan nama modul.
 */

defineProps({
  tone: {
    type: String,
    default: 'unknown',
  },
  label: {
    type: String,
    default: '',
  },
  /*
   * Bentuk pendek label untuk header sempit (audit tampilan, 2026-10-09);
   * dipakai hanya kalau muat satu baris. Kalau kosong, label panjang dipakai.
   */
  shortLabel: {
    type: String,
    default: '',
  },
  title: {
    type: String,
    default: '',
  },
  detail: {
    type: String,
    default: '',
  },
  printerName: {
    type: String,
    default: '',
  },
  printerAddress: {
    type: String,
    default: '',
  },
  printerStatusLabel: {
    type: String,
    default: 'Belum diketahui',
  },
  checkedAtLabel: {
    type: String,
    default: 'belum pernah',
  },
  nextSteps: {
    type: Array,
    default: () => [],
  },
  checking: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['refresh'])

const open = ref(false)

const root = ref(null)
const panel = ref(null)
const panelShift = ref(0)
const panelWidth = ref(360)

function positionPanel() {
  if (!panel.value || !root.value) { return }
  const anchor = root.value.getBoundingClientRect()
  const bounds = root.value.closest('.product-section')?.getBoundingClientRect()
  const minLeft = Math.max(12, bounds?.left ?? 12)
  const maxRight = Math.min(window.innerWidth - 12, bounds?.right ?? window.innerWidth - 12)
  /* 390px audit: the old panel started at -44px; constrain to the actual
     catalog pane as well as viewport, because that pane clips its children. */
  const width = Math.min(360, maxRight - minLeft)
  panelWidth.value = width
  const naturalLeft = anchor.right - width
  const left = Math.max(minLeft, Math.min(naturalLeft, maxRight - width))
  panelShift.value = left - naturalLeft
}

function toggle() {
  open.value = !open.value
}

function close() {
  open.value = false
}

/*
 * Panel adalah lapisan di atas daftar item, jadi ia harus bisa ditutup dengan
 * cara yang biasa dipakai orang: klik di luar, atau tombol Escape.
 */
function onDocumentPointerDown(event) {
  if (!root.value?.contains(event.target)) {
    close()
  }
}

function onKeydown(event) {
  if (event.key === 'Escape') {
    close()
  }
}

watch(open, (isOpen) => {
  if (typeof document === 'undefined') {
    return
  }

  if (isOpen) {
    document.addEventListener('pointerdown', onDocumentPointerDown, true)
    document.addEventListener('keydown', onKeydown)
    window.addEventListener('resize', positionPanel)
    void nextTick(positionPanel)
  } else {
    window.removeEventListener('resize', positionPanel)
    document.removeEventListener('pointerdown', onDocumentPointerDown, true)
    document.removeEventListener('keydown', onKeydown)
  }
})

onBeforeUnmount(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('resize', positionPanel)
  }
  if (typeof document === 'undefined') {
    return
  }

  document.removeEventListener('pointerdown', onDocumentPointerDown, true)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div ref="root" class="printer-status-wrap">
    <button
      type="button"
      class="printer-status"
      :class="[`is-${tone}`, { 'is-checking': checking, 'is-open': open }]"
      :title="title"
      :aria-label="`${label}. ${title}`"
      :aria-expanded="open"
      aria-controls="printer-status-panel"
      @click="toggle"
    >
      <span class="printer-dot" aria-hidden="true"></span>

      <span class="printer-label">{{ label }}</span>

      <span class="printer-label printer-label-short">{{ shortLabel || label }}</span>

      <svg class="printer-caret" :class="{ 'is-open': open }" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m7 10 5 5 5-5" /></svg>
    </button>

    <div
      v-if="open"
      id="printer-status-panel"
      ref="panel"
      class="printer-panel"
      :style="{ width: `${panelWidth}px`, transform: `translateX(${panelShift}px)` }"
      role="region"
      aria-label="Detail koneksi printer"
    >
      <div class="panel-head">
        <span class="printer-dot" :class="`is-${tone}`" aria-hidden="true"></span>

        <div>
          <p class="panel-title">{{ label }}</p>
          <p class="panel-sub">
            Status printer: {{ printerStatusLabel }} · diperiksa {{ checkedAtLabel }}
          </p>
        </div>
      </div>

      <dl class="panel-rows">
        <div class="panel-row">
          <dt>Nama printer</dt>
          <dd>{{ printerName || '-' }}</dd>
        </div>

        <div class="panel-row">
          <dt>Alamat printer</dt>
          <dd class="panel-mono">{{ printerAddress || '-' }}</dd>
        </div>

        <div class="panel-row">
          <dt>Terakhir diperiksa</dt>
          <dd>{{ checkedAtLabel }}</dd>
        </div>
      </dl>

      <p v-if="detail" class="panel-detail" role="status">{{ detail }}</p>

      <div class="panel-how">
        <p class="panel-how-title">Cara kerjanya</p>
        <p class="panel-how-body">
          Nota tidak lewat internet. Tablet mengirim data nota ke
          <strong>penghubung cetak</strong> — program kecil di komputer toko — lalu program itu
          meneruskannya ke printer lewat kabel LAN. Internet mati tidak menghentikan cetak, dan
          nota yang gagal dicetak tetap sah serta dicoba lagi otomatis saat printer hidup.
        </p>
      </div>

      <template v-if="nextSteps.length > 0">
        <p class="panel-how-title">Yang perlu diperiksa</p>

        <ol class="panel-steps">
          <li v-for="step in nextSteps" :key="step">{{ step }}</li>
        </ol>
      </template>

      <button
        type="button"
        class="panel-refresh"
        :disabled="checking"
        @click="emit('refresh')"
      >
        {{ checking ? 'MEMERIKSA...' : 'PERIKSA LAGI' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
/* ========================================
   PEMBUNGKUS (titik jangkar panel)
======================================== */

.printer-status-wrap {
  position: relative;
}

/* ========================================
   TOMBOL STATUS
======================================== */

.printer-status {
  display: flex;

  align-items: center;

  /* Tombol tidak diperas: kalau tidak muat, barisnya yang pindah. */
  flex-shrink: 0;

  gap: 8px;

  min-height: 48px;

  padding: 0 12px;

  border: 1px solid var(--pos-border);
  border-radius: 8px;

  background: var(--pos-surface);

  color: var(--pos-text);

  font-size: 14px;
  font-weight: 600;

  cursor: pointer;

  transition:
    background-color 0.15s ease,
    border-color 0.15s ease;
}

.printer-status:hover,
.printer-status.is-open {
  background: var(--pos-hover);
}

/*
 * Titik warna adalah pembeda utama — teksnya boleh hilang di layar sempit,
 * warnanya tidak.
 */

.printer-dot {
  flex-shrink: 0;

  width: 10px;
  height: 10px;

  border-radius: 50%;

  background: var(--pos-text-muted);
}

.printer-dot.is-ok {
  background: var(--pos-success-text);
}

.printer-dot.is-offline {
  background: var(--pos-danger);
}

.printer-status.is-ok {
  border-color: var(--pos-success-text);
}

.printer-status.is-offline {
  border-color: var(--pos-danger);
}

.printer-status.is-checking .printer-dot {
  opacity: 0.4;
}

.printer-caret.is-open {
  transform: rotate(180deg);
}

.printer-caret {
  flex-shrink: 0;
  color: var(--pos-text-muted);
  font-size: 11px;
  line-height: 1;
}

/* ========================================
   PANEL DETAIL
======================================== */

.printer-panel {
  position: absolute;

  top: calc(100% + 8px);
  right: 0;

  z-index: 900;

  width: min(360px, calc(100vw - 36px));

  /*
   * Panel boleh lebih tinggi dari layar pendek, jadi isinya digulung sendiri.
   * Batasnya menyisakan tinggi header; tanpa ini panel terpotong oleh
   * `overflow: hidden` di sekitar daftar item.
   */
  max-height: calc(100vh - 130px);

  overflow-y: auto;

  padding: 14px;

  box-sizing: border-box;

  border: 1px solid var(--pos-border);
  border-radius: 12px;

  background: var(--pos-surface);

  box-shadow: var(--pos-shadow);

  text-align: left;
}

.panel-head {
  display: flex;

  align-items: center;

  gap: 10px;

  padding-bottom: 10px;

  border-bottom: 1px solid var(--pos-border);
}

.panel-title {
  margin: 0;

  color: var(--pos-text);

  font-size: 15px;
  font-weight: 700;
}

.panel-sub {
  margin: 2px 0 0;

  color: var(--pos-text-muted);

  font-size: 12px;
}

.panel-rows {
  margin: 10px 0 0;
}

.panel-row {
  display: flex;

  align-items: baseline;

  gap: 10px;

  padding: 4px 0;
}

.panel-row dt {
  flex-shrink: 0;

  width: 128px;

  color: var(--pos-text-muted);

  font-size: 12.5px;
}

.panel-row dd {
  flex: 1;

  min-width: 0;

  margin: 0;

  color: var(--pos-text);

  font-size: 13px;
  font-weight: 600;

  overflow-wrap: anywhere;
}

.panel-mono {
  font-family: 'SFMono-Regular', ui-monospace, 'Roboto Mono', Menlo, monospace;
  font-size: 12.5px;
}

.panel-detail {
  margin: 10px 0 0;

  padding: 8px 10px;

  border: 1px solid var(--pos-danger);
  border-radius: 8px;

  background: var(--pos-outstock-bg);

  color: var(--pos-outstock-text);

  font-size: 12.5px;
  line-height: 1.45;
}

.panel-how {
  margin-top: 12px;

  padding: 10px 12px;

  border: 1px solid var(--pos-border);
  border-radius: 8px;

  background: var(--pos-surface-alt);
}

.panel-how-title {
  margin: 12px 0 0;

  color: var(--pos-text-muted);

  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.panel-how .panel-how-title {
  margin-top: 0;
}

.panel-how-body {
  margin: 6px 0 0;

  color: var(--pos-text);

  font-size: 12.5px;
  line-height: 1.5;
}

.panel-steps {
  margin: 8px 0 0;

  padding-left: 20px;

  color: var(--pos-text);

  font-size: 12.5px;
  line-height: 1.5;
}

.panel-steps li {
  margin-bottom: 4px;
}

.panel-refresh {
  width: 100%;

  min-height: 44px;

  margin-top: 12px;

  border: 1px solid var(--pos-border);
  border-radius: 8px;

  background: var(--pos-surface);

  color: var(--pos-text);

  font-size: 13px;
  font-weight: 700;

  cursor: pointer;

  transition: background-color 0.15s ease;
}

.panel-refresh:hover:not(:disabled) {
  background: var(--pos-hover);
}

.panel-refresh:disabled {
  opacity: 0.5;

  cursor: not-allowed;
}

/* ========================================
   LAYAR SEMPIT & PENDEK
======================================== */

/*
 * Label tidak pernah dibiarkan membungkus: tombolnya setinggi 44px, dan dua
 * baris teks di dalamnya berdesakan (audit tampilan, 2026-10-09). Yang turun
 * adalah bentuk labelnya, bukan tingginya.
 */

.printer-label {
  white-space: nowrap;
}

.printer-label-short {
  display: none;
}

/*
 * Ambang 1120px diukur, bukan ditebak (audit tampilan, 2026-10-09).
 *
 * Pada tablet kasir 1007px, kolom daftar item hanya 599px. Tombol header
 * (pil server 86 + printer 220 + tema 48 + Riwayat Nota 162 + tiga jarak 30)
 * butuh 545px, dan bersama judul "POS" 54px jumlahnya tepat 599px — nol sisa.
 * Di situ flexbox menekan tombol printer sampai labelnya pecah dua baris.
 * Perlu sekitar 1007px + margin supaya label panjang muat sebaris tanpa
 * berdesakan, jadi ambangnya dinaikkan ke 1120px (~113px sisa) dan di bawahnya
 * dipakai bentuk pendek "Printer offline" yang tetap satu baris (149px).
 */

@media (max-width: 1120px) {
  .printer-label {
    display: none;
  }

  .printer-label-short {
    display: inline;
  }
}

/*
 * Di bawah 900px judul "POS", toggle tema, dan Riwayat Nota lebih penting
 * daripada teks status. Titik warnanya tetap terlihat, dan arti lengkapnya ada
 * di `title` + panel yang bisa dibuka.
 */

@media (max-width: 900px) {
  .printer-label,
  .printer-label-short {
    display: none;
  }

  /*
   * Di bawah 900px daftar item hanya setinggi 62vh dan tidak menggulir halaman,
   * jadi panel dibatasi lebih pendek supaya ujungnya tidak terpotong.
   */
  .printer-panel {
    max-height: 46vh;
  }
}

/* Tablet mendatar hanya sekitar 600px tinggi; tombolnya ikut dirapatkan. */

@media (max-height: 720px) {
  .printer-status {
    min-height: 44px;
    height: 44px;
  }
}
</style>
