"""
BQAS batch ranker v3 — multi-year factor smoothing (DEFAULT).
Uses 3-year rolling averages for quality/health factors plus Revenue CAGR.
Valuation factors use latest single-year data.
EV filtering + FCF Yield capping for data sanity.
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

END_YEAR = 2024  # use 2022+2023+2024 rolling window

# Step 1: Fetch index constituents
print("Fetching CSI 300/500...")
csi300_codes = set()
csi500_codes = set()

try:
    import akshare as ak
    df300 = ak.index_stock_cons(symbol="000300")
    csi300_codes = set(df300['品种代码'].astype(str).str.zfill(6).tolist())
    print(f"  CSI 300: {len(csi300_codes)}")
except Exception as e:
    print(f"  CSI 300 failed: {e}")

try:
    df500 = ak.index_stock_cons(symbol="000905")
    csi500_codes = set(df500['品种代码'].astype(str).str.zfill(6).tolist())
    print(f"  CSI 500: {len(csi500_codes)}")
except Exception as e:
    print(f"  CSI 500 failed: {e}")

both = csi300_codes | csi500_codes

# Step 2: Load 3-year data
years = [END_YEAR - 2, END_YEAR - 1, END_YEAR]
periods = [f'{y}1231' for y in years]
print(f"\nLoading 3-year financial data ({years[0]}-{years[-1]})...")

stocks = pd.read_sql("SELECT code, name FROM stock_info", db)

income = pd.read_sql(f"""
    SELECT code, report_period, revenue, operating_profit, net_income, interest_expense
    FROM income WHERE report_period IN ({','.join(['?']*3)})
""", db, params=periods)

balance = pd.read_sql(f"""
    SELECT code, report_period, total_assets, total_liabilities, equity,
           goodwill, short_term_debt, long_term_debt, cash_equiv
    FROM balance WHERE report_period IN ({','.join(['?']*3)})
""", db, params=periods)

cashflow = pd.read_sql(f"""
    SELECT code, report_period, operating_cf, capex
    FROM cashflow WHERE report_period IN ({','.join(['?']*3)})
""", db, params=periods)

for df in [income, balance, cashflow]:
    for c in df.columns:
        if c not in ('code', 'report_period', 'name'):
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

print(f"  Raw: income={len(income)} balance={len(balance)} cashflow={len(cashflow)}")

# Merge into one flat frame
df = income.merge(balance, on=['code', 'report_period'], how='inner')
df = df.merge(cashflow, on=['code', 'report_period'], how='inner')
print(f"  Merged: {len(df)} rows, {df['code'].nunique()} unique stocks")

# Per-year computed columns
df['ebit'] = df['operating_profit'] + df['interest_expense']
df['invested'] = df['equity'] + df['short_term_debt'] + df['long_term_debt']
df['roe'] = np.where(df['equity'] > 1e6, df['net_income'] / df['equity'], 0)
df['roic'] = np.where(df['invested'] > 1e6, df['ebit'] / df['invested'], 0)
df['cfo_quality'] = np.where(df['net_income'].abs() > 1e6, df['operating_cf'] / df['net_income'], 0)
df['gross_margin'] = np.where(df['revenue'] > 1e6, df['operating_profit'] / df['revenue'], 0)
df['debt_ratio'] = np.where(df['total_assets'] > 1e6, df['total_liabilities'] / df['total_assets'], 0)
df['interest_coverage'] = np.where(
    (df['interest_expense'] > 1e6) & (df['operating_profit'] > 0),
    df['operating_profit'] / df['interest_expense'], np.nan)
df['goodwill_ratio'] = np.where(df['equity'] > 1e6, df['goodwill'] / df['equity'], 0)

# Aggregate to 3-year means (per stock)
print("\nAggregating 3-year means...")
agg = df.groupby('code').agg(
    revenue_mean=('revenue', 'mean'),
    net_income_mean=('net_income', 'mean'),
    ebit_mean=('ebit', 'mean'),
    operating_profit_mean=('operating_profit', 'mean'),
    interest_expense_mean=('interest_expense', 'mean'),
    equity_mean=('equity', 'mean'),
    invested_mean=('invested', 'mean'),
    total_assets_mean=('total_assets', 'mean'),
    roe_mean=('roe', 'mean'),
    roic_mean=('roic', 'mean'),
    cfo_quality_mean=('cfo_quality', 'mean'),
    gross_margin_mean=('gross_margin', 'mean'),
    debt_ratio_mean=('debt_ratio', 'mean'),
    interest_coverage_mean=('interest_coverage', 'mean'),
    goodwill_ratio_mean=('goodwill_ratio', 'mean'),
    # Revenue CAGR: y1 to y3
    revenue_y1=('revenue', lambda x: x.iloc[0] if len(x) >= 3 else x.iloc[-1]),
    revenue_y3=('revenue', lambda x: x.iloc[-1] if len(x) >= 3 else x.iloc[-1]),
    years_count=('report_period', 'nunique'),
).reset_index()

# Latest year data for valuation (EV/OE, FCF Yield)
latest_period = f'{END_YEAR}1231'
print(f"Loading latest-year valuation data ({latest_period})...")

inc_latest = pd.read_sql("""
    SELECT code, revenue, operating_profit, net_income, interest_expense
    FROM income WHERE report_period = ?
