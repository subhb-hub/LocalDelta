# LocalDelta roadmap

## v0.1 (implemented)

- Standard-library Python offline portfolio importer.
- Source-key and business-key duplicate protections.
- Native create/correct/void updates with audited before/after state.
- All-or-nothing SQLite transaction, local backup and receipts.
- Cloud privacy projection via HMAC asset tokens, context freshness HMAC.
- Local binding of cloud drafts for new transactions and snapshots for existing assets.
- Cross-platform tests and public-repository file guard.

## v0.2 (next priorities, not yet shipped)

1. **Private asset aliases without revealing names to cloud:** local alias registration and cloud-draft asset creation/correction, with explicit human resolution.
2. **Evidence inbox:** local OCR/import of CSV and screenshots, content hashes and source extraction quality; no automatic promotion of uncertain claims.
3. **Export/import structured receipts:** machine-verifiable audit reconciliation, CLI compare and selective rollback via compensating events.
4. **Intuitive installation:** standalone executable builds, Windows installer, local desktop or web UI bound only to localhost.
5. **External change workflow:** audited local migration or controlled reconciliation when users edit rows in Excel/SQLite externally.

## v0.3 (architectural migration)

1. Precisely typed original-currency amounts (`currency` + decimal/integer minor unit).
2. Accounts, instruments, tax lots, trades, fees, cash flows, valuations, FX rates, evidence and immutable audit entities.
3. Adapter protocol and regression-suite to support other local data domains.
4. Optional package signatures (Ed25519 or OS-keystore-backed credentials), multiple user identities and policy-controlled approval.
5. Signed offline release artifacts and reproducible packaging.

## Explicitly out of scope now

- Online trading, bank account authentication or automatic cash movement.
- Promised accuracy of OCR, AI generated information or investment returns.
- Cloud zero-knowledge execution over unshared private data.
- Multi-device automatic synchronization or eventual consistency.
