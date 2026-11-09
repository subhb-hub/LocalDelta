# LocalDelta v0.4-alpha: 多账户复式现金账本（已实现基础能力）

> **实施边界：** 这份文档描述当前 PR 中已落地的“Phase A + 最小只读查询”，不是原 RFC 中所有功能的完成声明。**尚未实现：** CSV/银行流水自动导入、独立原始账单观测、两端流水自动/人工配对、拆分转账匹配、期初余额核对、证券税务成本批次、可组合 GUI Dashboard 或 HTTP API。不要将账本流水净变动解释为完整账户余额或投资收益率。

## 目前的能力

- 原有三表和多币种表保持不变；备份优先的附加迁移，PDP revision 和数据/结构指纹包含新账本。
- 规范化的账户实体：稳定 UUID、机构、用户别名、账户类型、所有权范围（owned/third_party/system）。
- 原币复式事件：一个事件携带 2–1000 条分录，**每种货币的金额代数和必须为 0**。金额是精确的十进制字符串，支持的币种与精度沿用 v0.3。
- 将发生时刻、入账时刻与单条分录时间分别保留；另保存标准 UTC 时间用于时间排序和范围过滤。
- 创建记录统一通过 PDP/3.0 更新包：hash、来源键、预演、证据验证（若提供）、完整原子写入、备份、审计回执。Ledger 目前只能 create；需要纠错时不得直接修改数据库，后续应以明确冲正机制扩展。
- 本地只读 Python API + CLI JSON 输出：账户列表、账户流水、保守的资金流时间线投影。
- 只对同一事件内**恰好一条本人转出和一条本人转入**的简单 transfer/fx 事件生成箭头。其他复杂事件返回 unlinked，不强行匹配。返回 status=journal_recorded，**不表示已核验两端银行流水**。
- 账户流水接口包含 balance_complete=false；时间线 complete=false。两者均不会凭空生成期初余额、汇率或真实收益率。

## 使用命令

要求 Python 3.10+，无需第三方运行时依赖。已初始化 PDP 且启用多币种：

~~~bash
python localdelta.py money-upgrade --db private/portfolio.sqlite3
python localdelta.py ledger-upgrade --db private/portfolio.sqlite3
python localdelta.py status --db private/portfolio.sqlite3
python localdelta.py ledger-accounts --db private/portfolio.sqlite3
python localdelta.py ledger-accounts --db private/portfolio.sqlite3 --ownership owned
python localdelta.py ledger-entries --db private/portfolio.sqlite3 --account ACCOUNT_UUID --limit 100
python localdelta.py ledger-timeline --db private/portfolio.sqlite3
~~~

按时间查询要传入 **带时区** 的 ISO 8601 时间，时间轴的比较由归一化 UTC 时间完成：

~~~bash
python localdelta.py ledger-timeline --db private/portfolio.sqlite3 \
  --start 2026-01-01T00:00:00+08:00 \
  --end 2026-11-09T23:59:59+08:00
~~~

Windows PowerShell 中执行时可给时间字符串加引号。账户筛选可重复指定 --account，筛选后的时间线只绘制两端都在筛选范围内的箭头。账户 ID 为本地私人稳定 UUID，而不是银行完整账号。

### 快速运行虚构示例

~~~bash
python examples/ledger_quickstart.py
~~~

脚本创建隔离的临时库，启用多币种与账本，按顺序创建两个虚构本人账户和一项 250.00 CNY 的内部转账事件，执行 PDP plan/apply，最后输出时间线 JSON。运行结束会删除虚构临时资料。它不会发起真实资金操作。

## PDP/3.0 增量操作

PDP/3.0 延续 PDP/1.0 的 package_id、database_id、base_revision、base_data_fingerprint、schema_fingerprint、created_at、description、evidence、operations、content_hash。签署与证据核验流程不变。

在一个包的 operations 中，先创建账户再创建引用它的事件。单个账户操作：

