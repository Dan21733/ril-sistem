# Pedoman Pengembangan Backend POS

Berlaku untuk backend Django 5.2 + DRF di direktori ini. Pedoman ini merangkum
bagian POS dari kebutuhan pemilik “Da System” dan keputusan yang telah dicatat,
bukan memberi izin untuk membangun seluruh kebutuhan sekaligus.

## 1. Rujukan dan status

- [Aturan repo](../../AGENTS.md) dan [aturan POS](../AGENTS.md) mengatur scope,
  keamanan, izin skema, Git, dan verifikasi.
- [ARCHITECTURE.md](../../ARCHITECTURE.md) memiliki keputusan bisnis dan
  arsitektur. Dokumen ini tidak mengubah keputusan itu.
- Kontrak detail: [offline/PIN](OFFLINE_SYNC_DESIGN.md),
  [bukti transfer](PAYMENT_PROOF_DESIGN.md),
  [jalur cetak](PRINT_PATH_DESIGN.md),
  [pencarian alias](ITEM_ALIAS_SEARCH_DESIGN.md), dan
  [handoff frontend](../FRONTEND_HANDOFF.md).
- Status implementasi berikut ditinjau dari kode pada 2026-10-01; bukan hasil
  pengujian toko/produksi. Bila keputusan dan kode berbeda, laporkan gap,
  jangan mengubah aturan bisnis atau mengklaim fitur sudah selesai.
- Gunakan dokumen yang ada; jangan menambah PRD/CLAUDE.md yang menduplikasi
  sumber keputusan. Istilah bisnis dan komentar mengikuti Bahasa Indonesia;
  identifier teknis mengikuti kontrak yang ada, tidak di-rename insidental.

## 2. Batas tanggung jawab

POS memiliki workflow penjualan, nota, pembayaran kasir, dan pengiriman
transaksi. Inventory memiliki stok otoritatif; Finance memiliki catatan
keuangan, piutang/hutang, dan rekonsiliasi; Super Admin/Master memiliki harga
master, role, dan approval.

Selama Inventory belum ada, satu-satunya write stok yang disetujui adalah
pengurangan penjualan di `_process_inventory` melalui `persist_transaction`.
Tetap atomik dengan nota. Jangan menyebarkan logika stok, menambah penyesuaian
stok, atau menulis tabel modul lain tanpa kontrak dan izin.

## 3. Aturan aktif yang tidak boleh “diperbaiki”

- Stok awal > 0 mengizinkan jumlah jual melebihi stok; hasil boleh negatif.
  Stok awal <= 0 menolak penjualan. Contoh: stok 5, jual 8 → stok akhir -3.
- Harga kasir harus berada di floor–ceiling. Harga di bawah floor ditolak,
  termasuk online, sampai request/approval dan approver dapat disimpan.
  `min_price` dipublikasikan sebagai `floor_price`; modal/margin bukan floor.
  `max_price` = 0 berarti pricelist item itu **belum diisi**, bukan harga jual
  Rp 0: batas atasnya `min_price` (`_validate_item_price`), sama dengan katalog
  di perangkat (`pos-frontend/src/utils/priceRange.js` `resolveItemPrices`).
  `max_price` NULL tetap berarti master harga belum lengkap. Keputusan pemilik
  2026-10-07; tanpa aturan ini setiap nota item seperti itu ditolak "above the
  ceiling price".
- Tepat tiga literal pembayaran: `cash`, `edc`, `bank_transfer`. QRIS dan
  kartu masuk `edc`; tidak ada split payment atau “lainnya”. Non-cash harus
  tepat total; kembalian hanya untuk cash.
- POS tanpa login worker. Backend memverifikasi PIN enam digit dan menentukan
  worker; jangan percaya `worker_id` klien. Nama kasir ketikan telah dihapus.
- Nota final append-only. Koreksi adalah event baru yang merujuk nota asal;
  endpoint edit/delete nota final tidak boleh ditambahkan.
- Validasi payload/total/identitas/item/stok server otoritatif. Header, detail,
  dan efek stok atomik; row locks dan constraint DB tetap diperlukan.
- `sync_id` stabil untuk retry. Idempotent juga harus cocok fakta header,
  semua barang dan worker hasil PIN; perubahan atau bentrok ID → 409, tanpa
  mengubah nota tersimpan atau memotong stok lagi.
- Frontend tidak boleh menerima modal, margin, laba, supplier, atau nilai stok.
  Serializer menggunakan field allowlist, bukan menyembunyikannya di UI.
- PIN/hash tidak masuk respons item/history atau log. Direktori worker offline
  adalah pengecualian sensitif yang disetujui, bukan izin membocorkan PIN ke
  endpoint lain; baca batas format/risikonya dalam desain offline.
- **Pesan galat yang dibaca kasir berbahasa Indonesia** (permintaan pemilik
  2026-10-17). Tulis pesan domain baru dalam Bahasa Indonesia di sumbernya,
  dan jangan biarkan pesan bawaan DRF lolos: `pos/messages_id.py` adalah jaring
  pengamannya. Kode status dan bentuk respons tetap sama; hanya teks `detail`
  yang berubah.

