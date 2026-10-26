"""Tiket admin: izin sementara untuk mengajukan pindah stok antar lokasi dan perubahan rak.

- Pemilik membuka tiket untuk satu akun admin, dengan tujuan dan batas waktu.
- Selama tiket terbuka dan belum lewat batas, admin boleh menambah usulan
  (boleh massal: satu rak atau satu kategori sekaligus). Di luar itu tidak bisa input apa pun.
- Admin mengirim tiket. Pemilik memeriksa per baris, boleh mengubah jumlah/tujuan,
  lalu ACC baris yang dipilih. Baru saat itu stok per lokasi dan rak berubah.
- Total stok (kasir) tidak pernah berubah lewat tiket.
"""
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from pos.models import Item

from master.layanan import inti as layanan
from master.layanan.inti import Tolak, baca_angka, rapikan_teks
from master.layanan.lokasi import baca, jumlah_gudang, lokasi_toko, pindah, stok_di
from master.models import Rak, Tiket, TiketBaris, VarianInfo

HAK_BUKA = "kelola_lokasi"
HAK_PINDAH = "ops_tiket_pindah"
HAK_RAK = "ops_tiket_rak"


def jenis_untuk(hak):
    """Jenis tiket yang bisa dikerjakan akun dengan hak ini."""
    from master.akun.akses import HAK_TIKET_BARANG, HAK_TIKET_LOKASI
    return [j for j, perlu in (("lokasi", HAK_TIKET_LOKASI), ("barang", HAK_TIKET_BARANG), ("hitung", {"gudang_hitung"}))
            if hak & perlu]


def izin_barang_untuk(hak):
    """Izin tiket data barang (harga/alias/aktif) yang dimiliki akun ini."""
    from master.akun.akses import HAK_IZIN_BARANG
    return [i for i, h in HAK_IZIN_BARANG.items() if h in hak]


# ---------------------------------------------------------------------------
# Pemilik
# ---------------------------------------------------------------------------


def buka(pengguna, *, untuk, tujuan, batas, lingkup="", hak, jenis="lokasi", izin=None, kategori=(), brand=(),
         lokasi=None, rak=()):
    """Buka tiket. Jenis "lokasi" (pindah stok/rak) atau "barang" (usul harga, alias, aktif)."""
    from master.akun.akses import hak_pengguna
    from master.layanan import tiket_barang
    if HAK_BUKA not in hak:
        raise Tolak("Akun ini tidak boleh membuka tiket.")
    if jenis not in ("lokasi", "barang", "hitung"):
        raise Tolak("Jenis tiket tidak dikenal.")
    tujuan = rapikan_teks(tujuan)[:300]
    if not tujuan:
        raise Tolak("Isi tujuan tiket, mis. 'rotasi sanitary dari Gudang A ke Gudang B'.")
    hak_untuk = hak_pengguna(untuk) if untuk is not None else set()
    if untuk is None or "gudang" not in hak_untuk or jenis not in jenis_untuk(hak_untuk):
        raise Tolak("Pilih akun yang punya hak mengerjakan tiket jenis ini.")
    izin_bersih, saring = [], {}
    if jenis == "barang":
        izin_bersih = tiket_barang.bersihkan_izin(izin)
        kurang = [i for i in izin_bersih if i not in izin_barang_untuk(hak_untuk)]
        if kurang:
            nama = untuk.first_name or untuk.username
            raise Tolak(f"{nama} tidak punya hak mengusulkan {', '.join(kurang)}. Atur haknya di Karyawan & hak akses.")
        saring = tiket_barang.bersihkan_saring(kategori, brand)
    if jenis == "hitung":
        if "opname" not in hak:
            raise Tolak("Akun ini tidak boleh membuka tugas hitung.")
        from master.models import Lokasi
        l = Lokasi.objects.filter(pk=lokasi if str(lokasi or "").isdigit() else 0, aktif=True).first()
        if l is None:
            raise Tolak("Pilih lokasi yang dihitung.")
        rak_ada = set(Rak.objects.values_list("kode", flat=True))
        saring = {"lokasi": l.pk, "rak": sorted({r for r in rak or [] if r in rak_ada}),
                  "kategori": tiket_barang.bersihkan_saring(kategori, ())["kategori"]}
    if isinstance(batas, str):
        try:
            batas = datetime.fromisoformat(batas.strip())
        except ValueError:
            raise Tolak("Batas waktu tidak terbaca.") from None
    if batas is None:
        raise Tolak("Isi batas waktu.")
    if timezone.is_naive(batas):
        batas = timezone.make_aware(batas, timezone.get_current_timezone())
    if batas <= timezone.now():
        raise Tolak("Batas waktu harus di masa depan.")
    t = Tiket.objects.create(untuk=untuk, tujuan=tujuan, lingkup=rapikan_teks(lingkup)[:300],
                             dibuka_oleh=pengguna.username, batas=batas, jenis=jenis, izin=izin_bersih, saring=saring)
    layanan.catat(pengguna, aksi="tambah", objek="tiket", kolom="tiket",
                  baru=f"Tiket #{t.id} untuk {untuk.username} sampai {timezone.localtime(batas):%d-%m %H:%M}: {tujuan}")
    return t


