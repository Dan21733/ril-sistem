# Sinkronisasi Offline POS dan Aturan PIN

**Status tinjauan sumber: 2026-10-01**, ditambah catatan implementasi
2026-10-08. Offline-first adalah kebutuhan yang sudah disetujui, tetapi belum
berjalan end-to-end. Endpoint batch saat ini masih memakai satu PIN per batch,
**bukan** PIN per nota seperti revisi yang disetujui pada 2026-09-29.
Dokumentasi ini tidak mengubah kontrak aplikasi.

**2026-10-08 — gerbang segel dan cooldown PIN lokal (frontend saja, §3.1-3.2).**
Dua kekurangan yang tercatat di §2 sudah ditutup di sisi perangkat: nota tidak
lagi disegel sebelum PIN terbukti, dan percobaan PIN gagal dibatasi hitungan
lokal. Bukti yang ada: tes script checkout (4 kasus baru) dan pengukuran di
browser sungguhan lewat dev server — penolakan gerbang saat permintaan API
digagalkan dari halaman (tanpa baris, tanpa nomor terpakai, tanpa cetak), pesan
cooldown beserta hitungan berjalan, dan kenaikan Dexie v4→v5 pada database yang
sudah berisi data lama tanpa kehilangan store lain. Yang **belum** diuji:
perangkat kasir dengan LAN benar-benar dicabut, dan siklus baterai/reload di
tablet nyata.

**2026-10-08 — salinan katalog di perangkat (frontend saja, §3.3).**
Pencarian dan penjualan kini bisa **dimulai** dari salinan perangkat: seluruh
katalog (2.924 varian / ~15 halaman × 200 baris sejak 2026-10-12; sebelumnya
98 halaman × 30 baris) disalin ke
Dexie `items` bersama cap waktunya dalam satu transaksi (Dexie v6,
`catalog_meta`), disegarkan saat aplikasi dibuka dan berkala tiap 15 menit, dan
dipakai ketika `/api/items/` tidak menjawab. Bukti: 95 tes script (termasuk
`itemRepository`, `catalogCache`, `localCatalogSearch`, dan harness `App.vue`)
plus pengukuran di browser sungguhan — salinan penuh benar-benar tertulis,
pencarian serta "Muat lebih banyak" tetap jalan saat permintaan API digagalkan
dari halaman, dan pesan statusnya diukur pada 365x601 / 1007x601 / 601x1007 /
1440x900 dalam mode terang dan gelap. Dua cacat ditemukan dan diperbaiki lewat
verifikasi itu (§3.3). Yang **belum**: perangkat kasir dengan LAN benar-benar
dicabut dan kuota/eviksi penyimpanan tablet. PWA keluar scope 2026-10-09 — halaman memang tidak dijamin bisa dibuka ulang tanpa server penyaji frontend.

## Rujukan dan batas dokumen

- [ARCHITECTURE.md](../../ARCHITECTURE.md) memiliki keputusan bisnis.
- [Aturan repo](../../AGENTS.md), [aturan POS](../AGENTS.md), dan
  [aturan backend](AGENTS.md) mengatur cara kerja dan keselamatan perubahan.
- [Handoff frontend](../FRONTEND_HANDOFF.md) mencatat pekerjaan lintas-sisi;
  [jalur cetak](PRINT_PATH_DESIGN.md) dan
  [bukti transfer](PAYMENT_PROOF_DESIGN.md) memiliki kontrak terkait.
- Semua model server `managed = False`. Perubahan skema, data, atau aturan
  identitas tetap memerlukan izin; adanya rancangan bukan izin menjalankannya.
- Persetujuan, implementasi, dan bukti pengujian adalah tiga status berbeda.
  Tinjauan ini hanya membaca kode, tidak mengakses database atau printer toko.

## 1. Aturan yang berlaku

- POS tidak memiliki akun/username/password/login worker. Device token
  mengidentifikasi perangkat; backend menentukan worker dari PIN.
- Frontend tidak menentukan atau mengirim `worker_id`.
- Stok server/Inventory otoritatif. Offline memakai snapshot terakhir:
  stok > 0 boleh dijual walau kuantitas melebihi stok; stok <= 0 ditolak.
  Nota yang lolos offline tetap dapat ditolak server saat sync.
- `sync_id` adalah identitas transaksi/idempotensi; retry mempertahankan
  identitas dan fakta penjualan, bukan membuat transaksi pengganti.
- Nota final tidak diedit/dihapus. Penolakan atau konflik harus menyisakan
  nota dan alasan untuk tindak lanjut, bukan menghilangkan jejak.
- Revisi 2026-09-29: PIN wajib memfinalisasi **setiap** penjualan offline.
  Tanpa PIN yang cocok lokal, tidak ada nota final/cetak. Nama nota berasal
  dari pencocokan lokal; atribusi worker resmi tetap dilakukan server.
- Revisi itu menggantikan aturan lama bahwa offline tidak membutuhkan PIN dan
  semua nota diatribusikan kepada worker dari satu PIN batch.
- Cetak adalah best effort setelah finalisasi: printer gagal tidak membatalkan
  penjualan. Jalur cetak memakai bridge LAN, bukan internet.

## 2. Implementasi yang benar-benar ada

