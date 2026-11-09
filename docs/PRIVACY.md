# Privacy model: local by default, selectively disclose to cloud AI

## Trust boundaries

1. **Local trusted domain:** the SQLite database, private documents, original screenshots, receipts, private HMAC key, token-to-ID lookup, backup files and complete PDP packages.
2. **Optional AI domain:** a local model running under your chosen OS controls, or a remote model that sees only the subset you explicitly send.
3. **Public source repository:** executable code, unit tests, schemas, synthetic examples and docs. No personal database material.

## Multi-currency cloud drafts

`LocalDeltaDraft/2` includes explicit original-currency amount values *only* if the user chooses to provide them. `cloud-context` does not send holdings, account names, balances or monetary amounts. The local bridge restores the asset identity, validates currency/amount roles, and binds PDP/2.0. Pseudonymous tokens remain correlatable; uploaded transaction amounts may still be sensitive even without the asset name.

## Cloud workflow

`cloud-context` exports ONLY:
- `format`, an opaque HMAC-derived `context_token` tied to the current DB revision and data digest,
- an unordered set of HMAC-derived asset tokens,
- supported update types and a privacy warning.

Default mode exports no asset names, accounts, instruments, amounts, trade IDs or SHA-256 data fingerprints. `--detail category` optionally includes asset categories. The local HMAC key is created in a `.localdelta/` directory next to the database and never exported by the tool. Keep that directory private and backed up alongside the database if you want tokens to remain stable.

Cloud AI returns `LocalDeltaDraft/1` JSON with an `asset_token`, the fields you chose to disclose and a source indication. `bind-draft` verifies the token and context freshness, recovers asset ID/account/currency locally, validates table/column names and currency rules, and emits a PDP package. No network request is performed at any stage by LocalDelta.

### Exactly what it does *not* protect

- **Not encryption or zero-knowledge computing:** values you type or screenshots you upload to a cloud model are disclosed to that provider.
- **Not authentication:** a package SHA-256 checksum verifies integrity, not origin; there are no digital signatures in v0.1.0.
- **Not confidential backups:** local SQLite databases, receipt logs and backups are plaintext on disk. Use full-disk encryption and private filesystem permissions.
- **Not anonymization of metadata across repeated sharing:** a stable token may allow correlation of future messages about the same asset; the context token rotates each database revision, but asset tokens remain stable until you rotate the local key.
- **Not proof of extraction quality:** models can misread amounts, dates or transaction types. `confirmed` is a review assertion, not a cryptographic attestation.
- **Not fully general yet:** this privacy bridge only supports new transaction and snapshot records for existing assets. New asset creation and corrections need the local PDP path until privacy-aware support is implemented.

## Recommended threat mitigations

- If privacy is paramount, use a local AI and never send raw financial material to any cloud model.
- If asking cloud AI for help, share only necessary facts. Avoid contact details, account numbers, API keys and raw broker transaction references.
- Use `plan` and manual verification before `apply`, even for seemingly correct AI updates.
- Keep `.localdelta/`, `*.sqlite3`, `*.pdp.json`, images, CSV and screenshots out of public Git repositories.
- Store external backups in an encrypted location and periodically test restoration.
- After a cloud collaboration, retain local revision history; re-export a new `cloud-context` after any successful update.

## Local name/token mapping

`alias-map` writes a **private** file containing actual asset names and accounts alongside the opaque tokens. This file is excluded by `.gitignore`. It is intended for the local operator to select the right token before cloud collaboration; never upload or commit it.
