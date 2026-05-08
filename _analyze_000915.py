"""
华特达因 (000915) 逐因子拆解 v2 — 正确的DB schema
"""
import os, sys
os.chdir('/home/xxxsuli/bqas-model')
sys.path.insert(0, '/home/xxxsuli/bqas-model')

import sqlite3
import pandas as pd
import numpy as np

db = sqlite3.connect('/home/xxxsuli/bqas-model/bqas/data/cache/bqas.db')
code = '000915'

# JOIN income + balance + cashflow + stock_info
df = pd.read_sql_query(f"""
    SELECT i.code, i.report_period,
           i.revenue, i.operating_profit, i.net_income, i.interest_expense,
           b.total_assets, b.total_liabilities, b.equity, b.goodwill,
           b.accounts_receivable, b.inventory, b.cash_equiv,
           b.short_term_debt, b.long_term_debt,
           c.operating_cf, c.capex,
           s.name, s.industry_sw, s.total_shares, s.is_st
    FROM income i
    JOIN balance b ON i.code=b.code AND i.report_period=b.report_period
    JOIN cashflow c ON i.code=c.code AND i.report_period=c.report_period
    LEFT JOIN stock_info s ON i.code=s.code
    WHERE i.code='{code}'
    ORDER BY i.report_period DESC
""", db)

print(f"=== 华特达因({code}) 财报: {len(df)}期 ===")
print(f"期间: {df['report_period'].min()} ~ {df['report_period'].max()}")
if len(df) > 0:
    print(f"行业: {df['industry_sw'].iloc[0]}")
    print(f"总股本: {df['total_shares'].iloc[0]}")
    print(f"ST标记: {df['is_st'].iloc[0]}")
print()

# Show 5 latest periods
for _, r in df.head(5).iterrows():
    fcf = r['operating_cf'] + r['capex']  # capex is negative
    roe = r['net_income']/r['equity']*100 if r['equity']>0 else 0
    print(f"  {r['report_period']}: 营收{r['revenue']/1e8:.1f}亿 净利{r['net_income']/1e8:.1f}亿 ROE{roe:.1f}% CFO{r['operating_cf']/1e8:.1f}亿 FCF{fcf/1e8:.1f}亿")

latest = df.iloc[0]

# === FACTOR COMPUTATION (following batch_rank3 logic) ===
print("\n========================================")
print("  逐因子计算（对齐 batch_rank3）")
print("========================================")

# --- Q1: ROE (5yr avg) ---
df['roe'] = df['net_income'] / df['equity'].replace(0, np.nan)
roe_vals = df['roe'].tail(5).dropna()
roe_avg = roe_vals.mean() if len(roe_vals)>0 else 0
roe_score = min(roe_avg / 0.30, 1.0) * 10 if roe_avg > 0 else 0
print(f"\nQ1 ROE稳定性 (12%)")
print(f"  五年ROE: {[f'{x*100:.1f}%' for x in roe_vals]}")
print(f"  均值: {roe_avg*100:.1f}%")
print(f"  原始得分: {roe_score:.1f}/10")

# --- Q2: ROIC ---
ebit = latest['operating_profit'] + (latest['interest_expense'] or 0)
total_cap = latest['total_assets']
roic_val = ebit / total_cap if total_cap > 0 else 0
roic_score = min(roic_val / 0.15, 1.0) * 10 if roic_val > 0 else 0
print(f"\nQ2 ROIC (8%)")
print(f"  EBIT≈{ebit/1e8:.1f}亿 总资产={total_cap/1e8:.1f}亿 → ROIC={roic_val*100:.1f}%")
print(f"  原始得分: {roic_score:.1f}/10")

# --- Q3: FCF Yield ---
fcf_raw = latest['operating_cf'] + latest['capex']
# Need market cap - try to get from stock_info
shares = df['total_shares'].iloc[0] if pd.notna(df['total_shares'].iloc[0]) else 0
# No price data in DB (quotes is empty). Use approximate from operating data
# Fallback: use book value as proxy or skip market-based factors
print(f"\nQ3/V2: 估值因子 (需要价格数据)")
print(f"  FCF={fcf_raw/1e8:.1f}亿 总股本={shares}")
print(f"  ⚠ quotes表为空，无法计算EV/PB/FCF_Yield")
print(f"  估值维度在批量排名中可能因此得0分")

# --- Q4: CFO/NI ---
cfo = latest['operating_cf']
ni = latest['net_income']
cfo_ni = cfo / ni if ni != 0 else 0
cfo_ni_score = min(max(cfo_ni, 0) / 1.0, 1.0) * 10
print(f"\nQ4 现金流真实性 (5%)")
print(f"  CFO={cfo/1e8:.1f}亿 NI={ni/1e8:.1f}亿 → CFO/NI={cfo_ni:.2f}")
print(f"  原始得分: {cfo_ni_score:.1f}/10")

# --- Financial Health ---
debt_ratio = latest['total_liabilities'] / latest['total_assets'] if latest['total_assets']>0 else 0
print(f"\n财务健康:")
print(f"  资产负债率: {debt_ratio*100:.1f}%")
print(f"  总负债: {latest['total_liabilities']/1e8:.1f}亿")
print(f"  净资产: {latest['equity']/1e8:.1f}亿")

# Interest coverage
ie = latest['interest_expense'] or 0
ebit_op = latest['operating_profit'] or 0
ic = ebit_op / abs(ie) if ie != 0 else 999
print(f"  利息保障(operating_profit/|IE|): {ic:.1f}" if ic<900 else f"  利息保障: 无利息支出(满分)")

# --- Growth ---
df_s = df.sort_values('report_period')
rev_first = df_s['revenue'].iloc[0]
rev_last = df_s['revenue'].iloc[-1]
n = len(df_s) - 1
if rev_first > 0 and n > 0:
    cagr = (rev_last / rev_first) ** (1/n) - 1
    print(f"\n增长:")
    print(f"  营收 {df_s['report_period'].iloc[0]}→{df_s['report_period'].iloc[-1]} ({n}期)")
    print(f"  从{rev_first/1e8:.1f}亿→{rev_last/1e8:.1f}亿, CAGR={cagr*100:.1f}%")

# --- Check batch_rank3's actual scoring for 000915 ---
print("\n========================================")
print("  批量排名中的实际得分 (batch_rank3)")

# Run batch_rank3 but read its output
import subprocess
r = subprocess.run(['python3', 'batch_rank3.py'], capture_output=True, text=True, timeout=120, cwd='/home/xxxsuli/bqas-model')
lines = r.stdout.split('\n')
for l in lines:
    if '000915' in l or '华特达因' in l:
        print(f"  {l.strip()}")

# More broadly, find the section that lists top 30 with scores
in_table = False
for l in lines:
    if '华特达因' in l or ('000915' in l and ('分' in l or '.' in l)):
        print(f"  >>> {l.strip()}")
    if '中性化' in l and 'Top 30' in l:
        in_table = True
    if in_table and ('000915' in l or '华特' in l):
        print(f"  >>> {l.strip()}")

print("\n=== DONE ===")
