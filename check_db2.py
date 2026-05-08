import sqlite3, sys

db_path = '/home/xxxsuli/bqas-model/bqas/data/cache/bqas.db'
db = sqlite3.connect(db_path)

tables = [t[0] for t in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("Tables:", tables)

for t in tables:
    cnt = db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {cnt} rows")
    cols = [c[1] for c in db.execute(f"PRAGMA table_info({t})")]
    print(f"    cols: {cols[:8]}...")

# Check if index_constituents exists
if 'index_constituents' not in tables:
    print("\n⚠️ No index_constituents table! Need to create and populate.")
    sys.exit(0)

csi300 = set(row[0] for row in db.execute("SELECT DISTINCT code FROM index_constituents WHERE index_name='CSI300'"))
csi500 = set(row[0] for row in db.execute("SELECT DISTINCT code FROM index_constituents WHERE index_name='CSI500'"))
print(f"\nCSI300: {len(csi300)} stocks")
print(f"CSI500: {len(csi500)} stocks")

db.close()
