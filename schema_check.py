import sqlite3
db = sqlite3.connect('/home/xxxsuli/bqas-model/bqas/data/cache/bqas.db')
for table in ['income', 'balance', 'cashflow', 'stock_info']:
    cols = [c[1] for c in db.execute(f'PRAGMA table_info({table})')]
    print(f'{table}: {cols}')
    sample = db.execute(f'SELECT * FROM {table} LIMIT 1').fetchone()
    if sample:
        print(f'  sample: {sample}')
db.close()
