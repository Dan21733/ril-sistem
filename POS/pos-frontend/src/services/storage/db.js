import Dexie from 'dexie'

export const db = new Dexie('POSDatabase')

db.version(1).stores({
  device: 'id',
  sequence_ranges: 'device_id',
  items: '[item_name+variant]',
  transactions: 'sync_id, sync_status, cashier_name_normalized, transaction_id',
  payment_proofs: 'proof_id, sync_id',
})

/*
 * Versi 2 menambah indeks status unggah bukti transfer.
 *
 * Antrean bukti dicari lewat `upload_status` tanpa memindai seluruh tabel, dan
 * pasangan `[upload_status+captured_at]` memberi urutan "yang tertua lebih dulu"
 * tanpa pengurutan manual (PAYMENT_PROOF_DESIGN.md bagian 7.6). Ini perubahan
 * skema klien saja: tabel `payment_proof` di server tidak tersentuh, dan tabel
 * `payment_proofs` versi 1 selalu kosong sehingga tidak butuh fungsi upgrade.
 */
db.version(2).stores({
  device: 'id',
  sequence_ranges: 'device_id',
  items: '[item_name+variant]',
  transactions: 'sync_id, sync_status, cashier_name_normalized, transaction_id',
  payment_proofs: 'proof_id, sync_id, upload_status, [upload_status+captured_at]',
})

/*
 * Versi 3 menambah antrean cetak nota (PRINT_PATH_DESIGN.md §5).
 *
 * Kenapa di Dexie, bukan di memori: cetak adalah upaya terbaik dan tidak boleh
 * membatalkan nota, jadi job yang belum keluar harus bertahan sampai printer
 * hidup lagi — termasuk kalau tablet atau aplikasinya ditutup di tengah jalan.
 * Byte ESC/POS ikut disimpan supaya percobaan ulang mengirim isi yang sama
 * persis; bridge mengenali kiriman kembar dari `job_id`, bukan dari isinya.
 */
db.version(3).stores({
  device: 'id',
  sequence_ranges: 'device_id',
  items: '[item_name+variant]',
  transactions: 'sync_id, sync_status, cashier_name_normalized, transaction_id',
  payment_proofs: 'proof_id, sync_id, upload_status, [upload_status+captured_at]',
  print_jobs: 'job_id, sync_id, status, [status+created_at]',
})

/*
 * Versi 4 menambah direktori kasir untuk pencocokan PIN di perangkat
 * (KEPUTUSAN-9/10, OFFLINE_SYNC_DESIGN.md §7).
 *
 * Namanya bukan "pin": yang disimpan adalah nilai PIN **tersimpan** apa adanya
 * dari tabel `worker` di server — di tabel itu bentuknya hash bcrypt. Kunci
 * utamanya auto-increment karena nama kasir belum tentu unik, dan daftarnya
 * diganti utuh setiap penyegaran sehingga tidak ada rujukan antar-baris.
 * Indeks `fetched_at` hanya untuk mengetahui kapan terakhir daftar disegarkan.
 */
db.version(4).stores({
  device: 'id',
  sequence_ranges: 'device_id',
  items: '[item_name+variant]',
  transactions: 'sync_id, sync_status, cashier_name_normalized, transaction_id',
  payment_proofs: 'proof_id, sync_id, upload_status, [upload_status+captured_at]',
  print_jobs: 'job_id, sync_id, status, [status+created_at]',
  workers: '++id, pin_format, fetched_at',
})

/*
 * Versi 5 menambah penghitung percobaan PIN di perangkat (keputusan pemilik
 * 2026-10-08, OFFLINE_SYNC_DESIGN.md §3).
 *
 * Yang disimpan hanya jumlah kegagalan dan waktu blokir — bukan PIN, potongan,
 * atau hash-nya. Hash dari ruang enam digit bisa dibalik, dan berkas seperti
 * itu justru mempermudah penebakan; alasan lengkapnya ada di
 * `src/utils/pinCooldown.js`. Satu baris per perangkat (`scope` = 'device').
 */
db.version(5).stores({
  device: 'id',
  sequence_ranges: 'device_id',
  items: '[item_name+variant]',
  transactions: 'sync_id, sync_status, cashier_name_normalized, transaction_id',
  payment_proofs: 'proof_id, sync_id, upload_status, [upload_status+captured_at]',
  print_jobs: 'job_id, sync_id, status, [status+created_at]',
  workers: '++id, pin_format, fetched_at',
  pin_attempts: 'scope',
})

/*
 * Versi 6 menambah cap waktu salinan katalog (keputusan pemilik 2026-10-08,
 * OFFLINE_SYNC_DESIGN.md §3.3).
 *
 * Tabel `items` sudah ada sejak versi 1, tetapi belum pernah diisi jalur
 * mana pun; yang baru di sini hanya meta satu baris (`key` = 'catalog') berisi
 * waktu sinkron terakhir dan jumlah barisnya, ditulis dalam transaksi yang
 * sama dengan itemnya supaya cap waktu tidak pernah lebih baru dari isinya.
 */
db.version(6).stores({
  device: 'id',
  sequence_ranges: 'device_id',
  items: '[item_name+variant]',
  transactions: 'sync_id, sync_status, cashier_name_normalized, transaction_id',
  payment_proofs: 'proof_id, sync_id, upload_status, [upload_status+captured_at]',
  print_jobs: 'job_id, sync_id, status, [status+created_at]',
  workers: '++id, pin_format, fetched_at',
  pin_attempts: 'scope',
  catalog_meta: 'key',
})

export default db
