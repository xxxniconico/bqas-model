#!/usr/bin/env python3
"""Compare TTM table data vs base table data for 600519 (no pandas dependency)"""
import sqlite3
import json

CODE = '600519'
db_path = 'bqas/data/cache/bqas.db'
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# ── Part 1: List all tables and schemas ──
print("=" * 70)
print("DATABASE SCHEMA")
print("=" * 70)
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
for table in tables:
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    col_names = [c['name'] for c in cols]
    print(f"\n{table}: ({len(cols)} cols)")
    print(f"  columns: {col_names}")

# ── Part 2: TTM tables ──
print("\n" + "=" * 70)
print("PART 2: TTM TABLES (used by screener) for", CODE)
print("=" * 70)

for table_name in ['income_ttm', 'balance_ttm', 'cashflow_ttm']:
    try:
        rows = conn.execute(f"SELECT * FROM {table_name} WHERE code='{CODE}'").fetchall()
        if rows:
            for r in rows:
                d = dict(r)
                # Format large numbers
                for k, v in d.items():
                    if isinstance(v, (int, float)) and abs(v) > 1000000 and k != 'code':
                        d[k] = f"{v:,.0f}"
                print(f"\n{table_name}:")
                for k, v in d.items():
                    print(f"  {k}: {v}")
        else:
            print(f"\n{table_name}: NO DATA for {CODE}")
    except Exception as e:
        print(f"\n{table_name}: ERROR - {e}")

# Also check how many stocks have TTM data
print("\n--- TTM table row counts ---")
for table_name in ['income_ttm', 'balance_ttm', 'cashflow_ttm']:
    try:
        cnt = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        print(f"  {table_name}: {cnt} rows total")
    except:
        print(f"  {table_name}: DOES NOT EXIST")

# ── Part 3: Base tables (used by CLI fetch_financials) ──
print("\n" + "=" * 70)
print("PART 3: BASE TABLES (used by CLI) for", CODE)
print("=" * 70)

# Income
print("\n--- income table (latest 8 periods) ---")
rows = conn.execute(f"SELECT * FROM income WHERE code='{CODE}' ORDER BY report_period DESC LIMIT 8").fetchall()
for r in rows:
    d = dict(r)
    for k, v in d.items():
        if isinstance(v, (int, float)) and abs(v) > 1000000 and k != 'code':
            d[k] = f"{v:,.0f}"
    print(f"  {d}")

# Does income table have op_cost column?
first = conn.execute(f"SELECT * FROM income LIMIT 1").fetchone()
if first:
    income_cols = list(first.keys())
    print(f"\n  income columns: {income_cols}")
    if 'op_cost' in income_cols:
        print("  ✓ op_cost EXISTS in income table")
    else:
        print("  ✗ op_cost MISSING from income table")

# Balance
print("\n--- balance table (latest 5 periods) ---")
rows = conn.execute(f"SELECT * FROM balance WHERE code='{CODE}' ORDER BY report_period DESC LIMIT 5").fetchall()
for r in rows:
    d = dict(r)
    for k, v in d.items():
        if isinstance(v, (int, float)) and abs(v) > 1000000 and k != 'code':
            d[k] = f"{v:,.0f}"
    print(f"  {d}")

# Cashflow
print("\n--- cashflow table (latest 5 periods) ---")
rows = conn.execute(f"SELECT * FROM cashflow WHERE code='{CODE}' ORDER BY report_period DESC LIMIT 5").fetchall()
for r in rows:
    d = dict(r)
    for k, v in d.items():
        if isinstance(v, (int, float)) and abs(v) > 1000000 and k != 'code':
            d[k] = f"{v:,.0f}"
    print(f"  {d}")

# ── Part 4: Stock info & quotes ──
print("\n--- stock_info for", CODE, "---")
row = conn.execute(f"SELECT * FROM stock_info WHERE code='{CODE}'").fetchone()
if row:
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, (int, float)) and abs(v) > 1000000 and k != 'code':
            d[k] = f"{v:,.0f}"
    print(f"  {d}")

