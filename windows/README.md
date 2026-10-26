# Ril untuk Windows (laptop toko)

Ril membuka halaman toko (bawaan: gudang) layar penuh, tanpa alamat URL, tab, klik kanan, atau
DevTools. Hanya server toko yang bisa dibuka. Bar atas: jam, baterai, **Muat ulang**, **Matikan**.

**Menu pemilik:** klik tulisan **Ril** di kiri atas 5 kali dalam 3 detik (atau Ctrl+Shift+Alt+R),
lalu PIN pemilik. Isi: muat ulang, ubah alamat server, Wi-Fi, ganti PIN, status,
buka desktop Windows (perawatan), keluar akun, mulai ulang.

## Build
GitHub Actions (`.github/workflows/ril-windows.yml`) membangun `Ril.exe` setiap ada perubahan di
`windows/`. Unduh dari **Actions › Ril Windows › run terakhir › Artifacts**.

## Memasang di laptop toko
1. Akun **pemilik** (administrator) untuk kamu. Akun **karyawan** dibuat sebagai akun *Standard*.
2. Tailscale tersambung dengan key bertag (`tailscale up --auth-key=... --unattended`).
3. Login ke akun karyawan **sekali**, lalu **Sign out**.
4. Dari akun pemilik: ekstrak artifact ke satu folder, buka PowerShell **Run as administrator**
   di folder itu:
   ```
   powershell -ExecutionPolicy Bypass -File .\pasang-kiosk.ps1 -Akun karyawan
   ```
5. Login ke akun karyawan: Ril langsung terbuka dan minta **PIN pemilik** baru (buat oleh kamu).
6. Halaman gudang menampilkan kode perangkat: setujui di master (Kasir › Perangkat operasional).

Batalkan: `.\lepas-kiosk.ps1 -Akun karyawan` (akun karyawan harus sign out dulu).

## Catatan
- Butuh **Microsoft Edge WebView2 Runtime** (bawaan Windows 11; Windows 10 biasanya sudah ada).
- Shell pengganti, Task Manager mati, dan blokir USB dipasang lewat registry akun karyawan, jadi
  jalan di Windows Home. Belum diuji tahan terhadap Windows Update besar; cek lagi setelah update.
- Penyimpanan USB ditolak hanya di akun karyawan. Mouse/keyboard USB tetap jalan.
- Lupa PIN pemilik: login akun pemilik, hapus `C:\Users\<karyawan>\AppData\Local\Ril\setelan.json`
  (PIN baru diminta lagi saat Ril dibuka). Data login halaman (cookie perangkat) tidak ikut terhapus.
