import { computed, ref, watch } from 'vue'

/*
 * Mode gelap/terang POS.
 *
 * Kenapa modul kecil terpisah: preferensi tema hanya satu nilai untuk seluruh
 * aplikasi dan harus bertahan antar sesi, jadi tidak perlu masuk Pinia
 * (Pinia masih scaffold — lihat AGENTS.md §9).
 *
 * Atribut `data-theme` di <html> dipasang juga oleh skrip kecil di
 * index.html supaya layar tidak berkedip putih sebelum Vue siap.
 */

const STORAGE_KEY = 'posTheme'

function preferredTheme() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)

    if (stored === 'dark' || stored === 'light') {
      return stored
    }
  } catch {
    /*
     * Mode privat / penyimpanan diblokir: pilihan pemakai cukup ikut sistem.
     */
  }

  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function applyTheme(value) {
  document.documentElement.dataset.theme = value
  document.documentElement.style.colorScheme = value
}

const theme = ref(preferredTheme())

applyTheme(theme.value)

watch(theme, (value) => {
  applyTheme(value)

  try {
    localStorage.setItem(STORAGE_KEY, value)
  } catch {
    /* Preferensi tidak bisa disimpan; tema tetap berlaku untuk sesi ini. */
  }
})

const isDark = computed(() => theme.value === 'dark')

function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
}

export function useTheme() {
  return {
    theme,
    isDark,
    toggleTheme,
  }
}
