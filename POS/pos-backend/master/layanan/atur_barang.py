"""Atur ulang barang dan varian sekaligus (27 Okt 2026).

Tiga cara menyusun rencana, satu cara menerapkan:
- pindah: varian terpilih pindah ke barang lain (baru atau yang sudah ada), nama varian tetap.
- pisah:  tiap varian terpilih jadi barang sendiri "<nama barang> <varian>", tanpa varian.
- gabung: beberapa barang jadi varian dari satu barang. Nama varian = nama barang asal tanpa
          awalan yang sama (mis. "Keramik 50 X 50 Adana Grey" -> "Adana Grey").

Rencana selalu diperiksa dulu (periksa) dan ditampilkan ke pemilik; baru diterapkan setelah
disetujui. Penerapan memakai jalur yang sama dengan ganti nama (inti._pindah_kunci): baris
item, baris nota kasir, retur, dan mutasi kasir ikut pindah; VarianInfo (ID tetap) ikut,
jadi stok per gudang, riwayat stok, PO, dan opname tetap terhubung.
"""
from django.db import transaction

from pos.models import Item

from master.layanan import inti
from master.layanan.inti import Tolak, rapikan_teks, rapikan_varian
from master.models import ItemInfo, PemasokBarang, VarianInfo

HAK = "ganti_nama"


def _varian_aktif(ids):
    """VarianInfo yang masih punya baris item (bukan di keranjang sampah), urut sesuai permintaan."""
    ada = {v.pk: v for v in VarianInfo.objects.filter(pk__in=ids, sampah__isnull=True)}
    return [ada[i] for i in ids if i in ada]


def _kata(teks):
    return (teks or "").split()


def _awalan_sama(daftar_nama):
    """Kata-kata awal yang sama di semua nama (huruf besar/kecil diabaikan)."""
    kata = [_kata(n) for n in daftar_nama if n]
    if not kata:
        return 0
    n = 0
    while all(len(k) > n for k in kata) and len({k[n].lower() for k in kata}) == 1:
        n += 1
    return n


def _tanpa_awalan(nama, n):
    sisa = " ".join(_kata(nama)[n:])
    return sisa or nama


def _baris(v, item, varian):
    return {"id": v.pk, "asal_item": v.item_name, "asal_varian": v.variant,
            "item": rapikan_teks(item), "varian": rapikan_varian(varian)}


def susun(jenis, *, varian_ids=(), item_ids=(), tujuan=""):
    """Rencana awal (belum diperiksa). Mengembalikan daftar baris."""
    tujuan = rapikan_teks(tujuan)
    if jenis == "pindah":
        if not tujuan:
            raise Tolak("Isi nama barang tujuan.")
        return [_baris(v, tujuan, v.variant) for v in _varian_aktif(list(varian_ids))]
    if jenis == "pisah":
        return [_baris(v, f"{v.item_name} {v.variant}" if v.variant != inti.VARIAN_KOSONG else v.item_name,
                       inti.VARIAN_KOSONG) for v in _varian_aktif(list(varian_ids))]
    if jenis == "gabung":
        if not tujuan:
            raise Tolak("Isi nama barang gabungan.")
        nama = list(ItemInfo.objects.filter(pk__in=list(item_ids)).values_list("item_name", flat=True))
        if not nama:
            return []
        n = _awalan_sama(nama if len(nama) > 1 else nama + [tujuan])
        hasil = []
        for v in VarianInfo.objects.filter(item_name__in=nama, sampah__isnull=True).order_by("item_name", "variant"):
            dasar = _tanpa_awalan(v.item_name, n)
            if v.item_name.lower() == tujuan.lower():
                dasar = ""                                   # barang tujuan sendiri ikut dipilih: variannya tetap
            varian = dasar if v.variant == inti.VARIAN_KOSONG else f"{dasar} {v.variant}".strip()
            hasil.append(_baris(v, tujuan, varian))
        return hasil
    raise Tolak("Jenis pengaturan tidak dikenal.")


