"""New-database safety, CLI UX and GUI option gating (no private data)."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid
from unittest import mock

import localdelta
import pdp
import workflow


class CreateDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="localdelta-create-test-")
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.db = self.dir / "private" / "portfolio.sqlite3"

    def test_create_empty_database_initialized_and_backup(self):
        result = workflow.create_database(self.db)
        self.assertEqual(result["revision"], 0)
        self.assertEqual(result["counts"], {"assets": 0, "transactions": 0, "snapshots": 0})
        self.assertTrue(self.db.is_file())
        with contextlib.closing(sqlite3.connect(self.db)) as connection:
            names = {x[0] for x in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"assets", "transactions", "snapshots", "pdp_meta", "pdp_packages", "pdp_operations"} <= names)
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(len(list((self.db.parent / "pdp_backups").glob("*.sqlite3"))), 1)

    def test_existing_file_never_overwritten(self):
        self.db.parent.mkdir(parents=True)
        self.db.write_bytes(b"PRIVATE ORIGINAL DO NOT REPLACE")
        with self.assertRaisesRegex(workflow.WorkflowError, "绝不覆盖"):
            workflow.create_database(self.db)
        self.assertEqual(self.db.read_bytes(), b"PRIVATE ORIGINAL DO NOT REPLACE")

    def test_existing_database_never_reset(self):
        workflow.create_database(self.db)
        before = self.db.read_bytes()
        with self.assertRaises(workflow.WorkflowError):
            workflow.create_database(self.db)
        self.assertEqual(before, self.db.read_bytes())

    def test_invalid_suffix_and_empty_path_rejected(self):
        for candidate in (self.dir / "important.txt", self.dir / "archive", ""):
            with self.subTest(candidate=candidate):
                with self.assertRaises(workflow.WorkflowError):
                    workflow.create_database(candidate)
        self.assertFalse((self.dir / "important.txt").exists())

    def test_failed_init_removes_only_new_file(self):
        with mock.patch.object(workflow, "initialize", side_effect=workflow.WorkflowError("simulated init failure")):
            with self.assertRaisesRegex(workflow.WorkflowError, "simulated"):
                workflow.create_database(self.db)
        self.assertFalse(self.db.exists())

    def test_cli_create_and_refuse_overwrite(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(localdelta.main(["create", "--db", str(self.db)]), 0)
        state = json.loads(stdout.getvalue())
        self.assertEqual(state["status"], "created")
        self.assertEqual(state["counts"]["assets"], 0)
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(localdelta.main(["create", "--db", str(self.db)]), 2)
        self.assertIn("绝不覆盖", stderr.getvalue())

    def test_new_database_accepts_native_asset_patch(self):
        workflow.create_database(self.db)
        with contextlib.closing(pdp.connect(self.db)) as connection:
            meta = pdp.get_meta(connection)
        aid = str(uuid.uuid4())
        pkg = {
            "protocol": pdp.PROTOCOL, "package_id": str(uuid.uuid4()),
            "database_id": meta["db_id"], "base_revision": 0,
            "schema_fingerprint": meta["schema_digest"],
            "base_data_fingerprint": meta["data_digest"],
            "created_at": pdp.now(), "description": "synthetic first asset",
            "evidence": [],
            "operations": [{"op_id": str(uuid.uuid4()), "action": "create", "table": "assets",
                            "source_key": "synthetic:new-asset:001",
                            "source": {"kind": "manual", "reference": "test/asset-001", "certainty": "confirmed"},
                            "record": {"asset_id": aid, "asset_name": "SYNTHETIC ETF", "account": "TEST ACCOUNT",
                                       "currency": "CNY", "asset_type": "基金", "status": "在持"}}],
        }
        pkg["content_hash"] = pdp.package_digest(pkg)
        pack = self.dir / "first-asset.pdp.json"
        pack.write_text(json.dumps(pkg), encoding="utf-8")
        review = workflow.preview(self.db, pack)
        self.assertEqual(review.preview["status"], "preview")
        self.assertEqual(workflow.apply(review)["status"], "applied")
        self.assertEqual(workflow.status(self.db)["counts"]["assets"], 1)
        self.assertEqual(workflow.preview(self.db, pack).preview["status"], "already_applied")

    def test_terminal_menu_new_database_keyword(self):
        entries = iter(["NEW", str(self.db), "0"])
        with mock.patch("builtins.input", side_effect=lambda *_: next(entries)):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(__import__('terminal_ui').menu(), 0)
        self.assertEqual(workflow.status(self.db)["counts"]["assets"], 0)

    def test_gui_demo_flag_off_by_default(self):
        try:
            import desktop_gui
        except ImportError:
            self.skipTest("Tkinter missing")
        with mock.patch.object(desktop_gui.tk, "Tk") as tk_ctor, mock.patch.object(desktop_gui, "LocalDeltaApp") as app:
            self.assertEqual(desktop_gui.launch([]), 0)
            self.assertFalse(app.call_args.kwargs["show_demo"])
            app.reset_mock()
            self.assertEqual(desktop_gui.launch(["--show-demo"]), 0)
            self.assertTrue(app.call_args.kwargs["show_demo"])
            self.assertEqual(tk_ctor.call_count, 2)


if __name__ == "__main__":
    unittest.main()
