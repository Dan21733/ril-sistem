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
            item_name__in={d["item_name"] for b in baris.values() for d in b}).select_related("rak__tempat")}
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


def min_baris():
    """Nota dengan sebanyak ini baris barang atau lebih tetap disiapkan walau semuanya dari rak langsung."""
    from pos import pengaturan
    return max(1, pengaturan.angka("siapkan_min_baris", 5))


def _buat(n, cara, baris, info, kasir_nama=""):
    """Kelompok = (lokasi, PIC area) dari rak barang. Barang rak 'langsung' dilewati kecuali nota besar."""
    besar = len(baris) >= min_baris()
    with transaction.atomic():
        s, dibuat = Siapkan.objects.get_or_create(sync_id=n.sync_id, defaults={
            "nomor": n.transaction_id or "", "waktu_nota": n.transaction_time, "kasir_id": n.worker_id or "",
            "kasir_nama": kasir_nama or "", "pelanggan": (n.cust_name or "")[:150], "cara": cara})
        if not dibuat:
            return s
        per_kelompok = defaultdict(list)
        for d in baris:
            v = info.get((d["item_name"], d["variant"]))
            rak = v.rak if v is not None else None
            if rak is not None and rak.langsung and not besar:
                continue
            kunci = (rak.tempat_id, rak.pic_efektif_id) if rak is not None else (None, None)
            per_kelompok[kunci].append((v, d))
        if not per_kelompok:
            # Semua dari rak langsung (display): dicatat selesai supaya tidak ditarik lagi.
            s.status, s.selesai_waktu, s.selesai_oleh = "selesai", timezone.now(), "langsung (rak display)"
            s.save(update_fields=["status", "selesai_waktu", "selesai_oleh"])
            return s
        for kunci in sorted(per_kelompok, key=lambda x: (x[0] is None, x[0] or 0, x[1] or 0)):
            t = SiapkanTugas.objects.create(siapkan=s, lokasi_id=kunci[0], pic_id=kunci[1])
            SiapkanBaris.objects.bulk_create([
                SiapkanBaris(tugas=t, varian=v, item_name=d["item_name"], variant=d["variant"], jumlah=d["quantity"])
                for v, d in per_kelompok[kunci]])
    return s


def _akun_kasir(kasir_id):
    """User aplikasi gudang milik kasir pembuat nota (ops-<id>), kalau ada."""
    from django.contrib.auth import get_user_model
    return get_user_model().objects.filter(username=f"ops-{(kasir_id or '').lower()}").first()


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
    # Tim = anak buah PIC area barang ini; barang tanpa rak -> anak buah pelayan pembuat nota (kalau ia PIC).
    ketua_id = tugas.pic_id
    if ketua_id is None:
        akun = _akun_kasir(tugas.siapkan.kasir_id)
        ketua_id = akun.pk if akun else None
    tim = {k.id for k in karyawan if ketua_id and k.ketua_id == ketua_id}
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


# Halaman Siapkan tidak memakai login sesi (tablet bisa dipinjam). Yang mengikat orang hanya PIN:
# Diserahkan dan Menolak wajib PIN. Tugaskan / lewati "tidak ada" / "sibuk" tanpa PIN, dicatat atas
# nama kasir pembuat nota + perangkat (tidak memberatkan siapa pun).


def verifikasi_pin(pin, kunci):
    """PIN karyawan -> (worker_id, nama). Dibatasi per perangkat seperti masuk aplikasi gudang."""
    from pos.models import Worker
    from pos.pin_throttle import PinThrottled, check_pin_allowed, clear_pin_attempts, register_pin_failure
    from pos.worker_verification import InvalidWorkerCredentials, InvalidWorkerPinFormat, verify_worker_pin
    try:
        check_pin_allowed(kunci)
        wid = verify_worker_pin((pin or "").strip())
    except PinThrottled as e:
        raise Tolak(str(e))
    except InvalidWorkerPinFormat:
        raise Tolak("PIN harus 6 angka.")
    except InvalidWorkerCredentials:
        register_pin_failure(kunci)
        raise Tolak("PIN salah.")
    clear_pin_attempts(kunci)
    nama = Worker.objects.filter(id=wid).values_list("name", flat=True).first() or wid
    return wid, nama


def _tugas_buka(tugas_id):
    t = SiapkanTugas.objects.select_related("siapkan", "lokasi").filter(pk=str(tugas_id or "0").isdigit() and tugas_id or 0).first()
    if t is None or t.siapkan.status != "menunggu":
        raise Tolak("Nota ini sudah selesai atau dibatalkan.")
    return t


