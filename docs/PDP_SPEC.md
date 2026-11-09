# Portfolio Delta Protocol (PDP) · v1.0

> **v2 extension available:** original-currency `Money` amounts, dated FX, additive migration and Draft/2 are now documented separately in [MULTICURRENCY.md](MULTICURRENCY.md). PDP/1.0 continues to be accepted for existing CNY operations; this document deliberately describes the original v1 contract.

> 状态：针对既有三表投资组合 SQLite 结构实现并测试的**自定义协议 v1.0**。不是 IETF、SQLite 官方标准或金融清算协议。

## 1. 设计目标

1. 允许“人给资料，AI 生成增量包，本地离线程序验证/导入”，避免反复分享完整资金数据库。
2. **有且只有一次**：相同更新包重复导入必须是 no-op；相同金融业务事件换包重发也要被 source_key 与自然键检测拦截。
3. **原子性**：一个包中的操作必须整包成功或全部撤销。
4. **拒绝静默覆盖**：修订版本、数据集 SHA-256、逐字段 `before` 任一不匹配则拒绝。
5. **不可否认的审计过程**（指本地留痕，不是法律意义上的数字签名）：每项写入记录包 ID、操作 ID、前值、后值、来源和理由。
6. **结构兼容**：使用旧导出库的 `assets`、`transactions`、`snapshots`，新增独立的 `pdp_*` 元数据；迁移数据库结构必须另走受控升级，不允许补丁注入 SQL。
7. **来源与时间语义明确**：保留截图采集日、订单时间、确认日、基金正式净值日等不同维度；未核实的日期不用推断填充。

## 2. 参考与工程取舍

| 技术 | 借用设计 | 没有直接照搬的原因 |
| --- | --- | --- |
| RFC 6902 JSON Patch | `test` 前置检查与 patch 失败处理 | JSON Pointer 无法表达账户、券商交易号、买卖/付息等金融语义 |
| SQLite Session Extension | changeset、冲突处理、回滚思路 | 主要服务同结构 SQLite 之间的数据同步；编译扩展不一定启用，且需要双方已有数据库 |
| Flyway Versioned Migration | 版本号、历史记录、校验和 | AI 增量包禁止任意执行 SQL；模式迁移与业务更新分离 |
| SQLite BEGIN IMMEDIATE | 单写者互斥、原子提交 | 它只负责事务，不替应用判断重复交易 |
| SQLite Backup API | 一致性备份 | 不是自动的业务语义冲突检测 |
| Event Sourcing 的追加式日志 | 追溯更正历史 | v1 仍保留便于查询的实体表，不要求每次全量重放事件 |

以上部分机制用于取舍，**PDP 本身不声明与 JSON Patch、SQLite Changeset、Flyway 的文件格式互通**。

来源：
- https://www.rfc-editor.org/rfc/rfc6902.html
- https://www.sqlite.org/sessionintro.html
- https://documentation.red-gate.com/fd/versioned-migrations-273973333.html
- https://www.sqlite.org/lang_transaction.html
- https://sqlite.org/backup.html
- https://www.sqlite.org/pragma.html
- https://www.rfc-editor.org/rfc/rfc8785.html

## 3. 文件格式

推荐：`2026-11-08_fund-purchases.pdp.zip`，ZIP 仅允许：

```text
package.json
[evidence/IMG_0001.png]
[evidence/statement.csv]
```

或者单文件：`update.pdp.json`。如果 JSON 中有 `evidence` 项，导入时必须指向真实的 `evidence/` 文件夹，不能跳过证据 hash 检查。ZIP 不执行解压到系统目录，只从归档中按受限路径读取。单文件 JSON、单个证据和归档总大小均受限制。

`package.json` 结构：

```json
{
  "protocol": "PDP/1.0",
  "package_id": "39f14f68-e930-4ad1-919c-6bdcf4668e3b",
  "database_id": "来自最新 pdp_context.json 的 UUID",
  "base_revision": 0,
  "schema_fingerprint": "最新上下文中的64位小写十六进制SHA256",
  "base_data_fingerprint": "最新上下文中的64位小写十六进制SHA256",
  "created_at": "2026-11-08T17:00:00+08:00",
  "description": "本轮新增基金申购与持仓快照",
  "evidence": [
    {"path": "evidence/statement.csv", "sha256": "证据内容的64位小写十六进制SHA256"}
  ],
  "operations": [
    {
      "op_id": "6dd70008-b561-45b0-b2f7-c0684a037143",
      "action": "create",
      "table": "transactions",
      "source_key": "account:trading-platform:unique-order-001",
      "source": {"kind": "statement", "reference": "evidence/statement.csv", "certainty": "confirmed"},
      "record": {
        "transaction_id": "dc1c502b-23fb-41eb-b9e0-7a30aec4cf6d",
        "asset_id": "来自上下文中资产档案的真实UUID",
        "account": "演示券商",
        "kind": "申购",
        "currency": "CNY",
        "order_datetime": "2026-11-08T14:30:00+08:00",
        "total_paid_cny": "1000.00",
        "net_invested_cny": "998.80",
        "fee_cny": "1.20",
        "quantity": "865.36",
        "unit_nav": "1.1542"
      }
    }
  ],
  "content_hash": "除去 content_hash 后整个 JSON 对象的 PDP 规范化 SHA256"
}
```

