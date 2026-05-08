"""Fetch CSI 300/500 constituents and cross-reference with BQAS DB."""
import sqlite3
import json
import subprocess
import re

db_path = '/home/xxxsuli/bqas-model/bqas/data/cache/bqas.db'
db = sqlite3.connect(db_path)

# Create index_constituents table if not exists
db.execute("""
CREATE TABLE IF NOT EXISTS index_constituents (
    code TEXT,
    name TEXT,
    index_name TEXT,
    PRIMARY KEY (code, index_name)
)
""")
db.commit()

# Try fetching CSI 300 via curl (East Money)
def fetch_index_via_curl(board_code, index_name):
    """Fetch index constituents via East Money API."""
    codes = []
    for page in range(1, 6):  # 500 stocks max at 100 per page
        url = f"http://push2.eastmoney.com/api/qt/clist/get?pn={page}&pz=100&fs=b:{board_code}&fields=f12,f14"
        try:
            result = subprocess.run(
                ['curl', '-s', '--connect-timeout', '10', '--max-time', '15', url],
                capture_output=True, text=True, timeout=20
            )
            data = json.loads(result.stdout)
            items = data.get('data', {}).get('diff', [])
            if not items:
                break
            for item in items:
                codes.append((item['f12'], item['f14']))
            if len(items) < 100:
                break
        except Exception as e:
            print(f"  Error fetching page {page}: {e}")
            break
    return codes

print("Fetching CSI 300 constituents...")
csi300 = fetch_index_via_curl('BK0500', 'CSI300')
print(f"  Got {len(csi300)} stocks")

print("Fetching CSI 500 constituents...")
csi500 = fetch_index_via_curl('BK0700', 'CSI500')
print(f"  Got {len(csi500)} stocks")

# Save to DB
if csi300:
    db.executemany("INSERT OR REPLACE INTO index_constituents VALUES (?, ?, 'CSI300')", 
                   [(c, n) for c, n in csi300])
if csi500:
    db.executemany("INSERT OR REPLACE INTO index_constituents VALUES (?, ?, 'CSI500')", 
                   [(c, n) for c, n in csi500])
db.commit()

# Verify
cnt300 = db.execute("SELECT COUNT(*) FROM index_constituents WHERE index_name='CSI300'").fetchone()[0]
cnt500 = db.execute("SELECT COUNT(*) FROM index_constituents WHERE index_name='CSI500'").fetchone()[0]
print(f"\nDB: CSI300={cnt300}, CSI500={cnt500}")

db.close()
print("\nDone!")
