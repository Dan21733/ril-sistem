# POS Frontend Guidelines (Cashier App)

These guidelines govern the POS frontend in this folder. They are derived from
the POS portion of the product requirement document (Figma requirements PDF
provided by the product owner). Quotes in Indonesian are kept verbatim from
that document so nothing is lost in translation.

[ARCHITECTURE.md](../../ARCHITECTURE.md) owns confirmed business decisions;
[repo rules](../../AGENTS.md) and [POS rules](../AGENTS.md) govern safe work.
This file adds frontend constraints, not a competing PRD. Source quotes below
are requirements, not evidence of implementation; later decisions can supersede
initial quotes (login, shelf location, payment methods, and receipt copies).
Review date: 2026-10-01, source inspection only. Report code/decision conflicts
instead of silently redefining requirements.

## 1. What this app is

- `pos-frontend/` is the **cashier-facing selling program** ("Program Penjualan
  untuk karyawan"). It is the worker's program, not the owner's console.
- It is one of several separate programs. Each program has its own page and
  should not display screens it does not need: "setiap program memiliki page
  yang berbeda, jadi seharusnya di setiap program tidak ada tampilan yang tidak
  di perlukan".
- Backoffice/Master, Finance, and Inventory screens are **not** built here and
  must not be scaffolded here.

## 2. Measurable objectives the UI is judged against

- "Offline-first: kasir tetap bisa jualan walau internet atau server mati".
- "Penjualan yang efektif dan effisien se minim waktu mungkin".
- "Target terukur: 5 detik per baris barang – karyawan baru aman di hari
  pertama, 80% kecepatan senior dalam 2 minggu".
- "UI sesederhana mungkin – lebih sederhana dari Moka. Learning curve kecil itu
  requirement, bukan bonus".
- "Lag / delay 'Seminim' mungkin" — use debouncing ("Debouncer untuk minim
  resource") and avoid re-render churn; a slow search box is a defect, not a
  polish item.
- "Modal barang tidak boleh bocor. Paling bawah hanya boleh tahu harga floor".

## 3. The cashier flow to design for

### Implemented online checkout contract

The current checkout request must use `POST /api/transactions/` with JSON
`{ pin, transaction }`. `transaction` contains immutable `sync_id`, nullable
`transaction_id`, `transaction_time`, amounts, `payment_method`, and item rows
with `item_name`, `variant`, `quantity`, `unit_price`, `discount`, and
`subtotal`. Never send `worker_id`. Individual checkout persists the original
PIN per nota for retry (owner approval 2026-10-01); this sensitive field must not
enter paper/history/logs. Clear after synced/definite nonfinal rejection, not
while unresolved. Batch per-nota sync is still a gap. Keep the same
transaction facts/`sync_id` for ambiguous network/server failure retries.

- HTTP 201 means a new transaction; HTTP 200 means idempotent/already stored.
  In both cases clear the cart and show the returned note ID when present.
- HTTP 400 means this attempt was rejected, not proof an earlier attempt
  never committed. Only a complete first response for a nonfinal nota permits
  definite rejection. Final/ambiguous nota → review and block new checkout.
- HTTP 409 means identity conflict; do not retry automatically and require
  human review.
- HTTP 429 means PIN throttling; show the backend's wait duration and prevent
  another attempt until the countdown completes. Read `Retry-After` or the
  wait duration in the response/message.
- Other HTTP errors/network failures are ambiguous: keep the same transaction
  identity and payload for manual retry, rather than creating a new sale.
- Cashier-selected `unit_price` is bounded by `floor_price <= unit_price <=
  price` (ceiling). If a range value is missing, show that it is unavailable
  and disable negotiation, but do not block a sale at the unchanged pricelist
  price; backend validation remains authoritative.
- The batch-sync endpoint is separate from this online-checkout request and is
  not part of this checkout slice.

### Implemented device registration & auth header

- All API calls go through same-origin `/api` paths; the Vite dev server
  proxies them to the Django backend (see `vite.config.js`), so custom headers
  never require browser CORS for this same-origin dev path. The registration
  secret is injected by the server proxy, not sent by browser code.
- Device registration is **automatic** (owner decision: cashier machines live
  on a whitelisted network). On mount the app checks Dexie `device` (id 1);
  when it is empty and the device is online it calls
  `POST /api/devices/register/` once (single-flight guard), and the proxy
  injects `X-Device-Registration-Token` from `DEVICE_REGISTRATION_TOKEN`
  (no `VITE_` prefix, so it cannot leak via `import.meta.env`) in `.env` or
  `.env.local` — both now git-ignored (see `.env.local.example`). The token never reaches the
  browser bundle and is never persisted. `{device_id, device_code,
  device_token}` is stored in Dexie. Auto-registration also runs when the
  checkout finds no stored device and when the `online` event fires; on
  failure a dismissible status modal shows the reason with a "Coba Lagi"
  button. Pending storage does not prove multiple offline sales can safely
  finish/sync; the incomplete lifecycle is documented in the offline design.
- Backend renewal/production contracts live in
  [device handoff](../DEVICE_AUTO_REGISTRATION_HANDOFF.md). Frontend sends
  `{ device_id, device_token }` renewal first (same device and code, new
  token); only a 401-rejected renewal falls back once to a fresh registration
  that may allocate a new device code. Automatic transaction batch
  sync and the complete multi-nota offline lifecycle are not wired.
- `POST /api/transactions/` and the sequence-range allocation always send
  `Authorization: Device <device_token>`. HTTP 401 means the device token is
  missing/invalid/expired: the nota stays `pending` with its original
  `sync_id`, the registration modal reopens, and the same payload is retried
  after re-registration.
- Restore/submitting/retryable/rate_limited/review/conflict lock the cart.
  Sealed pending/final/unknown nota cannot be discarded/replaced. Retry uses
  original persisted PIN/payload/receipt, not current UI values; response loss
  remains sticky unknown across reload and later rejection. Legacy records
  without recovery facts require review, not a guessed replacement PIN.
- PIN reuse does not bind a worker if server ownership changes before first
  commit. Backend detects different worker on committed retry and returns 409;
  pre-submit server binding remains unimplemented. Do not promise otherwise.
- Restored carts lacking `floor_price` are reconciled from freshly loaded item
  data; a missing range never blocks a sale at pricelist price. Reconciliation
  also refreshes a stored ceiling/`unit_price` of 0 from the catalog, because a
  `max_price` = 0 item now sells at `min_price` (see the rule below); otherwise
  a `posCart` saved while the master price was empty would still sell at Rp 0.
- **`max_price` = 0 means the pricelist is not filled in yet (2026-10-07).**
  The catalog maps items through `resolveItemPrices()` in
  `src/utils/priceRange.js`: a list price of 0 makes the selling price
  `min_price` and clears `floor_price`, so the item shows without a floor code
  and `findCartPriceProblem` uses `min_price` as its ceiling as well. A NULL
  list price stays "price unavailable"; prices are never invented from the
  floor. The backend enforces the same ceiling (`_validate_item_price`), so the
  device and the server cannot disagree about these items.

### Implemented cashier identity from PIN (2026-09-30)

- The typed cashier-name field is **gone** (KEPUTUSAN-10 in the
  [offline design](../pos-backend/OFFLINE_SYNC_DESIGN.md#7-keputusan-dan-kekurangan-yang-masih-terbuka)).
  The name on the nota comes from local PIN matching rather than typed text. A stale directory or
  PIN ownership changes can still disagree with later server attribution;
  retry no longer accepts a replacement PIN, but do not guarantee permanent
  worker binding before first server commit.
- The device keeps a cashier directory (`src/services/workers/workerDirectory.js`,
  Dexie table `workers`, fetched from `GET /api/workers/offline-list/` with the
  device token). Matching is done **on the device** with bcrypt
  (`src/services/workers/pinMatch.js`, dependency `bcryptjs`), because the
  server stores raw bcrypt hashes in `worker.pin` and therefore cannot hand out
  plaintext from a bcrypt hash. The endpoint also supports legacy plaintext
  rows and returns stored values verbatim: it does not guarantee hashed-only
  data. Treat the whole directory as sensitive; never log/copy its contents.
- Rows whose `pin_format` is not `bcrypt`/`plaintext` are ignored rather than
  guessed at (a Django-format hash has no browser verifier). A PIN that matches
  nothing leaves the name empty and shows an explanatory notice — it **never**
  blocks an online submission: the server still verifies the PIN, and
  that PIN's nota is not printed before the server accepts it (see the printing
  section).
- The directory is refreshed on the three triggers of KEPUTUSAN-9: POS opened
  while connected, finalization while online, and offline → online. Failure is
  swallowed: the old list stays usable. This can affect both name and immediate
  print eligibility; it is not merely a cosmetic failure.
- Local matching already gates **immediate printing**, but a pending row can
  still be created for an unknown PIN. The complete offline-sale gate, local
  attempt cooldown and batch sync are missing; original PIN persistence now
  supports individual checkout retries.
  Offline finalization must not use the online unknown-PIN fallback.

### Gerbang PIN sebelum segel & cooldown lokal (2026-10-08)

- **Nota tidak disegel sebelum PIN terbukti** (KEPUTUSAN-11,
  [offline design](../pos-backend/OFFLINE_SYNC_DESIGN.md) §3.1). `App.vue`
  memutuskan **sebelum** `nextTransactionId()` dan `saveTransaction()`: PIN
  cocok lokal → segel + nomor + cetak (offline tetap jalan); PIN tidak dikenal +
  server terjangkau → jalur lama (segel, cetak menunggu penerimaan server); PIN
  tidak dikenal + server tak terjangkau → **ditolak** tanpa memakai nomor dan
  tanpa baris, keranjang tetap utuh.
- "Terjangkau" ditentukan `src/services/api/serverReachability.js` dari jawaban
  HTTP server, bukan `navigator.onLine`. Panggilan API yang sukses jadi bukti;
  kalau bukti basi dan PIN tidak dikenal, halaman pertama katalog diselidik
  (timeout 4 detik, satu percobaan ulang). Hanya PIN yang **tidak** dikenal yang
  menunggu jaringan.
- **Cooldown PIN lokal** (KEPUTUSAN-12, §3.2): 5 kegagalan → 30 detik, berlipat
  sampai 10 menit, disimpan di Dexie v5 tabel `pin_attempts` — hanya hitungan
  dan waktu, tanpa PIN atau hash-nya. Status `pin_cooldown` menahan kolom dan
  tombol selama hitungan; kirim ulang nota tersimpan **tidak pernah** ditahan.
  Ini penjaga UX, bukan batas keamanan: salinan IndexedDB tetap bisa ditebak di
  luar aplikasi.
- Bukti yang ada: tes script checkout (empat kasus baru) dan pengukuran di
  browser dev — penolakan gerbang ketika permintaan API digagalkan dari halaman
  (tanpa baris, tanpa nomor terpakai, tanpa cetak), pesan `pin_cooldown` dengan
  hitungan berjalan di 1007x601 terang & gelap, dan kenaikan Dexie v4→v5 pada
  data lama. **Belum** diverifikasi: perangkat kasir dengan LAN benar-benar
  dicabut dan siklus reload/daya di tablet nyata.

### Implemented receipt printing (2026-09-29)

- Nota is printed **2 rangkap** (white original → customer, carbon → shop); the
  warehouse copy is the digital archive, not a third sheet. The printer accepts
  only one carbon copy, and one print head means both sheets carry identical text.
- Browser sends bytes through the **LAN print bridge**, never directly to the
  printer or through `window.print()`: a page
  cannot open a raw TCP socket to printer port 9100, HTTPS pages are blocked by
  mixed content from calling `http://` on the LAN, and the print path must
  survive the internet being down. The bridge is a dumb relay and knows nothing
  about nota layout; design and contract live in
  [PRINT_PATH_DESIGN.md](../pos-backend/PRINT_PATH_DESIGN.md) and the
  [reference bridge](../print-bridge/README.md).
- Layout has one source of truth: `POS/docs/desain_nota_75x65.pdf`, three pages
  (nota 1:1, enlargement with notes, and the surat jalan), implemented by
  `src/services/printer/receipt.js`. The PDF is a frozen artifact now: its
generator script was removed in the repo cleanup, so change both by hand. Font B gives 40 columns on 76 mm paper;
  every line must stay within it, and `receiptWidthIssues` is the check.
- **Bentuk left-right (permintaan pemilik, 2026-09-30).** Label di kiri,
  nilainya rata kanan pada kolom yang sama (`No. Nota` / `Tanggal` / `Kasir` /
  `Layanan`), dan baris barang memuat **jumlah** + **jumlah akhir**
  ("1 Kunci Bulat x2          34.000") — harga satuan tidak dicetak lagi, dan
  status pengambilan memakai kata **DIANTAR** (bukan "ANTAR").
- Byte building is `src/services/printer/escpos.js`: reset, Font B, ASCII-only
  text (non-ASCII is transliterated, never passed through raw), trailing feed,
  then `GS V`. The cutter is optional — on a Type D unit the same bytes simply
  end in a manual tear. The trailing feed is `DEFAULT_FEED_LINES = 8`, not 4:
  the head-to-autocutter distance is ~13 mm (3-4 printed lines), so a shorter
  feed cut the closing "Terima kasih / Barang tidak dapat ditukar" lines off
  (owner report 2026-09-30). Change that constant, not the layout, if paper
  length needs tuning.
- **Surat jalan (nota antaran) untuk transaksi DIANTAR (2026-09-30).** Satu
  transaksi diantar menghasilkan **dua** pekerjaan cetak: nota (2 rangkap) dan
  surat jalan (satu lembar, `buildDeliverySlipLines`). Keduanya disusun di
  `receipt.js` dan masuk antrean yang sama; `job_id`-nya wajib berbeda (bridge
  mengenali kiriman kembar dari `X-Print-Job`) tetapi `sync_id`-nya sama supaya
  membuang nota ikut membuang surat jalannya. Isinya: identitas toko, nomor
  dokumen (kode toko + cap waktu), `SURAT JALAN`, tanggal panjang, nomor surat
  jalan (`SJ` + nomor urut nota), lalu **baris bergaris untuk diisi tangan** —
  Kepala YTH, No Hp, Alamat, Kendaraan, No.Polisi — daftar barang **tanpa
  harga** (`2  Solasi Kunci Bulat`), dan blok tanda tangan `Penerima,` /
  `Hormat Kami,`. `VITE_STORE_CODE` mengisi awalan nomor dokumen; kalau kosong
  yang tercetak hanya cap waktunya.
- **Pemeriksaan harga sebelum cetak (2026-09-30).** `App.vue` memeriksa
  batas tersedia melalui `src/utils/priceRange.js` sebelum menulis print job.
  PIN cocok lokal memungkinkan cetak sebelum respons server;  master harga,
  stok, atau direktori basi masih dapat menyebabkan penolakan sesudah kertas
  keluar. HTTP 400 pada nota final/ambigu menahan nota untuk review tanpa
  menghapus job/bukti; kertas tidak bisa ditarik kembali. Batas harga yang tidak tersedia lokal tidak memblokir harga
  pricelist; server tetap penentu akhir.
- Checkout stores the sealed nota and original PIN before HTTP; a locally
  matched PIN finalizes locally and can print immediately. An unmatched PIN
  waits for server acceptance to print. A rejected local-final nota remains in
  review with its paper/job; no unverified offline sale may be treated as final.
- **Nota dicetak hanya setelah transaksinya final (permintaan pemilik,
  2026-09-30).** What makes a sale final depends on who can verify the PIN:
  a PIN that matches the device's own cashier directory is final on the spot, so
  its nota is queued and printed **immediately**, offline included; a PIN the
  device does not recognize is only final once the server accepts it, so
  `App.vue` queues that nota after HTTP 200/201 (`ensureReceiptPrint`). Checking
  retained jobs per `sync_id` avoids a second job on the ordinary immediate /
  post-acceptance path. This is not an atomic unique constraint or durable
  exactly-once printing: bridge deduplication is in memory and printed jobs are
  pruned after 14 days. An unmatched local PIN waits for server acceptance;
  a stale directory may still match a PIN that the server later rejects.
- The queued job is flushed **immediately** after that write, not only on the
  next successful submit or app start: an offline sale has to put paper in the
  cashier's hand while the customer is still standing there. A failed print
  **never** cancels or blocks a finalized sale; the cashier falls back to the
  hand-written nota carrying the real nota number. Final-local and ambiguous
  notes rejected later remain in review; their print jobs are retained.
- Printer status is visible in the POS header, next to the dark-mode toggle
  (`src/composables/usePrinterStatus.js`). It polls the bridge's `GET /health`
  every 30s (and on `visibilitychange`, `online`/`offline`, after every print
  attempt, and on tap) and shows **Printer tersambung** / **Printer tidak
  tersambung** with the cause in the tooltip. Three states are distinguished:
  printer answering, bridge up but printer silent, bridge unreachable — the fix
  differs for each. Status is never taken from `navigator.onLine`: the bridge and
  printer are on the LAN, so internet state says nothing about them.
- Retry survives a dead printer: jobs live in Dexie `print_jobs` with their
  bytes and `job_id`, retry with backoff, and reuse the same ID. Bridge suppresses
  duplicates only while that ID remains in memory; restart/eviction/partial
  sends can duplicate paper. Deliberate history reprint uses a new job ID.
  The queue deliberately does **not** check `navigator.onLine` — the bridge and printer are
  on the LAN, so internet state is irrelevant.
- Forbidden fields must never reach paper. `assertPrintableSale` refuses any payload
  carrying margin/HPP/floor-price keys, and the printed field set is the one in
  the design PDF: store identity, nota number, date/time, cashier, fulfillment
  status, items, subtotal, discount, ongkir line, TOTAL, payment method, amount
  paid, change. Never print floor codes, cost/margin, or customer data.
- Configuration: `VITE_PRINT_BRIDGE_URL` (default `/print`, same-origin),
  `VITE_PRINT_TOKEN` (optional; it *does* reach the bundle, so it is a LAN fence
  and not strong authentication), and `VITE_STORE_NAME` / `VITE_STORE_ADDRESS` /
  `VITE_STORE_PHONE` / `VITE_STORE_CODE` (the first three go on the nota head,
  the fourth prefixes the surat jalan document number). All four live in
  `pos-frontend/.env.local` (or `.env`), are written by
  `pos-backend/tools/setup_env.py` and reported by its `--check`; they are outlet
  data, so never hardcode them in a component. The dev server proxies `/print`
  to `PRINT_BRIDGE_TARGET`.

### Implemented nota history (per device)

- The "Riwayat Nota" button in the POS header opens `TransactionHistory.vue`,
  which lists this device's own notas (`GET /api/transactions/history/`,
  paginated, newest first) and opens the item rows of one nota
  (`GET /api/transactions/history/<transaction_id>/`).
- **Per hari, dengan pencarian kalender (permintaan pemilik, 2026-09-30).**
  Daftarnya default **hari ini saja** (`?date=YYYY-MM-DD`, hari lokal toko
  menurut `TIME_ZONE`; nota pukul 01.30 WIB tidak boleh jatuh ke tanggal UTC
  sebelumnya). Hari lain dicari lewat input tanggal, dan tombol `HARI INI`
  mengembalikannya ke hari berjalan. Tanggal yang tidak terbaca dibalas 400,
  bukan diabaikan: filter yang diam-diam tidak jalan membuat kasir melihat nota
  hari lain.
- **Tombol `CETAK ULANG STRUK`.** Ada di panel detail nota dan memakai
  `reprintReceiptFromHistory`, yang merakit ulang nota dari jawaban server
  (byte lama di perangkat sudah dibuang retensi) dan mengirimnya dengan
  `job_id` baru; transaksi diantar ikut mencetak surat jalannya. Karena tabel
  `transactions` tidak punya kolom status pengantaran (`managed = False`),
  "diantar" disimpulkan dari `shipping_amount > 0`. Ini heuristik, bukan
  status tersimpan: pengantaran dengan ongkir nol tidak dapat dikenali kembali.
- Visibility is per device enforced by the backend via the `Txx-` nota prefix;
  another device's nota answers 404. Notas without a number (offline queue
  exhaustion) do not appear until a `device_id` column exists.
- Both calls send `Authorization: Device <token>`; HTTP 401 shows the
  re-registration message instead of a generic failure.
- The response contains only sale facts (no `cost_amount`, `min_price`,
  `sku`, `moka_id`, `pin`, `modal`, `margin`); do not add such fields to
  history views, caches, or fixtures.


Business-flow reference: [Mermaid source](../docs/pos_business_flow.mmd).
It describes the business process, not proof that every offline path is
complete. The current UI offers pickup/delivery with manually entered ongkir;
external `DIKIRIM`, persisted fulfillment/address/zone, and delivery approval
still need design. Warehouse receipt is digital; DIANTAR adds a separate
surat jalan. A single-sheet slip still requires appropriate physical paper.

## 4. Source requirements and their current status

These targets come from the initial owner brief, **not an implemented-feature
checklist**. Preserve the following status when reading the source quotes:

| Source requirement | Current decision/status |
| --- | --- |
| Shelf location | Not required, decision 2026-09-26; do not add it from old quotes |
| Login / “one tab, one account” | Superseded: no worker login; device-scoped visibility |
| QRIS / other payment choices | Exactly cash, edc, bank_transfer; QRIS/cards belong to edc |
| Below-floor PIN approval | Not implemented; rejected even online until approval design exists |
| Special price lists | Owned by Master; not derived by POS |
| Address/zone and shipping-tariff approval | Required direction, not persisted/enforced today; no zone tariff reference |
| Rounding, custom amount, shifts/cash movement | Not implemented; need explicit scope/design |
| Tempo, void/return/refund | Not implemented; need Finance/Master contracts and schema approval |
| Five offline notas then sync | Acceptance target; incomplete implementation, not a passing test |
| Catalog offline (search/sell from device copy) | Installed 2026-10-08 in the frontend (OFFLINE_SYNC_DESIGN.md §3.3); device test with LAN unplugged still missing |
| Five-second search/cart target | Not benchmarked on cashier hardware |


Search and cart:

- "Cari barang yang karyawan hafal → masuk keranjang 5 detik".
- "Ketik alias ('body lidah', 'aw tiga perempat') → barang ketemu". Aliases are
  reached through the item variant ("Pengunaan alias yang di akses melalui
  varian item").
- "Hasil pencarian menampilkan harga + stok + lokasi rak bersamaan; stok 0
  tetap menampilkan harga".
- "Satu item dengan banyak varian → hasil terkelompok rapi, tetap satu tap
  masuk keranjang".
- "Membantu karyawan untuk mencari nama barang dan lokasi barang dengan cepat
  kayak melihat lokasi rak barang di hasil pencarian".
- "Custom amount — jual barang di luar katalog".

Pricing and negotiation:

- "Nego di atas floor → langsung bisa".
- "Nego di bawah floor → minta PIN, tercatat siapa yang menyetujui".
- Current negotiation is floor–ceiling. Special shop/tukang price lists are
  Master-owned future inputs, not part of the implemented POS negotiation.
- Initial wording “total ongkir + harga floor” still needs an exact aggregate
  interpretation; do not invent a new check from it.

Payment and nota:

- Current payment options: cash, edc (QRIS/cards), bank_transfer. The initial
  “and others” wording is superseded by the confirmed three-method set.
- "Pembulatan ke atas otomatis, selisihnya muncul sebagai baris tersendiri di
  nota" (kelipatan 500 / 1000).
- "Printer mati → penjualan tetap jalan"; printing goes through a wifi printer.
- "Cetak / kirim invoice untuk penjualan tempo".

Shift, history, and offline:

- "Buka shift dengan modal awal, tutup shift dengan hitung fisik, selisih
  tampil".
- "Uang masuk/keluar non-penjualan tercatat dengan alasan".
- "Penjualan tempo → langsung muncul di daftar piutang".
- "Retur / void → minta PIN; nota lama tetap ada di riwayat, ditandai".
- "Klik nota di riwayat → daftar barangnya terbuka" ("bisa melihat riwayat
  transaksi per nota dan barangnya").
- "Ganti kasir di tengah shift, penjualan tetap nempel ke orang yang benar".
  The initial account wording is superseded; no worker login/session exists.
- "Internet mati → nota tetap keluar, tersimpan, tersinkron saat online".
- "Cabut internet → jual 5 nota → sambung lagi → 5 nota muncul di server, tidak
  ada yang dobel".
- Fulfillment cost checking: "ngecek ongkir per jarak atau zone".

**Katalog offline (keputusan pemilik 2026-10-08, OFFLINE_SYNC_DESIGN.md
§3.3).** Seluruh katalog disimpan di perangkat (`items` + `catalog_meta`,
Dexie v6), disegarkan saat aplikasi dibuka dan tiap 15 menit; ketika
`/api/items/` tidak menjawab, daftar dan pencarian dilayani dari salinan itu.
Aturan yang mengikat perubahan berikutnya:

- Baris salinan **wajib** melewati `mapApiItem()` yang sama dengan jalur online
  (`src/utils/catalogItem.js`), supaya harga/stok tidak bisa berbeda antar-jalur;
  harga tetap lewat `resolveItemPrices()` (aturan `max_price = 0`).
- Pencarian lokal hanya **substring nama+varian**. Alias tidak ada di salinan
  karena `/api/items/` tidak mengirim `item_alias`; jangan mengarang alias atau
  mengklaim hasil offline setara peringkat server.
- Penggantian salinan bersifat **ganti-utuh** dalam satu transaksi bersama
  `catalog_meta.synced_at`; jawaban kosong atau halaman gagal tidak boleh
  mengganti salinan lama. Satu sinkron berjalan pada satu waktu.
- Kasir wajib bisa membedakan tiga keadaan: dari server (tanpa pesan), dari
  salinan (pesan + umurnya, "basi" setelah 1 hari — permintaan pemilik
  2026-10-12, dulu 7 hari), dan tanpa salinan sama
  sekali.
- Setiap transaksi online yang sukses memicu *pengecekan* salinan katalog di
  latar (skenario B, 2026-10-12; council 2026-10-12: only-if-stale, bukan
  paksa — unduh penuh ±98 halaman terukur ±4 menit, jadi force per nota hanya
  mengunduh ulang isi yang sama): fire-and-forget dan single-flight, tidak
  menahan kasir dan tidak menyusun ulang baris di layar; begitu pula tiap
  batch "Kirim semua" yang mengirim ≥1 nota. Gagal sync tidak mengubah status
  sukses transaksi. Pesan "tanpa salinan" diperiksa **sebelum** pesan "dari salinan":
  salinan yang lenyap saat aplikasi masih terbuka tidak boleh diumumkan sebagai
  tersedia.
- Bentuk pemanggilan API Dexie **dikunci oleh tes**, bukan oleh pembacaan kode:
  `tests/itemRepository.test.js` menjalankan `replaceCatalog()` sungguhan di
  atas `db` palsu yang menirukan Dexie apa adanya. Ini karena
  `db.items.primaryKeys()` — metode Collection yang dipanggil sebagai Table —
  pernah membuat seluruh sinkron gagal tanpa pesan di layar (2.917 baris
  terunduh lalu dibuang). Jalur layanan di-stub, jadi hanya tes seperti ini yang
  menangkapnya.

## 5. Must fail (Wajib gagal)

Active security/integrity rules must not be bypassed in UI. Initial quotes
about login, address/zone, shipping tariffs, and role checks below are **not**
claims of implemented rejection: use the status table above and architecture.
Only implemented server checks can be described as enforced today.

- "Panggil endpoint langsung tanpa lewat tampilan → tetap ditolak sesuai role".
  Never treat a hidden/hidden-by-CSS button as an authorization boundary.
- "Simpan nota tanpa memilih AMBIL SENDIRI / DIANTAR → ditolak".
- "Pilih DIANTAR tanpa alamat + zona → ditolak".
- "Ongkir 0 atau di bawah tarif zona tanpa approval → ditolak".
- "Jual di bawah floor tanpa PIN → ditolak".
- "Edit atau hapus nota yang sudah selesai → tidak ada jalannya".
- "Ubah stok dengan cara apa pun → tidak ada jalannya".
- "Ubah harga master → tidak ada jalannya".
- "Lihat transaksi kasir lain atau tab lain → tidak muncul".
- "Transaksi tanpa login → tidak bisa".
- "Buka laporan laba / margin / nilai stok → ditolak".
- "Buka harga beli atau data supplier → ditolak".
- "Lihat data mentah yang diterima tab → kata 'modal' atau 'margin' tidak ada
  sama sekali". This is a **payload** requirement, not a UI requirement: do not
  request, log, cache, or store fields the cashier role may not see. Mock data,
  fixtures, and test snapshots must not contain them either.

Cashier-program constraints in full:

- "Tidak bisa mengubah atau menghapus data penjualan".
- "Tidak boleh melihat harga beli, faktur, atau nilai barang".
- "Tidak bisa menghapus atau mengedit nota yang sudah selesai. Salah? Void atau
  retur dengan approval — jejak yang lama tetap tersimpan".
- "Tidak bisa jual di bawah harga floor tanpa PIN approval".
- "Tidak bisa memberi ongkir gratis atau diskon terselubung tanpa approval".
- "Ongkir tidak bisa dihapus atau dikurangi dari tarif zona tanpa approval".
- "Tidak bisa mengeluarkan barang tanpa nota atau surat jalan".
- "Tidak bisa melihat transaksi kasir lain atau tab lain".
- "Tidak boleh ada transaksi tanpa login — satu tab, satu akun, PIN tidak
  dibagi".
- "Barang tidak keluar sebelum nota tercatat".
- "Tidak bisa mengubah harga master — hanya nego dalam rentang floor sampai
  ceiling".

## 6. Data visibility rules for the UI

- Hidden is not enough. "Data yang tidak boleh dilihat tidak dikirim ke tab –
  bukan sekadar disembunyikan di tampilan".
- All permission checks happen on the server ("Semua pengecekan izin di server,
  bukan di browser"). A frontend check only decides what to render, never what
  is allowed.
- Every approval is attributed: "Setiap approval tercatat atas nama orang –
  tidak ada aksi anonim".
- The UI must be able to show, for each action, the actor, timestamp, and device
  or tab identity where the requirement demands auditability.
- Append-only: completed notas are never edited or deleted in the client either.
  A correction is a new event that references the original.

## 7. Initial receipt-field reference (not the print allowlist)

The original brief listed the following fields. This list is historical input,
not permission to add fields to paper or API. The implemented print field set
is described in section 3 and [PRINT_PATH_DESIGN.md](../pos-backend/PRINT_PATH_DESIGN.md);
Finance-looking totals/customer fields below remain excluded unless approved:

`Receipt Number`, `Outlet`, `Date`, `Time`, `Customer`, `Customer Phone`,
`Items`, `Payment Method`, `Amount`, `Other`, `Note (Optional)`,
`Event Type`, `Reason of Refund`, `Collected By`, `Served By`,
and totals: `Gross`, `Sales`, `Discounts`, `Refunds`, `Net Sales`, `Gratuity`,
`Tax`, `Total`, `Collected`.

Nota is printed **2 rangkap**: original for the customer, carbon copy for the
shop. A third ply is impossible on this printer (one carbon copy maximum, one
print head, so no per-copy label); the warehouse copy is the digital archive
keyed by the real nota number. The separate delivery note ("surat jalan") is a
document of its own, printed once per DIANTAR transaction, not a row inside the
nota — see "Implemented receipt printing" for its contract.

## 8. Vocabulary

- Business terms stay in Bahasa Indonesia, technical terms in English: "Istilah
  bisnis pakai Bahasa Indonesia (stok_cache, modal_rata, buka_dus,
  harga_floor), istilah teknis Inggris".
- `Buka dus` = splitting a bulk package into retail units (nails, fence roll
  fiber, thread). Retail pricing is driven by the variant pair, so the variant
  is the selling unit in the cart.
- `Varian` = "di pos memang engga ada varian, namun di pendataan varian tetap
  ada" — variant exists for backoffice grouping; in the POS cart the selling
  identity remains `item_name + variant`.
- `Nego` = bargaining between floor and ceiling price. `Tempo` = credit sale
  that becomes a receivable/invoice.
- Code comments explain why, not what: "Komentar menjelaskan kenapa, bukan apa".

## 9. Existing frontend architecture rules that still apply

- Cart key must reflect `item_name + variant`; there is no synthetic id.
- The active screen is composed by `App.vue`, `ProductList.vue`, `SearchBar.vue`,
  and `CartList.vue`. Keep that boundary unless a feature requires a targeted
  change.
- The Vue starter scaffolding has been removed: Vue Router, `src/router/`,
  `src/views/`, `TheWelcome`/`WelcomeItem`, the template icons, and the demo
  `counter` store. `App.vue` composes the only screen; do not reintroduce an
  unused router or demo store as incidental work. Pinia stays wired in
  `main.js` without any store, so do not migrate state to it incidentally
  either.
- `src/services/storage/` (Dexie) is the local persistence layer for offline
  work. Extend it rather than adding a second storage mechanism.
- Offline selling uses the last known stock snapshot. Per `ARCHITECTURE.md`:
  stock > 0 may be sold and may go negative; stock <= 0 or negative may not be
  sold. Do not substitute a "cap at available stock" rule.
- Never claim offline support for a path that is not implemented end to end.

## 10. Out of scope for this app

Do not build here: purchase orders, supplier data, cost/purchase price,
receivables and payables books, profit/margin/stock-valuation reports, stock
opname counting screens, warehouse transfers, master data editing, or role and
permission administration. Those belong to Inventory, Finance, and Super
Admin/Master programs.

## 11. Definition of done

- The feature is verified against its "Wajib bisa" criteria and its "Wajib gagal"
  criteria, including at least one offline or failure path when the feature
  touches selling, printing, or sync.
- `npm run lint` runs with `--fix` in this repo; it modifies files. Do not treat
  it as a verification command.
- No dedicated frontend testing framework or typecheck script is configured.
  Node built-in regression tests via `npm test` (runs
  tests/checkout.test.js with script-level storage/network doubles, not real
  browser/IndexedDB). `npm run build` checks the Vite bundle.
  `npm run lint:check` runs oxlint + eslint read-only; `npm run lint` uses
  `--fix` and modifies files. New test tooling needs approval.
  Use [safe verification](../../README.md#verifikasi-aman).
- "Kalau dokumentasi belum diperbarui, fitur belum selesai" — update the
  relevant documentation with the behavior change.

## 12. Aturan tampilan & tema (permintaan pemilik, 2026-09-29)

Penyebab perubahan: POS dipakai di tablet Samsung Galaxy Tab A7 Lite, dan
ukuran desktop yang mengecil di layar sentuh membuat tombol sulit ditekan.

- **Setiap perubahan UI wajib diperiksa di beberapa resolusi layar (permintaan
  pemilik, 2026-10-07).** Satu tangkapan layar di satu ukuran jendela bukan
  bukti tampilan sudah selesai: tata letak yang benar di satu ukuran bisa rusak
  di ukuran lain, dan justru layar kasir yang jadi korbannya. Sebelum pekerjaan
  UI dinyatakan selesai, periksa minimal: (1) **~360-400px** (panel/jendela
  tersempit), (2) **1007x601** tablet kasir Galaxy Tab A7 Lite mendatar dan
  tegak, (3) **>=1366px** layar lebar; ditambah **mode terang dan gelap**. Yang
  diperiksa bukan hanya "kelihatan rapi": tidak ada overflow horizontal, tidak
  ada teks terpotong, sasaran sentuh tetap >=44px, dan tidak ada kontrol yang
  turun baris lalu **menggantung di kanan sementara separuh baris kosong**
  (persis keluhan pemilik 2026-10-07 pada filter Riwayat Nota). Bukti boleh dari
  browser sungguhan dengan mengukur `getBoundingClientRect()` per ukuran atau
  tangkapan layar tiap ukuran; ukuran yang tidak diuji wajib disebut di laporan,
  bukan dianggap aman.
- **Ambang `@media` harus berasal dari ukuran konten hasil pengukuran, bukan
  tebakan.** Tulis angka pengukurannya di komentar aturan beserta alasan ambang
  itu dipilih, dan jangan menaruh ambang di titik di mana kontennya hanya muat
  beberapa piksel: perbedaan huruf/font antar perangkat cukup untuk memecahnya.
  Kalau kontennya memang tidak bisa muat sebaris, rancang bentuk dua barisnya
  supaya disengaja (label di kiri, kontrol melebar mengisi baris seperti kolom
  tanggal), bukan dibiarkan membungkus sendiri.
- **Tiga metode pembayaran sebaris (permintaan pemilik, 2026-10-08).** Cash /
  EDC / Bank Transfer berdampingan di pane yang sama, bukan bertumpuk. Kartu di
  dalam baris membagi lebar pane sama rata, dan `min-width: 136px` berasal dari
  pengukuran: kata terpanjang "Transfer" ~65px pada huruf 16px + radio 20px +
  jarak 12px + padding terkecil 28px = 125px, diberi ruang lebih supaya
  perbedaan huruf antar perangkat tidak memecah tata letak. Di panel tersempit
  (pane 263px pada jendela 365px) barisnya pecah sendiri dan ketiganya kembali
  bertumpuk; tidak ada label yang dipotong. Di baris ini huruf pilihan aktif
  **tidak** ditebalkan: pada 601x1007 tebalnya mengubah "Bank Transfer" dari
  94px ke ~103px sedangkan ruang teksnya 98px, sehingga tinggi kartu berubah
  tepat saat kasir memilih. Diukur 365x601 / 1007x601 / 601x1007 / 1440x900,
  terang & gelap: tanpa overflow, tanpa teks terpotong, sasaran >=44px.
- **Setiap perubahan UI wajib diulas lewat tangkapan layar dari server dev
  yang sedang berjalan (permintaan pemilik, 2026-10-14).** Cara memeriksanya
  bukan menebak dari CSS: buka aplikasi di server Vite yang sudah hidup
  (port 5173; jangan menyalakan server kedua di port lain), arahkan ke layar
  yang diubah, ambil tangkapan layar pada resolusi dan tema di butir pertama,
  lalu nilai hasil tangkapannya sebelum pekerjaan dinyatakan selesai.
  Tangkapan yang tidak bisa diambil wajib disebut di laporan, bukan dianggap
  aman.

- **Warna hanya lewat token.** Semua warna komponen memakai variabel `--pos-*`
  di `src/assets/main.css`. Palet pemilik: latar `#EFE8DD`, kartu/panel
  `#FFFFFF`, item di keranjang `#F7F2EB`, border `#D6C8B6`, teks sekunder
  `#6B5B4E`, teks utama `#2E2119`, aksen teal `#0F766E` (**hanya** tombol
  Bayar, tombol Konfirmasi, dan fokus kotak pencarian), status stok tipis
  `#FDEBC8`/`#8A4B08`, habis `#F6DCD8`/`#B42318`. Jangan menulis heksadesimal
  langsung di komponen; ubah tokennya. Dua pasangan status itu juga satu-satunya
  sumber warna keluarga peringatan dan bahaya pada panel "peringatan + solusi"
  (`--pos-warning-*`, `--pos-danger-*`); hentian gradien yang tidak ada di palet
  (`-deep`, `-edge`) wajib diturunkan dari pasangannya — `-deep` satu langkah
  gelap dari latarnya, `-edge` = `-deep` dicampur tinta keluarga ~20% — bukan
  warna baru yang dipilih sendiri (permintaan pemilik, 2026-10-11: warna kedua
  panel peringatan harus mengikuti palet).
- **Mode gelap.** `[data-theme='dark']` menukar nilai token yang sama; pilihan
  disimpan di `localStorage.posTheme` (`src/composables/useTheme.js`) dan
  dipasang sebagai atribut di `<html>`. Skrip kecil di `index.html` memasangnya
  sebelum Vue mount agar tidak berkedip terang.
- **Sasaran sentuh minimal 44px** (tombol `+`/`−`/hapus, tombol modal), kotak
  pencarian 58px, dan tombol Bayar 64px. Media query tidak boleh mengecilkan
  ukuran ini; yang mengecil hanyalah layar di bawah 600px.
- **Kolom uang selalu bertitik ribuan; yang disimpan tetap digit polos
  (permintaan pemilik, 2026-10-07).** Semua kolom uang — uang diterima, ongkir,
  dan jumlah uang kembali/refund — memakai `src/utils/rupiahInput.js`
  (`formatRupiahInput` untuk tampilan, `digitsOnly` untuk nilai yang disimpan,
  `preventNonNumericInput` untuk penjaga keydown). "70000" dan "700000" hanya
  beda satu nol, dan itu uang, jadi kolom yang menampilkan digit polos adalah
  kolom yang salah baca. Aturannya: state tetap digit polos supaya payload JSON
  tidak pernah berisi `"70.000"`, dan `Number(...)` di jalur kirim tetap angka.
  Menempel teks berformat (`Rp 70.000`) wajib terbaca 70000, bukan 0.
  **Jarak antar chip nominal** (saran uang tunai di keranjang) minimal 12px:
  dengan 8px empat chip terlihat seperti satu balok, terutama chip terpilih.
  Warna chip tetap token biasa, tanpa aksen teal.
- **Tombol header tidak pernah membungkus atau diperas (audit tampilan,
  2026-10-09).** Diukur di tablet kasir 1007x601: kolom daftar item hanya 599px,
  sedangkan empat tombol header berlabel panjang butuh 545px plus judul "POS"
  54px — tepat 599px, jadi flexbox menekan tombol printer sampai label
  "Printer tidak tersambung" pecah dua baris di dalam kotak 44px. Aturannya:
  (1) tombol header `flex-shrink: 0`, jadi yang terjadi adalah barisnya pindah,
  bukan tombolnya mengecil (tombol tema pernah menciut ke 38px dari 48px);
  (2) label printer punya bentuk pendek (`shortLabel` di `usePrinterStatus.js`:
  "Printer siap"/"Printer offline"/"Memeriksa...") yang dipakai di bawah
  1120px — ambang itu berasal dari kebutuhan 1007px + margin, dan pada 1121px
  label panjang terbukti muat sebaris (19px, bukan 37px); (3) label + lencana di
  tombol "Riwayat Nota" selalu sebaris (`inline-flex`, bukan `display: block`
  yang membuat lencana turun ke baris kedua dan isinya melewati kotaknya
  sendiri); (4) di bawah 480px tombol "Riwayat Nota" keluar dari kelompok
  tombol status dan mengambil satu baris penuh sendiri (label kiri, lencana
  kanan) karena empat tombol butuh 334px sedangkan jendela 360px hanya punya
  ~299px ruang isi; (5) di tablet mendatar (lebar >=901px dan tinggi <=720px,
  permintaan pemilik 2026-10-12) Riwayat Nota dipaksa sebaris pil status dengan
  merapatkan jarak SAJA — kolom header 12px→8px, antar-tombol 10px→8px,
  padding tombol 18px→14px, jarak dalam 8px/6px→6px/4px — karena sebaris penuh
  butuh ~616px dari kolom 599px di 1007x601; tombol dan huruf tidak mengecil
  dan `flex-wrap` tetap jadi pengaman; (6) pil status server tanpa teks (<=900px) dikunci
  `min-width: 44px` supaya target sentuhnya tidak ikut menyusut. Diperiksa di
  jendela 360-390px, 601x1007, 1007x601, dan 1440x900, mode terang & gelap:
  tanpa overflow horizontal, tanpa teks terpotong, semua tombol header >=44px.
- **Keranjang tidak lagi kartu penuh layar.** Lebarnya dibatasi (maks 360px)
  dan dipisah dari daftar item hanya dengan garis di sisi kirinya. Ambang
  tumpukan adalah 900px, bukan 1024px: Galaxy Tab A7 Lite melaporkan viewport
  1007x601 saat mendatar dan pada lebar itu keranjang harus tetap di sisi kanan.
  Di bawah 900px keranjang pindah ke bawah daftar item dan halaman boleh
  menggulir. Untuk layar pendek (tinggi <= 720px) yang dirapatkan hanya jarak
  dan tinggi kolom isian modal; ukuran huruf dan sasaran sentuh tidak ikut
  mengecil.
- **Baris keranjang dirapatkan tanpa menghilangkan fitur (audit tampilan,
  2026-10-09).** Satu baris barang sebelumnya 165px, sehingga di layar kasir
  601px hanya dua setengah barang terlihat. Sekarang 141px: label "Harga"
  sebaris dengan kolomnya (label di atas kolom menambah satu baris teks per
  barang), kolom harga 96x44px, jarak dalam dan antar kartu dirapatkan. Kata
  "satuan" dibuang dari label karena kode floor 7 huruf (123px) akan terdorong
  ke baris kedua; arti lengkapnya tetap di `aria-label` kolom. Semua tombol
  jari tetap 44px, dan baris harga masih bisa membungkus sendiri (sengaja,
  `flex-wrap`) kalau kolom keranjang menyusut ke batas 300px. Catatan: panel
  detail nota antrean tidak terpengaruh aturan ini.
- **Kolom harga di keranjang ikut aturan kolom uang.** Kolom "Harga" per barang
  memakai `formatRupiahInput`/`digitsOnly` seperti uang diterima dan ongkir:
  tampil `95.000`, yang dikirim digit polos. Karena `type="number"` tidak sah
  menerima titik ribuan, kolomnya berganti ke `type="text"`; batas floor dan
  plafon tetap dicek di JavaScript (pesan validasinya tetap tidak menyebut angka
  floor) dan nilainya dikembalikan ke bentuk berformat saat ditolak.
- **Harga floor tidak tampil di daftar item.** Daftar item hanya memuat nama,
  harga pricelist, dan stok. Di keranjang, **harga floor itu sendiri** tampil
  sebagai kode huruf-angka (`src/utils/floorCode.js`, pil berlabel `Floor`):
  harga dibagi satuan bulat terbesarnya, angkanya disisipkan di antara huruf
  pengisi, lalu ditutup huruf penanda satuan dan satu huruf pemeriksa.
  Penanda satuan dibaca dengan kata kunci **CANGKIR**, dari juta ke rupiah:
  `C` juta, `A` ratus ribu, `N` puluh ribu, `G` ribu, `K` ratus, `I` puluh,
  `R` rupiah. Contoh: floor 15.000 → `G` dengan angka 15; floor 80.900 → `K`
  dengan angka 809; floor 2.500.000 → `A` dengan angka 25. Kalau angkanya lebih
  dari 4 digit (misalnya 1.405.700 yang hanya bulat dibagi 100), harga
  dibulatkan ke satuan terkecil yang masih muat 4 digit dan kode diberi awalan
  `~` supaya tidak terlihat lebih presisi daripada datanya; catatan tanda `~`
  muncul di header keranjang hanya saat ada kode seperti itu. Kalau harga floor
  belum tersedia untuk sebuah item, keranjang tidak menampilkan pil kode sama
  sekali. Huruf pengisi tidak pernah memakai `I` atau `O` supaya angka 1 dan 0
  di dalam kode tidak tertukar dengan huruf; `I` hanya boleh muncul sebagai
  penanda satuan. Kode ini **pengaburan tampilan, bukan keamanan** — batas bawah
  harga tetap tidak boleh dianggap rahasia teknis, karena itu pesan validasi
  harga juga tidak boleh menyebut angka floor (cukup "di luar batas floor").
  Panduan membaca kode untuk pemilik/kasir ada di
  `POS/docs/kode_harga_floor.pdf`. Generator PDF itu
  (`POS/docs/generate_floor_code_pdf.py`, yang dulu membandingkan isinya dengan
  `floorCode.js`) sudah dihapus saat bersih-bersih repo, jadi sekarang hanya
  `floorCode.js` yang dijaga otomatis; dokumennya disamakan manual.
- **Checkout tiga langkah:** (1) konfirmasi pesanan, (2) pengambilan + metode
  bayar + pembayaran dalam satu layar, (3) PIN kasir. Modal checkout hampir
  memenuhi layar supaya langkah 2 bisa dua kolom. Aturan validasi server
  (PIN, rentang harga, bukti transfer wajib di UI) tidak berubah.
- **Modal checkout menutup sendiri (permintaan pemilik, 2026-10-09).** Setelah
  `SELESAIKAN TRANSAKSI` modal tidak boleh tertinggal di layar. Keputusannya ada
  di satu tempat, `src/utils/checkoutModal.js` (`shouldCloseCheckoutModal`), dan
  dijalankan watcher `checkoutStatus` di `CartList.vue`: status terminal
  (`success`, `retryable`, `rate_limited`, `review`, `conflict`, `restoring`),
  atau `idle` **setelah** checkout berjalan (`submitting`/`restoring`). Yang
  terakhir itu jalur offline: nota sudah masuk antrean, keranjang dilepas untuk
  penjualan berikutnya, dan notifikasi antrean di Riwayat Nota — sebelum
  aturan ini modal tetap terbuka di langkah PIN dengan kolom kosong sehingga
  nota berikutnya tidak bisa dimulai. `idle` dari keadaan lain tidak menutup
  apa pun, karena `openConfirmation()` membuka modal justru saat status masih
  `idle`. `error` dan `pin_cooldown` sengaja tetap menahan modal agar pesannya
  dibaca kasir.
- **Notifikasi antrean tinggal di Riwayat Nota (permintaan pemilik,
  2026-10-09).** Baner antrean di kolom keranjang **dihapus** karena memakan
  tinggi layar kasir. Gantinya: chip antrean di baris judul
  `TransactionHistory.vue` ("N belum tersinkron" + tombol "Kirim semua";
  angka di tombolnya dihapus 2026-10-11, jumlahnya tetap di pesannya;
  sejak 2026-10-10 chip ini bukan band di bawah judul — lihat butir berikutnya),
  tombol `KIRIM ULANG NOTA INI` di panel detail nota yang
  masih antre, dan **lencana jumlah di tombol "Riwayat Nota"** (`App.vue`)
  sebagai notifikasi selagi modalnya belum dibuka. Kontraknya tidak berubah:
  `TransactionHistory.vue` tidak mengirim apa pun sendiri — ia meneruskan
  `resend-queued` / `resend-batch` ke `App.vue`, yang tetap pemilik gerbang
  status (checkout terkunci, cooldown PIN) dan PIN asli tiap nota. Baris nota
  antrean tetap muncul di daftar dengan lencana "Belum terkirim" / "Hasil
  belum diketahui". Panel detail nota antrean membaca salinan perangkat, dan
  **total barang/ongkos kirimnya dihitung dari `payload` yang tersimpan**
  (`item_amount` payload kalau ada, kalau tidak jumlah `subtotal` barangnya):
  baris antrean tidak mewarisi `item_amount` dari respons server, jadi tanpa
  aturan ini "Total barang" tampil `RpNaN`.
- **Dua lencana angka di tombol Riwayat Nota (permintaan pemilik, 2026-10-10).**
  Kasir harus tahu dari header bahwa ada kertas yang belum keluar, bukan hanya
  bahwa ada nota yang belum terkirim. Tombolnya karena itu membawa **dua
  penanda**, dan keduanya duduk dalam satu wadah (`.history-statuses`): di
  bawah 480px tombol memakai `justify-content: space-between`, jadi dua anak
  langsung akan tersebar di tengah tombol, bukan berkelompok di kanan.
  - **Lencana angka** = nota belum tersinkron ke server (aturan lama,
    2026-10-09); jumlahnya penting karena kirim ulang dilakukan per nota.
  - **Lencana angka** = nota yang kertasnya belum keluar dari printer
    (permintaan pemilik: "Riwayat Nota 5", bukan sekadar titik); jumlahnya
    penting karena kasir perlu tahu berapa nota yang menumpuk.
  Keduanya tidak bersandar pada warna saja: tiap lencana membawa glyph-nya
  sendiri (panah-kirim untuk server, printer untuk kertas). Lencana cetak memakai
  warna **peringatan** (amber), bukan bahaya — merah hanya untuk antrean yang
  belum tersinkron dan untuk penghubung cetak yang menolak permanen.
  Sumbernya antrean cetak perangkat: `App.vue` membaca
  `listOutstandingPrintJobs()` **sekali** untuk `outstandingPrintJobId`,
  `outstandingPrintCount`, dan `outstandingPrintKey`, dan yang dilaporkan adalah
  **nota yang transaksinya terjadi hari ini**, bukan pekerjaan cetak. Job nota
  dan surat jalan disatukan per `sync_id`; tanggal memakai `transaction_time`
  nota di zona waktu lokal kasir (fallback ke `created_at` untuk job lama).
  Semua nota hari ini yang belum tercetak dihitung, termasuk job baru yang
  belum dicoba. Job hari sebelumnya tidak menambah angka hari ini, tetapi tetap
  ada di antrean dan masih dapat dicoba ulang.
  Keputusan tampilannya (`title`, `aria-label`) ada di
  `src/utils/historyButtonNotice.js`; hitungan dan kunci job ada di
  `src/utils/printDelay.js`. Tes fokus ada di
  `tests/printDelay.test.js`, `tests/historyButtonNotice.test.js`, dan
  `tests/checkout.test.js`. Batasnya: ini pemberitahuan, bukan penghalang.
  - Menutup panel cetak memakai `localStorage` dan himpunan semua `job_id` yang
    belum tercetak. X menyembunyikan peringatan bersama tombol "Cetak ulang";
    job tidak dihapus dan retry otomatis berlanjut. Job baru/perubahan antrean
    memunculkan panel lagi; penolakan permanen penghubung selalu muncul kembali.
  - Diukur 2026-10-10 (harness `reports.local/history-button-print-dot`,
    browser sungguhan, API di-stub, data sintetis) untuk desain titik lama: 14
    kombinasi → **0 masalah**. Desain lencana-angka penggantinya diukur ulang
    2026-10-10 (harness `reports.local/history-count-badges`): 360x800,
    1007x601, 1440x900 × terang/gelap → **0 masalah**; detail angkanya di
    laporan harness. Belum diuji: printer/tablet fisik, nota sungguhan, audit
    axe menyeluruh, dan lebar <360px.
- **Paragraf penjelasan istilah di panel detail nota dihapus (permintaan
  pemilik, 2026-10-10).** Di bawah tombol `VOID` / `RETUR` / `REFUND` dulu ada
  satu paragraf tetap yang menerangkan ketiga istilah itu; pemilik meminta
  tulisan itu hilang supaya panel detail nota hanya memuat nota yang dipilih.
  Yang **tidak** ikut hilang: tiga tombol aksinya, dan petunjuk kontekstual yang
  memang milik keadaan saat itu — petunjuk atas (`Menampilkan nota hari ini.`),
  petunjuk nota antrean, serta petunjuk di dalam formulir (`Nilai barang
  dicentang…` untuk retur, `Isi 0 bila uang tidak keluar dari kas…` untuk void,
  dan catatan PIN penyetuju). Jadi istilahnya tetap dapat dikenali dari
  label tombol dan dari kalimat di formulir yang memang sedang dipakai; jangan
  menambahkan kembali paragraf penjelasan umum di panel detail tanpa permintaan
  baru. Diukur 2026-10-10 dengan browser sungguhan dan `TransactionHistory.vue`
  asli (harness `reports.local/refund-hint-removed`, API di-stub, data
  sintetis): 360x800 dan 1007x601 × terang/gelap → 0 masalah, paragraf benar
  hilang, tiga tombol tetap 44px, dan blok barang di bawahnya tetap berjarak
  14px (dari `margin` `.refund-actions`) sehingga tidak menempel. Yang belum
  diuji: nota dan backend sungguhan.
- **Saran nominal uang tunai: empat tombol di layar lebar (max 4, permintaan
  pemilik 2026-10-09), tiga tombol di layar kecil `max-width: 900px`
  (permintaan pemilik 2026-10-14).** Di langkah 2, begitu `Cash` dipilih, muncul
  nominal yang langsung mengisi kolom "Uang Diterima" — satu tap menggantikan
  ketikan angka. Semesta kandidat dihitung `src/utils/cashSuggestion.js`
  mengikuti PDF pemilik (`algoritma_pembayaran_tunai_minimum_Rp500_v2.pdf`):
  semua nominal yang merupakan **pembayaran minimum** dari uang yang beredar
  (Rp500–Rp100.000) — begitu jumlahnya mencapai total, sisa uang di dompet tidak
  ikut dihitung, jadi 350.000 dan 400.000 bukan saran sah untuk total 300.000
  sedangkan 301.000 dan 310.000 sah. Dari semesta itu yang ditampilkan empat:
  uang pas (total dibulatkan ke atas ke Rp500), jangkar 50rb dan 100rb
  terkecil, lalu slot sisa diisi nominal **paling bulat**. Contoh: total
  135.000 → 135.000, 140.000, 150.000, 200.000; total 307.000 → 307.000,
  310.000, 350.000, 400.000 (semestanya enam nominal; 308.000/320.000 tersisa di
  luar karena dua slot terakhir milik jangkar). Setiap saran selalu >= total dan
  selalu bisa dibentuk dari uang yang beredar; kalau jangkar berimpit dengan uang
  pas (total kelipatan 100rb), tombol yang tampil memang kurang dari empat,
  bukan diisi paksa. Tombolnya memakai token warna biasa (tanpa aksen teal) dan
  tinggi minimal 44px. `pieces` (kombinasi lembar) dan `roundness` ikut
  dikembalikan modul tetapi **belum ditampilkan di layar**. Ini alat bantu
  kecepatan, bukan aturan bisnis: total yang harus dibayar tidak diubah,
  validasi tetap di server, dan **pembulatan ke atas sebagai baris tersendiri di
  nota masih belum diimplementasikan**.
- **Hasil transaksi + status cetak pindah ke Riwayat Nota, lalu jadi chip di
  baris judul (permintaan pemilik, 2026-10-10).** Blok di keranjang yang
  berbunyi "Transaksi berhasil disimpan" + nomor nota + "Printer belum
  menjawab…" + tombol **Cetak ulang nota** sekarang hidup di
  `TransactionHistory.vue`. Bentuknya **chip status di baris judul**, bukan band
  di bawah judul. Versi pertama memakai band; pemilik menolaknya karena memakan
  ruang daftar nota dan pesannya terlalu panjang ("supaya tidak mengambil ruang
  riwayat nota; informasi warning juga tidak perlu detail"), jadi statusnya pindah
  ke baris judul, diringkas jadi status pendek, dan kalimat lengkapnya hanya
  dibawa `title`/`aria-label`. Batasnya jelas dan wajib dijaga:
  - Pesan yang **menghalangi penjualan berikutnya** tetap di keranjang:
    validasi harga, `review`, `conflict`, `retryable`, `rate_limited`,
    `pin_cooldown`, plus "Jangan kirim ulang…" dan tombol "Kirim ulang nota
    yang sama".
  - **Status bukti transfer tetap milik keranjang** (`proofSyncMessage`), bukan
    ikut pindah: pesannya tentang nota yang baru dibuat dan riwayat sudah punya
    penanda bukti per nota. Kalau bukti masih menunggu sementara notanya sudah
    sukses, blok keranjang hanya memuat pesan itu dengan kelas `is-proof-only`
    (token peringatan, bukan bahaya). Modal riwayat **tidak** menerima
    `proof-sync-message` supaya tidak ada dua pemilik pesan yang sama.
  - Kelayakan blok keranjang dihitung `cartFeedbackVisible` /
    `cartFeedbackIsProofOnly` di `CartList.vue`; status `success` tidak pernah
    lagi menampilkan nomor nota di keranjang (nota muncul di riwayat).
  - Tombol **Cetak ulang nota** meneruskan `retry-print` ke `App.vue`, yang
    memakai `retryPrintJobNow()` lama dengan `job_id` yang sama — bukan alur
    cetak-ulang riwayat (`reprintReceiptFromHistory`) yang menerbitkan job baru
    untuk nota tertentu. Karena itu salinan teksnya menyebut bahwa status dan
    kirim ulang berlaku untuk **antrean cetak perangkat ini**, bukan hanya nota
    terakhir atau nota yang dipilih, dan tombolnya tersembunyi tanpa
    `outstandingPrintJobId`.
  - Riwayat **tidak** dibuka otomatis setelah checkout; kasir membukanya
    sendiri lewat tombol/lencana di header.
  - **Tiap pesan bisa ditutup (×), dan menutup pesan tidak menyentuh
    pekerjaannya (permintaan pemilik, 2026-10-10; panel antrean menyusul
    2026-10-11).** Tiga pesan **hasil** ditutup lewat chip-nya sendiri
    (`dismissResult` / `dismissBatch`), sedangkan dua panel "peringatan +
    solusi" — cetak dan antrean belum-tersinkron — ditutup lewat lencana bulat
    di sudut kiri atasnya (`dismissPrint` / `dismissQueue`), bentuk dari gambar
    acuan pemilik. Panel antrean dulu satu-satunya peringatan tanpa tombol tutup
    (X-nya diminta pemilik 2026-10-11); sejak itu ia memakai bentuk yang sama
    dengan panel cetak, termasuk `aria-label` "Tutup peringatan antrean: N
    belum tersinkron". Penutupnya adalah **seluruh panel/chip** (tombol 44px
    berisi teks + glyph × dekoratif `aria-hidden`, `aria-label` + `title`), bukan
    kontrol kecil di dalam pil, supaya tidak ada dua sasaran dalam satu pil.
    Batas pentingnya:
    - Ini **state tampilan** di `TransactionHistory.vue` (`dismissedResult` /
      `dismissedPrint` / `dismissedQueue`), bukan perubahan state antrean.
      `App.vue` tetap pemilik `checkoutResult`, `printSyncMessage`,
      `outstandingPrintJobId`, `queuedNotices`, dan `batchSending`; menutup pesan
      **tidak** membuang pekerjaan cetak, tidak mengirim/membatalkan kirim ulang
      nota, tidak mematikan kirim ulang otomatis (antrean tetap dicoba berkala),
      tidak mematikan lencana angka di tombol "Riwayat Nota", dan tidak
      mengubah data.
    - Yang disimpan adalah **tanda tangan isinya**, jadi pesan muncul lagi begitu
      isinya berubah: nota berikutnya (transaction/sync id baru) atau status cetak
      baru (job baru / hasil cetak baru). Menutup satu pesan tidak menyembunyikan
      pesan berikutnya. Kunci penutupan panel juga **bertahan setelah muat ulang**
      (`src/utils/dismissedNotice.js` → `localStorage`): panel cetak memakai
      himpunan `job_id` belum tercetak, panel antrean memakai himpunan `sync_id`
      yang masih antre — bukan jumlahnya, supaya nota baru memunculkan
      peringatannya lagi.
    - Konsekuensi yang disadari: selama isinya belum berubah, tombol
      "Cetak ulang nota" ikut tersembunyi bersama pesannya. Kirim ulang otomatis
      tetap berjalan, dan indikator printer di header tetap menampilkan keadaan
      perangkat — jadi ini melepas akses manual, bukan melepas pengawasan.
    - Teks chip dibungkus `role="status"` (pengganti live region band lama),
      jadi perubahan status cetak/antrean selagi modal terbuka tetap diumumkan
      pembaca layar. Glyph × dekoratif `aria-hidden`.
    - Wadah chip memakai computed yang sama dengan isinya
      (`visibleCheckoutResult || showPrintNotice || showQueueNotice ||
      batchChipVisible`), bukan prop mentah: kalau tidak, menutup semua pesan
      menyisakan wadah kosong yang masih masuk urutan fokus keyboard (regresi
      ini tertangkap saat verifikasi browser versi band).
  - **Tinggi baris chip tidak ikut isinya, dan urutannya pekerjaan dulu.**
    Chip memakai `flex-wrap: nowrap` + `overflow-x: auto` di baris judul: enam
    pil (antrean, kirim semua, hasil transaksi, cetak, cetak ulang, hasil kirim)
    yang dibiarkan membungkus terukur menaikkan header ke 133px di 1440x900,
    sedangkan satu baris geser menahannya di 44px. Urutan DOM-nya **antrean +
    aksi, lalu hasil transaksi, lalu cetak + cetak ulang, lalu hasil kirim**: di
    layar sempit barisnya bergeser, jadi yang terlihat tanpa digeser harus yang
    belum selesai — bukan pesan sukses yang bisa menunggu.
  - **Judul mengalah lebih dulu daripada chip.** `.modal-title` diberi
    `flex-shrink: 0` (+ `min-width: 0`) supaya yang menyusut adalah baris chip;
    tanpa itu di 1007x601 judulnya diperas jadi 230px dan subjudulnya pecah tiga
    baris sehingga header tumbuh ke 142px meski stripnya sendiri hanya 55px. Di
    `max-width: 900px` baris chip pindah ke baris kedua dengan `order: 1` +
    lebar penuh (urutan DOM tidak berubah), `.modal-title` kembali boleh
    menyusut dan dibatasi `calc(100% - 56px)` agar tombol × tetap sebaris dengan
    judul — tanpa batas itu tombol × selalu memilih turun ke barisnya sendiri
    (52px ekstra di 360x800, header 233px → 209px).
  - **Pesan warning diringkas; kalimat penuhnya di `title`/`aria-label`.** Tiga
    computed label di `TransactionHistory.vue`: `resultChipLabel` ('Tersimpan ·
    T01-000020'), `printChipLabel` ('Cetak tertunda' / 'Cetak selesai' /
    'Cetak ditolak' / 'Cetak dikirim ulang' / 'Status cetak'), dan
    `batchChipLabel` ('Nota berhasil dikirim' / 'Kirim gagal' / 'Perlu
    diperiksa' / 'Belum terdaftar' / 'Tidak ada nota' / 'Hasil kirim').
    Pemetaan batch
    memeriksa pola dari yang paling khusus dengan regex case-insensitive, karena
    pesan nyatanya memuat dua sinyal sekaligus ("2 nota terkirim, 1 ditolak…"
    → 'Perlu diperiksa'). Teks yang tetap panjang dipotong elipsis (`max-width:
    240px`, 200px di layar sempit) — itu disengaja, bukan bug.
  - Verifikasi 2026-10-10 (harness `reports.local/history-header-chips`,
    browser terisolasi, API di-stub, data sintetis): 8 kombinasi terukur
    (360x800, 601x1007, 1007x601, 1440x900 × terang/gelap) dengan beban
    terberat sekaligus — chip antrean + aksi kirim + hasil transaksi + cetak +
    cetak ulang + hasil kirim. Hasil: 0 masalah, `.history-notices` /
    `.queue-notice` = 0 elemen (band benar-benar hilang), jarak header→daftar
    0px di semua ukuran, tinggi header 92px (1007x601 dan 1440x900), 159px
    (601x1007), 209px (360x800), chip dan aksi 44px, kontras teks chip terburuk
    4,83:1 (terang) / 7,15:1 (gelap), tanpa overflow dokumen maupun modal, tiga
    baris nota tetap termuat. Belum terverifikasi: cincin fokus `:focus-visible`
    di baris chip yang digeser (Tab sintetis di browser otomasi tidak memicu
    `:focus-visible`); kompensasinya `padding-inline: 2px` pada baris chip.
    Belum diuji juga: data riwayat nyata, printer/tablet fisik, dan transaksi
    final sungguhan. (Verifikasi band 2026-10-10 sebelumnya — 20 kombinasi,
    0 violation axe pada modal riwayat dan keranjang — sudah tidak berlaku untuk
    band itu karena bandnya dihapus.)
- **Bentuk panel "peringatan + solusi" dari gambar acuan pemilik (permintaan
  pemilik, 2026-10-11).** Pemilik mengirim gambar satu kotak: lencana tutup
  bulat di sudut kiri atas yang menjorok keluar kotak, pesan tebal di kiri, dan
  tombol solusi berupa pil terisi di kanan. Yang berubah di `TransactionHistory.vue`:
  - **Panel, bukan lagi teks tombol tanpa kotak.** Garis panel 2px (dulu 1px),
    sudut 16px, gradien `--notice-bg` → `--notice-deep`, dan bayangan lembut
    `--pos-shadow-soft`. Pesannya 15px/700 (dulu 13px/600) supaya terbaca dari
    jarak berdiri kasir; kalimat penuhnya tetap di `title`/`aria-label`.
  - **Solusinya wilayah di ujung kanan panel, bukan kotak di dalam kotak**
    ("Cetak ulang", "Kirim semua"; perubahan 2026-10-11 atas permintaan pemilik:
    *"tombol solusi tidak perlu berada di dalam kotaknya sendiri, cukup
    dipisahkan dari tombol warning dengan garis merah seperti itu"*). Yang
    mengurungnya tetap **satu** panel; aksinya kini: `border-left` 2px
    (`--notice-border`, jadi merah di panel bahaya dan amber di panel cetak)
    setinggi panel sebagai garis pemisah, latar gradien
    `--notice-deep` → `--notice-edge` (warna kotak pilnya dulu), sudut kanan
    `0 14px 14px 0` mengikuti lengkung panel (16px luar - 2px garis), dan
    tingginya `stretch` mengikuti panel (56px, minimal 44px). Pesannya membawa
    `padding-right: 16px` sebagai jarak ke garis itu — sekaligus penjaga saat
    panelnya tidak punya aksi sama sekali (panel cetak tanpa pekerjaan).
    Labelnya tetap `--pos-text` (>=4,5:1 di seluruh gradien).
  - **Lencana tutup** (`.notice-dismiss-badge`): lingkaran 30px berisi glyph X,
    duduk di sudut kiri atas panel dan menjorok 13px ke kiri + 17px ke atas,
    seperti acuan. Kotak sentuhnya tetap tombol 44px yang tidak terlihat; yang
    berubah hanya yang terlihat kasir. Warnanya mengikuti nada panelnya.
  - **Baris panel menyediakan ruang klip** (`padding: 16px 2px; margin-block:
    -16px`): tanpa itu `overflow` baris yang menggulir memotong separuh lencana
    yang menjorok. Bantalannya dinetralkan margin negatif supaya tinggi header
    tidak bertambah karena bantalan itu — yang menambah tinggi hanya panelnya
    sendiri (56-62px → 60px, +5px header).
  - **Nada warna tetap milik keluarga token POS**, bukan warna baru: alias lokal
    `--notice-*` menunjuk `--pos-*` dan dipetakan per nada — amber (kertas belum
    keluar, `.is-print`/`.is-wait`), merah (penghubung menolak permanen,
    `.is-danger`; juga panel antrean yang belum tersinkron), hijau (`.is-ok`),
    netral (`.is-info`). `printChipTone` yang tadinya dihitung tetapi tidak
    dipakai kini benar-benar dipakai template — itu juga yang menghapus error
    `no-unused-vars` di lint. Empat token baru ditambahkan untuk gradien:
    `--pos-warning-deep`, `--pos-warning-edge`, `--pos-danger-deep`,
    `--pos-danger-edge` (masing-masing di blok terang dan gelap), nilainya
    dijaga agar teks tetap >=4,5:1 di ujung tergelap. (`--pos-warning-edge`
    menyusul 2026-10-11 saat garis keluarga amber berubah menjadi tinta palet;
    lihat butir "Keluarga amber mode terang mengikuti palet pemilik".)
  - **Notifikasi "Perlu diperiksa" dihapus (permintaan pemilik, 2026-10-11).**
    Pesan batch yang memuat hasil campuran ("2 nota terkirim, 1 ditolak…") tidak
    lagi memunculkan chip apa pun: `batchChipLabel` mengembalikan **string
    kosong** untuk pola `ditolak|konflik|belum jelas|belum diketahui`, dan
    `batchChipVisible` digerbangi label itu — bukan lagi `props.batchMessage`.
    Label kosong dipilih, bukan label lain, supaya kalimat itu tidak jatuh ke
    cabang `terkirim` dan terbaca "Nota berhasil dikirim" (lebih buruk daripada
    diam).
    Yang tidak hilang: baris yang ditolak/ditahan tetap menjadi `review` /
    `conflict` di Dexie, dan saat halaman dimuat ulang `restorePendingCheckout`
    mengunci penjualan baru sambil menampilkan pesan pemeriksaan di kolom
    keranjang — jadi perlindungannya di gerbang, bukan di chip. Chip untuk
    keadaan lain ("Nota berhasil dikirim", "Kirim gagal", "Belum terdaftar",
    "Tidak ada nota", "Hasil kirim") tetap ada.
  - **Chip kiriman yang berhasil memakai kotak sukses yang sama dengan
    "Tersimpan" (permintaan pemilik, 2026-10-11: "tombol 'kirim selesai' diganti
    menjadi 'Nota berhasil dikirim' dengan warna box yang sama dengan peringatan
    nota masuk saat online").** Kalimatnya disimpan di satu konstanta
    (`BATCH_SENT_LABEL`) karena dipakai **dua** tempat: teks chipnya dan
    penentuan warnanya (`batchChipTone` → `is-ok`, yaitu
    `--pos-success-bg`/`--pos-success-text` — token yang sama dengan chip
    "Tersimpan"). Label lain tetap netral `is-info`: hijau hanya untuk kabar
    yang memang sudah beres, karena warna itulah yang membuat kasir berhenti
    memeriksa.
  - **Daftar nota menyegarkan diri saat ada nota selesai dikirim (laporan
    pemilik, 2026-10-11: "nota 000029 yang baru saja tersinkron tidak langsung
    berubah statusnya. Harus di-refresh terlebih dahulu baru berubah").**
    Sebelumnya satu-satunya pemicu muat ulang adalah membuka modal dan mengganti
    tanggal/cakupan/filter, sehingga baris nota itu tetap terbaca "Hasil belum
    diketahui" walau kirimannya sudah berhasil. Pemicunya sekarang **isi daftar
    antrean perangkat** milik `App.vue` (`queueSignature` = `sync_id:queue_label`
    tiap nota): nota yang keluar dari antrean berarti barisnya sudah ada di
    server, jadi saat itulah daftar dibaca ulang. Kuncinya berbasis isi, bukan
    identitas array — `App.vue` menyusun ulang array antrean yang sama isinya
    ketika membacanya, dan memuat ulang karena itu hanya membuat layar berkedip.
    Detail yang sedang terbuka ikut dibuka ulang **hanya** kalau barisnya
    berpindah jenis (antrean perangkat → baris server); selain itu tidak,
    supaya fokus dan posisi baca kasir tidak terganggu.
  - **Penjaga jawaban basi pada pemuatan daftar** (`loadSequence`): dua pemuatan
    halaman 1 bisa bertumpang tindih (ganti tanggal/filter, atau penyegaran
    otomatis di atas), dan yang boleh menulis ke layar hanya yang **terakhir
    diminta**. Tanpa ini jawaban lama yang datang belakangan bisa menaruh lagi
    baris "Hasil belum diketahui" — persis keluhan yang sama, hanya lebih
    jarang. `loading` juga hanya dimatikan oleh pemuatan terakhir, supaya
    indikator tidak hilang saat masih ada pembacaan berjalan.
  - Verifikasi 2026-10-11 (harness `reports.local/sync-refresh-2026-10-11`,
    komponen nyata + backend di-stub, satu nota antre lalu keluar dari antrean
    persis seperti `App.vue` setelah `applyBatchResults`): 2 ukuran (1007x601,
    1440x900) × terang/gelap → **0 masalah**. Terukur: barisnya berubah dari
    "Hasil belum diketahui" menjadi "Server ✓" dalam 55-84ms **tanpa** muat ulang
    halaman dan **tanpa** buka-tutup modal (elemen modalnya ditandai lebih dulu;
    kalau sampai ter-mount ulang, uji ini gagal), panel "N belum tersinkron"
    ikut hilang, chip hasil kirim berbunyi "Nota berhasil dikirim" dan kotaknya
    terukur sama dengan chip "Tersimpan" (rgb(230,244,238) di terang,
    rgb(22,48,42) di gelap). Belum diuji: pengiriman sungguhan ke backend, dan
    penyegaran saat nota masuk antrean dari perangkat lain.
  - **Yang sengaja TIDAK berubah:** warna keluarga tetap pembeda nada (bukan
    bentuknya), arti "cetak ditolak selalu muncul lagi setelah muat ulang",
    penutupan per himpunan `job_id`, urutan DOM (pekerjaan dulu), dan tombol
    "Cetak ulang" tetap hanya muncul dengan `outstandingPrintJobId`.
  - Verifikasi 2026-10-11 (harness `reports.local/notice-warning-solution`,
    App.vue asli, backend di-stub mati, data sintetis): 8 kombinasi
    (360x800, 601x1007, 1007x601, 1440x900 × terang/gelap) → **0 masalah**.
    Terukur: panel 60px dengan garis 2px dan gradien, pesan 15px/700, pil
    solusi 44px bergaris 2px, kotak sentuh tutup 44px, lencana 30px yang
    menjorok ke atas panel **tanpa** terpotong klip vertikal mana pun, jarak
    antar panel 32px, kontras terburuk 4,7:1 (terang) / 5,4:1 (gelap) pada
    pesan dan 5,9:1 / 10,6:1 pada glyph X, tanpa overflow dokumen. Nada lain
    (`.is-ok`, `.is-danger`, `.is-info`) diukur dengan kelas yang dipaksa lewat
    DOM — hanya CSS-nya, bukan keadaan aplikasi yang memilih nada itu.
  - Verifikasi lanjutan 2026-10-11 (harness
    `reports.local/notice-pair-split-2026-10-11`, `App.vue` asli, backend
    di-stub mati, data sintetis yang sama): 8 kombinasi (360x800, 601x1007,
    1007x601, 1440x900 × terang/gelap) → **0 masalah**. Terukur: garis pemisah
    2px sewarna nada panelnya (`rgb(180,35,24)` / `rgb(217,164,65)` di terang,
    `rgb(244,161,149)` / `rgb(122,90,28)` di gelap) membentang setinggi panel,
    wilayah aksi menempel tepi dalam kanan panel dengan sudut `0 14px 14px 0`,
    tingginya 56px, latarnya bergradien, kontras label 7,9-12,5:1, **tidak ada
    elemen lain bergaris keliling di dalam panel** (satu kotak saja), pesan di
    tengah panel dengan jarak 16px ke garis, lencana tutup tetap 30px di kotak
    sentuh 44px dan tidak terpotong, dan tinggi header tidak berubah
    (97px di 1007x601 dan 1440x900, 164px di 601x1007, 215px di 360x800).
    Regresi yang diukur ulang di pass yang sama: baris cakupan nota dan tombol
    kirim ulang di kanan atas kartu nota (lihat butirnya) tetap 0 masalah.
    Belum: screenshot visual pass ini (webview preview tidak mengomposisi saat
    dijalankan; angka di atas dari pengukuran DOM), dan keadaan panel tanpa
    aksi sama sekali (panel cetak tanpa pekerjaan) — hanya jalur CSS-nya yang
    diuji unit.
- **Panel diperkecil, barisnya membungkus, dan tetap di sebelah judul
  (permintaan pemilik, 2026-10-11, tiga revisi hari yang sama: "box warning
  terlihat sangat besar" + "kalau ada 2 warning di layar tab bisa sampai scroll
  ke kanan dan kiri" + "masih terlalu besar, dan di layar tab malah pindah ke
  bawah").** Tiga keputusan berubah dari butir panel di atas:
  - **Ukuran ringkas, tiga kali.** Panel 60px → 52px → 48px → **36px**, pesan
    15px/700 → 13px/700 → **12px/700** (di revisi terakhir hurufnya **tidak** ikut
    mengecil; tinggi barisnya dipatok **16px**), sudut panel 16px → 14px → 12px →
    **10px**, sudut kanan wilayah aksi 14px → 12px → 10px → **8px**, bantalan
    kiri panel 12px → **10px** dan (saat ada lencana tutup) 34px → **28px**, jarak
    pesan ke garis 12px → **10px**, lencana tutup 30px → 26px → 24px → **20px**
    dengan ikon 16px → 14px → **12px**. Chip di baris yang sama ikut turun
    (44px → **36px**, sudut 22px → **18px**) supaya barisnya tidak bergerigi.
    Permintaan terakhir pemilik adalah "bukan hanya fontnya yang dikecilkan
    tetapi box nya juga", jadi memang ruang kosongnya yang dipotong: 36px =
    2x2px garis + 32px isi, dan 32px itu pas untuk 16px teks + 8px bantalan atas
    dan bawah. Panel dan teksnya tetap dibatasi `100%`/`min-width: 0` supaya
    pesan panjang tidak meluber di lebar ekstrem.
  - **Target sentuh 44px tidak hilang walau kotaknya 36px.** Wilayah aksi (32px
    terlihat), lencana tutup (40x44px), dan chip (36px) melebarkan daerah
    kliknya sendiri: wilayah aksi dan chip lewat `::after` yang menonjol 4px
    keluar panel (44px), lencana tutup dengan kotak tombolnya sendiri yang
    memang 40x44px. Celah antar baris dinaikkan 8px → **14px**, karena tonjolan
    terbesar yang bisa bertemu adalah 6px (lencana, ke atas) + 4px (chip/aksi) —
    pada 12px keduanya masih bertumpuk 5px di 360x800, dan itu terukur lalu
    diperbaiki, bukan diasumsikan. Hit-testing `document.elementFromPoint`
    mengonfirmasi daerah klik itu benar-benar dipakai browser (tepi atas, tepi
    bawah, tepi kanan, dan batas luarnya), bukan sekadar angka di CSS.
  - **Lencana tutup tepat di titik sudut kiri atas.** Pemilik melaporkan
    "tombol X nya bergeser jadi tidak tepat berada pada ujung kiri atas": pusat
    lencananya dulu jatuh 19px ke dalam dan 8px ke bawah dari sudut, sedangkan
    gambar acuannya menaruh X di sudutnya. Sekarang pusat lencana dipasang di
    **puncak lengkung sudut** panel — untuk sudut 10px itu 10 - 10/√2 =
    **2,93px** dari tepi luar, dan terukur 3,0px di seluruh 14 kombinasi uji.
    Karena `position: absolute` diukur dari tepi **dalam** garis 2px, angkanya
    `top: -8px; left: -15px` (6px ke atas dan 13px ke kiri **dari tepi luar**).
    Daerah kliknya sengaja tidak simetris: melebar ke kanan dan ke bawah (38px),
    bukan ke kiri, supaya sentuhan di ujung kanan tombol "Kirim semua" panel
    tetangga (celah 16px) tidak menutup peringatan ini, dan ujung kanannya
    berhenti di x = 27px — tiga px sebelum huruf pertama pesannya (x = 30px,
    hasil bantalan kiri 28px).
  - **Barisnya membungkus, bukan menggeser.** `flex-wrap: nowrap` +
    `overflow-x: auto` (keputusan 2026-10-10 yang menahan tinggi header) diganti
    `flex-wrap: wrap` + `row-gap: 8px`, jarak antar elemen 32px → 20px → **16px**,
    dan bantalan klip `16px 2px` + `margin-block: -16px` dihapus karena tidak ada
    lagi `overflow` yang memotong lencana. Akibatnya tidak ada lagi peringatan
    yang tersembunyi di balik geseran kiri-kanan.
  - **Baris panel tetap di sebelah judul; yang menyusut judulnya.** Ambang
    baris-penuh sempat dinaikkan 900px → 1120px, tetapi di tablet itu justru
    membuat dua peringatan **selalu** turun ke bawah judul — pemilik
    melaporkannya sebagai "malah pindah ke bawah". Sekarang `.modal-title`
    memakai `flex-shrink: 1` (dulu `0`, yang berasal dari audit 2026-10-09 saat
    judul diperas sampai 230px oleh strip chip lama yang tinggi), sehingga ruang
    yang kurang diambil dari judul, bukan dengan memindahkan panelnya. Ambang
    baris-penuh kembali **900px**: di bawah itu panelnya pindah ke barisnya
    sendiri selebar header (di sana memang tidak ada ruang untuk dibagi lagi),
    dan blok itu sekarang juga memuat `max-width: calc(100% - 56px)` untuk
    judul supaya tombol × tidak ikut turun.
  - **Harga yang disadari:** dengan dua peringatan sekaligus, tinggi header di
    jendela sempit/medium naik (601x1007: 164px → 152px; 360x800: 215px → 259px)
    karena baris kedua benar-benar dipakai, bukan disembunyikan. Di ukuran kasir
    yang dipakai (1007x601, 1024x768) dan di desktop tinggi header **92px** — di
    bawah angka awal 97px dan jauh di bawah 156px dari versi baris-penuh 1120px.
    Beban terberat (2 panel + 2 chip) diukur ulang di pass ini lewat
    `notice-compact-worstcase-2026-10-11` (panel/chip 48px) dan
    `notice-corner-2026-10-11` (36px), keduanya 14 kombinasi → **0 masalah** dan
    turun di semua ukuran: 1007x601 **143px** (sama, judulnya kini 238px),
    1024x768 **123px** (dulu 143px), 1440x900 dan 1920x1080 **123px** (dulu
    137px), 601x1007 dan 768x1024 **190px** (dulu 208px), 360x800 **341px**
    (dulu 363px).
  - **Verifikasi 2026-10-11 (empat harness):**
    `reports.local/notice-smaller-2026-10-11` (App.vue asli, backend mati,
    2 nota antre + 5 nota cetak gagal) untuk beban dua-peringatan di 7 ukuran
    (360x800, 601x1007, 768x1024, 1024x768, 1007x601, 1440x900, 1920x1080) ×
    terang/gelap → **0 masalah**; `reports.local/notice-compact-worstcase-2026-10-11`
    (2 panel + 2 chip, beban terberat yang mungkin, panel 48px) di 7 ukuran ×
    2 tema → **0 masalah** juga; `reports.local/notice-corner-2026-10-11` adalah
    pass terkecil ini (panel/chip 36px, lencana di titik sudut) di 7 ukuran ×
    2 tema → **0 masalah**; `reports.local/notice-pair-split-2026-10-11` adalah
    pass yang lebih tua (ukuran 52px). Terukur di pass 36px: panel **36px** (di
    seluruh 14 kombinasi), wilayah aksi **32px** terlihat / **44px** daerah klik
    dan menempel tepi dalam kanan panel (sudut `0 8px 8px 0`, `border-left` 2px
    sewarna nada panel), pesan dan label aksi **12px** dengan tinggi baris 16px,
    lencana tutup **20px** (ikon 12px) dengan pusat di (3,0; 3,0) dari sudut luar
    panel — titik terluar lengkung 10px adalah 2,93px — dan kotak tombolnya
    **40x44px**; chip **36px**/sudut 18px/daerah klik 44px; tidak ada dua daerah
    klik yang bertumpuk (jarak terkecil antar dua daerah klik **3px**, di
    1440x900/1920x1080 antara lencana panel cetak dan chip "Tersimpan"; 4px di
    360x800 dan 6px di ukuran lain), tidak ada daerah
    klik yang keluar dari kotak header, `scrollWidth == clientWidth` di semua
    kombinasi (tidak ada geseran kiri-kanan), dokumen dan modal tidak meluber,
    lencana tidak terpotong leluhur, dan judul + baris panelnya mulai pada baris
    yang sama (`sameLine`) di 1007x601, 1024x768, 1440x900, dan 1920x1080 —
    itulah yang dikeluhkan pemilik di revisi sebelumnya. Baris panel memang
    selalu punya luberan tak terlihat 3-4px ke bawah karena `::after` daerah
    klik, tetapi tonjolannya tetap di dalam bantalan 18px header (diperiksa
    sebagai "daerah klik keluar dari kotak header"). Belum diuji: lebar <360px,
    printer/tablet fisik, dan nota sungguhan.
  - Batas yang belum diuji: printer/tablet fisik, nota sungguhan, audit axe
    menyeluruh, lebar <360px, dan cincin fokus `:focus-visible` lencana tutup.
    Di 360x800 baris panel memang membungkus menjadi beberapa baris (header
    259px) karena judul + dua panel tidak muat sebaris.
    Tinggi header yang baru: 97px (1007x601, 1440x900), 164px (601x1007), 215px
    (360x800); angka lama 92/159/209px berlaku untuk baris chip versi sebelumnya.
- **Tombol kirim ulang nota antre pindah ke kanan atas kartu nota (permintaan
  pemilik, 2026-10-11).** Dulu `KIRIM ULANG NOTA INI` duduk di bawah paragraf
  penjelasan di blok `queue-detail-actions`; sekarang ia ada di
  `.detail-summary-head` — sebaris dengan nomor nota, waktu, dan kasirnya — dan
  hanya muncul untuk nota yang masih antre di perangkat
  (`selectedNota.local_queue`). Aksinya tidak berubah: `resendQueued(sync_id)`
  dengan PIN asli nota, tetap terkunci selama `batchSending`. Paragraf
  penjelasannya tetap di tempat lama, tetapi sekarang menunjuk "tombol di kanan
  atas". Bentuknya dibuat lebih mencolok daripada tombol netral di panel itu:
  pil terisi bergaris 2px keluarga bahaya (`--pos-danger-bg` → `--pos-danger-deep`,
  hover ke `--pos-danger-edge`), teks 13px/800, ikon panah-ulang, tinggi 44px.
  Kolom teksnya memakai dasar `flex: 1 1 120px` supaya keputusan "tombol turun
  baris" memakai ukuran tombolnya, bukan lebar teksnya — di 1007x601 tombolnya
  tetap di kanan atas, dan hanya di 360x800 ia turun ke baris kedua.
- **Peringatan bukti transfer hilang seketika saat "Kirim semua" (laporan
  pemilik, 2026-10-11: "Peringatan ini tidak langsung hilang ketika klik tombol
  'Kirim semua'. Buat menjadi langsung hilang").** Sebabnya baris bukti menunggu
  notanya lebih dulu: selama nota itu masih di antrean, barisnya tetap tampil
  walau pengirimannya sudah dimulai — dan daftar bukti hanya dibaca ulang saat
  modal dibuka atau setelah aksi per baris. Yang berubah adalah
  `TransactionHistory.vue` dan modul baru `src/utils/bannerProofs.js`:
  - **`bannerProofs`** menyaring baris yang `sync_id`-nya ada di antrean
    perangkat (`props.queuedNotices`) selama `props.batchSending` berjalan, jadi
    panelnya hilang **begitu tombolnya ditekan**, bukan setelah unggahan selesai.
  - **Daftar dibaca ulang saat pengiriman selesai** (`watch` pada
    `props.batchSending`, yang sekaligus menjalankan antrean bukti): unggahan
    yang berhasil tetap hilang, yang gagal kembali dengan statusnya sendiri —
    tidak ada keadaan yang disembunyikan secara permanen.
  - **Cakupannya sengaja sempit:** hanya bukti yang notanya sedang dikirim.
    Bukti milik nota yang sudah ada di server, serta `blocked`/`conflict`/
    `captured`, tetap tampil karena mengirim antrean tidak membereskannya.
  - **Verifikasi 2026-10-11 (harness `reports.local/proof-hide-2026-10-11`,
    `App.vue` asli, data sintetis: 1 nota antre + 1 bukti transfer yang menunggu
    nota itu):** 4 kombinasi (1007x601 terang & gelap × backend mati & hidup,
    plus 360x800 terang) → **0 masalah**. Panel bukti hilang **25-53ms** setelah
    klik "Kirim semua" (pemicunya status pengiriman, bukan jawaban server); pada
    backend hidup ia tetap hilang setelah pengiriman selesai, pada backend mati
    ia kembali dengan baris dan tombol yang sama; mode gelap sama saja.
    Tangkapan dan JSON tiap kombinasi ada di folder itu. **Belum diuji:**
    server sungguhan, jaringan lambat, bukti milik nota dari perangkat lain, dan
    printer/tablet fisik.
- **Keluarga amber mode terang mengikuti palet pemilik, dan kedua panel
  peringatan berjarak 24px di layar lebar (permintaan pemilik, 2026-10-11:
  "berikan jarak lebih antar warning. Jika di layar yang lebih kecil sudah cukup
  berjarak tanpa membuat warning nya bisa di scroll maka biarkan" + "warna kedua
  warning tidak cocok ketika light mode", dengan gambar palet sebagai acuan).**
  Yang berubah hanya mode terang di `src/assets/main.css` dan
  `TransactionHistory.vue`:
  - **Latar dan tinta amber memakai pasangan palet** `#FDEBC8`/`#8A4B08` — nilai
    yang sama dengan keluarga "stok tipis", karena bagi kasir keduanya berarti
    hal yang sama: ada yang perlu dilihat. Krem `#FDF3E0` dan mustard `#D9A441`
    yang lama tidak ada di palet, dan mustard itu hanya 2,04:1 terhadap latarnya
    sehingga tepi panelnya nyaris tak terlihat.
  - **Garis = tinta** (`--pos-warning-border: #8a4b08`; 5,8:1 terhadap latar dan
    6,8:1 terhadap putih header) — resep yang sama dengan keluarga bahaya yang
    bergaris `--pos-danger`. Keluarga bahaya sendiri **tidak** disentuh: latar,
    tinta, dan garisnya sudah dari palet, dan tepinya (`--pos-danger-edge`)
    dipakai juga oleh tombol kirim ulang nota.
  - **Token baru `--pos-warning-edge`** (`#e3c693` terang, `#7a5a1c` gelap)
    memisahkan "warna garis" dari "ujung gradien wilayah aksi": selama garisnya
    masih mustard keduanya boleh satu nilai, tetapi setelah garisnya menjadi tinta
    gelap, memakai `--notice-border` akan membuat gradien aksinya ikut coklat
    gelap. Resepnya sama dengan `--pos-danger-edge`, yaitu `-deep` dicampur tinta
    keluarganya ~20%. Nilai gelapnya sengaja sama dengan `--pos-warning-border`
    yang lama supaya mode gelap tidak berubah.
  - **Jarak antar dua panel: 24px di layar >900px, tetap 16px di bawahnya**,
    lewat `column-gap` pada `.header-notices` di `@media (min-width: 901px)`
    (8px tambahan di atas jarak baris 16px). Bukan `margin` pada panel kedua: di
    901-1006px barisnya masih bisa membungkus, dan margin akan membuat panel yang
    turun ke baris kedua menjorok 8px dari panel pertama. Ambang 901px adalah
    pelengkap ambang barisnya (`max-width: 900px`), jadi tata letak layar kecil
    tidak tersentuh sama sekali.
  - **Verifikasi 2026-10-11 (harness `reports.local/palette-gap-2026-10-11`,
    `App.vue` asli, backend di-stub mati, data sintetis 2 nota antre + 5 cetak
    gagal):** 14 kombinasi (360x800, 601x1007, 768x1024, 900x800, 901x800,
    1007x601, 1440x900 × terang/gelap) → **0 masalah**. Terukur di DOM hidup:
    jarak antar panel 24px (1007x601, 1440x900) dan 16px di <=900px (di 601x1007
    dan 768x1024 kedua panel memang masih duduk sebaris tanpa perlu digeser);
    901x800 membungkus rapi — 14px antar baris, selisih kiri 0px; warna keluarga
    amber terang latar `rgb(253,235,200)`, garis dan tinta `rgb(138,75,8)`,
    wilayah aksi `rgb(249,229,182)` → `rgb(227,198,147)`; kontras tinta/latar
    5,8:1, tinta/ujung latar 5,5:1, garis/latar 5,8:1, label/ujung aksi 9,5:1;
    mode gelap terukur identik dengan nilai sebelum perubahan (`#33280f`,
    `#7a5a1c`, `#f2c37c`, `#493814`); tidak ada geseran dokumen maupun baris di
    semua kombinasi. Tangkapan layar tiap kombinasi ada di
    `reports.local/palette-gap-2026-10-11/gap-<ukuran>-<tema>.png`.
    **Catatan yang tidak ditutup:** kontras garis keluarga amber **mode gelap**
    (2,3:1 terhadap latarnya) sudah begitu sebelum perubahan ini dan sengaja
    tidak disentuh, karena pemilik memintanya untuk mode terang. **Belum diuji:**
    tablet/matahari toko langsung, mata kasir sungguhan, simulasi buta warna
    merah-hijau, dan printer fisik.
- **Verifikasi 2026-10-11 untuk tiga permintaan pemilik sekaligus (harness
  `reports.local/history-owner-2026-10-11`, `App.vue` asli, backend di-stub mati,
  data sintetis: 2 nota antre + 5 nota yang cetaknya gagal).** Delapan kombinasi
  (360x800, 601x1007, 1007x601, 1440x900 × terang/gelap) plus 1920x1080 gelap →
  **0 masalah**: (1) lencana tutup panel antrean ada dengan `aria-label` "Tutup
  peringatan antrean: 2 belum tersinkron", dan klip/kontrasnya diukur ulang oleh
  pengukuran panel 2026-10-11 yang diimpor (lencana 30px, kotak sentuh 44px);
  (2) baris cakupan sebaris tepat saat muat (496.6px max-content): 1440x900,
  1920x1080, dan 601x1007 sebaris; 1007x601 (404.6px) dan 360x800 (228px) turun
  karena memang tidak cukup, bukan karena ambang jendela; (3) tombol kirim ulang
  menempel kanan atas kartunya (selisih atas/kanan 0px, tidak menimpa kolom teks,
  tinggi 44px, tebal 800, garis 2px, bergradien, kontras label 7,9-12,7:1 di dua
  tema), di 360x800 ia turun ke baris kedua. Tinggi header tetap angka
  2026-10-11 (97px di 1007x601 dan 1440x900, 164px di 601x1007, 215px di
  360x800). Belum diuji: **klik** tombol kirim ulangnya di harness (sengaja
  tidak diklik supaya Dexie tetap di keadaan awal; perilaku kliknya diuji unit),
  nota server (backend dimatikan sehingga kartu nota server tidak ada di
  harness), printer/tablet fisik, dan lebar <360px.
- **Riwayat nota tidak berpindah halaman.** Daftar nota di panel kiri, detail
  barang perluas ke panel kanan (`TransactionHistory.vue`), termasuk penanda
  bukti transfer tertunda.
- **Filter "Status aksi" ikut baris "Cakupan nota" (permintaan pemilik,
  2026-10-07).** Di modal Riwayat Nota, label + dropdown `Status aksi` duduk
  sebaris dengan tombol `PERANGKAT INI` / `SEMUA PERANGKAT`, di ruang kosong
  sebelah kanannya (`.scope-row` + `.scope-status-group`); baris terpisahnya
  dihapus supaya panel kiri tidak memakai satu baris ekstra. Labelnya tetap
  ditampilkan agar kasir tahu itu filter apa. Select-nya memakai token POS
  (bukan tampilan bawaan browser yang putih di mode gelap) dengan tinggi 44px,
  sama dengan tombol di sebelahnya.
  **Sejak 2026-10-11 keputusan "turun atau tidak" diserahkan pada lebar barisnya
  sendiri, bukan lebar jendela.** Media query `max-width: 1365px` dihapus karena
  itulah keluhan pemilik ("kadang status aksi turun ke bawah di layar desktop"):
  di layar lebar pun dropdownnya turun padahal ruangnya ada, sementara di jendela
  1366px ke atas ia dipaksa sebaris walau panelnya sempit. Aturannya sekarang
  satu: sebaris kalau muat, turun kalau tidak — dan saat turun kelompoknya
  mengisi penuh barisnya (label di kiri, select melebar seperti kolom tanggal di
  bawahnya), bukan menggantung di kanan. Lebar max-content barisnya terukur
  496.6px; panel 574px (1440x900, 1920x1080) dan 501px (601x1007) memuatnya
  sebaris, panel 470px (1007x601) dan 360x800 memang tidak. Jarak kolomnya 8px
  (dari 10px) dan jarak di dalam kelompoknya 6px (dari 8px) supaya baris 601px
  ikut muat.
  Namun hasil sebaris itu **tidak muat di tablet**: tombol cakupan ~283px +
  kelompok status ~209px + jarak = ~502px, sedangkan panel kiri modal hanya
  ~406px pada 1007x601. Karena itu di `max-width: 1365px` kelompok status turun
  menjadi **satu baris penuh** (label di kiri, select melebar seperti kolom
  tanggal di bawahnya) lewat `flex: 1 1 100%`; hanya di >=1366px ia benar-benar
  sebaris dengan tombol cakupan. Ambang 1365px diukur, bukan ditebak, dan tidak
  boleh diturunkan ke arah 1280px (di situ sisa ruangnya hanya ~8px).
- **Keadaan menunggu memakai satu komponen (permintaan pemilik, 2026-10-07).**
  `src/components/LoadingSpinner.vue` (prop `label`; `compact` untuk dipakai di
  dalam tombol) dipakai daftar item saat page pertama belum datang, tombol
  "Muat lebih banyak" di katalog dan riwayat, serta pemuatan riwayat nota dan
  detail barang. Warnanya token netral (`--pos-divider` + `--pos-text-muted`),
  bukan aksen teal (aksen hanya untuk Bayar/Konfirmasi/fokus pencarian), dan
  putarannya diperlambat saat `prefers-reduced-motion`. Jangan menulis animasi
  memuat baru per layar.
- **Status printer di header (permintaan pemilik, 2026-09-30).** Tepat di
  sebelah toggle mode gelap, berupa tombol dengan titik warna + teks + tanda
  panah. Hijau bertanda `--pos-success-text`, merah `--pos-danger`, abu-abu
  `--pos-text-muted` saat belum diperiksa. Di bawah 900px teksnya disembunyikan
  dan hanya titik yang tersisa — judulnya tetap membawa arti penuh untuk pembaca
  layar. Tidak ada polling lebih cepat dari 30 detik: tablet kasir dipakai
  seharian, dan printer tidak berubah keadaan tiap detik.
- **Panel detail status printer (permintaan pemilik, 2026-09-30).** Satu
  ketukan pada tombol itu membuka panel ke **bawah**
  (`src/components/PrinterStatusPanel.vue`, satu-satunya tempat UI status
  printer sekarang): `position:absolute` dari tombolnya supaya `.page-header`
  tidak ikut memanjang, `max-height:calc(100vh - 130px)` (46vh di bawah 900px)
  dan `overflow-y:auto` karena `.pos-page` memakai `overflow:hidden` — panelnya
  yang menggulir, bukan halaman. Isinya: status (`printerStatusLabel` +
  "Status printer: X · diperiksa Y"), daftar `dl` (**hanya** Nama printer,
  Alamat printer, Terakhir diperiksa), kotak `detail` saat gagal, penjelasan
  "Cara kerjanya" (tablet → penghubung cetak → printer lewat LAN, bukan
  internet), daftar bernomor "Yang perlu diperiksa" (`nextSteps` dari
  composable, satu daftar per keadaan), dan tombol `PERIKSA LAGI` yang
  memancarkan `refresh` → `refreshPrinterStatus({ force: true })` di `App.vue`.
  Nama printer datang dari `printer_name` yang diisi di bridge, jadi bridge-nya
  sendiri yang menentukan label; alamatnya dari `printer`. Menutup: klik di
  luar (listener `pointerdown` capture), Escape, atau menekan tombolnya lagi;
  listener dilepas saat `unmount`. Panel hanya menyajikan ulang data
  `usePrinterStatus` — jangan menambah panggilan health di komponen ini.
- **Bahasa panel status untuk orang toko, bukan pengembang (permintaan pemilik,
  2026-09-30).** Kata "bridge", versi bridge, dan jumlah job yang diingat bridge
  sengaja **tidak** ditampilkan: yang dibutuhkan kasir/admin cuma nama perangkat
  yang bermasalah dan langkah berikutnya. Istilah yang dipakai adalah
  **"penghubung cetak"** (program kecil di komputer toko), dan itu berlaku untuk
  semua teks yang keluar ke pemakai — `usePrinterStatus.js` (`LABELS`,
  `NEXT_STEPS`, `title`), `printBridge.js` (pesan `detail` dan
  `PrintBridgeError`), dan `printQueue.js` (`summarizePrintSync`). Nama
  modul/identifier tetap memakai
  "bridge" (`printBridge.js`, `bridge_unreachable`): yang dijaga adalah teksnya,
  bukan nama berkas. Kalau menambah pesan pemakai baru, jangan bocorkan
  `jobs_seen`, port 9101, atau kata "bridge".
- **Panduan koneksi printer untuk admin toko.**
  `POS/docs/panduan_printer_pos.pdf` adalah versi bahasa toko dari halaman
  status di atas (cara kerja jalur LAN, arti tiap warna, langkah kalau merah,
  apa yang harus dikirim ke penyedia POS). Bahasanya mengikuti aturan yang sama:
  "penghubung cetak", tanpa versi/jumlah job; nama berkas `bridge.py` hanya
  muncul di bagian teknisi. PDF itu dulu dibuat otomatis oleh
  `POS/docs/generate_printer_guide_pdf.py` (membaca default env dan nama field
  health dari `POS/print-bridge/bridge.py`, lalu berhenti dengan galat kalau
  polanya tidak cocok); skrip itu dihapus saat bersih-bersih repo, jadi
  panduannya kini artefak beku dan harus disamakan manual dengan `bridge.py`.

### Polish utilitarian hangat (disetujui pemilik 2026-10-10)

- Palet hangat, aksen teal, batas komponen, tiga langkah, dan aturan transaksi
  dipertahankan. Katalog memakai tombol native (Enter/Space dan disabled), nama
  maksimal dua baris + varian terpisah; tinggi kartu 70px dan item virtual 76px
  tetap sinkron. Nama lengkap tetap menjadi accessible name dan title. Nama yang
  sangat panjang masih dapat terpotong; jangan mengklaim semua panjang muat.
- **Satu cincin fokus saja di kotak pencarian (laporan pemilik, 2026-10-10).**
  Kolom teks selalu cocok `:focus-visible` begitu diklik, jadi aturan fokus
  global `#app :is(button, input, …):focus-visible` sempat menggambar persegi
  netral 2px (`--pos-focus`) **di dalam** kolom pencarian, sementara pembungkus
  `.search-bar:focus-within` sudah menggambar border teal + cincin 3px. Dua
  cincin bersarang itu terbaca sebagai kotak asing. Perbaikannya satu
  pengecualian di `src/assets/main.css`: `#app .search-bar input:focus-visible
  { outline: none; }`. Ini bukan "menghapus focus ring" — penandanya tetap ada
  dan bukan warna saja (border berubah + cincin); aturan global tetap berlaku
  untuk kontrol lain dan dijaga tes (`tests/checkout.test.js`), yang sekaligus
  gagal kalau `:focus-within` pembungkusnya hilang sehingga pengecualian ini
  berubah jadi bug aksesibilitas. Kolomnya sendiri tetap `border: none` +
  `background: transparent`, jadi tidak ada kotak kedua.
- Input pencarian sendiri setinggi 56px (50px pada <=599px), bukan hanya
  pembungkus 58/52px. Label bisnis memakai barang/varian. Ikon pencarian, tema,
  panah kembali, penutup riwayat dan caret printer berupa SVG inline netral.
- `src/directives/modalFocus.js` adalah lifecycle UI POS, bukan state/service
  bisnis: dipakai checkout, riwayat, dan registrasi. Saat modal dibuka, fokus
  masuk ke dialog dan  cabang latar menjadi inert; Tab/Shift+Tab tetap di dalam,
  setelah tutup fokus dikembalikan ke pemicu yang masih dapat difokuskan atau
  pencarian jika pemicu disabled/hilang/tersembunyi, termasuk BODY saat startup.
  Escape memanggil penjaga penutupan yang dimiliki komponen; pending/submitting
  checkout tidak boleh dibypass. Saat langkah berganti, dialog difokuskan ulang.
- Checkout menunjukkan indikator Pesanan / Pembayaran / PIN kasir tanpa
  menambah langkah. Gerak slide dinonaktifkan untuk prefers-reduced-motion.
- Posisi panel printer dihitung dari tombol, viewport, **dan kolom katalog**.
  Pengukuran audit: di 360/390px posisi lama -44px; sesudah perbaikan x=22px,
  lebar 316/346px, sehingga seluruh panel berada dalam kolom yang overflow hidden.
  Lebar/posisi dihitung ulang ketika dibuka dan resize; tidak ada polling baru.
- `--pos-control-border` dan `--pos-focus` memisahkan batas/fokus kontrol dari
  divider dekoratif. Rasio border terhadap field-bg 3.72:1 terang / 4.16:1 gelap.
  Nilai uang memakai tabular figures; status invalid/fokus tidak ditimpa border
  netral. Palet teks/CTA lama tidak diganti.
- Verifikasi 2026-10-10: 70 pasangan screenshot/pengukuran di 360x800, 390x844,
  601x1007, 1007x601, 1440x900; kedua tema untuk utama, pesanan, Cash, PIN kosong,
  history kosong, printer gagal, dan registrasi gagal. Tidak ada overflow horizontal/target terukur
  <44px pada scope itu; nama contoh panjang muat di kartu 70px. Browser juga
  memeriksa Enter tambah barang, fokus modal/restore, Escape, harga sah/di bawah
  floor, EDC dan transfer+ongkir tanpa upload. Semua memakai profil terisolasi,
  katalog sintetis, API mutasi dan print diblokir; tidak ada transaksi final.
  Tes Node menambah kasus lifecycle fokus dan harness memuat directive asli.
  Pemeriksaan akhir: 121 tes, lint read-only, build dan diff check lulus; regresi
  startup terkonfirmasi di browser: Escape menutup registrasi gagal yang boleh
  ditutup, inert dilepas, lalu fokus menuju Cari barang.
  Bukan bukti tablet fisik, virtual keyboard, history berdata, upload, refund,
  antrean nyata, print fisik, atau seluruh state gagal; batas ini wajib disebut.

## 13. Open questions to raise before coding

Raise these instead of inventing an answer:

- Which nota fields may the POS role actually print, given POS may not see net
  or margin? Partly answered 2026-09-29: POS prints the field set in
  `POS/docs/desain_nota_75x65.pdf` (see "Implemented receipt printing"), and the
  Finance-looking totals are excluded. Anything beyond that set is still open.
- The blackout scenario: "Mati lampu se sumatra 1 hari, listrik dan internet
  mati"; and two cashiers selling 20 sacks each offline while only 30 sacks
  exist — what is the cashier's next step on screen?
- Bargaining plus delivery where shipping cost pushes the total below
  "harga floor + ongkir": what does the cashier see, and who approves?
- ~~Whether a delivery note ("surat jalan") must be assigned and printed from
  the POS screen~~ — answered 2026-09-30: yes, printed from the POS for every
  DIANTAR transaction, one sheet, with the carrier (vehicle and plate number)
  and the recipient details filled in **by hand**. What is still open is the
  persisted fulfillment/approval design and whether `DIKIRIM` (external shipping) needs
  its own slip; do not infer an approver from an ordinary reprint.
- `DIKIRIM` (external shipping) handling: which fields, tariffs, and statuses
  apply.
- The statuses a nota can show in history (void, retur) and their exact labels.
