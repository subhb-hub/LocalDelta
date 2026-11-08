# AI collaboration protocol

The local database is authoritative. The model is only a *candidate update author*.

## Local AI route (all data can remain on-device)

Run any on-device extraction or LLM process with a private statement or screenshot, plus the native PDP specification. The AI may propose operations against a local read-only inventory. Save its result as a PDP package. Always run the *separate* offline validator:

```bash
python localdelta.py plan --db portfolio.sqlite3 --package candidate.pdp.json
python localdelta.py apply --db portfolio.sqlite3 --package candidate.pdp.json
```

The tool does not bundle an AI runtime and does not currently perform extraction, OCR or agent orchestration.

## Cloud AI route (no full database needed)

1. Export `cloud-context.json` from the trusted local machine. It contains HMAC asset tokens but no account/asset names or balances.
2. Optionally export `private-aliases.json` **for local reading only**. Select which token corresponds to the asset you wish to update. Never upload the alias map.
3. Send the cloud model **only** the public `cloud-context.json` and *specific newly collected facts you are willing to disclose*. Unredacted statements and screenshots are sensitive.
4. Have the AI return a `LocalDeltaDraft/1` JSON structure. It must not invent dates, prices, units, charges or identities. If required information is missing, the AI must ask for it or return a human-readable pending item instead of an executable draft.
5. `bind-draft` resolves the token locally, restores the private asset ID/account/currency and ties the package to the current database revision.
6. Run `plan`, review its full changes, then `apply`. Re-export a fresh cloud context after success.

### Suggested prompt for the cloud AI

> I am using LocalDelta Draft/1. The attached cloud context contains opaque asset tokens, not identifying financial data. Based only on the incremental facts below, return a single JSON object that follows `cloud_draft.schema.json`. Do not request my complete database, account names, passwords or private alias map. Do not generate SQL. Represent currency amounts as decimal **strings**. Separate order date, confirmation date and price/valuation date. Never invent missing numbers. Use only existing asset tokens. If information is uncertain or this operation is unsupported, report it in plain text rather than guessing an executable patch. I will review and bind your draft locally before applying it.

### Draft structure

```json
{
  "format": "LocalDeltaDraft/1",
  "context_token": "<copy from cloud-context.json>",
  "operations": [
    {
      "action": "create",
      "table": "snapshots",
      "asset_token": "<asset token selected locally>",
      "record": {
        "screenshot_date": "2026-11-08",
        "market_value_cny": "123.45",
        "needs_valuation_date_check": true
      },
      "source": {
        "kind": "manual",
        "reference": "redacted-synthetic-input-001",
        "certainty": "confirmed"
      }
    }
  ]
}
```

Values above are **synthetic**; the context token and asset token are placeholders that must be replaced with values obtained from your own local database. The prompt is an example, not an authenticated command or proof of source reliability.

## Current limitations

- LocalDelta Draft/1 currently supports `create transactions` and `create snapshots` for existing assets only. Create assets, corrections and voids use native local PDP patches.
- Currency monetary fields in the legacy portfolio adapter are CNY-only, and legacy storage uses SQLite REAL.
- The local HMAC key and alias map must be kept private. A stable asset token may be correlatable across cloud conversations.
- A JSON `content_hash` is not a digital signature. Do not auto-apply arbitrary model output.
