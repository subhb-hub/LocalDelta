# LocalDelta｜本地优先的 AI 增量更新系统

**你的数据始终由本机掌控；AI 负责建议修改，LocalDelta 负责校验、备份、审计和原子写入。**

LocalDelta 允许本地 AI 或云端 AI 生成结构化的增量更新包，而不是反复传输完整数据库。**目前已实现的业务适配器是三表 SQLite 投资组合**，不是可以任意接入数据库的通用平台。

## 三种入口：选择你觉得方便的方式

需要 **Python 3.10+**，核心运行与 Tkinter 桌面界面都不依赖第三方 pip 包。

```bash
# 从 GitHub 克隆一次
git clone https://github.com/subhb-hub/LocalDelta.git
cd LocalDelta

# ① 原生图形窗口（推荐）
python localdelta.py gui

# ② 终端交互菜单（不需要记忆复杂参数）
python localdelta.py menu

# ③ 原生低级命令（保留用于自动化）
python localdelta.py --help
```

上述命令默认使用 `python`；如 Linux/macOS 只提供 `python3`，请替换为 `python3`。可用 `python --version` 检查版本。如果 `python -m tkinter` 不能打开窗口，说明该 Python 解释器没有安装或连接 Tk 图形组件；此时终端菜单仍可用。

## 30 秒体验：一键创建安全演示

**GUI：** Windows 可直接双击仓库根目录的 `Start-LocalDelta.cmd`（已有 Python 环境）；macOS/Linux 可运行 `sh Start-LocalDelta.sh`。或者运行 `python localdelta.py gui` → 点击 **「试用演示」** → 点击 **「预演 / 校验更新」** → 阅读完整字段差异 → 点击 **「人工确认并导入」**，输入 `APPLY`。完成后可切到「更新历史」核查。模拟数据位于用户主目录的 `.localdelta/demos/`，不会修改真实资产库。

**终端：**

```bash
# 生成一套新的虚构数据库 + 与其匹配的更新包
python localdelta.py demo

# 将上一步输出的两个路径填入下面；先预演再人工确认
python localdelta.py update --db "演示数据库路径" --package "演示更新包路径"
```

`update` 会显示每个变更的字段详情，然后要求在终端输入 `APPLY`；非交互环境默认拒绝写入，若确实需要脚本自动执行，可显式指定 `--yes`。同一包导入两次返回 `already_applied`，不会重复记账。

## 正式导入自己的数据库

1. 用 GUI 的「选择…」打开**本地** `portfolio.sqlite3`；如果是未经 PDP 初始化的旧库，点「首次初始化」，程序会先备份。
2. 使用「选择更新包…」载入 `.pdp.zip` 或 `.pdp.json`；带外部证据文件的 JSON 包可指定证据目录。
3. 点击「预演 / 校验更新」检查操作数、金额、前后值、来源和冲突情况。
4. 点击「人工确认并导入」，输入 `APPLY`。应用前会重新检查**刚才审核的文件内容、数据库修订号和指纹**；然后依旧由 PDP 引擎原子写入。
5. 数据库旁会生成 `pdp_backups/` 自动备份和 `pdp_receipts/` 审计回执。切换到「更新历史」查看最新记录。

若更喜欢终端菜单，`python localdelta.py menu --db "路径"` 可以完成同样的主要操作。保留独立命令：

```bash
python localdelta.py plan --db portfolio.sqlite3 --package update.pdp.zip
python localdelta.py apply --db portfolio.sqlite3 --package update.pdp.zip
python localdelta.py status --db portfolio.sqlite3
```

## 与云端 / 本地 AI 协作

**云端（尽量少披露）：** GUI 的「AI 协作」→「导出匿名上下文」，只将该上下文文件与自己选择披露的增量事实发送云端。云端 AI 依照 [`LocalDeltaDraft/1`](cloud_draft.schema.json) 返回 `draft.json`，你在 GUI 中选择「绑定 AI 草稿」，随后进行预演与人工确认。终端下对应：

```bash
python localdelta.py cloud-context --db portfolio.sqlite3 --out cloud-context.json
python localdelta.py bind-draft --db portfolio.sqlite3 --draft draft.json --out update.pdp.json
python localdelta.py update --db portfolio.sqlite3 --package update.pdp.json
```

**隐私边界：** 匿名上下文默认不含资产名、账户、余额、持仓或完整数据库；本机可另生成含名称的私有别名对照文件，**绝对不要将其上传到云端**。如果你主动发送原始截图、金额或账单给云端模型，这些内容仍会被云端看到。匿名代号也可能被跨轮关联；这不是零知识加密。

**本地 AI：** 可以读取你授权的本地资料直接生成原生 PDP 包；LocalDelta **暂不内置 OCR、Ollama/LLM 调用或自动截图识别**。无论谁生成补丁，执行之前都必须进行本地验证和人工确认。

**云端 Draft/1 当前只能为已有资产新增交易或估值快照；创建资产、更正与作废需使用原生 PDP 包。** 历史三表适配器的人民币金额字段仍有原始 `REAL` 精度限制，美元/港元财务流不能直接冒充人民币写入。

## 如何测试

```bash
python -m unittest discover -s tests -v
python examples/quickstart.py
```

更多说明： [桌面与终端操作手册](docs/USER_GUIDE.md) · [协议规范](docs/PDP_SPEC.md) · [隐私边界](docs/PRIVACY.md) · [AI 交互示例](docs/AI_WORKFLOW.md) · [产品路线图](docs/ROADMAP.md)。

**不要把真实数据库、`.localdelta/`、私有映射、交易包、截图或账单提交到公开 GitHub 仓库。** LocalDelta 没有为本地数据库和备份加密；请启用操作系统磁盘加密并保留离线备份。
