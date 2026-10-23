"""Inti aplikasi Penghubung Cetak (tanpa jendela, supaya bisa diuji di mana saja).

Aplikasi ini hanya MEMBUNGKUS `POS/print-bridge/bridge.py` milik POS, tidak
mengubahnya. Yang ditambahkan di sini:

- cari printer sendiri di jaringan (port 9100) dan mengenalinya lagi lewat
  alamat MAC kalau IP printer berubah (router membagi IP ulang);
- izin CORS + jawaban OPTIONS, supaya tablet juga bisa memanggil laptop ini
  langsung (VITE_PRINT_BRIDGE_URL) - jalur lewat server POS (`/print`) tidak
  membutuhkannya tapi juga tidak terganggu;
- hitungan nota tercetak dan kejadian terakhir untuk ditampilkan di layar;
- halaman uji, simpan pengaturan, dan alat bantu Windows (nyala otomatis,
  firewall, cegah laptop tidur).
"""
from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import bridge  # POS/print-bridge/bridge.py, dipakai apa adanya

NAMA_APLIKASI = "Penghubung Cetak"
PORT_PRINTER = 9100
PORT_BRIDGE = 9101
JARINGAN_TAILSCALE = ipaddress.ip_network("100.64.0.0/10")
WINDOWS = sys.platform.startswith("win")
TANPA_JENDELA = 0x08000000 if WINDOWS else 0  # CREATE_NO_WINDOW: tidak ada kedip cmd


# ---------------------------------------------------------------------------
# Pengaturan
# ---------------------------------------------------------------------------


def folder_data() -> Path:
    dasar = os.environ.get("APPDATA") or str(Path.home() / ".config")
    folder = Path(dasar) / "PenghubungCetak"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


@dataclass
class Pengaturan:
    printer_ip: str = ""
    printer_mac: str = ""
    printer_nama: str = "Kasir 1"
    port_bridge: int = PORT_BRIDGE
    token: str = ""
    nyala_otomatis: bool = True
    sudah_diatur: bool = False  # autostart sudah dipasang sekali

    @classmethod
    def baca(cls, berkas: Path | None = None) -> "Pengaturan":
        berkas = berkas or folder_data() / "pengaturan.json"
        try:
            data = json.loads(berkas.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        dikenal = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**dikenal)

    def simpan(self, berkas: Path | None = None) -> None:
        berkas = berkas or folder_data() / "pengaturan.json"
        sementara = berkas.with_suffix(".tmp")
        sementara.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(sementara, berkas)


def catat(pesan: str) -> None:
    """Log kecil di folder data; dipotong kalau lebih dari ~200 KB."""
    try:
        berkas = folder_data() / "catatan.txt"
        if berkas.exists() and berkas.stat().st_size > 200_000:
            isi = berkas.read_text(encoding="utf-8", errors="replace")[-100_000:]
            berkas.write_text(isi, encoding="utf-8")
        with berkas.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {pesan}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Alamat laptop ini
# ---------------------------------------------------------------------------


def alamat_lokal() -> list[str]:
    """Semua IPv4 laptop ini (LAN dan Tailscale), tanpa 127.x dan 169.254.x."""
    hasil: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            hasil.append(info[4][0])
    except OSError:
        pass
    # Alamat yang dipakai keluar ke internet/router (tanpa benar-benar mengirim).
    for tujuan in ("192.168.1.1", "10.255.255.255", "8.8.8.8"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((tujuan, 80))
                hasil.append(s.getsockname()[0])
        except OSError:
            pass
    rapi = []
    for a in hasil:
        ip = ipaddress.ip_address(a)
        if ip.is_loopback or ip.is_link_local or a in rapi:
            continue
        rapi.append(a)
    return rapi


def alamat_tailscale(alamat: list[str] | None = None) -> str:
    for a in alamat if alamat is not None else alamat_lokal():
        if ipaddress.ip_address(a) in JARINGAN_TAILSCALE:
            return a
    return ""


def alamat_lan(alamat: list[str] | None = None) -> list[str]:
    return [a for a in (alamat if alamat is not None else alamat_lokal())
            if ipaddress.ip_address(a) not in JARINGAN_TAILSCALE and ipaddress.ip_address(a).is_private]


# ---------------------------------------------------------------------------
# Mencari printer
# ---------------------------------------------------------------------------


def port_terbuka(ip: str, port: int = PORT_PRINTER, batas: float = 0.5) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=batas):
            return True
    except OSError:
        return False


