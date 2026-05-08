"""
BQAS batch ranker WITHOUT neutralization + CSI 300/500 cross-reference.
Fixed: handles NULL industry, skips neutralization.
"""
import sys, os
os.chdir('/home/xxxsuli/bqas-model')
sys.path.insert(0, '/home/xxxsuli/bqas-model')

import sqlite3
import pandas as pd
import numpy as np
from collections import Counter

db_path = '/home/xxxsuli/bqas-model/bqas/data/cache/bqas.db'
db = sqlite3.connect(db_path)

# ============================================================
# Step 1: Fetch CSI 300/500 constituents via akshare
# ============================================================
print("Fetching index constituents...")
csi300_codes = set()
csi500_codes = set()

try:
    import akshare as ak
    df300 = ak.index_stock_cons(symbol="000300")
    csi300_codes = set(df300['品种代码'].astype(str).str.zfill(6).tolist())
    print(f"  CSI 300: {len(csi300_codes)} stocks")
except Exception as e:
    print(f"  CSI 300 failed: {e}")

try:
    df500 = ak.index_stock_cons(symbol="000905")  
    csi500_codes = set(df500['品种代码'].astype(str).str.zfill(6).tolist())
    print(f"  CSI 500: {len(csi500_codes)} stocks")
except Exception as e:
    print(f"  CSI 500 failed: {e}")

both = csi300_codes | csi500_codes
print(f"  Combined: {len(both)} unique stocks")

# ============================================================
# Step 2: Load financial data
# ============================================================
print("\nLoading financial data...")
stocks = pd.read_sql("SELECT code, name, industry_sw FROM stock_info", db)
print(f"  Stocks: {len(stocks)}")

income = pd.read_sql("""
    SELECT code, report_period, revenue, operating_profit, net_income, 
           interest_expense,
           (operating_profit + interest_expense) as ebit
    FROM income WHERE report_period LIKE '2024%'
""", db)

balance = pd.read_sql("""
    SELECT code, report_period, total_assets, total_liabilities, equity,
           goodwill, inventory, accounts_receivable,
           short_term_debt, long_term_debt, cash_equiv
    FROM balance WHERE report_period LIKE '2024%'
""", db)

cashflow = pd.read_sql("""
    SELECT code, report_period, operating_cf, capex
    FROM cashflow WHERE report_period LIKE '2024%'
""", db)

# Filter to stocks with full data
valid = set(income['code']) & set(balance['code']) & set(cashflow['code'])
print(f"  Stocks with full 2024 data: {len(valid)}")

# Merge
df = stocks[stocks['code'].isin(valid)].copy()
df = df.merge(income[['code', 'revenue', 'operating_profit', 'net_income', 'interest_expense', 'ebit']], on='code', how='left')
df = df.merge(balance[['code', 'total_assets', 'total_liabilities', 'equity', 'goodwill', 'inventory', 'accounts_receivable', 'short_term_debt', 'long_term_debt', 'cash_equiv']], on='code', how='left')
df = df.merge(cashflow[['code', 'operating_cf', 'capex']], on='code', how='left')

for c in ['revenue', 'operating_profit', 'net_income', 'interest_expense', 'ebit',
          'total_assets', 'total_liabilities', 'equity', 'goodwill', 'inventory',
          'accounts_receivable', 'short_term_debt', 'long_term_debt', 'cash_equiv',
          'operating_cf', 'capex']:
    df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

# ============================================================
# Step 3: Industry mapping  
# ============================================================
# Since 99.9% of stock_info.industry_sw is NULL, infer from sector prefix
def infer_sector(code):
    """Infer broad sector from stock code prefix + known mappings."""
    # Known stocks from enrichment
    known = {
        '000661': '医疗', '300896': '消费', '000858': '消费', '600519': '消费',
    }
    if code in known:
        return known[code]
    
    # Shanghai main board: 600xxx, 601xxx, 603xxx, 605xxx
    if code.startswith('600') or code.startswith('601') or code.startswith('603') or code.startswith('605'):
        return '工业'  # Default for main board (diverse)
    # Shenzhen main board: 000xxx, 001xxx, 002xxx
    if code.startswith('000') or code.startswith('001') or code.startswith('002'):
        return '工业'
    # ChiNext: 300xxx
    if code.startswith('300') or code.startswith('301'):
        return '信息技术'
    # STAR: 688xxx
    if code.startswith('688'):
        return '信息技术'
    return '其他'

