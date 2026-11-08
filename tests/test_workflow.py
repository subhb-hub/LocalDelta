"""Headless tests for the shared terminal + desktop workflow (synthetic data)."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from examples.create_demo import main as create_demo
import terminal_ui
import workflow
import localdelta


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='localdelta-workflow-test-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        create_demo(self.directory, quiet=True)
        self.db = self.directory / 'demo.sqlite3'
        self.package = self.directory / 'example_update.pdp.zip'

    def rows(self, table):
        # sqlite3.Connection.__exit__ commits/rolls back but DOES NOT close.
        # Windows holds open file handles, so use closing() before temp cleanup.
        with contextlib.closing(sqlite3.connect(self.db)) as c:
            return c.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]

    def test_preview_does_not_write(self):
        result = workflow.preview(self.db, self.package)
        self.assertEqual(result.preview['status'], 'preview')
        self.assertEqual(len(result.operations), 3)
        self.assertEqual(workflow.status(self.db)['revision'], 0)
        self.assertEqual(self.rows('assets'), 0)
        self.assertIn('示例指数基金（虚构）', workflow.readable_review(result))

    def test_apply_with_backup_and_duplicate_noop(self):
        review = workflow.preview(self.db, self.package)
        output = workflow.apply(review)
        self.assertEqual(output['status'], 'applied')
        self.assertTrue(Path(output['backup_path']).is_file())
        self.assertTrue(Path(output['receipt_path']).is_file())
        self.assertEqual(workflow.status(self.db)['revision'], 1)
        self.assertEqual(tuple(self.rows(x) for x in ('assets', 'transactions', 'snapshots')), (1, 1, 1))
        duplicate = workflow.preview(self.db, self.package)
        self.assertEqual(duplicate.preview['status'], 'already_applied')
        self.assertEqual(workflow.apply(duplicate)['status'], 'already_applied')
        self.assertEqual(self.rows('transactions'), 1)

    def test_package_changed_after_preview_rejected(self):
        review = workflow.preview(self.db, self.package)
        with self.package.open('ab') as f:
            f.write(b'EVIL TRAILING BYTES')
        with self.assertRaisesRegex(workflow.WorkflowError, '修改'):
            workflow.apply(review)
        self.assertEqual(self.rows('assets'), 0)

    def test_interactive_update_cannot_apply_in_non_tty(self):
        fake_stdin = io.StringIO('APPLY\n')
        with mock.patch.object(terminal_ui.sys, 'stdin', fake_stdin):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                success = terminal_ui.interactive_update(str(self.db), str(self.package))
        self.assertFalse(success)
        self.assertEqual(workflow.status(self.db)['revision'], 0)

    def test_cli_explicit_yes_still_previews(self):
        with contextlib.redirect_stdout(io.StringIO()):
            rc = terminal_ui.cli_update(['--db', str(self.db), '--package', str(self.package), '--yes'])
        self.assertEqual(rc, 0)
        self.assertEqual(workflow.status(self.db)['revision'], 1)

    def test_cloud_privacy_and_bind(self):
        workflow.apply(workflow.preview(self.db, self.package))
        opaque = workflow.cloud_context(self.db)
        alias = workflow.private_alias_map(self.db)
        raw = opaque.read_text(encoding='utf-8')
        self.assertNotIn('示例指数基金', raw)
        self.assertNotIn('演示账户', raw)
        self.assertNotIn('1012.34', raw)
        self.assertIn('示例指数基金', alias.read_text(encoding='utf-8'))
        ctx = json.loads(raw)
        draft = {
            'format':'LocalDeltaDraft/1',
            'context_token':ctx['context_token'],
            'operations':[{ 'action':'create', 'table':'snapshots',
                'asset_token':ctx['assets'][0]['token'],
                'record':{'screenshot_date':'2026-11-09','market_value_cny':'1025.80',
                          'needs_valuation_date_check':True},
                'source':{'kind':'manual','reference':'synthetic-new-valuation','certainty':'confirmed'}}]
        }
        f = self.directory / 'draft.json'
        f.write_text(json.dumps(draft), encoding='utf-8')
        package = workflow.bind_draft(self.db, f)
        self.assertTrue(package.is_file())
        review = workflow.preview(self.db, package)
        self.assertEqual(len(review.operations), 1)
        workflow.apply(review)
        self.assertEqual(workflow.status(self.db)['revision'], 2)
        self.assertEqual(self.rows('snapshots'), 2)

    def test_gui_entry_import_no_display_dependency(self):
        try:
            import desktop_gui
        except ImportError:
            self.skipTest('Tkinter is not available in this Python build')
        self.assertTrue(callable(desktop_gui.launch))
        # CI on Windows can have cp1252 stdout even for UTF-8 source text.
        # Capture help in memory, independently of runner console encoding.
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                desktop_gui.launch(['--help'])
        self.assertEqual(cm.exception.code, 0)

    def test_demo_is_isolated(self):
        with mock.patch.object(Path, 'home', return_value=self.directory):
            with contextlib.redirect_stdout(io.StringIO()):
                demo_db, demo_pkg = workflow.make_demo()
        self.assertTrue(demo_db.exists())
        self.assertTrue(demo_pkg.exists())
        self.assertNotEqual(demo_db, self.db)
        self.assertEqual(workflow.status(demo_db)['counts']['assets'], 0)

    def test_stale_review_rejects_after_other_update(self):
        initial = workflow.preview(self.db, self.package)
        workflow.apply(initial)
        with self.assertRaises(workflow.WorkflowError):
            workflow.apply(initial)
        self.assertEqual(self.rows('assets'), 1)

    def test_missing_package_fails_closed(self):
        with self.assertRaises(workflow.WorkflowError):
            workflow.preview(self.db, self.directory/'absent.pdp.zip')
        self.assertEqual(workflow.status(self.db)['revision'], 0)

    def test_low_level_cli_kept_intact(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(localdelta.main(['status', '--db', str(self.db)]), 0)
        self.assertIn('healthy', out.getvalue())


if __name__ == '__main__':
    unittest.main()
