# Pencarian Item Lewat Alias (Kandidat-Dulu)

Status: **tuning endpoint selesai 2026-10-04; peringkat dua lapis sejak
2026-10-17.** Backend otomatis memakai peringkat alias bila berjalan di
PostgreSQL dan tabel `item_alias` tersedia; saklar konfigurasi pencarian telah
dihapus. Benchmark SELECT read-only
menemukan query gabungan satu-SQL lebih cepat dari implementasi alias awal,
meski tetap lebih lambat daripada query lama di lingkungan uji ini.

Keputusan 2026-10-02 (setelah pengukuran di database uji `pos_test`):

1. Alias disimpan di tabel sendiri (`item_alias`), bukan sebagai kolom baru di
   `item` — `item` dimiliki Inventory dan satu item bisa dicari lewat banyak
   frasa.
2. Pencarian diubah menjadi kandidat-dulu: baris dibatasi dulu dengan operator
   trigram yang dilayani GIN, baru kemiripan dihitung untuk kandidat.
3. **Revisi 2026-10-17 (permintaan pemilik): peringkat dua lapis.** Lapis
   pertama `text_sim = GREATEST(kemiripan nama, kemiripan varian)`, lapis kedua
   `alias_sim` yang hanya memecah seri dan mengurutkan hasil yang memang cuma
   cocok lewat alias. Sebelumnya ketiganya digabung `GREATEST`, sehingga alias
   persis (mis. "pintu") menggeser barang yang namanya memuat kata itu
   ("Aries Pintu - Abu") ke bawah. Alias tetap berfungsi menemukan barang,
   tetapi tidak lagi berbobot setara dengan nama/varian.
4. Jalur alias kandidat-dulu aktif otomatis pada PostgreSQL bila tabel
   `item_alias` tersedia; backend lain/tabel yang tidak ada menggunakan query
   trigram lama.

## 1. Masalah yang diukur

Database nyata: 2.917 item, 1.071 frasa alias unik (158 frasa dipakai minimal
5 item). Query aplikasi hari ini menghitung enam fungsi trigram untuk **setiap**
baris `item`; fungsi trigram di sisi kolom tidak bisa memakai index.

| Ukuran (158 frasa umum) | Query lama | Query alias |
| --- | --- | --- |
| Frasa tanpa satu pun hasil | 60 | 0 |
| Recall (item terkait yang muncul) | 37,1% | 96,7% |

Kueri nama barang tetap dijaga: 158 kueri nama, jalur lama 100% recall;
jalur alias 93,1% dengan 4 kueri berubah isi (peringkat alias dapat menggeser
item yang kemiripannya di bawahnya). Perbedaan itu disadari dan diterima demi
60 frasa yang tadinya kosong — bukan efek samping yang tidak terukur.

## 2. DDL (dijalankan pemilik database, dengan izin)

```sql
-- Pemetaan frasa pencarian -> item_name. Tidak ada FOREIGN KEY ke item:
-- berkas sumber memuat nama yang belum tentu ada di item, dan alias harus
-- bisa dimuat lebih dulu (skrip impor melaporkan nama yang belum cocok).
CREATE TABLE IF NOT EXISTS item_alias (
    frasa VARCHAR NOT NULL,
    item_name VARCHAR NOT NULL,
    PRIMARY KEY (frasa, item_name)
);

-- Untuk menggabungkan alias ke item dan melaporkan item yang belum cocok.
CREATE INDEX IF NOT EXISTS item_alias_name_idx ON item_alias (item_name);

-- Inilah index yang membuat frasa 3 huruf seperti "zink" tetap murah.
-- `pg_trgm` wajib sudah terpasang (produksi sudah punya).
CREATE INDEX IF NOT EXISTS item_alias_frasa_trgm_idx
    ON item_alias USING gin (frasa gin_trgm_ops);

-- Jalur nama/varian ikut memakai operator trigram, jadi index-nya
-- dibutuhkan agar kandidat-dulu benar-benar berskala.
CREATE INDEX IF NOT EXISTS item_name_trgm_idx ON item USING gin (item_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS item_variant_trgm_idx ON item USING gin (variant gin_trgm_ops);

ANALYZE item;
ANALYZE item_alias;
```

Catatan skala: pada 2.917 baris planner masih memilih seq scan (tabelnya kecil),
jadi index di atas belum terasa manfaatnya hari ini. Pada uji 29.170 baris
GIN dipakai (`Bitmap Index Scan`) dan biaya server turun dari 425 ms ke 42 ms
untuk bentuk kandidat-dulu.

## 3. Mengisi tabel alias

