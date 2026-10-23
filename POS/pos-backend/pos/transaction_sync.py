from enum import Enum

from .models import Transactions


class TransactionSyncStatus(Enum):
    NEW = "new"
    IDEMPOTENT = "idempotent"
    CONFLICT = "conflict"


class TransactionSyncError(Exception):
    """Base exception for transaction sync errors."""


class TransactionSyncConflict(TransactionSyncError):
    """Raised when transaction identity conflicts with an existing record."""


def resolve_transaction_request(sync_id, transaction_id):
    """
    Determine whether a transaction request is new, idempotent,
    or conflicting with an existing transaction.

    Returns:
        tuple[TransactionSyncStatus, Transactions | None]
    """
    transaction_by_sync_id = (
        Transactions.objects
        .filter(sync_id=sync_id)
        .first()
    )

    transaction_by_transaction_id = None

    if transaction_id is not None:
        transaction_by_transaction_id = (
            Transactions.objects
            .filter(transaction_id=transaction_id)
            .first()
        )

    if transaction_by_sync_id is None and transaction_by_transaction_id is None:
        return TransactionSyncStatus.NEW, None

    if transaction_by_sync_id is not None:
        if transaction_by_sync_id.transaction_id == transaction_id:
            return (
                TransactionSyncStatus.IDEMPOTENT,
                transaction_by_sync_id,
            )

        raise TransactionSyncConflict(
            "sync_id is already associated with a different transaction_id."
        )

    raise TransactionSyncConflict(
        "transaction_id is already associated with a different sync_id."
    )