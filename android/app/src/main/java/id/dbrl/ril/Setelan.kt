package id.dbrl.ril

import android.content.Context
import java.security.MessageDigest
import java.security.SecureRandom

/** Setelan per perangkat: mode, alamat halaman, dan PIN pemilik (disimpan sebagai hash bergaram). */
class Setelan(context: Context) {
    private val sp = context.getSharedPreferences("ril", Context.MODE_PRIVATE)

    var mode: String
        get() = sp.getString("mode", MODE_GUDANG) ?: MODE_GUDANG
        set(v) = sp.edit().putString("mode", v).apply()

    var alamatGudang: String
        get() = sp.getString("alamat_gudang", ALAMAT_GUDANG) ?: ALAMAT_GUDANG
        set(v) = sp.edit().putString("alamat_gudang", v.trim()).apply()

    var alamatKasir: String
        get() = sp.getString("alamat_kasir", ALAMAT_KASIR) ?: ALAMAT_KASIR
        set(v) = sp.edit().putString("alamat_kasir", v.trim()).apply()

    val alamat: String get() = if (mode == MODE_KASIR) alamatKasir else alamatGudang

    val punyaPin: Boolean get() = sp.contains("pin_hash")

    fun simpanPin(pin: String) {
        val garam = ByteArray(16).also { SecureRandom().nextBytes(it) }
        sp.edit().putString("pin_garam", garam.hex()).putString("pin_hash", hash(garam.hex(), pin)).apply()
    }

    fun cocokPin(pin: String): Boolean {
        val garam = sp.getString("pin_garam", null) ?: return false
        val simpan = sp.getString("pin_hash", null) ?: return false
        return MessageDigest.isEqual(simpan.toByteArray(), hash(garam, pin).toByteArray())
    }

    private fun hash(garam: String, pin: String): String {
        var b = (garam + pin).toByteArray()
        val md = MessageDigest.getInstance("SHA-256")
        repeat(50_000) { b = md.digest(b) }
        return b.hex()
    }

    private fun ByteArray.hex() = joinToString("") { "%02x".format(it) }

    companion object {
        const val MODE_GUDANG = "gudang"
        const val MODE_KASIR = "kasir"
        const val ALAMAT_GUDANG = "https://server-rumah.tail8bf945.ts.net:8443/gudang/"
        const val ALAMAT_KASIR = "https://server-rumah.tail8bf945.ts.net/"
        const val PAKET_TAILSCALE = "com.tailscale.ipn"
    }
}
