#!/usr/bin/env python3
"""Fetch HK stock financials from Sina Finance for HSTECH constituents.
One-shot: inserts basic stock_info for the 10 new HSTECH stocks.
Full financial parsing TBD if Sina provides structured data.
"""
import sqlite3, time, subprocess, logging
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

DB = Path(__file__).parent.parent / 'bqas' / 'data' / 'cache' / 'bqas_global.db'
conn = sqlite3.connect(str(DB))

# Ensure tables exist
conn.executescript("""
    CREATE TABLE IF NOT EXISTS stock_info_hk (
        code TEXT PRIMARY KEY, name TEXT, industry TEXT,
        market_cap REAL, country TEXT, sector TEXT,
        total_shares REAL, listing_date TEXT, currency TEXT
    );
    CREATE TABLE IF NOT EXISTS income_hk (
        code TEXT, report_period TEXT, revenue REAL,
        operating_profit REAL, net_income REAL,
        interest_expense REAL, ebit REAL,
        PRIMARY KEY(code, report_period)
    );
    CREATE TABLE IF NOT EXISTS balance_hk (
        code TEXT, report_period TEXT, total_assets REAL,
        total_liabilities REAL, equity REAL, goodwill REAL,
        cash REAL, inventory REAL, accounts_receivable REAL,
        long_term_invest REAL, short_term_debt REAL, long_term_debt REAL,
        PRIMARY KEY(code, report_period)
    );
    CREATE TABLE IF NOT EXISTS cashflow_hk (
        code TEXT, report_period TEXT, operating_cf REAL,
        capex REAL, financing_cf REAL,
        PRIMARY KEY(code, report_period)
    );
    CREATE TABLE IF NOT EXISTS quotes_hk (
        code TEXT, trade_date TEXT, close REAL, pb REAL,
        market_cap REAL, volume REAL,
        PRIMARY KEY(code, trade_date)
    );
""")
conn.commit()

HK_NAMES = {
    '0241.HK': '阿里健康',
    '0780.HK': '同程旅行',
    '1347.HK': '华虹半导体',
    '1698.HK': '腾讯音乐-SW',
    '2015.HK': '理想汽车-W',
    '6690.HK': '海尔智家',
    '9863.HK': '零跑汽车',
    '9866.HK': '蔚来-SW',
    '9868.HK': '小鹏集团-W',
    '9961.HK': '携程集团-S',
}

# Insert basic stock_info for all new HSTECH stocks
for ticker, name in HK_NAMES.items():
    existing = conn.execute("SELECT 1 FROM stock_info_hk WHERE code=?", (ticker,)).fetchone()
    if not existing:
        conn.execute(
            "INSERT OR REPLACE INTO stock_info_hk VALUES(?,?,?,?,?,?,?,?,?)",
            (ticker, name, '', 0, 'HK', '', 0, datetime.now().strftime('%Y-%m-%d'), 'HKD')
        )
        print(f"  {ticker} ✓ inserted ({name})")
    else:
        print(f"  {ticker} already exists")

conn.commit()
conn.close()
print("DONE")
