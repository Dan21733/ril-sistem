"""Penghubung Cetak - jendela sederhana untuk laptop toko.

Klik dua kali, selesai. Aplikasi ini:
- mencari printer kasir sendiri dan mengingatnya (juga kalau IP-nya berubah);
- menjalankan penghubung cetak POS (bridge.py) di port 9101;
- menampilkan satu warna: HIJAU siap, KUNING sedang mencari, MERAH ada masalah;
- nyala sendiri tiap laptop dinyalakan, dan menjaga laptop tidak tidur.

Opsi: --diam  (mulai diperkecil; dipakai saat nyala otomatis)
"""
from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import inti

BIRU = "#1f6fd1"
WARNA = {
    "hijau": ("#1e9e5a", "#ffffff"),
    "kuning": ("#f2b600", "#1d1d1d"),
    "merah": ("#d93a3a", "#ffffff"),
}
HURUF = "Segoe UI"


class Aplikasi:
    def __init__(self, akar: tk.Tk, diam: bool) -> None:
        self.akar = akar
        self.peng = inti.Pengaturan.baca()
        self.kunci = threading.Lock()
        self.keadaan = ("kuning", "MENCARI PRINTER...", "Tunggu sebentar.")
        self.sedang_cari = False
        self.gagal_berturut = 0
        self.cari_terakhir = 0.0
        self.pilihan: list[dict] | None = None
        self.menunggu_pilihan = False
        self.penghubung: inti.Penghubung | None = None

        akar.title(inti.NAMA_APLIKASI)
        akar.geometry("480x470")
        akar.minsize(460, 430)
        akar.configure(bg="#f4f6fa")
        akar.protocol("WM_DELETE_WINDOW", self.tutup_jendela)
        ikon = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "ikon.ico"
        try:
            akar.iconbitmap(default=str(ikon))
        except tk.TclError:
            pass  # bukan Windows / ikon tidak ada: tidak penting
        self.bangun()

        try:
            self.penghubung = inti.Penghubung(self.peng).mulai()
        except OSError as e:
            inti.catat(f"gagal menyalakan: {e}")
            self.atur("merah", "TIDAK BISA MENYALA",
                      f"Port {self.peng.port_bridge} dipakai program lain. Mulai ulang laptop.")
            return
        inti.catat(f"menyala, printer tersimpan {self.peng.printer_ip or '-'}")
        inti.jangan_tidur(True)

        if not self.peng.sudah_diatur:
            if self.peng.nyala_otomatis:
                alasan = inti.atur_nyala_otomatis(True)
                if alasan:
                    inti.catat(f"nyala otomatis gagal: {alasan}")
            self.peng.sudah_diatur = True
            self.peng.simpan()
        if diam:
            akar.iconify()

        threading.Thread(target=self.jaga, daemon=True).start()
        self.segarkan()

    # -- tampilan ---------------------------------------------------------

    def bangun(self) -> None:
        a = self.akar
        self.panel = tk.Frame(a, bg=WARNA["kuning"][0], padx=16, pady=18)
        self.panel.pack(fill="x")
        self.judul = tk.Label(self.panel, font=(HURUF, 20, "bold"), bg=WARNA["kuning"][0])
        self.judul.pack(anchor="w")
        self.ket = tk.Label(self.panel, font=(HURUF, 11), justify="left", wraplength=440, bg=WARNA["kuning"][0])
        self.ket.pack(anchor="w", pady=(4, 0))

        isi = tk.Frame(a, bg="#f4f6fa", padx=16, pady=12)
        isi.pack(fill="both", expand=True)
        self.hitung = tk.Label(isi, font=(HURUF, 13), bg="#f4f6fa", anchor="w")
        self.hitung.pack(fill="x")
        self.terakhir = tk.Label(isi, font=(HURUF, 10), fg="#555", bg="#f4f6fa", anchor="w",
                                 justify="left", wraplength=440)
        self.terakhir.pack(fill="x", pady=(2, 10))

        self.tombol_uji = tk.Button(isi, text="Cetak Halaman Uji", font=(HURUF, 13, "bold"),
                                    bg=BIRU, fg="white", activebackground="#185aab", activeforeground="white",
                                    relief="flat", pady=10, cursor="hand2", command=self.uji)
        self.tombol_uji.pack(fill="x")

        self.daftar = tk.Frame(isi, bg="#f4f6fa")
        self.daftar.pack(fill="x", pady=(8, 0))

        self.tombol_atur = tk.Button(isi, text="Pengaturan  ▸", font=(HURUF, 10), relief="flat",
                                     bg="#f4f6fa", fg=BIRU, cursor="hand2", command=self.buka_tutup_atur)
        self.tombol_atur.pack(anchor="w", pady=(10, 0))
        self.atur_frame = tk.Frame(isi, bg="#ffffff", padx=10, pady=8,
                                   highlightthickness=1, highlightbackground="#dde3ec")
        self.bangun_atur(self.atur_frame)

    def bangun_atur(self, f: tk.Frame) -> None:
        def baris(teks):
            r = tk.Frame(f, bg="#ffffff")
            r.pack(fill="x", pady=2)
            tk.Label(r, text=teks, width=13, anchor="w", bg="#ffffff", font=(HURUF, 9)).pack(side="left")
            return r

        r = baris("IP printer")
        self.isian_ip = tk.Entry(r, width=15)
        self.isian_ip.pack(side="left")
        tk.Button(r, text="Pakai", command=self.pakai_ip_manual).pack(side="left", padx=4)
        tk.Button(r, text="Cari ulang", command=lambda: self.mulai_cari(paksa=True, lupakan=True)).pack(side="left")

        r = baris("Nama printer")
        self.isian_nama = tk.Entry(r, width=15)
        self.isian_nama.insert(0, self.peng.printer_nama)
        self.isian_nama.pack(side="left")
        tk.Button(r, text="Simpan", command=self.simpan_nama).pack(side="left", padx=4)

        r = baris("Untuk server POS")
        self.alamat = tk.Entry(r, width=24, relief="flat", readonlybackground="#ffffff")
        self.alamat.pack(side="left")
        tk.Button(r, text="Salin", command=self.salin_alamat).pack(side="left", padx=4)

        self.var_otomatis = tk.BooleanVar(value=self.peng.nyala_otomatis)
        tk.Checkbutton(f, text="Nyala sendiri saat laptop dihidupkan", variable=self.var_otomatis,
                       bg="#ffffff", command=self.ubah_otomatis).pack(anchor="w", pady=(4, 0))
        r = tk.Frame(f, bg="#ffffff")
        r.pack(fill="x", pady=(6, 0))
        tk.Button(r, text="Izinkan di firewall", command=self.firewall).pack(side="left")
        tk.Button(r, text="Matikan penghubung", fg="#b00", command=self.matikan).pack(side="right")

    def atur(self, warna: str, judul: str, ket: str) -> None:
        with self.kunci:
            self.keadaan = (warna, judul, ket)

    def segarkan(self) -> None:
        warna, judul, ket = self.keadaan
        bg, fg = WARNA[warna]
        self.panel.configure(bg=bg)
        for w in (self.judul, self.ket):
            w.configure(bg=bg, fg=fg)
        self.judul.configure(text=judul)
        self.ket.configure(text=ket)
        self.akar.title(f"{inti.NAMA_APLIKASI} - {judul.title()}")

        if self.penghubung:
            st = self.penghubung.statistik
            self.hitung.configure(text=f"Hari ini: {st.tercetak} nota tercetak"
                                       + (f", {st.gagal} gagal" if st.gagal else ""))
            if st.waktu_terakhir:
                self.terakhir.configure(text=f"{time.strftime('%H:%M', time.localtime(st.waktu_terakhir))}  "
                                             f"{st.terakhir}")
            else:
                self.terakhir.configure(text="Belum ada nota masuk sejak aplikasi dinyalakan.")

        ts = inti.alamat_tailscale()
        lan = inti.alamat_lan()
        alamat = f"http://{ts or (lan[0] if lan else '?')}:{self.peng.port_bridge}"
        self.alamat.configure(state="normal")
        self.alamat.delete(0, "end")
        self.alamat.insert(0, alamat)
        self.alamat.configure(state="readonly")
        if self.isian_ip.focus_get() is not self.isian_ip:
            self.isian_ip.delete(0, "end")
            self.isian_ip.insert(0, self.peng.printer_ip)

        if self.pilihan is not None:
            self.tampilkan_pilihan(self.pilihan)
            self.pilihan = None
        self.akar.after(1000, self.segarkan)

    def tampilkan_pilihan(self, semua: list[dict]) -> None:
        for w in self.daftar.winfo_children():
            w.destroy()
        if not semua:
            return
        tk.Label(self.daftar, text="Ketemu beberapa printer. Cetak uji satu per satu, lalu pilih "
                                   "yang kertasnya keluar di printer kasir:",
                 bg="#f4f6fa", justify="left", wraplength=440, font=(HURUF, 10)).pack(anchor="w")
        for p in semua:
            r = tk.Frame(self.daftar, bg="#f4f6fa")
            r.pack(fill="x", pady=2)
            teks = p["ip"] + (f"  ({p['model']})" if p.get("model") else "")
            tk.Label(r, text=teks, bg="#f4f6fa", font=(HURUF, 10, "bold")).pack(side="left")
            tk.Button(r, text="Pakai ini", bg=BIRU, fg="white", relief="flat",
                      command=lambda p=p: self.pakai(p["ip"], p.get("mac", ""))).pack(side="right")
            tk.Button(r, text="Cetak uji", relief="flat",
                      command=lambda p=p: self.uji(p["ip"])).pack(side="right", padx=4)

    # -- kerja latar ------------------------------------------------------

    def jaga(self) -> None:
        """Tiap 5 detik cek printer. Gagal 3x berturut -> cari lagi (paling sering tiap 60 detik)."""
        self.mulai_cari(paksa=True)
        while True:
            time.sleep(5)
            if self.sedang_cari or not self.penghubung:
                continue
            if not self.peng.printer_ip and self.menunggu_pilihan:
                continue  # pemakai sedang memilih; jangan ganggu daftarnya
            if self.peng.printer_ip and self.penghubung.cek_printer():
                if self.gagal_berturut:
                    inti.catat(f"printer {self.peng.printer_ip} kembali menjawab")
                self.gagal_berturut = 0
                self.atur("hijau", "SIAP MENCETAK",
                          f"Printer {self.peng.printer_nama} ({self.peng.printer_ip}) tersambung. "
                          "Biarkan aplikasi ini menyala.")
                continue
            if self.peng.printer_ip:
                self.gagal_berturut += 1
                self.atur("merah", "PRINTER TIDAK MENJAWAB",
                          "Cek printer menyala, kertas terpasang, dan kabel LAN-nya tercolok. "
                          "Aplikasi mencari lagi sendiri.")
            if self.gagal_berturut >= 3 or not self.peng.printer_ip:
                self.mulai_cari()

    def mulai_cari(self, paksa: bool = False, lupakan: bool = False) -> None:
        if self.sedang_cari or (not paksa and time.time() - self.cari_terakhir < 60):
            return
        if lupakan:
            self.menunggu_pilihan = False
            self.peng.printer_ip, self.peng.printer_mac = "", ""
            self.peng.simpan()
        self.sedang_cari = True
        threading.Thread(target=self._cari, daemon=True).start()

    def _cari(self) -> None:
        try:
            if not self.peng.printer_ip:
                self.atur("kuning", "MENCARI PRINTER...", "Memeriksa jaringan toko, kira-kira 5 detik.")
            h = inti.cari_printer(self.peng)
            self.cari_terakhir = time.time()
            if h.ip:
                if h.ip != self.peng.printer_ip:
                    inti.catat(f"printer dipakai: {h.ip} ({h.pesan}) mac={h.mac or '-'}")
                self.pakai(h.ip, h.mac, dari_latar=True)
                self.pilihan = []
            elif h.pesan == "pilih":
                self.atur("kuning", "PILIH PRINTER KASIR", "Ada lebih dari satu printer di jaringan.")
                self.pilihan = h.semua
                self.menunggu_pilihan = True
            else:
                self.atur("merah", "PRINTER TIDAK DITEMUKAN", h.pesan)
        finally:
            self.sedang_cari = False

    # -- aksi -------------------------------------------------------------

    def pakai(self, ip: str, mac: str = "", dari_latar: bool = False) -> None:
        if not mac:
            mac = inti.tabel_arp().get(ip, "")
        self.peng.printer_ip, self.peng.printer_mac = ip, mac
        self.menunggu_pilihan = False
        self.peng.simpan()
        if self.penghubung:
            self.penghubung.ganti_printer(ip)
        self.gagal_berturut = 0
        self.atur("hijau", "SIAP MENCETAK",
                  f"Printer {self.peng.printer_nama} ({ip}) tersambung. Biarkan aplikasi ini menyala.")
        if not dari_latar:
            self.tampilkan_pilihan([])

    def uji(self, ip: str | None = None) -> None:
        ip = ip or self.peng.printer_ip
        if not ip:
            messagebox.showinfo(inti.NAMA_APLIKASI, "Printer belum ditemukan. Tunggu sampai layar hijau.")
            return
        self.tombol_uji.configure(state="disabled", text="Mengirim...")

        def kerja():
            alasan = inti.cetak_uji(ip, self.peng.printer_nama)
            self.akar.after(0, lambda: selesai(alasan))

        def selesai(alasan):
            self.tombol_uji.configure(state="normal", text="Cetak Halaman Uji")
            if alasan:
                messagebox.showerror(inti.NAMA_APLIKASI, f"Halaman uji tidak terkirim.\n\n{alasan}")
            else:
                messagebox.showinfo(inti.NAMA_APLIKASI,
                                    f"Halaman uji dikirim ke {ip}.\nCek kertasnya keluar dari printer kasir.")

        threading.Thread(target=kerja, daemon=True).start()

    def pakai_ip_manual(self) -> None:
        ip = self.isian_ip.get().strip()
        if not inti.port_terbuka(ip):
            messagebox.showerror(inti.NAMA_APLIKASI, f"{ip} tidak menjawab di port {inti.PORT_PRINTER}.")
            return
        self.pakai(ip)

    def simpan_nama(self) -> None:
        self.peng.printer_nama = self.isian_nama.get().strip() or "Kasir 1"
        self.peng.simpan()
        if self.penghubung and self.peng.printer_ip:
            self.penghubung.ganti_printer(self.peng.printer_ip)

    def salin_alamat(self) -> None:
        self.akar.clipboard_clear()
        self.akar.clipboard_append(self.alamat.get())

    def ubah_otomatis(self) -> None:
        self.peng.nyala_otomatis = self.var_otomatis.get()
        alasan = inti.atur_nyala_otomatis(self.peng.nyala_otomatis)
        if alasan:
            messagebox.showerror(inti.NAMA_APLIKASI, alasan)
        self.peng.simpan()

    def firewall(self) -> None:
        alasan = inti.izinkan_firewall(self.peng.port_bridge)
        if alasan:
            messagebox.showerror(inti.NAMA_APLIKASI, alasan)

    def buka_tutup_atur(self) -> None:
        if self.atur_frame.winfo_ismapped():
            self.atur_frame.pack_forget()
            self.tombol_atur.configure(text="Pengaturan  ▸")
            self.akar.geometry("480x470")
        else:
            self.atur_frame.pack(fill="x", pady=(4, 0))
            self.tombol_atur.configure(text="Pengaturan  ▾")
            self.akar.geometry("480x640")

    def tutup_jendela(self) -> None:
        # Tombol X hanya mengecilkan: kalau aplikasi mati, nota tidak tercetak.
        self.akar.iconify()

    def matikan(self) -> None:
        if messagebox.askyesno(inti.NAMA_APLIKASI,
                               "Kalau dimatikan, nota dari kasir TIDAK akan tercetak.\n\nYakin matikan?"):
            inti.catat("dimatikan dari jendela")
            if self.penghubung:
                self.penghubung.berhenti()
            inti.jangan_tidur(False)
            self.akar.destroy()


def main() -> int:
    diam = "--diam" in sys.argv
    peng = inti.Pengaturan.baca()
    if inti.port_bridge_dipakai(peng.port_bridge):
        if not diam:
            akar = tk.Tk()
            akar.withdraw()
            messagebox.showinfo(inti.NAMA_APLIKASI,
                                "Penghubung Cetak sudah menyala.\nCari jendelanya di taskbar bawah.")
        return 0
    akar = tk.Tk()
    Aplikasi(akar, diam)
    akar.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
