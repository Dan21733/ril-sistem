"""Siapkan barang: transaksi berlangsung setelah nota kasir dicetak (keputusan pemilik 11 Nov 2026).

Setiap nota kasir otomatis masuk daftar Siapkan di aplikasi gudang. Pelayan yang menjual wajib
menyiapkan barangnya; nota tidak boleh ditinggal. Barang dikelompokkan per lokasi (dari rak utama
barang). Tiap kelompok:
  - ditugaskan ke satu karyawan lapangan, dengan REKOMENDASI sistem (tim lokasi, beban hari ini),
    supaya pembagian kerja rata dan "sistem yang menunjuk", bukan pelayan;
  - atau disiapkan pelayan sendiri (barang display);
  - lalu dicentang Diserahkan.
Yang dilewati (tidak ada / sedang tugas lain / menolak) dan pilihan di luar rekomendasi tercatat
untuk evaluasi pemilik.

Belum mengurangi stok per lokasi: itu menyusul setelah opname Desember (stok per lokasi sudah benar).
Nota DP yang barangnya diambil nanti / dipesan dulu tidak masuk (ditangani alur piutang).
"""
from collections import defaultdict
from datetime import datetime, time, timedelta

from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from master.layanan.inti import Tolak, rapikan_teks
from master.models import (KaryawanLapangan, Lokasi, Siapkan, SiapkanBaris, SiapkanLewat, SiapkanTugas,
                           VarianInfo)

# Rekomendasi pindah ke luar tim kalau anggota tim sudah lebih banyak tugas sebanyak ini dari yang paling jarang.
SELISIH_PEMERATAAN = 3
KUNCI_MULAI = "siapkan_mulai"


def _awal_hari(tanggal=None):
    tanggal = tanggal or timezone.localdate()
    return timezone.make_aware(datetime.combine(tanggal, time.min))


def _mulai_tarik():
    """Batas nota yang ditarik: sejak fitur dipakai (disimpan sekali), paling jauh kemarin pagi."""
    from pos.models import PosSetting
    s = PosSetting.objects.filter(kunci=KUNCI_MULAI).first()
    if s is None:
        mulai = timezone.now() - timedelta(minutes=30)
        PosSetting.objects.create(kunci=KUNCI_MULAI, nilai=mulai.isoformat())
    else:
        mulai = datetime.fromisoformat(s.nilai)
    return max(mulai, _awal_hari() - timedelta(days=1))


def tarik_nota():
    """Nota kasir baru -> Siapkan. Aman dipanggil berulang (tiap halaman dibuka). Nota void -> batal."""
    from pos.models import NotaPiutang, RefundEvent, TransactionDetail, Transactions, Worker
    mulai = _mulai_tarik()
    ada = set(Siapkan.objects.filter(waktu_nota__gte=mulai - timedelta(hours=1)).values_list("sync_id", flat=True))
    baru = [n for n in Transactions.objects.filter(transaction_time__gte=mulai) if n.sync_id not in ada]
    if baru:
        nama_kasir = dict(Worker.objects.filter(id__in={n.worker_id for n in baru}).values_list("id", "name"))
        ids = [n.sync_id for n in baru]
        piutang = dict(NotaPiutang.objects.filter(sync_id__in=ids).values_list("sync_id", "status_barang"))
        void = set(RefundEvent.objects.filter(sync_id__in=ids, event_type="void").values_list("sync_id", flat=True))
        baris = defaultdict(list)
        for d in TransactionDetail.objects.filter(sync_id__in=ids).values("sync_id", "item_name", "variant", "quantity"):
            baris[d["sync_id"]].append(d)
        info = {(v.item_name, v.variant): v for v in VarianInfo.objects.filter(
            item_name__in={d["item_name"] for b in baris.values() for d in b}).select_related("rak")}
        for n in sorted(baru, key=lambda n: n.transaction_time):
            cara = piutang.get(n.sync_id) or "bawa"
            if cara in ("ambil", "pesan") or n.sync_id in void or not baris.get(n.sync_id):
                continue   # diambil nanti / dipesan: lewat alur piutang
            _buat(n, cara, baris[n.sync_id], info, nama_kasir.get(n.worker_id, ""))
    # Nota yang di-void setelah masuk daftar: tidak perlu disiapkan lagi.
    buka = list(Siapkan.objects.filter(status="menunggu").values_list("sync_id", flat=True))
    if buka:
        batal = set(RefundEvent.objects.filter(sync_id__in=buka, event_type="void").values_list("sync_id", flat=True))
        if batal:
            sp = Siapkan.objects.filter(sync_id__in=batal, status="menunggu")
            SiapkanTugas.objects.filter(siapkan__in=sp).exclude(status="selesai").update(status="batal")
            sp.update(status="batal", selesai_waktu=timezone.now(), selesai_oleh="nota di-void")


