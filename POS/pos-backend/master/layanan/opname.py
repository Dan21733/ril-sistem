"""Hitung stok (opname) oleh kepala gudang, disetujui pemilik di master.

- Kepala gudang menghitung tanpa melihat stok sistem (hitungan buta).
- Saat tiap barang dihitung, stok sistem saat itu ikut disimpan diam-diam.
- Stok baru berubah saat ACC. Yang diterapkan adalah SELISIH (hitungan - stok
  sistem saat dihitung), bukan angka hitungannya, supaya penjualan atau barang
  masuk di antara penghitungan dan ACC tidak ikut terhapus.
- Sejak 23 Okt 2026 kepala gudang hanya menghitung lewat tugas (tiket jenis "hitung") yang
  dibuka pemilik: lokasi dan lingkup (rak/kategori) ditentukan pemilik, satu tugas = satu opname.
- Toko yang stoknya tidak ada di kasir (belum punya kasir): hitungan hanya
  dicatat. Saat ACC tidak ada stok yang diubah; angkanya jadi catatan stok
  awal untuk toko itu.
"""
from django.db import transaction
from django.utils import timezone

from pos.models import Item

from master.layanan import inti as layanan
from master.layanan.inti import Tolak, baca_angka, rapikan_teks
from master.models import Opname, OpnameBaris, VarianInfo


def _toko(toko):
    from master.layanan import toko as svc_toko
    return toko or svc_toko.utama()


def _lokasi(toko, lokasi):
    """Lokasi hanya berlaku untuk toko yang memegang stok kasir. Toko (display) disimpan sebagai null."""
    if not toko.stok_di_kasir or lokasi is None or lokasi.jenis == "toko":
        return None
    return lokasi


def draf(pengguna, buat=True, toko=None, lokasi=None, tiket=None):
    toko = _toko(toko)
    lokasi = _lokasi(toko, lokasi)
    if tiket is not None:
        op = Opname.objects.filter(tiket=tiket).first()
        if op is not None and op.status != "draf":
            raise Tolak("Hitungan tugas ini sudah dikirim.")
        if op is None and buat:
            op = Opname.objects.create(dibuat_oleh=pengguna.username, toko=toko, lokasi=lokasi, tiket=tiket)
        return op
    op = Opname.objects.filter(status="draf", dibuat_oleh=pengguna.username, toko=toko, lokasi=lokasi,
                               tiket__isnull=True).first()
    if op is None and buat:
        op = Opname.objects.create(dibuat_oleh=pengguna.username, toko=toko, lokasi=lokasi)
    return op


def simpan_hitungan(pengguna, data, toko=None, lokasi=None, tiket=None, boleh=None):
    """Simpan isian hitung_<id varian> (yang kosong dilewati). Mengembalikan jumlah baris tersimpan."""
    isian = {}
    for kunci, nilai in data.items():
        if kunci.startswith("hitung_") and str(nilai).strip():
            isian[kunci[7:]] = baca_angka(nilai, boleh_kosong=False)
    if not isian:
        raise Tolak("Belum ada hitungan yang diisi.")
    if boleh is not None:
        isian = {k: v for k, v in isian.items() if k.isdigit() and int(k) in boleh}
        if not isian:
            raise Tolak("Barang itu di luar lingkup tugas ini.")
    with transaction.atomic():
        op = draf(pengguna, toko=toko, lokasi=lokasi, tiket=tiket)
        punya_stok = op.toko.stok_di_kasir
        from master.layanan.lokasi import stok_di
        for info in VarianInfo.objects.filter(pk__in=[k for k in isian if k.isdigit()]):
            stok = stok_di(info, op.lokasi) if punya_stok else 0
            OpnameBaris.objects.update_or_create(opname=op, varian=info, defaults={
                "item_name": info.item_name, "variant": info.variant, "hitung": isian[str(info.pk)],
                "stok_saat_hitung": stok or 0, "waktu_hitung": timezone.now(),
                "catatan": rapikan_teks(data.get(f"catatan_{info.pk}"))[:200],
            })
    return len(isian)


