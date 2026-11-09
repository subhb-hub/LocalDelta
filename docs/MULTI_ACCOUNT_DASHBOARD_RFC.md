# RFC: 多账户资金账本、资金流时间线与可组合看板（提案）

> **状态：整体仍为设计提案；Phase A（复式账本基础）和一部分只读时间线查询已在实验分支实现。** 已实现范围和命令见 [LEDGER_V04.md](LEDGER_V04.md)。原始流水配对、收益统计和自由组合 GUI 看板仍未实现。 对应拟议 v0.4+，不代表当前 CLI、数据库或 GUI 已有这些能力。所有例子均为虚构。坚持 local-first、默认离线、审计优先与不泄漏真实账户资料。

## 1. 目标与边界

LocalDelta v0.3 的业务主体是 `assets / transactions / snapshots`，`ld_currency_amounts` 使投资记录保留原币金额，`ld_fx_rates` 保存注明来源的历史参考汇率。它**不是**现金账户流水账本，也没有跨银行配对及可配置的数据看板（参见 `docs/MULTICURRENCY.md`）。

新功能需要同时满足：

1. 任意数量的银行、券商、交易平台、支付账户及其多币种子账户；支持同一家机构的多个独立账户，以及本人 / 其他人 / 外部往来方的**所有权边界**。
2. 原始单边流水不丢失；对同一经济事件的转出、到账、手续费、实际换汇、退款、收益分配、证券结算进行可审计的关联；未能匹配的事实必须可查询，绝不能凭金额相似度自动认定。
3. 图表与报表基于同一个独立于 UI 的**类型化查询 API**；按账户/币种/时间独立汇总与全局汇总都可复用。内部调拨不算收入，也不重复计入总资产。
4. 可组合 Dashboard：用户增删、排序、配置、复用多种图表块；资金流时间线只是可选的一个块。
5. 与 PDP 既有的修订号、数据/结构指纹、来源证据、计划预演、人工确认、自动备份、原子写入、作废/更正审计兼容；不能以图表需求为由绕过写入保护。

**非目标（首阶段）：** 自动登录银行、真实资金划转、自动抓取汇率、声称估计交易即为已证实交易、完整证券税务成本批次、向云端自动公开私有财务数据。

## 2. 领域模型：观测事实 ≠ 经济事件 ≠ 图表连线

至少区分如下实体：

| 实体 | 建议表 | 要点 |
|---|---|---|
| 金融账户 | `ld_accounts` | 稳定 UUID；机构、别名、类型、所有权 `owned/third_party/system`、真实/虚拟账户；一个账户可有多币种流水 |
| 流水证据 | `ld_statement_items` | 导入来源、源账户、原始流水 ID/行哈希、原文、交易/入账/起息时间、原币金额、证据引用、解析质量；**原始记录不可覆盖** |
| 经济事件 | `ld_events` | UUID、事件类别、发生与结算时间、事件状态、商户/交易对手、说明、来源、关联证券或订单 |
| 记账分录 | `ld_postings` | `event_id, account_id, amount, currency, role`；带正负号的**十进制字符串**。正数表示该账户余额增加，负数表示减少 |
| 流水/分录归属 | `ld_source_links` | 哪项原始证据支持哪个分录、复核状态、关联原因；一条经济事件可对应多条银行流水 |
| 转账配对 | `ld_transfer_matches` | 两端分录引用、分别分配的转出/转入金额与币种、匹配方法、证据、确认状态；允许分拆及多对多，但每条分录的已分配量不可超过其绝对金额 |
| 余额/估值事实 | `ld_balance_observations` | 独立的带日期账户实际余额，与按流水推导的账面余额对账；缺失前期余额不能伪装为完整余额 |
| 看板定义 | 单独本地配置或 `ld_dashboard_*` | 布局、组件类型、查询参数、样式；不混作金融交易，单独做版本控制与安全存储 |

**关键：** `assets.account` 目前是文本而非规范化账户外键。新增账户必须使用稳定 ID；旧资产到新账户的映射采用显式、可审核的别名迁移，不能只根据同名字符串自动合并。

### 所有权与纳入范围

- `owned`：用户纳入个人资产范围的账户。不同 owned 账户之间的调拨属于内部转移。
- `third_party`：第三方的真实账户 / 对手方（例如代汇款人）。不能未经授权视为用户资产。
- `system`：内部清算、未知来源暂记、手续费/费用、汇兑桥接等虚拟账户。只用于记账平衡与溯源，不构成用户资产。

