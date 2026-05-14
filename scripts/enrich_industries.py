#!/usr/bin/env python3
"""Batch enrich stock_info.industry_sw via XQ API for all A-share stocks.

Fills the 5488 stocks currently missing industry classification,
which causes the factor cache to use wrong dimension weights.
"""
import sqlite3
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

DB = "bqas/data/cache/bqas.db"

def enrich_one(code: str) -> tuple[str, str | None]:
    """Returns (code, industry_sw) or (code, None) on failure."""
    try:
        import akshare as ak
        # Map SH/SZ prefix
        prefix = "SH" if code.startswith("6") else "SZ"
        info = ak.stock_individual_basic_info_xq(symbol=f"{prefix}{code}", timeout=8)
        # XQ returns DataFrame with columns ['item', 'value']
        info_dict = dict(zip(info["item"], info["value"]))
        ind = info_dict.get("affiliate_industry", {})
        if isinstance(ind, dict):
            return code, ind.get("ind_name", "")
        return code, ""
    except Exception as e:
        return code, None

def main():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")
    
    # Get codes missing industry
    missing = conn.execute(
        "SELECT code FROM stock_info WHERE industry_sw IS NULL OR industry_sw=''"
    ).fetchall()
    codes = [r[0] for r in missing]
    total = len(codes)
    print(f"Need to enrich {total} stocks")
    
    if total == 0:
        print("All stocks already have industry_sw!")
        conn.close()
        return
    
    success = 0
    failed = 0
    t0 = time.time()
    
    # Thread pool — 15 concurrent workers
    with ThreadPoolExecutor(max_workers=15) as pool:
        futures = {pool.submit(enrich_one, c): c for c in codes}
        for i, f in enumerate(as_completed(futures), 1):
            code, industry = f.result()
            if industry is not None:
                conn.execute(
                    "UPDATE stock_info SET industry_sw=? WHERE code=?",
                    (industry, code)
                )
                success += 1
            else:
                failed += 1
            
            if i % 100 == 0:
                conn.commit()
                elapsed = time.time() - t0
                rate = i / elapsed
                eta = (total - i) / rate if rate > 0 else 0
                print(f"  {i}/{total} ({success} ok, {failed} fail) | {rate:.0f}/s | ETA {eta:.0f}s")
    
    conn.commit()
    elapsed = time.time() - t0
    print(f"\nDone: {success} enriched, {failed} failed in {elapsed:.0f}s")
    
    # Verify
    remaining = conn.execute(
        "SELECT COUNT(*) FROM stock_info WHERE industry_sw IS NULL OR industry_sw=''"
    ).fetchone()[0]
    print(f"Remaining empty: {remaining}")
    conn.close()

if __name__ == "__main__":
    main()
