"""Semua perubahan data barang lewat sini, supaya selalu tercatat di Log.

Layar, impor Excel, dan perintah baris semuanya memanggil fungsi yang sama.
"""
import re
import uuid

from django.db import connection, transaction

from pos.models import Item

from master.models import ItemInfo, Log, MutasiStok, Rak, VarianInfo

VARIAN_KOSONG = "N/A"


class Tolak(Exception):
    """Perubahan ditolak; pesannya aman ditampilkan ke pengguna."""


# kolom -> (label, jenis, hak yang dibutuhkan)
KOLOM_ITEM = {
    "category": ("Kategori", "teks_wajib", "ubah_info"),
    "brand": ("Brand", "teks", "ubah_info"),
    "sku": ("SKU", "teks", "ubah_info"),
    "min_price": ("Harga floor", "angka", "ubah_harga"),
    "max_price": ("Harga jual", "angka", "ubah_harga"),
    "cost_amount": ("Modal", "angka_opsional", "ubah_modal"),
    "stock_alert": ("Batas stok", "angka_opsional", "ubah_info"),
}
KOLOM_INFO = {
    "kode_rak": ("Rak", "rak", "ubah_info"),
    "alias_tambahan": ("Alias tambahan", "teks", "ubah_info"),
    "aktif": ("Aktif", "boolean", "ubah_info"),
    "modal_rata": ("Modal rata", "angka_opsional", "ubah_modal"),
}
KOLOM_VARIAN = {**KOLOM_ITEM, **KOLOM_INFO}
KOLOM_VARIAN["cost_amount"] = ("Modal terakhir", "angka_opsional", "ubah_modal")
KOLOM_RAHASIA = {"cost_amount", "modal_rata", "pemasok"}


def rapikan_teks(nilai):
    return re.sub(r"\s+", " ", str(nilai if nilai is not None else "")).strip()


def rapikan_varian(nilai):
    teks = rapikan_teks(nilai)
    return teks or VARIAN_KOSONG


def rapikan_alias(nilai):
    """Alias disimpan sebagai daftar dipisah koma, tanpa kembar dan tanpa kosong."""
    hasil, sudah = [], set()
    for bagian in re.split(r"[,;\n]", str(nilai or "")):
        teks = rapikan_teks(bagian).lower()
        if teks and teks not in sudah:
            sudah.add(teks)
            hasil.append(teks)
    return ", ".join(hasil)


def baca_angka(nilai, boleh_kosong):
    if nilai is None or (isinstance(nilai, str) and not nilai.strip()):
        if boleh_kosong:
            return None
        raise Tolak("Angka wajib diisi.")
    if isinstance(nilai, bool):
        raise Tolak("Bukan angka.")
    if isinstance(nilai, (int, float)):
        if isinstance(nilai, float) and not nilai.is_integer():
            raise Tolak("Harus bilangan bulat, tanpa desimal.")
        angka = int(nilai)
    else:
        teks = str(nilai).strip().lower().replace("rp", "").replace(" ", "")
        # 1.450.000 atau 1,450,000 -> 1450000. Desimal tidak dipakai (rupiah bulat).
        if re.fullmatch(r"-?\d{1,3}([.,]\d{3})+", teks) or re.fullmatch(r"-?\d+", teks):
            angka = int(re.sub(r"[.,]", "", teks))
        elif re.fullmatch(r"-?\d+[.,]0+", teks):
            angka = int(re.split(r"[.,]", teks)[0])
        else:
            raise Tolak(f"'{nilai}' bukan angka yang dikenali.")
    if angka < 0:
        raise Tolak("Tidak boleh negatif.")
    if angka > 2_000_000_000:
        raise Tolak("Angka terlalu besar.")
    return angka


def baca_boolean(nilai):
    if isinstance(nilai, bool):
        return nilai
    teks = str(nilai if nilai is not None else "").strip().lower()
    if teks in {"1", "true", "ya", "y", "aktif", "yes"}:
        return True
    if teks in {"0", "false", "tidak", "t", "nonaktif", "no", "n"}:
        return False
    raise Tolak(f"'{nilai}' bukan ya/tidak.")


def teks_log(nilai):
    if nilai is None:
        return ""
    if isinstance(nilai, bool):
        return "ya" if nilai else "tidak"
    return str(nilai)