def tugaskan(tugas_id, karyawan_id, *, perangkat=""):
    t = _tugas_buka(tugas_id)
    if t.status not in ("menunggu", "diambil"):
        raise Tolak("Kelompok ini sudah diserahkan.")
    k = KaryawanLapangan.objects.filter(pk=karyawan_id if str(karyawan_id or "").isdigit() else 0, aktif=True).first()
    if k is None:
        raise Tolak("Pilih karyawan yang mengambil.")
    rek = rekomendasi(t)
    saran = rek[0]["k"] if rek and rek[0]["alasan"] else None
    t.pengambil, t.sendiri, t.rekomendasi = k, False, saran
    # 3 teratas yang tidak sedang mengambil = rekomendasi (SOP: nomor 1; nomor 2 dan 3 juga sah).
    t.ikut_rekomendasi = saran is None or k.pk in {r["k"].pk for r in teratas(rek)}
    t.status, t.ditugaskan_waktu, t.ditugaskan_oleh = "diambil", timezone.now(), t.siapkan.kasir_nama
    t.perangkat = perangkat[:100]
    t.save()
    return t


def sendiri(tugas_id, *, perangkat=""):
    """Pelayan menyiapkan sendiri (barang display / sedikit)."""
    t = _tugas_buka(tugas_id)
    if t.status == "selesai":
        raise Tolak("Kelompok ini sudah diserahkan.")
    t.pengambil, t.sendiri, t.status = None, True, "diambil"
    t.ditugaskan_waktu, t.ditugaskan_oleh, t.perangkat = timezone.now(), t.siapkan.kasir_nama, perangkat[:100]
    t.save()
    return t


def lewati(tugas_id, karyawan_id, alasan, *, perangkat="", pin="", kunci_pin=""):
    """Lewati karyawan yang direkomendasikan. 'Menolak' wajib PIN: catatan itu memberatkan orang."""
    t = _tugas_buka(tugas_id)
    if alasan not in dict(SiapkanLewat.ALASAN):
        raise Tolak("Pilih alasannya.")
    k = KaryawanLapangan.objects.filter(pk=karyawan_id if str(karyawan_id or "").isdigit() else 0).first()
    if k is None:
        raise Tolak("Karyawan tidak ditemukan.")
    oleh = t.siapkan.kasir_nama
    if alasan == "menolak":
        _, oleh = verifikasi_pin(pin, kunci_pin)
    SiapkanLewat.objects.create(tugas=t, karyawan=k, alasan=alasan, oleh=oleh, perangkat=perangkat[:100])
    return t


def ulang(tugas_id):
    """Ganti orang: kelompok kembali belum ditugaskan (yang lama tidak dihitung dilewati)."""
    t = _tugas_buka(tugas_id)
    if t.status != "diambil":
        raise Tolak("Kelompok ini belum ditugaskan.")
    t.pengambil, t.sendiri, t.rekomendasi, t.status = None, False, None, "menunggu"
    t.ditugaskan_waktu, t.ditugaskan_oleh, t.ikut_rekomendasi = None, "", True
    t.save()
    return t


def _serahkan(t, wid, nama, perangkat):
    sekarang = timezone.now()
    if t.status == "menunggu":
        t.sendiri, t.ditugaskan_waktu, t.ditugaskan_oleh = True, sekarang, nama
    t.status, t.selesai_waktu, t.selesai_oleh = "selesai", sekarang, nama
    t.diambil_alih = (wid or "").upper() != (t.siapkan.kasir_id or "").upper()
    t.perangkat = perangkat[:100] or t.perangkat
    t.save()


def serahkan(tugas_id, *, pin, perangkat="", kunci_pin=""):
    """Barang kelompok ini sudah diserahkan ke pembeli (PIN). Dari 'belum ditugaskan' = disiapkan sendiri."""
    t = _tugas_buka(tugas_id)
    wid, nama = verifikasi_pin(pin, kunci_pin)
    with transaction.atomic():
        _serahkan(t, wid, nama, perangkat)
        _cek_selesai(t.siapkan, nama)
    return t


def serahkan_semua(siapkan_id, *, pin, perangkat="", kunci_pin=""):
    s = Siapkan.objects.filter(pk=siapkan_id if str(siapkan_id or "").isdigit() else 0, status="menunggu").first()
    if s is None:
        raise Tolak("Nota ini sudah selesai atau dibatalkan.")
    wid, nama = verifikasi_pin(pin, kunci_pin)
    with transaction.atomic():
        for t in s.tugas.select_related("siapkan").exclude(status__in=["selesai", "batal"]):
            _serahkan(t, wid, nama, perangkat)
        _cek_selesai(s, nama)
    return s


def _cek_selesai(s, nama):
    if not s.tugas.exclude(status__in=["selesai", "batal"]).exists():
        s.status, s.selesai_waktu, s.selesai_oleh = "selesai", timezone.now(), nama
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


def teratas(pilihan, n=3):
    """Rekomendasi yang ditampilkan: sampai 3 orang yang tidak sedang mengambil, urut rekomendasi."""
    if not pilihan or not pilihan[0]["alasan"]:
        return []
    return [p for p in pilihan if not p["sibuk"]][:n]


