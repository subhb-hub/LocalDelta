"""PDP/3.0 additive double-entry cash ledger (v0.4 foundation).

No automatic bank connections or transfer matching. Each submitted journal event
must balance independently in *each currency*. Account balances are not complete
without independently verified opening balances.
"""
from __future__ import annotations

from decimal import Decimal
import datetime as dt
import hashlib
import json
import sqlite3
import uuid

import multicurrency as mc
import pdp

PROTOCOL = "PDP/3.0"
ACCOUNT_TABLE = "ledger_accounts"
EVENT_TABLE = "ledger_events"
TABLES = (ACCOUNT_TABLE, EVENT_TABLE)
SCHEMA_TABLES = ("ld_accounts", "ld_ledger_events", "ld_postings", "ld_ledger_migrations")
INDEX_NAMES = ("ld_postings_account_currency", "ld_postings_event", "ld_ledger_events_date")
ACCOUNT_TYPES = {"bank", "broker", "payment", "wallet", "cash", "clearing", "expense", "equity", "other"}
OWNERSHIP = {"owned", "third_party", "system"}
EVENT_KINDS = {"transfer", "deposit", "withdrawal", "fee", "fx", "trade", "income", "adjustment", "other"}
POSTING_ROLES = {"principal", "counterparty", "clearing", "fee", "expense", "equity", "settlement", "other"}
ACCOUNT_FIELDS = {"account_id", "institution", "display_name", "account_type", "ownership"}
EVENT_REQUIRED = {"event_id", "kind", "occurred_at", "source_ref", "postings"}
EVENT_OPTIONAL = {"booked_at", "description"}
POSTING_REQUIRED = {"posting_id", "account_id", "amount", "currency", "role"}
POSTING_OPTIONAL = {"posted_at"}


def enabled(c):
    found = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('ld_accounts','ld_ledger_events','ld_postings','ld_ledger_migrations')"
    )}
    if found and found != set(SCHEMA_TABLES):
        pdp.fail("Ledger 扩展表不完整，请恢复备份或人工修复")
    return len(found) == len(SCHEMA_TABLES)


def require_enabled(c):
    if not enabled(c):
        pdp.fail("此数据库未启用多账户账本，请先执行 ledger-upgrade")


def extension_ddl(c):
    if not enabled(c):
        return []
    ddls = []
    for name in SCHEMA_TABLES + INDEX_NAMES:
        row = c.execute(
            "SELECT sql FROM sqlite_master WHERE name=? AND type=?",
            (name, "index" if name in INDEX_NAMES else "table"),
        ).fetchone()
        if row is None or row[0] is None:
            pdp.fail("Ledger 表或索引缺失：" + name)
        ddls.append(row[0])
    return ddls


def create_tables(c):
    c.execute("""CREATE TABLE ld_accounts (
        account_id TEXT PRIMARY KEY,
        institution TEXT NOT NULL,
        display_name TEXT NOT NULL,
        account_type TEXT NOT NULL CHECK(account_type IN
            ('bank','broker','payment','wallet','cash','clearing','expense','equity','other')),
        ownership TEXT NOT NULL CHECK(ownership IN ('owned','third_party','system')),
        created_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE ld_ledger_events (
        event_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK(kind IN
            ('transfer','deposit','withdrawal','fee','fx','trade','income','adjustment','other')),
        occurred_at TEXT NOT NULL,
        occurred_utc TEXT NOT NULL,
        booked_at TEXT,
        booked_utc TEXT,
        source_ref TEXT NOT NULL,
        description TEXT,
        created_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE ld_postings (
        posting_id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL REFERENCES ld_ledger_events(event_id),
        account_id TEXT NOT NULL REFERENCES ld_accounts(account_id),
        amount TEXT NOT NULL,
        currency TEXT NOT NULL,
        role TEXT NOT NULL,
        posted_at TEXT,
        posted_utc TEXT
    )""")
    c.execute("""CREATE TABLE ld_ledger_migrations (
        version INTEGER PRIMARY KEY,
        migrated_at TEXT NOT NULL,
        previous_revision INTEGER NOT NULL,
        previous_schema TEXT NOT NULL,
        previous_data TEXT NOT NULL
    )""")
    c.execute("CREATE INDEX ld_postings_account_currency ON ld_postings(account_id,currency,event_id)")
    c.execute("CREATE INDEX ld_postings_event ON ld_postings(event_id)")
    c.execute("CREATE INDEX ld_ledger_events_date ON ld_ledger_events(occurred_utc,event_id)")


