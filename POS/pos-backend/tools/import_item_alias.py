"""Impor frasa alias pencarian dari berkas Excel ke tabel ``item_alias``.

Kenapa ada
==========

Pencarian lewat frasa (``zink``, ``bak cuci piring``) membutuhkan pemetaan
frasa -> ``item_name`` di database. Sumbernya berkas kerja pemilik
(``Data dengan alias dan rak*.xlsx``, sheet ``item_alias``), yang memuat kolom
``alias`` (asli) dan ``alias_baru`` (hasil pembersihan: frasa yang 100%
diwakilkan kategori sudah dibuang). Skrip ini membaca ``alias_baru`` bila
terisi, jika tidak ``alias``, memecahnya per koma, lalu menulis pasangan
``(frasa, item_name)`` ke tabel ``item_alias``.

Batas dan keamanan
==================

- Tabel ``item_alias`` bukan milik migrasi Django; DDL-nya ada di
  ``ITEM_ALIAS_SEARCH_DESIGN.md`` dan dijalankan pemilik database. Skrip ini
  berhenti dengan pesan jelas bila tabelnya belum ada, dan TIDAK membuatnya.
- Menulis hanya bila diberi ``--yes``; tanpa itu skrip berjalan sebagai
  pratinjau. Tujuan koneksi dicetak sebagai host/nama database saja — tidak
  pernah mencetak kredensial.
- Tulis bersifat idempoten: ``ON CONFLICT DO NOTHING``, jadi menjalankannya
  dua kali tidak menggandakan frasa dan tidak menghapus apa pun.

Pemakaian, dari folder ``pos-backend`` (Git Bash)::

    # Pratinjau ke database yang ditunjuk DATABASE_URL:
    venv/Scripts/python.exe tools/import_item_alias.py --file "C:/.../Data dengan alias dan rak - alias dibersihkan.xlsx"

    # Menulis sungguhan:
    venv/Scripts/python.exe tools/import_item_alias.py --file "..." --yes
"""

import argparse
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402  (di belakang penyiapan path/settings)

django.setup()

from django.db import DatabaseError, connection  # noqa: E402

from pos.models import Item, ItemAliasPhrase  # noqa: E402

ALIAS_COLUMNS = ("alias_baru", "alias")
REPORT_SAMPLE = 10


def _normalize(phrase):
    """Rapikan satu frasa: tanpa spasi ganda, huruf kecil, spasi di ujung dibuang.

    Huruf kecil dipilih supaya pencarian kasir tidak bergantung pada kapitalisasi
    ketikan; trigram PostgreSQL membedakan huruf besar/kecil, jadi frasa simpan
    dan frasa ketik harus sama bentuknya.
    """
    return " ".join(phrase.split()).lower()


def _is_repeated_header(values, names):
    """Baris yang seluruh isinya sama dengan nama kolom = header ulangan.

    Sheet kerja pemilik memuat header dua kali (di baris 1 dan sisipan di
    tengah), dan tanpa pemeriksaan ini baris itu masuk sebagai item bernama
    \"item_name\".
    """
    seen = [
        str(values[name]).strip()
        for name in names
        if name and values.get(name) is not None
    ]
    return bool(seen) and seen == [name for name in names if name]


def _phrases_of(row, columns):
    """Ambil daftar frasa dari kolom alias pertama yang terisi (``alias_baru`` dulu)."""
    for column in columns:
        value = row.get(column)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        phrases = []
        for part in text.replace("\n", ",").split(","):
            phrase = _normalize(part)
            if phrase:
                phrases.append(phrase)
        if phrases:
            return phrases
    return []


def _read_workbook(path, sheet):
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)

    if sheet not in workbook.sheetnames:
        raise SystemExit(
            f"Sheet '{sheet}' tidak ada. Sheet yang tersedia: {workbook.sheetnames}"
        )

    rows = workbook[sheet].values
    header = next(rows, None)

    if not header:
        raise SystemExit(f"Sheet '{sheet}' kosong (tanpa baris header).")

    names = [str(name).strip() if name is not None else "" for name in header]

    if "item_name" not in names:
        raise SystemExit(
            f"Sheet '{sheet}' tidak punya kolom item_name; header: {names}"
        )

    columns = [name for name in ALIAS_COLUMNS if name in names]

    if not columns:
        raise SystemExit(
            f"Sheet '{sheet}' tidak punya kolom alias; header: {names}"
        )

    records = []
    for row in rows:
        values = {}
        for index, name in enumerate(names):
            if name and index < len(row):
                values[name] = row[index]

        item_name = values.get("item_name")
        item_name = str(item_name).strip() if item_name is not None else ""

        records.append(
            {
                "item_name": item_name,
                "phrases": _phrases_of(values, columns),
                "status": str(values.get("status_alias") or "").strip(),
                "header": _is_repeated_header(values, names),
            }
        )

    return records


