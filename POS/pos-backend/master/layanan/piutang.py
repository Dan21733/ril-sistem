"""DP, piutang pelanggan, barang ambil nanti / pesan dulu (rancangan 31 Okt 2026).

Aturan pemilik:
- Semua nota yang belum lunas wajib DP minimal (bawaan 50% harga barang; barang tertentu lebih tinggi).
- Di bawah minimal (invoice / ngutang) hanya untuk pelanggan di daftar tempo, dalam batas piutangnya,
  dan tetap wajib ACC pemilik per nota.
- Titip barang (sudah lunas, barang belum dibawa) juga wajib ACC.
- Barang "pesan dulu": stok baru berkurang saat barang diserahkan.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_CEILING

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from master.layanan import inti as layanan
from master.layanan.inti import Tolak, baca_angka, rapikan_teks
from master.models import ItemInfo, Pelanggan, PembayaranPiutang, Pengaturan, Piutang

STATUS_BARANG = dict(Piutang.BARANG)
CARA_BAYAR = dict(PembayaranPiutang.CARA)


def rp(n):
    return "Rp" + f"{int(n or 0):,}".replace(",", ".")


# ---------------------------------------------------------------------------
# Minimal DP
# ---------------------------------------------------------------------------

def dp_bawaan():
    return Pengaturan.ambil().dp_minimal_persen


def persen_dp(nama_barang):
    """{item_name: persen minimal DP} untuk nama-nama itu (bawaan kalau tidak diatur)."""
    bawaan = dp_bawaan()
    khusus = dict(ItemInfo.objects.filter(item_name__in=set(nama_barang), min_dp_persen__isnull=False)
                  .values_list("item_name", "min_dp_persen"))
    return {n: khusus.get(n, bawaan) for n in set(nama_barang)}


def simpan_min_dp(pengguna, item_info, nilai):
    teks = str(nilai or "").replace("%", "").strip()
    if teks == "":
        baru = None
    else:
        try:
            baru = int(teks)
        except ValueError:
            raise Tolak("Minimal DP harus angka persen, mis. 70.")
        if not 1 <= baru <= 100:
            raise Tolak("Minimal DP antara 1 dan 100%.")
    if item_info.min_dp_persen == baru:
        return False
    layanan.catat(pengguna, objek="barang", item_name=item_info.item_name, kolom="minimal DP",
                  lama=item_info.min_dp_persen, baru=baru)
    item_info.min_dp_persen = baru
    item_info.save(update_fields=["min_dp_persen"])
    return True


def minimal_dp(baris):
    """baris: [(item_name, subtotal)]. Minimal DP = jumlah subtotal x persen barangnya, dibulatkan ke atas."""
    persen = persen_dp([n for n, _ in baris])
    total = Decimal(0)
    for nama, subtotal in baris:
        total += Decimal(subtotal) * persen[nama] / 100
    return int(total.to_integral_value(rounding=ROUND_CEILING))


# ---------------------------------------------------------------------------
# Pelanggan
# ---------------------------------------------------------------------------

def sisa_berjalan(pelanggan, kecuali=None):
    qs = Piutang.objects.filter(pelanggan=pelanggan, batal=False).exclude(acc_status="ditolak")
    if kecuali is not None:
        qs = qs.exclude(pk=kecuali.pk)
    total = qs.aggregate(t=Sum(F("total") - F("dibayar")))["t"] or 0
    return max(0, total)


def simpan_pelanggan(pengguna, data, pelanggan=None):
    nama = rapikan_teks(data.get("nama"))[:150]
    if not nama:
        raise Tolak("Nama pelanggan wajib diisi.")
    telepon = rapikan_teks(data.get("telepon"))[:30]
    boleh = data.get("boleh_tempo") in ("1", "on", True)
    if boleh and not telepon:
        raise Tolak("Pelanggan tempo wajib punya No. HP.")
    if telepon and Pelanggan.objects.filter(telepon=telepon).exclude(pk=getattr(pelanggan, "pk", None)).exists():
        raise Tolak(f"No. HP {telepon} sudah dipakai pelanggan lain.")
    isi = {"nama": nama, "telepon": telepon, "boleh_tempo": boleh,
           "batas_piutang": baca_angka(data.get("batas_piutang"), True),
           "catatan": rapikan_teks(data.get("catatan"))[:300], "aktif": data.get("aktif", "1") in ("1", "on", True)}
    if pelanggan is None:
        pelanggan = Pelanggan.objects.create(**isi)
        layanan.catat(pengguna, aksi="tambah", objek="pelanggan", kolom="pelanggan",
                      baru=f"{nama} {telepon}{' tempo' if boleh else ''}")
    else:
        lama = f"{pelanggan.nama} {pelanggan.telepon} tempo={pelanggan.boleh_tempo} batas={pelanggan.batas_piutang}"
        for k, v in isi.items():
            setattr(pelanggan, k, v)
        pelanggan.save()
        layanan.catat(pengguna, objek="pelanggan", kolom="pelanggan", lama=lama,
                      baru=f"{nama} {telepon} tempo={boleh} batas={isi['batas_piutang']}")
    return pelanggan


def cari_pelanggan(nama="", telepon="", pelanggan_id=None):
    if pelanggan_id and str(pelanggan_id).isdigit():
        return Pelanggan.objects.filter(pk=int(pelanggan_id), aktif=True).first()
    telepon = rapikan_teks(telepon)
    if telepon:
        return Pelanggan.objects.filter(telepon=telepon, aktif=True).first()
    return None


# ---------------------------------------------------------------------------
# Rencana piutang untuk nota baru
# ---------------------------------------------------------------------------

def rencana(pengguna, *, hak, tanggal, total, baris, data):
    """Periksa isian bayar/barang nota baru. Hasil None (nota biasa: lunas & dibawa) atau dict untuk dibuat.

    baris: [(item_name, subtotal)] barang di nota. data: isian form (status_barang, dibayar,
    pembeli/nama, telepon, pelanggan, jatuh_tempo).
    """
    status = data.get("status_barang") or "bawa"
    if status not in STATUS_BARANG:
        raise Tolak("Pilih status barang.")
    teks_bayar = str(data.get("dibayar") if data.get("dibayar") is not None else "").strip()
    dibayar = total if teks_bayar == "" else baca_angka(teks_bayar, False)
    if dibayar < 0 or dibayar > total:
        raise Tolak(f"Jumlah dibayar harus di antara 0 dan total nota ({rp(total)}).")
    if dibayar >= total and status in ("bawa", "antar"):
        return None

    nama = rapikan_teks(data.get("nama") or data.get("pembeli"))[:150]
    telepon = rapikan_teks(data.get("telepon"))[:30]
    pelanggan = cari_pelanggan(nama, telepon, data.get("pelanggan"))
    if pelanggan:
        nama = nama or pelanggan.nama
        telepon = telepon or pelanggan.telepon
    if not nama:
        raise Tolak("Nota yang belum lunas atau barangnya belum dibawa wajib ada nama pembeli.")

    jenis_acc = ""
    minimal = minimal_dp(baris)
    if dibayar < total and dibayar < minimal:
        if not (pelanggan and pelanggan.boleh_tempo):
            raise Tolak(f"DP minimal {rp(minimal)}. Kurang dari itu hanya untuk pelanggan tempo (invoice), "
                        "dengan ACC pemilik.")
        if pelanggan.batas_piutang is not None and sisa_berjalan(pelanggan) + (total - dibayar) > pelanggan.batas_piutang:
            raise Tolak(f"Melewati batas piutang {pelanggan.nama} ({rp(pelanggan.batas_piutang)}); "
                        f"piutang berjalan {rp(sisa_berjalan(pelanggan))}.")
        jenis_acc = "invoice"
    elif dibayar >= total and status == "ambil":
        jenis_acc = "titip"

    jatuh_tempo = None
    if dibayar < total:
        from master.layanan.pembelian import baca_tanggal
        jatuh_tempo = baca_tanggal(data.get("jatuh_tempo"), label="Jatuh tempo") or \
            tanggal + timedelta(days=Pengaturan.ambil().jatuh_tempo_hari)
        if jatuh_tempo < tanggal:
            raise Tolak("Jatuh tempo tidak boleh sebelum tanggal nota.")

    otomatis = bool(jenis_acc) and "acc_piutang" in hak
    return {"status_barang": status, "dibayar": dibayar, "nama": nama, "telepon": telepon, "pelanggan": pelanggan,
            "jatuh_tempo": jatuh_tempo, "jenis_acc": jenis_acc, "minimal": minimal,
            "acc_status": ("disetujui" if otomatis else "menunggu") if jenis_acc else "",
            "acc_oleh": getattr(pengguna, "username", "") if otomatis else "",
            "stok_dikurangi": status != "pesan"}


def buat(pengguna, r, *, toko, nomor, tanggal, total, cara, sumber="master", nota_manual=None, kasir_sync_id=None):
    p = Piutang.objects.create(
        nota_manual=nota_manual, kasir_sync_id=kasir_sync_id, toko=toko, nomor=nomor, tanggal=tanggal,
        pelanggan=r["pelanggan"], nama=r["nama"], telepon=r["telepon"], total=total, dibayar=0,
        jatuh_tempo=r["jatuh_tempo"], status_barang=r["status_barang"], stok_dikurangi=r["stok_dikurangi"],
        jenis_acc=r["jenis_acc"], acc_status=r["acc_status"], acc_oleh=r["acc_oleh"],
        acc_waktu=timezone.now() if r["acc_status"] == "disetujui" else None,
        dibuat_oleh=getattr(pengguna, "username", "") or "")
    if r["dibayar"] > 0:
        waktu = timezone.make_aware(datetime.combine(tanggal, time(12, 0)), timezone.get_current_timezone()) \
            if tanggal != timezone.localdate() else timezone.now()
        _catat_bayar(pengguna, p, r["dibayar"], cara, "DP" if r["dibayar"] < total else "Lunas saat nota",
                     sumber, waktu)
    layanan.catat(pengguna, aksi="tambah", objek="piutang", kolom="piutang",
                  baru=f"{nomor} {p.nama}: total {total}, dibayar {r['dibayar']}, barang {p.get_status_barang_display()}"
                       + (f", {p.get_jenis_acc_display()} ({p.get_acc_status_display()})" if p.jenis_acc else ""))
    return p


# ---------------------------------------------------------------------------
# Pembayaran, barang, ACC
# ---------------------------------------------------------------------------

def _catat_bayar(pengguna, p, jumlah, cara, catatan, sumber, waktu=None):
    PembayaranPiutang.objects.create(piutang=p, waktu=waktu or timezone.now(), jumlah=jumlah, cara=cara,
                                     catatan=catatan[:300], oleh=getattr(pengguna, "username", "") or "", sumber=sumber)
    Piutang.objects.filter(pk=p.pk).update(dibayar=F("dibayar") + jumlah)
    p.refresh_from_db(fields=["dibayar"])


def bayar(pengguna, p, *, jumlah, cara="cash", catatan="", sumber="master"):
    with transaction.atomic():
        p = Piutang.objects.select_for_update().get(pk=p.pk)
        if p.batal:
            raise Tolak("Nota ini sudah dibatalkan.")
        if p.acc_status == "ditolak":
            raise Tolak("Nota ini ditolak pemilik.")
        jumlah = baca_angka(jumlah, False)
        if jumlah <= 0:
            raise Tolak("Isi jumlah yang dibayar.")
        if jumlah > p.sisa:
            raise Tolak(f"Lebih dari sisa tagihan ({rp(p.sisa)}).")
        if cara not in CARA_BAYAR:
            raise Tolak("Pilih cara bayar.")
        _catat_bayar(pengguna, p, jumlah, cara, rapikan_teks(catatan) or ("Pelunasan" if jumlah == p.sisa else "Cicilan"),
                     sumber)
        layanan.catat(pengguna, objek="piutang", kolom="pembayaran", lama=p.dibayar - jumlah, baru=p.dibayar,
                      catatan=f"{p.nomor} {p.nama}: {rp(jumlah)} {CARA_BAYAR[cara]}")
    return p


def serahkan(pengguna, p):
    """Barang 'ambil nanti' sudah diambil / barang pesanan sudah diserahkan."""
    with transaction.atomic():
        p = Piutang.objects.select_for_update(of=("self",)).select_related("nota_manual").get(pk=p.pk)
        if p.batal:
            raise Tolak("Nota ini sudah dibatalkan.")
        if not p.barang_tertunda:
            raise Tolak("Barang nota ini tidak sedang menunggu diambil.")
        if p.acc_status == "menunggu":
            raise Tolak("Nota ini masih menunggu ACC pemilik.")
        if p.acc_status == "ditolak":
            raise Tolak("Nota ini ditolak pemilik.")
        if not p.lunas and not (p.jenis_acc == "invoice" and p.acc_status == "disetujui"):
            raise Tolak(f"Masih kurang {rp(p.sisa)}. Catat pelunasannya dulu.")
        if not p.stok_dikurangi and p.nota_manual_id:
            from master.layanan import ambil
            from master.layanan.nota_manual import _ubah_stok
            n = p.nota_manual
            if n.toko.stok_di_kasir:
                for b in n.baris.select_related("varian", "lokasi"):
                    _ubah_stok(pengguna, b.varian, -b.jumlah, f"{n.nomor} pesanan diserahkan", b.lokasi, ubah_lokasi=False)
                    if b.lokasi_id is not None:
                        ambil.buat(b.varian, b.lokasi, b.jumlah, sumber="nota_manual", referensi=n.nomor, nota_manual=n)
            p.stok_dikurangi = True
        p.barang_selesai, p.barang_selesai_oleh = timezone.now(), getattr(pengguna, "username", "") or ""
        p.save(update_fields=["barang_selesai", "barang_selesai_oleh", "stok_dikurangi"])
        layanan.catat(pengguna, objek="piutang", kolom="barang diambil", baru=f"{p.nomor} {p.nama}")
    return p


def acc(pengguna, p, *, setuju, catatan=""):
    with transaction.atomic():
        p = Piutang.objects.select_for_update().get(pk=p.pk)
        if p.acc_status != "menunggu":
            raise Tolak("Nota ini tidak sedang menunggu ACC.")
        if not setuju and not rapikan_teks(catatan):
            raise Tolak("Isi alasan menolak.")
        p.acc_status = "disetujui" if setuju else "ditolak"
        p.acc_oleh, p.acc_waktu, p.acc_catatan = getattr(pengguna, "username", ""), timezone.now(), rapikan_teks(catatan)[:300]
        p.save(update_fields=["acc_status", "acc_oleh", "acc_waktu", "acc_catatan"])
        layanan.catat(pengguna, objek="piutang", kolom="acc", baru=f"{p.nomor} {p.get_jenis_acc_display()}: "
                      f"{p.get_acc_status_display()}", catatan=p.acc_catatan)
    return p


def batalkan_untuk_nota(n):
    Piutang.objects.filter(nota_manual=n).update(batal=True)


# ---------------------------------------------------------------------------
# Daftar
# ---------------------------------------------------------------------------

TAB = [("belum_lunas", "Belum lunas"), ("ambil", "Ambil nanti"), ("pesan", "Pesanan"), ("acc", "Menunggu ACC"),
       ("selesai", "Selesai")]


def daftar(tab, toko=None):
    qs = Piutang.objects.filter(batal=False).select_related("toko", "pelanggan")
    if toko is not None:
        qs = qs.filter(toko=toko)
    if tab == "belum_lunas":
        return qs.filter(dibayar__lt=F("total")).exclude(acc_status="ditolak").order_by("jatuh_tempo", "tanggal")
    if tab in ("ambil", "pesan"):
        return qs.filter(status_barang=tab, barang_selesai__isnull=True).exclude(acc_status="ditolak").order_by("tanggal")
    if tab == "acc":
        return qs.filter(acc_status="menunggu").order_by("dibuat")
    selesai = qs.filter(dibayar__gte=F("total")).exclude(status_barang__in=("ambil", "pesan"), barang_selesai__isnull=True)
    return (selesai | qs.filter(acc_status="ditolak")).distinct().order_by("-tanggal", "-id")


def hitungan(toko=None):
    return {k: daftar(k, toko).count() for k, _ in TAB if k != "selesai"}


def ringkas(toko=None):
    qs = daftar("belum_lunas", toko)
    hari_ini = timezone.localdate()
    return {"jumlah": qs.count(), "sisa": sum(p.sisa for p in qs),
            "lewat": sum(1 for p in qs if p.jatuh_tempo and p.jatuh_tempo < hari_ini)}


def lewat_tempo():
    hari_ini = timezone.localdate()
    return [p for p in daftar("belum_lunas") if p.jatuh_tempo and p.jatuh_tempo < hari_ini]


def teks_tagihan(p):
    baris = [f"*Tagihan {p.toko.nama}*", f"Nota {p.nomor} · {p.tanggal:%d-%m-%Y}", f"Kepada: {p.nama}", ""]
    if p.nota_manual_id:
        for b in p.nota_manual.baris.all():
            varian = "" if b.variant in ("", "N/A") else f" / {b.variant}"
            baris.append(f"- {b.item_name}{varian} x{b.jumlah} = {rp(b.subtotal)}")
        if p.nota_manual.ongkir:
            baris.append(f"- Ongkir {rp(p.nota_manual.ongkir)}")
        baris.append("")
    baris += [f"Total: {rp(p.total)}", f"Sudah dibayar: {rp(p.dibayar)}", f"*Sisa: {rp(p.sisa)}*"]
    if p.jatuh_tempo:
        baris.append(f"Jatuh tempo: {p.jatuh_tempo:%d-%m-%Y}")
    return "\n".join(baris)


def wa_url(p):
    from urllib.parse import quote
    telepon = "".join(c for c in (p.telepon or "") if c.isdigit())
    if telepon.startswith("0"):
        telepon = "62" + telepon[1:]
    return f"https://wa.me/{telepon}?text={quote(teks_tagihan(p))}"
