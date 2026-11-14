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
from django.db import models, transaction
from django.utils import timezone

from pos.models import Item

from master.layanan import inti as layanan
from master.layanan.inti import Tolak, baca_angka, rapikan_teks
from master.models import Opname, OpnameBaris, OpnameRak, VarianInfo


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
    from master.layanan.satuan import baca_qty
    mentah = {k[7:]: v for k, v in data.items() if k.startswith("hitung_") and str(v).strip() and k[7:].isdigit()}
    # Barang per meter/kg: hitungan diketik per satuan ("12,5"), disimpan per kelipatan.
    info_isian = {str(v.pk): v for v in VarianInfo.objects.filter(pk__in=list(mentah))}
    isian = {}
    for kunci, nilai in mentah.items():
        v = info_isian.get(kunci)
        isian[kunci] = baca_qty(nilai, v, nama=f"{v.item_name} / {v.variant}" if v else "")
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


# ---------------------------------------------------------------------------
# Hitung + posisi rak dari tablet (13 Nov 2026)
# ---------------------------------------------------------------------------

BATAS_KIRIM = 400


def kode_rak_baru(teks):
    """Kode rak yang diketik karyawan: huruf/angka/strip, huruf besar, maks 20."""
    import re
    kode = re.sub(r"\s+", "", str(teks or "")).upper()[:20]
    if kode and not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-/]*", kode):
        raise Tolak(f"Kode rak \"{teks}\" hanya boleh huruf, angka, titik, strip.")
    return kode


def _hitung_ulang(b):
    """Total baris = jumlah semua rak. Baris tanpa rincian rak lagi dihapus."""
    total = sum(b.per_rak.values_list("jumlah", flat=True))
    if not b.per_rak.exists():
        b.delete()
        return None
    OpnameBaris.objects.filter(pk=b.pk).update(hitung=total, waktu_hitung=timezone.now())
    return total


def simpan_entri(pengguna, t, entri):
    """Antrian tablet untuk tugas hitung (bukan sesi). Tiap entri {kunci, varian, rak, jumlah | hapus}.

    Hitungan per (barang, rak) ditimpa kalau diketik ulang. Stok sistem diambil diam-diam saat barang itu
    PERTAMA kali dihitung di tugas ini; selisih yang diterapkan saat ACC = total semua rak - stok itu.
    """
    from master.layanan.lokasi import stok_di
    from master.layanan.satuan import baca_qty
    from master.layanan.sesi_opname import rak_lokasi
    from master.layanan.tiket import _harus_bisa_diisi
    from master.models import Rak
    _harus_bisa_diisi(pengguna, t)
    if not isinstance(entri, list) or len(entri) > BATAS_KIRIM:
        raise Tolak("Isi kiriman tidak terbaca.")
    lokasi, toko = lokasi_tugas(t), t_toko(t)
    boleh = set(lingkup_tugas(t).values_list("id", flat=True))
    rak_sini = {r.kode: r for r in rak_lokasi(lokasi)}
    ids = {int(e.get("varian")) for e in entri if isinstance(e, dict) and str(e.get("varian", "")).isdigit()}
    varian = VarianInfo.objects.in_bulk(ids)
    ok, gagal = [], []
    with transaction.atomic():
        op = draf(pengguna, toko=toko, lokasi=lokasi, tiket=t)
        punya_stok = op.toko.stok_di_kasir
        for e in entri:
            kunci = str((e or {}).get("kunci", ""))[:80] if isinstance(e, dict) else ""
            try:
                if not isinstance(e, dict) or not str(e.get("varian", "")).isdigit():
                    raise Tolak("Barang tidak dikenal.")
                v = varian.get(int(e["varian"]))
                if v is None or not v.aktif:
                    raise Tolak("Barang tidak ditemukan atau sudah nonaktif.")
                if v.pk not in boleh:
                    raise Tolak(f"{v.item_name} di luar lingkup tugas ini.")
                kode = kode_rak_baru(e.get("rak"))
                rak, rak_baru = rak_sini.get(kode), ""
                if kode and rak is None:
                    if Rak.objects.filter(pk=kode).exists():
                        raise Tolak(f"Rak {kode} ada di lokasi lain, bukan di {lokasi.nama if lokasi else 'Toko'}.")
                    rak_baru = kode
                b = OpnameBaris.objects.filter(opname=op, varian=v).first()
                if b is not None and not b.per_rak.exists():
                    # Hitungan lama (sebelum ada rincian rak): jadikan baris "tanpa rak".
                    OpnameRak.objects.create(baris=b, rak=None, jumlah=b.hitung, waktu=b.waktu_hitung)
                saring = {"rak": rak, "rak_baru": rak_baru}
                if e.get("hapus"):
                    if b is not None:
                        b.per_rak.filter(**saring).delete()
                        _hitung_ulang(b)
                else:
                    jumlah = baca_qty(e.get("jumlah"), v, nama=v.item_name)
                    if jumlah < 0:
                        raise Tolak("Jumlah tidak boleh minus.")
                    if b is None:
                        b = OpnameBaris.objects.create(
                            opname=op, varian=v, item_name=v.item_name, variant=v.variant, hitung=0,
                            stok_saat_hitung=(stok_di(v, op.lokasi) or 0) if punya_stok else 0,
                            waktu_hitung=timezone.now())
                    OpnameRak.objects.update_or_create(baris=b, **saring, defaults={"jumlah": jumlah,
                                                                                   "waktu": timezone.now()})
                    _hitung_ulang(b)
                ok.append(kunci)
            except Tolak as tolak:
                gagal.append({"kunci": kunci, "pesan": str(tolak)})
    return {"ok": ok, "gagal": gagal}