def hapus_baris(pengguna, baris_id):
    OpnameBaris.objects.filter(pk=baris_id, opname__status="draf", opname__dibuat_oleh=pengguna.username).delete()


def ajukan(pengguna, catatan="", toko=None, lokasi=None, tiket=None):
    op = draf(pengguna, buat=False, toko=toko, lokasi=lokasi, tiket=tiket)
    if op is None or not op.baris.exists():
        raise Tolak("Belum ada barang yang dihitung.")
    op.status = "diajukan"
    op.diajukan_waktu = timezone.now()
    op.catatan = rapikan_teks(catatan)[:300]
    op.save()
    layanan.catat(pengguna, aksi="tambah", objek="opname", kolom="opname",
                  baru=f"Opname #{op.id}: {op.baris.count()} barang diajukan")
    return op


def rincian(op):
    """Baris opname dengan stok sekarang, stok sesudah ACC, dan nilai selisihnya (untuk pemilik)."""
    from master.layanan.lokasi import stok_di
    baris = list(op.baris.select_related("varian__rak"))
    barang = {(b["item_name"], b["variant"]): b for b in Item.objects.filter(
        item_name__in={x.item_name for x in baris}).values("item_name", "variant", "in_stock", "cost_amount")}
    for x in baris:
        # Nama bisa sudah diganti sejak dihitung: pakai nama terbaru dari varian.
        b = barang.get((x.varian.item_name, x.varian.variant)) or barang.get((x.item_name, x.variant)) or {}
        x.stok_total = b.get("in_stock") or 0
        punya_stok = op.toko is None or op.toko.stok_di_kasir
        x.stok_sekarang = stok_di(x.varian, op.lokasi, total=x.stok_total) if punya_stok else x.stok_total
        x.stok_jadi = x.stok_sekarang + x.selisih
        modal = x.varian.modal_rata if x.varian.modal_rata is not None else b.get("cost_amount")
        x.nilai_selisih = x.selisih * modal if modal is not None else None
        x.nilai_hitung = x.hitung * modal if modal is not None else None
    return baris


def acc(pengguna, op, pilih, *, hak):
    """Terapkan selisih baris yang dipilih. Baris lain dibiarkan (tidak diterapkan)."""
    if "opname" not in hak:
        raise Tolak("Akun ini tidak boleh menyetujui opname.")
    pilih = {int(x) for x in pilih if str(x).isdigit()}
    with transaction.atomic():
        op = Opname.objects.select_for_update().get(pk=op.pk)
        if op.status != "diajukan":
            raise Tolak("Opname ini sudah diputuskan.")
        punya_stok = op.toko is None or op.toko.stok_di_kasir
        for b in op.baris.select_related("varian"):
            if b.id not in pilih:
                continue
            if b.selisih and punya_stok:
                layanan.sesuaikan_stok(
                    pengguna, b.varian, cara="tambah" if b.selisih > 0 else "kurang", angka=abs(b.selisih),
                    jenis="hitung", hak=hak | {"ubah_stok"}, lokasi=op.lokasi,
                    catatan=f"Opname #{op.id} oleh {op.dibuat_oleh}: dihitung {b.hitung}"
                            + (f". {b.catatan}" if b.catatan else ""))
            b.diterapkan = True
            b.save(update_fields=["diterapkan"])
        op.status = "disetujui"
        op.diputus_oleh, op.diputus_waktu = pengguna.username, timezone.now()
        op.save()
        _tutup_tiket(op, "selesai", pengguna)
        layanan.catat(pengguna, objek="opname", kolom="status", lama="Menunggu ACC", baru="Disetujui",
                      catatan=f"Opname #{op.id}: {len(pilih)} dari {op.baris.count()} baris diterapkan")
    return op


