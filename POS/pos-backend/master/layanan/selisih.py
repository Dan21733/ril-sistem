"""Laporan pola selisih stok: dari opname yang di-ACC, hitung acak, dan barang diambil yang jumlahnya beda.

Tujuannya menemukan pola, bukan satu kejadian: selisih kecil yang terus berulang di
lokasi yang sama, pada orang yang sama, atau pada kategori yang sama.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta

from django.utils import timezone

from pos.models import Item

from master.models import HitungAcakBaris, OpnameBaris, PermintaanAmbil


def _rentang(dari, sampai):
    tz = timezone.get_current_timezone()
    return (timezone.make_aware(datetime.combine(dari, time.min), tz),
            timezone.make_aware(datetime.combine(sampai + timedelta(days=1), time.min), tz))


def kejadian(dari, sampai):
    """Daftar kejadian selisih (bukan nol) dalam rentang tanggal."""
    awal, akhir = _rentang(dari, sampai)
    hasil = []
    for b in (OpnameBaris.objects.filter(opname__status="disetujui", diterapkan=True,
                                         waktu_hitung__gte=awal, waktu_hitung__lt=akhir)
              .select_related("opname__lokasi", "varian")):
        if b.selisih:
            hasil.append({"sumber": "Opname", "waktu": b.waktu_hitung, "varian": b.varian,
                          "lokasi": b.opname.lokasi.nama if b.opname.lokasi else "Toko (display)",
                          "orang": b.opname.dibuat_oleh, "selisih": b.selisih, "ref": f"Opname #{b.opname_id}"})
    for b in (HitungAcakBaris.objects.filter(hitung_acak__status="selesai", hitung_acak__selesai_waktu__gte=awal,
                                             hitung_acak__selesai_waktu__lt=akhir)
              .select_related("hitung_acak__lokasi", "varian")):
        if b.selisih:
            hasil.append({"sumber": "Cek stok acak", "waktu": b.hitung_acak.selesai_waktu, "varian": b.varian,
                          "lokasi": b.hitung_acak.lokasi.nama, "orang": b.hitung_acak.oleh, "selisih": b.selisih,
                          "ref": f"Cek stok acak #{b.hitung_acak_id}"})
    for p in (PermintaanAmbil.objects.filter(status="diambil", diambil_waktu__gte=awal, diambil_waktu__lt=akhir)
              .select_related("diambil_lokasi", "varian")):
        if p.selisih:
            # Diambil lebih banyak dari permintaan = barang keluar gudang tanpa nota: dihitung kurang.
            hasil.append({"sumber": "Ambil barang", "waktu": p.diambil_waktu, "varian": p.varian,
                          "lokasi": p.diambil_lokasi.nama, "orang": p.diambil_oleh, "selisih": -p.selisih,
                          "ref": p.referensi})
    return hasil


def ringkas(dari, sampai):
    data = kejadian(dari, sampai)
    nama = {k["varian"].item_name for k in data}
    item = {(i["item_name"], i["variant"]): i for i in Item.objects.filter(item_name__in=nama)
            .values("item_name", "variant", "category", "cost_amount")}
    for k in data:
        v = k["varian"]
        i = item.get((v.item_name, v.variant), {})
        modal = v.modal_rata if v.modal_rata is not None else i.get("cost_amount")
        k["kategori"] = i.get("category") or "(sudah dihapus)"
        k["nilai"] = k["selisih"] * modal if modal is not None else None

    def kelompok(kunci):
        g = defaultdict(lambda: {"kejadian": 0, "kurang": 0, "lebih": 0, "nilai_kurang": 0, "nilai_lebih": 0})
        for k in data:
            x = g[kunci(k)]
            x["kejadian"] += 1
            if k["selisih"] < 0:
                x["kurang"] += -k["selisih"]
                x["nilai_kurang"] += -(k["nilai"] or 0)
            else:
                x["lebih"] += k["selisih"]
                x["nilai_lebih"] += k["nilai"] or 0
        return sorted(({"nama": n, **x} for n, x in g.items()), key=lambda r: (-r["nilai_kurang"], -r["kejadian"]))

    return {
        "jumlah": len(data),
        "lokasi": kelompok(lambda k: k["lokasi"]),
        "orang": kelompok(lambda k: f'{k["orang"]} ({k["sumber"].lower()})'),
        "kategori": kelompok(lambda k: k["kategori"]),
        "barang": kelompok(lambda k: f'{k["varian"].item_name} / {k["varian"].variant}')[:30],
        "kejadian": sorted(data, key=lambda k: k["waktu"], reverse=True)[:200],
    }
