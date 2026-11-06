<script setup>
/*
 * Pil status server di header POS (permintaan pemilik, 2026-10-09).
 *
 * Komponen ini sengaja "bodoh": seluruh keadaan datang sebagai prop dari
 * `useServerStatus()`, dan satu-satunya keluaran adalah permintaan memeriksa
 * ulang. Tanpa panel detail — pil ini indikator saja; antrean tetap dikirim
 * manual lewat tombol "Kirim semua".
 *
 * Bahasa untuk orang toko ("Online"/"Offline"), mengikuti gaya tombol status
 * printer di file sebelah (`PrinterStatusPanel.vue`): titik warna adalah
 * pembeda utama dan teks boleh hilang di layar sempit.
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
  title: {
    type: String,
    default: '',
  },
  checking: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['refresh'])
</script>

<template>
  <button
    type="button"
    class="server-status"
    :class="[`is-${tone}`, { 'is-checking': checking }]"
    :title="title"
    :aria-label="`${label}. ${title}`"
    @click="emit('refresh')"
  >
    <!--
      Ikon, bukan titik (permintaan pemilik, 2026-11-06): di tablet tegak teksnya
      hilang dan dua titik (server | printer) sulit dibedakan. Wi-Fi = internet/server;
      dicoret saat terputus, jadi bukan warna saja yang membedakan.
    -->
    <svg class="server-icon" :class="`is-${tone}`" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M2 8.8a15 15 0 0 1 20 0" />
      <path d="M5 12.6a10 10 0 0 1 14 0" />
      <path d="M8.5 16.3a5 5 0 0 1 7 0" />
      <circle cx="12" cy="19.6" r="1" fill="currentColor" stroke="none" />
      <path v-if="tone === 'offline'" d="M3 3l18 18" />
    </svg>

    <span class="server-label">{{ label }}</span>
  </button>
</template>

<style scoped>
/* ========================================
   PIL STATUS SERVER
======================================== */

.server-status {
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

.server-status:hover {
  background: var(--pos-hover);
}

/*
 * Titik warna adalah pembeda utama — teksnya boleh hilang di layar sempit,
 * warnanya tidak.
 */

.server-icon {
  flex-shrink: 0;

  color: var(--pos-text-muted);
}

.server-icon.is-ok {
  color: var(--pos-success-text);
}

.server-icon.is-offline {
  color: var(--pos-danger);
}

.server-status.is-ok {
  border-color: var(--pos-success-text);
}

.server-status.is-offline {
  border-color: var(--pos-danger);
}

.server-status.is-checking .server-icon {
  opacity: 0.4;
}

/* ========================================
   LAYAR SEMPIT & PENDEK
======================================== */

/*
 * Di bawah 900px titik warnanya tetap terlihat, dan arti lengkapnya ada di
 * `title` (sama seperti tombol status printer).
 */

@media (max-width: 900px) {
  .server-label {
    display: none;
  }

  /*
   * Tanpa teks, lebarnya hanya ikon + padding (42px). Dikunci ke 44px supaya
   * target sentuh tetap sebesar tombol lain (audit tampilan, 2026-10-09).
   */
  .server-status {
    justify-content: center;

    min-width: 44px;

    padding: 0 10px;
  }
}

/* Tablet mendatar hanya sekitar 600px tinggi; tombolnya ikut dirapatkan. */

@media (max-height: 720px) {
  .server-status {
    min-height: 44px;
    height: 44px;
  }
}
</style>
