"""Sesi opname: opname besar dengan banyak tablet sekaligus (keputusan pemilik 9 Nov 2026).

Alur (target Opname Desember 2026, toko tutup):
1. Pemilik membuka satu sesi, lalu tugas hitung per orang (lokasi + kategori/rak, boleh semua barang).
2. Di tablet, karyawan memilih rak tempat ia berdiri lalu mengetik jumlah. Tiap hitungan dicatat per
   TEMPAT (lokasi + rak). Daftar barang dimuat ke tablet sekali, jadi cari/isi tetap jalan tanpa sinyal.
   Stok sistem tidak pernah terlihat (hitungan buta).
3. Semua hitungan DIJUMLAH per barang per lokasi (palu: rak 3 = 4, rak 7 = 6 -> 10). Selisih dihitung
   sekali saat ACC, tidak per tugas, supaya hitungan di tempat berbeda tidak saling menimpa.
4. Sebelum ACC, pemilik membereskan:
   - tempat sama dihitung dua tugas (konflik): pilih salah satu;
   - barang yang stok sistemnya ada di satu lokasi tetapi tidak dihitung di sana: hitung ulang (tugas
     baru otomatis) atau dianggap 0;
   - barang yang tidak ditemukan sama sekali dan stoknya 0: boleh ditandai nonaktif.
5. ACC: stok per lokasi disesuaikan, rak barang diisi dari tempat hitungan (rak utama + rak tambahan),
   barang yang ditandai dinonaktifkan, hasil disimpan untuk nilai stok.
"""
from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from pos.models import Item

from master.layanan import inti as layanan
from master.layanan.inti import Tolak, rapikan_teks
from master.models import HitungTempat, Lokasi, Rak, SesiHasil, SesiOpname, Tiket, VarianInfo, VarianRak

BATAS_KIRIM = 500


def sesi_buka():
    return SesiOpname.objects.filter(status="buka").first()


def buka(pengguna, nama, *, hak):
    if "opname" not in hak:
        raise Tolak("Akun ini tidak boleh membuka opname.")
    nama = rapikan_teks(nama)[:100]
    if not nama:
        raise Tolak("Isi nama sesi, mis. 'Opname Desember 2026'.")
    if sesi_buka() is not None:
        raise Tolak("Masih ada sesi opname yang berjalan. Selesaikan atau batalkan dulu.")
    s = SesiOpname.objects.create(nama=nama, dibuka_oleh=pengguna.username)
    layanan.catat(pengguna, aksi="tambah", objek="opname", kolom="sesi", baru=f"Sesi opname #{s.id} dibuka: {nama}")
    return s


def batal(pengguna, sesi, *, hak):
    if "opname" not in hak:
        raise Tolak("Akun ini tidak boleh memutuskan opname.")
    if sesi.status != "buka":
        raise Tolak("Sesi ini sudah ditutup.")
    with transaction.atomic():
        Tiket.objects.filter(sesi=sesi, status__in=["terbuka", "dikirim"]).update(
            status="batal", diputus_oleh=pengguna.username, diputus_waktu=timezone.now())
        sesi.status, sesi.diputus_oleh, sesi.diputus_waktu = "batal", pengguna.username, timezone.now()
        sesi.save()
    layanan.catat(pengguna, objek="opname", kolom="sesi", baru=f"Sesi opname #{sesi.id} dibatalkan; stok tidak berubah")


# ---------------------------------------------------------------------------
# Tablet gudang
# ---------------------------------------------------------------------------


def rak_lokasi(lokasi):
    """Rak yang bisa dipilih di satu lokasi: rak lokasi itu + rak lama yang belum diberi lokasi."""
    return list(Rak.objects.filter(Q(tempat=lokasi) | Q(tempat__isnull=True)).order_by("kode"))


def barang_tablet(t):
    """Semua barang aktif untuk dimuat ke tablet (tanpa stok, harga, modal). `l` = masuk lingkup tugas."""
    from master.layanan.opname import lingkup_tugas
    lingkup = set(lingkup_tugas(t).values_list("pk", flat=True))
    info = {(i["item_name"], i["variant"]): i for i in Item.objects.values("item_name", "variant", "brand", "category")}
    hasil = []
    for v in VarianInfo.objects.filter(aktif=True, sampah__isnull=True).only(
            "id", "item_name", "variant", "rak_id", "satuan", "kelipatan", "alias_tambahan"):
        i = info.get((v.item_name, v.variant), {})
        k = int(v.kelipatan or 1) if v.satuan else 1
        hasil.append({"i": v.id, "n": v.item_name, "v": "" if v.variant == "N/A" else v.variant,
                      "b": i.get("brand") or "", "c": i.get("category") or "", "a": v.alias_tambahan or "",
                      "r": v.rak_id or "", "s": v.satuan or "", "k": k, "l": v.id in lingkup})
    return hasil


