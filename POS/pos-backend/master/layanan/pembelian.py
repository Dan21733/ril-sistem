"""Pembelian: PO -> barang datang -> ACC -> pembayaran.

Aturan yang dipegang:
- Stok, hutang, dan modal baru berubah saat ACC, tidak sebelumnya.
- Tidak ada penerimaan sebagian: kalau barang datang kurang, jumlahnya diubah
  ke yang datang dan wajib diberi catatan.
- Angka harga diketik seperti tertulis di faktur; pilihan PPN di faktur yang
  menentukan cara memisahkannya.
- Selama toko belum PKP, PPN yang dibayar ke supplier ikut menjadi modal.
- Ongkir dibagi ke tiap baris menurut nilainya dan ikut menjadi modal. Ongkir
  masuk hutang hanya kalau ditagih supplier di faktur yang sama.
- Stok dan modal hanya berubah untuk toko yang stoknya ada di kasir. Toko lain
  hanya dicatat hutangnya, sampai toko itu punya kasir sendiri.
- Retur hanya untuk pembelian yang sudah di-ACC: stok turun, hutang berkurang,
  dan modal rata dihitung mundur dengan modal pembelian itu.
"""
from datetime import date, datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from pos.models import Item

from master.layanan import inti as layanan
from master.layanan.inti import Tolak, baca_angka, rapikan_teks
from master.models import (
    MutasiStok,
    Pembayaran,
    Pembelian,
    PembelianBaris,
    Retur,
    ReturBaris,
    Supplier,
    TarifPPN,
    Toko,
    VarianInfo,
)


def baca_tanggal(nilai, *, wajib=False, label="Tanggal"):
    teks = str(nilai or "").strip()
    if not teks:
        if wajib:
            raise Tolak(f"{label} wajib diisi.")
        return None
    try:
        return datetime.strptime(teks, "%Y-%m-%d").date()
    except ValueError:
        raise Tolak(f"{label} tidak dikenali.") from None


def tarif_pada(tanggal):
    tarif = TarifPPN.objects.filter(mulai__lte=tanggal).order_by("-mulai").first()
    return tarif.persen if tarif else None


def pisah_pajak(jumlah, mode, persen):
    """(dpp, ppn) untuk satu baris. `jumlah` = harga x qty seperti di faktur."""
    if mode == "tanpa" or not persen:
        return jumlah, 0
    r = Decimal(persen) / Decimal(100)
    if mode == "termasuk":
        dpp = int((Decimal(jumlah) / (1 + r)).quantize(Decimal("1")))
        return dpp, jumlah - dpp
    ppn = int((Decimal(jumlah) * r).quantize(Decimal("1")))
    return jumlah, ppn


def hitung(po, *, tarif=None, toko_pkp=None):
    """Hitung baris dan total PO tanpa menyimpan. Dipakai untuk pratinjau dan ACC."""
    tanggal = po.tanggal_faktur or po.tanggal_terima or po.tanggal_pesan
    if tarif is None:
        tarif = po.tarif_ppn if po.status == "diterima" else (tarif_pada(tanggal) or Decimal(0))
    if toko_pkp is None:
        toko_pkp = po.toko_pkp if po.status == "diterima" else _toko(po).pkp_pada(tanggal)
    baris = []
    total_dpp = total_ppn = 0
    for b in po.baris.all():
        qty = b.jumlah_terima if b.jumlah_terima is not None else b.jumlah_pesan
        dpp, ppn = pisah_pajak(b.harga * qty, po.mode_ppn, tarif)
        # PKP boleh mengkreditkan PPN berfaktur, jadi PPN bukan modal. Belum PKP: PPN ikut modal.
        dasar_modal = dpp if toko_pkp else dpp + ppn
        baris.append({"objek": b, "qty": qty, "dpp": dpp, "ppn": ppn, "jumlah": dpp + ppn, "dasar_modal": dasar_modal,
                      "ongkir": 0, "berubah": b.jumlah_terima is not None and b.jumlah_terima != b.jumlah_pesan})
        total_dpp += dpp
        total_ppn += ppn
    _bagi_ongkir(baris, po.ongkir or 0)
    for h in baris:
        h["modal_satuan"] = int(round((h["dasar_modal"] + h["ongkir"]) / h["qty"])) if h["qty"] > 0 else 0
    ongkir_hutang = (po.ongkir or 0) if po.ongkir_ke_supplier else 0
    return {"baris": baris, "total_dpp": total_dpp, "total_ppn": total_ppn, "ongkir": po.ongkir or 0,
            "ongkir_hutang": ongkir_hutang, "total": total_dpp + total_ppn + ongkir_hutang,
            "tarif": tarif, "toko_pkp": toko_pkp}


