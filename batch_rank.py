"""
Quick ranker + CSI 300/500 cross-reference for BQAS analysis.
Does batch scoring using pandas on the full financial dataset.
"""
import sys, os
os.chdir('/home/xxxsuli/bqas-model')
sys.path.insert(0, '/home/xxxsuli/bqas-model')

import sqlite3
import pandas as pd
import numpy as np
import json

db_path = '/home/xxxsuli/bqas-model/bqas/data/cache/bqas.db'

# ============================================================
# Step 1: Fetch CSI 300/500 constituents
# ============================================================
print("Fetching index constituents via akshare...")
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
    import akshare as ak
    df500 = ak.index_stock_cons(symbol="000905")  
    csi500_codes = set(df500['品种代码'].astype(str).str.zfill(6).tolist())
    print(f"  CSI 500: {len(csi500_codes)} stocks")
except Exception as e:
    print(f"  CSI 500 failed: {e}")

both = csi300_codes | csi500_codes
print(f"  Combined: {len(both)} unique stocks\n")

# ============================================================
# Step 2: Load DB into pandas 
# ============================================================
print("Loading financial data from DB...")
db = sqlite3.connect(db_path)

# Get stock info
stocks = pd.read_sql("SELECT code, name, industry_sw, total_shares, listing_date FROM stock_info", db)
print(f"  Stocks: {len(stocks)}")

# Get latest year's income + balance + cashflow (use 2024 annual)  
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
    SELECT code, report_period, operating_cf, capex, financing_cf
    FROM cashflow WHERE report_period LIKE '2024%'
