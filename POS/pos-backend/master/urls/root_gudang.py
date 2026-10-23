"""URL akar server gudang (master.settings_gudang): HANYA /gudang/. Tidak ada /master/ dan /api/."""
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="gudang:beranda", permanent=False)),
    path("gudang/", include("master.urls.gudang")),
]
