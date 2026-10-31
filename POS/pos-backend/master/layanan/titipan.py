"""Barang titipan (konsinyasi): toko lain menitipkan barangnya di toko yang memegang kasir.

- Pemilik barang ada di ItemInfo.toko (kosong = toko utama/kasir).
- Syarat per varian (SyaratTitipan) berlaku mulai tanggal tertentu dan tidak ditimpa,
  supaya penjualan lama tetap dihitung dengan syarat lamanya.
- Floor barang titipan dihitung dari diskon maks: harga jual - diskon maks, dibulatkan ke atas.
- Setoran: penjualan barang titipan sejak setoran terakhir, dikurangi retur, dikurangi
  potongan toko, menjadi jumlah yang disetor ke pemilik barang.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from pos.models import Item

from master.layanan import inti as layanan
from master.layanan.inti import Tolak
from master.models import ItemInfo, Pengaturan, SetoranTitipan, SyaratTitipan, Toko, VarianInfo


# ---------------------------------------------------------------------------
# Pemilik barang
# ---------------------------------------------------------------------------

def daftar_toko():
    """Toko titipan yang aktif."""
    return list(Toko.objects.filter(aktif=True, titip_di__isnull=False))


def adalah_titipan(toko):
    return bool(toko and toko.titip_di_id)


def nama_milik(toko):
    """Nama barang milik toko ini. Toko titipan: barangnya sendiri. Toko lain: semua yang bukan titipan."""
    qs = ItemInfo.objects.all()
    return qs.filter(toko=toko) if adalah_titipan(toko) else qs.filter(toko__isnull=True)


def saring_barang(qs, toko, kolom="item_name"):
    """Saring queryset Item (atau apa pun yang punya item_name) menurut pemilik barang."""
    titipan = ItemInfo.objects.filter(toko__isnull=False)
    if adalah_titipan(toko):
        return qs.filter(**{f"{kolom}__in": titipan.filter(toko=toko).values("item_name")})
    return qs.exclude(**{f"{kolom}__in": titipan.values("item_name")})


def pemilik_per_nama(nama=None):
    """{item_name: Toko} untuk barang titipan saja."""
    qs = ItemInfo.objects.filter(toko__isnull=False).select_related("toko")
    if nama is not None:
        qs = qs.filter(item_name__in=nama)
    return {i.item_name: i.toko for i in qs}


# ---------------------------------------------------------------------------
# Angka
# ---------------------------------------------------------------------------

def satuan_bulat():
    return Pengaturan.ambil().pembulatan_diskon or 1


def bulat_atas(nilai, satuan=None):
    satuan = satuan or satuan_bulat()
    d = Decimal(nilai)
    return int((d / satuan).to_integral_value(rounding=ROUND_CEILING) * satuan)


def baca_persen(nilai, label):
    """'10', '10%', '2,5' -> Decimal; kosong -> None."""
    teks = str(nilai if nilai is not None else "").strip().replace("%", "").replace(",", ".")
    if teks == "":
        return None
    try:
        p = Decimal(teks)
    except InvalidOperation:
        raise Tolak(f"{label} harus angka persen, mis. 10 atau 2,5.") from None
    if p < 0 or p > 100:
        raise Tolak(f"{label} harus di antara 0 dan 100.")
    return p.quantize(Decimal("0.01"))


def floor_dari(jual, diskon_maks, satuan=None):
    """Floor = harga jual dikurangi diskon maks, dibulatkan ke atas, tidak lebih dari harga jual."""
    if jual is None or diskon_maks is None:
        return None
    return min(int(jual), bulat_atas(Decimal(jual) * (100 - Decimal(diskon_maks)) / 100, satuan))


def persen_teks(p):
    if p is None:
        return ""
    s = f"{Decimal(p).normalize():f}"
    return s.replace(".", ",")


# ---------------------------------------------------------------------------
# Syarat
# ---------------------------------------------------------------------------

def syarat_kini(varian_ids, tanggal=None):
    """{varian_id: SyaratTitipan} yang berlaku pada tanggal itu (default hari ini)."""
    tanggal = tanggal or timezone.localdate()
    hasil = {}
    for s in SyaratTitipan.objects.filter(varian_id__in=varian_ids, berlaku_mulai__lte=tanggal) \
            .order_by("varian_id", "-berlaku_mulai", "-id"):
        hasil.setdefault(s.varian_id, s)
    return hasil


class SyaratWaktu:
    """Potongan toko yang berlaku pada waktu penjualan, untuk banyak varian sekaligus."""

    def __init__(self, kunci):
        kunci = set(kunci)
        nama = {n for n, _ in kunci}
        info = {(v.item_name, v.variant): v.id for v in VarianInfo.objects.filter(item_name__in=nama)}
        self.id = {k: info[k] for k in kunci if k in info}
        self.riwayat = defaultdict(list)
        for s in SyaratTitipan.objects.filter(varian_id__in=self.id.values()).order_by("berlaku_mulai", "id"):
            self.riwayat[s.varian_id].append(s)

    def pada(self, kunci, tanggal):
        vid = self.id.get(kunci)
        hasil = None
        for s in self.riwayat.get(vid, ()):
            if s.berlaku_mulai <= tanggal:
                hasil = s
        return hasil


def segarkan_floor(pengguna, info, *, sumber="sistem", batch=None):
    """Tulis ulang floor varian titipan dari diskon maks yang berlaku hari ini. Mengembalikan floor baru."""
    s = syarat_kini([info.id]).get(info.id)
    if s is None or s.diskon_maks is None:
        return None
    baris = Item.objects.filter(item_name=info.item_name, variant=info.variant)
    lama = baris.values("min_price", "max_price").first()
    if not lama or lama["max_price"] is None:
        return None
    baru = floor_dari(lama["max_price"], s.diskon_maks)
    if baru != lama["min_price"]:
        baris.update(min_price=baru)
        layanan.catat(pengguna, sumber=sumber, item_name=info.item_name, variant=info.variant, kolom="min_price",
                      lama=lama["min_price"], baru=baru, batch=batch,
                      catatan=f"dari diskon maks {persen_teks(s.diskon_maks)}%")
    return baru


def floor_dikunci(info):
    """True kalau floor varian ini dihitung dari diskon maks (tidak boleh diketik manual)."""
    s = syarat_kini([info.id]).get(info.id)
    return s is not None and s.diskon_maks is not None


def simpan_syarat(pengguna, info, *, potongan, diskon_maks, berlaku_mulai=None, sumber="layar", batch=None):
    """Simpan syarat satu varian. Syarat dengan tanggal sama diganti; tanggal lain jadi riwayat baru."""
    pemilik = ItemInfo.objects.filter(item_name=info.item_name).select_related("toko").first()
    if not adalah_titipan(getattr(pemilik, "toko", None)):
        raise Tolak("Syarat titipan hanya untuk barang milik toko titipan.")
    potongan = baca_persen(potongan, "Potongan toko")
    diskon_maks = baca_persen(diskon_maks, "Diskon maks")
    hari_ini = timezone.localdate()
    berlaku_mulai = berlaku_mulai or hari_ini
    if berlaku_mulai > hari_ini:
        raise Tolak("Tanggal berlaku tidak boleh setelah hari ini.")
    kini = syarat_kini([info.id], berlaku_mulai).get(info.id)
    if kini and kini.potongan_toko == potongan and kini.diskon_maks == diskon_maks:
        return kini, False
    with transaction.atomic():
        s, _ = SyaratTitipan.objects.update_or_create(
            varian=info, berlaku_mulai=berlaku_mulai,
            defaults={"potongan_toko": potongan, "diskon_maks": diskon_maks,
                      "dibuat_oleh": getattr(pengguna, "username", "") or ""})
        lama = f"potongan {persen_teks(getattr(kini, 'potongan_toko', None)) or '-'}%, " \
               f"diskon maks {persen_teks(getattr(kini, 'diskon_maks', None)) or '-'}%" if kini else ""
        layanan.catat(pengguna, sumber=sumber, item_name=info.item_name, variant=info.variant, kolom="syarat_titipan",
                      lama=lama, baru=f"potongan {persen_teks(potongan) or '-'}%, diskon maks "
                                       f"{persen_teks(diskon_maks) or '-'}% mulai {berlaku_mulai:%d-%m-%Y}",
                      batch=batch)
        segarkan_floor(pengguna, info, sumber=sumber, batch=batch)
    return s, True


# ---------------------------------------------------------------------------
# Setoran
# ---------------------------------------------------------------------------

def setoran_terakhir(toko):
    return SetoranTitipan.objects.filter(toko=toko).order_by("-sampai", "-id").first()


def awal_periode(toko):
    """Hari pertama yang belum disetor: sehari setelah setoran terakhir, atau hari pertama ada penjualan."""
    akhir = setoran_terakhir(toko)
    if akhir:
        return akhir.sampai + timedelta(days=1)
    return None


def hitung(toko, dari, sampai):
    """Rincian setoran toko titipan untuk tanggal dari..sampai (termasuk keduanya).

    Penjualan: baris nota kasir dan nota tulis tangan untuk barang milik toko ini.
    Retur/void yang DICATAT dalam periode mengurangi (nilai = jumlah x harga di nota asal).
    Potongan toko memakai syarat yang berlaku pada tanggal penjualan asal.
    """
    from master.layanan import laporan as svc_laporan
    from pos.models import TransactionDetail

    zona = timezone.get_current_timezone()
    mulai = timezone.make_aware(datetime.combine(dari, time.min), zona)
    selesai = timezone.make_aware(datetime.combine(sampai + timedelta(days=1), time.min), zona)
    milik = set(ItemInfo.objects.filter(toko=toko).values_list("item_name", flat=True))
    utama = toko.titip_di
    _, baris = svc_laporan.ambil(mulai, selesai, toko=utama, potong_titipan=False)
    baris = [b for b in baris if b["item_name"] in milik]
    if utama:
        # Nota tulis tangan yang dicatat langsung atas nama toko titipan juga dihitung.
        _, manual = svc_laporan.ambil(mulai, selesai, toko=toko, sumber="manual", potong_titipan=False)
        baris += [b for b in manual if b["item_name"] in milik]
    kembali = svc_laporan.ambil_kembali(mulai, selesai, toko=utama) if utama else []

    # Harga di nota asal untuk barang yang diretur.
    asal_sync = {k["sync_id"] for k in kembali}
    harga_asal = {}
    for d in TransactionDetail.objects.filter(sync_id__in=asal_sync, item_name__in=milik).values(
            "sync_id", "item_name", "variant", "quantity", "subtotal"):
        if d["quantity"]:
            harga_asal[(d["sync_id"], d["item_name"], d["variant"])] = Decimal(d["subtotal"]) / d["quantity"]
    waktu_asal = {}
    if asal_sync:
        from pos.models import Transactions
        waktu_asal = dict(Transactions.objects.filter(sync_id__in=asal_sync).values_list("sync_id", "transaction_time"))

    kunci = {(b["item_name"], b["variant"]) for b in baris}
    kunci |= {(n, v) for k in kembali for n, v, _ in k["barang"] if n in milik}
    syarat = SyaratWaktu(kunci)

    per = {}

    def ambil_baris(nama, varian):
        k = (nama, varian)
        if k not in per:
            per[k] = {"item_name": nama, "variant": varian, "qty": 0, "jual": 0, "qty_retur": 0, "retur": 0,
                      "potongan": Decimal(0), "tanpa_syarat": False, "persen": set()}
        return per[k]

    for b in baris:
        r = ambil_baris(b["item_name"], b["variant"])
        tgl = timezone.localtime(b["nota"]["transaction_time"], zona).date()
        s = syarat.pada((b["item_name"], b["variant"]), tgl)
        r["qty"] += b["quantity"]
        r["jual"] += b["subtotal"]
        if s is None or s.potongan_toko is None:
            r["tanpa_syarat"] = True
        else:
            r["potongan"] += Decimal(b["subtotal"]) * s.potongan_toko / 100
            r["persen"].add(s.potongan_toko)

    for k in kembali:
        if k["jenis"] not in ("return", "void"):
            continue
        t = waktu_asal.get(k["sync_id"])
        tgl = timezone.localtime(t, zona).date() if t else dari
        for nama, varian, qty in k["barang"]:
            if nama not in milik or not qty:
                continue
            harga = harga_asal.get((k["sync_id"], nama, varian))
            if harga is None:
                continue
            nilai = int((harga * qty).to_integral_value(rounding=ROUND_HALF_UP))
            r = ambil_baris(nama, varian)
            r["qty_retur"] += qty
            r["retur"] += nilai
            s = syarat.pada((nama, varian), tgl)
            if s is not None and s.potongan_toko is not None:
                r["potongan"] -= Decimal(nilai) * s.potongan_toko / 100
                r["persen"].add(s.potongan_toko)

    rincian = []
    for r in layanan.urut_alami(list(per.values())):
        r["potongan"] = int(r["potongan"].to_integral_value(rounding=ROUND_HALF_UP))
        r["bersih"] = r["jual"] - r["retur"]
        r["disetor"] = r["bersih"] - r["potongan"]
        r["persen"] = ", ".join(persen_teks(p) + "%" for p in sorted(r["persen"]))
        rincian.append(r)
    total = {k: sum(r[k] for r in rincian) for k in ("qty", "jual", "qty_retur", "retur", "bersih", "potongan", "disetor")}
    return {"toko": toko, "dari": dari, "sampai": sampai, "rincian": rincian, "total": total,
            "tanpa_syarat": sum(1 for r in rincian if r["tanpa_syarat"])}


def tandai_disetor(pengguna, toko, dari, sampai, catatan=""):
    """Simpan setoran (salinan perhitungan). Periode tidak boleh tumpang tindih dengan setoran sebelumnya."""
    akhir = setoran_terakhir(toko)
    if akhir and dari <= akhir.sampai:
        raise Tolak(f"Periode ini sudah termasuk setoran sampai {akhir.sampai:%d-%m-%Y}.")
    if sampai < dari:
        raise Tolak("Tanggal akhir sebelum tanggal awal.")
    if sampai > timezone.localdate():
        raise Tolak("Tanggal akhir tidak boleh setelah hari ini.")
    h = hitung(toko, dari, sampai)
    if h["tanpa_syarat"]:
        raise Tolak(f"{h['tanpa_syarat']} barang belum punya potongan toko. Isi dulu supaya setoran benar.")
    with transaction.atomic():
        s = SetoranTitipan.objects.create(
            toko=toko, dari=dari, sampai=sampai, total_jual=h["total"]["jual"], total_retur=h["total"]["retur"],
            potongan=h["total"]["potongan"], disetor=h["total"]["disetor"],
            rincian=[{k: r[k] for k in ("item_name", "variant", "qty", "jual", "qty_retur", "retur", "potongan",
                                        "disetor", "persen")} for r in h["rincian"]],
            catatan=layanan.rapikan_teks(catatan)[:300], dibuat_oleh=getattr(pengguna, "username", "") or "")
        layanan.catat(pengguna, aksi="tambah", objek="setoran", kolom="setoran_titipan",
                      baru=f"{toko.nama} {dari:%d-%m-%Y} s.d. {sampai:%d-%m-%Y}: {s.disetor}")
    return s
