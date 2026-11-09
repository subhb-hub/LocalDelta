# LocalDelta roadmap

## v0.1 (implemented)

- Standard-library Python offline portfolio importer.
- Source-key and business-key duplicate protections.
- Native create/correct/void updates with audited before/after state.
- All-or-nothing SQLite transaction, local backup and receipts.
- Cloud privacy projection via HMAC asset tokens, context freshness HMAC.
- Local binding of cloud drafts for new transactions and snapshots for existing assets.
- Cross-platform tests and public-repository file guard.

## v0.2.0 (implemented: desktop and interactive terminal)

- Lightweight native Tkinter/ttk desktop client, no web server, no pip runtime dependencies.
- Interactive terminal menu and one-command guarded update with explicit confirmation.
- Preview details for all operations; automatically invalidate review on input changes and recheck before apply.
- One-click isolated synthetic demo; built-in cloud context/draft binding and recent audit history.
- Expanded headless tests for workflow safety and manually verified Tk window behavior.

## v0.3.0 (implemented: original-currency amounts and manual FX)

- Additive, backup-first migration of existing three-table portfolio DB, with legacy monetary provenance and revised data/schema fingerprints.
- PDP/2.0 typed `Money(amount, currency)` values; native CNY/HKD/USD transactions and snapshots; quote currency may differ from cash settlement currency.
- Manual dated/reference exchange-rate observations; no implicit or future-dated FX conversions. Reports explicitly mark missing valuations/rates.
- Cloud Draft/2 money values bound locally from pseudonymous assets, shared offline preview/approval and audit.
- **Not included:** fully balanced multi-currency cash postings, tax-lot cost accounting, automatic FX fetching, valuation P&L attribution.

## v0.4+ (proposed: multi-account ledger + composable dashboard; NOT implemented)

Design RFC: [Multi-account ledger, transfer timeline and dashboard](MULTI_ACCOUNT_DASHBOARD_RFC.md).

- Normalize institution/account identity and ownership scope; retain evidence-backed bank/broker statement observations and separate economic events / exact-currency postings.
- Add reviewed transfer matching, explicit executed FX and fees, split payments, unmatched-transaction reconciliation, and no-double-count portfolio totals.
- Publish local, read-only, typed query APIs for account cash movements, reconciliations, performance coverage and account-to-account flow timelines.
- Add a user-configurable block dashboard (timeline is an optional reusable block, not the sole UI).
- Preserve PDP audit, backups, migration compatibility, privacy and explicit confirmation; avoid default network listeners.

## Later priorities (not yet shipped)

1. **Private asset aliases without revealing names to cloud:** local alias registration and cloud-draft asset creation/correction, with explicit human resolution.
2. **Evidence inbox:** local OCR/import of CSV and screenshots, content hashes and source extraction quality; no automatic promotion of uncertain claims.
3. **Export/import structured receipts:** machine-verifiable audit reconciliation, CLI compare and selective rollback via compensating events.
4. **Intuitive distribution:** standalone executable builds / Windows installer; a full data dashboard is not yet implemented.
5. **External change workflow:** audited local migration or controlled reconciliation when users edit rows in Excel/SQLite externally.

## Further architectural development

1. Full double-entry cash posting, actual FX-exchange trade legs, tax lots and realized/unrealized gain attribution.
2. Precisely typed unit prices and security quantities; external adapter protocol for other local databases.
3. Optional Ed25519 package signatures, multi-user permissions and policy-controlled approvals.
4. Signed offline release artifacts and reproducible installers.

## Explicitly out of scope now

- Online trading, bank account authentication or automatic cash movement.
- Promised accuracy of OCR, AI generated information or investment returns.
- Cloud zero-knowledge execution over unshared private data.
- Multi-device automatic synchronization or eventual consistency.
