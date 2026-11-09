#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDP v1: conservative, offline, standard-library incremental SQLite importer.

No model-generated SQL is evaluated. Only three known tables and an allowlisted
set of operations are supported. Designed for the portfolio.sqlite3 export.
"""
import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import uuid
import zipfile

PROTOCOL = "PDP/1.0"
TABLES = ("assets", "transactions", "snapshots")
PK = {"assets": "asset_id", "transactions": "transaction_id", "snapshots": "snapshot_id"}
BASE_REQUIRED = {
    "assets": {"asset_id", "asset_name", "account", "currency"},
    "transactions": {"transaction_id", "asset_id", "kind", "account", "currency"},
    "snapshots": {"snapshot_id", "asset_id", "account", "currency"},
}
# Logical decimal columns: JSON transport MUST use string (e.g., "100.25").
DECIMALS = {
    "assets": {"position_quantity", "coupon_pct", "face_value_per_unit"},
    "transactions": {"total_paid_cny", "net_invested_cny", "fee_cny", "quantity", "unit_nav", "clean_price", "accrued_interest_per_unit", "full_price", "buy_ytm_pct", "cash_inflow_cny", "clean_amount_cny", "accrued_interest_total_cny"},
    "snapshots": {"market_value_cny", "quantity", "unit_price", "holding_pnl_cny", "cumulative_pnl_cny", "yesterday_pnl_cny", "interest_received_cny"},
}
MONEY = {"total_paid_cny", "net_invested_cny", "fee_cny", "cash_inflow_cny", "clean_amount_cny", "accrued_interest_total_cny", "market_value_cny", "holding_pnl_cny", "cumulative_pnl_cny", "yesterday_pnl_cny", "interest_received_cny"}
NONNEGATIVE = {"position_quantity", "face_value_per_unit", "quantity", "total_paid_cny", "net_invested_cny", "fee_cny", "cash_inflow_cny", "clean_amount_cny", "accrued_interest_total_cny", "market_value_cny"}
DATES = {
    "assets": {"maturity_date", "next_coupon_date", "quantity_verified_date"},
    "transactions": {"order_datetime", "confirmed_date", "nav_date"},
    "snapshots": {"screenshot_date", "official_valuation_date"},
}
NUMERIC_BOOLEANS = {"snapshots": {"needs_valuation_date_check"}}
BLOCKED_EDITS = {
    "assets": {"asset_id", "asset_name", "account", "currency"},
    "transactions": {"transaction_id", "asset_id", "account", "asset_name", "currency"},
    "snapshots": {"snapshot_id", "asset_id", "account", "asset_name", "currency"},
}
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
NUM_RE = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")
MAX_OPS = 1000
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_ZIP_BYTES = 120 * 1024 * 1024

class PDPError(Exception):
    pass


def fail(message):
    raise PDPError(message)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def unique_object(pairs):
    ret = {}
    for k, v in pairs:
        if k in ret:
            fail("JSON 包含重复键: " + k)
        ret[k] = v
    return ret


def json_parse(binary):
    if len(binary) > MAX_JSON_BYTES:
        fail("JSON 超过大小限制 4 MB")
    try:
        return json.loads(binary.decode("utf-8"), object_pairs_hook=unique_object,
                          parse_constant=lambda x: fail("禁止 NaN 或 Infinity"))
    except UnicodeDecodeError as e:
        fail("JSON 必须是 UTF-8: " + str(e))


def canonical(obj):
    # PDP-specific Python JSON canonicalization; not a claim of RFC 8785 compliance.
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def package_digest(pkg):
    return hashlib.sha256(canonical({k: v for k, v in pkg.items() if k != "content_hash"})).hexdigest()


def load_package(path, evidence_dir=None):
    path = Path(path)
    if not path.is_file():
        fail("找不到更新包: " + str(path))
    evidence = {}
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            names = [x.filename for x in infos if not x.is_dir()]
            if len(names) != len(set(names)):
                fail("ZIP 中包含重名路径")
            if "package.json" not in names:
                fail("ZIP 缺少 package.json")
            if any(not (n == "package.json" or (n.startswith("evidence/") and n.count("/") == 1)) for n in names):
                fail("ZIP 只允许 package.json 和 evidence/ 中的文件")
            if sum(x.file_size for x in infos) > MAX_ZIP_BYTES or any(x.file_size > MAX_ZIP_BYTES for x in infos):
                fail("ZIP 解压内容过大")
            pkg = json_parse(z.read("package.json"))
            for x in pkg.get("evidence", []):
                if not isinstance(x, dict):
                    fail("证据列表含非法对象")
                name = x.get("path", "")
                if name not in names or not name.startswith("evidence/"):
                    fail("未找到证据文件: " + name)
                actual = hashlib.sha256(z.read(name)).hexdigest()
                if actual != x.get("sha256"):
                    fail("证据 SHA-256 不匹配: " + name)
                evidence[name] = actual
            other_evidence = {n for n in names if n != "package.json"}
            if other_evidence != set(evidence):
                fail("ZIP 中存在未登记的证据文件")
    else:
        pkg = json_parse(path.read_bytes())
        if pkg.get("evidence"):
            if not evidence_dir:
                fail("本更新包要求证据文件，请提供 --evidence-dir 或将证据封装成 ZIP")
            base = Path(evidence_dir).resolve()
            for x in pkg["evidence"]:
                if not isinstance(x, dict):
                    fail("证据列表含非法对象")
                n = x.get("path", "")
                if not n.startswith("evidence/") or n.count("/") != 1:
                    fail("无效证据相对路径: " + n)
                file = (base / n).resolve()
                if base not in file.parents or not file.is_file():
                    fail("缺少证据文件: " + n)
                if file.stat().st_size > MAX_ZIP_BYTES:
                    fail("证据文件过大")
                actual = hashlib.sha256(file.read_bytes()).hexdigest()
                if actual != x.get("sha256"):
                    fail("证据 SHA-256 不匹配: " + n)
                evidence[n] = actual
    return pkg, evidence


def check_uuid(s, field):
    if not isinstance(s, str):
        fail(field + " 应为 UUID 字符串")
    try:
        uuid.UUID(s)
    except (ValueError, AttributeError):
        fail(field + " 不是有效 UUID: " + repr(s))


def check_str(v, field, required=False, limit=4096):
    if v is None:
        if required: fail(field + " 不可为空")
        return
    if not isinstance(v, str) or (required and not v.strip()) or len(v) > limit:
        fail(field + " 必须是合适长度的字符串")


def dec(v, column):
    if v is None:
        return None
    if not isinstance(v, str) or not NUM_RE.fullmatch(v):
        fail(column + " 必须是普通十进制字符串，例如 \"100.25\"，不可传 JSON 数字")
    try:
        d = Decimal(v)
    except InvalidOperation:
        fail(column + " 的十进制格式无效")
    if not d.is_finite() or abs(d) > Decimal("999999999999999"):
        fail(column + " 数值超出允许范围")
    if abs(d.as_tuple().exponent) > 10:
        fail(column + " 小数精度最多支持 10 位")
    if column in MONEY and d.as_tuple().exponent < -2:
        fail(column + " 是人民币金额字段，最多两位小数")
    if column in NONNEGATIVE and d < 0:
        fail(column + " 不允许负数")
    return d


def validate_date(v, col):
    if v is None: return
    check_str(v, col, required=True, limit=40)
    try:
        if col == "order_datetime" and "T" in v:
            d = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
            if d.utcoffset() is None:
                fail("order_datetime 时间需要明确时区")
        else:
            dt.date.fromisoformat(v)
    except (ValueError, TypeError) as e:
        fail(col + " 日期格式错误: " + str(e))


def table_columns(c, table):
    return {r[1] for r in c.execute('PRAGMA table_info("' + table + '")')}


def schema_fingerprint(c):
    ddls = []
    for t in TABLES:
        r = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
        if not r:
            fail("数据库缺少基础表: " + t)
        if not BASE_REQUIRED[t].issubset(table_columns(c, t)):
            fail("基础表缺少必要字段: " + t)
        ddls.append(r[0])
    # Include approved additive multi-currency schema, without changing old v1 fingerprints.
    from multicurrency import extension_ddl
    ddls.extend(extension_ddl(c))
    return hashlib.sha256(canonical(ddls)).hexdigest()


def data_fingerprint(c):
    """Detect out-of-band changes to the operational data and PDP audit state."""
    order = {
        "assets": "asset_id", "transactions": "transaction_id", "snapshots": "snapshot_id",
        "pdp_packages": "package_id", "pdp_operations": "op_id",
        "pdp_source_keys": "source_key", "pdp_voids": "table_name,entity_id",
        "pdp_exact_values": "table_name,entity_id,column_name",
    }
    from multicurrency import enabled
    if enabled(c):
        order.update({
            "ld_currency_amounts":"entity_table,entity_id,role",
            "ld_fx_rates":"rate_id",
            "ld_mc_migrations":"version",
        })
    h = hashlib.sha256()
    for t, col in order.items():
        h.update(t.encode("ascii"))
        for row in c.execute(f'SELECT * FROM "{t}" ORDER BY {col}'):
            h.update(canonical(dict(row)))
            h.update(b"\n")
    return h.hexdigest()


def check_db(c, strict_foreign=True):
    r = c.execute("PRAGMA integrity_check").fetchall()
    if [x[0] for x in r] != ["ok"]:
        fail("SQLite integrity_check 失败: " + repr(r[:4]))
    if strict_foreign:
        fk = c.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            fail("数据库发现悬空外键: " + repr(fk[:4]))


def connect(db):
    db = Path(db)
    if not db.is_file():
        fail("找不到 SQLite 数据库: " + str(db))
    c = sqlite3.connect(str(db), timeout=8, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=8000")
    c.execute("PRAGMA foreign_keys=ON")
    if c.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        fail("SQLite 外键校验无法启用")
    return c


def get_meta(c):
    if "pdp_meta" not in {x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        fail("该数据库尚未运行 PDP 初始化。请先执行: python pdp.py init --db <数据库>")
    x = c.execute("SELECT db_id, revision, schema_digest, data_digest FROM pdp_meta WHERE singleton=1").fetchone()
    if not x:
        fail("PDP 元数据损坏或缺失")
    return dict(x)


def fresh_backup(db, label):
    db = Path(db).resolve()
    dest_dir = db.parent / "pdp_backups"
    dest_dir.mkdir(exist_ok=True, parents=True)
    suffix = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = dest_dir / (db.stem + "_" + label + "_" + suffix + ".sqlite3")
    src = connect(db)
    try:
        target = sqlite3.connect(str(path))
        try:
            src.backup(target)
            target.commit()
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                fail("备份数据库一致性检查失败")
        finally:
            target.close()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        src.close()
    return str(path)


def command_init(args):
    c = connect(args.db)
    try:
        check_db(c)
        digest = schema_fingerprint(c)
        if "pdp_meta" in {x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
            existing=get_meta(c)
            if data_fingerprint(c)!=existing["data_digest"]:
                fail("数据库在 PDP 之外被修改，初始化检查失败")
            print("已初始化，不执行重复初始化。现有状态:")
            print(json.dumps(existing, ensure_ascii=False, indent=2))
            return
    finally:
        c.close()
    backup = fresh_backup(args.db, "before_pdp_init")
    c = connect(args.db)
    try:
        c.execute("BEGIN IMMEDIATE")
        if "pdp_meta" in {x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
            fail("其他操作已初始化数据库，请重试状态检查")
        c.execute("CREATE TABLE pdp_meta (singleton INTEGER PRIMARY KEY CHECK (singleton=1), db_id TEXT NOT NULL UNIQUE, revision INTEGER NOT NULL CHECK(revision>=0), schema_digest TEXT NOT NULL, data_digest TEXT NOT NULL)")
        c.execute("CREATE TABLE pdp_packages (package_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, previous_revision INTEGER NOT NULL, applied_revision INTEGER NOT NULL, applied_at TEXT NOT NULL, summary TEXT)")
        c.execute("CREATE TABLE pdp_operations (op_id TEXT PRIMARY KEY, package_id TEXT NOT NULL REFERENCES pdp_packages(package_id), action TEXT NOT NULL, table_name TEXT NOT NULL, entity_id TEXT NOT NULL, old_json TEXT, new_json TEXT, reason TEXT, source_json TEXT, applied_at TEXT NOT NULL)")
        c.execute("CREATE TABLE pdp_source_keys (source_key TEXT PRIMARY KEY, table_name TEXT NOT NULL, entity_id TEXT NOT NULL, package_id TEXT NOT NULL REFERENCES pdp_packages(package_id))")
        c.execute("CREATE TABLE pdp_voids (table_name TEXT NOT NULL, entity_id TEXT NOT NULL, package_id TEXT NOT NULL REFERENCES pdp_packages(package_id), void_reason TEXT NOT NULL, PRIMARY KEY(table_name,entity_id))")
        c.execute("CREATE TABLE pdp_exact_values (table_name TEXT NOT NULL, entity_id TEXT NOT NULL, column_name TEXT NOT NULL, decimal_value TEXT NOT NULL, PRIMARY KEY(table_name,entity_id,column_name))")
        c.execute("CREATE VIEW pdp_active_transactions AS SELECT t.* FROM transactions t WHERE NOT EXISTS (SELECT 1 FROM pdp_voids v WHERE v.table_name='transactions' AND v.entity_id=t.transaction_id)")
        c.execute("CREATE VIEW pdp_active_snapshots AS SELECT s.* FROM snapshots s WHERE NOT EXISTS (SELECT 1 FROM pdp_voids v WHERE v.table_name='snapshots' AND v.entity_id=s.snapshot_id)")
        new_id = str(uuid.uuid4())
        initial_data_digest = data_fingerprint(c)
        c.execute("INSERT INTO pdp_meta VALUES (1,?,?,?,?)", (new_id, 0, digest, initial_data_digest))
        check_db(c)
        c.execute("COMMIT")
        print(json.dumps({"ok":True,"db_id":new_id,"revision":0,"schema_fingerprint":digest,"pre_init_backup":backup}, ensure_ascii=False, indent=2))
    except Exception:
        if c.in_transaction: c.execute("ROLLBACK")
        raise
    finally:
        c.close()


def command_context(args):
    c = connect(args.db)
    try:
        m = get_meta(c)
        if schema_fingerprint(c) != m["schema_digest"]:
            fail("数据库核心表结构变更，需要适配器迁移，不允许自动继续")
        if data_fingerprint(c) != m["data_digest"]:
            fail("检测到未通过 PDP 记录的数据库修改，请恢复备份或执行受控迁移")
        assets = [dict(r) for r in c.execute("SELECT asset_id, asset_name, account, currency, asset_type, status FROM assets ORDER BY account,asset_name")]
        refs = [dict(r) for r in c.execute("SELECT transaction_id,account,transaction_ref,kind,order_datetime FROM transactions WHERE transaction_ref IS NOT NULL AND transaction_ref<>''")]
        d = {"protocol":PROTOCOL,"database_id":m["db_id"],"revision":m["revision"],"schema_fingerprint":m["schema_digest"],"data_fingerprint":m["data_digest"],"asset_catalog":assets,"existing_transaction_refs":refs,"latest_packages":[dict(r) for r in c.execute("SELECT package_id,applied_revision,applied_at FROM pdp_packages ORDER BY applied_revision DESC LIMIT 15")],"disclaimer":"无余额/持仓金额。此上下文仍包含资产名称和交易编号，请仅发送给可信的处理方。"}
        out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print("⚠ 私有上下文：此文件包含资产名称、资产 ID 与交易编号。不要作为匿名上下文上传云端；云端协作请用 localdelta.py cloud-context。输出: "+str(out))
        print("资产 %d 项，当前修订版本 %d"%(len(assets),m["revision"]))
    finally:c.close()


def validate_pkg(pkg, evidence):
    if not isinstance(pkg, dict): fail("更新包根节点必须是 JSON 对象")
    allowed = {"protocol","package_id","database_id","base_revision","schema_fingerprint","base_data_fingerprint","created_at","description","operations","evidence","content_hash"}
    extra = set(pkg)-allowed
    if extra: fail("更新包未知字段: "+repr(sorted(extra)))
    from multicurrency import PROTOCOL as PDP_V2, validate_op as validate_v2_op
    v2=pkg.get("protocol")==PDP_V2
    if pkg.get("protocol") not in (PROTOCOL,PDP_V2): fail("协议版本不兼容")
    for k in ("package_id","database_id"):
        check_uuid(pkg.get(k),k)
    if not isinstance(pkg.get("base_revision"),int) or isinstance(pkg.get("base_revision"),bool) or pkg["base_revision"]<0:
        fail("base_revision 必须是非负整数")
    if not isinstance(pkg.get("schema_fingerprint"),str) or not SHA_RE.fullmatch(pkg["schema_fingerprint"]):
        fail("schema_fingerprint 无效")
    if not isinstance(pkg.get("base_data_fingerprint"),str) or not SHA_RE.fullmatch(pkg["base_data_fingerprint"]):
        fail("缺少或无效的 base_data_fingerprint（请用最新 context.json）")
    if not isinstance(pkg.get("content_hash"),str) or not SHA_RE.fullmatch(pkg["content_hash"]):
        fail("缺少完整 content_hash")
    if pkg["content_hash"]!=package_digest(pkg): fail("package SHA-256 不匹配，内容已变化")
    check_str(pkg.get("created_at"),"created_at",True,50)
    try:
        if dt.datetime.fromisoformat(pkg["created_at"].replace("Z","+00:00")).utcoffset() is None:
            fail("created_at 必须包含时区")
    except ValueError:fail("created_at 格式无效")
    check_str(pkg.get("description"),"description",False)
    ops=pkg.get("operations")
    if not isinstance(ops,list) or not 1<=len(ops)<=MAX_OPS:fail("operations 必须包含 1-1000 项")
    seen=set()
    for i,o in enumerate(ops):
        if not isinstance(o,dict):fail(f"第 {i+1} 项操作必须为对象")
        allowed_ops={"op_id","action","table","record","id","before","changes","source_key","source","reason"}
        if v2: allowed_ops|={"money","money_before","money_changes"}
        if set(o)-allowed_ops:
            fail(f"第 {i+1} 项包含未知操作字段")
        check_uuid(o.get("op_id"),"op_id")
        if o["op_id"] in seen:fail("同一更新包有重复 op_id")
        seen.add(o["op_id"])
        if o.get("table") not in (TABLES+("fx_rates",) if v2 else TABLES):
            fail("不允许操作未知表: "+str(o.get("table")))
        if o.get("action") not in ("create","correct","void"):fail("不支持的操作")
        source=o.get("source")
        if not isinstance(source,dict) or set(source)!={"kind","reference","certainty"}:
            fail("source 必须包含且仅包含 kind、reference、certainty")
        if source.get("kind") not in ("screenshot","statement","csv","manual","other"):
            fail("来源类型无效")
        check_str(source.get("reference"),"source.reference",True,512)
        if source.get("certainty")!="confirmed":fail("存在未确认数据：请先人工复核，不允许导入")
        if o["action"] == "create":
            if "id" in o or "before" in o or "changes" in o or "money_before" in o or "money_changes" in o:
                fail("create 操作不可包含 id / before / changes / money_before / money_changes")
            if not isinstance(o.get("record"),dict):fail("create 需要 record")
            check_str(o.get("source_key"),"source_key",True,255)
            if len(o["source_key"].strip())<6:fail("source_key 太短，无法可靠去重")
        else:
            if "record" in o or "source_key" in o:fail("修正/作废不可包含 record/source_key")
            check_uuid(o.get("id"),"目标记录 ID")
            if not isinstance(o.get("before"),dict):
                fail("修正/作废必须提供 before 前置条件对象")
            if o["action"]=="correct":
                if not isinstance(o.get("changes"),dict):
                    fail("修正操作必须提供 changes 对象")
                has_money=v2 and bool(o.get('money_changes'))
                if not o["changes"] and not has_money:
                    fail("correct 需要实际字段修改")
                if not o['before'] and not has_money:
                    fail("correct 缺少旧值前置条件")
                if set(o["before"])!=set(o["changes"]):
                    fail("correct 的 before 必须逐字段覆盖 changes")
            elif not o['before']:
                fail('void 必须提供 before 非空前置条件')
            elif "changes" in o:
                fail("void 不允许 changes")
            check_str(o.get("reason"),"reason",True,512)
            if o["action"]=="void" and o["table"]=="assets":
                fail("不支持直接作废资产档案（避免关联孤儿）")
        if v2: validate_v2_op(o)
    ev=pkg.get("evidence",[])
    if not isinstance(ev,list):fail("evidence 必须为数组")
    ev_names=set()
    for e in ev:
        if not isinstance(e,dict) or set(e)!={"path","sha256"}:
            fail("证据项目必须包含 path、sha256")
        n=e["path"]
        if not isinstance(n,str) or not n.startswith("evidence/") or n.count("/")!=1 or n in ev_names:
            fail("证据路径无效或重复")
        ev_names.add(n)
        if not isinstance(e["sha256"],str) or not SHA_RE.fullmatch(e["sha256"]):
            fail("证据 SHA256 无效")
    if set(evidence)!=ev_names:fail("没有完整验证所有证据")


def get_row(c,table,pk):
    col=PK[table]
    row=c.execute(f'SELECT * FROM "{table}" WHERE "{col}"=?',(pk,)).fetchone()
    return dict(row) if row else None


def comparable(v, col, table):
    if v is None: return None
    if col in DECIMALS[table]:
        if isinstance(v,str):
            dec(v,col)
        elif isinstance(v,(float,int)) and not isinstance(v,bool):
            pass
        else:fail("数值前置条件类型错误: "+col)
        return Decimal(str(v))
    if col in NUMERIC_BOOLEANS.get(table,set()):
        if isinstance(v,bool):return int(v)
        if isinstance(v,int) and v in (0,1):return v
        fail("布尔前置条件类型错误")
    return v


def field_value(v, col, table):
    if v is None:return None
    if col in DECIMALS[table]:
        d=dec(v,col)
        return float(d)
    if col in NUMERIC_BOOLEANS.get(table,set()):
        if not isinstance(v,bool):fail(col+" 必须是布尔值 true/false")
        return int(v)
    if col in DATES.get(table,set()): validate_date(v,col)
    elif not isinstance(v,str):fail(col+" 必须是字符串或 null")
    elif len(v)>10000:fail(col+" 字符串过长")
    return v


def validate_columns(c,table,data):
    if not isinstance(data,dict) or not data:fail("字段集不能为空")
    actual=table_columns(c,table)
    extra=set(data)-actual
    if extra:fail("未识别的字段 "+str(table)+": "+repr(sorted(extra)))
    for k,v in data.items():
        field_value(v,k,table)


def validate_business_rules(table, rec):
    """Cross-field checks on a projected complete record, never guessing missing data."""
    if table == "assets":
        curr = rec.get("currency")
        if not isinstance(curr, str) or not re.fullmatch(r"[A-Z]{3}", curr):
            fail("资产币种必须为大写三字母 ISO 4217 代码，例如 CNY/USD/HKD")
    if table == "transactions":
        fee, paid, net = (rec.get(k) for k in ("fee_cny", "total_paid_cny", "net_invested_cny"))
        if all(x is not None for x in (fee, paid, net)):
            if abs(Decimal(str(paid))-Decimal(str(net))-Decimal(str(fee))) > Decimal("0.01000001"):
                fail("基金交易金额核对失败：总支付 ≠ 净投入 + 手续费")
        clean, accrued, full = (rec.get(k) for k in ("clean_price", "accrued_interest_per_unit", "full_price"))
        if all(x is not None for x in (clean, accrued, full)):
            if abs(Decimal(str(clean))+Decimal(str(accrued))-Decimal(str(full))) > Decimal("0.00011"):
                fail("债券交易净价 + 每份应计利息 ≠ 全价")
        qty = rec.get("quantity")
        if all(x is not None for x in (qty, full, paid)):
            expected_paid = Decimal(str(qty))*Decimal(str(full)) + (Decimal(str(fee)) if fee is not None else Decimal("0"))
            if abs(expected_paid-Decimal(str(paid))) > Decimal("0.025"):
                fail("债券交易金额校验失败：成交份额 × 全价 + 单列手续费 ≠ 总支付金额")
    if table == "snapshots":
        if rec.get("needs_valuation_date_check") == 0 and rec.get("official_valuation_date") is None:
            fail("未填写正式估值日期，不得标记为已核对估值日")


def find_dupe(c,table,record):
    if table=="assets":
        return c.execute("SELECT asset_id FROM assets WHERE account=? AND asset_name=?",(record.get("account"),record.get("asset_name"))).fetchone()
    if table=="transactions":
        ref=record.get("transaction_ref")
        if ref:
            return c.execute("SELECT transaction_id FROM transactions WHERE account=? AND transaction_ref=?",(record.get("account"),ref)).fetchone()
        return c.execute("SELECT transaction_id FROM transactions WHERE asset_id=? AND account=? AND kind=? AND order_datetime IS ? AND total_paid_cny IS ? AND quantity IS ? AND cash_inflow_cny IS ?", (record.get("asset_id"),record.get("account"),record.get("kind"),record.get("order_datetime"),record.get("total_paid_cny"),record.get("quantity"),record.get("cash_inflow_cny"))).fetchone()
    # Multiple capture times within same day are deliberately not silently merged.
    date=record.get("screenshot_date")
    if date is not None:
        return c.execute("SELECT snapshot_id FROM snapshots WHERE asset_id=? AND screenshot_date=?",(record.get("asset_id"),date)).fetchone()
    # Unknown date cannot be safely distinguished from existing unknown-date row.
    return c.execute("SELECT snapshot_id FROM snapshots WHERE asset_id=? AND screenshot_date IS NULL AND official_valuation_date IS ?",(record.get("asset_id"),record.get("official_valuation_date"))).fetchone()


def live_row(c,table,pk):
    if table in ("transactions","snapshots") and c.execute("SELECT 1 FROM pdp_voids WHERE table_name=? AND entity_id=?",(table,pk)).fetchone():
        fail(f"记录已作废，不可继续更新: {table}/{pk}")
    r=get_row(c,table,pk)
    if not r:fail(f"找不到目标记录: {table}/{pk}")
    return r


def relate(c,table,record):
    if table=="assets":
        return
    a=c.execute("SELECT asset_name,account,currency FROM assets WHERE asset_id=?",(record.get("asset_id"),)).fetchone()
    if not a:fail("引用了不存在的资产: "+str(record.get("asset_id")))
    if record.get("account") != a["account"]:fail("交易/估值所属账户与资产档案不一致")
    if record.get("currency") != a["currency"]:fail("交易/估值币种与资产档案不一致；外汇交易需独立建模")
    if record.get("asset_name") is None:
        record["asset_name"] = a["asset_name"]
    elif record.get("asset_name") != a["asset_name"]:
        fail("冗余资产名称与档案不一致")
    if record.get("currency") != "CNY":
        # Historical schema is CNY-only for numeric money fields.
        for k in MONEY:
            if k in record and record[k] is not None:
                fail("旧数据库的货币金额字段以 _cny 命名，不能把外币金额写进去；需要升级币种适配器")


def all_equal(actual,expected,table):
    for col,v in expected.items():
        if col not in actual: fail("前置检查包含未知字段: "+col)
        if comparable(actual[col],col,table)!=comparable(v,col,table):
            fail(f"乐观锁冲突 {table}.{col}: 当前 {actual[col]!r} 与期望 {v!r} 不一致")


def record_exact(c,table,pk,changes):
    for k,v in changes.items():
        if k in DECIMALS[table]:
            if v is None:
                c.execute("DELETE FROM pdp_exact_values WHERE table_name=? AND entity_id=? AND column_name=?",(table,pk,k))
            else:
                c.execute("INSERT INTO pdp_exact_values(table_name,entity_id,column_name,decimal_value) VALUES(?,?,?,?) ON CONFLICT(table_name,entity_id,column_name) DO UPDATE SET decimal_value=excluded.decimal_value",(table,pk,k,v))


def execute_op(c,o,pkg_id,protocol=PROTOCOL):
    from multicurrency import (PROTOCOL as PDP_V2, apply_fx, prepare_create,
                               after_core_op, enabled)
    table=o["table"];action=o["action"]
    if table=='fx_rates':
        if protocol != PDP_V2: fail('汇率记录只能使用 PDP/2.0')
        return apply_fx(c,o,pkg_id)
    # Make CNY compatibility columns while keeping all original-currency amounts.
    original_op=o
    if protocol==PDP_V2 and action=='create':o=prepare_create(o)
    col=PK[table];ts=now()
    if action=="create":
        input_rec=o["record"]
        validate_columns(c,table,input_rec)
        if not BASE_REQUIRED[table].issubset(input_rec):
            fail("create 缺少必要字段: "+str(BASE_REQUIRED[table]-set(input_rec)))
        pk=input_rec[col]
        check_uuid(pk,col)
        if get_row(c,table,pk):fail("记录 ID 已存在: "+pk)
        if c.execute("SELECT 1 FROM pdp_source_keys WHERE source_key=?",(o["source_key"],)).fetchone():
            fail("source_key 已导入过，疑似重复导入: "+o["source_key"])
        rec={k:field_value(v,k,table) for k,v in input_rec.items()}
        relate(c,table,rec)
        if table=="assets":
            for k in ("asset_name","account","currency"):
                check_str(rec.get(k),k,True,255)
        if table=="transactions":
            check_str(rec.get("kind"),"kind",True,30)
            if rec.get("total_paid_cny") is None and rec.get("cash_inflow_cny") is None and not (
                    protocol==PDP_V2 and original_op.get('money') and
                    any(k in original_op['money'] for k in ('cash_paid','cash_inflow'))):
                fail("交易至少应包含支付金额或现金流入金额")
            if not rec.get("order_datetime"):
                fail("交易必须包含 order_datetime")
        if table=="snapshots":
            if rec.get("market_value_cny") is None and rec.get("quantity") is None and not (
                    protocol==PDP_V2 and 'market_value' in original_op.get('money',{})):
                fail("估值快照至少应包含市值或数量")
        validate_business_rules(table,rec)
        duplicate=find_dupe(c,table,rec)
        if duplicate:
            fail(f"可能重复的现存 {table} 记录: {duplicate[0]}（不会自动覆盖）")
        keys=list(rec); sql=f'INSERT INTO "{table}" ('+','.join('"'+k+'"' for k in keys)+') VALUES ('+','.join('?' for _ in keys)+')'
        c.execute(sql,[rec[k] for k in keys])
        c.execute("INSERT INTO pdp_source_keys VALUES (?,?,?,?)",(o["source_key"],table,pk,pkg_id))
        record_exact(c,table,pk,input_rec)
        before=None;after=get_row(c,table,pk)
    else:
        pk=o["id"];before=live_row(c,table,pk)
        if o['before']:validate_columns(c,table,o["before"])
        all_equal(before,o["before"],table)
        if action=="correct":
            ch=o["changes"]
            if ch:validate_columns(c,table,ch)
            if set(ch)&BLOCKED_EDITS[table]:
                fail("不允许修改稳定 ID、资产身份或冗余关联字段。请走人工迁移流程。")
            rec={k:field_value(v,k,table) for k,v in ch.items()}
            if any(comparable(before[k],k,table)==comparable(v,k,table) for k,v in ch.items()):
                fail("correct 包含未发生变化的字段，请移除无效修改")
            if table in ("transactions","snapshots") and before.get("currency")!="CNY" and any(k in MONEY for k in ch):
                fail("非人民币记录不可修改以 _cny 命名的金额字段")
            full_projected=dict(before)
            full_projected.update(rec)
            validate_business_rules(table,full_projected)
            keys=list(rec)
            if keys:
                c.execute(f'UPDATE "{table}" SET '+','.join('"'+k+'"=?' for k in keys)+f' WHERE "{col}"=?',[rec[k] for k in keys]+[pk])
                record_exact(c,table,pk,ch)
            after=get_row(c,table,pk)
        else:
            c.execute("INSERT INTO pdp_voids VALUES (?,?,?,?)",(table,pk,pkg_id,o["reason"]))
            after={"voided":True,"record":before}
    c.execute("INSERT INTO pdp_operations(op_id,package_id,action,table_name,entity_id,old_json,new_json,reason,source_json,applied_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(o["op_id"],pkg_id,action,table,pk,json.dumps(before,ensure_ascii=False,sort_keys=True) if before is not None else None,json.dumps(after,ensure_ascii=False,sort_keys=True),o.get("reason"),json.dumps(o["source"],ensure_ascii=False,sort_keys=True),ts))
    if enabled(c):after_core_op(c,original_op,{'id':pk})
    return {"action":action,"table":table,"id":pk,"title":(after or {}).get("asset_name") or (before or {}).get("asset_name")}


def process(c,pkg,commit=False):
    from multicurrency import PROTOCOL as PDP_V2, require_enabled
    if pkg['protocol']==PDP_V2:require_enabled(c)
    m=get_meta(c)
    if schema_fingerprint(c)!=m["schema_digest"]:
        fail("目标数据库基础表结构变化，禁止无迁移导入")
    if data_fingerprint(c)!=m["data_digest"]:
        fail("发现未通过 PDP 记录的数据库修改（数据指纹不匹配）。拒绝导入。")
    if pkg["database_id"]!=m["db_id"]:
        fail("更新包指向另一份数据库（database_id 不匹配）")
    if pkg["schema_fingerprint"]!=m["schema_digest"]:
        fail("更新包数据库 schema_fingerprint 不匹配")
    existing=c.execute("SELECT content_hash,applied_revision FROM pdp_packages WHERE package_id=?",(pkg["package_id"],)).fetchone()
    if existing:
        if existing["content_hash"]!=pkg["content_hash"]:
            fail("包 ID 已被用于不同内容，存在严重冲突")
        return {"status":"already_applied","revision":existing["applied_revision"],"changes":[]}
    if pkg["base_data_fingerprint"]!=m["data_digest"]:
        fail("基线数据指纹冲突：两个副本即使修订版本相同，也不能混合更新包")
    if pkg["base_revision"]!=m["revision"]:
        fail(f"基线版本冲突：补丁基于 {pkg['base_revision']}，本地数据库为 {m['revision']}。需要重新生成增量包。")
    for o in pkg["operations"]:
        if c.execute("SELECT 1 FROM pdp_operations WHERE op_id=?",(o["op_id"],)).fetchone():
            fail("此 op_id 已在其他增量包中使用")
    c.execute("INSERT INTO pdp_packages(package_id,content_hash,previous_revision,applied_revision,applied_at,summary) VALUES(?,?,?,?,?,?)",(pkg["package_id"],pkg["content_hash"],m["revision"],m["revision"]+1,now(),pkg.get("description","")))
    changed=[]
    for o in pkg["operations"]:
        changed.append(execute_op(c,o,pkg["package_id"],pkg['protocol']))
    new_data_digest = data_fingerprint(c)
    c.execute("UPDATE pdp_meta SET revision=?, data_digest=? WHERE singleton=1",(m["revision"]+1,new_data_digest))
    check_db(c)
    return {"status":"applied" if commit else "preview","revision":m["revision"]+1,"changes":changed}


def command_patch(args,commit=False):
    pkg,evidence=load_package(args.package,getattr(args,"evidence_dir",None))
    validate_pkg(pkg,evidence)
    c=connect(args.db)
    backup=None
    try:
        # First check without writing (including whether the package has already landed).
        m=get_meta(c)
        if pkg["database_id"]!=m["db_id"]:fail("database_id 不匹配")
        if schema_fingerprint(c)!=m["schema_digest"] or data_fingerprint(c)!=m["data_digest"]:
            fail("数据库不完整或存在未审计的外部修改")
        old=c.execute("SELECT content_hash,applied_revision FROM pdp_packages WHERE package_id=?",(pkg["package_id"],)).fetchone()
        if old:
            if old["content_hash"]!=pkg["content_hash"]:fail("相同包 ID 对应不同内容")
            result={"status":"already_applied","revision":old["applied_revision"],"changes":[]}
            print(json.dumps(result,ensure_ascii=False,indent=2));return
    finally:c.close()
    if commit:
        backup=fresh_backup(args.db,"before_"+pkg["package_id"].replace("-","")[:12])
    c=connect(args.db)
    try:
        c.execute("BEGIN IMMEDIATE")
        result=process(c,pkg,commit)
        if commit and result["status"]!="already_applied":c.execute("COMMIT")
        else:c.execute("ROLLBACK")
    except Exception:
        if c.in_transaction:c.execute("ROLLBACK")
        raise
    finally:c.close()
    result["package_id"]=pkg["package_id"]
    if backup:result["backup_path"]=backup
    if commit and result["status"]=="applied":
        rec_dir=Path(args.db).resolve().parent/"pdp_receipts";rec_dir.mkdir(parents=True,exist_ok=True)
        dest=rec_dir/(pkg["package_id"]+".receipt.json")
        temp=dest.with_suffix(".tmp")
        result["applied_at"]=now()
        temp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        os.replace(temp,dest)
        result["receipt_path"]=str(dest)
    print(json.dumps(result,ensure_ascii=False,indent=2))


def command_inspect(args):
    c=connect(args.db)
    try:
        m=get_meta(c)
        if data_fingerprint(c)!=m["data_digest"]:
            fail("数据库未审计改动，无法输出可信的修正前置条件")
        row=get_row(c,args.table,args.id)
        if row is None:
            fail("找不到记录，请检查表名与 ID")
        exact=[dict(r) for r in c.execute("SELECT column_name,decimal_value FROM pdp_exact_values WHERE table_name=? AND entity_id=?",(args.table,args.id))]
        out={"table":args.table,"id":args.id,"database_id":m["db_id"],"revision":m["revision"],"record":row,"exact_new_decimals":exact}
        txt=json.dumps(out,ensure_ascii=False,indent=2)+"\n"
        if args.out:
            path=Path(args.out);path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(txt,encoding="utf8")
            print("已输出指定记录（含财务字段，请谨慎分享）: "+str(path))
        else:
            print(txt)
    finally:c.close()


def command_status(args):
    c=connect(args.db)
    try:
        m=get_meta(c)
        check_db(c)
        if data_fingerprint(c) != m["data_digest"]:
            fail("数据库在 PDP 之外被修改，数据指纹不匹配")
        from multicurrency import enabled
        res={"status":"healthy","database_id":m["db_id"],"revision":m["revision"],"data_fingerprint":m["data_digest"],"multicurrency_enabled":enabled(c),"counts":{t:c.execute("SELECT COUNT(*) FROM "+t).fetchone()[0] for t in TABLES},"active_counts":{"transactions":c.execute("SELECT COUNT(*) FROM pdp_active_transactions").fetchone()[0],"snapshots":c.execute("SELECT COUNT(*) FROM pdp_active_snapshots").fetchone()[0]},"recent_packages":[dict(r) for r in c.execute("SELECT * FROM pdp_packages ORDER BY applied_revision DESC LIMIT 10")]}
        print(json.dumps(res,ensure_ascii=False,indent=2))
    finally:c.close()


def command_restore(args):
    if not args.confirm_restore:
        fail("还原是破坏性操作，必须明确添加 --confirm-restore")
    if not Path(args.backup).is_file():fail("找不到指定备份")
    if Path(args.backup).resolve()==Path(args.db).resolve():fail("备份与目标路径不可相同")
    b=sqlite3.connect(str(args.backup))
    try: check_db(b)
    finally: b.close()
    safety=fresh_backup(args.db,"before_restore")
    src=sqlite3.connect(str(args.backup))
    try:
        dst=sqlite3.connect(str(args.db),timeout=8)
        try:
            src.backup(dst)
            dst.commit()
        finally:dst.close()
        c=connect(args.db)
        try:check_db(c)
        finally:c.close()
    finally:src.close()
    print(json.dumps({"restored_from":str(args.backup),"safety_backup":safety},ensure_ascii=False,indent=2))


def main(argv=None):
    ap=argparse.ArgumentParser(description="PDP 1.0: safely apply incremental portfolio patches")
    sub=ap.add_subparsers(dest="command",required=True)
    p=sub.add_parser("init",help="upgrade existing SQLite with non-destructive PDP tracking tables")
    p.add_argument("--db",required=True)
    p=sub.add_parser("context",help="export compact AI context, without account balances")
    p.add_argument("--db",required=True);p.add_argument("--out",default="pdp_context.json")
    for cmd in ("plan","apply"):
        p=sub.add_parser(cmd,help="simulate or atomically apply a package")
        p.add_argument("--db",required=True);p.add_argument("--package",required=True)
        p.add_argument("--evidence-dir",help="local folder containing evidence/<filename> for JSON packages")
    p=sub.add_parser("status",help="check database counts, revisions, integrity")
    p.add_argument("--db",required=True)
    p=sub.add_parser("inspect",help="read one record for a guarded correction")
    p.add_argument("--db",required=True)
    p.add_argument("--table",required=True,choices=TABLES)
    p.add_argument("--id",required=True)
    p.add_argument("--out",help="optional UTF-8 JSON output path")
    p=sub.add_parser("restore",help="restore a backup, preserving pre-restore safety copy")
    p.add_argument("--db",required=True);p.add_argument("--backup",required=True)
    p.add_argument("--confirm-restore",action="store_true")
    args=ap.parse_args(argv)
    try:
        if args.command=="init": command_init(args)
        elif args.command=="context":command_context(args)
        elif args.command=="plan":command_patch(args,False)
        elif args.command=="apply":command_patch(args,True)
        elif args.command=="status":command_status(args)
        elif args.command=="inspect":command_inspect(args)
        elif args.command=="restore":command_restore(args)
        return 0
    except (PDPError,sqlite3.Error,zipfile.BadZipFile,OSError,ValueError,KeyError) as e:
        print("PDP 拒绝执行: "+str(e),file=sys.stderr)
        return 2

if __name__=="__main__":sys.exit(main())
