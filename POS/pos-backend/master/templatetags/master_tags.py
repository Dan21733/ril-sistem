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
