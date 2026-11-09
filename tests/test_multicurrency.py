"""Multicurrency ledger migration, native patch safety, FX and report tests."""
from __future__ import annotations
import contextlib
from decimal import Decimal
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

import localdelta
import multicurrency as mc
import pdp
import workflow

SOURCE={'kind':'manual','reference':'synthetic-unit-test','certainty':'confirmed'}

def op(table,record,money=None):
    o={'op_id':str(uuid.uuid4()),'action':'create','table':table,
       'source_key':f'synthetic:{table}:{uuid.uuid4()}', 'source':dict(SOURCE),'record':record}
    if money is not None:o['money']=money
    return o


class MultiCurrencyTests(unittest.TestCase):
    def setUp(self):
        self.ctx=tempfile.TemporaryDirectory(prefix='localdelta-mc-')
        self.addCleanup(self.ctx.cleanup)
        self.folder=Path(self.ctx.name)
        self.db=self.folder/'portfolio.sqlite3'
        workflow.create_database(self.db)

    def cmd(self,*args):
        out=io.StringIO();err=io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
            result=localdelta.main(list(args))
        if result:raise AssertionError(f'command {args!r} failed: {err.getvalue()}')
        return json.loads(out.getvalue())

    def meta(self):
        with contextlib.closing(pdp.connect(self.db)) as c:
            return pdp.get_meta(c)

    def package(self,operations,protocol=mc.PROTOCOL):
        m=self.meta()
        p={'protocol':protocol,'package_id':str(uuid.uuid4()),'database_id':m['db_id'],
           'base_revision':m['revision'],'base_data_fingerprint':m['data_digest'],
           'schema_fingerprint':m['schema_digest'],'created_at':pdp.now(),
           'description':'multi currency synthetic test','operations':operations,'evidence':[]}
        p['content_hash']=pdp.package_digest(p)
        fn=self.folder/('pack-'+p['package_id']+'.pdp.json')
        fn.write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8')
        return fn

    def apply(self,pkg):
        prev=self.cmd('plan','--db',str(self.db),'--package',str(pkg))
        self.assertEqual(prev['status'],'preview')
        return self.cmd('apply','--db',str(self.db),'--package',str(pkg))

    def upgraded(self):
        info=mc.upgrade(self.db)
        self.assertTrue(info['status'] in ('upgraded','already_upgraded'))
        return info

    def assets(self):
        cny=str(uuid.uuid4());usd=str(uuid.uuid4());hkd=str(uuid.uuid4())
        os=[op('assets',{'asset_id':k,'asset_name':n,'account':'SYNTHETIC','currency':code,'asset_type':'stock','status':'active'})
            for k,n,code in [(cny,'SYN CNY FUND','CNY'),(usd,'SYN USD STOCK','USD'),(hkd,'SYN HKD REIT','HKD')]]
        self.apply(self.package(os))
        return cny,usd,hkd

    def test_upgrade_empty_is_additive_idempotent_and_backup(self):
        m=self.meta()
        res=self.upgraded()
        self.assertEqual(res['revision'],m['revision']+1)
        self.assertEqual(res['backfilled'],0)
        self.assertTrue(Path(res['backup_path']).is_file())
        self.assertEqual(mc.upgrade(self.db)['status'],'already_upgraded')
        self.assertEqual(self.meta()['revision'],1)
        self.assertTrue(self.cmd('status','--db',str(self.db))['multicurrency_enabled'])

    def test_v2_needs_upgrade_and_rollback(self):
        a=str(uuid.uuid4())
        pack=self.package([op('assets',{'asset_id':a,'asset_name':'NEEDS UPGRADE','account':'X','currency':'USD'})])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(localdelta.main(['apply','--db',str(self.db),'--package',str(pack)]),2)
        self.assertEqual(self.meta()['revision'],0)

    def test_create_three_currencies_and_reference_conversion(self):
        self.upgraded();cny,usd,hkd=self.assets()
        all_ops=[
            op('snapshots',{'snapshot_id':str(uuid.uuid4()),'asset_id':cny,'account':'SYNTHETIC','currency':'CNY',
                            'screenshot_date':'2026-11-09','needs_valuation_date_check':True},
               {'market_value':{'amount':'100.00','currency':'CNY'}}),
            op('snapshots',{'snapshot_id':str(uuid.uuid4()),'asset_id':usd,'account':'SYNTHETIC','currency':'USD',
                            'screenshot_date':'2026-11-09','needs_valuation_date_check':True},
               {'market_value':{'amount':'50.00','currency':'USD'}}),
            op('snapshots',{'snapshot_id':str(uuid.uuid4()),'asset_id':hkd,'account':'SYNTHETIC','currency':'HKD',
                            'screenshot_date':'2026-11-09','needs_valuation_date_check':True},
               {'market_value':{'amount':'70.00','currency':'HKD'}}),
            op('fx_rates',{'rate_id':str(uuid.uuid4()),'base_currency':'USD','quote_currency':'CNY',
                           'rate':'7.20','rate_date':'2026-11-09','rate_kind':'reference','source_ref':'SYNTHETIC EXCHANGE SOURCE'}),
        ]
        p=self.package(all_ops)
        self.apply(p)
        result=mc.report(self.db,base='CNY',as_of='2026-11-09')
        self.assertEqual(result['native_totals'],{'CNY':'100.00','HKD':'70.00','USD':'50.00'})
        self.assertEqual(result['converted_known_total'],'460.00')
        self.assertFalse(result['complete'])
        self.assertEqual(result['missing_rates'][0]['needed_pair'],'HKD/CNY')
        with contextlib.closing(pdp.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM ld_currency_amounts').fetchone()[0],3)
            self.assertEqual(c.execute('SELECT count(*) FROM snapshots WHERE market_value_cny IS NOT NULL').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT count(*) FROM snapshots').fetchone()[0],3)
        self.assertEqual(self.cmd('apply','--db',str(self.db),'--package',str(p))['status'],'already_applied')

    def test_trade_quote_currency_differs_from_cash_settlement(self):
        self.upgraded();_,_,hkd=self.assets()
        txn=str(uuid.uuid4())
        o=op('transactions',{'transaction_id':txn,'asset_id':hkd,'account':'SYNTHETIC','currency':'HKD',
                             'kind':'buy','order_datetime':'2026-11-09T14:00:00+08:00',
                             'quantity':'100','unit_nav':'50'},
             {'trade_gross':{'amount':'5000.00','currency':'HKD'},
              'cash_paid':{'amount':'645.00','currency':'USD'},
              'fee':{'amount':'5.00','currency':'HKD'}})
        self.apply(self.package([o]))
        with contextlib.closing(pdp.connect(self.db)) as c:
            self.assertIsNone(c.execute('SELECT total_paid_cny FROM transactions WHERE transaction_id=?',(txn,)).fetchone()[0])
            data=mc.get_money(c,'transactions',txn)
            self.assertEqual(data['cash_paid']['currency'],'USD')
            self.assertEqual(data['trade_gross']['currency'],'HKD')

    def test_fx_no_lookahead_and_no_stale_default(self):
        self.upgraded();_,usd,_=self.assets()
        snap=op('snapshots',{'snapshot_id':str(uuid.uuid4()),'asset_id':usd,'account':'SYNTHETIC','currency':'USD',
                             'screenshot_date':'2026-11-08','needs_valuation_date_check':True},
                {'market_value':{'amount':'10.00','currency':'USD'}})
        fx=op('fx_rates',{'rate_id':str(uuid.uuid4()),'base_currency':'USD','quote_currency':'CNY',
                           'rate':'8.00','rate_date':'2026-11-10','rate_kind':'reference','source_ref':'SYN future'})
        self.apply(self.package([snap,fx]))
        r=mc.report(self.db,'CNY','2026-11-09',max_age=7)
        self.assertEqual(r['converted_known_total'],'0.00')
        self.assertFalse(r['complete'])
        r=mc.report(self.db,'CNY','2026-11-11',max_age=0)
        self.assertFalse(r['complete'])
        r=mc.report(self.db,'CNY','2026-11-11',max_age=7)
        self.assertEqual(r['converted_known_total'],'80.00')

    def test_multicurrency_package_rejects_invalid_money_formats(self):
        self.upgraded()
        aid=str(uuid.uuid4())
        errors=[{'amount':10.0,'currency':'USD'},{'amount':'10.003','currency':'USD'},
                {'amount':'10.00','currency':'ZZZ'},{'amount':'-1.00','currency':'USD'}]
        for sample in errors:
            with self.subTest(sample=sample):
                o=op('assets',{'asset_id':aid,'asset_name':'X','account':'SYN','currency':'USD'})
                ss=op('snapshots',{'snapshot_id':str(uuid.uuid4()),'asset_id':aid,'account':'SYN','currency':'USD',
                                  'screenshot_date':'2026-11-09'}, {'market_value':sample})
                p=self.package([o,ss])
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(localdelta.main(['apply','--db',str(self.db),'--package',str(p)]),2)
                self.assertEqual(self.meta()['revision'],1)
                with contextlib.closing(pdp.connect(self.db)) as c:
                    self.assertEqual(c.execute('SELECT count(*) FROM assets').fetchone()[0],0)

    def test_jpy_zero_minor_units_and_kuwait_three(self):
        self.assertEqual(mc.money_value({'amount':'100','currency':'JPY'},'jpy')['amount'],'100')
        with self.assertRaises(pdp.PDPError):mc.money_value({'amount':'100.50','currency':'JPY'},'jpy')
        self.assertEqual(mc.money_value({'amount':'1.234','currency':'KWD'},'kwd')['amount'],'1.234')

    def test_money_cas_correction_prevents_wrong_old_amount(self):
        self.upgraded();_,usd,_=self.assets()
        ident=str(uuid.uuid4())
        o=op('snapshots',{'snapshot_id':ident,'asset_id':usd,'account':'SYNTHETIC','currency':'USD',
                          'screenshot_date':'2026-11-09'}, {'market_value':{'amount':'30.00','currency':'USD'}})
        self.apply(self.package([o]))
        correct={'op_id':str(uuid.uuid4()),'action':'correct','table':'snapshots','id':ident,
                 'before':{},'changes':{},'money_before':{'market_value':{'amount':'30.00','currency':'USD'}},
                 'money_changes':{'market_value':{'amount':'31.00','currency':'USD'}},
                 'reason':'synthetic correction','source':dict(SOURCE)}
        self.apply(self.package([correct]))
        with contextlib.closing(pdp.connect(self.db)) as c:
            self.assertEqual(mc.get_money(c,'snapshots',ident)['market_value']['amount'],'31.00')
        wrong=dict(correct,op_id=str(uuid.uuid4()),money_before={'market_value':{'amount':'30.00','currency':'USD'}},
                   money_changes={'market_value':{'amount':'32.00','currency':'USD'}})
        p=self.package([wrong])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(localdelta.main(['apply','--db',str(self.db),'--package',str(p)]),2)
        with contextlib.closing(pdp.connect(self.db)) as c:
            self.assertEqual(mc.get_money(c,'snapshots',ident)['market_value']['amount'],'31.00')

    def test_legacy_cny_backfill_and_future_v1_sync(self):
        # Simulate old CNY records before migration.
        a=str(uuid.uuid4());s=str(uuid.uuid4())
        p=self.package([
            op('assets',{'asset_id':a,'asset_name':'SYN LEGACY FUND','account':'OLD','currency':'CNY'}),
            op('snapshots',{'snapshot_id':s,'asset_id':a,'account':'OLD','currency':'CNY',
                             'screenshot_date':'2026-11-08','market_value_cny':'123.45'})],pdp.PROTOCOL)
        self.apply(p)
        before=self.cmd('status','--db',str(self.db))['counts']
        info=self.upgraded()
        self.assertEqual(info['backfilled'],1)
        self.assertEqual(self.cmd('status','--db',str(self.db))['counts'],before)
        with contextlib.closing(pdp.connect(self.db)) as c:
            self.assertEqual(mc.get_money(c,'snapshots',s)['market_value'],{'currency':'CNY','amount':'123.45'})
        ns=str(uuid.uuid4())
        p=self.package([op('snapshots',{'snapshot_id':ns,'asset_id':a,'account':'OLD','currency':'CNY',
                     'screenshot_date':'2026-11-09','market_value_cny':'125.00'})],pdp.PROTOCOL)
        self.apply(p)
        with contextlib.closing(pdp.connect(self.db)) as c:
            self.assertEqual(mc.get_money(c,'snapshots',ns)['market_value']['amount'],'125.00')

    def test_report_does_not_claim_complete_with_missing_snapshots(self):
        self.upgraded();self.assets()
        r=mc.report(self.db,as_of='2026-11-09')
        self.assertEqual(len(r['unvalued_assets']),3)
        self.assertFalse(r['complete'])
        self.assertEqual(r['converted_known_total'],'0.00')

    def test_cloud_draft_v2_privacy_binding_usd_snapshot(self):
        import privacy_bridge
        self.upgraded();_,usd,_=self.assets()
        context_file=self.folder/'cloud.json'
        privacy_bridge.export_context(self.db, context_file)
        ctx=json.loads(context_file.read_text(encoding='utf-8'))
        self.assertTrue(ctx['supports_native_money'])
        with contextlib.closing(pdp.connect(self.db)) as c:
            meta=pdp.get_meta(c)
        key=privacy_bridge.load_key(self.db)
        token=privacy_bridge.asset_token(key,meta['db_id'],usd)
        draft={'format':'LocalDeltaDraft/2','context_token':ctx['context_token'],'operations':[
            {'action':'create','table':'snapshots','asset_token':token,
             'record':{'screenshot_date':'2026-11-09','needs_valuation_date_check':True},
             'money':{'market_value':{'amount':'1750.00','currency':'USD'}},
             'source':{'kind':'manual','reference':'Synthetic USD quote','certainty':'confirmed'}}
        ]}
        df=self.folder/'draft.json';df.write_text(json.dumps(draft),encoding='utf-8')
        output=self.folder/'bound.pdp.json'
        privacy_bridge.bind_draft(self.db,df,output)
        packet=json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(packet['protocol'],mc.PROTOCOL)
        self.assertEqual(packet['operations'][0]['record']['currency'],'USD')
        self.assertEqual(packet['operations'][0]['money']['market_value']['amount'],'1750.00')
        self.apply(output)
        self.assertEqual(self.cmd('status','--db',str(self.db))['counts']['snapshots'],1)

    def test_cloud_v2_rejects_invalid_currency_and_stale_context(self):
        import privacy_bridge
        self.upgraded();_,usd,_=self.assets()
        ctx_file=self.folder/'ctx.json';privacy_bridge.export_context(self.db,ctx_file)
        ctx=json.loads(ctx_file.read_text())
        with contextlib.closing(pdp.connect(self.db)) as c:m=pdp.get_meta(c)
        token=privacy_bridge.asset_token(privacy_bridge.load_key(self.db),m['db_id'],usd)
        draft={'format':'LocalDeltaDraft/2','context_token':ctx['context_token'],'operations':[
            {'action':'create','table':'snapshots','asset_token':token,
             'record':{'screenshot_date':'2026-11-09'},
             'money':{'market_value':{'amount':'100.00','currency':'ZZZ'}},
             'source':dict(SOURCE)}]}
        f=self.folder/'bad.json';f.write_text(json.dumps(draft))
        with self.assertRaises(pdp.PDPError):privacy_bridge.bind_draft(self.db,f,self.folder/'badbound.json')
        draft['operations'][0]['money']['market_value']['currency']='USD'
        f.write_text(json.dumps(draft))
        first=self.folder/'goodbound.json'
        privacy_bridge.bind_draft(self.db,f,first)
        self.apply(first)
        with self.assertRaises(pdp.PDPError):privacy_bridge.bind_draft(self.db,f,self.folder/'stale.json')

    def test_same_currency_cash_arithmetic_mismatch_rolls_back(self):
        self.upgraded();_,usd,_=self.assets()
        ident=str(uuid.uuid4())
        o=op('transactions',{'transaction_id':ident,'asset_id':usd,'account':'SYNTHETIC',
                             'currency':'USD','kind':'buy',
                             'order_datetime':'2026-11-09T12:00:00+08:00'},
             {'cash_paid':{'amount':'100.00','currency':'USD'},
              'net_invested':{'amount':'94.00','currency':'USD'},
              'fee':{'amount':'5.00','currency':'USD'}})
        prior=self.meta()['revision']
        file=self.package([o])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(localdelta.main(['apply','--db',str(self.db),'--package',str(file)]),2)
        self.assertEqual(self.meta()['revision'],prior)
        with contextlib.closing(pdp.connect(self.db)) as c:
            self.assertFalse(c.execute('SELECT 1 FROM transactions WHERE transaction_id=?',(ident,)).fetchone())

    def test_tampering_typed_amount_detected(self):
        self.upgraded();_,usd,_=self.assets()
        o=op('snapshots',{'snapshot_id':str(uuid.uuid4()),'asset_id':usd,'account':'SYNTHETIC',
                          'currency':'USD','screenshot_date':'2026-11-09'},
             {'market_value':{'amount':'20.00','currency':'USD'}})
        self.apply(self.package([o]))
        with contextlib.closing(sqlite3.connect(self.db)) as c:
            c.execute("UPDATE ld_currency_amounts SET amount='9999.00'")
            c.commit()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(localdelta.main(['status','--db',str(self.db)]),2)
        with self.assertRaises(pdp.PDPError):mc.report(self.db,'CNY','2026-11-09')

    def test_upgrade_marks_floating_point_provenance(self):
        # Direct rows before PDP initialization represent imported legacy history.
        from pathlib import Path
        db2=self.folder/'old.sqlite3'
        c=sqlite3.connect(db2)
        try:
            c.executescript((Path(__file__).resolve().parents[1]/'legacy_schema.sql').read_text())
            aid=str(uuid.uuid4());sid=str(uuid.uuid4())
            c.execute('INSERT INTO assets (asset_id,asset_name,account,currency) VALUES (?,?,?,?)',
                      (aid,'OLD ASSET','TEST','CNY'))
            c.execute('INSERT INTO snapshots(snapshot_id,asset_id,account,currency,screenshot_date,market_value_cny) VALUES (?,?,?,?,?,?)',
                      (sid,aid,'TEST','CNY','2026-11-08',123.45))
            c.commit()
        finally:c.close()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pdp.main(['init','--db',str(db2)]),0)
        result=mc.upgrade(db2)
        self.assertEqual(result['backfilled'],1)
        with contextlib.closing(pdp.connect(db2)) as c:
            row=c.execute('SELECT amount,currency,origin FROM ld_currency_amounts').fetchone()
            self.assertEqual(tuple(row),('123.45','CNY','legacy_approx'))

    def test_report_yen_rounding_uses_minor_unit(self):
        self.upgraded();cny,_,_=self.assets()
        o=op('snapshots',{'snapshot_id':str(uuid.uuid4()),'asset_id':cny,'account':'SYNTHETIC','currency':'CNY',
                          'screenshot_date':'2026-11-09'},
             {'market_value':{'amount':'10.00','currency':'CNY'}})
        fx=op('fx_rates',{'rate_id':str(uuid.uuid4()),'base_currency':'CNY','quote_currency':'JPY',
                           'rate':'20.55','rate_date':'2026-11-09','rate_kind':'reference','source_ref':'SYN RATE'})
        self.apply(self.package([o,fx]))
        r=mc.report(self.db,'JPY','2026-11-09')
        self.assertEqual(r['converted_known_total'],'206')

    def test_fx_draft_and_cli_roundtrip(self):
        self.upgraded()
        d=self.cmd('fx-draft','--db',str(self.db),'--base','USD','--quote','CNY',
                   '--rate','7.1234','--date','2026-11-09','--source','SYNTHETIC EXAMPLE')
        f=Path(d['draft_package'])
        self.assertTrue(f.is_file())
        self.apply(f)
        with contextlib.closing(pdp.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT rate FROM ld_fx_rates').fetchone()[0],'7.1234')


if __name__ == '__main__':unittest.main()
