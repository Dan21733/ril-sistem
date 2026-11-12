"""Piutang pelanggan untuk tablet kasir (rancangan DP 31 Okt 2026, fase 2).

Sumber kebenaran piutang ada di master (`master_piutang`). Server kasir tidak
memuat aplikasi master, jadi tabelnya dibaca lewat SQL (pola yang sama dengan
`data_master.py`) dan hanya kalau tabelnya ada. Nota DP kasir yang belum sempat
diimpor master (penjadwal tiap menit) ikut ditampilkan dari `nota_piutang`.

Kasir tidak pernah menulis ke tabel master. Pelunasan dan "barang diambil"
dicatat di `aksi_piutang`; master yang menerapkannya.
"""
import datetime
import uuid

from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from .models import AksiPiutang, NotaPiutang, TransactionDetail, Transactions

# proses = nota diproses (pesanan, titipan, COD); invoice = pelanggan tempo (12 Nov 2026).
# belum_lunas / ambil = tab lama, tetap dilayani untuk tablet yang belum diperbarui.
TAB = ("proses", "invoice", "belum_lunas", "ambil")
CARA = ("cash", "edc", "bank_transfer")


def _ada(tabel, kolom=None):
    try:
        with connection.cursor() as cursor:
            if tabel not in connection.introspection.table_names(cursor):
                return False
            if kolom is None:
                return True
            return kolom in {c.name for c in connection.introspection.get_table_description(cursor, tabel)}
    except DatabaseError:
        return False


def _baris(sql, param=()):
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sql, list(param))
            kolom = [c[0] for c in cursor.description]
            return [dict(zip(kolom, r)) for r in cursor.fetchall()]
    except DatabaseError:
        return []


# ---------------------------------------------------------------------------
# Aturan DP untuk checkout
# ---------------------------------------------------------------------------

def min_dp_untuk(nama_item):
    """{item_name: persen} untuk barang yang minimal DP-nya diatur khusus."""
    if not nama_item or not _ada("master_item_info", "min_dp_persen"):
        return {}
    nama = list(nama_item)
    tanda = ", ".join(["%s"] * len(nama))
    return {r["item_name"]: r["min_dp_persen"] for r in _baris(
        f"SELECT item_name, min_dp_persen FROM master_item_info WHERE min_dp_persen IS NOT NULL "
        f"AND item_name IN ({tanda})", nama)}


def pemilik_untuk(nama_item):
    """{item_name: {"id", "nama"}} untuk barang milik toko titipan (brand)."""
    if not nama_item or not _ada("master_item_info", "toko_id") or not _ada("master_toko", "titip_di_id"):
        return {}
    nama = list(nama_item)
    tanda = ", ".join(["%s"] * len(nama))
    return {r["item_name"]: {"id": r["id"], "nama": r["nama"]} for r in _baris(
        f"SELECT i.item_name, t.id, t.nama FROM master_item_info i JOIN master_toko t ON t.id = i.toko_id "
        f"WHERE t.titip_di_id IS NOT NULL AND i.item_name IN ({tanda})", nama)}


def toko_titipan():
    """Toko titipan yang dijual di kasir ini: kop struk nota mereka (nama, alamat, telepon)."""
    if not _ada("master_toko", "titip_di_id"):
        return []
    aktif = "t.aktif" if connection.vendor != "sqlite" else "t.aktif = 1"
    return _baris(
        "SELECT t.id, t.nama, t.alamat, t.telepon FROM master_toko t JOIN master_toko k ON k.id = t.titip_di_id "
        f"WHERE k.stok_di_kasir AND {aktif} ORDER BY t.urutan, t.nama")


def aturan():
    """DP minimal bawaan, jatuh tempo, dan daftar pelanggan tempo (untuk dipilih kasir)."""
    hasil = {"dp_minimal_persen": 50, "jatuh_tempo_hari": 14, "pelanggan": [], "tersedia": False}
    if _ada("master_pengaturan", "dp_minimal_persen"):
        atur = _baris("SELECT dp_minimal_persen, jatuh_tempo_hari FROM master_pengaturan ORDER BY id LIMIT 1")
        if atur:
            hasil.update(atur[0])
        hasil["tersedia"] = True
    hasil["toko_titipan"] = toko_titipan()
    from . import pengaturan
    hasil["metode_bayar"] = pengaturan.metode_bayar()
    if _ada("master_pelanggan"):
        hasil["pelanggan"] = _baris(
            "SELECT id, nama, telepon FROM master_pelanggan WHERE aktif AND boleh_tempo ORDER BY nama, id")
    # Daftar hitam COD (12 Nov 2026): No. HP yang pernah menolak bayar penuh saat barang diantar.
    hasil["daftar_hitam"] = []
    if _ada("master_daftar_hitam"):
        aktif = "aktif" if connection.vendor != "sqlite" else "aktif = 1"
        hasil["daftar_hitam"] = [r["telepon"] for r in _baris(
            f"SELECT telepon FROM master_daftar_hitam WHERE {aktif} ORDER BY telepon")]
    return hasil