def _bagi_ongkir(baris, ongkir):
    """Bagi ongkir ke baris yang datang, sebanding nilainya. Sisa pembulatan masuk baris terakhir."""
    kena = [h for h in baris if h["qty"] > 0]
    if not ongkir or not kena:
        return
    kunci = "dasar_modal" if sum(h["dasar_modal"] for h in kena) > 0 else "qty"
    dasar = sum(h[kunci] for h in kena)
    terpakai = 0
    for h in kena[:-1]:
        h["ongkir"] = int(round(Decimal(ongkir) * h[kunci] / dasar))
        terpakai += h["ongkir"]
    kena[-1]["ongkir"] = ongkir - terpakai


def _nomor_baru(tanggal):
    awalan = f"PO-{tanggal:%y%m}-"
    terakhir = Pembelian.objects.filter(nomor__startswith=awalan).order_by("-nomor").values_list("nomor", flat=True).first()
    urut = int(terakhir.rsplit("-", 1)[1]) + 1 if terakhir else 1
    return f"{awalan}{urut:04d}"


def harga_beli_terakhir(info):
    b = (PembelianBaris.objects.filter(varian=info, pembelian__status="diterima")
         .order_by("-pembelian__acc_waktu").first())
    if b:
        return b.harga
    return Item.objects.filter(item_name=info.item_name, variant=info.variant).values_list("cost_amount", flat=True).first()


def _toko(po):
    return po.toko or Toko.objects.filter(stok_di_kasir=True).first() or Toko.objects.first()


def _isi_lokasi(po, b, data):
    """Lokasi tujuan per baris dari formulir: lokasi_<id baris>, atau lokasi_semua. Kosong = Toko."""
    if not stok_ikut(po):
        return
    from master.layanan.lokasi import baca
    nilai = data.get(f"lokasi_{b.id}")
    if nilai in (None, ""):
        nilai = data.get("lokasi_semua")
    lokasi = baca(nilai)
    b.lokasi = lokasi if lokasi.jenis == "gudang" else None
    b.save(update_fields=["lokasi"])


def stok_ikut(po):
    """Apakah PO ini mengubah stok dan modal (hanya toko yang stoknya ada di kasir)."""
    t = _toko(po)
    return bool(t and t.stok_di_kasir)


def buat(pengguna, *, supplier_id, tanggal_pesan, catatan="", toko=None):
    from master.layanan import toko as svc_toko
    supplier = Supplier.objects.filter(pk=supplier_id, aktif=True).first()
    if supplier is None:
        raise Tolak("Pilih supplier.")
    toko = toko if isinstance(toko, Toko) else svc_toko.ambil(toko)
    tanggal = baca_tanggal(tanggal_pesan, wajib=True, label="Tanggal pesan")
    with transaction.atomic():
        po = Pembelian.objects.create(
            nomor=_nomor_baru(tanggal), toko=toko, supplier=supplier, tanggal_pesan=tanggal,
            catatan=rapikan_teks(catatan), dibuat_oleh=pengguna.username,
            mode_ppn="termasuk" if supplier.pkp else "tanpa",
        )
        layanan.catat(pengguna, aksi="tambah", sumber="pembelian", objek="pembelian", kolom="pembelian",
                      baru=f"{po.nomor} - {supplier.nama}")
    return po


def _harus(po, *status):
    if po.status not in status:
        raise Tolak(f"Tidak bisa dilakukan saat status {po.get_status_display()}.")


def tambah_baris(po, *, varian_id, jumlah, harga):
    _harus(po, "dibuat")
    info = VarianInfo.objects.filter(pk=varian_id).first()
    if info is None:
        raise Tolak("Pilih barang dari hasil pencarian.")
    qty = baca_angka(jumlah, boleh_kosong=False)
    if qty <= 0:
        raise Tolak("Jumlah harus lebih dari nol.")
    if po.baris.filter(varian=info).exists():
        raise Tolak("Barang itu sudah ada di PO ini. Ubah jumlahnya di tabel.")
    return PembelianBaris.objects.create(
        pembelian=po, varian=info, item_name=info.item_name, variant=info.variant,
        jumlah_pesan=qty, harga=baca_angka(harga, boleh_kosong=False),
    )