print("\n--- quotes table (latest for", CODE, ") ---")
row = conn.execute(f"SELECT * FROM quotes WHERE code='{CODE}' ORDER BY trade_date DESC LIMIT 1").fetchone()
if row:
    d = dict(row)
    print(f"  {d}")
    # Recalculate PB
    info = conn.execute(f"SELECT total_shares FROM stock_info WHERE code='{CODE}'").fetchone()
    bal = conn.execute(f"SELECT equity FROM balance WHERE code='{CODE}' ORDER BY report_period DESC LIMIT 1").fetchone()
    bal_ttm = None
    try:
        bal_ttm = conn.execute(f"SELECT equity FROM balance_ttm WHERE code='{CODE}' AND report_period='TTM'").fetchone()
    except:
        pass
    if info and bal and info['total_shares'] and bal['equity']:
        total_shares = float(info['total_shares'])
        equity = float(bal['equity'])
        price = float(d['close'])
        pb_calc = price / (equity / total_shares)
        print(f"\n  PB check (quotes table):")
        print(f"    quotes.pb = {d['pb']}")
        print(f"    PB calc = price({price}) / (equity({equity:,.0f}) / shares({total_shares:,.0f})) = {pb_calc:.4f}")
        if bal_ttm:
            eq_ttm = float(bal_ttm['equity'])
            pb_ttm = price / (eq_ttm / total_shares)
            print(f"    PB using balance_ttm.equity({eq_ttm:,.0f}) = {pb_ttm:.4f}")

# ── Part 5: Check what quarterly data exists for TTM computation in CLI ──
print("\n" + "=" * 70)
print("PART 5: QUARTERLY DATA (for CLI TTM override)")
print("=" * 70)

quarterly = conn.execute(f"""
    SELECT report_period, revenue, operating_profit, net_income, interest_expense 
    FROM income 
    WHERE code='{CODE}' AND report_period NOT LIKE '%1231'
    ORDER BY report_period DESC LIMIT 8
""").fetchall()

if quarterly:
    print(f"\n--- Income quarterly (non-1231, latest 8) ---")
    for r in quarterly:
        d = dict(r)
        for k, v in d.items():
            if isinstance(v, (int, float)) and abs(v) > 1000000 and k != 'code':
                d[k] = f"{v:,.0f}"
        print(f"  {d}")

    # Show the TTM computation manually
    print("\n--- Manual TTM computation from quarterly ---")
    q_sorted = sorted(quarterly, key=lambda x: x['report_period'], reverse=True)[:4]
    if len(q_sorted) >= 4:
        tot_rev = sum(r['revenue'] or 0 for r in q_sorted)
        tot_op = sum(r['operating_profit'] or 0 for r in q_sorted)
        tot_ni = sum(r['net_income'] or 0 for r in q_sorted)
        tot_ie = sum(abs(r['interest_expense'] or 0) for r in q_sorted)
        print(f"  Periods: {[r['report_period'] for r in q_sorted]}")
        print(f"  TTM revenue = {tot_rev:,.0f}")
        print(f"  TTM operating_profit = {tot_op:,.0f}")
        print(f"  TTM net_income = {tot_ni:,.0f}")
        print(f"  TTM interest_expense = {tot_ie:,.0f}")
else:
    print("  No quarterly income data!")

# Also check quarterly cashflow
quarterly_cf = conn.execute(f"""
    SELECT report_period, operating_cf, capex FROM cashflow 
    WHERE code='{CODE}' AND report_period NOT LIKE '%1231'
    ORDER BY report_period DESC LIMIT 8
""").fetchall()
print(f"\n--- Cashflow quarterly (non-1231, latest {len(quarterly_cf)}) ---")
for r in quarterly_cf:
    d = dict(r)
    for k, v in d.items():
        if isinstance(v, (int, float)) and abs(v) > 1000000 and k != 'code':
            d[k] = f"{v:,.0f}"
    print(f"  {d}")

