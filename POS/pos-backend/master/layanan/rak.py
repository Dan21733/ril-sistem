"""Rak baris x kolom (14 Nov 2026).

Satu rak fisik (induk, mis. "A") dipecah jadi kotak: baris dihitung dari BAWAH (baris 1 = paling dekat
lantai, jadi menambah tingkat di atas tidak mengubah nomor lama), kolom dari KIRI saat menghadap rak.
Kode kotak: A-3-2 (rak A, baris 3, kolom 2). Rak 1 kolom memakai kode pendek A-3, sama dengan rak bertingkat lama.
Tiap kotak tetap satu baris tabel Rak, jadi semua yang menunjuk ke rak (varian, hitung, Siapkan) tidak berubah.
"""
import re

from django.db import transaction

from master.layanan import inti as layanan
from master.layanan.inti import Tolak
from master.models import Rak, Ruang

BATAS_BARIS = 20
BATAS_KOLOM = 20


def kode_induk(teks):
    kode = re.sub(r"\s+", "", str(teks or "")).upper()
    if not kode:
        raise Tolak("Kode rak wajib diisi, mis. A.")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-/]*", kode):
        raise Tolak("Kode rak hanya huruf, angka, titik, strip.")
    return kode


def kode_kotak(induk, baris, kolom, satu_kolom):
    return f"{induk}-{baris}" if satu_kolom else f"{induk}-{baris}-{kolom}"


def _sel(r):
    """Semua (baris, kolom) yang ditempati satu kotak; kotak gabungan menempati lebih dari satu."""
    b0, k0 = r.baris or 1, r.kolom or 1
    return [(b, k) for b in range(b0, b0 + max(r.tinggi, 1)) for k in range(k0, k0 + max(r.lebar, 1))]


def gabung(pengguna, kode):
    """Gabung kotak-kotak bersebelahan satu rak jadi satu kotak (tingkat tanpa sekat / tanpa papan).

    Kotak yang dipilih harus membentuk persegi panjang penuh. Yang dipakai kotak kiri-bawah (kodenya tetap);
    kotak lain dihapus, jadi harus kosong (tidak ada barang/hitungan yang menunjuk ke sana).
    """
    kotak = list(Rak.objects.filter(kode__in=list(kode)))
    if len(kotak) < 2:
        raise Tolak("Pilih minimal 2 kotak yang bersebelahan.")
    induk = {r.induk for r in kotak}
    if len(induk) != 1 or not next(iter(induk)):
        raise Tolak("Kotak yang digabung harus dari satu rak yang sama.")
    sel = [p for r in kotak for p in _sel(r)]
    if len(sel) != len(set(sel)):
        raise Tolak("Ada kotak yang tumpang tindih.")
    bs, ks = [b for b, _ in sel], [k for _, k in sel]
    tinggi, lebar = max(bs) - min(bs) + 1, max(ks) - min(ks) + 1
    if tinggi * lebar != len(sel):
        raise Tolak("Kotak yang dipilih harus membentuk persegi panjang penuh (tanpa lubang).")
    jangkar = next(r for r in kotak if (r.baris or 1, r.kolom or 1) == (min(bs), min(ks)))
    lain = [r for r in kotak if r.pk != jangkar.pk]
    isi = sorted(_dipakai([r.kode for r in lain]))
    if isi:
        raise Tolak("Kotak ini masih berisi barang atau hitungan: " + ", ".join(isi[:6])
                    + ". Pindahkan barangnya ke " + jangkar.kode + " dulu, baru bisa digabung.")
    with transaction.atomic():
        Rak.objects.filter(pk__in=[r.pk for r in lain]).delete()
        Rak.objects.filter(pk=jangkar.pk).update(lebar=lebar, tinggi=tinggi)
        layanan.catat(pengguna, aksi="ubah", objek="rak", kolom="rak",
                      baru=f"{jangkar.kode}: gabung {len(kotak)} kotak ({tinggi} baris x {lebar} kolom)")
    return jangkar.kode