# ---------------------------------------------------------------------------
# Daftar & detail
# ---------------------------------------------------------------------------

KOLOM_DASAR = ("id, kasir_sync_id, nota_manual_id, nomor, tanggal, nama, telepon, total, dibayar, jatuh_tempo, "
               "status_barang, barang_selesai, jenis_acc, acc_status")
KOLOM_BARU = ", jenis, dibuat_oleh, potongan_retur, uang_kembali, acc_catatan"


def _kolom():
    """Kolom master_piutang yang dibaca; kolom nota diproses (master 0054) hanya kalau sudah ada."""
    return KOLOM_DASAR + (KOLOM_BARU if _ada("master_piutang", "jenis") else "")


def jenis_dari(status_barang, dibayar, total):
    """Jenis nota diproses untuk nota yang belum diimpor master (aturan sama dengan master)."""
    if status_barang == "ambil":
        return "titipan"
    if status_barang == "pesan":
        return "pesanan"
    if status_barang == "antar" and dibayar < total:
        return "cod"
    return "invoice" if dibayar < total else ""


def _rapikan(r):
    r = dict(r)
    r["total"], r["dibayar"] = int(r["total"]), int(r["dibayar"])
    r["potongan_retur"], r["uang_kembali"] = int(r.get("potongan_retur") or 0), int(r.get("uang_kembali") or 0)
    r["sisa"] = max(0, r["total"] - r["potongan_retur"] - (r["dibayar"] - r["uang_kembali"]))
    if not r.get("jenis"):
        r["jenis"] = jenis_dari(r["status_barang"], r["dibayar"], r["total"])
    r["pembuat"] = (r.pop("dibuat_oleh", "") or "").replace(" (kasir)", "")
    r["acc_catatan"] = r.get("acc_catatan") or ""
    r["kasir_sync_id"] = str(uuid.UUID(str(r["kasir_sync_id"]))) if r.get("kasir_sync_id") else None
    for k in ("tanggal", "jatuh_tempo"):
        if r.get(k) is not None and hasattr(r[k], "isoformat"):
            r[k] = r[k].isoformat()
    r["barang_selesai"] = r["barang_selesai"].isoformat() if r.get("barang_selesai") else None
    r["kunci"] = f"p{r['id']}" if r.get("id") else f"k{r['kasir_sync_id']}"
    return r


def _uuid_sql(nilai):
    """UUID sebagai parameter SQL mentah: SQLite menyimpan 32 heksa tanpa tanda hubung."""
    u = uuid.UUID(str(nilai))
    return u.hex if connection.vendor == "sqlite" else str(u)


def _nama_pekerja(worker_id):
    from .models import Worker
    return Worker.objects.filter(pk=worker_id).values_list("name", flat=True).first() or "" if worker_id else ""


def _belum_diimpor():
    """Nota DP kasir yang belum punya baris master (master belum sempat mengimpor)."""
    baru = list(NotaPiutang.objects.filter(masuk__gte=timezone.now() - datetime.timedelta(days=3)).order_by("-masuk"))
    sudah = set()
    if baru and _ada("master_piutang"):
        tanda = ", ".join(["%s"] * len(baru))
        sudah = {uuid.UUID(str(r["kasir_sync_id"])).hex for r in _baris(
            f"SELECT kasir_sync_id FROM master_piutang WHERE kasir_sync_id IN ({tanda})",
            [_uuid_sql(n.sync_id) for n in baru])}
    hasil = []
    nota = {t.sync_id: t for t in Transactions.objects.filter(sync_id__in=[n.sync_id for n in baru])}
    for n in baru:
        if n.sync_id.hex in sudah or n.sync_id not in nota:
            continue
        t = nota[n.sync_id]
        hasil.append(_rapikan({
            "dibuat_oleh": _nama_pekerja(n.worker_id),
            "id": None, "kasir_sync_id": n.sync_id, "nota_manual_id": None,
            "nomor": n.transaction_id or "nota kasir", "tanggal": timezone.localdate(t.transaction_time),
            "nama": t.cust_name or "", "telepon": t.phone_no or "", "total": t.total_amount,
            "dibayar": n.dibayar, "jatuh_tempo": n.jatuh_tempo, "status_barang": n.status_barang,
            "barang_selesai": None, "jenis_acc": "", "acc_status": "", "baru": True,
        }))
    return hasil


