"""Menu Kasir di master: karyawan & PIN, tablet kasir, aturan kasir."""
import datetime

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone

from pos import pengaturan as aturan

from master.akun.akses import perlu
from master.layanan import karyawan as svc
from master.layanan import nota_tinjau as svc_tinjau
from master.layanan.inti import Tolak


@perlu("kelola_kasir")
def karyawan(request):
    if request.method == "POST":
        aksi, wid = request.POST.get("aksi"), request.POST.get("id")
        try:
            if aksi == "tambah":
                svc.rapikan_wa(request.POST.get("wa"))   # tolak nomor salah sebelum karyawan dibuat
                wid, _ = svc.tambah(request.user, request.POST.get("nama"))
                svc.atur_wa(request.user, wid, request.POST.get("wa"))
                messages.success(request, "Karyawan ditambahkan. PIN-nya ada di kotak PIN baru di atas.")
            elif aksi == "nama":
                if "wa" in request.POST:
                    svc.rapikan_wa(request.POST.get("wa"))   # nomor salah: tolak sebelum nama ikut tersimpan
                svc.ganti_nama(request.user, wid, request.POST.get("nama"))
                if "wa" in request.POST:
                    svc.atur_wa(request.user, wid, request.POST.get("wa"))
                messages.success(request, "Tersimpan.")
            elif aksi == "reset":
                svc.reset_pin(request.user, wid)
                messages.success(request, "PIN baru langsung berlaku. PIN lama tidak bisa dipakai lagi untuk nota baru.")
            elif aksi == "nonaktif":
                svc.nonaktifkan(request.user, wid)
                messages.success(request, "Karyawan dinonaktifkan. PIN-nya tidak bisa dipakai untuk nota baru.")
            elif aksi == "aktif":
                svc.aktifkan(request.user, wid)
                messages.success(request, "Karyawan aktif lagi dengan PIN baru.")
            elif aksi in ("penyetuju", "bukan_penyetuju"):
                svc.atur_penyetuju(request.user, wid, aksi == "penyetuju")
                messages.success(request, "Penyetuju harga di bawah floor diperbarui.")
            elif aksi == "hak":
                from master.layanan import akun_karyawan
                akun_karyawan.atur_semua(request.user, wid, request.POST.getlist("hak"))
                messages.success(request, "Hak akses tersimpan. Berlaku langsung; di tablet offline setelah daftar PIN diperbarui.")
            elif aksi == "sembunyikan":
                svc.sembunyikan_pin(request.user, wid or None)
                messages.success(request, "PIN disembunyikan.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:karyawan")
    from master.akun.akses import HAK_KARYAWAN_OPS, LABEL_HAK_OPS, PAKET_OPS
    from master.layanan import akun_karyawan
    svc.bersihkan_pin_tampil()
    daftar = svc.daftar()
    ops = akun_karyawan.hak_ops_semua()
    for d in daftar:
        d["hak"] = set(ops.get(d["id"], [])) | ({"pos_jual"} if d["jual"] else set()) | ({"penyetuju"} if d["penyetuju"] else set())
        ringkas = (["Kasir"] if d["jual"] else []) + (["Penyetuju"] if d["penyetuju"] else [])
        o = ops.get(d["id"], [])
        if o:
            ringkas.append(f"{len(o)} hak operasional")
        d["hak_ringkas"] = ringkas
        d["hak_ops_label"] = [LABEL_HAK_OPS[h] for h in o]
    orang = {d["id"]: d for d in daftar}
    pin_baru = []
    for t in svc.pin_untuk_dibagikan():
        d = orang.get(t.worker_id, {})
        nama = d.get("nama", t.worker_id)
        pin_baru.append({"nama": nama, "t": t, "wa": d.get("wa", ""), "wa_tampil": d.get("wa_tampil", ""),
                         "link_wa": svc.link_wa(d.get("wa", ""), svc.pesan_pin(nama, t.pin, t.berlaku_mulai))})
    return render(request, "master/kasir_karyawan.html", {
        "menu": "karyawan", "daftar": daftar, "pin_baru": pin_baru, "now_waktu": timezone.now(),
        "grup_hak": [("Kasir (tablet)", akun_karyawan.HAK_KASIR), *HAK_KARYAWAN_OPS],
        "paket_ops": [(k, v[0], " ".join(sorted(v[1]))) for k, v in PAKET_OPS.items()],
        "rotasi_aktif": aturan.baca("rotasi_aktif") == "1", "rotasi_berikut": svc.rotasi_berikut(),
    })


