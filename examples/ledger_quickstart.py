"""Synthetic PDP/3.0 multi-account workflow, not a real bank import.

Run from repo root: python examples/ledger_quickstart.py
Uses a disposable temporary directory and never touches personal databases.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ledger
import pdp
import workflow


def operation(table, record):
    return {
        "op_id": str(uuid.uuid4()),
        "action": "create",
        "table": table,
        "source_key": "synthetic:ledger:" + str(uuid.uuid4()),
        "source": {"kind": "manual", "reference": "synthetic-example", "certainty": "confirmed"},
        "record": record,
    }


def main():
    with tempfile.TemporaryDirectory(prefix="localdelta-v04-demo-") as tmp:
        db = Path(tmp) / "demo.sqlite3"
        workflow.create_database(db, enable_multicurrency=True)
        ledger.upgrade(db)
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        event = str(uuid.uuid4())
        operations = [
            operation("ledger_accounts", {
                "account_id": a, "institution": "DEMO-BANK",
                "display_name": "Demo Checking", "account_type": "bank", "ownership": "owned",
            }),
            operation("ledger_accounts", {
                "account_id": b, "institution": "DEMO-BROKER",
                "display_name": "Demo Broker Cash", "account_type": "broker", "ownership": "owned",
            }),
            operation("ledger_events", {
                "event_id": event, "kind": "transfer",
                "occurred_at": "2026-11-09T10:00:00+08:00",
                "source_ref": "synthetic-bank-transfer-001",
                "description": "Synthetic internal funding; no actual transfer",
                "postings": [
                    {"posting_id": str(uuid.uuid4()), "account_id": a, "amount": "-250.00",
                     "currency": "CNY", "role": "principal"},
                    {"posting_id": str(uuid.uuid4()), "account_id": b, "amount": "250.00",
                     "currency": "CNY", "role": "principal",
                     "posted_at": "2026-11-10T09:00:00+08:00"},
                ],
            }),
        ]
        with pdp.connect(db) as conn:
            meta = pdp.get_meta(conn)
        pkg = {
            "protocol": ledger.PROTOCOL,
            "package_id": str(uuid.uuid4()),
            "database_id": meta["db_id"],
            "base_revision": meta["revision"],
            "schema_fingerprint": meta["schema_digest"],
            "base_data_fingerprint": meta["data_digest"],
            "created_at": pdp.now(),
            "description": "Synthetic account-to-account ledger transfer",
            "evidence": [], "operations": operations,
        }
        pkg["content_hash"] = pdp.package_digest(pkg)
        package_path = Path(tmp) / "demo.pdp.json"
        package_path.write_text(json.dumps(pkg, ensure_ascii=False, indent=2), encoding="utf-8")
        # Preview and atomically apply via the standard PDP path (backup + receipt).
        pdp.main(["plan", "--db", str(db), "--package", str(package_path)])
        pdp.main(["apply", "--db", str(db), "--package", str(package_path)])
        print(json.dumps(ledger.transfer_timeline(db), ensure_ascii=False, indent=2))
        print("Synthetic demo complete; temporary files will be deleted.")


if __name__ == "__main__":
    main()
