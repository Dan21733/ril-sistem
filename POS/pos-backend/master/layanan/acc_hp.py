"""Daftar "Perlu ACC" untuk HP pemilik (rancangan 3 Nov 2026).

Satu baris = satu kejadian/tugas. Nota, invoice, buka dus, dan tutup kas
diputuskan langsung di lembar Cek; tugas dan opname (banyak barang) dibuka
lewat Periksa. Tiap baris membawa tokonya supaya bisa disaring per toko dan
diberi label di beranda "Semua toko".
"""
import datetime

from django.urls import reverse
from django.utils import timezone

from master.akun.akses import hak_pengguna
from pos.kas import rp


def _toko_map():
    from master.models import Toko
    return {t.pk: t for t in Toko.objects.all()}


def daftar(user):
    """Semua yang menunggu keputusan akun ini, urut dari yang paling lama."""
    from master.layanan.toko import utama
    hak = hak_pengguna(user)
    toko = _toko_map()
    kasir = utama()
    hasil = []

    if "kelola_kasir" in hak:
        from master.layanan import nota_tinjau
        for d in nota_tinjau.daftar()[:30]:
            n = d["n"]
            floor = "floor" in (n.alasan or "").lower()
            hasil.append({
                "kunci": f"nota:{n.sync_id}", "jenis": "nota", "id": str(n.sync_id), "toko": kasir,
                "label": "Harga di bawah floor" if floor else "Nota ditahan",
                "judul": f"Nota {n.transaction_id or ''}" + (f" · {d['pembeli']}" if d.get("pembeli") else ""),
                "sub": d.get("kasir") or "kasir tidak dikenal", "nilai": d.get("total"),
                "arti": "Terima: nota masuk penjualan dan stok dipotong. Tolak: tetap tercatat, tidak dihitung.",
                "waktu": d.get("waktu_nota") or n.masuk, "putus": True, "detail": d, "url": "",
            })

    if "acc_piutang" in hak:
        from master.models import Piutang
        for p in Piutang.objects.filter(batal=False, acc_status="menunggu").select_related("toko")[:30]:
            hasil.append({
                "kunci": f"piutang:{p.id}", "jenis": "piutang", "id": p.id, "toko": p.toko,
                "label": "Invoice (ngutang)" if p.jenis_acc == "invoice" else "Titip barang",
                "judul": f"{p.nama} · {p.nomor}", "sub": f"dibayar {rp(p.dibayar)} · sisa {rp(p.sisa)}",
                "arti": "Terima: pelanggan boleh membawa barang dulu dan membayar sisanya nanti.",
                "nilai": p.total, "waktu": timezone.make_aware(datetime.datetime.combine(p.tanggal, datetime.time.min)),
                "putus": True, "url": reverse("hp:piutang_detail", args=[p.id]),
            })

    if "kas" in hak:
        from pos.models import KasSesi
        for s in KasSesi.objects.filter(status="ditutup", acc_status="menunggu").order_by("ditutup_waktu")[:30]:
            hasil.append({
                "kunci": f"kas:{s.pk}", "jenis": "kas", "id": str(s.pk), "toko": toko.get(s.toko_id) or kasir,
                "label": "Tutup kas",
                "judul": "Uang cocok" if not s.selisih else f"Uang {'kurang' if s.selisih < 0 else 'lebih'} {rp(abs(s.selisih))}",
                "sub": f"{s.ditutup_oleh} · dihitung {rp(s.dihitung)} · seharusnya {rp(s.seharusnya)}",
                "nilai": None, "selisih": s.selisih,
                "arti": "Terima: hitungan dibenarkan, selisih tercatat di laporan. Tolak: tetap tercatat dengan alasanmu.",
                "waktu": s.ditutup_waktu, "putus": True, "url": reverse("hp:kas_detail", args=[s.pk]),
            })

    if "ubah_stok" in hak:
        from master.models import BukaDus
        for b in BukaDus.objects.filter(status="menunggu").select_related("konversi__dari", "konversi__ke")[:30]:
            hasil.append({
                "kunci": f"dus:{b.id}", "jenis": "dus", "id": b.id, "toko": kasir, "label": "Buka dus",
                "judul": f"{b.jumlah} {b.konversi.dari.item_name} → {b.isi_total} {b.konversi.ke.item_name}",
                "sub": b.oleh + (f" · {b.catatan}" if b.catatan else ""), "nilai": None, "waktu": b.waktu,
                "arti": "Terima: stok dus berkurang dan stok isinya bertambah.",
                "putus": True, "url": "",
            })

    if "opname" in hak:
        from master.models import Opname
        for op in Opname.objects.filter(status="diajukan").select_related("toko", "lokasi").prefetch_related("baris")[:30]:
            baris = list(op.baris.all())
            beda = [b for b in baris if b.selisih]
            tempat = op.lokasi.nama if op.lokasi_id else "Toko (display)"
            hasil.append({
                "kunci": f"opname:{op.id}", "jenis": "opname", "id": op.id, "toko": op.toko or kasir,
                "label": f"Stok opname · {tempat}",
                "judul": f"{len(baris)} barang dihitung · {len(beda)} selisih",
                "sub": ", ".join(f"{b.item_name} {b.selisih:+d}" for b in beda[:3]) or "semua cocok",
                "oleh": op.dihitung_oleh or op.dibuat_oleh, "nilai": None,
                "waktu": op.diajukan_waktu or op.dibuat_waktu, "putus": False,
                "url": reverse("hp:opname", args=[op.id]),
            })

    if "kelola_lokasi" in hak:
        from master.models import Tiket
        for t in Tiket.objects.filter(status="dikirim").exclude(jenis="hitung").select_related("untuk")[:30]:
            hasil.append({
                "kunci": f"tugas:{t.id}", "jenis": "tugas", "id": t.id, "toko": kasir,
                "label": t.get_jenis_display(), "judul": t.tujuan,
                "sub": f"dari {t.untuk.first_name or t.untuk.username}", "nilai": None,
                "waktu": t.dikirim_waktu or t.dibuka_waktu, "putus": False,
                "url": reverse("hp:tugas_detail", args=[t.id]),
            })

    if "pembelian" in hak:
        from master.models import Pembelian
        for po in Pembelian.objects.filter(status="menunggu").select_related("supplier", "toko")[:30]:
            hasil.append({
                "kunci": f"po:{po.id}", "jenis": "po", "id": po.id, "toko": po.toko or kasir,
                "label": "Barang datang", "judul": f"{po.nomor} · {po.supplier.nama}", "sub": "periksa di master",
                "nilai": None, "waktu": po.dibuat_waktu, "putus": False,
                "url": reverse("master:pembelian_detail", args=[po.id]),
            })

    hasil.sort(key=lambda x: x["waktu"].timestamp() if x["waktu"] else 0)
    return hasil


def saring(items, toko):
    """Hanya milik toko itu (None = semua toko)."""
    if toko is None:
        return items
    return [x for x in items if x["toko"] is None or x["toko"].pk == toko.pk]


def per_toko(items):
    """{toko_id: jumlah}"""
    hitung = {}
    for x in items:
        k = x["toko"].pk if x["toko"] else None
        hitung[k] = hitung.get(k, 0) + 1
    return hitung
