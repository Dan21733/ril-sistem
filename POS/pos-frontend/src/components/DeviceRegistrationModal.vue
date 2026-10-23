<script setup>
import { computed, ref } from 'vue'
import vModalFocus from '../directives/modalFocus'

import { registerOrRenewDevice } from '../services/api/deviceApi'
import deviceRepository from '../services/storage/deviceRepository'

const props = defineProps({
  visible: {
    type: Boolean,
    default: false,
  },
  cancellable: {
    type: Boolean,
    default: false,
  },
  /*
   * Status auto-registrasi: 'idle' | 'registering' | 'failed'
   */
  registrationState: {
    type: String,
    default: 'idle',
  },
  errorMessage: {
    type: String,
    default: '',
  },
})

const emit = defineEmits(['registered', 'dismiss'])

const isRetrying = ref(false)

const retryError = ref('')

const isBusy = computed(() => props.registrationState === 'registering' || isRetrying.value)

const shownError = computed(() => retryError.value || props.errorMessage)

const isTransientError = computed(
  () => /jaringan|koneksi|HTTP 5\d\d/i.test(shownError.value),
)

async function retry() {
  if (isBusy.value) {
    return
  }

  isRetrying.value = true
  retryError.value = ''

  try {
    /* Sama seperti registrasi otomatis: renewal dulu memakai kredensial
     * tersimpan, baru registrasi baru bila renewal ditolak 401. */
    let stored = null
    try {
      stored = await deviceRepository.getDevice()
    } catch {
      stored = null
    }
    const device = await registerOrRenewDevice(stored)

    if (!device?.device_id || !device?.device_code || !device?.device_token) {
      retryError.value = 'Respons registrasi tidak lengkap. Coba lagi.'
      return
    }

    await deviceRepository.saveDevice({
      device_id: device.device_id,
      device_code: device.device_code,
      device_token: device.device_token,
    })

    emit('registered', device)
  } catch (error) {
    if (error.status === 401) {
      retryError.value =
        'Token registrasi belum tersedia di server pengembangan. Isi DEVICE_REGISTRATION_TOKEN di file .env frontend lalu jalankan ulang dev server.'
    } else if (error.status === 409) {
      retryError.value = 'Semua kode device T01-T99 sudah terpakai. Hubungi pemilik.'
    } else {
      retryError.value = error.message || 'Registrasi device gagal. Periksa koneksi lalu coba lagi.'
    }
  } finally {
    isRetrying.value = false
  }
}

function dismiss() {
  emit('dismiss')
}
</script>

<template>
  <div
    v-if="props.visible"
    v-modal-focus="{ onEscape: () => { if (props.cancellable && !isBusy) dismiss() } }"
    class="modal-overlay"
    @click.self="dismiss"
  >
    <div class="registration-modal" role="dialog" aria-modal="true" aria-labelledby="registration-title" tabindex="-1">
      <div class="modal-header">
        <h2 id="registration-title">Registrasi Device</h2>

        <p v-if="isBusy">
          Mendaftarkan perangkat ini otomatis ke server...
        </p>
        <p v-else>
          Perangkat ini belum terdaftar. Pendaftaran berjalan otomatis lewat jaringan
          toko yang sudah di-whitelist; jika gagal, coba lagi atau hubungi pemilik.
        </p>
      </div>

      <div v-if="shownError" class="modal-body">
        <p class="registration-error" role="alert">
          {{ shownError }}
        </p>
        <p v-if="!isTransientError" class="registration-hint">
          Pastikan backend berjalan di port 8000 dan dev server ini dijalankan ulang
          setelah .env.local diisi.
        </p>
      </div>

      <div class="modal-footer">
        <button
          v-if="props.cancellable"
          type="button"
          class="cancel-button"
          :disabled="isBusy"
          @click="dismiss"
        >
          NANTI
        </button>

        <button
          type="button"
          class="confirm-button"
          :disabled="isBusy"
          @click="retry"
        >
          {{ isBusy ? 'MENDAFTARKAN...' : 'COBA LAGI' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-overlay {
  position: fixed;

  inset: 0;

  z-index: 2000;

  display: flex;

  justify-content: center;

  align-items: center;

  padding: 20px;

  background: var(--pos-overlay);
}

.registration-modal {
  width: min(480px, 100%);

  padding: 24px;

  box-sizing: border-box;

  background: var(--pos-surface);

  border: 1px solid var(--pos-border);

  border-radius: 12px;

  box-shadow: var(--pos-shadow);
}

.modal-header h2 {
  margin: 0 0 8px;

  color: var(--pos-text);

  font-size: 22px;

  font-weight: 700;
}

.modal-header p {
  margin: 0;

  color: var(--pos-text-muted);

  font-size: 15px;

  line-height: 1.5;
}

.modal-body {
  margin-top: 14px;
}

.registration-error {
  margin: 0;

  padding: 12px 14px;

  border: 1px solid var(--pos-danger);

  border-radius: 10px;

  background: var(--pos-outstock-bg);

  color: var(--pos-outstock-text);

  font-size: 14px;
}

.registration-hint {
  margin: 8px 0 0;

  color: var(--pos-text-muted);

  font-size: 13px;

  line-height: 1.5;
}

.modal-footer {
  display: flex;

  justify-content: flex-end;

  gap: 10px;

  margin-top: 18px;
}

.cancel-button {
  min-height: 52px;

  padding: 0 20px;

  border: 1px solid var(--pos-border);

  border-radius: 10px;

  background: var(--pos-surface);

  color: var(--pos-text);

  font-size: 15px;

  font-weight: 600;

  cursor: pointer;
}

.confirm-button {
  min-height: 52px;

  padding: 0 22px;

  border: 1px solid var(--pos-accent);

  border-radius: 10px;

  background: var(--pos-accent);

  color: var(--pos-accent-contrast);

  font-size: 15px;

  font-weight: 700;

  cursor: pointer;
}

.confirm-button:disabled {
  opacity: 0.4;

  cursor: not-allowed;
}
</style>