| Bagian | Status berdasarkan kode |
| --- | --- |
| Checkout online | `{ pin, transaction }`, device token wajib, validasi server, 201/200/400/401/409/429 |
| Penyimpanan checkout | Dexie menyimpan payload/PIN asli/snapshot dan pengaitan proof atomik; retry individual tanpa PIN pengganti |
| Banyak nota offline | Terpasang di frontend 2026-10-09 (opsi A slice 1, branch `opsi-a-antrean-lokal`): N nota boleh antre, keranjang bebas untuk jualan berikutnya, kirim ulang per nota; batch sync tetap menyusul |
| Batch sync frontend | Belum ada pemanggil `/api/transactions/sync/` |
| Batch sync backend | Sudah ada **kontrak lama**: PIN top-level sekali per batch, maksimal 200 nota, throttle bersama |
| PIN per nota pada batch | PIN asli sudah disimpan checkout individual; format/per-PIN unik pada batch belum dipasang |
| Direktori worker | Endpoint device-authenticated; disimpan di Dexie `workers`, dicocokkan dengan `bcryptjs`/plaintext |
| Finalisasi/cetak lokal | PIN cocok lokal mengizinkan cetak segera, termasuk ketika server tidak terjangkau; PIN tidak dikenal menunggu penerimaan server |
| Gerbang pembuatan baris offline | **Terpasang 2026-10-08 (frontend, §3.1):** PIN tak dikenal + server tak terjangkau ditolak sebelum nomor dipakai atau baris dibuat. PIN tak dikenal saat server terjangkau masih mengikuti jalur lama (segel, final setelah server menerima) |
| Cooldown PIN lokal | **Terpasang 2026-10-08 (frontend, §3.2):** 5 kegagalan → 30 detik, berlipat sampai 10 menit; disimpan di Dexie `pin_attempts`. Penjaga UX, bukan batas keamanan |
| Cache katalog/pencarian offline | **Terpasang 2026-10-08 (frontend, §3.3):** seluruh katalog disalin ke Dexie `items` + cap waktu `catalog_meta`; saat server tidak menjawab, daftar dan pencarian dilayani dari salinan |
| PWA/service worker | Keluar scope 2026-10-09; halaman tidak dijamin dapat dibuka ulang tanpa server penyaji frontend |
| Bukti transfer dan cetak | Antrean terpisah sudah ada; tidak berarti transaksi otomatis tersinkron |

Dexie saat ini versi 6: device, sequence_ranges, items, catalog_meta,
transactions, payment_proofs, print_jobs, workers, pin_attempts. Detail skema
klien ada di
[db.js](../pos-frontend/src/services/storage/db.js).

### 2.1 Prasyarat offline: apa yang membuat POS bisa berjalan tanpa server

Daftar berikut menjawab pertanyaan "apa saja yang membuat POS online bisa
berpindah ke offline". Statusnya menunjukkan **apa yang ada di repo**, bukan
klaim lulus: rincian dan batasnya ada di bagian yang dirujuk, dan kriteria lulus
tetap §8 (belum satu pun dinyatakan lulus, karena uji perangkat kasir belum
dijalankan).

| Prasyarat | Status di repo | Di mana / batasnya |
| --- | --- | --- |
| Deteksi server hidup/mati | Terpasang | `serverReachability.js`: probe `/api/items/?page=1`, timeout 4 dtk, 2 percobaan, hasil segar 20 dtk; hanya 2xx yang dihitung hidup (KEPUTUSAN-14); `navigator.onLine` hanya jalan pintas, bukan bukti |
| Salinan katalog di perangkat | Terpasang 2026-10-08 | §3.3; seluruh katalog + `catalog_meta`, pencarian lokal tanpa alias |
| Direktori kasir + pencocokan PIN lokal | Terpasang | §6/§7 (KEPUTUSAN-9/10); PIN tersimpan dicocokkan di perangkat |
| Jatah nomor nota lokal | Terpasang | `sequence_ranges`; jatah 1.000 nomor dari server (`SEQUENCE_RANGE_SIZE`), diisi saat online **setelah** jatah habis |
| Tulis-lokal-dulu + status antrean | Terpasang | Nomor lokal + `saveTransaction()` sebelum HTTP; `draft/pending/syncing/unknown/synced/rejected/review/conflict`, `unknown` ditulis sebelum HTTP |
| Cetak lewat LAN tanpa internet | Terpasang | [jalur cetak](PRINT_PATH_DESIGN.md); `print_jobs` menyimpan byte ESC/POS dan mengirim ulang ke bridge |
| Gerbang PIN sebelum segel + cooldown | Terpasang 2026-10-08 | §3.1-3.2; PIN tak dikenal + server tak terjangkau → tanpa nota/nomor/cetak |
| Antrean bukti transfer | Terpasang | [bukti transfer](PAYMENT_PROOF_DESIGN.md); unggah tetap menuntut server, hanya ditunda |
| Batch sync PIN-per-nota | **Terpasang slice 2, 2026-10-09** | `syncTransactionBatch` + `resendQueuedBatch` (tombol "Kirim semua"; sejak 2026-10-09 tinggal di notifikasi antrean modal Riwayat Nota — baner di kolom keranjang dihapus atas permintaan pemilik); server menerima envelope baru PIN-per-nota dan envelope lama (§4) |
| Aturan nomor saat jatah habis | **Belum diputuskan** | §5 dan KEPUTUSAN-1/-8; sekarang nota lahir tanpa nomor (`transaction_id` null) |
| Kepemilikan device untuk nota null, riwayat penolakan yang bisa di-query, role approver | **Belum** | Butuh izin skema/peran; §5 dan §6 |
| PWA/service worker + penyajian hasil build | **Keluar scope 2026-10-09** | Bukan prasyarat offline; offline = backend tak terjangkau, server penyaji frontend dianggap hidup. Tambahan 2026-10-09: service worker menuntut secure context, sedangkan tablet dibuka dari `http://<IP-LAN>` yang bukan secure context — jadi SW tidak bisa terpasang sebelum ada HTTPS; ID aman non-HTTPS sudah ditangani `services/id.js` (dipakai nota, bukti, dan refund) |

