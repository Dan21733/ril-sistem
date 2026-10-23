"""Tiket data barang: admin mengusulkan harga jual, floor, alias, dan aktif/nonaktif.

- Pemilik membuka tiket jenis "barang" untuk satu akun admin barang, memilih apa
  yang boleh diusulkan (izin) dan barang mana (kategori/brand, kosong = semua).
- Admin hanya melihat nama barang, varian, harga jual, floor, alias, dan status
  aktif. Tidak pernah modal, harga beli, supplier, laba, atau stok.
- Isian admin disimpan sebagai usulan (TiketUsulan), tidak mengubah barang.
- Pemilik memeriksa per baris (semua tercentang di awal), dengan tanda:
  floor/harga di bawah modal, perubahan besar, floor di atas harga jual, dan nilai
  yang sudah berubah sejak diusulkan. Yang di-ACC diterapkan lewat layanan inti
  (tercatat di Log, alias ikut terkirim ke kasir); sisanya ditolak.
"""
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from pos.models import Item

from master.layanan import inti as layanan
from master.layanan.inti import Tolak, baca_angka, rapikan_alias, rapikan_teks, urut_alami
from master.models import ItemInfo, Tiket, TiketUsulan, VarianInfo

IZIN = {"harga": ("max_price", "min_price"), "alias": ("alias",), "aktif": ("aktif",)}
LABEL = dict(TiketUsulan.KOLOM)
PER_HALAMAN = 40
BATAS_PERSEN = 20  # perubahan harga lebih dari ini ditandai di halaman ACC


# ---------------------------------------------------------------------------
# Pembantu
# ---------------------------------------------------------------------------


def bersihkan_izin(izin):
    hasil = [k for k in IZIN if k in set(izin or [])]
    if not hasil:
        raise Tolak("Centang minimal satu yang boleh diusulkan (harga, alias, atau aktif).")
    return hasil


def bersihkan_saring(kategori=(), brand=()):
    rapi = lambda xs: sorted({rapikan_teks(x) for x in xs or [] if rapikan_teks(x)})
    return {"kategori": rapi(kategori), "brand": rapi(brand)}


def lingkup_teks(t):
    s = t.saring or {}
    bagian = []
    if s.get("kategori"):
        bagian.append("kategori " + ", ".join(s["kategori"]))
    if s.get("brand"):
        bagian.append("brand " + ", ".join(s["brand"]))
    return "; ".join(bagian) or "semua barang"


def izin_teks(t):
    label = dict(Tiket.IZIN_BARANG)
    return ", ".join(label[k] for k in t.izin if k in label)


def _barang(t, cari=""):
    """Baris item (tabel kasir) yang masuk lingkup tiket, tanpa barang di keranjang sampah."""
    qs = Item.objects.all()
    s = t.saring or {}
    if s.get("kategori"):
        qs = qs.filter(category__in=s["kategori"])
    if s.get("brand"):
        qs = qs.filter(brand__in=s["brand"])
    for kata in rapikan_teks(cari).split()[:6]:
        qs = qs.filter(Q(item_name__icontains=kata) | Q(variant__icontains=kata) | Q(category__icontains=kata)
                       | Q(brand__icontains=kata))
    return qs


def _info_varian(baris, hanya_aktif):
    kunci = {(b["item_name"], b["variant"]) for b in baris}
    nama = {k[0] for k in kunci}
    hasil = {}
    for v in VarianInfo.objects.filter(item_name__in=nama, sampah__isnull=True):
        if (v.item_name, v.variant) in kunci and (v.aktif or not hanya_aktif):
            hasil[(v.item_name, v.variant)] = v
    return hasil


def izin_efektif(pengguna, t):
    """Yang boleh diusulkan: izin di tiket DAN hak akun admin saat ini (hak bisa dicabut setelah tiket dibuka)."""
    from master.akun.akses import hak_pengguna
    from master.layanan.tiket import izin_barang_untuk
    punya = izin_barang_untuk(hak_pengguna(pengguna))
    return [i for i in t.izin if i in punya]