""", db, params=(latest_period,))

bal_latest = pd.read_sql("""
    SELECT code, total_assets, total_liabilities, equity,
           goodwill, short_term_debt, long_term_debt, cash_equiv
    FROM balance WHERE report_period = ?
""", db, params=(latest_period,))

cf_latest = pd.read_sql("""
    SELECT code, operating_cf, capex
    FROM cashflow WHERE report_period = ?
""", db, params=(latest_period,))

latest = inc_latest.merge(bal_latest, on='code', how='inner')
latest = latest.merge(cf_latest, on='code', how='inner')

for c in latest.columns:
    if c != 'code':
        latest[c] = pd.to_numeric(latest[c], errors='coerce').fillna(0)

latest['ev'] = latest['equity'] + latest['total_liabilities'] - latest['cash_equiv']
latest.loc[latest['ev'] <= 1e6, 'ev'] = np.nan

fcf = latest['operating_cf'] - latest['capex'].abs()
latest['fcf_yield_raw'] = np.where(
    latest['ev'].notna() & (latest['ev'] > 1e6), fcf / latest['ev'], 0)
latest['fcf_yield'] = latest['fcf_yield_raw'].clip(0, 0.15)  # Cap at 15%

eval_df = latest[['code', 'operating_profit', 'ev', 'fcf_yield']].rename(
    columns={'operating_profit': 'op_latest'})

# Merge aggregated means with stocks and valuation data
df = agg.merge(stocks, on='code', how='inner')
df = df.merge(eval_df, on='code', how='inner')
print(f"  Merged: {len(df)} stocks with 3-year data + valuation")

# SANITY FILTERS
before = len(df)
df = df[df['equity_mean'] > 1e6]
print(f"  After equity > 0: {len(df)} (removed {before - len(df)})")
before = len(df)
df = df[df['total_assets_mean'] > 1e6]
print(f"  After assets > 0: {len(df)} (removed {before - len(df)})")
before = len(df)
df = df[df['revenue_mean'] > 1e6]
print(f"  After revenue > 0: {len(df)} (removed {before - len(df)})")
before = len(df)
df = df[df['years_count'] >= 2]
print(f"  After years >= 2: {len(df)} (removed {before - len(df)})")

# Step 3: Factor calculation with multi-year smoothing
print("\nCalculating factors (multi-year smoothing)...")

# Quality — 3-year means
df['roe_score'] = np.clip(df['roe_mean'].clip(0) / 0.30 * 10, 0, 10)
df['roic_score'] = np.clip(df['roic_mean'].clip(0) / 0.30 * 10, 0, 10)
df['cfo_score'] = np.clip(df['cfo_quality_mean'].clip(0) / 2.0 * 10, 0, 10)
df['gm_score'] = np.clip(df['gross_margin_mean'].clip(0) / 0.50 * 10, 0, 10)

# Revenue CAGR (3-year)
revenue_y1 = df['revenue_y1'].clip(lower=1)
revenue_y3 = df['revenue_y3'].clip(lower=1)
df['revenue_cagr'] = (revenue_y3 / revenue_y1) ** (1/2) - 1
df['growth_score'] = np.clip(df['revenue_cagr'].clip(0) / 0.15 * 10, 0, 10)

# Value — latest year only
df['ev_oe'] = np.where(
    (df['op_latest'] > 1e6) & df['ev'].notna(),
    df['ev'] / df['op_latest'], 999)
df['evoe_score'] = np.where(
    (df['op_latest'] > 1e6) & df['ev'].notna(),
    np.clip(20 / df['ev_oe'].clip(lower=1) * 2, 0, 10), 0)
df['fcf_score'] = np.clip(df['fcf_yield'] / 0.10 * 10, 0, 10)

# Health — 3-year means
df['debt_score'] = np.clip((1 - df['debt_ratio_mean'].clip(0, 1)) * 10, 0, 10)
df['ic_score'] = np.where(
    df['interest_coverage_mean'].notna() & (df['interest_coverage_mean'] < 999),
    np.clip(df['interest_coverage_mean'] / 10 * 5, 0, 10), 5)
df['aq_score'] = np.clip((1 - df['goodwill_ratio_mean'].clip(0, 1)) * 10, 0, 10)

# Composite scores with dimension caps
QUAL_MAX = 35
VAL_MAX = 30
HEALTH_MAX = 20
GOV = 3.5

# Quality weights: roe 30% + roic 25% + gm 10% + cfo 20% + growth 15%
df['quality'] = (df['roe_score'] * 0.30 + df['roic_score'] * 0.25 +
                 df['gm_score'] * 0.10 + df['cfo_score'] * 0.20 +
                 df['growth_score'] * 0.15) * QUAL_MAX / 10
df['value']   = (df['evoe_score'] * 0.50 + df['fcf_score'] * 0.50) * VAL_MAX / 10
df['health']  = (df['debt_score'] * 0.50 + df['ic_score'] * 0.25 +
                 df['aq_score'] * 0.25) * HEALTH_MAX / 10
df['gov']     = GOV

df['total_score'] = df['quality'] + df['value'] + df['health'] + df['gov']

print(f"  Score range: {df['total_score'].min():.1f} - {df['total_score'].max():.1f}")
print(f"  Mean: {df['total_score'].mean():.1f}, Median: {df['total_score'].median():.1f}")

# Step 4: Top 30
top50 = df.nlargest(50, 'total_score')[['code', 'name', 'total_score',
    'roe_mean', 'revenue_cagr', 'fcf_yield', 'debt_ratio_mean', 'op_latest']]

print(f"\n{'='*80}")
print(f"TOP 30 — Multi-Year Smoothing (3y means) — CSI 300/500 overlap")
print(f"{'='*80}")
print(f"{'#':<4} {'Code':<8} {'Name':<12} {'Score':<7} {'CSI300':<8} {'CSI500':<8} {'ROE3y':<8} {'CAGR':<8} {'FCFY':<8}")
print("-"*80)

outsiders = []
for i, (_, row) in enumerate(top50.head(30).iterrows()):
    code = row['code']
    in300 = code in csi300_codes
    in500 = code in csi500_codes
    roe_pct = row['roe_mean'] * 100
    cagr_pct = row['revenue_cagr'] * 100
    fcf_y = row['fcf_yield'] * 100
    print(f"{i+1:<4} {code:<8} {row['name']:<12} {row['total_score']:<7.1f} "
          f"{'✓' if in300 else '':<8} {'✓' if in500 else '':<8} "
          f"{roe_pct:<8.1f} {cagr_pct:<8.1f} {fcf_y:<8.1f}")

    if not in300 and not in500:
        outsiders.append(row)

# Step 5: Analyze outsiders
print(f"\n{'='*80}")
print(f"OUTSIDERS: {len(outsiders)} stocks not in CSI 300 or CSI 500")
print(f"{'='*80}")

for row in outsiders:
    code = row['code']
    name = row['name']
    roe_pct = row['roe_mean'] * 100
    fcf_y = row['fcf_yield'] * 100
    debt_r = row['debt_ratio_mean'] * 100
    op = row['op_latest'] / 1e8
    cagr_pct = row['revenue_cagr'] * 100

    # Check for data anomalies
    if fcf_y >= 14.9 and op > 0:
        verdict = "⚠️ FCF Yield触及上限 — 可能EV失真"
    elif roe_pct > 50:
        verdict = "⚠️ ROE极端 — 可能一次性收益"
    elif debt_r > 75:
        verdict = "⚠️ 高杠杆 — 财务风险大"
    elif roe_pct > 15 and fcf_y > 5:
        verdict = "✅ 价值发现 — 高质量但不在主流指数"
    elif roe_pct < 5:
        verdict = "⚠️ 低ROE — 总分虚高"
    else:
        verdict = "🔍 中性 — 需进一步审查"

    print(f"\n  {code} {name}  {row['total_score']:.1f}分")
    print(f"    ROE(3y): {roe_pct:.1f}%  CAGR: {cagr_pct:.1f}%  FCF Yield: {fcf_y:.1f}%  负债率: {debt_r:.1f}%  营业利润: {op:.1f}亿")
    print(f"    判断: {verdict}")

# Summary
print(f"\n{'='*80}")
print("SUMMARY")
print(f"{'='*80}")
top30_codes = set(top50.head(30)['code'].tolist())
in300 = top30_codes & csi300_codes
in500 = top30_codes & csi500_codes
in_either = in300 | in500
neither = top30_codes - in_either

print(f"  Multi-year smoothing (default)")
print(f"  CSI 300 overlap:   {len(in300)}/30 ({len(in300)/30*100:.0f}%)")
print(f"  CSI 500 overlap:   {len(in500)}/30 ({len(in500)/30*100:.0f}%)")
print(f"  In either:         {len(in_either)}/30 ({len(in_either)/30*100:.0f}%)")
print(f"  Not in either:     {len(neither)}/30 ({len(neither)/30*100:.0f}%)")

db.close()
print("\nDone!")
