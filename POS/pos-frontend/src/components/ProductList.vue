<script setup>
import { computed } from 'vue'
import { RecycleScroller } from 'vue-virtual-scroller'

import 'vue-virtual-scroller/dist/vue-virtual-scroller.css'

import LoadingSpinner from './LoadingSpinner.vue'

/* ========================================
   PROPS
======================================== */

const props = defineProps({
  /*
   * Item yang sudah dimuat dari API.
   *
   * Contoh:
   *
   * page 1 → 30 item
   * page 2 → 60 item
   * page 3 → 90 item
   */

  items: {
    type: Array,
    required: true,
  },

  /*
   * Apakah API masih memiliki page
   * berikutnya.
   */

  hasMore: {
    type: Boolean,
    default: false,
  },

  /*
   * Apakah sedang mengambil page
   * berikutnya dari API.
   */

  loading: {
    type: Boolean,
    default: false,
  },

  selectionDisabled: {
    type: Boolean,
    default: false,
  },
})

/* ========================================
   EVENTS
======================================== */

const emit = defineEmits(['add-to-cart', 'load-more'])


/* ========================================
   PRODUCT DATA
======================================== */

const products = computed(() => {
  return props.items.map((item, index) => ({
    ...item,

    /*
     * ID khusus RecycleScroller.
     *
     * Harus unik.
     */

    _virtualId: `${item.item_name}-${item.variant || 'N/A'}-${index}`,
  }))
})

/* ========================================
   PRICE
======================================== */

function formatPrice(price) {
  return new Intl.NumberFormat('id-ID', {
    style: 'currency',

    currency: 'IDR',

    maximumFractionDigits: 0,
  }).format(Number(price))
}

/* ========================================
   STOCK STATUS
======================================== */

function getStockStatus(item) {
  /*
   * Jika stock_alert NULL,
   * tidak memberikan warning.
   */

  if (item.stock_alert === null) {
    return 'normal'
  }

  const difference = Number(item.stock) - Number(item.stock_alert)

  /*
   * Critical
   */

  if (Number(item.stock) <= Number(item.stock_alert)) {
    return 'critical'
  }

  /*
   * Warning
   */

  if (difference <= 10) {
    return 'warning'
  }

  /*
   * Normal
   */

  return 'normal'
}

/*
 * Stok 0 atau minus tetap boleh dijual (keputusan pemilik, diterapkan
 * 2026-10-17): barangnya hanya ditandai, tidak dikunci. Stok minus diperiksa
 * pemilik di master.
 */
function isOutOfStock(item) {
  return Number(item.stock) <= 0
}

/*
 * Label satu baris "nama - varian" untuk daftar katalog. Varian kosong atau
 * 'N/A' berarti barang tidak punya varian: tampilkan namanya saja tanpa
 * pemisah menggantung.
 */

function productLabel(item) {
  if (item.variant && item.variant !== 'N/A') {
    return `${item.item_name} - ${item.variant}`
  }

  return item.item_name
}

/* ========================================
   LOAD MORE
======================================== */

function loadMore() {
  /*
   * Jangan mengirim request baru
   * jika sedang loading.
   */

  if (props.loading) {
    return
  }

  /*
   * Jangan request jika API sudah
   * tidak memiliki page berikutnya.
   */

  if (!props.hasMore) {
    return
  }

  emit('load-more')
}
</script>

<template>
  <div class="product-container">
    <!-- ==================================
         MEMUAT DAFTAR ITEM

         Page pertama belum datang: tanpa penanda ini layarnya kosong dan
         terlihat seperti katalog yang tidak punya barang.
    =================================== -->

    <div v-if="props.loading && props.items.length === 0" class="product-loading">
      <LoadingSpinner label="Memuat daftar item..." />
    </div>

    <!-- ==================================
         VIRTUALIZED PRODUCT LIST
    =================================== -->

    <RecycleScroller
      v-else
      class="product-scroller"
      :items="products"
      :item-size="76"
      key-field="_virtualId"
      :buffer="400"
    >
      <!-- ==================================
           PRODUCT
      =================================== -->

      <template #default="{ item }">
        <div class="product-item-wrapper">
          <button
            type="button"
            class="product-item"
            :disabled="props.selectionDisabled"
            :title="[item.item_name, item.variant !== 'N/A' ? item.variant : ''].filter(Boolean).join(' — ')"
            :class="{ 'out-of-stock': isOutOfStock(item) }"
            :aria-disabled="props.selectionDisabled"
            @click="!props.selectionDisabled && emit('add-to-cart', item)"
          >
            <!-- Nama + varian dalam satu baris: "nama - varian". -->

            <span class="product-info">
              <span class="product-name">{{ productLabel(item) }}</span>
              <!-- Letak barang (kode rak dari master), kalau sudah diisi. -->
              <span v-if="item.rak" class="product-rak">Rak {{ item.rak }}</span>
            </span>

            <!--
              Harga + stok saja. Harga floor sengaja tidak ditampilkan di daftar
              item (permintaan pemilik, 2026-09-29): batas bawah harga hanya
              muncul di keranjang dalam bentuk kode selisih.
            -->

            <span class="product-details">
              <strong>
                {{ item.price == null ? 'Harga tidak tersedia' : formatPrice(item.price) }}
              </strong>

              <span class="stock" :class="getStockStatus(item)">
                Stok
                {{ item.stock }}
              </span>
            </span>
          </button>
        </div>
      </template>

      <!-- ==================================
           LOAD MORE
      =================================== -->

      <template #after>
        <div v-if="props.hasMore || props.loading" class="load-more-container">
          <button
            type="button"
            class="load-more-button"
            :disabled="props.loading"
            @click="loadMore"
          >
            <LoadingSpinner v-if="props.loading" compact />

            <span v-else> Muat lebih banyak </span>
          </button>
        </div>

        <!-- Semua data sudah dimuat -->

        <div v-else-if="props.items.length > 0" class="end-of-list">
          Semua produk sudah ditampilkan
        </div>
      </template>
    </RecycleScroller>
  </div>