**机构不是账户；账户也不是币种。** 汇丰 HKD 钱包与 USD 钱包可以在同一用户账户之下按 `(account_id, currency)` 分别查询。交易平台的现金钱包和证券持仓亦须区别。

## 3. 账本不变量与缺失信息

**每个已入账经济事件按币种单独平衡：**

```text
For each event E and currency C:
  sum(posting.amount where posting.event=E and posting.currency=C) == 0
```

单边证据可以确认“某银行扣款”，但**不能证明对方到账**。例如本人的银行 A 支出 1,000 CNY：在未匹配时可记录 `A: -1000 CNY`、`待查往来: +1000 CNY`；待查对手方是虚拟项，不是另一家银行的虚构余额。若之后获证到账，则新增独立事实并配对，已存在的真实银行分录**不重复入账**；分类关系通过版本化链接或冲正/重分类事件补全。

已证实不同币种的换汇转账，**不要求不同币种数值相等**，而使用每个币种单独的清算桥。虚构例子：

```text
银行 A            -730.00 CNY   (本金 720 + 手续费 10)
FX 清算 CNY        +720.00 CNY
手续费费用账户      +10.00 CNY

银行 B            +775.00 HKD
FX 清算 HKD        -775.00 HKD
```

实际执行比率是 `775 / 720 HKD per CNY`，手续费额外记载；**不等同**参考汇率，也不能直接把所有汇差宣布为损失。参考汇率、执行汇率、银行手续费、时滞与最终到账金额要分别保留。

其他强制约束：

- 金额只以 `TEXT` 十进制存储，Python `Decimal` 计算；禁止货币 `REAL` 混算和隐式转换。
- 保留 `occurred_at`（交易发生）、`booked_at`（记账）、`value_date`（起息/结算）、`observed_at`（采集）以及各自时区，不随便回填未知值。
- `(provider, source_account_id, external_transaction_id)` 尽量去重；缺少外部流水 ID 时以来源文件哈希、行号与人工标识组合去重。金额/日期相同不是充分的去重依据。
- 原始导入值不可静默覆盖；候选配对有 `suggested` 状态，复核之后才变 `confirmed`。记录确认人、来源与时间。
- 部分配对、撤销、退款、费用拆分、第三方过账等均允许，图表应展示未分配余额和不完整警告。
- 余额应能由期初余额 + 已确认分录重建，并与银行余额快照核对；不能把“首条可见流水之前的缺口”解释为 0。

## 4. 资金流时间线：一张可选图表

**横轴**为发生/记账时间（可切换），**纵轴**为选定账户（可按机构分组或拆为货币子行），以有向边展示转出、到账及事件关联；连线可以跨不同日期，而不是强制同一时间发生。

- 箭头：来源账户 → 目标账户；粗细可选对应金额（先换算到指定比较币种且标明引用汇率）或固定等宽。
- 不同颜色/线型：内部转账、第三方转入、执行换汇、投资买卖、手续费；`suggested` 用虚线，未知一端的箭头连接到“待查/外部”轨道。
- 悬停/点击：源端原币金额、目标端原币金额、执行 FX、手续费、双方日期、银行参考号、证据引用、配对可信度、源流水与内部事件 ID。
- 同一笔 `A → B` 不应成为两个收入/支出；一笔拆分汇款可展示一个来源节点通向多个到账节点。
- 图表选区同步过滤下方流水明细；有 `unmatched`、`missing_fx` 和 `incomplete_history` 显著但不过度夸大的提示。

### 时间线 API 响应例子（纯虚构，仅为展示契约）

```json
{
  "schema": "LocalDeltaTimeline/1",
  "as_of": "2026-11-09",
  "account_lanes": [
    {"account_id": "acc-a", "label": "Bank A · CNY", "scope": "owned"},
    {"account_id": "acc-b", "label": "Bank B · HKD", "scope": "owned"}
  ],
  "edges": [{
    "id": "match-1",
    "from_account_id": "acc-a",
    "to_account_id": "acc-b",
    "sent_at": "2026-10-02T09:00:00+08:00",
    "received_at": "2026-10-03T11:00:00+08:00",
    "sent": {"amount": "720.00", "currency": "CNY"},
    "received": {"amount": "775.00", "currency": "HKD"},
    "fees": [{"amount": "10.00", "currency": "CNY"}],
    "status": "confirmed",
    "evidence_refs": ["local-statement-id-1", "local-statement-id-2"]
  }],
  "unmatched_count": 0,
  "complete": true,
  "warnings": []
}
```

UUID 占位符、日期和数额均是合成测试值；正式返回使用真实 UUID。该响应只供消费端绘制，**不作为数据库中的事实来源**。

