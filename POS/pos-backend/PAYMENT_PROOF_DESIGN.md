# Rancangan Penyimpanan Bukti Transfer (payment proof)

**Status: KEPUTUSAN SEBAGIAN — tujuh keputusan pemilik sudah dikunci (§3, §4,
§7.1–§7.3, dan §7.14–§7.15; semuanya 2026-09-28), dan tabel [SKEMA] di §5 sudah
dibuat di database setelah izin diberikan. Endpoint unggah (§6) sudah
diimplementasikan beserta tesnya. Sisi klien (§7) sudah diimplementasikan sesuai
rancangan di bagian ini dan diverifikasi di browser untuk kompresi, Dexie v2,
urutan nota-dulu, serta pemetaan 401/404/409/413/5xx — tetapi **belum diuji di
perangkat kasir asli**, dan `HEIC` dari galeri iOS tetap risiko yang belum
terbukti. Cara membaca/menampilkan bukti dari server belum ada dan masih
menunggu keputusan retensi/hak akses (§8).**

**Tinjauan sumber 2026-10-01:** capture, Dexie, upload, dan penanda bukti tertunda
ada di kode. Tidak ada pengujian browser/perangkat baru pada tinjauan ini;
catatan pengukuran sebelumnya di bawah adalah historis, bukan benchmark ulang.

Perubahan 2026-09-28 (pemilik): bentuk key di storage menjadi datar
([KEPUTUSAN-7], §7.14) dan klien berhenti mengompres foto yang tidak perlu,
lalu menargetkan 4,5 MB untuk yang perlu ([KEPUTUSAN-8], §7.15). Keduanya
menggantikan angka yang tertulis di versi pertama dokumen ini; barisnya sudah
diperbarui, bukan ditambahkan sebagai catatan kaki, supaya tidak ada dua angka
berbeda di satu dokumen.

Dokumen ini mencatat penyelesaian masalah awal: foto dahulu hanya berada di
memori tab. Kini blob dipersist lokal dan upload menyusul nota server; akses
baca server, retensi, dan produksi masih terbuka.

Aturan main yang mengikat dokumen ini:

- [Aturan repo](../../AGENTS.md) / [POS](../AGENTS.md) mengatur cara kerja;
  [arsitektur](../../ARCHITECTURE.md) memiliki keputusan bisnis;
  [desain offline](OFFLINE_SYNC_DESIGN.md) memiliki batas sync/PIN. Dokumen ini
  memiliki detail proof dan tidak mengubah keputusan atau kontrak mereka.
- Semua model POS `managed = False` → setiap perubahan skema butuh izin
  eksplisit; bagian yang menyentuh skema ditandai [SKEMA].
- Nota bersifat append-only (nota selesai tidak pernah diedit/dihapus) → bukti
  transfer mengikuti aturan yang sama: tidak pernah ditimpa, hanya ditambah.
- Offline-first adalah requirement, belum fitur end-to-end. Bukti tersimpan
  lokal adalah fondasi offline, bukan bukti seluruh checkout/sync telah lengkap.
- Yang disimpan di browser tidak boleh memuat data terlarang (`cost_amount`,
  margin, nilai stok, data supplier).

---

## 1. Masalah awal sebelum implementasi (riwayat 2026-09-28)

Butir berikut adalah keadaan **sebelum** pipeline proof dipasang, bukan status
kode saat ini. Checkout sekarang tiga langkah; angka STEP 6 di catatan lama
merujuk UI terdahulu.

- `CartList.vue` STEP 6 meminta foto bukti transfer (kamera + galeri) dan
  `completeTransaction()` menolak melanjutkan bila `bank_transfer` belum
  punya file.
- File hanya hidup sebagai `transferProofFile` (objek `File`) dan
  `transferProofPreview` (object URL) di memori komponen.
- `emit('complete-transaction')` **tidak** menyertakan bukti sama sekali.
- `paymentProofRepository.js` tidak diimpor oleh komponen mana pun; tabel
  Dexie `payment_proofs` (`proof_id, sync_id`) selalu kosong.
- Backend tidak punya `MEDIA_ROOT`/`MEDIA_URL`, `ImageField`/`FileField`,
  maupun endpoint upload.

Akibatnya: kasir dipaksa memotret bukti, lalu gambarnya dibuang saat komponen
dibongkar atau halaman di-reload. Tidak ada satu pun salinan — lokal maupun
server.

## 2. Hasil yang dituju

