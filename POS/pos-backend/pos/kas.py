"""Kas laci toko (rancangan 3 Nov 2026).

Satu sesi kas per toko: buka kas (modal) -> uang masuk/keluar -> tutup kas
(uang diambil + uang sisa, sisa jadi modal besok) -> ACC pemilik.

- Penjualan tidak pernah diblokir. Uang yang masuk sejak tutup kas sebelumnya
  dihitung ke sesi berikutnya (`KasSesi.awal_hitung`).
- Sisa minimal per toko (master Toko, bawaan Rp400.000): uang yang ditinggal di
  laci saat tutup tidak boleh kurang dari ini (untuk keperluan pagi). Pengganti
  "cadangan" terpisah dari rancangan awal (diubah 3 Nov 2026).
- Tablet tidak pernah melihat angka seharusnya / selisih (hitung buta); hanya
  pemilik (master/HP) yang melihatnya.

Modul ini dipakai server kasir (config.settings) dan master. Tabel master
dibaca lewat SQL dan hanya kalau ada (server kasir tidak memuat app master).
"""
import datetime
import uuid

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import pengaturan
from .models import (AksiPiutang, DeviceStatus, KasGerak, KasSesi, NotaTinjau, RefundEvent, Transactions,
                     Worker)

CADANGAN_BAWAAN = 400000
KATEGORI_KELUAR = [
    "Diambil pemilik",
    "Belanja keperluan toko",
    "Ongkir / bensin",
    "Makan & minum",
    "Bayar supplier / barang",
    "Lainnya",
]
JENIS_TABLET = ("keluar", "tambah_modal")
# Laporan antrean tablet lebih tua dari ini dianggap basi: tablet itu mungkin
# sudah lama mati. Tetap ditampilkan, tetapi pemilik bisa memaksa tutup.
ANTREAN_BASI = datetime.timedelta(hours=12)


class KasDitolak(Exception):
    """Permintaan kas tidak bisa dijalankan (pesannya untuk pengguna)."""


class AntreanBelumKosong(KasDitolak):
    def __init__(self, tablet):
        self.tablet = tablet
        nama = ", ".join(t["nama"] for t in tablet)
        super().__init__(
            f"Masih ada nota yang belum terkirim di: {nama}. Sambungkan tablet itu ke jaringan dan tunggu "
            "sampai antreannya kosong, baru tutup kas.")


def rp(n):
    n = int(n or 0)
    return ("−" if n < 0 else "") + "Rp" + f"{abs(n):,}".replace(",", ".")


def _baris(sql, param=()):
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sql, list(param))
            kolom = [c[0] for c in cursor.description]
            return [dict(zip(kolom, r)) for r in cursor.fetchall()]
    except DatabaseError:
        return []


def _ada(tabel):
    try:
        with connection.cursor() as cursor:
            return tabel in connection.introspection.table_names(cursor)
    except DatabaseError:
        return False


def toko_kasir():
    """id toko yang memegang kasir (stok_di_kasir). Satu toko selama kasir belum multi-toko."""
    if not _ada("master_toko"):
        return None
    benar = "stok_di_kasir" if connection.vendor != "sqlite" else "stok_di_kasir = 1"
    r = _baris(f"SELECT id FROM master_toko WHERE {benar} ORDER BY id LIMIT 1")
    return r[0]["id"] if r else None


SISA_MINIMAL_BAWAAN = 400000


def sisa_minimal(toko_id):
    """Uang minimal yang ditinggal di laci saat tutup kas (diatur per toko di master)."""
    if toko_id is None or not _ada("master_toko"):
        return SISA_MINIMAL_BAWAAN
    r = _baris("SELECT kas_sisa_minimal FROM master_toko WHERE id = %s", [toko_id])
    if not r or r[0].get("kas_sisa_minimal") is None:
        return SISA_MINIMAL_BAWAAN
    return max(0, int(r[0]["kas_sisa_minimal"]))


def cadangan_nominal():
    try:
        return max(0, int(pengaturan.baca("kas_cadangan") or CADANGAN_BAWAAN))
    except (TypeError, ValueError):
        return CADANGAN_BAWAAN


def _int(nilai, nama, *, minimal=0):
    try:
        n = int(str(nilai if nilai not in (None, "") else 0).replace(".", "").strip())
    except (TypeError, ValueError):
        raise KasDitolak(f"{nama} tidak sah.")
    if n < minimal:
        raise KasDitolak(f"{nama} tidak boleh kurang dari {minimal}.")
    return n


