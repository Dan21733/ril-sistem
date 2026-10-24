package id.dbrl.ril

import android.app.Activity
import android.app.ActivityManager
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.CookieManager
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast

/**
 * Satu layar: halaman kasir/gudang di WebView, dikunci sebagai satu-satunya aplikasi.
 * Menu pemilik: ketuk pojok kanan atas 5 kali dalam 3 detik, lalu masukkan PIN pemilik.
 */
class MainActivity : Activity() {
    private lateinit var setelan: Setelan
    private lateinit var web: WebView
    private lateinit var galat: LinearLayout
    private lateinit var galatTeks: TextView

    /** Mode perawatan: kunci dilepas sampai pemilik memilih "Kunci lagi" (tidak bertahan setelah restart). */
    private var perawatan = false

    private var ketukan = 0
    private var ketukanMulai = 0L
    private var salahPin = 0
    private var terkunciSampai = 0L
    private var unggahan: ValueCallback<Array<Uri>>? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setelan = Setelan(this)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(bangunLayar())
        siapkanWeb()
        web.loadUrl(setelan.alamat)
        if (!setelan.punyaPin) mintaPinBaru(wajib = true)
    }

    override fun onResume() {
        super.onResume()
        kunci()   // kembali dari pengaturan Wi-Fi/Tailscale: kunci dipasang lagi
    }

    private fun kunci() {
        if (perawatan || !Kebijakan.pemilik(this)) return
        Kebijakan.terapkan(this)
        val am = getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        if (am.lockTaskModeState == ActivityManager.LOCK_TASK_MODE_NONE) {
            try { startLockTask() } catch (e: Exception) { }
        }
    }

    private fun lepasKunci() {
        try { stopLockTask() } catch (e: Exception) { }
    }

    // ------------------------------------------------------------------ layar

    private fun dp(n: Int) = (n * resources.displayMetrics.density).toInt()

    private fun bangunLayar(): View {
        val akar = FrameLayout(this)
        web = WebView(this)
        akar.addView(web, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        galat = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(Color.parseColor("#F5F3EF"))
            setPadding(dp(24), dp(24), dp(24), dp(24))
            visibility = View.GONE
        }
        galatTeks = TextView(this).apply {
            textSize = 18f
            gravity = Gravity.CENTER
            setTextColor(Color.parseColor("#2A2724"))
        }
        val coba = Button(this).apply {
            text = "Coba lagi"
            setOnClickListener { galat.visibility = View.GONE; web.loadUrl(setelan.alamat) }
        }
        galat.addView(galatTeks)
        galat.addView(coba, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(16) })
        akar.addView(galat, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        // Pemicu menu pemilik: kotak tak terlihat di pojok kanan atas.
        val pojok = View(this).apply { setOnClickListener { ketukPojok() } }
        akar.addView(pojok, FrameLayout.LayoutParams(dp(56), dp(56), Gravity.TOP or Gravity.END))
        return akar
    }

    private fun siapkanWeb() {
        WebView.setWebContentsDebuggingEnabled(false)
        CookieManager.getInstance().setAcceptCookie(true)
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            cacheMode = WebSettings.LOAD_DEFAULT
            mediaPlaybackRequiresUserGesture = true
            allowFileAccess = false
            allowContentAccess = false
            setSupportMultipleWindows(false)
            builtInZoomControls = false
        }
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, req: WebResourceRequest): Boolean {
                // Hanya server toko yang boleh dibuka; tautan lain diabaikan.
                val boleh = Uri.parse(setelan.alamat).host
                return req.url.host != boleh
            }

            override fun onReceivedError(view: WebView, req: WebResourceRequest, err: WebResourceError) {
                if (req.isForMainFrame) tampilGalat()
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(view: WebView, cb: ValueCallback<Array<Uri>>, p: FileChooserParams): Boolean {
                unggahan?.onReceiveValue(null)
                unggahan = cb
                return try {
                    startActivityForResult(p.createIntent(), PILIH_BERKAS); true
                } catch (e: Exception) {
                    unggahan = null; false
                }
            }
        }
    }

    private fun tampilGalat() {
        galatTeks.text = "Tidak tersambung ke server toko.\n\nPastikan internet menyala dan Tailscale tersambung, lalu coba lagi."
        galat.visibility = View.VISIBLE
    }

    @Deprecated("Activity lama, cukup untuk satu pilihan berkas")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode == PILIH_BERKAS) {
            unggahan?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data))
            unggahan = null
            return
        }
        super.onActivityResult(requestCode, resultCode, data)
    }

    @Deprecated("Tombol kembali: mundur di halaman, tidak keluar aplikasi")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack()
    }

    // ------------------------------------------------------------------ menu pemilik

    private fun ketukPojok() {
        val sekarang = SystemClock.elapsedRealtime()
        if (sekarang - ketukanMulai > 3000) { ketukan = 0; ketukanMulai = sekarang }
        if (++ketukan >= 5) { ketukan = 0; mintaPin { menuPemilik() } }
    }

    private fun kotakPin(petunjuk: String) = EditText(this).apply {
        hint = petunjuk
        inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD
    }

    private fun mintaPin(lanjut: () -> Unit) {
        val sisa = terkunciSampai - SystemClock.elapsedRealtime()
        if (sisa > 0) { pesan("Terlalu banyak salah. Tunggu ${sisa / 1000 + 1} detik."); return }
        val isian = kotakPin("PIN pemilik")
        AlertDialog.Builder(this).setTitle("Menu pemilik").setView(isian)
            .setPositiveButton("Buka") { _, _ ->
                if (setelan.cocokPin(isian.text.toString())) { salahPin = 0; lanjut() }
                else {
                    salahPin++
                    if (salahPin >= 5) { salahPin = 0; terkunciSampai = SystemClock.elapsedRealtime() + 60_000 }
                    pesan("PIN salah.")
                }
            }
            .setNegativeButton("Batal", null).show()
    }

    private fun mintaPinBaru(wajib: Boolean) {
        val satu = kotakPin("PIN baru (minimal 6 angka)")
        val dua = kotakPin("Ulangi PIN")
        val isi = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(8), dp(20), 0)
            addView(satu); addView(dua)
        }
        val dlg = AlertDialog.Builder(this)
            .setTitle(if (wajib) "Buat PIN pemilik" else "Ganti PIN pemilik")
            .setMessage("PIN ini untuk membuka menu pemilik di tablet ini. Jangan beri tahu karyawan.")
            .setView(isi)
            .setPositiveButton("Simpan", null)
            .apply { if (!wajib) setNegativeButton("Batal", null) }
            .setCancelable(!wajib)
            .create()
        dlg.setOnShowListener {
            dlg.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val a = satu.text.toString()
                when {
                    a.length < 6 || !a.all { it.isDigit() } -> pesan("PIN minimal 6 angka.")
                    a != dua.text.toString() -> pesan("PIN tidak sama.")
                    else -> { setelan.simpanPin(a); pesan("PIN pemilik disimpan."); dlg.dismiss() }
                }
            }
        }
        dlg.show()
    }

    private fun menuPemilik() {
        val pemilik = Kebijakan.pemilik(this)
        val pilihan = mutableListOf<Pair<String, () -> Unit>>(
            "Muat ulang halaman" to { galat.visibility = View.GONE; web.loadUrl(setelan.alamat) },
            "Wi-Fi" to { bukaPengaturan(Settings.ACTION_WIFI_SETTINGS) },
            "Buka Tailscale" to { bukaTailscale() },
            "Mode: ${if (setelan.mode == Setelan.MODE_KASIR) "Kasir" else "Gudang"} (ganti)" to { gantiMode() },
            "Ubah alamat server" to { ubahAlamat() },
            "Sembunyikan / munculkan aplikasi" to { aturAplikasi() },
            "Ganti PIN pemilik" to { mintaPinBaru(wajib = false) },
            "Status perangkat" to { statusPerangkat() },
        )
        if (pemilik) {
            if (perawatan) pilihan.add(Pair("Kunci lagi") { perawatan = false; kunci(); pesan("Tablet dikunci lagi.") })
            else pilihan.add(Pair("Mode perawatan (buka kunci sementara)") { masukPerawatan() })
            pilihan.add(Pair("Lepas Ril dari tablet ini") { konfirmasiLepas() })
        }
        AlertDialog.Builder(this).setTitle("Menu pemilik")
            .setItems(pilihan.map { it.first }.toTypedArray()) { _, i -> pilihan[i].second() }
            .setNegativeButton("Tutup", null).show()
    }

    private fun bukaPengaturan(aksi: String) {
        lepasKunci()
        try { startActivity(Intent(aksi)) } catch (e: Exception) { pesan("Pengaturan tidak bisa dibuka.") }
    }

    private fun bukaTailscale() {
        val i = packageManager.getLaunchIntentForPackage(Setelan.PAKET_TAILSCALE)
        if (i == null) { pesan("Tailscale belum terpasang."); return }
        startActivity(i)   // Tailscale ada di daftar boleh lock task, jadi kunci tidak perlu dilepas
    }

    private fun gantiMode() {
        val nama = arrayOf("Gudang", "Kasir")
        val kini = if (setelan.mode == Setelan.MODE_KASIR) 1 else 0
        AlertDialog.Builder(this).setTitle("Mode tablet ini").setSingleChoiceItems(nama, kini) { d, i ->
            setelan.mode = if (i == 1) Setelan.MODE_KASIR else Setelan.MODE_GUDANG
            d.dismiss(); web.loadUrl(setelan.alamat)
        }.show()
    }

    private fun ubahAlamat() {
        val kasir = setelan.mode == Setelan.MODE_KASIR
        val isian = EditText(this).apply {
            setText(setelan.alamat)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        }
        AlertDialog.Builder(this).setTitle("Alamat ${if (kasir) "kasir" else "gudang"}").setView(isian)
            .setPositiveButton("Simpan") { _, _ ->
                val u = isian.text.toString().trim()
                if (!u.startsWith("https://")) { pesan("Alamat harus diawali https://"); return@setPositiveButton }
                if (kasir) setelan.alamatKasir = u else setelan.alamatGudang = u
                web.loadUrl(setelan.alamat)
            }
            .setNeutralButton("Bawaan") { _, _ ->
                if (kasir) setelan.alamatKasir = Setelan.ALAMAT_KASIR else setelan.alamatGudang = Setelan.ALAMAT_GUDANG
                web.loadUrl(setelan.alamat)
            }
            .setNegativeButton("Batal", null).show()
    }

    private fun aturAplikasi() {
        if (!Kebijakan.pemilik(this)) { pesan("Hanya bisa setelah Ril dijadikan device owner."); return }
        val daftar = Kebijakan.semuaAplikasi(this)
        if (daftar.isEmpty()) { pesan("Tidak ada aplikasi."); return }
        val centang = daftar.map { it.tersembunyi }.toBooleanArray()
        AlertDialog.Builder(this).setTitle("Centang = disembunyikan (tidak bisa jalan)")
            .setMultiChoiceItems(daftar.map { it.nama }.toTypedArray(), centang) { _, i, v -> centang[i] = v }
            .setPositiveButton("Simpan") { _, _ ->
                var gagal = 0
                daftar.forEachIndexed { i, a ->
                    if (a.tersembunyi != centang[i] && !Kebijakan.sembunyikan(this, a.paket, centang[i])) gagal++
                }
                pesan(if (gagal == 0) "Tersimpan." else "$gagal aplikasi tidak bisa diubah (aplikasi sistem inti).")
            }
            .setNegativeButton("Batal", null).show()
    }

    private fun statusPerangkat() {
        val am = getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val versi = packageManager.getPackageInfo(packageName, 0).versionName
        val teks = buildString {
            appendLine("Ril $versi · mode ${setelan.mode}")
            appendLine("Device owner: ${if (Kebijakan.pemilik(this@MainActivity)) "ya" else "BELUM"}")
            appendLine("Terkunci: ${if (am.lockTaskModeState != ActivityManager.LOCK_TASK_MODE_NONE) "ya" else "tidak"}")
            appendLine("Tailscale terpasang: ${if (Kebijakan.terpasang(this@MainActivity, Setelan.PAKET_TAILSCALE)) "ya" else "TIDAK"}")
            append("Alamat: ${setelan.alamat}")
        }
        AlertDialog.Builder(this).setTitle("Status perangkat").setMessage(teks).setPositiveButton("Tutup", null).show()
    }

    private fun masukPerawatan() {
        AlertDialog.Builder(this).setTitle("Mode perawatan")
            .setMessage("Kunci aplikasi, USB, dan opsi pengembang dibuka sampai kamu memilih \"Kunci lagi\" atau tablet dinyalakan ulang. Jangan tinggalkan tablet dalam mode ini.")
            .setPositiveButton("Buka kunci") { _, _ ->
                perawatan = true
                Kebijakan.cabutSementara(this)
                lepasKunci()
                pesan("Mode perawatan aktif.")
            }
            .setNegativeButton("Batal", null).show()
    }

    private fun konfirmasiLepas() {
        AlertDialog.Builder(this).setTitle("Lepas Ril dari tablet ini?")
            .setMessage("Semua kunci dicabut, aplikasi tersembunyi dimunculkan lagi, dan Ril bukan device owner lagi. Untuk memasang lagi perlu reset pabrik.")
            .setPositiveButton("Lepas") { _, _ ->
                lepasKunci()
                Kebijakan.lepas(this)
                pesan("Ril dilepas. Tablet kembali normal.")
            }
            .setNegativeButton("Batal", null).show()
    }

    private fun pesan(t: String) = Toast.makeText(this, t, Toast.LENGTH_LONG).show()

    companion object {
        private const val PILIH_BERKAS = 7
    }
}
