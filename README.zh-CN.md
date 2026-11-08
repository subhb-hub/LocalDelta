# LocalDelta｜本地优先的 AI 增量更新系统

**主旨：数据保留在本地，AI 只生成更新建议，本地程序负责验证与写入。**

LocalDelta 不限于投资组合。它想解决的是：当私有数据存于本地 SQLite、而信息不断以截图、账单、CSV 或文字的方式流入时，如何让本地 AI 或云端 AI 以安全、可追溯、可重复执行的方式共同维护数据库。

目前 **已实现** 的适配器是投资组合的三张表：资产、交易、估值快照。**不是**一个已经支持任意数据库的通用平台。

## 本地 AI 与云端 AI

**本地 AI：** 你可选择把完整账单、私有数据库交给运行在自己电脑上的模型处理。模型输出 PDP 更新包；离线导入器仍然校验、预演、备份并事务写入。

**云端 AI：** 先运行 `cloud-context`。输出只包含通过本地 HMAC 密钥生成的资产代号、上下文代号，以及协议可支持的操作。没有资产名称、账户名称、当前金额、持仓数量和交易编号。你可以仅向云端模型提供想让它看到的新增信息；云端输出匿名资产代号对应的候选草稿，`bind-draft` 再在本地恢复资产关联、账户、币种并绑定数据库状态。

**不应夸大隐私能力：** 云端只是不必获取完整数据库；如果你给云端发原始交易截图，截图上的隐私数据还是会被它看到。为了让云端 AI 做某项计算，它仍需要你提供该项计算所必需的事实，或者让本地程序先完成该计算。

## 一次性准备

要求 Python 3.10 或更新版本。工具仅依赖 Python 标准库。下文统一使用跨平台常见的 `python` 命令；如果你的 Linux/macOS 环境只提供 `python3`，请将命令中的 `python` 替换为 `python3`。在 Windows 中如已配置 Python，也可直接使用 `python`，无须依赖 Windows 专有的 `py` 启动器。首先运行 `python --version`（或 `python3 --version`）确认版本。

```powershell
python localdelta.py init --db portfolio.sqlite3
python localdelta.py status --db portfolio.sqlite3
```

## 日常更新：云端 AI 方案

```powershell
# 在本地导出隐私上下文
python localdelta.py cloud-context --db portfolio.sqlite3 --out cloud-context.json

# 在本机查看代号与真实资产的对应关系；这个文件含隐私，不能上传云端
python localdelta.py alias-map --db portfolio.sqlite3 --out private-aliases.json

# 只分享 cloud-context.json 和你愿意透露的增量信息。
# AI 返回 draft.json，不包含真实资产编号或账户身份。

# 回到本地绑定，得到可执行的更新包
python localdelta.py bind-draft --db portfolio.sqlite3 --draft draft.json --out update.pdp.json

# 模拟检查
python localdelta.py plan --db portfolio.sqlite3 --package update.pdp.json

# 人工审核后，原子导入（自动备份）
python localdelta.py apply --db portfolio.sqlite3 --package update.pdp.json
```

**注意：** `context` 是保留兼容的旧命令，会输出实际资产名称等信息，不是匿名化导出。要分享给云端，请使用 `cloud-context`。

示范：`python examples/quickstart.py`。所有示例均使用虚构数据。

## 可靠性

- 增量包重复导入会被识别，不能重复记账。
- 数据库版本、内容摘要和逐字段旧值检查，可防止旧补丁覆盖新数据。
- 先预演，再备份，再在 SQLite 单一事务里执行全部更新。
- 不允许由 AI 直接提供 SQL 执行，也不自动调用银行/券商。
- 审计日志记录更改前后、来源和补丁编号。
- 金额以十进制字符串传输；旧表中的 SQLite REAL 金额仍有限制，多币种精确核算是下一阶段任务。

更多内容见 [AI 协作示例](docs/AI_WORKFLOW.md)、[隐私模型](docs/PRIVACY.md)、[技术规范](docs/PDP_SPEC.md)、[路线图](docs/ROADMAP.md)。

## 发布到 GitHub

先在 GitHub 上创建空的 **Public** 仓库 `subhb-hub/LocalDelta`，不要预先勾选 README、License 或 .gitignore（本项目都已包含）。随后解压本工具包，在源码根目录执行：

- Windows PowerShell：`./scripts/publish-to-github.ps1`
- macOS/Linux：`bash scripts/publish-to-github.sh`

脚本会检查待上传文件是否包含数据库、图片、密钥、交易包等私有资料，列出拟公开文件，并要求你输入 `PUBLISH` 才会真正上传。需要本机 GitHub 认证。
