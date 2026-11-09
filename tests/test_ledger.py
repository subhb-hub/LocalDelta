"""PDP/3.0 multi-account double-entry ledger tests: synthetic values only."""
from __future__ import annotations

import contextlib
from decimal import Decimal
import io
import json
from pathlib import Path
import tempfile
import unittest
import uuid

import ledger
import localdelta
import multicurrency as mc
import pdp
import workflow


SOURCE = {"kind": "manual", "reference": "synthetic-only", "certainty": "confirmed"}
TIME = "2026-11-09T10:00:00+08:00"


def op(table, record, source_key=None):
    return {
        "op_id": str(uuid.uuid4()), "action": "create", "table": table,
        "source_key": source_key or ("synthetic:ledger:" + str(uuid.uuid4())),
        "source": dict(SOURCE), "record": record,
    }


def account(name, ownership="owned", category="bank"):
    ident = str(uuid.uuid4())
    return ident, op("ledger_accounts", {
        "account_id": ident, "institution": "SYNTHETIC",
        "display_name": name, "account_type": category,
        "ownership": ownership,
    })


def posting(account_id, amount, currency="CNY", role="principal", posted_at=None):
    value = {
        "posting_id": str(uuid.uuid4()), "account_id": account_id,
        "amount": amount, "currency": currency, "role": role,
    }
    if posted_at is not None:
        value["posted_at"] = posted_at
    return value


