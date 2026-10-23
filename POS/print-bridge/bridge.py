#!/usr/bin/env python3
"""Print bridge LAN untuk printer Epson TM-U220B (ESC/POS).

Jalur cetak POS (lihat POS/pos-backend/PRINT_PATH_DESIGN.md):

    Tablet (POS)              Mesin LAN                  Printer
    POST /print        ->     print bridge        ->     10.0.0.9:9100
    (byte ESC/POS)            relay TCP mentah           (ESC/POS)

Kenapa bridge ini ada: halaman web tidak bisa membuka TCP mentah ke port 9100
(port itu bukan HTTP), dan kalau POS disajikan lewat HTTPS, panggilan ke
`http://` di LAN diblokir sebagai mixed content. Yang bisa membuka koneksi hanya
proses di mesin, bukan browser.

Sifat yang mengikat desain — bridge adalah **pipa bodoh**:

- Ia tidak tahu apa itu nota, item, atau harga. Ia menerima byte ESC/POS dan
  meneruskannya apa adanya, sehingga tata letak nota hanya punya satu tempat:
  sisi POS (`pos-frontend/src/services/printer/`).
- Ia tidak menyimpan antrean panjang dan tidak menunggu operator. Antrean ada di
  POS. Kalau printer tidak terjangkau, jawabannya 503 dan POS yang mengatur
  percobaan ulang.
- `X-Print-Job` dipakai sebagai kunci idempotensi: kiriman ulang job yang sama
  dijawab 200 tanpa mencetak lagi, supaya percobaan ulang yang sebenarnya sudah
  berhasil tidak mengeluarkan nota kedua.
- Ia **tidak** tahu isi printer (kertas habis, pita kering, macet). `GET /health`
  hanya memastikan printer masih menerima koneksi di port cetaknya, tanpa
  mengirim byte apa pun — jadi aman dipanggil berkala oleh POS untuk menampilkan
  status "printer tersambung" di layar kasir.

Pemakaian:

    python bridge.py --printer-host 10.0.0.9 --printer-name "Kasir 1"
    python bridge.py --selftest        # uji relay + idempotensi tanpa printer

Opsi baris perintah dianjurkan karena bentuk variabel lingkungan berbeda per
shell di Windows - `set VAR=nilai` hanya berlaku di cmd, `$env:VAR="nilai"`
hanya di PowerShell - sehingga baris yang terlihat benar bisa berhenti dengan
"PRINTER_HOST belum diisi". Ketiga bentuk ini tetap didukung:

    Windows cmd        : set PRINTER_HOST=10.0.0.9 && python bridge.py
    Windows PowerShell : $env:PRINTER_HOST="10.0.0.9"; python bridge.py
    Linux/macOS        : PRINTER_HOST=10.0.0.9 python bridge.py

Konfigurasi (variabel lingkungan, atau opsi baris perintah yang senama):

    PRINTER_HOST          IP printer (WAJIB; beri DHCP reservation)
    PRINTER_PORT          default 9100 (raw ESC/POS Epson)
    PRINTER_NAME          default "Epson TM-U220B" — label yang dibaca kasir di
                          layar POS (mis. "Kasir 1"), bukan hasil deteksi printer
    PRINT_BRIDGE_HOST     default 0.0.0.0 (biarkan hanya di LAN)
    PRINT_BRIDGE_PORT     default 9101
    PRINT_BRIDGE_TOKEN    opsional; kalau diisi, header X-Print-Token wajib cocok
    PRINT_MAX_BYTES       default 65536 — batas ukuran satu pekerjaan cetak
    PRINT_CONNECT_TIMEOUT default 5 detik
    PRINT_JOB_MEMORY      default 500 — berapa ID job terakhir yang diingat
    PRINT_HEALTH_TIMEOUT  default 2 detik — batas tunggu saat GET /health
                          menyentuh printer; lebih pendek dari timeout cetak
                          karena pemakai manusia sedang menunggu jawabannya
"""

from __future__ import annotations

import argparse
from collections import OrderedDict
from dataclasses import dataclass
import http.client
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request

BRIDGE_VERSION = "1.0"

# Rute yang dikenali. Selain ini 404, termasuk POST ke rute lain.
PRINT_PATH = "/print"
HEALTH_PATH = "/health"

