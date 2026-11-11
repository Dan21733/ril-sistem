from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

from master.layanan.toko import saring as saring_toko
from master.layanan import opname as svc
from master.akun.akses import hak_pengguna, perlu
from master.layanan.inti import Tolak
from master.models import Opname


@perlu("opname")
def daftar(request):
    qs = saring_toko(Opname.objects.exclude(status="draf"), request).select_related("toko").annotate(n=Count("baris"))
    saring = request.GET.get("status", "")
    if saring in dict(Opname.STATUS):
        qs = qs.filter(status=saring)
    from master.models import SesiOpname
    return render(request, "master/opname.html", {
        "menu": "opname", "daftar": qs[:100], "saring": saring, "status": [s for s in Opname.STATUS if s[0] != "draf"],
        "sedang": Opname.objects.filter(status="draf").annotate(n=Count("baris")).filter(n__gt=0),
        "sesi": SesiOpname.objects.all()[:10],
    })


@perlu("opname")
def detail(request, opname_id):
    op = get_object_or_404(Opname.objects.exclude(status="draf"), pk=opname_id)
    if request.method == "POST":
        try:
            if request.POST.get("aksi") == "acc":
                svc.acc(request.user, op, request.POST.getlist("pilih"), hak=hak_pengguna(request.user))
                messages.success(request, "Opname di-ACC. Stok sudah disesuaikan dan tercatat di riwayat stok.")
            elif request.POST.get("aksi") == "tolak":
                svc.tolak(request.user, op, request.POST.get("alasan"), hak=hak_pengguna(request.user))
                messages.info(request, "Opname ditolak.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        return redirect("master:opname_detail", opname_id=op.id)
    baris = svc.rincian(op)
    beda = [b for b in baris if b.selisih]
    return render(request, "master/opname_detail.html", {
        "menu": "opname", "op": op, "baris": baris, "jumlah_beda": len(beda),
        "punya_stok": op.toko is None or op.toko.stok_di_kasir,
        "nilai_hitung": sum(b.nilai_hitung or 0 for b in baris),
        "nilai_selisih": sum(b.nilai_selisih or 0 for b in beda),
    })


# ---------------------------------------------------------------------------
# Sesi opname (opname besar, banyak tablet; 9 Nov 2026)
# ---------------------------------------------------------------------------


@perlu("opname")
def sesi_buka(request):
    from master.layanan import sesi_opname as svc_sesi
    if request.method == "POST":
        try:
            s = svc_sesi.buka(request.user, request.POST.get("nama"), hak=hak_pengguna(request.user),
                              alasan_wajib=request.POST.get("alasan_wajib") == "1")
            messages.success(request, f"Sesi {s.nama} dibuka. Sekarang buka tugas hitung untuk tiap orang.")
            return redirect("master:opname_sesi", sesi_id=s.id)
        except Tolak as tolak:
            messages.error(request, str(tolak))
    return redirect("master:opname")


@perlu("opname")
def sesi_detail(request, sesi_id):
    from datetime import timedelta

    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from master.layanan import sesi_opname as svc_sesi
    from master.layanan import tiket as svc_tiket
    from master.models import SesiOpname, Tiket
    sesi = get_object_or_404(SesiOpname, pk=sesi_id)
    hak = hak_pengguna(request.user)
    if request.method == "POST" and request.content_type == "application/json":
        return _sesi_json(request, sesi, hak)
    if request.method == "POST":
        p = request.POST
        aksi = p.get("aksi")
        try:
            if aksi in ("kembalikan", "batal_tugas"):
                t = get_object_or_404(Tiket, pk=p.get("tiket"), sesi=sesi)
                if aksi == "kembalikan":
                    svc_sesi.kembalikan(request.user, t, hak=hak)
                    messages.success(request, f"Tugas #{t.id} dibuka lagi 4 jam.")
                else:
                    svc_tiket.batalkan(request.user, t, hak=hak)
                    messages.info(request, f"Tugas #{t.id} dibatalkan; hitungannya tidak dipakai.")
            elif aksi == "hapus_hitungan":
                svc_sesi.hapus_hitungan(request.user, sesi, p.get("id"), hak=hak)
                messages.info(request, "Hitungan dibuang.")
            elif aksi == "anggap_nol":
                n = svc_sesi.anggap_nol(request.user, sesi, p.getlist("pilih"), hak=hak)
                messages.success(request, f"{n} barang dicatat 0.")
            elif aksi == "tugas_ulang":
                untuk = get_user_model().objects.filter(pk=p.get("untuk") or 0, is_active=True).first()
                tugas = svc_sesi.tugas_ulang(request.user, sesi, p.getlist("pilih"), untuk=untuk, hak=hak)
                messages.success(request, f"{len(tugas)} tugas hitung ulang dibuka untuk {untuk.first_name or untuk.username}.")
            elif aksi in ("nonaktif", "batal_nonaktif"):
                n = svc_sesi.tandai_nonaktif(request.user, sesi, p.getlist("pilih"), aksi == "nonaktif", hak=hak)
                messages.success(request, f"{n} barang " + ("akan dinonaktifkan saat ACC." if aksi == "nonaktif" else "tidak jadi dinonaktifkan."))
            elif aksi == "pilih_ulang":
                n = svc_sesi.pilih_ulang(request.user, sesi, p.getlist("pilih"), hak=hak)
                messages.success(request, f"{n} baris dipilih untuk dihitung ulang.")
            elif aksi == "batal_pilih":
                n = svc_sesi.batal_pilih(request.user, sesi, p.getlist("pilih"), hak=hak)
                messages.info(request, f"{n} pilihan dibuang.")
            elif aksi == "acak":
                n = svc_sesi.ambil_acak(request.user, sesi, p.get("n"), hak=hak)
                messages.success(request, f"{n} baris acak dipilih untuk dihitung ulang.")
            elif aksi == "rawan":
                pilih = svc_sesi.atur_rawan(request.user, p.getlist("kategori"), hak=hak)
                messages.success(request, f"Kategori rawan: {', '.join(pilih) or 'tidak ada'}.")
            elif aksi == "alasan_wajib":
                svc_sesi.atur_alasan_wajib(request.user, sesi, p.get("nyala") == "1", hak=hak)
                messages.success(request, "Alasan selisih " + ("wajib." if p.get("nyala") == "1" else "tidak wajib."))
            elif aksi == "acc":
                r = svc_sesi.acc(request.user, sesi, hak=hak)
                messages.success(request, f"{sesi.nama} di-ACC. Stok semua lokasi sudah disesuaikan.")
                for x in r.get("peringatan_harga") or []:
                    messages.warning(request, x)
            elif aksi == "batal":
                svc_sesi.batal(request.user, sesi, hak=hak)
                messages.info(request, "Sesi dibatalkan. Stok tidak berubah.")
        except Tolak as tolak:
            messages.error(request, str(tolak))
        tab = p.get("tab") or request.GET.get("tab") or ""
        return redirect(f"{request.path}?tab={tab}" if tab else request.path)
    tugas = list(Tiket.objects.filter(sesi=sesi).select_related("untuk").annotate(n=Count("hitungan")))
    from master.layanan.opname import lingkup_teks, lokasi_tugas
    for t in tugas:
        t.lingkup_teks, t.lokasi = lingkup_teks(t), lokasi_tugas(t)
        t.pic_sendiri = bool(svc_tiket.peringatan_pic(t))
    konteks = {"menu": "opname", "sesi": sesi, "tugas": tugas, "tab": request.GET.get("tab", "")}
    if sesi.status == "buka":
        r = svc_sesi.rekap(sesi)
        tab = konteks["tab"]
        from master.models import SesiHarga
        konteks["jumlah_draf"] = SesiHarga.objects.filter(sesi=sesi).count()
        if tab == "ulang":
            urut = sorted(r["baris"], key=lambda b: (-(b["nilai_awal"] or 0), b["v"].item_name))
            konteks.update(urut=urut if request.GET.get("semua") else urut[:300], urut_total=len(urut),
                           akurasi=svc_sesi.akurasi(r), kategori=svc_sesi.semua_kategori())
        elif tab == "harga":
            draf = {h.varian_id: h for h in SesiHarga.objects.filter(sesi=sesi)}
            per_v = {}
            for b in r["baris"]:
                x = per_v.setdefault(b["v"].id, {"v": b["v"], "nilai": 0, "hitung": 0})
                x["nilai"] += b["nilai"] or 0
                x["hitung"] += b["hitung"]
            from pos.models import Item
            harga = {(n, v): (j, f, m) for n, v, j, f, m in Item.objects.values_list(
                "item_name", "variant", "max_price", "min_price", "cost_amount")}
            daftar = sorted(per_v.values(), key=lambda x: (-x["nilai"], x["v"].item_name))
            for x in daftar:
                x["jual"], x["floor"], x["modal"] = harga.get((x["v"].item_name, x["v"].variant), (None, None, None))
                x["draf"] = draf.get(x["v"].id)
            konteks.update(harga=daftar if request.GET.get("semua") else daftar[:300], harga_total=len(daftar))
        elif tab == "selisih" and sesi.alasan_wajib:
            from master.models import AlasanSelisih
            konteks["pilihan_alasan"] = AlasanSelisih.PILIHAN
        admin = []
        for u in get_user_model().objects.filter(is_active=True).order_by("first_name", "username"):
            h = hak_pengguna(u)
            if "gudang" in h and "hitung" in svc_tiket.jenis_untuk(h) and not u.is_superuser:
                admin.append(u)
        konteks.update(r=r, admin=admin, jalan=sum(1 for t in tugas if t.bisa_diisi))
    else:
        konteks.update(h=svc_sesi.hasil_ringkas(sesi))
    return render(request, "master/opname_sesi.html", konteks)


def _sesi_json(request, sesi, hak):
    """Simpan per baris dari halaman sesi (hitung ulang, draf harga, alasan) tanpa memuat ulang halaman."""
    import json

    from django.http import JsonResponse

    from master.layanan import sesi_opname as svc_sesi
    from master.templatetags.master_tags import qty
    try:
        d = json.loads(request.body.decode("utf-8"))
        assert isinstance(d, dict)
    except (ValueError, UnicodeDecodeError, AssertionError):
        return JsonResponse({"ok": False, "pesan": "Isi kiriman tidak terbaca."}, status=400)
    try:
        aksi = d.get("aksi")
        if aksi == "isi_ulang":
            p = svc_sesi.isi_ulang(request.user, sesi, d.get("varian"), d.get("lokasi"), d.get("jumlah"),
                                   hak=hak, catatan=d.get("catatan") or "")
            return JsonResponse({"ok": True, "pesan": "Tersimpan.", "awal": qty(p.awal, p.varian),
                                 "ulang": qty(p.ulang, p.varian), "beda": p.ulang - (p.awal or 0),
                                 "beda_teks": qty(abs(p.ulang - (p.awal or 0)), p.varian)})
        if aksi == "harga":
            svc_sesi.simpan_harga(request.user, sesi, d.get("varian"), modal=d.get("modal") or "",
                                  jual=d.get("jual") or "", floor=d.get("floor") or "", hak=hak)
            return JsonResponse({"ok": True, "pesan": "Draf tersimpan."})
        if aksi == "alasan":
            svc_sesi.isi_alasan(request.user, sesi, d.get("varian"), d.get("lokasi"), d.get("alasan") or "",
                                hak=hak, catatan=d.get("catatan") or "")
            return JsonResponse({"ok": True, "pesan": "Tersimpan."})
    except Tolak as e:
        return JsonResponse({"ok": False, "pesan": str(e)}, status=400)
    return JsonResponse({"ok": False, "pesan": "Permintaan tidak dikenali."}, status=400)