def batalkan(pengguna, t, *, hak):
    if HAK_BUKA not in hak:
        raise Tolak("Akun ini tidak boleh membatalkan tiket.")
    if t.status not in ("terbuka", "dikirim"):
        raise Tolak("Tiket ini sudah diputuskan.")
    t.status = "batal"
    t.diputus_oleh, t.diputus_waktu = pengguna.username, timezone.now()
    t.save()
    layanan.catat(pengguna, objek="tiket", kolom="status", baru=f"Tiket #{t.id} dibatalkan")


def tolak(pengguna, t, alasan, *, hak):
    if HAK_BUKA not in hak:
        raise Tolak("Akun ini tidak boleh memutuskan tiket.")
    alasan = rapikan_teks(alasan)[:300]
    if not alasan:
        raise Tolak("Isi alasan penolakan.")
    if not t.bisa_diputus:
        raise Tolak("Tiket ini belum dikirim atau sudah diputuskan.")
    t.status = "ditolak"
    t.diputus_oleh, t.diputus_waktu, t.catatan_putusan = pengguna.username, timezone.now(), alasan
    t.save()
    layanan.catat(pengguna, objek="tiket", kolom="status", baru=f"Tiket #{t.id} ditolak", catatan=alasan)


def terapkan(pengguna, t, data, *, hak):
    """ACC baris yang dicentang (`pilih`). Jumlah dan tujuan baris pindah boleh diubah pemilik."""
    if HAK_BUKA not in hak:
        raise Tolak("Akun ini tidak boleh menyetujui tiket.")
    if t.jenis != "lokasi" or not t.bisa_diputus:
        raise Tolak("Tiket ini belum dikirim atau sudah diputuskan.")
    pilih = {int(x) for x in data.getlist("pilih") if str(x).isdigit()} if hasattr(data, "getlist") else \
        {int(x) for x in data.get("pilih", []) if str(x).isdigit()}
    berhasil = gagal = 0
    with transaction.atomic():
        t = Tiket.objects.select_for_update().get(pk=t.pk)
        for b in t.baris.select_related("varian", "rak", "dari", "ke"):
            if b.id not in pilih:
                b.hasil = "Tidak di-ACC"
                b.save(update_fields=["hasil"])
                continue
            try:
                with transaction.atomic():
                    _terapkan_baris(pengguna, t, b, data, hak)
                b.diterapkan, b.hasil = True, "Diterapkan"
                berhasil += 1
            except Tolak as tolak:
                b.hasil = str(tolak)[:300]
                gagal += 1
            b.save(update_fields=["diterapkan", "hasil", "jumlah", "ke"])
        t.status = "selesai"
        t.diputus_oleh, t.diputus_waktu = pengguna.username, timezone.now()
        t.save()
        layanan.catat(pengguna, objek="tiket", kolom="status",
                      baru=f"Tiket #{t.id}: {berhasil} diterapkan, {gagal} gagal, {t.baris.count() - berhasil - gagal} tidak di-ACC")
    return berhasil, gagal


