"""Read-only, bounded analytics API and extensible allowlisted block registry."""
from __future__ import annotations
from collections import defaultdict
from decimal import Decimal
import json

import pdp
import statements

# Third-party components register trusted Python handlers, never arbitrary SQL/JS
# received from a dashboard layout or HTTP request.
BLOCKS = {}
CUSTOM_VIEWS = {}


def register_view(key, handler):
    """Register trusted local Python queries. HTTP clients cannot register code."""
    if (not isinstance(key, str) or not key.replace('_', '').isalnum()
            or key in VIEWS or not callable(handler)):
        raise ValueError('Invalid or duplicate analytics view')
    VIEWS.add(key)
    CUSTOM_VIEWS[key] = handler


def register_block(key, title, view):
    if not isinstance(key, str) or not key.replace('_', '').isalnum() or key in BLOCKS:
        raise ValueError('Invalid or duplicate block key')
    if view not in VIEWS:
        raise ValueError('Unknown analytics view')
    BLOCKS[key] = {'type': key, 'title': title, 'view': view}


def pagination(limit=100, offset=0):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        pdp.fail('limit must be 1..1000')
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        pdp.fail('offset must be nonnegative')
    return limit, offset


class QuerySession:
    """One audited SQLite read snapshot shared by all dashboard blocks."""
    def __init__(self, db):
        self.c = statements.read_connection(db)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.c.close()

    def filters(self, account_ids=None, start=None, end=None, alias='o'):
        if account_ids is not None:
            if not isinstance(account_ids, list) or len(account_ids) > 100:
                pdp.fail('account_ids must contain at most 100 distinct UUIDs')
            for aid in account_ids:
                pdp.check_uuid(aid, 'account_id')
                if not self.c.execute('SELECT 1 FROM ld_accounts WHERE account_id=?', (aid,)).fetchone():
                    pdp.fail('Unknown account_id')
            if len(set(account_ids)) != len(account_ids):
                pdp.fail('Duplicate account_id')
        if start:
            statements.date(start)
        if end:
            statements.date(end)
        if start and end and start > end:
            pdp.fail('start must not follow end')
        cond, args = ['1=1'], []
        if account_ids is not None:
            cond.append(alias + '.account_id IN (' + ','.join('?' for _ in account_ids) + ')')
            args.extend(account_ids)
        if start:
            cond.append(alias + '.date>=?')
            args.append(start)
        if end:
            cond.append(alias + '.date<=?')
            args.append(end)
        return ' AND '.join(cond), args

    def accounts(self, **filters):
        return {'accounts': [dict(x) for x in self.c.execute(
            'SELECT account_id,institution,display_name,account_type,ownership FROM ld_accounts '
            "WHERE ownership='owned' ORDER BY institution,display_name")]}

    def movements(self, account_ids=None, start=None, end=None, limit=100, offset=0):
        pagination(limit, offset)
        cond, args = self.filters(account_ids, start, end)
        where = "o.kind='cash' AND " + cond
        total = self.c.execute('SELECT COUNT(*) FROM ld_effective_observations o WHERE ' + where, args).fetchone()[0]
        rows = [dict(x) for x in self.c.execute(
            'SELECT o.observation_id,o.account_id,o.date,o.currency,o.signed_amount,o.statement_amount,'
            'o.reported_balance,o.description,o.status,o.source_locator '
            'FROM ld_effective_observations o WHERE ' + where + ' ORDER BY o.date,o.observation_id LIMIT ? OFFSET ?',
            args + [limit, offset])]
        return {'rows': rows, 'total': total, 'limit': limit, 'offset': offset,
                'has_more': offset + len(rows) < total, 'balance_complete': False}

    def timeline(self, account_ids=None, start=None, end=None, limit=200, offset=0):
        pagination(limit, offset)
        self.filters(account_ids, start, end)
        cond, args = ["l.certainty!='rejected'"], []
        if account_ids is not None:
            placeholders = ','.join('?' for _ in account_ids)
            cond += ['(l.debit_id IN (SELECT observation_id FROM ld_observations WHERE account_id IN ('
                     + placeholders + ')) OR l.credit_id IN (SELECT observation_id FROM ld_observations '
                     'WHERE account_id IN (' + placeholders + ')))']
            args += account_ids + account_ids
        # Intersect the date span; delayed settlements crossing a boundary remain visible.
        if start:
            cond += ['max(l.sent_date,l.received_date)>=?']; args += [start]
        if end:
            cond += ['min(l.sent_date,l.received_date)<=?']; args += [end]
        base = (' FROM ld_effective_links l JOIN ld_observations d ON d.observation_id=l.debit_id '
                'JOIN ld_observations r ON r.observation_id=l.credit_id WHERE ' + ' AND '.join(cond))
        total = self.c.execute('SELECT COUNT(*)' + base, args).fetchone()[0]
        rows = [dict(x) for x in self.c.execute(
            'SELECT l.link_id,d.account_id AS from_account_id,r.account_id AS to_account_id,'
            'l.sent_date,l.received_date,l.debit_amount,l.credit_amount,d.currency AS sent_currency,'
            'r.currency AS received_currency,l.certainty,l.kind,l.debit_id,l.credit_id,'
            'd.source_locator AS debit_source,r.source_locator AS credit_source,'
            "CASE WHEN d.kind='transfer_evidence' OR r.kind='transfer_evidence' THEN 1 ELSE 0 END AS evidence_only_endpoint"
            + base + ' ORDER BY l.sent_date,l.link_id LIMIT ? OFFSET ?', args + [limit, offset])]
        lanes = self.accounts()['accounts']
        if account_ids is not None:
            used = set(account_ids) | {x[k] for x in rows for k in ('from_account_id', 'to_account_id')}
            lanes = [x for x in lanes if x['account_id'] in used]
        return {'account_lanes': lanes, 'edges': rows, 'total': total, 'limit': limit, 'offset': offset,
                'has_more': offset + len(rows) < total, 'time_precision': 'date', 'balance_complete': False,
                'note': 'Prepared matches are hypotheses. Posting dates are not exact transfer instants.'}

    def summary(self, account_ids=None, start=None, end=None, **unused):
        cond, args = self.filters(account_ids, start, end)
        totals = defaultdict(lambda: {'inflow': Decimal(0), 'outflow': Decimal(0), 'net_movement': Decimal(0),
                                      'known_rows': 0, 'unknown_direction_rows': 0})
        for row in self.c.execute('SELECT account_id,currency,signed_amount FROM ld_effective_observations o '
                                  "WHERE o.kind='cash' AND " + cond, args):
            bucket = totals[(row['account_id'], row['currency'])]
            if row['signed_amount'] is None:
                bucket['unknown_direction_rows'] += 1
                continue
            value = Decimal(row['signed_amount'])
            bucket['known_rows'] += 1
            bucket['net_movement'] += value
            bucket['inflow' if value >= 0 else 'outflow'] += abs(value)
        rows = []
        for (aid, ccy), values in sorted(totals.items()):
            rows.append(dict(account_id=aid, currency=ccy,
                             **{k: (format(v, 'f') if values['known_rows'] else None)
                                if isinstance(v, Decimal) else v for k, v in values.items()}))
        return {'rows': rows, 'balance': None, 'investment_return': None, 'complete': False,
                'note': 'Gross known cash movements include transfers and redemptions; they are not income or investment returns. '
                        'Unknown directions are excluded, currencies stay separate, and missing coverage is not zero.'}

    def positions(self, account_ids=None, start=None, end=None, limit=100, offset=0):
        pagination(limit, offset)
        cond, args = self.filters(account_ids, start, end)
        base = " FROM ld_effective_observations o WHERE o.kind='position' AND " + cond
        total = self.c.execute('SELECT COUNT(*)' + base, args).fetchone()[0]
        rows = [dict(x) for x in self.c.execute(
            'SELECT o.observation_id,o.account_id,o.date,o.currency,o.statement_amount AS market_value,'
            'o.description,o.raw_json,o.source_locator' + base + ' ORDER BY o.date DESC,o.observation_id LIMIT ? OFFSET ?',
            args + [limit, offset])]
        for row in rows:
            raw = json.loads(row.pop('raw_json'))
            row['quantity'] = raw.get('closing')
            row['instrument_key'] = raw.get('key')
        return {'rows': rows, 'total': total, 'has_more': offset + len(rows) < total,
                'limit': limit, 'offset': offset, 'note': 'Dated instrument observations, not whole-account NAV.'}

    def review(self, account_ids=None, start=None, end=None, limit=100, offset=0):
        pagination(limit, offset)
        cond, args = self.filters(account_ids, start, end)
        # Undated candidates stay visible even when the dashboard has a date filter.
        if start or end:
            cond += ' OR (o.date IS NULL AND ' + self.filters(account_ids)[0] + ')'
            args += self.filters(account_ids)[1]
            cond = '(' + cond + ')'
        base = (" FROM ld_effective_observations o WHERE (o.status='direction_unknown' OR o.kind IN ('trade_candidate','transfer_evidence') "
                "OR (o.kind='position' AND o.statement_amount IS NULL) "
                "OR (o.review_reason IS NOT NULL AND NOT EXISTS(SELECT 1 FROM ld_effective_links l "
                "WHERE (l.debit_id=o.observation_id OR l.credit_id=o.observation_id) AND l.certainty!='rejected'))) AND " + cond)
        total = self.c.execute('SELECT COUNT(*)' + base, args).fetchone()[0]
        rows = [dict(x) for x in self.c.execute(
            'SELECT o.observation_id,o.account_id,o.kind,o.date,o.currency,o.statement_amount,'
            'o.description,o.status,o.review_reason,o.source_locator' + base + ' ORDER BY o.date,o.observation_id LIMIT ? OFFSET ?',
            args + [limit, offset])]
        return {'rows': rows, 'total': total, 'has_more': offset + len(rows) < total,
                'limit': limit, 'offset': offset}

    def fx(self, account_ids=None, start=None, end=None, limit=100, offset=0):
        pagination(limit, offset)
        cond, args = self.filters(account_ids, start, end)
        base = " FROM ld_observations o WHERE o.kind='fx' AND " + cond
        total = self.c.execute('SELECT COUNT(*)' + base, args).fetchone()[0]
        rows = []
        for source in self.c.execute('SELECT *' + base + ' ORDER BY o.date,o.observation_id LIMIT ? OFFSET ?',
                                     args + [limit, offset]):
            raw = json.loads(source['raw_json'])
            rows.append({'observation_id': source['observation_id'], 'account_id': source['account_id'],
                         'date': source['date'], 'sold_currency': raw['sold_currency'],
                         'sold_amount': raw['sold_amount'], 'bought_currency': raw['bought_currency'],
                         'bought_amount': raw['bought_amount'], 'recorded_rate': raw['rate'],
                         'reference': raw['txn_reference'], 'source_locator': source['source_locator']})
        return {'rows': rows, 'total': total, 'has_more': offset + len(rows) < total,
                'limit': limit, 'offset': offset}

    def coverage(self, **unused):
        rows = [dict(x) for x in self.c.execute(
            'SELECT account_id,kind,MIN(date) AS first_date,MAX(date) AS last_date,COUNT(*) AS records '
            'FROM ld_observations GROUP BY account_id,kind ORDER BY account_id,kind')]
        return {'rows': rows, 'complete': False, 'note': 'Observed ranges do not prove continuous statement coverage.'}

    def portfolio(self, **filters):
        import portfolio
        return portfolio.accounts(self.c, **filters)

    def asset_catalog(self, **filters):
        import portfolio
        return portfolio.assets(self.c, **filters)

    def capital(self, **filters):
        import portfolio
        return portfolio.capital(self.c, **filters)

    def query(self, view, **filters):
        if not isinstance(view, str) or view not in VIEWS:
            pdp.fail('Unknown analytics view')
        if set(filters) - {'account_ids', 'start', 'end', 'limit', 'offset'}:
            pdp.fail('Unknown query filter')
        self.filters(filters.get('account_ids'), filters.get('start'), filters.get('end'))
        pagination(filters.get('limit', 100), filters.get('offset', 0))
        if view in CUSTOM_VIEWS:
            return CUSTOM_VIEWS[view](self, **filters)
        return getattr(self, view)(**filters)