def periksa(baris):
    """Lengkapi tiap baris dengan masalah (menghalangi) dan catatan (perlu diketahui)."""
    from pos.models import TransactionDetail
    ids = [int(b["id"]) for b in baris]
    varian = {v.pk: v for v in VarianInfo.objects.filter(pk__in=ids, sampah__isnull=True)}
    asal = {(v.item_name, v.variant) for v in varian.values()}
    item_ada = set(Item.objects.values_list("item_name", flat=True).distinct())
    item_ada_kecil = {n.lower(): n for n in item_ada}
    kunci_ada = set(VarianInfo.objects.values_list("item_name", "variant"))
    nota = {}
    for n, v in TransactionDetail.objects.filter(item_name__in={k[0] for k in asal}).values_list("item_name", "variant"):
        nota[(n, v)] = nota.get((n, v), 0) + 1
    stok = {(r["item_name"], r["variant"]): r for r in Item.objects.filter(
        item_name__in={k[0] for k in asal}).values("item_name", "variant", "in_stock", "max_price")}
    punya_varian = {n for n, v in asal if v != inti.VARIAN_KOSONG}
    tujuan_dipakai = {}
    hasil = []
    for b in baris:
        v = varian.get(int(b["id"]))
        r = {"id": int(b["id"]), "item": rapikan_teks(b.get("item")), "varian": rapikan_varian(b.get("varian")),
             "masalah": [], "catatan": []}
        if v is None:
            r.update(asal_item="?", asal_varian="?", berubah=False)
            r["masalah"].append("Varian ini sudah tidak ada atau ada di keranjang sampah.")
            hasil.append(r)
            continue
        r.update(asal_item=v.item_name, asal_varian=v.variant)
        s = stok.get((v.item_name, v.variant)) or {}
        r["stok"], r["harga"], r["nota"] = s.get("in_stock"), s.get("max_price"), nota.get((v.item_name, v.variant), 0)
        # Ikut penulisan nama barang yang sudah ada (huruf besar/kecil), supaya tidak jadi dua barang.
        if r["item"].lower() in item_ada_kecil:
            r["item"] = item_ada_kecil[r["item"].lower()]
        kunci = (r["item"], r["varian"])
        r["berubah"] = kunci != (v.item_name, v.variant)
        if not r["item"]:
            r["masalah"].append("Nama barang kosong.")
        if len(r["item"]) > 255 or len(r["varian"]) > 255:
            r["masalah"].append("Nama terlalu panjang.")
        if r["berubah"] and kunci in kunci_ada:
            r["masalah"].append(f"Sudah ada barang {r['item']} / {inti_varian(r['varian'])}.")
        if kunci in tujuan_dipakai:
            r["masalah"].append(f"Sama dengan baris {tujuan_dipakai[kunci] + 1}: ubah salah satu nama variannya.")
        tujuan_dipakai.setdefault(kunci, len(hasil))
        if r["berubah"] and r["item"] in item_ada and r["item"] != v.item_name:
            r["catatan"].append(f"Masuk ke barang yang sudah ada ({r['item']}).")
        if r["berubah"] and v.item_name in punya_varian and r["varian"] == inti.VARIAN_KOSONG and r["item"] != v.item_name:
            r["catatan"].append("Jadi barang tanpa varian.")
        if r["berubah"] and r["nota"]:
            r["catatan"].append(f"{r['nota']} baris nota kasir ikut memakai nama baru.")
        hasil.append(r)
    return hasil


def inti_varian(v):
    return "tanpa varian" if v == inti.VARIAN_KOSONG else v


def terapkan(pengguna, baris, *, hak):
    """Terapkan rencana yang sudah diperiksa. Semua berhasil, atau tidak sama sekali."""
    if HAK not in hak:
        raise Tolak("Akun ini tidak boleh mengganti nama atau memindah varian.")
    hasil = periksa(baris)
    salah = [(i, r) for i, r in enumerate(hasil) if r["masalah"]]
    if salah:
        i, r = salah[0]
        raise Tolak(f"Baris {i + 1}: {r['masalah'][0]}")
    pindah = [r for r in hasil if r["berubah"]]
    if not pindah:
        return 0, set()
    asal_nama = {r["asal_item"] for r in pindah}
    tujuan_nama = {r["item"] for r in pindah}
    with transaction.atomic():
        info_asal = {i.item_name: i for i in ItemInfo.objects.filter(item_name__in=asal_nama)}
        inti._pindah_kunci([((r["asal_item"], r["asal_varian"]), (r["item"], r["varian"])) for r in pindah])
        for r in pindah:
            VarianInfo.objects.filter(pk=r["id"]).update(item_name=r["item"], variant=r["varian"])
            info, baru = ItemInfo.objects.get_or_create(item_name=r["item"])
            sumber = info_asal.get(r["asal_item"])
            if baru and sumber is not None:
                # Barang baru mewarisi alias dan catatan supplier dari barang asalnya.
                if sumber.alias:
                    info.alias = sumber.alias
                    info.save(update_fields=["alias"])
                PemasokBarang.objects.bulk_create([
                    PemasokBarang(item_info=info, supplier_id=p.supplier_id, catatan=p.catatan, dibuat_oleh=p.dibuat_oleh)
                    for p in sumber.pemasok.all()])
            inti.catat(pengguna, aksi="ganti_nama", item_name=r["item"], variant=r["varian"], kolom="atur ulang",
                       lama=f"{r['asal_item']} / {inti_varian(r['asal_varian'])}",
                       baru=f"{r['item']} / {inti_varian(r['varian'])}",
                       catatan=f"{r['nota']} baris nota ikut diperbarui" if r["nota"] else "")
        from master.layanan import alias_kasir
        alias_kasir.tulis(asal_nama | tujuan_nama)
    return len(pindah), tujuan_nama


def ubah_aktif(pengguna, varian_ids, aktif, *, hak):
    """Aktif/nonaktifkan banyak varian sekaligus."""
    n = 0
    with transaction.atomic():
        for v in _varian_aktif(list(varian_ids)):
            _, berubah = inti.ubah_varian(pengguna, v, "aktif", "1" if aktif else "0", hak=hak)
            n += 1 if berubah else 0
    return n