def kandidat_alamat(lan: list[str]) -> list[str]:
    """Semua alamat /24 di sekitar IP LAN laptop ini (tanpa laptopnya sendiri)."""
    hasil: list[str] = []
    for a in lan:
        jaringan = ipaddress.ip_network(f"{a}/24", strict=False)
        for host in jaringan.hosts():
            h = str(host)
            if h != a and h not in hasil:
                hasil.append(h)
    return hasil


def pindai(alamat: list[str], port: int = PORT_PRINTER, batas: float = 0.4,
           pekerja: int = 96) -> list[str]:
    """Alamat yang menerima koneksi di port printer. Urut sesuai IP."""
    if not alamat:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=pekerja) as ex:
        hasil = list(ex.map(lambda ip: (ip, port_terbuka(ip, port, batas)), alamat))
    return [ip for ip, ok in hasil if ok]


_POLA_MAC = re.compile(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})")


def tabel_arp(teks: str | None = None) -> dict[str, str]:
    """{ip: mac} dari `arp -a`. MAC disamakan ke bentuk aa-bb-cc-dd-ee-ff."""
    if teks is None:
        try:
            teks = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=5,
                                  creationflags=TANPA_JENDELA).stdout
        except (OSError, subprocess.SubprocessError):
            return {}
    hasil = {}
    for ip, mac in _POLA_MAC.findall(teks):
        mac = mac.lower().replace(":", "-")
        if mac not in ("ff-ff-ff-ff-ff-ff", "00-00-00-00-00-00"):
            hasil[ip] = mac
    return hasil


def kenali_epson(ip: str, batas: float = 1.0) -> str:
    """Nama model kalau halaman web printer menyebut EPSON/TM-; "" kalau tidak tahu.

    Hanya petunjuk untuk memilih di antara beberapa printer. Banyak antarmuka
    jaringan TM punya halaman web di port 80; kalau tidak ada, tidak apa-apa.
    """
    try:
        with socket.create_connection((ip, 80), timeout=batas) as s:
            s.settimeout(batas)
            s.sendall(f"GET / HTTP/1.0\r\nHost: {ip}\r\n\r\n".encode())
            data = b""
            while len(data) < 20000:
                potong = s.recv(4096)
                if not potong:
                    break
                data += potong
    except OSError:
        return ""
    teks = data.decode("latin-1", errors="replace")
    model = re.search(r"TM-[A-Z0-9]+[A-Z0-9-]*", teks)
    if model:
        return model.group(0)
    return "EPSON" if "epson" in teks.lower() else ""


@dataclass
class HasilCari:
    ip: str = ""
    mac: str = ""
    model: str = ""
    semua: list[dict] = field(default_factory=list)  # [{ip, mac, model}]
    pesan: str = ""


def cari_printer(peng: Pengaturan, *, lan: list[str] | None = None,
                 pemindai=pindai, arp=tabel_arp, epson=kenali_epson,
                 cek=port_terbuka) -> HasilCari:
    """Tentukan printer kasir.

    Urutan: (1) IP tersimpan masih menjawab -> pakai; (2) printer dengan MAC
    tersimpan ditemukan di IP lain -> pakai IP barunya; (3) belum pernah ada
    printer: kalau hanya satu perangkat port 9100, atau hanya satu yang jelas
    Epson, pakai itu. Selain itu pemakai memilih sendiri (dengan halaman uji),
    karena printer kantor biasa juga membuka port 9100 dan mengirim byte nota
    ke sana menghasilkan kertas sampah.
    """
    if peng.printer_ip and cek(peng.printer_ip):
        mac = arp().get(peng.printer_ip, "") or peng.printer_mac
        # IP lama dipakai perangkat lain? Kalau MAC-nya beda, jangan percaya.
        if not peng.printer_mac or not mac or mac == peng.printer_mac:
            return HasilCari(ip=peng.printer_ip, mac=mac, pesan="tersimpan")

    lan = alamat_lan() if lan is None else lan
    if not lan:
        return HasilCari(pesan="Laptop ini belum tersambung ke jaringan toko (Wi-Fi/kabel).")
    ditemukan = pemindai(kandidat_alamat(lan))
    tabel = arp()
    semua = [{"ip": ip, "mac": tabel.get(ip, ""), "model": ""} for ip in ditemukan]

    if peng.printer_mac:
        for p in semua:
            if p["mac"] == peng.printer_mac:
                return HasilCari(ip=p["ip"], mac=p["mac"], semua=semua, pesan="pindah IP")
        if peng.printer_ip:
            return HasilCari(semua=semua, pesan="Printer kasir tidak ditemukan. Cek printer menyala dan kabel LAN-nya.")

    for p in semua:
        p["model"] = epson(p["ip"])
    if len(semua) == 1:
        p = semua[0]
        return HasilCari(ip=p["ip"], mac=p["mac"], model=p["model"], semua=semua, pesan="satu-satunya")
    epsons = [p for p in semua if p["model"]]
    if len(epsons) == 1:
        p = epsons[0]
        return HasilCari(ip=p["ip"], mac=p["mac"], model=p["model"], semua=semua, pesan="satu-satunya Epson")
    if not semua:
        return HasilCari(semua=semua, pesan="Tidak ada printer di jaringan. Cek printer menyala dan kabel LAN-nya.")
    return HasilCari(semua=semua, pesan="pilih")


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


