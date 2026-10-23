/*
 * UUID v4 buatan sendiri, bukan `crypto.randomUUID`.
 *
 * POS dibuka dari `http://<IP-LAN>` di tablet kasir, dan `crypto.randomUUID`
 * maupun `crypto.subtle` hanya tersedia pada secure context (https atau
 * localhost). `crypto.getRandomValues` tersedia di semua context, jadi itu yang
 * dipakai. Modul ini dipakai bersama oleh `sync_id` nota (App.vue) dan
 * `proof_id` bukti transfer, supaya keduanya punya syarat yang sama.
 */
export function createUuid() {
  if (!globalThis.crypto?.getRandomValues) {
    throw new Error('Perangkat ini tidak dapat membuat ID transaksi yang aman.')
  }

  const bytes = new Uint8Array(16)

  globalThis.crypto.getRandomValues(bytes)

  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80

  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')

  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export default {
  createUuid,
}