def hitungan_tablet(t):
    """Hitungan tugas ini untuk tablet: [{varian, rak (kode), jumlah}]. Baris lama tanpa rincian = tanpa rak."""
    op = Opname.objects.filter(tiket=t).first()
    if op is None:
        return []
    hasil = []
    for b in op.baris.prefetch_related("per_rak__rak"):
        rincian = list(b.per_rak.all())
        if not rincian:
            hasil.append({"varian": b.varian_id, "rak": "", "jumlah": b.hitung})
        hasil += [{"varian": b.varian_id, "rak": r.kode, "jumlah": r.jumlah} for r in rincian]
    return hasil


def perbarui_posisi(t):
    """Perbarui posisi rak di tugas ini? Dicentang pemilik saat membuat tugas (bawaan nyala)."""
    if t is None or t.sesi_id:
        return False                     # sesi opname mengatur rak sendiri saat sesi di-ACC
    return bool((t.saring or {}).get("posisi", True))


def _terapkan_posisi(pengguna, op, baris):
    """Rak barang dari hasil hitung tugas ini (hanya untuk lokasi tugas ini).

    Rak utama tetap kalau masih ada hitungannya, atau kalau rak utamanya di lokasi lain. Kalau rak utama di
    lokasi ini tapi barangnya tidak ditemukan di sana lagi, rak utama pindah ke rak dengan jumlah terbanyak.
    Rak tambahan di lokasi ini diganti dengan hasil hitung; rak tambahan di lokasi lain tidak disentuh.
    Rak baru yang diketik karyawan dibuat di lokasi tugas.
    """
    from master.models import Rak, VarianRak
    lokasi = lokasi_tugas(op.tiket) if op.tiket_id else op.lokasi
    lokasi_id = lokasi.pk if lokasi else None
    ditempatkan = 0
    for b in baris:
        per = {}
        for r in b.per_rak.select_related("rak"):
            if r.jumlah <= 0 or not r.kode:
                continue
            if r.rak_id is None:
                rak, baru = Rak.objects.get_or_create(pk=r.rak_baru, defaults={"nama": r.rak_baru, "tempat": lokasi})
                if baru:
                    layanan.catat(pengguna, aksi="tambah", objek="rak", kolom="rak", baru=r.rak_baru,
                                  catatan=f"Dari hitung tugas #{op.tiket_id}")
                r.rak = rak
                OpnameRak.objects.filter(pk=r.pk).update(rak=rak)
            per[r.rak.kode] = per.get(r.rak.kode, 0) + r.jumlah
        if not per:
            continue
        v = VarianInfo.objects.select_related("rak").get(pk=b.varian_id)
        utama = v.rak_id
        rak_utama_di_sini = bool(v.rak_id and v.rak and v.rak.tempat_id in (lokasi_id, None))
        if utama not in per and (utama is None or rak_utama_di_sini):
            utama = max(per, key=lambda k: (per[k], k))
        if utama != v.rak_id:
            VarianInfo.objects.filter(pk=v.pk).update(rak_id=utama)
            layanan.catat(pengguna, objek="varian", kolom="rak", lama=v.rak_id or "", baru=utama,
                          catatan=f"{v.item_name} / {v.variant}: posisi dari hitung tugas #{op.tiket_id}")
        VarianRak.objects.filter(varian=v, rak__tempat_id=lokasi_id).exclude(rak_id__in=list(per)).delete()
        for kode in per:
            if kode != utama:
                VarianRak.objects.get_or_create(varian=v, rak_id=kode)
        VarianRak.objects.filter(varian=v, rak_id=utama).delete()
        ditempatkan += 1
    return ditempatkan


