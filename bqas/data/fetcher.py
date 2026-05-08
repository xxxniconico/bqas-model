"""BQAS 数据获取层 (V3 — curl新浪行情 + akshare财报缓存)

数据源策略（WSL环境）：
- 行情（价格）: 新浪 hq.sinajs.cn → curl subprocess
- 财报缓存: akshare East Money Data Center → SQLite（首次 build_cache 一次性拉取）
- 股票列表: akshare stock_info_a_code_name()

⚠️ 绝不使用 push2.eastmoney.com / stock_zh_a_hist / stock_zh_a_spot_em — WSL下全断
"""

import sqlite3
import subprocess
import time
import logging
from pathlib import Path
from datetime import date, datetime
from typing import Optional

import pandas as pd
import akshare as ak
from tqdm import tqdm

from .schema import (
    StockInfo, IncomeStatement, BalanceSheet,
    CashFlowStatement, DailyQuote, FinancialData,
)

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)
DB_PATH = CACHE_DIR / "bqas.db"

RATE_LIMIT_SLEEP = 0.3


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stock_info (
            code TEXT PRIMARY KEY,
            name TEXT,
            industry_sw TEXT,
            listing_date TEXT,
            total_shares REAL,
            is_st INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS income (
            code TEXT, report_period TEXT,
            revenue REAL, operating_profit REAL, net_income REAL,
            interest_expense REAL,
            PRIMARY KEY (code, report_period)
        );
        CREATE TABLE IF NOT EXISTS balance (
            code TEXT, report_period TEXT,
            total_assets REAL, total_liabilities REAL, equity REAL,
            goodwill REAL, inventory REAL, accounts_receivable REAL,
            cash_equiv REAL, long_term_invest REAL,
            short_term_debt REAL, long_term_debt REAL,
            PRIMARY KEY (code, report_period)
        );
        CREATE TABLE IF NOT EXISTS cashflow (
            code TEXT, report_period TEXT,
            operating_cf REAL, capex REAL, financing_cf REAL,
            PRIMARY KEY (code, report_period)
        );
        CREATE TABLE IF NOT EXISTS quotes (
            code TEXT, trade_date TEXT,
            close REAL, market_cap REAL, pb REAL, pe REAL, turnover_amount REAL,
            PRIMARY KEY (code, trade_date)
        );
        CREATE TABLE IF NOT EXISTS meta (
            code TEXT PRIMARY KEY,
            audit_opinion TEXT,
            pledge_ratio REAL,
            dividend_yield REAL,
            last_updated TEXT
        );
    """)
    conn.commit()
    conn.close()


_init_db()


# ═══════════════════════════════════════════════════════════
#  curl 新浪行情
# ═══════════════════════════════════════════════════════════

def _sina_prefix(code: str) -> str:
    return 'sh' if code.startswith(('60', '68')) else 'sz'


def _fetch_sina_quote_curl(code: str) -> Optional[dict]:
    prefix = _sina_prefix(code)
    ticker = f"{prefix}{code}"
    cmd = [
        "curl", "-s", "--max-time", "8",
        "-H", "Referer: https://finance.sina.com.cn",
        f"https://hq.sinajs.cn/list={ticker}"
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=10)
        text = result.stdout.decode("gbk", errors="replace")
    except Exception as e:
        logger.warning(f"Sina curl failed for {code}: {e}")
        return None

    if '="' not in text:
        return None
    try:
        fields = text.split('"')[1].split(",")
    except (IndexError, ValueError):
        return None
    if len(fields) < 32:
        return None

    return {
        "name": fields[0],
        "price": float(fields[3]) if fields[3] else 0,
        "trade_date": fields[30],
        "volume": float(fields[8]) if fields[8] else 0,
        "amount": float(fields[9]) if fields[9] else 0,
    }


# ═══════════════════════════════════════════════════════════
#  全市场缓存构建
# ═══════════════════════════════════════════════════════════

def _clear_cache():
    conn = _get_conn()
    for table in ["income", "balance", "cashflow", "stock_info"]:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()


def _bulk_insert_income(period: str, df: pd.DataFrame):
    if df is None or df.empty:
        return
    conn = _get_conn()
    code_col = '股票代码' if '股票代码' in df.columns else '代码'
    for _, r in df.iterrows():
        try:
            code = str(r[code_col]).zfill(6)
            conn.execute(
                "INSERT OR REPLACE INTO income VALUES(?,?,?,?,?,?)",
                (code, period,
                 float(r.get("营业总收入", 0) or 0),
                 float(r.get("营业利润", 0) or 0),
                 float(r.get("净利润", 0) or 0),
                 abs(float(r.get("营业总支出-财务费用", 0) or 0)))
            )
        except Exception:
            continue
    conn.commit()
    conn.close()


def _bulk_insert_balance(period: str, df: pd.DataFrame):
    if df is None or df.empty:
        return
    conn = _get_conn()
    code_col = '股票代码' if '股票代码' in df.columns else '代码'
    for _, r in df.iterrows():
        try:
            code = str(r[code_col]).zfill(6)
            conn.execute(
                "INSERT OR REPLACE INTO balance VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (code, period,
                 float(r.get("资产-总资产", 0) or 0),
                 float(r.get("负债-总负债", 0) or 0),
                 float(r.get("股东权益合计", 0) or 0),
                 float(r.get("商誉", 0) or 0),
                 float(r.get("资产-存货", 0) or 0),
                 float(r.get("资产-应收账款", 0) or 0),
                 float(r.get("资产-货币资金", 0) or 0),
                 float(r.get("长期股权投资", 0) or 0),
                 float(r.get("短期借款", 0) or 0),
                 float(r.get("长期借款", 0) or 0))
            )
        except Exception:
            continue
    conn.commit()
    conn.close()


def _bulk_insert_cashflow(period: str, df: pd.DataFrame):
    if df is None or df.empty:
        return
    conn = _get_conn()
    code_col = '股票代码' if '股票代码' in df.columns else '代码'
    for _, r in df.iterrows():
        try:
            code = str(r[code_col]).zfill(6)
            investing = abs(float(r.get("投资性现金流-现金流量净额", 0) or 0))
            conn.execute(
                "INSERT OR REPLACE INTO cashflow VALUES(?,?,?,?,?)",
                (code, period,
                 float(r.get("经营性现金流-现金流量净额", 0) or 0),
                 investing,
                 float(r.get("融资性现金流-现金流量净额", 0) or 0))
            )
        except Exception:
            continue
    conn.commit()
    conn.close()


def build_full_cache(years: int = 5, force: bool = False):
    """构建全市场财报缓存（一次性拉取）"""
    if force:
        _clear_cache()

    current_year = datetime.now().year
    current_month = datetime.now().month
    periods = []
    for y in range(current_year - years, current_year + 1):
        if y == current_year and current_month < 4:
            continue
        periods.append(f"{y}1231")

    print(f"📦 将缓存 {len(periods)} 个报告期的全 A 股财报 ({periods[0]} ~ {periods[-1]})")
    print(f"   每个报告期拉取 3 张表（利润/负债/现金流），预计 3-5 分钟\n")

    for period in periods:
        print(f"  📊 {period}...", end=" ", flush=True)
        try:
            time.sleep(RATE_LIMIT_SLEEP)
            df_income = ak.stock_lrb_em(date=period)
            if df_income is not None and not df_income.empty:
                _bulk_insert_income(period, df_income)
        except Exception as e:
            print(f"利润表失败: {e}")
            df_income = None

        try:
            time.sleep(RATE_LIMIT_SLEEP)
            df_balance = ak.stock_zcfz_em(date=period)
            if df_balance is not None and not df_balance.empty:
                _bulk_insert_balance(period, df_balance)
        except Exception as e:
            print(f"负债表失败: {e}")
            df_balance = None

        try:
            time.sleep(RATE_LIMIT_SLEEP)
            df_cf = ak.stock_xjll_em(date=period)
            if df_cf is not None and not df_cf.empty:
                _bulk_insert_cashflow(period, df_cf)
        except Exception as e:
            print(f"现金流表失败: {e}")

        inc_count = df_income.shape[0] if df_income is not None and not df_income.empty else 0
        print(f"✓ ({inc_count} 条)")

    # 同时缓存股票列表
    print("  📋 股票列表...", end=" ", flush=True)
    try:
        time.sleep(RATE_LIMIT_SLEEP)
        df_stocks = ak.stock_info_a_code_name()
        df_stocks.columns = ["code", "name"]
        df_stocks["code"] = df_stocks["code"].astype(str).str.zfill(6)
        conn = _get_conn()
        for _, r in df_stocks.iterrows():
            conn.execute(
                "INSERT OR REPLACE INTO stock_info(code, name) VALUES(?,?)",
                (r["code"], r["name"])
            )
        conn.commit()
        conn.close()
        print(f"✓ ({len(df_stocks)} 只)")
    except Exception as e:
        print(f"失败: {e}")

    print(f"\n✅ 缓存构建完成！现在可以运行 bqas score <代码>")


# ═══════════════════════════════════════════════════════════
#  单股信息补全（akshare 按需拉取）
# ═══════════════════════════════════════════════════════════

def _enrich_stock_info(code: str):
    """按需补全单只股票的 total_shares / industry_sw / listing_date"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT total_shares, industry_sw, listing_date FROM stock_info WHERE code=?",
        (code,)
    ).fetchone()
    conn.close()

    if row and row["total_shares"] and row["industry_sw"]:
        return  # 已经补全过了

    # 尝试从 akshare 获取
    prefix = "SH" if code.startswith(("60", "68")) else "SZ"
    symbol = f"{prefix}{code}"

    total_shares = 0
    industry_sw = ""
    listing_date_str = ""

    try:
        time.sleep(RATE_LIMIT_SLEEP)
        basic = ak.stock_individual_basic_info_xq(symbol=symbol)
        if basic is not None and not basic.empty:
            # Xueqiu API: DataFrame with columns ['item', 'value']
            # Find relevant rows
            item_map = dict(zip(basic['item'], basic['value']))

            # Total shares: 'reg_asset' field (注册资本 = 总股本 for this API)
            total_shares = float(item_map.get('reg_asset', 0) or 0)

            # Industry: 'affiliate_industry' is a dict with 'ind_name'
            ind = item_map.get('affiliate_industry', {})
            if isinstance(ind, dict):
                industry_sw = ind.get('ind_name', '')
            elif isinstance(ind, str):
                industry_sw = ind

            # Listing date: Unix timestamp in ms
            ld = item_map.get('listed_date', 0)
            if ld:
                from datetime import datetime as dt
                listing_date_str = dt.fromtimestamp(ld / 1000).strftime('%Y-%m-%d')
    except Exception as e:
        logger.warning(f"_enrich_stock_info({code}) failed: {e}")

    # 写回缓存（先确保记录存在，再更新）
    if total_shares or industry_sw or listing_date_str:
        conn = _get_conn()
        conn.execute("INSERT OR IGNORE INTO stock_info(code, name) VALUES(?,?)", (code, code))
        conn.execute(
            "UPDATE stock_info SET total_shares=?, industry_sw=?, listing_date=? WHERE code=?",
            (total_shares, industry_sw, listing_date_str, code)
        )
        conn.commit()
        conn.close()


