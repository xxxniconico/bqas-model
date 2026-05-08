import sqlite3
db = sqlite3.connect('/home/xxxsuli/bqas-model/bqas/data/cache/bqas.db')

# Check stock_info data quality
print("=== stock_info ===")
total = db.execute("SELECT COUNT(*) FROM stock_info").fetchone()[0]
print(f"Total: {total}")
null_industry = db.execute("SELECT COUNT(*) FROM stock_info WHERE industry_sw IS NULL").fetchone()[0]
print(f"NULL industry_sw: {null_industry} ({null_industry/total*100:.0f}%)")
null_shares = db.execute("SELECT COUNT(*) FROM stock_info WHERE total_shares IS NULL OR total_shares = 0").fetchone()[0]
print(f"NULL/0 total_shares: {null_shares} ({null_shares/total*100:.0f}%)")

# Sample non-null industries
print("\n=== Non-null industry samples ===")
for row in db.execute("SELECT code, name, industry_sw FROM stock_info WHERE industry_sw IS NOT NULL LIMIT 15"):
    print(f"  {row}")

# Check income 2024 data
print("\n=== income 2024 ===")
cnt = db.execute("SELECT COUNT(*) FROM income WHERE report_period LIKE '2024%'").fetchone()[0]
print(f"2024 records: {cnt}")
cnt2 = db.execute("SELECT COUNT(*) FROM income WHERE report_period LIKE '2023%'").fetchone()[0]
print(f"2023 records: {cnt2}")

# Check if there are multiple periods per stock for 2024
multi = db.execute("""
    SELECT code, COUNT(*) as cnt FROM income 
    WHERE report_period LIKE '2024%' 
    GROUP BY code HAVING cnt > 1 LIMIT 5
""").fetchall()
print(f"\nStocks with multiple 2024 periods: {len(multi)}")
for m in multi:
    periods = db.execute("SELECT report_period FROM income WHERE code=? AND report_period LIKE '2024%'", (m[0],)).fetchall()
    print(f"  {m[0]}: {periods}")

db.close()
