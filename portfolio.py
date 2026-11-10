"""Audited portfolio structure and user cash classifications, independent of NAV.

This module never reads valuations or infers a present holding from old prices.
Raw asset/transaction/snapshot/observation records remain independently stored.
"""
from __future__ import annotations
from contextlib import closing
from decimal import Decimal
import hashlib
import json

import ledger
import pdp
import statements

TABLE_ORDER = {'ld_portfolio_sources': 'source_id', 'ld_asset_memberships': 'asset_id',
               'ld_portfolio_classifications': 'review_id', 'ld_portfolio_applied': 'source_id',
               'ld_portfolio_migrations': 'version'}
INDEX_NAMES = ('ld_memberships_account', 'ld_memberships_instrument',
               'ld_classifications_observation', 'ld_classifications_link')
CLASSES = {'internal_transfer', 'external_contribution', 'non_investment'}
MAPPING_STATUSES = {'documented', 'legacy_label', 'unresolved'}


def enabled(c):
    found = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")} & set(TABLE_ORDER)
    if found and found != set(TABLE_ORDER):
        pdp.fail('Portfolio extension is incomplete')
    return bool(found)


def extension_ddl(c):
    if not enabled(c):
        return []
    ddls = []
    for name in tuple(TABLE_ORDER) + INDEX_NAMES:
        row = c.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()
        if row is None or row[0] is None:
            pdp.fail('Missing portfolio table/index: ' + name)
        ddls.append(row[0])
    return ddls


def upgrade(db):
    with closing(pdp.connect(db)) as c:
        statements._check(c)
        if enabled(c):
            return {'status': 'already_upgraded'}
        if not statements.enabled(c):
            pdp.fail('Run statement-upgrade first')
    def mutate(c):
        ddls = [
            '''CREATE TABLE ld_portfolio_sources (
                source_id TEXT PRIMARY KEY,source_ref TEXT NOT NULL UNIQUE,
                sha256 TEXT NOT NULL,content TEXT NOT NULL,created_at TEXT NOT NULL)''',
            '''CREATE TABLE ld_asset_memberships (
                asset_id TEXT PRIMARY KEY REFERENCES assets(asset_id),
                account_id TEXT REFERENCES ld_accounts(account_id),instrument_key TEXT NOT NULL,
                mapping_status TEXT NOT NULL CHECK(mapping_status IN ('documented','legacy_label','unresolved')),
                source_ref TEXT NOT NULL,reviewed_at TEXT NOT NULL)''',
            '''CREATE TABLE ld_portfolio_classifications (
                review_id TEXT PRIMARY KEY,source_id TEXT NOT NULL REFERENCES ld_portfolio_sources,
                observation_id TEXT REFERENCES ld_observations,link_id TEXT REFERENCES ld_transfer_links,
                classification TEXT NOT NULL CHECK(classification IN ('internal_transfer','external_contribution','non_investment')),
                counter_account_id TEXT REFERENCES ld_accounts,basis_amount TEXT,basis_currency TEXT,
                user_note TEXT NOT NULL,revision INTEGER NOT NULL,recorded_at TEXT NOT NULL,
                CHECK((observation_id IS NULL)!=(link_id IS NULL)),
                CHECK((basis_amount IS NULL)=(basis_currency IS NULL)),
                UNIQUE(source_id,observation_id),UNIQUE(source_id,link_id))''',
            '''CREATE TABLE ld_portfolio_applied (
                source_id TEXT PRIMARY KEY REFERENCES ld_portfolio_sources,manifest_hash TEXT NOT NULL,
                manifest_json TEXT NOT NULL,previous_mappings_json TEXT NOT NULL,
                previous_accounts_json TEXT NOT NULL,applied_at TEXT NOT NULL)''',
            '''CREATE TABLE ld_portfolio_migrations (
                version INTEGER PRIMARY KEY,migrated_at TEXT NOT NULL,previous_revision INTEGER NOT NULL)''',
            'CREATE INDEX ld_memberships_account ON ld_asset_memberships(account_id,asset_id)',
            'CREATE INDEX ld_memberships_instrument ON ld_asset_memberships(instrument_key,account_id)',
            'CREATE INDEX ld_classifications_observation ON ld_portfolio_classifications(observation_id,revision DESC)',
            'CREATE INDEX ld_classifications_link ON ld_portfolio_classifications(link_id,revision DESC)',
        ]
        for sql in ddls:
            c.execute(sql)
        c.execute('INSERT INTO ld_portfolio_migrations VALUES (?,?,?)', (1,pdp.now(),pdp.get_meta(c)['revision']))
        return {'status': 'upgraded', 'portfolio_schema': 1}
    return statements._write(db, 'Enable portfolio structure independent of valuation', mutate,
                             audit_table='ld_portfolio_migrations', audit_action='migrate')


