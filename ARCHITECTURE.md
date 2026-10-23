# Business IT Ecosystem Architecture

## Document status

This document is the source of truth for confirmed business and architectural
decisions for the business IT ecosystem. It distinguishes confirmed decisions,
the current implementation, planned functionality, and open decisions.

It does not define database schemas or detailed implementation designs for
modules that do not yet exist. Any change to a confirmed decision requires
explicit review and an update to this document.

Documentation roles: [AGENTS.md](AGENTS.md) governs safe working practices;
this file owns confirmed decisions; [README.md](README.md) covers setup and
navigation; module design documents describe contracts and implementation gaps.
No separate PRD is needed for the current POS scope. This review (2026-10-01)
compares documents with source code, not with a running production system.

## Confirmed decisions

### Overall architecture

- The system will use a **modular monolith** architecture.
- Planned modules are POS, Inventory, Finance, and Super Admin/Master.
- Modules may share one backend/system, but each has clear business
  responsibilities.
- The system is not to be designed as separate microservices unless that is
  explicitly decided later.
- Modules must not silently modify another module's authoritative data or
  behavior. Cross-module contracts must be explicitly designed before
  implementation.

### Module responsibilities

| Module | Confirmed responsibility |
|---|---|
| POS | Cashier-facing sales workflow, cart, checkout, payment collection workflow, invoice/receipt workflow, and transaction initiation. |
| Inventory | Authoritative stock, stock movements, stock adjustments, receiving, transfers, stock opname, and inventory-related approvals. |
| Finance | Authoritative financial records, revenue/payment records, receivables, payables, reconciliation, and financial reporting. |
| Super Admin/Master | Master data, users/workers, roles/permissions, pricing controls, approvals, supplier management, and financial/inventory oversight. |

### Inventory ownership and selling rule

- Inventory is the source of truth for stock. POS must not directly own
  authoritative stock.
- A sale is permitted when current authoritative stock is greater than zero.
- The quantity sold may exceed current stock; resulting stock may be negative.
- A sale is rejected when current authoritative stock is zero or negative.

Example:

```text
stock = 5, sale quantity = 8  -> allowed, resulting stock = -3
stock = 0, sale quantity = 1  -> rejected
stock = -3, sale quantity = 1 -> rejected
```

This intentional rule must not be replaced with a conventional rule that caps
sale quantity at available stock.

### Offline-first POS

- Offline-first POS is a confirmed requirement. Scope 2026-10-09 (owner decision): offline means the backend is unreachable; the frontend server is assumed alive. A page reload while the frontend server is down is an accepted limitation, and PWA/service worker is out of scope.
- While offline, POS uses its last known/cached stock snapshot.
- If cached stock is greater than zero, the item may be sold; sale quantity may
  exceed cached stock.
- If cached stock is zero or negative, the item cannot be sold.
- Offline transactions are stored locally and queued for synchronization.
- When connectivity returns, the backend/Inventory becomes authoritative.
- Synchronization must prevent duplicate transactions, duplicate financial
  records, and duplicate stock deductions.
- Offline synchronization is not implemented by this decision document.

### Finance ownership and payment

- Finance is the source of truth for financial records and reconciliation.
- POS owns the cashier-facing sales and payment workflow.
- POS must not expose financial information that its role is not authorized to
  see.
- Split payment is not part of the initial design.
- A different payment arrangement is treated as another transaction according
  to the business process.
- Confirmed payment method set (2026-09-26): exactly three methods — `cash`,
  `edc`, and `bank_transfer`. `edc` covers QRIS and card payments; QRIS and
  card are not separate payment methods. The API literals are
  `cash` / `edc` / `bank_transfer`.
- A split-payment model must not be invented.

### Worker and cashier identity

- POS has no worker account, username/password, or login system. Do not add one
  without an explicit new requirement.
- Backend verification of the entered PIN determines the authoritative worker
  and records its Worker ID. Frontend must not select or send `worker_id`.
- Online transactions require PIN verification before server persistence.
- The typed cashier-name field was removed on 2026-09-30. The name on the nota
  comes from local PIN matching; that display name is not server identity.
- Visibility is per registered device/nota prefix, not per worker session.
- The 2026-09-26 offline-no-PIN and one-PIN-per-batch decisions were superseded
  by the revision below. The old batch contract still exists in code; this is
  an implementation gap, not a reversal of the newer decision.

