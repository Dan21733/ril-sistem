/*
 * Kolom uang di layar kasir: tampil bertitik ribuan, disimpan digit polos.
 *
 * Kenapa ada: kolom uang paling mudah salah baca kalau ditulis sebagai digit
 * polos. "70000" dan "700000" hanya beda satu angka nol, dan itu uang —
 * apalagi di tablet tempat kasir mengetik sambil melayani pelanggan. Karena itu
 * yang ditampilkan ke kasir selalu bertitik ribuan (`70.000`), sementara nilai
 * yang disimpan di state dan dikirim ke server tetap digit polos supaya payload
 * JSON tidak pernah berisi `"70.000"`, yang bukan angka.
 *
 * Dipakai kolom uang yang sama artinya di keranjang (uang diterima, ongkir)
 * dan di form retur/refund (jumlah uang kembali), supaya ketiganya berperilaku
 * identik: mengetik huruf tidak mengubah apa pun, menempel teks berbentuk
 * "Rp 70.000" tetap terbaca 70000.
 */

/* Digit saja, apa pun bentuk masukannya. */
export function digitsOnly(value) {
  return String(value ?? '').replace(/\D/g, '')
}

/*
 * Nilai siap tampil di `<input>`: digit dipisah titik per ribuan.
 * Kosong tetap kosong supaya placeholder (`0`) yang muncul, bukan "0".
 */
export function formatRupiahInput(value) {
  const digits = digitsOnly(value)

  if (!digits) {
    return ''
  }

  return new Intl.NumberFormat('id-ID').format(Number(digits))
}

/*
 * Tombol yang tidak memasukkan karakter tetapi tidak boleh diblokir saat
 * membersihkan input (hapus, geser kursor, tab).
 */
export const NON_CHARACTER_KEYS = [
  'Backspace',
  'Delete',
  'ArrowLeft',
  'ArrowRight',
  'ArrowUp',
  'ArrowDown',
  'Tab',
  'Home',
  'End',
  'Enter',
]

/*
 * Penjaga `keydown` supaya huruf tidak pernah muncul sebentar di kolom uang.
 * Pembersihan sebenarnya tetap dilakukan `digitsOnly` pada `input`
 * (tempel/autofill tidak lewat keydown), ini hanya menghindari kedipan.
 */
export function preventNonNumericInput(event) {
  if (NON_CHARACTER_KEYS.includes(event.key) || /^\d$/.test(event.key)) {
    return
  }

  event.preventDefault()
}

export default {
  digitsOnly,
  formatRupiahInput,
  preventNonNumericInput,
  NON_CHARACTER_KEYS,
}
