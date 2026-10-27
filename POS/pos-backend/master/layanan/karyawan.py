"""Karyawan kasir, PIN, ganti PIN terjadwal, dan tablet kasir.

PIN satu-satunya yang menentukan siapa kasirnya (kasir tidak memilih nama), jadi
setiap PIN yang mungkin dipakai pada waktu yang sama **wajib berbeda**. Pembuat
PIN di sini memeriksa ke semua PIN yang masih berlaku, yang akan berlaku, dan
yang baru berakhir (masa tenggang), bukan hanya PIN aktif sekarang.

Riwayat PIN (`worker_pin`) memberi tiap PIN jendela berlaku. Kasir mencocokkan
nota dengan PIN yang berlaku saat nota dibuat (lihat pos/worker_verification.py).
"""
import datetime
import re
import secrets

import bcrypt
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from pos import pengaturan as aturan
from pos.models import Device, DeviceStatus, Worker, WorkerPin
from pos.worker_verification import cocokkan_pin

from master.layanan import inti as layanan
from master.layanan.inti import Tolak
from master.models import KontakKaryawan, PinTampil

NAMA_HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
PIN_TAMPIL_HARI = 3  # PIN terbaca disimpan sampai 3 hari setelah mulai berlaku
BCRYPT_ROUNDS = 6  # sama dengan hash PIN lama; tablet mencocokkan offline


# ---------------------------------------------------------------------------
# PIN
# ---------------------------------------------------------------------------


def pin_lemah(pin):
    """PIN yang terlalu mudah ditebak orang di sebelah kasir."""
    if len(set(pin)) <= 2:  # 111111, 121212, 112211
        return True
    angka = [int(c) for c in pin]
    beda = {angka[i + 1] - angka[i] for i in range(5)}
    if beda in ({1}, {-1}):  # 123456, 654321
        return True
    if pin[:3] == pin[3:]:  # 123123
        return True
    if re.fullmatch(r"(19|20)\d\d(0[1-9]|1[0-2])", pin) or re.fullmatch(r"(0[1-9]|[12]\d|3[01])(0[1-9]|1[0-2])\d\d", pin):
        return True  # bentuk tanggal: 199812, 251298
    return False


def _hash(pin):
    return bcrypt.hashpw(pin.encode(), bcrypt.gensalt(BCRYPT_ROUNDS)).decode()


def pin_acak():
    for _ in range(500):
        pin = f"{secrets.randbelow(10**6):06d}"
        if pin_lemah(pin):
            continue
        if cocokkan_pin(pin):  # sudah dipakai siapa pun di jendela yang relevan
            continue
        return pin
    raise Tolak("Gagal membuat PIN unik. Coba lagi.")


def pastikan_riwayat():
    """Karyawan lama (PIN dari sistem lama) diberi baris riwayat berlaku sejak awal.

    Setelah ini semua aturan PIN (ganti, nonaktif, tenggang) berlaku seragam.
    """
    punya = set(WorkerPin.objects.values_list("worker_id", flat=True).distinct())
    for w in Worker.objects.only("id", "pin"):
        if w.id not in punya:
            WorkerPin.objects.create(worker_id=w.id, pin_hash=w.pin, berlaku_mulai=None)


def _tampilkan(baris, pin):
    PinTampil.objects.update_or_create(
        worker_pin_id=baris.id,
        defaults={"worker_id": baris.worker_id, "pin": pin,
                  "berlaku_mulai": baris.berlaku_mulai or timezone.now(),
                  "hapus_setelah": (baris.berlaku_mulai or timezone.now()) + datetime.timedelta(days=PIN_TAMPIL_HARI)})


def _batalkan_berikut(worker_id, sekarang):
    """Buang PIN yang belum mulai berlaku dan buka lagi jendela PIN sekarang."""
    akan = WorkerPin.objects.filter(worker_id=worker_id, berlaku_mulai__gt=sekarang)
    PinTampil.objects.filter(worker_pin_id__in=list(akan.values_list("id", flat=True))).delete()
    akan.delete()
    WorkerPin.objects.filter(worker_id=worker_id, berlaku_sampai__gt=sekarang).update(berlaku_sampai=None)


