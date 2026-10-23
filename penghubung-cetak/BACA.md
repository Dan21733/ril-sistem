# Penghubung Cetak (aplikasi laptop toko)

Aplikasi Windows sekali-klik yang menjalankan penghubung cetak POS
(`POS/print-bridge/bridge.py`, **tidak diubah**) di laptop admin toko.
Karyawan cukup melihat satu warna:

| Layar | Artinya | Yang dilakukan karyawan |
|---|---|---|
| **HIJAU** SIAP MENCETAK | Printer tersambung | Tidak ada. Biarkan menyala. |
| **KUNING** MENCARI / PILIH PRINTER | Sedang mencari, atau ada lebih dari satu printer | Tunggu. Kalau diminta memilih: tekan *Cetak uji* per printer, lalu *Pakai ini* pada yang kertasnya keluar di printer kasir. |
| **MERAH** PRINTER TIDAK MENJAWAB | Printer mati / kabel lepas | Nyalakan printer, colok kabel LAN. Aplikasi menyambung lagi sendiri. |

Yang dikerjakan sendiri oleh aplikasi:

- **Mencari printer** di jaringan toko (port 9100) lalu mengingatnya. Printer
  dikenali dari alamat MAC-nya, jadi kalau router memberi IP baru, aplikasi
  menemukannya lagi dan tidak salah pakai printer lain.
- **Menyala sendiri** saat laptop dihidupkan (dalam keadaan diperkecil).
- **Menjaga laptop tidak tidur** selama aplikasi menyala (layar tetap boleh mati).
- **Tombol X hanya mengecilkan** jendela. Mematikan hanya lewat
  *Pengaturan → Matikan penghubung*, dengan konfirmasi.
- Menghitung nota tercetak hari ini dan kejadian terakhir.
- Menerima panggilan langsung dari tablet (CORS), kalau nanti dipakai jalur
  langsung LAN. Jalur lewat server POS tidak membutuhkannya.

Pengaturan dan catatan kejadian tersimpan di `%APPDATA%\PenghubungCetak\`.

## 1. Membuat exe (sekali, di PC yang ada Python)

Klik dua kali `buat_exe.bat`. Ia membuat venv sendiri (`.venv-build`), memasang
PyInstaller, menjalankan uji, lalu menghasilkan `dist\Penghubung Cetak.exe`.
Laptop toko tidak perlu Python.

Kalau `POS/print-bridge/bridge.py` berubah (diperbarui teman), jalankan lagi
lalu salin ulang exe-nya.

## 2. Memasang di laptop toko

1. Pasang **Tailscale**, masuk dengan akun yang sama dengan PC server POS.
   Di admin console Tailscale, matikan *key expiry* untuk laptop ini supaya tidak
   tiba-tiba keluar dari tailnet.
2. Salin `Penghubung Cetak.exe` ke laptop (mis. ke Desktop), klik dua kali.
   - Kalau muncul "Windows protected your PC": *More info → Run anyway*
     (exe buatan sendiri tidak bertanda tangan).
   - Kalau Windows Firewall bertanya: centang jaringan **Private**, *Allow*.
     Atau buka *Pengaturan → Izinkan di firewall* (port 9101 hanya dibuka untuk
     jaringan lokal dan Tailscale).
3. Tunggu sampai **hijau**, tekan **Cetak Halaman Uji**, pastikan kertas keluar.
4. Laptop: colok charger; *Control Panel → Power Options → Choose what closing
   the lid does* → **Do nothing** (saat dicolok).

## 3. Menyambungkan server POS

Buka *Pengaturan* di aplikasi, salin isi **Untuk server POS** (bentuknya
`http://100.x.y.z:9101`, alamat Tailscale laptop; alamat ini tidak berubah).

Di komputer yang menjalankan `Start POS.bat`, isi di
`POS/pos-frontend/.env.local`:

```
PRINT_BRIDGE_TARGET=http://100.x.y.z:9101
```

lalu tutup dan jalankan ulang `Start POS.bat`. Panel printer di layar kasir
seharusnya menjadi "Printer tersambung".

Kalau tailnet memakai aturan akses (grants/ACL) yang membatasi, izinkan
komputer server POS ke laptop ini di `tcp:9101`.

## Batas yang perlu diketahui

- Lewat Tailscale, nota tercetak hanya kalau **internet toko dan internet
  server POS** sama-sama hidup. Karena server POS-nya juga di luar toko, saat
  internet toko mati tablet memang sudah tidak bisa ke server; antrean cetak
  di tablet mengulang sendiri setelah tersambung lagi.
- Kalau nanti mau printer tetap jalan saat internet mati, tablet perlu
  memanggil laptop ini langsung lewat LAN (`VITE_PRINT_BRIDGE_URL` di POS,
  laptop diberi IP tetap di router). Aplikasi ini sudah siap untuk itu (CORS),
  tapi itu perubahan konfigurasi POS yang perlu dibicarakan dengan teman.
- Status hijau berarti printer **menerima koneksi**. Kertas habis atau pita
  kering tidak terlihat dari sini (sama dengan `bridge.py`).
- Memori "nota yang sama tidak dicetak dua kali" hilang kalau aplikasi atau
  laptop dimulai ulang (sifat `bridge.py`).

## Berkas

| Berkas | Isi |
|---|---|
| `penghubung_cetak.py` | Jendela aplikasi |
| `inti.py` | Cari printer, pembungkus bridge, alat bantu Windows |
| `uji_inti.py` | Uji tanpa printer asli (`python uji_inti.py`) |
| `buat_exe.bat` | Membuat exe |
| `ikon.ico` | Ikon aplikasi |