def simpan_baris(po, data):
    """Simpan jumlah pesan dan harga semua baris dari form (qty_<id>, harga_<id>)."""
    _harus(po, "dibuat")
    with transaction.atomic():
        for b in po.baris.all():
            if f"hapus_{b.id}" in data:
                b.delete()
                continue
            qty = baca_angka(data.get(f"qty_{b.id}"), boleh_kosong=False)
            if qty <= 0:
                raise Tolak(f"{b.item_name}: jumlah harus lebih dari nol.")
            b.jumlah_pesan = qty
            b.harga = baca_angka(data.get(f"harga_{b.id}"), boleh_kosong=False)
            b.save(update_fields=["jumlah_pesan", "harga"])


def catat_datang(pengguna, po, data):
    """Barang datang: isi jumlah yang datang dan data faktur, lalu minta ACC."""
    _harus(po, "dibuat")
    if not po.baris.exists():
        raise Tolak("PO ini belum punya barang.")
    mode = data.get("mode_ppn")
    if mode not in dict(Pembelian.MODE_PPN):
        raise Tolak("Pilih cara PPN ditulis di faktur.")
    tanggal_terima = baca_tanggal(data.get("tanggal_terima"), wajib=True, label="Tanggal barang datang")
    tanggal_faktur = baca_tanggal(data.get("tanggal_faktur"), label="Tanggal faktur")
    if mode != "tanpa" and tarif_pada(tanggal_faktur or tanggal_terima) is None:
        raise Tolak("Tarif PPN untuk tanggal itu belum diisi di Pengaturan.")
    catatan = rapikan_teks(data.get("catatan_terima"))
    ongkir = baca_angka(data.get("ongkir"), boleh_kosong=True) or 0
    if ongkir < 0:
        raise Tolak("Ongkir tidak boleh minus.")
    with transaction.atomic():
        ada_beda = False
        ada_isi = False
        for b in po.baris.all():
            terima = baca_angka(data.get(f"terima_{b.id}"), boleh_kosong=False)
            b.jumlah_terima = terima
            b.harga = baca_angka(data.get(f"harga_{b.id}"), boleh_kosong=False)
            b.save(update_fields=["jumlah_terima", "harga"])
            _isi_lokasi(po, b, data)
            ada_beda = ada_beda or terima != b.jumlah_pesan
            ada_isi = ada_isi or terima > 0
        if not ada_isi:
            raise Tolak("Tidak ada barang yang datang. Kalau memang batal, batalkan PO-nya.")
        if ada_beda and not catatan:
            raise Tolak("Jumlah yang datang beda dari pesanan. Isi catatan perubahannya.")
        po.status = "menunggu"
        po.tanggal_terima = tanggal_terima
        po.diterima_oleh = pengguna.username
        po.catatan_terima = catatan
        po.nomor_faktur = rapikan_teks(data.get("nomor_faktur"))[:60]
        po.tanggal_faktur = tanggal_faktur
        po.jatuh_tempo = baca_tanggal(data.get("jatuh_tempo"), label="Jatuh tempo")
        po.mode_ppn = mode
        po.nomor_faktur_pajak = rapikan_teks(data.get("nomor_faktur_pajak"))[:60]
        po.ongkir = ongkir
        po.ongkir_ke_supplier = data.get("ongkir_tagihan") != "terpisah"
        po.save()
        layanan.catat(pengguna, sumber="pembelian", objek="pembelian", kolom="status",
                      lama="Dipesan", baru="Menunggu ACC", catatan=po.nomor)


def tambah_di_luar_pesanan(pengguna, po, *, varian_id, jumlah):
    """Gudang mencatat barang yang datang tapi tidak ada di pesanan. Harganya diisi pemilik sebelum ACC."""
    _harus(po, "dibuat")
    if po.hitung_ulang:
        raise Tolak("Selesaikan hitung ulang dulu.")
    info = VarianInfo.objects.filter(pk=varian_id or 0, sampah__isnull=True).first()
    if info is None:
        raise Tolak("Pilih barang dari hasil pencarian.")
    qty = baca_angka(jumlah, boleh_kosong=False)
    if qty <= 0:
        raise Tolak("Jumlah harus lebih dari nol.")
    if po.baris.filter(varian=info).exists():
        raise Tolak("Barang itu sudah ada di daftar pesanan. Isi jumlahnya di daftar.")
    b = PembelianBaris.objects.create(
        pembelian=po, varian=info, item_name=info.item_name, variant=info.variant, jumlah_pesan=0,
        jumlah_terima=qty, jumlah_hitung1=qty, harga=harga_beli_terakhir(info) or 0, di_luar_pesanan=True)
    layanan.catat(pengguna, sumber="pembelian", objek="pembelian", kolom="di luar pesanan",
                  baru=f"{po.nomor}: {info.item_name} / {info.variant} x {qty}")
    return b


