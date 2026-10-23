TRANSACTION_ID_SEPARATOR = "-"
TRANSACTION_SEQUENCE_WIDTH = 6

# Bentuk yang sama dengan build_transaction_id(): "T" + 2 digit kode device +
# "-" + 6 digit nomor urut = tepat 10 karakter, sesuai kolom
# transactions.transaction_id varchar(10).
TRANSACTION_ID_PATTERN = r"^T\d{2}-\d{6}$"


class TransactionIDError(ValueError):
    """Base exception for transaction ID formatting errors."""


def build_transaction_id(device_code, sequence):
    """
    Build a human-readable transaction ID.

    Example:
        T01 + 1 -> T01-000001
        T01 + 1000 -> T01-001000
        T99 + 999999 -> T99-999999
    """
    if not isinstance(device_code, str):
        raise TransactionIDError("Kode device harus berupa teks.")

    if len(device_code) != 3:
        raise TransactionIDError("Kode device harus tepat 3 karakter.")

    if not device_code.startswith("T"):
        raise TransactionIDError("Kode device harus berawalan T.")

    if not device_code[1:].isdigit():
        raise TransactionIDError("Kode device harus berisi dua digit.")

    if not isinstance(sequence, int):
        raise TransactionIDError("Nomor urut harus bilangan bulat.")

    if not 1 <= sequence <= 999999:
        raise TransactionIDError(
            "Nomor urut harus antara 1 dan 999999."
        )

    return (
        f"{device_code}"
        f"{TRANSACTION_ID_SEPARATOR}"
        f"{sequence:0{TRANSACTION_SEQUENCE_WIDTH}d}"
    )