def pisah(pengguna, kode):
    """Kebalikan gabung: kotak gabungan dipecah lagi jadi kotak-kotak biasa (kotak baru kosong)."""
    r = Rak.objects.filter(pk=kode).first()
    if r is None or (r.lebar <= 1 and r.tinggi <= 1):
        raise Tolak("Kotak ini bukan kotak gabungan.")
    satu = r.kode == f"{r.induk}-{r.baris}"            # rak 1 kolom memakai kode pendek A-3
    baru = [Rak(kode=kode_kotak(r.induk, b, k, satu), nama=r.nama, tempat_id=r.tempat_id, ruang_id=r.ruang_id,
                pic_id=r.pic_id, langsung=r.langsung, jenis=r.jenis, induk=r.induk, baris=b, kolom=k)
            for b, k in _sel(r) if (b, k) != (r.baris or 1, r.kolom or 1)]
    bentrok = set(Rak.objects.filter(kode__in=[x.kode for x in baru]).values_list("kode", flat=True))
    if bentrok:
        raise Tolak("Kode sudah dipakai: " + ", ".join(sorted(bentrok)[:5]))
    with transaction.atomic():
        Rak.objects.filter(pk=r.pk).update(lebar=1, tinggi=1)
        Rak.objects.bulk_create(baru)
        layanan.catat(pengguna, aksi="ubah", objek="rak", kolom="rak", baru=f"{r.kode}: dipisah jadi {len(baru) + 1} kotak")
    return len(baru) + 1


def _referensi():
    """Semua tabel yang menunjuk ke rak (termasuk relasi tersembunyi): (model, nama kolom)."""
    return [(f.related_model, f.field.name) for f in Rak._meta.get_fields(include_hidden=True)
            if f.auto_created and not f.concrete and f.is_relation]


def _dipakai(kode):
    """Kode rak yang masih ditunjuk sesuatu (barang, rak tambahan, hitungan, usulan tugas)."""
    hasil = set()
    for model, kolom in _referensi():
        hasil |= set(model.objects.filter(**{f"{kolom}__in": kode}).values_list(f"{kolom}_id", flat=True))
    return hasil


def _tugas_hitung_jalan(kode):
    """Tugas hitung yang masih dikerjakan di lokasi rak ini: kode kotak jangan diganti selama itu,
    karena tablet karyawan bisa masih menyimpan kode lama di antreannya."""
    from master.models import Tiket
    lokasi = set(Rak.objects.filter(kode__in=kode).values_list("tempat_id", flat=True))
    return [t for t in Tiket.objects.filter(jenis="hitung", status="terbuka")
            if (t.saring or {}).get("lokasi") in lokasi or set((t.saring or {}).get("rak") or []) & set(kode)]


def _ganti_kode(lama, baru):
    """Ganti kode satu kotak (kode = kunci utama): salin raknya, pindahkan semua yang menunjuk, hapus yang lama."""
    r = Rak.objects.get(pk=lama)
    salinan = Rak.objects.create(kode=baru, nama=r.nama, tempat_id=r.tempat_id, lokasi=r.lokasi,
                                 keterangan=r.keterangan, pic_id=r.pic_id, langsung=r.langsung,
                                 induk=r.induk, baris=r.baris, kolom=r.kolom)
    for model, kolom in _referensi():
        model.objects.filter(**{kolom: r}).update(**{kolom: salinan})
    r.delete()