Ringkasnya: prasyarat **pemicu** dan **bahan** sudah terpasang, tetapi jalur
menutup antrean (batch sync, penomoran cadangan) belum ada — jadi alur offline end-to-end belum bisa diklaim. Jaminan halaman tetap terbuka tanpa server penyaji frontend (PWA) bukan bagian alur ini sejak 2026-10-09.

### 2.2 Kondisi yang membuat POS harus berjalan offline

§2.1 mendaftar mekanismenya; bagian ini mendaftar **kondisinya**: apa yang putus,
apa yang dilakukan kode hari ini, dan apakah itu sudah benar. "Tertangani"
berarti jalurnya ada di kode — belum berarti lulus uji perangkat (lihat §8).

| Kondisi | Yang sebenarnya putus | Perilaku kode hari ini | Status |
| --- | --- | --- | --- |
| Tablet kehilangan WiFi total | Radio tablet (`navigator.onLine === false`) | Probe langsung sunyi; semua fetch gagal jaringan → gerbang menolak PIN tak dikenal dengan pesan jelas; katalog dari salinan; nota hanya untuk PIN cocok lokal; cetak ke bridge ikut antre | Tertangani, belum uji fisik |
| Backend `:8000` mati, frontend `:5173` hidup | Proses Django; proxy dev membalas 502 | **Diperbaiki 2026-10-09** (keputusan pemilik: hanya 2xx yang dihitung hidup): probe dan jalur submit mencatat 502 sebagai sunyi → gerbang menolak PIN tak dikenal dengan pesan "server tidak terjangkau", nomor tidak terpakai. Sebelumnya respons HTTP apa pun dihitung hidup sehingga gerbang terbuka untuk backend yang mati | Terpasang; bukti: tes `a 502 from a dead backend counts as silence` + verifikasi probe di browser |
| Frontend `:5173` mati | Dev server | Halaman tak bisa dibuka atau dimuat ulang; SPA yang sudah terbuka jalan sampai reload; reload = mati total. Dua akibat yang mudah salah dibaca: **pil status server ikut memerah** (probe menyapa origin halaman sendiri — `/api/items/?page=1` relatif — jadi mati-fronend tak bisa dibedakan dari mati-backend di layar; bukti segar dari katalog/checkout bisa menahan hijau sampai ±1 menit, ketuk pil = seketika), dan **jalur cetak ikut mati** karena `/print` diproksi dev server yang sama | Batasan yang diterima 2026-10-09; PWA keluar scope |
| Alamat backend berubah / target proxy salah | Konfigurasi sisi Node | Dari sisi tablet terlihat seperti WiFi mati (jalur offline bersih) atau seperti baris backend-mati (kalau proxy membalas 502); target hanya bisa diubah di server, bukan dari tablet; tidak ada diagnosis "salah alamat" di layar | Sebagian |
| Jaringan lambat (backend hidup tapi > 4 dtk) | Latensi | Probe menyatakan sunyi setelah 2 × 4 dtk → gerbang menolak PIN tak dikenal walau server sebenarnya bisa menjawab; submit sendiri timeout 30 dtk | Konservatif-aman; ambang belum diukur di jaringan toko |
| Aplikasi ditutup / tablet restart di tengah jalan | Proses browser | Dexie persist; antrean dibaca ulang dan checkout dikunci sampai terbaca; draft/pending/unknown mencegah duplikat | Terpasang, belum uji daya nyata |
| Penyimpanan browser dibersihkan / eviksi | Storage | Salinan hilang → pesan "belum tersimpan"; jatah nomor diminta ulang saat online; **jejak nota `unknown` yang hilang tidak bisa dipulihkan** (risiko duplikat/kehilangan); tanpa peringatan kuota | Sebagian; tanpa proteksi kuota |
| Jam tablet dimundurkan | Waktu | Cooldown PIN kebal (memakai waktu tertinggi yang pernah terlihat); **umur katalog tidak kebal** (dijepit ≥ 0 → salinan basi terlihat segar) | Celah kecil |
| Mati daya mendadak | Listrik | Tulis-lokal-dulu: yang sudah `saveTransaction` pulih sebagai draft/unknown; print job tersimpan | Terpasang di kode, belum uji nyata |
| Database backend mati / lambat | DB | Terlihat seperti baris lambat atau backend-mati, tergantung respons; tanpa pembeda khusus | Sebagian |
| Respons sukses yang identitasnya tidak cocok | Kontrak | `review` + penjualan diblokir sampai manusia memeriksa | Terpasang |
| 401 / device belum terdaftar | Identitas device | Auto-registrasi dicoba; gagal → `retryable`, "jangan membuat nota pengganti" | Terpasang |
| Bridge `:9101` / printer / kertas mati | Cetak | Penjualan tetap final; job antre + retry otomatis; header menunjukkan tidak tersambung (502 proxy diperlakukan benar sebagai mati di jalur ini, bukan lewat probe generik) | Terpasang |
| Cetak terputus di tengah struk | Kertas / daya printer | Dedup via `job_id` mencegah cetak ganda saat retry; struk setengah perlu cetak ulang manual dari riwayat | Sebagian |
| Dua tablet offline menjual stok yang sama | Konsistensi | Server menolak saat sync → ditahan review + blokir penjualan baru; tindak lanjut manusia | Alur terpasang, belum uji 2 perangkat |
| Jatah nomor habis saat offline | Penomoran | Nota lahir tanpa nomor (`transaction_id` null) | Menunggu keputusan pemilik (§5) |