def hapus_baris(pengguna, baris_id):
    OpnameBaris.objects.filter(pk=baris_id, opname__status="draf", opname__dibuat_oleh=pengguna.username).delete()


def ajukan(pengguna, catatan="", toko=None, lokasi=None, tiket=None, dihitung_oleh=None):
    op = draf(pengguna, buat=False, toko=toko, lokasi=lokasi, tiket=tiket)
    if op is None or not op.baris.exists():
        raise Tolak("Belum ada barang yang dihitung.")
    if dihitung_oleh is not None:
        dihitung_oleh = rapikan_teks(dihitung_oleh)[:200]
        if not dihitung_oleh:
            raise Tolak("Isi siapa saja yang menghitung.")
        op.dihitung_oleh = dihitung_oleh
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
    baris = list(op.baris.select_related("varian__rak").prefetch_related("per_rak__rak"))
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
        x.rincian_rak = [{"kode": r.kode or "", "jumlah": r.jumlah, "baru": r.rak_id is None and bool(r.rak_baru)}
                         for r in x.per_rak.all()]
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
        if perbarui_posisi(op.tiket):
            _terapkan_posisi(pengguna, op, [b for b in op.baris.all() if b.id in pilih])
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
    if s.get("varian"):
        syarat |= Q(pk__in=s["varian"])
    return qs.filter(syarat) if syarat else qs


def ringkas_rak(kode):
    """Daftar kotak rak dipendekkan per rak induk: semua 42 kotak rak G -> "G (semua 42 kotak)"."""
    from master.models import Rak
    kode = list(kode)
    ada = {r.kode: r for r in Rak.objects.filter(kode__in=kode)}
    per, sendiri = {}, []
    for k in kode:
        r = ada.get(k)
        (per.setdefault(r.induk, []) if r is not None and r.induk else sendiri).append(k)
    total = dict(Rak.objects.filter(induk__in=list(per)).values_list("induk").annotate(n=models.Count("kode"))
                 .values_list("induk", "n")) if per else {}
    teks = []
    for induk, isi in per.items():
        if len(isi) == total.get(induk) and len(isi) > 1:
            teks.append(f"{induk} (semua {len(isi)} kotak)")
        elif len(isi) > 3:
            teks.append(f"{induk}: {len(isi)} kotak")
        else:
            teks += isi
    return ", ".join(teks + sendiri)


def lingkup_teks(t):
    s = t.saring or {}
    l = lokasi_tugas(t)
    bagian = [f"di {l.nama}" if l else ""]
    if s.get("rak"):
        bagian.append("rak " + ringkas_rak(s["rak"]))
    if s.get("kategori"):
        bagian.append("kategori " + ", ".join(s["kategori"]))
    if s.get("varian"):
        bagian.append(f"hitung ulang {len(s['varian'])} barang")
    if not s.get("rak") and not s.get("kategori") and not s.get("varian"):
        bagian.append("semua barang")
    return " · ".join(x for x in bagian if x)


def kirim_tugas(pengguna, t, catatan="", dihitung_oleh=None):
    from master.layanan.tiket import _harus_bisa_diisi
    if t.jenis != "hitung":
        raise Tolak("Tiket ini bukan tugas hitung.")
    _harus_bisa_diisi(pengguna, t)
    with transaction.atomic():
        op = ajukan(pengguna, catatan, toko=t_toko(t), lokasi=lokasi_tugas(t), tiket=t, dihitung_oleh=dihitung_oleh)
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