def pasang_pin(worker_id, *, mulai=None, pin=None):
    """PIN baru untuk satu karyawan, berlaku mulai `mulai` (bawaan: sekarang).

    PIN yang sedang berlaku diakhiri tepat saat PIN baru mulai, jadi tidak ada
    celah dan tidak ada tumpang tindih. Kembalikan (baris_riwayat, pin_terbaca).
    """
    sekarang = timezone.now()
    mulai = mulai or sekarang
    with transaction.atomic():
        pastikan_riwayat()
        _batalkan_berikut(worker_id, sekarang)
        WorkerPin.objects.filter(worker_id=worker_id).filter(
            Q(berlaku_sampai__isnull=True) | Q(berlaku_sampai__gt=mulai)
        ).update(berlaku_sampai=mulai)
        pin = pin or pin_acak()
        hash_baru = _hash(pin)
        baris = WorkerPin.objects.create(worker_id=worker_id, pin_hash=hash_baru, berlaku_mulai=mulai)
        if mulai <= sekarang:
            # worker.pin ikut diperbarui untuk kode lama yang masih membacanya.
            Worker.objects.filter(id=worker_id).update(pin=hash_baru)
        _tampilkan(baris, pin)
    return baris, pin


# ---------------------------------------------------------------------------
# Karyawan
# ---------------------------------------------------------------------------


def aktif(worker_id, sekarang=None):
    sekarang = sekarang or timezone.now()
    return WorkerPin.objects.filter(worker_id=worker_id).filter(
        Q(berlaku_sampai__isnull=True) | Q(berlaku_sampai__gt=sekarang)).exists()


def _id_baru():
    angka = [int(i[2:]) for i in Worker.objects.values_list("id", flat=True) if re.fullmatch(r"ID\d{1,3}", i or "")]
    n = max(angka, default=0) + 1
    if n > 999:
        raise Tolak("Nomor karyawan sudah habis (ID999).")
    return f"ID{n:03d}"


def _nama_rapi(nama):
    nama = layanan.rapikan_teks(nama)[:60]
    if not nama:
        raise Tolak("Nama karyawan wajib diisi.")
    return nama


def tambah(pengguna, nama):
    nama = _nama_rapi(nama)
    if Worker.objects.filter(name__iexact=nama).exists():
        raise Tolak(f"Sudah ada karyawan bernama {nama}. Pakai nama yang bisa dibedakan.")
    with transaction.atomic():
        wid = _id_baru()
        pin = pin_acak()
        Worker.objects.create(id=wid, name=nama, pin=_hash(pin))
        pasang_pin(wid, pin=pin)
        layanan.catat(pengguna, aksi="tambah", objek="karyawan", kolom="karyawan", baru=f"{wid} {nama}")
    return wid, pin


def ganti_nama(pengguna, worker_id, nama):
    nama = _nama_rapi(nama)
    w = Worker.objects.filter(id=worker_id).first()
    if w is None:
        raise Tolak("Karyawan tidak ditemukan.")
    if Worker.objects.filter(name__iexact=nama).exclude(id=worker_id).exists():
        raise Tolak(f"Sudah ada karyawan bernama {nama}.")
    lama = w.name
    Worker.objects.filter(id=worker_id).update(name=nama)
    layanan.catat(pengguna, aksi="ubah", objek="karyawan", kolom="nama karyawan", lama=lama, baru=nama)


def rapikan_wa(teks):
    """'0812-3456 7890' / '+62 812...' / '62812...' / '812...' -> '628123457890'. Kosong -> ''."""
    angka = re.sub(r"\D", "", teks or "")
    if not angka:
        return ""
    if angka.startswith("0"):
        angka = "62" + angka[1:]
    elif angka.startswith("8"):
        angka = "62" + angka
    if not angka.startswith("62") or not 10 <= len(angka) <= 15:
        raise Tolak("Nomor WA tidak dikenali. Tulis seperti 0812 3456 7890.")
    return angka


def atur_wa(pengguna, worker_id, teks):
    if not Worker.objects.filter(id=worker_id).exists():
        raise Tolak("Karyawan tidak ditemukan.")
    wa = rapikan_wa(teks)
    lama = KontakKaryawan.objects.filter(worker_id=worker_id).values_list("wa", flat=True).first() or ""
    if wa == lama:
        return wa
    if wa:
        KontakKaryawan.objects.update_or_create(worker_id=worker_id, defaults={"wa": wa})
    else:
        KontakKaryawan.objects.filter(worker_id=worker_id).delete()
    layanan.catat(pengguna, aksi="ubah", objek="karyawan", kolom="nomor WA karyawan", lama=lama, baru=wa or "(dihapus)")
    return wa