def catat(pengguna, *, aksi="ubah", sumber="layar", objek="barang", item_name="", variant="",
          kolom="", lama=None, baru=None, batch=None, catatan=""):
    return Log.objects.create(
        pengguna=pengguna if getattr(pengguna, "pk", None) else None,
        nama_pengguna=getattr(pengguna, "username", "") or "sistem",
        aksi=aksi,
        sumber=sumber,
        objek=objek,
        item_name=item_name,
        variant=variant,
        kolom=kolom,
        lama=teks_log(lama),
        baru=teks_log(baru),
        rahasia=kolom in KOLOM_RAHASIA,
        batch=batch,
        catatan=catatan,
    )


# ---------------------------------------------------------------------------
# Menjaga tabel master tetap berpasangan dengan tabel item milik POS
# ---------------------------------------------------------------------------


def sinkron_info():
    """Buat ItemInfo/VarianInfo untuk barang yang belum punya.

    Barang bisa bertambah dari luar master (mis. langsung di database), jadi
    pasangan ini diperiksa ulang, bukan diasumsikan lengkap.
    """
    pasangan = set(Item.objects.values_list("item_name", "variant"))
    ada = set(VarianInfo.objects.values_list("item_name", "variant"))
    kurang = pasangan - ada
    if kurang:
        modal = {(n, v): m for n, v, m in Item.objects.values_list("item_name", "variant", "cost_amount")}
        baru = [VarianInfo(item_name=n, variant=v, modal_rata=modal.get((n, v))) for n, v in kurang]
        VarianInfo.objects.bulk_create(baru, batch_size=500, ignore_conflicts=True)
    else:
        baru = []

    nama = {n for n, _ in pasangan}
    nama_ada = set(ItemInfo.objects.values_list("item_name", flat=True))
    item_baru = [ItemInfo(item_name=n) for n in nama - nama_ada]
    if item_baru:
        ItemInfo.objects.bulk_create(item_baru, batch_size=500, ignore_conflicts=True)
    return len(baru), len(item_baru)


def pastikan_info():
    # Varian di keranjang sampah punya VarianInfo tanpa baris item, jadi tidak ikut dihitung.
    hidup = VarianInfo.objects.filter(sampah__isnull=True)
    if Item.objects.count() != hidup.count() or (
        Item.objects.values("item_name").distinct().count()
        != ItemInfo.objects.filter(item_name__in=hidup.values("item_name")).count()
    ):
        sinkron_info()


# ---------------------------------------------------------------------------
# Ubah satu kolom satu varian
# ---------------------------------------------------------------------------


def _siapkan_nilai(kolom, nilai):
    label, jenis, _ = KOLOM_VARIAN[kolom]
    if jenis == "angka":
        return baca_angka(nilai, boleh_kosong=False)
    if jenis == "angka_opsional":
        return baca_angka(nilai, boleh_kosong=True)
    if jenis == "teks_wajib":
        teks = rapikan_teks(nilai)
        if not teks:
            raise Tolak(f"{label} wajib diisi.")
        return teks
    if jenis == "teks":
        if kolom == "alias_tambahan":
            return rapikan_alias(nilai)
        teks = rapikan_teks(nilai)
        return teks or None if kolom == "sku" else teks
    if jenis == "boolean":
        return baca_boolean(nilai)
    if jenis == "rak":
        kode = rapikan_teks(nilai)
        if not kode:
            return None
        if not Rak.objects.filter(kode=kode).exists():
            raise Tolak(f"Rak '{kode}' belum terdaftar.")
        return kode
    raise Tolak("Kolom tidak dikenal.")