@perlu("kelola_kasir")
def perangkat_ops(request):
    """Perangkat yang boleh membuka aplikasi operasional (/gudang/): setujui, beri nama, cabut."""
    from master.layanan import perangkat_ops as svc_perangkat
    if request.method == "POST":
        aksi, pid = request.POST.get("aksi"), request.POST.get("id")
        try:
            if aksi == "setujui":
                p = svc_perangkat.setujui(request.user, pid, request.POST.get("nama"))
                messages.success(request, f"{p.kode} ({p.nama}) disetujui. Minta penggunanya menekan 'cek lagi'.")
            elif aksi == "nama":
                svc_perangkat.ganti_nama(request.user, pid, request.POST.get("nama"))
                messages.success(request, "Nama perangkat tersimpan.")
            elif aksi == "cabut":
                svc_perangkat.cabut(request.user, pid)
                messages.success(request, "Perangkat dicabut. Siapa pun yang sedang masuk di perangkat itu langsung keluar.")
            elif aksi == "hapus":
                svc_perangkat.hapus_menunggu(request.user, pid)
                messages.info(request, "Permintaan dihapus.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:perangkat_ops")
    return render(request, "master/perangkat_ops.html", {"menu": "perangkat", "tab_perangkat": "ops", **svc_perangkat.daftar()})


@perlu("kelola_kasir")
def karyawan_slip(request):
    sekarang = timezone.now()
    nama = {d["id"]: d["nama"] for d in svc.daftar()}
    slip = [{"nama": nama.get(t.worker_id, t.worker_id), "t": t}
            for t in svc.pin_untuk_dibagikan(sekarang)]
    return render(request, "master/kasir_slip.html", {"slip": slip, "sekarang": sekarang})


@perlu("kelola_kasir")
def tablet(request):
    if request.method == "POST":
        aksi = request.POST.get("aksi")
        try:
            if aksi == "pendaftaran":
                buka = request.POST.get("buka") == "1"
                svc.atur_pendaftaran(request.user, buka)
                messages.success(request, "Pendaftaran tablet baru dibuka." if buka else
                                 "Pendaftaran ditutup: hanya tablet di daftar ini yang bisa memakai kasir.")
            elif aksi == "nama":
                svc.atur_tablet(request.user, request.POST.get("id"), nama=request.POST.get("nama"))
                messages.success(request, "Nama tablet tersimpan.")
            elif aksi in ("aktif", "nonaktif"):
                svc.atur_tablet(request.user, request.POST.get("id"), aktif=aksi == "aktif")
                messages.success(request, "Tablet diaktifkan." if aksi == "aktif" else
                                 "Tablet dinonaktifkan. Ia tidak bisa mengirim nota atau membuka daftar barang.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:tablet")
    return render(request, "master/kasir_tablet.html", {
        "menu": "perangkat", "tab_perangkat": "tablet", "daftar": svc.daftar_tablet(),
        "terbuka": aturan.baca("pendaftaran_tablet") != "tutup",
    })


@perlu("kelola_kasir")
def aturan_kasir(request):
    if request.method == "POST":
        try:
            svc.simpan_aturan_pin(request.user, request.POST)
            messages.success(request, "Aturan kasir tersimpan.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:aturan_kasir")
    berikut = svc.rotasi_berikut()
    return render(request, "master/kasir_aturan.html", {
        "menu": "aturan_kasir", "a": aturan.semua(), "now_waktu": timezone.now(), "hari": list(enumerate(svc.NAMA_HARI)),
        "berikut": berikut, "penjadwal_hidup": svc.penjadwal_hidup(),
        "siapkan_mulai": (berikut - datetime.timedelta(days=aturan.angka("rotasi_siapkan_hari", 2))) if berikut else None,
    })


@perlu("kelola_kasir")
def nota_tinjau(request):
    if request.method == "POST":
        aksi, sid = request.POST.get("aksi"), request.POST.get("sync_id")
        try:
            if aksi == "terima":
                n = svc_tinjau.terima(request.user, sid, request.POST.get("worker_id"), request.POST.get("catatan"))
                messages.success(request, f"Nota {n.transaction_id or ''} diterima dan masuk penjualan.")
            elif aksi == "tolak":
                n = svc_tinjau.tolak(request.user, sid, request.POST.get("alasan"))
                messages.success(request, f"Nota {n.transaction_id or ''} ditolak.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect(f"{request.path}?status={request.POST.get('kembali', 'menunggu')}")
    status = request.GET.get("status", "menunggu")
    if status not in ("menunggu", "diterima", "ditolak"):
        status = "menunggu"
    from pos.models import Worker
    return render(request, "master/kasir_tinjau.html", {
        "menu": "nota_tinjau", "status": status, "daftar": svc_tinjau.daftar(status),
        "pekerja": list(Worker.objects.order_by("name").values_list("id", "name")),
    })
