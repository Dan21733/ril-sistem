from rest_framework import serializers

from .pin_throttle import (
    PinThrottled,
    check_pin_allowed,
    clear_pin_attempts,
    register_pin_failure,
)
from . import pengaturan
from .serializers import TransactionCreateSerializer
from .worker_verification import (
    InvalidWorkerCredentials,
    InvalidWorkerPinFormat,
    PinOutsideValidity,
    verify_worker_pin,
)


# Batas jumlah transaksi per batch sync: antrean offline dikirim berurutan, dan
# batas ini mencegah satu request raksasa memblokir worker lain.
MAX_BATCH_SYNC_TRANSACTIONS = 200


PESAN_BUKAN_KASIR = "PIN ini tidak diberi hak jualan di kasir."


def _verify_pin(pin, throttle_identifier, clear_on_success=True, waktu=None, cache=None, untuk_kasir=True):
    """Resolve the worker id behind a submitted PIN.

    Frontend tidak pernah menentukan worker_id (aturan 2026-09-26); seluruh
    identitas berasal dari PIN yang diverifikasi backend.

    Percobaan PIN dibatasi per sumber (device untuk klien terautentikasi,
    alamat IP untuk yang belum): verifikasi memindai seluruh tabel worker,
    jadi tanpa throttle endpoint ini bisa dipakai untuk menghambat DB.

    `clear_on_success=False` dipakai loop batch PIN-per-nota: hitungan
    kegagalan baru dihapus bila SELURUH verifikasi dalam request berhasil,
    sehingga satu PIN salah tidak memutihkan kegagalan PIN lain dalam
    batch yang sama.

    `waktu` = transaction_time nota: PIN dicocokkan dengan PIN yang berlaku
    saat nota dibuat (riwayat PIN, lihat worker_verification). `cache` dipakai
    ulang antar-nota dalam satu batch.

    `untuk_kasir`: PIN dipakai sebagai kasir (jualan, void, retur). Karyawan yang
    dicabut hak jualannya di master (`bukan_kasir`) ditolak; nota final dari
    tablet yang ditolak karena ini ikut jalur "Nota perlu ACC". PIN benar tapi
    tanpa hak bukan percobaan menebak, jadi tidak dihitung gagal.
    """
    # Tanpa identifier (mis. unit test serializer tanpa request) throttle
    # dilewati; view produksi selalu menyediakannya.
    if throttle_identifier:
        check_pin_allowed(throttle_identifier)

    try:
        worker_id = verify_worker_pin(pin, waktu=waktu, cache=cache)
    except InvalidWorkerPinFormat as exc:
        raise serializers.ValidationError(
            {"pin": str(exc)}
        ) from exc
    except PinOutsideValidity as exc:
        # PIN asli tapi di luar masa berlakunya: bukan percobaan menebak.
        raise serializers.ValidationError(
            {"pin": str(exc)}
        ) from exc
    except InvalidWorkerCredentials as exc:
        if throttle_identifier:
            register_pin_failure(throttle_identifier)
        raise serializers.ValidationError(
            {"pin": str(exc)}
        ) from exc

    if throttle_identifier and clear_on_success:
        clear_pin_attempts(throttle_identifier)

    if untuk_kasir and worker_id in pengaturan.bukan_kasir():
        raise serializers.ValidationError({"pin": PESAN_BUKAN_KASIR})

    return worker_id


def verifikasi_penyetuju(approver_pin, throttle_identifier, waktu=None, cache=None):
    """worker_id penyetuju harga di bawah floor, atau ValidationError.

    PIN penyetuju dicek seperti PIN kasir (ikut batas percobaan), lalu harus
    milik karyawan yang ditandai pemilik sebagai penyetuju di master.
    """
    try:
        approver_id = _verify_pin(approver_pin, throttle_identifier, clear_on_success=False,
                                  waktu=waktu, cache=cache, untuk_kasir=False)
    except serializers.ValidationError as exc:
        pesan = exc.detail.get("pin") if isinstance(exc.detail, dict) else exc.detail
        raise serializers.ValidationError({"approver_pin": pesan}) from exc

    if approver_id not in pengaturan.penyetuju_floor():
        raise serializers.ValidationError(
            {"approver_pin": "PIN ini bukan milik penyetuju harga di bawah floor."}
        )

    return approver_id


def pin_throttle_identifier(request):
    """Throttle key: device_code untuk klien terautentikasi, IP selainnya."""
    user = getattr(request, "user", None)
    device = getattr(user, "device", None) if user is not None else None

    if device is not None:
        return f"device:{device.device_code}"

    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")

    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        client_ip = request.META.get("REMOTE_ADDR", "unknown")

    return f"ip:{client_ip}"


class TransactionRequestSerializer(serializers.Serializer):
    pin = serializers.CharField(
        write_only=True,
        min_length=6,
        max_length=6,
    )
    transaction = TransactionCreateSerializer()
    # PIN penyetuju untuk harga di bawah floor (opsional).
    approver_pin = serializers.CharField(
        write_only=True, min_length=6, max_length=6, required=False, allow_null=True, allow_blank=True,
    )

    def validate(self, attrs):
        waktu = (attrs.get("transaction") or {}).get("transaction_time")
        attrs["worker_id"] = _verify_pin(
            attrs["pin"],
            self.context.get("pin_throttle_identifier"),
            waktu=waktu,
        )
        attrs["approver_id"] = None
        if attrs.get("approver_pin"):
            attrs["approver_id"] = verifikasi_penyetuju(
                attrs["approver_pin"], self.context.get("pin_throttle_identifier"), waktu=waktu,
            )
        return attrs


class TransactionBatchSyncSerializer(serializers.Serializer):
    """Envelope batch sync untuk antrean nota offline.

    Dua bentuk diterima selama transisi (slice 2 PIN-per-nota, 2026-10-09):
    - Lama (keputusan 2026-09-26): PIN wajib SEKALI untuk seluruh batch dan
      mengotorisasi operasi sync sebagai keseluruhan; worker hasil PIN ini
      yang dipakai untuk atribusi semua nota yang disinkronkan.
    - Baru: tiap anggota membawa `pin`-nya sendiri; worker tiap nota berasal
      dari PIN nota itu. Anggota tanpa PIN tidak menggagalkan envelope — nota
      itu ditolak per-nota di view dengan alasan yang jelas.
    Kedua bentuk PIN bersamaan ditolak sebagai ambiguitas; verifikasi PIN
    per-nota (termasuk throttle tengah-batch) adalah urusan view, bukan
    serializer ini.
    """

    pin = serializers.CharField(
        write_only=True,
        min_length=6,
        max_length=6,
        required=False,
    )

    transactions = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=False,
        max_length=MAX_BATCH_SYNC_TRANSACTIONS,
    )

    def validate(self, attrs):
        top_pin = attrs.get("pin")
        members = attrs.get("transactions", [])
        member_has_pin = any(
            isinstance(member, dict) and "pin" in member
            for member in members
        )

        if top_pin is not None and member_has_pin:
            raise serializers.ValidationError(
                {
                    "pin": (
                        "Pakai satu PIN batch atau PIN per nota, "
                        "jangan keduanya."
                    )
                }
            )

        if top_pin is not None:
            attrs["worker_id"] = _verify_pin(
                top_pin,
                self.context.get("pin_throttle_identifier"),
            )
            attrs["batch_mode"] = "legacy"
        else:
            attrs["batch_mode"] = "per_nota"

        return attrs