def event(items, kind="transfer", **kwargs):
    data = {
        "event_id": str(uuid.uuid4()), "kind": kind, "occurred_at": TIME,
        "source_ref": "synthetic-bank-statement-001", "postings": items,
    }
    data.update(kwargs)
    return op("ledger_events", data)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="localdelta-ledger-")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.db = self.folder / "synthetic.sqlite3"
        workflow.create_database(self.db)

    def cli(self, *args, success=True):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = localdelta.main(list(args))
        if success:
            self.assertEqual(rc, 0, err.getvalue())
            return json.loads(out.getvalue())
        self.assertNotEqual(rc, 0)
        return err.getvalue()

    def meta(self):
        with contextlib.closing(pdp.connect(self.db)) as c:
            return pdp.get_meta(c)

    def package(self, ops, protocol=ledger.PROTOCOL):
        m = self.meta()
        pkg = {
            "protocol": protocol, "package_id": str(uuid.uuid4()),
            "database_id": m["db_id"], "base_revision": m["revision"],
            "base_data_fingerprint": m["data_digest"],
            "schema_fingerprint": m["schema_digest"],
            "created_at": pdp.now(), "description": "synthetic ledger tests",
            "operations": ops, "evidence": [],
        }
        pkg["content_hash"] = pdp.package_digest(pkg)
        path = self.folder / (pkg["package_id"] + ".pdp.json")
        path.write_text(json.dumps(pkg, ensure_ascii=False), encoding="utf-8")
        return path

    def apply(self, ops):
        path = self.package(ops)
        preview = self.cli("plan", "--db", str(self.db), "--package", str(path))
        self.assertEqual(preview["status"], "preview")
        result = self.cli("apply", "--db", str(self.db), "--package", str(path))
        self.assertEqual(result["status"], "applied")
        return path

    def upgrade(self):
        mc.upgrade(self.db)
        return ledger.upgrade(self.db)

    def account_pair(self):
        a, ao = account("Bank A")
        b, bo = account("Bank B")
        self.apply([ao, bo])
        return a, b

    def test_migration_requires_money_extension(self):
        before = self.meta()
        self.cli("ledger-upgrade", "--db", str(self.db), success=False)
        self.assertEqual(self.meta()["revision"], before["revision"])
        with contextlib.closing(pdp.connect(self.db)) as c:
            self.assertFalse(ledger.enabled(c))

    def test_migration_is_additive_audited_and_idempotent(self):
        old = self.meta()
        mc.upgrade(self.db)
        info = self.cli("ledger-upgrade", "--db", str(self.db))
        self.assertEqual(info["status"], "upgraded")
        self.assertTrue(Path(info["backup_path"]).is_file())
        self.assertEqual(info["revision"], old["revision"] + 2)
        self.assertEqual(self.cli("ledger-upgrade", "--db", str(self.db))["status"], "already_upgraded")
        status = self.cli("status", "--db", str(self.db))
        self.assertTrue(status["ledger_enabled"])
        self.assertTrue(status["multicurrency_enabled"])
        self.assertEqual(status["counts"]["assets"], 0)
        with contextlib.closing(pdp.connect(self.db)) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM ld_ledger_migrations").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM pdp_packages").fetchone()[0], 2)

    def test_same_currency_transfer_owns_both_sides_not_external_income(self):
        self.upgrade()
        a, b = self.account_pair()
        tx = event([
            posting(a, "-1000.00"), posting(b, "1000.00", posted_at="2026-11-10T11:00:00+08:00"),
        ], booked_at=TIME, description="synthetic internal transfer")
        pack = self.apply([tx])
        entries_a = self.cli("ledger-entries", "--db", str(self.db), "--account", a)
        entries_b = self.cli("ledger-entries", "--db", str(self.db), "--account", b)
        self.assertEqual(entries_a["entries"][0]["amount"], "-1000.00")
        self.assertEqual(entries_b["entries"][0]["amount"], "1000.00")
        self.assertEqual(entries_b["entries"][0]["effective_at"], "2026-11-10T11:00:00+08:00")
        self.assertFalse(entries_b["balance_complete"])
        self.assertEqual(Decimal(entries_a["entries"][0]["amount"]) +
                         Decimal(entries_b["entries"][0]["amount"]), Decimal(0))
        self.assertEqual(len(self.cli("ledger-accounts", "--db", str(self.db),
                                      "--ownership", "owned")["accounts"]), 2)
        self.assertEqual(self.cli("apply", "--db", str(self.db), "--package", str(pack))["status"], "already_applied")

    def test_cross_currency_fee_and_fx_are_balanced_independently(self):
        self.upgrade()
        source_id, src = account("CNY bank")
        destination_id, dst = account("HKD bank")
        clearing_id, clearing = account("FX clearing", "system", "clearing")
        expense_id, expense = account("Bank charges", "system", "expense")
        self.apply([src, dst, clearing, expense])
        tx = event([
            posting(source_id, "-730.00", "CNY"),
            posting(clearing_id, "720.00", "CNY", "clearing"),
            posting(expense_id, "10.00", "CNY", "fee"),
            posting(destination_id, "775.00", "HKD"),
            posting(clearing_id, "-775.00", "HKD", "clearing"),
        ], kind="fx")
        self.apply([tx])
        with contextlib.closing(pdp.connect(self.db)) as c:
            rows = c.execute("SELECT currency,amount FROM ld_postings").fetchall()
            self.assertEqual(len(rows), 5)
            for currency in ("HKD", "CNY"):
                self.assertEqual(sum((Decimal(r["amount"]) for r in rows if r["currency"] == currency),
                                     Decimal(0)), Decimal(0))
        self.assertEqual(len(self.cli("ledger-accounts", "--db", str(self.db),
                                      "--ownership", "system")["accounts"]), 2)

    def test_unbalanced_event_fails_before_writing_anything(self):
        self.upgrade()
        a, b = self.account_pair()
        before = self.meta()
        bad = event([posting(a, "-1000.00"), posting(b, "999.99")])
        pack = self.package([bad])
        self.cli("plan", "--db", str(self.db), "--package", str(pack), success=False)
        self.cli("apply", "--db", str(self.db), "--package", str(pack), success=False)
        self.assertEqual(self.meta()["revision"], before["revision"])
        with contextlib.closing(pdp.connect(self.db)) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM ld_ledger_events").fetchone()[0], 0)

    def test_missing_account_rolls_back_earlier_valid_account_operation(self):
        self.upgrade()
        existing = str(uuid.uuid4())
        _, newacc = account("new but rolled back")
        bad = event([posting(existing, "-10.00"), posting(newacc["record"]["account_id"], "10.00")])
        p = self.package([newacc, bad])
        self.cli("plan", "--db", str(self.db), "--package", str(p), success=False)
        self.cli("apply", "--db", str(self.db), "--package", str(p), success=False)
        with contextlib.closing(pdp.connect(self.db)) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM ld_accounts").fetchone()[0], 0)

    def test_invalid_currency_time_type_amount_and_duplicate_posting(self):
        self.upgrade()
        a, b = self.account_pair()
        good = [posting(a, "-10.00"), posting(b, "10.00")]
        dup = [good[0], dict(good[1], posting_id=good[0]["posting_id"])]
        cases = [
            [dict(good[0], amount=-10.0), good[1]],
            [dict(good[0], amount="-10.001"), good[1]],
            [dict(good[0], currency="FAKE"), good[1]],
            dup,
        ]
        for posts in cases:
            with self.subTest(posts=posts):
                self.cli("plan", "--db", str(self.db),
                         "--package", str(self.package([event(posts)])), success=False)
        bad_time = event(good, occurred_at="2026-11-09T10:00:00")
        self.cli("plan", "--db", str(self.db), "--package", str(self.package([bad_time])), success=False)

    def test_malformed_enum_types_are_rejected_without_uncaught_exceptions(self):
        self.upgrade()
        a, bad_account = account("bad type")
        bad_account["record"]["account_type"] = ["bank"]
        self.cli("plan", "--db", str(self.db), "--package",
                 str(self.package([bad_account])), success=False)
        x, y = self.account_pair()
        bad_kind = event([posting(x, "-1.00"), posting(y, "1.00")])
        bad_kind["record"]["kind"] = ["transfer"]
        self.cli("plan", "--db", str(self.db), "--package",
                 str(self.package([bad_kind])), success=False)
        bad_role = event([posting(x, "-1.00"), posting(y, "1.00")])
        bad_role["record"]["postings"][0]["role"] = ["principal"]
        self.cli("plan", "--db", str(self.db), "--package",
                 str(self.package([bad_role])), success=False)

    def test_duplicate_event_source_detected_and_rejected(self):
        self.upgrade()
        a, b = self.account_pair()
        sk = "synthetic:ledger:event-unique"
        first = event([posting(a, "-20.00"), posting(b, "20.00")])
        first["source_key"] = sk
        self.apply([first])
        second = event([posting(a, "-20.00"), posting(b, "20.00")])
        second["source_key"] = sk
        self.cli("plan", "--db", str(self.db), "--package", str(self.package([second])), success=False)
        with contextlib.closing(pdp.connect(self.db)) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM ld_ledger_events").fetchone()[0], 1)

    def test_ledger_op_not_allowed_in_pdp2(self):
        self.upgrade()
        _, item = account("wrong protocol")
        self.cli("plan", "--db", str(self.db), "--package",
                 str(self.package([item], protocol=mc.PROTOCOL)), success=False)

    def test_direct_unreviewed_tamper_blocks_query_and_new_patches(self):
        self.upgrade()
        a, b = self.account_pair()
        self.apply([event([posting(a, "-15.00"), posting(b, "15.00")])])
        with contextlib.closing(pdp.connect(self.db)) as c:
            c.execute("UPDATE ld_postings SET amount='999.00' WHERE account_id=?", (a,))
            c.commit()
        self.cli("ledger-entries", "--db", str(self.db), "--account", a, success=False)
        self.cli("status", "--db", str(self.db), success=False)

    def test_timeline_returns_journal_arrows_not_claimed_reconciled(self):
        self.upgrade()
        a, b = self.account_pair()
        self.apply([event([
            posting(a, "-100.00", posted_at="2026-11-09T10:00:00+08:00"),
            posting(b, "100.00", posted_at="2026-11-10T12:00:00+09:00"),
        ])])
        result = self.cli("ledger-timeline", "--db", str(self.db))
        self.assertEqual(result["schema"], "LocalDeltaTimeline/0.1")
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["edges"]), 1)
        edge = result["edges"][0]
        self.assertEqual(edge["status"], "journal_recorded")
        self.assertEqual(edge["from_account_id"], a)
        self.assertEqual(edge["to_account_id"], b)
        self.assertEqual(edge["received_utc"], "2026-11-10T03:00:00.000000+00:00")
        self.assertEqual(len(self.cli("ledger-timeline", "--db", str(self.db),
                                      "--account", a)["edges"]), 0)

    def test_cross_timezone_sorting_and_filtering_are_by_instant(self):
        self.upgrade()
        a, b = self.account_pair()
        self.apply([event([posting(a, "-1.00"), posting(b, "1.00")],
                          occurred_at="2026-11-09T09:00:00+09:00")])
        self.apply([event([posting(a, "-2.00"), posting(b, "2.00")],
                          occurred_at="2026-11-09T02:00:00+00:00")])
        rows = self.cli("ledger-entries", "--db", str(self.db), "--account", a)["entries"]
        self.assertEqual([r["amount"] for r in rows], ["-1.00", "-2.00"])
        self.assertEqual(rows[0]["effective_utc"], "2026-11-09T00:00:00.000000+00:00")
        narrowed = self.cli("ledger-entries", "--db", str(self.db),
                            "--account", a, "--start", "2026-11-09T10:00:00+09:00")
        self.assertEqual([r["amount"] for r in narrowed["entries"]], ["-2.00"])

    def test_split_event_is_not_silently_reclassified_as_a_matched_transfer(self):
        self.upgrade()
        a, b = self.account_pair()
        third, c = account("Bank C")
        self.apply([c])
        self.apply([event([posting(a, "-10.00"), posting(b, "6.00"),
                           posting(third, "4.00")])])
        timeline = self.cli("ledger-timeline", "--db", str(self.db))
        self.assertEqual(timeline["edges"], [])
        self.assertEqual(len(timeline["unlinked_events"]), 1)

    def test_filters_limits_and_unknown_account(self):
        self.upgrade()
        a, b = self.account_pair()
        self.apply([event([posting(a, "-1.00"), posting(b, "1.00")])])
        self.apply([event([posting(a, "-2.00"), posting(b, "2.00")])])
        self.assertEqual(len(self.cli("ledger-entries", "--db", str(self.db),
                                      "--account", a, "--limit", "1")["entries"]), 1)
        self.assertEqual(len(self.cli("ledger-entries", "--db", str(self.db),
                                      "--account", a, "--limit", "1", "--offset", "1")["entries"]), 1)
        self.cli("ledger-entries", "--db", str(self.db), "--account", a,
                 "--limit", "0", success=False)
        self.cli("ledger-entries", "--db", str(self.db),
                 "--account", str(uuid.uuid4()), success=False)


if __name__ == "__main__":
    unittest.main()