def _buat(n, cara, baris, info, kasir_nama=""):
    with transaction.atomic():
        s, dibuat = Siapkan.objects.get_or_create(sync_id=n.sync_id, defaults={
            "nomor": n.transaction_id or "", "waktu_nota": n.transaction_time, "kasir_id": n.worker_id or "",
            "kasir_nama": kasir_nama or "", "pelanggan": (n.cust_name or "")[:150], "cara": cara})
        if not dibuat:
            return s
        per_lokasi = defaultdict(list)
        for d in baris:
            v = info.get((d["item_name"], d["variant"]))
            lokasi_id = v.rak.tempat_id if v is not None and v.rak is not None else None
            per_lokasi[lokasi_id].append((v, d))
        for lokasi_id in sorted(per_lokasi, key=lambda x: (x is None, x or 0)):
            t = SiapkanTugas.objects.create(siapkan=s, lokasi_id=lokasi_id)
            SiapkanBaris.objects.bulk_create([
                SiapkanBaris(tugas=t, varian=v, item_name=d["item_name"], variant=d["variant"], jumlah=d["quantity"])
                for v, d in per_lokasi[lokasi_id]])
    return s


# ---------------------------------------------------------------------------
# Rekomendasi
# ---------------------------------------------------------------------------


def beban_hari_ini():
    """{karyawan_id: jumlah tugas hari ini}, {id: waktu tugas terakhir}, set id yang sedang mengambil."""
    qs = SiapkanTugas.objects.filter(pengambil__isnull=False).exclude(status="batal")
    hari = qs.filter(ditugaskan_waktu__gte=_awal_hari()).values("pengambil").annotate(n=Count("id"))
    beban = {r["pengambil"]: r["n"] for r in hari}
    terakhir = dict(qs.values("pengambil").annotate(t=Max("ditugaskan_waktu")).values_list("pengambil", "t"))
    sibuk = set(qs.filter(status="diambil").values_list("pengambil", flat=True))
    return beban, terakhir, sibuk


