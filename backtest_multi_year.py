"""
P2-2 打分回测 v2：Multi-Year Factor Smoothing
- 质量/健康因子用 3年滚动均值
- 估值因子用最新单年数据
- 对比单年 vs 多年平滑的 Spearman 差异

时间点 A: 2023年报视角 (2021+2022+2023)
时间点 B: 2024年报视角 (2022+2023+2024)
时间点 C: 2025年报视角 (2023+2024+2025)
"""
import sys, os
os.chdir('/home/xxxsuli/bqas-model')
sys.path.insert(0, '/home/xxxsuli/bqas-model')

import sqlite3
import pandas as pd
import numpy as np
from scipy.stats import spearmanr

db_path = '/home/xxxsuli/bqas-model/bqas/data/cache/bqas.db'
db = sqlite3.connect(db_path)

# ============================================================
# Multi-year factor computation
# ============================================================
def compute_scores_multi_year(db, end_year):
    """Use 3-year rolling window ending at end_year."""
    years = [end_year - 2, end_year - 1, end_year]
    periods = [f'{y}1231' for y in years]
    
    # Pull 3 years of data
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
    
    stocks = pd.read_sql("SELECT code, name FROM stock_info", db)
    
    for df in [income, balance, cashflow]:
        for c in df.columns:
            if c not in ('code', 'report_period', 'name'):
                df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
    
    # Merge all into one flat frame
    df = income.merge(balance, on=['code', 'report_period'], how='inner')
    df = df.merge(cashflow, on=['code', 'report_period'], how='inner')
    
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
    agg = df.groupby('code').agg(
        revenue_mean=('revenue', 'mean'),
        net_income_mean=('net_income', 'mean'),
        ebit_mean=('ebit', 'mean'),
        operating_profit_mean=('operating_profit', 'mean'),
        interest_expense_mean=('interest_expense', 'mean'),
        equity_mean=('equity', 'mean'),
        invested_mean=('invested', 'mean'),
        roe_mean=('roe', 'mean'),
        roic_mean=('roic', 'mean'),
        cfo_quality_mean=('cfo_quality', 'mean'),
        gross_margin_mean=('gross_margin', 'mean'),
        debt_ratio_mean=('debt_ratio', 'mean'),
        interest_coverage_mean=('interest_coverage', 'mean'),
        goodwill_ratio_mean=('goodwill_ratio', 'mean'),
        # Revenue growth: CAGR from year1 to year3
        revenue_y1=('revenue', lambda x: x.iloc[0] if len(x) >= 3 else x.iloc[-1]),
        revenue_y3=('revenue', lambda x: x.iloc[-1] if len(x) >= 3 else x.iloc[-1]),
        years_count=('report_period', 'nunique'),
    ).reset_index()
    
    # Latest year data for valuation (EV/OE, FCF Yield)
    latest_period = f'{end_year}1231'
    
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
    latest['fcf_yield_raw'] = np.where(latest['ev'].notna() & (latest['ev'] > 1e6), fcf / latest['ev'], 0)
    latest['fcf_yield'] = latest['fcf_yield_raw'].clip(0, 0.15)
    
    eval_df = latest[['code', 'operating_profit', 'ev', 'fcf_yield']].rename(
        columns={'operating_profit': 'op_latest'})
    
    # Merge aggregated means with latest valuation data
    df = agg.merge(stocks, on='code', how='inner')
    df = df.merge(eval_df, on='code', how='inner')
    
    # Sanity filters
    df = df[df['equity_mean'] > 1e6]
    df = df[df['revenue_mean'] > 1e6]
    df = df[df['years_count'] >= 2]  # need at least 2 years
    
    # === SCORING with 3-year means ===
    
    # Quality
    df['roe_score'] = np.clip(df['roe_mean'].clip(0) / 0.30 * 10, 0, 10)
    df['roic_score'] = np.clip(df['roic_mean'].clip(0) / 0.30 * 10, 0, 10)
    df['cfo_score'] = np.clip(df['cfo_quality_mean'].clip(0) / 2.0 * 10, 0, 10)
    df['gm_score'] = np.clip(df['gross_margin_mean'].clip(0) / 0.50 * 10, 0, 10)
    
    # Revenue growth (CAGR)
    revenue_y1 = df['revenue_y1'].clip(lower=1)
    revenue_y3 = df['revenue_y3'].clip(lower=1)
    df['revenue_cagr'] = (revenue_y3 / revenue_y1) ** (1/2) - 1
    df['growth_score'] = np.clip(df['revenue_cagr'].clip(0) / 0.15 * 10, 0, 10)
    
    # Value (latest year)
    df['ev_oe'] = np.where(
        (df['op_latest'] > 1e6) & df['ev'].notna(),
        df['ev'] / df['op_latest'], 999)
    df['evoe_score'] = np.where(
        (df['op_latest'] > 1e6) & df['ev'].notna(),
        np.clip(20 / df['ev_oe'].clip(lower=1) * 2, 0, 10), 0)
    df['fcf_score'] = np.clip(df['fcf_yield'] / 0.10 * 10, 0, 10)
    
    # Health
    df['debt_score'] = np.clip((1 - df['debt_ratio_mean'].clip(0, 1)) * 10, 0, 10)
    df['ic_score'] = np.where(
        df['interest_coverage_mean'].notna() & (df['interest_coverage_mean'] < 999),
        np.clip(df['interest_coverage_mean'] / 10 * 5, 0, 10), 5)
    df['aq_score'] = np.clip((1 - df['goodwill_ratio_mean'].clip(0, 1)) * 10, 0, 10)
    
    # Composite scores
    QUAL_MAX, VAL_MAX, HEALTH_MAX, GOV = 35, 30, 20, 3.5
    
    df['quality'] = (df['roe_score'] * 0.30 + df['roic_score'] * 0.25 +
                     df['gm_score'] * 0.10 + df['cfo_score'] * 0.20 +
                     df['growth_score'] * 0.15) * QUAL_MAX / 10
    df['value']   = (df['evoe_score'] * 0.50 + df['fcf_score'] * 0.50) * VAL_MAX / 10
    df['health']  = (df['debt_score'] * 0.50 + df['ic_score'] * 0.25 +
                     df['aq_score'] * 0.25) * HEALTH_MAX / 10
    df['gov']     = GOV
    
    df['total_score'] = df['quality'] + df['value'] + df['health'] + df['gov']
    
    return df.sort_values('total_score', ascending=False).reset_index(drop=True)


