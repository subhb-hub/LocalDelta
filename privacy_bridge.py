"""LocalDelta opt-in cloud bridge: opaque asset tokens and guarded offline binding.

No remote requests. Cloud AI sees only the context file the user chooses to share.
This bridge currently supports *creating* transactions and valuation snapshots for
existing assets in the Portfolio Delta Protocol adapter.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import uuid

import pdp

CONTEXT_FORMAT = 'LocalDeltaCloudContext/1'
DRAFT_FORMAT = 'LocalDeltaDraft/1'
DRAFT_FORMAT_V2 = 'LocalDeltaDraft/2'
TOKEN_PREFIX = 'AST-'


def privacy_key_path(db):
    db = Path(db).resolve()
    return db.parent / '.localdelta' / ('privacy-' + db.name + '.key')


def load_key(db):
    path = privacy_key_path(db)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'wb') as fp:
                fp.write(secrets.token_bytes(32))
    try:
        if os.name != 'nt':
            os.chmod(path, 0o600)
        raw = path.read_bytes()
    except OSError as e:
        pdp.fail('本地隐私密钥无法读取: ' + str(e))
    if len(raw) != 32:
        pdp.fail('本地隐私密钥长度错误，拒绝生成不稳定的身份代号')
    return raw


def mac(key, message):
    return hmac.new(key, message.encode('utf-8'), hashlib.sha256).hexdigest()


def asset_token(key, database_id, asset_id):
    return TOKEN_PREFIX + mac(key, f'asset|{database_id}|{asset_id}')[:32]


def check_source_db(conn):
    meta = pdp.get_meta(conn)
    if pdp.schema_fingerprint(conn) != meta['schema_digest']:
        pdp.fail('数据库结构与 PDP 初始化时不一致')
    if pdp.data_fingerprint(conn) != meta['data_digest']:
        pdp.fail('存在未记录在审计中的数据库变更')
    return meta


def context_nonce(key, meta):
    return mac(key, f"context|{meta['db_id']}|{meta['revision']}|{meta['data_digest']}")


def get_cloud_context(conn, key, meta, detail='none'):
    if detail not in ('none', 'category'):
        pdp.fail('detail 必须是 none 或 category')
    rows = conn.execute('SELECT asset_id,asset_type FROM assets ORDER BY asset_id').fetchall()
    assets = []
    for r in rows:
        a = {'token': asset_token(key, meta['db_id'], r['asset_id'])}
        if detail == 'category':
            a['category'] = r['asset_type']
        assets.append(a)
    from multicurrency import enabled
    return {
        'format': CONTEXT_FORMAT,
        'context_token': context_nonce(key, meta),
        'supports_native_money': enabled(conn),
        'assets': assets,
        'supported_changes': ['create transaction', 'create snapshot'],
        'privacy_notice': 'No asset names, account identifiers, positions, balances, or transaction refs. Context tokens are opaque. Any facts you send separately to a cloud AI can still be disclosed.',
    }


def export_context(db, out, detail='none'):
    db = Path(db).resolve()
    c = pdp.connect(db)
    try:
        meta = check_source_db(c)
        key = load_key(db)
        ctx = get_cloud_context(c,key,meta,detail)
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ctx,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
        return {'created':str(path),'opaque_assets':len(ctx['assets']),'detail':detail}
    finally:
        c.close()


def export_local_aliases(db, out):
    """Create a SENSITIVE token/name lookup for local eyes only. Never share this file."""
    db=Path(db).resolve()
    c=pdp.connect(db)
    try:
        meta=check_source_db(c)
        key=load_key(db)
        rows=c.execute('SELECT asset_id,asset_name,account,currency FROM assets ORDER BY account,asset_name').fetchall()
        d={"format":"LocalDeltaPrivateAliases/1",
           "WARNING":"PRIVATE FILE. Contains actual asset names and accounts. DO NOT UPLOAD.",
           "assets":[{"token":asset_token(key,meta['db_id'],r['asset_id']),
                       "asset_name":r['asset_name'],"account":r['account'],"currency":r['currency']}
                      for r in rows]}
        dest=Path(out)
        dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists():pdp.fail('不覆盖已有的本地别名文件：'+str(dest))
        dest.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        if os.name!='nt':os.chmod(dest,0o600)
        return {"created_PRIVATE_alias_map":str(dest),"assets":len(rows),
                "warning":"Contains private names/accounts: NEVER upload this file to a cloud AI."}
    finally:c.close()


def _exact_dict(obj, expected, where):
    if not isinstance(obj,dict) or set(obj) != set(expected):
        pdp.fail(f'{where} 只能包含字段: {sorted(expected)}')


def bind_draft(db, draft_file, out):
    """Resolve opaque aliases only on the local machine. Draft contains no SQL.

    The resulting PDP patch may contain financial data and MUST remain private.
    """
    draft_path = Path(draft_file)
    draft = pdp.json_parse(draft_path.read_bytes())
    if not isinstance(draft,dict) or set(draft) != {'format','context_token','operations'}:
        pdp.fail('Cloud Draft 必须仅包含 format、context_token、operations')
    if draft['format'] not in (DRAFT_FORMAT,DRAFT_FORMAT_V2):
        pdp.fail('不支持的 Cloud Draft 格式')
    v2=draft['format']==DRAFT_FORMAT_V2
    from multicurrency import PROTOCOL as PDP_V2, require_enabled, check_native_money
    ops = draft['operations']
    if not isinstance(ops,list) or not 1<=len(ops)<=pdp.MAX_OPS:
        pdp.fail('Cloud Draft operations 数量无效')
    db = Path(db).resolve()
    c = pdp.connect(db)
    try:
        meta = check_source_db(c)
        if v2:require_enabled(c)
        key = load_key(db)
        if not hmac.compare_digest(str(draft['context_token']),context_nonce(key, meta)):
            pdp.fail('隐私上下文已过期、不匹配或来自其他数据库；请重新导出 cloud-context')
        rows=c.execute('SELECT asset_id,asset_name,account,currency FROM assets').fetchall()
        lookup={asset_token(key,meta['db_id'],r['asset_id']):dict(r) for r in rows}
        if len(lookup)!=len(rows):
            pdp.fail('资产匿名代号意外碰撞')
        out_ops=[]
        seen_sources=set()
        for index,d in enumerate(ops):
            fields={'action','table','asset_token','record','source'}
            if v2:fields.add('money')
            if not isinstance(d,dict) or set(d)!=fields:
                pdp.fail(f'第 {index+1} 条 Draft 操作字段不符')
            if v2:check_native_money(d['table'],d['money'])
            if d['action']!='create' or d['table'] not in ('transactions','snapshots'):
                pdp.fail('隐私桥目前只支持给现有资产创建交易或估值快照')
            asset=lookup.get(d['asset_token'])
            if not asset:
                pdp.fail('匿名资产代号不存在；请在本地先创建资产并重新导出云上下文')
            src=d['source']
            _exact_dict(src, ('kind','reference','certainty'),f'第 {index+1} 条 source')
            if src['kind'] not in ('manual','screenshot','statement','csv','other'):
                pdp.fail('无效 source.kind')
            pdp.check_str(src['reference'],'source.reference',True,512)
            if src['certainty']!='confirmed':
                pdp.fail('未核实数据不能绑定为可执行更新包')
            record=d['record']
            if not isinstance(record,dict) or not record:
                pdp.fail('Cloud Draft record 必须为非空对象')
            protected={pdp.PK[d['table']],'asset_id','asset_name','account','currency','notion_url'}
            if protected & set(record):
                pdp.fail('Cloud Draft 不能指定主键、真实资产身份、账户或币种')
            pdp.validate_columns(c,d['table'],record)
            data={'asset_id':asset['asset_id'], 'account':asset['account'], 'currency':asset['currency'], **record}
            pdp.validate_business_rules(d['table'], {k:pdp.field_value(v,k,d['table']) for k,v in data.items()})
            identity={'table':d['table'],'asset_id':asset['asset_id'],'record':record,'source':src}
            if v2:identity['money']=d['money']
            source_key='localdelta:'+hashlib.sha256(pdp.canonical(identity)).hexdigest()
            if source_key in seen_sources:
                pdp.fail('Cloud Draft 内存在重复来源/记录')
            seen_sources.add(source_key)
            record_id=str(uuid.uuid5(uuid.UUID(meta['db_id']),source_key))
            data[pdp.PK[d['table']]]=record_id
            out_op={
                'op_id':str(uuid.uuid5(uuid.UUID(meta['db_id']),'op:'+source_key)),
                'action':'create','table':d['table'],'source_key':source_key,
                'source':src,'record':data
            }
            if v2:out_op['money']=d['money']
            out_ops.append(out_op)
        pkg={
            'protocol':PDP_V2 if v2 else pdp.PROTOCOL,
            'package_id':str(uuid.uuid4()),
            'database_id':meta['db_id'],
            'base_revision':meta['revision'],
            'schema_fingerprint':meta['schema_digest'],
            'base_data_fingerprint':meta['data_digest'],
            'created_at':dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
            'description':'Bound locally from a privacy-preserving AI draft; review before apply',
            'evidence':[], 'operations':out_ops,
        }
        pkg['content_hash']=pdp.package_digest(pkg)
        pdp.validate_pkg(pkg,{})
        dest=Path(out)
        dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists():
            pdp.fail('目标更新包已经存在，禁止覆盖: '+str(dest))
        dest.write_text(json.dumps(pkg,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        return {'bound':str(dest),'operations':len(out_ops),'warning':'Review the full package locally, then run plan before apply.'}
    finally:
        c.close()
