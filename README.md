# POS 21733

POS web untuk tablet kasir: **Vue 3 + Vite**, **Django 5.2 + Django REST
Framework**, dan PostgreSQL. POS adalah modul pertama dari ekosistem modular
monolith; Inventory, Finance, dan Super Admin/Master belum diimplementasikan.

**Status ditinjau dari kode: 2026-10-01**, ditambah catatan implementasi
2026-10-08. Offline-first adalah kebutuhan yang sudah disetujui, **bukan fitur
yang sudah lengkap**. Penyimpanan lokal, cetak LAN, katalog tersimpan
(salinan seluruh katalog + pencarian lokal), dan gerbang/cooldown PIN ada,
tetapi PWA, sync otomatis banyak nota, dan lifecycle offline masih mempunyai
gap. Tinjauan ini bukan uji produksi; katalog tersimpan belum diuji di tablet
dengan LAN dicabut.

## Peta dokumentasi dan sumber kebenaran

Tidak diperlukan PRD terpisah untuk scope POS saat ini. Gunakan dokumen yang
ada sesuai perannya, tanpa menyalin semua aturan ke setiap berkas:

| Dokumen | Peran |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Keputusan bisnis/arsitektur yang disetujui, status implementasi, keputusan terbuka |
| [AGENTS.md](AGENTS.md) | Aturan kerja seluruh repo, scope, rahasia, database, Git |
| [Aturan POS](POS/AGENTS.md) | Batas kerja modul POS |
| [Pedoman backend](POS/pos-backend/AGENTS.md) / [frontend](POS/pos-frontend/AGENTS.md) | Konvensi lokal, kontrak terpasang, kebutuhan yang ditunda |
| [Handoff frontend](POS/FRONTEND_HANDOFF.md) | Status kontrak dan pekerjaan lintas-sisi |
| [Registrasi device](POS/DEVICE_AUTO_REGISTRATION_HANDOFF.md) | Auto-registration, renewal, trust model proxy |
| [Desain offline/PIN](POS/pos-backend/OFFLINE_SYNC_DESIGN.md) | Kontrak batch saat ini vs revisi yang belum terpasang |
| [Desain bukti transfer](POS/pos-backend/PAYMENT_PROOF_DESIGN.md) | Upload, penyimpanan, kompresi, batas akses/retensi |
| [Desain jalur cetak](POS/pos-backend/PRINT_PATH_DESIGN.md) | Finalisasi, nota/surat jalan, batas printer/bridge |
| [Desain void/retur/refund](POS/pos-backend/REFUND_DESIGN.md) | Event append-only, aturan uang vs barang, jalur lintas device, gap |
| [README bridge](POS/print-bridge/README.md) | Setup dan diagnosis penghubung cetak LAN |

Aturan kerja dan keputusan bisnis memiliki peran berbeda, bukan tangga untuk
menimpa keputusan secara diam-diam. Dokumen desain harus membedakan
**disetujui**, **diimplementasikan**, **usulan**, dan **terverifikasi**. Bila
kode tidak sesuai keputusan, laporkan gap; jangan menganggap kode mengganti
keputusan pemilik.

## Struktur proyek

```text
pos-21733/
├── AGENTS.md
├── ARCHITECTURE.md
├── README.md
└── POS/
    ├── AGENTS.md
    ├── Start POS.bat
    ├── DEVICE_AUTO_REGISTRATION_HANDOFF.md
    ├── FRONTEND_HANDOFF.md
    ├── ERD.png
    ├── docs/             # alur bisnis, panduan, schema.dbml, generator
    ├── pos-backend/      # API Django/DRF dan desain kontrak
    ├── pos-frontend/     # layar kasir Vue dan penyimpanan Dexie
    └── print-bridge/     # relai ESC/POS stdlib Python di LAN
```

## Menjalankan untuk development / LAN

Prasyarat: Python yang kompatibel dengan Django 5.2, Node
`^22.18.0 || >=24.12.0`, npm, dan database PostgreSQL dengan skema POS yang sudah
ada. Repo tidak membuat tabel bisnis secara otomatis.

### Windows: peluncur dua server

1. Buat virtual environment, dari root repo:
   ```powershell
   cd POS/pos-backend
   python -m venv venv
   .\venv\Scripts\python.exe tools\setup_env.py
   ```
   Setup meminta koneksi database dari pemilik, membuat rahasia lokal yang
   belum tersedia, menyamakan token proxy frontend, dan menambahkan konfigurasi
   identitas toko. Isi data toko yang benar sebelum menggunakan nota resmi.
