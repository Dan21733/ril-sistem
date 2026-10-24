# Ril: aplikasi kiosk tablet toko

Satu aplikasi untuk semua tablet toko. Tiap tablet diatur jadi **Gudang** atau **Kasir**.
Ril jadi layar utama dan satu-satunya aplikasi yang bisa dibuka (selain Tailscale).

Sebagai *device owner*, Ril mematikan: transfer file USB, opsi pengembang/USB debugging,
pasang APK dari luar, reset pabrik, safe mode, kartu SD, tambah pengguna. Tailscale dipaksa
selalu menyala. Aplikasi lain bisa disembunyikan (tidak jalan sama sekali) dari menu pemilik.

**Menu pemilik:** ketuk pojok kanan atas layar 5 kali dalam 3 detik, lalu masukkan PIN pemilik.
Isinya: muat ulang, Wi-Fi, buka Tailscale, ganti mode, ubah alamat, sembunyikan aplikasi,
ganti PIN, status, mode perawatan (buka kunci sementara), dan lepas Ril.

## Build

APK dibangun GitHub Actions (`.github/workflows/ril-android.yml`) setiap ada perubahan di
`android/`. Unduh dari tab **Actions › Ril Android › run terakhir › Artifacts**.
Butuh secret `RIL_KEYSTORE_B64` dan `RIL_KEYSTORE_PASSWORD` (lihat komentar di workflow).
Selalu kunci yang sama: tablet hanya menerima update dengan tanda tangan yang sama.

## Memasang di tablet (sekali per tablet)

Butuh PC Windows dengan **Android platform-tools** (`adb`) dan kabel USB data.

1. **Pastikan tidak ada data yang perlu.** Tablet kasir: semua nota sudah terkirim ke server.
2. **Reset pabrik.** Saat penyiapan awal: sambungkan Wi-Fi, **lewati semua akun**
   (Google dan Samsung). Device owner hanya bisa dipasang kalau belum ada akun sama sekali.
3. **Nyalakan USB debugging:** Pengaturan › Tentang tablet › Informasi perangkat lunak ›
   ketuk *Nomor versi* 7 kali › Opsi pengembang › *USB debugging*. Colok ke PC, izinkan.
4. **Pasang Tailscale** (APK, karena tanpa akun Google tidak ada Play Store):
   `adb install tailscale.apk`. Buka Tailscale, login (browser bawaan bisa dipakai tanpa
   akun Google), izinkan VPN. Di admin console Tailscale beri tag perangkat ini.
5. **Pasang Ril:** `adb install ril.apk`.
6. **Jadikan device owner (terakhir, karena setelah ini USB debugging mati):**
   ```
   adb shell dpm set-device-owner id.dbrl.ril/.AdminReceiver
   ```
   Harus keluar `Success`. Kalau gagal "already some accounts", ada akun yang belum
   dihapus: hapus di Pengaturan › Akun, atau ulangi reset.
7. Buka Ril, **buat PIN pemilik**, lalu lewat menu pemilik pilih mode (Gudang/Kasir).
8. Halaman gudang/kasir menampilkan kode perangkat: setujui di master
   (Kasir › Perangkat operasional) seperti biasa.
9. Lewat menu pemilik › *Sembunyikan aplikasi*: sembunyikan yang tidak perlu (satu per satu,
   tes setelahnya). Jangan sembunyikan keyboard.

## Update Ril

Menu pemilik › *Mode perawatan* (USB debugging bisa dinyalakan lagi) ›
`adb install -r ril-baru.apk` › menu pemilik › *Kunci lagi*. Data dan PIN tetap.

## Kalau PIN pemilik lupa

Reset pabrik juga diblokir, jadi jalan keluarnya flash ulang firmware Samsung (Odin) di
mode download. Simpan PIN pemilik di tempat aman.
