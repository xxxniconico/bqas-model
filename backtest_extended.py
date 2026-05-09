#!/usr/bin/env python3
"""
P3: Extended Multi-Year Backtest — 10 years (2016-2025)

Spearman stability analysis across all available year-end snapshots.
Uses 3-year rolling factor smoothing for quality/health dimensions.
"""

import sqlite3
import pandas as pd
import numpy as np
from scipy.stats import spearmanr

DB_PATH = 'bqas/data/cache/bqas.db'


def compute_scores(db, end_year):
    """Batch score all stocks using 3-year rolling window ending at end_year."""
    years = [end_year - 2, end_year - 1, end_year]
    periods = [f'{y}1231' for y in years]
    
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
            if c not in ('code', 'report_period'):
                df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
    
    # Merge
    df = income.merge(balance, on=['code', 'report_period'], how='inner')
    df = df.merge(cashflow, on=['code', 'report_period'], how='inner')
    
    # Per-year computed columns
    df['ebit'] = df['operating_profit'] + df['interest_expense'].abs()
    df['invested'] = df['equity'] + df['short_term_debt'] + df['long_term_debt']
    df['roe'] = np.where(df['equity'] > 1e6, df['net_income'] / df['equity'], 0)
    df['roic'] = np.where(df['invested'] > 1e6, df['ebit'] / df['invested'], 0)
    df['gm'] = np.where(df['revenue'] > 1e6, df['operating_profit'] / df['revenue'], 0)
    df['cfo_ratio'] = np.where(df['net_income'].abs() > 1e6,
                                df['operating_cf'] / df['net_income'], 0)
    df['debt_ratio'] = np.where(df['total_assets'] > 1e6,
                                 df['total_liabilities'] / df['total_assets'], 1)
    df['ic'] = np.where(df['interest_expense'].abs() > 1,
                         (df['operating_profit'] + df['interest_expense'].abs()) /
                         df['interest_expense'].abs(), 99)
    df['gw_ratio'] = np.where(df['equity'] > 1e6, df['goodwill'] / df['equity'], 0)
    
    # Group by code, compute 3-year means
    g = df.groupby('code')
    
    result = pd.DataFrame()
    result['roe_3y'] = g['roe'].mean()
    result['roic_3y'] = g['roic'].mean()
    result['gm_3y'] = g['gm'].mean()
    result['cfo_3y'] = g['cfo_ratio'].mean()
    result['debt_ratio_3y'] = g['debt_ratio'].mean()
    result['ic_3y'] = g['ic'].mean()
    result['gw_ratio_3y'] = g['gw_ratio'].mean()
    
    # Revenue CAGR
    last_y = df[df['report_period'] == periods[-1]].set_index('code')
    first_y = df[df['report_period'] == periods[0]].set_index('code')
    common = last_y.index.intersection(first_y.index)
    rev_cagr = ((last_y.loc[common, 'revenue'] / first_y.loc[common, 'revenue']) ** (1/2) - 1)
    result['revenue_cagr'] = rev_cagr
    
    # Filter: need at least 2 years of data
    result = result[g.size() >= 2]
    result = result.replace([np.inf, -np.inf], np.nan).fillna(0)
    
    # Entry filter
    result = result[(result['roe_3y'].abs() < 100) & (result['ic_3y'] < 999)]
    
    # Score
    result['q1_roe'] = np.clip(result['roe_3y'] / 0.30, 0, 1) * 12
    result['q2_roic'] = np.clip(result['roic_3y'] / 0.30, 0, 1) * 10
    result['q3_cfo'] = np.clip(result['cfo_3y'] / 2.0, 0, 1) * 8
    result['q4_gm'] = np.clip(result['gm_3y'] / 0.60, 0, 1) * 5
    result['v1_evoe'] = np.clip(result['roic_3y'] / 0.30, 0, 1) * 15
    result['h1_lev'] = np.clip(1 - result['debt_ratio_3y'] / 0.70, 0, 1) * 8
    result['h2_ic'] = np.clip(result['ic_3y'] / 20, 0, 1) * 7
    result['gov'] = 3.5
    
    result['total'] = (result['q1_roe'] + result['q2_roic'] + result['q3_cfo'] +
                       result['q4_gm'] + result['v1_evoe'] + result['h1_lev'] +
                       result['h2_ic'] + result['gov'])
    
    return result['total'].rename(str(end_year))


def main():
    db = sqlite3.connect(DB_PATH)
    
    # Compute scores for each year
    years = list(range(2018, 2026))  # 2018 needs 2016 data
    scores = {}
    for y in years:
        print(f"  Computing {y}...")
        scores[str(y)] = compute_scores(db, y)
    
    # Build comparison matrix
    print(f"\n{'='*70}")
    print(f"  Multi-Year Factor Smoothing: Spearman Stability Matrix")
    print(f"{'='*70}")
    print(f"{'Year':>6s}", end="")
    for y in years:
        print(f"{y:>8d}", end="")
    print(f"  {'Stocks':>8s}")
    print("-" * 70)
    
    all_corrs = []
    for y1 in years:
        s1 = scores[str(y1)]
        print(f"{y1:>6d}", end="")
        row_corrs = []
        for y2 in years:
            if y1 == y2:
                print(f"{'─':>8s}", end="")
                continue
            s2 = scores[str(y2)]
            common = s1.index.intersection(s2.index)
            if len(common) >= 30:
                corr, _ = spearmanr(s1[common], s2[common])
                row_corrs.append(corr)
                print(f"{corr:>+7.3f}", end="")
            else:
                print(f"{'N/A':>8s}", end="")
        print(f"  {len(s1):>8d}")
        all_corrs.extend(row_corrs)
    
    # Summary
    if all_corrs:
        mean_corr = np.mean(all_corrs)
        min_corr = np.min(all_corrs)
        max_corr = np.max(all_corrs)
        print(f"\n{'='*70}")
        print(f"  SUMMARY (multi-year smoothing, 3-yr rolling factors)")
        print(f"  Periods: {years[0]}-{years[-1]} ({len(years)} snapshots)")
        print(f"  Mean Spearman:  {mean_corr:.3f}")
        print(f"  Range: [{min_corr:.3f}, {max_corr:.3f}]")
        print(f"  Stability: {'✅ Excellent' if mean_corr > 0.80 else '⚠ Good' if mean_corr > 0.65 else '❌ Poor'}")
    
    # Year-over-year (adjacent pairs)
    print(f"\n  Year-over-Year Adjacent Pairs:")
    for y in years[:-1]:
        s1 = scores[str(y)]
        s2 = scores[str(y+1)]
        common = s1.index.intersection(s2.index)
        if len(common) >= 30:
            corr, _ = spearmanr(s1[common], s2[common])
            print(f"    {y}→{y+1}: Spearman={corr:.3f}  (n={len(common)})")
    
    db.close()


if __name__ == '__main__':
    main()
