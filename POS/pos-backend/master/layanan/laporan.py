"""Data untuk menu Nota dan Laporan: nota kasir (tabel POS, hanya dibaca) digabung
dengan nota manual yang dimasukkan dari master.

Catatan penting soal laba: nota di tabel POS tidak menyimpan modal saat barang
terjual. Laba di sini dihitung dari modal rata yang berlaku PADA WAKTU nota,
direkonstruksi dari log perubahan modal di master. Untuk nota sebelum master
dipakai, yang dipakai adalah modal paling awal yang diketahui.
"""
from bisect import bisect_right
from collections import defaultdict
import uuid
from datetime import datetime, time, timedelta

from django.db import connection
from django.utils import timezone

from pos.models import Item, TransactionDetail, Transactions, Worker

from master.models import Log, VarianInfo

METODE = {"cash": "Tunai", "bank_transfer": "Transfer", "edc": "EDC", "qris": "QRIS", "lainnya": "Lainnya"}


def label_metode(kode):
    return METODE.get(kode, kode or "-")


def rentang(awal, akhir):
    """(mulai, selesai) sadar zona waktu untuk tanggal lokal awal..akhir (inklusif)."""
    zona = timezone.get_current_timezone()
    mulai = timezone.make_aware(datetime.combine(awal, time.min), zona)
    selesai = timezone.make_aware(datetime.combine(akhir + timedelta(days=1), time.min), zona)
    return mulai, selesai


def baca_periode(get):
    """Periode dari parameter alamat. Bawaan: hari ini."""
    hari_ini = timezone.localdate()
    pilih = get.get("periode") or "hari"
    if pilih == "kemarin":
        awal = akhir = hari_ini - timedelta(days=1)
    elif pilih == "7":
        awal, akhir = hari_ini - timedelta(days=6), hari_ini
    elif pilih == "30":
        awal, akhir = hari_ini - timedelta(days=29), hari_ini
    elif pilih == "bulan":
        awal, akhir = hari_ini.replace(day=1), hari_ini
    elif pilih == "atur":
        try:
            awal = datetime.strptime(get.get("dari", ""), "%Y-%m-%d").date()
            akhir = datetime.strptime(get.get("sampai", ""), "%Y-%m-%d").date()
        except ValueError:
            awal = akhir = hari_ini
        if akhir < awal:
            awal, akhir = akhir, awal
        if (akhir - awal).days > 366:
            awal = akhir - timedelta(days=366)
    else:
        pilih, awal, akhir = "hari", hari_ini, hari_ini
    return {"pilih": pilih, "awal": awal, "akhir": akhir}


class ModalWaktu:
    """Modal rata per varian pada suatu waktu, dari log master."""

    def __init__(self, kunci):
        kunci = set(kunci)
        nama = {n for n, _ in kunci}
        self.kini = {}
        modal_item = {(n, v): m for n, v, m in Item.objects.filter(item_name__in=nama)
                      .values_list("item_name", "variant", "cost_amount")}
        for info in VarianInfo.objects.filter(item_name__in=nama):
            k = (info.item_name, info.variant)
            self.kini[k] = info.modal_rata if info.modal_rata is not None else modal_item.get(k)
        for k in kunci:
            self.kini.setdefault(k, modal_item.get(k))
        self.riwayat = defaultdict(list)
        for l in Log.objects.filter(kolom="modal_rata", item_name__in=nama).order_by("waktu", "id"):
            self.riwayat[(l.item_name, l.variant)].append((l.waktu, _angka(l.lama), _angka(l.baru)))
        # Daftar waktu per varian disiapkan sekali; dulu dibuat ulang untuk setiap baris nota.
        self.waktu = {k: [w for w, _, _ in d] for k, d in self.riwayat.items()}

    def pada(self, kunci, waktu):
        daftar = self.riwayat.get(kunci)
        if not daftar:
            return self.kini.get(kunci)
        i = bisect_right(self.waktu[kunci], waktu)
        if i == 0:
            return daftar[0][1] if daftar[0][1] is not None else daftar[0][2]
        return daftar[i - 1][2]


def _angka(teks):
    try:
        return int(teks)
    except (TypeError, ValueError):
        return None


