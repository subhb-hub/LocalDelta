# Security policy

LocalDelta is an early-stage tool for local financial records. Use it with care and preserve backups.

## Supported releases

Only the current source tree receives security fixes; no stable release/SLA exists yet.

## Reporting a vulnerability

Do **not** paste real account data, private databases, broker receipts, API tokens or exploitation payloads containing secrets into public GitHub issues. Once the repository is live, use GitHub's private vulnerability reporting if enabled, or contact its maintainer privately.

## Defensive limits

- Validate a proposed update with `plan` first.
- Read your local generated PDP package before `apply`.
- No authentication or digital-signature verification is currently implemented.
- Database backups, local receipts and HMAC keys are not encrypted by this software.
- Use private directories and OS disk encryption.
- Never put any files from `.localdelta/` into a public repository.
