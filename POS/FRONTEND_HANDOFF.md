# Handoff Backend ↔ Frontend POS

**Ditinjau dari kode: 2026-10-01.** Dokumen ini menghubungkan kontrak server dan
pekerjaan UI; bukan izin mengubah kode/skema. Status “ada” berarti implementasi
terlihat di sumber, bukan jaminan sudah diuji pada tablet/printer toko.

Rujukan: [arsitektur](../ARCHITECTURE.md),
[aturan frontend](pos-frontend/AGENTS.md),
[aturan backend](pos-backend/AGENTS.md),
[offline/PIN](pos-backend/OFFLINE_SYNC_DESIGN.md),
[jalur cetak](pos-backend/PRINT_PATH_DESIGN.md), dan
[bukti transfer](pos-backend/PAYMENT_PROOF_DESIGN.md).

## A. Kontrak dan fitur yang sudah terpasang

1. **Checkout online:** `POST /api/transactions/`,
   `Authorization: Device <token>`, body `{ pin, transaction }`.
   `transaction` memuat `sync_id`, nullable `transaction_id`, waktu, jumlah
   uang, metode bayar, dan `items`. Nama kasir/fulfillment hanya snapshot lokal,
   bukan field persisted server; frontend tidak mengirim `worker_id`.
   - 201 `new`, 200 `idempotent`: nota server tersimpan.
   - 400 pada percobaan pertama yang dijawab lengkap dan belum final lokal:
     rejected pasti, simpan alasan/bukti. Jika sudah final lokal atau ada hasil
     sebelumnya ambigu: tahan review, blokir checkout baru; jangan ganti PIN.
   - 401: kredensial device ditolak; pertahankan payload/ID untuk penyelesaian.
   - 409: konflik identitas/isi/kasir, tindak lanjut manusia, tidak retry otomatis.
   - Idempotent memeriksa fakta header/barang dan worker hasil PIN terhadap
     nota tersimpan; perubahan fakta/kasir tidak diterima sebagai sukses.
   - 429: baca `Retry-After`, tampilkan countdown.
   - Jaringan/5xx: hasil ambigu; server mungkin sudah menyimpan. Jangan
     mengganti ID/payload atau menganggap nota pasti belum diproses.
2. **Harga dan pembayaran:** `floor_price <= unit_price <= price` untuk batas
   yang tersedia. Frontend mengecek sebelum menulis print job; server tetap
   otoritatif. Harga di bawah floor **ditolak juga online** sampai approval
   dirancang. Pembayaran hanya `cash`, `edc`, `bank_transfer`; QRIS/kartu
   bagian EDC, kembalian hanya cash.
3. **Device:** registrasi otomatis single-flight, secret header disisipkan
   Vite/proxy, device disimpan Dexie. TTL normal 30 hari. Backend mendukung
   renewal sampai umur token 60 hari; frontend mengirim body renewal dulu dan
   hanya jatuh ke registrasi baru bila renewal ditolak 401; lihat
   [handoff device](DEVICE_AUTO_REGISTRATION_HANDOFF.md).
4. **Direktori worker:** `GET /api/workers/offline-list/` dengan device token,
   respons `{ workers: [{ name, pin_stored, pin_format }], generated_at }`.
   Browser menyimpan daftar di Dexie dan mencocokkan bcrypt/legacy plaintext;
   format `other` tidak didukung lokal. Nama nota bukan kolom ketikan lagi;
   atribusi worker resmi tetap dari PIN server. Daftar adalah data sensitif,
   bukan jaminan seluruh nilai hashed atau PIN tidak bisa dipulihkan.
5. **Cetak:** PIN cocok lokal → antre/coba cetak segera; tidak dikenal lokal →
   tunggu HTTP 200/201. Print job menyimpan snapshot/byte; printer gagal tidak
   membatalkan nota final. DIANTAR menambah surat jalan tanpa harga; nama toko
   lewat `VITE_STORE_*`. Printer-status panel dan retry queue LAN sudah ada.
6. **Riwayat:** list/detail server per prefix device, 20 nota/halaman,
   `?date=YYYY-MM-DD` menurut zona waktu backend. UI default hari ini, kalender,
   detail barang dan cetak ulang. Nota device lain → 404. Nullable nota belum
   muncul; daftar ini bukan riwayat lengkap semua nota offline/rejected lokal.
7. **Bukti transfer:** capture/kompres bila perlu → blob Dexie → kaitkan ke
   `sync_id` → unggah multipart setelah nota synced. Upload idempotent per
   `proof_id`; badge/retry bukti tertunda ada di history. Tidak ada endpoint
   membaca gambar server; kuota/retensi dan uji HEIC masih terbuka.