Sumber: sheet `item_alias` pada berkas pembersihan pemilik. Kolom `alias_baru`
(dipakai bila terisi; berisi frasa yang masih spesifik item) dipakai lebih dulu,
jika kosong baru `alias`. Frasa dipisah per koma, dirapikan, dan dijadikan huruf
kecil karena trigram PostgreSQL membedakan kapitalisasi.

```bash
cd POS/pos-backend
venv/Scripts/python.exe tools/import_item_alias.py --file "C:/.../Data dengan alias dan rak - alias dibersihkan.xlsx"
# tambahkan --yes untuk menulis sungguhan
```

Hasil uji 2026-10-02 di `pos_test`: 915 baris dibaca, 1 header ulangan
dilewati, 37 baris tanpa alias, **3.004 pasangan frasa-item** ditulis; dijalankan
kedua kali → 0 tulisan baru (idempoten, `ON CONFLICT DO NOTHING`), dan seluruh
`item_name` di berkas sudah ada di tabel `item`. Skrip menolak jalan bila tabel
`item_alias` belum ada, dan tidak pernah menghapus frasa.

## 4. Bentuk query

```sql
WITH alias_hits AS (            -- frasa yang mirip kueri; dilayani GIN
    SELECT a.item_name,
           MAX(GREATEST(SIMILARITY(a.frasa, q), WORD_SIMILARITY(q, a.frasa))) AS alias_sim
    FROM item_alias a WHERE a.frasa % q OR a.frasa %> q GROUP BY a.item_name
),
candidates AS (                 -- kandidat langsung: operator trigram + ILIKE
    SELECT item_name, variant FROM item
     WHERE item_name % q OR item_name %> q OR item_name ILIKE '%q%'
        OR variant % q OR variant %> q OR variant ILIKE '%q%'
    UNION
    SELECT i.item_name, i.variant FROM item i
    JOIN alias_hits h ON h.item_name = i.item_name
)
SELECT ..., GREATEST(SIMILARITY(item_name,q), SIMILARITY(variant,q),
                     WORD_SIMILARITY(q,item_name),
                     WORD_SIMILARITY(q,variant)) AS text_sim,
            COALESCE(h.alias_sim, 0) AS alias_sim
FROM item i JOIN candidates c ON ...
ORDER BY text_sim DESC, alias_sim DESC, item_name, variant
```

Lapis nama dan lapis alias tidak pernah tercampur: item yang lolos hanya lewat
`item_alias` pasti punya `text_sim` di bawah ambang trigram (0,3), sedangkan
setiap item yang cocok langsung punya `text_sim` ≥ ambang tersebut.

Operator dipilih karena bisa memakai index GIN, sedangkan pemanggilan fungsi
trigram (`SIMILARITY(...) >= 0.3`) memaksa pemeriksaan seluruh baris:

- `kolom % q` = `SIMILARITY(kolom, q) >= 0.3` (simetris, arah tidak penting);
- `kolom %>% q` = `WORD_SIMILARITY(q, kolom) >= 0.3` — arahnya sama dengan
  query aplikasi, dan PostgreSQL menulisnya kembali sebagai `<%` saat memakai index.

Bentuk `UNION` di atas (bukan join langsung ke `item_alias` per baris) dipilih
setelah uji: join hanya lewat `item_name` membuat satu kandidat meledak menjadi
semua variannya, sehingga 30 baris teratas dipenuhi duplikat.

## 5. Hasil benchmark dan uji terdahulu

Di `pos_test` (2.917 item; 1.071 frasa; skala ×10 adalah duplikasi sintetis
29.170 item dan 31.620 frasa), waktu **server** dari `EXPLAIN (ANALYZE)`, median ms:

| Bentuk | Frasa, 2.917 | Nama, 2.917 | Frasa, ×10 | Hasil |
| --- | --- | --- | --- | --- |
| Query lama | 44,0 | 35,6 | 425 | 60 frasa kosong |
| Kandidat-dulu tanpa alias | 41,8 | 36,8 | 42,0 | **identik dengan query lama** (0 beda dari 316 kueri) |
| Kandidat-dulu + alias (dipakai) | 78,9 | 19,0 | 245 | 0 frasa kosong, recall 96,7% |
| Alias saja (tanpa jalur nama) | 22,3 | 0,5 | 78,0 | tidak layak: 91 dari 158 kueri nama kosong |
| Alias sebagai satu kolom teks di `item` | 93,2 | 74,9 | 910 | paling lambat; tidak dipakai |

Kesimpulan yang menentukan pilihan: menambah tabel alias **tanpa** mengubah
query menjadi kandidat-dulu tidak mempercepat apa pun (59 ms vs 44 ms di 2.917
baris; 538 ms vs 425 ms di ×10). Percepatan datang dari kandidat-dulu, dan
aliasnya sendiri memberi kualitas hasil.

## 6. Benchmark endpoint dan tuning (2026-10-04)