上述带说明字样的占位字段不是可直接导入的合法值。实际示例位于 `examples/example_update.pdp.zip`，与 `examples/demo.sqlite3` 绑定。

### JSON SHA-256

```python
import json, hashlib
payload = {k:v for k,v in package.items() if k != "content_hash"}
message = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
package["content_hash"] = hashlib.sha256(message).hexdigest()
```

规范化方法是 **PDP 的 Python 确定性 JSON 编码**，不是完全遵守 RFC 8785 的通用 JSON Canonicalization Scheme。为避免跨语言歧义，未来若提供其他语言实现，需要验证同一批公开测试向量。SHA-256 仅表示数据完整性，无对称密钥或私钥，**不能证明来源真实性**。

### 金额与日期约束

- 所有金额、价格、份额、利率字段必须为 JSON **十进制字符串**，例如 `"12.34"`；禁止 `12.34` 这样的 JSON 浮点数，也禁止科学记数法。
- 人民币金额字段（`*_cny`）最多 2 位小数；单位净值/份额等最多 10 位小数。
- 交易时间精确到时间时应携带时区，如 `2026-11-08T14:30:00+08:00`；单独日期为 `YYYY-MM-DD`。
- 日期不能混用：`order_datetime`（申请/下单）、`confirmed_date`（确认）、`nav_date`（已核实的净值所属日）、`screenshot_date`（截图采集日）、`official_valuation_date`（正式估值日）。
- 缺失值使用 `null` 或不提供字段；不可将未知手续费假设为 0。

## 4. 三种唯一允许的操作

### `create`：新增不可重复记录

- 必需：`op_id`,`action`,`table`,`record`,`source_key`,`source`。
- 只能写入 `assets`,`transactions`,`snapshots` 的已存在列，不能执行 DDL/SQL/脚本。
- `record` 包含真实主键字段（`asset_id` / `transaction_id` / `snapshot_id`）和关联资产 ID；新 ID 采用 UUID。
- **同一券商交易号** (`account` + `transaction_ref`)、重复 `source_key`、已有相同账户与资产名称、已有同一资产同一天快照等，会拒绝自动导入。
- `source_key` 是金融业务事件的稳定身份标识。AI 不应每次收到相同截图都随机改变它；例如 `cmb:trade:26T33W0224`。
- 如果同日确实发生两笔完全相同金额的交易，应该提供可区分的券商流水号/时间及来源凭证，而不是绕过校验。

### `correct`：带旧值条件的更正（CAS，比较后交换）

示意：

```json
{
  "op_id":"新UUID",
  "action":"correct",
  "table":"transactions",
  "id":"目标现存交易UUID",
  "before":{"fee_cny":"5.69"},
  "changes":{"fee_cny":"5.70"},
  "reason":"银行账单二次复核",
  "source":{"kind":"statement","reference":"银行对账单第3行","certainty":"confirmed"}
}
```

`before` 字段必须与 `changes` 的键集合完全一致；逐个字段与目标库原值比较，不一致整包失败。主键、关系键和冗余资产/账户名称不可任意修改，涉及这些身份迁移必须生成特殊迁移计划并人工审阅。

### `void`：作废错误交易或快照

必须提供 `before` 前置条件与 `reason`。不物理删除原记录，`pdp_voids` 记录作废状态；财务统计应读取 `pdp_active_transactions` / `pdp_active_snapshots` 视图。资产档案在 v1 不允许作废，以防孤立外键。

## 5. 幂等与版本保护

每个库在首次 `init` 时生成不可混用的 `database_id`，初始 `revision = 0`。每个成功包加一版本（整个包，不是每条记录）。

- `package_id` 是包的不可变 UUID；首次应用记录摘要，重复的同 ID、同哈希操作为 no-op；同 ID 不同内容报错。
- 每条操作有唯一 `op_id`；跨包重复 ID 报错。
- `source_key` 是金融业务唯一键；跨包重复事件报错。
- `base_revision` 必须恰好等于当前数据库 `revision`（除非该包本就已导入）。
- `base_data_fingerprint` 必须等于前置业务数据及审计日志的 SHA-256。两个从同一库分叉、同为 revision 1 的副本，只要内容不同也不能混淆。
- `schema_fingerprint` 绑定最初的三张业务表结构；任何手工 schema 改动会中止导入。
- 导入器额外检查本地 `pdp_meta.data_digest` 与当前实际行内容；检测到没有通过 PDP 的直接修改即停止。

**结果**：`pdp.py context` 是**仅限本地或已信任环境**使用的兼容命令：会导出资产名称、资产 ID、交易引用号和数据指纹。**不要把它当成脱敏上下文上传云端。** 与云端模型合作请使用 `localdelta.py cloud-context` 导出的不含名称和交易流水的匿名代号，再由本地 `bind-draft` 补全实际关联。

## 6. 执行流程与恢复

