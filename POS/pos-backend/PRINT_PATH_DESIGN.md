# Rancangan Jalur Cetak POS (Epson TM-U220B)

**Status: dua hal sudah KEPUTUSAN TERKONFIRMASI 2026-09-29 — (1) nota dicetak
2 rangkap, (2) POS mencetak lewat print bridge di LAN — dan keduanya sudah
DIIMPLEMENTASIKAN 2026-09-29: sisi POS di
`pos-frontend/src/services/printer/` (encoder ESC/POS, tata letak 40 kolom,
klien bridge, antrean cetak `print_jobs`), bridge referensi di
`POS/print-bridge/`. Yang belum dikunci adalah sisi deployment: siapa yang
menjalankan bridge dan bagaimana POS menjangkaunya di produksi (§7), plus daftar
pengukuran di perangkat asli (§6).**

**Tinjauan sumber 2026-10-01:** history reprint dan surat jalan DIANTAR sudah
ada sejak 2026-09-30. Keberhasilan printer fisik, output satu lembar surat
jalan, dan exactly-once printing belum terbukti oleh keberadaan kode.

[Aturan repo](../../AGENTS.md) dan [aturan POS](../AGENTS.md) mengatur cara
kerja; [ARCHITECTURE.md](../../ARCHITECTURE.md) memiliki keputusan bisnis.
Dokumen ini memiliki detail jalur cetak, bukan keputusan baru. Kontrak
referensi terpasang dijelaskan di [README bridge](../print-bridge/README.md);
persetujuan deployment adalah persoalan terpisah. Laporkan konflik/gap, jangan
mengubah skema, API, atau keputusan pemilik secara diam-diam.

Desain tampilan nota ada di `POS/docs/desain_nota_75x65.pdf` (artefak beku;
skrip penghasilnya dihapus saat bersih-bersih repo). Dokumen ini menjawab pertanyaan yang
berbeda: **lewat jalur apa nota sampai ke printer**, dan **berapa lembar yang
keluar**.

---

## 1. Fakta perangkat yang mengikat desain

| Fakta | Nilai | Sumber |
|---|---|---|
| Lebar kertas | 76 mm nominal (desain nota memakai 75 mm) | spesifikasi TM-U220 |
| Lebar cetak | 63,5 mm | spesifikasi TM-U220 |
| Font B | 40 kolom per baris (batas keras) | spesifikasi TM-U220 |
| Kertas karbon | **satu salinan saja** (1 asli + 1 karbon) | Service Manual/Technical Reference TM-U220: "We provide roll paper and carbon roll paper with 1 copy for this printer… **TM-U220 can't print on carbon roll paper with 2 copies.**" Datasheet: "Carbon Paper Copy Capacity: One (1) original and one (1) copy" |
| Autocutter | **ada di Type B**; yang tidak punya adalah Type D | Technical Reference: "TM-U220 Type D does not have an autocutter unit… Use the manual cutter." |
| Antarmuka jaringan | ESC/POS mentah ke TCP port 9100 | interface Ethernet Epson |
| Pita | dua warna (hitam/merah) tersedia | spesifikasi TM-U220 |

Yang harus dipastikan di unit toko, bukan diasumsikan: tipe yang tertulis di
bodi printer (`M188B` = Type B) dan apakah autocutter-nya benar-benar bekerja.

Dampak autocutter ke desain: printer diberi umpan beberapa baris kosong di akhir
nota lalu perintah potong (ESC/POS `GS V`), supaya teks terakhir tidak ikut
terpotong. Kalau unitnya ternyata Type D, jalurnya sama, hanya berakhir dengan
sobek manual — jadi desain tetap menyediakan umpan akhir dan tidak bergantung
pada cutter.

## 2. [KEPUTUSAN 2026-09-29] Nota 2 rangkap, bukan 3

Yang memaksa: **satu kepala cetak, satu kali jalan.** Sekali cetak menghasilkan
dua lembar dengan teks **identik**; tidak ada cara mencetak "SALINAN TOKO" hanya
di lembar kedua. Batasnya bukan preferensi kita — dua salinan karbon ditolak
oleh printer ini (kutipan di §1).

