"""Daftar "perlu diperbaiki": aturan pengecekan data barang.

Tiap aturan punya dua bentuk yang harus selalu sejalan:
- `saring`  : filter untuk queryset Item (dipakai menghitung dan menyaring)
- `periksa` : cek satu baris di Python (dipakai memberi label di tabel)
"""
from django.db.models import Exists, F, OuterRef, Q

from master.models import ItemInfo, VarianInfo


def _alias_terisi():
    return ItemInfo.objects.filter(item_name=OuterRef("item_name")).exclude(alias="")


def _punya_rak():
    return VarianInfo.objects.filter(
        item_name=OuterRef("item_name"), variant=OuterRef("variant"), rak__isnull=False
    )


ATURAN = [
    {
        "kunci": "floor_terbalik",
        "label": "Floor di atas harga jual",
        "akibat": "Tidak bisa dijual di kasir.",
        "modal": False,
        "saring": lambda qs: qs.filter(max_price__gt=0, min_price__gt=F("max_price")),
        "periksa": lambda b: (b["max_price"] or 0) > 0 and (b["min_price"] or 0) > b["max_price"],
    },
    {
        "kunci": "harga_nol",
        "label": "Harga kosong atau nol",
        "akibat": "Tidak bisa masuk keranjang.",
        "modal": False,
        "saring": lambda qs: qs.filter(
            Q(max_price__isnull=True) | Q(max_price__lte=0) | Q(min_price__isnull=True) | Q(min_price__lte=0)
        ),
        "periksa": lambda b: (b["max_price"] or 0) <= 0 or (b["min_price"] or 0) <= 0,
    },
    {
        "kunci": "jual_dibawah_modal",
        "label": "Harga jual di bawah modal",
        "akibat": "Dijual rugi walau tanpa tawar.",
        "modal": True,
        "saring": lambda qs: qs.filter(max_price__gt=0, cost_amount__gt=F("max_price")),
        "periksa": lambda b: (b["max_price"] or 0) > 0 and (b["cost_amount"] or 0) > b["max_price"],
    },
    {
        "kunci": "floor_dibawah_modal",
        "label": "Floor di bawah modal",
        "akibat": "Kasir boleh menawar sampai rugi.",
        "modal": True,
        "saring": lambda qs: qs.filter(min_price__gt=0, cost_amount__gt=F("min_price")),
        "periksa": lambda b: (b["min_price"] or 0) > 0 and (b["cost_amount"] or 0) > b["min_price"],
    },
    {
        "kunci": "modal_kosong",
        "label": "Modal kosong atau nol",
        "akibat": "Laporan laba salah untuk barang ini.",
        "modal": True,
        "saring": lambda qs: qs.filter(Q(cost_amount__isnull=True) | Q(cost_amount__lte=0)),
        "periksa": lambda b: (b["cost_amount"] or 0) <= 0,
    },
    {
        "kunci": "alias_kosong",
        "label": "Alias kosong",
        "akibat": "Hanya bisa dicari dengan nama persisnya.",
        "modal": False,
        "saring": lambda qs: qs.filter(~Exists(_alias_terisi())),
        "periksa": lambda b: not (b.get("alias") or "").strip(),
    },
    {
        "kunci": "tanpa_rak",
        "label": "Belum ada rak",
        "akibat": "Karyawan tidak tahu letak barangnya.",
        "modal": False,
        "saring": lambda qs: qs.filter(~Exists(_punya_rak())),
        "periksa": lambda b: not b.get("kode_rak"),
    },
]

PER_KUNCI = {aturan["kunci"]: aturan for aturan in ATURAN}


def aturan_untuk(hak):
    """Aturan yang boleh dilihat: yang menyangkut modal hanya untuk yang berhak."""
    return [a for a in ATURAN if not a["modal"] or "lihat_modal" in hak]


def label_masalah(baris, hak):
    return [
        {"kunci": a["kunci"], "label": a["label"]}
        for a in aturan_untuk(hak)
        if a["periksa"](baris)
    ]


# Sel mana yang ditandai untuk tiap masalah (dipakai mewarnai sel di tabel).
SEL = {
    "floor_terbalik": ["min_price", "max_price"],
    "jual_dibawah_modal": ["max_price", "cost_amount"],
    "floor_dibawah_modal": ["min_price", "cost_amount"],
    "modal_kosong": ["cost_amount"],
}


def tanda_sel(baris, daftar_label):
    tanda = set()
    for m in daftar_label:
        tanda.update(SEL.get(m["kunci"], []))
        if m["kunci"] == "harga_nol":
            for kolom in ("min_price", "max_price"):
                if (baris.get(kolom) or 0) <= 0:
                    tanda.add(kolom)
    return sorted(tanda)
