"""
Full analysis: neutralized Top 30 vs CSI 300/500 overlap.
Identifies outsiders and checks if they're value discoveries or data anomalies.
"""
import sys
sys.path.insert(0, '/home/xxxsuli/bqas-model')

import sqlite3
import json

db_path = '/home/xxxsuli/bqas-model/bqas/data/cache/bqas.db'
db = sqlite3.connect(db_path)

# Step 1: Try fetching CSI 300/500 via akshare
print("Fetching CSI 300/500 constituents via akshare...")
try:
    import akshare as ak
    # CSI 300
    df300 = ak.index_stock_cons(symbol="000300")
    csi300_codes = set(df300['品种代码'].astype(str).tolist())
    csi300_names = dict(zip(df300['品种代码'].astype(str), df300['品种名称']))
    print(f"  CSI 300: {len(csi300_codes)} stocks")
    
    # CSI 500
    df500 = ak.index_stock_cons(symbol="000399")
    csi500_codes = set(df500['品种代码'].astype(str).tolist())
    csi500_names = dict(zip(df500['品种代码'].astype(str), df500['品种名称']))
    print(f"  CSI 500: {len(csi500_codes)} stocks")
    
    both = csi300_codes | csi500_codes
    
    # Save to DB for future use
    db.execute("CREATE TABLE IF NOT EXISTS index_constituents (code TEXT, name TEXT, index_name TEXT, PRIMARY KEY (code, index_name))")
    db.executemany("INSERT OR REPLACE INTO index_constituents VALUES (?, ?, 'CSI300')",
                   [(c, csi300_names.get(c, '')) for c in csi300_codes])
    db.executemany("INSERT OR REPLACE INTO index_constituents VALUES (?, ?, 'CSI500')",
                   [(c, csi500_names.get(c, '')) for c in csi500_codes])
    db.commit()
    print(f"  Saved to DB")
    
except Exception as e:
    print(f"  akshare failed: {e}")
    print("  Using DB fallback...")
    # Try from DB
    csi300_codes = set(row[0] for row in db.execute("SELECT code FROM index_constituents WHERE index_name='CSI300'"))
    csi500_codes = set(row[0] for row in db.execute("SELECT code FROM index_constituents WHERE index_name='CSI500'"))
    both = csi300_codes | csi500_codes
    print(f"  DB: CSI300={len(csi300_codes)}, CSI500={len(csi500_codes)}")

# Step 2: Get neutralized Top 30 from BQAS engine
print("\nComputing neutralized Top 30...")
from bqas.engine.ranker import Ranker

r = Ranker()
r.neutralize = True
top30 = r.rank_universe(limit=50)  # Get 50 to find 30 with data

print(f"Top 30 (neutralized):")
for i, (code, name, score) in enumerate(top30[:30]):
    in300 = "✓" if code in csi300_codes else " "
    in500 = "✓" if code in csi500_codes else " "
    print(f"  {i+1:2d}. {code} {name:8s} {score:5.1f}  CSI300[{in300}] CSI500[{in500}]")

# Step 3: Find outsiders (not in either index)
outsiders = [(code, name, score) for code, name, score in top30[:30] if code not in both]
print(f"\n{'='*60}")
print(f"OUTSIDERS: {len(outsiders)} stocks not in CSI 300 or CSI 500")
print(f"{'='*60}")

for code, name, score in outsiders:
    # Get financial details
    row = db.execute("""
        SELECT code, name, total_shares, listing_date, industry_sw 
        FROM stock_info WHERE code=?
    """, (code,)).fetchone()
    
    # Get latest financials
    fin = db.execute("""
        SELECT i.report_period, i.net_income, i.revenue, i.roi,
               b.total_assets, b.total_liabilities, b.equity,
               c.operating_cf
        FROM income i
        JOIN balance b ON i.code=b.code AND i.report_period=b.report_period
        JOIN cashflow c ON i.code=c.code AND i.report_period=c.report_period
        WHERE i.code=? 
        ORDER BY i.report_period DESC LIMIT 1
    """, (code,)).fetchone()
    
    # Get audit/pledge info
    meta = db.execute("SELECT audit_opinion, pledge_ratio FROM meta WHERE code=?", (code,)).fetchone()
    
    print(f"\n  {code} {name} 总分: {score:.1f}")
    if row:
        shares = row[2] or 0
        industry = row[4] or '未知'
        print(f"    总股本: {shares/1e8:.1f}亿  行业: {industry}")
        # Simple market cap estimate
        # Try to get price from quotes table
        quote = db.execute("SELECT close FROM quotes WHERE code=? ORDER BY trade_date DESC LIMIT 1", (code,)).fetchone()
        if quote and shares:
            mcap = quote[0] * shares / 1e8
            print(f"    估算市值: {mcap:.0f}亿  (股价: {quote[0]:.1f})")
    
    if fin:
        ni = fin[2] or 0
        rev = fin[3] or 0
        roe = (ni / fin[6] * 100) if fin[6] and fin[6] > 0 else 0
        print(f"    净利润: {ni/1e8:.1f}亿  营收: {rev/1e8:.1f}亿  ROE: {roe:.1f}%")
        print(f"    现金流: {fin[8]/1e8 if fin[8] else 0:.1f}亿  杠杆: {fin[5]/fin[4]*100 if fin[4] else 0:.0f}%")
    
    if meta:
        audit = meta[0] or '未知'
        pledge = meta[1] or 0
        print(f"    审计意见: {audit}  质押比例: {pledge}%")
    
    # Verdict
    print(f"    判断: ", end='')
    if not fin or not row:
        print("⚠️ 数据缺失 - 疑似异常")
    elif roe > 15 and fin[8] and fin[8] > ni * 0.8:
        print("✅ 价值发现 - 高ROE+现金流扎实")
    elif roe < 5:
        print("⚠️ 疑似数据异常 - ROE过低")
    else:
        print("🔍 存疑 - 需人工审查")

db.close()
print("\nDone!")