def ambil(mulai, selesai, *, kasir=None, metode=None, perangkat=None, toko=None, sumber=None, potong_titipan=True):
    """Nota dan barisnya dalam satu periode, lengkap dengan modal dan laba per baris.

    toko: hanya nota toko itu (nota kasir milik toko yang memegang stok kasir).
    sumber: "kasir" | "manual" | None (dua-duanya).
    potong_titipan: baris barang titipan memakai potongan toko sebagai laba
    (modal = bagian yang disetor ke pemilik barang).
    """
    nota_k, baris_k = ([], []) if (sumber == "manual" or (toko and not toko.stok_di_kasir)) else \
        _ambil_kasir(mulai, selesai, kasir=kasir, metode=metode, perangkat=perangkat)
    nota_m, baris_m = ([], []) if (sumber == "kasir" or kasir or perangkat) else \
        _ambil_manual(mulai, selesai, metode=metode, toko=toko)
    nota = sorted(nota_k + nota_m, key=lambda n: n["transaction_time"], reverse=True)
    baris = baris_k + baris_m
    if potong_titipan:
        _modal_titipan(nota, baris)
    return nota, baris


def _modal_titipan(nota, baris):
    """Barang titipan: modal = harga terjual - potongan toko yang berlaku pada tanggal nota."""
    from decimal import Decimal, ROUND_HALF_UP
    from master.layanan import titipan
    milik = titipan.pemilik_per_nama({b["item_name"] for b in baris})
    if not milik:
        return
    zona = timezone.get_current_timezone()
    syarat = titipan.SyaratWaktu({(b["item_name"], b["variant"]) for b in baris if b["item_name"] in milik})
    ubah = set()
    for b in baris:
        if b["item_name"] not in milik:
            continue
        s = syarat.pada((b["item_name"], b["variant"]), timezone.localtime(b["nota"]["transaction_time"], zona).date())
        b["titipan"] = milik[b["item_name"]].nama
        if s is None or s.potongan_toko is None:
            b["modal_satuan"], b["modal"], b["laba"] = None, None, None
        else:
            laba = int((Decimal(b["subtotal"]) * s.potongan_toko / 100).to_integral_value(rounding=ROUND_HALF_UP))
            b["modal"] = b["subtotal"] - laba
            b["modal_satuan"] = b["modal"] // b["quantity"] if b["quantity"] else None
            b["laba"] = laba
        ubah.add(id(b["nota"]))
    for n in nota:
        if id(n) not in ubah:
            continue
        n["modal"] = sum(b["modal"] for b in n["baris"] if b["modal"] is not None)
        n["modal_lengkap"] = all(b["modal"] is not None for b in n["baris"])
        n["laba"] = n["item_amount"] - n["modal"]


def _ambil_manual(mulai, selesai, *, metode=None, toko=None):
    from master.models import NotaManual
    from master.layanan.nota_manual import waktu_nota
    zona = timezone.get_current_timezone()
    qs = NotaManual.objects.filter(batal=False, tanggal__gte=timezone.localtime(mulai, zona).date(),
                                   tanggal__lt=timezone.localtime(selesai, zona).date()).select_related("toko")
    if metode:
        qs = qs.filter(cara_bayar=metode)
    if toko:
        qs = qs.filter(toko=toko)
    nota, baris = [], []
    info = {}
    daftar = list(qs.prefetch_related("baris"))
    nama = {b.item_name for n in daftar for b in n.baris.all()}
    if nama:
        info = {(k, v): (c, br) for k, v, c, br in Item.objects.filter(item_name__in=nama)
                .values_list("item_name", "variant", "category", "brand")}
    for m in daftar:
        n = {"sync_id": f"m{m.pk}", "manual_id": m.pk, "manual": True, "transaction_id": m.nomor_tulisan or m.nomor,
             "nomor": m.nomor, "toko": m.toko, "transaction_time": waktu_nota(m), "item_amount": m.total_barang,
             "shipping_amount": m.ongkir, "total_amount": m.total, "payment_method": m.cara_bayar,
             "payment_amount": m.total, "change_amount": 0, "kasir": "Nota tulis tangan",
             "metode": label_metode(m.cara_bayar), "pembeli": m.pembeli, "baris": [], "modal": 0, "modal_lengkap": True}
        for x in m.baris.all():
            kunci = (x.item_name, x.variant)
            b = {"sync_id": n["sync_id"], "item_name": x.item_name, "variant": x.variant, "quantity": x.jumlah,
                 "unit_price": x.harga, "discount": 0, "subtotal": x.subtotal}
            b["kategori"], b["brand"] = info.get(kunci, ("(sudah dihapus)", ""))
            b["modal_satuan"] = x.modal_satuan
            b["modal"] = x.modal_satuan * x.jumlah if x.modal_satuan else None
            b["laba"] = b["subtotal"] - b["modal"] if b["modal"] is not None else None
            b["nota"] = n
            n["baris"].append(b)
            if b["modal"] is None:
                n["modal_lengkap"] = False
            else:
                n["modal"] += b["modal"]
            baris.append(b)
        n["laba"] = n["item_amount"] - n["modal"]
        nota.append(n)
    return nota, baris


