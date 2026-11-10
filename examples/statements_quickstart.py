"""Synthetic private-intake/dashboard smoke test; no network or user data."""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analytics
import dashboard
import ledger
import statements
import workflow


def main():
    with tempfile.TemporaryDirectory(prefix='localdelta-synthetic-dashboard-') as temp:
        folder = Path(temp)
        db = folder / 'synthetic.sqlite3'
        workflow.create_database(db, enable_multicurrency=True)
        ledger.upgrade(db)
        statements.upgrade(db)
        source = folder / 'synthetic.csv'
        source.write_text('date,currency,signed_amount,statement_amount,description,reference\n'
                          '2026-01-01,USD,25.00,,Synthetic inflow,SYN-1\n'
                          '2026-01-02,USD,,3.00,Synthetic pending direction,SYN-2\n', encoding='utf-8')
        plan = statements.parse_cash_csv(source, 'synthetic-a', 'SYNTHETIC', 'Synthetic cash account')
        statements.import_plan(db, plan)
        assert statements.import_plan(db, plan)['status'] == 'already_imported'
        summary = analytics.query(db, 'summary')['rows'][0]
        assert summary['net_movement'] == '25.00' and summary['unknown_direction_rows'] == 1
        target = folder / 'private.html'
        dashboard.export(db, target)
        assert target.is_file()
        workflow.status(db)
    print('Synthetic statement import, exact-currency query and offline dashboard passed.')


if __name__ == '__main__':
    main()