def tampil_wa(wa):
    """'6281234567890' -> '0812-3456-7890' untuk dibaca."""
    if not wa:
        return ""
    lokal = "0" + wa[2:] if wa.startswith("62") else wa
    return "-".join([lokal[:4], lokal[4:8], lokal[8:]]).strip("-")


def pesan_pin(nama, pin, berlaku_mulai, sekarang=None):
    from django.utils.formats import date_format
    sekarang = sekarang or timezone.now()
    if berlaku_mulai > sekarang:
        kapan = "Berlaku mulai " + date_format(timezone.localtime(berlaku_mulai), "l, j F Y, H.i") + "."
    else:
        kapan = "Sudah berlaku sekarang."
    return (f"Halo {nama}, PIN kamu untuk kasir dan aplikasi toko: {pin}\n{kapan}\n"
            "Jangan beri tahu PIN ini ke siapa pun. Kalau lupa, minta reset ke pemilik.")


def link_wa(wa, teks):
    from urllib.parse import quote
    return f"https://wa.me/{wa}?text={quote(teks)}" if wa else f"https://wa.me/?text={quote(teks)}"


def reset_pin(pengguna, worker_id):
    if not Worker.objects.filter(id=worker_id).exists():
        raise Tolak("Karyawan tidak ditemukan.")
    _, pin = pasang_pin(worker_id)
    layanan.catat(pengguna, aksi="ubah", objek="karyawan", kolom="PIN karyawan", baru=f"{worker_id}: PIN baru")
    return pin


def nonaktifkan(pengguna, worker_id):
    """PIN karyawan berhenti berlaku sekarang. Nota yang ia buat SEBELUM ini
    (termasuk yang masih offline) tetap diterima selama masa tenggang."""
    sekarang = timezone.now()
    with transaction.atomic():
        pastikan_riwayat()
        _batalkan_berikut(worker_id, sekarang)
        WorkerPin.objects.filter(worker_id=worker_id, berlaku_sampai__isnull=True).update(berlaku_sampai=sekarang)
        PinTampil.objects.filter(worker_id=worker_id).delete()
    layanan.catat(pengguna, aksi="ubah", objek="karyawan", kolom="status karyawan", baru=f"{worker_id}: nonaktif")


def aktifkan(pengguna, worker_id):
    pin = reset_pin(pengguna, worker_id)
    layanan.catat(pengguna, aksi="ubah", objek="karyawan", kolom="status karyawan", baru=f"{worker_id}: aktif")
    return pin


def daftar():
    pastikan_riwayat()
    sekarang = timezone.now()
    tampil = {}
    for t in PinTampil.objects.filter(hapus_setelah__gt=sekarang):
        tampil.setdefault(t.worker_id, []).append(t)
    riwayat = {}
    for r in WorkerPin.objects.order_by("berlaku_mulai"):
        riwayat.setdefault(r.worker_id, []).append(r)
    hasil = []
    kontak = dict(KontakKaryawan.objects.values_list("worker_id", "wa"))
    penyetuju = aturan.penyetuju_floor()
    bukan_kasir = aturan.bukan_kasir()
    for w in Worker.objects.only("id", "name").order_by("name"):
        rs = riwayat.get(w.id, [])
        kini = next((r for r in rs if r.berlaku_pada(sekarang)), None)
        berikut = next((r for r in rs if r.berlaku_mulai and r.berlaku_mulai > sekarang), None)
        hasil.append({
            "id": w.id, "nama": w.name, "aktif": kini is not None or berikut is not None,
            "pin_sejak": kini.berlaku_mulai if kini else None,
            "berikut": berikut.berlaku_mulai if berikut else None,
            "tampil": sorted(tampil.get(w.id, []), key=lambda t: t.berlaku_mulai),
            "penyetuju": w.id in penyetuju,
            "jual": w.id not in bukan_kasir,
            "wa": kontak.get(w.id, ""), "wa_tampil": tampil_wa(kontak.get(w.id, "")),
        })
    hasil.sort(key=lambda d: (not d["aktif"], d["nama"].lower()))
    return hasil


