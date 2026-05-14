"""BQAS Global Scorer — US & HK stock scoring using yfinance data.

Reuses factor functions from factors.py — only data source changes.
"""
import sqlite3, logging
from pathlib import Path
from datetime import date, datetime
from ..data.schema import (
    StockInfo, IncomeStatement, BalanceSheet, CashFlowStatement,
    DailyQuote, FinancialData
)
from .industry import get_industry_group
from .blacklist import check_blacklist
from .factors import compute_all_factors

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "cache" / "bqas_global.db"

RATING_TABLE = [
    (85, "⭐⭐⭐⭐⭐", "巴菲特级别", "重仓候选"),
    (75, "⭐⭐⭐⭐",   "优秀企业",   "可配置"),
    (65, "⭐⭐⭐",    "良好企业",   "观察列表"),
    (50, "⭐⭐",     "一般",       "等更好价格"),
    (0,  "⭐",      "不合格",     "回避"),
]


def get_rating(total: float) -> dict:
    for threshold, stars, label, advice in RATING_TABLE:
        if total >= threshold:
            return {"stars": stars, "label": label, "advice": advice}
    return RATING_TABLE[-1][1:]


def score_stock_global(code: str, market: str = "us") -> dict:
    """Score a US or HK stock using yfinance data and BQAS factors.

    Args:
        code: ticker symbol (e.g., 'AAPL' or '0700.HK')
        market: 'us' or 'hk'

    Returns: same dict format as score_stock()
    """
    m = market
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # ── Stock Info ──
    info_row = conn.execute(
        f"SELECT * FROM stock_info_{m} WHERE code=?", (code,)
    ).fetchone()
    if not info_row:
        conn.close()
        return {"code": code, "error": "Stock not found in global DB", "total": 0}

    info = StockInfo(
        code=code,
        name=info_row["name"] or code,
        industry_sw=info_row["industry"] or "",
        listing_date=None,
        total_shares=float(info_row["total_shares"] or 0),
        is_st=False,
    )

    # ── Income (last 5 years) ──
    income_rows = conn.execute(
        f"SELECT * FROM income_{m} WHERE code=? ORDER BY report_period DESC LIMIT 5",
        (code,)
    ).fetchall()
    income = []
    for r in reversed(income_rows):
        income.append(IncomeStatement(
            code=code, report_year=int(r["report_period"][:4]),
            revenue=float(r["revenue"] or 0),
            operating_profit=float(r["operating_profit"] or 0),
            net_income=float(r["net_income"] or 0),
            interest_expense=float(r["interest_expense"] or 0),
            ebit=float(r["ebit"] or 0) or float(r["operating_profit"] or 0),
        ))

    # ── Balance (last 5 years) ──
    balance_rows = conn.execute(
        f"SELECT * FROM balance_{m} WHERE code=? ORDER BY report_period DESC LIMIT 5",
        (code,)
    ).fetchall()
    balance = []
    for r in reversed(balance_rows):
        balance.append(BalanceSheet(
            code=code, report_year=int(r["report_period"][:4]),
            total_assets=float(r["total_assets"] or 0),
            total_liabilities=float(r["total_liabilities"] or 0),
            equity=float(r["equity"] or 0),
            goodwill=float(r["goodwill"] or 0),
            inventory=float(r["inventory"] or 0),
            accounts_receivable=float(r["accounts_receivable"] or 0),
            cash=float(r["cash"] or 0),
            long_term_invest=float(r["long_term_invest"] or 0),
            short_term_debt=float(r["short_term_debt"] or 0),
            long_term_debt=float(r["long_term_debt"] or 0),
        ))

    # ── Cashflow (last 5 years) ──
    cf_rows = conn.execute(
        f"SELECT * FROM cashflow_{m} WHERE code=? ORDER BY report_period DESC LIMIT 5",
        (code,)
    ).fetchall()
    cashflow = []
    for r in reversed(cf_rows):
        cashflow.append(CashFlowStatement(
            code=code, report_year=int(r["report_period"][:4]),
            operating_cf=float(r["operating_cf"] or 0),
            capex=float(r["capex"] or 0),
            financing_cf=float(r["financing_cf"] or 0),
        ))

    # ── Latest Quote (from quotes table or stock_info fallback) ──
    quote_row = conn.execute(
        f"SELECT * FROM quotes_{m} WHERE code=? ORDER BY trade_date DESC LIMIT 1",
        (code,)
    ).fetchone()
    
    # Get market data: prefer quotes table, fallback to stock_info
    if quote_row:
        mc = float(quote_row["market_cap"] or 0) or (
            float(quote_row["close"] or 0) * float(info_row["total_shares"] or 0)
        )
        close = float(quote_row["close"] or 0)
        pb = float(quote_row["pb"] or 0)
    else:
        # Fallback: compute from stock_info + balance sheet (no live quotes)
        mc = float(info_row["market_cap"] or 0)
        ts = float(info_row["total_shares"] or 0)
        close = mc / ts if ts > 0 else 0
        # Compute PB from balance sheet equity if available
        if balance:
            latest_equity = balance[-1].equity  # most recent year
            pb = mc / latest_equity if latest_equity > 0 else 0
        else:
            pb = 0
    
    quotes = [DailyQuote(
        code=code,
        trade_date=date.today(),
        close=close,
        market_cap=mc,
        pb=pb,
        turnover_amount=float(quote_row["volume"] or 0) if quote_row else 1e12,  # no quote data → assume liquid
    )]

    conn.close()

    if not income or not balance:
        return {"code": code, "name": info.name, "error": "Insufficient financial data", "total": 0}

    # ── Build FinancialData ──
    data = FinancialData(
        info=info, income=income, balance=balance,
        cashflow=cashflow, quotes=quotes,
        audit_opinion="standard",  # US/HK: assume standard unless flagged
        dividend_yield=0, pledge_ratio=0,
    )

    # ── One-vote veto (market-specific: skips ST/pledge/fraud/restatement for global) ──
    passed, reason, checks = check_blacklist(data, market=m)
    if not passed:
        return {
            "code": code, "name": info.name, "passed_blacklist": False,
            "blacklist_reason": reason, "total": 0,
            "rating": "⛔", "rating_label": "一票否决", "rating_advice": reason,
        }

    # ── Factor computation ──
    industry_group = get_industry_group(info.industry_sw) or "其他"
    factors = compute_all_factors(data, industry_group)
    total = factors["weighted"]["total"]
    rating = get_rating(total)

    return {
        "code": code, "name": info.name,
        "industry_sw": info.industry_sw, "industry_group": industry_group,
        "passed_blacklist": True,
        "scores": factors, "total": total,
        "rating": rating["stars"], "rating_label": rating["label"],
        "rating_advice": rating["advice"],
    }