def validate_manifest(m):
    required = {'schema','source_ref','source_text','accounts','memberships','classifications'}
    if not isinstance(m,dict) or set(m) != required or m['schema'] != 'LocalDeltaPortfolio/1':
        pdp.fail('Unsupported portfolio manifest')
    pdp.check_str(m['source_ref'],'source_ref',True,1024)
    pdp.check_str(m['source_text'],'source_text',True,2_000_000)
    for key in ('accounts','memberships','classifications'):
        if not isinstance(m[key],list) or len(m[key]) > 10000:
            pdp.fail('Invalid portfolio list: ' + key)
    seen = set()
    for account in m['accounts']:
        ledger.validate_op({'table':'ledger_accounts','action':'create','record':account})
        if account['account_id'] in seen:
            pdp.fail('Duplicate account')
        seen.add(account['account_id'])
    seen = set()
    for row in m['memberships']:
        if not isinstance(row,dict) or set(row) != {'asset_id','account_id','instrument_key','mapping_status','source_ref'}:
            pdp.fail('Invalid asset membership fields')
        pdp.check_uuid(row['asset_id'],'asset_id')
        if row['asset_id'] in seen:
            pdp.fail('Duplicate asset membership')
        seen.add(row['asset_id'])
        if row['account_id'] is not None:
            pdp.check_uuid(row['account_id'],'account_id')
        if not isinstance(row['mapping_status'],str) or row['mapping_status'] not in MAPPING_STATUSES:
            pdp.fail('Invalid mapping status')
        if (row['mapping_status']=='unresolved') != (row['account_id'] is None):
            pdp.fail('Unresolved memberships must not assert an account')
        pdp.check_str(row['instrument_key'],'instrument_key',True,1024)
        pdp.check_str(row['source_ref'],'source_ref',True,2048)
    seen = set()
    for row in m['classifications']:
        if not isinstance(row,dict) or set(row) != {'target_type','target_id','classification','counter_account_id',
                                                   'basis_amount','basis_currency','signed_amount','user_note'}:
            pdp.fail('Invalid classification fields')
        if row['target_type'] not in ('observation','link'):
            pdp.fail('Unknown classification target')
        pdp.check_uuid(row['target_id'],'target_id')
        key = (row['target_type'],row['target_id'])
        if key in seen:
            pdp.fail('Duplicate classification target')
        seen.add(key)
        if not isinstance(row['classification'],str) or row['classification'] not in CLASSES:
            pdp.fail('Unknown cash classification')
        pdp.check_str(row['user_note'],'user_note',True,4096)
        if row['counter_account_id'] is not None:
            pdp.check_uuid(row['counter_account_id'],'counter_account_id')
        if row['classification']=='external_contribution':
            if row['target_type']!='observation' or row['basis_amount'] is None or row['basis_currency'] is None:
                pdp.fail('Contribution requires an observation and user-declared original funding basis')
            if Decimal(statements.amount(row['basis_amount'],row['basis_currency'],False)) <= 0:
                pdp.fail('Contribution must be positive')
        elif row['basis_amount'] is not None or row['basis_currency'] is not None:
            pdp.fail('Only contributions can have a funding basis')
        if row['target_type']=='link' and (row['classification']!='internal_transfer' or row['signed_amount'] is not None):
            pdp.fail('Transfer links are classified as internal transfers only')
        if row['classification']=='internal_transfer' and row['target_type']=='observation' and row['counter_account_id'] is None:
            pdp.fail('A one-sided internal transfer needs its owned counter-account')
    return {'status':'preview','accounts':len(m['accounts']),'asset_memberships':len(m['memberships']),
            'classifications':len(m['classifications']),'valuation_mode':'deferred',
            'note':'Catalog structure and user cash classifications; no current NAV, weights or returns.'}


