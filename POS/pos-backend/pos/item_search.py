"""Pencarian kandidat item dan alias di PostgreSQL."""

from __future__ import annotations

from django.db import DatabaseError, connection

from . import data_master
from .models import Item, ItemAliasPhrase

# %% operators are intentional: psycopg's pyformat parser treats bare % as a
# placeholder. PostgreSQL's GIN trigram indexes can serve these predicates.
_PAGE_SQL = """
WITH threshold AS MATERIALIZED (
    SELECT set_config('pg_trgm.similarity_threshold', %(threshold)s, true),
           set_config('pg_trgm.word_similarity_threshold', %(threshold)s, true)
),
alias_hits AS MATERIALIZED (
    SELECT a.item_name,
           MAX(GREATEST(SIMILARITY(a.frasa, %(q)s),
                        WORD_SIMILARITY(%(q)s, a.frasa))) AS alias_sim
    FROM {alias_table} a
    CROSS JOIN threshold
    WHERE a.frasa %% %(q)s OR a.frasa %%> %(q)s
    GROUP BY a.item_name
),
candidates AS MATERIALIZED (
    SELECT i.item_name, i.variant
    FROM {item_table} i
    CROSS JOIN threshold
    WHERE i.item_name %% %(q)s OR i.item_name %%> %(q)s
       OR i.item_name ILIKE %(like)s
       OR i.variant %% %(q)s OR i.variant %%> %(q)s
       OR i.variant ILIKE %(like)s
    UNION
    SELECT i.item_name, i.variant
    FROM {item_table} i
    JOIN alias_hits h ON h.item_name = i.item_name
    {kecuali_nonaktif}
),
total AS MATERIALIZED (
    SELECT COUNT(*) AS count FROM candidates
),
page_rows AS (
    SELECT i.item_name,
           i.variant,
           i.max_price,
           i.min_price,
           i.in_stock,
           i.stock_alert,
           /* Peringkat dua lapis (permintaan pemilik 2026-10-17): kemiripan
            * nama/varian selalu didahulukan, alias hanya memecah seri dan
            * mengurutkan hasil yang memang hanya cocok lewat alias. Sebelumnya
            * keduanya digabung GREATEST sehingga alias bisa menggeser item
            * yang namanya memuat kata kueri (mis. "Pintu") ke bawah. Item
            * yang lolos lewat alias saja pasti punya text_sim < threshold,
            * jadi lapis nama dan lapis alias tidak pernah tercampur. */
           GREATEST(SIMILARITY(i.item_name, %(q)s),
                    SIMILARITY(i.variant, %(q)s),
                    WORD_SIMILARITY(%(q)s, i.item_name),
                    WORD_SIMILARITY(%(q)s, i.variant)) AS text_sim,
           COALESCE(h.alias_sim, 0) AS alias_sim
    FROM {item_table} i
    JOIN candidates c
      ON c.item_name = i.item_name AND c.variant = i.variant
    LEFT JOIN alias_hits h ON h.item_name = i.item_name
    ORDER BY text_sim DESC, alias_sim DESC, i.item_name, i.variant
    LIMIT %(limit)s
    OFFSET CASE
        WHEN %(last_page)s THEN
            (GREATEST((SELECT count FROM total) - 1, 0) / %(limit)s) * %(limit)s
        ELSE %(offset)s
    END
)
SELECT p.item_name,
       p.variant,
       p.max_price,
       p.min_price,
       p.in_stock,
       p.stock_alert,
       p.text_sim,
       p.alias_sim,
       {kolom_rak},
       CASE WHEN %(include_count)s THEN total.count ELSE NULL END
FROM total
LEFT JOIN page_rows p ON TRUE
ORDER BY p.text_sim DESC NULLS LAST,
         p.alias_sim DESC NULLS LAST,
         p.item_name,
         p.variant
"""

_ITEM_FIELDS = (
    "item_name",
    "variant",
    "max_price",
    "min_price",
    "in_stock",
    "stock_alert",
)


def _item_from_row(row: tuple) -> Item:
    """Bangun satu Item dari baris _PAGE_SQL menurut NAMA kolom.

    Jangan pakai `Item.from_db` di sini: ia memetakan nilai secara posisional
    ke SEMUA concrete field model menurut urutan definisi model (melewatkan
    field yang tidak ada di daftar sebagai DEFERRED), BUKAN menurut urutan
    daftar field yang diberikan. Karena urutan definisi model adalah
    (..., in_stock, max_price, min_price, ...) sedangkan SELECT mengembalikan
    (..., max_price, min_price, in_stock, ...), from_db menggeser ketiganya:
    harga tampil = floor, floor = stok, stok = ceiling. Konstruksi eksplisit
    per nama kolom kebal terhadap itu.
    """
    item = Item(
        item_name=row[0],
        variant=row[1],
        max_price=row[2],
        min_price=row[3],
        in_stock=row[4],
        stock_alert=row[5],
    )
    if len(row) > 9:
        item.rak_kode = row[8]
    item._state.adding = False
    item._state.db = connection.alias
    return item

