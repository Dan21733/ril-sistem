"""Cetak kertas rak gudang (17 Nov 2026): peta rak, label cell, kartu barang.

Yang dicetak memakai kode pendek (yang dilihat karyawan). Tidak ada stok, harga, atau modal di kertas.
- Peta rak: satu A4 per rak, cell urut dari tingkat atas ke bawah, barang per cell + baris kosong tulis tangan.
- Label cell: kode cell besar untuk ditempel di tiap kotak (8 per A4).
- Kartu barang: A4 dibagi 4, satu kartu per barang per cell, tabel kosong tanggal / masuk / keluar / paraf.
"""
from collections import defaultdict

from django.shortcuts import render

from master.akun.akses import perlu
from master.layanan import rak as svc_rak
from master.models import Lokasi, Rak, VarianInfo


def _nama_barang(v):
    return v.item_name + (f" / {v.variant}" if v.variant and v.variant != "N/A" else "")


@perlu("kelola_rak")
def cetak(request):
    q = request.GET
    jenis = q.get("jenis") if q.get("jenis") in ("peta", "label", "kartu") else "peta"
    lokasi = Lokasi.objects.filter(pk=q.get("lokasi") or 0).first()
    rak_qs = Rak.objects.select_related("tempat", "ruang")
    rak_qs = rak_qs.filter(tempat=lokasi) if lokasi else rak_qs.filter(tempat__isnull=True)
    if (q.get("ruang") or "").isdigit():
        rak_qs = rak_qs.filter(ruang_id=int(q["ruang"]))
    pilih = q.get("rak") or ""
    if pilih:
        rak_qs = rak_qs.filter(induk=pilih) | rak_qs.filter(kode=pilih, induk="")
    semua = sorted(rak_qs, key=svc_rak.urut)

    # Barang per cell: rak utama + rak tambahan (aktif saja).
    isi = defaultdict(list)
    kode = [r.kode for r in semua]
    for v in VarianInfo.objects.filter(rak_id__in=kode, aktif=True, sampah__isnull=True).order_by("item_name", "variant"):
        isi[v.rak_id].append(v)
    for v in VarianInfo.objects.filter(tempat_rak__rak_id__in=kode, aktif=True, sampah__isnull=True).distinct():
        for t in v.tempat_rak.all():
            if t.rak_id in kode and v not in isi[t.rak_id]:
                isi[t.rak_id].append(v)

    # Satu kelompok per rak (induk) / area, cell urut tingkat atas -> bawah, kolom kiri -> kanan (seperti rak aslinya).
    kelompok, per = [], {}
    for r in semua:
        k = r.induk or r.kode
        if k not in per:
            per[k] = {"kunci": k, "pendek": svc_rak.pendek_dasar(r), "nama": r.nama, "jenis": r.jenis,
                      "ruang": r.ruang.nama if r.ruang_id else "", "lokasi": r.tempat.nama if r.tempat_id else "Tanpa lokasi",
                      "cell": []}
            kelompok.append(per[k])
        per[k]["cell"].append({"kode": svc_rak.kode_pendek(r), "baris": r.baris or 0, "kolom": r.kolom or 0,
                               "barang": [_nama_barang(v) for v in isi.get(r.kode, [])]})
    for g in kelompok:
        g["cell"].sort(key=lambda c: (-c["baris"], c["kolom"]))
        g["n_barang"] = sum(len(c["barang"]) for c in g["cell"])

    kartu = [{"barang": b, "cell": c["kode"], "rak": g["pendek"], "lokasi": g["lokasi"], "ruang": g["ruang"]}
             for g in kelompok for c in g["cell"] for b in c["barang"]]
    label = [{"kode": c["kode"], "rak": g["pendek"], "nama": g["nama"], "lokasi": g["lokasi"]}
             for g in kelompok for c in g["cell"]]
    from urllib.parse import urlencode
    dasar = urlencode({k: q.get(k) for k in ("lokasi", "ruang", "rak") if q.get(k)})
    return render(request, "master/rak_cetak.html", {
        "jenis": jenis, "dasar": dasar, "pilih": pilih, "lokasi": lokasi, "kelompok": kelompok, "kartu": kartu, "label": label,
        "baris_kosong": range(3), "baris_kartu": range(14),
        "halaman_kartu": [kartu[i:i + 4] for i in range(0, len(kartu), 4)],
        "halaman_label": [label[i:i + 8] for i in range(0, len(label), 8)],
    })
