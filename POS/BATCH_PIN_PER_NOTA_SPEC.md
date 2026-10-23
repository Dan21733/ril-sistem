# Spec: Batch Sync PIN-per-Nota (Slice 2 Opsi A)

> **Status: TERIMPLEMENTASI di cabang `slice-2-batch-pin-per-nota` (2026-10-09) —
> backend + frontend + uji hijau, menunggu review/merge dan uji perangkat.**
> Desain disepakati pemilik pada 2026-10-09 (diskusi slice 2).
> Dokumen keputusan pemilik tetap [pos-backend/OFFLINE_SYNC_DESIGN.md](pos-backend/OFFLINE_SYNC_DESIGN.md)
> (§4.2 target revisi, §5, §7–§8); file ini adalah spec implementasi slice 2,
> bukan pengganti dokumen tersebut. Klaim implementasi di sini harus
> diverifikasi terhadap kode sebelum fitur disebut selesai.

## 1. Hasil yang diinginkan

Setiap nota dalam batch antrean offline diatribusikan ke worker dari PIN-nya
sendiri oleh server, tanpa `worker_id` kiriman klien dan tanpa mengunci counter
PIN online (kriteria penerimaan OFFLINE_SYNC_DESIGN §8 no.4). Kirim ulang batch
yang sama memberi efek transaksi/stok tepat sekali; konflik identitas ditandai
dan tidak di-retry otomatis (§8 no.2).

## 2. Ruang lingkup

**Masuk:** revisi `POST /api/transactions/sync/` (serializer + view),
fungsi batch di frontend (`syncTransactionBatch`), tombol "Kirim semua" di
notifikasi antrean (sejak 2026-10-09 tinggal di modal Riwayat Nota; dulu banner
di kolom keranjang), pemetaan hasil per-nota ke status lokal, uji backend +
frontend, pembaruan dokumen kontrak.

**Tidak masuk:** pemulihan review yang berotorisasi, ikatan worker sebelum
commit, refill penomoran proaktif, rekonsiliasi stok Inventory lintas-modul,
filter approver, opsi B (jalur proxy) — semuanya tetap pekerjaan menyusul
per OFFLINE_SYNC_DESIGN §7.

**Batasan eksplisit:** tanpa tabel baru (penolakan = log terstruktur), tanpa
migrasi skema, tanpa menyimpan/mencatat PIN di luar `original_pin` IndexedDB
yang sudah ada, tanpa mengubah kontrak `POST /api/transactions/` satuan.

## 3. Kontrak baru

### 3.1 Request (bentuk baru)

```text
POST /api/transactions/sync/
Authorization: Device <token>
{ "transactions": [{ ...<payload transaction>, "pin": "123456" }, ...] }
```

- Tiap anggota membawa `pin` (string, tepat 6 digit) di samping field-field
  `TransactionCreateSerializer` yang berlaku hari ini.
- Tidak ada `pin` top-level. Tidak ada `worker_id` di request.
- `transactions`: 1–200 anggota (batas `MAX_BATCH_SYNC_TRANSACTIONS` tetap).

### 3.2 Response (bentuk baru)

```text
200 OK
{ "results": [{ "sync_id": "...", "transaction_id": "T01-000123",
                "status": "new|idempotent|rejected|conflict",
                "detail": "...", "retry_after_seconds": 42 (opsional) }, ...] }
```

- Tidak ada `worker_id` top-level pada respons bentuk baru.
- Hasil diurutkan sama dengan urutan request.

### 3.3 Kompatibilitas transisi (disetujui 2026-10-09)

- Server menerima **dua envelope** selama satu rilis: lama
  `{pin, transactions}` (satu worker untuk seluruh batch, respons tetap
  memuat `worker_id`) dan baru `{transactions: [{..., pin}]}`.
- Request yang mencampur PIN top-level **dan** PIN per-nota → `400`
  dengan detail "ambiguous pin placement" (tidak diproses sama sekali).
- Anggota tanpa `pin` pada envelope baru → nota itu `rejected`
  ("PIN wajib untuk setiap nota."), anggota lain tetap diproses.
- Tanggal sunset envelope lama ditetapkan saat rilis, dicatat di
  OFFLINE_SYNC_DESIGN §4 dan FRONTEND_HANDOFF; frontend baru hanya
  mengirim bentuk baru.

## 4. Semantik server

### 4.1 Verifikasi PIN per-nota

- Tiap nota diverifikasi independen via `_verify_pin` yang ada
  (format → `InvalidWorkerPinFormat`, kredensial → `InvalidWorkerCredentials`,
  pesan error tidak berubah agar tidak ada enumerasi worker).
- PIN salah / format salah → **hanya nota itu** `rejected` + `detail`;
  nota lain tetap diproses (keputusan pemilik 2026-10-09).
- PIN yang sama diulang dalam satu request diverifikasi **sekali**
  (deduplikasi dalam-request): satu kegagalan dihitung satu kali, dan 200
  nota satu kasir tidak berarti 200 pemindaian tabel worker.
- `clear_pin_attempts` hanya dipanggil bila **seluruh** verifikasi dalam
  request berhasil; keberhasilan sebagian tidak menghapus hitungan kegagalan.

### 4.2 Throttle

- Mekanisme existing dipertahankan: kunci `device:<device_code>`,
  `MAX_PIN_ATTEMPTS = 10`, lockout 5 menit, `429` + header `Retry-After`
  (modul `pin_throttle.py` tidak berubah).
