#!/usr/bin/env python3
"""Batch enrich stock_info.industry_sw from Sina Finance pages.
No akshare needed — pure HTTP scraping. Works on WSL.
"""
import sqlite3, subprocess, re, time, sys
from concurrent.futures import ThreadPoolExecutor, as_completed

DB = "bqas/data/cache/bqas.db"

def fetch_industry(code: str) -> str | None:
    """Fetch industry from Sina corp info page. Returns industry name or None."""
    url = f"http://vip.stock.finance.sina.com.cn/corp/go.php/vCI_CorpOtherInfo/stockid/{code}/menu_num/2.phtml"
    try:
        r = subprocess.run(
            ["curl", "-sL", "--max-time", "8", url,
             "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)"],
            capture_output=True, timeout=10
        )
        html = r.stdout.decode("gbk", errors="replace").replace('\n', '').replace('\r', '')
        # Pattern: 所属行业板块 header → next <td> value
        m = re.search(r'所属行业板块.*?<td[^>]*>([^<]+)</td>', html)
        if m:
            ind = m.group(1).strip()
            if ind and ind not in ("—", "-", ""):
                return ind
    except Exception:
        pass
    return None

def main():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")

    # Get codes WITHOUT industry
    missing = conn.execute(
        "SELECT code FROM stock_info WHERE industry_sw IS NULL OR industry_sw=''"
    ).fetchall()
    codes = [r[0] for r in missing]
    total = len(codes)
    print(f"Need industry for {total} stocks")

    if total == 0:
        print("All done!")
        conn.close()
        return

    success = 0
    failed = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(fetch_industry, c): c for c in codes}
        for i, f in enumerate(as_completed(futures), 1):
            code = futures[f]
            ind = f.result()
            if ind:
                conn.execute("UPDATE stock_info SET industry_sw=? WHERE code=?", (ind, code))
                success += 1
            else:
                failed += 1

            if i % 200 == 0:
                conn.commit()
                elapsed = time.time() - t0
                rate = i / elapsed
                eta = (total - i) / rate if rate > 0 else 0
                print(f"  {i}/{total} ({success} ok, {failed} fail) | {rate:.0f}/s | ETA {eta:.0f}s")

    conn.commit()
    elapsed = time.time() - t0
    print(f"\nDone: {success} enriched, {failed} failed in {elapsed:.0f}s")

    remaining = conn.execute(
        "SELECT COUNT(*) FROM stock_info WHERE industry_sw IS NULL OR industry_sw=''"
    ).fetchone()[0]
    print(f"Remaining: {remaining}")

    # Show distribution
    dist = conn.execute(
        "SELECT industry_sw, COUNT(*) FROM stock_info WHERE industry_sw != '' GROUP BY industry_sw ORDER BY COUNT(*) DESC LIMIT 20"
    ).fetchall()
    print("\nTop industries:")
    for ind, cnt in dist:
        print(f"  {ind}: {cnt}")

    conn.close()

if __name__ == "__main__":
    main()