def _harus_bisa_diisi(pengguna, t):
    from master.layanan.tiket import _harus_bisa_diisi as cek
    if t.jenis != "barang":
        raise Tolak("Tiket ini bukan tiket data barang.")
    cek(pengguna, t)
    if not izin_efektif(pengguna, t):
        raise Tolak("Akun ini tidak punya hak mengusulkan apa pun di tiket ini.")


# ---------------------------------------------------------------------------
# Admin (aplikasi operasional)
# ---------------------------------------------------------------------------


def halaman(t, cari="", nomor=1, izin=None):
    """Barang untuk satu halaman isian admin, dikelompokkan per nama barang.

    Hasil: (kelompok, nomor, jumlah_halaman, jumlah_varian). Tanpa modal/stok.
    """
    izin = t.izin if izin is None else izin
    hanya_aktif = "aktif" not in izin
    baris = list(_barang(t, cari).values("item_name", "variant", "max_price", "min_price"))
    info = _info_varian(baris, hanya_aktif)
    baris = urut_alami([b for b in baris if (b["item_name"], b["variant"]) in info])
    total = len(baris)
    jumlah_halaman = max(1, -(-total // PER_HALAMAN))
    nomor = min(max(1, nomor), jumlah_halaman)
    potong = baris[(nomor - 1) * PER_HALAMAN: nomor * PER_HALAMAN]
    usul = {}
    for u in TiketUsulan.objects.filter(tiket=t, item_name__in={b["item_name"] for b in potong}):
        usul[(u.item_name, u.variant, u.kolom)] = u.baru
    alias = dict(ItemInfo.objects.filter(item_name__in={b["item_name"] for b in potong}).values_list("item_name", "alias"))
    kelompok = []
    for b in potong:
        if not kelompok or kelompok[-1]["nama"] != b["item_name"]:
            nama = b["item_name"]
            kelompok.append({"nama": nama, "alias": alias.get(nama, ""), "alias_usul": usul.get((nama, "*", "alias")),
                             "varian": []})
        v = info[(b["item_name"], b["variant"])]
        u = lambda k: usul.get((b["item_name"], b["variant"], k))
        aktif_usul = u("aktif")
        kelompok[-1]["varian"].append({
            "id": v.id, "variant": b["variant"], "harga": b["max_price"], "floor": b["min_price"], "aktif": v.aktif,
            "harga_usul": u("max_price"), "floor_usul": u("min_price"),
            "aktif_usul": None if aktif_usul is None else aktif_usul == "ya",
        })
    return kelompok, nomor, jumlah_halaman, total


def _upsert(t, *, varian, item_name, variant, kolom, lama, baru):
    lama, baru = layanan.teks_log(lama), layanan.teks_log(baru)
    kunci = dict(tiket=t, item_name=item_name, variant=variant, kolom=kolom)
    if lama == baru:
        return TiketUsulan.objects.filter(**kunci).delete()[0] > 0
    u, dibuat = TiketUsulan.objects.get_or_create(**kunci, defaults={"varian": varian, "lama": lama, "baru": baru})
    if dibuat:
        return True
    if (u.lama, u.baru) != (lama, baru):
        u.lama, u.baru = lama, baru
        u.save(update_fields=["lama", "baru", "diubah"])
        return True
    return False


def simpan(pengguna, t, data):
    """Simpan isian satu halaman sebagai usulan. Isian yang sama dengan nilai sekarang = tidak ada usulan.

    Baris yang salah dilewati (dilaporkan), baris lain tetap tersimpan.
    Hasil: (jumlah_berubah, daftar_galat).
    """
    _harus_bisa_diisi(pengguna, t)
    izin = izin_efektif(pengguna, t)
    ids = [int(x) for x in data.getlist("v") if str(x).isdigit()][:PER_HALAMAN * 2]
    nama_item = [rapikan_teks(x) for x in data.getlist("i")][:PER_HALAMAN * 2]
    lingkup = _barang(t)
    varian = {v.id: v for v in VarianInfo.objects.filter(pk__in=ids, sampah__isnull=True)}
    item = {(r["item_name"], r["variant"]): r for r in
            lingkup.filter(item_name__in={v.item_name for v in varian.values()} | set(nama_item))
                   .values("item_name", "variant", "max_price", "min_price")}
    berubah, galat = 0, []
    with transaction.atomic():
        for vid in ids:
            v = varian.get(vid)
            r = item.get((v.item_name, v.variant)) if v else None
            if r is None or (not v.aktif and "aktif" not in izin):
                galat.append("Barang di luar tiket ini dilewati.")
                continue
            nama = f"{v.item_name} / {v.variant}"
            if "harga" in izin:
                try:
                    harga = baca_angka(data.get(f"h_{vid}"), boleh_kosong=False)
                    floor = baca_angka(data.get(f"f_{vid}"), boleh_kosong=False)
                except Tolak as e:
                    galat.append(f"{nama}: {e}")
                    continue
                if harga < 0 or floor < 0:
                    galat.append(f"{nama}: harga tidak boleh minus.")
                    continue
                if floor > harga:
                    galat.append(f"{nama}: floor ({floor:,}) lebih tinggi dari harga jual ({harga:,}).".replace(",", "."))
                    continue
                berubah += _upsert(t, varian=v, item_name=v.item_name, variant=v.variant, kolom="max_price",
                                   lama=r["max_price"], baru=harga)
                berubah += _upsert(t, varian=v, item_name=v.item_name, variant=v.variant, kolom="min_price",
                                   lama=r["min_price"], baru=floor)
            if "aktif" in izin:
                berubah += _upsert(t, varian=v, item_name=v.item_name, variant=v.variant, kolom="aktif",
                                   lama=v.aktif, baru=data.get(f"ak_{vid}") == "1")
        if "alias" in izin:
            ada = {r[0] for r in item}
            alias = dict(ItemInfo.objects.filter(item_name__in=nama_item).values_list("item_name", "alias"))
            for nama in nama_item:
                if nama not in ada or f"al_{nama}" not in data:
                    continue
                berubah += _upsert(t, varian=None, item_name=nama, variant="*", kolom="alias",
                                   lama=alias.get(nama, ""), baru=rapikan_alias(data.get(f"al_{nama}")))
    return berubah, galat


def hapus_semua(pengguna, t):
    _harus_bisa_diisi(pengguna, t)
    return t.usulan.all().delete()[0]


def kirim(pengguna, t, catatan=""):
    _harus_bisa_diisi(pengguna, t)
    n = t.usulan.count()
    if not n:
        raise Tolak("Belum ada usulan di tiket ini.")
    t.status = "dikirim"
    t.dikirim_waktu = timezone.now()
    t.catatan_admin = rapikan_teks(catatan)[:500]
    t.save()
    layanan.catat(pengguna, objek="tiket", kolom="status", baru=f"Tiket #{t.id} dikirim: {n} usulan data barang")
    return n


# ---------------------------------------------------------------------------
# Pemilik (master)
# ---------------------------------------------------------------------------


def _angka(teks):
    try:
        return int(teks)
    except (TypeError, ValueError):
        return None


def rincian(t, *, lihat_modal, batas_persen=BATAS_PERSEN):
    """Baris usulan dengan nilai sekarang dan tanda. Modal hanya diisi kalau `lihat_modal`."""
    usulan = list(t.usulan.all())
    kunci = {(u.item_name, u.variant) for u in usulan if u.variant != "*"}
    item = {(r["item_name"], r["variant"]): r for r in Item.objects.filter(item_name__in={k[0] for k in kunci})
            .values("item_name", "variant", "max_price", "min_price", "cost_amount")}
    aktif = {(v.item_name, v.variant): v.aktif for v in VarianInfo.objects.filter(item_name__in={k[0] for k in kunci})}
    alias = dict(ItemInfo.objects.filter(item_name__in={u.item_name for u in usulan}).values_list("item_name", "alias"))
    usul = {(u.item_name, u.variant, u.kolom): u.baru for u in usulan}
    hasil = []
    for u in usulan:
        r = item.get((u.item_name, u.variant), {})
        if u.kolom in ("max_price", "min_price"):
            sekarang = layanan.teks_log(r.get(u.kolom))
        elif u.kolom == "aktif":
            sekarang = layanan.teks_log(aktif.get((u.item_name, u.variant)))
        else:
            sekarang = alias.get(u.item_name, "")
        tanda = []
        if u.variant != "*" and not r:
            tanda.append(("hilang", "Barang sudah tidak ada"))
        if sekarang != u.lama:
            tanda.append(("berubah", f"Sudah berubah sejak diusulkan (dulu {u.lama or 'kosong'})"))
        modal = r.get("cost_amount") if lihat_modal else None
        if u.kolom in ("max_price", "min_price") and r:
            baru, kini = _angka(u.baru), _angka(sekarang)
            harga = _angka(usul.get((u.item_name, u.variant, "max_price"))) if (u.item_name, u.variant, "max_price") in usul else r["max_price"]
            floor = _angka(usul.get((u.item_name, u.variant, "min_price"))) if (u.item_name, u.variant, "min_price") in usul else r["min_price"]
            if kini and baru is not None and abs(baru - kini) * 100 > batas_persen * kini:
                arah = "naik" if baru > kini else "turun"
                tanda.append(("besar", f"{arah} {abs(baru - kini) * 100 // kini}%"))
            if harga is not None and floor is not None and floor > harga:
                tanda.append(("terbalik", "Floor di atas harga jual"))
            if lihat_modal and modal and baru is not None and baru < modal:
                tanda.append(("rugi", "Di bawah modal"))
        hasil.append({"u": u, "label": LABEL.get(u.kolom, u.kolom), "sekarang": sekarang, "modal": modal,
                      "tanda": tanda, "angka": u.kolom in ("max_price", "min_price")})
    return hasil


def terapkan(pengguna, t, data, *, hak):
    """ACC usulan yang dicentang (`pilih`); sisanya ditolak. Harga/floor boleh diubah pemilik (`nilai_<id>`)."""
    from master.layanan.tiket import HAK_BUKA
    if HAK_BUKA not in hak:
        raise Tolak("Akun ini tidak boleh menyetujui tiket.")
    if t.jenis != "barang" or not t.bisa_diputus:
        raise Tolak("Tiket ini belum dikirim atau sudah diputuskan.")
    pilih = {int(x) for x in data.getlist("pilih") if str(x).isdigit()}
    berhasil = gagal = ditolak = 0
    catatan = f"Tiket #{t.id} dari {t.untuk.first_name or t.untuk.username}"
    with transaction.atomic():
        t = Tiket.objects.select_for_update().get(pk=t.pk)
        # Harga jual dulu baru floor, supaya urutan sama dengan isian admin.
        urutan = {"max_price": 0, "min_price": 1, "aktif": 2, "alias": 3}
        for u in sorted(t.usulan.select_related("varian"), key=lambda u: (u.item_name, u.variant, urutan[u.kolom])):
            if u.id not in pilih:
                u.hasil = "Tidak di-ACC"
                u.save(update_fields=["hasil"])
                ditolak += 1
                continue
            try:
                with transaction.atomic():
                    if u.kolom == "alias":
                        info, _ = ItemInfo.objects.get_or_create(item_name=u.item_name)
                        layanan.ubah_alias_item(pengguna, info, u.baru, hak=hak, catatan=catatan)
                    else:
                        if u.varian is None:
                            raise Tolak("Barang sudah tidak ada.")
                        nilai = data.get(f"nilai_{u.id}", u.baru) if u.kolom != "aktif" else u.baru
                        baru, _ = layanan.ubah_varian(pengguna, u.varian, u.kolom, nilai, hak=hak, catatan=catatan)
                        u.baru = layanan.teks_log(baru)
                u.diterapkan, u.hasil = True, "Diterapkan"
                berhasil += 1
            except Tolak as e:
                u.hasil = str(e)[:300]
                gagal += 1
            u.save(update_fields=["diterapkan", "hasil", "baru"])
        t.status = "selesai"
        t.diputus_oleh, t.diputus_waktu = pengguna.username, timezone.now()
        t.save()
        layanan.catat(pengguna, objek="tiket", kolom="status",
                      baru=f"Tiket #{t.id}: {berhasil} diterapkan, {gagal} gagal, {ditolak} tidak di-ACC")
    return berhasil, gagal, ditolak
