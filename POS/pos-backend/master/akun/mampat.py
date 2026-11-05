"""Gzip hanya untuk teks (HTML, JSON, JS, CSS).

Foto faktur, bukti transfer, dan Excel sudah termampatkan; memampatkannya lagi hanya
membebani CPU server rumah tanpa memperkecil ukuran.
"""
from django.middleware.gzip import GZipMiddleware

TEKS = ("text/", "application/json", "application/javascript", "application/manifest+json", "image/svg+xml")


class GzipTeks(GZipMiddleware):
    def process_response(self, request, response):
        jenis = response.get("Content-Type", "")
        if not jenis.startswith(TEKS):
            return response
        return super().process_response(request, response)
