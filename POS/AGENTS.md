# POS Development Rules

These rules apply to AI-assisted and human development in this repository. The
project is an existing POS system, so changes must preserve its current schema,
contracts, and working behavior unless the user explicitly authorizes a
deliberate change.

Confirmed business rules live in [ARCHITECTURE.md](../ARCHITECTURE.md); setup
and documentation navigation live in [README.md](../README.md). Backend and
frontend guidelines add local working constraints. Requirement, implementation,
and verification status must be kept separate; do not treat a planned feature
as working merely because it is described in a design document.

## 1. Project architecture

- `pos-backend/` is a Django 5 + Django REST Framework backend. Its project
  package is `config` and its POS app is `pos`.
- `pos-frontend/` is a Vue 3 + Vite frontend. The active POS screen is composed
  directly by `src/App.vue`, `ProductList.vue`, `SearchBar.vue`, and
  `CartList.vue`.
- The frontend currently calls the backend directly for product data. Do not
  introduce new architectural layers, frameworks, state systems, routing
  patterns, or broad refactors unless explicitly requested.

## 2. Incremental development

- Work one complete, narrowly scoped feature at a time.
- Before changing code, inspect the files, API contract, and database entities
  directly affected by the requested feature.
- Prefer small, reviewable changes that preserve existing behavior outside the
  requested scope.
- Do not modify unrelated files, generated files, dependency manifests, or
  formatting across the repository as incidental cleanup.

## 3. Database and schema protection

- The POS tables are externally managed. All POS Django models use
  `managed = False`; Django migrations do not own these tables.
- Do not create, alter, delete, or rename database tables, columns,
  constraints, indexes, or records without explicit user authorization.
- Do not generate, apply, fake, edit, or delete Django migrations for the POS
  schema unless explicitly requested.
- Treat the existing schema as authoritative. Confirm the intended database
  compatibility before changing a model, query, or write path.
- Do not assume the repository's SQLite file is the active application
  database: backend configuration obtains the active connection from
  `DATABASE_URL`.
- One-off migration/helper scripts that touch a database (e.g. an
  Aiven-to-Neon copy) must be deleted after their run is verified, so they
  cannot be re-run accidentally. The Neon migration target is configured via
  `NEON_URL` in `pos-backend/.env` and follows the same secret rules as §9.

## 4. Composite item identity

- An item is identified by the composite key `item_name + variant`, not by a
  synthetic numeric identifier.
- Preserve this identity in Django model access, API payloads, cart keys,
  frontend comparisons, and transaction logic.
- `TransactionDetail` is identified by
  `sync_id + item_name + variant`. Do not replace this identity or add
  assumptions that a detail row has an `Item` foreign key without explicit
  schema authorization.

## 5. Transaction development

- `Transactions` and `TransactionDetail` are existing external tables. A
  transaction feature must create the header and its detail rows as one atomic
  backend operation.
- Preserve sale-time fields on `TransactionDetail`, especially `unit_price`,
  `discount`, and `subtotal`. Historical transaction totals must not depend on
  a later change to an item's current price.
- Validate every transaction on the server: worker identity, non-empty items,
  positive integer quantities, valid payment method, totals, payment amount,
  change, and available inventory.
- Do not trust browser-calculated totals, stock, prices, worker names, or
  payment values as authoritative.
- Inventory validation and any stock deduction must be performed atomically
  with transaction persistence. Do not add a write path that can partially save
  a sale. The confirmed rule intentionally allows quantity to exceed stock
  when starting stock is positive; see [ARCHITECTURE.md](../ARCHITECTURE.md).
- Ordinary item/history responses must not expose worker PINs or hashes.
  The approved device-authenticated offline-directory endpoint is an explicit
  exception; its sensitive payload and limitations are documented in
  [OFFLINE_SYNC_DESIGN.md](pos-backend/OFFLINE_SYNC_DESIGN.md).
- The typed cashier-name field has been removed. A locally resolved name is
  for the nota; backend PIN verification still determines the authoritative
  `worker_id`. Never accept client-selected worker identity.

## 6. Pickup and delivery behavior

- The existing checkout has two fulfillment selections: `pickup` and
  `delivery`.
- Pickup has no shipping charge. Delivery requires a non-negative shipping
  amount; its value contributes to the final total.
- The existing `Transactions` table stores `shipping_amount` but does not have
  a dedicated fulfillment-method, delivery-address, or delivery-status field.
  Do not invent persistence semantics for those values without an explicitly
  approved schema/API design.

## 7. Existing API contracts

- Preserve the existing product endpoint:

  `GET /api/items/?page=<number>&search=<term>`

- It returns paginated product results and currently exposes `item_name`,
  `variant`, `price` (from `max_price`), `floor_price` (from `min_price`),
  `stock` (from `in_stock`), and `stock_alert`.
- The endpoint uses 30-item pagination and PostgreSQL trigram-based search
  ranking. On PostgreSQL, use alias/candidate-first ranking automatically when
  `item_alias` exists; fall back to legacy ranking when it is missing or an
  alias query fails. Keep the response shape compatible.