def upgrade(db):
    """Backup-first additive migration; older assets and transactions stay unchanged."""
    c = pdp.connect(db)
    try:
        m = pdp.get_meta(c)
        mc.require_enabled(c)
        if pdp.schema_fingerprint(c) != m["schema_digest"] or pdp.data_fingerprint(c) != m["data_digest"]:
            pdp.fail("升级前数据库存在未经 PDP 审计的变化")
        if enabled(c):
            return {"status": "already_upgraded", "revision": m["revision"]}
        pdp.check_db(c)
    finally:
        c.close()

    backup = pdp.fresh_backup(db, "before_ledger_upgrade")
    c = pdp.connect(db)
    try:
        c.execute("BEGIN IMMEDIATE")
        m = pdp.get_meta(c)
        if enabled(c):
            pdp.fail("其他进程已升级；请重新检查状态")
        if pdp.schema_fingerprint(c) != m["schema_digest"] or pdp.data_fingerprint(c) != m["data_digest"]:
            pdp.fail("备份后数据库发生变化，拒绝升级")
        create_tables(c)
        c.execute("INSERT INTO ld_ledger_migrations VALUES (?,?,?,?,?)",
                  (3, pdp.now(), m["revision"], m["schema_digest"], m["data_digest"]))
        pkg_id = str(uuid.uuid4())
        c.execute("INSERT INTO pdp_packages VALUES (?,?,?,?,?,?)",
                  (pkg_id,
                   hashlib.sha256(("LocalDelta:migration:v3:" + m["db_id"]).encode()).hexdigest(),
                   m["revision"], m["revision"] + 1, pdp.now(),
                   "Controlled additive multi-account ledger migration"))
        c.execute(
            "INSERT INTO pdp_operations (op_id,package_id,action,table_name,entity_id,old_json,new_json,reason,source_json,applied_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), pkg_id, "migrate", "ld_ledger_migrations", "3", None,
             json.dumps({"version": 3}), "enable double-entry ledger",
             json.dumps({"kind": "system", "reference": "controlled schema migration", "certainty": "confirmed"}),
             pdp.now()))
        schema_digest = pdp.schema_fingerprint(c)
        data_digest = pdp.data_fingerprint(c)
        c.execute("UPDATE pdp_meta SET revision=?,schema_digest=?,data_digest=? WHERE singleton=1",
                  (m["revision"] + 1, schema_digest, data_digest))
        pdp.check_db(c)
        c.execute("COMMIT")
        return {"status": "upgraded", "revision": m["revision"] + 1, "backup_path": backup}
    except Exception:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        c.close()


def _datetime(value, name):
    if not isinstance(value, str) or len(value) > 50:
        pdp.fail(name + " 必须为带时区 ISO 8601 时间字符串")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError("timezone required")
    except ValueError:
        pdp.fail(name + " 必须携带有效时区")
    return value


def _utc(value):
    if value is None:
        return None
    _datetime(value, "timestamp")
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        dt.timezone.utc).isoformat(timespec="microseconds")


