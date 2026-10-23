"""Pembuat tabel POS (kasir).

Tabel POS tidak dikelola Django (managed=False) dan repo POS tidak memuat skrip
pembuatannya. Tabel-tabel itu dibuat di sini mengikuti POS/docs/schema.dbml dan
dokumen desain POS (ITEM_ALIAS_SEARCH_DESIGN.md untuk `item_alias`).

Semua perintah memakai IF NOT EXISTS, jadi aman dijalankan ke database yang
tabelnya sudah ada. Sejak 17 Okt 2026 database server rumah adalah database
utama kasir dan master; `master_setup` memanggil ini.
"""
from django.db import connection


def _jenis(koneksi=None):
    pg = (koneksi or connection).vendor == "postgresql"
    return {
        # Di luar PostgreSQL, Django menyimpan UUID sebagai 32 karakter heksadesimal.
        "uuid": "uuid" if pg else "char(32)",
        "ts": "timestamptz" if pg else "datetime",
        "bool": "boolean" if pg else "bool",
    }


def daftar_ddl(koneksi=None):
    j = _jenis(koneksi)
    return [
        f"""CREATE TABLE IF NOT EXISTS worker (
            id varchar(5) PRIMARY KEY,
            name text NOT NULL,
            pin text NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS device (
            id {j['uuid']} PRIMARY KEY,
            device_code varchar(3) NOT NULL UNIQUE
        )""",
        f"""CREATE TABLE IF NOT EXISTS device_transaction_sequence (
            device_id {j['uuid']} PRIMARY KEY REFERENCES device(id),
            next_sequence integer NOT NULL DEFAULT 1,
            updated_at {j['ts']} NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS item (
            moka_id varchar,
            category varchar,
            sku varchar,
            item_name varchar NOT NULL,
            ecommerce_item {j['bool']},
            brand varchar,
            variant varchar NOT NULL,
            min_price integer,
            max_price integer,
            in_stock integer,
            track_stock {j['bool']},
            track_alert {j['bool']},
            stock_alert integer,
            cost_amount integer,
            PRIMARY KEY (item_name, variant)
        )""",
        f"""CREATE TABLE IF NOT EXISTS transactions (
            transaction_id varchar(10) UNIQUE,
            worker_id varchar NOT NULL REFERENCES worker(id),
            transaction_time {j['ts']} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            total_amount integer NOT NULL,
            payment_method varchar NOT NULL,
            payment_amount integer NOT NULL,
            change_amount integer NOT NULL,
            item_amount integer NOT NULL,
            shipping_amount integer,
            cust_name text,
            phone_no varchar(20),
            sync_id {j['uuid']} PRIMARY KEY
        )""",
        f"""CREATE TABLE IF NOT EXISTS transaction_detail (
            item_name varchar NOT NULL,
            variant varchar NOT NULL,
            quantity integer NOT NULL,
            unit_price integer NOT NULL,
            discount integer DEFAULT 0,
            subtotal integer NOT NULL,
            sync_id {j['uuid']} NOT NULL REFERENCES transactions(sync_id),
            PRIMARY KEY (sync_id, item_name, variant),
            FOREIGN KEY (item_name, variant) REFERENCES item(item_name, variant)
        )""",
        f"""CREATE TABLE IF NOT EXISTS payment_proof (
            proof_id {j['uuid']} PRIMARY KEY,
            sync_id {j['uuid']} NOT NULL REFERENCES transactions(sync_id),
            object_key text NOT NULL UNIQUE,
            content_type varchar(32) NOT NULL,
            byte_size integer NOT NULL,
            sha256 varchar(64) NOT NULL,
            captured_at {j['ts']},
            uploaded_at {j['ts']} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            device_code varchar(3)
        )""",
        f"""CREATE TABLE IF NOT EXISTS refund_event (
            refund_id {j['uuid']} PRIMARY KEY,
            sync_id {j['uuid']} NOT NULL REFERENCES transactions(sync_id),
            transaction_id varchar(10),
            event_type varchar(10) NOT NULL,
            reason text NOT NULL,
            refund_method varchar(12) NOT NULL DEFAULT 'cash',
            refund_amount integer NOT NULL,
            approver_worker_id varchar(5) NOT NULL REFERENCES worker(id),
            device_code varchar(3) NOT NULL,
            created_at {j['ts']} NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS refund_event_detail (
            refund_id {j['uuid']} NOT NULL REFERENCES refund_event(refund_id),
            item_name varchar NOT NULL,
            variant varchar NOT NULL,
            quantity integer NOT NULL,
            unit_price integer NOT NULL,
            subtotal integer NOT NULL,
            PRIMARY KEY (refund_id, item_name, variant)
        )""",
        # Alias pencarian milik POS (per item_name, tanpa FK ke item).
        """CREATE TABLE IF NOT EXISTS item_alias (
            frasa varchar NOT NULL,
            item_name varchar NOT NULL,
            PRIMARY KEY (frasa, item_name)
        )""",
        "CREATE INDEX IF NOT EXISTS item_alias_name_idx ON item_alias (item_name)",
    ]


# Hanya PostgreSQL: pencarian kasir memakai fungsi trigram (TrigramSimilarity),
# jadi ekstensi pg_trgm wajib ada; index-nya mengikuti ITEM_ALIAS_SEARCH_DESIGN.md.
DDL_POSTGRES = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE INDEX IF NOT EXISTS item_alias_frasa_trgm_idx ON item_alias USING gin (frasa gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS item_name_trgm_idx ON item USING gin (item_name gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS item_variant_trgm_idx ON item USING gin (variant gin_trgm_ops)",
]


# Kolom yang ditambahkan POS setelah tabelnya ada (CUSTOMER_DESIGN.md, 2026-10-14).
# Database pengembangan yang dibuat sebelum tanggal itu belum punya kolom ini,
# padahal model Transactions sudah membacanya. Keduanya boleh NULL.
KOLOM_SUSULAN = [
    ("transactions", "cust_name", "text"),
    ("transactions", "phone_no", "varchar(20)"),
]


def buat_tabel_pos(koneksi=None):
    koneksi = koneksi or connection
    with koneksi.cursor() as cursor:
        for ddl in daftar_ddl(koneksi):
            cursor.execute(ddl)
        if koneksi.vendor == "postgresql":
            for ddl in DDL_POSTGRES:
                cursor.execute(ddl)
        for tabel, kolom, jenis in KOLOM_SUSULAN:
            ada = {k.name for k in koneksi.introspection.get_table_description(cursor, tabel)}
            if kolom not in ada:
                cursor.execute(f"ALTER TABLE {tabel} ADD COLUMN {kolom} {jenis}")


def tabel_ada(nama):
    return nama in connection.introspection.table_names()