### Offline PIN rule revision (confirmed 2026-09-29)

The owner has decided that the PIN must finalize every offline sale. This
supersedes the 2026-09-26 rules that offline PIN is not required, that the queue
never holds anything PIN-related, and that a batch sync verifies one PIN per
batch.

- **Offline PIN is mandatory.** A nota may only be created and printed after a
  PIN was entered and verified on the device. With no PIN, or with a PIN that
  does not match, no nota is created and nothing is queued. There is no
  unattributed fallback: a cashier who cannot supply a PIN cannot sell while
  offline.
  **Implemented on the device 2026-10-08 (owner decision):** the seal step now
  runs *before* numbering and local storage, so an unrecognized PIN while the
  server cannot be reached leaves no nota, spends no nota number, and queues no
  print job. Reachability is proven by an HTTP answer from the server, not by
  `navigator.onLine`. The pre-existing online fallback is unchanged: an
  unrecognized PIN may still be sent while the server answers, and its nota is
  final only after the server accepts it. See
  [OFFLINE_SYNC_DESIGN.md](POS/pos-backend/OFFLINE_SYNC_DESIGN.md) §3.1.
- **Verification happens on the device**, using a worker list issued by the
  backend and stored in IndexedDB. The typed PIN is matched against that list,
  and what it yields is the worker **name for the nota**. No `worker_id` is
  stored in the frontend: identity stays server-side, and the backend resolves
  the worker from the PIN at sync.
  **Corrected 2026-09-30, when the endpoint was built:** the list is *not*
  plaintext `{name, pin}`. The external `worker` table holds raw bcrypt hashes
  in its `pin` column, so the backend cannot hand out PINs it does not have;
  the endpoint issues stored values verbatim plus a format label
  (`GET /api/workers/offline-list/`, device token required), and the device
  matches with bcrypt in the browser. The 2026-09-29 plan for a per-worker
  PBKDF2 verifier stays dropped, but the "no hash in the browser" rule stays
  superseded in the direction of *hashes*. The endpoint also supports legacy
  `plaintext` values; it returns stored values rather than guaranteeing that
  every row is hashed. See the sensitive-data caveats in
  [OFFLINE_SYNC_DESIGN.md](POS/pos-backend/OFFLINE_SYNC_DESIGN.md).
- **Super Admin/approver PINs never enter the offline worker list.** Void,
  return,
  price override, and every approval action stay online and server-verified, so
  a device that leaks cannot unlock approver authority by sweeping a PIN.
  **Deferred 2026-09-29:** no approver role exists yet (the `worker` table has
  only `id`, `name`, and `pin`, and `transaction_service.py` states the schema
  cannot store an approver). Until the approval design exists, the offline
  worker list contains every worker, and that design must then exclude the
  approver. The same applies to "active workers": there is no active flag, so no
  further filtering is currently possible.
- **Local attempt throttling is mandatory**: an escalating cooldown persisted on
  the device after repeated failures, so a person holding the tablet cannot
  sweep PINs interactively.
  **Implemented 2026-10-08 with these parameters:** five failures block the next
  attempt for 30 seconds, each further failure doubles it up to a 10-minute cap,
  and the count resets on a locally matched PIN or server acceptance. Only
  counts and times are stored — never the PIN or a hash of it, because a hash of
  a six-digit space is a cracking aid. The owner may change the numbers without
  touching any server contract. See
  [OFFLINE_SYNC_DESIGN.md](POS/pos-backend/OFFLINE_SYNC_DESIGN.md) §3.2.
- **Refresh (three triggers, confirmed 2026-09-29):** POS is opened while
  connected; the cashier finalizes a transaction while online; the device
  returns from offline to online. A refresh replaces the whole list, so removed
  workers disappear and new workers appear. A list that has not been refreshed
  for a long time keeps accepting a PIN that was changed meanwhile, so the owner
  needs a staleness warning rather than discovering it at the counter.
- **Batch sync carries the typed PIN per nota, and the PIN is not a gate.**
  Because the frontend stores no `worker_id`, the only way the backend can
  attribute a nota is from its PIN, so each queued row carries one. The backend
  verifies once per **distinct** PIN in the batch (never once per nota, which
  would mean hundreds of bcrypt table scans) and attributes each nota to the
  worker that its PIN resolves to. An unmatched PIN prevents attribution;
  matched PINs do not waive server-side sale validation (items, price, stock,
  numbering, `sync_id` idempotency), which can also reject or conflict.