2. Klik [Start POS.bat](POS/Start%20POS.bat).

Peluncur memeriksa konfigurasi, mendeteksi IP LAN, lalu **memasang dependency
setiap kali dijalankan** di jendela backend/frontend (`pip install -r
requirements.txt` dan `npm install --prefer-offline`). Server hanya dimulai
jika instalasi berhasil. Ini bukan peluncur yang dijamin bekerja offline pada
mesin yang dependency/cache-nya belum lengkap; instalasi dapat mengubah
`node_modules`/lockfile sesuai perilaku npm.

Backend mendengarkan `0.0.0.0:8000`; Vite `0.0.0.0:5173`. Tablet membuka
`http://<IP-LAN>:5173`; seluruh `/api` diproxy Vite ke localhost backend.
Jika 5173 sudah dipakai, gunakan port yang dilaporkan Vite. Batasi firewall ke
jaringan toko tepercaya. `ALLOWED_HOSTS` hanya localhost sehingga akses langsung
ke Django dari IP LAN ditolak; itu bukan pengganti firewall port 8000.

**Peluncur tidak menyalakan print bridge.** Jalankan bridge terpisah mengikuti
[README-nya](POS/print-bridge/README.md). `/print` diproxy ke
`PRINT_BRIDGE_TARGET` (default `http://127.0.0.1:9101`), yaitu alamat komputer
bridge, **bukan** printer raw TCP port 9100. Tutup jendela server/bridge untuk
menghentikannya. `runserver` dan Vite dev server bukan server produksi.

### Menjalankan backend/frontend secara terpisah

Dari folder backend, setelah environment dikonfigurasi:

```bash
# Git Bash di Windows
source venv/Scripts/activate
# Linux/macOS: source venv/bin/activate
python -m pip install -r requirements.txt
python manage.py runserver
```

PowerShell memakai `.\venv\Scripts\Activate.ps1`. Endpoint item berada di
`http://127.0.0.1:8000/api/items/`; `/api/` sendiri bukan halaman daftar API.

Di terminal lain:

```bash
cd POS/pos-frontend
npm install
npm run dev
```

Layar lokal: `http://localhost:5173`. State checkout aktif berada di komponen;
Pinia/Vue Router terpasang sebagai scaffolding, bukan pengelola seluruh alur
kasir. Dexie menyimpan data lokal; `bcryptjs` dipakai untuk pencocokan PIN lokal.

**Pengujian memakai server yang sudah menyala.** Bila backend (`:8000`) dan
frontend (`:5173`) sudah dijalankan pemilik, pengujian browser/API memakai
port itu apa adanya — jangan membuka terminal/server baru di port lain.
Server ganda di port berbeda memecah proxy `/api`, state Dexie per origin,
dan hasil uji sehingga sulit dibandingkan.

## Konfigurasi dan rahasia

Backend membutuhkan `DATABASE_URL`, `SECRET_KEY`, `DEVICE_AUTH_SIGNING_KEY`,
dan `DEVICE_REGISTRATION_TOKEN`. Contoh nama variabel ada pada
[template backend](POS/pos-backend/.env.example) dan
[template frontend](POS/pos-frontend/.env.local.example), bukan nilai produksi.
Jangan mengirim rahasia melalui chat, source, screenshot, atau log.

Dari folder backend, pemeriksaan konfigurasi tanpa menulis:

```powershell
.\venv\Scripts\python.exe tools\setup_env.py --check
```

`--check` tidak membuktikan koneksi database, tabel, proxy, atau printer sehat.
`--force` adalah **rotasi** tiga rahasia, bukan diagnosis rutin: signing key
baru membatalkan token device lama, termasuk renewal dengan key lama. Jangan
jalankan tanpa persetujuan dan rencana pemulihan perangkat.

Frontend memakai berkas lokal ter-ignore:

