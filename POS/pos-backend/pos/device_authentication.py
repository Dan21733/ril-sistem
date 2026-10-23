import datetime
from dataclasses import dataclass
from uuid import UUID

from django.conf import settings
from django.core import signing
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import Device, DeviceStatus

# "Terakhir terlihat" cukup diperbarui sekali per 5 menit per tablet.
JEDA_TERAKHIR_DILIHAT = datetime.timedelta(minutes=5)


def _id_uuid(device):
    """UUID perangkat, atau None kalau bukan perangkat sungguhan (mis. tiruan uji)."""
    try:
        return UUID(str(device.id))
    except (TypeError, ValueError, AttributeError):
        return None


def pastikan_aktif(device):
    """Tolak tablet yang dinonaktifkan pemilik dari master (Kasir > Tablet kasir)."""
    device_id = _id_uuid(device)

    if device_id is None:
        return

    if DeviceStatus.objects.filter(device_id=device_id, aktif=False).exists():
        raise AuthenticationFailed(
            "Tablet ini dinonaktifkan pemilik. Hubungi pemilik untuk mengaktifkannya lagi."
        )


def catat_terlihat(device):
    device_id = _id_uuid(device)

    if device_id is None:
        return

    sekarang = timezone.now()
    diperbarui = DeviceStatus.objects.filter(device_id=device_id).filter(
        terakhir_dilihat__lt=sekarang - JEDA_TERAKHIR_DILIHAT
    ).update(terakhir_dilihat=sekarang)

    if not diperbarui and not DeviceStatus.objects.filter(device_id=device_id).exists():
        # Tablet yang terdaftar sebelum tabel status ada.
        DeviceStatus.objects.get_or_create(device_id=device_id, defaults={"terakhir_dilihat": sekarang})


def catat_pendaftaran(device):
    device_id = _id_uuid(device)

    if device_id is None:
        return

    sekarang = timezone.now()
    DeviceStatus.objects.update_or_create(
        device_id=device_id,
        defaults={"didaftar": sekarang, "terakhir_dilihat": sekarang, "aktif": True},
    )


DEVICE_TOKEN_SALT = "pos.device-authentication"

# Keputusan 2026-09-26: device token punya masa berlaku. Nota offline tetap
# aman karena antrean disinkronkan segera setelah koneksi kembali; token yang
# kedaluwarsa cukup diperbarui lewat ulang registrasi device oleh pemilik.
DEVICE_TOKEN_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 hari
# Device lama dapat diperbarui memakai token yang kedaluwarsa selama 30 hari
# setelah TTL normal; batas ini membatasi risiko token lama yang bocor.
DEVICE_TOKEN_RENEWAL_MAX_AGE_SECONDS = DEVICE_TOKEN_MAX_AGE_SECONDS * 2


@dataclass(frozen=True)
class AuthenticatedDevice:
    device: Device

    @property
    def is_authenticated(self):
        return True


def create_device_token(device):
    payload = {
        "device_id": str(device.id),
        "device_code": device.device_code,
    }

    return signing.dumps(
        payload,
        key=settings.DEVICE_AUTH_SIGNING_KEY,
        salt=DEVICE_TOKEN_SALT,
    )


def _decode_device_token(token, max_age=None):
    if max_age is None:
        max_age = DEVICE_TOKEN_MAX_AGE_SECONDS

    try:
        payload = signing.loads(
            token,
            key=settings.DEVICE_AUTH_SIGNING_KEY,
            salt=DEVICE_TOKEN_SALT,
            max_age=max_age,
        )
    except signing.SignatureExpired as exc:
        raise AuthenticationFailed(
            "Token device sudah kedaluwarsa."
            if max_age == DEVICE_TOKEN_MAX_AGE_SECONDS
            else "Token device di luar masa perpanjangan."
        ) from exc
    except signing.BadSignature as exc:
        raise AuthenticationFailed("Token device tidak valid.") from exc

    if not isinstance(payload, dict):
        raise AuthenticationFailed("Token device tidak valid.")

    device_id = payload.get("device_id")
    device_code = payload.get("device_code")

    if not device_id or not device_code:
        raise AuthenticationFailed("Token device tidak valid.")

    try:
        UUID(device_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise AuthenticationFailed("Token device tidak valid.") from exc

    return device_id, device_code


def get_device_from_token(token, max_age=None):
    """Return the device named by a valid, unexpired signed token."""
    device_id, device_code = _decode_device_token(token, max_age=max_age)

    try:
        device = Device.objects.get(id=device_id)
    except Device.DoesNotExist as exc:
        raise AuthenticationFailed("Token device tidak valid.") from exc

    if device.device_code != device_code:
        raise AuthenticationFailed("Token device tidak valid.")

    pastikan_aktif(device)

    return device


class DeviceTokenAuthentication(BaseAuthentication):
    keyword = "Device"

    def authenticate_header(self, request):
        # Tanpa ini DRF membalas 403 (bukan 401) ketika header device tidak
        # ada, sehingga klien tidak tahu kredensial device diperlukan.
        return self.keyword

    def authenticate(self, request):
        authorization = request.headers.get("Authorization")

        if not authorization:
            return None

        parts = authorization.split()

        if len(parts) != 2 or parts[0] != self.keyword:
            raise AuthenticationFailed(
                "Header otorisasi device tidak valid."
            )

        device = get_device_from_token(parts[1])
        catat_terlihat(device)

        return AuthenticatedDevice(device=device), parts[1]
