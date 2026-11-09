# Private statement intake and account dashboard

This extends the v0.4 ledger foundation with immutable source observations,
transfer allocations, review history, indexed query APIs and a composable local
dashboard. Statement intake **does not create double-entry journal postings**.
An independently supplied pair remains `likely` until a human appends an evidence
review. Cash net movement is never presented as investment performance.

## Start with your local database

Use Python 3.10 or newer; no pip packages are required.

```bash
python localdelta.py money-upgrade --db private/portfolio.sqlite3
python localdelta.py ledger-upgrade --db private/portfolio.sqlite3
python localdelta.py statement-upgrade --db private/portfolio.sqlite3
python localdelta.py statement-import --db private/portfolio.sqlite3 --bundle private/statements.zip
```

Review the counts and limits, then repeat the import with `--apply`. You can bind
the import to the preview with `--expected-hash <content_hash>`. Each write backs
up, acquires a transaction lock, rechecks the previous schema/data fingerprints,
appends a PDP audit entry and updates the revision atomically. Original portfolio
assets, transactions and snapshots stay in their existing tables. Statement data
is additionally included in the audit fingerprint. No network request is made.

```bash
python localdelta.py dashboard --db private/portfolio.sqlite3
python localdelta.py dashboard --db private/portfolio.sqlite3 --out private/accounts.html
```

The first command starts a read-only HTTP (Hypertext Transfer Protocol) service
on `127.0.0.1`, using a random port and access token. The URL printed in your
terminal contains the token in its fragment. The page moves it into memory and
uses an Authorization header. A reload needs the original token URL again.
The unauthenticated page shell contains no account data. Host and Origin are
checked, cross-origin access is disabled, responses are not cached, and request
URLs/data are not logged. Stop the service with Ctrl+C.

The second command exports a self-contained **private** HTML snapshot. It includes
all cash/table pages, supports offline account/date filters, and does not refresh
itself from the database. Do not publish this file. The desktop GUI also offers an
“账户看板” tab with a previewed archive import and dashboard export.

## What is stored

| Table / view | Purpose |
| --- | --- |
| `ld_accounts` | Stable IDs, institution, account type, ownership |
| `ld_statement_batches` | Logical input hash and import timestamp |
| `ld_source_documents` | Relative evidence path, byte count, SHA-256 hash, original text when available |
| `ld_observations` | Immutable cash, FX, position, trade candidate or independent transfer evidence; raw fields and source locator |
| `ld_transfer_links` | Two observation IDs, amounts allocated on both sides, posting dates, currencies through endpoints, confidence and matching evidence |
| `ld_review_decisions` | Append-only direction or link reviews with evidence and revision |
| `ld_effective_observations` | Original observations plus the latest reviewed sign; original signed amount remains available |
| `ld_effective_links` | Original link plus its latest review decision |

Money uses exact decimal strings, validated against the supported currency
precision. Unknown signed amounts are SQL NULL. Missing position values are NULL.
Only known signs enter cash movement sums. A currency/account with no known signed
rows has NULL movement totals, rather than a fabricated zero balance.

Transfers can allocate part of one statement row into several links, including
cross-currency allocations. Both sides have independent positive amounts; active
allocations cannot exceed the source amount. Rejected links retain their history
and release their allocation for replacement matching. FX rates and implicit
fees are never inferred from a difference between two currencies.

The source archive is inspected without extraction or executing any included
scripts. Traversal paths, symlinks, duplicate members, oversized content and
manifest mismatches are rejected. PDF (Portable Document Format), spreadsheet,
screenshot and database binaries are registered by hash; they are not executed or
implicitly imported. Keep the original archive beside the database for these
original bytes. Searchable text, structured source JSON/CSV and row-level raw data
are retained in SQLite.

## Adapters and duplicate handling

The review-pack adapter recognizes structured CSV headers and the prepared
three-bank pairing JSON. It separates CMB cash, ZA savings, ZA savings pots, ZA
investment and HSBC cash. HSBC pairing evidence is **not a complete HSBC cash
statement**. Other legacy investment accounts remain available in the original
portfolio tables; the adapter does not guess how ambiguous legacy account names
map to these bank accounts. Unmatched remittances remain review items.

Prepared third-party receipts and ambiguous source endpoints remain evidence,
not extra spendable cash. An external-transfer direction label in an auxiliary
matching list is not taken as proof that the other account belongs to the user.
Undated securities candidates remain in review even when a date filter is active.

For another bank or broker, normalize its export to UTF-8 CSV with `date`,
`currency` and `signed_amount` or `statement_amount`. Optional columns are
`description`, `balance`, `reference`. Dates use YYYY-MM-DD; amounts use ordinary
decimal strings. An unsigned amount remains pending. Use a distinct account key
for each actual account, including multiple accounts at the same institution.

```bash
python localdelta.py statement-import-csv --db private/portfolio.sqlite3 --csv private/bank.csv --account-key bank-a-cash --institution "Example Bank" --label "Bank A cash"
# After reviewing the preview, repeat with --apply.
```

