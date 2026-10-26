namespace Ril;

static class Program
{
    [STAThread]
    static void Main()
    {
        // Satu Ril per akun Windows.
        using var kunci = new Mutex(true, @"Local\RilKiosk", out bool pertama);
        if (!pertama) return;

        Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
        Application.ThreadException += (_, e) => Catatan.Tulis("ThreadException: " + e.Exception);
        AppDomain.CurrentDomain.UnhandledException += (_, e) => Catatan.Tulis("Unhandled: " + e.ExceptionObject);
        ApplicationConfiguration.Initialize();
        Application.Run(new FormUtama());
    }
}

/// <summary>Catatan galat sederhana di %LOCALAPPDATA%\Ril\ril.log (untuk diperiksa pemilik).</summary>
static class Catatan
{
    public static void Tulis(string teks)
    {
        try
        {
            Directory.CreateDirectory(Setelan.Folder);
            File.AppendAllText(Path.Combine(Setelan.Folder, "ril.log"), $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {teks}{Environment.NewLine}");
        }
        catch { }
    }
}