Catatan pembeda dari baris backend-mati di atas: 502 yang sama dulu
diperlakukan **berbeda** di dua jalur. Jalur katalog dan sinkron katalog
melempar pada `!response.ok` sehingga 502 jatuh ke salinan lama (benar sejak
awal), tetapi jalur probe dan `POST /api/transactions/` memanggil
`noteServerAnswer()` untuk respons HTTP apa pun sehingga 502 tercatat sebagai
"server hidup" (salah — diperbaiki 2026-10-09, KEPUTUSAN-14). Batas yang
tersisa di jalur ini: 401/429 dari backend yang hidup pun kini tercatat sebagai
sunyi untuk keperluan probe (aman-konservatif: gerbang menutup), sedangkan
penanganan 401/429 di jalur submit tidak berubah.

**Pengamanan individual 2026-10-01:** pending/unknown/final lokal tidak dapat
hapus/ganti lewat UI; restore mengunci checkout sampai antrean terbaca. Original
PIN dan snapshot digunakan ulang. Final yang ditolak ditahan review, penjualan
baru diblokir. Nota legacy tanpa PIN/snapshot memerlukan review, bukan PIN baru.
Lihat aturan hasil ambigu di §5; ini bukan multi-nota offline end-to-end.

## 3. Target alur offline yang disetujui

```text
Barang dari cache → pembayaran → PIN lokal cocok
  → bekukan identitas/payload + simpan nota dan PIN untuk atribusi
  → tampilkan nota → antrekan cetak melalui LAN
Koneksi server kembali
  → kirim batch dengan PIN per nota → hasil per nota
  → sukses: tandai synced → unggah bukti transfer secara terpisah
  → ditolak/konflik: pertahankan nota dan alasan, tindak lanjut manusia
```

Ini **target**, bukan laporan bahwa seluruh alur tersebut sudah terpasang.

- PIN yang tidak cocok/tidak diisi tidak boleh menghasilkan nota offline final
  atau baris antrean penjualan offline yang sah.
- Antrean harus membawa PIN per nota karena worker tidak dipilih klien. Itu
  pengecualian sensitif dari aturan lama “jangan pernah persist PIN”. Checkout
  individual kini menyimpan PIN asli sampai synced/ditolak pasti nonfinal;
  format batch target tetap belum diterima API.
- Server target memverifikasi sekali per **PIN unik** untuk atribusi, bukan
  memindai worker untuk setiap nota. Validasi harga/stok/nomor tetap dilakukan.
- Target sync tidak memakai satu PIN operator sebagai gate dan tidak mengonsumsi
  counter throttle PIN online. Ini **bukan** berarti server berhenti
  memverifikasi PIN tiap identitas nota.
- PIN worker yang berubah sebelum sync dapat membuat nota tidak teratribusi;
  nota dan alasan tetap disimpan untuk tindak lanjut.
- Kirim nota lama terlebih dahulu dan satu batch in-flight per device.
- Campuran hasil sukses/ditolak/konflik tidak boleh menghapus nota lain.

### 3.1 Gerbang sebelum nota disegel (2026-10-08)

Keputusan pemilik: **PIN yang tidak cocok lokal dan server yang tidak
terjangkau tidak boleh menghasilkan nota apa pun.** Urutannya dipindahkan ke
depan — sebelum `nextTransactionId()` dan `saveTransaction()` — supaya nomor
asli tidak terpakai dan tidak ada baris yang lahir untuk penjualan yang belum
terbukti.

| PIN cocok lokal | Server terjangkau | Hasil |
| --- | --- | --- |
| ya | ya / tidak | segel + nomor + `local_final` + cetak segera (perilaku lama, tidak berubah) |
| tidak | ya | segel + nomor, tanpa cetak; final hanya setelah server menerima (perilaku lama) |
| tidak | tidak | **ditolak**: nomor tidak terpakai, tidak ada baris, tidak ada cetak, keranjang tetap utuh |

