"""LocalDelta multicurrency extension (PDP/2.0) for the three-table portfolio.

Design references: Beancount units/cost/price; Portfolio Performance quote vs
settlement currencies; Martin Fowler Money value object; ISO 4217 minor units.
No remote FX service, binary floats, or implicit cross-currency conversions.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import hashlib
from pathlib import Path
import datetime as dt
import json
import re
import uuid

import pdp

PROTOCOL = 'PDP/2.0'
SCHEMA_TABLES = ('ld_currency_amounts','ld_fx_rates','ld_mc_migrations')
# The supported ISO 4217 subset is explicit. Never assume every currency has 2 decimals.
MINOR_UNITS = {
    'CNY':2, 'USD':2, 'HKD':2, 'EUR':2, 'GBP':2, 'CAD':2,
    'AUD':2, 'SGD':2, 'CHF':2, 'TWD':2, 'JPY':0, 'KRW':0,
    'INR':2, 'THB':2, 'MYR':2, 'AED':2, 'SAR':2, 'KWD':3,
    'BHD':3, 'OMR':3, 'VND':0, 'IDR':2, 'NZD':2,
}
# Intent-specific roles. Quote currency of the asset can differ from cash settlement.
ROLES = {
    'transactions': {
        'cash_paid':'total_paid_cny', 'net_invested':'net_invested_cny',
        'fee':'fee_cny', 'cash_inflow':'cash_inflow_cny',
        'clean_amount':'clean_amount_cny', 'accrued_interest':'accrued_interest_total_cny',
        'trade_gross':None, 'tax':None,
    },
    'snapshots': {
        'market_value':'market_value_cny','holding_pnl':'holding_pnl_cny',
        'cumulative_pnl':'cumulative_pnl_cny','yesterday_pnl':'yesterday_pnl_cny',
        'interest_received':'interest_received_cny',
    },
}
NONNEGATIVE_ROLES = {'cash_paid','net_invested','fee','cash_inflow','clean_amount',
                     'accrued_interest','trade_gross','tax','market_value'}
DECIMAL_RE = re.compile(r'^-?(?:0|[1-9]\d*)(?:\.\d+)?$')


def parse_decimal(value, label: str, scale: int = 10, positive: bool = False, nonnegative: bool = False) -> Decimal:
    if not isinstance(value, str) or len(value) > 80 or not DECIMAL_RE.fullmatch(value):
        pdp.fail(f'{label} 必须是 JSON 十进制字符串，不得使用浮点数或科学记数法')
    try:
        d = Decimal(value)
    except InvalidOperation:
        pdp.fail(f'{label} 金额无效')
    if not d.is_finite() or abs(d) > Decimal('999999999999999'):
        pdp.fail(f'{label} 金额超出范围')
    if -d.as_tuple().exponent > scale:
        pdp.fail(f'{label} 最多支持 {scale} 位小数')
    if positive and d <= 0 or nonnegative and d < 0:
        pdp.fail(f'{label} 数值符号无效')
    return d


def currency(code: str) -> str:
    if not isinstance(code, str) or code not in MINOR_UNITS:
        pdp.fail('暂不支持货币代码 '+str(code)+'；需要按 ISO 4217 扩展货币精度字典')
    return code


def money_value(obj: dict, label: str, role: str = '') -> dict:
    if not isinstance(obj,dict) or set(obj) != {'amount','currency'}:
        pdp.fail(f'{label} 需要且仅需要 amount、currency')
    code = currency(obj['currency'])
    amount = parse_decimal(obj['amount'], label, scale=MINOR_UNITS[code],nonnegative=role in NONNEGATIVE_ROLES)
    # Canonical representation: e.g. 2.00 and 2.0 compare equal as Money values.
    return {'amount': format(amount, 'f'), 'currency': code}


def enabled(c) -> bool:
    present = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ld_%'")}
    found = present.intersection(SCHEMA_TABLES)
    if found and found != set(SCHEMA_TABLES):
        pdp.fail('多币种扩展表不完整，禁止继续使用')
    return len(found) == len(SCHEMA_TABLES)


def extension_ddl(c) -> list[str]:
    if not enabled(c):return []
    d=[]
    for table in SCHEMA_TABLES:
        row=c.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?',('table',table)).fetchone()
        if not row or not row[0]:pdp.fail('多币种表结构异常：'+table)
        d.append(row[0])
    return d


def require_enabled(c):
    if not enabled(c):pdp.fail('此数据库尚未启用多币种，请先执行 money-upgrade（自动备份）')


def create_tables(c):
    c.execute('''CREATE TABLE ld_currency_amounts (
        entity_table TEXT NOT NULL CHECK(entity_table IN ('transactions','snapshots')),
        entity_id TEXT NOT NULL,
        role TEXT NOT NULL,
        amount TEXT NOT NULL,
        currency TEXT NOT NULL,
        origin TEXT NOT NULL CHECK(origin IN ('native','legacy_approx','legacy_exact')),
        PRIMARY KEY(entity_table,entity_id,role)
    )''')
    c.execute('''CREATE TABLE ld_fx_rates (
        rate_id TEXT PRIMARY KEY, base_currency TEXT NOT NULL, quote_currency TEXT NOT NULL,
        rate TEXT NOT NULL, rate_date TEXT NOT NULL,
        rate_kind TEXT NOT NULL CHECK(rate_kind IN ('reference','executed')),
        source_ref TEXT NOT NULL
    )''')
    c.execute('''CREATE TABLE ld_mc_migrations (
        version INTEGER PRIMARY KEY, migrated_at TEXT NOT NULL,
        previous_revision INTEGER NOT NULL, previous_schema TEXT NOT NULL,
        previous_data TEXT NOT NULL, backfilled_rows INTEGER NOT NULL
    )''')
    c.execute('CREATE INDEX ld_fx_pair_date ON ld_fx_rates(base_currency,quote_currency,rate_date)')


def check_native_money(table: str, values: dict) -> dict:
    if table not in ROLES:pdp.fail('money 只能用于 transactions 或 snapshots')
    if not isinstance(values,dict) or not values:pdp.fail('money 必须是非空的角色键值对象')
    bad = set(values)-set(ROLES[table])
    if bad:pdp.fail('未知资金角色：'+repr(sorted(bad)))
    return {role:money_value(v, table+'.'+role, role) for role,v in values.items()}


def get_money(c,table,ident) -> dict:
    return {r['role']:{'amount':r['amount'],'currency':r['currency']}
            for r in c.execute('SELECT role,amount,currency FROM ld_currency_amounts WHERE entity_table=? AND entity_id=?',(table,ident))}


def put_money(c,table,ident,values,origin='native'):
    for role,obj in check_native_money(table,values).items():
        c.execute('''INSERT INTO ld_currency_amounts (entity_table,entity_id,role,amount,currency,origin)
                     VALUES (?,?,?,?,?,?) ON CONFLICT(entity_table,entity_id,role) DO UPDATE SET
                     amount=excluded.amount, currency=excluded.currency, origin=excluded.origin''',
                  (table,ident,role,obj['amount'],obj['currency'],origin))


def validate_amount_consistency(c, table, ident):
    if table!='transactions':return
    values=get_money(c,table,ident)
    if all(k in values for k in ('cash_paid','net_invested','fee')):
        a,b,fee=(values[k] for k in ('cash_paid','net_invested','fee'))
        if a['currency']==b['currency']==fee['currency']:
            precision=Decimal('1').scaleb(-MINOR_UNITS[a['currency']])
            diff=abs(Decimal(a['amount'])-Decimal(b['amount'])-Decimal(fee['amount']))
            if diff>precision:
                pdp.fail('同币种现金支付 ≠ 净投入 + 手续费，拒绝导入')



def sync_legacy(c,table,ident,input_rec):
    if not enabled(c) or table not in ROLES:return
    row=pdp.get_row(c,table,ident)
    if row['currency'] != 'CNY':return
    exact={r['column_name']:r['decimal_value'] for r in c.execute(
        'SELECT column_name,decimal_value FROM pdp_exact_values WHERE table_name=? AND entity_id=?',(table,ident))}
    for role,col in ROLES[table].items():
        if not col or col not in input_rec:continue
        value=input_rec[col]
        if value is None:
            c.execute('DELETE FROM ld_currency_amounts WHERE entity_table=? AND entity_id=? AND role=?',(table,ident,role))
            continue
        val = exact.get(col) or format(Decimal(str(value)),'f')
        mv=money_value({'amount':val,'currency':'CNY'},f'legacy.{col}',role)
        c.execute('''INSERT INTO ld_currency_amounts VALUES (?,?,?,?,?,?)
                     ON CONFLICT(entity_table,entity_id,role) DO UPDATE SET
                     amount=excluded.amount,currency=excluded.currency,origin=excluded.origin''',
                  (table,ident,role,mv['amount'],'CNY','legacy_exact' if col in exact else 'legacy_approx'))


def upgrade(db):
    """One-way additive migration; original tables untouched; always makes a full backup."""
    c=pdp.connect(db)
    try:
        m=pdp.get_meta(c)
        if enabled(c):
            if pdp.schema_fingerprint(c)!=m['schema_digest'] or pdp.data_fingerprint(c)!=m['data_digest']:
                pdp.fail('数据库多币种模式已存在，但指纹不匹配')
            return {'status':'already_upgraded','revision':m['revision'],'backfilled':None}
        if pdp.schema_fingerprint(c)!=m['schema_digest'] or pdp.data_fingerprint(c)!=m['data_digest']:
            pdp.fail('迁移前数据库有未审计变更，拒绝升级')
        pdp.check_db(c)
        for table in ROLES:
            monetary=[col for col in ROLES[table].values() if col]
            rows=c.execute('SELECT '+','.join('"'+col+'"' for col in monetary)+',currency FROM '+table+' WHERE currency != ?',( 'CNY',)).fetchall()
            if any(any(r[col] is not None for col in monetary) for r in rows):
                pdp.fail('历史非 CNY 记录中存在 _cny 数值：需要先人工核实来源，不能猜测币种')
    finally:c.close()
    backup=pdp.fresh_backup(db,'before_multicurrency_upgrade')
    c=pdp.connect(db)
    try:
        c.execute('BEGIN IMMEDIATE')
        m=pdp.get_meta(c)
        if enabled(c):pdp.fail('其他进程已升级，请重新检查状态')
        if pdp.schema_fingerprint(c)!=m['schema_digest'] or pdp.data_fingerprint(c)!=m['data_digest']:
            pdp.fail('备份后数据库有变化，拒绝升级')
        create_tables(c)
        count=0
        for table,roles in ROLES.items():
            pk=pdp.PK[table]
            columns=[col for col in roles.values() if col]
            for row in c.execute('SELECT * FROM '+table):
                if row['currency']!='CNY':continue
                exact={r['column_name']:r['decimal_value'] for r in c.execute(
                    'SELECT column_name,decimal_value FROM pdp_exact_values WHERE table_name=? AND entity_id=?',(table,row[pk]))}
                for role,col in roles.items():
                    if not col or row[col] is None:continue
                    val=exact.get(col) or format(Decimal(str(row[col])).quantize(Decimal('0.01')), 'f')
                    mv=money_value({'amount':val,'currency':'CNY'},'backfill.'+col,role)
                    c.execute('INSERT INTO ld_currency_amounts VALUES (?,?,?,?,?,?)',
                              (table,row[pk],role,mv['amount'],'CNY','legacy_exact' if col in exact else 'legacy_approx'))
                    count+=1
        c.execute('INSERT INTO ld_mc_migrations VALUES (?,?,?,?,?,?)',(2,pdp.now(),m['revision'],m['schema_digest'],m['data_digest'],count))
        p_id=str(uuid.uuid4())
        c.execute('INSERT INTO pdp_packages VALUES (?,?,?,?,?,?)',
                  (p_id,hashlib.sha256(('LocalDelta:migration:v2:'+m['db_id']).encode('utf-8')).hexdigest(),m['revision'],m['revision']+1,pdp.now(),'Controlled additive multicurrency migration'))
        c.execute('''INSERT INTO pdp_operations(op_id,package_id,action,table_name,entity_id,old_json,new_json,reason,source_json,applied_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?)''',
                  (str(uuid.uuid4()),p_id,'migrate','ld_mc_migrations','2',None,
                   json.dumps({'backfilled':count},ensure_ascii=False),'enable typed multicurrency values',
                   json.dumps({'kind':'system','reference':'internal safe migration','certainty':'confirmed'}),pdp.now()))
        d=pdp.data_fingerprint(c);s=pdp.schema_fingerprint(c)
        c.execute('UPDATE pdp_meta SET revision=?,schema_digest=?,data_digest=? WHERE singleton=1',(m['revision']+1,s,d))
        pdp.check_db(c)
        c.execute('COMMIT')
        return {'status':'upgraded','revision':m['revision']+1,'backfilled':count,'backup_path':backup}
    except Exception:
        if c.in_transaction:c.execute('ROLLBACK')
        raise
    finally:c.close()


def validate_fx(record):
    if not isinstance(record,dict) or set(record)!={'rate_id','base_currency','quote_currency','rate','rate_date','rate_kind','source_ref'}:
        pdp.fail('fx_rates 记录必须有 rate_id, base_currency, quote_currency, rate, rate_date, rate_kind, source_ref')
    pdp.check_uuid(record['rate_id'],'rate_id')
    a=currency(record['base_currency']);b=currency(record['quote_currency'])
    if a==b:pdp.fail('汇率货币对不能相同')
    parse_decimal(record['rate'],'fx.rate',12,positive=True)
    if not isinstance(record['rate_date'],str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',record['rate_date']):
        pdp.fail('汇率日期必须严格使用 YYYY-MM-DD')
    pdp.validate_date(record['rate_date'],'rate_date')
    if record['rate_kind'] not in ('reference','executed'):
        pdp.fail('rate_kind 只能为 reference 或 executed')
    pdp.check_str(record['source_ref'],'source_ref',True,512)
    return record


def validate_op(op):
    table=op['table'];act=op['action']
    if table=='fx_rates':
        if act!='create' or any(k in op for k in ('money','money_before','money_changes')):
            pdp.fail('fx_rates 仅支持 create，不允许直接修改历史汇率')
        validate_fx(op.get('record'))
        return
    if table=='assets' and any(k in op for k in ('money','money_before','money_changes')):
        pdp.fail('资产档案本身不能附带资金记录')
    if table in ROLES:
        if act=='create':
            if any(k in op for k in ('money_before','money_changes')):
                pdp.fail('create 不允许 money_before/money_changes')
            if 'money' in op:check_native_money(table,op['money'])
        else:
            if 'money' in op:pdp.fail('correct/void 不允许 money')
            if act=='correct':
                b=op.get('money_before');n=op.get('money_changes')
                if b is not None or n is not None:
                    if not isinstance(b,dict) or not isinstance(n,dict) or not n or set(b)!=set(n):
                        pdp.fail('money_before 和 money_changes 必须是非空且同键的对象')
                    check_native_money(table,b)
                    check_native_money(table,n)
            elif any(k in op for k in ('money_before','money_changes')):
                pdp.fail('void 不允许 money_before/money_changes')


def prepare_create(op):
    """Populate v1 compatibility columns only if native asset currency is CNY."""
    if op['table'] not in ROLES or 'money' not in op:return op
    table=op['table']
    copy={**op,'record':dict(op['record'])}
    record=copy['record']
    if record.get('currency')=='CNY':
        for role,mv in check_native_money(table,op['money']).items():
            col=ROLES[table][role]
            if col and mv['currency']=='CNY':
                if col in record and record[col] is not None and Decimal(record[col])!=Decimal(mv['amount']):
                    pdp.fail('同一记录的 legacy CNY 金额与 money 冲突：'+col)
                record[col]=mv['amount']
    return copy


def apply_fx(c,op,package_id):
    record=validate_fx(op['record'])
    pk=record['rate_id']
    if c.execute('SELECT 1 FROM ld_fx_rates WHERE rate_id=?',(pk,)).fetchone():
        pdp.fail('汇率记录 ID 已存在')
    if c.execute('SELECT 1 FROM pdp_source_keys WHERE source_key=?',(op['source_key'],)).fetchone():
        pdp.fail('汇率来源 source_key 已存在')
    if c.execute('''SELECT 1 FROM ld_fx_rates WHERE base_currency=? AND quote_currency=? AND
                    rate_date=? AND rate_kind=? AND source_ref=?''',
                 (record['base_currency'],record['quote_currency'],record['rate_date'],record['rate_kind'],record['source_ref'])).fetchone():
        pdp.fail('同一日期/来源/货币对的汇率已存在，不能覆盖')
    cols=list(record)
    c.execute('INSERT INTO ld_fx_rates('+','.join('"'+x+'"' for x in cols)+') VALUES ('+','.join('?' for _ in cols)+')',[record[k] for k in cols])
    c.execute('INSERT INTO pdp_source_keys VALUES (?,?,?,?)',(op['source_key'],'fx_rates',pk,package_id))
    c.execute('''INSERT INTO pdp_operations(op_id,package_id,action,table_name,entity_id,old_json,new_json,reason,source_json,applied_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?)''',
              (op['op_id'],package_id,'create','fx_rates',pk,None,json.dumps(record,ensure_ascii=False,sort_keys=True),
               None,json.dumps(op['source'],ensure_ascii=False,sort_keys=True),pdp.now()))
    return {'action':'create','table':'fx_rates','id':pk,'title':record['base_currency']+'/'+record['quote_currency']+' '+record['rate']}


def after_core_op(c,original_op,result):
    if not enabled(c):return
    table=original_op['table'];action=original_op['action']
    if table not in ROLES:return
    ident=result['id']
    if action=='void':return
    if action=='create':
        if 'money' in original_op:
            put_money(c,table,ident,original_op['money'])
        else:
            sync_legacy(c,table,ident,original_op['record'])
    elif action=='correct':
        # The legacy fields and typed money roles are reconciled within the same transaction.
        sync_legacy(c,table,ident,original_op.get('changes',{}))
        if 'money_changes' in original_op:
            old=get_money(c,table,ident)
            for role,before in original_op['money_before'].items():
                if role not in old or (Decimal(old[role]['amount']) != Decimal(before['amount']) or old[role]['currency']!=before['currency']):
                    pdp.fail('资金字段原值不匹配：'+role)
            put_money(c,table,ident,original_op['money_changes'])
            # CNY-only mirror to legacy fields, foreign fields are never incorrectly mirrored.
            row=pdp.get_row(c,table,ident)
            if row['currency']=='CNY':
                for role,value in check_native_money(table,original_op['money_changes']).items():
                    col=ROLES[table][role]
                    if col:
                        if value['currency']=='CNY':
                            c.execute('UPDATE "'+table+'" SET "'+col+'"=? WHERE "'+pdp.PK[table]+'"=?',
                                      (float(Decimal(value['amount'])),ident))
                            pdp.record_exact(c,table,ident,{col:value['amount']})
                        else:
                            c.execute('UPDATE "'+table+'" SET "'+col+'"=NULL WHERE "'+pdp.PK[table]+'"=?',(ident,))
                            pdp.record_exact(c,table,ident,{col:None})
    # Recheck after all money/legacy mirrors are applied, in the same transaction.
    validate_amount_consistency(c,table,ident)
    pdp.validate_business_rules(table,pdp.get_row(c,table,ident))
    audit=c.execute('SELECT 1 FROM pdp_operations WHERE op_id=?',(original_op['op_id'],)).fetchone()
    if audit:
        j=pdp.get_row(c,table,ident)
        j['_money'] = get_money(c,table,ident)
        c.execute('UPDATE pdp_operations SET new_json=? WHERE op_id=?',(json.dumps(j,ensure_ascii=False,sort_keys=True),original_op['op_id']))


def _find_rate(c,base,quote,as_of,age):
    if base==quote:return Decimal('1'),{'source':'identity','rate_date':as_of,'rate':'1'}
    for b,q,reverse in ((base,quote,False),(quote,base,True)):
        row=c.execute('''SELECT * FROM ld_fx_rates WHERE base_currency=? AND quote_currency=?
                         AND rate_kind='reference' AND rate_date <= ? ORDER BY rate_date DESC,rate_id DESC LIMIT 1''',(b,q,as_of)).fetchone()
        if row and (dt.date.fromisoformat(as_of)-dt.date.fromisoformat(row['rate_date'])).days<=age:
            v=Decimal(row['rate'])
            return ((Decimal('1')/v) if reverse else v),dict(row)
    return None,None


def report(db,base='CNY',as_of=None,max_age=0):
    base=currency(base)
    if as_of is None:as_of=dt.date.today().isoformat()
    if not isinstance(as_of,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',as_of):
        pdp.fail('报表日期必须使用 YYYY-MM-DD')
    pdp.validate_date(as_of,'rate_date')
    if not isinstance(max_age,int) or max_age<0 or max_age>366:
        pdp.fail('max-age 必须是 0–366 天的非负整数')
    c=pdp.connect(db)
    try:
        require_enabled(c)
        meta=pdp.get_meta(c)
        if pdp.schema_fingerprint(c)!=meta['schema_digest'] or pdp.data_fingerprint(c)!=meta['data_digest']:
            pdp.fail('数据库结构或数据指纹不匹配')
        assets=c.execute('SELECT asset_id,asset_name,currency,account FROM assets').fetchall()
        minor_quantum=Decimal('1').scaleb(-MINOR_UNITS[base])
        holdings=[];totals={}; converted=Decimal('0');missing=[];unvalued=[]
        for asset in assets:
            snap=c.execute('''SELECT s.* FROM pdp_active_snapshots s WHERE s.asset_id=?
                              AND s.screenshot_date <= ? ORDER BY s.screenshot_date DESC,s.snapshot_id DESC LIMIT 1''',
                           (asset['asset_id'],as_of)).fetchone()
            if not snap:
                unvalued.append({'asset_id':asset['asset_id'],'currency':asset['currency'],'reason':'no_snapshot_as_of_date'})
                continue
            money=get_money(c,'snapshots',snap['snapshot_id']).get('market_value')
            if money is None:
                unvalued.append({'asset_id':asset['asset_id'],'currency':asset['currency'],'reason':'snapshot_has_no_market_value'})
                continue
            curr=money['currency'];amount=Decimal(money['amount'])
            totals[curr]=totals.get(curr,Decimal('0'))+amount
            rate,source=_find_rate(c,curr,base,as_of,max_age)
            result={'asset_id':asset['asset_id'],'asset_name':asset['asset_name'],'account':asset['account'],
                    'snapshot_date':snap['screenshot_date'],'native':money,'rate':None,'converted':None}
            if rate is None:
                missing.append({'asset_id':asset['asset_id'],'currency':curr,'needed_pair':f'{curr}/{base}'})
            else:
                cv=amount*rate;converted+=cv
                result['rate']={'value':format(rate,'.12f'),'source':source}
                result['converted']=format(cv.quantize(minor_quantum,rounding=ROUND_HALF_EVEN),'f')
            holdings.append(result)
        return {'as_of':as_of,'base_currency':base,'max_fx_age_days':max_age,
                'native_totals':{k:format(v,'f') for k,v in sorted(totals.items())},
                'converted_known_total':format(converted.quantize(minor_quantum,rounding=ROUND_HALF_EVEN),'f'),
                'complete':not missing and not unvalued,'missing_rates':missing,'unvalued_assets':unvalued,'holdings':holdings,
                'note':'Totals are PARTIAL when FX rates or market-value snapshots are missing. Snapshot dates may precede report as-of; FX uses report as-of date.'}
    finally:c.close()


def draft_fx_package(db,base,quote,rate,rate_date,source_ref,out=None):
    """Prepare an *unapplied* native FX update. GUI/CLI must still plan and approve."""
    base=currency(base);quote=currency(quote)
    if base==quote:pdp.fail('不能为相同货币创建汇率')
    parse_decimal(rate,'fx.rate',12,positive=True)
    if not isinstance(rate_date,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',rate_date):
        pdp.fail('汇率日期必须使用 YYYY-MM-DD')
    pdp.validate_date(rate_date,'rate_date')
    pdp.check_str(source_ref,'source_ref',True,512)
    c=pdp.connect(db)
    try:
        require_enabled(c)
        m=pdp.get_meta(c)
        if pdp.schema_fingerprint(c)!=m['schema_digest'] or pdp.data_fingerprint(c)!=m['data_digest']:
            pdp.fail('当前数据库有未经审计的变更，请先修复')
    finally:c.close()
    sk='fx:'+hashlib.sha256(pdp.canonical([base,quote,rate,rate_date,source_ref])).hexdigest()
    pkg={
        'protocol':PROTOCOL,'package_id':str(uuid.uuid4()),'database_id':m['db_id'],
        'base_revision':m['revision'],'schema_fingerprint':m['schema_digest'],
        'base_data_fingerprint':m['data_digest'],'created_at':pdp.now(),
        'description':f'FX {base}/{quote} {rate} at {rate_date} (reference, not execution)',
        'evidence':[],
        'operations':[{ 'op_id':str(uuid.uuid4()),'action':'create','table':'fx_rates','source_key':sk,
                        'source':{'kind':'manual','reference':source_ref,'certainty':'confirmed'},
                        'record':{'rate_id':str(uuid.uuid4()),'base_currency':base,
                                  'quote_currency':quote,'rate':rate,'rate_date':rate_date,
                                  'rate_kind':'reference','source_ref':source_ref}}]
    }
    pkg['content_hash']=pdp.package_digest(pkg)
    pdp.validate_pkg(pkg,{})
    if out is None:
        target=Path(db).resolve().parent/'.localdelta'/'outbox'
        target.mkdir(parents=True,exist_ok=True)
        out=target/('fx-'+uuid.uuid4().hex[:12]+'.pdp.json')
    out=Path(out)
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x',encoding='utf-8') as f:
        json.dump(pkg,f,ensure_ascii=False,indent=2)
        f.write('\n')
    return str(out)