def hitungan_tugas(t):
    """Hitungan tugas ini untuk tablet: [{varian, rak, jumlah (tersimpan)}]."""
    return [{"varian": h.varian_id, "rak": h.rak_id or "", "jumlah": h.jumlah}
            for h in HitungTempat.objects.filter(tiket=t)]


def simpan(pengguna, t, entri):
    """Simpan antrian dari tablet. Tiap entri {kunci, varian, rak, jumlah | hapus}.

    Hitungan per (tugas, barang, rak) ditimpa kalau diketik ulang (koreksi), bukan dijumlah.
    Mengembalikan {"ok": [kunci...], "gagal": [{"kunci", "pesan"}]}. Entri yang gagal tidak
    menggagalkan yang lain, supaya antrian di tablet bisa terus berkurang.
    """
    from master.layanan.opname import lokasi_tugas
    from master.layanan.satuan import baca_qty
    from master.layanan.tiket import _harus_bisa_diisi
    _harus_bisa_diisi(pengguna, t)
    if t.sesi is None or t.sesi.status != "buka":
        raise Tolak("Sesi opname tugas ini sudah ditutup.")
    if not isinstance(entri, list) or len(entri) > BATAS_KIRIM:
        raise Tolak("Isi kiriman tidak terbaca.")
    lokasi = lokasi_tugas(t)
    rak_boleh = {r.kode: r for r in rak_lokasi(lokasi)}
    ids = {int(e.get("varian")) for e in entri if isinstance(e, dict) and str(e.get("varian", "")).isdigit()}
    varian = VarianInfo.objects.in_bulk(ids)
    ok, gagal = [], []
    sekarang = timezone.now()
    oleh = pengguna.first_name or pengguna.username
    for e in entri:
        kunci = str((e or {}).get("kunci", ""))[:80] if isinstance(e, dict) else ""
        try:
            if not isinstance(e, dict) or not str(e.get("varian", "")).isdigit():
                raise Tolak("Barang tidak dikenal.")
            v = varian.get(int(e["varian"]))
            if v is None or not v.aktif:
                raise Tolak("Barang tidak ditemukan atau sudah nonaktif.")
            kode = str(e.get("rak") or "")
            rak = rak_boleh.get(kode) if kode else None
            if kode and rak is None:
                raise Tolak(f"Rak {kode} bukan rak di {lokasi.nama}.")
            kunci_baris = {"sesi": t.sesi, "tiket": t, "varian": v, "lokasi": lokasi, "rak": rak}
            if e.get("hapus"):
                HitungTempat.objects.filter(**kunci_baris).delete()
            else:
                jumlah = baca_qty(e.get("jumlah"), v, nama=v.item_name)
                if jumlah < 0:
                    raise Tolak("Jumlah tidak boleh minus.")
                HitungTempat.objects.update_or_create(**kunci_baris, defaults={
                    "jumlah": jumlah, "oleh": oleh, "waktu": sekarang,
                    "catatan": rapikan_teks(e.get("catatan"))[:200]})
            ok.append(kunci)
        except Tolak as tolak:
            gagal.append({"kunci": kunci, "pesan": str(tolak)})
    return {"ok": ok, "gagal": gagal}


def kirim(pengguna, t, dihitung_oleh=""):
    from master.layanan.tiket import _harus_bisa_diisi
    _harus_bisa_diisi(pengguna, t)
    n = HitungTempat.objects.filter(tiket=t).count()
    if not n:
        raise Tolak("Belum ada barang yang dihitung.")
    t.status, t.dikirim_waktu = "dikirim", timezone.now()
    t.catatan_admin = rapikan_teks(dihitung_oleh)[:500]
    t.save()
    layanan.catat(pengguna, objek="tiket", kolom="status", baru=f"Tugas hitung #{t.id} dikirim: {n} hitungan")
    return n


def kembalikan(pengguna, t, *, hak, jam=4):
    """Pemilik membuka lagi tugas yang sudah dikirim / lewat batas supaya bisa dilanjutkan."""
    if "opname" not in hak:
        raise Tolak("Akun ini tidak boleh memutuskan opname.")
    if t.sesi is None or t.sesi.status != "buka":
        raise Tolak("Sesi opname tugas ini sudah ditutup.")
    if t.status not in ("terbuka", "dikirim"):
        raise Tolak("Tugas ini sudah dibatalkan atau ditolak.")
    t.status, t.batas = "terbuka", timezone.now() + timedelta(hours=jam)
    t.save()