""", db)

# Filter to stocks with all 3 statements
valid = set(income['code']) & set(balance['code']) & set(cashflow['code'])
print(f"  Stocks with full 2024 data: {len(valid)}")

# Merge into wide format
df = stocks[stocks['code'].isin(valid)].copy()
df = df.merge(income[['code', 'revenue', 'operating_profit', 'net_income', 'interest_expense', 'ebit']], on='code', how='left')
df = df.merge(balance[['code', 'total_assets', 'total_liabilities', 'equity', 'goodwill', 'inventory', 'accounts_receivable', 'short_term_debt', 'long_term_debt', 'cash_equiv']], on='code', how='left')
df = df.merge(cashflow[['code', 'operating_cf', 'capex']], on='code', how='left')

# Clean numeric columns
for c in ['revenue', 'operating_profit', 'net_income', 'interest_expense', 'ebit',
          'total_assets', 'total_liabilities', 'equity', 'goodwill', 'inventory',
          'accounts_receivable', 'short_term_debt', 'long_term_debt', 'cash_equiv',
          'operating_cf', 'capex']:
    df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

# ============================================================
# Step 3: Industry mapping
# ============================================================
industry_map = {
    '医药生物': '医疗', '医疗服务': '医疗', '医疗器械': '医疗', '中药': '医疗', '化学制药': '医疗',
    '食品饮料': '消费', '白酒': '消费', '饮料制造': '消费', '食品加工': '消费', '家用电器': '消费',
    '银行': '金融', '证券': '金融', '保险': '金融', '多元金融': '金融', '房地产': '金融',
    '计算机': '信息技术', '电子': '信息技术', '通信': '信息技术', '传媒': '信息技术', '互联网': '信息技术',
    '汽车': '工业', '机械设备': '工业', '电气设备': '工业', '国防军工': '工业', '建筑装饰': '工业',
    '化工': '材料', '钢铁': '材料', '有色金属': '材料', '建筑材料': '材料', '采掘': '材料',
    '公用事业': '能源', '采掘': '能源', '交通运输': '能源',
}

def map_industry(sw):
    if not sw or pd.isna(sw):
        return '其他'
    for k, v in industry_map.items():
        if k in str(sw):
            return v
    return '其他'

df['industry'] = df['industry_sw'].apply(map_industry)
print(f"\n  Industry distribution:")
print(df['industry'].value_counts().to_string())

# ============================================================
# Step 4: Factor calculation (simplified batch version)
# ============================================================
print("\nCalculating factors...")

# Quality factors
df['roe'] = np.where(df['equity'] > 0, df['net_income'] / df['equity'], 0)
df['roe_score'] = np.clip(df['roe'] / 0.30 * 10, 0, 10)

# ROIC
invested = df['equity'] + df['short_term_debt'] + df['long_term_debt']
df['roic'] = np.where(invested > 0, df['ebit'] / invested, 0)
df['roic_score'] = np.clip(df['roic'] / 0.30 * 10, 0, 10)

# CFO quality
df['cfo_quality'] = np.where(df['net_income'] != 0, df['operating_cf'] / df['net_income'], 0)
df['cfo_score'] = np.clip(df['cfo_quality'] / 2.0 * 10, 0, 10)

# Gross margin
df['gross_margin'] = np.where(df['revenue'] > 0, (df['revenue'] - (df['revenue'] - df['operating_profit'])) / df['revenue'], 0)
df['gm_score'] = np.clip(df['gross_margin'] / 0.50 * 10, 0, 10)

# Value factors  
df['ev'] = df['equity'] + df['total_liabilities'] - df['cash_equiv']
df['ev_oe_ratio'] = np.where(df['operating_profit'] > 0, df['ev'] / df['operating_profit'], 100)
df['evoe_score'] = np.clip(20 / df['ev_oe_ratio'].clip(lower=1) * 2, 0, 10)

# FCF Yield  
fcf = df['operating_cf'] - df['capex'].abs()
df['fcf_yield'] = np.where(df['ev'] > 0, fcf / df['ev'], 0)
df['fcf_score'] = np.clip(df['fcf_yield'] / 0.10 * 10, 0, 10)

# Health factors
df['debt_ratio'] = np.where(df['total_assets'] > 0, df['total_liabilities'] / df['total_assets'], 0)
df['debt_score'] = np.clip((1 - df['debt_ratio']) / 0.70 * 10, 0, 10)

df['interest_coverage'] = np.where(df['interest_expense'] > 0, df['operating_profit'] / df['interest_expense'], 100)
df['ic_score'] = np.clip(df['interest_coverage'] / 10 * 5, 0, 10)

# Asset quality  
df['goodwill_ratio'] = np.where(df['equity'] > 0, df['goodwill'] / df['equity'], 0)
df['aq_score'] = np.clip((1 - df['goodwill_ratio'].clip(0,1)) * 10, 0, 10)

# ============================================================
# Step 5: Industry-specific weights and scoring
# ============================================================
weights = {
    '医疗': {'roe': 0.12, 'roic': 0.10, 'gm': 0.018, 'cfo': 0.112, 'evoe': 0.10, 'fcf': 0.10, 'debt': 0.15, 'ic': 0.10, 'aq': 0.05},
    '消费': {'roe': 0.10, 'roic': 0.08, 'gm': 0.025, 'cfo': 0.145, 'evoe': 0.10, 'fcf': 0.10, 'debt': 0.15, 'ic': 0.10, 'aq': 0.05},
    '金融': {'roe': 0.08, 'roic': 0.06, 'gm': 0.01,  'cfo': 0.20,  'evoe': 0.10, 'fcf': 0.10, 'debt': 0.15, 'ic': 0.10, 'aq': 0.05},
    '信息技术': {'roe': 0.12, 'roic': 0.12, 'gm': 0.02, 'cfo': 0.09,  'evoe': 0.10, 'fcf': 0.10, 'debt': 0.15, 'ic': 0.10, 'aq': 0.05},
    '工业': {'roe': 0.10, 'roic': 0.10, 'gm': 0.015, 'cfo': 0.135, 'evoe': 0.10, 'fcf': 0.10, 'debt': 0.15, 'ic': 0.10, 'aq': 0.05},
    '材料': {'roe': 0.10, 'roic': 0.10, 'gm': 0.01,  'cfo': 0.14,  'evoe': 0.10, 'fcf': 0.10, 'debt': 0.15, 'ic': 0.10, 'aq': 0.05},
    '能源': {'roe': 0.10, 'roic': 0.10, 'gm': 0.01,  'cfo': 0.14,  'evoe': 0.10, 'fcf': 0.10, 'debt': 0.15, 'ic': 0.10, 'aq': 0.05},
    '其他': {'roe': 0.10, 'roic': 0.10, 'gm': 0.015, 'cfo': 0.135, 'evoe': 0.10, 'fcf': 0.10, 'debt': 0.15, 'ic': 0.10, 'aq': 0.05},
}

def calc_score(row):
    ind = row['industry']
    w = weights.get(ind, weights['其他'])
    
    quality = (
        w['roe'] * row['roe_score'] +
        w['roic'] * row['roic_score'] +
        w['gm'] * row['gm_score'] +
        w['cfo'] * row['cfo_score']
    ) / (w['roe'] + w['roic'] + w['gm'] + w['cfo']) * 35
    
    value = (
        w['evoe'] * row['evoe_score'] +
        w['fcf'] * row['fcf_score']
    ) / (w['evoe'] + w['fcf']) * 30
    
    health = (
        w['debt'] * row['debt_score'] +
        w['ic'] * row['ic_score'] +
        w['aq'] * row['aq_score']
    ) / (w['debt'] + w['ic'] + w['aq']) * 20
    
    gov = 3.5  # Default governance score
    
    return quality + value + health + gov

df['total_score'] = df.apply(calc_score, axis=1)

# ============================================================
# Step 6: Neutralize (optional)
# ============================================================
neutralize = True
if neutralize:
    for ind in df['industry'].unique():
        mask = df['industry'] == ind
        if mask.sum() < 3:
            continue
        ind_mean = df.loc[mask, 'total_score'].mean()
        ind_std = df.loc[mask, 'total_score'].std()
        if ind_std > 0:
            df.loc[mask, 'total_score'] = (df.loc[mask, 'total_score'] - ind_mean) / ind_std * 10 + 60

# ============================================================
# Step 7: Top 50 & Cross-reference
# ============================================================
top50 = df.nlargest(50, 'total_score')[['code', 'name', 'industry', 'total_score', 'roe', 'gross_margin', 'fcf_yield']]

print(f"\n{'='*70}")
print(f"TOP 30 (neutralized) — with CSI 300/500 overlap")
print(f"{'='*70}")
print(f"{'#':<4} {'Code':<8} {'Name':<10} {'Score':<7} {'Ind':<8} {'CSI300':<8} {'CSI500':<8}")
print("-"*70)

outsiders = []
for i, (_, row) in enumerate(top50.head(30).iterrows()):
    code = row['code']
    in300 = code in csi300_codes
    in500 = code in csi500_codes
    print(f"{i+1:<4} {code:<8} {row['name']:<10} {row['total_score']:<7.1f} {row['industry']:<8} {'✓' if in300 else '':<8} {'✓' if in500 else '':<8}")
    
    if not in300 and not in500:
        outsiders.append(row)

# ============================================================
# Step 8: Analyze outsiders
# ============================================================
print(f"\n{'='*70}")
print(f"OUTSIDERS: {len(outsiders)} stocks not in CSI 300 or CSI 500")
print(f"{'='*70}")

if len(outsiders) == 0:
    print("No outsiders found — all Top 30 are in major indices.")
else:
    for row in outsiders:
        code = row['code']
        name = row['name']
        
        # Get detailed info from DB
        info = db.execute("SELECT total_shares, listing_date FROM stock_info WHERE code=?", (code,)).fetchone()
        meta = db.execute("SELECT audit_opinion, pledge_ratio FROM meta WHERE code=?", (code,)).fetchone()
        
        print(f"\n  {code} {name}  总分: {row['total_score']:.1f}  行业: {row['industry']}")
        
        if info:
            shares = info[0] or 0
            print(f"    总股本: {shares/1e8:.1f}亿  上市: {info[1]}")
        
        print(f"    ROE: {row['roe']*100:.1f}%  毛利率: {row['gross_margin']*100:.1f}%  FCF Yield: {row['fcf_yield']*100:.1f}%")
        
        if meta:
            audit = meta[0] or '未知'
            pledge = meta[1] or 0
            print(f"    审计意见: {audit}  质押比例: {pledge}%")
        
        # Verdict
        roe_pct = row['roe'] * 100
        gm_pct = row['gross_margin'] * 100
        fcf_y = row['fcf_yield'] * 100
        
        if roe_pct > 15 and fcf_y > 3:
            verdict = "✅ 价值发现 — 高ROE+正现金流，被指数遗漏的优质中小盘"
        elif roe_pct < 5:
            verdict = "⚠️ 数据异常 — ROE过低，总分虚高可能是因子计算错误"
        elif gm_pct < 10:
            verdict = "⚠️ 存疑 — 毛利率极低，可能是数据问题或非主营驱动"
        else:
            verdict = "🔍 中性 — 基本面中等，需进一步人工审查"
        
        print(f"    判断: {verdict}")

# Step 9: Summary stats
print(f"\n{'='*70}")
print("OVERLAP SUMMARY")
print(f"{'='*70}")

top30_codes = set(top50.head(30)['code'].tolist())
in300 = top30_codes & csi300_codes
in500 = top30_codes & csi500_codes
in_either = in300 | in500
neither = top30_codes - in_either

print(f"  CSI 300 overlap:       {len(in300)}/30 ({len(in300)/30*100:.0f}%)")
print(f"  CSI 500 overlap:       {len(in500)}/30 ({len(in500)/30*100:.0f}%)")
print(f"  In either index:       {len(in_either)}/30 ({len(in_either)/30*100:.0f}%)")
print(f"  Not in either:         {len(neither)}/30 ({len(neither)/30*100:.0f}%)")

# Industry breakdown
print(f"\n  Top 30 industry breakdown:")
for ind in top50.head(30)['industry'].value_counts().index:
    cnt = (top50.head(30)['industry'] == ind).sum()
    print(f"    {ind}: {cnt}")

db.close()
print("\nDone!")
