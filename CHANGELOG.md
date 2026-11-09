## v0.4.0a2 — 2026-11-09 — Private statement observations and account dashboard

- Added audited local intake for structured bank review archives and generic cash CSVs, preserving source hashes, raw fields, unknown directions and investment candidates.
- Added partial two-sided transfer allocations and append-only direction/link reviews with rejection and conflict handling; statement intake creates no journal postings.
- Added indexed per-account/per-currency read APIs, raw evidence drill-down, an authenticated loopback HTTP service, and a private offline dashboard with date/account filters and configurable blocks.
- Added a desktop account-dashboard tab and expanded public-tree guards for archives, HTML exports and unreviewed JSON files. Public tests use synthetic data exclusively.
- Preserved legacy database fingerprints when extensions are disabled and retained the Windows SQLite handle cleanup fix in the ledger quickstart.

## v0.3.0 — 2026-11-09 — Typed original-currency amounts and manual FX valuation

- Added opt-in, backup-first multicurrency migration (`money-upgrade`), plus GUI new-database enablement and multi-currency tab.
- Introduced additive `ld_currency_amounts`, `ld_fx_rates`, `ld_mc_migrations` tables; preserved existing three base tables, asset/transaction/snapshot counts, and v1 patches.
- Added guarded native `PDP/2.0` packages for original-currency transaction/snapshot amounts, money compare-and-swap corrections, and dated reference FX observations.
- Implemented `LocalDeltaDraft/2` anonymous cloud proposals for multi-currency amounts and local-only identity binding.
- Added explicit `money-report` projection with as-of FX, incomplete totals, source provenance and currency-specific rounding. No implicit FX conversion, no real-rate retrieval.
- Added standard-library regression tests, original-data migration integration, and documentation of architectural references and unsupported full-accounting features.

## v0.2.1 — Database creation and explicit demo mode

- Add **New database** to the Tkinter GUI and `python localdelta.py create --db ...` to the CLI.
- Initialize an empty three-table SQLite portfolio and PDP metadata in one guided workflow; refuse to overwrite existing files.
- Hide synthetic demo controls in the regular GUI; reveal them only with `python localdelta.py gui --show-demo`.
- Add terminal-menu `NEW` and automated regression tests covering schema integrity, no-overwrite and first-asset native patch.

# Changelog

## v0.2.0 — 2026-11-09

- Added native Tkinter desktop UI with file selection, safe preview, audited apply, privacy context export, local draft binding and update history.
- Added guarded interactive terminal menu and a one-command `update` operation with explicit manual approval.
- Added independent synthetic demo creation from GUI or terminal; does not touch real databases.
- Revalidates the reviewed package bytes and DB revision/data fingerprint before applying updates.
- Expanded tests to cover frontend orchestration and safety; preserved existing PDP/1.0 format and API.
- Updated cross-platform setup instructions (`python` with `python3` fallback) and user guide.

## v0.1.0 — 2026-11-08 (initial public-source candidate)

- Extracted a standalone offline portfolio delta importer from the PDP/1.0 prototype.
- Added `LocalDeltaCloudContext/1` privacy projection with local HMAC opaque asset IDs.
- Added `LocalDeltaDraft/1` local binder for create-transaction/create-snapshot changes.
- Added public-repository protection, CI tests, documentation and synthetic quickstart.
- Explicitly distinguished implemented features from planned adapter extensibility.