- "Terjangkau" bukan `navigator.onLine`: buktinya jawaban HTTP server
  (`src/services/api/serverReachability.js`). Panggilan API yang sukses
  menyegarkan bukti; kalau bukti sudah basi, perangkat menyelidik halaman
  pertama katalog (timeout 4 detik, satu percobaan ulang). `navigator.onLine`
  hanya dipakai sebagai jalan pintas negatif.
- PIN yang cocok lokal **tidak pernah** menunggu jaringan: itu yang membuat
  jualan tetap jalan saat server mati.
- Jalur kirim ulang nota tersimpan tidak melewati gerbang ini; PIN-nya sudah
  tersimpan bersama nota dan menahannya hanya akan menahan nota yang ada.

### 3.2 Cooldown PIN lokal (2026-10-08)

Keputusan pemilik: 5 kegagalan → tunggu 30 detik, tiap kegagalan berikutnya
berlipat dua sampai batas 10 menit; hitungan dihapus saat PIN cocok lokal atau
server menerima; percobaan lama (>15 menit) tidak ditumpuk.

- Baris tersimpan hanya **hitungan dan waktu** (`Dexie` v5, `pin_attempts`,
  satu baris per perangkat). PIN, potongan, atau hash-nya sengaja **tidak**
  disimpan: ruang enam digit terlalu kecil untuk sebuah berkas hash.
- `last_seen_at` menyimpan waktu tertinggi yang pernah terlihat sehingga
  memundurkan jam tablet tidak membuka blokir.
- Kegagalan penyimpanan tidak memblokir penjualan; server tetap verifikator
  akhir dan tetap punya throttle sendiri.
- Batas yang jujur: ini penjaga UX pada layar, bukan batas keamanan. Siapa pun
  yang menyalin IndexedDB tetap bisa menebak PIN di luar aplikasi (§6).

### 3.3 Salinan katalog di perangkat (2026-10-08)

Keputusan pemilik: **seluruh** katalog disimpan di tablet dan disegarkan
berkala, bukan hanya baris yang pernah dimuat. Yang dijual offline tetap
snapshot terakhir — harga pricelist dan stok saat salinan dibuat — dan server
adalah penentu akhir saat sync.

Yang terpasang di frontend:

- `src/services/catalog/catalogCache.js` mengambil seluruh halaman
  (`?page=n&page_size=200`, `MAX_PAGES = 200` sebagai pagar; server cap
  `max_page_size = 200`, default tanpa param tetap 30), memetakan
  barisnya lewat `mapApiItem()` yang **sama** dengan jalur online, lalu menulis
  ganti-utuh lewat `replaceCatalog()`.
- Penulisan item dan `catalog_meta.synced_at` berada dalam **satu transaksi**
  (Dexie v6). Item yang sudah hilang dari master ikut hilang dari tablet.
  Jawaban kosong atau halaman yang gagal **tidak** mengganti salinan lama.
- Satu sinkron berjalan pada satu waktu; pemanggil kedua ikut menunggu hasil
  yang sama, bukan mengunduh ulang.
- Pemicu: saat aplikasi dibuka, setelah daftar dari server dimuat, dan berkala
  tiap 15 menit (`CATALOG_REFRESH_INTERVAL_MS`). Ambang "basi" untuk peringatan:
  7 hari (`CATALOG_STALE_AFTER_MS`) — salinan tua tetap bisa dipakai, hanya
  diberi keterangan umur.
- Pencarian lokal (`src/utils/localCatalogSearch.js`) adalah **substring** pada
  nama dan varian, diurutkan menurut nama+varian, 30 baris/halaman. Ini bukan
  pengganti peringkat server: `/api/items/` tidak mengirim `item_alias`, dan
  alias itulah yang dipakai server untuk peringkat pencarian. Pencarian alias
  offline menuntut perubahan kontrak item — keputusan pemilik, bukan sesuatu
  yang dikarang di sisi kasir.