def atur_penyetuju(pengguna, worker_id, boleh):
    """Tandai karyawan yang PIN-nya boleh menyetujui harga di bawah floor."""
    if not Worker.objects.filter(id=worker_id).exists():
        raise Tolak("Karyawan tidak ditemukan.")
    ids = aturan.penyetuju_floor()
    ids = (ids | {worker_id}) if boleh else (ids - {worker_id})
    aturan.simpan(penyetuju_floor=",".join(sorted(ids)))
    layanan.catat(pengguna, aksi="ubah", objek="karyawan", kolom="penyetuju floor",
                  baru=f"{worker_id}: {'boleh' if boleh else 'tidak boleh'} menyetujui harga di bawah floor")


def sembunyikan_pin(pengguna, worker_id=None):
    qs = PinTampil.objects.all()
    if worker_id:
        qs = qs.filter(worker_id=worker_id)
    qs.delete()


def bersihkan_pin_tampil(sekarang=None):
    return PinTampil.objects.filter(hapus_setelah__lte=sekarang or timezone.now()).delete()[0]


# ---------------------------------------------------------------------------
# Ganti PIN terjadwal
# ---------------------------------------------------------------------------


def _baca_waktu(teks):
    if not teks:
        return None
    try:
        w = datetime.datetime.fromisoformat(teks)
    except ValueError:
        return None
    return w if timezone.is_aware(w) else timezone.make_aware(w)


def _jam(teks):
    try:
        j, m = (int(x) for x in str(teks).split(":"))
        return datetime.time(j % 24, m % 60)
    except (TypeError, ValueError):
        return datetime.time(21, 0)


def jadwal_pertama(sekarang=None):
    """Hari & jam rotasi terdekat setelah sekarang (waktu lokal toko)."""
    sekarang = timezone.localtime(sekarang or timezone.now())
    hari = aturan.angka("rotasi_hari", 5) % 7
    jam = _jam(aturan.baca("rotasi_jam"))
    calon = sekarang.replace(hour=jam.hour, minute=jam.minute, second=0, microsecond=0)
    calon += datetime.timedelta(days=(hari - calon.weekday()) % 7)
    if calon <= sekarang:
        calon += datetime.timedelta(days=7)
    return calon


def rotasi_berikut():
    return _baca_waktu(aturan.baca("rotasi_berikut"))


def simpan_aturan_pin(pengguna, data):
    """Simpan pengaturan ganti PIN. Jadwal dihitung ulang kalau berubah; PIN
    berikutnya yang sudah disiapkan untuk jadwal lama dibatalkan."""
    def angka(nama, bawah, atas, bawaan):
        try:
            nilai = int(str(data.get(nama, bawaan)).strip())
        except ValueError:
            raise Tolak(f"Isian {nama.replace('_', ' ')} harus angka.")
        if not bawah <= nilai <= atas:
            raise Tolak(f"Isian {nama.replace('_', ' ')} harus {bawah} sampai {atas}.")
        return nilai

    baru = {
        "rotasi_aktif": "1" if data.get("rotasi_aktif") else "0",
        "rotasi_interval_hari": str(angka("rotasi_interval_hari", 1, 90, 14)),
        "rotasi_hari": str(angka("rotasi_hari", 0, 6, 5)),
        "rotasi_jam": _jam(data.get("rotasi_jam") or "21:00").strftime("%H:%M"),
        "rotasi_siapkan_hari": str(angka("rotasi_siapkan_hari", 0, 7, 2)),
        "pin_tenggang_hari": str(angka("pin_tenggang_hari", 1, 60, 14)),
        "refund_maks_persen": str(angka("refund_maks_persen", 0, 100, 100)),
    }
    lama = aturan.semua()
    jadwal_berubah = any(lama.get(k) != baru[k] for k in ("rotasi_aktif", "rotasi_interval_hari", "rotasi_hari", "rotasi_jam"))
    with transaction.atomic():
        aturan.simpan(**baru)
        if jadwal_berubah:
            sekarang = timezone.now()
            for wid in set(WorkerPin.objects.filter(berlaku_mulai__gt=sekarang).values_list("worker_id", flat=True)):
                _batalkan_berikut(wid, sekarang)
            aturan.simpan(rotasi_berikut=jadwal_pertama().isoformat() if baru["rotasi_aktif"] == "1" else "")
    perubahan = [f"{k}: {lama.get(k)} -> {v}" for k, v in baru.items() if lama.get(k) != v]
    if perubahan:
        layanan.catat(pengguna, aksi="ubah", objek="aturan kasir", kolom="aturan kasir", baru="; ".join(perubahan))
    jalankan_rotasi()


