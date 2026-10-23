# Print bridge POS (referensi)

Layanan kecil di LAN yang meneruskan byte ESC/POS dari POS ke printer
Epson TM-U220B di port 9100. Rancangannya ada di
[PRINT_PATH_DESIGN.md](../pos-backend/PRINT_PATH_DESIGN.md); berkas ini
menjelaskan setup dan diagnosis. **Ditinjau dari kode: 2026-10-01**, tanpa uji
printer fisik baru.

**Status: implementasi referensi.** Dua keputusan yang sudah dikunci pemilik
(2026-09-29) — nota **2 rangkap** dan jalur cetak **lewat bridge di LAN** —
sudah dipegang [modul printer POS](../pos-frontend/src/services/printer/printQueue.js).
[Deployment](../pos-backend/PRINT_PATH_DESIGN.md#7-yang-masih-menunggu-keputusan)
masih menunggu pemilik operasional dan cara update. Layanan referensi ini dapat
diganti dengan implementasi lain yang mengikuti kontrak klien.

## Menjalankan

Dari folder `POS/print-bridge`, gunakan **Python 3.10+** dan pustaka standar
(tanpa `pip install`). Perintah ini menjalankan layanan; halaman uji berikutnya
mengirim kertas, jadi gunakan IP printer toko yang telah diizinkan, bukan
menganggap IP contoh sebagai target nyata:

```bash
python bridge.py --printer-host 10.0.0.9 --printer-name "Kasir 1"
```

Bentuk variabel lingkungan juga didukung, tapi sintaksnya berbeda per shell di
Windows: `set VAR=nilai` hanya berlaku di cmd, `$env:VAR="nilai"` hanya di
PowerShell. Salah memilih salah satunya membuat bridge berhenti dengan
"PRINTER_HOST belum diisi" walaupun barisnya terlihat benar — karena itu opsi
baris perintah di atas yang dianjurkan saat pemasangan.

```powershell
$env:PRINTER_HOST = "10.0.0.9"; $env:PRINTER_NAME = "Kasir 1"; python bridge.py
```

```bat
set PRINTER_HOST=10.0.0.9
set PRINTER_NAME=Kasir 1
python bridge.py
```

Uji tanpa printer asli (relai, idempotensi, printer mati):

```bash
python bridge.py --selftest
```

## Menguji printer langsung dari satu komputer

Sebelum menyalahkan penghubung cetak atau POS, pastikan dulu hal paling dasar:
komputer di toko memang bisa mencetak ke printer itu. `uji_cetak.py` hanya
melakukan itu - satu halaman uji lewat port mentah 9100, tanpa penghubung cetak
dan tanpa aplikasi POS. Hanya pustaka standar Python 3, jadi bisa dijalankan
dari laptop mana pun yang ada Python-nya.

```bash
python uji_cetak.py --host 10.0.0.9            # kirim satu halaman uji
python uji_cetak.py --host 10.0.0.9 --check    # hanya uji port, tidak mencetak
python uji_cetak.py --host 10.0.0.9 --no-cut   # unit tanpa autocutter (Type D)
```

Halaman uji memakai Font B, 40 kolom, umpan **4** baris lalu potong. Encoder
POS saat ini memakai **8** baris; hasil halaman pendek tidak membuktikan
panjang nota/cutter atau seluruh tata letak POS sudah benar. Kalau sambungannya
gagal, skrip mencetak daftar
periksa singkat (IP berubah, kabel, beda jaringan, firewall) dan keluar dengan
kode 1. Berhasil terkirim tetap bukan berarti berhasil tercetak: kertas habis,
pita kering, atau penutup printer terbuka baru ketahuan dari kertasnya.

Kalau laptopnya tidak punya Python, PowerShell berikut mengirim **satu halaman
uji fisik** serupa (umpan 4 baris); ini bukan diagnosis read-only:

```powershell
$ip = "10.0.0.9"
$body = "`n      UJI CETAK`n`n  Jalur cetak komputer ini OK.`n`n"
$head = [byte[]](27,64,27,77,1,27,97,1)     # reset, Font B, rata tengah
$tail = [byte[]](27,97,0,27,100,4,29,86,0)  # rata kiri, umpan 4, potong
[byte[]]$bytes = $head + [Text.Encoding]::ASCII.GetBytes($body) + $tail
$c = New-Object Net.Sockets.TcpClient
$c.Connect($ip, 9100)
$s = $c.GetStream(); $s.Write($bytes, 0, $bytes.Length); $s.Close(); $c.Close()
"terkirim $($bytes.Length) byte"
```

### Kalau nomor port-nya belum pasti

Angka 9100 bukan setelan yang perlu dicari-cari: Epson sendiri menyebutnya
**RAW port (Port 9100)**, dan pada antarmuka jaringan TM printer nomornya
umumnya tetap — yang biasanya bisa diubah di halaman TCP/IP hanyalah batas
waktu (timeout) port itu. Karena itu EpsonNet Config dipakai untuk memastikan
**alamat IP**-nya, dan nomor port-nya dipastikan dengan mencoba menyambung:

```bash
python uji_cetak.py --host 10.0.0.9 --check
```

Kalau sambungannya diterima, port itu benar; lanjutkan tanpa `--check` untuk
mencetak. Kalau ditolak, periksa dulu IP-nya: alamat yang tercetak oleh printer
saat uji mandiri (matikan printer, tahan tombol FEED, nyalakan sambil tetap
menahan) adalah sumber yang paling bisa dipercaya. Jangan tertipu angka lain
yang muncul di komputer: `515` (LPR), `3289` (EpsonNet Print), dan `80`/`443`
(halaman web printer) bukan jalur cetak yang dipakai POS. Kalau printernya
benar-benar disetel di port lain, isi `PRINTER_PORT` di penghubung cetak; sisi
POS tidak perlu tahu nomornya.

## Konfigurasi

| Variabel | Default | Keterangan |
|---|---|---|
| `PRINTER_HOST` | — | IP printer. **Wajib**; beri DHCP reservation di router. |
| `PRINTER_NAME` | `Epson TM-U220B` | Label yang ditampilkan di panel status POS. **Bukan deteksi**: bridge tidak menanyakan nama ke printer, jadi isi nama yang dikenali toko, mis. `Kasir 1`. |
| `PRINTER_PORT` | `9100` | Raw ESC/POS Epson. |
| `PRINT_BRIDGE_HOST` | `0.0.0.0` | Dengarkan hanya di LAN, jangan diekspos ke internet. |
| `PRINT_BRIDGE_PORT` | `9101` | Port bridge. |
| `PRINT_BRIDGE_TOKEN` | kosong | Kalau diisi, `X-Print-Token` wajib cocok. |
| `PRINT_MAX_BYTES` | `65536` | Batas satu pekerjaan cetak. |
| `PRINT_CONNECT_TIMEOUT` | `5` | Detik menunggu printer menerima koneksi. |
| `PRINT_JOB_MEMORY` | `500` | Berapa ID job terakhir yang diingat untuk idempotensi. |
| `PRINT_HEALTH_TIMEOUT` | `2` | Detik menunggu printer saat `GET /health`; dipakai POS yang menampilkan status printer di header. |

## Kontrak

- `POST /print` — body biner ESC/POS, header `X-Print-Job` **wajib**.
  - `200 {"status":"sent"}` — byte sudah terkirim ke socket printer.
  - `200 {"status":"duplicate"}` — ID sudah dikenali di memori; byte tidak
    dikirim lagi (header `X-Print-Duplicate: 1`). Ini bukan bukti kertas pertama
    telah keluar; baca batas deduplikasi di bawah.
  - `503 {"status":"printer_unreachable"}` — printer tidak menjawab; POS yang
    mengatur percobaan ulang. Job ini **tidak** dikunci sebagai sudah dicetak.
  - `400` / `401` / `411` / `413` — permintaan tidak sah; POS berhenti mencoba
    otomatis dan menandai pekerjaan itu `blocked`.
- `GET /health` (juga `GET /print/health`, karena POS memanggil bridge lewat
  satu alamat same-origin berprefix `/print` dan reverse proxy meneruskan prefix
  itu apa adanya):
  - `status` — bridge hidup. Ini yang dipakai healthcheck mesin/Docker, dan
    nilainya tetap `"ok"` walaupun printer mati: bridge-nya sendiri memang sehat.
  - `printer_status` — `"ok"` kalau printer menerima koneksi di port cetaknya,
    `"unreachable"` kalau tidak, disertai `printer_detail` berisi alasan.
  - `printer` (alamat tujuan), `printer_name` (label dari `PRINTER_NAME`),
    `jobs_seen`, `version`.
  - Pemeriksaan ini **tidak mengirim byte apa pun** ke printer, jadi aman
    dipanggil berkala (`PRINT_HEALTH_TIMEOUT` yang membatasi lamanya). Yang
    diukur hanya "printer menjawab", bukan "printer siap": kertas habis, pita
    kering, atau macet tidak terlihat dari sini dan baru ketahuan saat mencetak.

Bridge tidak menyentuh isi nota: tidak ada nama field, tidak ada tata letak.
Semua tata letak disusun di `pos-frontend/src/services/printer/`
(acuan desain: `POS/docs/desain_nota_75x65.pdf`).

## Status printer di layar kasir

POS memakai `GET /health` untuk menampilkan status di header (`Printer
tersambung` / `Printer tidak tersambung`) setiap 30 detik, setelah setiap
percobaan cetak, dan saat tombolnya diketuk. Karena `printer_status` dipisah
dari `status`, kasir bisa membedakan **printer mati** dari **bridge mati** — dua
hal yang perbaikannya berbeda. Pemeriksaan tetap boleh gagal: kalau bridge tidak
terjangkau, POS cukup menampilkan status itu dan tidak menahan penjualan.

Tombol status itu juga **terbuka ke bawah** menjadi panel detail
(`pos-frontend/src/components/PrinterStatusPanel.vue`) yang memuat nama printer
(`printer_name`), alamat printer, waktu pemeriksaan terakhir, kalimat penjelas
bagaimana nota sampai ke printer, dan daftar langkah perbaikan yang cocok
dengan status saat itu. Isi panel berasal dari field health di atas; kalau
bridge-nya versi lama yang belum mengirim `printer_status`, panel tetap
menampilkan status tersambung dengan catatan bahwa detail tidak tersedia.

Panel itu sengaja **tidak** memakai kata "bridge", tidak menampilkan versi,
dan tidak menampilkan `jobs_seen`: itu tiga hal yang tidak bisa ditindaklanjuti
oleh kasir maupun admin toko. Istilah yang dipakai di semua teks pemakai adalah
**"penghubung cetak"**. Field teknisnya tetap dilaporkan di `/health` supaya
diagnosa tidak kehilangan data — yang berubah hanya apa yang tampil di layar.
Karena itu, kalau menambah field health baru, putuskan lebih dulu apakah kasir
butuh melihatnya; default-nya tidak.

Panduan bahasa toko untuk admin/pemilik (cara kerja koneksi, arti status,
langkah kalau merah) ada di `POS/docs/panduan_printer_pos.pdf`; dulu dibuat ulang
lewat `POS/docs/generate_printer_guide_pdf.py` yang membaca kontrak dan default
dari berkas `bridge.py` di repo ini. Skrip itu dihapus saat bersih-bersih repo,
jadi kalau kontrak atau environment berubah, samakan panduannya manual.

## Menyambungkan POS

POS mengirim ke path relatif `/print` supaya same-origin dan bebas mixed content:

- **Dev**: `vite.config.js` mem-proxy `/print` ke `PRINT_BRIDGE_TARGET`
  (default `http://127.0.0.1:9101`) — alamat **mesin yang menjalankan penghubung
  cetak**, bukan alamat printer: printer memakai port 9100 dan bukan HTTP, jadi
  alamat printer di baris itu hanya menghasilkan "penghubung cetak tidak
  menjawab" di layar kasir. [Start POS.bat](../Start%20POS.bat) tidak menyalakan
  bridge; jalankan terpisah.
- **Produksi (belum dikonfigurasi):** gunakan reverse proxy origin POS yang
  meneruskan `/print` ke bridge LAN. HTTP LAN atau sertifikat bridge lokal
  dapat menghindari mixed content, tetapi **tidak** menyelesaikan CORS:
  bridge referensi tidak menyediakan OPTIONS/header CORS untuk browser
  lintas-origin. Alamat bridge berbeda port/origin memerlukan proxy atau
  perubahan CORS yang ditinjau, bukan hanya mengisi URL lengkap.

## Prasyarat jaringan

- Printer punya IP tetap (DHCP reservation); port 9100 hanya boleh dibuka untuk
  bridge, bukan untuk tablet.
- Bridge jalan di mesin LAN (mini PC/Raspberry Pi, atau mesin yang juga
  menyajikan POS), **bukan di tablet**: Android membatasi service latar.
- Bridge dan POS harus tetap hidup saat internet mati — jalur cetak tidak boleh
  melewati internet sejak awal.

## Keterbatasan yang diketahui

- **Deduplikasi hanya di memori dan terbatas** (default 500 ID). Restart atau
  eviction menghilangkan ID sehingga retry lama dapat mencetak lagi. Kiriman
  parsial/ack ambigu juga bisa menggandakan kertas. `200 sent` hanya berarti
  byte berhasil dikirim socket, **bukan exactly-once cetak fisik**. Cetak ulang
  disengaja operator memakai ID job baru, bukan retry job yang sama.
- **Tidak ada autentikasi per device.** Siapa pun di LAN bisa kirim byte ke
  printer. Kalau jaringan toko tidak sepenuhnya dipercaya, isi
  `PRINT_BRIDGE_TOKEN` dan setel `VITE_PRINT_TOKEN` di POS. Token `VITE_*`
  dapat dibaca browser, jadi bukan rahasia/auth kuat. GET health saat ini
  tidak memerlukan token dan melaporkan alamat printer; tetap batasi LAN/firewall.
- **Tidak ada antrean di sisi bridge**, sesuai desain: antrean ada di POS
  (Dexie, `print_jobs`), supaya bridge bisa dimatikan tanpa kehilangan pekerjaan.
- **`/health` menyentuh printer.** Kalau antarmuka printer menolak koneksi
  tambahan dari alat pemantau (beberapa unit hanya menerima satu klien),
  koneksi probe dapat berebut dengan cetak. Timeout lebih panjang belum tentu
  membantu; diagnosis frekuensi/probe dan antarmuka printer dulu. Tidak mengirim
  byte bukan jaminan tidak mempengaruhi kapasitas koneksi printer.
