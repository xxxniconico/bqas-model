"""
BQAS Dynamic Layer 2 — Factor Cache (CLI-aligned)

Uses CLI factor functions directly on SQLite data — NO API calls.
Scores match score_stock() for Q/H/G dimensions.
V dimension excluded (always real-time).

~5000 stocks in ~30-60 seconds (daily cron).
"""
import json, sqlite3, time, logging
from pathlib import Path
from datetime import datetime, date

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
CACHE_FILE = CACHE_DIR / "factor_cache.json"
DB_PATH = CACHE_DIR / "bqas.db"


def _load_all_financials() -> dict[str, dict]:
    """Load all financial data from SQLite, grouped by code.
    
    Returns dict: {code: {income: [...], balance: [...], cashflow: [...], info: {...}, meta: {...}}}
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Income (last 5 years, annual only — matches CLI's years=5)
    income_rows = conn.execute(
        "SELECT code, report_period, revenue, operating_profit, net_income, interest_expense, op_cost FROM income WHERE report_period LIKE '%1231' AND CAST(SUBSTR(report_period,1,4) AS INTEGER) >= CAST(strftime('%Y','now') AS INTEGER) - 5 ORDER BY report_period"
    ).fetchall()

    # Balance (last 5 years, annual only)
    balance_rows = conn.execute(
        "SELECT code, report_period, total_assets, total_liabilities, equity, short_term_debt, long_term_debt, goodwill, cash_equiv, inventory, accounts_receivable, long_term_invest FROM balance WHERE report_period LIKE '%1231' AND CAST(SUBSTR(report_period,1,4) AS INTEGER) >= CAST(strftime('%Y','now') AS INTEGER) - 5 ORDER BY report_period"
    ).fetchall()

    # Cashflow (last 5 years, annual only)
    cf_rows = conn.execute(
        "SELECT code, report_period, operating_cf, capex FROM cashflow WHERE report_period LIKE '%1231' AND CAST(SUBSTR(report_period,1,4) AS INTEGER) >= CAST(strftime('%Y','now') AS INTEGER) - 5 ORDER BY report_period"
    ).fetchall()

    # Stock info
    stock_rows = conn.execute(
        "SELECT code, name, industry_sw, listing_date, total_shares, is_st FROM stock_info"
    ).fetchall()

    # Meta
    meta_rows = conn.execute(
        "SELECT code, audit_opinion, pledge_ratio, dividend_yield FROM meta"
    ).fetchall()

    # Fraud
    fraud_codes = set(r[0] for r in conn.execute("SELECT DISTINCT code FROM financial_fraud").fetchall())

    # Restatement (with detail for blacklist)
    restatement_codes = {}
    for r in conn.execute("SELECT code, reason, notice_date FROM restatement_blacklist").fetchall():
        restatement_codes[r["code"]] = {"reason": r["reason"], "notice_date": r["notice_date"]}

    conn.close()

    # Group by code
    data = {}
    for r in stock_rows:
        code = r["code"]
        listing_d = None
        if r["listing_date"]:
            try:
                listing_d = date.fromisoformat(r["listing_date"])
            except Exception:
                pass
        data[code] = {
            "income": [],
            "balance": [],
            "cashflow": [],
            "info": {
                "code": code,
                "name": r["name"] or code,
                "industry_sw": r["industry_sw"] or "",
                "listing_date": listing_d,
                "total_shares": float(r["total_shares"] or 0),
                "is_st": bool(r["is_st"]),
            },
            "meta": {},
            "has_fraud": code in fraud_codes,
            "has_restatement": code in restatement_codes,
            "restatement_detail": restatement_codes.get(code, {}).get("reason", ""),
        }

    for r in income_rows:
        code = r["code"]
        if code in data:
            yr = int(r["report_period"][:4])
            data[code]["income"].append({
                "code": code,
                "report_year": yr,
                "revenue": float(r["revenue"] or 0),
                "operating_profit": float(r["operating_profit"] or 0),
                "net_income": float(r["net_income"] or 0),
                "interest_expense": float(r["interest_expense"] or 0),
                "ebit": float(r["operating_profit"] or 0) + float(r["interest_expense"] or 0),
                "op_cost": float(r["op_cost"] or 0),
            })

    for r in balance_rows:
        code = r["code"]
        if code in data:
            yr = int(r["report_period"][:4])
            data[code]["balance"].append({
                "code": code,
                "report_year": yr,
                "total_assets": float(r["total_assets"] or 0),
                "total_liabilities": float(r["total_liabilities"] or 0),
                "equity": float(r["equity"] or 0),
                "short_term_debt": float(r["short_term_debt"] or 0),
                "long_term_debt": float(r["long_term_debt"] or 0),
                "goodwill": float(r["goodwill"] or 0),
                "cash_equiv": float(r["cash_equiv"] or 0),
                "inventory": float(r["inventory"] or 0),
                "accounts_receivable": float(r["accounts_receivable"] or 0),
                "long_term_invest": float(r["long_term_invest"] or 0),
            })

    for r in cf_rows:
        code = r["code"]
        if code in data:
            yr = int(r["report_period"][:4])
            data[code]["cashflow"].append({
                "code": code,
                "report_year": yr,
                "operating_cf": float(r["operating_cf"] or 0),
                "capex": float(r["capex"] or 0),
            })

    for r in meta_rows:
        code = r["code"]
        if code in data:
            data[code]["meta"] = {
                "audit_opinion": r["audit_opinion"] or "",
                "pledge_ratio": float(r["pledge_ratio"] or 0),
                "dividend_yield": float(r["dividend_yield"] or 0),
            }

    # ══ TTM override: replace latest annual data with trailing 4 quarters ══
    ttm_conn = sqlite3.connect(str(DB_PATH))
    ttm_conn.row_factory = sqlite3.Row
    from collections import defaultdict
    q_data = defaultdict(lambda: {"income": {}, "balance": {}, "cf": {}})
    # Load quarterly (non-1231) for all periods
    for r in ttm_conn.execute(
        "SELECT code, report_period, revenue, operating_profit, net_income, interest_expense, op_cost FROM income WHERE report_period NOT LIKE '%1231'"
    ).fetchall():
        q_data[r["code"]]["income"][r["report_period"]] = r
    for r in ttm_conn.execute(
        "SELECT code, report_period, total_assets, total_liabilities, equity, cash_equiv, long_term_invest FROM balance WHERE report_period NOT LIKE '%1231'"
    ).fetchall():
        q_data[r["code"]]["balance"][r["report_period"]] = r
    for r in ttm_conn.execute(
        "SELECT code, report_period, operating_cf, capex FROM cashflow WHERE report_period NOT LIKE '%1231'"
    ).fetchall():
        q_data[r["code"]]["cf"][r["report_period"]] = r
    # ALSO load annual 1231 data — needed to compute standalone Q4
    for r in ttm_conn.execute(
        "SELECT code, report_period, revenue, operating_profit, net_income, interest_expense, op_cost FROM income WHERE report_period LIKE '%1231' AND CAST(SUBSTR(report_period,1,4) AS INTEGER) >= 2023"
    ).fetchall():
        q_data[r["code"]]["income"][r["report_period"]] = r
    for r in ttm_conn.execute(
        "SELECT code, report_period, total_assets, total_liabilities, equity, cash_equiv, long_term_invest FROM balance WHERE report_period LIKE '%1231' AND CAST(SUBSTR(report_period,1,4) AS INTEGER) >= 2023"
    ).fetchall():
        q_data[r["code"]]["balance"][r["report_period"]] = r
    for r in ttm_conn.execute(
        "SELECT code, report_period, operating_cf, capex FROM cashflow WHERE report_period LIKE '%1231' AND CAST(SUBSTR(report_period,1,4) AS INTEGER) >= 2023"
    ).fetchall():
        q_data[r["code"]]["cf"][r["report_period"]] = r
    ttm_conn.close()

    ttm_count = 0
    for code, qd in q_data.items():
        if code not in data:
            continue
        periods = sorted(qd["income"].keys(), reverse=True)
        if len(periods) < 5:
            continue
        # Take latest 5 periods (including annuals), compute standalone = period[i] - period[i+1]
        latest5 = periods[:5]
        latest_year = max(int(p[:4]) for p in latest5)
        standalone_pairs = list(zip(latest5[:4], latest5[1:5]))  # (current, previous) pairs
        
        ttm_inc = {"revenue": 0, "operating_profit": 0, "net_income": 0, "interest_expense": 0, "op_cost": 0}
        ttm_cf = {"operating_cf": 0, "capex": 0}
        for cur_p, prev_p in standalone_pairs:
            cur_r = qd["income"].get(cur_p, {})
            prev_r = qd["income"].get(prev_p, {})
            if cur_r:
                # Q1 is standalone (no subtraction), other quarters subtract previous cumulative
                is_q1 = cur_p.endswith("0331")
                sub = lambda f: float(cur_r[f] or 0) - (float(prev_r[f] or 0) if f in prev_r and not is_q1 else 0)
                ttm_inc["revenue"] += sub("revenue")
                ttm_inc["operating_profit"] += sub("operating_profit")
                ttm_inc["net_income"] += sub("net_income")
                ttm_inc["interest_expense"] += sub("interest_expense")
                ttm_inc["op_cost"] += sub("op_cost")
            cur_cf = qd["cf"].get(cur_p, {})
            prev_cf = qd["cf"].get(prev_p, {})
            if cur_cf:
                sub_cf = lambda f: float(cur_cf[f] or 0) - (float(prev_cf[f] or 0) if f in prev_cf and not is_q1 else 0)
                ttm_cf["operating_cf"] += sub_cf("operating_cf")
                ttm_cf["capex"] += sub_cf("capex")
        
        # Balance: use LATEST quarter (latest5[0])
        latest_bal = qd["balance"].get(latest5[0])
        
        # Override latest year in income
        stock = data[code]
        for inc in stock["income"]:
            if inc["report_year"] == latest_year:
                inc.update(ttm_inc)
                inc["ebit"] = ttm_inc["operating_profit"] + ttm_inc["interest_expense"]
                break
        # Override balance
        if latest_bal:
            for bal in stock["balance"]:
                if bal["report_year"] == latest_year:
                    for k in ["total_assets", "total_liabilities", "equity", "cash_equiv", "long_term_invest"]:
                        bal[k] = float(latest_bal[k] or 0)
                    break
        for cf in stock["cashflow"]:
            if cf["report_year"] == latest_year:
                cf.update(ttm_cf)
                break
        ttm_count += 1
    
    logger.info(f"Applied TTM override to {ttm_count} stocks")

    return data


def _compute_factors_for_code(code: str, raw: dict) -> dict | None:
    """Compute Q/H/G factors for a single stock using CLI factor functions."""
    from ..data.schema import (
        StockInfo, IncomeStatement, BalanceSheet, CashFlowStatement, FinancialData
    )
    from .factors import (
        factor_roe_stability, factor_roic, factor_cfo_quality,
        factor_gross_margin_moat, factor_leverage,
        factor_interest_coverage, factor_asset_quality,
        factor_shareholder_return, factor_audit_governance,
    )
    from .industry import get_industry_group, get_weights, get_special_rules
    from .blacklist import check_blacklist

    # Construct Pydantic objects
    info = StockInfo(**raw["info"])

    income_rows = [i for i in raw["income"] if i["revenue"] > 0]
    balance_rows = [b for b in raw["balance"] if b["total_assets"] > 0]
    cf_rows = [c for c in raw["cashflow"] if c["operating_cf"] != 0 or c["capex"] != 0]

    if not income_rows or not balance_rows:
        return None

    income = [IncomeStatement(**i) for i in income_rows]
    balance = [BalanceSheet(**b) for b in balance_rows]
    cashflow = [CashFlowStatement(**c) for c in cf_rows]

    meta = raw["meta"]
    data = FinancialData(
        info=info,
        income=income,
        balance=balance,
        cashflow=cashflow,
        quotes=[],
        has_financial_fraud_penalty=raw.get("has_fraud", False),
        audit_opinion=meta.get("audit_opinion", ""),
        dividend_yield=meta.get("dividend_yield", 0),
        pledge_ratio=meta.get("pledge_ratio", 0),
        restatement_risk=raw.get("has_restatement", False),
        restatement_detail=raw.get("restatement_detail", ""),
    )

    # Blacklist check
    passed, reason, checks = check_blacklist(data)
    if not passed:
        return None  # skip blacklisted

    # Industry
    industry_group = get_industry_group(info.industry_sw)
    weights = get_weights(industry_group)
    rules = get_special_rules(industry_group)
    is_financial = rules.get("skip_leverage", False)

    # Q dimension
    q_roe = factor_roe_stability(income, balance)
    q_roic = factor_roic(income, balance)
    q_cfo = factor_cfo_quality(income, cashflow)
    q_gm = factor_gross_margin_moat(income)

    q_raw = (
        q_roe.get("score", 0) * 0.12 +
        q_roic.get("score", 0) * 0.10 +
        q_cfo.get("score", 0) * 0.08 +
        q_gm.get("score", 0) * 0.05
    )

    # H dimension
    h_lev = factor_leverage(balance, is_financial)
    h_ic = factor_interest_coverage(income, is_financial)
    h_aq = factor_asset_quality(income, balance)

    h_raw = (
        h_lev.get("score", 0) * 0.08 +
        h_ic.get("score", 0) * 0.07 +
        h_aq.get("score", 0) * 0.05
    )

    # G dimension
    g_div = factor_shareholder_return(data)
    g_audit = factor_audit_governance(data)
    g_raw = g_div.get("score", 0) * 0.08 + g_audit.get("score", 0) * 0.07

    # 共享权重归一化 — 和 CLI 同一个函数，永不不一致
    from .factors import _weight_dimensions
    # Financial stocks have skipped leverage+IC → adjust h_max
    h_max_override = 0.08*5 + 0.07*5 + 0.05*10 if is_financial else None
    dims = _weight_dimensions(q_raw, 0, h_raw, g_raw, weights, h_max=h_max_override)
    q_weighted = dims["quality"]
    h_weighted = dims["health"]
    g_weighted = dims["gov"]

    return {
        "name": info.name,
        "industry": info.industry_sw,
        "quality": {
            "roe": round(q_roe.get("score", 0), 1),
            "roic": round(q_roic.get("score", 0), 1),
            "cfo": round(q_cfo.get("score", 0), 1),
            "gm": round(q_gm.get("score", 0), 1),
        },
        "health": {
            "leverage": round(h_lev.get("score", 0), 1),
            "ic": round(h_ic.get("score", 0), 1),
            "asset_q": round(h_aq.get("score", 0), 1),
        },
        "gov": {
            "dividend": round(g_div.get("score", 0), 1),
            "audit": round(g_audit.get("score", 0), 1),
        },
        "q_weighted": q_weighted,
        "h_weighted": h_weighted,
        "g_weighted": g_weighted,
    }


def build_factor_cache(force: bool = False) -> dict:
    """Build factor cache using CLI factor functions on SQLite data."""
    t0 = time.time()

    # Load existing cache for incremental
    existing = {}
    if not force and CACHE_FILE.exists():
        try:
            existing = json.loads(CACHE_FILE.read_text()).get("factors", {})
        except Exception:
            pass

    # Load all financial data
    logger.info("Loading financial data from SQLite...")
    all_data = _load_all_financials()
    logger.info(f"Loaded {len(all_data)} stocks from DB")

    factors_out = {}
    updated = 0
    skipped = 0

    for i, (code, raw) in enumerate(all_data.items()):
        # Skip if already cached and not forced
        if not force and code in existing:
            factors_out[code] = existing[code]
            skipped += 1
            continue

        try:
            result = _compute_factors_for_code(code, raw)
            if result:
                factors_out[code] = result
                updated += 1
        except Exception as e:
            logger.debug(f"Factor compute failed for {code}: {e}")
            continue

        if (i + 1) % 500 == 0:
            elapsed = time.time() - t0
            logger.info(f"  Progress: {i+1}/{len(all_data)} ({updated} updated, {skipped} skipped) in {elapsed:.0f}s")

    # Build blacklist cache — check ALL stocks, not just those missing from factors
    blacklist = {}
    for code, raw in all_data.items():
        from .blacklist import check_blacklist
        from ..data.schema import StockInfo, IncomeStatement, BalanceSheet, CashFlowStatement, FinancialData
        try:
            info = StockInfo(**raw["info"])
            income = [IncomeStatement(**i) for i in raw["income"] if i["revenue"] > 0]
            balance = [BalanceSheet(**b) for b in raw["balance"] if b["total_assets"] > 0]
            cf = [CashFlowStatement(**c) for c in raw["cashflow"]]
            rst_detail = raw.get("restatement_detail", "")

            data = FinancialData(
                info=info, income=income, balance=balance, cashflow=cf, quotes=[],
                has_financial_fraud_penalty=raw.get("has_fraud", False),
                audit_opinion=raw["meta"].get("audit_opinion", ""),
                dividend_yield=raw["meta"].get("dividend_yield", 0),
                pledge_ratio=raw["meta"].get("pledge_ratio", 0),
                restatement_risk=raw.get("has_restatement", False),
                restatement_detail=rst_detail,
            )
            passed, reason, _ = check_blacklist(data)
            if not passed:
                blacklist[code] = reason
                # Remove from factors if it was added
                if code in factors_out:
                    del factors_out[code]
        except Exception:
            pass

    cache = {
        "built_at": datetime.now().isoformat(),
        "stocks_count": len(factors_out),
        "blacklisted": len(blacklist),
        "factors": factors_out,
        "blacklist": blacklist,
    }

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False))

    elapsed = time.time() - t0
    logger.info(f"Cache built: {len(factors_out)} stocks, {len(blacklist)} blacklisted in {elapsed:.1f}s ({updated} updated, {skipped} reused)")
    return cache


def load_factor_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return {}


def cache_age_hours() -> float | None:
    cache = load_factor_cache()
    built = cache.get("built_at")
    if not built:
        return None
    try:
        return (datetime.now() - datetime.fromisoformat(built)).total_seconds() / 3600
    except Exception:
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    build_factor_cache(force=True)
