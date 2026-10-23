from django.db import connection, transaction
from django.conf import settings
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from rest_framework import serializers

from .models import Device, DeviceTransactionSequence


DEVICE_CODE_PREFIX = "T"
MIN_DEVICE_NUMBER = 1
MAX_DEVICE_NUMBER = 99

# PostgreSQL transaction-level advisory lock key.
# Hanya digunakan untuk serialisasi proses device registration.
DEVICE_REGISTRATION_LOCK_KEY = 2173301


class DeviceRegistrationSerializer(serializers.Serializer):
    device_id = serializers.UUIDField(required=False)
    device_token = serializers.CharField(
        required=False,
        write_only=True,
        trim_whitespace=False,
    )

    def validate(self, attrs):
        input_fields = set(self.initial_data or {})
        renewal_fields = {"device_id", "device_token"}

        if input_fields and input_fields != renewal_fields:
            raise serializers.ValidationError(
                "Isi device_id dan device_token sekaligus untuk memperbarui "
                "device."
            )

        return attrs

    @property
    def is_renewal(self):
        return bool(self.validated_data.get("device_id"))

    @property
    def renewal_token(self):
        return self.validated_data.get("device_token")


class DeviceRegistrationError(Exception):
    """Base exception for device registration errors."""

class InvalidDeviceRegistrationToken(DeviceRegistrationError):
    """Raised when the device registration token is invalid."""

class NoDeviceCodeAvailable(DeviceRegistrationError):
    """Raised when all T01-T99 device codes are already allocated."""

def validate_device_registration_token(token):
    """
    Validate the bootstrap token required for device registration.
    """
    expected_token = settings.DEVICE_REGISTRATION_TOKEN

    if not token or not constant_time_compare(
        token,
        expected_token,
    ):
        raise InvalidDeviceRegistrationToken(
            "Token registrasi device tidak valid."
        )

def _lock_device_registration():
    """
    Serialize concurrent device registration operations.

    The lock is transaction-scoped and is automatically released
    when the surrounding database transaction ends.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [DEVICE_REGISTRATION_LOCK_KEY],
        )


def _get_next_device_code():
    """
    Return the first available device code from T01 through T99.

    Caller must hold the device registration advisory lock.
    """
    existing_codes = set(
        Device.objects.values_list("device_code", flat=True)
    )

    for device_number in range(MIN_DEVICE_NUMBER, MAX_DEVICE_NUMBER + 1):
        device_code = f"{DEVICE_CODE_PREFIX}{device_number:02d}"

        if device_code not in existing_codes:
            return device_code

    raise NoDeviceCodeAvailable(
        "Kode device habis. Seluruh kode T01-T99 sudah dipakai."
    )


def register_device():
    """
    Atomically allocate a device code and create its sequence row.

    Returns:
        Device: newly registered device.
    """
    with transaction.atomic():
        _lock_device_registration()

        device_code = _get_next_device_code()

        device = Device.objects.create(
            device_code=device_code,
        )

        DeviceTransactionSequence.objects.create(
            device=device,
            next_sequence=1,
            # updated_at wajib diisi eksplisit: kolomnya NOT NULL dan modelnya
            # managed = False tanpa default Python, sehingga Django tetap
            # mengirim NULL (default CURRENT_TIMESTAMP milik DB tidak pernah
            # dipakai) dan registrasi device gagal 500.
            updated_at=timezone.now(),
        )

        return device