| Variabel | Tujuan/batas |
| --- | --- |
| `DEVICE_REGISTRATION_TOKEN` | Rahasia Node/proxy; tanpa prefix `VITE_`, harus sama dengan backend |
| `PRINT_BRIDGE_TARGET` | Target HTTP proxy print (komputer bridge, umumnya port 9101) |
| `VITE_STORE_NAME`, `VITE_STORE_ADDRESS`, `VITE_STORE_PHONE`, `VITE_STORE_CODE` | Data outlet yang memang tercetak; nama kosong menggunakan `TOKO` |
| `VITE_PRINT_BRIDGE_URL` | Default `/print`, same-origin |
| `VITE_PRINT_TOKEN` | Opsional, **masuk bundle browser**; hanya pagar LAN, bukan secret/auth kuat |

Nama lama `VITE_DEVICE_REGISTRATION_TOKEN` masih didukung sebagai fallback
kode, tetapi jangan dipakai untuk setup baru karena berpotensi masuk bundle.
Restart dev server setelah perubahan konfigurasi; build produksi membutuhkan
build ulang untuk nilai `VITE_*`.

Pada mesin client, buat ulang `venv` dan `node_modules`, jangan menyalinnya dari
mesin lain. Semua tablet yang memakai satu backend mengikuti signing key
backend itu. Backend berbeda dengan signing key berbeda tidak otomatis menerima
token yang diterbitkan mesin lain. Bila koneksi produksi dipakai, menjalankan
POS nyata dapat menulis transaksi/stok di sana; jangan menjadikannya sandbox.

## API yang terpasang

| Method | Path | Auth | Catatan |
| --- | --- | --- | --- |
| GET | `/api/items/` | Tidak wajib | Pagination 30, search nama/varian, ceiling/floor/stok tanpa modal |
| POST | `/api/devices/register/` | Header secret proxy | Awal 201; renewal device yang sama 200 |
| POST | `/api/devices/<device_id>/transaction-sequence/` | Device | Rentang 1000 nomor |
| GET | `/api/workers/offline-list/` | Device | Direktori **sensitif**, nilai tersimpan + format |
| POST | `/api/transactions/` | Device + PIN | Checkout online `{ pin, transaction }` |
| POST | `/api/transactions/sync/` | Device + PIN batch | **Masih kontrak lama satu PIN per batch**, maksimal 200 nota |
| GET | `/api/transactions/history/` | Device | 20/halaman; `date=YYYY-MM-DD` opsional, UI default hari ini |
| GET | `/api/transactions/history/<transaction_id>/` | Device | Nota dan detail, per prefix device |
| POST | `/api/payment-proofs/` | Device | Multipart, idempotent `proof_id`; tidak ada endpoint baca gambar |

Device token normal berlaku 30 hari; backend renewal menerima token lama sampai
umur 60 hari. **Frontend belum memakai renewal body**, sehingga registrasi
ulang saat ini dapat menghasilkan device/kode baru. Kebijakan kode habis,
pensiun, dan daur ulang masih terbuka.

POS tidak punya login worker; worker ditentukan dari PIN server, bukan nama
ketikan atau ID klien. History per **device**, bukan per kasir/session. Nota
bernomor device lain dibalas 404. Nota null tidak terlihat di history; upload
proof nota null saat ini melewati pemeriksaan prefix, sehingga jangan
mengklaim isolasi ownership lengkap untuk nota tersebut.

## Database dan bukti transfer

Semua model POS `managed = False`. Perubahan skema eksternal (DDL maupun
migrasi) membutuhkan izin pemilik, bukan dilakukan otomatis saat setup.
Identity item `item_name + variant`; detail transaksi
`sync_id + item_name + variant`. Nota `transaction_id` adalah nomor tampilan,
bukan foreign key detail. Backend menerima null tetapi belum mengalokasikan
nomor final pada sync.

Tabel utama: `item`, `worker`, `device`, `device_transaction_sequence`,
`transactions`, `transaction_detail`, `payment_proof`. Referensi ekspor skema
ada di [schema.dbml](POS/docs/schema.dbml) dan [ERD](POS/ERD.png); keduanya
snapshot dokumentasi, bukan bukti skema database sekarang telah diaudit.
Skrip penghasil keduanya (`tools/export_db_schema.py` dan
`tools/render_erd_png.py`) sudah dihapus saat bersih-bersih repo, jadi
menyegarkan snapshot berarti menulis ekspor baru lebih dulu; ekspor skema
menyentuh database dan karena itu butuh izin pemilik.

