import db from './db'
import { postKasGerak } from '../api/kasApi'
import deviceRepository from './deviceRepository'

/*
 * Antrean uang keluar / tambah modal di perangkat (kas, 2026-11-03).
 *
 * Uang keluar wajib dicatat saat kejadian, juga saat server tidak terjangkau:
 * catatan ditulis ke antrean dulu, baru dikirim. Jawaban pasti (diterima /
 * ditolak 400) menutup barisnya; gagal jaringan membiarkannya `pending`.
 * `gerak_id` sama dikirim ulang, jadi server tidak mencatat dua kali.
 */

export async function enqueueKasGerak(gerak) {
  const row = {
    ...gerak,
    status: 'pending',
    detail: null,
    attempts: 0,
    created_at: new Date().toISOString(),
  }

  await db.kas_gerak.put(row)
  return row
}

export async function listPendingKasGerak() {
  return db.kas_gerak.where('status').equals('pending').sortBy('created_at')
}

export async function listRecentKasGerak(limit = 30) {
  return db.kas_gerak.orderBy('created_at').reverse().limit(limit).toArray()
}

let flushing = null

export function flushKasGerak() {
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
    return { sent: 0, failed: 0, pending: (await listPendingKasGerak()).length }
  }

  let sent = 0
  let failed = 0

  for (const row of await listPendingKasGerak()) {
    try {
      await postKasGerak(device.device_token, {
        pin: row.pin,
        gerak_id: row.gerak_id,
        jenis: row.jenis,
        jumlah: row.jumlah,
        kategori: row.kategori,
        keterangan: row.keterangan,
        waktu: row.created_at,
      })
      await db.kas_gerak.update(row.gerak_id, { status: 'sent', pin: null, detail: null })
      sent += 1
    } catch (error) {
      if (error.status === 400) {
        await db.kas_gerak.update(row.gerak_id, { status: 'rejected', pin: null, detail: error.message })
        failed += 1
      } else {
        await db.kas_gerak.update(row.gerak_id, { attempts: (row.attempts || 0) + 1, detail: error.message })
        break
      }
    }
  }

  return { sent, failed, pending: (await listPendingKasGerak()).length }
}

export default { enqueueKasGerak, listPendingKasGerak, listRecentKasGerak, flushKasGerak }
