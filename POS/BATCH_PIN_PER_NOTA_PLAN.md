# Rencana Implementasi: Batch Sync PIN-per-Nota

> Spec: [BATCH_PIN_PER_NOTA_SPEC.md](BATCH_PIN_PER_NOTA_SPEC.md) (disetujui
> pemilik 2026-10-09). Rencana ini belum dieksekusi; eksekusi menunggu
> pemilik memilih metode di akhir dokumen.

## Prasyarat (sudah dipenuhi)

- Branch kerja slice 1: `opsi-a-antrean-lokal` (commit `5863b70`, bersih).
- Kontrak lama dipetakan dari kode: `TransactionBatchSyncSerializer`
  (`pos/transaction_request.py`), `TransactionBatchSyncAPIView`
  (`pos/views.py`), throttle (`pos/pin_throttle.py`), idempotensi
  (`pos/transaction_sync.py`), kirim per-nota frontend (`App.vue`
  `submitSealedTransaction` / `resendQueuedTransaction`).

## Irisan kerja

### Irisan 1 — Backend kontrak baru (dual-envelope)

- `pos/transaction_request.py`: terima PIN per anggota; tolak ambiguitas
  (PIN top-level + PIN per-nota) dengan 400; anggota tanpa PIN → DTO
  bertanda "pin hilang" untuk hasil `rejected` (bukan 400 envelope).
- `pos/views.py`: `TransactionBatchSyncAPIView` memilih jalur lama/baru;
  jalur baru verifikasi per-nota dengan deduplikasi dalam-request,
  `check_pin_allowed` di awal, lockout tengah-batch → sisa `rejected` +
  `retry_after_seconds`; respons baru tanpa `worker_id` top-level.
- Komentar keputusan di kedua file diperbarui (bukan diduplikasi).
- Verifikasi: uji backend baru spec §7 no.1–8 hijau.

### Irisan 2 — Frontend batch

- `services/api/transactionApi.js` (atau modul sejenis per konvensi):
  `syncTransactionBatch(deviceToken, [{...transaction, pin}])`.
- `CartList.vue`: tombol "Kirim semua" (dulu "Kirim semua (N)"; angkanya
  dihapus 2026-10-11) + pemetaan hasil per-nota
  (spec §5) — sejak 2026-10-09 tombolnya pindah ke `TransactionHistory.vue`,
  permintaan pemilik;
  `App.vue`: orkestrasi status lokal + perlindungan
  `restoring/submitting/conflict/review` + cooldown yang sudah ada.
- Verifikasi: uji frontend spec §7 no.9–10 + `npm test` dan `lint:check`
  hijau penuh.

### Irisan 3 — Dokumen kontrak

- `pos-backend/OFFLINE_SYNC_DESIGN.md` §4: catat bentuk baru + sunset
  envelope lama; `FRONTEND_HANDOFF.md`: frontend baru hanya kirim bentuk
  baru. Tanpa klaim implementasi sebelum kode + uji hijau.

## Urutan dan cabang

1. Buat cabang baru dari `opsi-a-antrean-lokal`:
   `slice-2-batch-pin-per-nota`.
2. Kerjakan Irisan 1 → uji backend → commit.
3. Kerjakan Irisan 2 → `npm test` + lint → commit.
4. Kerjakan Irisan 3 → commit. Tanpa merge ke `main` dalam rencana ini.

## Risiko yang dijaga

- Tanpa migrasi/skema/tabel baru (lapis AGENTS.md §5).
- Perubahan kontrak API eksplisit + terkoordinasi kedua sisi (§6);
  tidak ada consumer batch selain frontend baru + tes (caller batch
  frontend belum ada — terverifikasi via pencarian kode).
- PIN tidak masuk log/history/error (spec §6); fixture uji memakai PIN
  sintetis.

## Kriteria selesai irisan

- 10 uji spec §7 hijau; tidak ada uji lama yang dilemahkan (ekspektasi
  yang berubah perilaku didokumentasikan alasannya, seperti slice 1).
- `npm test`, `lint:check`, dan suite backend hijau setelah edit terakhir.
- Uji perangkat 5-nota (§8 no.1) tetap di luar rencana ini — prasyarat
  penutupan fitur, bukan penerimaan slice.
