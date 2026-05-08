#!/usr/bin/env python3
"""
P2-3: Price Simulation Backtest

Scores all stocks at each year-end, buys top-N, holds until next year-end,
computes forward returns. Uses real prices from quotes table.

Time points:
  2023-12-31 → 2024-12-31 (1yr forward)
  2024-12-31 → 2025-12-31 (1yr forward)

Output:
  - Per-period top-N portfolio returns
  - Cumulative return
  - Annualized return
  - Benchmark comparison (CSI 300 if available)
"""

import sqlite3
import pandas as pd
import numpy as np
import sys
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'bqas', 'data', 'cache', 'bqas.db')

# Periods for scoring and holding
PERIODS = [
    ('2023-12-31', '2024-12-31'),  # (score_date, sell_date)
    ('2024-12-31', '2025-12-31'),
]

TOP_N = 30  # Number of stocks in portfolio


def load_financials(db, year):
    """Load financial data for a given year-end."""
    period = f'{year}1231'
    income = pd.read_sql(
        "SELECT code, revenue, operating_profit, net_income, interest_expense "
        "FROM income WHERE report_period = ?", db, params=[period]
    )
    balance = pd.read_sql(
        "SELECT code, total_assets, total_liabilities, equity, goodwill, "
        "short_term_debt, long_term_debt, cash_equiv "
        "FROM balance WHERE report_period = ?", db, params=[period]
    )
    cashflow = pd.read_sql(
        "SELECT code, operating_cf, capex FROM cashflow WHERE report_period = ?",
        db, params=[period]
    )
    stocks = pd.read_sql("SELECT code, name FROM stock_info", db)
    return income, balance, cashflow, stocks


def batch_score(df, year):
    """Vectorized batch scoring (simplified from batch_rank3.py logic)."""
    df = df.copy()
    
    # Q1: ROE (12%)
    df['roe'] = np.where(df['equity'] > 1e6, df['net_income'] / df['equity'], 0)
    df['q1_roe_score'] = np.clip(df['roe'] / 0.30, 0, 1) * 12
    
    # Q2: ROIC (10%)
    df['invested'] = df['equity'] + df['short_term_debt'].fillna(0) + df['long_term_debt'].fillna(0)
    df['ebit'] = df['operating_profit'] + df['interest_expense'].fillna(0).abs()
    df['roic'] = np.where(df['invested'] > 1e6, df['ebit'] / df['invested'], 0)
    df['q2_roic_score'] = np.clip(df['roic'] / 0.30, 0, 1) * 10
    
    # Q3: CFO Quality (8%)
    df['cfo_ratio'] = np.where(df['net_income'].abs() > 1e6,
                                df['operating_cf'].fillna(0) / df['net_income'], 0)
    df['q3_cfo_score'] = np.clip(df['cfo_ratio'] / 2.0, 0, 1) * 8
    
    # Q4: Gross Margin (5%) — simplified
    df['gm'] = np.where(df['revenue'] > 1e6, df['operating_profit'] / df['revenue'], 0)
    df['q4_gm_score'] = np.clip(df['gm'] / 0.60, 0, 1) * 5
    
    # V1: EV/Operating Earnings (15%) — requires quotes, skip for now
    # Use simplified score based on ROIC (high ROIC = quality at any price)
    df['v1_evoe_score'] = np.clip(df['roic'] / 0.30, 0, 1) * 15
    
    # V2: FCF Yield (10%)
    df['fcf'] = df['operating_cf'].fillna(0) - df['capex'].fillna(0).abs()
    # Market cap from quotes — will add later
    df['v2_fcf_score'] = 0  # placeholder, filled after joining with quotes
    
    # H1: Leverage (8%)
    df['debt_ratio'] = np.where(df['total_assets'] > 1e6,
                                 df['total_liabilities'] / df['total_assets'], 1)
    df['h1_leverage_score'] = np.clip(1 - df['debt_ratio'] / 0.70, 0, 1) * 8
    
    # H2: Interest Coverage (7%)
    df['ic'] = np.where(df['interest_expense'].fillna(0).abs() > 1,
                         (df['operating_profit'] + df['interest_expense'].fillna(0).abs()) /
                         df['interest_expense'].fillna(0).abs(), 99)
    df['h2_ic_score'] = np.clip(df['ic'] / 20, 0, 1) * 7
    
    # Gov: Flat (not enriched in batch)
    df['gov_score'] = 3.5
    
    # Total
    df['total_score'] = (
        df['q1_roe_score'] + df['q2_roic_score'] + df['q3_cfo_score'] + df['q4_gm_score'] +
        df['v1_evoe_score'] + df['v2_fcf_score'] +
        df['h1_leverage_score'] + df['h2_ic_score'] +
        df['gov_score']
    )
    
    # FCF Yield with market cap (after joining quotes)
    return df