# Alias: POS memanggil bridge lewat satu alamat same-origin yang sudah memuat
# prefix jalur cetak (`/print`), dan reverse proxy meneruskan prefix itu apa
# adanya. Jadi pemeriksaan status juga harus terjawab di `/print/health`.
PRINT_HEALTH_PATH = f"{PRINT_PATH}{HEALTH_PATH}"

JOB_HEADER = "X-Print-Job"
TOKEN_HEADER = "X-Print-Token"


# ---------------------------------------------------------------------------
# Konfigurasi
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BridgeConfig:
    printer_host: str
    printer_port: int
    printer_name: str
    bridge_host: str
    bridge_port: int
    token: str
    max_bytes: int
    connect_timeout: float
    job_memory: int
    health_timeout: float

    @classmethod
    def from_environ(cls, environ: dict[str, str] | None = None) -> "BridgeConfig":
        env = os.environ if environ is None else environ

        printer_host = (env.get("PRINTER_HOST") or "").strip()

        if not printer_host:
            raise SystemExit(
                "PRINTER_HOST belum diisi. Contoh:\n"
                "    python bridge.py --printer-host 10.0.0.9\n"
                '    Windows PowerShell : $env:PRINTER_HOST="10.0.0.9"; python bridge.py\n'
                "    Windows cmd        : set PRINTER_HOST=10.0.0.9 && python bridge.py\n"
                "    Linux/macOS        : PRINTER_HOST=10.0.0.9 python bridge.py"
            )

        return cls(
            printer_host=printer_host,
            printer_port=int(env.get("PRINTER_PORT") or 9100),
            # Label, bukan deteksi: ESC/POS tidak memberi nama model yang bisa
            # dipercaya, dan yang dibutuhkan kasir cuma "printer yang mana".
            printer_name=(env.get("PRINTER_NAME") or "Epson TM-U220B").strip(),
            bridge_host=(env.get("PRINT_BRIDGE_HOST") or "0.0.0.0").strip(),
            bridge_port=int(env.get("PRINT_BRIDGE_PORT") or 9101),
            token=(env.get("PRINT_BRIDGE_TOKEN") or "").strip(),
            max_bytes=int(env.get("PRINT_MAX_BYTES") or 65536),
            connect_timeout=float(env.get("PRINT_CONNECT_TIMEOUT") or 5.0),
            job_memory=max(1, int(env.get("PRINT_JOB_MEMORY") or 500)),
            health_timeout=float(env.get("PRINT_HEALTH_TIMEOUT") or 2.0),
        )


class PrinterUnreachable(RuntimeError):
    """Printer tidak menerima koneksi atau menolak menulis. POS yang mengulang."""


# ---------------------------------------------------------------------------
# Relay byte ke printer
# ---------------------------------------------------------------------------


class PrinterRelay:
    """Mengirim byte ke printer dan menutup koneksi.

    Satu pekerjaan = satu koneksi. TM-U220B tidak butuh sesi panjang, dan
    membuka-menutup per pekerjaan membuat "printer mati" ketahuan dengan cepat
    sebagai kegagalan koneksi, bukan sebagai tulisan yang menggantung.
    """

    def __init__(self, config: BridgeConfig) -> None:
        self.config = config

    def probe(self) -> tuple[str, str | None]:
        """Cek printer tanpa mengirim byte apa pun.

        Bridge tidak bisa membaca status printer lewat ESC/POS dengan aman
        (`DLE EOT` memakai jalur yang sama dengan pekerjaan cetak dan tidak
        semua antarmuka menjawabnya). Karena itu yang diukur adalah hal paling
        dasar yang bisa dipastikan: printer menerima koneksi di port cetaknya
        atau tidak. Yang dilaporkan adalah "printer menjawab", bukan "printer
        siap" — kertas habis atau pita kering tetap tidak terlihat dari sini.
        """
        try:
            with socket.create_connection(
                (self.config.printer_host, self.config.printer_port),
                timeout=self.config.health_timeout,
            ):
                return ("ok", None)
        except (OSError, socket.timeout) as error:
            return ("unreachable", f"{error.__class__.__name__}: {error}")

    def send(self, payload: bytes) -> None:
        if not payload:
            raise ValueError("Pekerjaan cetak kosong.")

        try:
            with socket.create_connection(
                (self.config.printer_host, self.config.printer_port),
                timeout=self.config.connect_timeout,
            ) as connection:
                connection.sendall(payload)

                # Half-close: printer tahu akhir pekerjaan, kita tetap bisa
                # membaca kalau ada galat, tapi tidak menunggu selamanya.
                try:
                    connection.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
        except (OSError, socket.timeout) as error:
            raise PrinterUnreachable(
                f"Printer {self.config.printer_host}:{self.config.printer_port} "
                f"tidak terjangkau ({error.__class__.__name__}: {error})."
            ) from error


