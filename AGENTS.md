# Global Business IT Ecosystem Rules

These rules apply to the repository as a whole and to every business module
within it. Module-specific instructions, when present, add to these rules and
govern the implementation details of that module.

## Documentation ownership

- [ARCHITECTURE.md](ARCHITECTURE.md) owns confirmed business and architectural
  decisions. These instructions own working rules, not alternative business
  requirements.
- [README.md](README.md) is the setup guide and documentation index. Module
  design documents own detailed contracts and must distinguish implemented
  behavior, approved requirements, proposals, and unverified results.
- Read the instructions in the affected directory before editing. More specific
  instructions add local constraints; they cannot silently change a confirmed
  business decision. Report conflicts rather than choosing a new rule.
- Update existing documents instead of creating a duplicate PRD or instruction
  file. Verify implementation claims against code; a documented decision is not
  evidence that a feature is implemented or tested.

## 1. Repository and module structure

- The repository is a business IT ecosystem composed of separate business
  modules, including POS, Inventory, Finance, and Super Admin/Master.
- Treat each module as an independent bounded area of responsibility.
- Do not assume that a module's internal architecture, data model, APIs, or
  workflows exist until they are present in the repository or explicitly
  specified.
- Keep module-specific rules and implementation decisions inside the relevant
  module. Do not copy them into global code or apply them to another module
  without an explicit shared design.

## 2. Incremental module-by-module development

- Develop one clearly scoped module feature or integration slice at a time.
- Inspect the affected module and its documented contracts before making
  changes.
- Prefer small, reviewable changes that preserve behavior outside the requested
  scope.
- Build future modules only when explicitly requested; do not scaffold,
  populate, or infer their implementation in advance.

## 3. Shared architecture principles

- Preserve clear ownership of domain behavior and data. A module owns its own
  internal implementation unless an explicit shared contract says otherwise.
- Reuse shared platform capabilities only when their interface, ownership,
  lifecycle, and security requirements have been deliberately defined.
- Avoid premature global abstractions, broad framework changes, or repository-
  wide refactors.
- Keep dependencies directional and explicit. Do not create hidden coupling
  through direct database access, implicit shared state, or undocumented file
  dependencies.

## 4. Cross-module integration

- Design and document cross-module contracts before implementing an
  integration.
- Define the contract's owner, input/output data, validation, error behavior,
  versioning/compatibility expectations, authorization, and consistency model.
- A module must not silently alter another module's data, database schema,
  behavior, API output, or business rules.
- Use explicit APIs, events, or other approved interfaces for integration; do
  not couple modules through another module's private tables or implementation
  details.
- POS already has a transaction batch-sync endpoint and local persistence.
  Future cross-module synchronization and reconciliation still need explicit
  ownership and conflict contracts; do not infer them from the POS endpoint.

## 5. Database and schema protection

- Treat every database schema as a protected contract.
- Do not create, alter, delete, rename, or directly repair tables, columns,
  constraints, indexes, or records without explicit authorization.
- Do not create, apply, edit, fake, or delete migrations unless the requested
  work explicitly includes the schema change and identifies its owning module.
- Never allow one module to write another module's database tables without an
  explicit, reviewed cross-module ownership and consistency design.
- Do not invent schemas for modules that are not yet implemented.

## 6. API contract protection

- Preserve existing public and inter-module API contracts unless an explicit
  contract change is requested.
- New or changed APIs must specify ownership, request/response shape,
  validation, authorization, error behavior, and backwards compatibility.
- Do not silently repurpose fields, change meanings, or rely on undocumented
  API behavior.
- Coordinate breaking changes across every known consumer before implementing
  them.

## 7. Authentication and authorization

- Treat authentication, identity, session handling, and role-based access
  control (RBAC) as shared system concerns.
- Do not invent separate or incompatible authentication/authorization behavior
  inside an individual module without an explicit shared design.
- Enforce authorization on the server-side boundary for all protected actions;
  client-side visibility is not authorization.
- Apply least privilege, validate tenant/organization boundaries where they are
  introduced, and never expose credentials, PINs, tokens, or authorization
  decisions unnecessarily.

## 8. Security and secret handling

- Never read, print, commit, copy, or expose `.env` contents, credentials,
  database URLs, API keys, tokens, connection strings, private keys, or PINs.
