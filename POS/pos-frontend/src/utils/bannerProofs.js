/*
 * Baris bukti transfer yang boleh tampil di panel riwayat (permintaan pemilik,
 * 2026-10-11: peringatan bukti "tidak langsung hilang" setelah menekan "Kirim
 * semua"). Bukti yang notanya sedang dikirim tidak ditampilkan selama
 * pengiriman itu berjalan — pekerjaannya sudah dimulai, dan barisnya kembali
 * sendiri kalau ternyata gagal, karena daftarnya dibaca ulang begitu
 * pengiriman selesai.
 *
 * Aturannya sengaja sempit — hanya bukti yang `sync_id`-nya ada di antrean yang
 * sedang dikirim, bukan semua bukti:
 * - bukti yang menunggu notanya (kasus yang dilaporkan) ikut hilang karena
 *   notanya memang sedang naik ke server;
 * - bukti milik nota yang sudah ada di server tidak ikut hilang oleh tombol
 *   ini: mengirim antrean tidak menyentuh unggahan yang sudah jalan sendiri;
 * - `blocked`/`conflict`/`captured` tetap tampil, karena yang bisa
 *   membereskannya manusia, bukan pengiriman ulang.
 *
 * Fungsi murni supaya perilaku "hilang seketika, kembali kalau gagal" bisa
 * diuji tanpa memasang komponen.
 */
export function visibleBannerProofs(proofs, { batchSending = false, queuedSyncIds = null } = {}) {
  const list = Array.isArray(proofs) ? proofs : []

  if (!batchSending) {
    return list
  }

  const queued = queuedSyncIds instanceof Set ? queuedSyncIds : new Set(queuedSyncIds || [])

  return list.filter((proof) => !(proof?.sync_id && queued.has(proof.sync_id)))
}

export default { visibleBannerProofs }