def _terapkan_baris(pengguna, t, b, data, hak):
    catatan = f"Tiket #{t.id}" + (f": {b.catatan}" if b.catatan else "")
    if b.jenis == "pindah":
        if f"jumlah_{b.id}" in data:
            b.jumlah = baca_angka(data.get(f"jumlah_{b.id}"), boleh_kosong=False)
        if data.get(f"ke_{b.id}"):
            b.ke = baca(data.get(f"ke_{b.id}"))
        pindah(pengguna, b.varian, dari=b.dari, ke=b.ke, jumlah=b.jumlah, catatan=catatan, hak=hak | {"kelola_lokasi"})
    elif b.jenis == "rak":
        layanan.ubah_varian(pengguna, b.varian, "kode_rak", b.rak_id if b.rak else "", hak=hak | {"ubah_info"},
                            catatan=catatan)
    elif b.jenis == "rak_lokasi":
        lama = b.rak.tempat.nama if b.rak.tempat else "-"
        b.rak.tempat = b.ke
        b.rak.save(update_fields=["tempat"])
        layanan.catat(pengguna, objek="rak", kolom="rak", lama=f"{b.rak.kode} di {lama}",
                      baru=f"{b.rak.kode} di {b.ke.nama}", catatan=catatan)


# ---------------------------------------------------------------------------
# Admin (aplikasi operasional)
# ---------------------------------------------------------------------------


def _harus_bisa_diisi(pengguna, t):
    if t.untuk_id != pengguna.pk or not t.untuk.is_active:
        raise Tolak("Tiket ini bukan untuk akun ini.")
    if t.status != "terbuka":
        raise Tolak("Tiket ini sudah dikirim atau ditutup.")
    if t.lewat_batas:
        raise Tolak("Batas waktu tiket ini sudah lewat. Minta pemilik membuka tiket baru.")


def _harus_lokasi(pengguna, t, perlu=None):
    from master.akun.akses import hak_pengguna
    if t.jenis != "lokasi":
        raise Tolak("Tiket ini bukan tiket pindah lokasi/rak.")
    _harus_bisa_diisi(pengguna, t)
    if perlu and perlu not in hak_pengguna(pengguna):
        raise Tolak("Akun ini tidak punya hak " + ("mengusulkan pindah stok." if perlu == HAK_PINDAH else "mengusulkan perubahan rak."))


def _lokasi_beda(dari, ke):
    if dari.pk == ke.pk:
        raise Tolak("Lokasi asal dan tujuan sama.")
    if not ke.aktif:
        raise Tolak(f"Lokasi {ke.nama} tidak aktif.")


def tambah_pindah(pengguna, t, *, varian_id, dari, ke, jumlah, catatan=""):
    _harus_lokasi(pengguna, t, HAK_PINDAH)
    info = VarianInfo.objects.filter(pk=varian_id or 0, sampah__isnull=True).first()
    if info is None:
        raise Tolak("Pilih barang dari hasil pencarian.")
    dari, ke = baca(dari), baca(ke)
    _lokasi_beda(dari, ke)
    qty = baca_angka(jumlah, boleh_kosong=False)
    if qty <= 0:
        raise Tolak("Jumlah harus lebih dari nol.")
    return TiketBaris.objects.create(tiket=t, jenis="pindah", varian=info, dari=dari, ke=ke, jumlah=qty,
                                     catatan=rapikan_teks(catatan)[:300])