class Statistik:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.hari = time.strftime("%Y-%m-%d")
        self.tercetak = 0
        self.gagal = 0
        self.terakhir = ""      # teks kejadian terakhir
        self.waktu_terakhir = 0.0

    def tambah(self, status: str) -> None:
        with self.lock:
            hari = time.strftime("%Y-%m-%d")
            if hari != self.hari:
                self.hari, self.tercetak, self.gagal = hari, 0, 0
            if status == "sent":
                self.tercetak += 1
                self.terakhir = "Nota terkirim ke printer"
            elif status == "printer_unreachable":
                self.gagal += 1
                self.terakhir = "Nota GAGAL: printer tidak menjawab (kasir akan mencoba lagi)"
            elif status == "duplicate":
                self.terakhir = "Nota yang sama dikirim ulang (tidak dicetak dua kali)"
            else:
                return
            self.waktu_terakhir = time.time()


class HandlerToko(bridge.PrintBridgeHandler):
    """Handler bridge + CORS + hitungan. Logika cetaknya tetap milik bridge.py."""

    def end_headers(self) -> None:  # noqa: D401
        asal = self.headers.get("Origin")
        if asal:
            self.send_header("Access-Control-Allow-Origin", asal)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Expose-Headers", "X-Print-Job, X-Print-Duplicate")
        super().end_headers()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         self.headers.get("Access-Control-Request-Headers")
                         or "Content-Type, X-Print-Job, X-Print-Token")
        self.send_header("Access-Control-Max-Age", "600")
        if self.headers.get("Access-Control-Request-Private-Network"):
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def respond(self, status, body, extra_headers=None):
        if self.command == "POST" and isinstance(body, dict):
            self.server.statistik.tambah(body.get("status", ""))  # type: ignore[attr-defined]
        super().respond(status, body, extra_headers)

    def log_message(self, fmt, *args):
        if self.command != "GET":  # cek status tiap 30 detik tidak perlu dicatat
            catat(f"{self.address_string()} {fmt % args}")


def buat_config(peng: Pengaturan, ip: str, host: str = "0.0.0.0") -> bridge.BridgeConfig:
    return bridge.BridgeConfig(
        printer_host=ip or "192.0.2.1", printer_port=PORT_PRINTER,
        printer_name=peng.printer_nama or "Kasir 1",
        bridge_host=host, bridge_port=peng.port_bridge, token=peng.token,
        max_bytes=65536, connect_timeout=5.0, job_memory=500, health_timeout=2.0)