1. Bukti transfer tersimpan permanen dan tidak bisa hilang saat reload.
2. Penjualan transfer tetap bisa diselesaikan saat offline.
3. Struktur hari ini (development) menyediakan ruang untuk pindah ke
   penyimpanan produksi tanpa menulis ulang model, kontrak, atau data.

## 3. [KEPUTUSAN-1] SELESAI (2026-09-28): storage

- **Development**: filesystem lokal lewat storage backend bawaan Django; file
  di `POS/pos-backend/media/payment_proofs/` (ter-gitignore).
- **Produksi**: **belum diputuskan — ditunda.** Deployment produksi belum ada
  sama sekali (tidak ada Dockerfile/nginx/systemd), jadi keputusan storage
  produksi ditunda sampai konteksnya nyata.

Syarat yang mengikat kedua sisi — inilah "ruang untuk produksi" yang diminta:

1. **Satu pintu akses.** Hanya `pos/payment_proof_storage.py` yang tahu cara
   menyusun path di disk dan cara menghasilkan URL. Tidak ada view, model,
   atau serializer yang menyusun path sendiri.
2. **Backend dipilih lewat setting, bukan import langsung.** Produksi cukup
   mengganti `STORAGES["default"]` (mis. ke object storage S3-compatible
   dengan presigned URL) tanpa menyentuh view atau model.
3. **Yang disimpan di database adalah key objek, bukan URL.** Contoh:
   `payment_proofs/2026/09/<proof_id>.jpg` — datar, tanpa subfolder per nota
   ([KEPUTUSAN-7], §7.14). URL dibuat saat dibaca.
   Dengan begitu pindah backend tidak memerlukan migrasi data — ini yang
   membuat keputusan produksi bisa ditunda dengan aman.
4. **Key dibuat server**, tidak pernah dari nama file kiriman klien.
5. Bila produksi kelak memakai object storage: file privat, URL berumur
   pendek, bucket tidak boleh public.

## 4. [KEPUTUSAN-2] SELESAI (2026-09-28): nota tanpa bukti

Nota `bank_transfer` yang tersinkron tetapi buktinya belum terkirim
**diterima** dan ditandai **"bukti tertunda"** — bukan ditolak.

Alasan: kegagalan upload tidak boleh membatalkan nota final; foto dikirim
terpisah setelah nota tersimpan. PIN/validasi penjualan tetap berlaku sesuai
[desain offline](OFFLINE_SYNC_DESIGN.md); ini bukan izin menyelesaikan penjualan
transfer tanpa PIN atau mengabaikan gap sync.

Konsekuensi yang harus disadari:

- Gate di UI tetap dipertahankan (kasir tidak bisa menekan BAYAR untuk transfer
  tanpa memotret bukti) — ditegaskan ulang sebagai [KEPUTUSAN-6] (§7.3),
  termasuk saat retry — tetapi otoritas kebenarannya ada di server.
- Server **wajib** punya cara menemukan nota transfer yang buktinya tidak
  pernah datang. Tanpa itu, bukti yang hilang tidak bisa dilacak.

Status "bukti tertunda" **dapat diturunkan dari query**, bukan kolom baru.
Query ini referensi desain/operasional, **bukan endpoint laporan yang sudah
ada**. Penanda UI saat ini berasal dari antrean Dexie perangkat tersebut:

```sql
SELECT t.sync_id, t.transaction_id, t.transaction_time, t.worker_id
FROM transactions t
LEFT JOIN payment_proof p ON p.sync_id = t.sync_id
WHERE t.payment_method = 'bank_transfer'
  AND p.proof_id IS NULL
ORDER BY t.transaction_time;
```

Alasan memilih turunan dibanding kolom status: `payment_method` sudah ada,
jumlahnya kecil pada skala satu toko, dan kolom status baru berarti satu lagi
nilai yang bisa menyimpang dari kenyataan — persis kelas masalah yang sudah
kita temukan di model `managed = False`.

## 5. [SKEMA] Yang dibutuhkan di database — SUDAH DIBUAT (2026-09-28)

**Tabel terpisah, bukan kolom di `transactions`.** Alasannya:

- Satu nota bisa punya lebih dari satu bukti (transfer dipecah dua kali).
- `transaction_id` nullable untuk nota offline, jadi tidak bisa dipakai sebagai
  kunci; kuncinya `sync_id`.
- Sejalan dengan skema Dexie yang sudah ada (`payment_proofs: 'proof_id, sync_id'`).