def hapus_di_luar_pesanan(pengguna, po, baris_id):
    _harus(po, "dibuat")
    po.baris.filter(pk=baris_id, di_luar_pesanan=True).delete()


def catat_datang_gudang(pengguna, po, data):
    """Barang datang dicatat gudang tanpa melihat jumlah pesanan (terima buta).

    Tahap 1: semua baris diisi. Baris pesanan yang jumlahnya beda dari pesanan diminta
    dihitung ulang sekali (gudang tidak diberi tahu angka seharusnya, lebih atau kurangnya).
    Tahap 2: hitungan ulang dipakai dan catatan wajib. Lalu menunggu ACC pemilik.
    Hasil: "ulang" (masih ada yang harus dihitung ulang) atau "selesai".
    """
    _harus(po, "dibuat")
    if not po.baris.exists():
        raise Tolak("Pesanan ini belum punya barang.")
    surat_jalan = rapikan_teks(data.get("surat_jalan"))[:60]
    catatan = rapikan_teks(data.get("catatan_terima"))
    ulang = set(po.hitung_ulang or [])
    with transaction.atomic():
        po = Pembelian.objects.select_for_update().get(pk=po.pk)
        if ulang:
            baris = list(po.baris.filter(pk__in=ulang))
            for b in baris:
                mentah = data.get(f"terima_{b.id}")
                if mentah is None or not str(mentah).strip():
                    raise Tolak(f"{b.item_name} / {b.variant}: hitungan ulang belum diisi.")
                b.jumlah_terima = baca_angka(mentah, boleh_kosong=False)
                if b.jumlah_terima < 0:
                    raise Tolak("Jumlah tidak boleh minus.")
                b.save(update_fields=["jumlah_terima"])
            if not catatan:
                raise Tolak("Isi catatan untuk pemilik, mis. ada yang rusak, sobek, atau memang kurang.")
        else:
            ada_isi = False
            for b in po.baris.all():
                mentah = data.get(f"terima_{b.id}")
                if mentah is None or not str(mentah).strip():
                    raise Tolak(f"{b.item_name} / {b.variant}: jumlah yang datang belum diisi. Isi 0 kalau tidak datang.")
                terima = baca_angka(mentah, boleh_kosong=False)
                if terima < 0:
                    raise Tolak("Jumlah tidak boleh minus.")
                b.jumlah_terima = b.jumlah_hitung1 = terima
                b.save(update_fields=["jumlah_terima", "jumlah_hitung1"])
                _isi_lokasi(po, b, data)
                ada_isi = ada_isi or terima > 0
            if not ada_isi:
                raise Tolak("Tidak ada barang yang datang. Kalau pesanannya batal, beri tahu pemilik.")
            po.tanggal_terima = baca_tanggal(data.get("tanggal_terima"), label="Tanggal barang datang") or timezone.localdate()
            po.catatan_terima = " ".join(x for x in (f"Surat jalan {surat_jalan}." if surat_jalan else "", catatan) if x)
            beda = [b.id for b in po.baris.filter(di_luar_pesanan=False) if b.jumlah_terima != b.jumlah_pesan]
            if beda:
                po.hitung_ulang = beda
                po.diterima_oleh = (pengguna.first_name or pengguna.username)[:150]
                po.save()
                layanan.catat(pengguna, sumber="pembelian", objek="pembelian", kolom="hitung ulang",
                              baru=f"{po.nomor}: {len(beda)} barang diminta dihitung ulang")
                return "ulang"
        if ulang:
            po.catatan_terima = " ".join(x for x in (po.catatan_terima, f"Hitung ulang: {catatan}") if x)
        po.hitung_ulang = []
        po.status = "menunggu"
        po.diterima_oleh = (pengguna.first_name or pengguna.username)[:150]
        po.save()
        layanan.catat(pengguna, sumber="pembelian", objek="pembelian", kolom="status",
                      lama="Dipesan", baru="Menunggu ACC", catatan=f"{po.nomor}: dicatat gudang")
    return "selesai"


def sisa_belum_datang(po):
    """Baris pesanan yang datang kurang dari pesanan: [(baris, sisa)]."""
    return [(b, b.jumlah_pesan - (b.jumlah_terima or 0)) for b in po.baris.filter(di_luar_pesanan=False)
            if b.jumlah_terima is not None and b.jumlah_terima < b.jumlah_pesan]