Identical logical archives import once even when repackaged. Reused observations
have deterministic identities. The generic CSV adapter uses account + reference
when supplied, otherwise exact row content and repeated-occurrence ordinal.
Changed amounts for a known reference are rejected. Content-only identities
cannot prove that differently described exports represent the same transaction:
normalize references before merging overlapping exports. Independent observations
are not silently merged solely because their amounts/dates happen to match.

## Review and drill-down

```bash
python localdelta.py statement-inspect --db private/portfolio.sqlite3 --id UUID
python localdelta.py statement-review --db private/portfolio.sqlite3 --id UUID --decision direction --signed-amount -12.34 --evidence "Checked original withdrawal column"
# Inspect the proposal; add --apply to append it.
```

A direction review can change only the sign, never the magnitude of the original
cash amount. Link decisions are `confirmed`, `likely`, `conflicting`, `rejected`.
Evidence is required. Reviews never alter the original row or pairing claim.
Confirmed links cannot contradict known/reviewed cash signs. A later review
supersedes the effective decision while preserving the full history.

## Query API

```python
import analytics

rows = analytics.query(db, 'movements', account_ids=[account_id],
                       start='2026-01-01', end='2026-12-31', limit=100, offset=0)
flows = analytics.query(db, 'timeline', account_ids=[account_id])
raw_and_reviews = analytics.record(db, observation_id)
```

HTTP clients use `Authorization: Bearer TOKEN`. Paths:

| Path | Data |
| --- | --- |
| `/api/accounts` | Owned accounts |
| `/api/movements` | Signed/unsigned cash records, source locations, reported balances |
| `/api/timeline` | Lane IDs, two posting dates, allocated original amounts, confidence, evidence references |
| `/api/summary` | Per-account/per-currency known gross movement, pending counts; no balance/return estimate |
| `/api/positions` | Dated security observations, including missing valuations |
| `/api/fx` | Original sold/bought currency amounts and recorded execution rates |
| `/api/review` | Pending directions, candidates, unresolved evidence endpoints and missing valuations |
| `/api/coverage` | Observed date ranges, explicitly incomplete |
| `/api/records/UUID` | Full raw observation/link, source metadata and review history |
| `/api/blocks` | Registered blocks |
| `/api/dashboard` | One coherent snapshot for all dashboard blocks |
| `/api/query?view=timeline` | Equivalent allowlisted view query |

Filters: repeated `account_id`, `start`, `end`, `limit` (1–1000), `offset`.
Paginated results include `total` and `has_more`; dashboard exports fetch all pages
inside the same read transaction. Timeline filters intersect the span of the two
posting dates, including delayed settlement across a filter boundary. Selecting
one account keeps its transfer counterpart visible. Both posting dates have date
precision; no exact transfer time is invented.

Account/date observations use the `(account_id,date,observation_id)` index.
Transfer allocation lookup uses separate debit/credit indexes; interval filters
have expression indexes on both date bounds. Review lookups use target/revision
indexes. Each query session validates audit fingerprints once and uses a single
SQLite read snapshot, so dashboard blocks see a coherent revision. Fingerprint
validation remains linear in stored evidence; query predicates and pagination
use indexes. For very large archives, callers should share one `QuerySession`
instead of opening a new session for every block.

## Adding blocks

The dashboard supports adding/removing/reordering up to 20 blocks, saved in the
browser. Built-ins are timeline, cash movement, review queue, position history,
cash records, source coverage and executed FX records. The block registry is trusted local Python:

```python
analytics.register_block('my_cash_table', 'My account view', 'movements')

def my_view(session, **filters):
    # Use session.c with parameterized SQL and session.filters for account/date scope.
    return {'rows': [{'metric': 'synthetic example'}]}

analytics.register_view('my_view', my_view)
analytics.register_block('my_metrics', 'My metrics', 'my_view')
```

Custom row views receive a generic table renderer. Add a trusted renderer in the
dashboard source for other chart forms. Layouts and HTTP requests cannot execute
SQL, Python or JavaScript. A local view extension is code and must be reviewed
like the rest of the application.

## Verification and limits

Synthetic tests cover old database compatibility, atomic imports and rollback,
unknown signs, per-currency sums, repeated inputs, reference conflicts, split
allocations, source integrity, evidence reviews, date overlap, query indexes,
authentication, Host/Origin rejection, read-only HTTP and HTML payload escaping.
The existing cross-platform CI still checks Python 3.10/3.12/3.13 on Windows,
macOS and Ubuntu. User data must never be added to CI fixtures, screenshots,
workflow artifacts, logs or PR descriptions.

This release does not complete missing statement periods, automatically verify
all PDF extraction, post observed transfers into the journal, link every legacy
asset account, or calculate account time-weighted/money-weighted returns. Those
require independently checked signs, account coverage, opening balances and dated
whole-account valuations. The observation/review/query layers provide the data
model for that later reconciliation without presenting incomplete data as truth.