```sql
CREATE TABLE payment_proof (
    proof_id     uuid        PRIMARY KEY,
    sync_id      uuid        NOT NULL REFERENCES transactions(sync_id),
    object_key   text        NOT NULL,
    content_type varchar(32) NOT NULL,
    byte_size    integer     NOT NULL,
    sha256       varchar(64) NOT NULL,
    captured_at  timestamptz,
    uploaded_at  timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    device_code  varchar(3),
    CONSTRAINT payment_proof_object_key_key UNIQUE (object_key),
    CONSTRAINT payment_proof_byte_size_check
        CHECK (byte_size > 0 AND byte_size <= 5242880),
    CONSTRAINT payment_proof_sha256_check
        CHECK (sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX payment_proof_sync_id_idx ON payment_proof (sync_id);
```

Catatan penting untuk siapa pun yang mengerjakan DDL ini: kolom `NOT NULL`
yang tidak diisi eksplisit oleh ORM pernah membuat
`POST /api/devices/register/` membalas 500 (kolom `updated_at` di
`device_transaction_sequence`). Karena itu model Django untuk tabel ini wajib
memberi nilai Python untuk `uploaded_at` — jangan mengandalkan default DB saja.

`device_code` mencatat device pengunggah. **Belum ada endpoint baca proof**;
policy baca/retensi belum disetujui. Upload mengecek prefix nota bernomor,
tetapi nota null melewati check ini karena `transactions` belum punya device
column. Itu gap ownership, bukan jaminan isolasi perangkat.

## 6. Kontrak unggah

`POST /api/payment-proofs/` — multipart, header `Authorization: Device <token>`.

Field: `proof_id` (uuid, **dibuat klien**), `sync_id` (uuid nota), `file`.

| Status | Arti |
| --- | --- |
| `201` | Bukti baru tersimpan |
| `200` | Idempotent — `proof_id` sama dengan isi (sha256) sama |
| `400` | Tipe/ukuran tidak sah |
| `401` | Device token tidak ada/kedaluwarsa |
| `404` | `sync_id` tidak dikenal atau bukan milik device ini |
| `409` | `proof_id` sudah ada tetapi isinya berbeda |
| `413` | Melebihi batas ukuran |

Aturan:

- `proof_id` dari klien yang membuat endpoint ini idempotent; retry tidak
  pernah menghasilkan bukti ganda.
- Validasi tipe dari **magic bytes**, bukan `Content-Type` kiriman klien.
  Hanya `jpeg`, `png`, `webp`. **Tolak `svg`** — SVG bisa memuat skrip
  (vektor XSS).
- Batas ukuran 5 MB, dan `sha256` dihitung di server.
- `object_key` dibuat server: `payment_proofs/<tahun>/<bulan>/<proof_id>.<ext>`
  ([KEPUTUSAN-7], §7.14). Ekstensi ditentukan dari magic bytes, jadi isi berkas
  dan namanya tidak pernah bisa berbeda.
- Bukti adalah append-only: tidak ada endpoint update maupun delete. Salah
  unggah → unggah bukti baru.

Catatan urutan yang penting: unggah hanya boleh untuk nota yang **sudah ada di
server**. HTTP 400 tidak membuktikan percobaan sebelumnya belum tersimpan.
Nota final/ambigu ditahan review; bukti tetap di perangkat sampai konfirmasi
synced. Rejected pasti nonfinal juga menyimpan bukti, tidak menghapus jejak.

## 7. Sisi klien

Keputusan pemilik 2026-09-28 mengunci capture, penanda tertunda, foto wajib,
key datar, dan kompresi. Pipeline berikut **sudah terpasang**; bagian yang masih
usulan/gap ditandai tersendiri. Nomor STEP 6 adalah sebutan UI lama: foto kini
dipilih di langkah pembayaran checkout tiga langkah.

### 7.1 [KEPUTUSAN-4] SELESAI (2026-09-28): kapan blob disimpan

Bukti disimpan ke Dexie **saat foto dipilih di langkah pembayaran**, bukan saat checkout
ditekan. Konsekuensinya disengaja:

- `proof_id` dibuat klien pada saat itu juga dan blob hasil proses langsung
  masuk IndexedDB, sehingga bukti selamat walau tab ditutup, halaman di-reload,
  atau aplikasi mati sebelum checkout.
- Record bukti hidup lebih dulu daripada notanya: `sync_id` belum ada saat itu,
  jadi kolomnya nullable dan diisi ketika nota dibuat di `completeTransaction()`.
- Preview di UI dibuat dari blob tersimpan, sehingga yang dilihat kasir identik
  dengan yang akan dikirim (bukan foto asli sebelum kompres).
