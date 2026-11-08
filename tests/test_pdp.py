"""Run with: python -m unittest discover -s tests -v"""
import contextlib
import copy
import io
import json
import sqlite3
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pdp


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.ctx=tempfile.TemporaryDirectory()
        self.dir=Path(self.ctx.name)
        self.db=self.dir/'portfolio.sqlite3'
        c=sqlite3.connect(self.db)
        c.executescript((Path(__file__).resolve().parents[1]/'legacy_schema.sql').read_text(encoding='utf8'))
        c.close()
        self.run_cmd('init','--db',str(self.db))
        c=pdp.connect(self.db)
        self.meta=pdp.get_meta(c)
        c.close()
        self.asset=str(uuid.uuid4())

    def tearDown(self):self.ctx.cleanup()

    def run_cmd(self,*args):
        out=io.StringIO()
        with contextlib.redirect_stdout(out):
            code=pdp.main(list(args))
        self.assertEqual(code,0,out.getvalue())
        return out.getvalue()

    def current_digest(self):
        c=pdp.connect(self.db)
        try:return pdp.get_meta(c)['data_digest']
        finally:c.close()

    def pkg(self, ops, rev=0, **extra):
        d={
            'protocol':pdp.PROTOCOL,'package_id':str(uuid.uuid4()),'database_id':self.meta['db_id'],
            'base_revision':rev,'schema_fingerprint':self.meta['schema_digest'],
            'base_data_fingerprint':self.current_digest(),
            'created_at':'2026-11-08T15:00:00+08:00','description':'Automated demo',
            'evidence':[],'operations':ops}
        d.update(extra)
        d['content_hash']=pdp.package_digest(d)
        return d

    @staticmethod
    def source():return {'kind':'manual','reference':'test synthetic data','certainty':'confirmed'}

    def asset_op(self,pk=None,name='测试基金'):
        return {'op_id':str(uuid.uuid4()),'action':'create','table':'assets','source_key':'test:asset:'+str(uuid.uuid4()),'source':self.source(),
                'record':{'asset_id':pk or self.asset,'asset_name':name,'account':'单元测试账户','currency':'CNY','asset_type':'基金','status':'在持'}}

    def tx_op(self,pk=None,asset=None,amount='100.00',fee='1.00',net='99.00'):
        return {'op_id':str(uuid.uuid4()),'action':'create','table':'transactions','source_key':'test:transaction:'+str(uuid.uuid4()),'source':self.source(),
                'record':{'transaction_id':pk or str(uuid.uuid4()),'asset_id':asset or self.asset,'kind':'申购','account':'单元测试账户','currency':'CNY',
                          'order_datetime':'2026-11-08T14:00:00+08:00','total_paid_cny':amount,'fee_cny':fee,'net_invested_cny':net,'quantity':'99.00','unit_nav':'1.0000'}}

    def snapshot_op(self,pk=None,date='2026-11-08',amount='101.23'):
        return {'op_id':str(uuid.uuid4()),'action':'create','table':'snapshots','source_key':'test:snapshot:'+str(uuid.uuid4()),'source':self.source(),
                'record':{'snapshot_id':pk or str(uuid.uuid4()),'asset_id':self.asset,'account':'单元测试账户','currency':'CNY',
                          'screenshot_date':date,'market_value_cny':amount,'needs_valuation_date_check':True}}

    def file(self,pkg):
        path=self.dir/('update-'+pkg['package_id']+'.pdp.json')
        path.write_text(json.dumps(pkg,ensure_ascii=False,indent=2),encoding='utf8')
        return str(path)

    def value(self,query):
        c=sqlite3.connect(self.db)
        try:return c.execute(query).fetchone()[0]
        finally:c.close()

    def test_plan_is_not_commit(self):
        p=self.file(self.pkg([self.asset_op(),self.tx_op(),self.snapshot_op()]))
        self.assertIn('preview',self.run_cmd('plan','--db',str(self.db),'--package',p))
        self.assertEqual(self.value('SELECT COUNT(*) FROM assets'),0)
        self.assertEqual(self.value('SELECT revision FROM pdp_meta'),0)

    def test_atomic_apply_and_idempotence(self):
        p=self.file(self.pkg([self.asset_op(),self.tx_op(),self.snapshot_op()]))
        self.run_cmd('apply','--db',str(self.db),'--package',p)
        self.assertEqual(self.value('SELECT COUNT(*) FROM assets'),1)
        self.assertEqual(self.value('SELECT COUNT(*) FROM transactions'),1)
        self.assertEqual(self.value('SELECT COUNT(*) FROM snapshots'),1)
        self.assertEqual(self.value('SELECT revision FROM pdp_meta'),1)
        self.assertIn('already_applied',self.run_cmd('apply','--db',str(self.db),'--package',p))
        self.assertEqual(self.value('SELECT revision FROM pdp_meta'),1)
        self.assertEqual(self.value('SELECT COUNT(*) FROM pdp_exact_values'),6)  # five trade decimals + one snapshot decimal

    def test_atomic_rollback_after_second_bad_op(self):
        bad=[self.asset_op(),self.asset_op(pk=str(uuid.uuid4()),name='测试基金')]
        p=self.file(self.pkg(bad))
        with self.assertRaises(pdp.PDPError):
            self._direct(p)
        self.assertEqual(self.value('SELECT COUNT(*) FROM assets'),0)
        self.assertEqual(self.value('SELECT COUNT(*) FROM pdp_packages'),0)

    def _direct(self,file):
        pkg, evidence=pdp.load_package(file)
        pdp.validate_pkg(pkg,evidence)
        c=pdp.connect(self.db)
        try:
            c.execute('BEGIN IMMEDIATE')
            try:return pdp.process(c,pkg,True)
            finally:c.execute('ROLLBACK')
        finally:c.close()

    def test_hash_integrity(self):
        pkg=self.pkg([self.asset_op()]);pkg['description']='tampered'
        with self.assertRaises(pdp.PDPError):pdp.validate_pkg(pkg,{})

    def test_revision_conflict(self):
        p=self.file(self.pkg([self.asset_op()],rev=9))
        with self.assertRaises(pdp.PDPError):self._direct(p)
        self.assertEqual(self.value('SELECT revision FROM pdp_meta'),0)

    def test_cas_conflict(self):
        p=self.file(self.pkg([self.asset_op()]))
        self.run_cmd('apply','--db',str(self.db),'--package',p)
        cor={'op_id':str(uuid.uuid4()),'action':'correct','table':'assets','id':self.asset,
             'before':{'notes':'wrong old text'},'changes':{'notes':'new text'},
             'reason':'test change','source':self.source()}
        q=self.file(self.pkg([cor],rev=1))
        with self.assertRaises(pdp.PDPError):self._direct(q)
        self.assertEqual(self.value('SELECT revision FROM pdp_meta'),1)

    def test_float_money_rejected(self):
        op=self.tx_op();op['record']['total_paid_cny']=100.0
        p=self.file(self.pkg([self.asset_op(),op]))
        with self.assertRaises(pdp.PDPError):self._direct(p)

    def test_invalid_math_rejected(self):
        p=self.file(self.pkg([self.asset_op(),self.tx_op(amount='100.00',fee='2.00',net='99.00')]))
        with self.assertRaises(pdp.PDPError):self._direct(p)

    def test_foreign_key_rejected(self):
        op=self.tx_op(asset=str(uuid.uuid4()))
        p=self.file(self.pkg([op]))
        with self.assertRaises(pdp.PDPError):self._direct(p)

    def test_suspected_duplicate_existing_snapshot(self):
        p=self.file(self.pkg([self.asset_op(),self.snapshot_op()]))
        self.run_cmd('apply','--db',str(self.db),'--package',p)
        q=self.file(self.pkg([self.snapshot_op()],rev=1))
        with self.assertRaises(pdp.PDPError):self._direct(q)

    def test_void_and_active_view(self):
        txid=str(uuid.uuid4())
        p=self.file(self.pkg([self.asset_op(),self.tx_op(pk=txid)]))
        self.run_cmd('apply','--db',str(self.db),'--package',p)
        void={'op_id':str(uuid.uuid4()),'action':'void','table':'transactions','id':txid,
              'before':{'kind':'申购','total_paid_cny':'100.00'},'reason':'simulation cancellation','source':self.source()}
        q=self.file(self.pkg([void],rev=1))
        self.run_cmd('apply','--db',str(self.db),'--package',q)
        self.assertEqual(self.value('SELECT COUNT(*) FROM transactions'),1)
        self.assertEqual(self.value('SELECT COUNT(*) FROM pdp_active_transactions'),0)

    def test_detects_out_of_band_edit(self):
        p=self.file(self.pkg([self.asset_op()]))
        self.run_cmd('apply','--db',str(self.db),'--package',p)
        c=sqlite3.connect(self.db)
        try:
            c.execute("UPDATE assets SET notes=? WHERE asset_id=?",('manual, untracked',self.asset));c.commit()
        finally:c.close()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(pdp.main(['status','--db',str(self.db)]),2)
        # Package should not accept the tampered db even if revision matches.
        o={'op_id':str(uuid.uuid4()),'action':'correct','table':'assets','id':self.asset,
           'before':{'status':'在持'},'changes':{'status':'待核实'},
           'reason':'test','source':self.source()}
        q=self.file(self.pkg([o],rev=1))
        with self.assertRaises(pdp.PDPError):self._direct(q)

    def test_stale_branch_fingerprint(self):
        p=self.file(self.pkg([self.asset_op()]))
        self.run_cmd('apply','--db',str(self.db),'--package',p)
        o={'op_id':str(uuid.uuid4()),'action':'correct','table':'assets','id':self.asset,
           'before':{'status':'在持'},'changes':{'status':'待核实'},
           'reason':'test','source':self.source()}
        q=self.pkg([o],rev=1)
        q['base_data_fingerprint']='0'*64
        q['content_hash']=pdp.package_digest(q)
        with self.assertRaises(pdp.PDPError):self._direct(self.file(q))

    def test_restore_to_pre_apply_backup(self):
        p=self.file(self.pkg([self.asset_op()]))
        out=json.loads(self.run_cmd('apply','--db',str(self.db),'--package',p))
        self.assertEqual(self.value('SELECT COUNT(*) FROM assets'),1)
        self.run_cmd('restore','--db',str(self.db),'--backup',out['backup_path'],'--confirm-restore')
        self.assertEqual(self.value('SELECT COUNT(*) FROM assets'),0)
        self.assertEqual(self.value('SELECT revision FROM pdp_meta'),0)

    def test_untrusted_column_rejected(self):
        op=self.asset_op();op['record']['asset_name); DROP TABLE assets; --']='bad'
        with self.assertRaises(pdp.PDPError):self._direct(self.file(self.pkg([op])))

    def test_correction_and_exact_decimal(self):
        snapid=str(uuid.uuid4())
        p=self.file(self.pkg([self.asset_op(),self.snapshot_op(pk=snapid)]))
        self.run_cmd('apply','--db',str(self.db),'--package',p)
        op={'op_id':str(uuid.uuid4()),'action':'correct','table':'snapshots','id':snapid,
            'before':{'market_value_cny':'101.23'},'changes':{'market_value_cny':'102.31'},
            'reason':'source reviewed','source':self.source()}
        q=self.file(self.pkg([op],rev=1))
        self.run_cmd('apply','--db',str(self.db),'--package',q)
        self.assertEqual(self.value('SELECT market_value_cny FROM snapshots'),102.31)
        self.assertEqual(self.value("SELECT decimal_value FROM pdp_exact_values WHERE column_name='market_value_cny'"),'102.31')
        self.assertEqual(self.value('SELECT COUNT(*) FROM pdp_operations'),3)


if __name__=='__main__':unittest.main()