df['industry'] = df['code'].apply(infer_sector)

ind_counts = df['industry'].value_counts()
print(f"\n  Industry distribution (inferred):")
print(ind_counts.to_string())

# ============================================================
# Step 4: Factor calculation
# ============================================================
print("\nCalculating factors...")

# Quality
df['roe'] = np.where(df['equity'] > 0, df['net_income'] / df['equity'], 0)
df['roe_score'] = np.clip(df['roe'] / 0.30 * 10, 0, 10)

invested = df['equity'] + df['short_term_debt'] + df['long_term_debt']
df['roic'] = np.where(invested > 0, df['ebit'] / invested, 0)
df['roic_score'] = np.clip(df['roic'] / 0.30 * 10, 0, 10)

df['cfo_quality'] = np.where(df['net_income'] != 0, df['operating_cf'] / df['net_income'], 0)
df['cfo_score'] = np.clip(df['cfo_quality'] / 2.0 * 10, 0, 10)

# Gross margin: (revenue - COGS) / revenue. Approximate COGS as revenue - operating_profit - SG&A
# Simplified: use operating_profit / revenue as proxy (not exact but directional)
df['gross_margin'] = np.where(df['revenue'] > 0, df['operating_profit'] / df['revenue'], 0)
df['gm_score'] = np.clip(np.abs(df['gross_margin']) / 0.50 * 10, 0, 10)

# Value
df['ev'] = df['equity'] + df['total_liabilities'] - df['cash_equiv']
df['ev_oe_ratio'] = np.where(df['operating_profit'] > 1e6, df['ev'] / df['operating_profit'], 100)
df['evoe_score'] = np.clip(20 / df['ev_oe_ratio'].clip(lower=1) * 2, 0, 10)

fcf = df['operating_cf'] - df['capex'].abs()
df['fcf_yield'] = np.where(df['ev'] > 0, fcf / df['ev'], 0)
df['fcf_score'] = np.clip(df['fcf_yield'] / 0.10 * 10, 0, 10)

# Health  
df['debt_ratio'] = np.where(df['total_assets'] > 0, df['total_liabilities'] / df['total_assets'], 0)
df['debt_score'] = np.clip((1 - df['debt_ratio']) / 0.70 * 10, 0, 10)

df['interest_coverage'] = np.where(df['interest_expense'] > 1e6, df['operating_profit'] / df['interest_expense'], 100)
df['ic_score'] = np.clip(df['interest_coverage'] / 10 * 5, 0, 10)

df['goodwill_ratio'] = np.where(df['equity'] > 0, df['goodwill'] / df['equity'], 0)
df['aq_score'] = np.clip((1 - df['goodwill_ratio'].clip(0,1)) * 10, 0, 10)

# ============================================================
# Step 5: Uniform weights (no industry differentiation since it's missing)
# ============================================================
w = {
    'roe': 0.10, 'roic': 0.08, 'gm': 0.02, 'cfo': 0.15,
    'evoe': 0.15, 'fcf': 0.15,
    'debt': 0.15, 'ic': 0.05, 'aq': 0.05
}
# Normalize per dimension
qual_w = w['roe'] + w['roic'] + w['gm'] + w['cfo']
val_w = w['evoe'] + w['fcf']
hlth_w = w['debt'] + w['ic'] + w['aq']

df['quality'] = (
    w['roe'] * df['roe_score'] + 
    w['roic'] * df['roic_score'] + 
    w['gm'] * df['gm_score'] + 
    w['cfo'] * df['cfo_score']
) / qual_w * 35

df['value'] = (
    w['evoe'] * df['evoe_score'] + 
    w['fcf'] * df['fcf_score']
) / val_w * 30

df['health'] = (
    w['debt'] * df['debt_score'] + 
    w['ic'] * df['ic_score'] + 
    w['aq'] * df['aq_score']
) / hlth_w * 20

# Governance fixed at 3.5 (no data)
df['gov'] = 3.5

df['total_score'] = df['quality'] + df['value'] + df['health'] + df['gov']

# Filter outliers (scores > 100 or < 0)
df = df[(df['total_score'] >= 0) & (df['total_score'] <= 100)]