## 4. Skema yang mengikat

Model server `managed = False`; skema eksternal tidak dimiliki migrasi Django.
Jangan menjalankan DDL, migrasi, import/repair, atau perubahan record tanpa izin.

- Item: composite identity `item_name + variant`.
- Transaction header: PK `sync_id`; `transaction_id` unik, nullable,
  varchar(10), format `Txx-NNNNNN`.
- TransactionDetail: PK `sync_id + item_name + variant`, bukan nomor nota.
  Detail harus menunjuk item yang ada; custom amount butuh desain baru.
- `transactions.worker_id` wajib. Sejak CUSTOMER_DESIGN.md (2026-10-14) tabel
  juga punya `cust_name` (TEXT, NULL = nota lama) dan `phone_no`
  (VARCHAR(20), NULL = tidak dicatat). Tabel tetap belum punya kolom device,
  fulfillment, alamat, zona, shift, approval, atau penanda verifikasi offline.
- Device code `T01`–`T99` ada dalam prefix nota. Riwayat memakai prefix yang
  diverifikasi saat write; nota null tidak mempunyai dasar scoping itu.
- `payment_proof` sudah ada setelah izin 2026-09-28; metadata mereferensikan
  `sync_id`, file disimpan melalui storage backend.
- Waktu kolom `NOT NULL` harus diisi sesuai model; default DB tidak membantu
  bila ORM mengirim null eksplisit.

## 5. Kontrak yang terpasang

| Endpoint | Perilaku saat ini |
| --- | --- |
| `GET /api/items/` | 30 item/halaman, trigram kandidat-dulu + peringkat alias otomatis pada PostgreSQL bila `item_alias` tersedia; fallback ke trigram lama bila tabel tidak ada/query alias gagal. Field: `item_name`, `variant`, `price`, `floor_price`, `stock`, `stock_alert`; tanpa lokasi rak |
| `POST /api/transactions/` | Device token + `{ pin, transaction }`; 201 baru, 200 idempotent, 400 validasi/domain, 401 device, 409 konflik, 429 PIN |
| `POST /api/transactions/sync/` | Dual-envelope transisi (slice 2, 2026-10-09): lama `{ pin, transactions }` (satu worker, respons ada `worker_id`) dan baru `{ transactions: [{ ..., pin }] }` (worker per nota, respons tanpa `worker_id`); maks 200, hasil per nota + log penolakan |
| `GET /api/transactions/history/` | Device token, 20/halaman, opsional `date=YYYY-MM-DD` menurut zona waktu toko |
| `GET /api/transactions/history/<transaction_id>/` | Nota + detail; device lain → 404; field fakta jual saja |
| `POST /api/devices/register/` | Header registrasi server-side; awal 201; renewal ID+token lama 200 untuk device sama |
| `POST /api/devices/<device_id>/transaction-sequence/` | Device token, rentang 1000 nomor, menolak rentang yang melewati 999999 |
| `GET /api/workers/offline-list/` | Device token; `{ workers: [{ name, pin_stored, pin_format }], generated_at }` |
| `POST /api/payment-proofs/` | Device token, multipart, `proof_id` idempotent; upload saja, tanpa endpoint baca/update/delete |

Autentikasi device normal 30 hari; token renewal diterima sampai 60 hari sejak
issue. Header `X-Device-Registration-Token` tetap otoritas, allowlist jaringan
bukan penggantinya. Produksi memerlukan injection proxy tepercaya, bukan secret
browser. Frontend masih registrasi body kosong, belum memakai renewal.

PIN online dan batch **saat ini** memakai throttle bersama: 10 gagal → 5 menit,
429 + `Retry-After`, cache file lintas-proses satu host. Verifikasi mendukung
bcrypt mentah, hash Django, dan teks legacy.**Gap keputusan offline:** PIN per nota dan verifikasi server per PIN unik terpasang dua sisi (slice 2, 2026-10-09; throttle per-device dipertahankan, bukan dihapus). Sisa: sunset envelope lama, pemulihan review berotorisasi, ikatan worker sebelum commit, refill penomoran, rekonsiliasi Inventory, dan uji tablet dengan LAN dicabut. Jangan mendokumentasikan sunset sebagai sudah terjadi.

**Gap nomor null:** backend menerima null tetapi tidak memberi nomor final saat
sync. Riwayat tidak menampilkannya; upload bukti melewati pemeriksaan prefix
jika nota null. Ini keterbatasan isolasi device, bukan bukti scoping aman.

## 6. Kebutuhan sumber yang bukan fitur aktif

Kebutuhan awal tetap dicatat, tetapi status di bawah mengalahkan pembacaan bahwa
semuanya sudah harus ada pada API hari ini. Jangan implementasi tanpa scope,
kontrak, dan keputusan/izin yang diperlukan.