def jalankan_rotasi(sekarang=None):
    """Dipanggil penjadwal (master_jadwal) tiap menit. Aman dipanggil berulang.

    - Saat jadwal sudah lewat: jadwal berikutnya = jadwal + interval. PIN yang
      disiapkan untuk jadwal itu otomatis mulai berlaku (jendela riwayatnya).
    - `rotasi_siapkan_hari` sebelum jadwal: PIN baru dibuat untuk semua karyawan
      aktif, supaya pemilik sempat membagikannya sebelum berlaku.
    """
    if aturan.baca("rotasi_aktif") != "1":
        return 0
    sekarang = sekarang or timezone.now()
    berikut = rotasi_berikut()
    if berikut is None:
        berikut = jadwal_pertama(sekarang)
    interval = datetime.timedelta(days=max(1, aturan.angka("rotasi_interval_hari", 14)))
    while berikut <= sekarang:
        berikut += interval
    if aturan.baca("rotasi_berikut") != berikut.isoformat():
        aturan.simpan(rotasi_berikut=berikut.isoformat())

    siapkan = datetime.timedelta(days=aturan.angka("rotasi_siapkan_hari", 2))
    if sekarang < berikut - siapkan:
        return 0
    dibuat = 0
    pastikan_riwayat()
    for wid in Worker.objects.order_by("id").values_list("id", flat=True):
        if not aktif(wid, sekarang):
            continue
        if WorkerPin.objects.filter(worker_id=wid, berlaku_mulai=berikut).exists():
            continue
        pasang_pin(wid, mulai=berikut)
        dibuat += 1
    if dibuat:
        layanan.catat(None, aksi="ubah", sumber="sistem", objek="karyawan", kolom="PIN karyawan",
                      baru=f"{dibuat} PIN baru disiapkan, berlaku {timezone.localtime(berikut):%d-%m-%Y %H:%M}")
    return dibuat


def detak():
    aturan.simpan(jadwal_detak=timezone.now().isoformat())


def penjadwal_hidup(sekarang=None):
    terakhir = _baca_waktu(aturan.baca("jadwal_detak"))
    return terakhir is not None and (sekarang or timezone.now()) - terakhir < datetime.timedelta(minutes=10)


# ---------------------------------------------------------------------------
# Tablet kasir
# ---------------------------------------------------------------------------


def daftar_tablet():
    status = {s.device_id: s for s in DeviceStatus.objects.all()}
    hasil = []
    for d in Device.objects.order_by("device_code"):
        s = status.get(d.id)
        hasil.append({"id": d.id, "kode": d.device_code, "nama": s.nama if s else "",
                      "aktif": s.aktif if s else True, "didaftar": s.didaftar if s else None,
                      "terakhir": s.terakhir_dilihat if s else None})
    return hasil


def atur_tablet(pengguna, device_id, *, nama=None, aktif=None):
    d = Device.objects.filter(id=device_id).first()
    if d is None:
        raise Tolak("Tablet tidak ditemukan.")
    s, _ = DeviceStatus.objects.get_or_create(device_id=d.id)
    if nama is not None:
        s.nama = layanan.rapikan_teks(nama)[:60]
    if aktif is not None:
        s.aktif = bool(aktif)
        layanan.catat(pengguna, aksi="ubah", objek="tablet", kolom="status tablet",
                      baru=f"{d.device_code}: {'aktif' if s.aktif else 'nonaktif'}")
    s.save()
    return s


def atur_pendaftaran(pengguna, buka):
    aturan.simpan(pendaftaran_tablet="buka" if buka else "tutup")
    layanan.catat(pengguna, aksi="ubah", objek="tablet", kolom="pendaftaran tablet",
                  baru="dibuka" if buka else "ditutup")