def validate_op(op):
    """Strict field allowlist, exact currency scale and per-currency balance."""
    if op.get("table") not in TABLES or op.get("action") != "create":
        pdp.fail("Ledger v0.4 仅支持创建账户和不可变记账事件")
    if any(k in op for k in ("money", "money_before", "money_changes")):
        pdp.fail("Ledger 分录必须使用独立原币 amount/currency，不能使用交易 money 扩展")
    record = op.get("record")
    if not isinstance(record, dict):
        pdp.fail("Ledger record 必须是对象")
    if op["table"] == ACCOUNT_TABLE:
        if set(record) != ACCOUNT_FIELDS:
            pdp.fail("ledger_accounts 字段不符合白名单")
        pdp.check_uuid(record["account_id"], "account_id")
        pdp.check_str(record["institution"], "institution", True, 255)
        pdp.check_str(record["display_name"], "display_name", True, 255)
        if record["account_type"] not in ACCOUNT_TYPES or record["ownership"] not in OWNERSHIP:
            pdp.fail("无效的账户类型或所有权范围")
        return record

    if not EVENT_REQUIRED.issubset(record) or set(record) - (EVENT_REQUIRED | EVENT_OPTIONAL):
        pdp.fail("ledger_events 字段不符合白名单")
    pdp.check_uuid(record["event_id"], "event_id")
    if record["kind"] not in EVENT_KINDS:
        pdp.fail("无效经济事件类型")
    _datetime(record["occurred_at"], "occurred_at")
    if record.get("booked_at") is not None:
        _datetime(record["booked_at"], "booked_at")
    pdp.check_str(record["source_ref"], "source_ref", True, 512)
    if record.get("description") is not None:
        pdp.check_str(record["description"], "description", False, 1024)
    postings = record["postings"]
    if not isinstance(postings, list) or not 2 <= len(postings) <= 1000:
        pdp.fail("经济事件必须包含 2 至 1000 条分录")
    ids = set()
    totals = {}
    currencies = {}
    for item in postings:
        if not isinstance(item, dict) or not POSTING_REQUIRED.issubset(item) or set(item) - (POSTING_REQUIRED | POSTING_OPTIONAL):
            pdp.fail("分录字段不符合白名单")
        pdp.check_uuid(item["posting_id"], "posting_id")
        pdp.check_uuid(item["account_id"], "account_id")
        if item["posting_id"] in ids:
            pdp.fail("事件包含重复 posting_id")
        ids.add(item["posting_id"])
        code = mc.currency(item["currency"])
        amount = mc.parse_decimal(item["amount"], "posting.amount", scale=mc.MINOR_UNITS[code])
        if amount == 0:
            pdp.fail("分录金额不能为零")
        if item["role"] not in POSTING_ROLES:
            pdp.fail("无效的分录角色")
        if item.get("posted_at") is not None:
            _datetime(item["posted_at"], "posted_at")
        totals[code] = totals.get(code, Decimal(0)) + amount
        currencies[code] = currencies.get(code, 0) + 1
    if any(total != 0 for total in totals.values()):
        pdp.fail("复式分录必须按每种货币独立平衡：" + str(totals))
    if any(n < 2 for n in currencies.values()):
        pdp.fail("每种货币至少需要两条相互平衡的分录")
    return record


def apply_op(c, op, package_id):
    require_enabled(c)
    record = validate_op(op)
    table = op["table"]
    source_key = op["source_key"]
    if c.execute("SELECT 1 FROM pdp_source_keys WHERE source_key=?", (source_key,)).fetchone():
        pdp.fail("Ledger source_key 已存在，疑似重复来源")
    ts = pdp.now()
    if table == ACCOUNT_TABLE:
        ident = record["account_id"]
        c.execute("INSERT INTO ld_accounts VALUES (?,?,?,?,?,?)",
                  (ident, record["institution"], record["display_name"],
                   record["account_type"], record["ownership"], ts))
        summary = dict(record)
        audit_table = "ld_accounts"
    else:
        ident = record["event_id"]
        for item in record["postings"]:
            if not c.execute("SELECT 1 FROM ld_accounts WHERE account_id=?", (item["account_id"],)).fetchone():
                pdp.fail("分录引用不存在的 account_id")
        c.execute("INSERT INTO ld_ledger_events VALUES (?,?,?,?,?,?,?,?,?)",
                  (ident, record["kind"], record["occurred_at"], _utc(record["occurred_at"]),
                   record.get("booked_at"), _utc(record.get("booked_at")),
                   record["source_ref"], record.get("description"), ts))
        for item in record["postings"]:
            c.execute("INSERT INTO ld_postings VALUES (?,?,?,?,?,?,?,?)",
                      (item["posting_id"], ident, item["account_id"],
                       item["amount"], item["currency"], item["role"],
                       item.get("posted_at"), _utc(item.get("posted_at"))))
        summary = record
        audit_table = "ld_ledger_events"
    c.execute("INSERT INTO pdp_source_keys VALUES (?,?,?,?)", (source_key, audit_table, ident, package_id))
    c.execute(
        "INSERT INTO pdp_operations (op_id,package_id,action,table_name,entity_id,old_json,new_json,reason,source_json,applied_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (op["op_id"], package_id, "create", audit_table, ident, None,
         json.dumps(summary, ensure_ascii=False, sort_keys=True), None,
         json.dumps(op["source"], ensure_ascii=False, sort_keys=True), ts))
    return {"action": "create", "table": table, "id": ident,
            "title": record.get("display_name") or record.get("description") or record.get("kind")}