# ---------------------------------------------------------------------------
# Memori idempotensi
# ---------------------------------------------------------------------------


class JobMemory:
    """Mengingat `X-Print-Job` terakhir supaya kiriman kembar tidak mencetak dua kali.

    Sengaja di memori, bukan di disk: isinya hanya jendela percobaan ulang
    beberapa menit, dan bridge harus tetap bisa dijalankan tanpa menulis apa pun.
    Konsekuensinya dicatat di README — restart bridge mengosongkan memori ini,
    sehingga job lama yang diulang setelah restart akan tercetak sekali lagi.
    """

    def __init__(self, capacity: int) -> None:
        self.capacity = max(1, capacity)
        self.lock = threading.Lock()
        self.seen: OrderedDict[str, float] = OrderedDict()

    def remember(self, job_id: str) -> bool:
        """True kalau job ini baru; False kalau sudah pernah diterima."""
        with self.lock:
            if job_id in self.seen:
                self.seen.move_to_end(job_id)
                return False

            self.seen[job_id] = time.time()

            while len(self.seen) > self.capacity:
                self.seen.popitem(last=False)

            return True

    def forget(self, job_id: str) -> None:
        """Pekerjaan yang gagal ke printer tidak dianggap sudah dicetak."""
        with self.lock:
            self.seen.pop(job_id, None)

    def __len__(self) -> int:
        with self.lock:
            return len(self.seen)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class PrintBridgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"PosPrintBridge/{BRIDGE_VERSION}"

    # -- util ---------------------------------------------------------------

    @property
    def config(self) -> BridgeConfig:
        return self.server.config  # type: ignore[attr-defined]

    @property
    def relay(self) -> PrinterRelay:
        return self.server.relay  # type: ignore[attr-defined]

    @property
    def memory(self) -> JobMemory:
        return self.server.memory  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - nama dari stdlib
        sys.stderr.write(
            f"[{time.strftime('%H:%M:%S')}] {self.address_string()} {fmt % args}\n"
        )

    def respond(self, status: int, body: dict, extra_headers: dict[str, str] | None = None) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")

        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)

        self.end_headers()
        self.wfile.write(payload)

    def drain_body(self, length: int) -> bool:
        """Buang body permintaan yang tidak akan dipakai.

        Dipakai sebelum menolak permintaan: pada HTTP/1.1 koneksi tetap hidup
        setelah balasan, jadi body yang tertinggal akan terbaca sebagai
        permintaan berikutnya. Mengembalikan False kalau body terlalu besar
        untuk dibuang dengan murah — pemanggil menutup koneksinya.
        """
        if length <= 0:
            return True

        if length > self.config.max_bytes:
            return False

        remaining = length

        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 4096))

            if not chunk:
                break

            remaining -= len(chunk)

        return True

    def reject(
        self,
        status: int,
        detail: str,
        length: int | None = None,
        close: bool = False,
    ) -> None:
        """Tolak permintaan tanpa memproses isinya, tetap menjaga koneksi sehat."""
        if length is None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0

        if close or not self.drain_body(length):
            self.close_connection = True
            self.respond(status, {"detail": detail}, {"Connection": "close"})
            return

        self.respond(status, {"detail": detail})

    def token_ok(self) -> bool:
        expected = self.config.token

        if not expected:
            return True

        return self.headers.get(TOKEN_HEADER, "") == expected

    # -- rute ---------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - nama dari stdlib
        path = self.path.split("?", 1)[0]

        if path in (HEALTH_PATH, PRINT_HEALTH_PATH):
            # `status` = bridge hidup (dipakai healthcheck/Docker), sementara
            # `printer_status` = printer menjawab koneksi. Keduanya dipisah
            # supaya printer yang mati tidak membuat bridge dianggap rusak dan
            # supaya POS bisa membedakan "bridge mati" dari "printer mati".
            printer_status, printer_detail = self.relay.probe()
            body = {
                "status": "ok",
                "version": BRIDGE_VERSION,
                "printer": f"{self.config.printer_host}:{self.config.printer_port}",
                "printer_name": self.config.printer_name,
                "printer_status": printer_status,
                "jobs_seen": len(self.memory),
            }

            if printer_detail:
                body["printer_detail"] = printer_detail

            self.respond(200, body)
            return

        if path == PRINT_PATH:
            self.reject(405, "Gunakan POST untuk /print.")
            return

        self.reject(404, "Rute tidak dikenal.")

    def do_POST(self) -> None:  # noqa: N802 - nama dari stdlib
        path = self.path.split("?", 1)[0]

        if path != PRINT_PATH:
            self.reject(404, "Rute tidak dikenal.")
            return

        if not self.token_ok():
            self.reject(401, "Token bridge tidak cocok.")
            return

        job_id = (self.headers.get(JOB_HEADER) or "").strip()

        if not job_id:
            # Tanpa kunci idempotensi, kiriman ulang akan mencetak nota kedua.
            self.reject(
                400,
                f"Header {JOB_HEADER} wajib diisi agar cetak ulang tidak dobel.",
            )
            return

        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            # Ukuran body tidak diketahui, jadi tidak bisa dibuang dengan murah.
            self.reject(411, "Kirim dengan Content-Length.", close=True)
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.reject(400, "Content-Length tidak sah.", close=True)
            return

        if length <= 0:
            self.reject(400, "Isi pekerjaan cetak kosong.")
            return

        if length > self.config.max_bytes:
            self.reject(
                413,
                f"Pekerjaan cetak melebihi {self.config.max_bytes} byte.",
                length=length,
            )
            return

        payload = self.rfile.read(length)

        if len(payload) != length:
            self.reject(400, "Isi pekerjaan cetak terpotong.")
            return

        if not self.memory.remember(job_id):
            # Job yang sama sudah diterima; percobaan ulang client tidak boleh
            # menghasilkan lembar kedua.
            self.respond(
                200,
                {"status": "duplicate", "job_id": job_id, "bytes": len(payload)},
                {JOB_HEADER: job_id, "X-Print-Duplicate": "1"},
            )
            return

        try:
            self.relay.send(payload)
        except PrinterUnreachable as error:
            # Belum tercetak, jadi job ini boleh dicoba lagi oleh POS.
            self.memory.forget(job_id)
            self.respond(
                503,
                {"status": "printer_unreachable", "job_id": job_id, "detail": str(error)},
                {JOB_HEADER: job_id},
            )
            return
        except ValueError as error:
            self.memory.forget(job_id)
            self.respond(400, {"status": "empty", "job_id": job_id, "detail": str(error)})
            return

        self.respond(
            200,
            {"status": "sent", "job_id": job_id, "bytes": len(payload)},
            {JOB_HEADER: job_id},
        )