### 初始化

1. 校验三个基础表、外键与 `PRAGMA integrity_check`。
2. 通过 SQLite Backup API 创建可恢复备份。
3. 只新增 `pdp_meta`、`pdp_packages`、`pdp_operations`、`pdp_source_keys`、`pdp_voids`、`pdp_exact_values` 与有效记录视图，不修改原始 35/12/26 个记录。
4. 使用 `BEGIN IMMEDIATE` 原子提交结构初始化。

### 预演 (`plan`)

1. 校验 JSON 格式/必填项、内容 SHA-256 与证据文件哈希。
2. 检查 database_id、revision、schema/data 指纹。
3. 在实际 SQLite 临时写事务中执行全部校验与模拟写入，之后强制 `ROLLBACK`。
4. 输出即将新增/更正/作废哪些记录；数据库修订号不变化。

### 真正执行 (`apply`)

1. 先判定是否已导入；若已成功且哈希一致，直接返回 `already_applied`。
2. SQLite Backup API 自动备份，备份失败就不执行更新。
3. `BEGIN IMMEDIATE` 竞争单写者锁；原子处理全部操作、唯一键与业务金额约束、历史审计与新版本号。
4. 完整性检查与外键检查通过后 `COMMIT`；任意异常则整包 `ROLLBACK`。
5. 提交后写 `pdp_receipts/<package-id>.receipt.json`。若“成功提交但未能输出回执”，重新导入同一包会得到 `already_applied`，可依库内 `pdp_packages` 验证已提交。

### 恢复 (`restore`)

需显式 `--confirm-restore`，先再做一份当前库安全备份，然后把选择的原始备份写回。操作完成后对账，切勿误将错误的历史分支覆盖最新工作。

## 7. 金融领域一致性校验

- 基金申购：若 `total_paid_cny`、`net_invested_cny`、`fee_cny` 三项都有值，检查 `总支付 = 净投入 + 手续费`，允许 0.01 元展示误差。
- 债券买入：若净价、每份应计利息与全价均有值，检查 `净价 + 应计利息 ≈ 全价`；若同时有数量/支付额，校验 `份额 × 全价 + 单列手续费 ≈ 实付金额`，允许显示精度造成的少量舍入误差。
- 同一资产档案在一次关系内必须具备一致的持有账户、币种与名称。
- 对于 `needs_valuation_date_check=false` 的快照，必须存在真实 `official_valuation_date`，否则拒绝。
- 任何支付金额、净价、份额不允许从当前市值反推，也不将已收到的利息与买入时支付的应计利息混淆。

## 8. 安全边界、兼容性与未实现功能

- **没有数字签名**：任何拥有文件的人可以修改 JSON 并重新计算 SHA256；hash 仅保证意外改动可检出，不是来源认证。应在本地审阅预演结果。未来可选 Ed25519 签名。
- **不自动接受未确认值**：`source.certainty` 仅允许 `confirmed`。AI 应将不确定信息留在待核实表，而不是把猜测标成确认。
- **只支持现有业务字段**：禁止传入 schema changes、SQLite 表名/字段名的自由 SQL，禁止任何可执行代码，必须白名单验证。
- **固定基础表兼容**：旧库业务金额为 `REAL`（二进制浮点），PDP 以 Decimal 字符串传输并将本次输入的金额原文单独保存为 TEXT，仍不能代替全面的精确金融金额建模。
- **多币种限制**：旧表 `*_cny` 不能表示美元/港元原币金额；对应记录不允许冒用。未来 v2 应建立账户币种、原币整数最小单位、FX 汇率基准日、现金流层。
- **不支持自动跨分支合并、自动股票拆分、自动价格抓取、自动赎回会计核算、银行实时同步或自动换汇**。这些需要后续数据模型升级，不应该暗中“方便地推导”。
- **备份不加密**：建议 Windows BitLocker、受限目录、离线异地备份，不把包含真实持仓的补丁上传到公开仓库。

## 9. 版本策略

- 协议主版本变更可能不兼容；小版本可以增加向后兼容字段，但 v1 导入器对未知字段保守拒绝。
- 数据库 schema 迁移与增量业务更新是两个独立事件；升级前必须备份、出迁移报告、验证原始行和关联数量。
- 同一数据库快照最多一个权威本地写入者；不同设备不要各自更新后尝试强制合并。先将一方补丁顺序应用于另一方（若版本/数据指纹不匹配则重新生成包）。

## 10. AI 交互合同（建议以后固定复制）

> 根据 PDP/1.0 处理我发送的 `pdp_context.json` 和新增截图/CSV。请先逐笔识别并检查账户、产品代码、业务事件 ID、下单时间、确认日期、币种、金额、费用、份额、来源。如遇不明确或冲突，不要猜测，不纳入 `source.certainty=confirmed` 的可导入操作，另写待核事项。生成完整、经哈希校验的 `.pdp.zip`，其中只包含允许的 create/correct/void 操作；修改原有行必须具备逐字段 before 旧值。附交易汇总、可能重复项和自检结果。不输出 SQL。不要宣称已经导入我的本地数据库。

