#!/usr/bin/env python3
"""Cadangan database server rumah: salinan lokal + salinan terenkripsi di Neon.

Neon hanya menyimpan byte acak. Kunci (CADANGAN_KUNCI) tidak pernah dikirim ke
Neon; tanpa kunci itu cadangan tidak bisa dibuka siapa pun.

Cara kerja satu cadangan:
1. `pg_dump -Fc` database utama (format custom, sudah terkompresi).
2. Salinan polos ke folder lokal (HDD), untuk pemulihan cepat di rumah.
3. Isi yang sama dipotong 4 MB, tiap potong dienkripsi AES-256-GCM dengan nonce
   acak. Data tambahan (AAD) memuat id cadangan, nomor potong, dan tanda potong
   terakhir, jadi potong yang ditukar, dibuang, atau dipotong ekornya ketahuan
   saat dibuka.
4. Diunggah ke tabel `cadangan_berkas` / `cadangan_potong` di Neon, lalu diunduh
   ulang dan dicek dengan `pg_restore --list` sebelum ditandai berhasil.

Perintah:
    cadangan.py jalan                  # tunggu jadwal harian (CADANGAN_JAM), terus-menerus
    cadangan.py sekali                 # buat satu cadangan sekarang
    cadangan.py daftar                 # daftar cadangan di Neon
    cadangan.py unduh [ID|terbaru]     # unduh + buka ke folder lokal (.dump)
    cadangan.py uji [ID|terbaru]       # unduh, buka, dan cek bisa dipulihkan

Variabel lingkungan:
    DATABASE_URL            database utama (wajib)
    CADANGAN_KUNCI          64 karakter heksadesimal (32 byte) (wajib untuk Neon)
    CADANGAN_NEON_URL       koneksi PostgreSQL Neon; kosong = hanya salinan lokal
    CADANGAN_FOLDER         default /data/cadangan/db
    CADANGAN_JAM            default 02:30 (waktu lokal TZ)
    CADANGAN_SIMPAN_NEON    default 14 cadangan terakhir
    CADANGAN_SIMPAN_LOKAL   default 7 cadangan terakhir
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import psycopg2
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

UKURAN_POTONG = int(os.getenv("CADANGAN_UKURAN_POTONG") or 4 * 1024 * 1024)
VERSI_FORMAT = 1

DDL_NEON = [
    """CREATE TABLE IF NOT EXISTS cadangan_berkas (
        id uuid PRIMARY KEY,
        dibuat timestamptz NOT NULL DEFAULT now(),
        sumber text NOT NULL,
        versi_format integer NOT NULL,
        kunci_id text NOT NULL,
        jumlah_potong integer,
        ukuran_asli bigint,
        sha256_asli text,
        selesai boolean NOT NULL DEFAULT false
    )""",
    """CREATE TABLE IF NOT EXISTS cadangan_potong (
        berkas_id uuid NOT NULL REFERENCES cadangan_berkas(id) ON DELETE CASCADE,
        urut integer NOT NULL,
        nonce bytea NOT NULL,
        isi bytea NOT NULL,
        PRIMARY KEY (berkas_id, urut)
    )""",
]


class Gagal(Exception):
    pass


def tulis(pesan: str) -> None:
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {pesan}", flush=True)


# ---------------------------------------------------------------------------
# Kunci dan enkripsi
# ---------------------------------------------------------------------------


def kunci() -> bytes:
    nilai = (os.getenv("CADANGAN_KUNCI") or "").strip()
    try:
        k = bytes.fromhex(nilai)
    except ValueError:
        k = b""
    if len(k) != 32:
        raise Gagal("CADANGAN_KUNCI harus 64 karakter heksadesimal (buat dengan: openssl rand -hex 32).")
    return k


def kunci_id(k: bytes) -> str:
    """Penanda kunci (bukan kuncinya): untuk tahu cadangan mana dibuat kunci mana."""
    return hashlib.sha256(b"cadangan-dbrl|" + k).hexdigest()[:12]


def aad(berkas_id: uuid.UUID, urut: int, terakhir: bool) -> bytes:
    return f"dbrl-cadangan|v{VERSI_FORMAT}|{berkas_id}|{urut}|{int(terakhir)}".encode()


def enkripsi(k: bytes, berkas_id: uuid.UUID, urut: int, terakhir: bool, isi: bytes) -> tuple[bytes, bytes]:
    nonce = os.urandom(12)
    return nonce, AESGCM(k).encrypt(nonce, isi, aad(berkas_id, urut, terakhir))


def dekripsi(k: bytes, berkas_id: uuid.UUID, urut: int, terakhir: bool, nonce: bytes, isi: bytes) -> bytes:
    try:
        return AESGCM(k).decrypt(nonce, isi, aad(berkas_id, urut, terakhir))
    except Exception as e:  # InvalidTag
        raise Gagal(f"Potong {urut} tidak bisa dibuka: kunci salah atau data rusak/diubah.") from e


# ---------------------------------------------------------------------------
# Dump lokal
# ---------------------------------------------------------------------------


def folder() -> Path:
    f = Path(os.getenv("CADANGAN_FOLDER") or "/data/cadangan/db")
    f.mkdir(parents=True, exist_ok=True)
    return f


def dump_lokal() -> Path:
    url = os.getenv("DATABASE_URL") or ""
    if not url:
        raise Gagal("DATABASE_URL kosong.")
    nama = folder() / f"db-{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.dump"
    sementara = nama.with_suffix(".sebagian")
    with open(sementara, "wb") as keluar:
        hasil = subprocess.run(["pg_dump", "-Fc", "--no-owner", "--no-privileges", url],
                               stdout=keluar, stderr=subprocess.PIPE)
    if hasil.returncode != 0:
        sementara.unlink(missing_ok=True)
        raise Gagal("pg_dump gagal: " + hasil.stderr.decode(errors="replace")[-500:])
    os.replace(sementara, nama)
    cek_bisa_dipulihkan(nama)
    return nama


def cek_bisa_dipulihkan(berkas: Path) -> int:
    hasil = subprocess.run(["pg_restore", "--list", str(berkas)], capture_output=True)
    if hasil.returncode != 0:
        raise Gagal("pg_restore --list gagal: " + hasil.stderr.decode(errors="replace")[-300:])
    baris = [b for b in hasil.stdout.decode(errors="replace").splitlines() if b and not b.startswith(";")]
    if not any(" TABLE DATA " in b for b in baris):
        raise Gagal("Cadangan tidak memuat data tabel apa pun.")
    return len(baris)


def rapikan_lokal() -> None:
    simpan = int(os.getenv("CADANGAN_SIMPAN_LOKAL") or 7)
    semua = sorted(folder().glob("db-*.dump"))
    for lama in semua[:-simpan] if simpan > 0 else []:
        lama.unlink(missing_ok=True)
    for sisa in folder().glob("*.sebagian"):
        sisa.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Neon
# ---------------------------------------------------------------------------


def neon():
    url = (os.getenv("CADANGAN_NEON_URL") or "").strip()
    if not url:
        return None
    c = psycopg2.connect(url, connect_timeout=20)
    with c, c.cursor() as cur:
        for ddl in DDL_NEON:
            cur.execute(ddl)
    return c


def unggah(c, k: bytes, berkas: Path) -> uuid.UUID:
    berkas_id = uuid.uuid4()
    sha = hashlib.sha256()
    ukuran = 0
    with c, c.cursor() as cur:
        cur.execute("INSERT INTO cadangan_berkas (id, sumber, versi_format, kunci_id) VALUES (%s, %s, %s, %s)",
                    (str(berkas_id), os.getenv("CADANGAN_SUMBER") or "server-rumah", VERSI_FORMAT, kunci_id(k)))
    urut = 0
    with open(berkas, "rb") as f:
        potong = f.read(UKURAN_POTONG)
        while True:
            berikut = f.read(UKURAN_POTONG)
            terakhir = not berikut
            sha.update(potong)
            ukuran += len(potong)
            nonce, isi = enkripsi(k, berkas_id, urut, terakhir, potong)
            with c, c.cursor() as cur:
                cur.execute("INSERT INTO cadangan_potong (berkas_id, urut, nonce, isi) VALUES (%s, %s, %s, %s)",
                            (str(berkas_id), urut, psycopg2.Binary(nonce), psycopg2.Binary(isi)))
            urut += 1
            if terakhir:
                break
            potong = berikut
    with c, c.cursor() as cur:
        cur.execute("UPDATE cadangan_berkas SET jumlah_potong=%s, ukuran_asli=%s, sha256_asli=%s WHERE id=%s",
                    (urut, ukuran, sha.hexdigest(), str(berkas_id)))
    return berkas_id


def pilih(c, pilihan: str = "terbaru", hanya_selesai: bool = True):
    with c.cursor() as cur:
        if pilihan in ("", "terbaru"):
            cur.execute("SELECT id, jumlah_potong, sha256_asli, kunci_id, dibuat FROM cadangan_berkas "
                        + ("WHERE selesai " if hanya_selesai else "") + "ORDER BY dibuat DESC LIMIT 1")
        else:
            cur.execute("SELECT id, jumlah_potong, sha256_asli, kunci_id, dibuat FROM cadangan_berkas "
                        "WHERE id::text LIKE %s ORDER BY dibuat DESC LIMIT 2", (pilihan + "%",))
        hasil = cur.fetchall()
    if not hasil:
        raise Gagal("Cadangan tidak ditemukan di Neon.")
    if len(hasil) > 1 and pilihan not in ("", "terbaru"):
        raise Gagal("Awalan ID cocok dengan lebih dari satu cadangan; tulis lebih panjang.")
    return hasil[0]


def unduh_ke(c, k: bytes, baris, tujuan: Path) -> Path:
    berkas_id, jumlah, sha_asli, kid, _ = baris
    if kid != kunci_id(k):
        raise Gagal(f"Cadangan ini dibuat dengan kunci lain (penanda {kid}), bukan kunci di .env ({kunci_id(k)}).")
    if not jumlah:
        raise Gagal("Cadangan ini belum selesai diunggah.")
    berkas_id = uuid.UUID(str(berkas_id))
    sha = hashlib.sha256()
    sementara = tujuan.with_suffix(".sebagian")
    with open(sementara, "wb") as keluar, c.cursor() as cur:
        for urut in range(jumlah):
            cur.execute("SELECT nonce, isi FROM cadangan_potong WHERE berkas_id=%s AND urut=%s",
                        (str(berkas_id), urut))
            row = cur.fetchone()
            if row is None:
                raise Gagal(f"Potong {urut} hilang.")
            polos = dekripsi(k, berkas_id, urut, urut == jumlah - 1, bytes(row[0]), bytes(row[1]))
            sha.update(polos)
            keluar.write(polos)
    if sha.hexdigest() != sha_asli:
        sementara.unlink(missing_ok=True)
        raise Gagal("Isi cadangan tidak cocok dengan sidik aslinya.")
    os.replace(sementara, tujuan)
    return tujuan


def rapikan_neon(c) -> None:
    simpan = int(os.getenv("CADANGAN_SIMPAN_NEON") or 14)
    with c, c.cursor() as cur:
        # Unggahan yang terputus dan sudah lewat sehari dibuang.
        cur.execute("DELETE FROM cadangan_berkas WHERE NOT selesai AND dibuat < now() - interval '1 day'")
        cur.execute("DELETE FROM cadangan_berkas WHERE selesai AND id NOT IN ("
                    "SELECT id FROM cadangan_berkas WHERE selesai ORDER BY dibuat DESC LIMIT %s)", (simpan,))


# ---------------------------------------------------------------------------
# Perintah
# ---------------------------------------------------------------------------


def sekali() -> None:
    mulai = time.time()
    berkas = dump_lokal()
    tulis(f"Salinan lokal: {berkas} ({berkas.stat().st_size / 1e6:.1f} MB)")
    rapikan_lokal()
    c = neon()
    if c is None:
        tulis("CADANGAN_NEON_URL kosong: hanya salinan lokal.")
        return
    k = kunci()
    try:
        berkas_id = unggah(c, k, berkas)
        # Bukti bisa dipulihkan: unduh ulang dari Neon, buka, dan baca isinya.
        with tempfile.TemporaryDirectory() as tmp:
            uji = unduh_ke(c, k, pilih(c, str(berkas_id), hanya_selesai=False), Path(tmp) / "uji.dump")
            n = cek_bisa_dipulihkan(uji)
        with c, c.cursor() as cur:
            cur.execute("UPDATE cadangan_berkas SET selesai=true WHERE id=%s", (str(berkas_id),))
        rapikan_neon(c)
        tulis(f"Neon: cadangan {berkas_id} tersimpan dan teruji ({n} objek), {time.time() - mulai:.0f} detik.")
    finally:
        c.close()


def daftar() -> None:
    c = neon()
    if c is None:
        raise Gagal("CADANGAN_NEON_URL kosong.")
    with c.cursor() as cur:
        cur.execute("SELECT id, dibuat, ukuran_asli, selesai, kunci_id FROM cadangan_berkas ORDER BY dibuat DESC")
        for i, d, u, s, kid in cur.fetchall():
            print(f"{str(i)[:8]}  {d:%Y-%m-%d %H:%M}  {(u or 0) / 1e6:7.1f} MB  "
                  f"{'ok' if s else 'BELUM SELESAI'}  kunci {kid}")
        cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
        print("Ukuran database Neon:", cur.fetchone()[0])
    c.close()


def unduh(pilihan: str, cek_saja: bool = False) -> None:
    c = neon()
    if c is None:
        raise Gagal("CADANGAN_NEON_URL kosong.")
    k = kunci()
    baris = pilih(c, pilihan)
    if cek_saja:
        with tempfile.TemporaryDirectory() as tmp:
            n = cek_bisa_dipulihkan(unduh_ke(c, k, baris, Path(tmp) / "uji.dump"))
        tulis(f"Cadangan {baris[0]} ({baris[4]:%Y-%m-%d %H:%M}) bisa dibuka dan dipulihkan ({n} objek).")
    else:
        tujuan = folder() / f"pulih-{baris[4]:%Y%m%d-%H%M%S}.dump"
        unduh_ke(c, k, baris, tujuan)
        cek_bisa_dipulihkan(tujuan)
        tulis(f"Terunduh dan terbuka: {tujuan}")
    c.close()


def jadwal_berikut(sekarang: dt.datetime, jam: str) -> dt.datetime:
    j, m = (int(x) for x in jam.split(":"))
    target = sekarang.replace(hour=j, minute=m, second=0, microsecond=0)
    return target if target > sekarang else target + dt.timedelta(days=1)


def jalan() -> None:
    jam = os.getenv("CADANGAN_JAM") or "02:30"
    if os.getenv("CADANGAN_NEON_URL"):
        kunci()  # gagal di awal kalau kunci salah, bukan jam 2 pagi
    tulis(f"Penjadwal cadangan aktif, tiap hari jam {jam}.")
    while True:
        berikut = jadwal_berikut(dt.datetime.now(), jam)
        time.sleep(max(1, (berikut - dt.datetime.now()).total_seconds()))
        for percobaan in range(3):
            try:
                sekali()
                break
            except Exception as e:  # internet putus, Neon tidur, dsb.
                tulis(f"GAGAL (percobaan {percobaan + 1}/3): {e}")
                time.sleep(600)


def main(argv: list[str]) -> int:
    perintah = argv[1] if len(argv) > 1 else "sekali"
    pilihan = argv[2] if len(argv) > 2 else "terbaru"
    try:
        if perintah == "jalan":
            jalan()
        elif perintah == "sekali":
            sekali()
        elif perintah == "daftar":
            daftar()
        elif perintah == "unduh":
            unduh(pilihan)
        elif perintah == "uji":
            unduh(pilihan, cek_saja=True)
        else:
            print(__doc__)
            return 2
    except Gagal as e:
        tulis(f"GAGAL: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
