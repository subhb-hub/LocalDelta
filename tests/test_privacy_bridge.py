import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pdp
import localdelta
import privacy_bridge


class PrivacyBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.dir=Path(self.temp.name)
        self.db=self.dir/'portfolio.sqlite3'
        self.asset_id=str(uuid.uuid4())
        c=sqlite3.connect(self.db)
        c.executescript((Path(__file__).resolve().parents[1]/'legacy_schema.sql').read_text(encoding='utf-8'))
        c.execute('INSERT INTO assets (asset_id,asset_name,account,currency,asset_type,status) VALUES (?,?,?,?,?,?)',
                  (self.asset_id,'SUPER-PRIVATE-INVESTMENT-NAME','secret-broker','CNY','fund','held'))
        c.commit()
        c.close()
        self.run_cmd('init','--db',str(self.db))
        self.cloud_file=self.dir/'cloud.json'
        self.run_cmd('cloud-context','--db',str(self.db),'--out',str(self.cloud_file))
        self.cloud=json.loads(self.cloud_file.read_text())
        self.token=self.cloud['assets'][0]['token']
        self.draft_file=self.dir/'draft.json'
        self.pkg_file=self.dir/'update.pdp.json'

    def tearDown(self):
        self.temp.cleanup()

    def run_cmd(self,*args):
        buf=io.StringIO()
        with contextlib.redirect_stdout(buf):
            code=localdelta.main(args)
        self.assertEqual(code,0,buf.getvalue())
        return json.loads(buf.getvalue()) if buf.getvalue().lstrip().startswith('{') else buf.getvalue()

    def draft(self,**changes):
        d={
            'format':privacy_bridge.DRAFT_FORMAT,
            'context_token':self.cloud['context_token'],
            'operations':[{ 'action':'create','table':'snapshots','asset_token':self.token,
                            'record':{'screenshot_date':'2026-11-08','market_value_cny':'123.45','needs_valuation_date_check':True},
                            'source':{'kind':'manual','reference':'synthetic-test-record-0001','certainty':'confirmed'}}]
        }
        d.update(changes)
        self.draft_file.write_text(json.dumps(d,ensure_ascii=False),encoding='utf-8')
        return d

    def test_context_does_not_leak_names_accounts_balances_or_refs(self):
        t=self.cloud_file.read_text()
        for secret in ('SUPER-PRIVATE','secret-broker','portfolio.sqlite3','transaction_ref','123.45','data_fingerprint','schema_fingerprint','database_id'):
            self.assertNotIn(secret,t)
        self.assertEqual(len(self.token),36)
        self.assertEqual(len(self.cloud['assets']),1)

    def test_cloud_context_has_private_secret_outside_database(self):
        k=privacy_bridge.privacy_key_path(self.db)
        self.assertTrue(k.exists())
        self.assertEqual(len(k.read_bytes()),32)
        self.assertNotEqual(k.read_bytes().hex(),self.cloud['context_token'])

    def test_bind_plan_apply_reimport(self):
        self.draft()
        self.run_cmd('bind-draft','--db',str(self.db),'--draft',str(self.draft_file),'--out',str(self.pkg_file))
        patch=json.loads(self.pkg_file.read_text())
        self.assertEqual(patch['operations'][0]['record']['account'],'secret-broker')
        self.assertEqual(patch['operations'][0]['record']['asset_id'],self.asset_id)
        self.assertNotIn(self.token,self.pkg_file.read_text())
        self.assertEqual(self.run_cmd('plan','--db',str(self.db),'--package',str(self.pkg_file))['status'],'preview')
        self.assertEqual(self.run_cmd('apply','--db',str(self.db),'--package',str(self.pkg_file))['status'],'applied')
        self.assertEqual(self.run_cmd('apply','--db',str(self.db),'--package',str(self.pkg_file))['status'],'already_applied')
        c=sqlite3.connect(self.db)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0],1)
        c.close()

    def test_draft_stale_after_update(self):
        self.draft()
        self.run_cmd('bind-draft','--db',str(self.db),'--draft',str(self.draft_file),'--out',str(self.pkg_file))
        self.run_cmd('apply','--db',str(self.db),'--package',str(self.pkg_file))
        with self.assertRaisesRegex(pdp.PDPError,'已过期'):
            privacy_bridge.bind_draft(self.db,self.draft_file,self.dir/'other.json')

    def test_reject_unrecognized_asset_token(self):
        d=self.draft()
        d['operations'][0]['asset_token']='AST-'+'a'*32
        self.draft_file.write_text(json.dumps(d),encoding='utf-8')
        with self.assertRaisesRegex(pdp.PDPError,'代号不存在'):
            privacy_bridge.bind_draft(self.db,self.draft_file,self.pkg_file)

    def test_reject_sensitive_identifying_fields(self):
        d=self.draft()
        d['operations'][0]['record']['account']='remote-controlled'
        self.draft_file.write_text(json.dumps(d),encoding='utf-8')
        with self.assertRaisesRegex(pdp.PDPError,'不能指定'):
            privacy_bridge.bind_draft(self.db,self.draft_file,self.pkg_file)

    def test_reject_unconfirmed_source(self):
        d=self.draft()
        d['operations'][0]['source']['certainty']='uncertain'
        self.draft_file.write_text(json.dumps(d),encoding='utf-8')
        with self.assertRaisesRegex(pdp.PDPError,'未核实'):
            privacy_bridge.bind_draft(self.db,self.draft_file,self.pkg_file)

    def test_reject_invalid_decimal_precision(self):
        d=self.draft()
        d['operations'][0]['record']['market_value_cny']='1.001'
        self.draft_file.write_text(json.dumps(d),encoding='utf-8')
        with self.assertRaisesRegex(pdp.PDPError,'最多两位'):
            privacy_bridge.bind_draft(self.db,self.draft_file,self.pkg_file)

    def test_reject_unknown_record_column(self):
        d=self.draft()
        d['operations'][0]['record']['dangerous_sql']='DROP TABLE assets'
        self.draft_file.write_text(json.dumps(d),encoding='utf-8')
        with self.assertRaisesRegex(pdp.PDPError,'未识别的字段'):
            privacy_bridge.bind_draft(self.db,self.draft_file,self.pkg_file)

    def test_reject_duplicate_draft(self):
        d=self.draft()
        d['operations'].append(d['operations'][0])
        self.draft_file.write_text(json.dumps(d),encoding='utf-8')
        with self.assertRaisesRegex(pdp.PDPError,'重复来源'):
            privacy_bridge.bind_draft(self.db,self.draft_file,self.pkg_file)

    def test_local_account_currency_are_resolved_not_sent(self):
        d=self.draft()
        d['operations'][0]['table']='transactions'
        d['operations'][0]['record']={'kind':'申购','order_datetime':'2026-11-08','total_paid_cny':'100.00','fee_cny':'0.20','net_invested_cny':'99.80'}
        self.draft_file.write_text(json.dumps(d),encoding='utf-8')
        self.run_cmd('bind-draft','--db',str(self.db),'--draft',str(self.draft_file),'--out',str(self.pkg_file))
        self.assertEqual(self.run_cmd('plan','--db',str(self.db),'--package',str(self.pkg_file))['status'],'preview')

    def test_private_alias_map_is_separate(self):
        out=self.dir/'private-aliases.json'
        privacy_bridge.export_local_aliases(self.db,out)
        self.assertIn('SUPER-PRIVATE-INVESTMENT-NAME',out.read_text())
        self.assertNotIn('SUPER-PRIVATE-INVESTMENT-NAME',self.cloud_file.read_text())
        self.assertIn(self.token,out.read_text())

    def test_category_opt_in(self):
        p=self.dir/'category.json'
        privacy_bridge.export_context(self.db,p,'category')
        t=p.read_text()
        self.assertIn('fund',t)
        self.assertNotIn('SUPER-PRIVATE',t)


if __name__=='__main__':unittest.main()