- Mengganti foto yang belum uploaded dapat memakai ulang `proof_id` lokal;
  foto uploaded tidak ditimpa. Foto tanpa nota dibersihkan melalui jalur batal /
  ganti metode bayar; proof tanpa `sync_id` tetap mungkin tertinggal setelah
  aplikasi mati. Jangan menganggap semua blob yatim otomatis direstorasi/dihapus.

### 7.2 [KEPUTUSAN-5] SELESAI (2026-09-28): tampilan bukti tertunda

Penanda berada **di halaman riwayat nota**, bukan layar terpisah:

- `TransactionHistory.vue` menandai nota yang buktinya belum terunggah dan
  menyediakan aksi unggah ulang per nota.
- Modal status checkout di `App.vue` menampilkan kegagalan unggah bukti terpisah
  dari status notanya. Nota dan bukti memang punya nasib berbeda: nota bisa
  `synced` sementara buktinya masih `retryable`.
- Tidak ada layar khusus. Bila jumlah bukti tertunda kelak jadi masalah nyata,
  itu ditinjau ulang dengan data, bukan diramalkan sekarang.

### 7.3 [KEPUTUSAN-6] SELESAI (2026-09-28): foto tetap wajib

Checkout `bank_transfer` **tidak pernah** boleh lanjut tanpa foto bukti;
perilaku sekarang dipertahankan. Maka:

- Foto wajib pada checkout baru di `CartList.vue`; repository memvalidasi
  record proof dan mengaitkannya atomik ke nota. Retry memakai pengaitan asli,
  tidak meminta foto baru atau mengambil UI pembayaran yang berubah.
- "Bukti tertunda" di sisi server (§4) tetap diperlukan, tetapi lahir dari
  **unggah yang gagal**, bukan dari kasir yang sengaja melewati foto.
- Nota transfer yang sudah tersimpan tanpa bukti tidak dianggap cacat: notanya
  sah, buktinya menyusul.

### 7.4 Alur pipeline bukti (bukan offline end-to-end)

```text
CartList langkah pembayaran
  pilih foto ──► proses (utuh / encode JPEG) ──► Dexie payment_proofs
                        │                        (proof_id, sync_id NULL,
                        │                         upload_status: captured)
                        └─► preview dari blob tersimpan

tekan Checkout (foto wajib ada — §7.3)
  emit complete-transaction { ..., proofId }

App.vue completeTransaction()
  createSyncId() ──► nota + pengaitan proof Dexie atomik (sync_status: pending)
  bukti: sync_id diisi, upload_status: pending; captured_at asli dipertahankan
  POST /api/transactions/
       ├─ 201/200 → nota synced ──► flushPaymentProofs()  (tidak memblok UI)
       ├─ 400 pertama nonfinal pasti → rejected; final/ambigu → review
       ├─ 401/429 → pending/unknown; 5xx/jaringan → unknown
       │            UI retryable/rate_limited; bukti tetap menunggu notanya
       └─ 409 → nota conflict, bukti menunggu

flushPaymentProofs()
  hanya untuk nota sync_status === 'synced'
  POST /api/payment-proofs/ (multipart, Authorization: Device <token>)
```

Urutan **nota dulu, bukti menyusul** adalah inti §4 dan tidak boleh dibalik:
bukti hanya boleh diunggah untuk nota yang sudah ada di server.

### 7.5 Kompresi sebelum antre

Modul `src/services/image/compressProofImage.js` ([KEPUTUSAN-8], §7.15).
Prinsipnya: **jangan mengompres lebih dari yang perlu.**

1. Baca berkas dengan `createImageBitmap(file)` (fallback `Image` + object URL
   bila `createImageBitmap` tidak ada) dan ambil dimensi aslinya.
2. **Jalur hemat.** Kalau berkas sudah `jpeg`/`png`/`webp` (tiga format yang
   diterima server) dan ukurannya ≤ target 4,5 MiB, berkas diteruskan **apa
   adanya**: tidak ada re-encode, jadi tidak ada penurunan mutu. Screenshot
   bukti transfer yang besarnya ratusan KB termasuk di sini, dan PNG screenshot
   yang diteruskan utuh justru lebih tajam daripada hasil konversi ke JPEG.
3. **Jalur kompres.** Selain itu (terlalu besar, atau `HEIC`/format lain yang
   tidak diterima server) gambar di-encode ulang ke JPEG **pada resolusi
   aslinya** — tidak diperkecil dulu — dengan pencarian kualitas:
   - coba kualitas **0,95**; kalau sudah muat, selesai;
   - kalau tidak, coba **0,5**; kalau pun tidak muat, resolusi diturunkan ke
     2560 px lalu 1600 px dan pencarian diulang;
   - di antara kedua ujung itu, kualitas tertinggi yang masih muat dicari
     dengan 3 langkah titik tengah. Hasilnya sedekat mungkin dengan batas,
     bukan sekecil mungkin.
