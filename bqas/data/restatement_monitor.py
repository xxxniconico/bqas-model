#!/usr/bin/env python3
"""BQAS 会计重述舆情监控

扫描东方财富全A股公告，检测会计差错更正/财务重述/追溯调整关键词，
命中后自动录入 restatement_blacklist 表。
"""

import sqlite3
import json
import time
import sys
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.parse import urlencode

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else __import__('pathlib').Path(__file__).parent / "cache" / "bqas.db"

KEYWORDS = [
    "会计差错更正",
    "前期会计差错",
    "前期差错更正",
    "追溯调整",
    "追溯重述",
    "财务重述",
]

BASE_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/",
}


def fetch_ann_page(page=1, begin_date="2025-01-01", end_date=None, page_size=50):
    """拉取一页全A股公告"""
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    params = {
        "sr": -1,
        "page_size": page_size,
        "page_index": page,
        "ann_type": "A",
        "begin_time": begin_date,
        "end_time": end_date,
    }
    url = f"{BASE_URL}?{urlencode(params)}"
    req = Request(url, headers=HEADERS)
    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        return data.get("data", {}).get("list", [])
    except Exception as e:
        print(f"  ⚠️ API error page {page}: {e}")
        return []


def scan_restatements(days_back=30, max_pages=10):
    """扫描公告，返回命中列表"""
    begin_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    print(f"🔍 扫描 {begin_date} ~ 今天的全A股公告...")

    hits = []
    seen = set()

    for page in range(1, max_pages + 1):
        items = fetch_ann_page(page=page, begin_date=begin_date)
        if not items:
            break

        for item in items:
            title = item.get("title_ch", "")
            codes_list = item.get("codes", [])

            # 关键词匹配
            matched = None
            for kw in KEYWORDS:
                if kw in title:
                    matched = kw
                    break

            if not matched:
                continue

            # 提取股票代码和名称
            for c in codes_list:
                code = c.get("stock_code", "").strip()
                name = c.get("short_name", "").strip()
                if not code:
                    continue

                key = (code, item.get("notice_date", ""))
                if key in seen:
                    continue
                seen.add(key)

                hits.append({
                    "code": code.zfill(6),
                    "name": name,
                    "reason": f"公告提及「{matched}」: {title}",
                    "notice_date": item.get("notice_date", ""),
                })
                print(f"  ⚠️ {code} {name} → {matched} | {item.get('notice_date', '')[:10]}")

        time.sleep(0.5)  # 礼貌限速

    print(f"\n📊 共发现 {len(hits)} 条重述公告")
    return hits


def save_to_db(hits, db_path):
    """写入 restatement_blacklist 表（去重）"""
    if not hits:
        print("  ✅ 无新增，跳过")
        return 0

    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS restatement_blacklist (
            code TEXT PRIMARY KEY,
            name TEXT,
            reason TEXT,
            notice_date TEXT,
            detected_at TEXT DEFAULT (datetime('now'))
        )
    """)

    new_count = 0
    for h in hits:
        existing = conn.execute(
            "SELECT 1 FROM restatement_blacklist WHERE code=? AND notice_date=?",
            (h["code"], h["notice_date"])
        ).fetchone()
        if existing:
            continue

        conn.execute(
            "INSERT OR REPLACE INTO restatement_blacklist(code, name, reason, notice_date) VALUES(?,?,?,?)",
            (h["code"], h["name"], h["reason"], h["notice_date"])
        )
        new_count += 1

    conn.commit()

    # 统计总黑名单
    total = conn.execute("SELECT COUNT(*) FROM restatement_blacklist").fetchone()[0]
    conn.close()

    print(f"  ✅ 新增 {new_count} 条，当前黑名单总计 {total} 只")
    return new_count


if __name__ == "__main__":
    hits = scan_restatements(days_back=90, max_pages=15)
    save_to_db(hits, DB_PATH)