## 5. 统一查询层与 API

**先实现 Python 进程内查询服务和 CLI JSON 导出，不默认开启 HTTP 服务。** 查询必须只读（或只读 SQLite 连接）、参数化、可分页，遵守受控账户筛选与时段、使用明确时区和 FX 来源。外部 HTTP / 桌面 agent 接入作为后续可选能力，并单独处理鉴权与敏感字段权限。

建议的稳定服务接口：

| 逻辑接口 | 输入 | 输出 |
|---|---|---|
| `list_accounts` | scope/type/institution | 账户与币种、账户范围 |
| `query_entries` | account IDs / date range / category / page | 事件、分录、来源、匹配状态 |
| `query_transfer_timeline` | account IDs / time axis / from / to | 账户轨道、匹配连线、单边流水 |
| `query_unmatched` | accounts / status | 未确认配对与剩余未分配金额 |
| `query_balance_series` | accounts / currencies / dates | 已知期初余额、余额序列、对账差额、覆盖度 |
| `query_account_performance` | account / range / report currency | 估值、**外部**现金流、费用、收益构成、缺失项 |
| `query_currency_exposure` | date / report currency | 按原币分组与已证实转换 |
| `query_portfolio_summary` | owned account set / date | 去重后的资产汇总、待查流水警告 |

兼容将来 HTTP 的只读路由，可在本地 adapter 映射成 `GET /v1/accounts`、`/v1/entries`、`/v1/transfers/timeline`、`/v1/transfers/unmatched`、`/v1/accounts/{id}/performance` 等。**HTTP endpoint 只是规划，不是已实现接口。**

任何返回金额的接口必须提供 `amount/currency`，需要汇总折算时还要说明 `report_currency`、`fx_rate_date`、`fx_source`、`missing_rates`、`complete`。当估值或现金流不完整时，可返回已知小计，不可命名成完整净资产或真实收益率。账户余额增长 ≠ 投资收益；TWR（时间加权收益率）、MWR/IRR（资金加权收益率/内部收益率）只有在估值与外部现金流足够完整时才能计算。

### 面向统计的投影（不要将其当作第二份账本）

可建立 SQLite 只读 View / materialized-like cache：

- `v_active_ledger_postings`（当前有效分录）
- `v_account_cash_movements`（按账户、币种、时间）
- `v_confirmed_transfer_links`（已复核内部资金流）
- `v_external_cash_flows`（已知的外部注入/提款，排除 owned→owned）
- `v_unmatched_statement_items`（尚未配对/有争议的观测）
- `v_valuation_coverage`（快照/汇率缺口）

缓存以 `database_id + revision + query parameters` 为键，任何成功的 PDP 更新、冲正或迁移都会失效。首阶段使用正确索引与按需查询，勿在写入时持久保存两套可漂移的总资产统计。

建议索引：`ld_postings(account_id, currency, event_id)`、`ld_events(booked_at, event_id)`、`ld_statement_items(account_id, booked_at)`、`ld_transfer_matches(from_posting_id)` 和 `(to_posting_id)`；索引应通过查询计划与真实规模基准测试验证。

## 6. Dashboard：组件注册表，不允许任意执行 SQL

Dashboard 应提供统一的 **BlockRegistry + DataQuery + Renderer** 三层：

- Block type 定义标题、配置 Schema、允许的 Query API、组件能力和渲染器。
- 用户可增删、拖动排序、调整大小、复制区块，选择账户/时间/报表币种、保存多个不同布局。
- 首批内置块：`transfer-timeline`、`account-balance`、`account-comparison`、`fx-cost-breakdown`、`transaction-table`、`reconciliation-inbox`。
- 自定义块优先支持**受控指标/图型组合**（例如维度 `account` + 指标 `fees` + 视图 `bar`），而非用户直接输入 SQL、任意 Python 或不受控远程脚本。
- 旧 Tkinter GUI 可先用 Canvas/ttk 和 Matplotlib（仅作为可选依赖）渲染；若未来改为 Web，复用查询契约，不改底层金融模型。

建议的用户布局文档：

```json
{
  "schema": "LocalDeltaDashboard/1",
  "dashboard_id": "personal-overview",
  "title": "资金全景",
  "blocks": [
    {"id": "a", "type": "transfer-timeline", "order": 0, "span": 12,
     "config": {"account_ids": [], "time_axis": "booked_at", "start": null, "end": null}},
    {"id": "b", "type": "account-comparison", "order": 1, "span": 6,
     "config": {"metric": "known_value", "report_currency": "CNY"}},
    {"id": "c", "type": "reconciliation-inbox", "order": 2, "span": 6,
     "config": {"status": ["unmatched", "suggested"]}}
  ]
}
```

