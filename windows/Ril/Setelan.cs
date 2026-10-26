using System.Security.Cryptography;
using System.Text.Json;

namespace Ril;

/// <summary>Setelan per akun Windows: alamat halaman dan PIN pemilik (hash PBKDF2 bergaram).</summary>
public class Setelan
{
    public const string AlamatBawaan = "https://server-rumah.tail8bf945.ts.net:8443/gudang/";
    public static string Folder => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Ril");
    static string Berkas => Path.Combine(Folder, "setelan.json");

    public string Alamat { get; set; } = AlamatBawaan;
    public string PinGaram { get; set; }
    public string PinHash { get; set; }

    public bool PunyaPin => !string.IsNullOrEmpty(PinHash);

    public static Setelan Baca()
    {
        try
        {
            if (File.Exists(Berkas)) return JsonSerializer.Deserialize<Setelan>(File.ReadAllText(Berkas)) ?? new Setelan();
        }
        catch (Exception e) { Catatan.Tulis("Setelan rusak, pakai bawaan: " + e.Message); }
        return new Setelan();
    }

    public void Simpan()
    {
        Directory.CreateDirectory(Folder);
        File.WriteAllText(Berkas, JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true }));
    }

    public void AturPin(string pin)
    {
        var garam = RandomNumberGenerator.GetBytes(16);
        PinGaram = Convert.ToHexString(garam);
        PinHash = Convert.ToHexString(Rfc2898DeriveBytes.Pbkdf2(pin, garam, 100_000, HashAlgorithmName.SHA256, 32));
        Simpan();
    }

    public bool CocokPin(string pin)
    {
        if (!PunyaPin || string.IsNullOrEmpty(pin)) return false;
        var hasil = Rfc2898DeriveBytes.Pbkdf2(pin, Convert.FromHexString(PinGaram), 100_000, HashAlgorithmName.SHA256, 32);
        return CryptographicOperations.FixedTimeEquals(hasil, Convert.FromHexString(PinHash));
    }

    public string Host => Uri.TryCreate(Alamat, UriKind.Absolute, out var u) ? u.Authority : "";
}