~~~json
{
  "op_id": "c5c53ce4-bd5c-48de-bb87-e1b71a6a3b03",
  "action": "create",
  "table": "ledger_accounts",
  "source_key": "synthetic:account:demo-a",
  "source": {"kind": "manual", "reference": "synthetic-only", "certainty": "confirmed"},
  "record": {
    "account_id": "c0e7be4f-bdd9-442d-9d05-7d91d338d56c",
    "institution": "DEMO BANK",
    "display_name": "Example A",
    "account_type": "bank",
    "ownership": "owned"
  }
}
~~~

对应的经济事件（账户 UUID 必须预先存在，内容仅为示意）：

~~~json
{
  "op_id": "5246200a-4c4d-44bc-9591-f2987d2ecb52",
  "action": "create",
  "table": "ledger_events",
  "source_key": "synthetic:event:transfer-001",
  "source": {"kind": "manual", "reference": "synthetic-only", "certainty": "confirmed"},
  "record": {
    "event_id": "01f78177-a286-4426-8ebe-8475956a9372",
    "kind": "transfer",
    "occurred_at": "2026-11-09T10:00:00+08:00",
    "source_ref": "synthetic-bank-statement-001",
    "postings": [
      {
        "posting_id": "7e30f00a-df9c-48ec-ad94-7c375f5a8abd",
        "account_id": "c0e7be4f-bdd9-442d-9d05-7d91d338d56c",
        "amount": "-100.00", "currency": "CNY", "role": "principal"
      },
      {
        "posting_id": "53538dbc-98d1-480e-a46c-f315884b4672",
        "account_id": "1bc08f6b-7130-49b1-b2c3-500c559a70dc",
        "amount": "100.00", "currency": "CNY", "role": "principal"
      }
    ]
  }
}
~~~

需要完整可用的 package，请参考 examples/ledger_quickstart.py 的 envelope 生成与哈希代码。示例 UUID 和流水号是虚构的，禁止在真实账户中照搬固定 ID。

### 跨币种、手续费和清算

对按银行实际执行汇率兑换的多币种资金流，每一种币种分别平衡。例如（**全部为虚构金额**）：

| 分录账户 | 原币变动 | 用途 |
|---|---:|---|
| 本人 CNY 银行 | -730.00 CNY | 减少 720.00 本金 + 10.00 手续费 |
| FX CNY 桥接账户 | +720.00 CNY | 人民币兑换腿 |
| 手续费虚拟账户 | +10.00 CNY | 已发生费用 |
| 本人 HKD 银行 | +775.00 HKD | 港币到账 |
| FX HKD 桥接账户 | -775.00 HKD | 港币兑换腿 |

上表不代表账户余额完整、汇率报价真实或银行双方已独立核对。实际执行兑换率为 775/720（HKD per CNY），应结合订单证据；不能使用当前参考汇率代替执行价格。

## 安全约束与未来顺序

1. 不要将真实账户 ID、流水证据、交易描述或数据库文件提交到公共 GitHub。
2. 如果仅获得单边扣款证据，可通过系统 suspense/clearing 账户记录已知资金变动，但该虚拟分录**不等于另一家银行实际到账**。
3. 本 PR 暂不支持纠错或作废账本事件。若发现已记入错误事实，应先暂停继续导入，保留原备份，等待后续带冲正与复核的 PDP 扩展，不要直接执行 SQL 覆盖。
4. 应优先补充 ld_statement_items、ld_source_links、ld_transfer_matches、分拆资金关联和已知余额快照，再提供通用 Analytics API 与自由组合 Dashboard。这些是 RFC 中的下一阶段，而非当前已可用功能。
5. 复式记账事件之间的关系不能靠金额和时间相似度自动确认为银行流水匹配结果。当前 timeline 只展示人工提交的事件级关系。

完整目标架构见 [MULTI_ACCOUNT_DASHBOARD_RFC.md](MULTI_ACCOUNT_DASHBOARD_RFC.md)，多币种前置能力见 [MULTICURRENCY.md](MULTICURRENCY.md)。