4. Kembalikan
   `{ blob, contentType, width, height, byteSize, reencoded, quality }`.
   `reencoded: false` berarti berkas asli diteruskan utuh.

Angka yang diukur di browser (canvas `toBlob`, 12 MP) sebagai acuan setelan:

| Jenis gambar | 1600 px q0,7 (perilaku lama) | resolusi penuh (perilaku sekarang) |
| --- | --- | --- |
| Screenshot bank (warna rata) | 131 KB | 315–366 KB (q0,95, dan biasanya diteruskan utuh) |
| Foto kamera (derau penuh, kasus terburuk) | 879 KB | 4,13 MB (q0,5 — batas kualitas) |

Biaya yang harus disadari: foto yang perlu dikompres kini butuh beberapa kali
encode (1,7 detik untuk 12 MP di mesin pengembang, bisa 2–3× lebih lama di
tablet), sementara foto yang diteruskan utuh hampir gratis (89 ms untuk 4 MB).
Satu-satunya angka yang perlu diubah kalau unggahan terasa terlalu berat adalah
`TARGET_PROOF_BYTES`.

Kegagalan yang harus ditangani sebagai pesan Indonesia, bukan diam-diam:
berkas bukan gambar; dekode gagal (jalur utama galeri iOS memberi `HEIC` yang
belum didukung browser); hasil masih > 5 MB setelah resolusi diturunkan sampai
1600 px pada kualitas 0,5. Semua berujung "ambil ulang foto" — dan **perilaku
HEIC wajib diuji di perangkat asli**, bukan diasumsikan.

### 7.6 Perubahan indeks proof pada Dexie v2 (sekarang v4)

Cuplikan berikut mencatat penambahan indeks proof pada versi 2, bukan seluruh
skema saat ini. Versi 3 menambah print_jobs dan versi 4 menambah workers; sumber
aktual adalah [db.js](../pos-frontend/src/services/storage/db.js).


```js
db.version(2).stores({
  device: 'id',
  sequence_ranges: 'device_id',
  items: '[item_name+variant]',
  transactions: 'sync_id, sync_status, cashier_name_normalized, transaction_id',
  payment_proofs: 'proof_id, sync_id, upload_status, [upload_status+captured_at]',
})
```

- `upload_status` diindeks supaya antrean bisa dicari tanpa memindai seluruh
tabel; indeks gabungan `[upload_status+captured_at]` memberi urutan "yang
tertua lebih dulu" tanpa pengurutan manual.
- Menaikkan Dexie ke versi 2 adalah **perubahan skema klien saja**, bukan
  server, jadi tidak butuh izin [SKEMA].
- Tabel `payment_proofs` v1 selalu kosong (tidak ada yang menulis ke sana), jadi
  tidak perlu fungsi `upgrade()`. Baris `transactions` yang sudah ada tidak
  tersentuh.

### 7.7 Bentuk record dan status bukti

```js
{
  proof_id,        // uuid dibuat klien; kunci idempotensi endpoint
  sync_id,         // uuid nota; NULL sampai nota dibuat
  transaction_id,  // nomor nota bila sudah ada, untuk tampilan riwayat
  blob,            // JPEG hasil kompres, atau berkas asli bila diteruskan utuh
  content_type,    // 'image/jpeg', atau 'image/png'/'image/webp' bila utuh
  byte_size, width, height,
  recompressed,    // false = berkas asli diteruskan apa adanya (KEPUTUSAN-8)
  captured_at,     // ISO dari jam perangkat (bukan otoritas server)
  created_at,
  upload_status,
  upload_attempts, last_attempt_at, next_attempt_at,
  server_sha256,   // diisi dari respons server saat sukses
  detail,          // pesan terakhir untuk kasir
}
```

| Status | Arti | Pemicu masuk |
| --- | --- | --- |
| `captured` | Diproses & tersimpan, nota belum ada | Foto dipilih di langkah pembayaran |
| `pending` | Bukti terhubung ke nota; unggah menunggu nota synced | `sync_id` diisi saat checkout |
| `uploading` | Sedang dikirim | Flush mulai mengirim |
| `uploaded` | Diterima server (`201`/`200`) | Respons sukses |
| `retryable` | Gagal sementara, akan dicoba lagi | Jaringan/`5xx`/`401`/`429` |
| `conflict` | Butuh tindakan manusia | `409` |
| `blocked` | Upload tidak dapat dilanjutkan otomatis | `400`/`404`, nota `rejected`, atau kompres ulang gagal |

