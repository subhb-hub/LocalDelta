# Contributing

Thanks for helping make local-first AI-assisted data management safer.

1. Open an issue describing a concrete bug or adapter design proposal. **No private records or screenshots.**
2. Keep the core runtime standard-library-only unless an optional integration justifies a dependency.
3. Add a failing test for a bug, then make it pass on Linux/Windows/macOS.
4. Preserve backward compatibility of existing PDP/1.0 updates or document a migration.
5. Use synthetic data only. Never commit personal records, tokens, broker names tied to real accounts, databases or private snapshots.
6. Security bugs should be reported privately rather than with a public exploit.

```bash
python -m unittest discover -s tests -v
python scripts/check_public_tree.py
```
