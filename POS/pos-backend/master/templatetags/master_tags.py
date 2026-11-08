from django import template

register = template.Library()


@register.filter
def ribuan(nilai):
    """17000 -> 17.000 ; None -> kosong."""
    if nilai is None or nilai == "":
        return ""
    try:
        return f"{int(nilai):,}".replace(",", ".")
    except (TypeError, ValueError):
        return nilai


@register.filter
def rupiah(nilai):
    teks = ribuan(nilai)
    if isinstance(teks, str) and teks.startswith("-"):
        return f"−Rp{teks[1:]}"     # -25000 -> −Rp25.000 (bukan Rp-25.000)
    return f"Rp{teks}" if teks != "" else "-"


@register.filter
def varian(nilai):
    """Varian 'N/A' di tabel POS berarti barang tanpa varian."""
    return "tanpa varian" if nilai in (None, "", "N/A") else nilai


@register.filter
def dalam(nilai, daftar):
    """{% if menu|dalam:"stok opname" %}: cocok utuh per kata, bukan potongan teks."""
    return nilai in str(daftar).split()


@register.filter
def singkat(nilai):
    """Rupiah singkat untuk label grafik: 1.250.000 -> 1,3 jt; 85.000 -> 85 rb."""
    try:
        n = int(nilai or 0)
    except (TypeError, ValueError):
        return ""
    tanda = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000_000:
        return f"{tanda}{n / 1_000_000_000:.1f} M".replace(".", ",").replace(",0 ", " ")
    if n >= 1_000_000:
        return f"{tanda}{n / 1_000_000:.1f} jt".replace(".", ",").replace(",0 ", " ")
    if n >= 1_000:
        return f"{tanda}{round(n / 1_000)} rb"
    return f"{tanda}{n}"


@register.filter
def get(kamus, kunci):
    """{{ hitung|get:k }}: ambil isi dict per kunci di template."""
    try:
        return kamus.get(kunci, "")
    except AttributeError:
        return ""


@register.filter
def kurang(a, b):
    """a - b untuk angka (kosong kalau bukan angka)."""
    try:
        return int(a) - int(b)
    except (TypeError, ValueError):
        return ""


# Satuan (meter/kg) di layar gudang, PO, transfer: `{{ jumlah|qty:varian }}` -> "12,5 m".
@register.filter
def qty(jumlah, x):
    from master.layanan.satuan import format_jumlah, satuan_dari
    s, k = satuan_dari(x)
    if k <= 1 or jumlah in (None, ""):
        return ribuan(jumlah)
    return format_jumlah(jumlah, s, k)


@register.filter
def qty_isi(jumlah, x):
    """Nilai awal isian jumlah (tanpa satuan, koma desimal): 125 -> "12,5"."""
    from master.layanan.satuan import angka_teks, satuan_dari
    s, k = satuan_dari(x)
    if jumlah in (None, ""):
        return ""
    return angka_teks(jumlah, k) if k > 1 else jumlah


@register.filter
def harga_qty(harga, x):
    """Harga tersimpan per kelipatan -> harga per satuan (2.500 -> 25.000)."""
    from master.layanan.satuan import satuan_dari
    s, k = satuan_dari(x)
    return harga * k if (k > 1 and harga is not None) else harga


@register.filter
def akhiran(x):
    from master.layanan.satuan import satuan_dari
    s, k = satuan_dari(x)
    return f"/{s}" if k > 1 else ""


@register.filter
def berkoma(x):
    from master.layanan.satuan import satuan_dari
    return satuan_dari(x)[1] > 1


@register.filter
def nama_satuan(x):
    from master.layanan.satuan import satuan_dari
    s, k = satuan_dari(x)
    return s if k > 1 else ""