def rekomendasi(tugas, keadaan=None, karyawan=None):
    """Daftar karyawan lapangan berurutan; yang pertama = rekomendasi. Tiap isi: dict k, alasan, beban, tim, sibuk.

    Urutan: anggota tim lokasi yang tidak sedang mengambil, tugas hari ini paling sedikit, paling lama
    menganggur. Kalau anggota tim terpilih sudah SELISIH_PEMERATAAN tugas lebih banyak dari orang luar tim
    yang paling jarang, yang luar tim direkomendasikan (pemerataan). Yang dilewati untuk tugas ini disisihkan.
    """
    beban, terakhir, sibuk = keadaan or beban_hari_ini()
    karyawan = karyawan if karyawan is not None else list(KaryawanLapangan.objects.filter(aktif=True))
    tim = set(tugas.lokasi.tim.values_list("id", flat=True)) if tugas.lokasi_id else set()
    dilewati = set(SiapkanLewat.objects.filter(tugas=tugas).values_list("karyawan_id", flat=True))
    awal = timezone.make_aware(datetime(2000, 1, 1))
    kunci = lambda k: (beban.get(k.id, 0), terakhir.get(k.id) or awal, k.nama)
    bebas = [k for k in karyawan if k.id not in sibuk and k.id not in dilewati]
    tim_bebas = sorted([k for k in bebas if k.id in tim], key=kunci)
    luar_bebas = sorted([k for k in bebas if k.id not in tim], key=kunci)
    pilih, alasan = None, ""
    if tim_bebas:
        pilih, alasan = tim_bebas[0], "tim lokasi"
        if luar_bebas and beban.get(pilih.id, 0) - beban.get(luar_bebas[0].id, 0) >= SELISIH_PEMERATAAN:
            pilih, alasan = luar_bebas[0], "pemerataan"
    elif luar_bebas:
        pilih, alasan = luar_bebas[0], ("tim sedang sibuk" if tim else "tugas paling sedikit")
    sisa = [k for k in tim_bebas + luar_bebas if k != pilih]
    sisa += sorted([k for k in karyawan if k.id in sibuk and k.id not in dilewati], key=kunci)
    urut = ([pilih] if pilih else []) + sisa
    return [{"k": k, "alasan": alasan if k == pilih else "", "beban": beban.get(k.id, 0), "tim": k.id in tim,
             "sibuk": k.id in sibuk} for k in urut]


# ---------------------------------------------------------------------------
# Aksi pelayan
# ---------------------------------------------------------------------------


def _nama(pengguna):
    return pengguna.first_name or pengguna.username


def _tugas_buka(tugas_id):
    t = SiapkanTugas.objects.select_related("siapkan", "lokasi").filter(pk=tugas_id).first()
    if t is None or t.siapkan.status != "menunggu":
        raise Tolak("Nota ini sudah selesai atau dibatalkan.")
    return t


def tugaskan(pengguna, tugas_id, karyawan_id):
    t = _tugas_buka(tugas_id)
    if t.status not in ("menunggu", "diambil"):
        raise Tolak("Kelompok ini sudah diserahkan.")
    k = KaryawanLapangan.objects.filter(pk=karyawan_id or 0, aktif=True).first()
    if k is None:
        raise Tolak("Pilih karyawan yang mengambil.")
    rek = rekomendasi(t)
    saran = rek[0]["k"] if rek else None
    t.pengambil, t.sendiri, t.rekomendasi = k, False, saran
    t.ikut_rekomendasi = saran is None or saran.pk == k.pk
    t.status, t.ditugaskan_waktu, t.ditugaskan_oleh = "diambil", timezone.now(), _nama(pengguna)
    t.save()
    return t


def sendiri(pengguna, tugas_id):
    """Pelayan menyiapkan sendiri (barang display / sedikit)."""
    t = _tugas_buka(tugas_id)
    if t.status == "selesai":
        raise Tolak("Kelompok ini sudah diserahkan.")
    t.pengambil, t.sendiri, t.status = None, True, "diambil"
    t.ditugaskan_waktu, t.ditugaskan_oleh = timezone.now(), _nama(pengguna)
    t.save()
    return t


def lewati(pengguna, tugas_id, karyawan_id, alasan):
    t = _tugas_buka(tugas_id)
    if alasan not in dict(SiapkanLewat.ALASAN):
        raise Tolak("Pilih alasannya.")
    k = KaryawanLapangan.objects.filter(pk=karyawan_id or 0).first()
    if k is None:
        raise Tolak("Karyawan tidak ditemukan.")
    SiapkanLewat.objects.create(tugas=t, karyawan=k, alasan=alasan, oleh=_nama(pengguna))
    return t


def ulang(pengguna, tugas_id):
    """Ganti orang: kelompok kembali belum ditugaskan (yang lama tidak dihitung dilewati)."""
    t = _tugas_buka(tugas_id)
    if t.status != "diambil":
        raise Tolak("Kelompok ini belum ditugaskan.")
    t.pengambil, t.sendiri, t.rekomendasi, t.status = None, False, None, "menunggu"
    t.ditugaskan_waktu, t.ditugaskan_oleh, t.ikut_rekomendasi = None, "", True
    t.save()
    return t