def _buat_lanjutan(pengguna, po, sisa):
    """PO baru untuk sisa yang belum datang (kiriman sebagian): pemasok, toko, harga sama."""
    hari_ini = timezone.localdate()
    baru = Pembelian.objects.create(
        nomor=_nomor_baru(hari_ini), toko=po.toko, supplier=po.supplier, tanggal_pesan=hari_ini,
        catatan=f"Sisa dari {po.nomor}", dibuat_oleh=pengguna.username, mode_ppn=po.mode_ppn, lanjutan_dari=po)
    for b, n in sisa:
        PembelianBaris.objects.create(pembelian=baru, varian=b.varian, item_name=b.item_name, variant=b.variant,
                                      jumlah_pesan=n, harga=b.harga)
    layanan.catat(pengguna, aksi="tambah", sumber="pembelian", objek="pembelian", kolom="pembelian",
                  baru=f"{baru.nomor} - sisa dari {po.nomor} ({len(sisa)} barang)")
    return baru


def lengkapi_faktur(pengguna, po, data):
    """Sebelum ACC: pemilik mengisi atau membetulkan harga dan data faktur. Jumlah datang tidak diubah di sini."""
    _harus(po, "menunggu")
    mode = data.get("mode_ppn")
    if mode not in dict(Pembelian.MODE_PPN):
        raise Tolak("Pilih cara PPN ditulis di faktur.")
    tanggal_faktur = baca_tanggal(data.get("tanggal_faktur"), label="Tanggal faktur")
    if mode != "tanpa" and tarif_pada(tanggal_faktur or po.tanggal_terima) is None:
        raise Tolak("Tarif PPN untuk tanggal itu belum diisi di Pengaturan.")
    with transaction.atomic():
        for b in po.baris.all():
            b.harga = baca_angka(data.get(f"harga_{b.id}"), boleh_kosong=False)
            b.save(update_fields=["harga"])
            if f"lokasi_{b.id}" in data:
                _isi_lokasi(po, b, data)
        po.nomor_faktur = rapikan_teks(data.get("nomor_faktur"))[:60]
        po.tanggal_faktur = tanggal_faktur
        po.jatuh_tempo = baca_tanggal(data.get("jatuh_tempo"), label="Jatuh tempo")
        po.mode_ppn = mode
        po.nomor_faktur_pajak = rapikan_teks(data.get("nomor_faktur_pajak"))[:60]
        po.ongkir = baca_angka(data.get("ongkir"), boleh_kosong=True) or 0
        po.ongkir_ke_supplier = data.get("ongkir_tagihan") != "terpisah"
        po.save()


def kembalikan(pengguna, po):
    _harus(po, "menunggu")
    po.status = "dibuat"
    po.hitung_ulang = []
    po.save(update_fields=["status", "hitung_ulang"])
    layanan.catat(pengguna, sumber="pembelian", objek="pembelian", kolom="status",
                  lama="Menunggu ACC", baru="Dipesan", catatan=f"{po.nomor}: dikembalikan untuk diperbaiki")


def batal(pengguna, po, alasan):
    _harus(po, "dibuat", "menunggu")
    alasan = rapikan_teks(alasan)
    if not alasan:
        raise Tolak("Isi alasan pembatalan.")
    lama = po.get_status_display()
    po.status = "batal"
    po.catatan = (po.catatan + "\n" if po.catatan else "") + f"Dibatalkan: {alasan}"
    po.save(update_fields=["status", "catatan"])
    layanan.catat(pengguna, sumber="pembelian", objek="pembelian", kolom="status", lama=lama, baru="Batal",
                  catatan=f"{po.nomor}: {alasan}")