8. **Pencarian:** API nama/varian dengan debounce 300 ms. Sejak 2026-10-08
   salinan **seluruh** katalog ada di Dexie `items` dengan cap waktu
   `catalog_meta` (Dexie v6, disegarkan saat dibuka + tiap 15 menit), dan saat
   server tidak menjawab daftar dilayani salinan itu: pencarian lokal
   substring nama/varian, 30 baris/halaman, dengan pesan umur salinan di bawah
   jumlah varian (OFFLINE_SYNC_DESIGN.md §3.3). Yang **belum**: alias tidak
   ikut tersimpan (server memakai `item_alias` untuk peringkat, `/api/items/`
   tidak mengirimnya, jadi pencarian alias offline menuntut keputusan kontrak
   item), kuota/eviksi penyimpanan tablet belum diukur, dan uji dengan LAN
   benar-benar dicabut belum dilakukan. Waktu target 5 detik tetap belum
   terukur.

## B. Gap implementasi offline yang disetujui

- Revisi keputusan 2026-09-29 mewajibkan PIN cocok lokal sebelum nota offline
  final dan PIN per nota pada batch. **Slice 2 terpasang (2026-10-09)**:
  backend dual-envelope transisi — lama `{ pin, transactions }` dan baru
  `{ transactions: [{ ...transaction, pin }, ...] }` → `{ results: [...] }`
  tanpa `worker_id`; PIN salah hanya menggagalkan nota itu. Maksimum 200,
  throttle PIN per-device tetap. Campuran PIN top-level + per-nota → 400.
  Sunset envelope lama ditetapkan saat rilis.