- Tiga keadaan yang dilihat kasir: `server` (tanpa pesan), `cache` ("Mode
  offline: daftar dari katalog tersimpan …" beserta umurnya), dan tanpa salinan
  sama sekali ("…katalog perangkat belum tersimpan").

Dua cacat yang ditemukan lewat verifikasi browser dan sudah diperbaiki:

1. `replaceCatalog()` memanggil `db.items.primaryKeys()`, padahal di Dexie 4
   metode itu milik **Collection**. Seluruh transaksi gagal, sehingga 2.917
   baris terunduh penuh lalu dibuang tanpa satu pun pesan di layar. Sekarang
   `toCollection().primaryKeys()`, dan bentuk pemanggilan itu dikunci oleh
   `tests/itemRepository.test.js` yang menirukan API Dexie apa adanya (tabel
   palsunya sengaja **tidak** punya `primaryKeys`). Tes ini gagal 4/4 kalau
   pemanggilan lamanya dikembalikan.
2. Urutan pesan: keadaan "tidak ada salinan" sekarang diperiksa **sebelum**
   "dari salinan". Sebelumnya, salinan yang lenyap saat aplikasi masih terbuka
   (penyimpanan dibersihkan) masih diumumkan sebagai "daftar dari katalog
   tersimpan", sehingga kasir mengira harganya masih bisa dipercaya.

Batas yang jujur dan belum diuji:

- Pengujian dilakukan dengan **menggagalkan permintaan API dari halaman**, bukan
  dengan mencabut LAN di tablet kasir. Keduanya berbeda pada waktu tunggu,
  pesan browser, dan perilaku jaringan nyata.
- Kuota penyimpanan tablet untuk ±2.917 baris dan perilaku eviksi browser belum
  diukur; belum ada pembatasan/penuaan salinan selain pesan umur.
- Halaman aplikasi belum dijamin terbuka tanpa server penyaji frontend (PWA keluar scope 2026-10-09; tablet saat ini memuat aplikasi dari Vite dev server).
- Baris yang sudah tampil dari jawaban server terakhir tetap di layar ketika
  server tak terjangkau dan salinan tidak ada; baris itu **tidak** disaring
  ulang oleh pencarian karena tidak ada salinan yang bisa disaring.
- Modal/margin dan alias tidak ikut tersimpan — sama seperti `GET /api/items/`.

## 4. Kontrak batch saat ini vs target

Endpoint: `POST /api/transactions/sync/`, header
`Authorization: Device <device_token>`, maksimal 200 transaksi.

### 4.1 Kontrak yang terpasang sekarang

```text
Request:
{ pin: <PIN operator batch>, transactions: [<payload transaction>, ...] }

Response HTTP 200:
{ worker_id, results: [{ sync_id, transaction_id, status, detail }, ...] }
```

- Payload setiap anggota adalah objek `transaction`, **bukan** envelope
  `{ pin, transaction }` milik endpoint online.
- Satu PIN diverifikasi untuk seluruh batch. Semua nota baru memakai worker
  tersebut. PIN salah menggagalkan envelope sebelum nota diproses (400);
  throttle dapat membalas 429 dengan `Retry-After`; token salah/tidak ada → 401.
- `worker_id` pada respons dibuat server, bukan dipilih atau dikirim klien.
- Setiap nota diproses atomik sendiri; hasil `new`, `idempotent`, `rejected`,
  atau `conflict`. Tidak seluruh batch dijamin atomik.
- Penolakan dicatat sebagai log terstruktur, tanpa tabel penolakan baru.
- Rujukan kode: [transaction_request.py](pos/transaction_request.py) dan
  [TransactionBatchSyncAPIView](pos/views.py).

### 4.2 Revisi PIN-per-nota (terpasang slice 2, 2026-10-09)

```text
Request (baru):
{ transactions: [{ ...<payload transaction>, pin: <PIN nota> }, ...] }

Response (baru):
{ results: [{ sync_id, transaction_id, status, detail }, ...] }
```

Tidak ada PIN top-level, tidak ada `worker_id` kiriman klien maupun pada
respons baru. Worker ditentukan server dari PIN masing-masing nota; PIN
salah hanya menggagalkan nota itu (`rejected` + alasan), nota lain tetap
diproses. PIN yang sama diulang dalam satu request diverifikasi sekali.
Throttle per-device tetap: terkunci di awal → seluruh request 429 +
`Retry-After`; terkunci di tengah batch → sisa nota yang belum diverifikasi
menjadi `rejected` + `retry_after_seconds`. Hitungan kegagalan dihapus hanya
bila seluruh verifikasi dalam request berhasil.

Selama transisi server menerima DUA envelope: lama (§4.1, respons tetap
memuat `worker_id`) dan baru. Request yang mencampur PIN top-level dan PIN
per-nota ditolak 400 tanpa nota diproses. Frontend baru hanya mengirim
bentuk baru (tombol "Kirim semua" + kirim per-nota tetap ada; lokasinya di
modal Riwayat Nota sejak 2026-10-09); tanggal
sunset envelope lama ditetapkan saat rilis. Rujukan kode: spec
[../BATCH_PIN_PER_NOTA_SPEC.md](../BATCH_PIN_PER_NOTA_SPEC.md).

## 5. Retry, penolakan, penomoran

- `new`/`idempotent`: tandai synced, lalu izinkan unggah bukti; efek stok terjadi
  satu kali. Identitas sama dengan fakta berubah tidak boleh dipakai sebagai
  jalur mengedit nota. Backend memeriksa fakta header, semua baris barang,
  dan worker hasil PIN sebelum sukses idempotent; perubahan → 409.
- Penolakan domain: pertahankan alasan. Pada target batch, nota ditandai
  rejected; UI riwayat server saat ini bukan riwayat lengkap antrean lokal.
- Konflik identitas: jangan retry otomatis atau membuat pengganti; eskalasi.
- Jaringan/5xx/timeout/JSON tidak lengkap: hasil ambigu (`unknown`). Penanda
  persisten ditulis sebelum HTTP. Pertahankan payload/ID/PIN/snapshot; jangan
  berasumsi server belum menyimpan. Respons 200/201 harus cocok ID dan status.
- `local_final` terpisah dari status kiriman. First 400 dijawab lengkap pada
  nota nonfinal dapat menjadi rejected pasti; penolakan setelah hasil ambigu
  atau final lokal → review, bukti/print job tetap, checkout baru diblokir.
  401/429 retry tidak menghapus ketidakpastian sebelumnya; countdown persisten.
- PIN asli sensitif di IndexedDB, tidak masuk print/history/log. PIN yang
  dipindah sebelum commit pertama belum terikat worker lama; server binding
  diperlukan bila kebijakan itu ingin dijamin. Retry terhadap nota tersimpan
  dengan worker berbeda → 409, tidak mengubah worker historis.
- Stok cache bukan otoritas. Penyegaran stok diambil dari API saat terhubung;
  rekonsiliasi Inventory lintas-modul belum ditentukan.

Nomor asli `Txx-NNNNNN` tetap 10 karakter dan tidak mengandung tanggal.
Rentang dialokasikan 1000 nomor; allocator menolak rentang yang akhir nomornya
melewati 999999. Daur ulang nomor tidak diizinkan.

**Kebijakan pensiun kode (keputusan pemilik 2026-10-09):** kode device yang
mencapai `Txx-999999` dipensiunkan — tidak dipakai lagi, tidak didaur ulang
(nomor lama sudah ada di server sehingga pengulangan akan bertabrakan
identitas). Perangkat mendaftar ulang dan memakai kode bebas berikutnya
(`T01`–`T99`, kode bebas pertama). Registrasi ulang tidak me-reset counter
kode yang habis; baris sequence kode pensiun tetap habis.

**Gap penomoran:** keputusan awal (2026-09-26) mengizinkan nomor sementara lokal
+ `transaction_id: null`; keputusan nota tangan (2026-09-29) menuntut nomor
asli dan cadangan yang ditambah sebelum habis. Perilaku saat cadangan benar-benar
habis belum diselaraskan. Kode frontend sekarang dapat menghasilkan null;
backend menyimpan null dan **tidak** mengalokasikan nomor final saat sync.
Tidak ada generator nomor sementara atau refill proaktif saat cadangan menipis.
Sejak 2026-10-09 (spec opsi A slice 1): jatah habis saat offline → penjualan
baru ditahan dengan pesan jelas, keranjang utuh, antrean aman.

Nota null tidak muncul dalam riwayat server per prefix. Pada unggah bukti,
pemeriksaan prefix juga dilewati bila nota null: kepemilikan perangkat tidak
bisa dibuktikan dari nomor. Ini keterbatasan keamanan nyata, bukan jaminan
isolasi antar-device. Desain device per transaksi masih memerlukan izin skema.

## 6. Direktori worker dan data sensitif

### 6b. Kontrak endpoint daftar worker offline

`GET /api/workers/offline-list/`, device token wajib.

```text
{ workers: [{ name, pin_stored, pin_format }], generated_at }
```

- `pin_stored` adalah nilai tersimpan `worker.pin` apa adanya, bukan PIN yang
  didekode server. Format: `bcrypt`, `plaintext`, atau `other`.
- Browser mendukung bcrypt/legacy plaintext; `other` (misalnya hash berformat
  Django) diabaikan lokal. Backend mendukung bcrypt, hash Django, dan teks lama.
- Tidak ada `worker_id` pada respons direktori. Jangan menyalin/log nilai
  direktori, hash, atau PIN ke dokumentasi, chat, maupun fixture nyata.
- Data operasional pernah dilaporkan memakai bcrypt, tetapi endpoint **tidak
  menjamin** semua baris hashed. Bila plaintext ada, nilainya terekspos langsung.
- Direktori hanya diterbitkan melalui endpoint ini, **tidak** disisipkan pada
  respons registrasi/renewal. Frontend mengambilnya secara terpisah.
- Daftar diganti utuh saat refresh. Pemicu klien: mount, transaksi sukses di
  server, dan event online. Fetch membutuhkan device token yang tersedia;
  keberhasilan tiap refresh tidak dijamin hanya karena event-nya terjadi.
- Semua worker saat ini diterbitkan: tidak ada role approver atau active flag
  untuk filter. Pengecualian approver adalah kebutuhan desain approval mendatang,
  bukan kontrol yang sudah berjalan.

Bcrypt memperlambat tebakan, tetapi PIN enam digit tetap hanya memiliki 10^6
kemungkinan; durasi cracking bergantung cost/hardware dan belum diukur.
Cooldown UI tidak menghentikan tebakan terhadap salinan IndexedDB atau klien
modifikasi. PIN ketikan yang direncanakan tersimpan per nota juga data sensitif;
hash direktori tidak melindungi nilai ketikan itu. Risiko ini tidak boleh
“diselesaikan” dengan klaim PIN tidak dapat dipulihkan.

Direktori basi dapat mengenali PIN lama atau tidak mengenali worker baru.
Timestamp refresh tersedia; peringatan umur daftar dan ambangnya belum lengkap.
Worker yang belum dikenal lokal tidak dapat memfinalisasi offline sesuai aturan;
online masih dapat mengirim PIN ke server, cetak menunggu penerimaan.

## 7. Keputusan dan kekurangan yang masih terbuka

| Rujukan keputusan lama | Keadaan sekarang |
| --- | --- |
| KEPUTUSAN-1 / -8: penomoran | Nomor asli nota tangan dikunci; fallback ketika cadangan habis belum diselaraskan (§5) |
| KEPUTUSAN-2 / -7: identitas offline | PIN-per-nota terpasang dua sisi (slice 2, 2026-10-09); envelope lama diterima sementara menunggu sunset (§4) |
| KEPUTUSAN-3: penolakan | Log server ada; riwayat penolakan queryable/audit store masih perlu desain |
| KEPUTUSAN-4: visibilitas | Riwayat per prefix terpasang; transaksi null tidak bisa di-scope secara memadai |
| KEPUTUSAN-5: harga | Floor–ceiling diterapkan; di bawah floor ditolak sampai approval dirancang, bahkan saat online |
| KEPUTUSAN-6: format nota | `Txx-NNNNNN`, tanggal terpisah; tidak berubah |
| KEPUTUSAN-9: direktori | Nilai tersimpan + format (koreksi 2026-09-30), bukan verifier PBKDF2 atau worker ID; tiga pemicu refresh |
| KEPUTUSAN-10: nama | Kolom ketikan dihapus; nama dari pencocokan PIN, identitas tetap server |
| KEPUTUSAN-11: gerbang segel (2026-10-08) | PIN tak dikenal + server tak terjangkau → tanpa nota, tanpa nomor, tanpa cetak (§3.1). Terpasang di frontend; notanya sudah diuji lewat tes script dan browser dev, belum di perangkat dengan LAN dicabut |
| KEPUTUSAN-12: cooldown PIN (2026-10-08) | 5 gagal → 30 detik, berlipat sampai 10 menit, persisten di perangkat (§3.2). Nilai ambang/waktu bisa diubah pemilik tanpa mengubah kontrak server |
| KEPUTUSAN-13: salinan katalog (2026-10-08) | Seluruh katalog disimpan di perangkat dan disegarkan berkala; pencarian lokal hanya nama+varian (tanpa alias); basi diberi keterangan setelah 7 hari (§3.3). Terpasang di frontend, belum diuji dengan LAN dicabut |
| KEPUTUSAN-14: bukti hidup hanya 2xx (2026-10-09) | Probe dan jalur submit hanya mencatat respons 2xx sebagai server hidup; 4xx/5xx (termasuk 502 proxy saat backend mati) dicatat sunyi sehingga gerbang PIN tak dikenal menutup. Mengubah perilaku lama yang menghitung respons HTTP apa pun sebagai jawaban (§2.2, §3) |

Yang belum selesai: batch revisi kedua sisi (PIN per nota), pemulihan review yang berotorisasi,
ikatan worker sebelum commit, penomoran cadangan, serta pengujian perangkat
kasir. Gerbang segel, cooldown PIN lokal, dan salinan katalog sudah terpasang di
sisi perangkat (§3.1-3.3) dengan bukti di atas; itu belum menjadikan alur
offline end-to-end lulus. Pembaruan ini
tidak memberi izin baru untuk kode/skema atau memutuskan solusi keamanan.

## 8. Kriteria penerimaan untuk menutup fitur offline

Ini kriteria target, **belum dinyatakan lulus** pada tinjauan dokumentasi ini:

1. Putus koneksi server → jual 5 nota → sambung kembali → kelima nota masuk
   tanpa duplikasi transaksi atau pemotongan stok.
2. Kirim ulang batch sama → efek transaksi/stok tepat sekali; konflik ID
   ditandai dan tidak di-retry otomatis.
3. PIN tidak ada/tidak cocok lokal → tidak ada nota offline final atau cetak;
   percobaan gagal dibatasi cooldown persisten. *Sebagian terpasang
   2026-10-08*: penolakan sebelum segel dan cooldown perangkat ada (§3.1-3.2),
   tetapi lulus penuh tetap menuntut pengujian di perangkat dengan LAN dicabut.
4. Setiap nota diatribusikan dari PIN-nya sendiri oleh server, tanpa worker ID
   klien atau PIN operator batch. Sync tidak mengunci counter PIN online.
5. Stok cache 5, jual 8 → diizinkan; server tetap berhak menolak bila stoknya
   sudah <= 0. Penolakan tetap terlihat dan beralasan. *Sebagian terpasang
   2026-10-08*: salinan katalog yang menyediakan stok snapshotted sudah ada
   (§3.3); lulus penuh tetap menuntut uji di perangkat kasir.
6. Printer mati → nota final tetap ada; retry LAN bekerja tanpa internet.
   Lihat batas deduplikasi dan hasil socket di [jalur cetak](PRINT_PATH_DESIGN.md).
7. Reload/perubahan jaringan tidak kehilangan pending, bukti, atau jejak nota
   final; payload ambigu tidak dibuang atau diganti identitas.
8. Cadangan nomor habis mengikuti keputusan pemilik yang sudah diselaraskan;
   tidak mengarang nomor asli atau mengklaim null otomatis diberi nomor server.
9. Refresh direktori, PIN basi/berubah, worker baru, dan filter approver (setelah
   role ada) mempunyai jalur uji yang jelas.

Uji database harus mengikuti [verifikasi aman](../../README.md#verifikasi-aman),
bukan memakai database bersama/produksi. Kode, dokumen, dan bukti uji harus
menunjukkan status yang sama sebelum fitur disebut selesai.
