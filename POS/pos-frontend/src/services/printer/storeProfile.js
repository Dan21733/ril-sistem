/*
 * Identitas toko yang tercetak di kepala nota.
 *
 * Kenapa lewat env: nama toko, alamat, dan telepon berbeda per outlet dan tidak
 * boleh di-hardcode di dalam komponen. Variabel ini bukan rahasia — isinya
 * memang tampil di nota pelanggan — jadi prefix VITE_ memang tepat di sini
 * (berbeda dengan DEVICE_REGISTRATION_TOKEN yang harus tetap di sisi Node).
 *
 * Nilai kosong berarti barisnya tidak dicetak, bukan dicetak sebagai placeholder:
 * nota tanpa alamat lebih baik daripada nota bertuliskan "Alamat toko".
 */

const DEFAULTS = {
  name: 'TOKO',
  address: '',
  phone: '',
  /*
   * Kode toko hanya dipakai sebagai awalan nomor dokumen surat jalan
   * (formulir toko memakai "kode + cap waktu"). Dibiarkan kosong kalau belum
   * diisi: nomor dokumen tanpa awalan masih unik, sedangkan awalan karangan
   * akan tercetak sebagai nomor resmi yang salah.
   */
  code: '',
}

function readEnv(key) {
  try {
    const value = import.meta.env?.[key]

    return typeof value === 'string' ? value.trim() : ''
  } catch {
    /* Diuji lewat Node (bukan Vite): import.meta.env tidak ada. */
    return ''
  }
}

export function getStoreProfile() {
  return {
    name: readEnv('VITE_STORE_NAME') || DEFAULTS.name,
    address: readEnv('VITE_STORE_ADDRESS') || DEFAULTS.address,
    phone: readEnv('VITE_STORE_PHONE') || DEFAULTS.phone,
    code: readEnv('VITE_STORE_CODE') || DEFAULTS.code,
  }
}

export default {
  getStoreProfile,
}
