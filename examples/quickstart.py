#!/usr/bin/env python3
"""End-to-end synthetic, isolated demo. Makes no network requests."""
from pathlib import Path
import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import localdelta


def execute(*args):
    out=io.StringIO()
    with contextlib.redirect_stdout(out):
        code=localdelta.main(list(args))
    if code:
        raise SystemExit('Command failed: '+' '.join(args))
    return out.getvalue()


def main():
    with tempfile.TemporaryDirectory(prefix='localdelta-demo-') as tmp:
        dir=Path(tmp)
        db=dir/'synthetic.sqlite3'
        c=sqlite3.connect(db)
        c.executescript((ROOT/'legacy_schema.sql').read_text(encoding='utf-8'))
        c.execute('INSERT INTO assets (asset_id,asset_name,account,currency,asset_type,status) VALUES(?,?,?,?,?,?)',
                  (str(uuid.uuid4()),'SYNTHETIC SAMPLE ETF','DEMO BROKER','CNY','fund','active'))
        c.commit();c.close()
        execute('init','--db',str(db))
        ctx=dir/'cloud-context.json'
        execute('cloud-context','--db',str(db),'--out',str(ctx))
        token=json.loads(ctx.read_text())
        print('Cloud-safe context contains:',list(token.keys()))
        print('Names and balances have NOT been sent to any service.')
        draft={
            'format':'LocalDeltaDraft/1',
            'context_token':token['context_token'],
            'operations':[{ 'action':'create', 'table':'snapshots',
                            'asset_token':token['assets'][0]['token'],
                            'record':{'screenshot_date':'2026-11-08','market_value_cny':'123.45',
                                      'needs_valuation_date_check':True},
                            'source':{'kind':'manual','reference':'synthetic-example-0001','certainty':'confirmed'}}]
        }
        draft_file=dir/'draft.json'
        draft_file.write_text(json.dumps(draft,indent=2),encoding='utf-8')
        package=dir/'update.pdp.json'
        execute('bind-draft','--db',str(db),'--draft',str(draft_file),'--out',str(package))
        preview=json.loads(execute('plan','--db',str(db),'--package',str(package)))
        assert preview['status']=='preview'
        applied=json.loads(execute('apply','--db',str(db),'--package',str(package)))
        assert applied['status']=='applied'
        repeated=json.loads(execute('apply','--db',str(db),'--package',str(package)))
        assert repeated['status']=='already_applied'
        status=json.loads(execute('status','--db',str(db)))
        assert status['counts']['snapshots']==1
        print('PASS: preview -> atomic apply -> repeated import is no-op -> record count 1')
        print('Temporary demo database removed automatically.')


if __name__=='__main__':main()