# ============================================================
# Run both single-year and multi-year
# ============================================================
print("=" * 80)
print("P2-2 回测 v2：Multi-Year Factor Smoothing vs Single-Year")
print("=" * 80)

years = [2023, 2024, 2025]
results_1y = {}
results_3y = {}

# Single-year (original logic, simplified)
def compute_scores_single_year(db, year):
    """Mirrors the original single-year compute_scores with growth factor added."""
    from backtest_score import compute_scores
    return compute_scores(db, year)

for yr in years:
    print(f"\n⏳ 计算 {yr}年报视角...")
    print(f"   单年因子...", end=" ")
    results_1y[yr] = compute_scores_single_year(db, yr)
    print(f"{len(results_1y[yr])} 只 | {results_1y[yr]['total_score'].median():.1f} median")
    
    print(f"   多年平滑...", end=" ")
    results_3y[yr] = compute_scores_multi_year(db, yr)
    print(f"{len(results_3y[yr])} 只 | {results_3y[yr]['total_score'].median():.1f} median")

# ============================================================
# Compare Spearman
# ============================================================
print(f"\n{'=' * 80}")
print("Spearman 排序相关系数对比")
print(f"{'=' * 80}")

common_codes = set(results_1y[2023]['code']) & set(results_1y[2024]['code']) & set(results_1y[2025]['code'])
common_3y = set(results_3y[2023]['code']) & set(results_3y[2024]['code']) & set(results_3y[2025]['code'])

# Use intersection of both to be fair
common = sorted(common_codes & common_3y)
print(f"  共同股票池: {len(common)} 只")

