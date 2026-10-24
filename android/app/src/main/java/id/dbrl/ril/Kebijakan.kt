package id.dbrl.ril

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.os.Build
import android.os.UserManager

/**
 * Kebijakan device owner. Hanya berlaku kalau Ril sudah dijadikan device owner.
 *
 * - USB transfer file, opsi pengembang/USB debugging, pasang APK dari luar, reset pabrik,
 *   safe mode, kartu SD, dan tambah pengguna dimatikan.
 * - Ril jadi layar utama dan satu-satunya aplikasi yang bisa dibuka (lock task), selain Tailscale.
 * - Tailscale dipaksa selalu menyala (always-on VPN).
 *
 * "Mode perawatan" mencabut semua batasan sementara (mis. untuk memasang update lewat kabel),
 * lalu "Kunci lagi" memasangnya kembali.
 */
object Kebijakan {
    private val BATASAN = listOf(
        UserManager.DISALLOW_USB_FILE_TRANSFER,
        UserManager.DISALLOW_DEBUGGING_FEATURES,
        UserManager.DISALLOW_INSTALL_UNKNOWN_SOURCES,
        UserManager.DISALLOW_FACTORY_RESET,
        UserManager.DISALLOW_SAFE_BOOT,
        UserManager.DISALLOW_MOUNT_PHYSICAL_MEDIA,
        UserManager.DISALLOW_ADD_USER,
    )

    fun admin(c: Context) = ComponentName(c, AdminReceiver::class.java)
    fun dpm(c: Context) = c.getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
    fun pemilik(c: Context) = dpm(c).isDeviceOwnerApp(c.packageName)

    fun terpasang(c: Context, paket: String) = try {
        c.packageManager.getApplicationInfo(paket, 0); true
    } catch (e: PackageManager.NameNotFoundException) {
        false
    }

    /** Pasang semua kebijakan. Aman dipanggil berulang. */
    fun terapkan(c: Context) {
        if (!pemilik(c)) return
        val d = dpm(c)
        val a = admin(c)
        BATASAN.forEach { d.addUserRestriction(a, it) }

        val boleh = mutableListOf(c.packageName)
        if (terpasang(c, Setelan.PAKET_TAILSCALE)) boleh += Setelan.PAKET_TAILSCALE
        d.setLockTaskPackages(a, boleh.toTypedArray())
        if (Build.VERSION.SDK_INT >= 28) {
            // Status bar hanya menampilkan jam, baterai, dan sinyal; notifikasi dan panel cepat terkunci.
            d.setLockTaskFeatures(a, DevicePolicyManager.LOCK_TASK_FEATURE_SYSTEM_INFO)
        }

        val home = IntentFilter(Intent.ACTION_MAIN).apply {
            addCategory(Intent.CATEGORY_HOME)
            addCategory(Intent.CATEGORY_DEFAULT)
        }
        d.addPersistentPreferredActivity(a, home, ComponentName(c, MainActivity::class.java))
        d.setKeyguardDisabled(a, true)

        if (terpasang(c, Setelan.PAKET_TAILSCALE)) {
            try {
                // lockdown=false: kalau Tailscale belum tersambung, internet biasa tetap jalan
                // (perlu untuk update Play Store dan saat login ulang Tailscale).
                d.setAlwaysOnVpnPackage(a, Setelan.PAKET_TAILSCALE, false)
            } catch (e: Exception) {
                // Tailscale belum pernah dibuka/diizinkan sebagai VPN; dicoba lagi saat aplikasi dibuka.
            }
        }
    }

    /** Mode perawatan: cabut batasan dan kunci aplikasi sementara. Device owner tetap. */
    fun cabutSementara(c: Context) {
        if (!pemilik(c)) return
        val d = dpm(c)
        val a = admin(c)
        BATASAN.forEach { d.clearUserRestriction(a, it) }
        d.setKeyguardDisabled(a, false)
    }

    /** Lepas sepenuhnya: tablet kembali jadi tablet biasa. Aplikasi tersembunyi dimunculkan lagi. */
    fun lepas(c: Context) {
        if (!pemilik(c)) return
        val d = dpm(c)
        val a = admin(c)
        cabutSementara(c)
        semuaAplikasi(c).filter { it.tersembunyi }.forEach { d.setApplicationHidden(a, it.paket, false) }
        d.setLockTaskPackages(a, arrayOf<String>())
        d.clearPackagePersistentPreferredActivities(a, c.packageName)
        try { d.setAlwaysOnVpnPackage(a, null, false) } catch (e: Exception) { }
        d.clearDeviceOwnerApp(c.packageName)
    }

    data class Aplikasi(val paket: String, val nama: String, val tersembunyi: Boolean)

    /** Aplikasi yang bisa disembunyikan: yang punya ikon di layar, atau yang sudah disembunyikan. */
    @Suppress("DEPRECATION")
    fun semuaAplikasi(c: Context): List<Aplikasi> {
        val pm = c.packageManager
        val d = dpm(c)
        val a = admin(c)
        val jangan = setOf(c.packageName, Setelan.PAKET_TAILSCALE, "com.android.settings", "com.android.systemui")
        return pm.getInstalledApplications(PackageManager.MATCH_UNINSTALLED_PACKAGES)
            .filter { it.packageName !in jangan }
            .mapNotNull { info ->
                val sembunyi = pemilik(c) && d.isApplicationHidden(a, info.packageName)
                if (!sembunyi && pm.getLaunchIntentForPackage(info.packageName) == null) return@mapNotNull null
                if (!sembunyi && (info.flags and ApplicationInfo.FLAG_INSTALLED) == 0) return@mapNotNull null
                Aplikasi(info.packageName, pm.getApplicationLabel(info).toString(), sembunyi)
            }
            .sortedBy { it.nama.lowercase() }
    }

    fun sembunyikan(c: Context, paket: String, sembunyi: Boolean): Boolean {
        if (!pemilik(c)) return false
        return dpm(c).setApplicationHidden(admin(c), paket, sembunyi)
    }
}
