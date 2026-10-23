import assert from 'node:assert/strict'
import test from 'node:test'
import { webcrypto } from 'node:crypto'

import { saveSealKey, sealApproverPin } from '../src/services/workers/pinSeal.js'

function memoryStorage() {
  const data = new Map()
  return { getItem: (k) => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)) }
}

test('PIN penyetuju dikunci dengan kunci publik server dan hanya terbuka dengan kunci privat', async () => {
  const subtle = webcrypto.subtle
  const pair = await subtle.generateKey(
    { name: 'RSA-OAEP', modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' },
    true, ['encrypt', 'decrypt'],
  )
  const spki = Buffer.from(await subtle.exportKey('spki', pair.publicKey)).toString('base64')
  const storage = memoryStorage()
  saveSealKey(spki, storage)

  const sealed = await sealApproverPin('730264', { storage, subtle })
  assert.ok(sealed.startsWith('rsa1:'))
  assert.ok(!sealed.includes('730264'))
  const opened = await subtle.decrypt({ name: 'RSA-OAEP' }, pair.privateKey, Buffer.from(sealed.slice(5), 'base64'))
  assert.equal(new TextDecoder().decode(opened), '730264')
})

test('tanpa kunci atau WebCrypto: null, pemanggil memakai teks biasa', async () => {
  assert.equal(await sealApproverPin('730264', { storage: memoryStorage(), subtle: webcrypto.subtle }), null)
  const storage = memoryStorage()
  saveSealKey('bukan-kunci', storage)
  assert.equal(await sealApproverPin('730264', { storage, subtle: webcrypto.subtle }), null)
  assert.equal(await sealApproverPin('730264', { storage, subtle: undefined }), null)
})
