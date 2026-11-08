"""Reject tracked private artifacts in a public source repository."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {'.py','.md','.txt','.json','.sql','.toml','.yml','.yaml','.ps1','.sh'}
ALLOWED_NAMES = {'.gitignore','.gitattributes','LICENSE'}
PRIVATE_PARTS = {'.localdelta','pdp_backups','pdp_receipts','secrets','private','backups'}


def main():
    r = subprocess.run(['git','ls-files','-z'],cwd=ROOT,capture_output=True)
    if r.returncode:
        print('No Git repo detected: scanning source files.')
        paths = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*')
                 if p.is_file() and not any(x in ('.git','__pycache__') for x in p.parts)]
    else:
        paths = [p.decode('utf-8') for p in r.stdout.split(b'\0') if p]
    blocked=[]
    for rel in paths:
        p=Path(rel)
        if p.suffix.lower() not in ALLOWED and p.name not in ALLOWED_NAMES:
            blocked.append(f'{rel}: extension not allowed')
        elif any(part in PRIVATE_PARTS for part in p.parts):
            blocked.append(f'{rel}: private directory')
        elif p.name.endswith('.pdp.json') and rel!='examples/example_update.pdp.json':
            blocked.append(f'{rel}: live patches must stay private')
        elif ('context' in p.name.lower() or 'alias' in p.name.lower()) and p.suffix=='.json':
            blocked.append(f'{rel}: context exports must stay private')
    if blocked:
        print('PUBLIC SOURCE AUDIT FAILED:\n'+'\n'.join(blocked))
        return 1
    print(f'Public tree check passed ({len(paths)} files)')
    return 0


if __name__=='__main__':
    sys.exit(main())