def acc(pengguna, po, sisa=None):
    """ACC: di sinilah stok, modal, dan hutang berubah.

    Kiriman sebagian: kalau ada barang yang datang kurang dari pesanan, `sisa` wajib dipilih:
    "tunggu" = dibuatkan PO lanjutan untuk sisanya, "batal" = sisanya tidak jadi.
    """
    from master.layanan.lokasi import ubah_gudang
    with transaction.atomic():
        po = Pembelian.objects.select_for_update().get(pk=po.pk)
        _harus(po, "menunggu")
        kurang = sisa_belum_datang(po)
        if kurang and sisa not in ("tunggu", "batal"):
            raise Tolak("Ada barang yang datang kurang dari pesanan. Pilih: sisanya ditunggu (PO lanjutan) atau tidak jadi.")
        tanggal = po.tanggal_faktur or po.tanggal_terima
        tarif = tarif_pada(tanggal) or Decimal(0)
        if po.mode_ppn != "tanpa" and not tarif:
            raise Tolak("Tarif PPN untuk tanggal faktur belum diisi di Pengaturan.")
        toko_pkp = _toko(po).pkp_pada(tanggal)
        hasil = hitung(po, tarif=tarif, toko_pkp=toko_pkp)
        ubah_stok = stok_ikut(po)

        for h in hasil["baris"]:
            b, qty = h["objek"], h["qty"]
            b.dpp, b.ppn, b.modal_satuan, b.ongkir = h["dpp"], h["ppn"], h["modal_satuan"], h["ongkir"]
            b.save(update_fields=["dpp", "ppn", "modal_satuan", "ongkir"])
            if qty <= 0 or not ubah_stok:
                continue
            info = VarianInfo.objects.select_for_update().get(pk=b.varian_id)
            baris_item = Item.objects.select_for_update().filter(item_name=info.item_name, variant=info.variant)
            sekarang = baris_item.values("in_stock", "cost_amount").first()
            if sekarang is None:
                raise Tolak(f"{b.item_name} / {b.variant} sudah tidak ada di tabel item.")
            stok_lama = sekarang["in_stock"] or 0
            modal_lama = sekarang["cost_amount"]
            modal_baru = h["modal_satuan"]

            # Modal rata: rata-rata tertimbang dengan stok yang ada. Stok minus dianggap nol.
            dasar = max(stok_lama, 0)
            rata_lama = info.modal_rata if info.modal_rata is not None else (modal_lama if modal_lama is not None else modal_baru)
            rata_baru = int(round((dasar * rata_lama + qty * modal_baru) / (dasar + qty)))

            baris_item.update(in_stock=stok_lama + qty, cost_amount=modal_baru)
            MutasiStok.objects.create(
                pengguna=pengguna, nama_pengguna=pengguna.username, varian=info,
                item_name=info.item_name, variant=info.variant, jenis="beli", jumlah=qty,
                stok_sebelum=stok_lama, stok_sesudah=stok_lama + qty, catatan=po.nomor, lokasi=b.lokasi,
            )
            ubah_gudang(info, b.lokasi, qty)
            if modal_lama != modal_baru:
                layanan.catat(pengguna, sumber="pembelian", item_name=info.item_name, variant=info.variant,
                              kolom="cost_amount", lama=modal_lama, baru=modal_baru, catatan=po.nomor)
            if info.modal_rata != rata_baru:
                layanan.catat(pengguna, sumber="pembelian", item_name=info.item_name, variant=info.variant,
                              kolom="modal_rata", lama=info.modal_rata, baru=rata_baru, catatan=po.nomor)
                info.modal_rata = rata_baru
                info.save(update_fields=["modal_rata"])

        if kurang and sisa == "tunggu":
            po.lanjutan_baru = _buat_lanjutan(pengguna, po, kurang)
        po.status = "diterima"
        po.acc_oleh = pengguna.username
        po.acc_waktu = timezone.now()
        po.tarif_ppn = tarif if po.mode_ppn != "tanpa" else Decimal(0)
        po.toko_pkp = toko_pkp
        po.total_dpp, po.total_ppn, po.total = hasil["total_dpp"], hasil["total_ppn"], hasil["total"]
        po.save()
        layanan.catat(pengguna, sumber="pembelian", objek="pembelian", kolom="status",
                      lama="Menunggu ACC", baru="Diterima", catatan=f"{po.nomor}: hutang {po.total}")
    return po


def bayar(pengguna, po, *, tanggal, jumlah, cara, catatan=""):
    with transaction.atomic():
        po = Pembelian.objects.select_for_update().get(pk=po.pk)
        _harus(po, "diterima")
        nilai = baca_angka(jumlah, boleh_kosong=False)
        if nilai <= 0:
            raise Tolak("Jumlah bayar harus lebih dari nol.")
        if nilai > po.sisa:
            raise Tolak(f"Jumlah bayar melebihi sisa hutang ({po.sisa}).")
        if cara not in dict(Pembayaran.CARA):
            raise Tolak("Pilih cara bayar.")
        Pembayaran.objects.create(
            pembelian=po, tanggal=baca_tanggal(tanggal, wajib=True, label="Tanggal bayar"),
            jumlah=nilai, cara=cara, catatan=rapikan_teks(catatan)[:300], oleh=pengguna.username,
        )
        po.terbayar = po.pembayaran.aggregate(t=Sum("jumlah"))["t"] or 0
        po.save(update_fields=["terbayar"])
        layanan.catat(pengguna, aksi="tambah", sumber="pembelian", objek="pembayaran", kolom="pembayaran",
                      baru=nilai, catatan=f"{po.nomor}: sisa {po.sisa}")
    return po