def ubah_ukuran(pengguna, *, induk, baris, kolom, nama="", tempat=None, pic=None, langsung=False, kurangi=False):
    """Atur ukuran rak induk jadi baris x kolom.

    - Kotak baru dibuat (nama/lokasi/PIC ikut kotak yang sudah ada, kalau ada).
    - Rak 1 kolom yang ditambah kolomnya: kode pendek A-3 diganti jadi A-3-1 supaya semua kotak seragam;
      barang, rak tambahan, dan hitungan yang menunjuk ke kotak itu ikut pindah ke kode baru.
    - kurangi=True: kotak di luar ukuran baru dihapus, asal kosong (tidak ada barang/hitungan yang menunjuk).
    Hasil: {"dibuat": [...], "diganti": [(lama, baru)], "dihapus": [...]}.
    """
    induk = kode_induk(induk)
    if not 1 <= baris <= BATAS_BARIS:
        raise Tolak(f"Jumlah baris 1 sampai {BATAS_BARIS}.")
    if not 1 <= kolom <= BATAS_KOLOM:
        raise Tolak(f"Jumlah kolom 1 sampai {BATAS_KOLOM}.")
    ada = list(Rak.objects.filter(induk=induk))
    contoh = ada[0] if ada else None
    nama = layanan.rapikan_teks(nama) or (contoh.nama if contoh else f"Rak {induk}")
    if contoh is not None:
        tempat, pic, langsung = contoh.tempat, contoh.pic, contoh.langsung
    lebar = kolom if kurangi else max([kolom] + [r.kolom or 1 for r in ada])
    satu = lebar == 1
    if len(kode_kotak(induk, max(baris, 1), lebar, satu)) > 20:
        raise Tolak("Kode rak terlalu panjang (kode kotak maksimal 20 karakter).")
    posisi = {(r.baris or 1, r.kolom or 1): r for r in ada}
    tertutup = {p for r in ada for p in _sel(r)}           # termasuk sel yang tertutup kotak gabungan
    lewat = [r for r in ada if kurangi and (r.baris or 1) <= baris and (r.kolom or 1) <= kolom
             and ((r.baris or 1) + r.tinggi - 1 > baris or (r.kolom or 1) + r.lebar - 1 > kolom)]
    if lewat:
        raise Tolak(f"Kotak gabungan {lewat[0].kode} melewati ukuran baru. Pisahkan dulu kotaknya.")
    hapus = [r for (b, k), r in posisi.items() if kurangi and (b > baris or k > kolom)]
    tetap = [r for r in ada if r not in hapus]
    ganti = [(r.kode, kode_kotak(induk, r.baris or 1, r.kolom or 1, satu)) for r in tetap
             if r.kode != kode_kotak(induk, r.baris or 1, r.kolom or 1, satu)]
    rencana = [Rak(kode=kode_kotak(induk, b, k, satu), nama=nama, tempat=tempat, pic=pic, langsung=langsung,
                   induk=induk, baris=b, kolom=k)
               for b in range(1, baris + 1) for k in range(1, kolom + 1) if (b, k) not in tertutup]
    if hapus:
        isi = sorted(_dipakai([r.kode for r in hapus]))
        if isi:
            raise Tolak("Kotak ini masih berisi barang atau hitungan: " + ", ".join(isi[:6])
                        + ". Pindahkan barangnya dulu, baru rak bisa diperkecil.")
    if ganti and _tugas_hitung_jalan([lama for lama, _ in ganti]):
        t = _tugas_hitung_jalan([lama for lama, _ in ganti])[0]
        raise Tolak(f"Kode kotak rak {induk} perlu diganti (A-3 jadi A-3-1), tapi tugas hitung #{t.id} masih berjalan "
                    "di lokasi ini. Tunggu tugas itu dikirim dan diputuskan dulu.")
    kode_baru = [r.kode for r in rencana] + [b for _, b in ganti]
    lama_semua = {r.kode for r in ada}
    bentrok = set(Rak.objects.filter(kode__in=kode_baru).exclude(kode__in=lama_semua).values_list("kode", flat=True))
    if bentrok:
        raise Tolak("Kode sudah dipakai rak lain: " + ", ".join(sorted(bentrok)[:5])
                    + ". Pakai kode rak lain atau hapus rak itu dulu.")
    with transaction.atomic():
        for r in hapus:
            r.delete()
        # Ganti kode lewat nama sementara dulu, supaya A-1 -> A-1-1 tidak bentrok dengan kotak lain di tengah jalan.
        for lama, baru in ganti:
            _ganti_kode(lama, f"~{lama}"[:20])
        for lama, baru in ganti:
            _ganti_kode(f"~{lama}"[:20], baru)
        Rak.objects.bulk_create(rencana)
        if rencana or ganti or hapus:
            layanan.catat(pengguna, aksi="ubah" if ada else "tambah", objek="rak", kolom="rak",
                          baru=f"Rak {induk}: {baris} baris x {kolom} kolom ({len(rencana)} kotak baru"
                               + (f", {len(ganti)} kode diganti" if ganti else "")
                               + (f", {len(hapus)} kotak dihapus" if hapus else "") + ")")
    return {"dibuat": rencana, "diganti": ganti, "dihapus": [r.kode for r in hapus], "sudah_ada": len(ada) - len(hapus)}


def buat(pengguna, *, induk, nama, baris, kolom, tempat=None, pic=None, langsung=False):
    """Buat rak baris x kolom, atau tambah baris/kolom rak yang sudah ada (tidak pernah mengurangi).
    Hasil: (kotak dibuat, jumlah kotak yang sudah ada)."""
    h = ubah_ukuran(pengguna, induk=induk, baris=baris, kolom=kolom, nama=nama, tempat=tempat, pic=pic,
                    langsung=langsung)
    return h["dibuat"], h["sudah_ada"]


def urut(r):
    """Urutan tampil: per rak induk, lalu baris, lalu kolom; rak tunggal urut kode alami."""
    alami = [(0, int(x), "") if x.isdigit() else (1, 0, x.lower()) for x in re.split(r"(\d+)", r.induk or r.kode) if x]
    return (alami, r.baris or 0, r.kolom or 0, r.kode)


