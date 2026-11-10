"""Audited, local statement observations and transfer allocations.

Observations are evidence, not double-entry journal postings. Unknown signs and
incomplete account coverage never become balances, income, or investment returns.
Only the standard library is required. No archive code is executed.
"""
from __future__ import annotations

from contextlib import closing
from decimal import Decimal
import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import uuid
import zipfile

import ledger
import multicurrency as mc
import pdp

TABLE_ORDER = {
    "ld_statement_batches": "batch_id", "ld_source_documents": "document_id",
    "ld_observations": "observation_id", "ld_transfer_links": "link_id",
    "ld_review_decisions": "decision_id",
    "ld_statement_migrations": "version",
}
INDEX_NAMES = ("ld_observations_account_date", "ld_observations_kind_status_date",
               "ld_links_sent", "ld_links_received", "ld_links_debit", "ld_links_credit",
               "ld_reviews_observation", "ld_reviews_link")
VIEW_NAMES = ('ld_effective_observations', 'ld_effective_links')
MAX_ARCHIVE = 150 * 1024 * 1024
NAMESPACE = uuid.UUID("4d86e4c1-87ab-4992-90c2-249d206de5aa")


def ident(value):
    return str(uuid.uuid5(NAMESPACE, value))


def enabled(c):
    found = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    present = found & set(TABLE_ORDER)
    if present and present != set(TABLE_ORDER):
        pdp.fail("Statement extension is incomplete")
    return bool(present)


def extension_ddl(c):
    if not enabled(c):
        return []
    result = []
    for name in tuple(TABLE_ORDER) + INDEX_NAMES + VIEW_NAMES:
        row = c.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if row is None or row[0] is None:
            pdp.fail("Missing statement table/index: " + name)
        result.append(row[0])
    return result


