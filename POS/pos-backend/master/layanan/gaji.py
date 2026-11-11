"""Gaji karyawan (G, keputusan pemilik 11 Nov 2026).

- Satu daftar semua karyawan: pelayan (pos.Worker) dan karyawan lapangan (tanpa akun).
- Per orang: gaji pokok, tunjangan (tetap per periode atau per hari masuk), periode harian /
  mingguan / bulanan, dan hari/tanggal gajian. Untuk periode harian, pokok = per hari.
- Hari gajian: pengingat push jam 12.00 (lihat layanan.push), dan daftar "jatuh tempo".
- "Sudah dibayar" otomatis menjadi Biaya toko kategori Gaji (dari laci atau luar laci).
- Hanya akun dengan hak `gaji` (pemilik: Daniel + Mama). Bonus belum dibuat (mulai Januari 2027).
"""
import calendar
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from master.layanan import inti as layanan
from master.layanan.inti import Tolak, baca_angka, rapikan_teks
from master.models import BayarGaji, GajiKaryawan, KaryawanLapangan

KATEGORI = "Gaji"


def _harus(hak):
    if "gaji" not in hak:
        raise Tolak("Akun ini tidak boleh mengurus gaji.")


def rp(n):
    return f"Rp{int(n or 0):,}".replace(",", ".")


# ---------------------------------------------------------------------------
# Daftar dan pengaturan
# ---------------------------------------------------------------------------


def daftar():
    """Semua karyawan (pelayan + lapangan) beserta aturan gajinya (None = belum diatur)."""
    from pos.models import Worker
    gaji_w = {g.worker_id: g for g in GajiKaryawan.objects.exclude(worker_id="")}
    gaji_l = {g.lapangan_id: g for g in GajiKaryawan.objects.filter(lapangan__isnull=False)}
    hasil = []
    for w in Worker.objects.only("id", "name").order_by("name"):
        hasil.append({"kunci": f"w-{w.id}", "nama": w.name, "jenis": "Pelayan", "gaji": gaji_w.get(w.id)})
    for k in KaryawanLapangan.objects.order_by("nama"):
        if k.aktif or k.id in gaji_l:
            hasil.append({"kunci": f"l-{k.id}", "nama": k.nama, "jenis": "Lapangan", "gaji": gaji_l.get(k.id),
                          "nonaktif": not k.aktif})
    for x in hasil:
        x["jumlah"] = perkiraan(x["gaji"]) if x["gaji"] else None
    return hasil


def _orang(kunci):
    """'w-ID001' / 'l-3' -> (worker_id, lapangan, nama)."""
    from pos.models import Worker
    jenis, _, nilai = str(kunci or "").partition("-")
    if jenis == "w":
        w = Worker.objects.filter(pk=nilai).first()
        if w:
            return w.id, None, w.name
    elif jenis == "l" and nilai.isdigit():
        k = KaryawanLapangan.objects.filter(pk=int(nilai)).first()
        if k:
            return "", k, k.nama
    raise Tolak("Karyawan tidak ditemukan.")


