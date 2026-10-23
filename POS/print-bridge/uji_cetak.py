#!/usr/bin/env python3
"""Uji cetak langsung dari satu komputer ke printer Epson (ESC/POS).

Kenapa ada: sebelum melibatkan POS dan penghubung cetak, pertanyaan pertama saat
memasang printer biasanya cuma satu - "apakah komputer ini bisa mencetak ke
printer itu?". Berkas ini menjawab pertanyaan itu saja: satu halaman uji, tanpa
server, tanpa aplikasi POS, tanpa penghubung cetak. Hanya pustaka standar
Python 3, jadi bisa dijalankan dari laptop mana pun yang ada Python-nya.

Pemakaian:

    PRINTER_HOST=10.0.0.9 python uji_cetak.py
    python uji_cetak.py --host 10.0.0.9
    python uji_cetak.py --host 10.0.0.9 --check     # hanya uji port 9100
    python uji_cetak.py --host 10.0.0.9 --no-cut    # unit tanpa autocutter

Bedanya dengan bridge: bridge adalah layanan yang menetap dan menerima pekerjaan
cetak dari POS. Berkas ini sekali jalan - kirim satu halaman, laporkan hasilnya,
keluar. Perintah dan ukuran halamannya sama dengan nota POS
(`pos-frontend/src/services/printer/escpos.js`): Font B, 40 kolom, kertas 76 mm,
umpan 4 baris, lalu potong. Jadi kalau halaman ini keluar rapi, tata letak nota
juga akan rapi.

Yang diukur hanya "printer menerima koneksi dan byte terkirim". Kertas habis,
pita kering, atau penutup printer terbuka tidak terlihat dari sini - hasil
akhirnya tetap kertas yang keluar (atau tidak keluar).

Kode keluar: 0 byte terkirim, 1 sambungan/kirim gagal, 2 pemakaian salah.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import os
import socket
import sys
import unicodedata

# ---------------------------------------------------------------------------
# Perintah ESC/POS
#
# Disalin dari encoder POS supaya halaman uji ini benar-benar mewakili jalur
# yang sama. Kalau encoder POS berubah (mis. lebar kolom), ubah juga di sini:
# halaman uji yang berbeda dari nota justru menyesatkan saat mencari masalah.
# ---------------------------------------------------------------------------

# Font B pada kertas 76 mm: 40 kolom. Batas keras, bukan preferensi.
COLUMNS = 40

INIT = bytes([0x1B, 0x40])  # ESC @ - reset printer
FONT_B = bytes([0x1B, 0x4D, 0x01])  # ESC M 1 - Font B
ALIGN_LEFT = bytes([0x1B, 0x61, 0x00])  # ESC a 0
ALIGN_CENTER = bytes([0x1B, 0x61, 0x01])  # ESC a 1
BOLD_ON = bytes([0x1B, 0x45, 0x01])  # ESC E 1
BOLD_OFF = bytes([0x1B, 0x45, 0x00])  # ESC E 0
LF = bytes([0x0A])
CUT = bytes([0x1D, 0x56, 0x00])  # GS V 0 - potong penuh (diabaikan tanpa cutter)

# (teks, perataan, tebal)
PageLine = tuple[str, str, bool]


def to_printer_text(value: str) -> str:
    """Buang semua di luar ASCII yang bisa dicetak printer (halaman kode PC437)."""
    decomposed = unicodedata.normalize("NFKD", str(value))
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))

    return without_marks.encode("ascii", "replace").decode("ascii")


def wrap(text: str, columns: int = COLUMNS) -> list[str]:
    """Patahkan baris panjang supaya tidak ada teks yang hilang di kertas."""
    output: list[str] = []

    for raw_line in to_printer_text(text).split("\n"):
        if not raw_line.strip():
            output.append("")
            continue

        current = ""

        for word in raw_line.split(" "):
            while len(word) > columns:
                if current:
                    output.append(current)
                    current = ""

                output.append(word[:columns])
                word = word[columns:]

            candidate = word if not current else f"{current} {word}"

            if len(candidate) <= columns:
                current = candidate
                continue

            output.append(current)
            current = word

        output.append(current)

    return output


def encode_page(lines: list[PageLine], *, feed_lines: int = 4, cut: bool = True) -> bytes:
    """Baris halaman uji -> byte ESC/POS, urutan perintahnya sama dengan nota POS."""
    payload = bytearray()
    payload += INIT
    payload += FONT_B
    payload += ALIGN_LEFT

    bold = False

    for text, align, want_bold in lines:
        if want_bold != bold:
            payload += BOLD_ON if want_bold else BOLD_OFF
            bold = want_bold

        payload += ALIGN_CENTER if align == "center" else ALIGN_LEFT

        for chunk in wrap(text):
            payload += chunk.encode("ascii")
            payload += LF

    if bold:
        payload += BOLD_OFF

    payload += ALIGN_LEFT
    payload += LF * max(0, feed_lines)

    if cut:
        payload += CUT

    return bytes(payload)


def build_test_page(host: str, port: int, *, now: datetime | None = None) -> list[PageLine]:
    """Isi halaman uji. Pendek dengan sengaja: tujuannya membuktikan jalur, bukan mengukur."""
    stamp = (now or datetime.now()).strftime("%d-%m-%Y %H:%M:%S")

    return [
        ("UJI CETAK", "center", True),
        (stamp, "center", False),
        ("", "left", False),
        ("Kalau halaman ini keluar rapi,", "left", False),
        ("jalur cetak dari komputer ini", "left", False),
        ("ke printer sudah benar.", "left", False),
        ("", "left", False),
        (f"Printer  : {host}:{port}", "left", False),
        (f"Komputer : {socket.gethostname()}", "left", False),
        ("", "left", False),
        ("Lembar putih dan karbon harus", "left", False),
        ("berisi teks yang sama.", "left", False),
    ]


def explain_failure(host: str, port: int, error: BaseException) -> None:
    """Terjemahkan galat jaringan jadi daftar periksa yang bisa dikerjakan orang toko."""
    print(f"GAGAL: printer {host}:{port} tidak menjawab ({error.__class__.__name__}: {error}).")
    print("")
    print("Yang biasanya jadi sebab:")
    print("  - IP printer salah atau sudah berubah. Pastikan alamatnya tetap")
    print("    (DHCP reservation di router atau IP statis lewat EpsonNet Config).")
    print("  - Kabel LAN printer lepas, atau printer dalam keadaan mati.")
    print("  - Komputer dan printer tidak berada di jaringan yang sama.")
    print("  - Port 9100 tertutup di jaringan atau oleh firewall komputer ini.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Kirim satu halaman uji ESC/POS ke printer Epson lewat port mentah 9100.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("PRINTER_HOST", ""),
        help="IP printer. Kalau kosong, dibaca dari variabel lingkungan PRINTER_HOST.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PRINTER_PORT") or 9100),
        help="Port printer, default 9100 (raw ESC/POS Epson).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("PRINT_CONNECT_TIMEOUT") or 5.0),
        help="Detik menunggu printer menerima koneksi, default 5.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Hanya uji sambungan ke port printer; tidak mengirim byte apa pun.",
    )
    parser.add_argument(
        "--no-cut",
        action="store_true",
        help="Jangan kirim perintah potong; untuk unit tanpa autocutter (Type D).",
    )
    args = parser.parse_args(argv)

    host = (args.host or "").strip()

    if not host:
        print("PRINTER_HOST belum diisi.")
        print("Contoh: python uji_cetak.py --host 10.0.0.9")
        return 2

    print(f"Menghubungi printer {host}:{args.port} ...")

    payload = b""

    try:
        with socket.create_connection((host, args.port), timeout=args.timeout) as connection:
            print("Printer menerima koneksi.")

            if args.check:
                print("Sampai di sini saja: tidak ada byte yang dikirim (mode --check).")
                return 0

            payload = encode_page(build_test_page(host, args.port), cut=not args.no_cut)
            connection.sendall(payload)

            # Half-close: printer tahu akhir pekerjaan, kita tidak menunggu balasan.
            try:
                connection.shutdown(socket.SHUT_WR)
            except OSError:
                pass
    except (OSError, socket.timeout) as error:
        explain_failure(host, args.port, error)
        return 1

    print(f"Halaman uji terkirim: {len(payload)} byte.")
    print("")
    print("Ambil kertasnya sekarang. Kalau keluar satu halaman 'UJI CETAK', jalur cetak")
    print("dari komputer ini ke printer sudah benar.")
    print("")
    print("Catatan: terkirim belum berarti tercetak. Kertas habis, pita kering, atau")
    print("penutup printer terbuka baru ketahuan dari kertasnya.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