class Penghubung:
    """Bridge yang jalan di latar; printer tujuannya bisa diganti tanpa restart."""

    def __init__(self, peng: Pengaturan, host: str = "0.0.0.0") -> None:
        self.peng = peng
        self.config = buat_config(peng, peng.printer_ip, host)
        self.httpd = bridge.ThreadingHTTPServer((host, peng.port_bridge), HandlerToko)
        self.httpd.daemon_threads = True
        self.httpd.config = self.config
        self.httpd.relay = bridge.PrinterRelay(self.config)
        self.httpd.memory = bridge.JobMemory(500)
        self.httpd.statistik = Statistik()
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="penghubung", daemon=True)

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    @property
    def statistik(self) -> Statistik:
        return self.httpd.statistik

    def mulai(self) -> "Penghubung":
        self.thread.start()
        return self

    def berhenti(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def ganti_printer(self, ip: str) -> None:
        self.config = buat_config(self.peng, ip, self.config.bridge_host)
        self.httpd.config = self.config
        self.httpd.relay = bridge.PrinterRelay(self.config)

    def cek_printer(self) -> bool:
        if not self.peng.printer_ip:
            return False
        return self.httpd.relay.probe()[0] == "ok"


def port_bridge_dipakai(port: int = PORT_BRIDGE) -> bool:
    """Ada penghubung cetak lain yang sudah jalan di laptop ini?"""
    return port_terbuka("127.0.0.1", port, 0.5)


# ---------------------------------------------------------------------------
# Halaman uji
# ---------------------------------------------------------------------------


def halaman_uji(nama: str, ip: str) -> bytes:
    """Satu halaman pendek, 40 kolom Font B, sama gaya dengan uji_cetak.py."""
    baris = [
        "", "UJI CETAK", "", "Penghubung cetak OK.",
        f"Printer : {nama}"[:40], f"Alamat  : {ip}"[:40],
        f"Waktu   : {time.strftime('%d-%m-%Y %H:%M')}", "",
        "Kalau kertas ini keluar dari printer", "kasir, berarti printernya benar.", "",
    ]
    isi = bytearray(b"\x1b\x40\x1b\x4d\x01\x1b\x61\x01")
    for b in baris:
        isi += b.encode("ascii", "replace") + b"\n"
    isi += b"\x1b\x61\x00\x1b\x64\x04\x1d\x56\x00"
    return bytes(isi)


def cetak_uji(ip: str, nama: str) -> str:
    """"" kalau terkirim, selain itu alasannya."""
    try:
        cfg = buat_config(Pengaturan(printer_nama=nama), ip)
        bridge.PrinterRelay(cfg).send(halaman_uji(nama, ip))
        return ""
    except bridge.PrinterUnreachable as e:
        return str(e)


# ---------------------------------------------------------------------------
# Windows: nyala otomatis, firewall, jangan tidur
# ---------------------------------------------------------------------------

KUNCI_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"


def perintah_diri(*tambahan: str) -> str:
    if getattr(sys, "frozen", False):
        dasar = f'"{sys.executable}"'
    else:
        pyw = Path(sys.executable).with_name("pythonw.exe")
        dasar = f'"{pyw if pyw.exists() else sys.executable}" "{Path(__file__).with_name("penghubung_cetak.py")}"'
    return " ".join([dasar, *tambahan])


def atur_nyala_otomatis(nyala: bool) -> str:
    """"" kalau berhasil, selain itu alasannya."""
    if not WINDOWS:
        return "Hanya untuk Windows."
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KUNCI_RUN, 0, winreg.KEY_SET_VALUE) as k:
            if nyala:
                winreg.SetValueEx(k, NAMA_APLIKASI, 0, winreg.REG_SZ, perintah_diri("--diam"))
            else:
                try:
                    winreg.DeleteValue(k, NAMA_APLIKASI)
                except FileNotFoundError:
                    pass
        return ""
    except OSError as e:
        return str(e)


def izinkan_firewall(port: int = PORT_BRIDGE) -> str:
    """Minta izin admin (jendela UAC) untuk membuka port bridge hanya dari LAN + Tailscale."""
    if not WINDOWS:
        return "Hanya untuk Windows."
    import ctypes
    nama = f"{NAMA_APLIKASI} {port}"
    arg = (f'/c netsh advfirewall firewall delete rule name="{nama}" & '
           f'netsh advfirewall firewall add rule name="{nama}" dir=in action=allow protocol=TCP '
           f'localport={port} profile=any remoteip=LocalSubnet,100.64.0.0/10')
    hasil = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", arg, None, 0)
    return "" if hasil > 32 else "Izin admin ditolak atau gagal."


def jangan_tidur(aktif: bool = True) -> None:
    """Laptop tidak tidur sendiri selama aplikasi ini menyala (layar boleh mati)."""
    if not WINDOWS:
        return
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if aktif else 0))
