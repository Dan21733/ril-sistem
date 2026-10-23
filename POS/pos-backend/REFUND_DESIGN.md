# Refund / Retur / Void POS

Status: **diterapkan** (backend + frontend + DDL di database Neon). Aturan final
disetujui pemilik secara delegasi pada 2026-10-06: pemilik meminta seluruh use
case void/retur/refund dipikirkan dan implementasi terbaik dipilih agen, jadi
keputusan di §6 adalah keputusan yang berlaku, bukan usulan.

Batas yang tetap dipegang:

- `ARCHITECTURE.md` tetap pemilik makna bisnis (Void, Return, Refund, Exchange);
  dokumen ini tidak mengubah makna itu, hanya memilih aturan teknis di dalamnya.
- POS **hanya mencatat event**; efek stok tetap milik Inventory lewat kontrak
  terpisah (belum ada). Tidak ada kolom/tabel Inventory yang disentuh di sini.
- `worker` hanya punya `id/name/pin` — tidak ada kolom role/approver, jadi
  "approver" selalu berarti "worker yang PIN-nya dipakai di aksi ini".

## 1. Makna yang dipegang (dari ARCHITECTURE.md)

| Istilah | Makna terkunci | Yang terjadi di POS |
| --- | --- | --- |
| Void | Batalkan nota selesai karena salah input kasir atau pesanan benar-benar batal | Event `void`, nota tidak dihapus/diubah |
| Return | Pelanggan mengembalikan barang (termasuk cacat / minta ganti) | Event `return` + baris barang |
| Refund | Uang kembali ke pelanggan sebagai bagian proses return/refund; normalnya cash | Event `refund` (uang saja) atau `refund_amount` di event `return` |
| Exchange | **Bukan** tipe tersendiri: retur/refund barang lama + penjualan baru | Event `return` (uang boleh 0) + nota baru biasa |

Aturan yang mengikat semuanya (dan karena itu tidak dilanggar di sini): nota asal
tidak pernah diubah; setiap aksi adalah event baru; wajib PIN approver + alasan +
jejak audit; otorisasi yang sah ada di server.

## 2. Semua use case yang ditangani

Nota = transaksi tersimpan. Event bersifat *append-only* dan terminal hanya pada
void.

| # | Use case | Hasil |
| --- | --- | --- |
| 1 | Salah input kasir / pelanggan batal, nota belum pernah diapa-apakan | `void` 201; uang yang dibayar boleh dicatat di `refund_amount` (tunai: prefilled total, boleh diubah ke 0) |
| 2 | Nota salah input setelah ada retur/refund | **409**: `void` ditolak, selesaikan lewat jalur retur/refund |
| 3 | Void dua kali / aksi apa pun setelah void | **409** (nota void terminal) |
| 4 | Barang kembali seluruhnya, uang kembali penuh | `return` barang penuh + `refund_amount` ≤ total nota |
| 5 | Barang kembali sebagian, uang sebanding | `return` parsial, divalidasi per baris & kumulatif |
| 6 | Barang kembali, belum ada uang keluar (pelanggan minta ganti) | `return` dengan `refund_amount = 0` |
| 7 | Tukar barang (exchange) | `return` uang 0, lalu nota pengganti dicatat sebagai penjualan baru |
| 8 | Uang kembali tanpa barang (mis. ongkir tidak jadi dikirim) | `refund` murni uang; plafon = sisa total nota |
| 9 | Barang sudah diretur kemarin, uangnya dibayarkan hari ini | `refund` murni uang; plafon = sisa total nota |
| 10 | Pelanggan coba klaim uang lebih besar dari total nota | **400** (`Uang kembali melebihi total nota.`) |
| 11 | Barang berdiskon diklaim dua kali sebesar harga sebelum diskon | **400** (`benar-benar dibayar`), plafon per baris = `transaction_detail.subtotal` |
| 12 | Jumlah barang melebihi yang dijual | **400** |
| 13 | Barang yang tidak ada di nota asal | **400** |
| 14 | Satu event memuat barang yang sama dua kali | **400** |
| 15 | Harga satuan di atas harga yang dibayar | **400** |
| 16 | `subtotal` tidak sama dengan `qty × unit_price` | **400** |
| 17 | PIN salah | **401** (di-throttle; berulang → **429**) |
| 18 | Kasir nota asal sudah pulang (shift berganti) | 201 — PIN kasir lain yang sah diterima, approver tercatat, respons menandai `self_approval` |
| 19 | Pelanggan kembali ke tab lain (nota device lain) | 201 dari device mana pun; respons/riwayat menandai `cross_device` |
| 20 | Retry jaringan dengan `refund_id` sama dan isi sama | **200 idempotent**, tidak ada event ganda |
| 21 | `refund_id` sama tapi isi berbeda | **409** |
| 22 | Dua permintaan bersamaan untuk nota yang sama | Dikunci barisnya (`SELECT … FOR UPDATE`) sehingga jatah tidak terpakai dua kali |
| 23 | Nota asal tidak ada / tidak terlihat | **404** |
| 24 | Pemilik ingin meninjau semua void/retur/refund satu hari | `?refund_status=void|returned|refunded|none` di riwayat |
| 25 | Nota void/retur/refund dicetak ulang | Kertas diberi banner `*** NOTA VOID / DIBATALKAN ***`, `*** SUDAH ADA RETUR BARANG ***`, atau `*** SUDAH ADA REFUND UANG ***` |
| 26 | Nota tanpa nomor (offline, jatah nomor habis) | Tetap bisa di-void/retur/refund: kunci event adalah `sync_id`, `device_code` diambil dari device pemanggil |