rankings_1y = {}
rankings_3y = {}
for yr in years:
    r1 = results_1y[yr].set_index('code')
    r3 = results_3y[yr].set_index('code')
    r1['rank'] = r1['total_score'].rank(ascending=False)
    r3['rank'] = r3['total_score'].rank(ascending=False)
    rankings_1y[yr] = r1.loc[common, 'rank'].values
    rankings_3y[yr] = r3.loc[common, 'rank'].values

print(f"\n  单年因子 Spearman:")
print(f"  {'':>8} {'2023':>8} {'2024':>8} {'2025':>8}")
print(f"  {'-'*32}")
for y1 in years:
    row = f"  {y1}  "
    for y2 in years:
        rho, _ = spearmanr(rankings_1y[y1], rankings_1y[y2])
        row += f" {rho:7.3f}"
    print(row)

print(f"\n  多年平滑 Spearman:")
print(f"  {'':>8} {'2023':>8} {'2024':>8} {'2025':>8}")
print(f"  {'-'*32}")
for y1 in years:
    row = f"  {y1}  "
    for y2 in years:
        rho, _ = spearmanr(rankings_3y[y1], rankings_3y[y2])
        row += f" {rho:7.3f}"
    print(row)

# Improvement
print(f"\n  提升幅度:")
for y1 in years:
    for y2 in years:
        if y1 < y2:
            rho1, _ = spearmanr(rankings_1y[y1], rankings_1y[y2])
            rho3, _ = spearmanr(rankings_3y[y1], rankings_3y[y2])
            delta = rho3 - rho1
            print(f"    {y1}↔{y2}: {rho1:.3f} → {rho3:.3f}  (Δ{delta:+.3f})")

# ============================================================
# Top 30 overlap comparison
# ============================================================
print(f"\n{'=' * 80}")
print("Top 30 重合度对比")
print(f"{'=' * 80}")

for label, results in [("单年因子", results_1y), ("多年平滑", results_3y)]:
    top30_sets = {yr: set(results[yr].head(30)['code'].tolist()) for yr in years}
    
    print(f"\n  [{label}]")
    for y1 in years:
        for y2 in years:
            if y1 < y2:
                overlap = top30_sets[y1] & top30_sets[y2]
                print(f"    {y1}∩{y2}: {len(overlap)}/30 ({len(overlap)/30*100:.0f}%)")
    
    three = top30_sets[2023] & top30_sets[2024] & top30_sets[2025]
    print(f"    三年全重合: {len(three)}/30 ({len(three)/30*100:.0f}%)")

# ============================================================
# Multi-year Top 30 detail
# ============================================================
print(f"\n{'=' * 80}")
print("多年平滑 Top 10（各时间点）")
print(f"{'=' * 80}")
for yr in years:
    print(f"\n  {yr}年报视角 Top 10:")
    for i, (_, row) in enumerate(results_3y[yr].head(10).iterrows()):
        roe = row.get('roe_mean', 0) * 100
        print(f"    {i+1}. {row['code']} {row['name']:<10} {row['total_score']:.1f}分  ROE(3y):{roe:.1f}%")

# ============================================================
# Three-way consistent stocks (multi-year)
# ============================================================
print(f"\n{'=' * 80}")
print("多年平滑：三年全部在 Top 30 的股票")
print(f"{'=' * 80}")
three = set(results_3y[2023].head(30)['code']) & set(results_3y[2024].head(30)['code']) & set(results_3y[2025].head(30)['code'])
print(f"  共 {len(three)} 只:")
for code in sorted(three):
    name = results_3y[2023].set_index('code').loc[code, 'name']
    s23 = results_3y[2023].set_index('code').loc[code, 'total_score']
    s24 = results_3y[2024].set_index('code').loc[code, 'total_score']
    s25 = results_3y[2025].set_index('code').loc[code, 'total_score']
    print(f"    {code} {name:<10}  {s23:.1f} → {s24:.1f} → {s25:.1f}")

db.close()
print(f"\n{'=' * 80}")
print("回测完成！")
print(f"{'=' * 80}")