- **Printing is best effort and never part of finalization.** The order is PIN,
  then queue write, then show the nota. If the WiFi printer is unreachable —
  including when the router itself is down, not only the ISP — the cashier
  hand-writes the nota using the **real nota number** shown on screen (for
  example `T01-000001`), never the temporary local number. The device must
  therefore keep a reserve of allocated numbers and top it up while online, and
  the offline nota preview must be complete and legible: number, items,
  quantities, prices, totals, payment method, change, and the cashier name from
  the PIN. A print failure must never cancel a finalized sale. Payment proof
  photos follow the nota in the same sync cycle but as a separate idempotent
  multipart request,  never inside the batch JSON body.
- **The typed cashier name field is removed (decided 2026-09-29, implemented
  2026-09-30).** The name printed on a nota comes from the PIN-resolved worker
  in the offline worker list, rather than an independently typed name. The
  intent is consistent attribution. Retry now preserves the original PIN and
  name snapshot; a stale directory or reassignment of that PIN before the first
  server commit can still disagree with server attribution. The field was never
  persisted server-side (the transaction serializer has no such field and the
  `transactions` table has no such column), so there was no data to migrate.
  A PIN the device does not recognize may still be sent to the server on the
  online path, but printing waits for HTTP 200/201. This online fallback does
  not permit an unverified offline sale.
- **Accepted risk, recorded rather than treated as a bug:**
  - Device storage exposes the worker directory to offline guessing. Bcrypt
    slows guesses but a six-digit PIN has only 10^6 possibilities; cracking
    time depends on hash cost and hardware and has not been benchmarked here.
    Legacy plaintext rows, if present, expose their value directly. Original PIN
    persistence for individual checkout retry is also sensitive, not protected
    merely because the directory uses hashes.
  - A modified client can skip local checks. Server PIN-derived attribution,
    validation, numbering, idempotency, token TTL, and review are separate
    controls; they do not make device storage secret.

**Required sync behavior:** per-nota PIN verification must not consume the
shared online PIN-attempt throttle. Verification for attribution still happens;
"no batch PIN gate" does not mean "no PIN verification".

**Implementation gap (source review 2026-10-01):** the current batch serializer
and view still require one top-level PIN, attribute the entire batch to that
worker, and use the shared throttle. Frontend does not call this endpoint.
Individual checkout now persists its original typed PIN for retry, clearing it
on confirmed success or definite nonfinal rejection. Local matching gates immediate printing,
but not creation of every pending row; local cooldown and the complete offline
lifecycle are still missing. Details and current vs target payloads live in
[OFFLINE_SYNC_DESIGN.md](POS/pos-backend/OFFLINE_SYNC_DESIGN.md).

### Receipt copies and print path (confirmed 2026-09-29)