def _kunci_uuid(nilai):
    """sync_id sebagai teks hex, sama untuk SQLite (teks) maupun PostgreSQL (UUID)."""
    return nilai.hex if isinstance(nilai, uuid.UUID) else str(nilai).replace("-", "").lower()


def _baris_kasir(qs_nota, per_id):
    """Semua baris nota dalam satu query (dulu satu query per 500 nota).

    SQL-nya tetap dibuat Django, tapi dijalankan lewat cursor supaya ratusan ribu baris
    tidak melewati konversi per kolom yang tidak dipakai di sini.
    """
    if not per_id:
        return []
    sql, param = TransactionDetail.objects.filter(sync_id__in=qs_nota.values("sync_id")).values_list(
        "sync_id", "item_name", "variant", "quantity", "unit_price", "discount", "subtotal").query.sql_with_params()
    per_hex = {_kunci_uuid(k): k for k in per_id}
    baris = []
    with connection.cursor() as cur:
        cur.execute(sql, param)
        for sid, nama, varian, qty, harga, diskon, subtotal in cur.fetchall():
            k = per_hex.get(_kunci_uuid(sid))
            if k is None:
                continue
            baris.append({"sync_id": k, "item_name": nama, "variant": varian, "quantity": qty,
                          "unit_price": harga, "discount": diskon, "subtotal": subtotal})
    return baris


def _ambil_kasir(mulai, selesai, *, kasir=None, metode=None, perangkat=None):
    qs = Transactions.objects.filter(transaction_time__gte=mulai, transaction_time__lt=selesai)
    if kasir:
        qs = qs.filter(worker_id=kasir)
    if metode:
        qs = qs.filter(payment_method=metode)
    if perangkat:
        qs = qs.filter(transaction_id__startswith=perangkat + "-")
    nota = list(qs.order_by("-transaction_time").values(
        "sync_id", "transaction_id", "worker_id", "transaction_time", "item_amount", "shipping_amount",
        "total_amount", "payment_method", "payment_amount", "change_amount"))
    per_id = {n["sync_id"]: n for n in nota}
    pekerja = dict(Worker.objects.values_list("id", "name"))
    for n in nota:
        n["kasir"] = pekerja.get(n["worker_id"], n["worker_id"])
        n["metode"] = label_metode(n["payment_method"])
        n["shipping_amount"] = n["shipping_amount"] or 0
        n["baris"] = []
        n["modal"] = 0
        n["modal_lengkap"] = True

    baris = _baris_kasir(qs, per_id)
    modal = ModalWaktu({(b["item_name"], b["variant"]) for b in baris})
    info = {(k, v): (c, br) for k, v, c, br in Item.objects.filter(
        item_name__in={b["item_name"] for b in baris}).values_list("item_name", "variant", "category", "brand")}
    for b in baris:
        n = per_id[b["sync_id"]]
        kunci = (b["item_name"], b["variant"])
        satuan = modal.pada(kunci, n["transaction_time"])
        b["kategori"], b["brand"] = info.get(kunci, ("(sudah dihapus)", ""))
        b["modal_satuan"] = satuan
        b["modal"] = satuan * b["quantity"] if satuan else None
        b["laba"] = b["subtotal"] - b["modal"] if b["modal"] is not None else None
        b["nota"] = n
        n["baris"].append(b)
        if b["modal"] is None:
            n["modal_lengkap"] = False
        else:
            n["modal"] += b["modal"]
    for n in nota:
        n["laba"] = n["item_amount"] - n["modal"]
    return nota, baris


