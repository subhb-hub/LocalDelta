# Portfolio structure, independently of valuation

The portfolio extension registers account ownership, asset-to-account membership,
user-confirmed transfer relationships and cash classifications. Snapshot dates,
market values, current holding quantities, allocation weights and investment
returns are a separate layer. Missing valuation data does not block this layer.

## Commands

```bash
python localdelta.py portfolio-upgrade --db private/portfolio.sqlite3
python localdelta.py portfolio-import --db private/portfolio.sqlite3 --manifest private/confirmation.json
# Review the preview, then repeat with --apply.
python localdelta.py query --db private/portfolio.sqlite3 --view portfolio
python localdelta.py query --db private/portfolio.sqlite3 --view asset_catalog --limit 100
python localdelta.py query --db private/portfolio.sqlite3 --view capital
python localdelta.py dashboard --db private/portfolio.sqlite3
```

The new read views are also available through the existing authenticated loopback
API: `/api/portfolio`, `/api/asset_catalog`, `/api/capital`. Account filters and
pagination remain parameterized. Asset membership is structural; date filters do
not silently turn the catalog into historical holdings. `capital` filters use
receipt dates. The dashboard defaults to account structure, asset catalog,
confirmed transfer lanes and confirmed funding basis when the extension is active.
Old databases keep their original dashboard mode.

## Stored structure

| Table | Meaning |
| --- | --- |
| `ld_asset_memberships` | Original asset ID, normalized account ID when supported, instrument grouping key, mapping status and source reference |
| `ld_portfolio_sources` | Exact user confirmation text, reference and SHA-256 |
| `ld_portfolio_classifications` | Append-only per-observation/per-link classification, owned counter-account and separately declared funding basis |
| `ld_portfolio_applied` | Applied manifest, its hash and previous mapping/account states |
| `ld_portfolio_migrations` | Additive migration audit |

The existing `assets`, `transactions`, `snapshots`, source observations and raw
transfer links are preserved. Source-backed mappings and legacy account labels
remain distinguishable. A placeholder with no institution evidence keeps a NULL
account reference and its original label. Accounts may be registered before
holdings are supplied; zero registered asset records is not a zero account value.
Historical asset records are not claimed to be presently held.

Cash classifications are `internal_transfer`, `external_contribution`, and
`non_investment`. Non-investment movements stay in the full cash history.
One-sided internal transfers must identify an owned counter-account; no missing
receipt or balance is fabricated. Internal transfer links use the existing
append-only evidence-review history. Raw source signs remain unchanged when a
user confirms their effective cash direction.

An external contribution can preserve both a user-declared original-currency
funding basis and a different-currency bank receipt. These are two descriptions
of the same contribution, not additional cash postings. Exact decimal totals are
separate by currency. `capital` reports only the currently classified contribution
observations, never total lifetime principal, income or portfolio NAV. A later
review of the same observation supersedes its old classification.

## Private manifest

The schema is `LocalDeltaPortfolio/1`. Required keys are `schema`, `source_ref`,
`source_text`, `accounts`, `memberships`, `classifications`.

- Accounts use the existing ledger account fields. Existing institution/type/
  ownership cannot be changed through this import; a reviewed display label may
  be normalized.
- Membership fields: `asset_id`, nullable `account_id`, `instrument_key`,
  `mapping_status` (`documented`, `legacy_label`, `unresolved`), `source_ref`.
- Classification fields: `target_type` (`observation` or `link`), `target_id`,
  `classification`, nullable `counter_account_id`, nullable `basis_amount` and
  `basis_currency`, nullable `signed_amount`, `user_note`.
- A contribution requires a positive effective cash receipt and a positive
  user-declared basis. Link targets can only be internal transfers. A confirmed
  sign can change direction, not original magnitude.

Imports are backed up and atomic. The full manifest is validated before mutation;
foreign keys, direction conflicts and transfer allocations are checked in the
transaction. Replaying the same source and manifest is a no-op. Reinterpreting an
already-applied source under different mappings is rejected: supply a new reviewed
source reference. All tables and indexes participate in schema/data fingerprints.
Keep manifests, confirmation text and databases private; the public source audit
rejects unreviewed JSON data.

## Validation

Synthetic tests cover migration and backwards compatibility, legacy asset and
snapshot preservation, atomic rollback, funding/receipt currency separation,
internal transfer and personal-flow exclusion, source idempotence, account
identity restrictions, unresolved asset ownership, and tampering detection.
