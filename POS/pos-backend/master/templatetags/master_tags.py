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
