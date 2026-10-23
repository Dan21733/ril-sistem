<script setup>
/*
 * Penanda "sedang memuat" tunggal untuk POS (permintaan pemilik, 2026-10-07).
 *
 * Sebelumnya setiap layar menunggu dengan caranya sendiri: daftar item kosong
 * tanpa keterangan saat page pertama belum datang (layar terlihat menggantung),
 * tombol "Muat lebih banyak" hanya berganti kata, dan riwayat nota menulis teks
 * polos. Satu komponen supaya perilakunya seragam dan tidak ada animasi baru
 * yang ditulis ulang per layar (aturan tampilan §12).
 *
 * Warnanya memakai token netral `--pos-divider` + `--pos-text-muted`, bukan
 * aksen teal: sesuai aturan tampilan §12, aksen hanya untuk tombol Bayar,
 * tombol Konfirmasi, dan fokus kotak pencarian.
 */
defineProps({
  label: {
    type: String,
    default: 'Memuat...',
  },

  /*
   * Versi rapat untuk dipakai di dalam tombol (mis. "Muat lebih banyak"):
   * cincinnya lebih kecil, jarak dan warnanya mengikuti tombolnya.
   */
  compact: {
    type: Boolean,
    default: false,
  },
})
</script>

<template>
  <span class="loading-spinner" :class="{ 'is-compact': compact }" role="status">
    <span class="spinner-ring" aria-hidden="true"></span>

    <span class="spinner-label">{{ label }}</span>
  </span>
</template>

<style scoped>
.loading-spinner {
  display: flex;

  gap: 10px;

  align-items: center;

  justify-content: center;

  padding: 18px 14px;

  color: var(--pos-text-muted);

  font-size: 14px;
}

.loading-spinner.is-compact {
  display: inline-flex;

  padding: 0;

  color: inherit;

  font-size: inherit;
}

.spinner-ring {
  display: block;

  flex-shrink: 0;

  width: 26px;

  height: 26px;

  border: 3px solid var(--pos-divider);

  border-top-color: var(--pos-text-muted);

  border-radius: 50%;

  animation: spinner-turn 0.8s linear infinite;
}

.loading-spinner.is-compact .spinner-ring {
  width: 14px;

  height: 14px;

  border-width: 2px;
}

@keyframes spinner-turn {
  to {
    transform: rotate(360deg);
  }
}

/*
 * Pengguna yang meminta animasi minim tetap perlu tahu aplikasinya bekerja,
 * jadi putarannya diperlambat, bukan dimatikan.
 */
@media (prefers-reduced-motion: reduce) {
  .spinner-ring {
    animation-duration: 2.4s;
  }
}
</style>
