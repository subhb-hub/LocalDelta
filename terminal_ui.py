"""A small interactive terminal client; retains low-level PDP CLI for scripts."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys

import workflow


def _ask_path(prompt: str, current: str = "") -> str:
    value = input(f"{prompt}{f' [{current}]' if current else ''}: ").strip().strip('"')
    return value or current


def _print_status(db: str) -> None:
    info = workflow.status(db)
    print(f"\n数据库健康 · 修订版本 {info['revision']}")
    counts = info['counts']
    print(f"资产 {counts['assets']} · 交易 {counts['transactions']} · 快照 {counts['snapshots']}")
    for pack in info['recent_packages'][:5]:
        print(f"  v{pack['applied_revision']}  {pack['applied_at']}  {pack['summary'] or pack['package_id']}")


def interactive_update(db: str, package: str, evidence_dir: str | None = None, *, yes: bool = False) -> bool:
    review = workflow.preview(db, package, evidence_dir)
    print('\n' + workflow.readable_review(review) + '\n')
    if review.preview['status'] == 'already_applied':
        print('更新包已应用，无需重复处理。')
        return True
    if not yes:
        if not sys.stdin.isatty():
            print('没有交互式终端；请在终端人工确认，或明确传入 --yes。', file=sys.stderr)
            return False
        response = input('确认写入本地数据库并创建备份？输入 APPLY 继续：').strip()
        if response != 'APPLY':
            print('已取消，数据库未改变。')
            return False
    result = workflow.apply(review)
    print(f"结果：{result['status']} · 数据库版本 {result['revision']}")
    if result.get('backup_path'):
        print('自动备份：' + result['backup_path'])
    if result.get('receipt_path'):
        print('审计回执：' + result['receipt_path'])
    return True


def menu(db: str = "") -> int:
    print('LocalDelta · 本地增量更新')
    print('所有数据库读写都在本机完成。')
    while True:
        try:
            if not db:
                selected = _ask_path('输入现有数据库路径，或输入 NEW 新建数据库（回车退出）')
                if not selected:
                    return 0
                if selected.strip().upper() == 'NEW':
                    new_path = _ask_path('请输入新数据库文件路径（.sqlite3）')
                    if new_path:
                        try:
                            workflow.create_database(new_path)
                            db = str(Path(new_path).expanduser().resolve())
                            print('已创建新的空白数据库：' + db)
                        except (workflow.WorkflowError, OSError, ValueError) as exc:
                            print('创建失败：' + str(exc), file=sys.stderr)
                    continue
                db = selected
            print('\n' + '=' * 46)
            print(f'当前数据库：{db}')
            print('1. 查看状态与历史   2. 预演并导入更新包')
            print('3. 导出匿名 AI 上下文   4. 绑定 AI 草稿')
            print('5. 切换数据库   6. 初始化已有数据库   7. 新建空白数据库')
            print('0. 退出')
            choice = input('请选择 [0-7]：').strip()
            if choice == '0':
                return 0
            if choice == '1':
                _print_status(db)
            elif choice == '2':
                file = _ask_path('更新包路径 (.pdp.json / .pdp.zip)')
                if file:
                    try:
                        interactive_update(db, file)
                    except workflow.WorkflowError as exc:
                        if '证据' in str(exc):
                            folder = _ask_path('如 JSON 需要证据目录，可输入路径（否则留空）')
                            if folder:
                                interactive_update(db, file, folder)
                            else:
                                raise
                        else:
                            raise
            elif choice == '3':
                p = workflow.cloud_context(db)
                print('匿名上下文已生成：' + str(p))
                print('请仅分享这份文件和你选择公开的新事实，勿上传私有别名映射。')
            elif choice == '4':
                f = _ask_path('AI 草稿 draft.json 路径')
                if f:
                    p = workflow.bind_draft(db, f)
                    print('已在本地绑定为增量包：' + str(p))
                    interactive_update(db, str(p))
            elif choice == '5':
                db = ''
            elif choice == '6':
                if input('数据库初始化将先自动备份。输入 INIT 继续：').strip() == 'INIT':
                    info = workflow.initialize(db)
                    print('初始化完成，修订版本：' + str(info['revision']))
            elif choice == '7':
                new_path = _ask_path('请输入新数据库文件路径（.sqlite3）')
                if new_path:
                    workflow.create_database(new_path)
                    db = str(Path(new_path).expanduser().resolve())
                    print('已创建并打开新的空白数据库：' + db)
            else:
                print('无效选择，请重新输入。')
        except (workflow.WorkflowError, OSError, ValueError) as exc:
            print('操作未完成：' + str(exc), file=sys.stderr)
        except (EOFError, KeyboardInterrupt):
            print('\n已退出。')
            return 0


def cli_update(args: list[str]) -> int:
    ap = argparse.ArgumentParser(prog='localdelta.py update', description='一条命令预演、人工确认并导入增量包')
    ap.add_argument('--db', required=True, help='SQLite 数据库路径')
    ap.add_argument('--package', required=True, help='.pdp.zip / .pdp.json 更新包')
    ap.add_argument('--evidence-dir', help='仅用于带 evidence 的单文件 JSON 包')
    ap.add_argument('--yes', action='store_true', help='明确跳过交互确认；仍重新预演和检查版本')
    opts = ap.parse_args(args)
    try:
        success = interactive_update(opts.db, opts.package, opts.evidence_dir, yes=opts.yes)
    except (workflow.WorkflowError, OSError, ValueError) as exc:
        print('更新失败：' + str(exc), file=sys.stderr)
        return 2
    return 0 if success else 1