def simpan(pengguna, kunci, data, *, hak):
    """Atur gaji satu orang. data: pokok, tunjangan, cara_tunjangan, periode, hari_gajian, tanggal_gajian, toko, aktif, catatan."""
    from master.models import Toko
    _harus(hak)
    worker_id, lapangan, nama = _orang(kunci)
    periode = data.get("periode") or "bulanan"
    if periode not in dict(GajiKaryawan.PERIODE):
        raise Tolak("Pilih periode gaji.")
    cara = data.get("cara_tunjangan") or "tetap"
    if cara not in dict(GajiKaryawan.CARA_TUNJANGAN):
        raise Tolak("Pilih cara tunjangan.")
    pokok = baca_angka(data.get("pokok"), boleh_kosong=True) or 0
    tunjangan = baca_angka(data.get("tunjangan"), boleh_kosong=True) or 0
    if pokok < 0 or tunjangan < 0:
        raise Tolak("Gaji tidak boleh minus.")
    hari = tanggal = None
    if periode == "mingguan":
        hari = baca_angka(data.get("hari_gajian"), boleh_kosong=True)
        if hari is None or not 0 <= hari <= 6:
            raise Tolak("Pilih hari gajian.")
    elif periode == "bulanan":
        tanggal = baca_angka(data.get("tanggal_gajian"), boleh_kosong=True)
        if tanggal is None or not 1 <= tanggal <= 31:
            raise Tolak("Isi tanggal gajian 1–31.")
    toko = None
    if str(data.get("toko") or "").isdigit():
        toko = Toko.objects.filter(pk=int(data["toko"])).first()
    cari = {"worker_id": worker_id} if worker_id else {"lapangan": lapangan}
    g = GajiKaryawan.objects.filter(**cari).first() or GajiKaryawan(**cari)
    lama = None if g.pk is None else ringkas_aturan(g)
    g.nama, g.toko, g.pokok, g.tunjangan, g.cara_tunjangan = nama, toko, pokok, tunjangan, cara
    g.periode, g.hari_gajian, g.tanggal_gajian = periode, hari, tanggal
    g.aktif = str(data.get("aktif", "1")) not in ("0", "false", "False", "")
    g.catatan = rapikan_teks(data.get("catatan"))[:200]
    g.save()
    baru = ringkas_aturan(g)
    if lama != baru:
        # Nominal gaji tidak ditulis ke log umum (log bisa dibaca akun lain); cukup tanda berubah.
        layanan.catat(pengguna, aksi="tambah" if lama is None else "ubah", objek="gaji", kolom=nama,
                      baru="aturan gaji diatur")
    return g


def ringkas_aturan(g):
    if g.periode == "mingguan":
        jadwal = f"tiap {dict(GajiKaryawan.HARI).get(g.hari_gajian, '?')}"
    elif g.periode == "bulanan":
        jadwal = f"tiap tgl {g.tanggal_gajian}"
    else:
        jadwal = "tiap hari"
    tunj = ""
    if g.tunjangan:
        tunj = f" + tunjangan {rp(g.tunjangan)}{'/hari masuk' if g.cara_tunjangan == 'per_hari' else ''}"
    return f"{g.get_periode_display()} {rp(g.pokok)}{tunj}, {jadwal}{'' if g.aktif else ' (nonaktif)'}"


def hari_periode(g):
    """Banyak hari kerja bawaan satu periode (untuk tunjangan per hari)."""
    return {"harian": 1, "mingguan": 6, "bulanan": 26}[g.periode]


def perkiraan(g, hari_masuk=None):
    """Jumlah dibayar satu periode: (pokok, tunjangan, total)."""
    if g.cara_tunjangan == "per_hari":
        hari = hari_periode(g) if hari_masuk is None else hari_masuk
        tunj = g.tunjangan * hari
    else:
        tunj = g.tunjangan
    return {"pokok": g.pokok, "tunjangan": tunj, "total": g.pokok + tunj}


# ---------------------------------------------------------------------------
# Jadwal
# ---------------------------------------------------------------------------


def gajian_pada(g, tanggal):
    if g.periode == "harian":
        return True
    if g.periode == "mingguan":
        return g.hari_gajian is not None and tanggal.weekday() == g.hari_gajian
    if g.tanggal_gajian is None:
        return False
    akhir = calendar.monthrange(tanggal.year, tanggal.month)[1]
    return tanggal.day == min(g.tanggal_gajian, akhir)


def gajian_terakhir(g, hari_ini=None):
    """Tanggal gajian terakhir yang sudah lewat atau hari ini (paling jauh 62 hari ke belakang)."""
    hari_ini = hari_ini or timezone.localdate()
    for mundur in range(0, 63):
        t = hari_ini - timedelta(days=mundur)
        if gajian_pada(g, t):
            return t
    return None