def _uuid(nilai):
    try:
        return uuid.UUID(str(nilai))
    except (TypeError, ValueError):
        raise KasDitolak("Sesi kas tidak dikenal.")


def nama_kasir(worker_id):
    return Worker.objects.filter(id=worker_id).values_list("name", flat=True).first() or str(worker_id or "")


# ---------------------------------------------------------------------------
# Sesi
# ---------------------------------------------------------------------------


def sesi_buka(toko_id):
    return KasSesi.objects.filter(toko_id=toko_id, status="buka").first()


def sesi_tutup_terakhir(toko_id):
    return KasSesi.objects.filter(toko_id=toko_id, status="ditutup").order_by("-ditutup_waktu").first()


def modal_saran(toko_id):
    """Modal hari ini = uang sisa saat tutup kas terakhir."""
    lalu = sesi_tutup_terakhir(toko_id)
    return lalu.uang_sisa if lalu else 0


def cadangan_terpakai(toko_id):
    """Cadangan yang sudah dipakai dan belum diisi lagi."""
    q = KasGerak.objects.filter(toko_id=toko_id, batal=False)
    pakai = q.filter(jenis="pakai_cadangan").aggregate(n=Sum("jumlah"))["n"] or 0
    isi = q.filter(jenis="isi_cadangan").aggregate(n=Sum("jumlah"))["n"] or 0
    return max(0, pakai - isi)


def ada_tablet_kas():
    """Ada tablet yang boleh buka/tutup kas? Kalau tidak, kas dibuka/ditutup pemilik (master/HP)."""
    return DeviceStatus.objects.filter(aktif=True, boleh_kas=True).exists()


def _jendela(sesi, akhir=None):
    return sesi.awal_hitung, akhir or sesi.ditutup_waktu or timezone.now()


def _waktu_payload(payload):
    w = parse_datetime(str((payload or {}).get("transaction_time") or ""))
    if w is not None and timezone.is_naive(w):
        w = timezone.make_aware(w)
    return w


def hitung(sesi, akhir=None):
    """Angka laci untuk satu sesi (seharusnya ada di laci + rinciannya)."""
    awal, akhir = _jendela(sesi, akhir)
    kasir = sesi.toko_id is not None and sesi.toko_id == toko_kasir()
    r = {"awal": awal.isoformat(), "akhir": akhir.isoformat(), "modal_awal": sesi.modal_awal,
         "tunai_nota": 0, "jumlah_nota_tunai": 0, "tunai_ditahan": 0, "jumlah_ditahan": 0,
         "pelunasan_tunai": 0, "refund_tunai": 0, "edc": 0, "transfer": 0, "jumlah_transfer": 0,
         "keluar": 0, "tambah_modal": 0, "pakai_cadangan": 0, "isi_cadangan": 0, "per_kategori": {}}
    if kasir:
        nota = Transactions.objects.filter(transaction_time__gt=awal, transaction_time__lte=akhir)
        for n in nota.values("payment_method", "payment_amount", "change_amount"):
            bersih = int(n["payment_amount"] or 0) - int(n["change_amount"] or 0)
            if n["payment_method"] == "cash":
                r["tunai_nota"] += bersih
                r["jumlah_nota_tunai"] += 1
            elif n["payment_method"] == "edc":
                r["edc"] += bersih
            elif n["payment_method"] == "bank_transfer":
                r["transfer"] += bersih
                r["jumlah_transfer"] += 1
        # Nota final di tablet yang ditahan server: uangnya sudah diterima kasir.
        for t in NotaTinjau.objects.filter(status__in=("menunggu", "ditolak"),
                                           masuk__gt=awal - datetime.timedelta(days=3)).only("payload", "masuk"):
            p = t.payload or {}
            w = _waktu_payload(p) or t.masuk
            if not (awal < w <= akhir) or p.get("payment_method") != "cash":
                continue
            try:
                r["tunai_ditahan"] += int(p.get("payment_amount") or 0) - int(p.get("change_amount") or 0)
                r["jumlah_ditahan"] += 1
            except (TypeError, ValueError):
                continue
        for a in AksiPiutang.objects.filter(jenis="bayar", waktu__gt=awal, waktu__lte=akhir).exclude(
                status="ditolak").values("cara", "jumlah"):
            if a["cara"] == "cash":
                r["pelunasan_tunai"] += a["jumlah"]
            elif a["cara"] == "edc":
                r["edc"] += a["jumlah"]
            elif a["cara"] == "bank_transfer":
                r["transfer"] += a["jumlah"]
        r["refund_tunai"] = RefundEvent.objects.filter(
            refund_method="cash", created_at__gt=awal, created_at__lte=akhir).aggregate(n=Sum("refund_amount"))["n"] or 0
    for g in KasGerak.objects.filter(toko_id=sesi.toko_id, batal=False, waktu__gt=awal, waktu__lte=akhir):
        r[g.jenis] += g.jumlah
        if g.jenis == "keluar":
            r["per_kategori"][g.kategori or "Lainnya"] = r["per_kategori"].get(g.kategori or "Lainnya", 0) + g.jumlah
    r["seharusnya"] = (sesi.modal_awal + r["tunai_nota"] + r["tunai_ditahan"] + r["pelunasan_tunai"]
                       - r["refund_tunai"] - r["keluar"] + r["tambah_modal"] + r["pakai_cadangan"]
                       - r["isi_cadangan"])
    return r