Pemetaan salinan yang dikunci:

| Lembar | Isi | Penerima |
|---|---|---|
| Asli (putih) | asli | pelanggan |
| Karbon (kuning) | salinan | toko — arsip fisik |

- Salinan gudang **tidak** dicetak. Arsipnya digital per **nomor nota asli**
  (KEPUTUSAN-8), sama seperti riwayat nota yang sudah ada di backend.
- Surat jalan terpisah untuk `DIANTAR` sudah diminta dan diimplementasikan
  pada 2026-09-30 (§5a). Dokumen `DIKIRIM` masih terbuka; surat jalan bukan
  nota ketiga atau salinan nota berharga.
- Konsekuensi yang diterima: roll 2 ply lebih pendek daripada roll bond (90 ft
  vs 165 ft) sehingga lebih sering ganti kertas, dan lembar karbon lebih pudar
  daripada lembar asli. Keterbacaan arsip toko setelah disimpan lama **belum
  diukur** (§6).
- Menggandakan nota berharga untuk memaksa tiga rangkap bukan jalur yang
  disetujui. Surat jalan adalah dokumen berbeda; jika nota memakai roll karbon
  dan surat jalan harus satu lembar, pilihan kertas/prosedur operator tetap
  perlu diuji. Encoder tidak dapat menghilangkan salinan karbon secara software.

Aturan lama yang berbunyi "cetak nota 3 rangkap" (customer/shop/warehouse)
digantikan oleh keputusan ini, dan teks di `POS/pos-frontend/AGENTS.md` sudah
diperbarui mengikuti keputusan ini (2026-09-29).

## 3. Kenapa browser tidak bisa langsung ke printer

Moka POS bisa "connect printer dari aplikasi" karena ia **aplikasi native
Android**: permintaan cetak tidak pernah keluar dari browser, tetapi masuk ke
lapisan native yang boleh membuka Bluetooth SPP, USB host, atau print
service/SDK ESC-POS, lalu mengirim byte mentah. Yang membuka koneksi adalah
aplikasinya, bukan halaman web.

Aplikasi web kita tidak punya jalur native itu (service worker/PWA juga
belum diimplementasikan). Empat sebabnya, semuanya teknis dan tidak bisa
dinegosiasi:

1. Browser tidak bisa membuka TCP mentah. Port 9100 bukan HTTP, jadi
   `fetch('http://<ip-printer>:9100')` tidak punya arti.
2. Kalau halaman POS disajikan lewat HTTPS, panggilan ke `http://` di LAN
   diblokir sebagai mixed content.
3. WebUSB hanya untuk printer USB fisik dan butuh gesture + izin; Web Bluetooth
   di Android hanya BLE, sedangkan TM-U220B bukan perangkat BLE.
4. Jalur cetak harus tetap hidup saat **internet** mati (router dan printer di
   LAN masih hidup), jadi apa pun yang melewati internet sejak awal salah.

## 4. [KEPUTUSAN 2026-09-29] Jalur cetak: print bridge di LAN

POS tidak menyentuh printer secara langsung. Perantaraannya satu layanan kecil
di dalam LAN:

```text
Tablet (POS)                Mesin LAN                     Printer
fetch POST /print   ->   print bridge (HTTP)   ->   10.0.0.9:9100
(body byte ESC/POS)      relay TCP mentah            (ESC/POS)
```

Sifat bridge yang dikunci: **pipa bodoh**. Ia menerima byte ESC/POS dan
meneruskannya apa adanya; ia tidak tahu apa itu nota, item, atau harga. Dengan
begitu tata letak tetap punya satu tempat (frontend sebagai pembuat byte, dengan
`POS/docs/desain_nota_75x65.pdf` sebagai acuan desain), dan format nota bisa
berubah tanpa menyentuh bridge.

Kontrak **referensi yang terpasang** —
[bridge.py](../print-bridge/bridge.py) menyediakan `--selftest`; kepemilikan
operasional/deployment masih terbuka. Implementasi pengganti harus menjaga
kompatibilitas klien yang ada:

