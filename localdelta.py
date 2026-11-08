#!/usr/bin/env python3
"""LocalDelta: local-first, AI-neutral incremental updates (portfolio adapter)."""
import argparse
import json
import sys
import sqlite3
import zipfile

import pdp
import privacy_bridge


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
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
    sys.exit(main())
