#!/usr/bin/env python3
"""Build a synthetic demonstration DB and two signed-by-hash test packages."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import uuid
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import pdp


def main():
    out=Path(__file__).resolve().parent
    db=out/'demo.sqlite3'
    for p in [db]:p.unlink(missing_ok=True)
    c=sqlite3.connect(db)
    try:c.executescript((ROOT/'legacy_schema.sql').read_text(encoding='utf8'))
    finally:c.close()
    code=pdp.main(['init','--db',str(db)])
    assert code==0
    c=pdp.connect(db)
    meta=pdp.get_meta(c);c.close()
    asset_id=str(uuid.uuid4());tran_id=str(uuid.uuid4());snapshot_id=str(uuid.uuid4())
    source={'kind':'manual','reference':'evidence/sample_statement.txt','certainty':'confirmed'}
    pkg={
        'protocol':pdp.PROTOCOL,
        'package_id':str(uuid.uuid4()),
        'database_id':meta['db_id'],
        'base_revision':0,
        'schema_fingerprint':meta['schema_digest'],
        'base_data_fingerprint':meta['data_digest'],
        'created_at':pdp.now(),
        'description':'纯模拟数据：展示资产、交易和快照如何一次原子写入',
        'evidence':[],
        'operations':[
            {'op_id':str(uuid.uuid4()),'action':'create','table':'assets','source_key':'demo:asset:sim-fund-001', 'source':source,
             'record':{'asset_id':asset_id,'asset_name':'示例指数基金（虚构）','account':'演示账户','asset_type':'基金','currency':'CNY','status':'在持'}},
            {'op_id':str(uuid.uuid4()),'action':'create','table':'transactions','source_key':'demo:order-0001','source':source,
             'record':{'transaction_id':tran_id,'asset_id':asset_id,'kind':'申购','account':'演示账户','currency':'CNY',
                       'order_datetime':'2026-11-08T10:30:00+08:00','total_paid_cny':'1000.00','fee_cny':'1.00','net_invested_cny':'999.00',
                       'quantity':'999.0000','unit_nav':'1.0000','transaction_ref':'DEMO-TX-0001','source_status':'截图已确认'}},
            {'op_id':str(uuid.uuid4()),'action':'create','table':'snapshots','source_key':'demo:valuation-2026-11-08','source':source,
             'record':{'snapshot_id':snapshot_id,'asset_id':asset_id,'account':'演示账户','currency':'CNY',
                       'screenshot_date':'2026-11-08','market_value_cny':'1012.34','holding_pnl_cny':'12.34',
                       'needs_valuation_date_check':True,'notes':'演示记录；官方估值日待定'}},
        ]}
    evidence_content='示例：该文本只是模拟证据，不是真实金融流水。\n'.encode('utf8')
    ep=out/'evidence'/'sample_statement.txt';ep.parent.mkdir(exist_ok=True)
    ep.write_bytes(evidence_content)
    pkg['evidence']=[{'path':'evidence/sample_statement.txt','sha256':hashlib.sha256(evidence_content).hexdigest()}]
    pkg['content_hash']=pdp.package_digest(pkg)
    (out/'example_update.pdp.json').write_text(json.dumps(pkg,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    with zipfile.ZipFile(out/'example_update.pdp.zip','w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('package.json',json.dumps(pkg,ensure_ascii=False,indent=2))
        z.writestr('evidence/sample_statement.txt',evidence_content)
    (out/'example_correction.pdp.json').unlink(missing_ok=True)
    print('Created synthetic fixtures (no real investments).')

if __name__=='__main__':main()
