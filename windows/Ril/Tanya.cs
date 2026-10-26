namespace Ril;

/// <summary>Kotak isian sederhana dengan huruf besar (untuk PIN, alamat, dan konfirmasi).</summary>
static class Tanya
{
    static readonly Font Huruf = new("Segoe UI", 12f);

    public static string Teks(IWin32Window induk, string judul, string pesan, string awal = "", bool rahasia = false)
    {
        using var f = new Form
        {
            Text = judul, FormBorderStyle = FormBorderStyle.FixedDialog, StartPosition = FormStartPosition.CenterScreen,
            MinimizeBox = false, MaximizeBox = false, ShowInTaskbar = false, TopMost = true, Font = Huruf,
            ClientSize = new Size(460, 170), Padding = new Padding(16),
        };
        var label = new Label { Text = pesan, AutoSize = false, Dock = DockStyle.Top, Height = 52 };
        var isian = new TextBox { Text = awal, Dock = DockStyle.Top, UseSystemPasswordChar = rahasia };
        var ok = new Button { Text = "OK", DialogResult = DialogResult.OK, Width = 110, Height = 38 };
        var batal = new Button { Text = "Batal", DialogResult = DialogResult.Cancel, Width = 110, Height = 38 };
        var bawah = new FlowLayoutPanel { Dock = DockStyle.Bottom, FlowDirection = FlowDirection.RightToLeft, Height = 46 };
        bawah.Controls.AddRange(new Control[] { batal, ok });
        f.Controls.Add(bawah);
        f.Controls.Add(isian);
        f.Controls.Add(label);
        f.AcceptButton = ok;
        f.CancelButton = batal;
        f.Shown += (_, _) => { isian.Focus(); isian.SelectAll(); };
        return f.ShowDialog(induk) == DialogResult.OK ? isian.Text : null;
    }

    public static bool Ya(IWin32Window induk, string judul, string pesan) =>
        MessageBox.Show(induk, pesan, judul, MessageBoxButtons.YesNo, MessageBoxIcon.Question, MessageBoxDefaultButton.Button2) == DialogResult.Yes;

    public static void Info(IWin32Window induk, string judul, string pesan) =>
        MessageBox.Show(induk, pesan, judul, MessageBoxButtons.OK, MessageBoxIcon.Information);
}