## 3. Aturan validasi (implementasi: `pos/refund_service.py`)

- `event_type` hanya `void | return | refund`.
- **void**: tanpa baris barang; hanya boleh pada nota yang **belum punya event
  apa pun** (pristine); `refund_amount` boleh 0..`total_amount`.
- **return**: wajib ≥1 baris barang; `refund_amount` boleh 0 ke atas.
- **refund**: tanpa baris barang; `refund_amount` wajib > 0.
- Baris barang: harus ada di nota asal; tanpa duplikat dalam satu event;
  `quantity > 0`; `quantity ≤ sisa jatah`; `unit_price ≤ harga asal`;
  `subtotal = unit_price × quantity`;
  `nilai kembali kumulatif per baris ≤ transaction_detail.subtotal`
  (**plafon diskon-aware**: barang berdiskon tidak bisa diklaim dua kali).
- **Plafon uang global**: `uang keluar kumulatif (void + retur + refund) ≤
  transactions.total_amount`. Plafon tunggal ini berlaku untuk void, retur,
  maupun refund murni: pelanggan boleh menerima kembali uang yang
  benar-benar dibayarnya tanpa harus mencatat barang kembali lebih dulu
  (keputusan pemilik 2026-10-17). Perlindungan klaim ganda tetap ada di
  plafon per baris (`transaction_detail.subtotal`).
- Idempotency diperiksa **sebelum** aturan jatah: retry yang sah tetap dibalas
  200 walau nota sudah bergerak sejak permintaan pertama. Perbandingan isi
  mencakup sync_id, tipe, jumlah uang, alasan, metode, dan seluruh baris barang.
- Penulisan event + detail terjadi dalam satu transaksi, dengan baris nota
  dikunci lebih dulu.

## 4. Tabel (DDL sudah diterapkan di Neon; model `managed = False`)

```sql
CREATE TABLE refund_event (
  refund_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  sync_id uuid NOT NULL REFERENCES transactions(sync_id),
  transaction_id varchar(10),
  event_type varchar(10) NOT NULL
    CHECK (event_type IN ('void', 'return', 'refund')),
  reason text NOT NULL CHECK (char_length(reason) >= 3),
  refund_method varchar(12) NOT NULL DEFAULT 'cash'
    CHECK (refund_method IN ('cash', 'edc', 'bank_transfer')),
  refund_amount int NOT NULL CHECK (refund_amount >= 0),
  approver_worker_id varchar(5) NOT NULL REFERENCES worker(id),
  device_code varchar(3) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE refund_event_detail (
  refund_id uuid NOT NULL REFERENCES refund_event(refund_id),
  item_name varchar NOT NULL,
  variant varchar NOT NULL,
  quantity int NOT NULL CHECK (quantity > 0),
  unit_price int NOT NULL CHECK (unit_price >= 0),
  subtotal int NOT NULL CHECK (subtotal >= 0),
  PRIMARY KEY (refund_id, item_name, variant)
);

CREATE INDEX refund_event_sync_id_idx ON refund_event (sync_id);
CREATE INDEX refund_event_created_at_idx ON refund_event (created_at);
```

Konvensi: `refund_id` dibuat klien (idempotency retry, seperti `proof_id`);
append-only (tidak ada UPDATE/DELETE dari API; koreksi = event baru); PIN
approver tidak disimpan di mana pun.

## 5. API (sudah diimplementasikan)

- `GET /api/transactions/history/?scope=self|all&date=YYYY-MM-DD&refund_status=…`
  - `self` (bawaan) = prefix `Txx-` device pemanggil; `all` = semua device,
    **wajib** header `X-Approver-PIN` (PIN worker valid, di-throttle).
  - `refund_status` opsional: `none|void|returned|refunded`; nilai lain 400.
  - Tiap baris membawa `device_code` (turunan prefix, `null` bila tanpa nomor)
    dan `refund_status` agregat (prioritas void > refunded > returned > none).
- `GET /api/transactions/history/<transaction_id>/` — nota device lain dibalas
  404 kecuali disertai `X-Approver-PIN`; respons memuat `items` dan `refunds[]`
  (setiap event membawa `approver_name`, `self_approval`, `cross_device`).