_alias_table_state: bool | None = None


def alias_table_exists() -> bool:
    """Cache per-proses keberadaan tabel yang skemanya dikelola di luar Django."""
    global _alias_table_state
    if _alias_table_state is None:
        try:
            with connection.cursor() as cursor:
                tables = connection.introspection.table_names(cursor)
            _alias_table_state = ItemAliasPhrase._meta.db_table in tables
        except DatabaseError:
            _alias_table_state = False
    return _alias_table_state


def reset_alias_table_cache() -> None:
    """Lupakan deteksi tabel setelah DDL dan pada tes."""
    global _alias_table_state
    _alias_table_state = None


def _format_sql(template: str) -> str:
    # Varian nonaktif dan kode rak dari master (pos/data_master.py), hanya
    # kalau tabel master ada di database ini.
    master = data_master.tersedia()
    return template.format(
        item_table=connection.ops.quote_name(Item._meta.db_table),
        alias_table=connection.ops.quote_name(ItemAliasPhrase._meta.db_table),
        kecuali_nonaktif=f"EXCEPT {data_master.NONAKTIF_SQL}" if master else "",
        kolom_rak=f"({data_master.RAK_SQL.format(t='p')})" if master else "NULL",
    )


def _params(search: str) -> dict[str, object]:
    return {"q": search, "like": f"%{search}%", "threshold": "0.3"}


def _execute_search_sql(sql: str, params: dict[str, object]) -> list[tuple]:
    with connection.cursor() as cursor:
        cursor.execute(_format_sql(sql), params)
        return cursor.fetchall()


class AliasSearchResults:
    """Hasil alias: total dan satu halaman diambil dalam satu query SQL."""

    ordered = True

    def __init__(self, search: str, fallback_queryset):
        self.search = search
        self.fallback_queryset = fallback_queryset
        self._count: int | None = None
        self._page_items: list[Item] = []
        self._page_key: tuple[int, int, bool, bool] | None = None

    def __len__(self) -> int:
        # Django's Paginator consults len(object_list) before using the count
        # property. Resolve one item and total together for that protocol.
        if self._count is None:
            self.fetch_page(1, 0, False, include_count=True)
        return self._count

    @property
    def count(self) -> int:
        if self._count is None:
            self.fetch_page(
                limit=1,
                offset=0,
                last_page=False,
                include_count=True,
            )
        return self._count

    def fetch_page(
        self,
        limit: int,
        offset: int,
        last_page: bool,
        include_count: bool = True,
    ):
        key = (limit, offset, last_page, include_count)
        if self._page_key == key and (not include_count or self._count is not None):
            return self._count, self._page_items

        params = {
            **_params(self.search),
            "limit": limit,
            "offset": offset,
            "last_page": last_page,
            "include_count": include_count,
        }
        rows = _execute_search_sql(_PAGE_SQL, params)
        if not rows:
            raise RuntimeError(
                "Kueri pencarian alias tidak mengembalikan total"
            )

        if rows[0][-1] is not None:
            self._count = int(rows[0][-1])
        elif include_count:
            raise DatabaseError(
                "Kueri pencarian alias tidak mengembalikan jumlah total"
            )
        self._page_items = [
            _item_from_row(row)
            for row in rows
            if row[0] is not None
        ]
        return self._count, self._page_items

    def __getitem__(self, page_slice: slice) -> list[Item]:
        if not isinstance(page_slice, slice) or page_slice.step is not None:
            raise TypeError(
                "Hasil pencarian alias hanya mendukung potongan paginator"
            )
        start = page_slice.start or 0
        stop = page_slice.stop
        if start < 0 or stop is None or stop < start:
            raise ValueError("Potongan halaman pencarian alias tidak valid")
        if start == 0:
            _, items = self.fetch_page(stop, 0, False)
            return items
        _, items = self.fetch_page(stop - start, start, False)
        return items


def search_items(search: str, fallback_queryset) -> AliasSearchResults | None:
    """Buat hasil alias bila backend PostgreSQL dan tabel mapping tersedia."""
    if connection.vendor != "postgresql" or not alias_table_exists():
        return None
    return AliasSearchResults(search, fallback_queryset)