def ambil_kembali(mulai, selesai, *, kasir=None, metode=None, perangkat=None, toko=None, sumber=None):
    """Refund, retur, dan void yang DICATAT dalam periode (QA 18 Okt 2026).

    Dihitung pada tanggal kejadiannya, bukan tanggal nota asal, supaya laporan
    satu hari cocok dengan uang yang keluar-masuk laci hari itu. Tiap kejadian:
    - `uang`: uang yang dikembalikan ke pembeli (refund_amount).
    - `modal_kembali`: modal barang yang kembali ke stok (retur dan void),
      dihitung dengan modal pada waktu nota asal. Kalau barangnya sudah
      diterima di master, hanya jumlah yang masuk stok; yang rusak tetap rugi.
      Kalau belum diterima, dianggap kembali semua dan ditandai `menunggu`.
    """
    from master.layanan import retur_jual
    from master.models import PenerimaanReturBaris
    from pos.models import RefundEvent, RefundEventDetail

    if sumber == "manual" or (toko and not toko.stok_di_kasir) or not retur_jual.tersedia():
        return []
    qs = RefundEvent.objects.filter(created_at__gte=mulai, created_at__lt=selesai)
    if metode:
        qs = qs.filter(refund_method=metode)
    events = list(qs.order_by("-created_at"))
    if not events:
        return []
    asal = {n["sync_id"]: n for n in Transactions.objects.filter(sync_id__in={e.sync_id for e in events})
            .values("sync_id", "transaction_id", "worker_id", "transaction_time")}
    if kasir:
        events = [e for e in events if (asal.get(e.sync_id) or {}).get("worker_id") == kasir]
    if perangkat:
        events = [e for e in events if e.device_code == perangkat
                  or ((asal.get(e.sync_id) or {}).get("transaction_id") or "").startswith(perangkat + "-")]

    barang = {e.refund_id: [] for e in events}
    retur = [e.refund_id for e in events if e.event_type == "return"]
    for d in RefundEventDetail.objects.filter(refund_id__in=retur):
        barang[d.refund_id].append([d.item_name, d.variant, d.quantity])
    void = {e.sync_id: e.refund_id for e in events if e.event_type == "void"}
    for d in TransactionDetail.objects.filter(sync_id__in=list(void)):
        barang[void[d.sync_id]].append([d.item_name, d.variant, d.quantity])
    diterima = defaultdict(dict)
    for b in PenerimaanReturBaris.objects.filter(penerimaan__refund_id__in=list(barang)).select_related("penerimaan"):
        diterima[b.penerimaan.refund_id][(b.item_name, b.variant)] = b.masuk

    modal = ModalWaktu({(n, v) for daftar in barang.values() for n, v, _ in daftar})
    pekerja = dict(Worker.objects.values_list("id", "name"))
    hasil = []
    for e in events:
        n = asal.get(e.sync_id) or {}
        waktu_asal = n.get("transaction_time") or e.created_at
        total_modal, lengkap = 0, True
        for nama, varian, qty in barang[e.refund_id]:
            if e.refund_id in diterima:
                qty = diterima[e.refund_id].get((nama, varian), 0)
            satuan = modal.pada((nama, varian), waktu_asal)
            if satuan is None:
                lengkap = False
            else:
                total_modal += satuan * qty
        hasil.append({
            "refund_id": e.refund_id, "sync_id": e.sync_id, "waktu": e.created_at, "jenis": e.event_type,
            "label": retur_jual.LABEL.get(e.event_type, e.event_type), "transaction_id": n.get("transaction_id"),
            "kasir": pekerja.get(n.get("worker_id"), n.get("worker_id") or "-"), "alasan": e.reason,
            "metode": label_metode(e.refund_method), "uang": e.refund_amount or 0, "modal_kembali": total_modal,
            "modal_lengkap": lengkap, "barang": barang[e.refund_id],
            "menunggu": e.event_type in ("return", "void") and e.refund_id not in diterima,
        })
    return hasil


def ringkas(nota, kembali=()):
    omzet = sum(n["item_amount"] for n in nota)
    modal = sum(n["modal"] for n in nota)
    uang_kembali = sum(k["uang"] for k in kembali)
    modal_kembali = sum(k["modal_kembali"] for k in kembali)
    bersih = omzet - uang_kembali
    laba = omzet - modal - uang_kembali + modal_kembali
    return {
        "nota": len(nota),
        "omzet_kotor": omzet,
        "kembali": len(kembali),
        "uang_kembali": uang_kembali,
        "modal_kembali": modal_kembali,
        # omzet dan laba = BERSIH setelah refund/retur/void dalam periode.
        "omzet": bersih,
        "ongkir": sum(n["shipping_amount"] for n in nota),
        "total": sum(n["total_amount"] for n in nota) - uang_kembali,
        "laba": laba,
        "margin": round(laba / bersih * 100, 1) if bersih else None,
        "rata": round(omzet / len(nota)) if nota else 0,
        "tanpa_modal": sum(1 for n in nota if not n["modal_lengkap"])
        + sum(1 for k in kembali if not k["modal_lengkap"]),
    }


