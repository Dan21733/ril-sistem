"""Toko: daftar toko dan penentuan toko yang sedang dipakai di sebuah halaman."""
from django.db import transaction

from master.layanan import inti as layanan
from master.layanan.inti import Tolak, rapikan_teks
from master.models import Toko
from master.layanan.pembelian import baca_tanggal


def aktif():
    return list(Toko.objects.filter(aktif=True))


def boleh_untuk(user):
    """Toko aktif yang boleh dibuka akun ini. Daftar kosong di hak akun = semua toko."""
    semua = aktif()
    if getattr(user, "is_superuser", False):
        return semua
    from master.models import HakAkun
    izin = HakAkun.objects.filter(user=user).values_list("toko", flat=True).first() or []
    izin = {int(x) for x in izin if str(x).isdigit()}
    return [t for t in semua if t.pk in izin] if izin else semua


def toko_aktif(request):
    """Toko yang sedang dikerjakan di master (dipilih di menu samping, diingat per akun)."""
    if hasattr(request, "_toko_aktif"):
        return request._toko_aktif
    boleh = boleh_untuk(request.user)
    per_id = {t.pk: t for t in boleh}
    pilih = per_id.get(request.session.get("master_toko") or 0)
    if pilih is None:
        from master.models import PreferensiAkun
        pref = PreferensiAkun.objects.filter(user=request.user).values_list("toko_id", flat=True).first()
        pilih = per_id.get(pref or 0)
    if pilih is None:
        kasir = utama()
        pilih = per_id.get(getattr(kasir, "pk", 0)) or (boleh[0] if boleh else None)
    request._toko_aktif = pilih
    return pilih


def toko_gudang(request):
    """Toko yang sedang dikerjakan di aplikasi gudang (?toko=, diingat di sesi), hanya yang diizinkan."""
    if hasattr(request, "_toko_gudang"):
        return request._toko_gudang
    boleh = {t.pk: t for t in boleh_untuk(request.user)}
    t = None
    if boleh:
        nilai = request.GET.get("toko", "")
        if nilai.isdigit() and int(nilai) in boleh:
            request.session["gudang_toko"] = int(nilai)
        t = boleh.get(request.session.get("gudang_toko") or 0)
        if t is None:
            kasir = utama()
            t = boleh.get(getattr(kasir, "pk", 0)) or next(iter(boleh.values()))
    request._toko_gudang = t
    return t


def saring(qs, request, kolom="toko"):
    """Hanya data toko yang sedang aktif. Data lama tanpa toko dianggap milik toko yang memegang stok kasir."""
    from django.db.models import Q
    t = toko_aktif(request)
    if t is None:
        return qs.none()
    syarat = Q(**{kolom: t})
    if t == utama():
        syarat |= Q(**{f"{kolom}__isnull": True})
    return qs.filter(syarat)


def pilih_toko(request, toko_id):
    from master.models import PreferensiAkun
    t = next((x for x in boleh_untuk(request.user) if str(x.pk) == str(toko_id)), None)
    if t is None:
        raise Tolak("Toko ini tidak bisa dibuka dari akun ini.")
    request.session["master_toko"] = t.pk
    PreferensiAkun.objects.update_or_create(user=request.user, defaults={"toko": t})
    request._toko_aktif = t
    return t


def utama():
    """Toko yang stoknya ada di kasir; kalau belum ada, toko aktif pertama."""
    return Toko.objects.filter(stok_di_kasir=True).first() or Toko.objects.filter(aktif=True).first()


def ambil(nilai, *, wajib=True):
    """Toko dari isian form. Kosong = toko utama (supaya tampilan satu toko tidak perlu memilih)."""
    if nilai in (None, ""):
        t = utama()
    else:
        t = Toko.objects.filter(pk=nilai if str(nilai).isdigit() else 0, aktif=True).first()
    if t is None and wajib:
        raise Tolak("Pilih toko.")
    return t


