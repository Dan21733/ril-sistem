from django.db import transaction
from django.utils import timezone

from .models import DeviceTransactionSequence


SEQUENCE_RANGE_SIZE = 1000
MAX_TRANSACTION_SEQUENCE = 999999


class DeviceSequenceError(Exception):
    """Base exception for device transaction sequence errors."""


class DeviceSequenceNotFound(DeviceSequenceError):
    """Raised when a device has no transaction sequence row."""


def allocate_transaction_sequence(device):
    """
    Atomically allocate the next transaction ID sequence range
    for a device.

    Returns:
        tuple[int, int]: (start_sequence, end_sequence)
    """
    with transaction.atomic():
        sequence = (
            DeviceTransactionSequence.objects
            .select_for_update()
            .filter(device=device)
            .first()
        )

        if sequence is None:
            raise DeviceSequenceNotFound(
                "Jatah nomor nota untuk device ini belum ada."
            )

        start_sequence = sequence.next_sequence
        end_sequence = (
            start_sequence + SEQUENCE_RANGE_SIZE - 1
        )

        if end_sequence > MAX_TRANSACTION_SEQUENCE:
            raise DeviceSequenceError(
                "Jatah nomor nota device ini sudah habis."
            )

        sequence.next_sequence = end_sequence + 1
        sequence.updated_at = timezone.now()

        sequence.save(
            update_fields=[
                "next_sequence",
                "updated_at",
            ]
        )

        return start_sequence, end_sequence