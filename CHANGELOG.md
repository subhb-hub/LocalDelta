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