| Kebutuhan | Status/batas |
| --- | --- |
| Lokasi rak | Tidak diperlukan menurut keputusan 2026-09-26 |
| Alias pencarian | Kode ada (2026-10-04): jalur kandidat-dulu + tabel `item_alias` aktif otomatis di PostgreSQL bila tabel tersedia; fallback trigram lama bila tabel hilang atau query alias gagal. DDL/impor/benchmark ada di [ITEM_ALIAS_SEARCH_DESIGN.md](ITEM_ALIAS_SEARCH_DESIGN.md), pengisi tabel `tools/import_item_alias.py` |
| Cari → cart ±5 detik | Target UX, belum benchmark perangkat kasir |
| Approval harga di bawah floor | Belum ada; saat ini ditolak. Master perlu request/approver store |
| Harga special toko/tukang | Milik Master, bukan turunan floor/ceiling buatan POS |
| Shift, modal awal, kas fisik, kas masuk/keluar + alasan | Belum ada; perlu ownership/desain penyimpanan |
| Void, return/refund | Kode ada (2026-10-06): event append-only `refund_event` + `POST /api/refunds/`, riwayat `scope=all`/`refund_status`, banner status di cetak ulang. Approval = PIN worker sah (belum ada kolom role), efek stok tetap milik Inventory. Aturan + gap di [REFUND_DESIGN.md](REFUND_DESIGN.md) |
| Pembulatan 500/1000 sebagai baris nota | Belum ada; saran nominal tunai tidak mengubah total |
| Custom amount | Belum ada; bertentangan dengan FK item saat ini |
| Tempo → piutang/invoice | Belum ada; perlu kontrak Finance |
| `DIKIRIM`, alamat/zona `DIANTAR` | Belum dipersist; UI pickup/delivery + ongkir manual bukan pengganti desain |
| Tarif zona, approval ongkir gratis/reduced | Belum ada acuan tarif/approval; tidak dapat diklaim tervalidasi |
| “Penjualan di atas total ongkir + floor” | Makna agregat belum dikunci; jangan menganggap check harga per baris menyelesaikannya |
| Account/login dari kutipan awal | Diganti keputusan POS tanpa login; visibilitas per device, bukan sesi kasir |
| Audit/event dan device per transaksi | Belum ada representasi lengkap; perlu skema yang disetujui |
| `buka_dus` | Menyentuh Inventory, bukan write POS tambahan |

Cetak nota/surat jalan DIANTAR dan history reprint sudah ada di frontend.
Penandaan void/retur/refund pada kertas cetak ulang disusun di `receipt.js`
(banner status) tetapi hasil printer fisiknya belum diuji. `DIKIRIM`,
deployment bridge, dan hasil printer fisik tidak boleh diklaim selesai karena
kode cetak sudah ada.

## 7. Definisi selesai dan verifikasi aman

- Perubahan kontrak mempunyai contoh payload dan konsumen yang kompatibel.
- Jalur uang/stok/identitas diuji sukses **dan** gagal, termasuk retry/conflict.
- Stok 5 jual 8, stok 0 ditolak, retry ID sama tanpa stok ganda adalah contoh
  wajib; skenario 5 nota offline adalah target belum terbukti, bukan hasil uji.
- Dokumentasi membedakan requirement, implementasi, dan hasil verifikasi.
- Perubahan pencarian item diuji di `pos.tests.ItemAliasSearchTests` (frasa
  alias menemukan item; kueri nama menghasilkan daftar dan urutan yang sama;
  tabel alias hilang → mundur ke jalur lama, bukan error).
- Gunakan [verifikasi aman](../../README.md#verifikasi-aman). `manage.py test`
  dengan engine PostgreSQL menolak berjalan tanpa database uji disposable
  (`POS_TEST_DATABASE_URL` atau `POS_TEST_RUN=1`); suite PG dijalankan via
  `tools/run_pg_tests.py`, bukan dengan `DATABASE_URL` operasional.
- Tes yang butuh PostgreSQL memakai `ExternalPostgresTableMixin`: TEMP TABLE
  sekali pakai + skip eksplisit di SQLite. TEMP TABLE tidak membuktikan
  locking lintas-sesi atau race sebenarnya; klaim tidak lebih dari itu.
- SQLite smoke tests tidak membuktikan PostgreSQL trigram, locking, race,
  constraint, atau TEMP-table integration.
- Tinjau diff, pertahankan perubahan pengguna, dan jangan commit/push kecuali
  diminta. Komentar menjelaskan alasan keputusan, bukan hanya mengulang kode.

## 8. Wajib tanya sebelum perubahan

- Skema: device per nota, fulfillment/alamat, shift, approval, pembulatan,
  custom amount, atau perubahan lanjutan pada tabel event void/return/refund
  (desain dan DDL awalnya sudah disetujui pemilik 2026-10-06 —
  [REFUND_DESIGN.md](REFUND_DESIGN.md)).
- Kontrak Finance/Inventory/Master dan penyelesaian konflik stok offline.
- Penomoran ketika cadangan asli habis, atribusi sync, retensi/akses bukti,
  approval ongkir, RBAC, atau aturan bisnis yang belum dikunci.
- Perubahan yang menyentuh rahasia, data produksi, scope lain, atau perubahan
  kontributor lain. Dokumen yang mengandung gap bukan izin memperbaiki kode.