def serahkan(pengguna, tugas_id):
    """Barang kelompok ini sudah diserahkan ke pembeli. Dari 'belum ditugaskan' = disiapkan sendiri."""
    t = _tugas_buka(tugas_id)
    sekarang = timezone.now()
    with transaction.atomic():
        if t.status == "menunggu":
            t.sendiri, t.ditugaskan_waktu, t.ditugaskan_oleh = True, sekarang, _nama(pengguna)
        t.status, t.selesai_waktu, t.selesai_oleh = "selesai", sekarang, _nama(pengguna)
        t.save()
        _cek_selesai(t.siapkan, pengguna)
    return t


def serahkan_semua(pengguna, siapkan_id):
    s = Siapkan.objects.filter(pk=siapkan_id, status="menunggu").first()
    if s is None:
        raise Tolak("Nota ini sudah selesai atau dibatalkan.")
    for t in s.tugas.exclude(status__in=["selesai", "batal"]):
        serahkan(pengguna, t.pk)
    return s


def _cek_selesai(s, pengguna):
    if not s.tugas.exclude(status__in=["selesai", "batal"]).exists():
        s.status, s.selesai_waktu, s.selesai_oleh = "selesai", timezone.now(), _nama(pengguna)
        s.save(update_fields=["status", "selesai_waktu", "selesai_oleh"])


def tutup(pengguna, siapkan_id, catatan=""):
    """Pemilik menutup nota yang tertinggal di daftar (mis. sudah diserahkan tapi lupa dicentang)."""
    s = Siapkan.objects.filter(pk=siapkan_id, status="menunggu").first()
    if s is None:
        raise Tolak("Nota ini sudah selesai atau dibatalkan.")
    oleh = f"{_nama(pengguna)} (ditutup pemilik{': ' + rapikan_teks(catatan)[:80] if catatan else ''})"
    s.tugas.exclude(status__in=["selesai", "batal"]).update(status="selesai", selesai_waktu=timezone.now(), selesai_oleh=oleh)
    s.status, s.selesai_waktu, s.selesai_oleh = "selesai", timezone.now(), oleh
    s.save(update_fields=["status", "selesai_waktu", "selesai_oleh"])


# ---------------------------------------------------------------------------
# Tampilan
# ---------------------------------------------------------------------------


def daftar_buka():
    """Nota yang sedang disiapkan, lengkap dengan kelompok, baris, rak, dan rekomendasi."""
    daftar = list(Siapkan.objects.filter(status="menunggu").order_by("waktu_nota")
                  .prefetch_related("tugas__baris__varian__rak", "tugas__baris__varian__tempat_rak",
                                    "tugas__lokasi__pic", "tugas__pengambil"))
    keadaan = beban_hari_ini()
    karyawan = list(KaryawanLapangan.objects.filter(aktif=True))
    for s in daftar:
        s.daftar_tugas = list(s.tugas.all())
        for t in s.daftar_tugas:
            if t.status == "menunggu":
                t.pilihan = rekomendasi(t, keadaan, karyawan)
            t.daftar_baris = list(t.baris.all())
            for b in t.daftar_baris:
                v = b.varian
                b.rak_teks = ""
                if v is not None:
                    raks = ([v.rak_id] if v.rak_id else []) + [r.rak_id for r in v.tempat_rak.all() if r.rak_id != v.rak_id]
                    b.rak_teks = ", ".join(raks)
    return daftar


def selesai_hari_ini(limit=30):
    return list(Siapkan.objects.filter(status="selesai", selesai_waktu__gte=_awal_hari())
                .order_by("-selesai_waktu")[:limit])