def sisa_retur(po):
    """{baris_id: jumlah yang masih bisa diretur}."""
    sudah = dict(ReturBaris.objects.filter(retur__pembelian=po).values_list("baris_id").annotate(t=Sum("jumlah")))
    return {b.id: (b.jumlah_terima or 0) - (sudah.get(b.id) or 0) for b in po.baris.all()}


def retur(pengguna, po, data):
    """Retur ke supplier. `data` berisi retur_<id baris>, tanggal, alasan."""
    alasan = rapikan_teks(data.get("alasan"))[:300]
    if not alasan:
        raise Tolak("Isi alasan retur.")
    tanggal = baca_tanggal(data.get("tanggal"), wajib=True, label="Tanggal retur")
    with transaction.atomic():
        po = Pembelian.objects.select_for_update().get(pk=po.pk)
        _harus(po, "diterima")
        boleh = sisa_retur(po)
        dok = Retur.objects.create(pembelian=po, tanggal=tanggal, alasan=alasan, oleh=pengguna.username)
        nilai_total = 0
        for b in po.baris.all():
            qty = baca_angka(data.get(f"retur_{b.id}"), boleh_kosong=True) or 0
            if qty == 0:
                continue
            if qty < 0:
                raise Tolak(f"{b.item_name}: jumlah retur tidak boleh minus.")
            if qty > boleh[b.id]:
                raise Tolak(f"{b.item_name} / {b.variant}: paling banyak bisa diretur {boleh[b.id]}.")
            # Nilai retur sebanding dengan yang ditagih. Retur terakhir mengambil sisa,
            # supaya total retur tidak pernah beda serupiah dari nilai baris.
            lalu = ReturBaris.objects.filter(baris=b).aggregate(d=Sum("dpp"), p=Sum("ppn"))
            if qty == boleh[b.id]:
                dpp, ppn = b.dpp - (lalu["d"] or 0), b.ppn - (lalu["p"] or 0)
            else:
                dpp = int(round(Decimal(b.dpp) * qty / b.jumlah_terima))
                ppn = int(round(Decimal(b.ppn) * qty / b.jumlah_terima))
            lokasi_retur = None
            if stok_ikut(po):
                from master.layanan.lokasi import baca
                nilai_lok = data.get(f"lokasi_{b.id}")
                lokasi_retur = baca(nilai_lok) if nilai_lok not in (None, "") else b.lokasi
                if lokasi_retur is not None and lokasi_retur.jenis != "gudang":
                    lokasi_retur = None
            ReturBaris.objects.create(retur=dok, baris=b, jumlah=qty, dpp=dpp, ppn=ppn, lokasi=lokasi_retur)
            nilai_total += dpp + ppn
            if not stok_ikut(po):
                continue

            info = VarianInfo.objects.select_for_update().get(pk=b.varian_id)
            baris_item = Item.objects.select_for_update().filter(item_name=info.item_name, variant=info.variant)
            stok_lama = baris_item.values_list("in_stock", flat=True).first()
            if stok_lama is None and not baris_item.exists():
                raise Tolak(f"{b.item_name} / {b.variant} sudah tidak ada di tabel item.")
            stok_lama = stok_lama or 0
            baris_item.update(in_stock=stok_lama - qty)
            MutasiStok.objects.create(
                pengguna=pengguna, nama_pengguna=pengguna.username, varian=info,
                item_name=info.item_name, variant=info.variant, jenis="retur_beli", jumlah=-qty,
                stok_sebelum=stok_lama, stok_sesudah=stok_lama - qty, catatan=f"{po.nomor}: {alasan}",
                lokasi=lokasi_retur,
            )
            from master.layanan.lokasi import ubah_gudang
            ubah_gudang(info, lokasi_retur, -qty)
            # Modal rata dihitung mundur: keluarkan barang itu dengan modal pembeliannya.
            # Kalau stok tidak cukup untuk dihitung (habis/minus) atau hasilnya tidak masuk akal, biarkan.
            dasar = max(stok_lama, 0)
            if info.modal_rata is not None and dasar - qty > 0:
                rata_baru = int(round((dasar * info.modal_rata - qty * b.modal_satuan) / (dasar - qty)))
                if rata_baru > 0 and rata_baru != info.modal_rata:
                    layanan.catat(pengguna, sumber="pembelian", item_name=info.item_name, variant=info.variant,
                                  kolom="modal_rata", lama=info.modal_rata, baru=rata_baru, catatan=f"Retur {po.nomor}")
                    info.modal_rata = rata_baru
                    info.save(update_fields=["modal_rata"])
        if nilai_total == 0 and not dok.baris.exists():
            raise Tolak("Isi jumlah barang yang diretur.")
        dok.nilai = nilai_total
        dok.save(update_fields=["nilai"])
        po.diretur = po.retur.aggregate(t=Sum("nilai"))["t"] or 0
        po.save(update_fields=["diretur"])
        layanan.catat(pengguna, aksi="tambah", sumber="pembelian", objek="retur", kolom="retur",
                      baru=nilai_total, catatan=f"{po.nomor}: {alasan}")
    return dok


