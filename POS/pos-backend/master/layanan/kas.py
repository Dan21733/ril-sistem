"""Kas laci di master dan HP: buka/tutup oleh pemilik, uang keluar, ACC tutup kas.

Logika hitung ada di `pos.kas` (dipakai juga server kasir). Modul ini hanya
mengubah galat menjadi `Tolak`, mencatat log, dan menyiapkan data tampilan.
"""
import uuid

from django.utils import timezone

from pos import kas as inti
from pos.models import DeviceStatus, KasGerak, KasSesi

from master.layanan import inti as layanan
from master.layanan.inti import Tolak

rp = inti.rp
KATEGORI_KELUAR = inti.KATEGORI_KELUAR

BARIS_RINCIAN = [
    ("modal_awal", "Modal saat buka", 1),
    ("tunai_nota", "Penjualan tunai", 1),
    ("tunai_ditahan", "Nota tunai yang ditahan (perlu ACC)", 1),
    ("pelunasan_tunai", "Pelunasan / DP tunai di Transaksi", 1),
    ("tambah_modal", "Tambah modal", 1),
    ("pakai_cadangan", "Cadangan dipakai", 1),
    ("refund_tunai", "Refund / void tunai", -1),
    ("keluar", "Uang keluar", -1),
    ("isi_cadangan", "Diisikan lagi ke cadangan", -1),
]


def nama(pengguna):
    return (getattr(pengguna, "first_name", "") or getattr(pengguna, "username", "") or "pemilik")[:150]


def _jalankan(fungsi, *a, **kw):
    try:
        return fungsi(*a, **kw)
    except inti.KasDitolak as e:
        raise Tolak(str(e))


def toko_kasir_id():
    return inti.toko_kasir()


def punya_kas(toko):
    return toko is not None and toko.pk == inti.toko_kasir()


def rincian_baris(r):
    """[(label, nilai bertanda)] untuk tabel rincian, tanpa baris nol (kecuali modal)."""
    hasil = []
    for kunci, label, tanda in BARIS_RINCIAN:
        n = int(r.get(kunci) or 0)
        if n or kunci == "modal_awal":
            if kunci == "tunai_nota" and r.get("jumlah_nota_tunai"):
                label = f"{label} ({r['jumlah_nota_tunai']} nota)"
            hasil.append((label, n * tanda))
    return hasil


def tablet_kas():
    """Tablet yang boleh buka & tutup kas: [{"nama"}] (nama tablet, atau kodenya kalau belum diberi nama)."""
    from pos.models import Device
    kode = dict(Device.objects.values_list("id", "device_code"))
    return [{"nama": s.nama or f"Tablet {kode.get(s.device_id, '?')}"}
            for s in DeviceStatus.objects.filter(aktif=True, boleh_kas=True).order_by("nama")]


def ringkas(toko):
    """Keadaan kas toko untuk master/HP (lengkap dengan angka seharusnya; hanya untuk pemilik)."""
    if not punya_kas(toko):
        return None
    s = inti.sesi_buka(toko.pk)
    hasil = {
        "sesi": s, "hitung": inti.hitung(s) if s else None, "modal_saran": inti.modal_saran(toko.pk),
        "cadangan": inti.cadangan_nominal(), "cadangan_terpakai": inti.cadangan_terpakai(toko.pk),
        "tablet_kas": tablet_kas(), "antrean": inti.antrean_tertunda(),
        "menunggu_acc": list(KasSesi.objects.filter(toko_id=toko.pk, status="ditutup", acc_status="menunggu")
                             .order_by("-ditutup_waktu")),
        "terakhir": inti.sesi_tutup_terakhir(toko.pk),
    }
    hasil["tutup_hari_ini"] = bool(s is None and hasil["terakhir"] and
                                   timezone.localdate(hasil["terakhir"].ditutup_waktu) == timezone.localdate())
    if s is not None:
        hasil["rincian"] = rincian_baris(hasil["hitung"])
        hasil["gerak"] = inti.gerak_sesi(s)
    return hasil


def riwayat(toko, batas=40):
    return list(KasSesi.objects.filter(toko_id=getattr(toko, "pk", None)).order_by("-dibuka_waktu")[:batas])


def jumlah_menunggu():
    return KasSesi.objects.filter(status="ditutup", acc_status="menunggu").count()


def buka(pengguna, toko, data, dari="master"):
    if not punya_kas(toko):
        raise Tolak("Kas hanya untuk toko yang memegang kasir.")
    s = _jalankan(inti.buka, toko.pk, modal=data.get("modal"), cadangan_lengkap=bool(data.get("cadangan_lengkap")),
                  pakai_cadangan=data.get("pakai_cadangan") or 0, catatan=data.get("catatan") or "",
                  oleh=nama(pengguna), dari=dari)
    layanan.catat(pengguna, aksi="tambah", objek="kas", kolom="buka kas", baru=f"{toko.nama}: modal {rp(s.modal_awal)}")
    return s