def get_prices(db, codes, date_str):
    """Get close prices for given codes on or before date_str."""
    codes_str = ','.join([f"'{c}'" for c in codes])
    query = f"""
        SELECT code, close, market_cap
        FROM quotes
        WHERE code IN ({codes_str}) AND trade_date <= '{date_str}'
        ORDER BY code, trade_date DESC
    """
    df = pd.read_sql(query, db)
    # Keep only the last price per stock (closest to date)
    df = df.groupby('code').first().reset_index()
    return df


def run_backtest():
    db = sqlite3.connect(DB_PATH)
    
    print("=" * 60)
    print("  BQAS Price Simulation Backtest")
    print("=" * 60)
    
    results = []
    portfolio_values = []
    
    for score_date_str, sell_date_str in PERIODS:
        score_year = score_date_str[:4]
        print(f"\n{'─' * 40}")
        print(f"  Period: {score_date_str} → {sell_date_str}")
        print(f"{'─' * 40}")
        
        # Load financials
        income, balance, cashflow, stocks = load_financials(db, score_year)
        
        # Merge
        df = income.merge(balance, on='code', how='inner')
        df = df.merge(cashflow, on='code', how='inner')
        df = df.merge(stocks, on='code', how='left')
        
        # Numeric cleanup
        for c in df.columns:
            if c not in ('code', 'name', 'report_period'):
                df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
        
        # Entry filter
        df = df[(df['equity'] > 1e6) & (df['total_assets'] > 1e6) & (df['revenue'] > 1e6)]
        
        # Score
        df = batch_score(df, score_year)
        
        # Get prices for top stocks
        top_n = df.nlargest(TOP_N, 'total_score')
        codes = top_n['code'].tolist()
        
        buy_prices = get_prices(db, codes, score_date_str)
        sell_prices = get_prices(db, codes, sell_date_str)
        
        # Join prices
        top_n = top_n.merge(buy_prices[['code', 'close']], on='code', how='left',
                            suffixes=('', '_buy'))
        top_n = top_n.rename(columns={'close': 'buy_price'})
        top_n = top_n.merge(sell_prices[['code', 'close']], on='code', how='left')
        top_n = top_n.rename(columns={'close': 'sell_price'})
        
        # Compute returns for stocks with both prices
        valid = top_n[(top_n['buy_price'] > 0) & (top_n['sell_price'] > 0)]
        valid['return'] = valid['sell_price'] / valid['buy_price'] - 1
        
        if len(valid) == 0:
            print(f"  ⚠ No valid prices found! (buy_prices: {len(buy_prices)}, sell_prices: {len(sell_prices)})")
            continue
        
        portfolio_return = valid['return'].mean()
        
        print(f"\n  Portfolio: {len(valid)}/{TOP_N} stocks with valid prices")
        print(f"  Portfolio Return: {portfolio_return:+.2%}")
        print(f"\n  Top {min(10, len(valid))} Holdings:")
        for _, row in valid.head(10).iterrows():
            print(f"    {row['code']} {row['name']:<8s}  "
                  f"Score={row['total_score']:.1f}  "
                  f"Buy={row['buy_price']:.1f}  Sell={row['sell_price']:.1f}  "
                  f"Return={row['return']:+.1%}")
        
        results.append({
            'period': f"{score_date_str} → {sell_date_str}",
            'n_valid': len(valid),
            'return': portfolio_return,
        })
        portfolio_values.append(portfolio_return)
    
    # Summary
    print(f"\n{'=' * 60}")
    print(f"  SUMMARY")
    print(f"{'=' * 60}")
    for r in results:
        print(f"  {r['period']}: {r['n_valid']} stocks, {r['return']:+.2%}")
    
    # Benchmark (CSI 300, approximate from public data)
    # 2023-12-29: ~3431, 2024-12-31: ~3934 (+14.7%), 2025-12-31: ~4078 (+3.7%)
    benchmarks = {
        '2023-12-31 → 2024-12-31': 0.147,
        '2024-12-31 → 2025-12-31': 0.037,
    }
    
    print(f"\n{'─' * 40}")
    print(f"  Benchmark (CSI 300)")
    for r in results:
        bm = benchmarks.get(r['period'], 0)
        alpha = r['return'] - bm
        print(f"  {r['period']}: CSI300={bm:+.1%}  Alpha={alpha:+.1%}")
    
    if portfolio_values:
        cumulative = np.prod([1 + r for r in portfolio_values]) - 1
        csi_cum = np.prod([1 + benchmarks[r['period']] for r in results]) - 1
        annualized = (1 + cumulative) ** (1 / len(portfolio_values)) - 1
        print(f"\n  BQAS Cumulative:  {cumulative:+.2%}")
        print(f"  CSI300 Cumulative: {csi_cum:+.2%}")
        print(f"  BQAS Annualized:  {annualized:+.2%}")
        print(f"  Sharpe-like Ratio: {np.mean(portfolio_values) / max(np.std(portfolio_values), 0.01):.2f}")
    
    db.close()


if __name__ == '__main__':
    run_backtest()