def statistik(mulai, akhir):
    """Rekap per karyawan lapangan dan per pelayan pada rentang tanggal [mulai, akhir]."""
    awal, ujung = _awal_hari(mulai), _awal_hari(akhir) + timedelta(days=1)
    tugas = SiapkanTugas.objects.filter(ditugaskan_waktu__gte=awal, ditugaskan_waktu__lt=ujung).exclude(status="batal")
    per = {k.id: {"k": k, "tugas": 0, "selesai": 0, "menit": [], "tidak_ada": 0, "sibuk": 0, "menolak": 0,
                  "menolak_oleh": defaultdict(int), "hari": set()}
           for k in KaryawanLapangan.objects.all()}
    pelayan = defaultdict(lambda: {"tugas": 0, "sendiri": 0, "bukan_rek": 0, "menolak_dicatat": 0})
    for t in tugas.select_related("pengambil"):
        p = pelayan[t.ditugaskan_oleh or "-"]
        if t.sendiri:
            p["sendiri"] += 1
            continue
        p["tugas"] += 1
        if not t.ikut_rekomendasi:
            p["bukan_rek"] += 1
        if t.pengambil_id in per:
            r = per[t.pengambil_id]
            r["tugas"] += 1
            r["hari"].add(timezone.localtime(t.ditugaskan_waktu).date())
            if t.status == "selesai" and t.selesai_waktu:
                r["selesai"] += 1
                r["menit"].append((t.selesai_waktu - t.ditugaskan_waktu).total_seconds() / 60)
    for l in SiapkanLewat.objects.filter(waktu__gte=awal, waktu__lt=ujung):
        r = per.get(l.karyawan_id)
        if r is None:
            continue
        r[l.alasan] += 1
        if l.alasan == "menolak":
            r["menolak_oleh"][l.oleh] += 1
            pelayan[l.oleh]["menolak_dicatat"] += 1
    baris = []
    for r in per.values():
        if not r["k"].aktif and not r["tugas"]:
            continue
        r["menit_tengah"] = round(sorted(r["menit"])[len(r["menit"]) // 2]) if r["menit"] else None
        r["menolak_oleh"] = sorted(r["menolak_oleh"].items(), key=lambda x: -x[1])
        r["jumlah_hari"] = len(r["hari"])
        baris.append(r)
    baris.sort(key=lambda r: (-r["tugas"], r["k"].nama))
    angka = sorted(r["tugas"] for r in baris if r["k"].aktif)
    tengah = angka[len(angka) // 2] if angka else 0
    for r in baris:
        # Batas tetap (bukan "di bawah rata-rata"): kurang dari 60% nilai tengah = perlu ditanyakan.
        r["rendah"] = bool(tengah) and r["k"].aktif and r["tugas"] < 0.6 * tengah
    return {"karyawan": baris, "tengah": tengah,
            "pelayan": sorted(pelayan.items(), key=lambda x: -x[1]["tugas"])}


# ---------------------------------------------------------------------------
# Karyawan lapangan & tim
# ---------------------------------------------------------------------------


def simpan_karyawan(pengguna, *, id=None, nama, aktif=True, catatan=""):
    nama = rapikan_teks(nama)[:60]
    if not nama:
        raise Tolak("Isi nama karyawan.")
    lain = KaryawanLapangan.objects.filter(nama__iexact=nama)
    if id:
        lain = lain.exclude(pk=id)
    if lain.exists():
        raise Tolak(f"Nama {nama} sudah ada. Tambahkan pembeda, mis. {nama} B.")
    if id:
        k = KaryawanLapangan.objects.get(pk=id)
        k.nama, k.aktif, k.catatan = nama, bool(aktif), rapikan_teks(catatan)[:200]
        k.save()
    else:
        k = KaryawanLapangan.objects.create(nama=nama, aktif=True, catatan=rapikan_teks(catatan)[:200])
    if not k.aktif:
        k.lokasi_tim.clear()
    return k


def atur_tim(lokasi, karyawan_ids):
    ids = [int(x) for x in karyawan_ids if str(x).isdigit()]
    lokasi.tim.set(KaryawanLapangan.objects.filter(pk__in=ids, aktif=True))
