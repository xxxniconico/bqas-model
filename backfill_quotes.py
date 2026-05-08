#!/usr/bin/env python3
"""
BQAS Quotes Backfill — fetch daily K-line from Sina, resample to monthly close,
store in quotes table for price-simulation backtesting.

Strategy:
  1. Read stock list + total_shares from stock_info
  2. For each stock, fetch Sina daily K-line (last ~2 years, ~500 pts)
  3. Extract last trading day of each month → monthly close
  4. Compute market_cap = close × total_shares
  5. INSERT OR REPLACE into quotes table

Usage:
  python backfill_quotes.py              # Full backfill (~5000 stocks, ~60 min)
  python backfill_quotes.py --top 100    # Top 100 stocks only (~2 min)
  python backfill_quotes.py --codes 600519,000858  # Specific stocks
"""

import sqlite3
import subprocess
import json
import sys
import os
import time
from datetime import date, datetime
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(__file__), 'bqas', 'data', 'cache', 'bqas.db')


def fetch_sina_kline(symbol, datalen=800):
    """Fetch daily K-line from Sina. Returns list of {day, open, high, low, close, volume}."""
    url = (
        f"https://quotes.sina.cn/cn/api/jsonp_v2.php/data/"
        f"CN_MarketDataService.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={datalen}"
    )
    cmd = f'curl -s --max-time 10 "{url}" -H "Referer: https://finance.sina.com.cn"'
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=12)
        raw = result.stdout
        if 'data(' not in raw:
            return None
        start = raw.index('data(') + 5
        end = raw.rindex(')')
        return json.loads(raw[start:end])
    except Exception as e:
        return None


def monthly_close(klines):
    """Extract last trading day of each month from daily klines.
    Returns [{trade_date, close, volume}, ...]"""
    by_month = defaultdict(list)
    for k in klines:
        month_key = k['day'][:7]  # "2024-01"
        by_month[month_key].append(k)
    
    result = []
    for month_key in sorted(by_month):
        last = by_month[month_key][-1]  # last trading day of month
        result.append({
            'trade_date': last['day'],
            'close': float(last['close']),
            'volume': int(last.get('volume', 0)),
        })
    return result


def backfill(db_path, codes=None, top_n=None, limit=None):
    """Main backfill function."""
    db = sqlite3.connect(db_path)
    
    # Get stocks to process
    if codes:
        placeholders = ','.join(['?'] * len(codes))
        rows = db.execute(
            f"SELECT code, name, total_shares FROM stock_info WHERE code IN ({placeholders})",
            codes
        ).fetchall()
    elif top_n:
        # Top N by equity (proxy for importance) — use latest balance
        rows = db.execute("""
            SELECT si.code, si.name, si.total_shares
            FROM stock_info si
            JOIN balance b ON si.code = b.code AND b.report_period = '20251231'
            ORDER BY b.equity DESC
            LIMIT ?
        """, (top_n,)).fetchall()
    else:
        # All stocks with balance data (for quotes to be useful in backtesting)
        rows = db.execute("""
            SELECT DISTINCT si.code, si.name, si.total_shares
            FROM stock_info si
            JOIN balance b ON si.code = b.code AND b.report_period = '20251231'
        """).fetchall()
    
    if limit:
        rows = rows[:limit]
    
    print(f"📊 Backfilling quotes for {len(rows)} stocks...")
    
    total_inserted = 0
    errors = 0
    t0 = time.time()
    
    for i, (code, name, total_shares) in enumerate(rows):
        # Determine Sina symbol prefix
        prefix = 'sh' if code.startswith(('6', '9')) else 'sz'
        symbol = f"{prefix}{code}"
        
        klines = fetch_sina_kline(symbol, datalen=600)
        if not klines:
            errors += 1
            if errors <= 5:
                print(f"  ⚠ {code} {name}: API failed")
            continue
        
        monthly = monthly_close(klines)
        if not monthly:
            continue
        
        inserted = 0
        for m in monthly:
            close_price = m['close']
            market_cap = close_price * total_shares if (total_shares or 0) > 0 else 0
            try:
                db.execute("""
                    INSERT OR REPLACE INTO quotes(code, trade_date, close, market_cap, pb, turnover_amount)
                    VALUES(?, ?, ?, ?, 0, 0)
                """, (code, m['trade_date'], close_price, market_cap))
                inserted += 1
            except Exception:
                pass
        
        db.commit()
        total_inserted += inserted
        
        # Progress every 50 stocks
        if (i + 1) % 50 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            eta = (len(rows) - i - 1) / rate if rate > 0 else 0
            print(f"  [{i+1}/{len(rows)}] {code} {name}: {inserted} pts | "
                  f"{rate:.1f} stocks/s | ETA {eta:.0f}s")
    
    elapsed = time.time() - t0
    print(f"\n✅ Done: {total_inserted} records, {errors} errors, {elapsed:.0f}s "
          f"({len(rows)/elapsed:.1f} stocks/s)")
    
    # Show coverage
    stock_count = db.execute("SELECT COUNT(DISTINCT code) FROM quotes").fetchone()[0]
    month_count = db.execute("SELECT COUNT(DISTINCT trade_date) FROM quotes").fetchone()[0]
    print(f"   Coverage: {stock_count} stocks × {month_count} months")
    
    db.close()


if __name__ == '__main__':
    codes = None
    top_n = None
    
    args = sys.argv[1:]
    if '--codes' in args:
        idx = args.index('--codes')
        codes = args[idx + 1].split(',')
    if '--top' in args:
        idx = args.index('--top')
        top_n = int(args[idx + 1])
    
    backfill(DB_PATH, codes=codes, top_n=top_n)