- `POST /print` dengan body **biner ESC/POS** (tidak mendekode base64), plus header
  `X-Print-Job` untuk mengurangi duplikasi retry selama ID masih ada di memori
  bridge; ini bukan jaminan exactly-once fisik.
- Balasan `200` = byte sudah terkirim ke socket; `503` = printer tidak
  terjangkau. Bridge tidak pernah menunggu operator dan tidak pernah menyimpan
  antrean panjang: antrean ada di sisi POS (§5).
- `GET /health` (juga `/print/health`, karena POS memanggil bridge lewat satu
  alamat same-origin berprefix `/print`) melaporkan dua hal terpisah: `status` =
  bridge hidup, dan `printer_status` = `ok`/`unreachable` hasil sentuhan singkat
  ke port printer (tanpa mengirim byte apa pun, dibatasi `PRINT_HEALTH_TIMEOUT`).
  Pemisahan itu disengaja: printer mati tidak boleh membuat bridge dianggap
  rusak, dan ``/health`` yang hanya membuktikan bridge hidup tidak cukup untuk
  memberi tahu kasir apakah koneksi printer tersedia; probe tidak menjamin
  kertas keluar. Badan yang sama juga membawa
  `printer` (alamat tujuan), `printer_name` (label `PRINTER_NAME` di bridge,
  bukan hasil deteksi), `jobs_seen`, dan `version`, supaya panel status POS bisa
  menyebut perangkat yang sedang dibicarakan.
- Bridge hanya mendengarkan di LAN, tidak pernah diekspos ke internet, dan
  printer sebaiknya hanya menerima koneksi dari bridge.
- Klien POS tidak boleh menyertakan modal/margin/harga beli dalam byte cetak.
  Bridge tidak memvalidasi field bisnis: ia menerima byte mentah, jadi
  allowlist/larangan field dijaga pembuat nota, bukan relai.

**Status printer di layar kasir (2026-09-30).** Header POS menampilkan
`Printer tersambung` / `Printer tidak tersambung` tepat di sebelah toggle mode
gelap, hasil `GET /health` setiap 30 detik, setelah setiap percobaan cetak, dan
saat tombolnya diketuk. Tombolnya membuka panel ke bawah: nama printer dan
"printer online", alamat printer, waktu pemeriksaan terakhir, penjelasan jalur
cetak, dan langkah perbaikan yang cocok dengan keadaan saat itu — jadi kasir
tidak perlu menebak, dan admin toko bisa membaca perangkat mana yang
bermasalah. Panel itu memakai bahasa orang toko (permintaan pemilik,
2026-09-30): kata "bridge", versi, dan jumlah job yang diingat bridge tidak
ditampilkan; program penghubungnya disebut **"penghubung cetak"**. Ketiga field
teknisnya tetap ada di badan `/health` untuk keperluan diagnosa. Tiga keadaan
dibedakan karena perbaikannya berbeda:
printer menjawab, bridge hidup tetapi printer mati, dan bridge tidak terjangkau
(judul tombol menyebut alamat serta pukul berapa terakhir diperiksa). Status ini
**tidak pernah** diambil dari `navigator.onLine` — bridge dan printer ada di LAN,
jadi internet mati bukan bukti apa pun. Yang bisa dipastikan hanya "printer
menjawab koneksi": kertas habis, pita kering, atau macet tetap baru ketahuan
saat mencetak (§6).

Prasyarat jaringan:

- Printer diberi IP tetap (DHCP reservation); port 9100 hanya terbuka untuk
  bridge.
- Bridge jalan di mesin LAN (mini PC/Raspberry Pi, atau mesin yang juga
  menyajikan POS), **bukan di tablet**: Android membatasi service latar, jadi
  bridge di tablet rapuh.
- Proxy `/print` pada origin POS menjaga same-origin dan menghindari mixed
  content. HTTP LAN atau sertifikat lokal saja tidak mengatasi CORS pada origin
  berbeda: bridge referensi tidak menyediakan OPTIONS/header CORS. Jalur
  lintas-origin perlu proxy atau perubahan yang ditinjau.
