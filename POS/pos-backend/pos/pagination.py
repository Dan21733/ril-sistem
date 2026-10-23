import logging

from django.core.paginator import InvalidPage, Page
from django.db import DatabaseError, connection, transaction
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination

logger = logging.getLogger(__name__)


class ItemPagination(PageNumberPagination):
    page_size = 30
    # Sync katalog tablet boleh meminta halaman besar (?page_size=200) supaya
    # 2924 baris selesai dalam ~15 request, bukan 98. Default tetap 30 agar
    # kontrak lama (probe, tablet lama) tidak berubah; max 200 mencegah
    # ?page_size raksasa yang membebani DB.
    page_size_query_param = "page_size"
    max_page_size = 200
    page_query_param = "page"
    # Pesan ini bisa tampil di UI kalau tablet meminta halaman di luar jangkauan.
    invalid_page_message = "Halaman {page_number} tidak valid atau di luar jangkauan."

    def paginate_queryset(self, queryset, request, view=None):
        self.request = request
        # Base class normally sets this, but this override builds its own
        # paginator for the fast-path, so resolve it explicitly.
        self.page_size = self.get_page_size(request)
        fallback_queryset = getattr(queryset, "fallback_queryset", None)
        if fallback_queryset is None:
            return super().paginate_queryset(queryset, request, view)

        paginator = self.django_paginator_class(queryset, self.page_size)
        page_number = self.get_page_number(request, paginator)
        is_last_page = page_number in self.last_page_strings
        try:
            page_number = 1 if is_last_page else int(page_number)
        except (TypeError, ValueError) as exc:
            self._raise_invalid_page(page_number, exc)
        if page_number < 1:
            self._raise_invalid_page(
                page_number,
                InvalidPage("Nomor halaman harus lebih dari 0"),
            )

        offset = (page_number - 1) * self.page_size
        try:
            with transaction.atomic():
                count, page_items = queryset.fetch_page(
                    self.page_size,
                    offset,
                    is_last_page,
                )
        except DatabaseError as exc:
            logger.warning(
                "Alias item search failed (%s); falling back to legacy query.",
                exc.__class__.__name__,
            )
            return super().paginate_queryset(fallback_queryset, request, view)

        paginator.count = count
        if is_last_page:
            page_number = max(1, (count + self.page_size - 1) // self.page_size)
        try:
            page_number = paginator.validate_number(page_number)
        except InvalidPage as exc:
            self._raise_invalid_page(page_number, exc)
        self.page = Page(page_items, page_number, paginator)

        if paginator.num_pages > 1 and self.template is not None:
            self.display_page_controls = True
        return page_items

    def _raise_invalid_page(self, page_number, exc):
        msg = self.invalid_page_message.format(
            page_number=page_number,
            message=str(exc),
        )
        raise NotFound(msg) from exc


class TransactionPagination(PageNumberPagination):
    """Riwayat nota per halaman; konvensi param sama dengan ItemPagination."""

    page_size = 20
    page_size_query_param = None
    page_query_param = "page"
    # Pesan bawaan DRF berbahasa Inggris; riwayat nota menampilkannya apa
    # adanya, jadi diganti di sini.
    invalid_page_message = "Halaman {page_number} tidak valid atau di luar jangkauan."
