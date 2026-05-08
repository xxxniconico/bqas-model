"""Check database state for BQAS index analysis."""
import sqlite3

db = sqlite3.connect('/home/xxxsuli/bqas-model/data/bqas.db')

# List tables
tables = [t[0] for t in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("Tables:", tables)

for t in tables:
    cnt = db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {cnt}")
    # Show schema
    cols = [c[1] for c in db.execute(f"PRAGMA table_info({t})")]
    print(f"    columns: {cols}")

# Sample financials
print("\nSample financials:")
for row in db.execute("SELECT code, name, report_date, roe FROM financials LIMIT 5"):
    print(f"  {row}")

db.close()
