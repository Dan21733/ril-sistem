# Spec: Antrean Multi-Nota Offline + Riwayat Gabungan (Opsi A, Slice 1)

Tanggal: 2026-10-09
Status: menunggu review pemilik
Lingkup: slice pertama opsi A — antrean lokal + riwayat gabungan. Batch sync
PIN-per-nota dan keluar-dari-proxy (opsi B) explicitly di luar lingkup.

## 1. Latar dan keputusan pemilik

- Offline = backend tak terjangkau; server penyaji frontend dianggap hidup;
  PWA keluar scope (keputusan 2026-10-09, ARCHITECTURE.md + OFFLINE_SYNC_DESIGN.md).
- Slice ini: kasir bisa jualan beruntun saat offline; kirim ke server menyusul.
- Jatah nomor habis saat offline → tahan penjualan baru (keputusan 2026-10-09).
- Riwayat: satu daftar + lencana status (keputusan 2026-10-09).

## 2. Perubahan perilaku

### 2.1 Penguncian checkout (§1 desain)

- Status `retryable` / `unknown` / `draft` TIDAK lagi mengunci keranjang.
  `isEditingLocked` (CartList.vue) hanya untuk
  `restoring` / `submitting` / `review` / `conflict` (+ cooldown PIN berjalan).
- Notifikasi antrean menetap selama ada nota belum-sync: "N nota belum
  tersinkron ke server" + ajakan kirim ulang tertua dulu, beserta tombol
  "Kirim semua (N)". **Sejak 2026-10-09 tinggal di modal Riwayat Nota**
  (permintaan pemilik: baner di kolom keranjang memakan tempat), ditambah
  lencana jumlah di tombol "Riwayat Nota".
- `review` / `conflict` tetap mengunci penjualan baru (butuh manusia).

### 2.2 Antrean (§2 desain)

- Reuse store Dexie `transactions` + indeks `sync_status` yang sudah ada.
  Tanpa migrasi skema (db.js tetap versi 6).
- Util baru `listQueuedTransactions()` (baca saja): filter `sync_status` IN
  (`draft`, `pending`, `syncing`, `unknown`), urut tertua dulu. Status UI
  `retryable` bukan nilai Dexie — ia dipetakan dari baris-baris itu.
- Kirim ulang individual per nota via endpoint yang sudah ada
  (`POST /api/transactions/` + PIN asli tersimpan). FIFO dihimbau di UI,
  tidak dipaksa di slice ini.

### 2.3 Riwayat gabungan (§3 desain)

- TransactionHistory.vue menggabung: baris server (apa adanya) + baris lokal
  belum-sync dengan lencana `Belum terkirim` / `Hasil belum diketahui`.
- Baris lokal: tidak dihitung dalam total server, tidak diklaim sebagai bukti
  sah server; klik membuka rincian dari payload lokal (barang, total, kasir).
- Saat fetch server gagal (offline), daftar lokal tetap tampil + pesan
  server yang jujur (menggantikan `Failed to fetch` kosong).

### 2.4 Nomor habis (§4 desain)

- `sequence_ranges` habis saat offline → penjualan baru ditahan, pesan jelas
  ("jatah nomor habis, sambungkan kembali"), keranjang utuh, antrean aman.
- Tidak ada nomor karangan; tidak ada `transaction_id` null baru dari jalur ini.

### 2.5 Yang TIDAK berubah

- Gerbang PIN sebelum segel, cooldown PIN, salinan katalog: tidak tersentuh.
- Kontrak API, skema server, skema Dexie: tidak tersentuh.
- Cetak tetap antre via print_jobs seperti hari ini.

## 3. Berkas tersentuh

- `POS/pos-frontend/src/App.vue` — status/locking, lencana antrean di tombol
  "Riwayat Nota".
- `POS/pos-frontend/src/components/CartList.vue` — `isEditingLocked`.
- `POS/pos-frontend/src/components/TransactionHistory.vue` — notifikasi antrean +
  tombol kirim (sejak 2026-10-09, menggantikan banner `CartList.vue`).
- `POS/pos-frontend/src/components/TransactionHistory.vue` — merge + lencana.
- `POS/pos-frontend/src/services/storage/transactionQueue.js` (baru) —
  `listQueuedTransactions()` baca-saja.
- `POS/pos-frontend/tests/` — kasus harness App.vue baru (§4).
- `POS/pos-backend/OFFLINE_SYNC_DESIGN.md` — catat implementasi slice ini.

## 4. Kriteria lulus slice (§5 desain)

1. API dimatikan → 3 nota beruntun (PIN dikenal lokal) → ketiganya antre
   dengan `sync_id` unik, keranjang bersih tiap selesai.
2. Riwayat memetakan ketiganya sebagai baris lokal berlabel benar;
   baris server tidak berubah.
3. Jatah nomor habis → penjualan ke-4 ditahan dengan pesan, antrean 3 tetap utuh.
4. Seluruh 96+ tes lama tetap hijau + tes baru hijau.
5. Tidak ada migrasi Dexie; DB lama versi 6 tetap terbuka tanpa upgrade.

## 5. Risiko jujur

- Retry manual per nota atas hasil ambigu tetap bisa duplikat di sisi kasir
  bila nota sebenarnya sudah tercatat — mitigasi: kirim ulang memakai
  `sync_id` sama (idempoten), tidak pernah membuat nota pengganti.
- FIFO hanya himbauan di slice ini; urutan kirim acak antar-nota ditanggung
  idempotensi server, bukan urutan.