- New transaction endpoints must be added deliberately and must not silently
  alter the product endpoint or break the current frontend request contract.

## 8. Frontend architecture preservation

- Keep the active POS flow in its current component boundary unless a feature
  requires a targeted change: `App.vue` owns product/cart state; product search
  and pagination use the backend API; `CartList.vue` owns checkout UI state.
- The cart key must continue to reflect `item_name + variant`.
- Preserve `posCart` persistence and the existing Dexie storage layer. Do not
  restore the removed typed cashier-name field or its old localStorage entry.
- The Vue starter scaffolding (Vue Router, `src/router/`, `src/views/`, the
  welcome components/icons, and the demo counter store) has been removed;
  `App.vue` is the only screen. Pinia remains installed and wired in `main.js`
  without any store. Do not migrate state or screens to either of them as
  incidental refactoring.
- Dexie/IndexedDB stores device data, ranges, transactions, payment proofs,
  print jobs, and the worker directory. Checkout uses the transaction store;
  printing can run through the LAN without internet.
- A service worker/PWA, offline catalog search, automatic transaction batch
  sync, local PIN cooldown, and the complete offline-sale lifecycle are not
  implemented. Do not advertise end-to-end offline support from storage or
  printing alone; see [OFFLINE_SYNC_DESIGN.md](pos-backend/OFFLINE_SYNC_DESIGN.md).

## 9. Secrets and credentials

- Never read, print, commit, copy, or expose `.env` contents, credentials,
  database URLs, API keys, PINs, tokens, or connection strings.
- Do not add credentials to source code, scripts, tests, documentation, logs,
  commands, or error messages.
- If a credential is discovered in tracked content, do not repeat it. Report
  only that a secret is present and ask for direction before any remediation.
- Configuration must continue to use environment-based secrets rather than
  embedding secret values in application code.

## 10. Git safety

- Inspect status and diffs before and after requested changes when permitted.
- Preserve pre-existing user changes. Do not overwrite, discard, reset,
  checkout, clean, stash, amend, force-push, or otherwise alter Git history
  unless the user explicitly requests that action.
- Do not stage or commit changes unless explicitly asked.

## 11. Testing and verification

- Add or update focused tests with each behavior change when a suitable test
  location and safe test setup exist.
- Never run Django tests with the machine's shared/production database
  configuration. Use the isolated smoke-test instructions in
  [README.md](../README.md#verifikasi-aman). PostgreSQL integration tests need
  a separately authorized disposable database; SQLite is not equivalent.
- UI changes (screen, panel, modal, component) must be verified at the screen
  resolutions POS actually runs on, not at one window size: the narrowest panel
  POS is used on, the cashier tablet (1007x601, landscape and portrait), and a
  wide screen, in both light and dark theme. A layout that is correct at one
  size can break at another. Verify by measuring or capturing each size, not by
  reading the CSS, and report any resolution that could not be checked; the
  concrete sizes, tokens, and breakpoints for this UI live in the
  [frontend guidelines](pos-frontend/AGENTS.md) section 12.
- Frontend has Node built-in checkout regression tests with script/storage
  doubles, not a browser testing framework or real IndexedDB coverage.
- `npm run lint` is not read-only: its configured commands use `--fix`. Do not
  run it as a verification step unless source modifications are authorized.
- Before running local verification, check whether the dev servers are already
  running (Vite on port 5173, Django on port 8000). If they are, reuse that
  terminal for the test commands; if not, open a separate terminal for testing
  and close it afterwards. Never leave stray servers, watchers, or terminals
  running after verification. This includes a server started only to show the
  user a preview: reuse the running Vite instead of starting another one on a
  different port, and stop anything you started before finishing.
- Do not run migrations, import scripts, seed scripts, database shells, or
  commands that can alter records without explicit approval.

## 12. Scope control

- Implement only the requested feature and the minimum supporting changes.
- Do not upgrade dependencies, change framework versions, reorganize folders,
  replace styling, remove starter files, change ports/origins, or clean up
  unrelated code unless explicitly requested.
- Do not modify the existing database configuration, CORS settings, or
  deployment settings as part of an unrelated feature.

## 13. When an AI agent must ask for clarification

Ask before proceeding when any of the following is unclear or would expand the
requested scope:

- A feature requires a database schema change, migration, data repair, import,
  or direct database write.
- Pickup/delivery data must be persisted beyond the current
  `shipping_amount` field.
- A transaction ID format, worker authentication/PIN flow, payment-provider
  confirmation flow, discount rule, cancellation/refund rule, or inventory
  adjustment rule is not specified.
- A change would alter the existing `/api/items/` response contract or require
  frontend deployment/API-origin changes.
- A requested action would expose, rotate, delete, or otherwise handle a
  credential or other secret.
- The work would require touching files outside the feature's direct scope or
  discarding another contributor's changes.