def _read_checked(db):
    """Dedicated read-only connection; reject unaudited changes before reporting."""
    uri = __import__("pathlib").Path(db).expanduser().resolve().as_uri() + "?mode=ro"
    c = sqlite3.connect(uri, uri=True)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        require_enabled(c)
        meta = pdp.get_meta(c)
        if pdp.schema_fingerprint(c) != meta["schema_digest"] or pdp.data_fingerprint(c) != meta["data_digest"]:
            pdp.fail("账本存在未审计变更，拒绝输出统计结果")
        return c
    except Exception:
        c.close()
        raise


def list_accounts(db, ownership=None):
    if ownership is not None and ownership not in OWNERSHIP:
        pdp.fail("无效 ownership 过滤器")
    c = _read_checked(db)
    try:
        sql = "SELECT account_id,institution,display_name,account_type,ownership FROM ld_accounts"
        args = ()
        if ownership:
            sql += " WHERE ownership=?"
            args = (ownership,)
        sql += " ORDER BY institution,display_name,account_id"
        return {"accounts": [dict(r) for r in c.execute(sql, args)]}
    finally:
        c.close()


def account_movements(db, account_id, start=None, end=None, limit=100, offset=0):
    pdp.check_uuid(account_id, "account_id")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        pdp.fail("limit 必须在 1–1000 之间")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        pdp.fail("offset 必须为非负整数")
    for name, value in (("start", start), ("end", end)):
        if value is not None:
            _datetime(value, name)
    if start and end and _utc(start) > _utc(end):
        pdp.fail("start 时间不得晚于 end")
    c = _read_checked(db)
    try:
        if not c.execute("SELECT 1 FROM ld_accounts WHERE account_id=?", (account_id,)).fetchone():
            pdp.fail("账户不存在")
        cond = ["p.account_id=?"]
        args = [account_id]
        if start:
            cond.append("COALESCE(p.posted_utc,e.booked_utc,e.occurred_utc)>=?")
            args.append(_utc(start))
        if end:
            cond.append("COALESCE(p.posted_utc,e.booked_utc,e.occurred_utc)<=?")
            args.append(_utc(end))
        sql = """SELECT p.posting_id,p.event_id,p.account_id,p.amount,p.currency,p.role,
                        COALESCE(p.posted_at,e.booked_at,e.occurred_at) AS effective_at,
                        COALESCE(p.posted_utc,e.booked_utc,e.occurred_utc) AS effective_utc,
                        e.kind,e.description,e.source_ref
                 FROM ld_postings p JOIN ld_ledger_events e ON p.event_id=e.event_id
                 WHERE """ + " AND ".join(cond) + " ORDER BY effective_utc, p.posting_id LIMIT ? OFFSET ?"
        args.extend([limit, offset])
        rows = [dict(r) for r in c.execute(sql, args)]
        return {"account_id": account_id, "entries": rows,
                "limit": limit, "offset": offset,
                "balance_complete": False,
                "note": "Cash movements only: opening balance and independent reconciliation are not yet implemented."}
    finally:
        c.close()


