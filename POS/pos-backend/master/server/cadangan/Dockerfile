# Penjadwal cadangan: pg_dump versi 17 (sama dengan database) + Python untuk
# enkripsi dan unggah ke Neon. Lihat cadangan.py.
FROM postgres:17-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-psycopg2 python3-cryptography \
    && rm -rf /var/lib/apt/lists/*
COPY cadangan.py /app/cadangan.py
ENTRYPOINT ["python3", "/app/cadangan.py"]
CMD ["jalan"]