def _create_tables(c):
    # Execute separately: executescript would implicitly commit an active migration.
    ddls = [
        """CREATE TABLE ld_statement_batches (
            batch_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL UNIQUE,
            imported_at TEXT NOT NULL, description TEXT NOT NULL)""",
        """CREATE TABLE ld_source_documents (
            document_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES ld_statement_batches,
            relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
            text_content TEXT, UNIQUE(batch_id,relative_path))""",
        """CREATE TABLE ld_observations (
            observation_id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES ld_source_documents,
            row_key TEXT NOT NULL, account_id TEXT NOT NULL REFERENCES ld_accounts,
            kind TEXT NOT NULL CHECK(kind IN ('cash','fx','position','trade_candidate','transfer_evidence')),
            date TEXT, currency TEXT, signed_amount TEXT, statement_amount TEXT,
            reported_balance TEXT, description TEXT NOT NULL, source_locator TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('direction_known','direction_unknown','review_only')),
            raw_json TEXT NOT NULL, review_reason TEXT, UNIQUE(document_id,row_key))""",
        """CREATE TABLE ld_transfer_links (
            link_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES ld_statement_batches,
            debit_id TEXT NOT NULL REFERENCES ld_observations,
            credit_id TEXT NOT NULL REFERENCES ld_observations,
            debit_amount TEXT NOT NULL, credit_amount TEXT NOT NULL,
            sent_date TEXT NOT NULL, received_date TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('transfer','fx')),
            certainty TEXT NOT NULL CHECK(certainty IN ('confirmed','likely','conflicting')),
            evidence_json TEXT NOT NULL, CHECK(debit_id != credit_id))""",
        """CREATE TABLE ld_statement_migrations (
            version INTEGER PRIMARY KEY, migrated_at TEXT NOT NULL,
            previous_revision INTEGER NOT NULL)""",
        """CREATE TABLE ld_review_decisions (
            decision_id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
            observation_id TEXT REFERENCES ld_observations,
            link_id TEXT REFERENCES ld_transfer_links,
            decision TEXT NOT NULL CHECK(decision IN ('direction','confirmed','likely','conflicting','rejected')),
            signed_amount TEXT, evidence TEXT NOT NULL, decided_at TEXT NOT NULL,
            CHECK((observation_id IS NOT NULL AND link_id IS NULL AND decision='direction' AND signed_amount IS NOT NULL)
               OR (observation_id IS NULL AND link_id IS NOT NULL AND decision!='direction' AND signed_amount IS NULL)))""",
        "CREATE INDEX ld_observations_account_date ON ld_observations(account_id,date,observation_id)",
        "CREATE INDEX ld_observations_kind_status_date ON ld_observations(kind,status,date,observation_id)",
        "CREATE INDEX ld_links_sent ON ld_transfer_links(min(sent_date,received_date),link_id)",
        "CREATE INDEX ld_links_received ON ld_transfer_links(max(sent_date,received_date),link_id)",
        "CREATE INDEX ld_links_debit ON ld_transfer_links(debit_id)",
        "CREATE INDEX ld_links_credit ON ld_transfer_links(credit_id)",
        "CREATE INDEX ld_reviews_observation ON ld_review_decisions(observation_id,revision DESC,decision_id DESC)",
        "CREATE INDEX ld_reviews_link ON ld_review_decisions(link_id,revision DESC,decision_id DESC)",
        """CREATE VIEW ld_effective_observations AS SELECT
            o.observation_id,o.document_id,o.row_key,o.account_id,o.kind,o.date,o.currency,
            COALESCE((SELECT d.signed_amount FROM ld_review_decisions d WHERE d.observation_id=o.observation_id
                ORDER BY d.revision DESC,d.decision_id DESC LIMIT 1),o.signed_amount) AS signed_amount,
            o.statement_amount,o.reported_balance,o.description,o.source_locator,
            CASE WHEN EXISTS(SELECT 1 FROM ld_review_decisions d WHERE d.observation_id=o.observation_id)
                THEN 'direction_reviewed' ELSE o.status END AS status,o.raw_json,
            o.signed_amount AS raw_signed_amount,o.review_reason FROM ld_observations o""",
        """CREATE VIEW ld_effective_links AS SELECT
            l.link_id,l.batch_id,l.debit_id,l.credit_id,l.debit_amount,l.credit_amount,l.sent_date,l.received_date,l.kind,
            COALESCE((SELECT d.decision FROM ld_review_decisions d WHERE d.link_id=l.link_id
                ORDER BY d.revision DESC,d.decision_id DESC LIMIT 1),l.certainty) AS certainty,
            l.evidence_json FROM ld_transfer_links l""",
    ]
    for sql in ddls:
        c.execute(sql)


def _check(c):
    meta = pdp.get_meta(c)
    if (pdp.schema_fingerprint(c), pdp.data_fingerprint(c)) != (meta['schema_digest'], meta['data_digest']):
        pdp.fail("Unaudited database change; restore/review before importing")
    return meta