def transfer_timeline(db, start=None, end=None, account_ids=None, limit=200, offset=0):
    """Return evidence-backed journal arrows, not bank-statement reconciliation.

    Only simple events with exactly one owned debit and one owned credit become
    edges. Complex/split/unmatched cases are explicitly returned as unlinked
    event IDs, never guessed into a transfer.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 250:
        pdp.fail("limit 必须为 1–250")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        pdp.fail("offset 必须为非负整数")
    for label, value in (("start", start), ("end", end)):
        if value is not None:
            _datetime(value, label)
    if start and end and _utc(start) > _utc(end):
        pdp.fail("start 不得晚于 end")
    if account_ids is not None:
        if not isinstance(account_ids, list) or len(account_ids) > 250:
            pdp.fail("account_ids 必须为最多 250 个 UUID 的数组")
        for item in account_ids:
            pdp.check_uuid(item, "account_id")
        if len(set(account_ids)) != len(account_ids):
            pdp.fail("account_ids 不得重复")
    c = _read_checked(db)
    try:
        all_accounts = {r["account_id"]: dict(r) for r in c.execute(
            "SELECT account_id,institution,display_name,account_type,ownership FROM ld_accounts")}
        if account_ids is not None:
            if any(a not in all_accounts for a in account_ids):
                pdp.fail("账户筛选包含未知 ID")
            selected = set(account_ids)
        else:
            selected = {a for a, rec in all_accounts.items() if rec["ownership"] == "owned"}
        lanes = [
            {"account_id": a, "label": all_accounts[a]["display_name"],
             "institution": all_accounts[a]["institution"], "scope": all_accounts[a]["ownership"]}
            for a in sorted(selected)
        ]
        conditions = ["kind IN ('transfer','fx')"]
        params = []
        if start:
            conditions.append("occurred_utc>=?")
            params.append(_utc(start))
        if end:
            conditions.append("occurred_utc<=?")
            params.append(_utc(end))
        sql = ("SELECT event_id,kind,occurred_at,occurred_utc,booked_at,booked_utc,source_ref "
               "FROM ld_ledger_events WHERE " + " AND ".join(conditions) +
               " ORDER BY occurred_utc,event_id LIMIT ? OFFSET ?")
        events = [dict(row) for row in c.execute(sql, params + [limit, offset])]
        post_by_event = {e["event_id"]: [] for e in events}
        if events:
            placeholders = ",".join("?" for _ in events)
            for row in c.execute(
                "SELECT posting_id,event_id,account_id,amount,currency,role,posted_at,posted_utc "
                "FROM ld_postings WHERE event_id IN (" + placeholders + ") ORDER BY posting_id",
                [e["event_id"] for e in events]
            ):
                post_by_event[row["event_id"]].append(dict(row))
        edges = []
        unlinked = []
        for event in events:
            entries = post_by_event[event["event_id"]]
            negatives = [p for p in entries if Decimal(p["amount"]) < 0
                         and all_accounts[p["account_id"]]["ownership"] == "owned"]
            positives = [p for p in entries if Decimal(p["amount"]) > 0
                         and all_accounts[p["account_id"]]["ownership"] == "owned"]
            if len(negatives) != 1 or len(positives) != 1:
                unlinked.append({"event_id": event["event_id"],
                                 "reason": "complex_or_unmatched_requires_review"})
                continue
            a, b = negatives[0], positives[0]
            if a["account_id"] not in selected or b["account_id"] not in selected:
                continue
            fees = [{"amount": format(abs(Decimal(p["amount"])), "f"),
                     "currency": p["currency"]} for p in entries if p["role"] == "fee"]
            edges.append({
                "id": event["event_id"], "from_account_id": a["account_id"],
                "to_account_id": b["account_id"],
                "sent_at": a["posted_at"] or event["booked_at"] or event["occurred_at"],
                "received_at": b["posted_at"] or event["booked_at"] or event["occurred_at"],
                "sent_utc": a["posted_utc"] or event["booked_utc"] or event["occurred_utc"],
                "received_utc": b["posted_utc"] or event["booked_utc"] or event["occurred_utc"],
                "sent": {"amount": format(abs(Decimal(a["amount"])), "f"), "currency": a["currency"]},
                "received": {"amount": format(Decimal(b["amount"]), "f"), "currency": b["currency"]},
                "fees": fees, "kind": event["kind"], "status": "journal_recorded",
                "source_ref": event["source_ref"],
            })
        return {
            "schema": "LocalDeltaTimeline/0.1",
            "account_lanes": lanes, "edges": edges, "unlinked_events": unlinked,
            "limit": limit, "offset": offset, "complete": False,
            "warnings": [
                "Journal arrows are not independently reconciled bank transfers.",
                "Opening balances and partial statement matching are not yet implemented.",
            ],
        }
    finally:
        c.close()