print(f"\n  Score range: {df['total_score'].min():.1f} - {df['total_score'].max():.1f}")
print(f"  Mean: {df['total_score'].mean():.1f}, Median: {df['total_score'].median():.1f}")

# ============================================================
# Step 6: Top 50 & Cross-reference
# ============================================================
top50 = df.nlargest(50, 'total_score')[['code', 'name', 'industry', 'total_score', 'roe', 'fcf_yield', 'debt_ratio']]

print(f"\n{'='*80}")
print(f"TOP 30 — with CSI 300/500 overlap")
print(f"{'='*80}")
print(f"{'#':<4} {'Code':<8} {'Name':<12} {'Score':<7} {'Ind':<8} {'CSI300':<8} {'CSI500':<8}")
print("-"*80)

outsiders = []
for i, (_, row) in enumerate(top50.head(30).iterrows()):
    code = row['code']
    in300 = code in csi300_codes
    in500 = code in csi500_codes
    print(f"{i+1:<4} {code:<8} {row['name']:<12} {row['total_score']:<7.1f} {row['industry']:<8} {'✓' if in300 else '':<8} {'✓' if in500 else '':<8}")
    
    if not in300 and not in500:
        outsiders.append(row)

# ============================================================
# Step 7: Analyze outsiders
# ============================================================
print(f"\n{'='*80}")
print(f"OUTSIDERS: {len(outsiders)} stocks not in CSI 300 or CSI 500")
print(f"{'='*80}")

if len(outsiders) == 0:
    print("No outsiders found.")
else:
    for row in outsiders:
        code = row['code']
        name = row['name']
        
        info = db.execute("SELECT total_shares, listing_date, industry_sw FROM stock_info WHERE code=?", (code,)).fetchone()
        meta = db.execute("SELECT audit_opinion, pledge_ratio FROM meta WHERE code=?", (code,)).fetchone()
        
        roe_pct = row['roe'] * 100
        fcf_y = row['fcf_yield'] * 100
        debt_r = row['debt_ratio'] * 100
        
        print(f"\n  {code} {name}  总分: {row['total_score']:.1f}  行业推断: {row['industry']}")
        
        if info:
            shares = info[0] or 0
            sw_ind = info[2] or '未分类'
            print(f"    申万行业: {sw_ind}  总股本: {shares/1e8 if shares else '?'}亿  上市: {info[1]}")
        
        print(f"    ROE: {roe_pct:.1f}%  FCF Yield: {fcf_y:.1f}%  负债率: {debt_r:.1f}%")
        
        if meta:
            audit = meta[0] or '未知'
            pledge = meta[1] or 0
            print(f"    审计意见: {audit}  质押比例: {pledge}%")
        
        # Quality check: FCF Yield > 100% is a red flag (EV calculation issue for tiny companies)
        if fcf_y > 100:
            verdict = "⚠️ 数据异常 — FCF Yield > 100% (EV极小，小市值公司分子膨胀)"
        elif roe_pct > 30 and fcf_y > 10:
            verdict = "✅ 价值发现 — 高ROE+健康现金流，被指数遗漏的优质中小盘"
        elif roe_pct < 5:
            verdict = "⚠️ 数据异常 — ROE过低"
        elif debt_r > 80:
            verdict = "⚠️ 存疑 — 高负债率，财务风险大"
        else:
            verdict = "🔍 中性 — 需进一步审查"
        
        print(f"    判断: {verdict}")

# Step 8: Summary
print(f"\n{'='*80}")
print("OVERLAP SUMMARY")
print(f"{'='*80}")

top30_codes = set(top50.head(30)['code'].tolist())
in300 = top30_codes & csi300_codes
in500 = top30_codes & csi500_codes
in_either = in300 | in500
neither = top30_codes - in_either

print(f"  CSI 300 overlap:       {len(in300)}/30 ({len(in300)/30*100:.0f}%)")
print(f"  CSI 500 overlap:       {len(in500)}/30 ({len(in500)/30*100:.0f}%)")
print(f"  In either index:       {len(in_either)}/30 ({len(in_either)/30*100:.0f}%)")
print(f"  Not in either:         {len(neither)}/30 ({len(neither)/30*100:.0f}%)")

print(f"\n  Top 30 sector breakdown (inferred):")
for ind, cnt in Counter(top50.head(30)['industry']).most_common():
    print(f"    {ind}: {cnt}")

db.close()
print("\nDone!")
