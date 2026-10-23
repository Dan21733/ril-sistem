"""Uji modul master, dipisah per bagian.

Jalankan semua:   python manage.py test master --settings=master.settings
Satu bagian:      python manage.py test master.tests.test_lokasi --settings=master.settings

Tabel POS tidak dikelola Django, jadi dibuat dulu lewat master.layanan.legacy di dalam
database uji (bukan database yang sedang dipakai).
"""
