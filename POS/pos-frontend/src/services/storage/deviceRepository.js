import db from './db'

const ACTIVE_DEVICE_ID = 1

export async function getDevice() {
  return db.device.get(ACTIVE_DEVICE_ID)
}

export async function saveDevice(device) {
  return db.device.put({
    ...device,
    id: ACTIVE_DEVICE_ID,
  })
}

export async function clearDevice() {
  return db.device.delete(ACTIVE_DEVICE_ID)
}

export default {
  getDevice,
  saveDevice,
  clearDevice,
}