# ── Part 6: Compare TTM vs base for V dimension fields ──
print("\n" + "=" * 70)
print("PART 6: TTM vs BASE COMPARISON (V dimension fields)")
print("=" * 70)

# Fields used by V dimension:
# operating_profit, interest_expense (from income)
# total_assets, total_liabilities, equity, cash_equiv, long_term_invest (from balance)
# operating_cf, capex (from cashflow)

# Get TTM data
ttm_inc = None
ttm_bal = None
ttm_cf = None
try:
    ttm_inc = conn.execute(f"SELECT * FROM income_ttm WHERE code='{CODE}' AND report_period='TTM'").fetchone()
except: pass
try:
    ttm_bal = conn.execute(f"SELECT * FROM balance_ttm WHERE code='{CODE}' AND report_period='TTM'").fetchone()
except: pass
try:
    ttm_cf = conn.execute(f"SELECT * FROM cashflow_ttm WHERE code='{CODE}' AND report_period='TTM'").fetchone()
except: pass

# Get latest base annual data
base_inc = conn.execute(f"SELECT * FROM income WHERE code='{CODE}' ORDER BY report_period DESC LIMIT 1").fetchone()
base_bal = conn.execute(f"SELECT * FROM balance WHERE code='{CODE}' ORDER BY report_period DESC LIMIT 1").fetchone()
base_cf = conn.execute(f"SELECT * FROM cashflow WHERE code='{CODE}' ORDER BY report_period DESC LIMIT 1").fetchone()

print("\n--- Income fields ---")
inc_fields = ['operating_profit', 'interest_expense']
if ttm_inc and base_inc:
    for f in inc_fields:
        ttm_val = ttm_inc[f] if f in ttm_inc.keys() else 'N/A'
        base_val = base_inc[f] if f in base_inc.keys() else 'N/A'
        diff = None
        if ttm_val != 'N/A' and base_val != 'N/A' and ttm_val and base_val:
            diff = f"{float(ttm_val - base_val):,.0f} ({(float(ttm_val)/float(base_val)-1)*100:.1f}%)" if float(base_val) != 0 else 'N/A (base=0)'
        print(f"  {f}: TTM={ttm_val if ttm_val != 'N/A' else 'N/A'} vs BASE={base_val if base_val != 'N/A' else 'N/A'} diff={diff}")

print("\n--- Balance fields ---")
bal_fields = ['total_assets', 'total_liabilities', 'equity', 'cash_equiv', 'long_term_invest']
if ttm_bal and base_bal:
    for f in bal_fields:
        ttm_val = ttm_bal[f] if f in ttm_bal.keys() else 'N/A'
        base_val = base_bal[f] if f in base_bal.keys() else 'N/A'
        diff = None
        if ttm_val != 'N/A' and base_val != 'N/A' and ttm_val and base_val:
            diff = f"{float(ttm_val - base_val):,.0f} ({(float(ttm_val)/float(base_val)-1)*100:.1f}%)" if float(base_val) != 0 else 'N/A'
        print(f"  {f}: TTM={ttm_val if ttm_val != 'N/A' else 'N/A'} vs BASE={base_val if base_val != 'N/A' else 'N/A'} diff={diff}")

print("\n--- Cashflow fields ---")
cf_fields = ['operating_cf', 'capex']
if ttm_cf and base_cf:
    for f in cf_fields:
        ttm_val = ttm_cf[f] if f in ttm_cf.keys() else 'N/A'
        base_val = base_cf[f] if f in base_cf.keys() else 'N/A'
        diff = None
        if ttm_val != 'N/A' and base_val != 'N/A' and ttm_val and base_val:
            diff = f"{float(ttm_val - base_val):,.0f} ({(float(ttm_val)/float(base_val)-1)*100:.1f}%)" if float(base_val) != 0 else 'N/A'
        print(f"  {f}: TTM={ttm_val if ttm_val != 'N/A' else 'N/A'} vs BASE={base_val if base_val != 'N/A' else 'N/A'} diff={diff}")

conn.close()