def _menunggu_per_kunci():
    """Aksi kasir yang belum diproses master, supaya tampilan langsung ikut berubah."""
    hasil = {}
    for a in AksiPiutang.objects.filter(status="baru"):
        kunci = f"p{a.piutang_id}" if a.piutang_id else f"k{a.sync_id}"
        hasil.setdefault(kunci, []).append(a)
    return hasil


def _terapkan_tertunda(r, tertunda):
    r["menunggu"] = []
    for a in tertunda.get(r["kunci"], []):
        if a.jenis == "bayar":
            r["dibayar"] += a.jumlah
            r["sisa"] = max(0, r["sisa"] - a.jumlah)
        else:
            r["barang_selesai"] = a.waktu.isoformat()
        r["menunggu"].append({"jenis": a.jenis, "jumlah": a.jumlah})
    return r


def _berjalan(r):
    """Belum selesai: masih ada sisa, barangnya belum diambil/diserahkan, atau aksi kasir belum diproses.
    Nota yang ditolak pemilik tetap tampil sampai kasir membatalkannya."""
    tertunda = r["status_barang"] in ("ambil", "pesan") and not r["barang_selesai"]
    return r["sisa"] > 0 or tertunda or bool(r["menunggu"]) or r["acc_status"] == "ditolak"


def daftar(tab, cari=""):
    if tab not in TAB:
        tab = "proses"
    baris = []
    if _ada("master_piutang"):
        syarat = ("batal = false AND (dibayar < total OR acc_status = 'ditolak' "
                  "OR (status_barang IN ('ambil', 'pesan') AND barang_selesai IS NULL))")
        urut = "tanggal, id"
        if connection.vendor == "sqlite":
            syarat = syarat.replace("false", "0")
        baris = [_rapikan(r) for r in _baris(f"SELECT {_kolom()} FROM master_piutang WHERE {syarat} ORDER BY {urut} LIMIT 500")]
    baris = _belum_diimpor() + baris
    tertunda = _menunggu_per_kunci()
    baris = [_terapkan_tertunda(r, tertunda) for r in baris]
    if tab == "belum_lunas":
        baris = [r for r in baris if (r["sisa"] > 0 or r["menunggu"]) and r["acc_status"] != "ditolak"]
    elif tab == "ambil":
        baris = [r for r in baris if r["status_barang"] in ("ambil", "pesan") and r["acc_status"] != "ditolak"
                 and (not r["barang_selesai"] or r["menunggu"])]
    elif tab == "invoice":
        baris = [r for r in baris if r["jenis"] == "invoice" and _berjalan(r)]
    else:
        baris = [r for r in baris if r["jenis"] != "invoice" and _berjalan(r)]
    teks = (cari or "").strip().lower()
    if teks:
        baris = [r for r in baris if teks in f"{r['nama']} {r['telepon']} {r['nomor']}".lower()]
    return baris


def hitungan():
    return {t: len(daftar(t)) for t in TAB}