- Cara menyalakan bridge saat boot dan siapa yang memperbaruinya masih
  keputusan deployment yang belum ada, jadi jangan menganggap bridge sudah jalan
  di toko. Implementasi referensinya ada di `POS/print-bridge/bridge.py`;
  POS sisi klien sudah siap memakainya (`/print` same-origin secara default).

## 5. Urutan finalisasi, cetak, dan cetak ulang

Urutan yang mengikat (lihat [arsitektur](../../ARCHITECTURE.md) dan
[desain offline](OFFLINE_SYNC_DESIGN.md)):
**PIN terverifikasi → finalisasi/persist nota → antre cetak → tampilkan/cetak.**
Cetak adalah upaya terbaik dan tidak pernah membatalkan nota yang sudah sah.

- **Kapan print job ditulis (permintaan pemilik, 2026-09-30).** Bedakan record
transaksi pending dari record `print_jobs`: pending dapat tersimpan sebelum PIN
terverifikasi, tetapi print job baru dibuat melalui jalur berikut:
  - PIN **cocok** di direktori kasir perangkat → final di tempat, jadi baris
    antrean ditulis sebelum kiriman ke server dan kertasnya langsung dicoba
    keluar (inilah yang membuat nota offline tetap tercetak);
  - PIN **tidak dikenal** perangkat → final hanya setelah server menerima
    (HTTP 201/200). Sebelum itu tidak ada **print job** baru; record transaksi
    pending masih dapat ada. PIN salah/tidak dikenal lokal tidak langsung
    mencetak kertas sebelum verifikasi server.
  `ensureReceiptPrint` memeriksa job yang masih tersimpan per `sync_id` untuk
  menghindari enqueue kedua pada jalur normal; pemeriksaan ini bukan unique
  constraint atomik atau jaminan exactly-once setelah retensi/restart.
  Local PIN cocok juga tidak menjamin server menerima stok/harga/identitas;
  penolakan domain dapat datang setelah kertas keluar. Nota final lokal yang
  ditolak kini ditahan review bersama bukti/print job, tidak dibuang (§5 desain offline).

- Cetak gagal (printer mati, kabel lepas, bridge mati) → nota tetap sah, kasir
  menulis nota tangan memakai **nomor nota asli** yang tampil di layar.
- Baris cetak ulang **sudah** bertahan sampai printer hidup lagi: store Dexie
  `print_jobs` menyimpan byte dan `job_id`, mencoba ulang dengan backoff, dan
  tidak membuang job hanya karena usianya. Checkout tidak lagi menghapus job
  karena discard/penolakan nota final atau ambigu; job yang sudah terkirim disimpan
  14 hari (`PRINT_JOB_RETENTION_DAYS`) — angka itu default yang dipilih, bukan
  keputusan pemilik. Retry dijadwalkan dengan backoff (10 detik lalu bertambah,
  maksimal 5 menit), tetapi flush saat ini berbasis event/mount/checkout/aksi
  manual; belum ada timer yang otomatis menjalankan flush saat jadwal tiba.
- Cetak ulang dari riwayat nota **sudah terpasang** (2026-09-30): tombol di panel
  detail riwayat memakai `printQueue.reprintReceiptFromHistory`, yang merakit
  ulang nota dari jawaban server dan mengirimnya dengan `job_id` baru — cetak
  ulang memang berarti kertas harus keluar lagi. Lihat §5a.
- Karena bridge bisa menerima kiriman ulang, `X-Print-Job` wajib sama untuk
  percobaan ulang job yang sama, dan berbeda untuk nota baru — sudah dipenuhi
  (`escpos.buildPrintJob`, `printJobRepository`).
- Baris antrean yang baru ditulis **langsung dicoba cetak**, bukan menunggu
  kiriman berikutnya berhasil atau aplikasi dibuka ulang: nota offline harus
  keluar selagi pelanggan masih di depan meja (2026-09-30). Berlaku untuk jalur
  PIN yang sudah dibuktikan perangkat; jalur yang menunggu server mencoba cetak
  begitu server menerima transaksinya, pada detik yang sama nota ditampilkan.