def label(r):
    """Untuk karyawan: "Rak A · baris 3 · kolom 2" (kolom tidak disebut untuk rak 1 kolom, kode A-3)."""
    if not r.induk or not r.baris:
        return f"Rak {r.kode}"
    return f"Rak {r.induk} · baris {r.baris}" + (f" · kolom {r.kolom}" if r.kode == f"{r.induk}-{r.baris}-{r.kolom}" else "")


def pilihan_tugas():
    """Pilihan rak di form buat tugas: satu pilihan per rak induk ("Rak G · 42 kotak", nilai "induk:G"),
    rak tunggal tetap sendiri. Diuraikan lagi jadi kode kotak oleh `uraikan`."""
    hasil, per = [], {}
    for r in sorted(Rak.objects.select_related("tempat"), key=urut):
        if r.induk:
            if r.induk not in per:
                per[r.induk] = {"nilai": f"induk:{r.induk}", "kode": r.induk, "nama": r.nama, "tempat": r.tempat,
                                "tempat_id": r.tempat_id, "n": 0}
                hasil.append(per[r.induk])
            per[r.induk]["n"] += 1
        else:
            hasil.append({"nilai": r.kode, "kode": r.kode, "nama": r.nama, "tempat": r.tempat, "tempat_id": r.tempat_id, "n": 1})
    return hasil


def uraikan(nilai):
    """["induk:G", "R1"] -> semua kode kotak rak G + "R1"."""
    nilai = list(nilai or [])
    induk = [n[6:] for n in nilai if str(n).startswith("induk:")]
    kode = [n for n in nilai if not str(n).startswith("induk:")]
    if induk:
        kode += list(Rak.objects.filter(induk__in=induk).values_list("kode", flat=True))
    return kode


# ---------------------------------------------------------------------------
# Ruangan (14 Nov 2026): pengelompokan posisi di dalam lokasi. Stok tetap per lokasi.
# ---------------------------------------------------------------------------

def tambah_ruang(pengguna, lokasi, nama):
    nama = layanan.rapikan_teks(nama)[:60]
    if lokasi is None:
        raise Tolak("Pilih lokasinya dulu.")
    if not nama:
        raise Tolak("Nama ruangan wajib diisi, mis. Ruangan 1.")
    if Ruang.objects.filter(lokasi=lokasi, nama__iexact=nama).exists():
        raise Tolak(f"Ruangan {nama} sudah ada di {lokasi.nama}.")
    r = Ruang.objects.create(lokasi=lokasi, nama=nama, urutan=Ruang.objects.filter(lokasi=lokasi).count())
    layanan.catat(pengguna, aksi="tambah", objek="ruang", kolom="ruang", baru=f"{lokasi.nama}: {nama}")
    return r


def ubah_ruang(pengguna, ruang, nama):
    nama = layanan.rapikan_teks(nama)[:60]
    if not nama:
        raise Tolak("Nama ruangan wajib diisi.")
    if Ruang.objects.filter(lokasi=ruang.lokasi, nama__iexact=nama).exclude(pk=ruang.pk).exists():
        raise Tolak(f"Ruangan {nama} sudah ada.")
    lama, ruang.nama = ruang.nama, nama
    ruang.save(update_fields=["nama"])
    layanan.catat(pengguna, objek="ruang", kolom="nama", lama=lama, baru=nama)


def hapus_ruang(pengguna, ruang):
    """Rak di dalamnya tidak ikut terhapus; hanya jadi "tanpa ruangan"."""
    n = ruang.rak.count()
    layanan.catat(pengguna, aksi="hapus", objek="ruang", kolom="ruang", lama=f"{ruang.lokasi.nama}: {ruang.nama}",
                  catatan=f"{n} rak jadi tanpa ruangan")
    ruang.delete()
    return n


def ruang_untuk(tempat, ruang_id):
    """Ruangan yang dipilih di form, hanya kalau memang ruangan di lokasi rak itu."""
    if not str(ruang_id or "").isdigit() or tempat is None:
        return None
    return Ruang.objects.filter(pk=int(ruang_id), lokasi=tempat).first()


def label_lengkap(r):
    """Untuk tombol di tablet: "Ruangan 1 · Rak A · baris 3 · kolom 2" / "Ruangan 2 · Area keramik"."""
    if r.jenis != "rak":
        dasar = r.nama or r.kode
    else:
        dasar = label(r)
    return f"{r.ruang.nama} · {dasar}" if r.ruang_id and r.ruang else dasar
