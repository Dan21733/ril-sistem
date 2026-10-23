# Handoff Frontend → Backend: Pendaftaran Device Otomatis

**Ditinjau dari kode: 2026-10-01.** Rujukan:
[handoff frontend](FRONTEND_HANDOFF.md),
[arsitektur](../ARCHITECTURE.md), dan
[deviceApi.js](pos-frontend/src/services/api/deviceApi.js).
Kontrak backend renewal sudah ada; pemanggil frontend masih registrasi awal.

## Apa yang sudah diimplementasikan frontend (2026-09-27)

Keputusan pemilik: perangkat kasir berada di **jaringan yang di-whitelist**, jadi
pendaftaran device tidak boleh meminta manusia mengetik token. Frontend kini
melakukan **auto-registrasi**:

1. Saat aplikasi dibuka, frontend memeriksa Dexie `device` (id 1).
2. Jika kosong dan perangkat online, frontend memanggil
   `POST /api/devices/register/` **tanpa header rahasia dari browser**.
3. Header `X-Device-Registration-Token` disuntikkan **di sisi dev server**
   (Vite proxy, dari `DEVICE_REGISTRATION_TOKEN` — nama tanpa prefix `VITE_`
   agar tidak berpotensi terekspos ke browser — di `.env`/`.env.local`; file
   kini ter-ignore di frontend, tidak pernah masuk bundle maupun git).
4. `{device_id, device_code, device_token}` disimpan lokal; `device_token`
   dipakai sebagai `Authorization: Device <token>` di semua endpoint transaksi.
5. Jika auto-registrasi gagal (server mati, 401 token salah, 409 kode habis),
   modal status menampilkan alasan + tombol "Coba Lagi". Modal bisa ditutup;
   nota pending dapat tersimpan lokal, tetapi transaksi online tidak diterima
   sebelum device terdaftar. Ini bukan jaminan lifecycle banyak nota offline
   sudah lengkap.
6. Guard single-flight mencegah dua registrasi paralel (mount + event online +
   checkout bisa nyaris bersamaan); auto-retry juga berjalan saat event
   `online` kembali.

Flowchart: [sumber Mermaid](docs/device_auto_registration_flow.mmd) dan
[pratinjau HTML](docs/device_auto_registration_flow.html). Keduanya menjelaskan
alur registrasi awal, bukan bukti frontend sudah melakukan renewal.

## Keputusan pemilik & tindak lanjut backend/frontend

### 1. Registrasi ulang device (keputusan: perbarui device yang sama)

Tanpa identitas device pada request, `POST /api/devices/register/` membuat
record baru dan mengalokasikan kode T01–T99. Pemilik memilih agar pembaruan
token mempertahankan `device_id`/`device_code` yang sama.

Backend kini mendukung dua bentuk request:

- Registrasi awal: body kosong `{}` → `201 Created` dan membuat device baru.
- Pembaruan: `{ "device_id": "<UUID>", "device_token": "<token device lama>" }`
  → `200 OK`, device/kode tetap sama, token baru diterbitkan. Header rahasia
  `X-Device-Registration-Token` tetap wajib pada keduanya. Backend menerima
  token lama yang ditandatangani paling lama **60 hari sejak diterbitkan**
  hanya untuk alur pembaruan; autentikasi endpoint transaksi tetap memakai TTL
  normal 30 hari. Token yang lebih tua/tidak cocok menghasilkan 401.

**Tindak lanjut frontend:** simpan body lama `device_id` + `device_token` dan
kirimkannya pada registrasi ulang setelah 401 token kedaluwarsa. Client
registrasi saat ini masih mengirim body kosong, jadi re-registration dengan
mempertahankan device belum aktif di frontend. Jika token sudah melewati batas
60 hari dan tidak dapat diperbarui, registrasi awal akan mencoba membuat
device baru dan tetap dapat gagal 409 bila kode T01–T99 sudah penuh; lifecycle
pensiun/daur ulang kode belum diputuskan.

### 2. Deployment produksi: header rahasia tetap di luar browser

Keputusan pemilik: produksi menggunakan frontend **same-origin** dengan backend
atau reverse proxy tepercaya yang menyisipkan header hanya untuk path
`/api/devices/register/`. Header `X-Device-Registration-Token` tidak boleh
masuk bundle atau dikirim browser-side. Jika kelak perlu registrasi langsung
lintas-origin dari browser, backend perlu mengizinkan header itu di
`CORS_ALLOW_HEADERS` dan trust model secret perlu ditinjau ulang; preflight
saat ini tidak mengizinkannya.

### 3. Trust model: token otoritatif, whitelist hanya perimeter

Keputusan pemilik: backend tetap memvalidasi token registrasi sebagai kontrol
otorisasi. Whitelist jaringan adalah perimeter tambahan/operasional, bukan
pengganti token; tidak ada allowlist IP khusus di backend saat ini.

### 4. Kontrak lain dan status

- Respons tetap `{device_id, device_code, device_token}`; registrasi awal `201`,
  pembaruan device yang sama `200`.
- Frontend tidak menentukan/mengirim `worker_id` untuk transaksi. Checkout
  individual kini menyimpan PIN ketikan asli pada record nota IndexedDB sampai
  server mengonfirmasi sukses atau penolakan nonfinal pertama; hasil ambigu
  mempertahankannya agar retry tidak berganti kasir. Ini rahasia sensitif,
  bukan data terenkripsi; lihat [desain offline](pos-backend/OFFLINE_SYNC_DESIGN.md).
  PIN directory worker terpisah berisi bcrypt/legacy plaintext untuk pencocokan lokal.
- Setelah transaksi menerima 401, frontend menjaga `sync_id`, payload dan PIN
  asli. Kredensial device saat ini di-auto-registrasi (renewal token lama belum
  dipakai UI), lalu nota yang sama dapat dikirim ulang.
- Produksi masih perlu konfigurasi/deployment same-origin atau reverse proxy;
  keputusan arsitektur ini bukan bukti deployment production sudah tersedia.
