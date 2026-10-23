# Data pelanggan per nota (nama + No. HP)

Status: **diterapkan** (backend + frontend + DDL di bawah; DDL dijalankan
pemilik database, bukan migrasi Django — model `managed = False`).

Keputusan pemilik 2026-10-14: setiap penjualan baru wajib mencatat **nama
pelanggan** (ditegakkan di layar checkout langkah 2); **No. HP opsional**.
Nama tercetak di struk, dan surat jalan (nota antaran) terisi nama + No. HP
otomatis bila ada.

## 1. Kolom (`transactions`)

| Kolom | Tipe | Null | Arti |
| --- | --- | --- | --- |
| `cust_name` | TEXT | Ya | Nama pelanggan. NULL = nota lama dari sebelum fitur ini (termasuk antrean offline yang disegel sebelum update). Penjualan baru selalu terisi lewat UI. |
| `phone_no` | VARCHAR(20) | Ya | No. HP pelanggan, opsional. NULL/kosong = tidak dicatat. |

Aturan format (ditegakkan serializer `TransactionCreateSerializer` dan UI):

- Nama: 1–120 karakter setelah trim. String kosong/blank dinormalisasi
  menjadi NULL (warisan), bukan ditolak — supaya antrean offline lama dan
  klien lama tetap bisa tersinkron.
- No. HP: kosong/NULL diizinkan; bila diisi, maksimal 20 karakter dan hanya
  boleh berisi digit, awalan `+` opsional, spasi, tanda hubung, atau kurung:
  `^+?[0-9][0-9\s()\-]*$`.

## 2. DDL (dijalankan pemilik database, dengan izin)

Tabel yang sudah berisi baris lama tidak bisa langsung ditambah kolom
`NOT NULL` tanpa default, jadi kedua kolom dibuat nullable; baris lama
tetap NULL (warisan) dan penjualan baru selalu mengisinya lewat aplikasi.

```sql
ALTER TABLE transactions ADD COLUMN cust_name TEXT;
ALTER TABLE transactions ADD COLUMN phone_no VARCHAR(20);
```

Tidak ada backfill: NULL berarti "nota sebelum fitur pelanggan", dan kertas
cetak lama (tanpa baris pelanggan / surat jalan tulis tangan) tetap sah
sebagai dokumennya.

## 3. Alur data

1. `CartList.vue` langkah 2: input nama (wajib) + No. HP (opsional) di panel
   pengambilan; tombol KONFIRMASI PEMBAYARAN nonaktif sampai nama terisi
   (dan No. HP lolos pola bila diisi).
2. `App.vue buildTransaction()` mengirim `cust_name`/`phone_no` di payload
   `POST /api/transactions/` dan batch sync; `buildReceiptSale()`
   membekukannya ke snapshot cetak.
3. `persist_transaction()` menyimpan keduanya; retry idempotent membandingkan
   keduanya seperti fakta header lain (beda nama/HP = 409 konflik).
4. `TransactionHistorySerializer` mengembalikannya ke riwayat + detail, dan
   `saleFromHistoryDetail()` memetakannya kembali untuk cetak ulang.
5. `receipt.js`: struk mencetak baris `Pelanggan` (selalu ada untuk nota
   baru) dan `No. HP` (bila ada); surat jalan mengisi `Kepala YTH` dan
   `No Hp` otomatis, dan kembali ke garis tulis tangan bila datanya NULL
   (nota lama).

## 4. Yang sengaja tidak ada

- Tidak ada tabel pelanggan/master pelanggan: ini atribut per nota, bukan
  relasi. Riwayat pelanggan agregat adalah kebutuhan Finance/CRM, bukan POS.
- Tidak ada validasi operator seluler atau OTP: kolom ini untuk tulisan di
  kertas, bukan identitas terautentikasi.
- Alamat tujuan tetap garis tulis tangan di surat jalan (kebutuhan
  alamat/zona `DIANTAR` yang dipersist belum didesain — lihat AGENTS.md §6).