Bukti dipersist di Dexie saat dipilih, dikaitkan ke nota melalui `sync_id`, lalu
upload setelah nota server diterima. Metadata server menyimpan **key**, bukan
URL: `payment_proofs/<tahun>/<bulan>/<proof_id>.<ext>`. Development memakai
filesystem privat di backend; `/media/` tidak dipublikasikan. Pilihan storage
produksi dan akses baca/retensi belum selesai; blob lokal tetap disimpan setelah
upload. Lihat [desain bukti](POS/pos-backend/PAYMENT_PROOF_DESIGN.md).

## Verifikasi aman

**Jangan menjalankan `manage.py test` dengan database bersama/produksi.**
Test runner membuat/menghapus database `test_*` di server yang dituju
`DATABASE_URL`. Sejak 2026-10-02, `manage.py test` dengan engine PostgreSQL
menolak berjalan kecuali memakai database uji disposable: set
`POS_TEST_DATABASE_URL` (minta ke pemilik database, terpisah dari produksi)
atau `POS_TEST_RUN=1` bila `DATABASE_URL` saat ini sudah disposable.
SQLite tidak kena guard ini.

Smoke test tanpa akses database bisnis, contoh **Git Bash**, dari backend:

```bash
DATABASE_URL='sqlite://:memory:' \
SECRET_KEY='local-smoke-test-only' \
DEVICE_AUTH_SIGNING_KEY='local-smoke-test-only' \
DEVICE_REGISTRATION_TOKEN='local-smoke-test-only' \
venv/Scripts/python.exe manage.py test pos
```

Nilai di atas dummy khusus pengujian, bukan kredensial operasional. Full
suite di SQLite: tes yang butuh PostgreSQL di-skip eksplisit
(`ExternalPostgresTableMixin`), sisanya harus lulus tanpa koneksi database
bisnis. Status 2026-10-02: **152 lulus, 19 di-skip eksplisit (3 kelas PG)**.
Suite yang sama di PostgreSQL disposable: **171 lulus, 0 gagal** (PostgreSQL
18, database `pos_test` terpisah; satu ekspektasi urutan riwayat yang salah
tulis diperbaiki, bukan kode aplikasi). SQLite tidak membuktikan trigram,
locking, race, atau TEMP-table PostgreSQL; jangan mengandalkan hitungan tes
lama.

Suite PostgreSQL di database disposable — checkout, retry idempoten,
konflik 409, stok, device, history, item/trigram — dari backend:

```bash
POS_TEST_DATABASE_URL='postgresql://user:sandi@host:5432/pos_test' \
venv/Scripts/python.exe tools/run_pg_tests.py
```

Runner menolak bila URL uji sama dengan `DATABASE_URL` operasional dan tidak
pernah mencetak rahasia apa pun. Tes PG memakai tabel eksternal sebagai
TEMP TABLE sekali pakai; tabel operasional tidak disentuh
(`managed=False`). Prasyarat sekali saja per server: database `pos_test`
(disposable, dibuat 2026-10-02 di server sample), ekstensi `pg_trgm` di
dalamnya, dan database maintenance kosong bernama `postgres` (Django
membutuhkannya untuk membuat/menghapus database `test_*`; tanpa itu teardown
gagal dengan `cannot drop the currently open database`). Batasan yang masih
terbuka: TEMP TABLE hidup per sesi koneksi sehingga suite ini **tidak**
membuktikan locking lintas-sesi atau pengiriman bersamaan yang sebenarnya;
itu butuh staging dengan tabel nyata dan izin pemilik. Bila database uji
tidak berizin memasang `pg_trgm`, tes pencarian di-skip eksplisit.

Frontend, dari direktori `POS/pos-frontend`:

```bash
npm test # regresi checkout (script Vue aktual + storage/network doubles)
npm run lint:check # oxlint + eslint read-only; `npm run lint` memakai --fix
npm run build # memeriksa bundle, bukan bukti aturan bisnis lulus
```

Uji checkout memakai script Vue aktual dengan doubles, bukan browser,
IndexedDB, server, atau printer nyata. Tidak ada script typecheck atau
framework test tambahan. Bridge memiliki selftest lokal tanpa printer:

```bash
python POS/print-bridge/bridge.py --selftest
```

Pemeriksaan otomatis: setiap push/PR menjalankan
[.github/workflows/pos-checks.yml](.github/workflows/pos-checks.yml)
— smoke SQLite, suite PostgreSQL disposable, frontend, dan bridge selftest
— tanpa rahasia operasional.