def uang_kembali(pengguna, po, *, tanggal, jumlah, cara, catatan=""):
    """Supplier mengembalikan uang (retur setelah faktur dibayar). Dicatat sebagai pembayaran minus."""
    with transaction.atomic():
        po = Pembelian.objects.select_for_update().get(pk=po.pk)
        _harus(po, "diterima")
        nilai = baca_angka(jumlah, boleh_kosong=False)
        if nilai <= 0:
            raise Tolak("Jumlah harus lebih dari nol.")
        if nilai > po.lebih_bayar:
            raise Tolak(f"Jumlah melebihi uang yang harus dikembalikan supplier ({po.lebih_bayar}).")
        if cara not in dict(Pembayaran.CARA):
            raise Tolak("Pilih cara.")
        Pembayaran.objects.create(
            pembelian=po, tanggal=baca_tanggal(tanggal, wajib=True, label="Tanggal"),
            jumlah=-nilai, cara=cara, catatan=(rapikan_teks(catatan) or "Uang kembali dari supplier")[:300],
            oleh=pengguna.username,
        )
        po.terbayar = po.pembayaran.aggregate(t=Sum("jumlah"))["t"] or 0
        po.save(update_fields=["terbayar"])
        layanan.catat(pengguna, aksi="tambah", sumber="pembelian", objek="pembayaran", kolom="uang_kembali",
                      baru=nilai, catatan=po.nomor)
    return po


def hutang_terbuka(toko=None):
    """Semua faktur yang belum lunas: dari PO dan dari faktur manual. Urut jatuh tempo."""
    from master.models import FakturManual
    po = Pembelian.objects.filter(status="diterima").select_related("supplier", "toko")
    fm = FakturManual.objects.select_related("supplier", "toko")
    if toko:
        po, fm = po.filter(toko=toko), fm.filter(toko=toko)
    aktif = [p for p in po if p.sisa > 0] + [f for f in fm if f.sisa > 0]
    aktif.sort(key=lambda x: (x.jatuh_tempo is None, x.jatuh_tempo or date.max, x.supplier.nama))
    return aktif


def ringkasan_hutang(hari_ini=None, toko=None):
    hari_ini = hari_ini or timezone.localdate()
    aktif = hutang_terbuka(toko)
    return {
        "jumlah": len(aktif),
        "total": sum(p.sisa for p in aktif),
        "lewat": [p for p in aktif if p.jatuh_tempo and p.jatuh_tempo < hari_ini],
        "segera": [p for p in aktif if p.jatuh_tempo and 0 <= (p.jatuh_tempo - hari_ini).days <= 7],
    }


def tambah_foto(pengguna, po, berkas):
    """Simpan foto nota/faktur distributor untuk PO ini (aturan berkas sama dengan faktur manual)."""
    import os
    import uuid
    from django.core.files.storage import default_storage
    from master.layanan.faktur import JENIS_BERKAS, MAKS_FOTO, MAKS_UKURAN
    from master.models import FotoPembelian
    berkas = [b for b in berkas if b]
    if not berkas:
        return 0
    if len(berkas) + po.foto.count() > MAKS_FOTO:
        raise Tolak(f"Paling banyak {MAKS_FOTO} foto per pembelian.")
    for f in berkas:
        if f.content_type not in JENIS_BERKAS:
            raise Tolak(f"{f.name}: hanya foto JPG/PNG/WEBP atau PDF.")
        if f.size > MAKS_UKURAN:
            raise Tolak(f"{f.name}: lebih dari 15 MB.")
    for f in berkas:
        kunci = default_storage.save(f"master/pembelian/{po.pk}/{uuid.uuid4().hex}{JENIS_BERKAS[f.content_type]}", f)
        FotoPembelian.objects.create(pembelian=po, object_key=kunci, content_type=f.content_type,
                                     nama_asli=os.path.basename(f.name)[:200], ukuran=f.size, oleh=pengguna.username)
    layanan.catat(pengguna, sumber="pembelian", objek="pembelian", kolom="foto", baru=f"{po.nomor}: {len(berkas)} foto")
    return len(berkas)
