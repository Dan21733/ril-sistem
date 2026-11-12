"""Nota diproses: pesanan, titipan, COD, invoice (rancangan DP 31 Okt 2026, aturan baru 12 Nov 2026).

Aturan pemilik (SOP, 12 Nov 2026):
- Dibawa sekarang: wajib lunas. Kurang dari lunas hanya invoice (pelanggan tempo, dalam batas, ACC per nota).
- Diantar (COD): bayar 0 sampai lunas di toko, sisanya ditagih di tempat. Wajib nama dan No. HP. Tidak boleh
  jadi utang: kalau pembeli tidak membayar penuh, barang dibawa pulang (retur) dan No. HP-nya masuk daftar hitam
  COD. Nomor di daftar hitam tidak boleh COD lagi.
- Titip di toko: wajib lunas dan ACC pemilik per nota.
- Pesan dulu: DP minimal (bawaan 50% harga barang; barang tertentu lebih tinggi) atau lunas. Stok baru berkurang
  saat barang diserahkan.
- Nota kasir yang melanggar aturan di atas (mis. tablet memakai aturan lama) tetap tercatat tetapi wajib ACC
  pemilik (jenis_acc "sop"); ditolak = kasir membatalkan nota.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_CEILING

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from master.layanan import inti as layanan
from master.layanan.inti import Tolak, baca_angka, rapikan_teks
from master.models import DaftarHitam, ItemInfo, Pelanggan, PembayaranPiutang, Pengaturan, Piutang

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
# Daftar hitam COD
# ---------------------------------------------------------------------------

def telepon_baku(telepon):
    """Nomor HP untuk dicocokkan: hanya angka, awalan 62 jadi 0. Kosong kalau terlalu pendek."""
    angka = "".join(c for c in str(telepon or "") if c.isdigit())
    if angka.startswith("62"):
        angka = "0" + angka[2:]
    return angka if len(angka) >= 8 else ""


def di_daftar_hitam(telepon):
    baku = telepon_baku(telepon)
    return bool(baku) and DaftarHitam.objects.filter(telepon=baku, aktif=True).exists()


def masuk_daftar_hitam(p, alasan):
    """Catat No. HP pembeli nota ini ke daftar hitam COD (sekali saja; yang pernah dibuka diaktifkan lagi)."""
    baku = telepon_baku(p.telepon)
    if not baku:
        return None
    d, baru = DaftarHitam.objects.get_or_create(telepon=baku, defaults={
        "nama": p.nama[:150], "alasan": alasan[:300], "nomor_nota": p.nomor[:40]})
    if not baru and not d.aktif:
        d.aktif, d.nama, d.alasan, d.nomor_nota = True, p.nama[:150], alasan[:300], p.nomor[:40]
        d.dibuka_oleh, d.dibuka_waktu = "", None
        d.save()
    if baru or d.aktif:
        layanan.catat(None, aksi="tambah", objek="daftar hitam COD", kolom="telepon", baru=f"{p.nama} {baku}",
                      catatan=f"{p.nomor}: {alasan}"[:300])
    return d


def buka_daftar_hitam(pengguna, d):
    if not d.aktif:
        raise Tolak("Nomor ini sudah dibuka.")
    d.aktif, d.dibuka_oleh, d.dibuka_waktu = False, getattr(pengguna, "username", "") or "", timezone.now()
    d.save(update_fields=["aktif", "dibuka_oleh", "dibuka_waktu"])
    layanan.catat(pengguna, aksi="ubah", objek="daftar hitam COD", kolom="aktif", lama="ya", baru="dibuka",
                  catatan=f"{d.nama} {d.telepon}")
    return d


# ---------------------------------------------------------------------------
# Aturan SOP per jenis nota (dipakai nota tulis tangan master dan impor nota kasir)
# ---------------------------------------------------------------------------

def _cek_tempo(pelanggan, kurang):
    """'' kalau boleh invoice, atau alasan kenapa tidak."""
    if not (pelanggan and pelanggan.boleh_tempo):
        return "bukan pelanggan tempo"
    if pelanggan.batas_piutang is not None and sisa_berjalan(pelanggan) + kurang > pelanggan.batas_piutang:
        return (f"melewati batas piutang {pelanggan.nama} ({rp(pelanggan.batas_piutang)}; "
                f"berjalan {rp(sisa_berjalan(pelanggan))})")
    return ""


def nilai_sop(*, status, dibayar, total, minimal, pelanggan, nama, telepon):
    """Jenis nota, ACC yang dibutuhkan, dan pelanggaran SOP (teks; kosong = sesuai SOP).

    Dipanggil hanya untuk nota yang memang diproses (belum lunas, atau barang belum dibawa).
    """
    lunas = dibayar >= total
    kurang = max(0, total - dibayar)
    jenis, acc, langgar = "", "", ""
    if status == "ambil":
        jenis, acc = "titipan", "titip"
        if not lunas:
            langgar = f"Titipan wajib lunas (kurang {rp(kurang)})."
    elif status == "pesan":
        jenis = "pesanan"
        if not lunas and dibayar < minimal:
            alasan = _cek_tempo(pelanggan, kurang)
            if alasan:
                langgar = f"DP pesanan minimal {rp(minimal)} ({alasan})."
            else:
                jenis, acc = "invoice", "invoice"
    elif status == "antar":
        jenis = "cod"
        if not rapikan_teks(nama) or not telepon_baku(telepon):
            langgar = "COD wajib ada nama dan No. HP pembeli."
        elif di_daftar_hitam(telepon):
            langgar = "No. HP ini ada di daftar hitam COD (pernah menolak bayar saat barang diantar)."
    else:
        alasan = _cek_tempo(pelanggan, kurang)
        if alasan:
            jenis, langgar = "invoice", f"Barang dibawa wajib lunas; kurang {rp(kurang)} hanya untuk invoice ({alasan})."
        else:
            jenis, acc = "invoice", "invoice"
    return {"jenis": jenis, "jenis_acc": "sop" if langgar else acc, "langgar": langgar}


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

    minimal = minimal_dp(baris)
    sop = nilai_sop(status=status, dibayar=dibayar, total=total, minimal=minimal, pelanggan=pelanggan,
                    nama=nama, telepon=telepon)
    if sop["langgar"]:
        raise Tolak(sop["langgar"])
    jenis_acc = sop["jenis_acc"]

    jatuh_tempo = None
    if dibayar < total:
        from master.layanan.pembelian import baca_tanggal
        jatuh_tempo = tanggal if sop["jenis"] == "cod" else (
            baca_tanggal(data.get("jatuh_tempo"), label="Jatuh tempo")
            or tanggal + timedelta(days=Pengaturan.ambil().jatuh_tempo_hari))
        if jatuh_tempo < tanggal:
            raise Tolak("Jatuh tempo tidak boleh sebelum tanggal nota.")

    otomatis = bool(jenis_acc) and "acc_piutang" in hak
    return {"status_barang": status, "dibayar": dibayar, "nama": nama, "telepon": telepon, "pelanggan": pelanggan,
            "jatuh_tempo": jatuh_tempo, "jenis": sop["jenis"], "jenis_acc": jenis_acc, "minimal": minimal,
            "acc_status": ("disetujui" if otomatis else "menunggu") if jenis_acc else "",
            "acc_oleh": getattr(pengguna, "username", "") if otomatis else "",
            "stok_dikurangi": status != "pesan"}


def buat(pengguna, r, *, toko, nomor, tanggal, total, cara, sumber="master", nota_manual=None, kasir_sync_id=None):
    p = Piutang.objects.create(
        nota_manual=nota_manual, kasir_sync_id=kasir_sync_id, toko=toko, nomor=nomor, tanggal=tanggal,
        pelanggan=r["pelanggan"], nama=r["nama"], telepon=r["telepon"], total=total, dibayar=0,
        jatuh_tempo=r["jatuh_tempo"], status_barang=r["status_barang"], stok_dikurangi=r["stok_dikurangi"],
        jenis=r.get("jenis", ""),
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
        if cara not in CARA_BAYAR or cara == "campur":
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
        elif not p.stok_dikurangi and p.kasir_sync_id:
            _kurangi_stok_kasir(p, pengguna)
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

TAB = [("pesanan", "Pesanan"), ("cod", "COD"), ("titipan", "Titipan"), ("invoice", "Invoice"), ("acc", "Menunggu ACC"),
       ("aturan_lama", "Aturan lama"), ("selesai", "Selesai")]


def _q_belum_lunas():
    from django.db.models import Q
    # dibayar - uang_kembali < total - potongan_retur
    return Q(dibayar__lt=F("total") - F("potongan_retur") + F("uang_kembali"))


def _belum_lunas(qs):
    return qs.filter(_q_belum_lunas())


def _berjalan(qs):
    """Belum selesai: belum lunas, atau barangnya masih menunggu diambil/diserahkan. Yang ditolak tidak."""
    from django.db.models import Q
    tertunda = Q(status_barang__in=("ambil", "pesan"), barang_selesai__isnull=True)
    return qs.exclude(acc_status="ditolak").filter(_q_belum_lunas() | tertunda)


def daftar(tab, toko=None):
    qs = Piutang.objects.filter(batal=False).select_related("toko", "pelanggan")
    if toko is not None:
        qs = qs.filter(toko=toko)
    if tab == "belum_lunas":
        return _belum_lunas(qs.exclude(acc_status="ditolak")).order_by("jatuh_tempo", "tanggal")
    if tab in ("ambil", "pesan"):
        return qs.filter(status_barang=tab, barang_selesai__isnull=True).exclude(acc_status="ditolak").order_by("tanggal")
    if tab in ("pesanan", "cod", "titipan", "invoice"):
        return _berjalan(qs.filter(jenis=tab)).order_by("tanggal", "id")
    if tab == "acc":
        return qs.filter(acc_status="menunggu").order_by("dibuat")
    if tab == "aturan_lama":
        return _berjalan(qs.filter(aturan_lama=True)).order_by("tanggal", "id")
    from django.db.models import Q
    tertunda = Q(status_barang__in=("ambil", "pesan"), barang_selesai__isnull=True)
    return qs.filter(Q(acc_status="ditolak") | ~(_q_belum_lunas() | tertunda)).order_by("-tanggal", "-id")


def hitungan(toko=None):
    return {k: daftar(k, toko).count() for k, _ in TAB if k != "selesai"}


def tab_untuk(p):
    """Tab tempat nota ini tampil (untuk tautan kembali)."""
    return p.jenis or "selesai"


# Halaman HP (12 Nov 2026): tiga halaman terpisah, bukan satu halaman bertab.
HALAMAN_HP = {
    "pesanan": ("Pesanan", ("pesanan", "cod"), "Pesanan (DP) dan COD yang belum selesai."),
    "titipan": ("Titipan", ("titipan",), "Barang pelanggan yang dititip di toko (sudah lunas)."),
    "invoice": ("Piutang & invoice", ("invoice",), "Pelanggan tempo yang belum lunas."),
}


def halaman_untuk(p):
    return {"cod": "pesanan"}.get(p.jenis, p.jenis if p.jenis in HALAMAN_HP else "pesanan")


def daftar_halaman(halaman, toko=None):
    qs = Piutang.objects.filter(batal=False, jenis__in=HALAMAN_HP[halaman][1]).select_related("toko", "pelanggan")
    if toko is not None:
        qs = qs.filter(toko=toko)
    return _berjalan(qs).order_by("tanggal", "id")


def hitungan_halaman(toko=None):
    return {k: daftar_halaman(k, toko).count() for k in HALAMAN_HP}


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


# ---------------------------------------------------------------------------
# Nota DP dari tablet kasir (fase 2, 2 Nov 2026)
# ---------------------------------------------------------------------------
# Server kasir tidak memuat aplikasi master. Ia hanya mencatat nota DP
# (`nota_piutang`) dan aksi kasir (`aksi_piutang`); master mengimpor dan
# menerapkannya di sini, dipanggil penjadwal tiap menit dan saat halaman
# piutang dibuka.


class _Kasir:
    """Pengguna semu untuk log: nama kasir dari PIN, bukan akun master."""
    pk = None

    def __init__(self, nama):
        self.username = nama


def _nama_kasir(worker_id):
    from pos.models import Worker
    nama = Worker.objects.filter(pk=worker_id).values_list("name", flat=True).first() if worker_id else None
    return f"{nama or worker_id or 'kasir'} (kasir)"


def _kurangi_stok_kasir(p, pengguna):
    """Barang pesanan nota kasir diserahkan: stok dipotong sekarang, dicatat di riwayat stok kasir."""
    from pos.models import Item, MutasiKasir, TransactionDetail
    for d in TransactionDetail.objects.filter(sync_id=p.kasir_sync_id).order_by("item_name", "variant"):
        item = Item.objects.select_for_update().filter(item_name=d.item_name, variant=d.variant).first()
        if item is None:
            continue
        sebelum = item.in_stock or 0
        item.in_stock = sebelum - d.quantity
        item.save(update_fields=["in_stock"])
        MutasiKasir.objects.create(sync_id=p.kasir_sync_id, transaction_id=p.nomor if p.nomor[:1] == "T" else None,
                                   worker_id="", item_name=d.item_name, variant=d.variant, jumlah=-d.quantity,
                                   stok_sebelum=sebelum, stok_sesudah=item.in_stock)


def barang(p):
    """Baris barang nota piutang (nota tulis tangan atau nota kasir), bentuk sama untuk template."""
    if p.nota_manual_id:
        return list(p.nota_manual.baris.all())
    if p.kasir_sync_id:
        from pos.models import TransactionDetail
        return [{"item_name": d.item_name, "variant": d.variant, "jumlah": d.quantity, "harga": d.unit_price,
                 "subtotal": d.subtotal} for d in TransactionDetail.objects.filter(sync_id=p.kasir_sync_id)
                .order_by("item_name", "variant")]
    return []


def _impor_satu(n):
    from pos.models import TransactionDetail, Transactions
    from master.models import Toko
    t = Transactions.objects.filter(sync_id=n.sync_id).first()
    if t is None:
        return None
    baris = [(d.item_name, d.subtotal) for d in TransactionDetail.objects.filter(sync_id=n.sync_id)]
    total = int(t.total_amount)
    dibayar = min(int(n.dibayar), total)
    minimal = minimal_dp(baris)
    pelanggan = Pelanggan.objects.filter(pk=n.pelanggan_id).first() if n.pelanggan_id else None
    if pelanggan is None and t.phone_no:
        pelanggan = cari_pelanggan("", t.phone_no)
    nama = (t.cust_name or (pelanggan.nama if pelanggan else "") or "")[:150]
    telepon = (t.phone_no or (pelanggan.telepon if pelanggan else ""))[:30]
    sop = nilai_sop(status=n.status_barang, dibayar=dibayar, total=total, minimal=minimal, pelanggan=pelanggan,
                    nama=nama, telepon=telepon)
    jenis_acc, catatan = sop["jenis_acc"], sop["langgar"]
    tanggal = timezone.localdate(t.transaction_time)
    jatuh_tempo = None
    if dibayar < total:
        jatuh_tempo = tanggal if sop["jenis"] == "cod" else (
            n.jatuh_tempo or tanggal + timedelta(days=Pengaturan.ambil().jatuh_tempo_hari))
    from pos.models import NotaToko
    milik = NotaToko.objects.filter(sync_id=n.sync_id).values_list("toko_id", flat=True).first()
    toko = (Toko.objects.filter(pk=milik).first() if milik else None) \
        or Toko.objects.filter(stok_di_kasir=True).first() or Toko.objects.order_by("id").first()
    pengguna = _Kasir(_nama_kasir(n.worker_id))
    r = {"status_barang": n.status_barang, "dibayar": dibayar, "pelanggan": pelanggan,
         "nama": nama or "Tanpa nama", "telepon": telepon, "jenis": sop["jenis"],
         "jatuh_tempo": jatuh_tempo, "jenis_acc": jenis_acc, "minimal": minimal,
         "acc_status": "menunggu" if jenis_acc else "", "acc_oleh": "", "stok_dikurangi": not n.stok_ditunda}
    p = buat(pengguna, r, toko=toko, nomor=t.transaction_id or f"kasir {str(n.sync_id)[:8]}", tanggal=tanggal,
             total=total, cara=n.cara if n.cara in CARA_BAYAR else "lainnya", sumber="kasir", kasir_sync_id=n.sync_id)
    if catatan:
        Piutang.objects.filter(pk=p.pk).update(acc_catatan=catatan[:300])
    return p


def _terapkan_aksi(a):
    from pos.models import AksiPiutang
    p = None
    if a.piutang_id:
        p = Piutang.objects.filter(pk=a.piutang_id).first()
    elif a.sync_id:
        p = Piutang.objects.filter(kasir_sync_id=a.sync_id).first()
        if p is None:
            return False  # nota kasir belum diimpor: coba lagi putaran berikutnya
    pengguna = _Kasir(_nama_kasir(a.worker_id))
    try:
        if p is None:
            raise Tolak("Piutang tidak ditemukan.")
        if a.jenis == "bayar":
            bayar(pengguna, p, jumlah=a.jumlah, cara=a.cara, catatan=a.catatan, sumber="kasir")
        else:
            serahkan(pengguna, p)
        status, pesan = "diterapkan", ""
    except Tolak as e:
        status, pesan = "ditolak", str(e)[:300]
    AksiPiutang.objects.filter(pk=a.pk, status="baru").update(status=status, pesan=pesan)
    return True


def _terapkan_retur():
    """Barang yang dikembalikan pembeli (event retur kasir) mengurangi tagihan piutang nota itu.

    COD yang barangnya dibawa pulang = pembeli menolak bayar penuh: No. HP-nya masuk daftar hitam COD.
    Dihitung ulang dari semua event retur nota itu, jadi aman diulang.
    """
    from collections import defaultdict
    from pos.models import RefundEvent, RefundEventDetail, TransactionDetail
    p_kasir = Piutang.objects.filter(kasir_sync_id__isnull=False, batal=False)
    events = list(RefundEvent.objects.filter(event_type="return", sync_id__in=p_kasir.values("kasir_sync_id")))
    if not events:
        return 0
    per_nota = defaultdict(list)
    for e in events:
        per_nota[e.sync_id].append(e)
    n = 0
    for p in p_kasir.filter(kasir_sync_id__in=list(per_nota)):
        harga = {(d.item_name, d.variant): (d.subtotal / d.quantity if d.quantity else 0)
                 for d in TransactionDetail.objects.filter(sync_id=p.kasir_sync_id)}
        ids = [e.refund_id for e in per_nota[p.kasir_sync_id]]
        potongan = round(sum(harga.get((d.item_name, d.variant), 0) * d.quantity
                             for d in RefundEventDetail.objects.filter(refund_id__in=ids)))
        kembali = sum(int(e.refund_amount or 0) for e in per_nota[p.kasir_sync_id])
        if (potongan, kembali) == (p.potongan_retur, p.uang_kembali):
            continue
        Piutang.objects.filter(pk=p.pk).update(potongan_retur=potongan, uang_kembali=kembali)
        layanan.catat(None, objek="piutang", kolom="retur", lama=p.potongan_retur, baru=potongan,
                      catatan=f"{p.nomor} {p.nama}: barang dikembalikan {rp(potongan)}, uang kembali {rp(kembali)}")
        if p.jenis == "cod" and potongan > 0:
            masuk_daftar_hitam(p, "COD: tidak membayar penuh, barang dibawa pulang.")
        n += 1
    return n


def sinkron_kasir():
    """Impor nota DP kasir baru, batalkan piutang yang notanya di-void, terapkan aksi kasir.
    Aman dipanggil berulang. Tanpa tabel kasir (database uji master lama): tidak melakukan apa-apa."""
    from django.db import DatabaseError
    try:
        from pos.models import AksiPiutang, NotaPiutang, RefundEvent
        sudah = Piutang.objects.filter(kasir_sync_id__isnull=False).values("kasir_sync_id")
        baru = list(NotaPiutang.objects.exclude(sync_id__in=sudah).order_by("masuk")[:200])
    except DatabaseError:
        return 0
    n = 0
    for nota in baru:
        with transaction.atomic():
            if not Piutang.objects.filter(kasir_sync_id=nota.sync_id).exists() and _impor_satu(nota):
                n += 1
    void = RefundEvent.objects.filter(event_type="void").values("sync_id")
    Piutang.objects.filter(kasir_sync_id__in=void, batal=False).update(batal=True)
    _terapkan_retur()
    for a in AksiPiutang.objects.filter(status="baru").order_by("masuk")[:200]:
        _terapkan_aksi(a)
    return n
