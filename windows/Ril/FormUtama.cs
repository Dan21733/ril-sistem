using System.Diagnostics;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace Ril;

/// <summary>
/// Layar penuh: bar atas (nama, jam, baterai, Muat ulang, Matikan) dan halaman toko di WebView2.
/// Tidak ada alamat URL, klik kanan, DevTools, atau tab. Hanya server toko yang bisa dibuka.
/// Menu pemilik: klik tulisan "Ril" di kiri atas 5 kali dalam 3 detik, lalu PIN pemilik.
/// </summary>
public class FormUtama : Form
{
    readonly Setelan setelan = Setelan.Baca();
    readonly WebView2 web = new() { Dock = DockStyle.Fill };
    readonly Panel bar = new() { Dock = DockStyle.Top, Height = 40, BackColor = Color.FromArgb(35, 33, 30), Padding = new Padding(10, 0, 6, 0) };
    readonly Label logo = new() { Text = "Ril", AutoSize = true, ForeColor = Color.White, Font = new Font("Segoe UI Semibold", 13f), Cursor = Cursors.Default };
    readonly Label info = new() { AutoSize = true, ForeColor = Color.FromArgb(217, 213, 204), Font = new Font("Segoe UI", 11f) };
    readonly Panel galat = new() { Dock = DockStyle.Fill, BackColor = Color.FromArgb(246, 245, 242), Visible = false };
    readonly Label galatTeks = new() { AutoSize = false, TextAlign = ContentAlignment.MiddleCenter, Dock = DockStyle.Fill, Font = new Font("Segoe UI", 14f), ForeColor = Color.FromArgb(31, 29, 26) };
    readonly System.Windows.Forms.Timer jam = new() { Interval = 1000 };
    readonly System.Windows.Forms.Timer coba = new() { Interval = 5000 };
    int ketukan;
    DateTime ketukanMulai;
    int salahPin;
    DateTime terkunciSampai;

    public FormUtama()
    {
        Text = "Ril";
        FormBorderStyle = FormBorderStyle.None;
        WindowState = FormWindowState.Maximized;
        BackColor = Color.FromArgb(246, 245, 242);
        KeyPreview = true;

        // bar atas: kiri logo + info, kanan tombol
        var kanan = new FlowLayoutPanel { Dock = DockStyle.Right, AutoSize = true, FlowDirection = FlowDirection.LeftToRight, WrapContents = false, Padding = new Padding(0, 4, 0, 0) };
        kanan.Controls.Add(TombolBar("⟳  Muat ulang", (_, _) => MuatUlang()));
        kanan.Controls.Add(TombolBar("⏻  Matikan", (_, _) => Matikan()));
        var kiri = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.LeftToRight, WrapContents = false, Padding = new Padding(0, 8, 0, 0) };
        kiri.Controls.Add(logo);
        kiri.Controls.Add(info);
        bar.Controls.Add(kiri);
        bar.Controls.Add(kanan);
        logo.Click += (_, _) => KetukLogo();

        var cobaLagi = new Button { Text = "Coba lagi", Dock = DockStyle.Bottom, Height = 54, Font = new Font("Segoe UI", 13f) };
        cobaLagi.Click += (_, _) => MuatUlang();
        galat.Controls.Add(galatTeks);
        galat.Controls.Add(cobaLagi);

        Controls.Add(web);
        Controls.Add(galat);
        Controls.Add(bar);

        jam.Tick += (_, _) => PerbaruiInfo();
        jam.Start();
        PerbaruiInfo();
        coba.Tick += (_, _) => { if (galat.Visible) MuatUlang(); };