def _write(db, description, mutate, audit_table="ld_statement_batches", audit_action="statement_import", source=None):
    """Backup, lock, recheck, mutate, audit, fingerprint, atomic commit."""
    with closing(pdp.connect(db)) as c:
        before = _check(c)
        ledger.require_enabled(c)
    backup = pdp.fresh_backup(db, "before_statement_write")
    with closing(pdp.connect(db)) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            meta = _check(c)
            if meta != before:
                pdp.fail("Database changed during backup; retry the preview")
            package_id = str(uuid.uuid4())
            result = mutate(c)
            c.execute("INSERT INTO pdp_packages VALUES (?,?,?,?,?,?)", (
                package_id, hashlib.sha256(pdp.canonical(result)).hexdigest(),
                meta['revision'], meta['revision'] + 1, pdp.now(), description))
            c.execute("""INSERT INTO pdp_operations
                (op_id,package_id,action,table_name,entity_id,old_json,new_json,reason,source_json,applied_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", (
                str(uuid.uuid4()), package_id, audit_action, audit_table,
                result.get('batch_id', 'migration'), None, json.dumps(result, ensure_ascii=False),
                description, json.dumps(source or {'kind': 'local_statement', 'certainty': 'unverified'}), pdp.now()))
            c.execute("UPDATE pdp_meta SET revision=?,schema_digest=?,data_digest=? WHERE singleton=1", (
                meta['revision'] + 1, pdp.schema_fingerprint(c), pdp.data_fingerprint(c)))
            pdp.check_db(c)
            c.execute("COMMIT")
            return dict(result, revision=meta['revision'] + 1, backup_path=backup)
        except Exception:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise


def upgrade(db):
    with closing(pdp.connect(db)) as c:
        _check(c)
        if enabled(c):
            return {'status': 'already_upgraded'}
    def mutate(c):
        _create_tables(c)
        c.execute("INSERT INTO ld_statement_migrations VALUES (?,?,?)", (1, pdp.now(), pdp.get_meta(c)['revision']))
        return {'status': 'upgraded', 'statement_schema': 1}
    return _write(db, 'Enable immutable statement evidence and transfer allocations', mutate)


def amount(value, currency, signed=True):
    currency = mc.currency(currency)
    if not isinstance(value, str):
        pdp.fail('Statement amounts must be decimal strings')
    value = value.replace(',', '')
    number = mc.parse_decimal(value, 'statement amount', scale=mc.MINOR_UNITS[currency])
    if not signed and number < 0:
        pdp.fail('Expected a nonnegative statement amount')
    return format(number, 'f')


def date(value):
    pdp.check_str(value, 'date', True, 10)
    try:
        parsed = dt.date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
    except ValueError:
        pdp.fail('Expected date YYYY-MM-DD')
    return value


def _archive(path):
    """Read bounded bytes, reject traversal/duplicates and verify any manifest."""
    data = {}
    with zipfile.ZipFile(path) as z:
        infos = [x for x in z.infolist() if not x.is_dir()]
        if len(infos) > 2000 or sum(x.file_size for x in infos) > MAX_ARCHIVE:
            pdp.fail('Statement archive is too large')
        for info in infos:
            p = PurePosixPath(info.filename)
            if (p.is_absolute() or '..' in p.parts or '\\' in info.filename
                    or ':' in info.filename or info.filename in data
                    or (info.external_attr >> 16) & 0o170000 == 0o120000):
                pdp.fail('Unsafe or duplicate archive member')
            data[info.filename] = z.read(info)
    if 'MANIFEST.json' in data:
        manifest = json.loads(data['MANIFEST.json'])
        for entry in manifest.get('files', []):
            raw = data.get(entry['path'])
            if raw is None or len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
                pdp.fail('Archive manifest mismatch: ' + entry['path'])
    digest = hashlib.sha256(pdp.canonical({k: hashlib.sha256(v).hexdigest()
                                        for k, v in sorted(data.items()) if k != 'MANIFEST.json'})).hexdigest()
    return data, digest


def parse_bundle(path):
    """Adapter for the three-bank review pack; only supported structured inputs.

    The raw PDF/text/CSV evidence is preserved. Candidate rows never execute or
    become trades. Prepared matching claims are imported as likely, not confirmed.
    """
    data, digest = _archive(path)
    result = {'batch_id': ident('batch:' + digest), 'content_hash': digest,
              'documents': [], 'accounts': {}, 'observations': [], 'links': [], 'warnings': []}
    doc_by_name = {}
    for name, raw in sorted(data.items()):
        doc_id = ident('document:' + hashlib.sha256(raw).hexdigest() + ':' + name)
        result['documents'].append({'document_id': doc_id, 'relative_path': name,
            'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw),
            'text_content': raw.decode('utf-8-sig') if Path(name).suffix.lower() in ('.txt', '.csv', '.json', '.md') else None})
        doc_by_name[name] = doc_id

    def account(key, institution, label, category='bank'):
        aid = ident('account:' + key)
        result['accounts'][aid] = (aid, institution, label, category, 'owned', pdp.now())
        return aid
    cmb = account('cmb:cash', 'CMB', '招商银行 · 现金')
    za = account('za:savings', 'ZA Bank', '众安银行 · 储蓄')
    pot = account('za:pot', 'ZA Bank', '众安银行 · 储蓄罐')
    investment = account('za:investment', 'ZA Bank', '众安银行 · 投资', 'broker')
    hsbc = account('hsbc:cash', 'HSBC', '汇丰香港 · 现金')
    rows_by_locator = {}
    occurrences = {}

    def add(doc, row_key, aid, kind, raw, day=None, ccy=None, signed=None,
            unsigned=None, balance=None, description='', locator=None, status='review_only'):
        if day:
            day = date(day)
        if ccy:
            ccy = mc.currency(ccy)
        if any(v is not None for v in (signed, unsigned, balance)) and not ccy:
            pdp.fail('Amount needs a currency')
        signed = amount(signed, ccy) if signed is not None else None
        unsigned = amount(unsigned, ccy, False) if unsigned is not None else None
        balance = amount(balance, ccy) if balance is not None else None
        if kind == 'transfer_evidence':
            oid = ident('row:' + doc + ':' + row_key)
        else:
            semantic = hashlib.sha256(pdp.canonical([aid, kind, raw])).hexdigest()
            occurrence = occurrences.get((doc, semantic), 0)
            occurrences[(doc, semantic)] = occurrence + 1
            oid = ident('observation:' + semantic + ':' + str(occurrence))
        obs = {'observation_id': oid, 'document_id': doc,
               'row_key': row_key, 'account_id': aid, 'kind': kind, 'date': day,
               'currency': ccy, 'signed_amount': signed, 'statement_amount': unsigned,
               'reported_balance': balance, 'description': description,
               'source_locator': json.dumps(locator or {}, ensure_ascii=False),
               'status': status, 'raw_json': json.dumps(raw, ensure_ascii=False, sort_keys=True),
               'review_reason': None}
        result['observations'].append(obs)
        if kind == 'cash':
            loc = locator or {}
            key = (aid, loc.get('statement'), str(loc.get('page')), day, ccy,
                   Decimal(unsigned if unsigned is not None else signed).copy_abs())
            rows_by_locator.setdefault(key, []).append(obs)
        return obs

    matches = None
    for name, content in sorted(data.items()):
        if Path(name).suffix == '.csv':
            reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
            fields = set(reader.fieldnames or [])
            for index, r in enumerate(reader, 1):
                key = 'csv:' + str(index)
                loc = {'statement': r.get('statement', r.get('file')), 'page': r.get('page'), 'row': index}
                if {'signed_amount', 'balance', 'currency', 'account_type'} <= fields:
                    add(doc_by_name[name], key, cmb, 'cash', r, r['date'], r['currency'],
                        signed=r['signed_amount'], balance=r['balance'], description=r['description'],
                        locator=loc, status='direction_known')
                elif {'statement_amount', 'ending_balance', 'section'} <= fields:
                    ccy = r['section'].split()[0]
                    add(doc_by_name[name], key, pot if 'Pot' in r['section'] else za, 'cash', r,
                        r['date'], ccy, unsigned=r['statement_amount'], balance=r['ending_balance'] or None,
                        description=r['description'], locator=loc, status='direction_unknown')
                elif {'sold_currency', 'bought_currency', 'txn_reference'} <= fields:
                    amount(r['bought_amount'], r['bought_currency'], False)
                    mc.parse_decimal(r['rate'], 'executed FX rate', positive=True)
                    add(doc_by_name[name], key, za, 'fx', r, r['date'], r['sold_currency'],
                        unsigned=r['sold_amount'], description='FX ' + r['sold_currency'] + ' → ' + r['bought_currency'], locator=loc)
                elif {'closing', 'value', 'month', 'key'} <= fields:
                    add(doc_by_name[name], key, investment, 'position', r, r['month'], r['currency'],
                        unsigned=r['value'] or None, description=r['name'], locator=loc)
                elif {'candidate_type', 'context_text', 'date_detected'} <= fields:
                    add(doc_by_name[name], key, investment, 'trade_candidate', r,
                        description=r['context_text'], locator=loc)
        elif Path(name).suffix == '.json' and name != 'MANIFEST.json':
            obj = json.loads(content)
            if isinstance(obj, dict) and {'cmb', 'za', 'hsbc', 'za_hsbc'} <= set(obj):
                if matches:
                    pdp.fail('Multiple matching datasets in archive')
                matches = (doc_by_name[name], obj)
    if not result['observations']:
        pdp.fail('No supported structured statement data found')

    if matches:
        doc, matched = matches
        def endpoint(key, aid, r, day, ccy, value, direction=None, stmt=None, page=None):
            loc = {'statement': stmt or r.get('statement'), 'page': page or r.get('page')}
            value = amount(value, ccy, False)
            candidates = rows_by_locator.get((aid, loc['statement'], str(loc['page']), day, ccy, Decimal(value)), [])
            if direction:
                candidates = [x for x in candidates if x['signed_amount'] is None or
                              (Decimal(x['signed_amount']) > 0) == (direction == 'in')]
            if len(candidates) == 1:
                return candidates[0]
            # Keep independent matching evidence without counting it as another cash row.
            evidence_key = 'endpoint:' + aid + ':' + str(r.get('id') or key)
            return add(doc, evidence_key, aid, 'transfer_evidence', r, day, ccy, unsigned=value,
                       description='Prepared transfer evidence; cash endpoint unresolved', locator=loc)
        za_receipts = {r['id']: r for r in matched['za']}
        hsbc_receipts = {r['id']: r for r in matched['hsbc']}
        def link(key, a, b, av, bv, raw):
            result['links'].append({'link_id': ident('link:' + doc + ':' + key),
                'batch_id': result['batch_id'], 'debit_id': a['observation_id'], 'credit_id': b['observation_id'],
                'debit_amount': amount(av, a['currency'], False), 'credit_amount': amount(bv, b['currency'], False),
                'sent_date': a['date'], 'received_date': b['date'], 'kind': 'transfer',
                'certainty': 'likely', 'evidence_json': json.dumps(raw, ensure_ascii=False, sort_keys=True)})
        for r in matched['cmb']:
            a = endpoint('cmb:' + r['id'], cmb, r, r['date'], r['currency'], r['amount_cny'], 'out')
            receipt = za_receipts.get(r.get('receipt_id')) or hsbc_receipts.get(r.get('receipt_id'))
            if not receipt:
                a['review_reason'] = 'unmatched_transfer'
                result['warnings'].append({'type': 'unmatched_transfer', 'observation_id': a['observation_id']})
                continue
            aid = za if r['receipt_id'] in za_receipts else hsbc
            b = endpoint('receipt:' + r['receipt_id'], aid, receipt, receipt['date'], receipt['currency'], receipt['amount'], 'in')
            link('cmb:' + r['id'], a, b, r['amount_cny'], receipt['amount'], r)
        for r in matched['hsbc']:
            endpoint('hsbc:' + r['id'], hsbc, r, r['date'], r['currency'], r['amount'], r['direction'])
        for r in matched['za_hsbc']:
            receipt = hsbc_receipts.get(r.get('hsbc_id'))
            if not receipt:
                continue
            # Direction on the standalone external-transfer list is NOT trusted.
            outgoing = r['direction'] == 'ZA→HSBC'
            a = endpoint('zh:za:' + r['id'], za, r, r['date'], r['currency'], r['amount'],
                         'out' if outgoing else 'in', r['za_statement'], r['za_page'])
            b = endpoint('hsbc:' + receipt['id'], hsbc, receipt, receipt['date'], receipt['currency'], receipt['amount'], receipt['direction'])
            if outgoing:
                link('zh:' + r['id'], a, b, r['amount'], receipt['amount'], r)
            else:
                link('zh:' + r['id'], b, a, receipt['amount'], r['amount'], r)
    # Identical references may be encountered several times in matching data.
    result['observations'] = list({x['observation_id']: x for x in result['observations']}.values())
    validate_plan(result)
    return result


def validate_plan(plan):
    """Validate currency/sign and cumulative allocation on both transfer sides."""
    rows = {x['observation_id']: x for x in plan['observations']}
    allocated = {}
    for link in plan['links']:
        if link['debit_id'] == link['credit_id']:
            pdp.fail('Transfer cannot allocate a row to itself')
        for side, sign in (('debit', -1), ('credit', 1)):
            row = rows[link[side + '_id']]
            value = Decimal(amount(link[side + '_amount'], row['currency'], False))
            if value <= 0:
                pdp.fail('Transfer allocation must be positive')
            known = row['signed_amount']
            if known is not None and Decimal(known) * sign <= 0:
                pdp.fail('Transfer allocation conflicts with statement direction')
            total = allocated.get(row['observation_id'], Decimal(0)) + value
            maximum = abs(Decimal(known if known is not None else row['statement_amount']))
            if total > maximum:
                pdp.fail('Transfer allocations exceed the source amount')
            allocated[row['observation_id']] = total


def preview_bundle(path):
    return summarize_plan(parse_bundle(path))


def summarize_plan(plan):
    counts = {}
    for row in plan['observations']:
        counts[row['kind']] = counts.get(row['kind'], 0) + 1
    return {'status': 'preview', 'batch_id': plan['batch_id'], 'content_hash': plan['content_hash'],
            'documents': len(plan['documents']), 'accounts': len(plan['accounts']),
            'observations': counts, 'transfer_links': len(plan['links']),
            'warnings': plan['warnings'], 'journal_postings_created': 0,
            'note': 'Prepared evidence only; unknown directions and candidate trades remain pending.'}


def import_bundle(db, path, expected_hash=None):
    plan = parse_bundle(path)
    return import_plan(db, plan, expected_hash)


def parse_cash_csv(path, account_key, institution, display_name):
    """Portable adapter for any bank/broker. Decimal strings, ISO dates only.

    Required columns: date,currency and either signed_amount or statement_amount.
    Optional: description,balance,reference. An unsigned row remains pending.
    Account keys must identify the actual account, not just its institution.
    """
    for value in (account_key, institution, display_name):
        pdp.check_str(value, 'account identity', True, 255)
    raw = Path(path).read_bytes()
    if len(raw) > 10 * 1024 * 1024:
        pdp.fail('CSV exceeds 10 MiB')
    digest = hashlib.sha256(raw + pdp.canonical([account_key, institution, display_name])).hexdigest()
    aid, doc = ident('account:custom:' + account_key), ident('cash-csv:' + digest)
    plan = {'batch_id': ident('batch:' + digest), 'content_hash': digest,
            'accounts': {aid: (aid, institution, display_name, 'bank', 'owned', pdp.now())},
            'documents': [{'document_id': doc, 'relative_path': Path(path).name,
                           'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw),
                           'text_content': raw.decode('utf-8-sig')}],
            'observations': [], 'links': [], 'warnings': []}
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    fields = set(reader.fieldnames or [])
    if not {'date', 'currency'} <= fields or not fields & {'signed_amount', 'statement_amount'}:
        pdp.fail('CSV needs date,currency,signed_amount or statement_amount')
    occurrences, references = {}, set()
    for index, row in enumerate(reader, 1):
        ccy = mc.currency(row['currency'])
        signed = amount(row['signed_amount'], ccy) if row.get('signed_amount') else None
        unsigned = amount(row['statement_amount'], ccy, False) if row.get('statement_amount') else None
        if signed is None and unsigned is None:
            pdp.fail('Cash row has no amount')
        if signed is not None and unsigned is not None and abs(Decimal(signed)) != Decimal(unsigned):
            pdp.fail('Signed and unsigned amounts disagree')
        reference = row.get('reference')
        if reference and reference in references:
            pdp.fail('Duplicate reference in account CSV')
        if reference:
            references.add(reference)
        semantic = reference or hashlib.sha256(pdp.canonical(row)).hexdigest()
        occurrence = occurrences.get(semantic, 0)
        occurrences[semantic] = occurrence + 1
        plan['observations'].append({
            'observation_id': ident('cash-row:' + aid + ':' + semantic + ':' + str(occurrence)), 'document_id': doc,
            'row_key': str(index), 'account_id': aid, 'kind': 'cash', 'date': date(row['date']),
            'currency': ccy, 'signed_amount': signed, 'statement_amount': unsigned,
            'reported_balance': amount(row['balance'], ccy) if row.get('balance') else None,
            'description': row.get('description', ''),
            'source_locator': json.dumps({'file': Path(path).name, 'row': index}),
            'status': 'direction_known' if signed is not None else 'direction_unknown',
            'raw_json': json.dumps(row, ensure_ascii=False, sort_keys=True), 'review_reason': None})
    if not plan['observations']:
        pdp.fail('Empty CSV')
    return plan


def import_plan(db, plan, expected_hash=None):
    validate_plan(plan)
    if expected_hash is not None and expected_hash != plan['content_hash']:
        pdp.fail('Archive changed after preview')
    with closing(pdp.connect(db)) as c:
        _check(c)
        if not enabled(c):
            pdp.fail('Run statement-upgrade first')
        if c.execute('SELECT 1 FROM ld_statement_batches WHERE content_hash=?', (plan['content_hash'],)).fetchone():
            return {'status': 'already_imported', 'batch_id': plan['batch_id']}
    def mutate(c):
        c.execute('INSERT INTO ld_statement_batches VALUES (?,?,?,?)', (
            plan['batch_id'], plan['content_hash'], pdp.now(), 'Local three-bank statement review pack'))
        for aid, values in plan['accounts'].items():
            existing = c.execute('SELECT * FROM ld_accounts WHERE account_id=?', (aid,)).fetchone()
            if existing and tuple(existing)[:5] != values[:5]:
                pdp.fail('Account identity conflict')
            if not existing:
                c.execute('INSERT INTO ld_accounts VALUES (?,?,?,?,?,?)', values)
        for doc in plan['documents']:
            c.execute('INSERT OR IGNORE INTO ld_source_documents VALUES (?,?,?,?,?,?)', (
                doc['document_id'], plan['batch_id'], doc['relative_path'], doc['sha256'],
                doc['size_bytes'], doc['text_content']))
        for row in plan['observations']:
            old = c.execute('SELECT * FROM ld_observations WHERE observation_id=?', (row['observation_id'],)).fetchone()
            if old:
                if any(old[k] != row[k] for k in ('account_id', 'kind', 'date', 'currency',
                                                 'signed_amount', 'statement_amount', 'reported_balance', 'description')):
                    pdp.fail('Conflicting observation for an existing source reference')
            else:
                c.execute('INSERT INTO ld_observations VALUES (' + ','.join('?' * len(row)) + ')', tuple(row.values()))
        # Existing observations may already be allocated by an earlier overlapping pack.
        for link in plan['links']:
            if c.execute('SELECT 1 FROM ld_transfer_links WHERE link_id=?', (link['link_id'],)).fetchone():
                continue
            for side in ('debit', 'credit'):
                oid = link[side + '_id']
                row = c.execute('SELECT * FROM ld_effective_observations WHERE observation_id=?', (oid,)).fetchone()
                spent = sum((Decimal(x[0]) for x in c.execute(
                    "SELECT debit_amount FROM ld_effective_links WHERE debit_id=? AND certainty!='rejected' UNION ALL "
                    "SELECT credit_amount FROM ld_effective_links WHERE credit_id=? AND certainty!='rejected'", (oid, oid))), Decimal(0))
                if row['signed_amount'] is not None and (
                        (Decimal(row['signed_amount']) < 0) != (side == 'debit')):
                    pdp.fail('Transfer conflicts with reviewed cash direction')
                if spent + Decimal(link[side + '_amount']) > abs(Decimal(row['signed_amount'] or row['statement_amount'])):
                    pdp.fail('Overlapping transfer allocations require review')
            c.execute('INSERT INTO ld_transfer_links VALUES (' + ','.join('?' * len(link)) + ')', tuple(link.values()))
        return dict(summarize_plan(plan), status='imported')
    return _write(db, 'Import private statement observations (no journal posting)', mutate)


def read_connection(db):
    c = sqlite_connect_ro(db)
    try:
        c.execute('BEGIN')
        _check(c)
        if not enabled(c):
            pdp.fail('Statement extension not enabled')
        return c
    except Exception:
        c.close()
        raise


def sqlite_connect_ro(db):
    import sqlite3
    c = sqlite3.connect(Path(db).expanduser().resolve().as_uri() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA query_only=ON')
    return c


def append_review(c, target_id, decision, evidence, signed_amount=None):
    """Append a human evidence decision. Raw observations and matches never change."""
    pdp.check_uuid(target_id, 'target_id')
    pdp.check_str(evidence, 'review evidence', True, 4096)
    if decision not in {'direction', 'confirmed', 'likely', 'conflicting', 'rejected'}:
        pdp.fail('Unknown review decision')
    if not enabled(c):
        pdp.fail('Statement extension not enabled')
    obs_id, link_id, value = None, None, None
    if decision == 'direction':
        row = c.execute('SELECT * FROM ld_observations WHERE observation_id=?', (target_id,)).fetchone()
        if not row or row['kind'] != 'cash':
            pdp.fail('Direction reviews require a cash observation')
        value = amount(signed_amount, row['currency'])
        original = row['signed_amount'] if row['signed_amount'] is not None else row['statement_amount']
        if abs(Decimal(value)) != abs(Decimal(original)):
            pdp.fail('Direction review may change sign only; amount corrections require new evidence')
        for link in c.execute("SELECT debit_id,credit_id FROM ld_effective_links WHERE certainty='confirmed' "
                              'AND (debit_id=? OR credit_id=?)', (target_id, target_id)):
            if (link['debit_id'] == target_id and Decimal(value) >= 0
                    or link['credit_id'] == target_id and Decimal(value) <= 0):
                pdp.fail('Direction conflicts with a confirmed transfer; review that link first')
        obs_id = target_id
    else:
        if signed_amount is not None or not c.execute('SELECT 1 FROM ld_transfer_links WHERE link_id=?', (target_id,)).fetchone():
            pdp.fail('Link review requires an existing transfer and no signed amount')
        link = c.execute('SELECT * FROM ld_transfer_links WHERE link_id=?', (target_id,)).fetchone()
        if decision != 'rejected':
            for side in ('debit', 'credit'):
                oid = link[side + '_id']
                row = c.execute('SELECT * FROM ld_observations WHERE observation_id=?', (oid,)).fetchone()
                used = sum((Decimal(x[0]) for x in c.execute(
                    "SELECT debit_amount FROM ld_effective_links WHERE debit_id=? AND link_id!=? AND certainty!='rejected' "
                    "UNION ALL SELECT credit_amount FROM ld_effective_links WHERE credit_id=? AND link_id!=? AND certainty!='rejected'",
                    (oid, target_id, oid, target_id))), Decimal(0))
                if used + Decimal(link[side + '_amount']) > abs(Decimal(row['signed_amount'] or row['statement_amount'])):
                    pdp.fail('Review would reactivate an overallocated transfer')
        if decision == 'confirmed':
            for side, sign in (('debit', -1), ('credit', 1)):
                signed = c.execute('SELECT signed_amount FROM ld_effective_observations WHERE observation_id=?',
                                   (link[side + '_id'],)).fetchone()[0]
                if signed is not None and Decimal(signed) * sign <= 0:
                    pdp.fail('Cannot confirm a transfer with conflicting cash directions')
        link_id = target_id
    did = str(uuid.uuid4())
    c.execute('INSERT INTO ld_review_decisions VALUES (?,?,?,?,?,?,?,?)', (
        did, pdp.get_meta(c)['revision'] + 1, obs_id, link_id, decision, value, evidence, pdp.now()))
    return {'status': 'reviewed', 'decision_id': did, 'target_id': target_id, 'decision': decision}


def review(db, target_id, decision, evidence, signed_amount=None):
    return _write(db, 'Append a statement evidence review',
                  lambda c: append_review(c, target_id, decision, evidence, signed_amount))
