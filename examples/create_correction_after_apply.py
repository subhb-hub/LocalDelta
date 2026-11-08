#!/usr/bin/env python3
"""Run ONLY after applying the first demo update. Creates a CAS correction package."""
from pathlib import Path
import sys
import uuid
import json
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import pdp

p=Path(__file__).resolve().parent
c=pdp.connect(p/'demo.sqlite3')
try:
    meta=pdp.get_meta(c)
    if meta['revision']!=1:
        pdp.fail('Please apply example_update first (demo revision must be 1)')
    row=c.execute("SELECT snapshot_id,holding_pnl_cny FROM snapshots LIMIT 1").fetchone()
    if not row:pdp.fail('Demo snapshot missing')
    sid=row[0]
    current=round(row[1],2)
finally:c.close()
pack={
    'protocol':pdp.PROTOCOL,'package_id':str(uuid.uuid4()),
    'database_id':meta['db_id'],'schema_fingerprint':meta['schema_digest'],
    'base_data_fingerprint':meta['data_digest'], 'base_revision':meta['revision'],
    'created_at':pdp.now(), 'description':'Correction example: change synthetic holding P&L by 1.00 CNY',
    'evidence':[], 'operations':[
        {'op_id':str(uuid.uuid4()),'action':'correct','table':'snapshots','id':sid,
         'before':{'holding_pnl_cny':format(current,'.2f')},
         'changes':{'holding_pnl_cny':format(current+1,'.2f')},
         'reason':'synthetic demonstration correction',
         'source':{'kind':'manual','reference':'test correction event','certainty':'confirmed'}}
    ]
}
pack['content_hash']=pdp.package_digest(pack)
file=p/'example_correction.pdp.json'
file.write_text(json.dumps(pack,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print('Generated',file)
