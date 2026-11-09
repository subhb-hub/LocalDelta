#!/usr/bin/env python3
"""Fully synthetic multicurrency end-to-end test; does not use real FX or portfolios."""
from __future__ import annotations
from pathlib import Path
import contextlib
import io
import json
import sys
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import localdelta
import multicurrency
import pdp
import workflow


def main():
    source={'kind':'manual','reference':'SYNTHETIC MULTICURRENCY TEST','certainty':'confirmed'}
    with tempfile.TemporaryDirectory(prefix='localdelta-multicurrency-demo-') as temp:
        db=Path(temp)/'portfolio.sqlite3'
        info=workflow.create_database(db,enable_multicurrency=True)
        with contextlib.closing(pdp.connect(db)) as c:meta=pdp.get_meta(c)
        ids={code:str(uuid.uuid4()) for code in ('CNY','USD','HKD')}
        ops=[]
        def create(table,record,money=None):
            o={'op_id':str(uuid.uuid4()),'action':'create','table':table,
               'source_key':'synthetic:multicurrency:'+str(uuid.uuid4()),
               'source':source,'record':record}
            if money is not None:o['money']=money
            ops.append(o)
        for code in ids:
            create('assets',{'asset_id':ids[code],'asset_name':f'SYNTHETIC {code} SECURITY',
                             'account':'SYNTHETIC BROKER','currency':code,'asset_type':'fund'})
        for code,amount in [('CNY','100.00'),('USD','50.00'),('HKD','80.00')]:
            create('snapshots',{'snapshot_id':str(uuid.uuid4()),'asset_id':ids[code],
                 'account':'SYNTHETIC BROKER','currency':code,'screenshot_date':'2026-11-09'},
                 {'market_value':{'amount':amount,'currency':code}})
        create('transactions',{'transaction_id':str(uuid.uuid4()),'asset_id':ids['HKD'],
            'account':'SYNTHETIC BROKER','currency':'HKD',
            'kind':'buy','order_datetime':'2026-11-09T09:00:00+08:00','quantity':'80'},
            {'trade_gross':{'amount':'800.00','currency':'HKD'},
             'cash_paid':{'amount':'103.00','currency':'USD'}})
        for code,rate in [('USD','7.20'),('HKD','0.90')]:
            create('fx_rates',{'rate_id':str(uuid.uuid4()),'base_currency':code,'quote_currency':'CNY',
                 'rate':rate,'rate_date':'2026-11-09','rate_kind':'reference',
                 'source_ref':'SYNTHETIC RATE - NOT REAL'})
        pack={'protocol':multicurrency.PROTOCOL,'package_id':str(uuid.uuid4()),
             'database_id':meta['db_id'],'base_revision':meta['revision'],
             'base_data_fingerprint':meta['data_digest'],'schema_fingerprint':meta['schema_digest'],
             'created_at':pdp.now(),'description':'Synthetic USD/HKD/CNY example',
             'evidence':[],'operations':ops}
        pack['content_hash']=pdp.package_digest(pack)
        file=Path(temp)/'test.pdp.json';file.write_text(json.dumps(pack),encoding='utf-8')
        def cli(action):
            out=io.StringIO()
            with contextlib.redirect_stdout(out):
                assert localdelta.main([action,'--db',str(db),'--package',str(file)])==0
            return json.loads(out.getvalue())
        assert cli('plan')['status']=='preview'
        assert cli('apply')['status']=='applied'
        assert cli('apply')['status']=='already_applied'
        report=multicurrency.report(db,'CNY','2026-11-09')
        assert report['complete'] and report['converted_known_total']=='532.00',report
        print('PASS: synthetic CNY+USD+HKD native valuations, dated FX, offline atomic import, idempotence')
        print('Report total: 532.00 CNY (SYNTHETIC exchange rates only)')
        print('Demo database disposed; no user portfolio accessed.')


if __name__=='__main__':main()