- `check_pin_allowed` di awal request: bila sudah terkunci → seluruh
  request dibalas `429` seperti hari ini (belum ada nota diproses).
- Lockout yang **terpicu di tengah batch** → nota yang sudah diproses
  tidak dibatalkan; tiap nota yang belum diverifikasi menjadi `rejected`
  dengan `detail` throttled + `retry_after_seconds`. Tidak ada pembatalan
  parsial.

### 4.3 Idempotensi dan konflik (tidak berubah)

- Resolusi identitas via `resolve_transaction_request` tetap:
  kirim ulang batch yang sama (PIN sama) → `idempotent`, efek stok tepat
  sekali.
- PIN berbeda yang menghasilkan worker berbeda untuk nota tersimpan, atau
  fakta berubah → `conflict` (HTTP per-nota, bukan 409 envelope);
  worker historis tidak diubah, tidak ada retry otomatis.
- Tiap nota tetap atomik sendiri; tidak ada atomisitas seluruh batch.
- Penolakan dicatat sebagai log terstruktur; tanpa tabel penolakan baru.

## 5. Frontend

- Fungsi baru `syncTransactionBatch(deviceToken, [{...transaction, pin}])`
  di `services/api/` (nama modul mengikuti konvensi `transactionApi.js`);
  endpoint satuan `POST /api/transactions/` tidak berubah.
- Tombol "Kirim semua (N)" (sejak 2026-10-11 tanpa angka: "Kirim semua")
  duduk di notifikasi antrean, di samping tombol
  kirim per-nota yang sudah ada (slice 1). Sejak 2026-10-09 keduanya tinggal di
  `TransactionHistory.vue` (permintaan pemilik: banner di kolom keranjang
  memakan tempat) dan hanya meneruskan event ke `App.vue`.
- Penolakannya ada di `App.vue`, bukan di tombol: `resendQueuedBatch` berhenti
  saat `checkoutStatus` ∈ {restoring, submitting, conflict, review} atau saat
  cooldown PIN lokal berjalan; tombolnya sendiri nonaktif selama batch terbang.
- Urutan kirim = `created_at` menaik; satu request per ≤200 nota.
- PIN tiap nota = `original_pin` tersimpan (tidak pernah diminta ulang,
  tidak pernah dicatat ke log/history; konsisten dengan slice 1).
- Pemetaan hasil → `updateTransactionStatus` lokal:
  `new`/`idempotent` → `synced`; `rejected` → `rejected` beralasan;
  `conflict` → `conflict`. Nota `rejected`/`conflict`/`review` tetap butuh
  manusia (tidak auto-resend), konsisten dengan aturan slice 1.
- Unggah bukti (`flushProofs`) dan refresh direktori worker hanya untuk
  nota yang `synced`; kegagalan batch tidak mengosongkan keranjang draft.

## 6. Keamanan dan audit

- PIN per-nota hanya hidup di memori request dan `original_pin` IndexedDB
  yang sudah ada; tidak masuk print/history/log/error message.
- Batas 200 + deduplikasi verifikasi menjaga beban DB dari pemindaian
  tabel worker per percobaan (ancaman yang sudah didokumentasikan di
  `transaction_request.py`).
- Throttle server + cooldown lokal 5-gagal/30-detik (KEPUTUSAN-12) tetap
  dua lapis yang independen; tidak ada yang diklaim sebagai batas
  keamanan terhadap salinan IndexedDB (risiko OFFLINE_SYNC_DESIGN §6
  tetap dinyatakan apa adanya).

## 7. Uji yang wajib hijau

Backend (`pos/tests.py`, kelas `TransactionBatchSyncAPITests`):
1. Dua PIN berbeda → dua worker berbeda pada dua nota.
2. Satu PIN salah → hanya nota itu `rejected`, sisanya `new`.
3. Kirim ulang batch yang sama → semua `idempotent`, stok tepat sekali.
4. PIN berbeda untuk nota tersimpan (worker berbeda) → `conflict`,
   worker historis utuh.
5. Envelope lama `{pin, transactions}` tetap diterima (respons ada
   `worker_id`).
6. Campuran PIN top-level + PIN per-nota → `400`, nol nota diproses.
7. Anggota tanpa `pin` → `rejected` untuk anggota itu saja.
8. Batas 201 anggota → `400`.

Frontend (`tests/`, mengikuti harness `checkout.test.js`):
9. Tombol batch memetakan hasil campuran (synced/rejected/conflict) ke
   status lokal yang benar; keranjang draft utuh.
10. Nota `rejected`/`conflict` hasil batch tidak auto-resend.

Verifikasi mengikuti "verifikasi aman" (README): DB uji, bukan DB
bersama/produksi. Kriteria lulus = OFFLINE_SYNC_DESIGN §8 no.2 + no.4.

## 8. Rollout

1. Backend dual-envelope + uji (bagian 7, no.1–8) → merge.
2. Frontend batch + uji (no.9–10) → merge; rilis bersama, frontend hanya
   mengirim bentuk baru.
3. Tetapkan tanggal sunset envelope lama; catat di OFFLINE_SYNC_DESIGN §4
   dan FRONTEND_HANDOFF; hapus jalur lama pada rilis berikutnya.
4. Uji perangkat kasir (5 nota offline → online, §8 no.1) tetap prasyarat
   sebelum fitur offline disebut selesai — slice ini saja tidak menutupnya.