def gerak_sesi(sesi):
    awal, akhir = _jendela(sesi)
    return list(KasGerak.objects.filter(toko_id=sesi.toko_id, waktu__gt=awal, waktu__lte=akhir))


def _toko_wajib(toko_id):
    if toko_id is None:
        raise KasDitolak("Toko kasir belum diatur di master (Toko > stok di kasir).")
    return toko_id


@transaction.atomic
def buka(toko_id, *, modal, cadangan_lengkap, oleh, dari, catatan="", pakai_cadangan=0, worker_id=""):
    _toko_wajib(toko_id)
    modal = _int(modal, "Modal")
    pakai = _int(pakai_cadangan, "Cadangan dipakai")
    if pakai > cadangan_nominal():
        raise KasDitolak(f"Cadangan hanya {rp(cadangan_nominal())}.")
    if sesi_buka(toko_id) is not None:
        raise KasDitolak("Kas toko ini sudah dibuka.")
    sekarang = timezone.now()
    lalu = sesi_tutup_terakhir(toko_id)
    try:
        with transaction.atomic():
            s = KasSesi.objects.create(
                toko_id=toko_id, status="buka",
                # Kas pertama: mulai sedetik sebelum buka supaya "pakai cadangan" saat buka ikut terhitung.
                awal_hitung=lalu.ditutup_waktu if lalu else sekarang - datetime.timedelta(seconds=1),
                dibuka_waktu=sekarang, dibuka_oleh=oleh[:150], dibuka_dari=dari[:20],
                modal_saran=lalu.uang_sisa if lalu else 0, modal_awal=modal,
                cadangan_lengkap_buka=bool(cadangan_lengkap), catatan_buka=str(catatan or "")[:300])
    except IntegrityError:
        raise KasDitolak("Kas toko ini sudah dibuka.")
    if pakai:
        KasGerak.objects.create(gerak_id=uuid.uuid4(), toko_id=toko_id, jenis="pakai_cadangan", jumlah=pakai,
                                keterangan="Dipakai sebagai modal saat buka kas", worker_id=worker_id or "",
                                oleh=oleh[:150], device_code=dari[:20], waktu=sekarang)
    return s


def antrean_tertunda(kecuali=None):
    """Tablet aktif yang melaporkan nota/aksi belum terkirim: [{"nama", "jumlah", "basi"}]."""
    from .models import Device
    kode = dict(Device.objects.values_list("id", "device_code"))
    batas = timezone.now() - ANTREAN_BASI
    hasil = []
    for s in DeviceStatus.objects.filter(aktif=True, antrean__gt=0):
        if kecuali is not None and s.device_id == kecuali:
            continue
        hasil.append({"nama": s.nama or kode.get(s.device_id, "tablet"), "jumlah": s.antrean,
                      "basi": bool(s.antrean_waktu and s.antrean_waktu < batas)})
    return hasil


def lapor_antrean(device_id, jumlah):
    try:
        n = max(0, int(jumlah))
    except (TypeError, ValueError):
        return
    DeviceStatus.objects.update_or_create(device_id=device_id,
                                          defaults={"antrean": n, "antrean_waktu": timezone.now()})