# ---------------------------------------------------------------------------
# Rekap untuk pemilik
# ---------------------------------------------------------------------------


def _hitungan_sah(sesi):
    return HitungTempat.objects.filter(sesi=sesi).filter(
        Q(tiket__isnull=True) | Q(tiket__status__in=["terbuka", "dikirim", "selesai"]))


def rekap(sesi):
    """Bandingkan jumlah hitungan per barang per lokasi dengan stok sistem sekarang.

    Mengembalikan dict: baris (barang yang dihitung), konflik, belum (stok ada tapi tidak dihitung di
    lokasi itu), tak_ditemukan (tidak dihitung di mana pun dan stoknya 0), dan angka ringkasan.
    """
    from master.layanan.ambil import dipesan
    from master.layanan.lokasi import gudang_aktif, jumlah_gudang, lokasi_toko
    toko = lokasi_toko()
    gudang = gudang_aktif()
    semua_lokasi = [toko] + gudang
    nama_lokasi = {l.id: l for l in semua_lokasi}
    hit = list(_hitungan_sah(sesi).select_related("rak", "tiket__untuk"))
    jumlah = defaultdict(int)
    tempat = defaultdict(list)
    for h in hit:
        jumlah[(h.varian_id, h.lokasi_id)] += h.jumlah
        tempat[(h.varian_id, h.lokasi_id)].append(h)
    # Tempat yang sama (barang + lokasi + rak) dihitung lebih dari satu tugas: tidak boleh dijumlah diam-diam.
    per_tempat = defaultdict(list)
    for h in hit:
        per_tempat[(h.varian_id, h.lokasi_id, h.rak_id)].append(h)
    konflik_kunci = {k for k, d in per_tempat.items() if len({x.tiket_id for x in d}) > 1}

    varian = {v.id: v for v in VarianInfo.objects.filter(
        Q(aktif=True, sampah__isnull=True) | Q(pk__in={k[0] for k in jumlah})).select_related("rak")}
    total = {(n, v): s or 0 for n, v, s in Item.objects.values_list("item_name", "variant", "in_stock")}
    modal_akhir = {(n, v): m for n, v, m in Item.objects.values_list("item_name", "variant", "cost_amount")}
    gdg = jumlah_gudang(varian)
    pesan = dipesan(varian)

    baris, belum, tak_ditemukan = [], [], []
    for vid, v in varian.items():
        kunci = (v.item_name, v.variant)
        per = gdg.get(vid, {})
        sistem = {l.id: per.get(l.id, 0) for l in gudang}
        sistem[toko.id] = total.get(kunci, 0) - sum(per.values()) + pesan.get(vid, 0)
        modal = v.modal_rata if v.modal_rata is not None else modal_akhir.get(kunci)
        dihitung_di = [l for l in semua_lokasi if (vid, l.id) in jumlah]
        for l in semua_lokasi:
            if (vid, l.id) in jumlah:
                n = jumlah[(vid, l.id)]
                baris.append({"v": v, "lokasi": l, "sistem": sistem[l.id], "hitung": n, "selisih": n - sistem[l.id],
                              "modal": modal, "nilai": None if modal is None else n * modal,
                              "nilai_selisih": None if modal is None else (n - sistem[l.id]) * modal,
                              "tempat": tempat[(vid, l.id)]})
            elif sistem[l.id] and v.aktif:
                belum.append({"v": v, "lokasi": l, "sistem": sistem[l.id], "dihitung_lain": bool(dihitung_di)})
        if not dihitung_di and v.aktif and not any(sistem.values()):
            tak_ditemukan.append(v)
    urut = lambda r: (layanan.kunci_alami(r["v"].item_name), layanan.kunci_alami(r["v"].variant), r["lokasi"].urutan)
    baris.sort(key=urut)
    belum.sort(key=urut)
    tak_ditemukan.sort(key=lambda v: (layanan.kunci_alami(v.item_name), layanan.kunci_alami(v.variant)))
    konflik = [{"v": varian.get(k[0]), "lokasi": nama_lokasi.get(k[1]), "rak": k[2], "hitungan": per_tempat[k]}
               for k in sorted(konflik_kunci, key=lambda k: (k[0], k[1], k[2] or ""))]
    from pos.models import Transactions
    return {
        "baris": baris, "beda": [b for b in baris if b["selisih"]], "konflik": konflik, "belum": belum,
        "tak_ditemukan": tak_ditemukan,
        "nilai": sum(b["nilai"] or 0 for b in baris),
        "nilai_selisih": sum(b["nilai_selisih"] or 0 for b in baris),
        "tanpa_modal": [b for b in baris if b["modal"] is None and b["hitung"]],
        "jumlah_barang": len({b["v"].id for b in baris}),
        "transaksi_kasir": Transactions.objects.filter(transaction_time__gte=sesi.dibuka_waktu).count(),
        "nonaktifkan": set(sesi.nonaktifkan or []),
    }


