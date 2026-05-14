#!/usr/bin/env python3
"""Batch enrich stock_info with industry classification from XQ API.

Uses _enrich_stock_info per stock with rate limiting.
~5000 stocks in ~45 min. One-time run.
"""
import sys, time, sqlite3
sys.path.insert(0, "/home/xxxsuli/bqas-model")
from bqas.data.fetcher import _enrich_stock_info, _get_conn

conn = _get_conn()
# Find stocks without industry classification
rows = conn.execute("""
    SELECT code, name FROM stock_info 
    WHERE industry_sw IS NULL OR industry_sw = '' OR name = code
    ORDER BY code
""").fetchall()
conn.close()

total = len(rows)
print(f"Need to enrich: {total} stocks")
print(f"Estimated time: {total * 0.6 / 60:.0f} min")

enriched = 0
errors = 0
t0 = time.time()

for i, (code, name) in enumerate(rows):
    try:
        _enrich_stock_info(code)
        enriched += 1
    except Exception as e:
        errors += 1
        if errors <= 5:
            print(f"  Error {code}: {e}")

    # Rate limit
    time.sleep(0.5)

    if (i + 1) % 200 == 0:
        elapsed = time.time() - t0
        eta = elapsed / (i+1) * (total - i - 1)
        print(f"  {i+1}/{total} ({enriched} ok, {errors} err) — {elapsed:.0f}s elapsed, ETA {eta:.0f}s")

elapsed = time.time() - t0

# Verify
conn = _get_conn()
remaining = conn.execute("SELECT COUNT(*) FROM stock_info WHERE industry_sw IS NULL OR industry_sw = ''").fetchone()[0]
conn.close()

print(f"\nDone in {elapsed:.0f}s ({elapsed/60:.1f} min)")
print(f"Enriched: {enriched}, Errors: {errors}, Still empty: {remaining}")
