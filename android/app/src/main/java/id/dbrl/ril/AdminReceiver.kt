package id.dbrl.ril

import android.app.admin.DeviceAdminReceiver
import android.content.Context
import android.content.Intent

/** Penerima device admin. Dijadikan device owner sekali lewat `adb shell dpm set-device-owner`. */
class AdminReceiver : DeviceAdminReceiver() {
    override fun onEnabled(context: Context, intent: Intent) {
        Kebijakan.terapkan(context)
    }
}
