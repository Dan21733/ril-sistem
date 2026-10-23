import datetime

import bcrypt
from django.contrib.auth.hashers import check_password, identify_hasher
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from . import pengaturan
from .models import Worker, WorkerPin

# Awalan hash bcrypt mentah, mis. `$2a$06$...`. Tabel worker dikelola di luar
# Django dan isinya berasal dari sistem lama, jadi bentuk ini wajib didukung.
BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")


class InvalidWorkerCredentials(ValueError):
    """Raised when a Worker identity and PIN cannot be verified."""


class PinOutsideValidity(InvalidWorkerCredentials):
    """PIN-nya dikenal, tetapi tidak berlaku pada waktu nota dibuat.

    Bukan tanda menebak PIN, jadi tidak dihitung sebagai percobaan gagal.
    """


class InvalidWorkerPinFormat(ValueError):
    """Raised when a submitted PIN is not exactly six digits."""


# Jam tablet yang maju tidak boleh membuat PIN yang belum berlaku ikut diterima.
TOLERANSI_JAM_MAJU = datetime.timedelta(minutes=10)


def _cek_format(pin):
    if not isinstance(pin, str) or len(pin) != 6 or not pin.isascii() or not pin.isdigit():
        raise InvalidWorkerPinFormat("PIN harus tepat 6 digit.")


def jendela_relevan(sekarang=None):
    """Baris riwayat PIN yang masih mungkin dipakai: belum berakhir, atau
    berakhir belum lebih dari masa tenggang."""
    sekarang = sekarang or timezone.now()
    tenggang = datetime.timedelta(days=max(0, pengaturan.angka("pin_tenggang_hari", 14)))
    return WorkerPin.objects.filter(
        Q(berlaku_sampai__isnull=True) | Q(berlaku_sampai__gt=sekarang - tenggang)
    )


def cocokkan_pin(pin, sekarang=None):
    """Semua jendela berlaku yang hash-nya cocok dengan PIN ini.

    Hasil: daftar (worker_id, berlaku_mulai, berlaku_sampai). Karyawan lama yang
    belum punya riwayat PIN dicocokkan dengan `worker.pin` dan dianggap
    berlaku tanpa batas waktu (None, None), sama seperti sebelum ada riwayat.
    """
    _cek_format(pin)
    hasil = []
    punya_riwayat = set(WorkerPin.objects.values_list("worker_id", flat=True).distinct())

    for baris in jendela_relevan(sekarang).only("worker_id", "pin_hash", "berlaku_mulai", "berlaku_sampai"):
        if _pin_matches(baris.pin_hash, pin):
            hasil.append((baris.worker_id, baris.berlaku_mulai, baris.berlaku_sampai))

    for worker in Worker.objects.only("id", "pin").iterator():
        if worker.id in punya_riwayat:
            continue
        if _pin_matches(worker.pin, pin):
            hasil.append((worker.id, None, None))

    return hasil


def verify_worker_pin(pin, waktu=None, cache=None):
    """Return the verified Worker ID, without exposing the stored PIN.

    `waktu` = saat nota dibuat (transaction_time). PIN dicocokkan dengan
    jendela berlaku pada saat itu, jadi nota offline yang dibuat sebelum PIN
    diganti tetap diterima walau baru dikirim sesudahnya. Tanpa `waktu`,
    dipakai waktu sekarang.

    `cache` (dict, opsional): hasil `cocokkan_pin` per PIN dipakai ulang dalam
    satu permintaan batch, supaya bcrypt tidak dihitung ulang per nota.
    """
    _cek_format(pin)
    sekarang = timezone.now()

    if waktu is None or waktu > sekarang + TOLERANSI_JAM_MAJU:
        waktu = sekarang

    if cache is not None and pin in cache:
        cocok = cache[pin]
    else:
        cocok = cocokkan_pin(pin, sekarang)
        if cache is not None:
            cache[pin] = cocok

    for worker_id, mulai, sampai in cocok:
        if (mulai is None or mulai <= waktu) and (sampai is None or waktu < sampai):
            return worker_id

    if cocok:
        raise PinOutsideValidity(
            "PIN ini tidak berlaku pada waktu nota dibuat (sudah diganti atau "
            "belum mulai berlaku)."
        )

    raise InvalidWorkerCredentials("PIN kasir tidak cocok.")