def kelompok_baris(baris, kunci, urut="omzet"):
    """Kelompokkan baris penjualan. kunci: barang | varian | kategori | brand."""
    ambil_kunci = {
        "barang": lambda b: (b["item_name"],),
        "varian": lambda b: (b["item_name"], "" if b["variant"] == "N/A" else b["variant"]),
        "kategori": lambda b: (b["kategori"] or "(kosong)",),
        "brand": lambda b: (b["brand"] or "(kosong)",),
    }[kunci]
    hasil = {}
    for b in baris:
        k = ambil_kunci(b)
        h = hasil.setdefault(k, {"nama": " / ".join(x for x in k if x), "qty": 0, "omzet": 0, "laba": 0, "nota": set(), "tanpa_modal": False})
        h["qty"] += b["quantity"]
        h["omzet"] += b["subtotal"]
        if b["laba"] is None:
            h["tanpa_modal"] = True
        else:
            h["laba"] += b["laba"]
        h["nota"].add(b["sync_id"])
    daftar = list(hasil.values())
    for h in daftar:
        h["nota"] = len(h["nota"])
        h["margin"] = round(h["laba"] / h["omzet"] * 100, 1) if h["omzet"] else None
    return _urut_dan_bagian(daftar, urut)


HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]


def kelompok_nota(nota, kunci, urut="omzet"):
    """Kelompokkan nota. kunci: kasir | bayar | hari | jam | harian (hari dalam seminggu).

    "jam" hanya nota kasir: nota tulis tangan tidak punya jam.
    """
    zona = timezone.get_current_timezone()
    ambil_kunci = {
        "kasir": lambda n: n["kasir"],
        "bayar": lambda n: n["metode"],
        "hari": lambda n: n["transaction_time"].astimezone(zona).date(),
        "jam": lambda n: n["transaction_time"].astimezone(zona).hour,
        "harian": lambda n: n["transaction_time"].astimezone(zona).weekday(),
    }[kunci]
    if kunci == "jam":
        nota = [n for n in nota if not n.get("manual")]
    hasil = {}
    for n in nota:
        k = ambil_kunci(n)
        h = hasil.setdefault(k, {"nama": k, "nota": 0, "omzet": 0, "laba": 0, "total": 0, "tanpa_modal": False})
        h["nota"] += 1
        h["omzet"] += n["item_amount"]
        h["total"] += n["total_amount"]
        h["laba"] += n["laba"]
        h["tanpa_modal"] = h["tanpa_modal"] or not n["modal_lengkap"]
    daftar = list(hasil.values())
    for h in daftar:
        h["margin"] = round(h["laba"] / h["omzet"] * 100, 1) if h["omzet"] else None
        h["qty"] = h["nota"]
    if kunci in ("hari", "jam", "harian"):
        if kunci == "jam":
            ada = {h["nama"] for h in daftar}
            for j in range(min([7, *ada]), max([17, *ada]) + 1):   # jam kosong tetap tampil di grafik
                if j not in ada:
                    daftar.append({"nama": j, "nota": 0, "omzet": 0, "laba": 0, "total": 0, "tanpa_modal": False,
                                   "margin": None, "qty": 0})
        if kunci == "harian":
            ada = {h["nama"] for h in daftar}
            daftar += [{"nama": d, "nota": 0, "omzet": 0, "laba": 0, "total": 0, "tanpa_modal": False, "margin": None,
                        "qty": 0} for d in range(7) if d not in ada]
        daftar.sort(key=lambda h: h["nama"])
        puncak = max((h["omzet"] for h in daftar), default=0)
        for h in daftar:
            h["bagian"] = round(h["omzet"] / puncak * 100) if puncak else 0
            if kunci == "jam":
                h["label"] = f"{h['nama']:02d}.00"
            elif kunci == "harian":
                h["label"] = HARI[h["nama"]]
        return daftar
    return _urut_dan_bagian(daftar, urut)


def _urut_dan_bagian(daftar, urut):
    kolom = urut if urut in ("omzet", "laba", "qty", "margin") else "omzet"
    daftar.sort(key=lambda h: (h[kolom] is None, -(h[kolom] or 0), str(h["nama"])))
    puncak = max((h["omzet"] for h in daftar), default=0)
    for h in daftar:
        h["bagian"] = round(h["omzet"] / puncak * 100) if puncak else 0
    return daftar