`uploading` bersifat sementara seperti `syncing` pada nota: bila aplikasi mati
saat status ini, saat start dinaikkan ulang ke `retryable` (mengikuti pola
`restorePendingCheckout()` yang sudah ada).

### 7.8 Kontrak modul

| Berkas | Isi |
| --- | --- |
| `src/services/id.js` | `createUuid()` hasil pemindahan `createSyncId()` dari `App.vue`, dipakai nota **dan** bukti. Tetap memakai `crypto.getRandomValues`, bukan `crypto.randomUUID`. |
| `src/services/image/compressProofImage.js` | `compressProofImage(file, { targetBytes, forceRecompress })` → `{ blob, contentType, width, height, byteSize, reencoded, quality }`; melempar `ProofCompressionError` berpesan bahasa Indonesia. Setelan bawaan `TARGET_PROOF_BYTES` 4,5 MB dan `MAX_PROOF_BYTES` 5 MB; `forceRecompress` dipakai antrean saat server membalas `413`, dengan target 2 MB. |
| `src/services/api/paymentProofApi.js` | `uploadPaymentProof({ deviceToken, proofId, syncId, capturedAt, blob })` memakai `FormData`. Mengembalikan `{ status, body }`, **bukan** melempar error: pemanggilnya harus membedakan `401`/`404`/`409`/`413` untuk menentukan status antrean lokal. |
| `src/services/storage/paymentProofRepository.js` | CRUD lokal `getPaymentProof`, `getPaymentProofsBySyncId`, `savePaymentProof`, `deletePaymentProof`, `clearPaymentProofs`; linking, status unggah, kandidat/outstanding, pemulihan stuck, blocked, reset retry, dan delete per sync ID. Sumber kode memiliki daftar ekspor aktual. |
| `src/services/paymentProof/uploadQueue.js` | `attachProofToTransaction`, `flushPaymentProofs` (single-flight), `retryPaymentProofNow`, `resubmitPaymentProofAsNew`, `summarizeProofSync`, `backoffDelayMs`. |

Aturan bisnis: bukti uploaded tidak ditimpa/dihapus; foto belum terkait nota
boleh dibersihkan melalui jalur batal yang sah. **Gap implementasi:** fungsi
repository delete/clear tidak menjaga status, dan discard pending menghapus
proof per sync ID tanpa guard uploaded. Jangan menganggap repository menegakkan
append-only, atau “belum terkonfirmasi” membuktikan nota belum diterima server.
Perlindungan lifecycle masih perlu pekerjaan kode terpisah.

### 7.9 Algoritma flush

1. Kalau `navigator.onLine` bernilai false, keluar tanpa mencoba apa pun.
2. Kandidat = `upload_status` `pending` atau `retryable` dengan
   `next_attempt_at` kosong/terlampaui, urut `captured_at` tertua dulu.
3. Untuk setiap kandidat, pastikan notanya `sync_status === 'synced'`. Kalau
   belum, **lewati tanpa menandai gagal** — ini menunggu, bukan kegagalan.
4. Batas terpasang 3 kandidat per siklus; kandidat yang menunggu nota juga
   memakan slot, sehingga bukan jaminan tiga file siap selalu terunggah.
5. Tandai `uploading`, kirim, lalu petakan hasilnya (§7.10).
6. Backoff: percobaan ke-n menunggu `min(2^(n-1) × 5 detik, 5 menit)`
   (pertama 5 detik). Jadwal dicek saat flush dipicu; ini bukan timer mandiri.
7. Dipicu oleh: nota yang baru `synced`, event `online`, saat mount, dan saat
   kasir membuka riwayat nota (hanya bila ada yang tertunda).

### 7.10 Pemetaan hasil HTTP

| HTTP | Status lokal | Yang dilihat kasir |
| --- | --- | --- |
| `201` | `uploaded` | Bukti tersimpan |
| `200` | `uploaded` | Sudah tercatat sebelumnya (idempotent) |
| `400` | `blocked` | "Format bukti tidak didukung, ambil ulang foto" |
| `401` | `retryable` | "Perangkat tidak terautentikasi"; antrean mengembalikan flag unauthorized, tetapi belum ada renewal device otomatis yang mempertahankan identitas |
| `404` | `blocked` | "Nota belum ada di server" — ditahan sampai notanya `synced` |
| `409` | `conflict` | "Bukti ini sudah ada dengan isi berbeda" → tombol "unggah sebagai bukti baru" (proof_id baru) |
| `413` | `retryable` atau `blocked` | Encode ulang target 2 MiB jika hasil lebih kecil; blocked bila gagal. Tidak ada flag persisten yang membatasi jumlah re-encode lintas percobaan |
| `5xx`/jaringan | `retryable` | Dicoba lagi otomatis di latar belakang |

