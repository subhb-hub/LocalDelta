"""Lightweight native LocalDelta desktop GUI (Tkinter/ttk, no pip dependencies).

The GUI never writes to portfolio tables directly: plan/apply use workflow.py ->
PDP, including optimistic concurrency, backups, audit, and idempotency.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import workflow


APP_TITLE = "LocalDelta · 本地 AI 增量更新"


def _settings_path() -> Path:
    return Path.home() / ".localdelta" / "ui-settings.json"


def _remember(db: str) -> None:
    """Store only a local path; never store account data or keys here."""
    if not db:
        return
    try:
        target = _settings_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(target.parent, 0o700)
        target.write_text(json.dumps({"last_database": db}, ensure_ascii=False), encoding="utf-8")
        if os.name != "nt":
            os.chmod(target, 0o600)
    except OSError:
        # Remembering a path is optional; persistence failures must not block work.
        pass


def _last_db() -> str:
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
        s = data.get("last_database", "")
        return s if isinstance(s, str) and Path(s).is_file() else ""
    except (OSError, ValueError, AttributeError):
        return ""


class LocalDeltaApp(ttk.Frame):
    def __init__(self, root: tk.Tk, database: str = "") -> None:
        super().__init__(root, padding=14)
        self.root = root
        self.root.title(APP_TITLE)
        self.root.minsize(780, 580)
        self.root.geometry("940x740")
        self.pack(fill="both", expand=True)
        self.database = tk.StringVar(value=database or _last_db())
        self.package = tk.StringVar()
        self.evidence_dir = tk.StringVar()
        self.state = tk.StringVar(value="选择本地 SQLite 数据库后开始")
        self.health = tk.StringVar(value="尚未检查数据库")
        self.last_preview: workflow.Review | None = None
        self._build()
        for var in (self.database, self.package, self.evidence_dir):
            var.trace_add("write", self._input_changed)
        if self.database.get():
            self._check_db(quiet=True)

    def _build(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 9))
        ttk.Label(header, text="LocalDelta", font=("Arial", 19, "bold")).pack(side="left")
        ttk.Label(header, text="本地优先 · AI 建议、本机验证和写入", foreground="#536575").pack(side="right")

        db_frame = ttk.LabelFrame(self, text="本地数据库", padding=9)
        db_frame.pack(fill="x")
        ttk.Entry(db_frame, textvariable=self.database).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(db_frame, text="选择…", command=self._choose_db).pack(side="left", padx=(0, 4))
        ttk.Button(db_frame, text="检查状态", command=self._check_db).pack(side="left", padx=(0, 4))
        ttk.Button(db_frame, text="首次初始化", command=self._init_db).pack(side="left", padx=(0, 4))
        ttk.Button(db_frame, text="试用演示", command=self._make_demo).pack(side="left")

        ttk.Label(self, textvariable=self.health, foreground="#24654f").pack(fill="x", pady=(7, 8))
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)
        update_tab = ttk.Frame(notebook, padding=10)
        ai_tab = ttk.Frame(notebook, padding=10)
        history_tab = ttk.Frame(notebook, padding=10)
        help_tab = ttk.Frame(notebook, padding=10)
        notebook.add(update_tab, text="  增量更新  ")
        notebook.add(ai_tab, text="  AI 协作  ")
        notebook.add(history_tab, text="  更新历史  ")
        notebook.add(help_tab, text="  使用说明  ")
        self._build_update(update_tab)
        self._build_ai(ai_tab)
        self._build_history(history_tab)
        self._build_help(help_tab)
        self.notebook = notebook

        statusbar = ttk.Frame(self)
        statusbar.pack(fill="x", pady=(8, 0))
        ttk.Separator(statusbar).pack(fill="x", pady=(0, 7))
        ttk.Label(statusbar, textvariable=self.state, anchor="w").pack(fill="x")

    def _build_update(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="选择 AI 返回的 PDP 更新包（单文件 JSON 或 ZIP）", font=("Arial", 10, "bold")).pack(anchor="w")
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(6, 6))
        ttk.Entry(row, textvariable=self.package).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(row, text="选择更新包…", command=self._choose_package).pack(side="left")

        evidence = ttk.Frame(parent)
        evidence.pack(fill="x")
        ttk.Label(evidence, text="证据目录（可选）").pack(side="left", padx=(0, 6))
        ttk.Entry(evidence, textvariable=self.evidence_dir).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(evidence, text="浏览…", command=self._choose_evidence).pack(side="left")

        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", pady=(9, 8))
        ttk.Button(buttons, text="① 预演 / 校验更新", command=self._preview).pack(side="left", padx=(0, 6))
        self.apply_button = ttk.Button(buttons, text="② 人工确认并导入", command=self._apply, state="disabled")
        self.apply_button.pack(side="left")
        ttk.Label(buttons, text="不自动写入；确认前会再次校验", foreground="#596a75").pack(side="right")

        ttk.Label(parent, text="变更概览", font=("Arial", 10, "bold")).pack(anchor="w")
        overview = ttk.Frame(parent)
        overview.pack(fill="both", expand=False, pady=(3, 8))
        columns = ("index", "action", "table", "record")
        self.ops = ttk.Treeview(overview, columns=columns, show="headings", height=6)
        for c, title, width in (("index", "序号", 50), ("action", "动作", 90),
                                ("table", "数据表", 110), ("record", "记录/摘要", 530)):
            self.ops.heading(c, text=title)
            self.ops.column(c, width=width, minwidth=35, stretch=(c == "record"))
        scrollbar = ttk.Scrollbar(overview, orient="vertical", command=self.ops.yview)
        self.ops.configure(yscrollcommand=scrollbar.set)
        self.ops.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.ops.bind("<<TreeviewSelect>>", self._select_operation)

        ttk.Label(parent, text="本地变更详情（包含金额、来源和修改前后值）", font=("Arial", 10, "bold")).pack(anchor="w")
        detail_frame = ttk.Frame(parent)
        detail_frame.pack(fill="both", expand=True, pady=(4, 0))
        self.details = tk.Text(detail_frame, wrap="word", height=11, font=("Consolas", 10), state="disabled")
        detail_scroll = ttk.Scrollbar(detail_frame, orient="vertical", command=self.details.yview)
        self.details.configure(yscrollcommand=detail_scroll.set)
        self.details.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")
        self._set_details("选好数据库和更新包后，点击「预演 / 校验更新」。\n\n支持重复包检测、旧值冲突、完整性校验和自动备份。")

    def _build_ai(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="云端 AI：只分享最少的信息", font=("Arial", 13, "bold")).pack(anchor="w", pady=(0, 10))
        intro = ("云端上下文默认不含资产名称、账户名称、持仓及余额。云端 AI 可以根据你自愿提供的增量事实生成 draft.json。\n"
                 "返回后，在本机绑定匿名代号，并通过增量更新页预演、确认、写入。")
        ttk.Label(parent, text=intro, wraplength=760, justify="left").pack(fill="x", pady=(0, 14))
        ttk.Button(parent, text="① 导出匿名 cloud-context.json", command=self._export_context).pack(anchor="w", pady=4)
        ttk.Label(parent, text="生成到数据库旁的 .localdelta/outbox/ 文件夹；仅把这份文件和你选定的新事实发给云端 AI。",
                  wraplength=760, foreground="#596a75").pack(anchor="w", pady=(0, 12))
        ttk.Button(parent, text="② 生成私有资产代号对照表（仅本机查看）", command=self._export_aliases).pack(anchor="w", pady=4)
        ttk.Label(parent, text="⚠ 对照表包含真实账户与资产名称，绝不能上传到云端或公开 GitHub。",
                  wraplength=760, foreground="#a75a21").pack(anchor="w", pady=(0, 12))
        ttk.Button(parent, text="③ 选择 AI 草稿并在本机绑定…", command=self._bind_draft).pack(anchor="w", pady=4)
        ttk.Label(parent, text="绑定只生成本地 PDP 包，不修改数据库；生成后会切换到「增量更新」选项卡供你预演。",
                  wraplength=760, foreground="#596a75").pack(anchor="w", pady=(0, 12))
        ttk.Separator(parent).pack(fill="x", pady=12)
        ttk.Label(parent, text="本地 AI 模型", font=("Arial", 12, "bold")).pack(anchor="w")
        ttk.Label(parent,
                  text="本地 AI 可直接读取你授权的本地文件并生成原生 PDP 包。LocalDelta 当前不内置模型或 OCR；请由你的 Ollama / 其他工具负责提取数据。",
                  wraplength=760, justify="left").pack(fill="x", pady=8)

    def _build_history(self, parent: ttk.Frame) -> None:
        ttk.Button(parent, text="刷新本地审计历史", command=self._refresh_history).pack(anchor="w", pady=(0, 10))
        columns = ("version", "date", "description", "package_id")
        self.audit = ttk.Treeview(parent, columns=columns, show="headings")
        for c, title, width in (("version", "版本", 70), ("date", "应用时间 (UTC)", 190),
                                ("description", "更新说明", 360), ("package_id", "包 ID", 300)):
            self.audit.heading(c, text=title)
            self.audit.column(c, width=width, minwidth=50, stretch=(c == "description"))
        self.audit.pack(fill="both", expand=True)
        ttk.Label(parent, text="记录来源于本机 pdp_packages；导入操作的详细回执位于数据库旁的 pdp_receipts 文件夹。",
                  wraplength=770, foreground="#596a75").pack(anchor="w", pady=(8, 0))

    def _build_help(self, parent: ttk.Frame) -> None:
        info = (
            "快速上手\n\n"
            "1. 选择已有的 portfolio.sqlite3（首次使用未初始化的数据库时，点击「首次初始化」）。\n"
            "2. 在「增量更新」中选择 update.pdp.json / update.pdp.zip。\n"
            "3. 点击「预演 / 校验更新」，阅读操作和字段变更。\n"
            "4. 点击「人工确认并导入」，输入 APPLY 才会更新。\n\n"
            "如使用云端 AI：\n"
            "在「AI 协作」导出匿名上下文 → 只发送你允许披露的数据 → 下载 draft.json → 本机绑定 → 预演 → 导入。\n\n"
            "原生终端命令（脚本可调用）：\n"
            "  python localdelta.py menu\n"
            "  python localdelta.py gui\n"
            "  python localdelta.py update --db portfolio.sqlite3 --package update.pdp.zip\n"
            "  python localdelta.py plan --db portfolio.sqlite3 --package update.pdp.zip\n"
            "  python localdelta.py apply --db portfolio.sqlite3 --package update.pdp.zip\n\n"
            "保护说明\n"
            "所有资金数据与备份保存在本地。软件不连接云服务。\n"
            "不要上传 private-aliases、数据库、真实账单到公开仓库。\n"
            "若需恢复，请保留 pdp_backups 中的备份；恢复属于单独的高风险操作。"
        )
        t = tk.Text(parent, wrap="word", font=("Consolas", 11), padx=12, pady=10)
        t.insert("1.0", info)
        t.configure(state="disabled")
        t.pack(fill="both", expand=True)

    def _set_details(self, text: str) -> None:
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    def _input_changed(self, *_args: object) -> None:
        # Editing the database / package / evidence inputs invalidates previous approval.
        self._invalidate_review()
        self.state.set("输入已变化，请重新预演后再导入")

    def _make_demo(self) -> None:
        try:
            db, package = workflow.make_demo()
            self.database.set(str(db))
            self.package.set(str(package))
            self.evidence_dir.set("")
            self._check_db()
            self.state.set("已准备虚构演示数据：点击「预演 / 校验更新」开始")
            self.notebook.select(0)
            messagebox.showinfo("演示环境已准备", f"创建了独立的虚构演示数据库：\n{db}\n\n更新包：\n{package}\n\n不会修改你的真实资产数据。", parent=self.root)
        except (workflow.WorkflowError, OSError, ValueError) as exc:
            messagebox.showerror("创建演示失败", str(exc), parent=self.root)

    def _invalidate_review(self) -> None:
        self.last_preview = None
        self.apply_button.configure(state="disabled")
        for item in self.ops.get_children():
            self.ops.delete(item)

    def _choose_db(self) -> None:
        path = filedialog.askopenfilename(title="选择 SQLite 数据库", filetypes=[("SQLite 数据库", "*.sqlite3 *.sqlite *.db"), ("所有文件", "*.*")])
        if path:
            self.database.set(path)
            self._invalidate_review()
            self._check_db()

    def _choose_package(self) -> None:
        path = filedialog.askopenfilename(title="选择 PDP 更新包", filetypes=[("PDP 更新包", "*.zip *.json"), ("所有文件", "*.*")])
        if path:
            self.package.set(path)
            self._invalidate_review()
            self.state.set("已选新更新包，请重新预演")

    def _choose_evidence(self) -> None:
        directory = filedialog.askdirectory(title="选择证据文件的父目录")
        if directory:
            self.evidence_dir.set(directory)
            self._invalidate_review()

    def _require_db(self) -> str | None:
        db = self.database.get().strip()
        if not db:
            messagebox.showwarning("需要数据库", "请先选择本地 SQLite 数据库。", parent=self.root)
            return None
        return db

    def _check_db(self, quiet: bool = False) -> None:
        db = self._require_db() if not quiet else self.database.get().strip()
        if not db:
            return
        try:
            info = workflow.status(db)
            n = info['counts']
            self.health.set(f"✓ 数据库正常 · 版本 {info['revision']} · 资产 {n['assets']} · 交易 {n['transactions']} · 快照 {n['snapshots']}")
            self.state.set("数据库验证成功")
            _remember(db)
        except (workflow.WorkflowError, OSError) as exc:
            self.health.set("数据库未就绪；如尚未初始化，请点击「首次初始化」")
            self.state.set("数据库状态检查失败")
            if not quiet:
                messagebox.showerror("检查失败", str(exc), parent=self.root)

    def _init_db(self) -> None:
        db = self._require_db()
        if not db:
            return
        answer = simpledialog.askstring("首次初始化", "将在初始化前自动备份数据库。请输入 INIT 继续：", parent=self.root)
        if answer != "INIT":
            return
        try:
            workflow.initialize(db)
            self._invalidate_review()
            self._check_db()
            messagebox.showinfo("初始化完成", "PDP 元数据已准备好，原有业务记录未被替换。", parent=self.root)
        except (workflow.WorkflowError, OSError) as exc:
            messagebox.showerror("初始化失败", str(exc), parent=self.root)

    def _preview(self) -> None:
        db = self._require_db()
        if not db:
            return
        path = self.package.get().strip()
        if not path:
            messagebox.showwarning("需要更新包", "请先选择更新包。", parent=self.root)
            return
        self._invalidate_review()
        self.state.set("正在预演 / 校验增量包…")
        self.root.update_idletasks()
        try:
            review = workflow.preview(db, path, self.evidence_dir.get().strip() or None)
            self.last_preview = review
            self._set_details(workflow.readable_review(review))
            for index, op in enumerate(review.operations, 1):
                label = op.get("record", {}).get("asset_name") or op.get("record", {}).get("transaction_ref") or op.get("id", "")
                self.ops.insert("", "end", iid=str(index), values=(index, op["action"], op["table"], label))
            if review.preview["status"] == "preview":
                self.apply_button.configure(state="normal")
                self.state.set(f"预演通过 · {len(review.operations)} 项变更 · 尚未修改数据库")
            else:
                self.state.set("该增量包已导入过 · 无需重复执行")
            _remember(db)
        except (workflow.WorkflowError, OSError, ValueError) as exc:
            self.state.set("预演失败：数据库未修改")
            self._set_details(str(exc))
            messagebox.showerror("预演 / 校验失败", str(exc), parent=self.root)

    def _select_operation(self, event: object) -> None:
        selected = self.ops.selection()
        if not selected or self.last_preview is None:
            return
        idx = int(selected[0]) - 1
        op = self.last_preview.operations[idx]
        self._set_details(f"第 {idx + 1} 项操作\n\n" + json.dumps(op, ensure_ascii=False, indent=2))

    def _apply(self) -> None:
        review = self.last_preview
        if review is None or review.preview['status'] != 'preview':
            return
        response = simpledialog.askstring(
            "确认应用更新", f"将向本地数据库应用 {len(review.operations)} 项变更并自动备份。\n"
                             "该操作不会自动同步到云端。\n\n输入 APPLY 确认：", parent=self.root)
        if response != "APPLY":
            self.state.set("用户已取消导入；数据库未修改")
            return
        self.apply_button.configure(state="disabled")
        self.state.set("正在重新校验并写入数据库…")
        self.root.update_idletasks()
        try:
            result = workflow.apply(review)
            self._invalidate_review()
            self._check_db(quiet=True)
            self._refresh_history(quiet=True)
            self.state.set(f"导入结果：{result['status']} · 版本 {result['revision']}")
            desc = f"状态：{result['status']}\n版本：{result['revision']}"
            if result.get('backup_path'):
                desc += f"\n\n自动备份：\n{result['backup_path']}"
            if result.get('receipt_path'):
                desc += f"\n\n审计回执：\n{result['receipt_path']}"
            self._set_details(desc)
            messagebox.showinfo("导入结果", desc, parent=self.root)
        except (workflow.WorkflowError, OSError, ValueError) as exc:
            self._invalidate_review()
            self.state.set("导入失败或发生冲突；请重新预演")
            self._set_details(str(exc))
            messagebox.showerror("导入未完成", str(exc), parent=self.root)

    def _refresh_history(self, quiet: bool = False) -> None:
        db = self.database.get().strip()
        if not db:
            if not quiet:
                messagebox.showwarning("需要数据库", "请先选择数据库", parent=self.root)
            return
        try:
            rows = workflow.history(db)
            for item in self.audit.get_children():
                self.audit.delete(item)
            for x in rows:
                self.audit.insert("", "end", values=(x['applied_revision'], x['applied_at'], x.get('summary', ''), x['package_id']))
            self.state.set(f"已读取最近 {len(rows)} 次更新的审计记录")
        except (workflow.WorkflowError, OSError) as exc:
            if not quiet:
                messagebox.showerror("历史记录读取失败", str(exc), parent=self.root)

    def _export_context(self) -> None:
        db = self._require_db()
        if not db:
            return
        try:
            p = workflow.cloud_context(db)
            messagebox.showinfo("匿名上下文已导出", f"文件保存在本机：\n{p}\n\n仅分享你允许公开的新事实。", parent=self.root)
        except (workflow.WorkflowError, OSError) as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.root)

    def _export_aliases(self) -> None:
        db = self._require_db()
        if not db:
            return
        if not messagebox.askyesno("隐私警告", "将生成包含真实资产名称、账户信息的本地对照文件。\n请勿上传云端 AI 或公开 GitHub。\n\n继续？", parent=self.root):
            return
        try:
            p = workflow.private_alias_map(db)
            messagebox.showinfo("本地对照表已导出", f"仅供本机使用：\n{p}", parent=self.root)
        except (workflow.WorkflowError, OSError) as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.root)

    def _bind_draft(self) -> None:
        db = self._require_db()
        if not db:
            return
        draft = filedialog.askopenfilename(title="选择 AI 草稿 draft.json", filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
        if not draft:
            return
        try:
            pack = workflow.bind_draft(db, draft)
            self.package.set(str(pack))
            self.evidence_dir.set("")
            self._invalidate_review()
            self.notebook.select(0)
            self.state.set("AI 草稿已在本机绑定；请点击预演，审核后才可导入")
            messagebox.showinfo("绑定完成", f"已创建本地更新包：\n{pack}\n\n下一步：先预演，再人工确认导入。", parent=self.root)
        except (workflow.WorkflowError, OSError) as exc:
            messagebox.showerror("绑定失败", str(exc), parent=self.root)


def launch(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='localdelta.py gui', description='LocalDelta 轻量本地桌面界面')
    ap.add_argument('--db', default='', help='可选：直接打开本地 SQLite 数据库')
    opts = ap.parse_args(argv)
    try:
        root = tk.Tk()
        LocalDeltaApp(root, database=opts.db)
        root.mainloop()
        return 0
    except tk.TclError as exc:
        print(f"无法启动图形界面：{exc}。请检查系统是否提供桌面会话及 Tkinter；也可使用 python localdelta.py menu。", file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(launch())