def ubah_varian(pengguna, info, kolom, nilai, *, hak, sumber="layar", batch=None, catatan=""):
    """Ubah satu kolom milik satu varian. Mengembalikan (nilai_baru, berubah)."""
    if kolom not in KOLOM_VARIAN:
        raise Tolak("Kolom ini tidak bisa diubah dari sini.")
    label, _, hak_perlu = KOLOM_VARIAN[kolom]
    if hak_perlu not in hak:
        raise Tolak(f"Akun ini tidak boleh mengubah {label.lower()}.")
    baru = _siapkan_nilai(kolom, nilai)

    with transaction.atomic():
        if kolom in KOLOM_ITEM:
            baris = Item.objects.filter(item_name=info.item_name, variant=info.variant)
            lama = baris.values_list(kolom, flat=True).first()
            if not baris.exists():
                raise Tolak("Barang ini sudah tidak ada di tabel item.")
            if lama == baru or (lama in (None, "") and baru in (None, "")):
                return baru, False
            baris.update(**{kolom: baru})
            # Selama belum ada catatan barang masuk, modal rata mengikuti modal
            # terakhir. Begitu keduanya pernah dibedakan, masing-masing jalan sendiri.
            if kolom == "cost_amount" and info.modal_rata in (None, lama):
                rata_lama = info.modal_rata
                info.modal_rata = baru
                info.save(update_fields=["modal_rata"])
                if rata_lama != baru:
                    catat(pengguna, sumber=sumber, item_name=info.item_name, variant=info.variant,
                          kolom="modal_rata", lama=rata_lama, baru=baru, batch=batch,
                          catatan="mengikuti modal terakhir")
        elif kolom == "kode_rak":
            lama = info.rak_id
            if lama == baru:
                return baru, False
            info.rak_id = baru
            info.save(update_fields=["rak"])
        else:
            lama = getattr(info, kolom)
            if lama == baru:
                return baru, False
            setattr(info, kolom, baru)
            info.save(update_fields=[kolom])
        catat(pengguna, sumber=sumber, item_name=info.item_name, variant=info.variant,
              kolom=kolom, lama=lama, baru=baru, batch=batch, catatan=rapikan_teks(catatan)[:300])
        if kolom == "alias_tambahan":
            from master.layanan import alias_kasir
            alias_kasir.tulis([info.item_name])
    return baru, True


# ---------------------------------------------------------------------------
# Ubah harga massal
# ---------------------------------------------------------------------------

KOLOM_HARGA = ("min_price", "max_price", "cost_amount")
CARA_MASSAL = {"persen": "Naik/turun persen", "rupiah": "Naik/turun rupiah", "tetapkan": "Tetapkan ke angka"}
BULAT = (0, 100, 500, 1000)


