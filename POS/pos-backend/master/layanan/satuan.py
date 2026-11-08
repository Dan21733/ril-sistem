"""Jual per satuan dengan kelipatan (keputusan pemilik 6 Nov 2026).

Barang seperti fiber pagar, seng plat roll, kawat loket, amplas, karpet talang dijual per
meter tetapi pembeli bisa minta 1,5 m, dan sisa potongan (80 cm, 20 cm) dijual murah.
Input harga bebas tidak boleh, dan tabel nota POS menyimpan jumlah sebagai bilangan bulat.

Jalan keluarnya: varian diberi `satuan` + `kelipatan`. Dengan satuan "m" dan kelipatan 10,
satu angka di database = 0,1 m: stok 500 = 50 m, nota jumlah 15 = 1,5 m, harga 2.500 =
Rp 25.000 per meter. Semua layar menampilkan satuan aslinya; isi tabel POS tetap bulat.

Masa uji coba (Nov–Des 2026): nota lama barang yang diubah tidak dihitung ulang (data uji,
akan direset). Stok, harga, modal, dan stok per lokasi dikonversi saat satuan dipasang.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from master.layanan.inti import Tolak, catat

SATUAN = [("", "per buah (biasa)"), ("m", "meter"), ("kg", "kg"), ("l", "liter")]
KELIPATAN = [(1, "utuh (1)"), (10, "0,1"), (100, "0,01")]
NAMA_SATUAN = dict(SATUAN)


def format_jumlah(jumlah, satuan="", kelipatan=1):
    """Jumlah tersimpan -> teks tampilan: (15, "m", 10) -> "1,5 m"; (3, "", 1) -> "3"."""
    if jumlah is None:
        return ""
    k = int(kelipatan or 1)
    if k <= 1:
        teks = f"{int(jumlah):,}".replace(",", ".")
    else:
        nilai = Decimal(int(jumlah)) / Decimal(k)
        teks = f"{nilai.normalize():f}".replace(".", ",")
    return f"{teks} {satuan}".strip() if satuan else teks


def angka_teks(jumlah, kelipatan=1):
    """Jumlah tersimpan -> angka per satuan tanpa nama satuan: (15, 10) -> "1,5"."""
    return format_jumlah(jumlah, "", kelipatan)


def baca_jumlah(teks, kelipatan=1):
    """Ketikan per satuan ("1,5", "2") -> jumlah tersimpan bulat (15, 20). Biasa: angka bulat."""
    k = int(kelipatan or 1)
    bersih = str(teks if teks is not None else "").strip().replace(" ", "")
    if k <= 1:
        return int(bersih.replace(".", "")) if bersih.replace(".", "").lstrip("-").isdigit() else None
    bersih = bersih.replace(".", "").replace(",", ".") if bersih.count(",") else bersih
    try:
        nilai = Decimal(bersih) * k
    except Exception:
        return None
    if not nilai.is_finite() or nilai != nilai.to_integral_value():
        # 1,55 m pada kelipatan 0,1 m tidak bisa disimpan; jangan dibulatkan diam-diam.
        return None
    return int(nilai)


def baca_harga(teks, kelipatan=1):
    """Harga ketikan per satuan ("25.000") -> harga tersimpan per kelipatan (2500)."""
    digit = "".join(c for c in str(teks or "") if c.isdigit())
    if digit == "":
        return None
    return _bagi(int(digit), int(kelipatan or 1))


def varian_satuan(info):
    """(satuan, kelipatan) efektif satu VarianInfo; kelipatan 1 kalau satuannya biasa."""
    s = getattr(info, "satuan", "") or ""
    return s, (int(getattr(info, "kelipatan", 1) or 1) if s else 1)


def label(satuan, kelipatan=1):
    """Teks pengaturan: ("m", 10) -> "per m, kelipatan 0,1 m"; ("", 1) -> "per buah"."""
    if not satuan:
        return "per buah"
    if int(kelipatan or 1) <= 1:
        return f"per {satuan}"
    return f"per {satuan}, kelipatan {format_jumlah(1, satuan, kelipatan)}"


def harga_per_satuan(harga, kelipatan=1):
    """Harga tersimpan (per kelipatan) -> harga per satuan."""
    if harga is None:
        return None
    return int(harga) * int(kelipatan or 1)


def _bagi(nilai, pembagi):
    if nilai is None:
        return None
    return int((Decimal(int(nilai)) / Decimal(pembagi)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _penghalang(varian_ids):
    """Dokumen yang masih berjalan dan menyimpan jumlah barang ini dalam satuan lama."""
    from master.models import OpnameBaris, PembelianBaris, PermintaanAmbil, TiketBaris, TransferBaris
    cek = [
        (PembelianBaris.objects.filter(varian_id__in=varian_ids, pembelian__status__in=["dibuat", "menunggu"]),
         "pesanan pembelian yang belum diterima"),
        (OpnameBaris.objects.filter(varian_id__in=varian_ids, opname__status__in=["draf", "diajukan"]),
         "stok opname yang belum selesai"),
        (TransferBaris.objects.filter(varian_id__in=varian_ids, transfer__status__in=["draf", "dikirim"]),
         "transfer antar toko yang belum diterima"),
        (TiketBaris.objects.filter(varian_id__in=varian_ids, tiket__status__in=["terbuka", "dikirim"]),
         "tugas gudang yang belum selesai"),
        (PermintaanAmbil.objects.filter(varian_id__in=varian_ids, status="menunggu"),
         "permintaan ambil gudang yang belum diambil"),
    ]
    return [nama for qs, nama in cek if qs.exists()]


def atur(pengguna, item_name, satuan, kelipatan):
    """Pasang satuan + kelipatan untuk semua varian satu barang, lalu konversi angka tersimpannya.

    Kelipatan naik (1 -> 10): stok x10, harga/modal :10 (dibulatkan ke rupiah).
    Kelipatan turun (10 -> 1): stok :10 (harus pas), harga/modal x10.
    """
    from pos.models import Item

    from master.models import StokLokasi, VarianInfo
    satuan = (satuan or "").strip()
    if satuan not in NAMA_SATUAN:
        raise Tolak("Satuan tidak dikenal.")
    try:
        kelipatan = int(kelipatan or 1)
    except (TypeError, ValueError):
        raise Tolak("Kelipatan tidak dikenal.")
    if kelipatan not in dict(KELIPATAN):
        raise Tolak("Kelipatan tidak dikenal.")
    if not satuan:
        kelipatan = 1
    daftar = list(VarianInfo.objects.filter(item_name=item_name))
    if not daftar:
        raise Tolak("Barang tidak ditemukan.")
    halangan = _penghalang([v.id for v in daftar if v.kelipatan != kelipatan])
    if halangan:
        raise Tolak("Selesaikan dulu " + ", ".join(halangan) + " untuk barang ini; jumlahnya masih dalam satuan lama.")
    berubah = 0
    with transaction.atomic():
        for v in VarianInfo.objects.select_for_update().filter(pk__in=[x.pk for x in daftar]):
            lama_k = v.kelipatan or 1
            if v.satuan == satuan and lama_k == kelipatan:
                continue
            item = Item.objects.select_for_update().filter(item_name=v.item_name, variant=v.variant).first()
            if kelipatan != lama_k:
                if kelipatan % lama_k == 0:
                    kali, bagi = kelipatan // lama_k, 1
                elif lama_k % kelipatan == 0:
                    kali, bagi = 1, lama_k // kelipatan
                else:
                    raise Tolak("Kelipatan tidak bisa dikonversi.")
                lokasi = list(StokLokasi.objects.select_for_update().filter(varian=v))
                stok_lama = item.in_stock if item else None
                for angka in [stok_lama] + [s.jumlah for s in lokasi]:
                    if angka is not None and bagi > 1 and angka % bagi:
                        raise Tolak(f"Stok {v.item_name} / {v.variant} ({format_jumlah(angka, satuan, lama_k)}) "
                                    f"tidak pas ke kelipatan baru. Rapikan stoknya dulu.")
                if item:
                    lama = {"in_stock": item.in_stock, "max_price": item.max_price, "min_price": item.min_price,
                            "cost_amount": item.cost_amount}
                    baru = {
                        "in_stock": None if item.in_stock is None else item.in_stock * kali // bagi,
                        "max_price": _bagi(item.max_price, kali) * bagi if item.max_price is not None else None,
                        "min_price": _bagi(item.min_price, kali) * bagi if item.min_price is not None else None,
                        "cost_amount": _bagi(item.cost_amount, kali) * bagi if item.cost_amount is not None else None,
                    }
                    Item.objects.filter(item_name=v.item_name, variant=v.variant).update(**baru)
                    for kolom in baru:
                        if lama[kolom] != baru[kolom]:
                            catat(pengguna, objek="varian", item_name=v.item_name, variant=v.variant, kolom=kolom,
                                  lama=lama[kolom], baru=baru[kolom], catatan="konversi satuan")
                for s in lokasi:
                    StokLokasi.objects.filter(pk=s.pk).update(jumlah=s.jumlah * kali // bagi)
                # Isi buka dus yang HASIL-nya barang ini (mis. 1 sak -> 50 kg) ikut satuan baru.
                from master.models import Konversi
                for kv in Konversi.objects.select_for_update().filter(ke=v):
                    if bagi > 1 and kv.isi % bagi:
                        raise Tolak(f"Isi buka dus {kv.dari.item_name} -> {v.item_name} tidak pas ke kelipatan baru.")
                    Konversi.objects.filter(pk=kv.pk).update(isi=kv.isi * kali // bagi)
                if v.modal_rata is not None:
                    v.modal_rata = _bagi(v.modal_rata, kali) * bagi
            catat(pengguna, objek="varian", item_name=v.item_name, variant=v.variant, kolom="satuan",
                  lama=label(v.satuan, lama_k), baru=label(satuan, kelipatan))
            v.satuan, v.kelipatan = satuan, kelipatan
            v.save(update_fields=["satuan", "kelipatan", "modal_rata"])
            berubah += 1
    return berubah


# ---------------------------------------------------------------------------
# Tahap 3 (9 Nov 2026): baca/tampil jumlah & harga per varian di semua layar
# gudang, PO, transfer. `x` boleh VarianInfo, baris yang punya `.varian`, atau dict.
# ---------------------------------------------------------------------------

def satuan_dari(x):
    """(satuan, kelipatan efektif) dari varian / baris ber-varian / dict; (\"\", 1) kalau tidak ada."""
    if x is None:
        return "", 1
    if isinstance(x, dict):
        s = x.get("satuan") or ""
        return s, (int(x.get("kelipatan") or 1) if s else 1)
    if hasattr(x, "satuan"):
        return varian_satuan(x)
    v = getattr(x, "varian", None)
    return varian_satuan(v) if v is not None else ("", 1)


def baca_qty(nilai, x, nama="", boleh_kosong=False):
    """Jumlah yang diketik orang (per satuan: "12,5") -> jumlah tersimpan (125). Tolak kalau bukan kelipatan."""
    from master.layanan.inti import baca_angka
    s, k = satuan_dari(x)
    if k <= 1:
        return baca_angka(nilai, boleh_kosong=boleh_kosong)
    if nilai is None or not str(nilai).strip():
        if boleh_kosong:
            return None
        raise Tolak(f"{nama + ': ' if nama else ''}jumlah wajib diisi.")
    hasil = baca_jumlah(nilai, k)
    if hasil is None:
        raise Tolak(f"{nama + ': ' if nama else ''}jumlah harus kelipatan {format_jumlah(1, s, k)} (mis. 12,5).")
    return hasil


def baca_harga_qty(nilai, x, boleh_kosong=False):
    """Harga per satuan yang diketik (Rp 25.000/m) -> harga tersimpan per kelipatan (2.500)."""
    from master.layanan.inti import baca_angka
    s, k = satuan_dari(x)
    if k <= 1:
        return baca_angka(nilai, boleh_kosong=boleh_kosong)
    if nilai is None or not str(nilai).strip():
        if boleh_kosong:
            return None
        raise Tolak("Harga wajib diisi.")
    return baca_harga(nilai, k)