@transaction.atomic
def tutup(sesi_id, *, uang_sisa, oleh, dari, diambil=None, ambil_100=0, ambil_50=0, cadangan_lengkap=True,
          catatan="", isi_cadangan=0, worker_id="", paksa=False, device_id=None):
    """Tutup kas: uang yang diambil (rupiah) + uang sisa di laci. `ambil_100`/`ambil_50` hanya untuk
    tablet lama yang masih mengirim jumlah lembar."""
    s = KasSesi.objects.select_for_update().filter(pk=_uuid(sesi_id)).first()
    if s is None or s.status != "buka":
        raise KasDitolak("Kas ini sudah ditutup.")
    if uang_sisa in (None, ""):
        raise KasDitolak("Isi uang sisa di laci.")
    a100 = _int(ambil_100, "Lembar 100 ribu")
    a50 = _int(ambil_50, "Lembar 50 ribu")
    ambil = _int(diambil, "Uang diambil")
    sisa = _int(uang_sisa, "Uang sisa")
    isi = _int(isi_cadangan, "Isi cadangan")
    minimal = sisa_minimal(s.toko_id)
    total = ambil + a100 * 100000 + a50 * 50000 + sisa
    if sisa < min(minimal, total):
        raise KasDitolak(f"Uang sisa di laci minimal {rp(minimal)} untuk keperluan pagi. "
                         f"Kurangi uang yang diambil.")
    if not paksa:
        tertunda = antrean_tertunda(kecuali=device_id)
        if tertunda:
            raise AntreanBelumKosong(tertunda)
    sekarang = timezone.now()
    if isi:
        KasGerak.objects.create(gerak_id=uuid.uuid4(), toko_id=s.toko_id, jenis="isi_cadangan", jumlah=isi,
                                keterangan="Diisi dari laci saat tutup kas", worker_id=worker_id or "",
                                oleh=oleh[:150], device_code=dari[:20], waktu=sekarang)
    s.ditutup_waktu = sekarang
    s.ambil_100, s.ambil_50, s.diambil_uang, s.uang_sisa = a100, a50, ambil, sisa
    s.cadangan_lengkap_tutup = bool(cadangan_lengkap)
    s.ditutup_oleh, s.ditutup_dari = oleh[:150], dari[:20]
    s.catatan_tutup = str(catatan or "")[:300]
    s.rincian = hitung(s)
    s.rincian["paksa"] = bool(paksa)
    s.seharusnya = s.rincian["seharusnya"]
    s.selisih = s.dihitung - s.seharusnya
    s.status = "ditutup"
    s.acc_status = "menunggu"
    s.save()
    return s


def acc(sesi_id, *, setuju, oleh, catatan=""):
    with transaction.atomic():
        s = KasSesi.objects.select_for_update().filter(pk=_uuid(sesi_id)).first()
        if s is None or s.status != "ditutup":
            raise KasDitolak("Kas belum ditutup.")
        if s.acc_status != "menunggu":
            raise KasDitolak("Tutup kas ini sudah diputuskan.")
        catatan = str(catatan or "").strip()
        if not setuju and not catatan:
            raise KasDitolak("Tulis alasan menolak.")
        s.acc_status = "diterima" if setuju else "ditolak"
        s.acc_oleh, s.acc_waktu, s.acc_catatan = oleh[:150], timezone.now(), catatan[:300]
        s.save(update_fields=["acc_status", "acc_oleh", "acc_waktu", "acc_catatan"])
    return s


# ---------------------------------------------------------------------------
# Uang keluar / tambah modal / cadangan
# ---------------------------------------------------------------------------


def _sesi_tertutup_untuk(toko_id, waktu):
    """Sesi yang sudah ditutup dan jendelanya mencakup `waktu` (angkanya sudah dibekukan)."""
    return KasSesi.objects.filter(toko_id=toko_id, status="ditutup", awal_hitung__lt=waktu,
                                  ditutup_waktu__gte=waktu).first()


