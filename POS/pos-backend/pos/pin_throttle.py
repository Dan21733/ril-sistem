"""PIN attempt throttling without new infrastructure.

Keputusan 2026-09-26: tidak ada Redis. Hitungan percobaan disimpan di cache
Django; settings memakai FileBasedCache sehingga semua proses di satu mesin
berbagi hitungan lewat filesystem. Verifikasi PIN memindai seluruh tabel
worker per percobaan, jadi ini bukan hanya soal keamanan tapi juga beban DB.

Kontrak untuk klien: setelah batas terlampaui, percobaan dibalas HTTP 429
dengan Retry-After (detik) sampai masa tunggu berakhir.
"""

import time

from django.core.cache import cache


MAX_PIN_ATTEMPTS = 10
LOCKOUT_SECONDS = 5 * 60  # 5 menit setelah batas terlampaui


class PinThrottled(Exception):
    """Percobaan PIN melewati batas; masih dalam masa tunggu."""

    def __init__(self, retry_after_seconds):
        self.retry_after_seconds = int(retry_after_seconds)
        super().__init__(
            "Terlalu banyak percobaan PIN. Coba lagi dalam "
            f"{self.retry_after_seconds} detik."
        )


def _cache_key(identifier):
    return f"pos.pin-throttle.{identifier}"


def check_pin_allowed(identifier):
    """Raise PinThrottled when the identifier is locked out."""
    key = _cache_key(identifier)

    lockout_until = cache.get(f"{key}.lockout_until")

    if lockout_until is not None:
        remaining = lockout_until - time.time()

        if remaining > 0:
            raise PinThrottled(remaining)

        # Masa tunggu berakhir: bersihkan hitungan lama.
        clear_pin_attempts(identifier)


def register_pin_failure(identifier):
    """Record one failed attempt; lock out when the limit is reached."""
    key = _cache_key(identifier)

    attempts = cache.get(f"{key}.attempts", 0) + 1
    cache.set(f"{key}.attempts", attempts, LOCKOUT_SECONDS)

    if attempts >= MAX_PIN_ATTEMPTS:
        cache.set(
            f"{key}.lockout_until",
            time.time() + LOCKOUT_SECONDS,
            LOCKOUT_SECONDS,
        )


def clear_pin_attempts(identifier):
    """Reset the counter (called after a successful verification)."""
    key = _cache_key(identifier)
    cache.delete(f"{key}.attempts")
    cache.delete(f"{key}.lockout_until")