Benchmark menggunakan koneksi database yang sudah dikonfigurasi atas izin
pemilik, tetapi **hanya** transaksi PostgreSQL `READ ONLY`, `statement_timeout`
8 detik, serta GET SELECT-only `/api/items/`; tidak ada DDL atau perubahan data.
Sampel endpoint mencakup Django dan serialisasi. Dataset: 2.917 item dan 3.004
pasangan alias. Latency absolut ikut dipengaruhi koneksi remote, bukan target SLA.

Implementasi awal menjalankan COUNT dan page SELECT terpisah, setelan trigram
pada query terpisah, serta kadang menghitung ulang total. Hasil median awal
(5 request, dua warmup):

| Kueri | Total lama/alias | Overlap halaman 1 | Lama (ms) | Alias awal (ms) |
| --- | ---: | ---: | ---: | ---: |
| `zink` | 83/145 | 19/30 | 318,7 | 708,2 |
| `body kunci` | 112/203 | 8/30 | 313,4 | 767,6 |
| `bak cuci piring` | 9/130 | 1/9 | 331,4 | 781,3 |
| `keran tembok` | 373/678 | 29/30 | 337,7 | 770,4 |

Setelah threshold, hitung total kandidat, peringkat dan pemilihan halaman
digabung dalam satu SQL (7 sampel setelah dua warmup, diselang-seling):

| Kueri | Lama (ms) | Alias awal (ms) | Alias satu-query (ms) | Total lama/alias | Overlap |
| --- | ---: | ---: | ---: | ---: | ---: |
| `zink` | 302,1 | 605,3 | 362,3 | 83/145 | 19/30 |
| `body kunci` | 337,8 | 649,8 | 421,5 | 112/203 | 8/30 |
| `bak cuci piring` | 354,0 | 666,3 | 434,1 | 9/130 | 1/9 |
| `keran tembok` | 330,2 | 715,5 | 427,9 | 373/678 | 29/30 |

Tuning memangkas latency alias awal sekitar 1,5–1,7×; varian satu-query tetap
sekitar 1,2–1,3× lebih lambat daripada endpoint legacy, dan memperluas recall
secara nyata. Atas permintaan owner untuk mengoptimalkan sebelum memutuskan,
kode mengaktifkan alias otomatis bila prasyarat DB ada sambil mempertahankan
fallback query lama. Angka ini berlaku pada sesi remote terukur saja, bukan SLA.
Index trigram tetap diperlukan saat dataset membesar.

Catatan verifikasi: PostgreSQL suite tidak dapat dijalankan dengan aman karena
tidak tersedia database disposable; suite tidak dijalankan pada database yang
sama dengan benchmark. Endpoint diuji read-only langsung.

## 7. Batas jujur

- Hasil akhir masih menunjukkan regression latency alias sekitar 20–30% pada
  empat kueri ini; alias dipilih untuk recall, bukan speedup.
- Benchmark sintetis ×10 adalah duplikasi baris, bukan data toko bertumbuh
  sungguhan; ulangi pengukuran setelah index/schema berubah dan pada staging.
- Sejak peringkat dua lapis (2026-10-17), alias **tidak** bisa lagi mendahului
  item yang namanya/variannya cocok langsung; ia hanya menentukan urutan di
  dalam kelompok alias. Overlap halaman 1 pada benchmark §6 (1/9–29/30)
  diukur dengan peringkat `GREATEST` yang lama dan belum diukur ulang.
- 149 baris di sumber alias masih perlu dicek dan 37 baris tidak memiliki alias.

## 8. Langkah database tujuan

1. Pastikan tabel dan index DDL §2 tersedia pada database tujuan.
2. Impor alias dengan `tools/import_item_alias.py` (pratinjau dulu tanpa
   `--yes`; periksa laporan "item_name tak ada di tabel").
3. Tidak ada flag aplikasi untuk diatur. PostgreSQL memakai pencarian alias
   otomatis bila tabel ada; database/backend tanpa prasyarat tersebut memakai
   jalur trigram lama.
4. Uji frasa nyata dan nama barang setelah tabel alias tersedia.

## 9. Verifikasi otomatis

`pos/tests.py` → `ItemAliasSearchTests` (butuh PostgreSQL disposable, lihat
README bagian Verifikasi aman):

```bash
POS_TEST_DATABASE_URL='postgresql://user:sandi@host:5432/pos_test' \
  venv/Scripts/python.exe tools/run_pg_tests.py pos.tests.ItemAliasSearchTests
```

Tesnya mengunci frasa yang hanya ada di alias, kesetaraan pencarian nama
pada halaman pertama, fallback bila tabel alias tak tersedia, fallback ketika
query alias gagal, dan total/paginasi tepat di atas 150 hasil.