# ═══════════════════════════════════════════════════════════
#  单股数据加载（从缓存）
# ═══════════════════════════════════════════════════════════

def fetch_stock_info(code: str) -> Optional[StockInfo]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM stock_info WHERE code=?", (code,)).fetchone()
    conn.close()
    if row:
        return StockInfo(
            code=row["code"], name=row["name"],
            industry_sw=row["industry_sw"] or "",
            listing_date=date.fromisoformat(row["listing_date"]) if row["listing_date"] else None,
            total_shares=row["total_shares"] or 0,
            is_st=bool(row["is_st"]),
        )
    return None


def fetch_financials(code: str, years: int = 5) -> FinancialData:
    """从缓存加载单只股票财务数据 + curl 获取实时行情"""
    info = fetch_stock_info(code)
    if info is None:
        info = StockInfo(code=code, name=code)

    # 按需补全总股本和行业（首次查询时自动拉取并缓存）
    _enrich_stock_info(code)
    info = fetch_stock_info(code) or info

    # 获取 Sina 实时行情（名字 + 价格）
    sina = _fetch_sina_quote_curl(code)
    price = sina["price"] if sina else 0
    name = sina["name"] if sina else info.name
    if name and name != code:
        info = StockInfo(
            code=code, name=name,
            industry_sw=info.industry_sw,
            listing_date=info.listing_date,
            total_shares=info.total_shares,
            is_st=info.is_st,
        )

    current_year = datetime.now().year
    current_month = datetime.now().month
    periods = []
    for y in range(current_year - years, current_year + 1):
        if y == current_year and current_month < 4:
            continue
        periods.append(f"{y}1231")

    # 从缓存加载
    conn = _get_conn()
    income_list, balance_list, cashflow_list = [], [], []

    for period in periods:
        row = conn.execute("SELECT * FROM income WHERE code=? AND report_period=?", (code, period)).fetchone()
        if row:
            income_list.append(IncomeStatement(
                code=code, report_year=int(period[:4]),
                revenue=float(row["revenue"] or 0),
                operating_profit=float(row["operating_profit"] or 0),
                net_income=float(row["net_income"] or 0),
                interest_expense=float(row["interest_expense"] or 0),
                ebit=float(row["operating_profit"] or 0) + float(row["interest_expense"] or 0),
            ))
        else:
            income_list.append(IncomeStatement(code=code, report_year=int(period[:4])))

        row = conn.execute("SELECT * FROM balance WHERE code=? AND report_period=?", (code, period)).fetchone()
        if row:
            balance_list.append(BalanceSheet(
                code=code, report_year=int(period[:4]),
                total_assets=float(row["total_assets"] or 0),
                total_liabilities=float(row["total_liabilities"] or 0),
                equity=float(row["equity"] or 0),
                goodwill=float(row["goodwill"] or 0),
                inventory=float(row["inventory"] or 0),
                accounts_receivable=float(row["accounts_receivable"] or 0),
                cash=float(row["cash_equiv"] or 0),
                long_term_invest=float(row["long_term_invest"] or 0),
                short_term_debt=float(row["short_term_debt"] or 0),
                long_term_debt=float(row["long_term_debt"] or 0),
            ))
        else:
            balance_list.append(BalanceSheet(code=code, report_year=int(period[:4])))

        row = conn.execute("SELECT * FROM cashflow WHERE code=? AND report_period=?", (code, period)).fetchone()
        if row:
            cashflow_list.append(CashFlowStatement(
                code=code, report_year=int(period[:4]),
                operating_cf=float(row["operating_cf"] or 0),
                capex=float(row["capex"] or 0),
                financing_cf=float(row["financing_cf"] or 0),
            ))
        else:
            cashflow_list.append(CashFlowStatement(code=code, report_year=int(period[:4])))

    conn.close()

    return FinancialData(info=info, income=income_list, balance=balance_list, cashflow=cashflow_list)


def fetch_quotes(code: str, start: str = "2020-01-01") -> list[DailyQuote]:
    """获取行情（新浪 curl，从缓存/资产负债表推算市值和PB）"""
    sina = _fetch_sina_quote_curl(code)
    if sina is None:
        return []

    price = sina["price"]
    trade_date = sina["trade_date"]

    # 获取总股本和净资产
    conn = _get_conn()
    info_row = conn.execute("SELECT total_shares FROM stock_info WHERE code=?", (code,)).fetchone()
    total_shares = float(info_row["total_shares"]) if info_row and info_row["total_shares"] else 0

    eq_row = conn.execute(
        "SELECT equity FROM balance WHERE code=? ORDER BY report_period DESC LIMIT 1", (code,)
    ).fetchone()
    equity = float(eq_row["equity"]) if eq_row and eq_row["equity"] else 0
    conn.close()

    market_cap = price * total_shares if total_shares > 0 else 0
    pb = price / (equity / total_shares) if equity > 0 and total_shares > 0 else 0

    quote = DailyQuote(
        code=code,
        trade_date=date.fromisoformat(trade_date) if trade_date else date.today(),
        close=price,
        market_cap=market_cap,
        pb=pb,
        turnover_amount=sina["amount"],
    )
    return [quote]