</template>

<style scoped>
/* ========================================
   PRODUCT CONTAINER
======================================== */

/*
 * Penanda muat memenuhi tinggi kolom katalog supaya cincinnya duduk di tengah
 * area yang masih kosong, bukan menempel di atas.
 */
.product-loading {
  display: flex;

  flex: 1;

  align-items: center;

  justify-content: center;
}

.product-container {
  display: flex;

  flex-direction: column;

  width: 100%;

  height: 100%;

  min-height: 0;

  overflow: hidden;
}

/* ========================================
   VIRTUAL SCROLLER
======================================== */

.product-scroller {
  flex: 1;

  width: 100%;

  min-height: 0;
}

/* ========================================
   PRODUCT ITEM WRAPPER

   item-size = 76px
   wrapper    = 76px
======================================== */

.product-item-wrapper {
  box-sizing: border-box;

  width: 100%;

  height: 76px;

  padding-bottom: 6px;
}

/* ========================================
   PRODUCT ITEM

   76px total
   - 6px gap
   = 70px actual card
======================================== */

.product-item {
  display: flex;

  justify-content: space-between;

  align-items: center;

  width: 100%;

  height: 70px;

  padding: 6px 14px;

  box-sizing: border-box;

  background: var(--pos-surface);

  border: 1px solid var(--pos-border);

  border-radius: 8px;

  text-align: left;

  cursor: pointer;

  transition:
    background-color 0.15s ease,
    border-color 0.15s ease;
}

.product-item:hover:not(:disabled) {
  background: var(--pos-hover);

  border-color: var(--pos-text-muted);
}

.product-item[aria-disabled='true'] {
  opacity: 0.6;
  cursor: not-allowed;
}

.product-rak {
  display: block;
  margin-top: 2px;
  color: var(--pos-text-muted);
  font-size: 13px;
  font-weight: 500;
}

/* Stok 0/minus: tetap bisa dipilih, hanya diberi tanda. */
.product-item.out-of-stock {
  opacity: 0.75;
}

/* ========================================
   PRODUCT NAME
======================================== */

.product-info {
  min-width: 0;
}

.product-name {
  margin: 0;

  color: var(--pos-text);

  font-size: 16px;

  font-weight: 600;

  line-height: 1.25;

  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow-wrap: anywhere;
}

/* ========================================
   PRICE + STOCK
======================================== */

.product-details {
  display: flex;

  flex-direction: column;

  align-items: flex-end;

  gap: 4px;

  margin-left: 14px;

  flex-shrink: 0;
}

.product-details strong {
  color: var(--pos-text);

  font-size: 17px;

  font-weight: 700;
}

.product-details > .stock {
  padding: 1px 8px;

  border: 1px solid transparent;
  border-radius: 999px;

  color: var(--pos-text-muted);

  font-size: 14px;

  font-weight: 600;
}

/* ========================================
   STOCK STATUS
======================================== */

.stock.warning {
  background: var(--pos-lowstock-bg);
  border-color: var(--pos-lowstock-bg);
  color: var(--pos-lowstock-text);
}

.stock.critical {
  background: var(--pos-outstock-bg);
  border-color: var(--pos-outstock-bg);
  color: var(--pos-outstock-text);
}

/* ========================================
   LOAD MORE
======================================== */

.load-more-container {
  display: flex;

  justify-content: center;

  align-items: center;

  width: 100%;

  padding: 12px 0;
}

.load-more-button {
  min-height: 46px;

  padding: 0 24px;

  color: var(--pos-text);

  background: var(--pos-surface);

  border: 1px solid var(--pos-border);

  border-radius: 8px;

  font-size: 15px;

  font-weight: 600;

  cursor: pointer;

  transition:
    background-color 0.15s ease,
    border-color 0.15s ease,
    opacity 0.15s ease;
}

.load-more-button:hover:not(:disabled) {
  background: var(--pos-hover);

  border-color: var(--pos-text-muted);
}

.load-more-button:active:not(:disabled) {
  transform: translateY(1px);
}

.load-more-button:disabled {
  opacity: 0.6;

  cursor: wait;
}

/* ========================================
   END OF LIST
======================================== */

.end-of-list {
  padding: 12px 0;

  color: var(--pos-text-muted);

  text-align: center;

  font-size: 14px;
}

/* ========================================
   LAYAR SEMPIT
======================================== */

@media (max-width: 599px) {
  .product-item {
    padding: 6px 10px;
  }

  .product-name {
    font-size: 14px;
  }

  .product-details {
    margin-left: 8px;
  }

  .product-details strong {
    font-size: 15px;
  }

  .product-details > .stock {
    font-size: 12px;
  }
}
</style>