def apply_manifest(db, m):
    preview = validate_manifest(m)
    digest = hashlib.sha256(pdp.canonical(m)).hexdigest()
    source_hash = hashlib.sha256(m['source_text'].encode('utf-8')).hexdigest()
    sid = statements.ident('portfolio-source:' + m['source_ref'] + ':' + source_hash)
    with closing(pdp.connect(db)) as c:
        statements._check(c)
        if not enabled(c):
            pdp.fail('Run portfolio-upgrade first')
        old = c.execute('SELECT manifest_hash FROM ld_portfolio_applied WHERE source_id=?',(sid,)).fetchone()
        if old:
            if old[0] != digest:
                pdp.fail('Same confirmation source has a different mapping; supply a new reviewed source reference')
            return {'status':'already_applied','source_id':sid}
    def mutate(c):
        c.execute('INSERT INTO ld_portfolio_sources VALUES (?,?,?,?,?)',
                  (sid,m['source_ref'],source_hash,m['source_text'],pdp.now()))
        previous_accounts, previous_mappings = [], []
        for account in m['accounts']:
            aid = account['account_id']
            old = c.execute('SELECT * FROM ld_accounts WHERE account_id=?',(aid,)).fetchone()
            if old:
                if any(old[k] != account[k] for k in ('institution','account_type','ownership')):
                    pdp.fail('Existing account identity cannot be changed by a portfolio import')
                previous_accounts.append(dict(old))
                c.execute('UPDATE ld_accounts SET display_name=? WHERE account_id=?',(account['display_name'],aid))
            else:
                c.execute('INSERT INTO ld_accounts VALUES (?,?,?,?,?,?)',
                          (aid,account['institution'],account['display_name'],account['account_type'],account['ownership'],pdp.now()))
        for row in m['memberships']:
            if not c.execute('SELECT 1 FROM assets WHERE asset_id=?',(row['asset_id'],)).fetchone():
                pdp.fail('Membership references an unknown asset')
            old = c.execute('SELECT * FROM ld_asset_memberships WHERE asset_id=?',(row['asset_id'],)).fetchone()
            if old:
                previous_mappings.append(dict(old))
            c.execute('INSERT OR REPLACE INTO ld_asset_memberships VALUES (?,?,?,?,?,?)',
                      (row['asset_id'],row['account_id'],row['instrument_key'],row['mapping_status'],row['source_ref'],pdp.now()))
        evidence = 'User confirmation: ' + m['source_ref']
        for row in sorted(m['classifications'],key=lambda r:r['target_type']=='link'):
            target, ident = row['target_type'], row['target_id']
            table, column = ('ld_observations','observation_id') if target=='observation' else ('ld_transfer_links','link_id')
            original = c.execute('SELECT * FROM '+table+' WHERE '+column+'=?',(ident,)).fetchone()
            if not original:
                pdp.fail('Unknown classification target')
            if row['counter_account_id']:
                account = c.execute('SELECT ownership FROM ld_accounts WHERE account_id=?',(row['counter_account_id'],)).fetchone()
                if not account or account[0]!='owned':
                    pdp.fail('Internal transfer counter-account must be owned')
            if target=='observation':
                if original['kind']!='cash':
                    pdp.fail('Cash classifications require actual cash observations, not duplicate evidence')
                if row['signed_amount'] is not None:
                    value = Decimal(statements.amount(row['signed_amount'],original['currency']))
                    if row['classification']=='external_contribution' and value <= 0:
                        pdp.fail('Contribution must be a receipt')
                    statements.append_review(c,ident,'direction',evidence+'; '+row['user_note'],row['signed_amount'])
                elif row['classification']=='external_contribution':
                    pdp.fail('Contribution receipt direction needs confirmation')
            else:
                owners = [r[0] for r in c.execute(
                    'SELECT a.ownership FROM ld_observations o JOIN ld_accounts a USING(account_id) '
                    'WHERE o.observation_id IN (?,?)', (original['debit_id'], original['credit_id']))]
                if len(owners) != 2 or any(owner != 'owned' for owner in owners):
                    pdp.fail('Internal transfer links require two owned account endpoints')
                statements.append_review(c,ident,'confirmed',evidence+'; '+row['user_note'])
            c.execute('INSERT INTO ld_portfolio_classifications VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                      (statements.ident('classification:'+sid+':'+target+':'+ident),sid,
                       ident if target=='observation' else None,ident if target=='link' else None,
                       row['classification'],row['counter_account_id'],row['basis_amount'],row['basis_currency'],
                       row['user_note'],pdp.get_meta(c)['revision']+1,pdp.now()))
        c.execute('INSERT INTO ld_portfolio_applied VALUES (?,?,?,?,?,?)',
                  (sid,digest,json.dumps(m,ensure_ascii=False),json.dumps(previous_mappings,ensure_ascii=False),
                   json.dumps(previous_accounts,ensure_ascii=False),pdp.now()))
        return dict(preview,status='applied',source_id=sid)
    return statements._write(db,'Apply portfolio structure and explicit user classifications',mutate,
                             audit_table='ld_portfolio_applied',audit_action='portfolio_update',
                             source={'kind':'user_confirmation','reference':m['source_ref']})