class PrintBridgeServer:
    """HTTP server bridge; bisa dijalankan sebagai thread (dipakai selftest)."""

    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self.httpd = ThreadingHTTPServer((config.bridge_host, config.bridge_port), PrintBridgeHandler)
        self.httpd.daemon_threads = True

        # Handler membaca atribut ini dari instance server.
        self.httpd.config = config  # type: ignore[attr-defined]
        self.httpd.relay = PrinterRelay(config)  # type: ignore[attr-defined]
        self.httpd.memory = JobMemory(config.job_memory)  # type: ignore[attr-defined]

        self.thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.config.bridge_host in ("", "0.0.0.0") else self.config.bridge_host

        return f"http://{host}:{self.port}"

    def start(self) -> "PrintBridgeServer":
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="print-bridge")
        self.thread.daemon = True
        self.thread.start()

        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

        if self.thread:
            self.thread.join(timeout=5)


def lan_addresses(port: int) -> list[str]:
    """Alamat LAN yang bisa dipakai tablet. Ditampilkan saat bridge menyala
    supaya tidak perlu menebak IP mesin penyaji POS."""
    addresses: list[str] = []
    hostname = socket.gethostname()

    try:
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            address = info[4][0]

            if address not in addresses and not address.startswith("127."):
                addresses.append(address)
    except OSError:
        pass

    return [f"http://{address}:{port}{PRINT_PATH}" for address in addresses]


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------