def simpan(pengguna, data, toko=None):
    kode = rapikan_teks(data.get("kode")).upper()[:10]
    nama = rapikan_teks(data.get("nama"))[:100]
    if not kode or not nama:
        raise Tolak("Kode dan nama toko wajib diisi.")
    if Toko.objects.filter(kode__iexact=kode).exclude(pk=getattr(toko, "pk", None)).exists():
        raise Tolak(f"Kode {kode} sudah dipakai toko lain.")
    pkp = data.get("pkp") == "1"
    pkp_mulai = baca_tanggal(data.get("pkp_mulai"), label="Tanggal mulai PKP") if pkp else None
    if pkp and pkp_mulai is None:
        raise Tolak("Isi tanggal mulai PKP.")
    titip_di = None
    if data.get("titip_di"):
        titip_di = Toko.objects.filter(pk=data["titip_di"] if str(data["titip_di"]).isdigit() else 0,
                                       aktif=True, titip_di__isnull=True).first()
        if titip_di is None or (toko is not None and titip_di.pk == toko.pk):
            raise Tolak("Pilih toko tempat barang titipan dijual.")
        if toko is not None and (toko.stok_di_kasir or toko.titipan.exists()):
            raise Tolak(f"{toko.nama} memegang kasir atau punya toko titipan, jadi tidak bisa dijadikan titipan.")
    elif toko is not None and toko.titip_di_id and toko.barang_milik.exists():
        raise Tolak(f"{toko.nama} masih punya {toko.barang_milik.count()} barang titipan. Pindahkan dulu barangnya.")
    isi = {"kode": kode, "nama": nama, "titip_di": titip_di, "atas_nama": rapikan_teks(data.get("atas_nama"))[:150],
           "alamat": (data.get("alamat") or "").strip()[:500], "telepon": rapikan_teks(data.get("telepon"))[:50],
           "pkp": pkp, "pkp_mulai": pkp_mulai}
    with transaction.atomic():
        if toko is None:
            toko = Toko.objects.create(urutan=Toko.objects.count() + 1, **isi)
            layanan.catat(pengguna, aksi="tambah", objek="toko", kolom="toko", baru=f"{kode} {nama}")
        else:
            lama = f"{toko.kode} {toko.nama}, {'PKP sejak ' + str(toko.pkp_mulai) if toko.pkp else 'belum PKP'}" \
                   + (f", titipan di {toko.titip_di.nama}" if toko.titip_di_id else "")
            for k, v in isi.items():
                setattr(toko, k, v)
            toko.save()
            baru = f"{toko.kode} {toko.nama}, {'PKP sejak ' + str(toko.pkp_mulai) if toko.pkp else 'belum PKP'}" \
                   + (f", titipan di {toko.titip_di.nama}" if toko.titip_di_id else "")
            if lama != baru:
                layanan.catat(pengguna, objek="toko", kolom="toko", lama=lama, baru=baru)
    return toko


def jadikan_stok_kasir(pengguna, toko):
    """Pindahkan tanda 'stok ada di kasir' ke toko ini (hanya satu toko yang boleh)."""
    with transaction.atomic():
        lama = Toko.objects.filter(stok_di_kasir=True).exclude(pk=toko.pk).first()
        Toko.objects.exclude(pk=toko.pk).update(stok_di_kasir=False)
        toko.stok_di_kasir = True
        toko.save(update_fields=["stok_di_kasir"])
        layanan.catat(pengguna, objek="toko", kolom="stok_di_kasir", lama=getattr(lama, "nama", ""), baru=toko.nama)


def ubah_aktif(pengguna, toko):
    if toko.aktif and toko.stok_di_kasir:
        raise Tolak("Toko yang stoknya ada di kasir tidak bisa dinonaktifkan.")
    if toko.aktif and Toko.objects.filter(aktif=True).count() <= 1:
        raise Tolak("Harus ada minimal satu toko aktif.")
    toko.aktif = not toko.aktif
    toko.save(update_fields=["aktif"])
    layanan.catat(pengguna, objek="toko", kolom="aktif", baru=f"{toko.nama}: {'aktif' if toko.aktif else 'nonaktif'}")


PILIHAN_PEMBULATAN = (1, 100, 500, 1000)


def simpan_pembulatan(pengguna, nilai):
    """Kelipatan pembulatan ke atas untuk diskon persen di kasir dan floor barang titipan."""
    from master.models import Pengaturan
    try:
        baru = int(str(nilai))
    except (TypeError, ValueError):
        baru = None
    if baru not in PILIHAN_PEMBULATAN:
        raise Tolak("Pilih pembulatan yang tersedia.")
    atur = Pengaturan.ambil()
    if atur.pembulatan_diskon != baru:
        layanan.catat(pengguna, objek="pengaturan", kolom="pembulatan_diskon", lama=atur.pembulatan_diskon, baru=baru)
        atur.pembulatan_diskon = baru
        atur.save(update_fields=["pembulatan_diskon"])
    # Kasir membaca nilai yang sama lewat pengaturan POS (dikirim di /api/health/).
    from pos import pengaturan
    pengaturan.simpan(pembulatan_diskon=baru)


def simpan_dp(pengguna, persen, hari):
    """DP minimal bawaan (persen dari harga barang) dan lama jatuh tempo nota belum lunas."""
    from master.models import Pengaturan
    try:
        persen, hari = int(str(persen).replace("%", "").strip()), int(str(hari).strip())
    except (TypeError, ValueError):
        raise Tolak("DP minimal dan jatuh tempo harus angka.")
    if not 1 <= persen <= 100:
        raise Tolak("DP minimal antara 1 dan 100%.")
    if not 1 <= hari <= 365:
        raise Tolak("Jatuh tempo antara 1 dan 365 hari.")
    atur = Pengaturan.ambil()
    for kolom, baru in (("dp_minimal_persen", persen), ("jatuh_tempo_hari", hari)):
        if getattr(atur, kolom) != baru:
            layanan.catat(pengguna, objek="pengaturan", kolom=kolom, lama=getattr(atur, kolom), baru=baru)
            setattr(atur, kolom, baru)
    atur.save(update_fields=["dp_minimal_persen", "jatuh_tempo_hari"])
