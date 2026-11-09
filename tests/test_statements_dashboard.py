"""Synthetic-only integration tests; never load user statements in CI."""
from contextlib import closing
from copy import deepcopy
import csv
import hashlib
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile

import analytics
import dashboard
import ledger
import pdp
import statements
import workflow


def csv_bytes(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode('utf-8-sig')


def synthetic_pack(path):
    cash = {'statement': 'synthetic-a.pdf', 'page': '1', 'account_type': 'bank_statement',
            'date': '2026-03-01', 'currency': 'CNY', 'signed_amount': '-12.34',
            'balance': '87.66', 'description': 'synthetic transfer'}
    unsigned = {'statement': 'synthetic-b.pdf', 'page': '1', 'section': 'HKD Savings',
                'date': '2026-03-02', 'description': 'synthetic receipt',
                'statement_amount': '13.00', 'ending_balance': '13.00'}
    receipt = {'id': 'SYN-B-1', 'date': '2026-03-02', 'amount': '13.00',
               'currency': 'HKD', 'statement': 'synthetic-b.pdf', 'page': 1}
    outgoing = dict(cash, id='SYN-A-1', amount_cny='12.34', receipt_id='SYN-B-1')
    match = {'cmb': [outgoing], 'za': [receipt], 'hsbc': [], 'za_hsbc': []}
    files = {'cash-a.csv': csv_bytes([cash]), 'cash-b.csv': csv_bytes([unsigned]),
             'matches.json': json.dumps(match).encode(), 'evidence/synthetic.txt': b'synthetic evidence only'}
    manifest = {'files': [{'path': k, 'bytes': len(v), 'sha256': hashlib.sha256(v).hexdigest()}
                          for k, v in files.items()]}
    with zipfile.ZipFile(path, 'w') as z:
        for k, v in files.items():
            z.writestr(k, v)
        z.writestr('MANIFEST.json', json.dumps(manifest))


class StatementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='localdelta-synthetic-statements-')
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.db = self.folder / 'synthetic.sqlite3'
        workflow.create_database(self.db, enable_multicurrency=True)
        ledger.upgrade(self.db)
        self.archive = self.folder / 'synthetic.zip'
        synthetic_pack(self.archive)

    def import_pack(self):
        statements.upgrade(self.db)
        return statements.import_bundle(self.db, self.archive)

    def healthy(self):
        return workflow.status(self.db)

    def test_additive_audited_import_preserves_unknown_direction_and_is_idempotent(self):
        old = self.healthy()
        info = self.import_pack()
        self.assertEqual(info['journal_postings_created'], 0)
        self.assertTrue(Path(info['backup_path']).is_file())
        self.assertEqual(self.healthy()['revision'], old['revision'] + 2)
        revision = self.healthy()['revision']
        self.assertEqual(statements.import_bundle(self.db, self.archive)['status'], 'already_imported')
        self.assertEqual(self.healthy()['revision'], revision)
        with closing(statements.read_connection(self.db)) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ld_postings').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ld_source_documents').fetchone()[0], 5)
            self.assertEqual(c.execute("SELECT signed_amount FROM ld_observations WHERE status='direction_unknown'").fetchone()[0], None)
            self.assertEqual(c.execute('SELECT certainty FROM ld_transfer_links').fetchone()[0], 'likely')
        summary = analytics.query(self.db, 'summary')
        self.assertIsNone(summary['investment_return'])
        pending = next(x for x in summary['rows'] if x['currency'] == 'HKD')
        self.assertIsNone(pending['net_movement'])
        self.assertEqual(pending['unknown_direction_rows'], 1)

    def test_expected_hash_and_manifest_mismatch_reject_without_writing(self):
        statements.upgrade(self.db)
        old = self.healthy()['revision']
        with self.assertRaises(pdp.PDPError):
            statements.import_bundle(self.db, self.archive, '0' * 64)
        with zipfile.ZipFile(self.archive) as z:
            content = {n: z.read(n) for n in z.namelist()}
        content['evidence/synthetic.txt'] = b'tampered synthetic evidence'
        with zipfile.ZipFile(self.archive, 'w') as z:
            for name, value in content.items():
                z.writestr(name, value)
        with self.assertRaises(pdp.PDPError):
            statements.import_bundle(self.db, self.archive)
        self.assertEqual(self.healthy()['revision'], old)

    def test_traversal_and_symbolic_links_rejected(self):
        for name in ('../escape.csv', '/absolute.csv', 'C:/windows.csv', 'a\\b.csv'):
            with zipfile.ZipFile(self.archive, 'w') as z:
                z.writestr(name, 'x')
            with self.assertRaises(pdp.PDPError):
                statements.preview_bundle(self.archive)
        with zipfile.ZipFile(self.archive, 'w') as z:
            info = zipfile.ZipInfo('link')
            info.external_attr = 0o120777 << 16
            z.writestr(info, 'target')
        with self.assertRaises(pdp.PDPError):
            statements.preview_bundle(self.archive)

    def test_split_allocations_both_sides_and_overallocation(self):
        plan = statements.parse_bundle(self.archive)
        link = plan['links'][0]
        first = dict(link, debit_amount='6.00', credit_amount='6.00')
        second = dict(link, link_id=statements.ident('synthetic-second'), debit_amount='6.34', credit_amount='7.00')
        plan['links'] = [first, second]
        statements.validate_plan(plan)
        plan['links'][1]['credit_amount'] = '7.01'
        with self.assertRaises(pdp.PDPError):
            statements.validate_plan(plan)
        plan['links'] = [dict(link, debit_amount='12.35')]
        with self.assertRaises(pdp.PDPError):
            statements.validate_plan(plan)

    def test_cross_batch_allocation_overflow_rolls_back(self):
        self.import_pack()
        plan = statements.parse_bundle(self.archive)
        plan['content_hash'] = 'e' * 64
        plan['batch_id'] = statements.ident('synthetic-overlap')
        plan['links'][0]['link_id'] = statements.ident('synthetic-conflicting-link')
        plan['links'][0]['batch_id'] = plan['batch_id']
        old = self.healthy()['revision']
        with self.assertRaises(pdp.PDPError):
            statements.import_plan(self.db, plan)
        self.assertEqual(self.healthy()['revision'], old)
        self.assertEqual(analytics.query(self.db, 'timeline')['total'], 1)

    def test_out_of_band_mutation_rejected_by_api(self):
        self.import_pack()
        with closing(pdp.connect(self.db)) as c:
            c.execute("UPDATE ld_observations SET signed_amount='-99.00' WHERE signed_amount IS NOT NULL")
        with self.assertRaises(pdp.PDPError):
            analytics.query(self.db, 'summary')

    def test_pagination_filters_overlap_and_query_indexes(self):
        self.import_pack()
        accounts = analytics.query(self.db, 'accounts')['accounts']
        source = next(x['account_id'] for x in accounts if x['institution'] == 'CMB')
        rows = analytics.query(self.db, 'movements', account_ids=[source], start='2026-03-01', end='2026-03-01', limit=1)
        self.assertEqual(rows['total'], 1)
        self.assertFalse(rows['has_more'])
        self.assertEqual(analytics.query(self.db, 'movements', account_ids=[], limit=1)['total'], 0)
        self.assertEqual(analytics.query(self.db, 'timeline', start='2026-03-02', end='2026-03-02')['total'], 1)
        self.assertEqual(analytics.query(self.db, 'timeline', account_ids=[source])['total'], 1)
        with closing(statements.read_connection(self.db)) as c:
            details = ' '.join(str(tuple(r)) for r in c.execute(
                'EXPLAIN QUERY PLAN SELECT observation_id FROM ld_observations WHERE account_id=? AND date>=? AND date<=?',
                (source, '2026-03-01', '2026-03-03')))
            self.assertIn('ld_observations_account_date', details)
            self.assertNotIn('SCAN ld_observations', details)
        for kwargs in ({'limit': 0}, {'offset': -1}, {'account_ids': [{}]}, {'start': '20260301'},
                       {'start': '2026-04-01', 'end': '2026-03-01'}, {'view': 'arbitrary SQL'}):
            with self.assertRaises(pdp.PDPError):
                analytics.query(self.db, **dict({'view': 'movements'}, **kwargs))

    def test_generic_csv_exact_currency_and_overlap_reference_conflict(self):
        statements.upgrade(self.db)
        path = self.folder / 'generic.csv'
        rows = [{'date': '2026-02-01', 'currency': 'KWD', 'signed_amount': '1.234',
                 'reference': 'SYN-1', 'description': '</script><script>window.bad=1</script>'}]
        path.write_bytes(csv_bytes(rows))
        plan = statements.parse_cash_csv(path, 'synthetic-bank', 'SYNTHETIC', 'Bank A')
        statements.import_plan(self.db, plan)
        self.assertEqual(analytics.query(self.db, 'summary')['rows'][0]['inflow'], '1.234')
        # Re-export with a different file name/extra row retains reference identity.
        rows.append(dict(rows[0], reference='SYN-2', signed_amount='2.000'))
        path.write_bytes(csv_bytes(rows))
        statements.import_plan(self.db, statements.parse_cash_csv(path, 'synthetic-bank', 'SYNTHETIC', 'Bank A'))
        self.assertEqual(analytics.query(self.db, 'movements')['total'], 2)
        old = self.healthy()['revision']
        rows[0]['signed_amount'] = '9.999'
        path.write_bytes(csv_bytes(rows))
        with self.assertRaises(pdp.PDPError):
            statements.import_plan(self.db, statements.parse_cash_csv(path, 'synthetic-bank', 'SYNTHETIC', 'Bank A'))
        self.assertEqual(self.healthy()['revision'], old)
        html = dashboard.render(dashboard.snapshot_data(self.db))
        self.assertNotIn('</script><script>window.bad=1', html)
        self.assertIn('\\u003c/script', html)

    def test_snapshot_all_pages_and_registry(self):
        self.import_pack()
        data = dashboard.snapshot_data(self.db)
        self.assertEqual(data['data']['movements']['total'], len(data['data']['movements']['rows']))
        self.assertEqual(set(data['data']), analytics.VIEWS)
        out = self.folder / 'private.html'
        dashboard.export(self.db, out)
        self.assertTrue(out.exists())
        with self.assertRaises(ValueError):
            analytics.register_block('bad', 'Bad', 'SELECT secret')

    def test_review_history_changes_effective_data_without_mutating_evidence(self):
        self.import_pack()
        pending = next(x for x in analytics.query(self.db, 'movements')['rows'] if x['signed_amount'] is None)
        statements.review(self.db, pending['observation_id'], 'direction', 'Synthetic original column checked', '13.00')
        effective = analytics.record(self.db, pending['observation_id'])
        self.assertEqual(effective['signed_amount'], '13.00')
        self.assertIsNone(effective['raw_signed_amount'])
        self.assertEqual(len(effective['reviews']), 1)
        self.assertEqual(analytics.query(self.db, 'review')['total'], 0)
        with self.assertRaises(pdp.PDPError):
            statements.review(self.db, pending['observation_id'], 'direction', 'Synthetic wrong amount', '99.00')
        link = analytics.query(self.db, 'timeline')['edges'][0]
        statements.review(self.db, link['link_id'], 'rejected', 'Synthetic conflicting reference')
        self.assertEqual(analytics.query(self.db, 'timeline')['total'], 0)
        statements.review(self.db, link['link_id'], 'confirmed', 'Synthetic both endpoints checked')
        self.assertEqual(analytics.query(self.db, 'timeline')['edges'][0]['certainty'], 'confirmed')
        self.assertEqual(len(analytics.record(self.db, link['link_id'])['reviews']), 2)
        self.healthy()

    def test_loopback_api_requires_token_and_rejects_host_origin_writes(self):
        self.import_pack()
        server, token = dashboard.make_server(self.db)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def get(path, headers=None, method='GET'):
                c = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                try:
                    c.request(method, path, headers=headers or {})
                    r = c.getresponse()
                    return r.status, r.read().decode(), dict(r.headers)
                finally:
                    c.close()
            code, body, headers = get('/')
            self.assertEqual(code, 200)
            self.assertNotIn('synthetic transfer', body)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(get('/api/movements')[0], 401)
            auth = {'Authorization': 'Bearer ' + token}
            self.assertEqual(get('/api/movements', auth)[0], 200)
            self.assertEqual(get('/api/dashboard', auth)[0], 200)
            self.assertEqual(get('/api/movements?limit=1&limit=2', auth)[0], 400)
            self.assertEqual(get('/api/movements?limit=0', auth)[0], 400)
            self.assertEqual(get('/api/movements?sql=SELECT', auth)[0], 400)
            self.assertEqual(get('/api/movements', dict(auth, Host='evil.invalid'))[0], 403)
            self.assertEqual(get('/api/movements', dict(auth, Origin='https://evil.invalid'))[0], 403)
            self.assertEqual(get('/api/movements', auth, method='POST')[0], 501)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
