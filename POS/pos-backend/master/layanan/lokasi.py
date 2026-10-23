"""Stok per lokasi untuk toko yang memegang stok kasir.

Total stok tetap di kasir (`item.in_stock`). Gudang menyimpan jumlahnya di
StokLokasi; stok Toko (display) = total - jumlah semua gudang. Toko yang minus
berarti barang terjual dari rak tanpa pernah resmi diambil dari gudang.
"""
from collections import defaultdict

from django.db import transaction
from django.db.models import Count, Sum

from pos.models import Item

from master.layanan.inti import Tolak, baca_angka, catat, rapikan_teks
from master.models import Lokasi, MutasiLokasi, Rak, StokLokasi, VarianInfo

HAK = "kelola_lokasi"


def lokasi_toko():
    toko = Lokasi.objects.filter(jenis="toko").first()
    if toko is None:
        toko = Lokasi.objects.create(kode="TOKO", nama="Toko (display)", jenis="toko", urutan=0)
    return toko


def gudang_aktif():
    return list(Lokasi.objects.filter(jenis="gudang", aktif=True))


def pilihan():
    """Lokasi aktif untuk dipilih di formulir, Toko lebih dulu."""
    lokasi_toko()
    return list(Lokasi.objects.filter(aktif=True).order_by("-jenis", "urutan", "id"))  # 'toko' > 'gudang'


def baca(nilai):
    """Lokasi dari id di formulir. Kosong atau tidak dikenal = Toko (display)."""
    if nilai not in (None, "") and str(nilai).isdigit():
        lokasi = Lokasi.objects.filter(pk=int(nilai)).first()
        if lokasi is not None:
            if not lokasi.aktif:
                raise Tolak(f"Lokasi {lokasi.nama} tidak aktif.")
            return lokasi
    return lokasi_toko()


def ubah_gudang(info, lokasi, delta):
    """Tambah/kurangi jumlah di satu gudang. Untuk Toko tidak ada yang disimpan.

    Dipanggil di dalam transaksi alur yang mengubah total stok (barang datang,
    opname, buka dus), supaya total dan pembagiannya selalu berubah bersama.
    """
    if not delta or lokasi is None or lokasi.jenis == "toko":
        return
    s = StokLokasi.objects.select_for_update().filter(varian=info, lokasi=lokasi).first()
    if s is None:
        StokLokasi.objects.create(varian=info, lokasi=lokasi, jumlah=delta)
    else:
        s.jumlah += delta
        s.save(update_fields=["jumlah"])


def stok_di(info, lokasi, total=None):
    """Stok sistem satu varian di satu lokasi saat ini."""
    if lokasi is None or lokasi.jenis == "toko":
        return rincian(info, total=total)[lokasi_toko().id]
    return StokLokasi.objects.filter(varian=info, lokasi=lokasi).values_list("jumlah", flat=True).first() or 0


def jumlah_gudang(varian_ids):
    """{varian_id: {lokasi_id: jumlah}} untuk gudang yang jumlahnya bukan nol."""
    hasil = defaultdict(dict)
    for v, l, n in StokLokasi.objects.filter(varian_id__in=list(varian_ids)).exclude(jumlah=0).values_list(
            "varian_id", "lokasi_id", "jumlah"):
        hasil[v][l] = n
    return hasil


def rincian(info, total=None):
    """Jumlah per lokasi untuk satu varian: {lokasi_id: jumlah}, termasuk toko."""
    if total is None:
        total = Item.objects.filter(item_name=info.item_name, variant=info.variant).values_list(
            "in_stock", flat=True).first() or 0
    from master.layanan.ambil import dipesan
    gudang = jumlah_gudang([info.id]).get(info.id, {})
    hasil = dict(gudang)
    # Barang yang sudah terjual tapi belum diambil dari gudang masih tercatat di gudang;
    # di Toko ia tidak pernah ada, jadi ditambahkan kembali.
    hasil[lokasi_toko().id] = total - sum(gudang.values()) + dipesan([info.id]).get(info.id, 0)
    return hasil