- **A nota is printed 2 rangkap, not 3.** The Epson TM-U220B supports one
  original plus one carbon copy only (Epson's TM-U220 reference: "TM-U220 can't
  print on carbon roll paper with 2 copies"), so the former "customer / shop /
  warehouse" wording cannot be a single print. There is also only one print head
  and one pass, so both sheets carry identical text and no per-copy label is
  possible. The confirmed mapping: original sheet → customer, carbon sheet →
  shop (physical archive). The warehouse copy is the digital archive looked up
  by real nota number, not a third ply. A separate surat jalan for `DIANTAR`
  was requested and implemented on 2026-09-30: one document without prices,
  recipient/carrier details filled by hand. `DIKIRIM` handling remains open.
  Single-sheet output still requires the appropriate physical paper; software
  cannot suppress the carbon copy when a two-ply roll is loaded.
- **POS sends ESC/POS from the browser to a LAN print bridge, never directly
  to the printer's raw socket and never through `window.print()`.** A browser
  cannot open a raw TCP socket to printer port 9100, an
  HTTPS-served POS page is blocked by mixed content from calling `http://` on
  the LAN, and the print path must keep working when the internet is down. The
  bridge is a dumb relay: it forwards ESC/POS bytes to the printer and knows
  nothing about nota layout. A reference bridge (`POS/print-bridge/`, stdlib-only
  Python, `POST /print` + `GET /health`, `X-Print-Job` idempotency) and the
  POS-side print path (`pos-frontend/src/services/printer/`: ESC/POS encoder,
  40-column receipt layout, bridge client, durable `print_jobs` retry queue) now
  exist. Who runs and updates the bridge, and how POS reaches it in production,
  is still undecided.
- **The cashier must see printer connectivity (2026-09-30).** This status
  does not detect paper, ribbon, jam, or cutter readiness.
  The POS header shows `Printer tersambung` / `Printer tidak tersambung` next to
  the dark-mode toggle, from a periodic `GET /health` poll. The bridge reports
  two separate things — bridge alive, and printer accepting a connection
  (`printer_status`, probed without sending a single byte) — so the screen can
  tell a dead printer from a dead bridge, which need different fixes. Status is
  never inferred from `navigator.onLine`; the bridge and printer are on the LAN.
  The button also expands downward into a detail panel: printer name as
  configured on the bridge (`PRINTER_NAME`, a label rather than detection),
  printer address, when the status was last checked, and the fix steps for the
  state at hand. The panel deliberately talks like a shop, not like the code:
  it says "penghubung cetak" instead of "bridge" and hides version and job
  counters, because a cashier only needs to know which device to go look at.
  A plain-language guide for the shop admin — how the LAN chain works, what
  each status means, what to send when it stays red — is generated from the
  bridge contract into `POS/docs/panduan_printer_pos.pdf`.
- Detail and the measurement list live in
  [PRINT_PATH_DESIGN.md](POS/pos-backend/PRINT_PATH_DESIGN.md).
  Ordering is unchanged: PIN, then queue write, then print, with print failure
  never cancelling a finalized sale and a reprint queue that survives a dead
  printer. The queued job is flushed immediately, so an offline sale puts paper
  in the cashier's hand while the customer is still at the counter.

### Offline numbering, rejection trace, visibility, and PIN throttling (confirmed 2026-09-26)

- Real nota numbers remain `Txx-NNNNNN` (10 characters, no date). The earlier
  exhaustion rule allowed a temporary display number and nullable server ID.
  The 2026-09-29 handwritten-nota rule requires a real number and proactive
  range replenishment. How to complete a sale when that reserve is exhausted
  remains unresolved; do not silently approve a new fallback or rejection.
  Current frontend may return null when allocation fails or the range is empty;
  backend accepts and stores null but does not assign a number at sync. No
  temporary-number display or proactive low-water refill is wired. See
  [OFFLINE_SYNC_DESIGN.md](POS/pos-backend/OFFLINE_SYNC_DESIGN.md#7-keputusan-dan-kekurangan-yang-masih-terbuka).
- A transaction rejected during batch sync stays in the device queue and
  remains visible with the rejection reason; the server additionally writes a
  structured log entry for each rejection. No rejection table is introduced
  now; a queryable server-side representation belongs to the audit/event
  design.
- History visibility: a device may only read transactions belonging to its own
  tab, enforced through the device code embedded in the nota number prefix
  (`Txx-`). Transactions still without a number cannot be scoped this way until
  an explicit device reference exists on the transaction. Because the prefix is
  client-supplied, the backend must verify it was issued by the authenticated
  device; history scoping must not rely on an unverified client value.
- PIN attempt throttling uses Django's cache framework with a shared file-based
  cache across processes on one host, plus DRF throttling and a short lockout
  window after repeated failures. Redis is not required; if the deployment
  later spans hosts, the cache backend is swapped without changing the
  throttling code.

### Security hardening implemented (2026-09-26, code status)

- **Device auth is mandatory** on both transaction endpoints
  (`POST /api/transactions/` and `POST /api/transactions/sync/`); requests
  without a device token get 401. The device code from the token is now the
  authority for transaction attribution.
- **PIN throttling is active** (`pos/pin_throttle.py`): 10 failed attempts
  lock out the source (device code, or IP when unauthenticated) for 5 minutes;
  locked-out requests get 429 with `Retry-After`. Counters live in
  `FileBasedCache` (`.pin_throttle_cache/`), no Redis.
- **Device token TTL is 30 days** (`DEVICE_TOKEN_MAX_AGE_SECONDS`) for transaction
  authentication; expired tokens answer "Device token has expired." The owner
  confirmed (2026-09-27) that registration renews the same device: the backend
  accepts its signed prior token for renewal for up to 60 days after issue, and
  returns a fresh token without allocating another T01–T99 code. Initial
  registration still creates a new device. If the old token is beyond that
  renewal window, device retirement/code recycling remains undecided.
- Registration-token trust decision (2026-09-27): `X-Device-Registration-Token`
  remains authoritative; network whitelisting is defense-in-depth only. No
  backend source-IP allowlist is implemented. Production must keep the secret
  server-side via same-origin/reverse-proxy injection on
  `/api/devices/register/`; cross-origin browser registration would require a
  reviewed CORS/security change.
- **Nota prefix verification**: the `transaction_id` prefix must match the
  authenticated device's `device_code`; a device can no longer forge another
  tab's nota numbers. Null `transaction_id` (offline exhaustion) bypasses this
  check by design.
- **Change amount by payment method**: non-cash payments must exactly match
  the total; overpayment with change is only valid for cash.
- Hygiene: `/admin/` route removed (no admin site — no accounts), duplicate
  `SecurityMiddleware` removed, `CorsMiddleware` moved first so error/preflight
  responses carry CORS headers.

### Completed transactions and corrections

- Completed transaction records must not be edited or deleted.
- Business records follow an append-only principle.
- A correction after completion is a new recorded action/event that references
  the original transaction; the original remains visible in history.

### Ambiguous checkout recovery (confirmed 2026-10-01)

- An unanswered submit is not a failed sale: server persistence may have
  completed. Preserve its `sync_id`, nota number, timestamp, items, amounts,
  payment, original PIN and receipt snapshot across retry/reload.
- Store the original typed PIN per nota for individual retry, as approved by
  the owner; never accept a replacement PIN from retry UI or expose/log it.
  IndexedDB is sensitive device storage, not encryption. Clear this PIN after
  confirmed server success or definite rejection of a nonfinal first attempt;
  keep it while recovery/review is unresolved.
- A draft cart is editable. Once sealed for submission it is not replaced or
  discarded. Track local finality independently of server confirmation.
  An earlier unknown result remains unknown despite a later 400/401/429.
- A locally final/printed nota later rejected is retained for manual review,
  together with payment proof and print jobs. New checkout is blocked while
  review/conflict or an unresolved nota exists. No void, repair, or approval
  operation is authorized by this rule.
- A fully answered first 400 for a nonfinal nota can be a definite rejection;
  retain its record/reason/proof, then allow correction of the draft. Legacy
  unresolved/rejected records without original credentials cannot be safely
  upgraded by guessing a new PIN; require review.
- Backend idempotent success requires matching stored sale facts and the
  PIN-resolved worker, not merely matching IDs. Changed facts/worker return
  409 without changing the stored sale. No new server fields, schema or lookup
  endpoint are introduced.
- Boundary: storing the same PIN is not a permanent worker binding. If that
  PIN is reassigned before the first commit, existing schemas cannot prove its
  former owner. A retry against an already committed sale detects a different
  worker and conflicts; server-issued pre-submit binding remains future work.
- Number-null device scoping, device renewal, complete multi-nota offline sync,
  physical print exactly-once, and a manual-review resolution workflow remain
  separate gaps. This safeguard does not claim they are solved.

### Void

- Void is used when a transaction was entered incorrectly or a customer
  genuinely cancels an order.
- A void does not delete or edit the completed transaction.
- A void creates a new recorded action/event that references the original
  transaction and preserves it.
- Void always requires Super Admin/Master approval, the approver's PIN, an
  identified approver, a required reason, and audit information.
- The exact internal database representation remains an implementation
  decision.

### Return and refund

- The business supports returns and refunds.
- A return is used when a customer returns goods, including defective
  production or a requested replacement.
- When an item is physically returned, Inventory receives the item back and
  stock effect is positive.
- A refund returns money to the customer. The normal/preferred refund method
  for the business is cash; a refund is not required to use the original
  payment method.
- An exchange is not a separate transaction type. It is processed as a
  return/refund of the original item followed by a new sale for the replacement
  item.
- Return/refund preserves the original transaction, creates a new recorded
  action/event referencing it, and requires Super Admin/Master approval, the
  approver's PIN, an identified approver, a required reason, and audit history.
- Figma requirements include refund information, event type, refund reason,
  PIN-required return/void actions, and retention of the original note in
  history.
- Additional refund rules must not be invented.

### Business meanings

| Term | Confirmed meaning |
|---|---|
| Void | Cancel/invalidate a completed transaction because of cashier error or genuine order cancellation. |
| Return | Customer returns goods. |
| Refund | Money is returned to the customer as part of a return/refund process. |
| Exchange | Not a separate transaction type; return/refund the original item, then create a new sale for the replacement item. |

### Approval and anti-fraud controls

- Return/refund and void always require Super Admin/Master approval.
- A reason is always required.
- Approval must identify the approving person; anonymous approval is not
  allowed.
- Server-side authorization is authoritative. Frontend-only permission checks
  are insufficient.

### Pricing and visibility

- POS must respect floor price and ceiling/list price.
- Selling above floor is allowed within permitted pricing rules.
- Selling below floor requires Super Admin/Master approval and must record who
  approved it.
- POS workers must not receive cost price, margin, supplier information,
  profit, inventory valuation, or other restricted financial information.
- Restricted data must not merely be hidden in the UI; unauthorized data must
  not be sent to the client. Authorization is enforced server-side.
- Confirmed negotiation mechanism (2026-09-26): a cashier may set the selling
  price freely as long as it stays within `floor <= unit_price <= ceiling`, and
  needs no owner approval inside that range. Prices below the floor or above
  the ceiling are rejected by the backend.
- Below-floor selling is rejected outright until the approval design exists,
  because an approved below-floor sale must record who approved it and the
  current schema has nowhere to store that.
- Confirmed below-floor request direction (2026-09-26): the cashier submits a
  request to Super Admin/Master with an optional price and a note. If no price
  is given, the approver enters the price; if a price is given, the approver
  may approve it as-is or change it. Implementing this needs a request/approval
  store (schema) and the Master-side program to act on it.
- The floor price may be sent to POS (it is the lowest price the lowest role is
  allowed to know); cost price, margin, supplier data, and stock valuation must
  never be sent.
- Different price lists (special shop/tukang prices, Super Admin special
  prices) are master data owned by Super Admin/Master. POS must not derive or
  invent prices from floor/ceiling; surfacing those lists in POS is a future
  design.
- Confirmed unfinished-pricelist rule (2026-10-07): `max_price` = 0 in the item
  master data means the list price has not been filled in yet, not a selling
  price of Rp 0. For such an item the selling price is `min_price`, and because
  that price already sits at the lower bound the item has **no floor left**: it
  is shown without a floor code in the cart and its ceiling for local and
  server validation is `min_price`. `max_price` NULL still means the master
  price is incomplete, and POS must not invent a price from the floor. Recorded
  because live data ("Aries Pintu - Abu", min 245.000 / max 0) displayed Rp 0
  and every nota for it was rejected as "above the ceiling price".

### Fulfillment

- Every completed note must explicitly have a fulfillment method; it cannot be
  empty.
- Supported business concepts include `AMBIL SENDIRI`, `DIANTAR`, and external
  shipping where applicable.
- For `DIANTAR`, address and zone are required.
- Shipping cost is a separate line on the note. Confirmed 2026-09-26: the
  cashier enters the shipping amount directly in POS (per the POS UI), so no
  zone/distance tariff data is required at this stage; "below zone tariff"
  cannot be enforced until Super Admin/Master provides tariffs.
- Free or reduced shipping requires approval according to the approval rules.
- Additional fulfillment behavior must not be invented.

### Idempotency, audit, and integrity

- Offline synchronization uses stable transaction/event identifiers.
- Submitting the same event twice must recognize it as the same event and
  record it once only; stock and financial effects occur once only, including
  after network retries.
- Important business records/actions are append-only and are never deleted as
  historical correction mechanisms.
- Relevant actions preserve actor, timestamp, device/tab identity where
  applicable, action/event type, required reason, approval information where
  applicable, and a reference to the original transaction for a
  correction/reversal.

### POS transaction flow rules (confirmed 2026-09-26)

- Online flow: backend verifies worker PIN, validates the payload, resolves
  `sync_id`/`transaction_id`, checks idempotency/conflict, locks inventory
  rows, rejects sale when current stock <= 0, allows any quantity when stock
  > 0, deducts stock, persists header + details, then returns success.
- Offline flow: POS uses last-known/cached stock only; cached stock <= 0
  rejects the sale; cached stock > 0 allows any quantity. Offline
  transactions wait in a local queue and are not authoritative for
  inventory.
- Offline validation never overrides backend validation: a sale allowed
  offline can still be rejected at sync time when authoritative stock is
  <= 0. The offline queue must keep rejected transactions visible with the
  backend's rejection reason (nota tetap ada di riwayat, ditandai).
- Inventory remains the source of truth; POS never becomes authoritative.
- Transaction identity: `sync_id` is the UUID primary identity for
  synchronization/idempotency; `transaction_id` is the human-readable nota
  number. Both are immutable after creation.
- Idempotency outcomes: same `sync_id` + same `transaction_id` returns the
  existing transaction (no duplicate); same `sync_id` + different
  `transaction_id` is a 409 conflict; same `transaction_id` + different
  `sync_id` is a 409 conflict. This matches the implemented
  `resolve_transaction_request` behavior.

### Inventory integration seam (confirmed 2026-09-26)

- POS is designed to accept a future Inventory module without redesign:
  today's sale-time stock validation and deduction are isolated in a single
  backend function (`_process_inventory` behind `persist_transaction`), so
  the future Inventory contract replaces that seam's internals while the
  transaction flow, endpoint contract, idempotency model, and tests stay
  stable.
- Until Inventory owns stock, the POS sale deduction against the externally
  managed `item` table is the only sanctioned POS write outside its own
  transaction tables, and sale validation/deduction must stay atomic with
  transaction persistence.
- POS must not add stock adjustments, stock columns/tables, or access other
  Inventory-owned structures without an approved cross-module design (see
  "Cross-module integration").
- The future Inventory design must preserve the confirmed sale rules:
  Inventory is the source of truth; sale allowed only when current stock
  > 0; quantity may exceed stock and push it negative; stock <= 0 rejects;
  offline-allowed sales may be rejected at sync; idempotent sync deducts
  stock exactly once.

- The existing POS database is externally managed.
- Django POS models use `managed = False`.
- No migrations or database schema changes are permitted unless explicitly
  requested and approved.
- Existing composite identities must be preserved:
  - Item: `item_name + variant`
  - TransactionDetail: `sync_id + item_name + variant`
- Sale-time transaction prices remain historically accurate and must not change
  dynamically when master pricing changes.

### Implementation principle

Before implementing a feature:

1. Inspect the existing implementation.
2. Identify affected modules.
3. Identify source-of-truth ownership.
4. Identify cross-module effects.
5. Define API/event contracts.
6. Identify validation rules.
7. Identify audit requirements.
8. Implement the smallest required change.
9. Test it.
10. Review the diff.
11. Update documentation when behavior or architecture changes.

Unrelated refactoring is not part of this process.

## Existing implementation

Only the POS module is currently implemented in the repository.

- POS is a Django 5 + Django REST Framework backend with a Vue 3 + Vite
  frontend.
- The product API is `GET /api/items/`, with pagination and search. The item
  response exposes product name, variant, sale price (ceiling), floor price,
  stock, and stock alert data — never cost, margin, supplier, or stock value.
- Online checkout is implemented end to end: the frontend sends
  `POST /api/transactions/` with `{ pin, transaction }` and a device token;
  the backend verifies the PIN against the worker table, validates the
  payload, enforces `floor <= unit_price <= ceiling`, checks and deducts
  stock atomically with transaction persistence, and returns 201 (new) or
  200 (idempotent). The frontend never sends `worker_id`.
- Offline batch endpoint exists: `POST /api/transactions/sync/` currently
  accepts one PIN per batch and reports per-transaction outcomes (`new` /
  `idempotent` / `rejected` / `conflict`). It does not yet implement the approved
  per-nota PIN revision, and frontend has no batch-sync caller.
- Transaction history reads are implemented: `GET
  /api/transactions/history/` (paginated list) and `GET
  /api/transactions/history/<transaction_id>/` (one nota with its item
  rows), scoped to the authenticated device's own nota prefix.
- Device identity is established by `POST /api/devices/register/` and used as
  `Authorization: Device <token>` on transaction endpoints; nota numbers are
  allocated per device from ranges of 1000
  (`POST /api/devices/<device_id>/transaction-sequence/`).
- The current frontend supports product search, cart management, pickup versus
  delivery selection, shipping-cost entry, cash/EDC/bank-transfer selection,
  per-line price negotiation within floor–ceiling, cash-change calculation,
  and a per-device nota history view.
- Frontend stores the cart in localStorage and checkout records, payment-proof
  blobs, device data, ranges, worker directory, and print jobs in Dexie.
  Cashier name is PIN-resolved, not typed. Pending transactions are retried
  individually with a re-entered PIN; offline catalog search, automatic
  multi-nota sync, local cooldown, and complete offline finalization are missing.
- Device-authenticated `GET /api/workers/offline-list/` supplies the directory
  for local matching. LAN printing, a printer-status panel, surat jalan for
  delivery, daily history filtering, and deliberate history reprints exist.
- Payment-proof capture/persistence and separate idempotent multipart upload
  exist. Reading proofs from the server, production storage, and retention
  remain open; see [PAYMENT_PROOF_DESIGN.md](POS/pos-backend/PAYMENT_PROOF_DESIGN.md).
- Existing POS transaction models contain transaction header and detail
  concepts, including a sale-time `unit_price`, `discount`, and `subtotal` on
  `TransactionDetail`.
- The current POS transaction model has a nullable `shipping_amount`, but does
  not itself establish the approved representation of fulfillment, address,
  zone, tariff, void, return, refund, approval, or audit events.
- Not yet integrated with Finance or Inventory modules; the sale-time stock
  effect remains isolated in the inventory seam described above.

## Acceptance examples (requirements, not a test report)

| Scenario | Required outcome |
| --- | --- |
| Stock 5, quantity 8 | Allowed, resulting stock -3 |
| Stock 0 or negative | Sale rejected |
| Retry same transaction identity | No second transaction or stock deduction |
| Conflicting nota/sync identity | Conflict, no silent replacement |
| No matching offline PIN | No finalized offline nota or print |
| Printer fails after finalization | Sale remains recorded; print failure is separate |

These examples do not claim the incomplete offline lifecycle passes today.
Detailed checks belong to the existing module design documents, not a new PRD.

## Future/planned functionality

The following are confirmed directions but are not currently implemented in the
repository:

- Inventory module ownership of authoritative stock and stock movements.
- Finance module ownership of financial records and reconciliation.
- Super Admin/Master ownership of shared master data, users/workers,
  roles/permissions, pricing controls, and approvals.
- Cross-module Inventory and Finance effects of a finalized POS transaction.
- Offline-first POS selling path: the local queue and batch sync exist, but
  offline completion without connectivity is not wired end to end yet.
- Void, return, refund, and approval workflows.
- Delivery fulfillment details, including address, zone, tariff, and approval
  of free/reduced shipping.

## Open decisions

The following remain open and must be explicitly designed from the existing
schema and approved before coding:

- Exact transaction lifecycle and status model.
- Exact database representation of sale, void, return, and refund events.
- Exact Inventory reconciliation behavior during offline synchronization.
- Exact refund authorization workflow details.
- Exact financial treatment of voids and refunds.
- Exact behavior for non-cash refunds.
- Exact audit/event storage model.
- Exact shared authentication and RBAC model (POS itself has no
  account/login system by design; approvals still need an authorization model).
- A queryable server-side representation of transactions rejected at offline
  synchronization (the device queue plus a structured server log are the
  confirmed interim behavior).
- Exact API/event contracts between modules.

The following confirmed rules also require an implementation design before
coding; this document does not choose that design:

- How modular-monolith module boundaries are enforced within the shared system.
- Cross-module event/reconciliation contracts beyond the implemented POS
  `sync_id` transaction idempotency and payment-proof `proof_id`.
- How Inventory communicates authoritative acceptance/rejection and stock
  effects to POS.
- How Finance receives and reconciles authoritative sale, payment, refund, and
  void records.
- Approver identity/RBAC and authorization beyond existing worker PIN/device
  verification; completion of the approved per-nota sync/local PIN safeguards.
- Below-floor approval, zone tariffs, and free/reduced-shipping approval beyond
  the implemented floor/ceiling checks.
- How the LAN print bridge is **deployed and kept alive** (a reference
  implementation exists; its ownership, update path, and how the tablet reaches
  it in production do not), and the final retained shape of the POS-side print
  and reprint queue (`print_jobs` retention is currently a chosen default, not a
  confirmed decision).
- Whether `DIKIRIM` needs a separate delivery document; `DIANTAR` already
  produces a surat jalan. Delivery identity cannot yet be reconstructed exactly
  from history: reprint currently infers delivery from `shipping_amount > 0`.
- Exhausted offline real-number reserves, including the limits of nullable nota
  ownership/history and proof-upload scoping without a transaction device column.