def hapus_hitungan(pengguna, sesi, hitungan_id, *, hak):
    """Pemilik membuang satu hitungan (mis. tempat yang dihitung dua orang)."""
    if "opname" not in hak:
        raise Tolak("Akun ini tidak boleh memutuskan opname.")
    if sesi.status != "buka":
        raise Tolak("Sesi ini sudah ditutup.")
    h = HitungTempat.objects.filter(sesi=sesi, pk=hitungan_id).select_related("varian").first()
    if h is None:
        raise Tolak("Hitungan tidak ditemukan.")
    h.delete()
    layanan.catat(pengguna, objek="opname", kolom="sesi", item_name=h.varian.item_name, variant=h.varian.variant,
                  lama=h.jumlah, baru="", catatan=f"Sesi #{sesi.id}: hitungan dibuang")


def _pasangan(sesi, isian):
    """'varianId-lokasiId' dari formulir -> [(VarianInfo, Lokasi)]."""
    pasang = []
    for x in isian:
        a, _, b = str(x).partition("-")
        if a.isdigit() and b.isdigit():
            pasang.append((int(a), int(b)))
    varian = VarianInfo.objects.in_bulk({a for a, _ in pasang})
    lokasi = Lokasi.objects.in_bulk({b for _, b in pasang})
    return [(varian[a], lokasi[b]) for a, b in pasang if a in varian and b in lokasi]


def anggap_nol(pengguna, sesi, isian, *, hak, catatan=""):
    """Barang yang sudah dicek ulang dan memang tidak ada di lokasi itu: dicatat hitung 0."""
    if "opname" not in hak:
        raise Tolak("Akun ini tidak boleh memutuskan opname.")
    if sesi.status != "buka":
        raise Tolak("Sesi ini sudah ditutup.")
    pasang = _pasangan(sesi, isian)
    if not pasang:
        raise Tolak("Pilih barang yang dianggap 0.")
    catatan = rapikan_teks(catatan)[:200] or "dicek pemilik: tidak ada"
    sekarang = timezone.now()
    with transaction.atomic():
        for v, l in pasang:
            HitungTempat.objects.update_or_create(sesi=sesi, tiket=None, varian=v, lokasi=l, rak=None, defaults={
                "jumlah": 0, "oleh": pengguna.username, "waktu": sekarang, "catatan": catatan})
    return len(pasang)


def tugas_ulang(pengguna, sesi, isian, *, untuk, hak, jam=4):
    """Satu tugas hitung ulang per lokasi untuk barang yang dipilih dari daftar belum dihitung."""
    from master.layanan import tiket as svc_tiket
    if sesi.status != "buka":
        raise Tolak("Sesi ini sudah ditutup.")
    pasang = _pasangan(sesi, isian)
    if not pasang:
        raise Tolak("Pilih barang yang dihitung ulang.")
    per_lokasi = defaultdict(list)
    for v, l in pasang:
        per_lokasi[l].append(v.id)
    hasil = []
    with transaction.atomic():
        for l, ids in per_lokasi.items():
            hasil.append(svc_tiket.buka(
                pengguna, untuk=untuk, tujuan=f"Hitung ulang {sesi.nama}", batas=timezone.now() + timedelta(hours=jam),
                hak=hak, jenis="hitung", lokasi=l.id, sesi=sesi, varian=ids))
    return hasil


def tandai_nonaktif(pengguna, sesi, varian_ids, ya, *, hak):
    if "opname" not in hak:
        raise Tolak("Akun ini tidak boleh memutuskan opname.")
    if sesi.status != "buka":
        raise Tolak("Sesi ini sudah ditutup.")
    ids = {int(x) for x in varian_ids if str(x).isdigit()}
    sekarang = set(sesi.nonaktifkan or [])
    sesi.nonaktifkan = sorted(sekarang | ids if ya else sekarang - ids)
    sesi.save(update_fields=["nonaktifkan"])
    return len(ids)


# ---------------------------------------------------------------------------
# ACC
# ---------------------------------------------------------------------------


