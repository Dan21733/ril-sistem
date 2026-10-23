"""WSGI untuk server gudang. Dipakai waitress: master.wsgi_gudang:application"""
import os

from django.core.wsgi import get_wsgi_application

os.environ["DJANGO_SETTINGS_MODULE"] = "master.settings_gudang"

application = get_wsgi_application()
