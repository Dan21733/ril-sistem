import assert from 'node:assert/strict'
import test from 'node:test'

import { shouldCloseCheckoutModal } from '../src/utils/checkoutModal.js'
import modalFocus from '../src/directives/modalFocus.js'

/*
 * Permintaan pemilik 2026-10-09: setelah SELESAIKAN TRANSAKSI modal harus keluar
 * sendiri. Kasus yang paling mudah rusak adalah jalur offline — nota masuk
 * antrean, induk mengembalikan status ke `idle` — dan justru itu yang dulu
 * meninggalkan modal terbuka di langkah PIN.
 */

function focusHarness() {
  const listeners = new Map()
  const body = { children: [], parentElement: null }
  const background = { inert: false }
  const trigger = {
    isConnected: true, disabled: false, tabIndex: 0, closest: () => null,
    getClientRects: () => [{}],
    focus() { document.activeElement = this },
  }
  const dialog = { focus() { document.activeElement = this } }
  const control = () => ({
    disabled: false, tabIndex: 0, closest: () => null,
    getClientRects: () => [{}], focus() { document.activeElement = this },
  })
  const first = control()
  const last = control()
  const root = {
    parentElement: body,
    querySelector: () => dialog,
    querySelectorAll: () => [first, last],
    contains: e => [dialog, first, last].includes(e),
  }
  body.children = [background, root]
  const document = {
    activeElement: trigger,
    addEventListener: (name, listener) => listeners.set(name, listener),
    removeEventListener: name => listeners.delete(name),
    querySelector: () => first,
  }
  const key = (value, shiftKey = false) => {
    const event = { key: value, shiftKey, prevented: false,
      preventDefault() { this.prevented = true }, stopPropagation() {} }
    listeners.get('keydown')(event)
    return event
  }
  return { document, root, dialog, first, last, trigger, background, listeners, key }
}

test('dialog contains Tab focus, handles guarded Escape and restores its trigger', () => {
  const h = focusHarness()
  const oldDocument = globalThis.document
  const oldStyle = globalThis.getComputedStyle
  globalThis.document = h.document
  globalThis.getComputedStyle = () => ({ visibility: 'visible' })
  let dismissals = 0
  let submitting = true
  const options = { onEscape: () => { if (!submitting) { dismissals += 1 } }, step: 1 }
  try {
    modalFocus.mounted(h.root, { value: options })
    assert.equal(h.document.activeElement, h.dialog)
    assert.equal(h.background.inert, true)
    h.key('Tab', true)
    assert.equal(h.document.activeElement, h.last)
    h.key('Tab')
    assert.equal(h.document.activeElement, h.first)
    h.key('Tab', true)
    assert.equal(h.document.activeElement, h.last)
    h.key('Escape')
    assert.equal(dismissals, 0)
    submitting = false
    h.key('Escape')
    assert.equal(dismissals, 1)
    modalFocus.updated(h.root, { value: { ...options, step: 2 } })
    assert.equal(h.document.activeElement, h.dialog)
    modalFocus.beforeUnmount(h.root)
    assert.equal(h.background.inert, false)
    assert.equal(h.document.activeElement, h.trigger)
    assert.equal(h.listeners.size, 0)
  } finally {
    globalThis.document = oldDocument
    globalThis.getComputedStyle = oldStyle
  }
})

test('dialog with no enabled controls keeps focus and preserves prior inert state', () => {
  const h = focusHarness()
  const oldDocument = globalThis.document
  const oldStyle = globalThis.getComputedStyle
  globalThis.document = h.document
  globalThis.getComputedStyle = () => ({ visibility: 'visible' })
  h.root.querySelectorAll = () => []
  h.background.inert = true
  try {
    modalFocus.mounted(h.root, { value: {} })
    h.document.activeElement = h.trigger
    assert.equal(h.key('Tab').prevented, true)
    assert.equal(h.document.activeElement, h.dialog)
    modalFocus.beforeUnmount(h.root)
    assert.equal(h.background.inert, true)
  } finally {
    globalThis.document = oldDocument
    globalThis.getComputedStyle = oldStyle
  }
})

test('dialog opened on startup or with an unavailable trigger returns focus to search', () => {
  const oldDocument = globalThis.document
  const oldStyle = globalThis.getComputedStyle
  globalThis.getComputedStyle = () => ({ visibility: 'visible' })
  try {
    for (const unavailable of [
      { tabIndex: -1 }, // BODY is active when startup registration opens.
      { disabled: true },
      { isConnected: false },
      { getClientRects: () => [] },
    ]) {
      const h = focusHarness()
      Object.assign(h.trigger, unavailable)
      globalThis.document = h.document
      modalFocus.mounted(h.root, { value: {} })
      modalFocus.beforeUnmount(h.root)
      assert.equal(h.document.activeElement, h.first)
      assert.equal(h.background.inert, false)
      assert.equal(h.listeners.size, 0)
    }
  } finally {
    globalThis.document = oldDocument
    globalThis.getComputedStyle = oldStyle
  }
})

test('status terminal menutup modal dari keadaan apa pun', () => {
  const closing = ['success', 'retryable', 'rate_limited', 'review', 'conflict', 'restoring']

  for (const status of closing) {
    assert.equal(shouldCloseCheckoutModal(status, ''), true, `${status} harus menutup`)
    assert.equal(shouldCloseCheckoutModal(status, 'submitting'), true, `${status} harus menutup`)
  }
})

test('nota offline yang masuk antrean menutup modal', () => {
  /* `submitting` -> `idle` adalah jalur offline: App.vue melepas keranjang
     untuk penjualan berikutnya lalu mengembalikan status ke `idle`. */
  assert.equal(shouldCloseCheckoutModal('idle', 'submitting'), true)
  assert.equal(shouldCloseCheckoutModal('idle', 'restoring'), true)
})

test('idle di luar checkout tidak menutup apa pun', () => {
  /* Modal dibuka saat status masih `idle` (checkout lama yang `success`/`error`
     ditutup tepat sebelumnya), jadi `idle` tanpa checkout berjalan bukan sinyal
     untuk menutup. */
  for (const previous of ['', 'idle', 'success', 'error', 'pin_cooldown']) {
    assert.equal(shouldCloseCheckoutModal('idle', previous), false, `idle dari ${previous} tidak menutup`)
  }
})

test('status yang masih berjalan atau butuh pesan tidak menutup modal', () => {
  for (const status of ['submitting', 'pin_cooldown', 'error', 'pending', 'unknown', 'syncing']) {
    assert.equal(shouldCloseCheckoutModal(status, 'submitting'), false, `${status} tidak menutup`)
  }
})