def catat_gerak(data, *, toko_id, oleh, dari, worker_id="", jenis_boleh=None):
    """Catat satu gerak kas (idempoten per `gerak_id`). Hasil: (gerak, baru?)."""
    _toko_wajib(toko_id)
    try:
        gerak_id = uuid.UUID(str(data.get("gerak_id")))
    except (TypeError, ValueError):
        raise KasDitolak("gerak_id tidak sah.")
    lama = KasGerak.objects.filter(gerak_id=gerak_id).first()
    if lama is not None:
        return lama, False
    jenis = data.get("jenis") or "keluar"
    if jenis not in dict(KasGerak.JENIS) or (jenis_boleh is not None and jenis not in jenis_boleh):
        raise KasDitolak("Jenis catatan kas tidak dikenal.")
    jumlah = _int(data.get("jumlah"), "Jumlah", minimal=1)
    kategori = str(data.get("kategori") or "").strip()[:40]
    keterangan = str(data.get("keterangan") or "").strip()[:300]
    if jenis == "keluar":
        if kategori not in KATEGORI_KELUAR:
            raise KasDitolak("Pilih kategori uang keluar.")
        if kategori == "Lainnya" and not keterangan:
            raise KasDitolak("Tulis keterangan untuk uang keluar lainnya.")
    else:
        kategori = ""
    waktu = parse_datetime(str(data.get("waktu") or "")) or timezone.now()
    if timezone.is_naive(waktu):
        waktu = timezone.make_aware(waktu)
    waktu = min(waktu, timezone.now())
    tertutup = _sesi_tertutup_untuk(toko_id, waktu)
    if tertutup is not None:
        # Kas sudah ditutup sesudah kejadian ini (antrean offline yang telat):
        # catat sekarang supaya masuk ke kas yang sedang berjalan, bukan mengubah angka yang sudah dibekukan.
        keterangan = (keterangan + f" (terjadi {timezone.localtime(waktu):%d/%m %H.%M}, tercatat telat)").strip()[:300]
        waktu = timezone.now()
    try:
        with transaction.atomic():
            g = KasGerak.objects.create(gerak_id=gerak_id, toko_id=toko_id, jenis=jenis, jumlah=jumlah,
                                        kategori=kategori, keterangan=keterangan, worker_id=str(worker_id or ""),
                                        oleh=oleh[:150], device_code=dari[:20], waktu=waktu)
    except IntegrityError:
        return KasGerak.objects.get(gerak_id=gerak_id), False
    return g, True


def batal_gerak(gerak_id, *, oleh, alasan):
    g = KasGerak.objects.filter(gerak_id=_uuid(gerak_id)).first()
    if g is None:
        raise KasDitolak("Catatan kas tidak ditemukan.")
    if g.batal:
        raise KasDitolak("Catatan ini sudah dibatalkan.")
    if _sesi_tertutup_untuk(g.toko_id, g.waktu) is not None:
        raise KasDitolak("Kas yang memuat catatan ini sudah ditutup. Koreksi lewat catatan ACC.")
    alasan = str(alasan or "").strip()
    if not alasan:
        raise KasDitolak("Tulis alasan membatalkan.")
    g.batal, g.batal_oleh, g.batal_alasan = True, oleh[:150], alasan[:300]
    g.save(update_fields=["batal", "batal_oleh", "batal_alasan"])
    return g


# ---------------------------------------------------------------------------
# Untuk tablet
# ---------------------------------------------------------------------------


def _gerak_json(g):
    return {"gerak_id": str(g.gerak_id), "jenis": g.jenis, "jumlah": g.jumlah, "kategori": g.kategori,
            "keterangan": g.keterangan, "oleh": g.oleh, "waktu": g.waktu.isoformat(), "batal": g.batal}


def status_tablet(device_id):
    """Yang boleh dilihat tablet: status sesi, modal, uang keluar. Tanpa angka seharusnya/selisih."""
    toko_id = toko_kasir()
    s = sesi_buka(toko_id) if toko_id is not None else None
    st = DeviceStatus.objects.filter(device_id=device_id).first() if device_id else None
    lalu = sesi_tutup_terakhir(toko_id) if toko_id is not None else None
    hasil = {
        "toko_id": toko_id,
        "boleh_kas": bool(st and st.boleh_kas),
        "mode_pemilik": not ada_tablet_kas(),
        "kategori": KATEGORI_KELUAR,
        "sisa_minimal": sisa_minimal(toko_id),
        "cadangan": cadangan_nominal(),
        "cadangan_terpakai": cadangan_terpakai(toko_id) if toko_id is not None else 0,
        "modal_saran": lalu.uang_sisa if lalu else 0,
        "tutup_terakhir": lalu.ditutup_waktu.isoformat() if lalu else None,
        "sesi": None,
    }
    if s is not None:
        hasil["sesi"] = {
            "id": str(s.id), "dibuka_waktu": s.dibuka_waktu.isoformat(), "dibuka_oleh": s.dibuka_oleh,
            "modal_awal": s.modal_awal, "gerak": [_gerak_json(g) for g in gerak_sesi(s) if not g.batal],
        }
    return hasil