        KeyDown += (_, e) => { if (e.Control && e.Shift && e.Alt && e.KeyCode == Keys.R) MenuPemilik(); };
        Shown += async (_, _) =>
        {
            if (!setelan.PunyaPin) BuatPin(wajib: true);
            await SiapkanWeb();
        };
    }

    Button TombolBar(string teks, EventHandler klik)
    {
        var b = new Button
        {
            Text = teks, AutoSize = true, Height = 32, FlatStyle = FlatStyle.Flat, ForeColor = Color.White,
            BackColor = Color.FromArgb(52, 49, 44), Font = new Font("Segoe UI", 10.5f), Margin = new Padding(6, 0, 0, 0), TabStop = false,
        };
        b.FlatAppearance.BorderColor = Color.FromArgb(74, 70, 63);
        b.Click += klik;
        return b;
    }

    void PerbaruiInfo()
    {
        var p = SystemInformation.PowerStatus;
        var baterai = p.BatteryChargeStatus.HasFlag(BatteryChargeStatus.NoSystemBattery) ? ""
            : $"   ·   Baterai {Math.Round(p.BatteryLifePercent * 100)}%{(p.PowerLineStatus == PowerLineStatus.Online ? " (dicas)" : "")}";
        info.Text = $"   {DateTime.Now:dddd, d MMM  HH:mm}{baterai}";
    }

    // ------------------------------------------------------------------ halaman

    async Task SiapkanWeb()
    {
        try
        {
            var env = await CoreWebView2Environment.CreateAsync(null, Path.Combine(Setelan.Folder, "WebView2"));
            await web.EnsureCoreWebView2Async(env);
        }
        catch (Exception e)
        {
            Catatan.Tulis("WebView2 gagal: " + e);
            TampilGalat("Komponen WebView2 tidak ditemukan di laptop ini.\nPasang \"Microsoft Edge WebView2 Runtime\" lalu buka Ril lagi.");
            return;
        }
        var s = web.CoreWebView2.Settings;
        s.AreDevToolsEnabled = false;
        s.AreDefaultContextMenusEnabled = false;
        s.AreBrowserAcceleratorKeysEnabled = false;   // F5, Ctrl+P, F12, Ctrl+F, dll. dimatikan
        s.IsStatusBarEnabled = false;
        s.IsZoomControlEnabled = true;
        s.IsGeneralAutofillEnabled = false;
        s.IsPasswordAutosaveEnabled = false;

        web.CoreWebView2.NavigationStarting += (_, e) =>
        {
            // Hanya server toko. Tautan lain diabaikan.
            if (!Uri.TryCreate(e.Uri, UriKind.Absolute, out var u) || !string.Equals(u.Authority, setelan.Host, StringComparison.OrdinalIgnoreCase))
                e.Cancel = true;
        };
        web.CoreWebView2.NewWindowRequested += (_, e) =>
        {
            e.Handled = true;   // tidak ada jendela/tab baru
            if (Uri.TryCreate(e.Uri, UriKind.Absolute, out var u) && string.Equals(u.Authority, setelan.Host, StringComparison.OrdinalIgnoreCase))
                web.CoreWebView2.Navigate(e.Uri);
        };
        web.CoreWebView2.NavigationCompleted += (_, e) =>
        {
            if (e.IsSuccess) { galat.Visible = false; coba.Stop(); }
            else if (e.WebErrorStatus != CoreWebView2WebErrorStatus.OperationCanceled)
                TampilGalat("Tidak tersambung ke server toko.\n\nPastikan Wi-Fi menyala dan Tailscale tersambung.\nRil mencoba lagi otomatis setiap 5 detik.");
        };
        web.CoreWebView2.ProcessFailed += (_, e) =>
        {
            Catatan.Tulis("Proses WebView2 berhenti: " + e.ProcessFailedKind);
            BeginInvoke(new Action(MuatUlang));
        };
        web.CoreWebView2.Navigate(setelan.Alamat);
    }

    void TampilGalat(string teks)
    {
        galatTeks.Text = teks;
        galat.Visible = true;
        galat.BringToFront();
        bar.BringToFront();
        coba.Start();
    }

    void MuatUlang()
    {
        if (web.CoreWebView2 == null) return;
        try { web.CoreWebView2.Navigate(setelan.Alamat); } catch (Exception e) { Catatan.Tulis("Muat ulang gagal: " + e.Message); }
    }

    void Matikan()
    {
        if (Tanya.Ya(this, "Matikan laptop", "Matikan laptop sekarang?"))
            Jalankan("shutdown", "/s /t 0");
    }

    static void Jalankan(string berkas, string argumen = "")
    {
        try { Process.Start(new ProcessStartInfo(berkas, argumen) { UseShellExecute = true }); }
        catch (Exception e) { Catatan.Tulis($"Gagal menjalankan {berkas}: {e.Message}"); }
    }

    // ------------------------------------------------------------------ menu pemilik

    void KetukLogo()
    {
        if ((DateTime.Now - ketukanMulai).TotalSeconds > 3) { ketukan = 0; ketukanMulai = DateTime.Now; }
        if (++ketukan >= 5) { ketukan = 0; MenuPemilik(); }
    }

    bool CekPin()
    {
        if (DateTime.Now < terkunciSampai)
        {
            Tanya.Info(this, "Menu pemilik", $"Terlalu banyak salah. Tunggu {(int)(terkunciSampai - DateTime.Now).TotalSeconds + 1} detik.");
            return false;
        }
        var pin = Tanya.Teks(this, "Menu pemilik", "Masukkan PIN pemilik.", rahasia: true);
        if (pin == null) return false;
        if (setelan.CocokPin(pin)) { salahPin = 0; return true; }
        if (++salahPin >= 5) { salahPin = 0; terkunciSampai = DateTime.Now.AddMinutes(1); }
        Tanya.Info(this, "Menu pemilik", "PIN salah.");
        return false;
    }

    void BuatPin(bool wajib)
    {
        while (true)
        {
            var a = Tanya.Teks(this, wajib ? "Buat PIN pemilik" : "Ganti PIN pemilik",
                "PIN untuk membuka menu pemilik di laptop ini (minimal 6 angka). Jangan beri tahu karyawan.", rahasia: true);
            if (a == null) { if (wajib) continue; return; }
            if (a.Length < 6 || !a.All(char.IsDigit)) { Tanya.Info(this, "PIN", "PIN minimal 6 angka."); continue; }
            var b = Tanya.Teks(this, "Ulangi PIN", "Ketik ulang PIN yang sama.", rahasia: true);
            if (b != a) { Tanya.Info(this, "PIN", "PIN tidak sama. Ulangi."); continue; }
            setelan.AturPin(a);
            Tanya.Info(this, "PIN", "PIN pemilik disimpan.");
            return;
        }
    }

    void MenuPemilik()
    {
        if (!CekPin()) return;
        var menu = new ContextMenuStrip { Font = new Font("Segoe UI", 12f), ShowImageMargin = false };
        menu.Items.Add("Muat ulang halaman", null, (_, _) => MuatUlang());
        menu.Items.Add("Ubah alamat server", null, (_, _) => UbahAlamat());
        menu.Items.Add("Pengaturan Wi-Fi", null, (_, _) => Jalankan("ms-settings:network-wifi"));
        menu.Items.Add("Ganti PIN pemilik", null, (_, _) => BuatPin(wajib: false));
        menu.Items.Add("Status", null, (_, _) => Status());
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Buka desktop Windows (perawatan)", null, (_, _) =>
        {
            if (Tanya.Ya(this, "Perawatan", "Membuka desktop Windows di akun ini sampai keluar akun. Jangan tinggalkan laptop dalam keadaan ini. Lanjut?"))
                Jalankan("explorer.exe");
        });
        menu.Items.Add("Keluar akun (sign out)", null, (_, _) => { if (Tanya.Ya(this, "Keluar akun", "Keluar dari akun Windows ini?")) Jalankan("shutdown", "/l"); });
        menu.Items.Add("Mulai ulang laptop", null, (_, _) => { if (Tanya.Ya(this, "Mulai ulang", "Mulai ulang laptop sekarang?")) Jalankan("shutdown", "/r /t 0"); });
        menu.Show(logo, new Point(0, logo.Height + 8));
    }

    void UbahAlamat()
    {
        var u = Tanya.Teks(this, "Alamat server", "Alamat halaman yang dibuka Ril (harus https://). Kosongkan untuk alamat bawaan.", setelan.Alamat);
        if (u == null) return;
        u = u.Trim();
        if (u.Length == 0) u = Setelan.AlamatBawaan;
        if (!u.StartsWith("https://", StringComparison.OrdinalIgnoreCase) || !Uri.TryCreate(u, UriKind.Absolute, out _))
        {
            Tanya.Info(this, "Alamat server", "Alamat harus diawali https:// dan lengkap.");
            return;
        }
        setelan.Alamat = u;
        setelan.Simpan();
        MuatUlang();
    }

    void Status()
    {
        var versi = typeof(FormUtama).Assembly.GetName().Version;
        string webview;
        try { webview = CoreWebView2Environment.GetAvailableBrowserVersionString(); } catch { webview = "tidak ditemukan"; }
        Tanya.Info(this, "Status", $"Ril {versi}\nAkun Windows: {Environment.UserName}\nAlamat: {setelan.Alamat}\nWebView2: {webview}\nCatatan: {Path.Combine(Setelan.Folder, "ril.log")}");
    }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        // Alt+F4 dan sejenisnya tidak menutup Ril; hanya mati/keluar akun Windows yang boleh.
        if (e.CloseReason == CloseReason.UserClosing) { e.Cancel = true; return; }
        base.OnFormClosing(e);
    }
}
