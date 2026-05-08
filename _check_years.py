import sqlite3
db = sqlite3.connect('/home/xxxsuli/bqas-model/bqas/data/cache/bqas.db')
for table in ['income', 'balance', 'cashflow']:
    rows = db.execute(f'SELECT DISTINCT report_period FROM {table} ORDER BY report_period').fetchall()
    print(f'{table}: {[r[0] for r in rows]}')
db.close()
