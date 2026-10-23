<script setup>
defineProps({
  modelValue: {
    type: String,
    default: '',
  },
})

defineEmits(['update:modelValue'])
</script>

<template>
  <div class="search-bar">
    <svg class="search-icon" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4 4" /></svg>

    <input
      :value="modelValue"
      type="search"
      placeholder="Cari nama barang atau varian..."
      aria-label="Cari barang"
      @input="$emit('update:modelValue', $event.target.value)"
    />
  </div>
</template>

<style scoped>
/*
 * Kotak pencarian adalah kontrol paling sering dipakai kasir, jadi ukurannya
 * dibuat nyaman untuk jari di tablet (Galaxy Tab A7 Lite) — bukan ukuran
 * desktop yang mengecil di layar sentuh.
 */

.search-bar {
  display: flex;
  align-items: center;

  width: 100%;
  min-height: 58px;

  padding: 0 0 0 16px;

  background: var(--pos-surface);
  border: 1px solid var(--pos-border);
  border-radius: 10px;

  transition:
    border-color 0.15s ease,
    box-shadow 0.15s ease;
}

.search-bar:focus-within {
  border-color: var(--pos-accent);
  box-shadow: 0 0 0 3px var(--pos-accent-soft);
}

.search-icon {
  flex-shrink: 0;
  pointer-events: none;
  margin-right: 12px;

  color: var(--pos-text-muted);
  font-size: 26px;
  line-height: 1;
}

input {
  width: 100%;
  min-width: 0;
  min-height: 56px;
  padding: 0 16px 0 0;

  border: none;
  outline: none;

  background: transparent;

  color: var(--pos-text);
  font-size: 18px;
}

input::placeholder {
  color: var(--pos-text-muted);
}

input::-webkit-search-cancel-button {
  cursor: pointer;
}

@media (max-width: 599px) {
  .search-bar {
    min-height: 52px;
    padding: 0 0 0 12px;
  }

  .search-icon {
    margin-right: 9px;
    font-size: 22px;
  }

  input {
    min-height: 50px;
    padding-right: 12px;
    font-size: 16px;
  }
}
</style>
