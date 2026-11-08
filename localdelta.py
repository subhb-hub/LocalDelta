#!/usr/bin/env python3
"""LocalDelta: local-first, AI-neutral incremental updates (portfolio adapter)."""
import argparse
import json
from pathlib import Path
import sys
import sqlite3
import zipfile

import pdp
import privacy_bridge


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    if args == ['--help'] or args == ['-h']:
        print('LocalDelta：本地优先、AI 协作的增量更新工具\n')
        print('  python localdelta.py gui [--db 路径]    图形界面（默认不显示演示）')
        print('  python localdelta.py gui --show-demo     仅在测试时显示演示按钮')
        print('  python localdelta.py create --db 路径    创建新的空白数据库（不会覆盖）')
        print('  python localdelta.py menu [--db 路径]   交互式终端菜单')
        print('  python localdelta.py demo               创建独立的虚构演示数据库和更新包')
        print('  python localdelta.py update --db 路径 --package 文件   预演 + 人工确认 + 应用')
        print('  python localdelta.py status|init|plan|apply|context|inspect|restore ...')
        print('  python localdelta.py cloud-context|alias-map|bind-draft ...')
        return 0
    if not args:
        if sys.stdin.isatty():
            from terminal_ui import menu
            return menu()
        # Non-interactive invocation should not block waiting for input.
        return pdp.main(['--help'])
    if args[0] in ('gui', 'desktop'):
        try:
            from desktop_gui import launch
        except ImportError as exc:
            print('此 Python 环境缺少 Tkinter：'+str(exc)+'。可使用终端菜单 python localdelta.py menu。',file=sys.stderr)
            return 2
        return launch(args[1:])
    if args[0] in ('menu', 'interactive'):
        from terminal_ui import menu
        p = argparse.ArgumentParser(description='LocalDelta interactive terminal menu')
        p.add_argument('--db', default='')
        return menu(p.parse_args(args[1:]).db)
    if args[0] in ('create', 'new'):
        from workflow import WorkflowError, create_database
        p = argparse.ArgumentParser(prog='localdelta.py create', description='新建空白投资组合数据库并初始化 PDP')
        p.add_argument('--db', required=True, help='新数据库保存路径（需以 .sqlite3/.sqlite/.db 结尾）')
        a = p.parse_args(args[1:])
        try:
            info = create_database(a.db)
            print(json.dumps({'status': 'created', 'database': str(Path(a.db).expanduser().resolve()),
                              'revision': info['revision'], 'counts': info['counts']}, ensure_ascii=False, indent=2))
            return 0
        except (WorkflowError, OSError, ValueError) as exc:
            print('创建失败：' + str(exc), file=sys.stderr)
            return 2
    if args[0] == 'demo':
        from workflow import make_demo
        db, package = make_demo()
        print('已创建独立的虚构投资演示数据：')
        print('数据库：'+str(db))
        print('更新包：'+str(package))
        print('启动图形界面：python localdelta.py gui --db '+repr(str(db)))
        print('或在终端操作：python localdelta.py update --db '+repr(str(db))+' --package '+repr(str(package)))
        return 0
    if args[0] == 'update':
        from terminal_ui import cli_update
        return cli_update(args[1:])
    if args and args[0]=='cloud-context':
        p=argparse.ArgumentParser(description='Export minimal cloud-safe opaque asset references')
        p.add_argument('--db',required=True)
        p.add_argument('--out',required=True)
        p.add_argument('--detail',choices=['none','category'],default='none')
        a=p.parse_args(args[1:])
        try:
            print(json.dumps(privacy_bridge.export_context(a.db,a.out,a.detail),ensure_ascii=False,indent=2))
            return 0
        except (pdp.PDPError,OSError,sqlite3.Error,ValueError) as e:
            print('LocalDelta rejected: '+str(e),file=sys.stderr)
            return 2
    if args and args[0]=='alias-map':
        p=argparse.ArgumentParser(description='Export a PRIVATE name-to-token map. NEVER upload it.')
        p.add_argument('--db',required=True)
        p.add_argument('--out',required=True)
        a=p.parse_args(args[1:])
        try:
            print(json.dumps(privacy_bridge.export_local_aliases(a.db,a.out),ensure_ascii=False,indent=2))
            return 0
        except (pdp.PDPError,OSError,sqlite3.Error,ValueError) as e:
            print('LocalDelta rejected: '+str(e),file=sys.stderr)
            return 2
    if args and args[0]=='bind-draft':
        p=argparse.ArgumentParser(description='Bind a cloud AI draft to a local database (offline)')
        p.add_argument('--db',required=True)
        p.add_argument('--draft',required=True)
        p.add_argument('--out',required=True)
        a=p.parse_args(args[1:])
        try:
            print(json.dumps(privacy_bridge.bind_draft(a.db,a.draft,a.out),ensure_ascii=False,indent=2))
            return 0
        except (pdp.PDPError,OSError,sqlite3.Error,ValueError,TypeError) as e:
            print('LocalDelta rejected: '+str(e),file=sys.stderr)
            return 2
    return pdp.main(args)


if __name__=='__main__':
    # Windows/CI output pipes may default to cp1252 and reject Chinese UI text.
    # Use UTF-8 for our own CLI messages when the stream allows reconfiguration.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError, OSError):
            pass
    sys.exit(main())