def daftar_offline(sekarang=None, mundur_hari=2, maju_hari=3):
    """(nama, hash) untuk dicocokkan tablet saat offline.

    Memuat PIN yang berlaku sekarang, PIN lama yang baru berakhir, dan PIN
    berikutnya yang segera berlaku; keputusan akhir tetap di server
    (berdasarkan waktu nota).
    """
    sekarang = sekarang or timezone.now()
    awal = sekarang - datetime.timedelta(days=mundur_hari)
    akhir = sekarang + datetime.timedelta(days=maju_hari)
    # Penyetuju harga di bawah floor TIDAK dikirim ke tablet (QA 18 Okt 2026):
    # hash PIN 6 digit di tablet bisa ditebak habis di laptop dalam ~1,5 jam,
    # dan PIN penyetuju membuka harga di bawah floor. PIN penyetuju hanya
    # dicek server; saat offline nota di bawah floor ditahan untuk ACC master.
    # Karyawan tanpa hak jualan (pengaturan `bukan_kasir`) juga tidak dikirim.
    penyetuju = pengaturan.penyetuju_floor() | pengaturan.bukan_kasir()
    workers = list(Worker.objects.exclude(id__in=penyetuju).only("name", "pin").order_by("name"))
    riwayat = {}

    for baris in WorkerPin.objects.filter(
        Q(berlaku_sampai__isnull=True) | Q(berlaku_sampai__gt=awal),
        Q(berlaku_mulai__isnull=True) | Q(berlaku_mulai__lt=akhir),
    ).order_by("berlaku_mulai"):
        riwayat.setdefault(baris.worker_id, []).append(baris.pin_hash)

    punya_riwayat = set(WorkerPin.objects.values_list("worker_id", flat=True).distinct())
    hasil = []

    for w in workers:
        if w.id in punya_riwayat:
            for pin_hash in riwayat.get(w.id, []):
                hasil.append((w.name, pin_hash))
        else:
            hasil.append((w.name, w.pin))

    return hasil


def describe_pin_format(stored_pin):
    """Sebutkan bentuk nilai `worker.pin` supaya klien tahu cara mencocokkannya.

    Dipakai endpoint daftar worker offline (OFFLINE_SYNC_DESIGN.md §7): tablet
    harus bisa mencocokkan PIN yang diketik tanpa server, dan hanya bentuk yang
    benar-benar bisa diverifikasi klien yang boleh dianggap terpakai.

    - `bcrypt`    : hash bcrypt mentah (`$2a$…`) — isi tabel saat ini; tablet
      mencocokkannya dengan bcrypt juga, bukan dengan membandingkan teks.
    - `plaintext` : teks biasa, jalur data lama.
    - `other`     : hash berformat Django lain. Browser tidak punya hashernya,
      jadi tablet mengabaikan baris ini alih-alih mencocokkan dengan asumsi.
    """
    if not isinstance(stored_pin, str) or not stored_pin:
        return "other"

    if stored_pin.startswith(BCRYPT_PREFIXES):
        return "bcrypt"

    try:
        identify_hasher(stored_pin)
    except ValueError:
        return "plaintext"

    return "other"


def _pin_matches(stored_pin, submitted_pin):
    """Cocokkan PIN tersimpan dengan PIN kiriman kasir.

    Tiga bentuk harus diterima karena tabel worker diisi di luar Django:

    1. hash bcrypt mentah (`$2a$…`) — inilah isi tabel saat ini; nilainya tidak
       bisa diverifikasi Django karena `identify_hasher` membaca nama algoritma
       sebelum `$` pertama dan pada bentuk ini hasilnya string kosong;
    2. hash berformat Django (`pbkdf2_sha256$…`);
    3. teks biasa, sebagai jalur terakhir untuk data lama.
    """
    if stored_pin.startswith(BCRYPT_PREFIXES):
        try:
            return bcrypt.checkpw(
                submitted_pin.encode("utf-8"),
                stored_pin.encode("utf-8"),
            )
        except ValueError:
            # Nilai rusak (salt/hash tidak sah) diperlakukan sebagai tidak cocok,
            # bukan kegagalan server.
            return False

    try:
        identify_hasher(stored_pin)
    except ValueError:
        return constant_time_compare(stored_pin, submitted_pin)

    return check_password(submitted_pin, stored_pin)
