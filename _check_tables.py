import sqlite3
db = sqlite3.connect('/home/xxxsuli/bqas-model/bqas/data/cache/bqas.db')
tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("Tables:", tables)
for t in tables:
    count = db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {count} rows")

# Check schema for financials-like table
for t in tables:
    if 'finance' in t.lower() or 'income' in t.lower() or 'balance' in t.lower():
        cols = [c[1] for c in db.execute(f"PRAGMA table_info({t})").fetchall()]
        print(f"\n  {t} columns: {cols[:20]}")