def tolak(pengguna, op, alasan, *, hak):
    if "opname" not in hak:
        raise Tolak("Akun ini tidak boleh memutuskan opname.")
    alasan = rapikan_teks(alasan)[:300]
    if not alasan:
        raise Tolak("Isi alasan penolakan supaya kepala gudang tahu apa yang harus dihitung ulang.")
    if op.status != "diajukan":
        raise Tolak("Opname ini sudah diputuskan.")
    op.status = "ditolak"
    op.diputus_oleh, op.diputus_waktu, op.catatan_putusan = pengguna.username, timezone.now(), alasan
    op.save()
    _tutup_tiket(op, "ditolak", pengguna, alasan)
    layanan.catat(pengguna, objek="opname", kolom="status", lama="Menunggu ACC", baru="Ditolak",
                  catatan=f"Opname #{op.id}: {alasan}")


def _tutup_tiket(op, status, pengguna, alasan=""):
    """Tugas hitung ikut selesai/ditolak bersama opnamenya."""
    t = op.tiket
    if t is None or t.status in ("selesai", "ditolak", "batal"):
        return
    t.status = status
    t.diputus_oleh, t.diputus_waktu = pengguna.username, timezone.now()
    t.catatan_putusan = alasan[:300]
    t.save(update_fields=["status", "diputus_oleh", "diputus_waktu", "catatan_putusan"])


# ---------------------------------------------------------------------------
# Tugas hitung (tiket jenis "hitung")
# ---------------------------------------------------------------------------


def lokasi_tugas(t):
    from master.models import Lokasi
    return Lokasi.objects.filter(pk=(t.saring or {}).get("lokasi") or 0).first()


def lingkup_tugas(t):
    """Varian yang boleh dihitung di tugas ini: rak dan/atau kategori dari pemilik. Kosong = semua barang aktif."""
    from django.db.models import Q
    s = t.saring or {}
    qs = VarianInfo.objects.filter(aktif=True, sampah__isnull=True)
    syarat = Q()
    if s.get("rak"):
        syarat |= Q(rak_id__in=s["rak"])
    if s.get("kategori"):
        syarat |= Q(item_name__in=Item.objects.filter(category__in=s["kategori"]).values("item_name"))
    return qs.filter(syarat) if syarat else qs


def lingkup_teks(t):
    s = t.saring or {}
    l = lokasi_tugas(t)
    bagian = [f"di {l.nama}" if l else ""]
    if s.get("rak"):
        bagian.append("rak " + ", ".join(s["rak"]))
    if s.get("kategori"):
        bagian.append("kategori " + ", ".join(s["kategori"]))
    if not s.get("rak") and not s.get("kategori"):
        bagian.append("semua barang")
    return " · ".join(x for x in bagian if x)


def kirim_tugas(pengguna, t, catatan=""):
    from master.layanan.tiket import _harus_bisa_diisi
    if t.jenis != "hitung":
        raise Tolak("Tiket ini bukan tugas hitung.")
    _harus_bisa_diisi(pengguna, t)
    with transaction.atomic():
        op = ajukan(pengguna, catatan, toko=t_toko(t), lokasi=lokasi_tugas(t), tiket=t)
        t.status = "dikirim"
        t.dikirim_waktu = timezone.now()
        t.catatan_admin = rapikan_teks(catatan)[:500]
        t.save()
    return op


def teruskan_tugas(pengguna, t, *, hak):
    """Pemilik: tugas lewat batas tapi sudah ada hitungan; diteruskan ke opname untuk diperiksa."""
    if "opname" not in hak:
        raise Tolak("Akun ini tidak boleh memutuskan opname.")
    op = Opname.objects.filter(tiket=t, status="draf").first()
    if op is None or not op.baris.exists():
        raise Tolak("Belum ada hitungan di tugas ini.")
    with transaction.atomic():
        op.status = "diajukan"
        op.diajukan_waktu = timezone.now()
        op.catatan = "Diteruskan pemilik setelah lewat batas waktu"
        op.save()
        t.status = "dikirim"
        t.dikirim_waktu = timezone.now()
        t.save()
    return op


def t_toko(t):
    from master.layanan import toko as svc_toko
    return svc_toko.utama()