- Frontend memanggil batch sync lewat tombol "Kirim semua" di notifikasi
  antrean (`syncTransactionBatch` + `resendQueuedBatch` di `App.vue`); kirim
  per-nota tetap ada. Sejak 2026-10-09 tempat keduanya adalah modal Riwayat Nota
  (notifikasi antrean + tombol `KIRIM ULANG NOTA INI`), dengan lencana jumlah di
  tombol "Riwayat Nota" — baner di kolom keranjang dihapus atas permintaan
  pemilik karena memakan tinggi layar kasir. Sejak 2026-10-10 notifikasi itu
  berbentuk **chip ringkas di baris judul modal**, bukan band di bawah judul,
  supaya daftar nota tidak kehilangan ruang dan pesannya tidak panjang. Sejak
  2026-10-11 peringatan + tombol solusinya memakai bentuk dari gambar acuan
  pemilik: kotak bergaris 2px dengan gradien warna nadanya, pesan 12px/700,
  lencana tutup bulat di sudut kiri atas (daerah kliknya tetap 44px), dan aksi
  solusinya berupa **wilayah** di ujung kanan panel yang dipisahkan garis 2px
  sewarna nadanya — bukan kotak pil di dalam kotak (revisi 2026-10-11 yang
  sama). Labelnya "Kirim semua" tanpa angka, dan notifikasi "Perlu diperiksa"
  untuk hasil batch campuran dihapus; pemeriksaannya tetap muncul sebagai
  pesan kritis di kolom keranjang saat halaman dimuat ulang. Revisi berikutnya
  di hari yang sama mengecilkan panelnya bertahap sampai **36px** (60px → 52px
  → 48px → 36px; pesan tetap 12px/700 dengan tinggi baris 16px, lencana tutup
  20px, chip di baris yang sama ikut 36px) dan mengubah barisnya dari satu baris
  geser menjadi **baris yang membungkus** (jarak antar elemen 16px, jarak antar
  baris 14px): dua peringatan di layar tablet tidak lagi tersembunyi di balik
  geseran kiri-kanan, dan barisnya **tetap di sebelah judul** — yang menyusut
  judulnya (`flex-shrink: 1`), bukan panelnya yang turun ke bawah (pemilik
  melaporkan versi "baris penuh di bawah 1120px" sebagai "malah pindah ke
  bawah"; ambang baris-penuh kembali 900px). Kotaknya mengecil **tanpa**
  memboroskan sasaran jari: wilayah aksi, chip, dan lencana tutup melebarkan
  daerah kliknya sendiri sampai 44px, dan tidak ada dua daerah klik yang
  bertumpuk. Lencana tutupnya duduk **tepat di titik sudut kiri atas** panel
  (pusatnya 2,93px dari tepi luar, yaitu puncak lengkung sudut 10px) dan hanya
  melebar ke kanan-bawah, supaya tidak berebut sentuhan dengan tombol aksi
  panel tetangga. Tinggi header dengan beban terberat (2 peringatan + 2 chip):
  143px di 1007x601, 123px di 1024x768 dan desktop, 190px di tablet tegak,
  341px di 360x800. Angka per resolusi ada di `pos-frontend/AGENTS.md`.
  Revisi terakhir hari itu menyentuh warnanya, bukan bentuknya: keluarga amber
  **mode terang** memakai pasangan amber palet pemilik (`#FDEBC8`/`#8A4B08`,
  sama dengan keluarga "stok tipis") dengan garis = tinta seperti panel bahaya,
  menggantikan krem `#FDF3E0` + mustard `#D9A441` yang tidak ada di palet, dan
  hentian gradien wilayah aksinya pindah ke token `--pos-warning-edge` supaya
  tidak ikut menjadi coklat gelap; mode gelap tidak berubah satu piksel pun.
  Dua panel yang duduk sebaris kini berjarak 24px lewat `column-gap` barisnya di
  layar >900px (di bawah itu tetap 16px, dan di 901-1006px panel yang membungkus
  tidak menjorok). Nadanya tetap keluarga token POS: amber = kertas belum keluar,
  merah = perlu tindakan. Ukuran, warna, dan batasnya tercatat di
  `pos-frontend/AGENTS.md`. Peringatan bukti transfer di kolom kiri juga
  **hilang seketika** saat "Kirim semua" ditekan — baris bukti yang notanya
  sedang dikirim tidak digantung di layar — dan kembali sendiri dengan
  statusnya kalau pengirimannya gagal; daftarnya dibaca ulang begitu batch
  selesai, bukan menunggu modal dibuka lagi.
  Tombol
  "Riwayat Nota" membawa dua lencana angka: nota belum tersinkron ke server
  dan nota yang transaksinya terjadi hari ini tetapi belum tercetak. Nota nota
  dan surat jalan disatukan per `sync_id`; job kemarin tidak menambah angka
  hari ini, tetapi masih bisa dicoba ulang. Penutupan panel cetak bertahan
  setelah refresh (kunci = himpunan seluruh `job_id` belum tercetak), dan X
  menyembunyikan tombol ulang bersama warning tanpa menghapus job; sejak
  2026-10-11 panel "N belum tersinkron" memakai X yang sama (kunci = himpunan
  `sync_id` yang masih antre), jadi tidak ada lagi peringatan yang tidak bisa
  ditutup. Chip hasil kirim batch yang berhasil berbunyi **"Nota berhasil
  dikirim"** dengan kotak hijau yang sama seperti chip "Tersimpan" saat nota
  masuk online (permintaan pemilik, 2026-10-11); label lain tetap netral.
  Sejak hari yang sama daftar nota **menyegarkan diri sendiri** begitu ada nota
  selesai dikirim: pemicunya isi daftar antrean perangkat (nota yang keluar dari
  antrean berarti barisnya sudah ada di server), bukan lagi harus membuka ulang
  modal atau memuat ulang halaman — sebelumnya baris nota yang baru tersinkron
  tetap terbaca "Hasil belum diketahui" sampai di-refresh. Pemuatan daftar juga
  dijaga dari jawaban basi (`loadSequence`) supaya jawaban lama tidak menaruh
  kembali status antrean.
  Penutupan itu **tampilan** saja: nota tetap menunggu di antrean,
  lencana angka di tombol "Riwayat Nota" tetap, dan mengirim ulang tetap
  memakai PIN asli nota. Tombol `KIRIM ULANG NOTA INI` per nota juga pindah ke
  kanan atas kartu nota (sebaris dengan nomor + waktu + kasirnya), dengan pil
  bergaris dari keluarga bahaya supaya lebih mencolok. Baris filter "Cakupan
  nota" + "Status aksi" kini membungkus hanya kalau barisnya sendiri tidak
  cukup — ambang lebar jendela `max-width: 1365px` dihapus karena itu yang
  membuatnya "kadang turun" di layar desktop. Ukuran, kontras, dan batasnya
  tercatat di `pos-frontend/AGENTS.md`. Checkout
  individual menyimpan PIN asli per nota di Dexie
  (sensitif), retry/batch tanpa PIN pengganti; hapus PIN setelah synced atau
  ditolak pasti nonfinal.
- Local matching mengizinkan **cetak segera**. Sejak 2026-10-08 pembuatan baris
  untuk PIN tak dikenal dijaga gerbang: PIN tak dikenal + server tak terjangkau
  ditolak sebelum nomor dipakai (`src/services/api/serverReachability.js`), dan
  percobaan gagal dibatasi cooldown lokal 5×30 detik berlipat (Dexie v5
  `pin_attempts`). Yang **belum**: peringatan umur daftar kasir basi, dan
  pengujian di tablet dengan LAN dicabut. Detail: OFFLINE_SYNC_DESIGN.md §3.1-3.2.
- Pending/ambigu/final terkunci dan tidak dapat dibuang/diganti. `unknown`
  persisten ditulis sebelum HTTP, tidak diturunkan oleh penolakan retry.
  `local_final` terpisah; `review`/`conflict` memblokir penjualan baru dan
  mempertahankan bukti/print job. Nota lama tanpa PIN/snapshot memerlukan review.
- PWA/service worker, refill rentang sebelum menipis, dan nomor final untuk
  nota null belum terpasang. Katalog offline sudah terpasang di perangkat
  (§3.3), tetapi halaman aplikasi belum dijamin bisa dibuka ulang tanpa server
  dan tablet masih memuat aplikasi dari Vite dev server.
- Penolakan lokal disimpan, tetapi UI history server tidak menampilkan semua
  nota rejected/pending yang belum diterima server.

Detail dan kriteria penerimaan ada di
[OFFLINE_SYNC_DESIGN.md](pos-backend/OFFLINE_SYNC_DESIGN.md); dokumen ini tidak
menduplikasi seluruh aturan atau mengeksekusi perbaikannya.

### Pengamanan checkout individual (2026-10-01)

- Nota, PIN asli, snapshot cetak, dan pengaitan proof disimpan atomik sebelum
  submit/cetak. Facts tidak ditimpa melalui repository status; draft saja yang
  boleh dihapus. Tidak ada PIN/worker ID dalam snapshot cetak.
- UI terkunci selama restore/submit/retry/review. Retry langsung mengirim nota
  yang sama; timeout 30 detik atau respons sukses tidak cocok → unknown.
- Penanda asal cart mencegah cart yang sudah synced pulih sebagai draft baru
  jika aplikasi mati sebelum cart dibersihkan. Storage gagal → fail closed.
- Web Locks digunakan jika tersedia; transaksi IndexedDB menolak nota kedua
  dan submit paralel. HTTP LAN tidak selalu menyediakan Web Locks; ini bukan
  jaminan single-tab lintas semua browser.
- PIN sama belum mengikat worker jika PIN dipindah sebelum commit pertama.
  Riwayat null belum dapat direkonsiliasi aman; tidak ditambah endpoint lookup.
- [Uji frontend](pos-frontend/tests/checkout.test.js) menggunakan Node built-in
  dan VM dengan storage/network doubles, bukan uji browser/IndexedDB asli.

## C. Menunggu keputusan bisnis, kontrak, atau izin skema

- Menyelaraskan fallback saat nomor asli habis dengan aturan nota tangan.
- Kolom device per transaksi untuk visibilitas nota null dan ownership bukti;
  upload sekarang melewati check prefix bila nomor null.
- Fulfillment persisted: UI pickup/delivery tidak menyimpan status/alamat/zona.
  History reprint menebak delivery dari `shipping_amount > 0`; delivery dengan
  ongkir nol tidak bisa dikenali dengan cara itu. `DIKIRIM` belum ada.
- Approval nego di bawah floor, tarif/approval ongkir, role/approver dan
  pemisahan worker offline; jangan menambah login worker dari kutipan lama.
- Pembulatan nota 500/1000, custom amount, shift/kas non-penjualan, void/return/
  refund, tempo/invoice dan integrasi Finance/Inventory/Master.
- Deployment produksi (proxy, bridge auto-start/update), retention job/bukti,
  endpoint baca proof dan hak aksesnya. Setup dev bukan deployment produksi.

## D. Verifikasi sebelum menyebut selesai

- Uji 5 nota offline → tersinkron tanpa duplikasi; retry ambigu tetap memakai
  identitas sama; PIN salah tidak menghasilkan nota offline final/cetak.
- Uji stok 5 jual 8 diperbolehkan; stok <= 0 ditolak; efek stok tidak berulang.
- Printer mati/pulih dan bridge restart: bedakan byte terkirim dari kertas
  tercetak; deduplikasi bridge hanya memori, bukan exactly-once fisik.
- Gunakan [verifikasi aman](../README.md#verifikasi-aman), tanpa database
  bersama/produksi. Perilaku browser, tablet, dan printer tetap perlu dibuktikan
  terpisah dari pemeriksaan kode dan XML/PDF yang valid.