### 5a. Satu transaksi DIANTAR = dua pekerjaan cetak (2026-09-30)

Permintaan pemilik: transaksi berstatus **diantar** harus mencetak satu dokumen
lagi, **nota antaran (surat jalan)** yang menemani barang keluar toko.

- Satu transaksi diantar menghasilkan **dua baris** di `print_jobs`: nota
  (2 rangkap, teks identik seperti §2) dan surat jalan (satu lembar, tanpa satu
  pun harga). Keduanya dibangun `receipt.js`
  (`buildReceiptLines` / `buildDeliverySlipLines`) dan dikirim ke penghubung
  cetak lewat jalur yang sama persis.
- `job_id` **wajib berbeda** untuk keduanya: bridge mengenali kiriman kembar
  dari `X-Print-Job`, jadi surat jalan dengan `job_id` nota akan dijawab
  `duplicate` dan tidak pernah tercetak. Keduanya berbagi `sync_id` untuk
  menelusuri dokumen dari nota sama; nota final/ambigu tidak dapat dibuang.
- HTTP 400 sesudah final lokal/hasil ambigu menahan nota untuk review, tanpa
  menghapus print job. Nota/surat jalan mungkin sudah keluar dan kertas tidak
  dapat ditarik kembali. Nota belum final yang ditolak pasti tidak punya print
  job. Ini mempertahankan aturan bahwa masalah server tidak membatalkan final lokal.
- Cetak ulang dari riwayat nota dirakit ulang dari jawaban server, bukan dari
  byte di perangkat (byte lama sudah dibuang retensi). Karena tabel
  `transactions` dikelola di luar Django (`managed = False`) dan tidak punya
  kolom status pengantaran, satu-satunya jejak "diantar" yang tersisa adalah
  `shipping_amount > 0`. Ini heuristik: ongkir nol diizinkan validasi saat ini,
  sehingga delivery nol tidak bisa dikenali ulang dari data server saja.
- Yang dibiarkan **bergaris untuk diisi tangan** di surat jalan: nama penerima
  (Kepala YTH), nomor HP, alamat tujuan, kendaraan, dan nomor polisi. POS tidak
  punya field untuk semuanya, dan menebaknya di atas kertas lebih berbahaya
  daripada menyediakan garis isian.

## 6. Yang harus diukur atau diuji sebelum dipakai di toko

Semuanya di perangkat asli (tablet Tab A7 Lite di LAN toko), bukan di laptop:

1. Tablet menjangkau origin POS/proxy `/print`; komputer bridge menjangkau
   printer port 9100. Tablet tidak memerlukan akses raw TCP printer, dan port
   9100 sebaiknya dibatasi ke bridge.
2. Autocutter unit yang terpasang bekerja (Type B) atau tidak (Type D) — ini
   menentukan apakah nota berakhir dengan potong otomatis atau sobek manual.
3. Keterbacaan lembar karbon setelah disimpan beberapa minggu di toko.
4. Roll 2 ply vs single-ply: keterbacaan, biaya, frekuensi ganti, dan prosedur
   menghasilkan nota 2 rangkap + surat jalan 1 lembar. Mengirim job kedua pada
   roll karbon yang sama tetap menghasilkan salinan karbon kedua.
5. Halaman POS bisa dilayani dari LAN lewat HTTP (atau proxy yang sama) tanpa
   memicu mixed content di tablet.
6. Nama worker dari PIN sudah layak cetak di nota (isi `Worker.name`), karena
   nama itu sekarang satu-satunya nama di kertas.

## 7. Yang masih menunggu keputusan

- Surat jalan `DIANTAR` sudah terpasang (§5a); kebutuhan `DIKIRIM`, persisted
  fulfillment, dan prosedur fisik satu-lembar masih terbuka.