def jatuh_tempo(hari_ini=None):
    """Yang harus dibayar: gajian terakhir tiap orang aktif yang belum dibayar.

    Harian: hanya hari ini (gaji harian yang terlewat dibayar dari halaman gaji, tidak menumpuk)."""
    hari_ini = hari_ini or timezone.localdate()
    hasil = []
    gaji = list(GajiKaryawan.objects.filter(aktif=True).select_related("toko"))
    dibayar = set(BayarGaji.objects.filter(tanggal_gajian__gte=hari_ini - timedelta(days=62))
                  .values_list("gaji_id", "tanggal_gajian"))
    for g in gaji:
        t = hari_ini if g.periode == "harian" else gajian_terakhir(g, hari_ini)
        if t is None or (g.id, t) in dibayar:
            continue
        hasil.append({"g": g, "tanggal": t, "lewat": (hari_ini - t).days, **perkiraan(g)})
    hasil.sort(key=lambda x: (x["tanggal"], x["g"].nama))
    return hasil


def gajian_hari_ini(hari_ini=None):
    hari_ini = hari_ini or timezone.localdate()
    return [x for x in jatuh_tempo(hari_ini) if x["tanggal"] == hari_ini]


# ---------------------------------------------------------------------------
# Bayar
# ---------------------------------------------------------------------------


def _kategori_gaji():
    from master.models import KategoriBiaya
    k, _ = KategoriBiaya.objects.get_or_create(nama=KATEGORI, defaults={"biaya_usaha": True, "urutan": 5})
    if not k.aktif:
        k.aktif = True
        k.save(update_fields=["aktif"])
    return k


def bayar(pengguna, gaji_id, tanggal, *, hak, sumber="luar_laci", hari_masuk=None, toko=None):
    """Tandai sudah dibayar: catat BayarGaji + Biaya kategori Gaji. Satu tanggal gajian sekali."""
    from master.layanan import biaya as svc_biaya
    from master.layanan.pembelian import baca_tanggal
    _harus(hak)
    g = GajiKaryawan.objects.filter(pk=gaji_id).select_related("toko").first() if str(gaji_id).isdigit() else None
    if g is None:
        raise Tolak("Data gaji tidak ditemukan.")
    t = tanggal if isinstance(tanggal, date) else baca_tanggal(tanggal, label="Tanggal gajian")
    if t is None or not gajian_pada(g, t):
        raise Tolak("Tanggal itu bukan hari gajian orang ini.")
    if t > timezone.localdate():
        raise Tolak("Gajian belum sampai.")
    if BayarGaji.objects.filter(gaji=g, tanggal_gajian=t).exists():
        raise Tolak(f"Gaji {g.nama} untuk {t:%d-%m-%Y} sudah dibayar.")
    hari = None
    if g.cara_tunjangan == "per_hari" and g.tunjangan:
        hari = baca_angka(hari_masuk, boleh_kosong=True)
        if hari is None:
            hari = hari_periode(g)
        if hari < 0 or hari > 31:
            raise Tolak("Isi hari masuk 0–31.")
    angka = perkiraan(g, hari)
    toko = g.toko or toko
    with transaction.atomic():
        biaya = None
        if angka["total"] > 0:
            _kategori_gaji()
            biaya = svc_biaya.catat(pengguna, toko, {
                "kategori": KATEGORI, "jumlah": str(angka["total"]), "sumber": sumber,
                "tanggal": t.isoformat() if sumber != "laci" else "",
                "keterangan": f"Gaji {g.nama} ({g.get_periode_display().lower()}, {t:%d-%m-%Y})"})
        b = BayarGaji.objects.create(gaji=g, tanggal_gajian=t, pokok=angka["pokok"], tunjangan=angka["tunjangan"],
                                     hari_masuk=hari, jumlah=angka["total"], biaya=biaya,
                                     dibayar_oleh=pengguna.first_name or pengguna.username)
    return b


def riwayat(n=50):
    return list(BayarGaji.objects.select_related("gaji")[:n])
