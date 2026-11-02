import db from './db'
import { postPiutangAction } from '../api/piutangApi'
import deviceRepository from './deviceRepository'

/*
 * Antrean aksi piutang (pelunasan / barang diambil) di perangkat.
 *
 * Aksi selalu ditulis ke antrean dulu, baru dikirim. Jawaban server yang pasti
 * (diterima / ditolak 400) menutup barisnya; gagal jaringan membiarkannya
 * `pending` untuk dicoba lagi. `aksi_id` sama dikirim ulang, jadi server tidak
 * mencatat dua kali.
 */

export async function enqueuePiutangAction(action) {
  const row = {
    ...action,
    status: 'pending',
    detail: null,
    attempts: 0,
    created_at: new Date().toISOString(),
  }

  await db.piutang_actions.put(row)
  return row
}

export async function listPendingPiutangActions() {
  return db.piutang_actions.where('status').equals('pending').sortBy('created_at')
}

export async function listRecentPiutangActions(limit = 50) {
  const rows = await db.piutang_actions.orderBy('created_at').reverse().limit(limit).toArray()
  return rows
}

let flushing = null

export function flushPiutangActions() {
  if (!flushing) {
    flushing = runFlush().finally(() => {
      flushing = null
    })
  }

  return flushing
}

async function runFlush() {
  const device = await deviceRepository.getDevice()

  if (!device?.device_token) {
    return { sent: 0, failed: 0, pending: (await listPendingPiutangActions()).length }
  }

  let sent = 0
  let failed = 0

  for (const row of await listPendingPiutangActions()) {
    try {
      await postPiutangAction(device.device_token, {
        pin: row.pin,
        aksi_id: row.aksi_id,
        jenis: row.jenis,
        kunci: row.kunci,
        jumlah: row.jumlah,
        cara: row.cara,
        catatan: row.catatan,
        waktu: row.created_at,
      })
      await db.piutang_actions.update(row.aksi_id, { status: 'sent', pin: null, detail: null })
      sent += 1
    } catch (error) {
      if (error.status === 400) {
        await db.piutang_actions.update(row.aksi_id, { status: 'rejected', pin: null, detail: error.message })
        failed += 1
      } else {
        await db.piutang_actions.update(row.aksi_id, { attempts: (row.attempts || 0) + 1, detail: error.message })
        /* Server tidak terjangkau: sisanya pasti gagal juga, coba lagi nanti. */
        break
      }
    }
  }

  return { sent, failed, pending: (await listPendingPiutangActions()).length }
}

export default { enqueuePiutangAction, listPendingPiutangActions, listRecentPiutangActions, flushPiutangActions }