def tambah_massal(pengguna, t, *, dari, ke, rak=None, kategori=None, ikut_rak=False, catatan=""):
    """Semua barang di satu rak (atau satu kategori) yang punya stok di lokasi asal, pindah semua ke tujuan."""
    _harus_lokasi(pengguna, t, HAK_PINDAH)
    dari, ke = baca(dari), baca(ke)
    _lokasi_beda(dari, ke)
    rak_obj = Rak.objects.filter(kode=rak).first() if rak else None
    kategori = rapikan_teks(kategori)
    if rak_obj is None and not kategori:
        raise Tolak("Pilih rak atau kategori.")
    qs = VarianInfo.objects.filter(sampah__isnull=True)
    if rak_obj is not None:
        qs = qs.filter(rak=rak_obj)
    if kategori:
        qs = qs.filter(item_name__in=Item.objects.filter(category=kategori).values("item_name"))
    if ikut_rak:
        from master.akun.akses import hak_pengguna
        if HAK_RAK not in hak_pengguna(pengguna):
            raise Tolak("Akun ini tidak punya hak mengusulkan perubahan rak. Hilangkan centang 'raknya juga dipindah'.")
    calon = list(qs)
    if dari.jenis == "gudang":
        jumlah = {v: d.get(dari.id, 0) for v, d in jumlah_gudang(c.id for c in calon).items()}
        isi = [(c, jumlah.get(c.id, 0)) for c in calon]
    else:
        isi = [(c, stok_di(c, dari)) for c in calon]
    isi = [(c, n) for c, n in isi if n > 0]
    catatan = rapikan_teks(catatan)[:300]
    with transaction.atomic():
        for c, n in isi:
            TiketBaris.objects.create(tiket=t, jenis="pindah", varian=c, dari=dari, ke=ke, jumlah=n, catatan=catatan)
        if ikut_rak and rak_obj is not None and ke.jenis == "gudang":
            TiketBaris.objects.create(tiket=t, jenis="rak_lokasi", rak=rak_obj, ke=ke, catatan=catatan)
    if not isi and not (ikut_rak and rak_obj is not None):
        raise Tolak(f"Tidak ada barang {'di rak ' + rak_obj.kode if rak_obj else 'kategori ' + kategori} yang punya stok di {dari.nama}.")
    return len(isi)


def tambah_rak(pengguna, t, *, varian_id, rak, catatan=""):
    _harus_lokasi(pengguna, t, HAK_RAK)
    info = VarianInfo.objects.filter(pk=varian_id or 0, sampah__isnull=True).first()
    if info is None:
        raise Tolak("Pilih barang dari hasil pencarian.")
    rak_obj = Rak.objects.filter(kode=rak).first() if rak else None
    if rak and rak_obj is None:
        raise Tolak(f"Rak {rak} belum terdaftar.")
    return TiketBaris.objects.create(tiket=t, jenis="rak", varian=info, rak=rak_obj, catatan=rapikan_teks(catatan)[:300])


def tambah_rak_lokasi(pengguna, t, *, rak, ke, catatan=""):
    _harus_lokasi(pengguna, t, HAK_RAK)
    rak_obj = Rak.objects.filter(kode=rak).first()
    if rak_obj is None:
        raise Tolak("Pilih rak.")
    ke = baca(ke)
    if ke.jenis != "gudang" and ke.pk != lokasi_toko().pk:
        raise Tolak("Lokasi tidak dikenal.")
    return TiketBaris.objects.create(tiket=t, jenis="rak_lokasi", rak=rak_obj, ke=ke, catatan=rapikan_teks(catatan)[:300])


def hapus_baris(pengguna, t, baris_id):
    _harus_lokasi(pengguna, t)
    TiketBaris.objects.filter(tiket=t, pk=baris_id).delete()


def kirim(pengguna, t, catatan=""):
    _harus_lokasi(pengguna, t)
    if not t.baris.exists():
        raise Tolak("Belum ada usulan di tiket ini.")
    t.status = "dikirim"
    t.dikirim_waktu = timezone.now()
    t.catatan_admin = rapikan_teks(catatan)[:500]
    t.save()
    layanan.catat(pengguna, objek="tiket", kolom="status", baru=f"Tiket #{t.id} dikirim: {t.baris.count()} usulan")
