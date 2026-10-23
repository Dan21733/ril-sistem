import { fileURLToPath, URL } from 'node:url'

import process from 'node:process'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  /*
   * Kredensial device hanya hidup di sisi Node (dev server), tidak pernah
   * masuk bundle browser. Token registrasi dibaca dari .env.local — file itu
   * ter-ignore (*.local) dan tidak boleh di-commit.
   */
  const env = loadEnv(mode, process.cwd(), '')

  /*
   * DEVICE_REGISTRATION_TOKEN (tanpa prefix) dipilih: variabel berprefix
   * VITE_ berpotensi terekspos ke browser lewat import.meta.env. Nama lama
   * tetap didukung agar setup yang sudah ada tidak rusak.
   */
  const deviceRegistrationToken =
    env.DEVICE_REGISTRATION_TOKEN || env.VITE_DEVICE_REGISTRATION_TOKEN || ''

  /*
   * Print bridge (PRINT_PATH_DESIGN.md §4). POS mengirim byte ESC/POS ke path
   * relatif `/print`, dan proxy ini yang meneruskannya ke bridge di LAN. Dipilih
   * same-origin supaya tablet tidak kena mixed content dan bridge tidak perlu
   * menambahkan header CORS sendiri. Targetnya bukan rahasia, tapi juga tidak
   * perlu sampai ke bundle: cukup variabel sisi Node.
   */
  const printBridgeTarget = env.PRINT_BRIDGE_TARGET || 'http://127.0.0.1:9101'

  /*
   * Salah alamat di sini mahal waktunya: /print harus diarahkan ke penghubung
   * cetak (HTTP, port 9101), bukan ke printer (raw ESC/POS, port 9100). Port
   * 9100 di target hampir selalu berarti alamat printer yang tertulis ke situ,
   * jadi diperingatkan saat dev server start - bukan dibiarkan muncul sebagai
   * "penghubung cetak tidak menjawab" di layar kasir.
   */
  if (printBridgeTarget) {
    try {
      if (new URL(printBridgeTarget).port === '9100') {
        console.warn(
          '[print] PRINT_BRIDGE_TARGET memakai port 9100 - itu port printer, bukan ' +
            'penghubung cetak. Isi alamat komputer yang menjalankan bridge.py ' +
            '(umumnya port 9101).',
        )
      }
    } catch {
      console.warn(
        `[print] PRINT_BRIDGE_TARGET tidak terbaca sebagai URL: ${printBridgeTarget}. ` +
          'Contoh yang benar: http://127.0.0.1:9101',
      )
    }
  }

  /*
   * Semua request API lewat proxy ini (same-origin, bebas daftar CORS
   * backend). Saat token registrasi tersedia, proxy menyuntikkan header
   * X-Device-Registration-Token untuk /api/devices/register/ saja, jadi
   * pendaftaran device berjalan otomatis: mesin kasir ada di jaringan yang
   * di-whitelist pemilik dan tidak ada secret yang diketik manusia.
   *
   * Dipakai dev server DAN preview server (`npm run preview`): toko
   * menjalankan hasil `npm run build`, bukan dev server, tapi tablet tetap
   * menyapa same-origin /api dan /print — jadi proxy harus identik di
   * keduanya. Jangan tambah proxy di satu sisi saja.
   */
  const storeProxy = {
    '/api/devices/register/': {
      target: 'http://localhost:8000',
      changeOrigin: true,
      headers: deviceRegistrationToken
        ? { 'X-Device-Registration-Token': deviceRegistrationToken }
        : {},
    },
    '/api': {
      target: 'http://localhost:8000',
      changeOrigin: true,
    },
    '/print': {
      target: printBridgeTarget,
      changeOrigin: true,
    },
  }

  return {
    plugins: [
      vue(),
      vueDevTools(),
    ],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      proxy: storeProxy,
    },
    preview: {
      proxy: storeProxy,
    },
  }
})
