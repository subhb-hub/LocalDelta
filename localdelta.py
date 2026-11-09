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
        print('  python localdelta.py money-upgrade --db 路径    备份并启用原币金额扩展')
        print('  python localdelta.py ledger-upgrade --db 路径   备份并启用复式现金账本')
        print('  python localdelta.py ledger-accounts --db 路径 [--ownership owned]')
        print('  python localdelta.py ledger-entries --db 路径 --account 账户UUID')
        print('  python localdelta.py ledger-timeline --db 路径 [--start ISO时间] [--end ISO时间]')
        print('  python localdelta.py statement-upgrade --db 路径   启用原始流水与配对证据')
        print('  python localdelta.py statement-import --db 路径 --bundle 私密资料包.zip [--apply]')
        print('  python localdelta.py dashboard --db 路径 [--out 私密看板.html]')
        print('  python localdelta.py query --db 路径 --view timeline [--account UUID]')
        print('  python localdelta.py money-report --db 路径 --currency CNY --as-of YYYY-MM-DD')
        print('  python localdelta.py fx-draft --db 路径 --base USD --quote CNY --rate 7.20 --date YYYY-MM-DD --source 人工来源')
        print('  python localdelta.py cloud-context|alias-map|bind-draft ...')
        return 0
    if not args:
        if sys.stdin.isatty():
            from terminal_ui import menu
            return menu()
        # Non-interactive invocation should not block waiting for input.
        return pdp.main(['--help'])
    if args[0] in ('statement-upgrade', 'statement-import', 'statement-import-csv',
                   'statement-inspect', 'statement-review', 'dashboard', 'query'):
        import statements
        import analytics
        import dashboard
        action = args[0]
        ap = argparse.ArgumentParser(prog='localdelta.py ' + action)
        ap.add_argument('--db', required=True)
        if action in ('statement-inspect', 'statement-review'):
            ap.add_argument('--id', required=True)
            if action == 'statement-review':
                ap.add_argument('--decision', required=True,
                                choices=['direction', 'confirmed', 'likely', 'conflicting', 'rejected'])
                ap.add_argument('--signed-amount')
                ap.add_argument('--evidence', required=True)
                ap.add_argument('--apply', action='store_true')
        elif action == 'statement-import':
            ap.add_argument('--bundle', required=True)
            ap.add_argument('--apply', action='store_true', help='确认写入原始观察，不生成记账分录')
            ap.add_argument('--expected-hash', help='预演返回的 content_hash')
        elif action == 'statement-import-csv':
            ap.add_argument('--csv', required=True)
            ap.add_argument('--account-key', required=True)
            ap.add_argument('--institution', required=True)
            ap.add_argument('--label', required=True)
            ap.add_argument('--apply', action='store_true')
            ap.add_argument('--expected-hash')
        elif action == 'dashboard':
            ap.add_argument('--out', help='导出可离线筛选的私密 HTML；不指定则启动本机只读 API')
            ap.add_argument('--port', type=int, default=0)
            ap.add_argument('--no-browser', action='store_true')
        elif action == 'query':
            ap.add_argument('--view', required=True, choices=sorted(analytics.VIEWS))
            ap.add_argument('--account', action='append', dest='account_ids')
            ap.add_argument('--start', help='YYYY-MM-DD')
            ap.add_argument('--end', help='YYYY-MM-DD')
            ap.add_argument('--limit', type=int, default=100)
            ap.add_argument('--offset', type=int, default=0)
        o = ap.parse_args(args[1:])
        try:
            if action == 'statement-inspect':
                result = analytics.record(o.db, o.id)
            elif action == 'statement-review':
                result = (statements.review(o.db, o.id, o.decision, o.evidence, o.signed_amount) if o.apply
                          else {'status': 'preview', 'record': analytics.record(o.db, o.id),
                                'decision': o.decision, 'signed_amount': o.signed_amount,
                                'evidence': o.evidence, 'note': 'Add --apply to append this review'})
            elif action == 'statement-upgrade':
                result = statements.upgrade(o.db)
            elif action == 'statement-import':
                result = (statements.import_bundle(o.db, o.bundle, o.expected_hash) if o.apply
                          else statements.preview_bundle(o.bundle))
            elif action == 'statement-import-csv':
                plan = statements.parse_cash_csv(o.csv, o.account_key, o.institution, o.label)
                result = (statements.import_plan(o.db, plan, o.expected_hash) if o.apply
                          else statements.summarize_plan(plan))
            elif action == 'dashboard':
                if o.out:
                    result = dashboard.export(o.db, o.out)
                else:
                    dashboard.serve(o.db, o.port, not o.no_browser)
                    return 0
            else:
                result = analytics.query(o.db, o.view, account_ids=o.account_ids,
                                         start=o.start, end=o.end, limit=o.limit, offset=o.offset)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (pdp.PDPError, OSError, ValueError, TypeError, KeyError, sqlite3.Error, zipfile.BadZipFile) as exc:
            print('LocalDelta 操作被拒绝：' + str(exc), file=sys.stderr)
            return 2
    if args[0] in ('ledger-upgrade', 'ledger-accounts', 'ledger-entries', 'ledger-timeline'):
        import ledger
        action=args[0]
        ap=argparse.ArgumentParser(prog='localdelta.py '+action)
        ap.add_argument('--db',required=True)
        if action=='ledger-accounts':
            ap.add_argument('--ownership',choices=sorted(ledger.OWNERSHIP))
        if action=='ledger-entries':
            ap.add_argument('--account',required=True,help='稳定账户 UUID')
            ap.add_argument('--start',help='带时区起始时间 ISO 8601')
            ap.add_argument('--end',help='带时区截止时间 ISO 8601')
            ap.add_argument('--limit',type=int,default=100)
            ap.add_argument('--offset',type=int,default=0)
        if action=='ledger-timeline':
            ap.add_argument('--start',help='带时区起始时间 ISO 8601')
            ap.add_argument('--end',help='带时区截止时间 ISO 8601')
            ap.add_argument('--account',action='append',dest='accounts',help='重复传入多个账户 UUID')
            ap.add_argument('--limit',type=int,default=200)
            ap.add_argument('--offset',type=int,default=0)
        o=ap.parse_args(args[1:])
        try:
            if action=='ledger-upgrade':result=ledger.upgrade(o.db)
            elif action=='ledger-accounts':result=ledger.list_accounts(o.db,o.ownership)
            elif action=='ledger-timeline':result=ledger.transfer_timeline(o.db,o.start,o.end,o.accounts,o.limit,o.offset)
            else:result=ledger.account_movements(o.db,o.account,o.start,o.end,o.limit,o.offset)
            print(json.dumps(result,ensure_ascii=False,indent=2));return 0
        except (pdp.PDPError,OSError,ValueError,sqlite3.Error) as exc:
            print('Ledger 操作被拒绝：'+str(exc),file=sys.stderr);return 2
    if args[0] in ('money-upgrade','money-report','fx-draft','money-currencies'):
        import multicurrency as mc
        action=args[0]
        ap=argparse.ArgumentParser(prog='localdelta.py '+action)
        if action!='money-currencies':ap.add_argument('--db',required=True)
        if action=='money-report':
            ap.add_argument('--currency',default='CNY',help='报表币种 ISO 4217')
            ap.add_argument('--as-of',help='报表估值日期 YYYY-MM-DD')
            ap.add_argument('--max-fx-age',type=int,default=0,help='允许使用的历史参考汇率最大天数，默认 0')
        if action=='fx-draft':
            ap.add_argument('--base',required=True,help='基准货币，如 USD')
            ap.add_argument('--quote',required=True,help='计价货币，如 CNY')
            ap.add_argument('--rate',required=True,help='1 BASE = RATE QUOTE')
            ap.add_argument('--date',required=True,help='汇率生效日期 YYYY-MM-DD')
            ap.add_argument('--source',required=True,help='参考汇率来源，不要使用虚构汇率')
        o=ap.parse_args(args[1:])
        try:
            if action=='money-upgrade':result=mc.upgrade(o.db)
            elif action=='money-report':result=mc.report(o.db,o.currency,o.as_of,o.max_fx_age)
            elif action=='fx-draft':result={'draft_package':mc.draft_fx_package(o.db,o.base,o.quote,o.rate,o.date,o.source),
                                            'note':'Only prepared, not applied. Use plan then apply.'}
            else:result={'minor_units':mc.MINOR_UNITS}
            print(json.dumps(result,ensure_ascii=False,indent=2));return 0
        except (pdp.PDPError,OSError,ValueError,sqlite3.Error) as exc:
            print('多币种操作被拒绝：'+str(exc),file=sys.stderr);return 2
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