def detail(kunci):
    """kunci: 'p<id>' (piutang master) atau 'k<sync_id>' (nota kasir belum diimpor)."""
    r = None
    if kunci.startswith("p") and kunci[1:].isdigit() and _ada("master_piutang"):
        ada = _baris(f"SELECT {_kolom()} FROM master_piutang WHERE id = %s", [int(kunci[1:])])
        r = _rapikan(ada[0]) if ada else None
    elif kunci.startswith("k"):
        r = next((x for x in _belum_diimpor() if x["kunci"] == kunci), None)
        if r is None and _ada("master_piutang"):
            try:
                ada = _baris(f"SELECT {_kolom()} FROM master_piutang WHERE kasir_sync_id = %s", [_uuid_sql(kunci[1:])])
            except ValueError:
                ada = []
            r = _rapikan(ada[0]) if ada else None
    if r is None:
        return None
    if r.get("kasir_sync_id"):
        r["barang"] = [
            {"nama": d.item_name, "varian": d.variant, "jumlah": d.quantity, "harga": d.unit_price, "subtotal": d.subtotal}
            for d in TransactionDetail.objects.filter(sync_id=r["kasir_sync_id"]).order_by("item_name", "variant")]
    elif r.get("nota_manual_id") and _ada("master_nota_manual_baris"):
        r["barang"] = [
            {"nama": b["item_name"], "varian": b["variant"], "jumlah": b["jumlah"], "harga": b["harga"],
             "subtotal": int(b["subtotal"])}
            for b in _baris("SELECT item_name, variant, jumlah, harga, subtotal FROM master_nota_manual_baris "
                            "WHERE nota_id = %s ORDER BY id", [r["nota_manual_id"]])]
    else:
        r["barang"] = []
    r["pembayaran"] = []
    if r.get("id") and _ada("master_pembayaran_piutang"):
        r["pembayaran"] = [
            {"waktu": p["waktu"].isoformat() if hasattr(p["waktu"], "isoformat") else p["waktu"], "jumlah": int(p["jumlah"]),
             "cara": p["cara"], "oleh": p["oleh"], "catatan": p["catatan"]}
            for p in _baris("SELECT waktu, jumlah, cara, oleh, catatan FROM master_pembayaran_piutang "
                            "WHERE piutang_id = %s ORDER BY waktu, id", [r["id"]])]
    elif r.get("kasir_sync_id"):
        n = NotaPiutang.objects.filter(sync_id=r["kasir_sync_id"]).first()
        if n and n.dibayar:
            r["pembayaran"] = [{"waktu": n.masuk.isoformat(), "jumlah": n.dibayar, "cara": n.cara, "oleh": "", "catatan": "DP"}]
    return _terapkan_tertunda(r, _menunggu_per_kunci())


# ---------------------------------------------------------------------------
# Aksi kasir
# ---------------------------------------------------------------------------

class AksiDitolak(ValueError):
    pass


def catat_aksi(data, worker_id, device_code):
    """Catat satu aksi (idempoten per `aksi_id`). Hasil: (aksi, baru?)."""
    try:
        aksi_id = uuid.UUID(str(data.get("aksi_id")))
    except (TypeError, ValueError):
        raise AksiDitolak("aksi_id tidak sah.")
    lama = AksiPiutang.objects.filter(aksi_id=aksi_id).first()
    if lama is not None:
        return lama, False
    jenis = data.get("jenis")
    if jenis not in ("bayar", "serahkan"):
        raise AksiDitolak("Jenis aksi tidak dikenal.")
    kunci = str(data.get("kunci") or "")
    piutang_id, sync_id = None, None
    if kunci.startswith("p") and kunci[1:].isdigit():
        piutang_id = int(kunci[1:])
    elif kunci.startswith("k"):
        try:
            sync_id = uuid.UUID(kunci[1:])
        except ValueError:
            raise AksiDitolak("Nota tidak dikenal.")
    else:
        raise AksiDitolak("Nota tidak dikenal.")
    jumlah, cara = 0, ""
    if jenis == "bayar":
        try:
            jumlah = int(data.get("jumlah"))
        except (TypeError, ValueError):
            raise AksiDitolak("Jumlah bayar tidak sah.")
        if jumlah <= 0:
            raise AksiDitolak("Isi jumlah yang dibayar.")
        cara = data.get("cara") or "cash"
        if cara not in CARA:
            raise AksiDitolak("Cara bayar tidak dikenal.")
    from django.utils.dateparse import parse_datetime
    waktu = parse_datetime(str(data.get("waktu") or "")) or timezone.now()
    if timezone.is_naive(waktu):
        waktu = timezone.make_aware(waktu)
    a = AksiPiutang.objects.create(
        aksi_id=aksi_id, jenis=jenis, piutang_id=piutang_id, sync_id=sync_id, jumlah=jumlah, cara=cara,
        catatan=str(data.get("catatan") or "")[:300], worker_id=str(worker_id), device_code=device_code or "",
        waktu=min(waktu, timezone.now()))
    return a, True
