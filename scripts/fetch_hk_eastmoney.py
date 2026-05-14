#!/usr/bin/env python3
"""Fetch HK stock financials from East Money DC API.
Standalone — no bqas imports, pure sqlite3 + requests.
"""
import sqlite3, time, json, sys
from pathlib import Path
import requests

DB = Path('/home/xxxsuli/bqas-model/bqas/data/cache/bqas_global.db')
API = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
HEADERS = {'User-Agent': 'Mozilla/5.0'}

def dc_fetch(report, code5, extra_filter="", page_size=10):
    """Fetch all pages from East Money DC API."""
    filt = f'(SECURITY_CODE="{code5}")'
    if extra_filter:
        filt = f'{filt}({extra_filter})'
    all_data = []
    page = 1
    while True:
        url = (f"{API}?reportName={report}&columns=ALL"
               f"&filter={filt}&pageSize={page_size}&pageNumber={page}"
               f"&sortColumns=REPORT_DATE&sortTypes=-1")
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            d = r.json()
            if not d.get('success'): break
            rows = d['result'].get('data') or []
            all_data.extend(rows)
            if page >= d['result'].get('pages', 1): break
            page += 1
            time.sleep(0.5)
        except Exception as e:
            print(f"  API error: {e}")
            break
    return all_data

def fetch_one(code4, code5):
    """Fetch all data for one HK stock."""
    print(f"  {code4} ({code5})...")
    
    # Main indicators (annual only)
    main = [r for r in dc_fetch('RPT_HKF10_FN_MAININDICATOR', code5)
            if r.get('DATE_TYPE_CODE') == '001']
    if not main:
        print(f"    No annual data")
        return False
    
    # Income detail → interest expense
    income_det = dc_fetch('RPT_HKF10_FN_INCOME', code5)
    interest_map = {}
    for r in income_det:
        if r.get('ITEM_NAME') == '融资成本':
            y = r['REPORT_DATE'][:4]
            interest_map[y] = float(r.get('AMOUNT', 0) or 0)
    
    # Balance detail → specific items
    bal_det = dc_fetch('RPT_HKF10_FN_BALANCE', code5)
    bal_map = {}
    for r in bal_det:
        y = r['REPORT_DATE'][:4]
        bal_map.setdefault(y, {})[r['ITEM_NAME']] = float(r.get('AMOUNT', 0) or 0)
    
    # Write
    conn = sqlite3.connect(str(DB))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS income_hk(code TEXT, report_period TEXT, revenue REAL,
            operating_profit REAL, net_income REAL, interest_expense REAL, ebit REAL,
            PRIMARY KEY(code, report_period));
        CREATE TABLE IF NOT EXISTS balance_hk(code TEXT, report_period TEXT, total_assets REAL,
            total_liabilities REAL, equity REAL, goodwill REAL, cash REAL, inventory REAL,
            accounts_receivable REAL, long_term_invest REAL, short_term_debt REAL,
            long_term_debt REAL, PRIMARY KEY(code, report_period));
        CREATE TABLE IF NOT EXISTS cashflow_hk(code TEXT, report_period TEXT,
            operating_cf REAL, capex REAL, financing_cf REAL,
            PRIMARY KEY(code, report_period));
    """)
    
    n = 0
    for r in main:
        try:
            y = r['REPORT_DATE'][:4]
            p = f"{y}-12-31"
            bm = bal_map.get(y, {})
            
            # Income
            rev = float(r.get('OPERATE_INCOME', 0) or 0)
            op = float(r.get('OPERATE_PROFIT', 0) or 0)
            ni = float(r.get('HOLDER_PROFIT', 0) or 0)
            ie = interest_map.get(y, 0)
            ebit = op + ie
            conn.execute("INSERT OR REPLACE INTO income_hk VALUES(?,?,?,?,?,?,?)",
                         (code4, p, rev, op, ni, ie, ebit))
            
            # Balance
            ta = float(r.get('TOTAL_ASSETS', 0) or 0)
            tl = float(r.get('TOTAL_LIABILITIES', 0) or 0)
            eq = float(r.get('TOTAL_PARENT_EQUITY', 0) or 0)
            cash = float(r.get('END_CASH', 0) or 0)
            inv = bm.get('存货', 0)
            ar = bm.get('应收帐款', bm.get('应收账款', 0))
            std = (bm.get('短期贷款', 0) + bm.get('短期借款', 0) + bm.get('融资租赁负债(流动)', 0))
            ltd = (bm.get('长期贷款', 0) + bm.get('长期借款', 0) + bm.get('融资租赁负债(非流动)', 0))
            gw = bm.get('商誉', 0)
            lti = bm.get('投资物业', 0) + bm.get('中长期存款', 0)
            
            conn.execute("INSERT OR REPLACE INTO balance_hk VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                         (code4, p, ta, tl, eq, gw, cash, inv, ar, lti, std, ltd))
            
            # Cashflow
            ocf = float(r.get('NETCASH_OPERATE', 0) or 0)
            fcf = float(r.get('NETCASH_FINANCE', 0) or 0)
            icf = float(r.get('NETCASH_INVEST', 0) or 0)
            capex = abs(icf) * 0.6 if icf < 0 else 0
            
            conn.execute("INSERT OR REPLACE INTO cashflow_hk VALUES(?,?,?,?,?)",
                         (code4, p, ocf, capex, fcf))
            
            # Update stock_info
            name = r.get('SECURITY_NAME_ABBR', '')
            ts = float(r.get('HK_COMMON_SHARES', 0) or 0)
            mc = float(r.get('TOTAL_MARKET_CAP', 0) or 0)
            if name:
                conn.execute("UPDATE stock_info_hk SET name=?, total_shares=COALESCE(NULLIF(?,0),total_shares), market_cap=COALESCE(NULLIF(?,0),market_cap) WHERE code=?",
                             (name, ts, mc, code4))
            
            n += 1
        except Exception as e:
            print(f"    Error {p}: {e}")
    
    conn.commit()
    conn.close()
    print(f"    ✓ {n} years written")
    return n >= 3


if __name__ == '__main__':
    # Stocks missing data
    missing = [
        ('0780.HK', '00780'), ('1347.HK', '01347'), ('1698.HK', '01698'),
        ('3888.HK', '03888'), ('6618.HK', '06618'), ('6690.HK', '06690'),
        ('9626.HK', '09626'), ('9660.HK', '09660'), ('9863.HK', '09863'),
        ('9866.HK', '09866'), ('9868.HK', '09868'), ('9961.HK', '09961'),
    ]
    
    # Also fetch existing stocks that have yfinance data but no interest_expense/capex detail
    # Check if --refresh flag
    if '--refresh' in sys.argv:
        conn = sqlite3.connect(str(DB))
        all_hk = conn.execute("SELECT code FROM stock_info_hk ORDER BY code").fetchall()
        conn.close()
        missing = [(r[0], r[0].replace('.HK','').zfill(5)) for r in all_hk]
    
    ok = 0
    for i, (c4, c5) in enumerate(missing):
        print(f"[{i+1}/{len(missing)}]", end=" ")
        try:
            if fetch_one(c4, c5):
                ok += 1
        except Exception as e:
            print(f"    FAILED: {e}")
        time.sleep(2)
    
    print(f"\nDone: {ok}/{len(missing)} succeeded")