def acc(pengguna, sesi, *, hak):
    from master.layanan import atur_barang
    from master.layanan.lokasi import stok_di
    if "opname" not in hak:
        raise Tolak("Akun ini tidak boleh menyetujui opname.")
    with transaction.atomic():
        sesi = SesiOpname.objects.select_for_update().get(pk=sesi.pk)
        if sesi.status != "buka":
            raise Tolak("Sesi ini sudah ditutup.")
        jalan = Tiket.objects.filter(sesi=sesi, status="terbuka", batas__gt=timezone.now())
        if jalan.exists():
            raise Tolak(f"Masih ada {jalan.count()} tugas yang sedang dihitung. Tunggu dikirim, atau batalkan.")
        r = rekap(sesi)
        if r["konflik"]:
            raise Tolak(f"Ada {len(r['konflik'])} tempat yang dihitung dua tugas. Pilih salah satu dulu.")
        if r["belum"]:
            raise Tolak(f"Ada {len(r['belum'])} barang yang stoknya tercatat tapi belum dihitung. "
                        "Hitung ulang atau anggap 0 dulu.")
        if not r["baris"]:
            raise Tolak("Belum ada hitungan di sesi ini.")
        hak_stok = hak | {"ubah_stok"}
        # Gudang dulu, lalu toko: stok toko = total - gudang, jadi dihitung ulang setelah gudang disesuaikan.
        for b in sorted(r["baris"], key=lambda b: b["lokasi"].jenis == "toko"):
            v, l = b["v"], b["lokasi"]
            sistem = stok_di(v, l)
            selisih = b["hitung"] - sistem
            if selisih:
                layanan.sesuaikan_stok(pengguna, v, cara="tambah" if selisih > 0 else "kurang", angka=abs(selisih),
                                       jenis="hitung", hak=hak_stok, lokasi=None if l.jenis == "toko" else l,
                                       catatan=f"{sesi.nama} (sesi opname #{sesi.id}): dihitung {b['hitung']} di {l.nama}")
            SesiHasil.objects.create(sesi=sesi, varian=v, lokasi=l, sistem=sistem, hitung=b["hitung"], modal=b["modal"])
        _isi_rak(sesi)
        nonaktif = [x for x in (sesi.nonaktifkan or []) if not any(
            b["hitung"] for b in r["baris"] if b["v"].id == x)]
        if nonaktif:
            atur_barang.ubah_aktif(pengguna, nonaktif, False, hak=hak | {"ubah_info"})
        Tiket.objects.filter(sesi=sesi, status__in=["terbuka", "dikirim"]).update(
            status="selesai", diputus_oleh=pengguna.username, diputus_waktu=timezone.now())
        sesi.status, sesi.diputus_oleh, sesi.diputus_waktu = "disetujui", pengguna.username, timezone.now()
        sesi.save()
        layanan.catat(pengguna, objek="opname", kolom="sesi", lama="Sedang dihitung", baru="Disetujui",
                      catatan=f"Sesi #{sesi.id} {sesi.nama}: {r['jumlah_barang']} barang, {len(r['beda'])} selisih, "
                              f"{len(nonaktif)} dinonaktifkan")
    return r


def _isi_rak(sesi):
    """Rak barang dari tempat hitungan: rak utama tetap kalau masih terpakai, sisanya jadi rak tambahan."""
    per = defaultdict(lambda: defaultdict(int))
    for vid, rak, n in _hitungan_sah(sesi).exclude(rak=None).filter(jumlah__gt=0).values_list("varian_id", "rak_id", "jumlah"):
        per[vid][rak] += n
    for v in VarianInfo.objects.filter(pk__in=list(per)):
        rak = per[v.id]
        utama = v.rak_id if v.rak_id in rak else max(rak, key=lambda k: (rak[k], k))
        if utama != v.rak_id:
            VarianInfo.objects.filter(pk=v.pk).update(rak_id=utama)
        VarianRak.objects.filter(varian=v).delete()
        VarianRak.objects.bulk_create([VarianRak(varian=v, rak_id=k) for k in sorted(rak) if k != utama])


def hasil_ringkas(sesi):
    """Untuk sesi yang sudah di-ACC: nilai stok hasil hitung."""
    hasil = list(sesi.hasil.select_related("varian", "lokasi"))
    return {
        "nilai": sum(h.hitung * h.modal for h in hasil if h.modal is not None),
        "tanpa_modal": sum(1 for h in hasil if h.modal is None and h.hitung),
        "beda": sum(1 for h in hasil if h.hitung != h.sistem),
        "nilai_selisih": sum((h.hitung - h.sistem) * h.modal for h in hasil if h.modal is not None),
        "jumlah": len({h.varian_id for h in hasil}),
    }