- `POST /api/refunds/` — `{ refund_id, sync_id, approver_pin, event_type, reason,
  refund_method, refund_amount, items? }` + device token.
  Balasan: `{ refund_id, sync_id, transaction_id, event_type, status:
  recorded|idempotent, self_approval, cross_device }` — 201 baru / 200 idempotent.
  Validasi 400, konflik 409, PIN salah 401 (throttle 429), nota tidak ada 404.
  Tidak ada input pengguna yang menghasilkan 500. Seluruh `detail` galat
  berbahasa Indonesia (permintaan pemilik 2026-10-17); pesan bawaan DRF yang
  ikut tampil di UI diterjemahkan `pos/messages_id.py`.

## 6. Keputusan pemilik & penyimpangan yang disengaja

Keputusan 2026-10-05 yang masih berlaku:

1. `scope=all` dibuka dengan gerbang PIN kasir + throttle.
2. POS hanya mencatat event; tanpa write stok (Inventory mengonsumsi nanti).

Keputusan 2026-10-06 (menggantikan sebagian keputusan 2026-10-05):

3. **Approver: PIN worker sah mana pun**, bukan hanya kasir nota asal. Alasan:
   aturan lama membuat retur/refund mustahil pada pergantian shift (kasir nota
   asal sudah pulang) dan membuat refund lintas device tidak mungkin dipakai
   walau `scope=all` sudah membukanya. Tanggung jawab tetap teridentifikasi:
   `approver_worker_id` tersimpan, dan respons + riwayat menandai
   `self_approval` (approver = kasir nota asal) agar pemilik bisa menelusuri
   approval yang menyetujui nota sendiri.

   **Direvisi keputusan pemilik (disetujui di chat, pengganti butir 3):**
   approver **wajib kasir nota asal** — PIN sah milik kasir lain ditolak
   **403** dan tidak ada event yang tercatat. Syarat: retur hanya transaksi
   sendiri + persetujuan master yang sifatnya prosedural di luar POS (kasir
   mencantumkan ACC master di kolom alasan). Konsekuensi yang diterima
   pemilik: bila kasir nota asal sudah pulang/resign, retur nota itu mustahil
   sampai peran master ada di sistem. Barang retur = **ditahan** (nanti
   dipilih ke stok/gudang di luar POS, bukan kembali ke rak); uang refund
   keluar dari **kas sekarang** (rekap kas/shift tetap backlog §7). Exchange
   tetap **alur manual dua langkah** (retur uang 0 + nota baru) sesuai
   persetujuan pemilik.
4. **Void hanya untuk nota bersih** dan boleh membawa `refund_amount`. Alasan:
   void setelah retur parsial akan menghitung ganda (barang sudah kembali, nota
   dibatalkan); dan cancelling nota yang sudah dibayar berarti uang keluar,
   tanpa tabel kas keluar di POS saat ini satu-satunya tempat mencatatnya
   adalah event void ini.
5. **`refund` = uang tanpa barang; `return` = barang (+ uang opsional).**
   Alasan: satu perbuatan satu label, sehingga status riwayat dan audit tidak
   ambigu (dulu keduanya bisa membawa barang).
6. **Uang tidak pernah melebihi total nota**, dan plafon per baris memakai
   `subtotal` (diskon-aware). Alasan: pelanggan tidak mungkin menerima lebih
   dari yang dibayarnya, sementara klaim ganda nilai barang tetap tertutup
   oleh plafon per baris.

   **Direvisi keputusan pemilik (disetujui di chat, 2026-10-17):** plafon uang
   kumulatif adalah `total_amount` nota, **bukan** nilai barang yang kembali +
   ongkir. Alasan: nota Rp 420.000 yang dibayar penuh ditolak saat kasir
   mengembalikan Rp 420.000 karena belum ada event retur, padahal uang itu
   memang uang pelanggan. Konsekuensi yang diterima pemilik: refund murni
   (uang keluar tanpa barang kembali) kini bisa sampai total nota; pemisahan
   makna void/retur/refund dan audit PIN tetap seperti sebelumnya.
7. Idempotency ketat + kunci baris nota, karena event ini menggerakkan uang.
8. Cetak ulang nota beraksi diberi banner status (use case 25).

## 7. Yang belum dikerjakan (jujur, bukan klaim)

- Efek stok Inventory untuk `return` (kontrak antar-modul belum ada).
  Makna yang dikunci pemilik: event `return` = barang **ditahan**, bukan
  kembali ke rak — Inventory mengonsumsi dengan status tahan.
- Role/approver sungguhan (Super Admin/Master) — tabel `worker` belum punya
  kolomnya, jadi "approval" masih berarti PIN worker.
- Endpoint daftar event lintas nota (hanya filter status di riwayat yang ada).
- Penyesuaian otomatis kas/shift: uang refund belum masuk rekap kas karena
  desain kas/shift belum ada.
- Exchange masih alur dua langkah manual (retur + nota baru), belum ada tombol
  pintasan yang membuat nota pengganti otomatis.
