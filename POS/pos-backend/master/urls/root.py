"""URL akar saat proses dijalankan dengan master.settings.

Sengaja tidak memuat /api/ milik POS: proses master hanya melayani master.
"""
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="master:dashboard", permanent=False)),
    path("master/", include("master.urls.master")),
    path("gudang/", include("master.urls.gudang")),
]