Uji tablet, LAN/internet putus, PIN, foto HEIC, kertas/cutter, dan karbon harus
terpisah dari pemeriksaan kode. Daftar kriteria target ada di desain masing-
masing; jangan melaporkan semuanya lulus karena file berhasil digenerate.

## Diagram dan panduan

- [Sumber alur bisnis Mermaid](POS/docs/pos_business_flow.mmd) (sumber utama) →
  [SVG](POS/docs/pos_business_flow.svg), [PNG](POS/docs/pos_business_flow.png),
  [draw.io](POS/docs/pos_business_flow.drawio), [GraphML](POS/docs/pos_business_flow.xml).
  Ubah sumber alur, bukan hasilnya. Generator Python yang dulu merender berkas
  hasil sudah dihapus saat bersih-bersih repo, jadi render ulang memakai Mermaid
  CLI (`npx @mermaid-js/mermaid-cli`) dan simpan dengan nama berkas yang sama;
  PNG membutuhkan Chrome/Chromium. XML valid bukan bukti import draw.io/visual
  benar: konektor output saat ini berbasis waypoint absolut dan tidak mengikuti
  node yang dipindah. Validasi perimeter diamond/import terakhir belum selesai.
- [Desain nota](POS/docs/desain_nota_75x65.pdf),
  [panduan printer](POS/docs/panduan_printer_pos.pdf), dan
  [kode floor](POS/docs/kode_harga_floor.pdf) sekarang artefak beku: generator
  Python-nya di folder docs sudah dihapus saat bersih-bersih repo, jadi perubahan
  tata letak berarti memperbarui dokumennya secara manual.
- [Presentasi interaktif](POS/docs/pos_business_process_interactive.pptx) adalah
  artefak lama; belum diregenerasi mengikuti alur terbaru.
- [Pertimbangan Tailscale](POS/docs/pertimbangan_tailscale_pos.docx) adalah
  kajian opsional, bukan keputusan deployment; tampilan Word belum diverifikasi.

## Yang belum selesai

Prioritas gap yang terlihat di sumber, bukan scope implementasi otomatis:

- Offline end-to-end dan revisi PIN per nota; lifecycle final/pending ambigu
  yang masih bisa dibuang; PWA dan sync multi-nota. Gerbang PIN sebelum segel,
  cooldown PIN lokal, dan salinan katalog (seluruh katalog + pencarian lokal,
  [§3.3](POS/pos-backend/OFFLINE_SYNC_DESIGN.md)) sudah ada di perangkat sejak
  2026-10-08, tetapi ketiganya belum diuji di tablet dengan LAN dicabut, dan
  halaman aplikasi belum dijamin terbuka tanpa server.
- Cadangan nomor asli/fallback habis dan device ownership untuk nota null.
- Renewal device frontend, role/approval, void/return/refund, shift, pembulatan,
  custom amount, tempo/integrasi Finance/Inventory/Master.
- Fulfillment/alamat/zona persisted; history reprint menebak delivery dari
  ongkir > 0 sehingga delivery berongkir nol tidak dapat direkonstruksi tepat.
- Proof read/retention/quota dan deployment produksi. `DEBUG=True` masih
  hardcoded; belum ada konfigurasi server produksi yang siap operasi.
- Verifikasi perangkat kasir dan artefak diagram/dokumen yang disebut di atas.

## Ekspor Google Sheets

Alat ini berada di repo terpisah
[ApCPT42/DB-to-sheets](https://github.com/ApCPT42/DB-to-sheets), bukan bagian
runtime POS. Instruksi repo alat itu berlaku untuk menjalankannya. Jangan
mengekspor `item` dengan modal atau `worker` dengan hash/PIN ke layanan lain
hanya karena koneksi databasenya tersedia.

## Aturan singkat development

- Jangan commit konfigurasi rahasia, `venv`, `node_modules`, media/bukti, atau
  ekspor database. Gunakan contoh sintetis, bukan data kasir nyata.
- Jangan memperluas scope ke modul baru atau mengubah keputusan/skema saat
  memperbaiki dokumentasi.
- Setiap perubahan perilaku harus memperbarui dokumen yang memiliki aturan itu,
  diuji pada jalur sukses/gagal, dan dilaporkan batas verifikasinya.
- Commit/push hanya jika diminta; perubahan kontributor lain tetap dipertahankan.
