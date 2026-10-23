import db from './db'

export class SequenceRangeExhaustedError extends Error {
  constructor(deviceId) {
    super(`No active sequence remains for device: ${deviceId}`)
    this.name = 'SequenceRangeExhaustedError'
    this.deviceId = deviceId
  }
}

export async function getActiveRange(deviceId) {
  return db.sequence_ranges.get(deviceId)
}

export async function saveActiveRange(range) {
  return db.sequence_ranges.put({ ...range })
}

export async function clearActiveRange(deviceId) {
  return db.sequence_ranges.delete(deviceId)
}

export async function consumeNextSequence(deviceId) {
  return db.transaction('rw', db.sequence_ranges, async () => {
    const range = await db.sequence_ranges.get(deviceId)

    if (!range || range.next_sequence > range.end_sequence) {
      throw new SequenceRangeExhaustedError(deviceId)
    }

    const consumedSequence = range.next_sequence

    await db.sequence_ranges.put({
      ...range,
      next_sequence: consumedSequence + 1,
    })

    return consumedSequence
  })
}

export default {
  getActiveRange,
  saveActiveRange,
  clearActiveRange,
  consumeNextSequence,
}
