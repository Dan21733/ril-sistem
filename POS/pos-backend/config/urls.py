"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.urls import include, path

# Route django.contrib.admin sengaja dihapus: POS tidak punya admin site
# (models managed=False, tanpa akun/login — aturan 2026-09-26), dan
# /admin/ yang terekspos tanpa pemakaian hanya memperluas permukaan serangan.

urlpatterns = [
    path("api/", include("pos.urls")),
]