VIEWS = {'accounts', 'movements', 'timeline', 'summary', 'positions', 'review', 'coverage', 'fx'}
VIEWS.update({'portfolio', 'asset_catalog', 'capital'})
for _key, _title, _view in (
    ('account_timeline', '账户转账时间线', 'timeline'), ('cash_movements', '已知现金变动', 'summary'),
    ('review_queue', '待核验记录', 'review'), ('position_history', '持仓估值记录', 'positions'),
    ('cash_table', '逐笔流水', 'movements'), ('source_coverage', '资料覆盖范围', 'coverage'),
    ('fx_history', '实际换汇记录', 'fx')):
    register_block(_key, _title, _view)
register_block('portfolio_accounts', '组合账户结构', 'portfolio')
register_block('portfolio_assets', '资产档案与账户归属', 'asset_catalog')
register_block('confirmed_capital', '已确认入金依据', 'capital')


def query(db, view, **filters):
    with QuerySession(db) as session:
        return session.query(view, **filters)


def dashboard_data(db, **filters):
    with QuerySession(db) as session:
        return {'blocks': list(BLOCKS.values()), 'revision': pdp.get_meta(session.c)['revision'],
                'data': {view: session.query(view, **filters) for view in sorted(VIEWS)}}


def record(db, record_id):
    """Private drill-down: immutable raw data, original source hashes and reviews."""
    pdp.check_uuid(record_id, 'record_id')
    with QuerySession(db) as session:
        c = session.c
        row = c.execute('SELECT * FROM ld_effective_observations WHERE observation_id=?', (record_id,)).fetchone()
        if row:
            value = dict(row)
            value['raw'] = json.loads(value.pop('raw_json'))
            value['source_locator'] = json.loads(value['source_locator'])
            value['document'] = dict(c.execute(
                'SELECT document_id,relative_path,sha256,size_bytes FROM ld_source_documents WHERE document_id=?',
                (row['document_id'],)).fetchone())
            value['reviews'] = [dict(x) for x in c.execute(
                'SELECT * FROM ld_review_decisions WHERE observation_id=? ORDER BY revision,decision_id', (record_id,))]
            return value
        row = c.execute('SELECT * FROM ld_effective_links WHERE link_id=?', (record_id,)).fetchone()
        if not row:
            pdp.fail('Unknown observation or transfer link')
        value = dict(row)
        value['evidence'] = json.loads(value.pop('evidence_json'))
        value['reviews'] = [dict(x) for x in c.execute(
            'SELECT * FROM ld_review_decisions WHERE link_id=? ORDER BY revision,decision_id', (record_id,))]
        return value