class FakePrinter:
    """Printer palsu: kumpulkan byte yang masuk pada port sembarang."""

    def __init__(self) -> None:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(8)

        # `accept()` dengan batas waktu: stop() cukup membalik `running`, dan
        # thread bangun sendiri paling lama 0,2 detik. Ini menggantikan
        # kebiasaan `close()` dari thread lain untuk membatalkan accept(),
        # yang perilakunya beda per platform: di Linux syscall `accept()`
        # menahan referensi socket, jadi `close()` tidak menghentikannya dan
        # port tetap menerima koneksi (job dianggap terkirim) sampai ada
        # koneksi baru datang.
        self.socket.settimeout(0.2)

        self.port = self.socket.getsockname()[1]
        self.received: list[bytes] = []
        self.lock = threading.Lock()
        self.running = True
        self.thread = threading.Thread(target=self._serve, name="fake-printer")
        self.thread.daemon = True

    def _serve(self) -> None:
        while self.running:
            try:
                connection, _ = self.socket.accept()
            except TimeoutError:
                continue
            except OSError:
                return

            with connection:
                # Koneksi hasil accept() tidak boleh mewarisi batas waktu
                # socket listen di atas: membaca pekerjaan cetak memang harus
                # menunggu sampai klien menutup koneksi.
                connection.settimeout(None)

                chunks = []

                while True:
                    chunk = connection.recv(4096)

                    if not chunk:
                        break

                    chunks.append(chunk)

                with self.lock:
                    self.received.append(b"".join(chunks))

    def start(self) -> "FakePrinter":
        self.thread.start()

        return self

    def stop(self) -> None:
        if not self.running:
            return

        self.running = False

        # Tunggu thread benar-benar berhenti dulu (paling lama 0,2 detik
        # karena batas waktu accept() di atas), baru tutup socket. Urutan ini
        # tidak bergantung pada platform dan memastikan port sudah mati saat
        # stop() kembali, sehingga koneksi berikutnya ditolak -- syarat agar
        # selftest "printer mati dijawab 503" berlaku di Linux juga.
        self.thread.join(timeout=5)

        try:
            self.socket.close()
        except OSError:
            pass

    @property
    def jobs(self) -> list[bytes]:
        with self.lock:
            return list(self.received)


def post_job(url: str, job_id: str, payload: bytes, token: str = "") -> tuple[int, dict]:
    request = urllib_request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/octet-stream",
            JOB_HEADER: job_id,
            **({TOKEN_HEADER: token} if token else {}),
        },
    )

    try:
        with urllib_request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8") or "{}")


