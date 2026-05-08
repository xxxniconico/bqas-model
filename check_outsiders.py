"""Check the 7 outsiders - stocks in neutralized Top 30 but not in CSI 300/500."""
import sys
sys.path.insert(0, '/home/xxxsuli/bqas-model')

import sqlite3
from bqas.engine.scorer import score_stock

db = sqlite3.connect('/home/xxxsuli/bqas-model/data/bqas.db')

# First, check what tables we have
tables = [t[0] for t in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("Available tables:")
for t in tables:
    cnt = db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {cnt} rows")

# Check if we have index constituent data
has_index = 'index_constituents' in tables
if has_index:
    csi300 = set(row[0] for row in db.execute("SELECT code FROM index_constituents WHERE index_name='CSI300'"))
    csi500 = set(row[0] for row in db.execute("SELECT code FROM index_constituents WHERE index_name='CSI500'"))
    print(f"\nCSI300 members: {len(csi300)}")
    print(f"CSI500 members: {len(csi500)}")
else:
    print("\nNo index_constituents table - need to fetch index data first")

# Check what financial data we have
print("\nSample financials:")
for row in db.execute("SELECT code, name, report_date, roe FROM financials LIMIT 5"):
    print(f"  {row}")

db.close()
