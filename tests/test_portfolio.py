"""Synthetic portfolio structure / cash classification tests; no user data."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

import analytics
import dashboard
import ledger
import multicurrency
import pdp
import portfolio
import statements
import workflow
from test_statements_dashboard import synthetic_pack


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='localdelta-portfolio-test-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.db = self.folder/'synthetic.sqlite3'
        self.asset, self.placeholder = str(uuid.uuid4()), str(uuid.uuid4())
        with closing(sqlite3.connect(self.db)) as c:
            c.executescript((Path(__file__).resolve().parents[1]/'legacy_schema.sql').read_text())
            for aid, name, state in [(self.asset,'SYNTHETIC Fund','在持'),(self.placeholder,'SYNTHETIC legacy placeholder','历史记录')]:
                c.execute('INSERT INTO assets (asset_id,asset_name,account,asset_type,currency,status,position_quantity) '
                          'VALUES (?,?,?,?,?,?,?)',(aid,name,'Synthetic account','基金','CNY',state,2.5))
            c.execute('INSERT INTO snapshots (snapshot_id,asset_id,account,currency,market_value_cny) VALUES (?,?,?,?,?)',
                      (str(uuid.uuid4()),self.asset,'Synthetic account','CNY',123.45))
            c.commit()
        workflow.initialize(self.db)
        multicurrency.upgrade(self.db);ledger.upgrade(self.db);statements.upgrade(self.db)
        archive = self.folder/'synthetic.zip';synthetic_pack(archive)
        statements.import_bundle(self.db,archive)
        csv = self.folder/'synthetic.csv'
        csv.write_text('date,currency,signed_amount,statement_amount,reference\n'
                       '2026-03-01,HKD,,9.00,SYN-CAP\n'
                       '2026-03-02,CNY,-1.00,,SYN-PERSONAL\n'
                       '2026-03-03,CNY,-4.00,,SYN-INTERNAL\n',encoding='utf-8')
        statements.import_plan(self.db,statements.parse_cash_csv(csv,'synthetic-funding','SYNTHETIC','Synthetic funding'))
        with closing(pdp.connect(self.db)) as c:
            self.original_assets=[tuple(r) for r in c.execute('SELECT * FROM assets ORDER BY asset_id')]
            self.original_snapshots=[tuple(r) for r in c.execute('SELECT * FROM snapshots ORDER BY snapshot_id')]
            self.source_account=dict(c.execute("SELECT * FROM ld_accounts WHERE institution='CMB'").fetchone())
            self.cash={json.loads(r['raw_json']).get('reference'):r['observation_id'] for r in c.execute("SELECT * FROM ld_observations WHERE kind='cash'")}
            self.link=c.execute('SELECT link_id FROM ld_transfer_links').fetchone()[0]
        self.broker=str(uuid.uuid4())

    def manifest(self):
        source = {k:self.source_account[k] for k in ledger.ACCOUNT_FIELDS}
        source['display_name']='Synthetic combined account'
        def classification(kind, ident, category, signed=None, basis=None, counter=None):
            return {'target_type':kind,'target_id':ident,'classification':category,
                    'signed_amount':signed,'basis_amount':basis,'basis_currency':'CNY' if basis else None,
                    'counter_account_id':counter,'user_note':'Synthetic explicit user confirmation'}
        return {'schema':'LocalDeltaPortfolio/1','source_ref':'synthetic-confirmation-v1',
                'source_text':'Synthetic source: confirm internal transfer, funding and personal exclusion.',
                'accounts':[source,{'account_id':self.broker,'institution':'SYNTHETIC',
                                   'display_name':'Synthetic broker','account_type':'broker','ownership':'owned'}],
                'memberships':[{'asset_id':self.asset,'account_id':source['account_id'],'instrument_key':'SYNTHETIC-FUND:CNY',
                                'mapping_status':'documented','source_ref':'synthetic bank source'},
                               {'asset_id':self.placeholder,'account_id':None,'instrument_key':'SYNTHETIC-PLACEHOLDER:CNY',
                                'mapping_status':'unresolved','source_ref':'synthetic old placeholder'}],
                'classifications':[classification('link',self.link,'internal_transfer'),
                                   classification('observation',self.cash['SYN-CAP'],'external_contribution','9.00','8.00'),
                                   classification('observation',self.cash['SYN-PERSONAL'],'non_investment','-1.00'),
                                   classification('observation',self.cash['SYN-INTERNAL'],'internal_transfer','-4.00',counter=self.broker)]}

    def test_disabled_views_work_with_older_databases(self):
        for view in ('portfolio','asset_catalog','capital'):
            self.assertFalse(analytics.query(self.db,view)['enabled'])
        self.assertIn('portfolio',dashboard.snapshot_data(self.db)['data'])

    def test_upgrade_and_import_preserve_all_snapshot_and_asset_rows(self):
        info=portfolio.upgrade(self.db)
        self.assertTrue(Path(info['backup_path']).exists())
        self.assertEqual(portfolio.upgrade(self.db)['status'],'already_upgraded')
        portfolio.apply_manifest(self.db,self.manifest())
        with closing(pdp.connect(self.db)) as c:
            self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM assets ORDER BY asset_id')],self.original_assets)
            self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM snapshots ORDER BY snapshot_id')],self.original_snapshots)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ld_portfolio_classifications').fetchone()[0],4)
        workflow.status(self.db)

    def test_confirmed_transfer_and_funding_are_distinct_and_exact(self):
        portfolio.upgrade(self.db);portfolio.apply_manifest(self.db,self.manifest())
        self.assertEqual(analytics.query(self.db,'timeline')['edges'][0]['certainty'],'confirmed')
        result=analytics.query(self.db,'capital')
        self.assertEqual(result['basis_totals'],[{'currency':'CNY','amount':'8.00'}])
        self.assertEqual(result['rows'][0]['received_amount'],'9.00')
        self.assertEqual(result['rows'][0]['received_currency'],'HKD')
        with closing(pdp.connect(self.db)) as c:
            self.assertIsNone(c.execute('SELECT signed_amount FROM ld_observations WHERE observation_id=?',
                                       (self.cash['SYN-CAP'],)).fetchone()[0])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ld_postings').fetchone()[0],0)

    def test_catalog_does_not_claim_valuation_or_current_holdings(self):
        portfolio.upgrade(self.db);portfolio.apply_manifest(self.db,self.manifest())
        rows=analytics.query(self.db,'asset_catalog',limit=1)
        self.assertEqual(rows['total'],2);self.assertTrue(rows['has_more'])
        self.assertEqual(rows['valuation_mode'],'deferred')
        for row in analytics.query(self.db,'asset_catalog')['rows']:
            self.assertNotIn('market_value_cny',row)
            self.assertNotIn('position_quantity',row)
        structure=analytics.query(self.db,'portfolio')
        self.assertEqual(structure['unresolved_asset_accounts'],1)
        broker=next(r for r in structure['rows'] if r['account_id']==self.broker)
        self.assertEqual(broker['asset_records'],0)  # registered account, not a fabricated zero balance
        self.assertNotIn('balance',broker)

    def test_idempotence_and_changed_source_guard(self):
        portfolio.upgrade(self.db);manifest=self.manifest();portfolio.apply_manifest(self.db,manifest)
        revision=workflow.status(self.db)['revision']
        self.assertEqual(portfolio.apply_manifest(self.db,manifest)['status'],'already_applied')
        self.assertEqual(workflow.status(self.db)['revision'],revision)
        manifest['accounts'][0]['display_name']='Changed mapping without new reviewed source'
        with self.assertRaises(pdp.PDPError):portfolio.apply_manifest(self.db,manifest)
        self.assertEqual(workflow.status(self.db)['revision'],revision)

    def test_invalid_late_target_rolls_back_entire_batch(self):
        portfolio.upgrade(self.db);m=self.manifest()
        m['classifications'][-1]['target_id']=str(uuid.uuid4())
        revision=workflow.status(self.db)['revision']
        with self.assertRaises(pdp.PDPError):portfolio.apply_manifest(self.db,m)
        self.assertEqual(workflow.status(self.db)['revision'],revision)
        with closing(pdp.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ld_portfolio_sources').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ld_review_decisions').fetchone()[0],0)
            self.assertFalse(c.execute('SELECT 1 FROM ld_accounts WHERE account_id=?',(self.broker,)).fetchone())

    def test_invalid_classification_and_account_takeover_rejected(self):
        portfolio.upgrade(self.db)
        m=self.manifest();m['classifications'][1]['signed_amount']='-9.00'
        with self.assertRaises(pdp.PDPError):portfolio.apply_manifest(self.db,m)
        m=self.manifest();m['classifications'][1]['basis_amount']='0.001'
        with self.assertRaises(pdp.PDPError):portfolio.validate_manifest(m)
        m=self.manifest();m['accounts'][0]['ownership']='third_party'
        with self.assertRaises(pdp.PDPError):portfolio.apply_manifest(self.db,m)
        workflow.status(self.db)

    def test_out_of_band_portfolio_change_is_detected(self):
        portfolio.upgrade(self.db);portfolio.apply_manifest(self.db,self.manifest())
        with closing(pdp.connect(self.db)) as c:c.execute("UPDATE ld_asset_memberships SET instrument_key='tampered'")
        with self.assertRaises(pdp.PDPError):analytics.query(self.db,'asset_catalog')


if __name__=='__main__':unittest.main()