def pindah(pengguna, info, *, dari, ke, jumlah, catatan="", hak):
    if HAK not in hak:
        raise Tolak("Akun ini tidak boleh memindah stok antar lokasi.")
    jumlah = baca_angka(jumlah, boleh_kosong=False)
    if jumlah <= 0:
        raise Tolak("Jumlah harus lebih dari nol.")
    if dari.pk == ke.pk:
        raise Tolak("Lokasi asal dan tujuan sama.")
    if not ke.aktif:
        raise Tolak(f"Lokasi {ke.nama} tidak aktif.")
    with transaction.atomic():
        baris = Item.objects.select_for_update().filter(item_name=info.item_name, variant=info.variant).first()
        if baris is None:
            raise Tolak("Barang ini tidak ada di tabel kasir.")
        stok = {s.lokasi_id: s for s in StokLokasi.objects.select_for_update().filter(varian=info)}
        di_gudang = sum(s.jumlah for s in stok.values())
        tersedia = (baris.in_stock or 0) - di_gudang if dari.jenis == "toko" else (
            stok[dari.pk].jumlah if dari.pk in stok else 0)
        if jumlah > tersedia:
            raise Tolak(f"Stok di {dari.nama} hanya {max(tersedia, 0)}.")
        for lokasi, delta in ((dari, -jumlah), (ke, jumlah)):
            if lokasi.jenis == "toko":
                continue
            s = stok.get(lokasi.pk) or StokLokasi.objects.create(varian=info, lokasi=lokasi, jumlah=0)
            s.jumlah += delta
            s.save(update_fields=["jumlah"])
        MutasiLokasi.objects.create(
            pengguna=pengguna if getattr(pengguna, "pk", None) else None,
            nama_pengguna=getattr(pengguna, "username", "") or "sistem",
            varian=info, item_name=info.item_name, variant=info.variant,
            dari=dari, ke=ke, jumlah=jumlah, catatan=rapikan_teks(catatan),
        )
        catat(pengguna, item_name=info.item_name, variant=info.variant, kolom="lokasi",
              lama=f"{dari.nama}", baru=f"{jumlah} ke {ke.nama}", catatan=catatan)


# ---------------------------------------------------------------------------
# Daftar lokasi
# ---------------------------------------------------------------------------


def simpan_lokasi(pengguna, *, hak, id=None, kode, nama, keterangan="", urutan=0):
    if HAK not in hak:
        raise Tolak("Akun ini tidak boleh mengelola lokasi.")
    kode = rapikan_teks(kode).upper()
    nama = rapikan_teks(nama)
    if not kode or not nama:
        raise Tolak("Kode dan nama lokasi wajib diisi.")
    if len(kode) > 20:
        raise Tolak("Kode lokasi maksimal 20 karakter.")
    lain = Lokasi.objects.filter(kode=kode)
    if id:
        lain = lain.exclude(pk=id)
    if lain.exists():
        raise Tolak(f"Kode {kode} sudah dipakai.")
    urutan = baca_angka(urutan, boleh_kosong=True) or 0
    if id:
        lokasi = Lokasi.objects.get(pk=id)
        lama = f"{lokasi.kode} - {lokasi.nama}"
        lokasi.kode, lokasi.nama, lokasi.keterangan, lokasi.urutan = kode, nama, rapikan_teks(keterangan), urutan
        lokasi.save()
        catat(pengguna, objek="lokasi", kolom="lokasi", lama=lama, baru=f"{kode} - {nama}")
    else:
        lokasi = Lokasi.objects.create(kode=kode, nama=nama, jenis="gudang", keterangan=rapikan_teks(keterangan),
                                       urutan=urutan)
        catat(pengguna, aksi="tambah", objek="lokasi", kolom="lokasi", baru=f"{kode} - {nama}")
    return lokasi


def ubah_aktif(pengguna, lokasi, *, hak):
    if HAK not in hak:
        raise Tolak("Akun ini tidak boleh mengelola lokasi.")
    if lokasi.jenis == "toko":
        raise Tolak("Lokasi toko tidak bisa dinonaktifkan.")
    if lokasi.aktif and StokLokasi.objects.filter(lokasi=lokasi).exclude(jumlah=0).exists():
        raise Tolak(f"{lokasi.nama} masih menyimpan stok. Pindahkan dulu sebelum dinonaktifkan.")
    lokasi.aktif = not lokasi.aktif
    lokasi.save(update_fields=["aktif"])
    catat(pengguna, objek="lokasi", kolom="aktif", baru=f"{lokasi.kode}: {'aktif' if lokasi.aktif else 'nonaktif'}")


def ringkasan_lokasi():
    """Daftar lokasi dengan jumlah rak, varian, dan unit (gudang)."""
    unit = dict(StokLokasi.objects.values("lokasi").annotate(n=Sum("jumlah")).values_list("lokasi", "n"))
    varian = dict(StokLokasi.objects.exclude(jumlah=0).values("lokasi").annotate(n=Count("id")).values_list("lokasi", "n"))
    rak = defaultdict(int)
    for t in Rak.objects.exclude(tempat=None).values_list("tempat_id", flat=True):
        rak[t] += 1
    hasil = []
    for l in Lokasi.objects.all():
        hasil.append({"lokasi": l, "unit": unit.get(l.id, 0), "varian": varian.get(l.id, 0), "rak": rak.get(l.id, 0)})
    return hasil


def varian_hidup():
    return VarianInfo.objects.filter(sampah__isnull=True)
