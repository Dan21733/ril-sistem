<script setup>
/**
 * Papan angka kasir (permintaan pemilik, 2026-11-06): pengganti keyboard Samsung
 * untuk semua isian angka di tablet. Lihat `src/utils/numpad.js`.
 *
 * Cara kerja: di layar sentuh setiap isian angka diberi `inputmode="none"` supaya
 * keyboard sistem tidak terbuka. Saat isian itu fokus, papan ini muncul di
 * sampingnya. Tombol menulis ke isian lalu mengirim event `input` (dan `change`
 * saat selesai), jadi penangan milik tiap isian tetap yang memformat dan
 * memvalidasi; papan ini tidak tahu arti angkanya.
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { letakPapan, papanAktif, PEMILIH_ISIAN_ANGKA, PEMILIH_ISIAN_TERTANDA, tekanTombol } from '../utils/numpad'

const papan = ref(null)
const target = ref(null)
const posisi = ref({ left: 0, top: 0 })
let segar = false
let berubah = false
let pengamat = null
let aktif = false

const desimal = computed(() => target.value?.dataset.numpadMode === 'decimal')
const tombolKiriBawah = computed(() => {
  const isian = target.value
  if (!isian) return null
  if (desimal.value) return ','
  // PIN (maxlength) dan jumlah barang (type=number) tidak butuh 000.
  if (isian.maxLength > 0 || isian.type === 'number') return null
  return '000'
})
const baris = computed(() => [['1', '2', '3'], ['4', '5', '6'], ['7', '8', '9'], ['hapus', '0', tombolKiriBawah.value]])

function tandai(akar = document) {
  akar.querySelectorAll?.(PEMILIH_ISIAN_ANGKA).forEach((isian) => {
    if (isian.dataset.numpadMode) return
    isian.dataset.numpadMode = isian.getAttribute('inputmode') || 'numeric'
    isian.setAttribute('inputmode', 'none')
  })
}

function aturLetak() {
  const isian = target.value
  const el = papan.value
  if (!isian || !el) return
  const kotak = isian.getBoundingClientRect()
  posisi.value = letakPapan(kotak, { lebar: el.offsetWidth, tinggi: el.offsetHeight },
    { lebar: window.innerWidth, tinggi: window.innerHeight })
}

async function buka(isian) {
  if (target.value && target.value !== isian) selesai(false)
  target.value = isian
  segar = true
  berubah = false
  await nextTick()
  aturLetak()
}

function kirim(isian, jenis) {
  isian.dispatchEvent(new Event(jenis, { bubbles: true }))
}

function selesai(blur = true) {
  const isian = target.value
  target.value = null
  if (!isian) return
  if (berubah) kirim(isian, 'change')
  berubah = false
  if (blur && document.activeElement === isian) isian.blur()
}

function tekan(tombol) {
  const isian = target.value
  if (!isian || isian.disabled || isian.readOnly) return
  if (tombol === 'ok') {
    isian.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }))
    // Penangan Enter bisa memindah fokus ke isian berikutnya (jumlah -> harga); jangan menutup papan milik isian baru itu.
    if (target.value === isian) selesai(document.activeElement === isian)
    else if (berubah) kirim(isian, 'change')
    return
  }
  const baru = tekanTombol(isian.value, tombol, { segar, maks: isian.maxLength > 0 ? isian.maxLength : 0, desimal: desimal.value })
  segar = false
  if (baru === isian.value) return
  isian.value = baru
  berubah = true
  kirim(isian, 'input')
  // Isian bisa berubah lebar (format Rp); jaga papan tetap di sampingnya.
  nextTick(aturLetak)
}

function saatFokus(e) {
  const isian = e.target
  if (!(isian instanceof HTMLInputElement) || !isian.matches(PEMILIH_ISIAN_TERTANDA)) return
  tandai(isian.parentElement || document)
  buka(isian)
}

function saatLepasFokus(e) {
  if (e.target !== target.value) return
  // Fokus pindah ke isian angka lain: `saatFokus` yang mengurus.
  if (e.relatedTarget instanceof HTMLInputElement && e.relatedTarget.matches(PEMILIH_ISIAN_TERTANDA)) return
  selesai(false)
}

function saatTekanLuar(e) {
  if (!target.value) return
  if (papan.value?.contains(e.target) || e.target === target.value) return
  selesai()
}

onMounted(() => {
  aktif = papanAktif(window)
  if (!aktif) return
  tandai()
  pengamat = new MutationObserver((daftar) => {
    for (const m of daftar) m.addedNodes.forEach((n) => n.nodeType === 1 && (n.matches?.(PEMILIH_ISIAN_ANGKA) ? tandai(n.parentElement) : tandai(n)))
  })
  pengamat.observe(document.body, { childList: true, subtree: true })
  document.addEventListener('focusin', saatFokus)
  document.addEventListener('focusout', saatLepasFokus)
  document.addEventListener('pointerdown', saatTekanLuar, true)
  window.addEventListener('resize', aturLetak)
  window.addEventListener('scroll', aturLetak, true)
})

onBeforeUnmount(() => {
  if (!aktif) return
  pengamat?.disconnect()
  document.removeEventListener('focusin', saatFokus)
  document.removeEventListener('focusout', saatLepasFokus)
  document.removeEventListener('pointerdown', saatTekanLuar, true)
  window.removeEventListener('resize', aturLetak)
  window.removeEventListener('scroll', aturLetak, true)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="target"
      ref="papan"
      class="numpad"
      role="group"
      aria-label="Papan angka"
      :style="{ left: `${posisi.left}px`, top: `${posisi.top}px` }"
      @pointerdown.prevent
    >
      <template v-for="(isi, i) in baris" :key="i">
        <template v-for="(t, j) in isi" :key="`${i}-${j}`">
          <button v-if="t === 'hapus'" type="button" class="numpad-key is-muted" aria-label="Hapus satu angka" @pointerdown.prevent="tekan('hapus')">
            <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 5h11v14H9l-6-7z" /><path d="m12 9 5 6m0-6-5 6" /></svg>
          </button>
          <button v-else-if="t" type="button" class="numpad-key" @pointerdown.prevent="tekan(t)">{{ t }}</button>
          <span v-else aria-hidden="true" />
        </template>
      </template>
      <button type="button" class="numpad-key is-ok" @pointerdown.prevent="tekan('ok')">OK</button>
    </div>
  </Teleport>
</template>

<style scoped>
/*
 * Ukuran: tombol 72x54 (sasaran sentuh >=44px), jarak 8, padding 10:
 * lebar 3*72 + 2*8 + 20 = 252px, tinggi 5*54 + 4*8 + 20 = 322px. Muat di sisi
 * kiri kolom bayar tablet mendatar (1007x601) dan di atas/bawah isian di
 * tablet tegak (601x1007). z-index di atas modal checkout (1000).
 */
.numpad {
  position: fixed;
  z-index: 1200;
  display: grid;
  grid-template-columns: repeat(3, 72px);
  grid-auto-rows: 54px;
  gap: 8px;
  padding: 10px;
  background: var(--pos-surface);
  border: 1px solid var(--pos-border);
  border-radius: 14px;
  box-shadow: var(--pos-shadow);
  touch-action: manipulation;
  user-select: none;
}

.numpad-key {
  display: grid;
  place-items: center;
  padding: 0;
  border: 1px solid var(--pos-border);
  border-radius: 10px;
  background: var(--pos-surface-alt);
  color: var(--pos-text);
  font: inherit;
  font-size: 24px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}

.numpad-key:active {
  background: var(--pos-hover);
  border-color: var(--pos-control-border);
}

.numpad-key.is-muted {
  color: var(--pos-text-muted);
}

.numpad-key.is-ok {
  grid-column: 1 / -1;
  background: var(--pos-text);
  border-color: var(--pos-text);
  color: var(--pos-surface);
  font-size: 18px;
  letter-spacing: 0.08em;
}
</style>