def _where(account_ids):
    if account_ids is None:
        return '1=1',[]
    return 'm.account_id IN ('+','.join('?' for _ in account_ids)+')',account_ids


def assets(c, account_ids=None, limit=100, offset=0, **unused):
    if not enabled(c):
        return {'enabled':False,'rows':[],'total':0,'has_more':False}
    where,args = _where(account_ids)
    base = (' FROM assets a LEFT JOIN ld_asset_memberships m ON m.asset_id=a.asset_id '
            'LEFT JOIN ld_accounts x ON x.account_id=m.account_id WHERE '+where)
    total = c.execute('SELECT COUNT(*)'+base,args).fetchone()[0]
    rows = [dict(r) for r in c.execute(
        'SELECT a.asset_id,a.asset_name,a.asset_type,a.currency,a.status AS original_status,'
        'a.account AS original_account_label,m.instrument_key,m.account_id,x.display_name AS account_name,'
        "COALESCE(m.mapping_status,'unresolved') AS mapping_status,m.source_ref"+base+
        ' ORDER BY x.display_name,a.asset_type,a.asset_name,a.asset_id LIMIT ? OFFSET ?',args+[limit,offset])]
    return {'enabled':True,'rows':rows,'total':total,'has_more':offset+len(rows)<total,
            'limit':limit,'offset':offset,'valuation_mode':'deferred',
            'note':'Asset catalog and provenance only. Historical/placeholder records are not asserted current holdings.'}


def accounts(c, account_ids=None, **unused):
    if not enabled(c):
        return {'enabled':False,'rows':[],'valuation_mode':'deferred'}
    where,args = _where(account_ids)
    # Conditions refer to the account itself so registered accounts without assets remain visible.
    where=where.replace('m.account_id','x.account_id')
    rows=[dict(r) for r in c.execute(
        'SELECT x.account_id,x.display_name,x.institution,x.account_type,x.ownership,'
        'COUNT(m.asset_id) AS asset_records,COUNT(DISTINCT m.instrument_key) AS registered_instruments '
        "FROM ld_accounts x LEFT JOIN ld_asset_memberships m ON m.account_id=x.account_id WHERE x.ownership='owned' AND "+where+
        ' GROUP BY x.account_id ORDER BY x.institution,x.display_name',args)]
    unresolved=c.execute('SELECT COUNT(*) FROM assets a LEFT JOIN ld_asset_memberships m ON m.asset_id=a.asset_id '
                         'WHERE m.account_id IS NULL').fetchone()[0]
    return {'enabled':True,'rows':rows,'unresolved_asset_accounts':unresolved,'valuation_mode':'deferred',
            'note':'Registered accounts and instrument counts, not balances, current holdings or allocation weights.'}


def capital(c, account_ids=None, start=None, end=None, **unused):
    if not enabled(c):
        return {'enabled':False,'rows':[],'basis_totals':[]}
    conditions=["r.classification='external_contribution'",'NOT EXISTS(SELECT 1 FROM ld_portfolio_classifications newer '
                'WHERE newer.observation_id=r.observation_id AND newer.revision>r.revision)']
    args=[]
    if account_ids is not None:
        conditions.append('o.account_id IN ('+','.join('?' for _ in account_ids)+')');args.extend(account_ids)
    if start:
        conditions.append('o.date>=?');args.append(start)
    if end:
        conditions.append('o.date<=?');args.append(end)
    rows=[dict(r) for r in c.execute(
        'SELECT r.observation_id,o.account_id,o.date,r.basis_amount,r.basis_currency,'
        'o.signed_amount AS received_amount,o.currency AS received_currency,r.user_note,s.source_ref '
        'FROM ld_portfolio_classifications r JOIN ld_effective_observations o ON o.observation_id=r.observation_id '
        'JOIN ld_portfolio_sources s ON s.source_id=r.source_id WHERE '+' AND '.join(conditions)+
        ' ORDER BY o.date,r.observation_id',args)]
    totals={}
    for row in rows:
        code=row['basis_currency'];totals[code]=totals.get(code,Decimal(0))+Decimal(row['basis_amount'])
    return {'enabled':True,'rows':rows,'basis_totals':[{'currency':k,'amount':format(v,'f')} for k,v in sorted(totals.items())],
            'complete':False,'note':'User-confirmed funding basis, not an extra currency cash posting or total lifetime principal. '
            'Internal transfers and personal non-investment movements are excluded.'}
