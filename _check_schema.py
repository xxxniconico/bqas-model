import sqlite3
db = sqlite3.connect('/home/xxxsuli/bqas-model/bqas/data/cache/bqas.db')
for t in ['income', 'balance', 'cashflow', 'stock_info']:
    cols = db.execute(f"PRAGMA table_info({t})").fetchall()
    print(f"{t}: {[c[1] for c in cols]}")
# Also check first row of cashflow
r = db.execute("SELECT * FROM cashflow LIMIT 1").fetchone()
if r:
    cols = [c[1] for c in db.execute("PRAGMA table_info(cashflow)").fetchall()]
    print(f"\ncashflow sample: {dict(zip(cols, r))}")
