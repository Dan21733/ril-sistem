"""Generator logo review POS (alat bantu, bukan dependency produk).

Logo digambar prosedural dengan Pillow: bentuk geometris datar dua-tiga warna
(krem + teal + coklat dari main.css), tanpa gradien/bayangan — supaya tetap
tegas di ukuran favicon 16px. Seri DB = monogram huruf DB; ubah STORE_TEXT
lalu jalankan ulang. File review TIDAK dipasang ke public/ sebelum disetujui.
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
CREAM = (0xEF, 0xE8, 0xDD)
BROWN = (0x2E, 0x21, 0x19)
TEAL = (0x0F, 0x76, 0x6E)
SIZE = 666
STORE_TEXT = "DB"
FONT = "C:/Windows/Fonts/arialbd.ttf"


def font(size):
    return ImageFont.truetype(FONT, size)


def key_horizontal(d, cx_left, y, color, scale=1.0):
    """Kunci horizontal: ring kiri, batang, dua gigi di kanan."""
    r = int(55 * scale)
    w = int(26 * scale)
    d.ellipse([cx_left - r, y - r, cx_left + r, y + r], outline=color, width=w)
    x0 = cx_left + r + 8
    x1 = int(x0 + 235 * scale)
    d.line([x0, y, x1, y], fill=color, width=w)
    for tx in (x1 - int(50 * scale), x1):
        d.line([tx, y, tx, y + int(55 * scale)], fill=color, width=w)


def centered_text(d, y, text, color, size):
    f = font(size)
    box = d.textbbox((0, 0), text, font=f)
    tw = box[2] - box[0]
    d.text(((SIZE - tw) / 2 - box[0], y), text, font=f, fill=color)


def db1():
    """Monogram batang: D dan B berbagi satu tiang, digambar dari
    garis + busur tebal (bukan huruf font) — cushion 50px."""
    img = Image.new("RGB", (SIZE, SIZE), CREAM)
    d = ImageDraw.Draw(img)
    w = 50
    # Tiang bersama (round caps).
    d.line([225, 150, 225, 516], fill=BROWN, width=w)
    d.ellipse([200, 125, 250, 175], fill=BROWN)
    d.ellipse([200, 491, 250, 541], fill=BROWN)
    # Mangkuk D: setengah elips kanan dari (225,150) ke (225,333).
    d.arc([225 - 255, 150, 225 + 255, 333], start=-90, end=90,
          fill=BROWN, width=w)
    # Mangkuk B: dua setengah elips kanan bertumpuk.
    w2 = 42
    d.arc([225 - 175, 333, 225 + 175, 424], start=-90, end=90,
          fill=BROWN, width=w2)
    d.arc([225 - 175, 424, 225 + 175, 515], start=-90, end=90,
          fill=BROWN, width=w2)
    # Aksen teal: titik kunci di dalam mangkuk D.
    d.ellipse([300, 216, 350, 266], fill=TEAL)
    return img


def db2():
    """Lencana: papan teal, DB krem, kunci kecil krem di bawah."""
    img = Image.new("RGB", (SIZE, SIZE), CREAM)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([70, 70, 596, 596], radius=110, fill=TEAL)
    centered_text(d, 130, STORE_TEXT, CREAM, 235)
    key_horizontal(d, 248, 460, CREAM, scale=0.70)
    return img


def db3():
    """Cap DB: ring ganda coklat, huruf DB, kunci teal di bawah."""
    img = Image.new("RGB", (SIZE, SIZE), CREAM)
    d = ImageDraw.Draw(img)
    d.ellipse([43, 43, 623, 623], outline=BROWN, width=14)
    d.ellipse([75, 75, 591, 591], outline=BROWN, width=6)
    centered_text(d, 140, STORE_TEXT, BROWN, 225)
    key_horizontal(d, 248, 455, TEAL, scale=0.70)
    return img


def ascii_preview(img, width=56):
    small = img.resize((width, width * img.height // img.width)).convert("L")
    ramp = " .:-=+*#%@"
    data = list(small.get_flattened_data())
    return "\n".join(
        "".join(ramp[p * len(ramp) // 256] for p in data[y * width:(y + 1) * width])
        for y in range(small.height)
    )


def phil1():
    """Satu Tiang: D+B berbagi satu tiang (kasir penanggung jawab).
    Mangkuk D coklat = nota; mangkuk B teal = arus kembali (retur/refund);
    titik teal = identitas PIN, awal semua aksi."""
    img = Image.new("RGB", (SIZE, SIZE), CREAM)
    d = ImageDraw.Draw(img)
    w = 50
    d.line([225, 150, 225, 516], fill=BROWN, width=w)
    d.ellipse([200, 125, 250, 175], fill=BROWN)
    d.ellipse([200, 491, 250, 541], fill=BROWN)
    d.arc([225 - 255, 150, 225 + 255, 333], start=-90, end=90,
          fill=BROWN, width=w)
    w2 = 42
    d.arc([225 - 175, 333, 225 + 175, 424], start=-90, end=90,
          fill=TEAL, width=w2)
    d.arc([225 - 175, 424, 225 + 175, 515], start=-90, end=90,
          fill=TEAL, width=w2)
    d.ellipse([300, 216, 350, 266], fill=TEAL)
    return img


def _ring_tick(d, cx, cy, r_out, length, angle_deg, color, width):
    a = math.radians(angle_deg)
    r_in = r_out - length
    d.line([cx + r_in * math.cos(a), cy + r_in * math.sin(a),
            cx + r_out * math.cos(a), cy + r_out * math.sin(a)],
           fill=color, width=width)


def phil2():
    """Lingkaran Kepercayaan: ring = kepercayaan master+toko; tiga torehan
    = void/retur/refund yang append-only; DB dijaga di pusat."""
    img = Image.new("RGB", (SIZE, SIZE), CREAM)
    d = ImageDraw.Draw(img)
    d.ellipse([43, 43, 623, 623], outline=BROWN, width=14)
    d.ellipse([75, 75, 591, 591], outline=BROWN, width=6)
    _ring_tick(d, 333, 333, 274, 52, -55, BROWN, 22)
    _ring_tick(d, 333, 333, 274, 52, -30, TEAL, 22)
    _ring_tick(d, 333, 333, 274, 52, -5, BROWN, 22)
    centered_text(d, 165, STORE_TEXT, BROWN, 200)
    key_horizontal(d, 265, 452, TEAL, scale=0.60)
    return img


def phil3():
    """Dua Arus: D kerangka (barang fisik) vs B solid (nilai uang); tiap
    nota mempertemukan keduanya; kunci = arus yang dikunci identitas."""
    img = Image.new("RGB", (SIZE, SIZE), CREAM)
    d = ImageDraw.Draw(img)
    w = 50
    # Mangkuk B solid dulu, tiang digambar di atasnya supaya menyatu.
    d.pieslice([225 - 175, 333, 225 + 175, 424], start=-90, end=90, fill=TEAL)
    d.pieslice([225 - 175, 424, 225 + 175, 515], start=-90, end=90, fill=TEAL)
    d.line([225, 150, 225, 516], fill=BROWN, width=w)
    d.ellipse([200, 125, 250, 175], fill=BROWN)
    d.ellipse([200, 491, 250, 541], fill=BROWN)
    d.arc([225 - 255, 150, 225 + 255, 333], start=-90, end=90,
          fill=BROWN, width=w)
    key_horizontal(d, 270, 575, BROWN, scale=0.55)
    return img


def tb1():
    """Pintu & Gembok: D = daun pintu (busur + palang Z + lubang kunci),
    B = gembok yang tergantung di tiang (behel + badan + lubang kunci),
    tiang bersama = toko. Kasir = penjaga ambang."""
    img = Image.new("RGB", (SIZE, SIZE), CREAM)
    d = ImageDraw.Draw(img)
    w = 50
    # Tiang.
    d.line([225, 140, 225, 540], fill=BROWN, width=w)
    d.ellipse([200, 115, 250, 165], fill=BROWN)
    d.ellipse([200, 515, 250, 565], fill=BROWN)
    # Daun pintu: busur + palang diagonal + lubang kunci teal.
    d.arc([225 - 255, 140, 225 + 255, 323], start=-90, end=90,
          fill=BROWN, width=w)
    d.line([225, 155, 465, 308], fill=BROWN, width=30)
    d.ellipse([309, 199, 361, 251], fill=TEAL)
    d.polygon([(319, 245), (351, 245), (361, 285), (309, 285)], fill=TEAL)
    # Gembok: behel setengah lingkaran + badan + lubang kunci krem.
    d.arc([225, 360, 400, 478], start=180, end=360, fill=TEAL, width=36)
    d.line([400, 419, 400, 470], fill=TEAL, width=36)
    d.rounded_rectangle([252, 460, 408, 556], radius=30, fill=TEAL)
    d.ellipse([315, 477, 345, 507], fill=CREAM)
    d.rectangle([323, 495, 337, 526], fill=CREAM)
    return img


def tb2():
    """Fondasi: DB di atas tiga lapis undakan (coklat-teal-coklat).
    Toko bangunan = fondasi; POS = fondasi pencatatan; lapis = nota,
    event audit, rekap — makin ke bawah makin lebar, makin stabil."""
    img = Image.new("RGB", (SIZE, SIZE), CREAM)
    d = ImageDraw.Draw(img)
    centered_text(d, 140, STORE_TEXT, BROWN, 215)
    d.rectangle([120, 405, 546, 437], fill=BROWN)
    d.rectangle([165, 449, 501, 481], fill=TEAL)
    d.rectangle([210, 493, 456, 525], fill=BROWN)
    return img


def tb3():
    """Atap & Ambang: segitiga pelana = struktur paling kaku (pembukuan
    yang tidak goyah) + naungan kepercayaan; palang teal = ambang pintu,
    kasir sebagai ambang tiap transaksi keluar-masuk."""
    img = Image.new("RGB", (SIZE, SIZE), CREAM)
    d = ImageDraw.Draw(img)
    w = 26
    d.line([100, 320, 333, 130], fill=BROWN, width=w)
    d.line([333, 130, 566, 320], fill=BROWN, width=w)
    d.line([140, 320, 526, 320], fill=BROWN, width=w)
    centered_text(d, 350, STORE_TEXT, BROWN, 175)
    d.rectangle([190, 505, 476, 533], fill=TEAL)
    return img


def kl1():
    """Stoa Bertiang: D+B sebagai pilaster beralur di bawah balok
    arkitraf dengan ritme triglif (coklat-teal). Keteguhan prinsip:
    kejujuran timbangan dan nota."""
    img = Image.new("RGB", (SIZE, SIZE), CREAM)
    d = ImageDraw.Draw(img)
    w = 54
    # Balok + triglif.
    d.rectangle([110, 84, 556, 134], fill=BROWN)
    for j, x in enumerate((130, 219, 308, 397, 462)):
        d.rectangle([x, 134, x + 64, 170],
                    fill=BROWN if j % 2 == 0 else TEAL)
    # Tiang beralur.
    d.line([225, 170, 225, 500], fill=BROWN, width=w)
    d.ellipse([198, 143, 252, 197], fill=BROWN)
    d.line([214, 185, 214, 485], fill=CREAM, width=8)
    d.line([236, 185, 236, 485], fill=CREAM, width=8)
    d.arc([225 - 240, 170, 225 + 240, 335], start=-90, end=90,
          fill=BROWN, width=w)
    w2 = 40
    d.arc([225 - 165, 335, 225 + 165, 417], start=-90, end=90,
          fill=BROWN, width=w2)
    d.arc([225 - 165, 417, 225 + 165, 499], start=-90, end=90,
          fill=BROWN, width=w2)
    # Alas.
    d.rectangle([140, 512, 492, 544], fill=BROWN)
    return img


def kl2():
    """Benang Labirin: seluruh DB satu garis tak terputus (labirin
    Kreta itu unicursal — satu jalan tanpa cabang), seperti jejak audit
    yang append-only. Titik teal = pusat labirin, benang Ariadne."""
    img = Image.new("RGB", (SIZE, SIZE), CREAM)
    d = ImageDraw.Draw(img)
    pts = [(225, y) for y in range(540, 149, -12)]
    for deg in range(-90, 91, 6):
        a = math.radians(deg)
        pts.append((225 + 255 * math.cos(a), 241.5 + 91.5 * math.sin(a)))
    pts += [(225, y) for y in range(345, 425, 12)]
    for deg in range(-90, 91, 6):
        a = math.radians(deg)
        pts.append((225 + 175 * math.cos(a), 378.5 + 45.5 * math.sin(a)))
    for deg in range(-90, 91, 6):
        a = math.radians(deg)
        pts.append((225 + 175 * math.cos(a), 469.5 + 45.5 * math.sin(a)))
    pts = [(round(x), round(y)) for x, y in pts]
    d.line(pts, fill=BROWN, width=50, joint="curve")
    d.ellipse([200, 515, 250, 565], fill=BROWN)
    d.ellipse([370, 439, 430, 499], fill=TEAL)
    return img


def kl3():
    """Batu Penjuru: bingkai ganda + dentil + empat batu penjuru teal
    mengapit DB; kunci = permainan kata Yunani 'meander/kunci' sekaligus
    dagangan toko."""
    img = Image.new("RGB", (SIZE, SIZE), CREAM)
    d = ImageDraw.Draw(img)
    d.rectangle([90, 90, 576, 576], outline=BROWN, width=18)
    d.rectangle([122, 122, 544, 544], outline=BROWN, width=6)
    for cx, cy in ((122, 122), (498, 122), (122, 498), (498, 498)):
        d.rectangle([cx, cy, cx + 46, cy + 46], fill=TEAL)
    x = 196
    while x < 450:
        d.rectangle([x, 96, x + 18, 114], fill=BROWN)
        x += 34
    centered_text(d, 195, STORE_TEXT, BROWN, 215)
    key_horizontal(d, 262, 462, TEAL, scale=0.62)
    return img


def gr1():
    """Timbangan Kairos: tiang + balok + dua neraca di atas DB.
    Kairos = momen keputusan yang tepat; timbangan = kejujuran ukuran:
    harga di nota sama dengan harga yang dibayar, tak kurang tak lebih."""
    img = Image.new("RGB", (SIZE, SIZE), CREAM)
    d = ImageDraw.Draw(img)
    d.ellipse([311, 106, 355, 150], outline=BROWN, width=16)
    d.rectangle([320, 140, 346, 215], fill=BROWN)
    d.rectangle([213, 150, 453, 172], fill=BROWN)
    for cx in (233, 433):
        d.line([cx, 172, cx - 33, 235], fill=BROWN, width=8)
        d.line([cx, 172, cx + 33, 235], fill=BROWN, width=8)
        d.pieslice([cx - 50, 220, cx + 50, 270], start=0, end=180,
                   fill=TEAL)
    centered_text(d, 320, STORE_TEXT, BROWN, 190)
    return img


def gr2():
    """Techne Bersilang: palu dan kunci bersilang di bawah DB dalam
    lingkaran. Techne = kemahiran tangan; toko perkakas menjual
    perpanjangan tangan tukang, POS adalah perkakas kasir."""
    img = Image.new("RGB", (SIZE, SIZE), CREAM)
    d = ImageDraw.Draw(img)
    d.ellipse([43, 43, 623, 623], outline=BROWN, width=14)
    d.ellipse([75, 75, 591, 591], outline=BROWN, width=6)
    centered_text(d, 115, STORE_TEXT, BROWN, 165)
    # Palu diagonal: gagang + kepala tegak lurus.
    d.line([235, 345, 385, 495], fill=BROWN, width=24)
    hx, hy = 235, 345
    dx, dy = 0.7071, 0.7071
    nx, ny = -0.7071, 0.7071
    head = [(hx + dx * 65 + nx * 32, hy + dy * 65 + ny * 32),
            (hx + dx * 65 - nx * 32, hy + dy * 65 - ny * 32),
            (hx - dx * 65 - nx * 32, hy - dy * 65 - ny * 32),
            (hx - dx * 65 + nx * 32, hy - dy * 65 + ny * 32)]
    d.polygon(head, fill=BROWN)
    # Kunci diagonal berlawanan: ring + batang + dua gigi.
    d.ellipse([405, 295, 465, 355], outline=TEAL, width=20)
    d.line([415, 345, 295, 465], fill=TEAL, width=20)
    d.line([335, 425, 365, 395], fill=TEAL, width=20)
    d.line([305, 455, 335, 425], fill=TEAL, width=20)
    return img


def gr3():
    """Tungku Tempa: landasan pandai besi + percikan teal di bawah DB.
    Kunci dan gembok lahir dari tempa dan api; harga yang jujur ditempa
    dari catatan yang jujur — POS adalah tungkunya."""
    img = Image.new("RGB", (SIZE, SIZE), CREAM)
    d = ImageDraw.Draw(img)
    centered_text(d, 165, STORE_TEXT, BROWN, 150)
    for cx, cy, s in ((270, 140, 16), (396, 108, 20), (500, 170, 13)):
        d.polygon([(cx, cy - s), (cx + s, cy), (cx, cy + s), (cx - s, cy)],
                  fill=TEAL)
    d.rectangle([190, 400, 476, 440], fill=BROWN)
    d.polygon([(476, 400), (560, 420), (476, 440)], fill=BROWN)
    d.polygon([(270, 440), (396, 440), (370, 500), (296, 500)], fill=BROWN)
    d.rectangle([240, 500, 426, 528], fill=BROWN)
    return img


if __name__ == "__main__":
    for i, make in enumerate((phil1, phil2, phil3), start=1):
        img = make()
        path = os.path.join(HERE, f"logo-phil-{i}.jpeg")
        img.save(path, "JPEG", quality=92)

    for i, make in enumerate((tb1, tb2, tb3), start=1):
        img = make()
        path = os.path.join(HERE, f"logo-tb-{i}.jpeg")
        img.save(path, "JPEG", quality=92)

    for i, make in enumerate((kl1, kl2, kl3), start=1):
        img = make()
        path = os.path.join(HERE, f"logo-kl-{i}.jpeg")
        img.save(path, "JPEG", quality=92)

    for i, make in enumerate((gr1, gr2, gr3), start=1):
        img = make()
        path = os.path.join(HERE, f"logo-gr-{i}.jpeg")
        img.save(path, "JPEG", quality=92)
        print(f"== DB kandidat {i}: {path} {img.size}")
        print(ascii_preview(img))
        print()