def run_selftest() -> int:
    """Uji relay, idempotensi, dan printer-mati tanpa printer asli."""
    checks: list[str] = []

    def expect(condition: bool, message: str) -> None:
        if not condition:
            raise AssertionError(message)

        checks.append(message)

    def wait_for(condition, message: str, timeout: float = 5.0) -> None:
        """Tunggu sampai kondisi benar, bukan sekali baca.

        Bridge menjawab 200 begitu byte terkirim; printer palsu mencatat job
        setelah koneksi ditutup, di thread lain. Membaca daftar jobs tepat
        setelah respons balapan dengan pencatatan itu, jadi pemeriksaan yang
        positif menunggu.
        """
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            if condition():
                checks.append(message)
                return

            time.sleep(0.01)

        raise AssertionError(message)

    printer = FakePrinter().start()

    config = BridgeConfig(
        printer_host="127.0.0.1",
        printer_port=printer.port,
        printer_name="Uji TM-U220B",
        bridge_host="127.0.0.1",
        bridge_port=0,
        token="",
        max_bytes=4096,
        connect_timeout=2.0,
        job_memory=8,
        health_timeout=2.0,
    )
    bridge = PrintBridgeServer(config).start()

    try:
        payload = bytes([0x1B, 0x40]) + b"NOTA UJI\n" + bytes([0x1D, 0x56, 0x00])

        # 1. Relay apa adanya: printer menerima byte yang sama persis.
        status, body = post_job(f"{bridge.url}{PRINT_PATH}", "job-1", payload)
        expect(status == 200 and body.get("status") == "sent", "job baru dijawab 200 sent")
        wait_for(lambda: printer.jobs == [payload], "byte diteruskan apa adanya ke printer")

        # 2. Kiriman ulang job yang sama tidak mencetak lembar kedua.
        status, body = post_job(f"{bridge.url}{PRINT_PATH}", "job-1", payload)
        expect(status == 200 and body.get("status") == "duplicate", "job kembar dijawab duplicate")

        # Pemeriksaan negatif: beri waktu pencatatan yang tertunda, lalu
        # pastikan jumlahnya tetap satu.
        time.sleep(0.2)
        expect(len(printer.jobs) == 1, "job kembar tidak dikirim lagi ke printer")

        # 3. Job berbeda tetap tercetak.
        status, _ = post_job(f"{bridge.url}{PRINT_PATH}", "job-2", payload + b"2")
        expect(status == 200, "job baru berikutnya dijawab 200")
        wait_for(lambda: len(printer.jobs) == 2, "job baru berikutnya tetap tercetak")

        # 3b. Health check saat printer hidup, termasuk lewat alias
        #     `PRINT_HEALTH_PATH` yang dipakai POS di belakang proxy same-origin.
        for health_path in (HEALTH_PATH, PRINT_HEALTH_PATH):
            with urllib_request.urlopen(f"{bridge.url}{health_path}", timeout=10) as response:
                health = json.loads(response.read().decode("utf-8"))
                expect(
                    response.status == 200 and health.get("printer_status") == "ok",
                    f"{health_path} melaporkan printer tersambung",
                )
                expect(
                    health.get("printer") == f"127.0.0.1:{printer.port}",
                    f"{health_path} menyebut alamat printer yang benar",
                )
                expect(
                    health.get("printer_name") == "Uji TM-U220B",
                    f"{health_path} ikut melaporkan label printer",
                )

        # Probe status membuka koneksi lalu menutupnya; printer palsu mencatat
        # koneksi itu, jadi yang diperiksa adalah isinya benar-benar kosong.
        wait_for(
            lambda: printer.jobs[2:] == [b"", b""],
            "pemeriksaan status hanya membuka koneksi, tanpa mengirim byte",
        )

        # 4. Tanpa X-Print-Job ditolak, karena cetak ulang jadi tidak aman.
        request = urllib_request.Request(
            f"{bridge.url}{PRINT_PATH}",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/octet-stream"},
        )
        try:
            urllib_request.urlopen(request, timeout=10)
            raise AssertionError("job tanpa header seharusnya ditolak")
        except urllib_error.HTTPError as error:
            expect(error.code == 400, "job tanpa X-Print-Job ditolak 400")

        # 5. Batas ukuran.
        status, _ = post_job(f"{bridge.url}{PRINT_PATH}", "job-3", b"x" * 5000)
        expect(status == 413, "job melebihi batas dijawab 413")

        # 6. Satu koneksi keep-alive: permintaan yang ditolak tidak boleh
        #    meninggalkan body tertinggal yang terbaca sebagai permintaan berikutnya.
        connection = http.client.HTTPConnection("127.0.0.1", bridge.port, timeout=10)
        connection.request(
            "POST", PRINT_PATH, body=b"x" * 64, headers={"Content-Type": "application/octet-stream"}
        )
        rejected = connection.getresponse()
        rejected.read()
        expect(rejected.status == 400, "permintaan tanpa header ditolak pada koneksi keep-alive")

        before = len(printer.jobs)
        connection.request(
            "POST",
            PRINT_PATH,
            body=payload,
            headers={"Content-Type": "application/octet-stream", JOB_HEADER: "job-keepalive"},
        )
        followed = connection.getresponse()
        followed.read()
        expect(followed.status == 200, "koneksi yang sama masih bisa dipakai setelah penolakan")
        wait_for(
            lambda: len(printer.jobs) == before + 1,
            "pekerjaan pada koneksi yang sama tetap tercetak",
        )
        connection.close()

        # 7. Printer mati -> 503, dan job itu TIDAK diingat sebagai sudah cetak.
        printer.stop()

        # Kontrak yang menjamin langkah berikutnya benar di semua platform:
        # setelah stop() port printer harus benar-benar menolak koneksi. Di
        # Linux `close()` saja tidak membatalkan `accept()` yang sedang
        # blokir, jadi port tetap menerima koneksi dan job dianggap terkirim
        # (200, bukan 503) -- inilah yang dulu membuat selftest merah hanya
        # di CI Linux.
        # Batas waktunya pendek: di Linux port mati langsung menolak
        # (ConnectionRefused), sedangkan di Windows koneksi ke port yang baru
        # ditutup bisa menggantung sampai batas waktu alih-alih ditolak.
        try:
            with socket.create_connection(("127.0.0.1", printer.port), timeout=0.5):
                port_closed = False
        except OSError:
            port_closed = True

        expect(port_closed, "port printer tertutup setelah stop")

        time.sleep(0.1)
        seen_before = len(bridge.httpd.memory)
        status, body = post_job(f"{bridge.url}{PRINT_PATH}", "job-4", payload)
        expect(status == 503 and body.get("status") == "printer_unreachable", "printer mati dijawab 503")
        expect(len(bridge.httpd.memory) == seen_before, "job gagal tidak dikunci sebagai sudah dicetak")

        # 8. Health check saat printer mati: bridge tetap sehat, printer tidak.
        with urllib_request.urlopen(f"{bridge.url}{HEALTH_PATH}", timeout=10) as response:
            health = json.loads(response.read().decode("utf-8"))
            expect(
                response.status == 200 and health.get("status") == "ok",
                "health check menjawab bridge hidup walau printer mati",
            )
            expect(
                health.get("printer_status") == "unreachable",
                "health check melaporkan printer tidak terjangkau saat printer mati",
            )
            expect(
                bool(health.get("printer_detail")),
                "alasan printer tidak terjangkau ikut dilaporkan",
            )
    finally:
        bridge.stop()
        printer.stop()

    print(f"Selftest print bridge: {len(checks)} pemeriksaan lolos.")
    for message in checks:
        print(f"  ok - {message}")

    return 0


