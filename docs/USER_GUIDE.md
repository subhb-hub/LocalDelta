# LocalDelta v0.2.0 — Desktop and terminal guide

## Requirements

- Python >= 3.10, SQLite (bundled in Python), and the existing three-table portfolio schema.
- The CLI uses only Python's standard library. The desktop window also uses `tkinter`, available with many Python installations. If absent on Linux, use your distribution's Tk package or continue with the terminal.
- The project does **not** require Docker, a daemon, a browser, Node.js or an AI service.

## 1. Safest first run: synthetic demo

```bash
python localdelta.py gui
```

Windows users with Python on PATH can double-click **Start-LocalDelta.cmd**. On Linux/macOS, `sh Start-LocalDelta.sh` launches the same GUI.

Click **试用演示** (Try demo), then **预演 / 校验更新** (Preview), examine changes, then **人工确认并导入** (Apply), type `APPLY`. The app creates a new unique demo folder under `~/.localdelta/demos/` containing a fake database, JSON and ZIP packages. The demo never modifies a real investment database.

Or from a terminal:

```bash
python localdelta.py demo
python localdelta.py menu
```

Paste the printed database path and follow the guided choices.

## 2. Import real updates

Select the real `portfolio.sqlite3` database. First-time initialization is optional if it was already initialized with PDP; otherwise the **首次初始化** button runs an automatic backup and creates metadata tables. Inspect the status and counts.

Choose an untrusted `.pdp.zip` or `.pdp.json` package. For JSON with external evidence, point **证据目录** at the folder that contains `evidence/<filename>`. ZIP can carry its evidence within the archive. Click preview: invalid packages, source hashes, unknown columns, duplicate trades, stale versions and modified database states fail closed. View all before/after fields and provenance locally. After approving, type `APPLY` to trigger another full review and the existing importer. Check `pdp_backups` and `pdp_receipts` beside the database.

**Changing any visible input clears the approval.** Another application changing the database after preview also makes the update fail until you preview again. Do not use a generic SQLite editor to modify PDP-managed tables outside the importer.

## 3. Terminal shortcuts

```bash
python localdelta.py menu                              # easy guided local menu
python localdelta.py menu --db "portfolio.sqlite3"
python localdelta.py update --db "portfolio.sqlite3" --package "change.pdp.zip"
python localdelta.py update --db "portfolio.sqlite3" --package "change.pdp.zip" --yes   # scripts only, explicit opt-out from prompt
python localdelta.py status --db "portfolio.sqlite3"
python localdelta.py plan --db "portfolio.sqlite3" --package "change.pdp.zip"
```

The single-command `update` **always** shows a preview, asks for literal `APPLY` in an interactive terminal, then rechecks and writes. When piped from a script without `--yes`, it refuses any write. `--yes` should only be used in scripts where the package has been independently approved.

## 4. Cloud AI with minimal disclosure

Within the **AI 协作** tab, export `cloud-context.json`, then share it with only new facts you choose to disclose. **Do not share private-aliases.json**. Have the AI generate a `LocalDeltaDraft/1` JSON (consult `docs/AI_WORKFLOW.md`). Select that draft in the GUI: binding resolves the asset token locally and produces a PDP update; then preview and approve on the update tab. The system does not connect to cloud AI automatically.

A cloud draft currently supports only a *new transaction* or *new snapshot* for an *existing asset*. Corrections, voids and new assets require a native PDP patch, which can be produced by a trusted local AI or prepared under the full private local context. Information deliberately sent in a raw screenshot to a cloud AI is disclosed to that cloud provider, regardless of anonymized context.

## 5. Storage and maintenance

- Real databases, backups, exact financial change packages, receipts and private alias maps are unencrypted local files; use full-disk encryption and private folder permissions.
- The GUI remembers **only the last database file path** at `~/.localdelta/ui-settings.json`; no positions, balances or keys are stored in its UI settings.
- Anonymous exports and bound drafts default to `<database-folder>/.localdelta/outbox/`. Treat bound PDP packages as private.
- Public GitHub must contain only application source code, synthetic tests and documentation.
- The GUI is intentionally a thin offline client, not a data exploration dashboard and not an AI/OCR application. Running operations for very large databases may briefly block the window; use the existing CLI for long batch tasks.