def tutup(pengguna, sesi_id, data, dari="master"):
    s = _jalankan(inti.tutup, sesi_id, ambil_100=data.get("ambil_100"), ambil_50=data.get("ambil_50"),
                  uang_sisa=data.get("uang_sisa"), cadangan_lengkap=bool(data.get("cadangan_lengkap")),
                  isi_cadangan=data.get("isi_cadangan") or 0, catatan=data.get("catatan") or "",
                  oleh=nama(pengguna), dari=dari, paksa=bool(data.get("paksa")))
    layanan.catat(pengguna, aksi="ubah", objek="kas", kolom="tutup kas",
                  baru=f"dihitung {rp(s.dihitung)}, seharusnya {rp(s.seharusnya)}, selisih {rp(s.selisih)}")
    return s


def catat_gerak(pengguna, toko, data, dari="master"):
    if not punya_kas(toko):
        raise Tolak("Kas hanya untuk toko yang memegang kasir.")
    data = {k: data.get(k) for k in ("gerak_id", "jenis", "jumlah", "kategori", "keterangan")}
    if not data.get("gerak_id"):
        data["gerak_id"] = str(uuid.uuid4())
    g, _ = _jalankan(inti.catat_gerak, data, toko_id=toko.pk, oleh=nama(pengguna), dari=dari)
    layanan.catat(pengguna, aksi="tambah", objek="kas", kolom=g.get_jenis_display().lower(),
                  baru=f"{rp(g.jumlah)} {g.kategori} {g.keterangan}".strip())
    return g


def batal_gerak(pengguna, gerak_id, alasan):
    g = _jalankan(inti.batal_gerak, gerak_id, oleh=nama(pengguna), alasan=alasan)
    layanan.catat(pengguna, aksi="hapus", objek="kas", kolom=g.get_jenis_display().lower(),
                  lama=f"{rp(g.jumlah)} {g.kategori}".strip(), catatan=alasan)
    return g


def acc(pengguna, sesi_id, setuju, catatan=""):
    s = _jalankan(inti.acc, sesi_id, setuju=setuju, oleh=nama(pengguna), catatan=catatan)
    layanan.catat(pengguna, aksi="ubah", objek="kas", kolom="ACC tutup kas",
                  baru=f"{'diterima' if setuju else 'ditolak'}: selisih {rp(s.selisih)}", catatan=catatan)
    return s


def detail(sesi_id):
    try:
        s = KasSesi.objects.filter(pk=uuid.UUID(str(sesi_id))).first()
    except ValueError:
        s = None
    if s is None:
        return None
    r = s.rincian if s.status == "ditutup" and s.rincian else inti.hitung(s)
    sekarang = inti.hitung(s) if s.status == "ditutup" else r
    return {"s": s, "hitung": r, "rincian": rincian_baris(r), "gerak": inti.gerak_sesi(s),
            # Nota yang masuk telat sesudah tutup (seharusnya jarang: tutup ditahan selama ada antrean).
            "berubah": s.status == "ditutup" and sekarang["seharusnya"] != r["seharusnya"],
            "sekarang": sekarang}


def atur_tablet_kas(pengguna, device_id, boleh):
    from pos.models import Device
    try:
        d = Device.objects.filter(id=uuid.UUID(str(device_id))).first()
    except ValueError:
        d = None
    if d is None:
        raise Tolak("Tablet tidak ditemukan.")
    s, _ = DeviceStatus.objects.get_or_create(device_id=d.id)
    s.boleh_kas = bool(boleh)
    s.save(update_fields=["boleh_kas"])
    layanan.catat(pengguna, aksi="ubah", objek="tablet", kolom="boleh buka & tutup kas",
                  baru=f"{s.nama or s.device_id}: {'ya' if s.boleh_kas else 'tidak'}")
    return s


def angka_hari_ini(toko):
    """Untuk beranda HP: tunai di laci, transfer, EDC hari ini (atau sesi berjalan)."""
    r = ringkas(toko)
    if r is None:
        return None
    s = r["sesi"]
    h = r["hitung"] if s else None
    return {"buka": s is not None, "dibuka_waktu": s.dibuka_waktu if s else None,
            "seharusnya": h["seharusnya"] if h else None, "menunggu_acc": len(r["menunggu_acc"]),
            "ditutup_waktu": r["terakhir"].ditutup_waktu if r["terakhir"] else None,
            "terakhir_hari_ini": bool(r["terakhir"] and timezone.localdate(r["terakhir"].ditutup_waktu)
                                      == timezone.localdate())}