def environment_with_overrides(arguments: argparse.Namespace) -> dict[str, str]:
    """Gabungkan variabel lingkungan dengan opsi baris perintah (opsi menang).

    Bukan sekadar kenyamanan: di Windows `set VAR=nilai` hanya berlaku di cmd dan
    `$env:VAR="nilai"` hanya di PowerShell, jadi baris yang terlihat benar bisa
    berhenti dengan "PRINTER_HOST belum diisi". Opsi baris perintah sama di semua
    shell, sehingga menjadi bentuk yang dianjurkan saat pemasangan di toko.
    """
    env = dict(os.environ)

    if arguments.printer_host:
        env["PRINTER_HOST"] = arguments.printer_host

    if arguments.printer_port:
        env["PRINTER_PORT"] = str(arguments.printer_port)

    if arguments.printer_name:
        env["PRINTER_NAME"] = arguments.printer_name

    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="Uji relay dan idempotensi tanpa printer asli.",
    )
    parser.add_argument(
        "--printer-host",
        default="",
        help="IP printer; sama dengan PRINTER_HOST, tapi aman di shell mana pun.",
    )
    parser.add_argument(
        "--printer-port",
        type=int,
        default=0,
        help="Port printer; sama dengan PRINTER_PORT (bawaan 9100).",
    )
    parser.add_argument(
        "--printer-name",
        default="",
        help='Label printer di layar kasir, mis. "Kasir 1"; sama dengan PRINTER_NAME.',
    )
    parser.add_argument(
        "--print-design",
        metavar="PDF",
        default=str(Path(__file__).resolve().parent.parent / "docs" / "desain_nota_75x65.pdf"),
        help="Hanya dicatat di banner: acuan tata letak nota.",
    )
    args = parser.parse_args(argv)

    if args.selftest:
        return run_selftest()

    config = BridgeConfig.from_environ(environment_with_overrides(args))
    server = PrintBridgeServer(config).start()

    print(f"Print bridge {BRIDGE_VERSION} menyala di {server.url}")
    print(f"Printer tujuan      : {config.printer_host}:{config.printer_port}")
    print(f"Acuan tata letak    : {args.print_design}")
    print(f"Token bridge        : {'aktif' if config.token else 'tidak dipakai'}")
    print(f"Status printer      : {server.url}{PRINT_HEALTH_PATH}")

    for address in lan_addresses(server.port):
        print(f"Alamat untuk tablet : {address}")

    print("Tekan Ctrl+C untuk berhenti.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nMenghentikan bridge...")
    finally:
        server.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
