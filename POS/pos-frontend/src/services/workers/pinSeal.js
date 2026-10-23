/*
 * Mengunci PIN penyetuju di antrean tablet (QA 18 Okt 2026).
 *
 * Nota offline yang disetujui di bawah floor membawa PIN penyetuju sampai
 * terkirim. Supaya PIN itu tidak tersimpan sebagai teks biasa di tablet,
 * tablet menguncinya dengan kunci publik server (RSA-OAEP SHA-256) dan hanya
 * server yang bisa membukanya (pos-backend/pos/kunci_pin.py).
 *
 * Kunci publik ikut di jawaban daftar kasir offline dan disimpan di
 * localStorage. Butuh WebCrypto (halaman HTTPS / secure context). Kalau kunci
 * atau WebCrypto tidak ada, `sealApproverPin` mengembalikan null dan pemanggil
 * memakai teks biasa seperti dulu, supaya nota tidak pernah gagal dibuat.
 */
const STORAGE_KEY = 'pos.kunci_pin'
const PREFIX = 'rsa1:'

function defaultStorage() {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

export function saveSealKey(spkiBase64, storage = defaultStorage()) {
  if (!spkiBase64 || !storage) {
    return
  }
  try {
    storage.setItem(STORAGE_KEY, String(spkiBase64))
  } catch {
    /* Penyimpanan penuh/diblokir: tetap jalan dengan teks biasa. */
  }
}

function readSealKey(storage) {
  try {
    return storage?.getItem(STORAGE_KEY) || null
  } catch {
    return null
  }
}

function toBytes(base64) {
  const text = atob(base64)
  const bytes = new Uint8Array(text.length)
  for (let i = 0; i < text.length; i += 1) {
    bytes[i] = text.charCodeAt(i)
  }
  return bytes
}

function toBase64(buffer) {
  const bytes = new Uint8Array(buffer)
  let text = ''
  for (let i = 0; i < bytes.length; i += 1) {
    text += String.fromCharCode(bytes[i])
  }
  return btoa(text)
}

export async function sealApproverPin(pin, { storage = defaultStorage(), subtle = globalThis.crypto?.subtle } = {}) {
  const key = readSealKey(storage)
  if (!pin || !key || !subtle) {
    return null
  }
  try {
    const publicKey = await subtle.importKey(
      'spki', toBytes(key), { name: 'RSA-OAEP', hash: 'SHA-256' }, false, ['encrypt'],
    )
    const sealed = await subtle.encrypt({ name: 'RSA-OAEP' }, publicKey, new TextEncoder().encode(String(pin)))
    return PREFIX + toBase64(sealed)
  } catch {
    return null
  }
}

export default { saveSealKey, sealApproverPin }
