# Server Rumah via Tailscale + Laptop Toko sebagai Bridge Printer

**Konteks:** server POS (Django + halaman kasir) di rumah, diakses tablet toko lewat
Tailscale; laptop nganggur di toko menjadi penghubung cetak. Status: Oktober 2026.

## 1. Topologi yang dianjurkan

```text
Tablet --Tailscale (100.x)--> server rumah (API + halaman kasir)
Tablet --LAN toko -----------> laptop bridge (:9101) --> printer (9100)
```

Prinsipnya: **jalur cetak tetap LAN-lokal**, hanya API dan halaman yang lewat
Tailscale. Internet mati pun printer tetap jalan dan nota tetap keluar; backend
yang antre di tablet (desain offline yang sudah ada).

Yang TIDAK dianjurkan: mem-proxy `/print` lewat server rumah. Itu membuat
printer ikut mati saat internet mati dan menghilangkan keunggulan desain.

## 2. Server rumah via Tailscale

- Tablet dan server join tailnet yang sama; tablet membuka `http://100.x.y.z`
  (atau nama MagicDNS). Latensi WireGuard biasanya kecil; relay DERP pun masih
  cukup untuk POS.
- `http://100.x` **bukan secure context** (hanya localhost yang dianggap aman),
  jadi: service worker tetap tidak terpasang, `crypto.randomUUID` tetap
  undefined. ID aman non-HTTPS sudah ditangani `services/id.js` (nota, bukti,
  refund). HTTPS hanya datang via `tailscale serve` (nama `.ts.net`).
- Titik kegagalan tunggal pindah ke rumah: listrik/ISP rumah mati = semua toko
  berjalan offline-backend sampai koneksi pulih. Perlu UPS di rumah dan alamat
  yang stabil. Pastikan antrean Dexie tablet cukup untuk skenario mati seharian.
- Celah yang tersisa: halaman kasir disajikan dari rumah, jadi tablet yang
  aplikasinya tertutup tidak bisa buka ulang halaman saat internet mati total
  (PWA keluar scope). Tablet yang halamannya sudah terbuka tetap bisa jualan
  offline. Opsi lanjutan: sajikan salinan frontend dari laptop toko sebagai
  fallback (keputusan deployment tersendiri).

## 3. Laptop toko sebagai bridge printer

Syarat kecil (stdlib Python 3.10+, tanpa `pip install`), sesuai
[README bridge](../print-bridge/README.md):

1. Colok LAN toko, beri IP tetap (DHCP reservation).
2. `python bridge.py --printer-host <IP-printer> --printer-name "Kasir 1"`.
3. Matikan sleep/hibernate, lid-close = nothing, autostart saat boot.
4. Firewall: port 9101 hanya dari LAN toko, jangan diekspos ke internet.
5. Uji: `uji_cetak.py --check` lalu satu halaman uji sebelum dipakai kasir.

Bonus laptop: baterai = UPS gratis saat listrik kedip.

## 4. Perbandingan: server di rumah vs server di toko

| Aspek | Server di rumah (+ Tailscale) | Server di toko (laptop/mini PC) |
|---|---|---|
| Akses multi-toko/cabang | Mudah, semua cabang ke satu titik | Perlu VPN/tunnel per cabang |
| Ketergantungan internet toko | Total untuk API/sync | Hanya untuk hal di luar LAN |
| Ketergantungan listrik/ISP rumah | Ya, titik gagal tunggal | Tidak |
| Latensi API | Kecil (biasanya <50 ms) | Sangat kecil (LAN) |
| Perawatan fisik server | Di rumah, terjangkau pemilik | Di toko, butuh orang/SOP |
| Keamanan fisik | Rumah (terkontrol) | Toko (ramai, risiko colok-cabut) |
| Biaya perangkat | Nol (pakai yang ada) | Perlu mesin standby di toko |
| Jalur cetak | Tetap LAN-lokal via bridge (sama) | LAN-lokal via bridge (sama) |
| Secure context / PWA | Tetap tidak (http IP biasa) | Tetap tidak, kecuali HTTPS lokal |

## 5. Kesimpulan

Server rumah + Tailscale **layak** untuk satu toko, dengan dua syarat: UPS dan
stabilitas koneksi rumah dijaga, dan jalur cetak tidak ikut lewat Tailscale.
Keuntungan terbesarnya adalah satu titik data untuk (calon) cabang berikutnya;
harga yang dibayar adalah ketergantungan pada internet dan listrik rumah.

Jika toko hanya satu dan internetnya tidak stabil, server di toko (bisa
digabung di laptop bridge yang sama) lebih tahan banting: seluruh sistem hidup
di LAN dan internet mati hanya menunda hal yang memang butuh internet.
Keputusan ini bisa ditunda sampai pola mati-listrik/mati-internet toko terukur
— kode aplikasi tidak perlu berubah untuk kedua pilihan.
