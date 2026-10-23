"""WSGI untuk server master. Dipakai waitress: master.wsgi_master:application"""
import os

from django.core.wsgi import get_wsgi_application

os.environ["DJANGO_SETTINGS_MODULE"] = "master.settings"

application = get_wsgi_application()
