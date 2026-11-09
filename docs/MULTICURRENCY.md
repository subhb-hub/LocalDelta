# LocalDelta Multicurrency Design — PDP/2.0

**Status:** Implemented alpha extension for the existing portfolio adapter. No remote access, automatic FX fetch, tax-lot accounting, full double-entry posting engine, or cross-currency cash reconciliation is included yet.

## Design sources

1. **Beancount**: A commodity (security) position is distinct from a purchase *cost* and a later *market price*. Currency conversions are explicitly recorded, not silently inferred from today's price. See [Beancount design](https://bradyt.github.io/beancount-docs/24_beancount_design_doc/) and [inventory/cost documentation](https://beancount.io/docs/Basics/inventories).
2. **Portfolio Performance**: The quote currency of a security need not be the currency of the cash/deposit account that actually paid for it. Fees can be in yet another currency. Reference historical FX rates may differ from the executed bank/broker rate. See [buy/sell currency documentation](https://help.portfolio-performance.info/en/reference/transaction/buy-sell/) and [FX reference documentation](https://help.portfolio-performance.info/en/reference/view/general-data/currencies/).
3. **GnuCash**: Keep exchange rates and actual exchange trades distinct. Its trading accounts represent multi-currency transfers; LocalDelta does *not* yet have a full trading-accounts system. See [GnuCash multicurrency](https://gnucash.org/docs/v5/C/gnucash-guide/chapter_currency.html).
4. **ISO 4217**: Standard three-letter currency codes and official minor-unit definitions are maintained by [SIX](https://www.six-group.com/en/products-services/financial-information/market-reference-data/data-standards.html). LocalDelta presently implements an explicit supported subset, not the complete ISO registry.
5. **Money value objects**: An amount and its currency must be handled together, and unrelated currencies cannot be added without an explicit conversion. See [Martin Fowler, Money](https://martinfowler.com/eaaCatalog/money.html). [SQLite documents](https://www.sqlite.org/floatingpoint.html) explain why REAL is not exact for decimal monetary values.

## Independent concepts

- **Instrument quote currency**: `assets.currency` is the asset's trading/reporting denomination (`USD` for a U.S. security). It is *not* automatically the currency used to pay for that trade.
- **Settlement currency**: the currency of `money.cash_paid` or `money.cash_inflow` on a transaction. An HKD-quoted asset may be settled in USD.
- **Fee/tax currency**: independently attached to `money.fee` and `money.tax`.
- **Valuation currency**: explicitly attached to `money.market_value` on a snapshot; it may differ from the report currency.
- **Report currency**: selected at query time (`CNY`, `HKD`, `USD`, ...). Changing it never alters stored transactions.
- **Reference FX rate**: a quote with date, direction, source and type. `USD/CNY=7.20` always means **1 USD = 7.20 CNY**, not the inverse.
- **Executed FX**: a real currency conversion is a separate business event with both cash legs and actual fees. The v0.3 extension only stores rates and intentionally does not claim to reconstruct such a trade from a reference rate.

## Data model and compatibility

The existing `assets`, `transactions`, `snapshots`, `pdp_*` entities and public v1 protocol remain intact.

A controlled `money-upgrade` creates, **without replacing the old tables**:

| Table | Purpose |
|---|---|
| `ld_currency_amounts` | Exact decimal strings and explicit currency codes per business record and semantic role. E.g. `snapshots/UUID/market_value = 1762.30 USD`. |
| `ld_fx_rates` | Append-only reference/executed-rate observations with directional pair, rate date and provenance. Reports **only use reference rates**. |
| `ld_mc_migrations` | Migration provenance, source revision and number of legacy CNY amounts backfilled. |

Legacy `_cny` values are mirrored to `ld_currency_amounts` during migration. When original amounts came from SQLite `REAL` and had no exact PDP text record, the migration rounds to two decimals and marks `origin=legacy_approx`. **This is an approximation of a previously approximate representation, not a verified source amount.** Data backed by `pdp_exact_values` use `origin=legacy_exact`. Every future native PDP/2.0 monetary update is kept exactly in decimal `TEXT`. For **CNY-quoted assets** and CNY-native roles, legacy `_cny` columns are also updated only to preserve compatibility. Foreign monetary values are **never** inserted into CNY-named columns.

Migration increments the database revision, records an audit entry, updates the schema/data fingerprints and creates a full SQLite backup. Any existing patch prepared against the old revision is now **stale** and must be regenerated. Repeat migrations are no-ops.

## Native PDP/2.0 JSON patch example

Below is a **synthetic** operation. Replace every UUID, database fingerprint and identifier with a value derived from the *local* database. The envelope retains the original `package_id`, `database_id`, `base_revision`, `schema_fingerprint`, `base_data_fingerprint`, `operations`, `evidence` and `content_hash` conventions.

```json
{
  "op_id": "7f8f258c-c6e7-4c9c-9325-38d272ba50a4",
  "action": "create",
  "table": "transactions",
  "source_key": "synthetic-broker:trade-001",
  "source": {"kind": "manual", "reference": "synthetic statement 001", "certainty": "confirmed"},
  "record": {
    "transaction_id": "cab7d8c3-bc0c-46a7-a604-fefb6c9a6420",
    "asset_id": "0c987f43-56f5-4dd7-85f8-4d693fcdac36",
    "account": "SYNTHETIC BROKER",
    "currency": "HKD",
    "kind": "buy",
    "order_datetime": "2026-11-09T09:30:00+08:00",
    "quantity": "100.00"
  },
  "money": {
    "trade_gross": {"amount": "5000.00", "currency": "HKD"},
    "cash_paid": {"amount": "646.00", "currency": "USD"},
    "fee": {"amount": "5.00", "currency": "HKD"}
  }
}
```

This records actual amounts in their own currencies, with **no imaginary CNY value** and **no implicit trade exchange rate**. The package is not self-balancing, and does not replace a double-entry ledger.

### Supported semantic roles

- Transaction: `cash_paid`, `net_invested`, `fee`, `cash_inflow`, `clean_amount`, `accrued_interest`, `trade_gross`, `tax`.
- Snapshot: `market_value`, `holding_pnl`, `cumulative_pnl`, `yesterday_pnl`, `interest_received`.
- Corrections: `money_before` and `money_changes` must have matching role keys and exact amount+currency values. A mismatching precondition rejects the whole package. `void` still uses the existing audit/active-view mechanism.
- FX references: native `table="fx_rates"` / `action="create"`, with `rate_id`, `base_currency`, `quote_currency`, `rate`, `rate_date`, `rate_kind`, `source_ref`. Historical rates are append-only.

Currency amounts travel as decimal **strings**, not JSON floating point. The program validates currency codes and official minor units for its supported subset (USD/HKD/CNY=2 digits, JPY=0, KWD=3). The rate itself is a positive decimal string of at most 12 fractional digits.

## Using the CLI

```bash
python localdelta.py money-upgrade --db private/portfolio.sqlite3
python localdelta.py money-currencies

# Prepare an unapplied, reference-only FX patch, e.g. using a verified historical rate:
python localdelta.py fx-draft --db private/portfolio.sqlite3 \
  --base USD --quote CNY --rate 7.20 --date 2026-11-09 \
  --source 'MANUAL-SYNTHETIC-TEST'

# Review and apply the resulting file through normal guarded plan/apply.
# Report converts *only* values with reference FX evidence from the correct date:
python localdelta.py money-report --db private/portfolio.sqlite3 \
  --currency CNY --as-of 2026-11-09 --max-fx-age 0
```

**The 7.20 rate above is deliberately synthetic and MUST NOT be reused as a claimed real market rate.** A reference exchange rate is neither the executed price paid by a bank nor proof of profitability.

GUI: `python localdelta.py gui` → **多币种** tab. Existing DB: click **启用旧库多币种** (requires typing `UPGRADE`); newly created GUI databases enable multicurrency immediately. Reference FX form only prepares a patch; the user must preview and explicitly approve it on the **增量更新** tab.

## Privacy-preserving cloud AI

Run `cloud-context` on a migrated DB. The JSON remains pseudonymized and advertises `supports_native_money`. The AI may produce `LocalDeltaDraft/2` for an existing anonymized asset, with `money` roles containing only amounts and currencies that the user explicitly chooses to disclose. The local `bind-draft` resolves the private account and asset identity and emits a native PDP/2.0 package. See `cloud_draft_v2.schema.json` and `docs/AI_WORKFLOW.md`. Sharing asset-specific amounts can itself expose sensitive financial data even when the asset token is anonymous.

## Valuation and incomplete-data semantics

`money-report` chooses each asset's latest **active** snapshot on or before the query date. It preserves each snapshot's own date; the user chooses the report-as-of date for reference FX. For conversions, exact-date rates are the default (`--max-fx-age 0`); you may explicitly allow older quotes with `--max-fx-age 7`. The calculator uses `Decimal` and rounds only the displayed final amount to the report currency's minor unit (e.g. whole JPY). It *never* looks ahead at future FX quotes.

A report with missing snapshots or FX observations says `complete=false` and lists those absences. Its `converted_known_total` is **only a partial sum**, never represented as the total portfolio net asset value when incomplete. Holdings are not reconciled against trade lots or cash balances; this is a snapshot valuation, not an audited performance statement.

## Out of scope (important)

- Cash account double-entry posting, actual FX conversion reconciliation, FIFO/tax lot cost basis and realized/unrealized FX-attribution reporting.
- Brokerage-specific currency tax rounding, withholding taxes, automatic bank links, currency arbitrage, secure signing, automatic online rates or automated image parsing.
- All ISO 4217 currency codes: unsupported codes are rejected until their official minor units are explicitly added and tested.
- The legacy `unit_nav`, `unit_price`, `quantity` columns use SQLite `REAL` and are not yet a fully decimal securities/lot ledger. Future versions should introduce typed quote-price and quantity lots.

These limitations are intentional: supporting currencies *safely* without inventing missing books is more important than claiming a complete general accounting system.