这类配置是本地展示偏好，不能携带凭据、完整银行账号或从 UI 直接注入 SQL。账户 ID 数组为空时可约定代表当前授权的所有 owned 账户，但必须明确记录默认范围。

## 7. 受控迁移、PDP 集成和隐私

- v0.3 数据库 **先备份后升级**；新建 `ld_*` 表，不重命名/破坏现有三表和 `ld_currency_amounts/ld_fx_rates`。
- 建议新协议 `PDP/3.0`（最终可经设计审查再定）：允许受白名单约束的账户、事件、分录、流水证据、匹配和冲正操作。保持 `plan → 人工审查 → apply`；原子校验账本平衡、分配上限、来源唯一与余额快照的一致性。
- 新财务表及匹配审计必须进入 `pdp.schema_fingerprint` 与 `pdp.data_fingerprint` 的受控范围；现有 v1/v2 包、老库不升级时仍沿用旧逻辑，升级后旧包需因修订号变化而重新生成。
- 调用 LLM 可提出**配对候选**和资金类别，但不能凭模糊证据将 `suggested` 直接写成 `confirmed`；云端上下文默认不含账户姓名、银行流水、完整余额。**不得把公共仓库中的样例换成真实用户流水**。
- 本机 GUI/API 不默认监听网络；未来如提供 localhost HTTP，要显式开关、访问令牌、最小权限、CORS/CSRF 保护和审计；单纯绑定 127.0.0.1 不是完整鉴权机制。

## 8. 开发顺序与验收标准

1. **阶段 A（账本基础）**：确定 DDL、事件与分录不变量，补全 synthetic fixtures；原子迁移和全面审计；`PDP/3.0` 新建流水、账户、分录的最小能力。
2. **阶段 B（关系与对账）**：支持来源唯一性、单边流水待查、人工确认配对、跨币种/手续费/第三方、拆分汇款与冲正；实现对账差额 API。
3. **阶段 C（查询 API）**：按账户/时段/币种稳定查询，内部调拨剔除、缺失数据标记、账户级现金流与估值统计；性能索引与分页。
4. **阶段 D（可组合 GUI）**：Dashboard 配置持久化、区块注册表、增删与筛选、时间线 Canvas 组件；至少一套只读交互联动示例。
5. **阶段 E（扩展）**：可选本地 HTTP API、增量导入适配器、证券账户现金与持仓联动、更加完整的收益归因。

**必须通过的测试：**

- A→B 的同币种内部转账在全局资产/净流入统计中计数一次且外部净流入为零；账户 A/B 自己的现金流分别正确。
- A 的扣款先导入、B 的到账后导入：第一阶段显示待查，配对后不新增第二套真实银行流水；人工撤销配对后可以恢复待查状态。
- 多笔金额相同、入账时间相近时不会自动合并；一笔支出对多笔收款支持金额分配上限。
- 不同币种的执行汇率、银行手续费、参考汇率分别存储；不依靠今天汇率修改历史金额。
- 第三方账户不能被误归类为 owned；外部汇款 ≠ 内部资产调拨。
- 空库、缺失期初余额、缺失 FX、缺失估值时返回 `complete=false` 和具体缺口，不出现虚假零余额或虚假收益。
- 任意一条错误分录、重复来源、过度分配、指纹变更均导致整个更新包回滚；旧 v0.3 流程回归测试全绿。
- Dashboard 区块的创建/删除/重排不影响任何金融数据；不允许通过区块配置执行原生 SQL。

## 9. 当前代码落点（供后续实现）

- `pdp.py`：白名单校验、审计、事务、数据/结构指纹；必须在此接入新账本协议。
- `multicurrency.py`：沿用货币校验、Decimal 和历史参考汇率；**不能**把参考 FX 记录误当作执行换汇。
- `desktop_gui.py`：在 `ttk.Notebook` 增加“看板”页面，图形控件不应持有记账规则。
- `localdelta.py`：统一 CLI JSON 入口供脚本、Agent 与 Dashboard 使用。
- 建议新增 `ledger.py`（账户/事件/配对）、`analytics.py`（只读查询）、`dashboard.py`（组件声明和配置）、`tests/test_ledger.py`、`tests/test_analytics.py`。

**决策原则：先保证资金事件模型、完整性与来源可追溯，再实现时间线和自由组合看板。**