def _target_description():
    """Host/nama database saja; kredensial tidak pernah dicetak."""
    settings_dict = connection.settings_dict
    return (
        f"{settings_dict.get('HOST') or '?'}:{settings_dict.get('PORT') or '?'} "
        f"/ {settings_dict.get('NAME') or '?'}"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--file",
        required=True,
        help="Berkas Excel sumber (sheet item_alias).",
    )
    parser.add_argument(
        "--sheet",
        default="item_alias",
        help="Nama sheet sumber (bawaan: item_alias).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Batasi jumlah baris yang diproses (0 = semua) untuk uji coba.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Tulis sungguhan ke database. Tanpa ini hanya pratinjau.",
    )

    args = parser.parse_args(argv)

    source = Path(args.file)

    if not source.exists():
        raise SystemExit(f"Berkas tidak ditemukan: {source}")

    records = _read_workbook(source, args.sheet)

    if args.limit:
        records = records[: args.limit]

    table = ItemAliasPhrase._meta.db_table
    pairs = []
    seen = set()
    without_alias = 0
    empty_item_name = 0
    duplicate_pairs = 0
    repeated_headers = 0

    for record in records:
        if record["header"]:
            repeated_headers += 1
            continue

        if not record["item_name"]:
            empty_item_name += 1
            continue

        if not record["phrases"]:
            without_alias += 1
            continue

        for phrase in record["phrases"]:
            pair = (phrase, record["item_name"])

            if pair in seen:
                duplicate_pairs += 1
                continue

            seen.add(pair)
            pairs.append(pair)

    print(f"[alias] sumber       : {source.name} / sheet {args.sheet}")
    print(f"[alias] tujuan       : {_target_description()}")
    print(f"[alias] baris dibaca : {len(records)}")
    print(f"[alias] header ulang : {repeated_headers}")
    print(f"[alias] tanpa alias  : {without_alias}")
    print(f"[alias] item kosong  : {empty_item_name}")
    print(f"[alias] frasa ganda  : {duplicate_pairs}")
    print(f"[alias] pasangan     : {len(pairs)}")

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = %s AND table_schema = ANY(current_schemas(FALSE))",
                [table],
            )
            table_exists = cursor.fetchone() is not None
    except DatabaseError as exc:
        raise SystemExit(f"Gagal memeriksa database: {exc}")

    if not table_exists:
        raise SystemExit(
            f"Tabel {table} belum ada di tujuan ini. Jalankan DDL di "
            "ITEM_ALIAS_SEARCH_DESIGN.md lebih dulu (butuh izin pemilik "
            "database); skrip ini tidak membuat tabel."
        )

    with connection.cursor() as cursor:
        cursor.execute(f"SELECT frasa, item_name FROM {table}")
        existing = set(cursor.fetchall())

        cursor.execute(f"SELECT DISTINCT item_name FROM {Item._meta.db_table}")
        known_items = {row[0] for row in cursor.fetchall()}

    new_pairs = [pair for pair in pairs if pair not in existing]
    unknown_items = sorted({item for _, item in pairs if item not in known_items})

    missing_from_source = sorted(existing - set(pairs))

    print(f"[alias] sudah ada    : {len(pairs) - len(new_pairs)}")
    print(f"[alias] akan ditulis : {len(new_pairs)}")
    print(f"[alias] di tabel tapi tak ada di sumber: {len(missing_from_source)} "
          "(tidak dihapus oleh skrip ini)")
    print(f"[alias] item_name tak ada di tabel item: {len(unknown_items)}")

    for item in unknown_items[:REPORT_SAMPLE]:
        print(f"        - {item}")

    if len(unknown_items) > REPORT_SAMPLE:
        print(f"        ... dan {len(unknown_items) - REPORT_SAMPLE} lagi")

    if not args.yes:
        print("[alias] pratinjau saja; tambahkan --yes untuk menulis.")
        return 0

    if not new_pairs:
        print("[alias] tidak ada yang perlu ditulis.")
        return 0

    statement = (
        f"INSERT INTO {table} (frasa, item_name) VALUES (%s, %s) "
        "ON CONFLICT DO NOTHING"
    )

    try:
        with connection.cursor() as cursor:
            cursor.executemany(statement, new_pairs)
    except DatabaseError as exc:
        raise SystemExit(f"Impor gagal, tidak ada frasa yang ditambahkan: {exc}")

    with connection.cursor() as cursor:
        cursor.execute(f"SELECT count(*) FROM {table}")
        total = cursor.fetchone()[0]

    print(f"[alias] selesai      : {len(new_pairs)} frasa ditambahkan, "
          f"{total} pasangan di tabel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