- Field nota mana yang boleh dicetak POS: `POS/docs/desain_nota_75x65.pdf` sudah
  menjawab sebagian (tidak dicetak: margin/HPP, kode harga floor, data
  pelanggan), sisanya masih terbuka di `POS/pos-frontend/AGENTS.md` §13.
- Apakah bridge ikut jadi bagian sistem yang di-deploy bersama POS, siapa yang
  menyalakannya, dan bagaimana ia diperbarui.
- Bentuk akhir antrean cetak ulang: store dan perilakunya sudah ada, tetapi
  retensi (14 hari) dan batas ukurannya masih default yang dipilih, bukan
  keputusan pemilik.
- Penandaan status `void`/retur di nota cetak.

## 8. Kriteria penerimaan dan batas verifikasi

Kode menyediakan fondasi untuk kriteria ini, **bukan bukti semuanya lulus**.
Tinjauan 2026-10-01 tidak menjalankan selftest baru atau printer fisik; klaim
pengujian lama tidak menggantikan verifikasi ulang versi saat ini.

- Cetak dari tablet di LAN berhasil tanpa internet. — POS tidak memeriksa
  `navigator.onLine` di jalur cetak; yang menentukan hanya jawaban bridge.
- Printer dimatikan → nota tetap sah dan job masuk antrean cetak ulang. —
  Print job ditulis sesudah finalisasi lokal atau penerimaan server, sesuai
  jalur PIN (§5); gagal kirim dijadwalkan ulang. Selftest menyediakan skenario
  printer palsu mati → 503, bukan bukti printer toko.
- Retry memakai `job_id` sama; bridge bisa menjawab `200 duplicate` selama ID
  masih di memori (maksimal 500 default). Restart/eviction, kegagalan kirim
  sebagian, atau ack ambigu dapat menghasilkan duplikasi; tidak ada jaminan
  exactly-once kertas. Cetak ulang disengaja dari history memakai ID baru.
- Nota yang keluar = 2 lembar (asli + karbon) dengan isi identik, 40 kolom,
  tiap baris ≤ 40 karakter, dan tidak memuat field terlarang. —
  `receipt.assertReceiptWidth`/`receiptWidthIssues` menjaga kolomnya,
  `receipt.assertPrintableSale` menolak field margin/HPP/harga floor, dan
  `escpos.encodeReceipt` hanya mengirim ASCII (non-ASCII ditransliterasi).
- Jalur cetak tidak pernah memblokir penyimpanan transaksi. —
  `queueReceiptPrint` menelan kegagalannya sendiri; nota sudah tersimpan sebelum
  ada satu byte pun disiapkan. Pending/final/ambigu kini tidak dapat dibuang
  melalui checkout; lihat desain offline untuk batas pemulihan review.
- Kasir tahu status printer **sebelum** menyobek nota. — Header POS memanggil
  `GET /health` berkala dan membedakan printer mati dari bridge mati; satu
  ketukan pada tombolnya membuka panel detail (nama printer, alamat printer,
  langkah perbaikan) sekaligus memaksa pemeriksaan ulang lewat tombol di dalam
  panel.
  Polling/label terlihat di implementasi; uji status pada jaringan/printer
  toko tetap perlu dilakukan. Kertas habis/pita/cutter tidak terbaca dari probe.
- Admin toko bisa diberi tahu cara kerja jalur cetak tanpa membaca kode. —
  `POS/docs/panduan_printer_pos.pdf` dulu dibuat langsung dari kontrak
  `bridge.py` oleh `POS/docs/generate_printer_guide_pdf.py`; skrip itu dihapus
  saat bersih-bersih repo, jadi panduan sekarang artefak beku dan harus
  disamakan manual kalau env atau field health berubah.
- History reprint dan surat jalan DIANTAR **sudah ada**. Penandaan void/retur,
  field tambahan, dan `DIKIRIM` belum dirancang/dipasang (§7).
- Encoder POS saat ini memakai umpan akhir 8 baris. Uji langsung
  [uji_cetak.py](../print-bridge/uji_cetak.py) masih default 4 baris, sehingga
  halaman uji pendek bukan bukti panjang nota/cutter sudah tepat.