def hitung_massal(lama, cara, angka, bulat):
    """Nilai baru untuk satu sel. `lama` kosong tidak dihitung kecuali cara 'tetapkan'."""
    if cara == "tetapkan":
        baru = angka
    elif lama is None:
        return None
    elif cara == "persen":
        baru = lama * (100 + angka) / 100
    else:
        baru = lama + angka
    if bulat:
        # Dibulatkan ke atas, supaya kenaikan tidak hilang oleh pembulatan.
        baru = -(-baru // bulat) * bulat
    return max(0, int(round(baru)))


def baca_parameter_massal(data, hak):
    kolom = data.get("kolom")
    if kolom not in KOLOM_HARGA:
        raise Tolak("Pilih kolom yang mau diubah.")
    if KOLOM_VARIAN[kolom][2] not in hak:
        raise Tolak(f"Akun ini tidak boleh mengubah {KOLOM_VARIAN[kolom][0].lower()}.")
    cara = data.get("cara")
    if cara not in CARA_MASSAL:
        raise Tolak("Pilih cara perubahan.")
    teks = str(data.get("angka") or "").strip().replace(" ", "")
    if cara == "persen":
        try:
            angka = float(teks.replace(",", ".").replace("%", ""))
        except ValueError:
            raise Tolak("Persen harus angka, misalnya 5 atau -2,5.") from None
        if not -90 <= angka <= 500:
            raise Tolak("Persen di luar batas wajar (-90 sampai 500).")
        if angka == 0:
            raise Tolak("Persen nol tidak mengubah apa-apa.")
    else:
        minus = teks.startswith("-")
        angka = baca_angka(teks.lstrip("+-"), boleh_kosong=False)
        if minus:
            if cara == "tetapkan":
                raise Tolak("Tidak boleh negatif.")
            angka = -angka
        if cara == "rupiah" and angka == 0:
            raise Tolak("Nol tidak mengubah apa-apa.")
    try:
        bulat = int(data.get("bulat") or 0)
    except ValueError:
        bulat = 0
    if bulat not in BULAT:
        raise Tolak("Pembulatan tidak dikenal.")
    return {"kolom": kolom, "cara": cara, "angka": angka, "bulat": bulat,
            "catatan": rapikan_teks(data.get("catatan"))[:300]}


def rencana_massal(queryset, param):
    """Daftar perubahan untuk queryset Item (belum menyimpan apa pun)."""
    kolom = param["kolom"]
    rencana = []
    for b in queryset.values("item_name", "variant", "min_price", "max_price", "cost_amount"):
        lama = b[kolom]
        baru = hitung_massal(lama, param["cara"], param["angka"], param["bulat"])
        if baru is None or baru == lama:
            continue
        sesudah = {**b, kolom: baru}
        rencana.append({
            "item_name": b["item_name"], "variant": b["variant"], "lama": lama, "baru": baru,
            "terbalik": (sesudah["max_price"] or 0) > 0 and (sesudah["min_price"] or 0) > sesudah["max_price"],
        })
    rencana.sort(key=lambda r: (r["item_name"].casefold(), r["variant"].casefold()))
    return rencana


def terapkan_massal(pengguna, queryset, param, *, hak):
    import uuid

    batch = uuid.uuid4()
    rencana = rencana_massal(queryset, param)
    info = {(v.item_name, v.variant): v for v in VarianInfo.objects.filter(
        item_name__in={r["item_name"] for r in rencana}
    )}
    jumlah = 0
    with transaction.atomic():
        for r in rencana:
            v = info.get((r["item_name"], r["variant"]))
            if v is None:
                continue
            _, berubah = ubah_varian(pengguna, v, param["kolom"], r["baru"], hak=hak,
                                     sumber="massal", batch=batch, catatan=param["catatan"])
            jumlah += int(berubah)
    return jumlah, batch


# ---------------------------------------------------------------------------
# Ubah di tingkat item (berlaku untuk semua variannya)
# ---------------------------------------------------------------------------


def ubah_alias_item(pengguna, item_info, nilai, *, hak, sumber="layar", batch=None, catatan=""):
    if "ubah_info" not in hak:
        raise Tolak("Akun ini tidak boleh mengubah alias.")
    baru = rapikan_alias(nilai)
    if item_info.alias == baru:
        return baru, False
    with transaction.atomic():
        lama = item_info.alias
        item_info.alias = baru
        item_info.save(update_fields=["alias"])
        catat(pengguna, sumber=sumber, item_name=item_info.item_name, variant="*",
              kolom="alias", lama=lama, baru=baru, batch=batch, catatan=rapikan_teks(catatan)[:300])
        from master.layanan import alias_kasir
        alias_kasir.tulis([item_info.item_name])
    return baru, True


def ubah_kolom_item(pengguna, item_name, kolom, nilai, *, hak):
    """Ubah kategori atau brand untuk semua varian satu item."""
    if kolom not in ("category", "brand"):
        raise Tolak("Kolom ini tidak bisa diubah di tingkat item.")
    if "ubah_info" not in hak:
        raise Tolak("Akun ini tidak boleh mengubah data barang.")
    baru = _siapkan_nilai(kolom, nilai)
    with transaction.atomic():
        baris = Item.objects.filter(item_name=item_name)
        lama = sorted({v or "" for v in baris.values_list(kolom, flat=True)})
        if lama == [baru or ""]:
            return baru, False
        baris.update(**{kolom: baru})
        catat(pengguna, item_name=item_name, variant="*", kolom=kolom,
              lama=" | ".join(lama), baru=baru)
    return baru, True


def ganti_nama_kelompok(pengguna, kolom, lama, baru, *, hak):
    """Ganti nama satu kategori atau brand di semua barang."""
    if kolom not in ("category", "brand"):
        raise Tolak("Hanya kategori atau brand.")
    if "ubah_info" not in hak:
        raise Tolak("Akun ini tidak boleh mengubah data barang.")
    baru = rapikan_teks(baru)
    if not baru:
        raise Tolak("Nama baru wajib diisi.")
    if baru == lama:
        return 0
    with transaction.atomic():
        jumlah = Item.objects.filter(**{kolom: lama}).update(**{kolom: baru})
        if jumlah:
            catat(pengguna, aksi="ganti_nama", objek="kategori" if kolom == "category" else "brand",
                  kolom=kolom, lama=lama, baru=baru, catatan=f"{jumlah} varian")
    return jumlah


# ---------------------------------------------------------------------------
# Ganti nama barang / varian
# ---------------------------------------------------------------------------
#
# Di tabel POS, kunci barang adalah (item_name, variant) dan nota merujuk ke
# kunci itu. Ganti nama karena itu bukan edit biasa: baris barang disalin ke
# nama baru, baris nota dipindahkan, lalu baris lama dihapus, semuanya dalam
# satu transaksi. Cara ini tidak bergantung pada ON UPDATE CASCADE.


def _kolom_tabel(nama_tabel):
    with connection.cursor() as cursor:
        deskripsi = connection.introspection.get_table_description(cursor, nama_tabel)
    return [kolom.name for kolom in deskripsi]


def _pindah_kunci(pasangan):
    """pasangan: daftar ((nama_lama, varian_lama), (nama_baru, varian_baru))."""
    q = connection.ops.quote_name
    kolom = _kolom_tabel("item")
    lain = [k for k in kolom if k not in ("item_name", "variant")]
    daftar_kolom = ", ".join(q(k) for k in ["item_name", "variant", *lain])
    daftar_pilih = ", ".join(["%s", "%s", *[q(k) for k in lain]])
    with connection.cursor() as cursor:
        for (nama_lama, varian_lama), (nama_baru, varian_baru) in pasangan:
            cursor.execute(
                f"INSERT INTO item ({daftar_kolom}) SELECT {daftar_pilih} FROM item "
                "WHERE item_name = %s AND variant = %s",
                [nama_baru, varian_baru, nama_lama, varian_lama],
            )
            cursor.execute(
                "UPDATE transaction_detail SET item_name = %s, variant = %s "
                "WHERE item_name = %s AND variant = %s",
                [nama_baru, varian_baru, nama_lama, varian_lama],
            )
            cursor.execute(
                "DELETE FROM item WHERE item_name = %s AND variant = %s",
                [nama_lama, varian_lama],
            )


def jumlah_baris_nota(item_name, variant=None):
    from pos.models import TransactionDetail

    qs = TransactionDetail.objects.filter(item_name=item_name)
    if variant is not None:
        qs = qs.filter(variant=variant)
    return qs.count()


def ganti_nama_item(pengguna, item_info, nama_baru, *, hak):
    if "ganti_nama" not in hak:
        raise Tolak("Akun ini tidak boleh mengganti nama barang.")
    nama_baru = rapikan_teks(nama_baru)
    nama_lama = item_info.item_name
    if not nama_baru:
        raise Tolak("Nama baru wajib diisi.")
    if nama_baru == nama_lama:
        return False
    if VarianInfo.objects.filter(item_name=nama_lama, sampah__isnull=False).exists():
        raise Tolak("Barang ini punya varian di keranjang sampah. Pulihkan atau hapus permanen dulu sebelum ganti nama.")
    if ItemInfo.objects.filter(item_name=nama_baru).exists() or Item.objects.filter(item_name=nama_baru).exists():
        raise Tolak(f"Nama '{nama_baru}' sudah dipakai barang lain. Menggabung dua barang belum didukung.")
    with transaction.atomic():
        varian = list(Item.objects.filter(item_name=nama_lama).values_list("variant", flat=True))
        nota = jumlah_baris_nota(nama_lama)
        _pindah_kunci([((nama_lama, v), (nama_baru, v)) for v in varian])
        VarianInfo.objects.filter(item_name=nama_lama).update(item_name=nama_baru)
        item_info.item_name = nama_baru
        item_info.save(update_fields=["item_name"])
        from master.layanan import alias_kasir
        alias_kasir.ganti_nama(nama_lama, nama_baru)
        catat(pengguna, aksi="ganti_nama", item_name=nama_baru, variant="*", kolom="item_name",
              lama=nama_lama, baru=nama_baru,
              catatan=f"{len(varian)} varian, {nota} baris nota ikut diperbarui")
    return True


def ganti_nama_varian(pengguna, info, varian_baru, *, hak):
    if "ganti_nama" not in hak:
        raise Tolak("Akun ini tidak boleh mengganti nama varian.")
    varian_baru = rapikan_varian(varian_baru)
    varian_lama = info.variant
    if varian_baru == varian_lama:
        return False
    if Item.objects.filter(item_name=info.item_name, variant=varian_baru).exists():
        raise Tolak(f"Varian '{varian_baru}' sudah ada di barang ini.")
    if VarianInfo.objects.filter(item_name=info.item_name, variant=varian_baru).exclude(pk=info.pk).exists():
        raise Tolak(f"Varian '{varian_baru}' ada di keranjang sampah. Pulihkan atau hapus permanen dulu.")
    with transaction.atomic():
        nota = jumlah_baris_nota(info.item_name, varian_lama)
        _pindah_kunci([((info.item_name, varian_lama), (info.item_name, varian_baru))])
        info.variant = varian_baru
        info.save(update_fields=["variant"])
        catat(pengguna, aksi="ganti_nama", item_name=info.item_name, variant=varian_baru,
              kolom="variant", lama=varian_lama, baru=varian_baru,
              catatan=f"{nota} baris nota ikut diperbarui")
    return True


# ---------------------------------------------------------------------------
# Tambah barang / varian
# ---------------------------------------------------------------------------


def tambah_varian(pengguna, *, item_name, variant, category, brand="", min_price, max_price,
                  cost_amount=None, in_stock=0, stock_alert=None, sku=None, kode_rak=None,
                  hak, sumber="layar", batch=None):
    if not {"ubah_info", "ubah_harga"} <= set(hak):
        raise Tolak("Menambah barang butuh hak mengubah data dan harga.")
    item_name = rapikan_teks(item_name)
    variant = rapikan_varian(variant)
    category = rapikan_teks(category)
    if not item_name:
        raise Tolak("Nama barang wajib diisi.")
    if not category:
        raise Tolak("Kategori wajib diisi.")
    if Item.objects.filter(item_name=item_name, variant=variant).exists():
        raise Tolak("Barang dengan nama dan varian itu sudah ada.")
    if VarianInfo.objects.filter(item_name=item_name, variant=variant).exists():
        raise Tolak("Barang dengan nama dan varian itu ada di keranjang sampah. Pulihkan dari menu Sampah, "
                    "atau hapus permanen dulu.")
    harga_floor = baca_angka(min_price, boleh_kosong=False)
    harga_jual = baca_angka(max_price, boleh_kosong=False)
    modal = baca_angka(cost_amount, boleh_kosong=True) if "ubah_modal" in hak else None
    stok = baca_angka(in_stock, boleh_kosong=True) or 0
    batas = baca_angka(stock_alert, boleh_kosong=True)
    rak = None
    if kode_rak:
        rak = Rak.objects.filter(kode=rapikan_teks(kode_rak)).first()
        if rak is None:
            raise Tolak(f"Rak '{kode_rak}' belum terdaftar.")
    with transaction.atomic():
        Item.objects.create(
            item_name=item_name, variant=variant, category=category, brand=rapikan_teks(brand),
            min_price=harga_floor, max_price=harga_jual, cost_amount=modal, in_stock=stok,
            stock_alert=batas, sku=rapikan_teks(sku) or None, moka_id=None,
            ecommerce_item=False, track_stock=True, track_alert=batas is not None,
        )
        ItemInfo.objects.get_or_create(item_name=item_name)
        info = VarianInfo.objects.create(item_name=item_name, variant=variant, rak=rak, modal_rata=modal)
        catat(pengguna, aksi="tambah", sumber=sumber, item_name=item_name, variant=variant,
              baru=f"floor {harga_floor}, jual {harga_jual}", batch=batch)
    return info


# ---------------------------------------------------------------------------
# Stok
# ---------------------------------------------------------------------------

# 'beli' dan 'buka_dus' hanya dibuat oleh alurnya sendiri, tidak bisa dipilih untuk penyesuaian manual.
JENIS_MUTASI = {k: v for k, v in MutasiStok.JENIS if k not in ("beli", "buka_dus", "retur_beli", "transfer_keluar", "transfer_masuk", "jual_manual", "impor", "retur_jual")}


def sesuaikan_stok(pengguna, info, *, cara, angka, jenis, catatan="", hak, lokasi=None):
    """Ubah stok satu varian dan catat di riwayat stok.

    cara: "tetapkan" (hasil hitung fisik), "tambah", atau "kurang".
    Mengembalikan (stok_baru, selisih). Stok minus diperbolehkan lewat "kurang".
    """
    if "ubah_stok" not in hak:
        raise Tolak("Akun ini tidak boleh mengubah stok.")
    if jenis not in JENIS_MUTASI:
        raise Tolak("Pilih alasan perubahan stok.")
    if cara not in ("tetapkan", "tambah", "kurang"):
        raise Tolak("Cara perubahan stok tidak dikenal.")
    nilai = baca_angka(angka, boleh_kosong=False)
    catatan = rapikan_teks(catatan)[:300]
    if jenis == "lain" and not catatan:
        raise Tolak("Alasan 'Lainnya' wajib diberi catatan.")
    with transaction.atomic():
        baris = Item.objects.select_for_update().filter(item_name=info.item_name, variant=info.variant)
        sekarang = baris.values_list("in_stock", flat=True).first()
        if not baris.exists():
            raise Tolak("Barang ini sudah tidak ada di tabel item.")
        sebelum = sekarang or 0
        if cara == "tetapkan":
            sesudah = nilai
        elif cara == "tambah":
            sesudah = sebelum + nilai
        else:
            sesudah = sebelum - nilai
        selisih = sesudah - sebelum
        if selisih == 0 and sekarang is not None:
            return sesudah, 0
        baris.update(in_stock=sesudah)
        MutasiStok.objects.create(
            pengguna=pengguna if getattr(pengguna, "pk", None) else None,
            nama_pengguna=getattr(pengguna, "username", "") or "sistem",
            varian=info, item_name=info.item_name, variant=info.variant, jenis=jenis,
            jumlah=selisih, stok_sebelum=sebelum, stok_sesudah=sesudah, catatan=catatan,
            lokasi=lokasi if lokasi is not None and lokasi.jenis == "gudang" else None,
        )
        if lokasi is not None:
            from master.layanan.lokasi import ubah_gudang
            ubah_gudang(info, lokasi, selisih)
    return sesudah, selisih


def samakan_varian(pengguna, item_info, acuan, kolom, *, hak):
    """Salin floor / harga jual / modal terakhir satu varian ke semua varian lain barang itu.

    Tiap sel lewat ubah_varian, jadi aturan hak dan log sama dengan edit satu per satu.
    Modal rata tidak disentuh: itu hasil hitungan pembelian tiap varian.
    """
    kolom = [k for k in KOLOM_HARGA if k in kolom]
    if not kolom:
        raise Tolak("Pilih kolom yang mau disamakan.")
    if acuan.item_name != item_info.item_name:
        raise Tolak("Varian acuan bukan milik barang ini.")
    sumber = Item.objects.filter(item_name=acuan.item_name, variant=acuan.variant).values(*kolom).first()
    if sumber is None:
        raise Tolak("Varian acuan sudah tidak ada di tabel item.")
    batch = uuid.uuid4()
    n = 0
    with transaction.atomic():
        lain = VarianInfo.objects.filter(item_name=item_info.item_name, sampah__isnull=True).exclude(pk=acuan.pk)
        for info in lain:
            for k in kolom:
                _, berubah = ubah_varian(pengguna, info, k, sumber[k], hak=hak, batch=batch,
                                         catatan=f"disamakan dengan varian {acuan.variant}")
                n += berubah
    return n


def kunci_alami(teks):
    """Urutan yang enak dibaca: tidak peduli huruf besar-kecil, dan angka diurut
    sebagai angka (CW 53 sebelum CW 415). Spasi diabaikan, jadi 'Cw420' dan
    'CW 427' berurutan."""
    rapat = re.sub(r"\s+", "", str(teks or "").casefold())
    return [int(b) if b.isdigit() else b for b in re.split(r"(\d+)", rapat)]


def urut_alami(baris, nama="item_name", varian="variant"):
    """Urutkan daftar dict menurut nama lalu varian, angka sebagai angka (4 MM sebelum 12 MM)."""
    return sorted(baris, key=lambda b: (kunci_alami(b[nama]), kunci_alami(b.get(varian, ""))))
