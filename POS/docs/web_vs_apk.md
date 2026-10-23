# Web App vs APK untuk POS Toko

**Konteks:** POS kasir multi-tablet, printer Epson TM-U220B via LAN, backend Django + print bridge di LAN. Status: Oktober 2026.
**Rekomendasi: tetap Web app + bridge.** APK baru layak jika ada syarat keras (lihat §4).

## 1. Web app (kondisi saat ini + bridge LAN)

Arsitektur: `Tablet (browser) → POST /print ke penghubung cetak (HTTP LAN) → TCP 9100 ke printer`.

### Kelebihan

1. **Multi-tablet 1 printer sudah terjawab.** Semua tablet mengarah ke satu bridge; bridge menyerikan antrean ke printer. Tidak ada batasan 1-ke-1 seperti Bluetooth.
2. **Update sekali, semua ikut.** Perbaikan layout nota / aturan kasir dipasang di server; semua tablet langsung memakai versi sama. Tidak ada versi timpang antar tablet.
3. **Biaya rawat paling kecil.** Tanpa pipeline build Android, signing key, review store, atau uji per merek tablet. Satu basis kode web.
4. **Antrean terpusat.** Idempotensi `X-Print-Job`, status `Printer tersambung/tidak` di header, dan retry `print_jobs` sudah jadi dan teruji.
5. **Investasi tidak hangus.** Jika suatu hari perlu APK, web yang ada tinggal dibungkus — logika nota, antrean, dan retry dipakai ulang.

### Kekurangan

1. **Tidak bisa sentuh printer langsung.** Browser tidak boleh buka Bluetooth SPP, USB host, atau TCP mentah — wajib lewat bridge. Artinya tetap butuh satu mesin LAN standby untuk bridge.
2. **Halaman bergantung server penyaji.** Service worker/PWA belum ada; tablet masih memuat aplikasi dari server. Kalau server penyaji mati, halaman tidak bisa dibuka ulang (data antrean lokal tetap aman).
3. **Flush antrean hanya saat halaman terbuka.** Tidak ada service latar; retry jalan saat aplikasi dibuka (mount/checkout/aksi manual).
4. **Tablet tidak bisa dikunci penuh.** Kasir masih bisa pindah tab/aplikasi; tidak ada mode kios bawaan browser.

## 2. APK (bungkus web + plugin cetak native)

Arsitektur opsi A (disarankan jika jadi APK): web yang ada dibungkus (mis. Capacitor) + satu plugin native kecil; hanya transport terakhir `sendPrintJob` diganti dari `fetch` ke soket native. Opsi B (tulis ulang native penuh) tidak direkomendasikan — biayanya besar tanpa manfaat tambahan untuk toko ini.

### Kelebihan

1. **Printer langsung tanpa bridge.** Boleh buka Bluetooth Classic SPP, USB OTG, dan TCP mentah ke 9100. Satu tablet bisa pairing langsung ke printer/adapter Bluetooth — satu-satunya cara "tablet → printer Bluetooth" yang benar-benar jalan.
2. **Service latar & auto-start.** Antrean cetak bisa dicoba ulang walau aplikasi di-background; aplikasi bisa start otomatis saat tablet reboot.
3. **Mode kios.** Kunci aplikasi di depan (lock task), cocok untuk tablet kasir yang dipegang banyak orang.
4. **Aset terbundel.** Halaman kasir tetap terbuka walau server penyaji mati; tinggal sinkronisasi data yang tetap butuh jaringan seperti sekarang.

### Kekurangan

1. **Beban permanen baru.** Pipeline build, signing key (hilang = install ulang semua + data antrean hilang), versionCode per rilis, distribusi manual/Play, uji tiap merek dan versi Android.
2. **Bluetooth tetap 1-ke-1.** APK tidak mengubah fisika Bluetooth: satu printer Bluetooth hanya melayani satu koneksi aktif. Multi-tablet tetap butuh printer LAN atau satu tablet host — masalah yang sama seperti web.
3. **Versi gampang timpang (sebar manual).** Tanpa mekanisme update otomatis, tablet bisa menjalankan versi beda-beda. Perlu daftar versi per tablet + cek `version.json` di LAN.
4. **Hilangnya antrean terpusat (jika bridge dipensiunkan).** Tiap tablet mengelola antreannya sendiri; tabrakan cetak bersamaan diserap retry per tablet, bukan satu titik. Bridge boleh dipertahankan untuk ini — tapi berarti mesin standby tetap ada, menghilangkan sebagian alasan pindah ke APK.
5. **Backend tetap sama.** Aturan PIN, idempotensi, validasi stok/harga tidak berubah — APK hanya mengganti cara byte sampai ke printer.

## 3. Perbandingan ringkas

| Aspek | Web + bridge | APK |
|---|---|---|
| Multi-tablet 1 printer LAN | Ya, antrean terpusat | Ya, antrean per tablet (atau tetap via bridge) |
| Printer Bluetooth langsung | Tidak bisa | Bisa (tetap 1-ke-1) |
| Mesin tambahan | 1 (bridge) | Tidak (direct), atau tetap ada jika antrean terpusat dipertahankan |
| Update | Sekali di server | Per tablet (manual/SOP versi) |
| Kunci tablet mode kios | Tidak | Ya |
| Cetak saat app di-background | Tidak | Ya (service native) |
| Biaya rawat | Kecil | Sedang–besar |

## 4. Kesimpulan dan syarat pindah

**Tetap Web app + bridge** selama printer di LAN dan kebutuhan toko adalah multi-tablet 1 printer dengan tim kecil. Kelemahan web yang tersisa (PWA, penyajian dari server LAN) lebih murah ditutup daripada memelihara APK.

**Pindah ke APK jika salah satu menjadi syarat keras:** (a) printer harus Bluetooth/USB langsung tanpa mesin tambahan, (b) tablet harus dikunci kios, atau (c) cetak harus jalan dari background berjam-jam. Jika itu terjadi, ambil opsi bungkus (Capacitor + plugin cetak), bukan tulis ulang.