def daftar_buka():
    """Nota yang sedang disiapkan, lengkap dengan kelompok, baris, rak, dan rekomendasi."""
    daftar = list(Siapkan.objects.filter(status="menunggu").order_by("waktu_nota")
                  .prefetch_related("tugas__baris__varian__rak", "tugas__baris__varian__tempat_rak",
                                    "tugas__lokasi", "tugas__pic", "tugas__pengambil"))
    keadaan = beban_hari_ini()
    karyawan = list(KaryawanLapangan.objects.filter(aktif=True))
    for s in daftar:
        s.daftar_tugas = list(s.tugas.all())
        for t in s.daftar_tugas:
            if t.status == "menunggu":
                t.pilihan = rekomendasi(t, keadaan, karyawan)
                t.teratas = teratas(t.pilihan)
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
                .exclude(selesai_oleh="langsung (rak display)").order_by("-selesai_waktu")[:limit])


def statistik(mulai, akhir):
    """Rekap per karyawan lapangan dan per pelayan pada rentang tanggal [mulai, akhir]."""
    awal, ujung = _awal_hari(mulai), _awal_hari(akhir) + timedelta(days=1)
    tugas = SiapkanTugas.objects.filter(ditugaskan_waktu__gte=awal, ditugaskan_waktu__lt=ujung).exclude(status="batal")
    per = {k.id: {"k": k, "tugas": 0, "selesai": 0, "menit": [], "tidak_ada": 0, "sibuk": 0, "menolak": 0,
                  "menolak_oleh": defaultdict(int), "hari": set()}
           for k in KaryawanLapangan.objects.select_related("ketua")}
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
    tim = defaultdict(list)
    for r in baris:
        ketua = r["k"].ketua
        tim[(ketua.first_name or ketua.username) if ketua else ""].append(r)
    per_tim = [{"pic": nama, "baris": rs, "tugas": sum(r["tugas"] for r in rs)}
               for nama, rs in sorted(tim.items(), key=lambda x: (x[0] == "", x[0]))]
    return {"karyawan": baris, "tengah": tengah, "per_tim": per_tim,
            "pelayan": sorted(pelayan.items(), key=lambda x: -x[1]["tugas"])}


# ---------------------------------------------------------------------------
# Karyawan lapangan & tim
# ---------------------------------------------------------------------------


def simpan_karyawan(pengguna, *, id=None, nama, aktif=True, catatan="", ketua=None):
    """ketua: id akun PIC; None = tidak diubah (untuk ubah), "" = tanpa tim."""
    from master.layanan.pembelian import penerima_boleh
    nama = rapikan_teks(nama)[:60]
    if not nama:
        raise Tolak("Isi nama karyawan.")
    lain = KaryawanLapangan.objects.filter(nama__iexact=nama)
    if id:
        lain = lain.exclude(pk=id)
    if lain.exists():
        raise Tolak(f"Nama {nama} sudah ada. Tambahkan pembeda, mis. {nama} B.")
    k = KaryawanLapangan.objects.get(pk=id) if id else KaryawanLapangan(aktif=True)
    k.nama, k.catatan = nama, rapikan_teks(catatan)[:200]
    if id:
        k.aktif = bool(aktif)
    if ketua is not None:
        if str(ketua).isdigit():
            pic = {u.pk: u for u in penerima_boleh()}.get(int(ketua))
            if pic is None:
                raise Tolak("Ketua tim harus karyawan yang memakai aplikasi gudang.")
            k.ketua = pic
        else:
            k.ketua = None
    k.save()
    return k


def pegang_lokasi(pic_id, lokasi_ids):
    """Kartu PIC: lokasi yang dicentang dipegang PIC ini (pindah dari PIC lain); yang tidak dicentang dilepas."""
    from master.layanan.pembelian import penerima_boleh
    pic = {u.pk: u for u in penerima_boleh()}.get(int(pic_id)) if str(pic_id or "").isdigit() else None
    if pic is None:
        raise Tolak("PIC tidak dikenal.")
    ids = {int(x) for x in lokasi_ids if str(x).isdigit()}
    Lokasi.objects.filter(pic=pic, aktif=True).exclude(pk__in=ids).update(pic=None)
    Lokasi.objects.filter(pk__in=ids, aktif=True).update(pic=pic)


def tim_per_pic():
    """Kartu Tim gudang: [{pic, lokasi, rak (PIC per rak), anak}] + karyawan tanpa tim."""
    from master.layanan.pembelian import penerima_boleh
    from master.models import Rak
    pics = penerima_boleh()
    karyawan = list(KaryawanLapangan.objects.filter(aktif=True))
    lokasi = list(Lokasi.objects.filter(aktif=True).select_related("pic").order_by("urutan", "nama"))
    rak = list(Rak.objects.exclude(pic=None).select_related("tempat"))
    kartu = [{"pic": u, "lokasi": [l for l in lokasi if l.pic_id == u.pk], "rak": [r for r in rak if r.pic_id == u.pk],
              "anak": [k for k in karyawan if k.ketua_id == u.pk]} for u in pics]
    ada = {u.pk for u in pics}
    return {"kartu": kartu, "tanpa_tim": [k for k in karyawan if k.ketua_id not in ada], "lokasi": lokasi,
            "nonaktif": list(KaryawanLapangan.objects.filter(aktif=False))}