`409` upload tidak di-retry otomatis. Nota checkout `rejected`/`review`/
`unknown` belum boleh mengunggah bukti: antrean melewatinya sampai `synced`.
Jangan menyimpulkan nota tidak pernah tersimpan dari penolakan retry; bukti
serta pengaitannya tetap utuh untuk tindak lanjut.

### 7.11 Risiko yang sudah diketahui

- **`crypto.subtle` tidak tersedia di context tidak aman.** POS dibuka dari
  `http://<IP-LAN>` di tablet, dan di sana `crypto.subtle.digest` tidak ada —
  itulah sebabnya `App.vue` membuat UUID dengan `crypto.getRandomValues`.
  Karena itu **jangan** memakai hash SHA-256 klien sebagai syarat apa pun;
  `sha256` otoritatif tetap milik server (§6). Kalau deteksi duplikat lokal
  memang dibutuhkan, pakai checksum non-kriptografis dan jangan pernah
  mengirimnya sebagai bukti keaslian.
- **Batas ukuran.** Target klien 4,5 MiB (4.718.592 byte); batas file server
  5 MiB (5.242.880 byte). Overhead multipart tidak dihitung sebagai ukuran file
  oleh service, tetapi proxy dapat membatasi ukuran request secara berbeda.
  Jalur 413 tetap perlu diuji; tidak otomatis terjadi karena target dekat batas.
- **Kuota IndexedDB.** Bukti tertunda menumpuk di perangkat; perlu batas
  (mis. 50 bukti atau **400 MB** — naik dari 100 MB karena berkas sekarang
  boleh mendekati batas server) dan pesan jelas saat batas itu tercapai.
- **Nomor nota belum tentu ada.** Nota boleh tersimpan tanpa `transaction_id`
  saat jatah nomor habis; unggah bukti memakai `sync_id`, jadi ini tidak
  menghalangi.
- **Jam perangkat tidak otoritatif.** `captured_at` dikirim apa adanya;
  `uploaded_at` dari server yang dipakai sebagai jejak audit.
- **Satu nota bisa punya banyak bukti** (transfer dipecah dua kali). Struktur
  `proof_id` + `sync_id` sudah mendukungnya; tampilan riwayat harus
  menampilkan daftar, bukan satu berkas.
- **HEIC dari galeri iOS** — jalur kegagalan paling mungkin di lapangan.

### 7.12 Yang di luar cakupan rancangan ini

- Endpoint membaca/menampilkan bukti dari server — belum ada, dan menunggu
  keputusan retensi/hak akses (§8). Karena itu blob lokal **tidak** dihapus
  setelah `uploaded`; menghapusnya akan membuat bukti tak bisa dilihat sama
  sekali sampai endpoint baca ada.
- Endpoint update/delete bukti: tidak akan dibuat (append-only, §6).
- Arsip bukti bersama nota 2 rangkap (§8).

### 7.13 Yang masih perlu diputuskan setelah pipeline terpasang

- Hapus blob lokal setelah `uploaded`? Rekomendasi: tahan sampai endpoint baca
  ada, lalu hapus dan simpan metadatanya saja.
- Kuota bukti tertunda (usulan 50 bukti atau 400 MB) belum dipasang. Jumlah
  unggah per siklus **sudah** default 3 (`MAX_UPLOADS_PER_CYCLE`), bukan keputusan
  kuota total/retensi.

### 7.14 [KEPUTUSAN-7] SELESAI (2026-09-28): key storage datar

`payment_proofs/<tahun>/<bulan>/<proof_id>.<ext>` — folder bulan langsung berisi
daftar gambar, **tanpa subfolder per nota**.

Yang jadi nama berkas adalah `proof_id`, bukan `sync_id`. Alasannya
mengikat: satu nota boleh punya lebih dari satu bukti (jalur pemulihan `409`
membuat bukti baru dengan `proof_id` baru untuk `sync_id` yang sama, §7.10),
sehingga nama berbasis `sync_id` akan menabrak berkas bukti pertama dan ditolak
sebagai berkas yatim. `object_key` juga punya constraint `UNIQUE`, jadi
bentroknya nyata, bukan teoretis.

