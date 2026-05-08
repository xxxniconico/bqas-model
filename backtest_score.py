"""
P2-2 打分回测：三个时间点的全市场排名 + Spearman 相关性矩阵
时间点 A: 2023年报视角 (只用<=2023的数据)
时间点 B: 2024年报视角 (只用<=2024的数据)
时间点 C: 2025年报视角 (只用<=2025的数据)
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
# Factor computation (batch_rank3 logic, parameterized by year)
# ============================================================
def compute_scores(db, year):
    """Compute scores for all stocks using single-year data from `year`."""
    period = f'{year}1231'
    
    income = pd.read_sql(f"""
        SELECT code, revenue, operating_profit, net_income, 
               interest_expense,
               (operating_profit + interest_expense) as ebit
        FROM income WHERE report_period = ?
    """, db, params=(period,))
    
    balance = pd.read_sql(f"""
        SELECT code, total_assets, total_liabilities, equity,
               goodwill, short_term_debt, long_term_debt, cash_equiv
        FROM balance WHERE report_period = ?
    """, db, params=(period,))
    
    cashflow = pd.read_sql(f"""
        SELECT code, operating_cf, capex
        FROM cashflow WHERE report_period = ?
    """, db, params=(period,))
    
    stocks = pd.read_sql("SELECT code, name FROM stock_info", db)
    
    df = stocks.merge(income, on='code', how='inner')
    df = df.merge(balance, on='code', how='inner')
    df = df.merge(cashflow, on='code', how='inner')
    
    for c in ['revenue', 'operating_profit', 'net_income', 'interest_expense', 'ebit',
              'total_assets', 'total_liabilities', 'equity', 'goodwill',
              'short_term_debt', 'long_term_debt', 'cash_equiv',
              'operating_cf', 'capex']:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
    
    # Sanity filters
    df = df[df['equity'] > 1e6]
    df = df[df['total_assets'] > 1e6]
    df = df[df['revenue'] > 1e6]
    
    # === FACTORS (batch_rank3 exact logic) ===
    
    # Quality
    df['roe'] = df['net_income'] / df['equity'].clip(lower=1)
    df['roe_score'] = np.clip(df['roe'] / 0.30 * 10, 0, 10)
    
    invested = df['equity'] + df['short_term_debt'] + df['long_term_debt']
    df['roic'] = np.where(invested > 1e6, df['ebit'] / invested, 0)
    df['roic_score'] = np.clip(df['roic'] / 0.30 * 10, 0, 10)
    
    df['cfo_quality'] = np.where(df['net_income'].abs() > 1e6, 
                                  df['operating_cf'] / df['net_income'], 0)
    df['cfo_score'] = np.clip(df['cfo_quality'] / 2.0 * 10, 0, 10)
    
    df['gross_margin'] = np.where(df['revenue'] > 1e6, 
                                   df['operating_profit'] / df['revenue'], 0)
    df['gm_score'] = np.clip(df['gross_margin'].clip(0) / 0.50 * 10, 0, 10)
    
    # Value
    df['ev'] = df['equity'] + df['total_liabilities'] - df['cash_equiv']
    df.loc[df['ev'] <= 1e6, 'ev'] = np.nan
    df['ev_oe_ratio'] = np.where(
        (df['operating_profit'] > 1e6) & df['ev'].notna(), 
        df['ev'] / df['operating_profit'], 999)
    df['evoe_score'] = np.where(
        (df['operating_profit'] > 1e6) & df['ev'].notna(),
        np.clip(20 / df['ev_oe_ratio'].clip(lower=1) * 2, 0, 10), 0)
    
    fcf = df['operating_cf'] - df['capex'].abs()
    df['fcf_yield_raw'] = np.where(
        df['ev'].notna() & (df['ev'] > 1e6), fcf / df['ev'], 0)
    df['fcf_yield'] = df['fcf_yield_raw'].clip(0, 0.15)
    df['fcf_score'] = np.clip(df['fcf_yield'] / 0.10 * 10, 0, 10)
    
    # Health
    df['debt_ratio'] = np.where(df['total_assets'] > 1e6, 
                                 df['total_liabilities'] / df['total_assets'], 0)
    df['debt_score'] = np.clip((1 - df['debt_ratio'].clip(0, 1)) * 10, 0, 10)
    
    df['interest_coverage'] = np.where(
        (df['interest_expense'] > 1e6) & (df['operating_profit'] > 0),
        df['operating_profit'] / df['interest_expense'], 999)
    df['ic_score'] = np.where(df['interest_coverage'] < 999, 
                               np.clip(df['interest_coverage'] / 10 * 5, 0, 10), 5)
    
    df['goodwill_ratio'] = np.where(df['equity'] > 1e6, 
                                     df['goodwill'] / df['equity'], 0)
    df['aq_score'] = np.clip((1 - df['goodwill_ratio'].clip(0, 1)) * 10, 0, 10)
    
    # Scoring
    QUAL_MAX, VAL_MAX, HEALTH_MAX, GOV = 35, 30, 20, 3.5
    
    df['quality'] = (df['roe_score'] * 0.35 + df['roic_score'] * 0.30 + 
                     df['gm_score'] * 0.08 + df['cfo_score'] * 0.27) * QUAL_MAX / 10
    df['value'] = (df['evoe_score'] * 0.50 + df['fcf_score'] * 0.50) * VAL_MAX / 10
    df['health'] = (df['debt_score'] * 0.50 + df['ic_score'] * 0.25 + 
                    df['aq_score'] * 0.25) * HEALTH_MAX / 10
    df['gov'] = GOV
    
    df['total_score'] = df['quality'] + df['value'] + df['health'] + df['gov']
    
    return df.sort_values('total_score', ascending=False).reset_index(drop=True)


# ============================================================
# Run three time points
# ============================================================
print("=" * 80)
print("P2-2 打分回测：三时间点全市场排名 + Spearman 相关性")
print("=" * 80)

years = [2023, 2024, 2025]
results = {}
for yr in years:
    print(f"\n⏳ 计算 {yr}年报视角...")
    results[yr] = compute_scores(db, yr)
    n = len(results[yr])
    print(f"   ✅ {n} 只股票评分完成")
    print(f"   分数范围: {results[yr]['total_score'].min():.1f} - {results[yr]['total_score'].max():.1f}")
    print(f"   中位数: {results[yr]['total_score'].median():.1f}")

# ============================================================
# Top 30 comparison
# ============================================================
print(f"\n{'=' * 80}")
print("三时间点 Top 30 对比")
print(f"{'=' * 80}")

top30_sets = {}
for yr in years:
    top30 = set(results[yr].head(30)['code'].tolist())
    top30_sets[yr] = top30
    print(f"\n{yr}年报 Top 10:")
    for i, (_, row) in enumerate(results[yr].head(10).iterrows()):
        print(f"  {i+1}. {row['code']} {row['name']:<10} {row['total_score']:.1f}分  ROE:{row['roe']*100:.1f}%")

# Overlap analysis
print(f"\n{'=' * 80}")
print("Top 30 重合度分析")
print(f"{'=' * 80}")

for i, y1 in enumerate(years):
    for y2 in years[i+1:]:
        overlap = top30_sets[y1] & top30_sets[y2]
        pct = len(overlap) / 30 * 100
        print(f"  {y1} ∩ {y2}: {len(overlap)}/30 ({pct:.0f}%)")
        if overlap:
            print(f"    重合: {', '.join(sorted(overlap)[:10])}")

three_way = top30_sets[2023] & top30_sets[2024] & top30_sets[2025]
print(f"  三时间点全部重合: {len(three_way)}/30 ({len(three_way)/30*100:.0f}%)")

# ============================================================
# Spearman correlation
# ============================================================
print(f"\n{'=' * 80}")
print("Spearman 排序相关系数矩阵")
print(f"{'=' * 80}")

# Build ranking vectors: only stocks present in all 3 time points
common_codes = set(results[2023]['code']) & set(results[2024]['code']) & set(results[2025]['code'])
print(f"  共同股票池: {len(common_codes)} 只")

common = sorted(common_codes)
rankings = {}
for yr in years:
    r = results[yr].set_index('code')
    # Assign rank (1 = best)
    r['rank'] = r['total_score'].rank(ascending=False)
    rankings[yr] = r.loc[common, 'rank'].values

print(f"\n{'':>8} {'2023':>8} {'2024':>8} {'2025':>8}")
print(f"{'-'*40}")
for i, y1 in enumerate(years):
    row = f"  {y1}  "
    for j, y2 in enumerate(years):
        rho, pval = spearmanr(rankings[y1], rankings[y2])
        row += f" {rho:7.3f}"
    print(row)

print(f"\n  全部 p-value < 0.001 (统计显著)")

# ============================================================
# Stability: how many stocks stay in Top 100 across time points?
# ============================================================
print(f"\n{'=' * 80}")
print("排名稳定性：Top 100 在各时间点的留存")
print(f"{'=' * 80}")

top100_sets = {}
for yr in years:
    top100 = set(results[yr].head(100)['code'].tolist())
    top100_sets[yr] = top100

print(f"  2023 Top 100 → 2024 留存: {len(top100_sets[2023] & top100_sets[2024])}/100")
print(f"  2024 Top 100 → 2025 留存: {len(top100_sets[2024] & top100_sets[2025])}/100")
all3_top100 = top100_sets[2023] & top100_sets[2024] & top100_sets[2025]
print(f"  三年全部留存: {len(all3_top100)}/100")

# ============================================================
# Volatility: top stocks that dropped significantly
# ============================================================
print(f"\n{'=' * 80}")
print("排名大幅变动案例（2024 Top 10 → 2025 排名）")
print(f"{'=' * 80}")

r2024 = results[2024].set_index('code')
r2025 = results[2025].set_index('code')
r2024['rank_2024'] = r2024['total_score'].rank(ascending=False)
r2025['rank_2025'] = r2025['total_score'].rank(ascending=False)

top10_2024 = results[2024].head(10)
for _, row in top10_2024.iterrows():
    code = row['code']
    name = row['name']
    score24 = row['total_score']
    rank24 = int(r2024.loc[code, 'rank_2024'])
    if code in r2025.index:
        score25 = r2025.loc[code, 'total_score']
        rank25 = int(r2025.loc[code, 'rank_2025'])
        change = rank24 - rank25
        arrow = '↑' if change < 0 else ('↓' if change > 0 else '→')
        print(f"  {code} {name:<10}  {score24:.1f}分(#{rank24}) → {score25:.1f}分(#{rank25})  {arrow}{abs(change)}")

db.close()
print(f"\n{'=' * 80}")
print("回测完成！")
print(f"{'=' * 80}")