- Do not add secrets to source code, tests, scripts, documentation, logs,
  commands, or error messages.
- If a secret is discovered in tracked content, do not reproduce it. Report
  only that a secret is present and ask for direction before remediation.
- Keep secret values environment- or secret-manager-based; do not introduce
  module-specific embedded credentials.
- Validate untrusted input at system boundaries and preserve secure defaults.

## 9. Auditability and data integrity

- Financial and inventory consistency are critical system properties.
- Design state-changing operations so they are atomic, validated, and
  recoverable. Do not permit partial writes, silent data loss, or ambiguous
  outcomes.
- Preserve historical business facts such as finalized transaction amounts,
  source references, timestamps, and responsible identities where the owning
  module's schema supports them.
- Do not overwrite or delete financial, inventory, or audit-relevant data
  without explicit authorization and a defined audit/retention approach.
- Cross-module state changes must have an explicit consistency, retry, and
  failure-handling design before implementation.

## 10. Git safety

- Preserve existing contributor changes and inspect diffs when permitted.
- Do not overwrite, discard, reset, checkout, clean, stash, amend,
  force-push, or otherwise alter Git history unless explicitly requested.
- Do not stage, commit, push, create branches, or open pull requests unless
  explicitly asked.

## 11. Testing and verification

- Add or update focused tests with each behavior change when a safe and
  suitable test setup exists.
- Run verification appropriate to the change, but do not run commands that
  alter databases, migrations, source files, dependencies, or generated
  artifacts without authorization.
- Treat integrations involving money, stock, authorization, synchronization,
  or cross-module contracts as high-risk and verify their failure paths as well
  as successful behavior.
- Any UI change (screen, panel, modal, component) must be checked at the
  different screen resolutions the program actually runs on — narrow, the
  target device, and wide — in every supported theme, not at one window size.
  A layout that is correct at one size can break at another. Verify by
  measuring or capturing each size, not by reading the CSS/JSX, and report any
  resolution that could not be checked. Each module's guidelines own the
  concrete sizes and breakpoints for its own UI.
- Report what was verified and what was not run, including the reason.

## 12. Scope control

- Implement only the requested module feature and the minimum necessary
  supporting changes.
- Do not upgrade dependencies, change framework versions, reorganize modules,
  modify deployment configuration, or perform broad cleanup as incidental
  work.
- Do not create files, code, schemas, APIs, or architectural designs for
  future modules unless explicitly requested.

## 13. Working across module boundaries

- Identify every module affected before proposing or implementing a shared
  change.
- Keep ownership explicit: identify the source of truth for each data element
  and which module is allowed to change it.
- Make integration changes in coordinated, backward-compatible increments; do
  not leave one module dependent on an unpublished or incomplete contract.
- Avoid direct cross-module data mutation. If an exception is required, obtain
  explicit approval and document the ownership, authorization, transaction,
  and rollback expectations.

## 14. When an AI agent must ask for clarification

Ask before proceeding when any of the following is unclear or would expand the
requested scope:

- A change affects more than one module, a shared contract, or the owner of a
  data element.
- The work needs a schema change, migration, data import/repair, or direct
  database write.
- A financial, inventory, authentication, authorization, audit, offline-sync,
  conflict-resolution, retry, or rollback rule is unspecified.
- A new module, shared service, API, event, identity/RBAC model, or deployment
  mechanism would need to be introduced.
- A change could expose, rotate, delete, or otherwise handle a secret.
- The work would modify unrelated files or discard another contributor's
  changes.

## 15. Task and thread scope

- Repository instructions apply to every thread; this file does not assign all
  conversations to the backend. Follow the scope explicitly authorized by the
  user. A backend-only task does not authorize frontend behavior changes, and
  a documentation task does not authorize application or database changes.
- Read the local [backend guidelines](POS/pos-backend/AGENTS.md) or
  [frontend guidelines](POS/pos-frontend/AGENTS.md) for the affected area.
- Backend owns server-side validation and PIN-derived worker attribution;
  frontend follows the published API contracts. Do not invent one side's
  behavior to make the other side easier to implement.
- Coordinate changes spanning frontend and backend without overriding module
  ownership or decisions in [ARCHITECTURE.md](ARCHITECTURE.md).