Yang hilang dan yang tidak: pengelompokan per nota tidak lagi terlihat di
filesystem, tetapi tidak ada informasi yang benar-benar hilang — `sync_id`
tetap tersimpan di baris `payment_proof`, jadi daftar bukti satu nota cukup
di-query. Yang dijaga tetap: key relatif, dibuat server, dan database menyimpan
key bukan URL (§3).

**Bukti lama tetap berlaku.** Yang tersimpan di database adalah key, bukan pola,
jadi tiga berkas yang sudah ada dengan bentuk `<sync_id>/<proof_id>.jpg` tetap
terbaca tanpa perubahan kode. Memindahkannya hanyalah kerapian — dan itu berarti
`UPDATE` di database bersama, jadi menunggu izin terpisah.

### 7.15 [KEPUTUSAN-8] SELESAI (2026-09-28): kompres hanya bila perlu, lalu sedekat mungkin ke batas

Dua perubahan dari versi pertama dokumen ini:

1. Berkas yang **sudah memenuhi syarat server** (jpeg/png/webp dan ≤ 4,5 MB)
   dikirim apa adanya, tanpa re-encode sama sekali.
2. Untuk yang memang harus dikompres: resolusi asli dipertahankan dan kualitasnya
dinaikkan setinggi mungkin sampai mendekati batas 5 MB — bukan diturunkan ke
1600 px/0,7 tanpa alasan.

Konsekuensi yang harus diterima bersamaan: ukuran bukti bisa naik dari puluhan
KB menjadi beberapa MB, terutama untuk foto kamera. Itu memengaruhi kuota
penyimpanan di perangkat (§7.11) dan waktu unggah di jaringan toko. Batas
tunggalnya `TARGET_PROOF_BYTES`; kalau ternyata terlalu berat, yang berubah
hanya konstanta itu, bukan alurnya.

## 8. Yang masih menunggu keputusan

- **[KEPUTUSAN-3] Storage produksi** — ditunda (2026-09-28). Pilihan yang sudah
  dibahas: object storage privat (R2/S3-compatible), Postgres `bytea`, atau
  filesystem server aplikasi. Syarat swappable di §3 membuat keputusan ini bisa
  diambil nanti tanpa mengubah model maupun kontrak.
- **[SKEMA] SELESAI (2026-09-28):** tabel `payment_proof` (§5) sudah dibuat di
  database setelah izin diberikan; tidak menunggu keputusan lagi.
- Retensi bukti dan siapa yang boleh melihatnya (device kasir saja, pemilik,
  atau Finance).
- Apakah bukti transfer perlu ikut diarsipkan bersama nota 2 rangkap (asli pelanggan + karbon toko; salinan gudang digital — lihat `PRINT_PATH_DESIGN.md` §2).

## 9. Kriteria penerimaan (bukan laporan semua uji lulus)

- UI checkout `bank_transfer` mewajibkan foto bukti (KEPUTUSAN-6), tetapi
  server sengaja menerima nota tanpa upload proof. Jangan mengklaim API
  transaksi menegakkan foto wajib; bukti menyusul nota melalui `sync_id`.
- Bukti yang sudah diunggah tidak pernah hilang atau tertimpa.
- Target: nota transfer yang buktinya belum datang bisa ditemukan. Query
  referensi ada (§4), tetapi endpoint laporan server belum ada; badge lokal
  tidak membuktikan semua nota di seluruh perangkat sudah terpantau.
- Bukti yang sudah memenuhi syarat server tidak dikompres ulang, dan yang
  dikompres berhenti sedekat mungkin dengan batas 5 MB, bukan sekecil mungkin
  (KEPUTUSAN-8).
- Abstraksi key/storage memungkinkan pergantian backend tanpa merancang ulang
  model/view/kontrak. Pemindahan berkas lama, dependency storage, akses, dan
  validasi produksi tetap pekerjaan tersendiri; bukan hanya toggle setting.
- Test backend minimal: unggah sukses, idempotent, isi berbeda → 409, tipe
  ditolak (svg), > 5 MB ditolak, `sync_id` milik device lain → 404, dan query
  "bukti tertunda".
- Sisi klien (§7): foto diproses (kompres hanya bila perlu) sebelum disimpan,
  tidak pernah ditahan hanya di memori tab; bukti terkirim otomatis setelah notanya `synced` dan saat
  perangkat kembali online; notanya tidak pernah menunggu bukti; dan bukti yang
  gagal permanen terlihat kasir sebagai bukti tertunda, bukan hilang diam-diam.
- Dokumen ini dan `ARCHITECTURE.md` diperbarui saat keputusan dikunci.
