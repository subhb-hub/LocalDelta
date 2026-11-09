> **Multi-currency update (v0.3.0):** Legacy three-table fields remain unchanged for compatibility. The controlled migration adds `ld_currency_amounts` exact original-currency money and `ld_fx_rates` for dated FX reference data. Do not put USD/HKD cash values into the legacy `*_cny` columns. See [MULTICURRENCY.md](MULTICURRENCY.md).

# Portfolio adapter v0.1 — supported scope

The current adapter is intentionally explicit. `legacy_schema.sql` defines three tables:

- `assets`: one instrument × one account, with a stable asset ID;
- `transactions`: a subscription/buy/sell/redemption/coupon/cashflow, linked to an asset;
- `snapshots`: observed portfolio value or units at an observation date, separate from official price date.

PDP also creates `pdp_*` metadata tables for version, source keys, audit history, void marks and preserved exact decimal input strings.

## Monetary semantics

- JSON amounts are **decimal strings**; numeric JSON floats are rejected.
- The current legacy fields `*_cny` have **CNY-only** meaning and are backed by SQLite REAL. They are not a replacement for a precise multicurrency accounting ledger.
- USD/HKD asset definitions are possible; USD/HKD financial transactions in `*_cny` fields are blocked.
- Bond clean price, purchased accrued interest, dirty price and later coupon receipts are stored separately.
- Order time, settlement/confirmation time, official valuation date and screenshot capture date must not be silently conflated.

## Cloud bridge restrictions

Cloud draft supports **creating transactions or valuation snapshots for existing assets** only. It doesn't create new assets from pseudonyms; it doesn't correct or void records. Run those through the local native PDP patch path, or use a local AI. New adapters should implement the same trust boundary without assuming a shared full database.

## Porting other local data types

The core semantics—versioned change packages, preconditions, source keys, offline plan/apply, audit receipts—are reusable. However, **adapter registry, plug-in API and migration engine are not implemented yet**. To support another dataset, an explicit adapter must define allowed tables, identities, validation functions, deduplication rules, and privacy projection before enabling its writes.
