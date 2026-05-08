"""BQAS 因子计算引擎

4 大维度 × 10+ 子因子，基于设计文档 V2 最终版。
每个因子返回 0-10 分的标准化得分。
"""

import math
from statistics import mean, median, stdev
from ..data.schema import FinancialData, IncomeStatement, BalanceSheet, CashFlowStatement, DailyQuote
from .beneish import compute_beneish_m_score

# ── Industry PB median cache (computed from quotes table) ──
_industry_pb_cache: dict = {}

def _get_industry_pb_median(industry_group: str) -> float:
    """Get industry median PB from quotes table. Lazy-loads and caches."""
    global _industry_pb_cache
    if not _industry_pb_cache:
        import sqlite3
        from pathlib import Path
        db_path = Path(__file__).parent.parent / "data" / "cache" / "bqas.db"
        try:
            conn = sqlite3.connect(str(db_path))
            # Get latest PB for each stock in each industry
            rows = conn.execute("""
                SELECT si.industry_sw, q.pb
                FROM quotes q
                JOIN stock_info si ON q.code = si.code
                WHERE q.trade_date = (SELECT MAX(trade_date) FROM quotes)
                  AND q.pb > 0 AND q.pb < 100
                  AND si.industry_sw IS NOT NULL AND si.industry_sw != ''
            """).fetchall()
            conn.close()
            
            # Group by industry_group (mapped from industry_sw)
            from .industry import get_industry_group as _get_group
            by_group = {}
            for ind_sw, pb in rows:
                grp = _get_group(ind_sw)
                if grp not in by_group:
                    by_group[grp] = []
                by_group[grp].append(pb)
            
            from statistics import median as _median
            for grp, pbs in by_group.items():
                _industry_pb_cache[grp] = _median(pbs)
        except Exception:
            pass
    
    return _industry_pb_cache.get(industry_group, None)


# ═══════════════════════════════════════════════════════════
#  维度 I：企业质量 (Quality) — 35%
# ═══════════════════════════════════════════════════════════

def factor_roe_stability(income: list[IncomeStatement], balance: list[BalanceSheet], years: int = 5) -> dict:
    """Q1: ROE 稳定性（12%）

    ROE_5y = mean(net_income / equity)
    得分 = min(ROE_5y, 30%) / 30% × 10
    """
    if not income or not balance:
        return {"score": 0, "roe_5y": 0, "missing_data": True}

    bal_eq = {b.report_year: b.equity for b in balance}
    pairs = []
    for inc in income[-years:]:
        eq = bal_eq.get(inc.report_year, 0)
        if eq > 0 and inc.report_year in bal_eq:
            roe = inc.net_income / eq
            pairs.append(roe)

    if not pairs:
        return {"score": 0, "roe_5y": 0, "missing_data": True}

    roe_5y = mean(pairs)
    score = min(roe_5y, 0.30) / 0.30 * 10

    # DuPont decomposition (latest year with real data)
    dupont = {}
    if income and balance:
        real_income = [i for i in sorted(income, key=lambda x: x.report_year) if i.revenue > 0]
        real_balance = [b for b in sorted(balance, key=lambda x: x.report_year) if b.total_assets > 0]
        if real_income and real_balance:
            latest_inc = real_income[-1]
            latest_bal = real_balance[-1]
            if latest_inc.revenue > 0 and latest_bal.total_assets > 0 and latest_bal.equity > 0:
                net_margin = latest_inc.net_income / latest_inc.revenue
                asset_turnover = latest_inc.revenue / latest_bal.total_assets
                equity_mult = latest_bal.total_assets / latest_bal.equity
                dupont = {
                    "net_margin": round(net_margin, 4),
                    "asset_turnover": round(asset_turnover, 4),
                    "equity_multiplier": round(equity_mult, 2),
                    "roe_reconstructed": round(net_margin * asset_turnover * equity_mult, 4),
                }
    return {"score": round(score, 1), "roe_5y": round(roe_5y, 4),
            "years_available": len(pairs), "dupont": dupont}


def factor_roic(income: list[IncomeStatement], balance: list[BalanceSheet], years: int = 5) -> dict:
    """Q2: ROIC 超额收益（10%）

    ROIC_5y = mean(EBIT / (equity + debt))
    得分 = min(ROIC_5y, 30%) / 30% × 10
    """
    if not income or not balance:
        return {"score": 0, "roic_5y": 0, "missing_data": True}

    pairs = []
    for inc in income[-years:]:
        bal = next((b for b in balance if b.report_year == inc.report_year), None)
        if bal:
            invested = bal.equity + bal.short_term_debt + bal.long_term_debt
            if invested > 0:
                pairs.append(inc.ebit / invested)

    if not pairs:
        return {"score": 0, "roic_5y": 0, "missing_data": True}

    roic_5y = mean(pairs)
    score = min(roic_5y, 0.30) / 0.30 * 10
    return {"score": round(score, 1), "roic_5y": round(roic_5y, 4), "years_available": len(pairs)}


def factor_cfo_quality(income: list[IncomeStatement], cashflow: list[CashFlowStatement], years: int = 3) -> dict:
    """Q3: CFO 真实性（8%）

    CFO_quality = mean(OCF) / mean(NI) — 近 3 年均值
    得分 = min(CFO_quality, 2.0) / 2.0 × 10
    """
    pairs = []
    for cf in cashflow[-years:]:
        inc = next((i for i in income if i.report_year == cf.report_year), None)
        if inc and inc.net_income != 0:
            pairs.append(cf.operating_cf / inc.net_income)

    if not pairs:
        # 降级：用全量
        for cf in cashflow:
            inc = next((i for i in income if i.report_year == cf.report_year), None)
            if inc and inc.net_income != 0:
                pairs.append(cf.operating_cf / inc.net_income)

    if not pairs:
        return {"score": 0, "cfo_quality": 0, "missing_data": True}

    cfo_quality = mean(pairs)
    score = min(cfo_quality, 2.0) / 2.0 * 10
    return {"score": round(score, 1), "cfo_quality": round(cfo_quality, 4)}


def factor_gross_margin_moat(income: list[IncomeStatement], years: int = 5) -> dict:
    """Q4: 毛利率护城河（5%）

    GM_median = median(gm[-3:])
    GM_stability = 1 - stdev(gm[-5:]) / mean(gm[-5:])
    得分 = GM_rank × 8 + GM_stability × 2
    简化：无全市场分位数时用绝对值映射
    """
    if not income:
        return {"score": 0, "gm_median": 0, "missing_data": True}

    # 毛利率 = (revenue - cost) / revenue
    # 由于 akshare 财报不一定有 COGS，用 (revenue - (revenue - operating_profit - interest_expense)) 近似
    # 简化：当无 COGS 数据时，用 operating_profit / revenue 作为毛利近似
    gms = []
    for inc in income[-years:]:
        if inc.revenue > 0:
            gm = inc.operating_profit / inc.revenue
            gms.append(max(-1, min(gm, 1)))

    if not gms:
        return {"score": 0, "gm_median": 0, "missing_data": True}

    gm_median = median(gms[-3:]) if len(gms) >= 3 else mean(gms)

    # GM 水平分（绝对值映射，无全市场分位数时）
    if gm_median >= 0.60:   gm_rank = 1.0
    elif gm_median >= 0.40: gm_rank = 0.8
    elif gm_median >= 0.25: gm_rank = 0.6
    elif gm_median >= 0.10: gm_rank = 0.4
    else:                   gm_rank = 0.2

    # 稳定性
    if len(gms) >= 3:
        try:
            gm_stability = max(0, 1 - stdev(gms) / (abs(mean(gms)) + 0.001))
        except:
            gm_stability = 0.5
    else:
        gm_stability = 0.5

    score = gm_rank * 8 + gm_stability * 2
    return {"score": round(score, 1), "gm_median": round(gm_median, 4), "gm_stability": round(gm_stability, 4)}


# ═══════════════════════════════════════════════════════════
#  维度 II：估值水平 (Value) — 30%
# ═══════════════════════════════════════════════════════════

def factor_ev_operating_earnings(
    quotes: list[DailyQuote], balance: list[BalanceSheet],
    income: list[IncomeStatement], industry_group: str = "其他"
) -> dict:
    """V1: EV / Operating Earnings（15%）

    EV = 总市值 + 总负债 - 现金 - 长投 × 0.5
    OpEarnings = 营业利润 + 折旧摊销 + 财务费用（简化：营业利润 + 利息支出）
    得分 = (15 - min(EV/OE, 15)) / 15 × 10
    """
    if not quotes or not balance or not income:
        return {"score": 0, "ev_oe": 999, "missing_data": True}

    latest_quote = quotes[-1]
    latest_balance = sorted(balance, key=lambda x: x.report_year, reverse=True)[0]
    latest_income = sorted(income, key=lambda x: x.report_year, reverse=True)[0]

    mcap = latest_quote.market_cap
    if mcap <= 0:
        return {"score": 0, "ev_oe": 999, "missing_data": True}

    ev = mcap + latest_balance.total_liabilities - latest_balance.cash - latest_balance.long_term_invest * 0.5
    if ev <= 0:
        return {"score": 0, "ev_oe": 999, "negative_ev": True}
    op_earnings = latest_income.operating_profit + latest_income.interest_expense

    if op_earnings <= 0:
        return {"score": 0, "ev_oe": 999, "negative_earnings": True}

    ev_oe = ev / op_earnings
    score = max(0, (15 - min(ev_oe, 15)) / 15 * 10)
    return {"score": round(score, 1), "ev_oe": round(ev_oe, 2)}


def factor_fcf_yield(quotes: list[DailyQuote], cashflow: list[CashFlowStatement]) -> dict:
    """V2: FCF Yield（10%）

    FCF = OCF - Capex
    FCF_Yield = FCF / 总市值
    得分 = min(FCF_Yield, 0.15) / 0.15 × 10
    """
    if not quotes or not cashflow:
        return {"score": 0, "fcf_yield": 0, "missing_data": True}

    latest_cf = sorted(cashflow, key=lambda x: x.report_year, reverse=True)[0]
    latest_quote = quotes[-1]

    fcf = latest_cf.operating_cf - abs(latest_cf.capex)
    mcap = latest_quote.market_cap

    if mcap <= 0:
        return {"score": 0, "fcf_yield": 0, "missing_data": True}

    fcf_yield = fcf / mcap
    score = min(max(fcf_yield, 0), 0.15) / 0.15 * 10
    return {"score": round(score, 1), "fcf_yield": round(fcf_yield, 4)}


def factor_pb_industry_adj(
    quotes: list[DailyQuote], industry_group: str = "其他", industry_median_pb: float = None
) -> dict:
    """V3: PB 行业调整（5%）

    因子值 = PB / 行业 PB 中位数
    得分 = max(0, 1 - 因子值) × 10
    """
    if not quotes:
        return {"score": 0, "missing_data": True}

    pb = quotes[-1].pb
    if pb <= 0:
        return {"score": 0, "pb": pb, "missing_data": True}

    if industry_median_pb is None or industry_median_pb <= 0:
        # 无行业中位数时用绝对估值
        if pb < 1.0:   score = 9
        elif pb < 2.0: score = 7
        elif pb < 3.0: score = 5
        elif pb < 5.0: score = 3
        else:          score = 1
        return {"score": score, "pb": pb, "no_industry_median": True}

    factor_val = pb / industry_median_pb
    score = max(0, 1 - factor_val) * 10
    return {"score": round(score, 1), "pb": pb, "industry_median_pb": industry_median_pb}


# ═══════════════════════════════════════════════════════════
#  维度 III：财务健康 (Health) — 20%
# ═══════════════════════════════════════════════════════════

def factor_leverage(balance: list[BalanceSheet], is_financial: bool = False) -> dict:
    """H1: 杠杆率（8%）

    debt_ratio = 总负债 / 总资产
    得分 = max(0, 1 - debt_ratio / 0.70) × 10
    金融行业跳过
    """
    if is_financial:
        return {"score": 5, "skipped_financial": True}

    if not balance:
        return {"score": 0, "missing_data": True}

    latest = sorted(balance, key=lambda x: x.report_year, reverse=True)[0]
    if latest.total_assets <= 0:
        return {"score": 0, "missing_data": True}

    debt_ratio = latest.total_liabilities / latest.total_assets
    score = max(0, 1 - debt_ratio / 0.70) * 10
    return {"score": round(score, 1), "debt_ratio": round(debt_ratio, 4)}


def factor_interest_coverage(income: list[IncomeStatement]) -> dict:
    """H2: 利息覆盖倍数（7%）

    IC = (营业利润 + 财务费用) / 利息支出
    得分 = min(IC, 20) / 20 × 10
    """
    if not income:
        return {"score": 0, "missing_data": True}

    latest = sorted(income, key=lambda x: x.report_year, reverse=True)[0]
    if latest.interest_expense <= 0:
        # 无利息支出 = 无有息负债 = 安全
        return {"score": 10, "ic": 999, "no_interest_expense": True}

    ic = (latest.operating_profit + latest.interest_expense) / latest.interest_expense
    score = min(ic, 20) / 20 * 10
    return {"score": round(score, 1), "ic": round(ic, 2)}


def factor_asset_quality(income: list[IncomeStatement], balance: list[BalanceSheet]) -> dict:
    """H3: 资产质量（5%）

    检查：应收账款/营收 + 存货/总资产 + 商誉风险
    得分 = (AR_score + Inv_score + GW_score) / 3 × 10
    """
    if not income or not balance:
        return {"score": 0, "missing_data": True}

    latest_inc = sorted(income, key=lambda x: x.report_year, reverse=True)[0]
    latest_bal = sorted(balance, key=lambda x: x.report_year, reverse=True)[0]

    # 应收/营收
    if latest_inc.revenue > 0:
        ar_ratio = latest_bal.accounts_receivable / latest_inc.revenue
    else:
        ar_ratio = 0
    ar_score = max(0, 1 - ar_ratio / 0.5)

    # 存货/总资产
    if latest_bal.total_assets > 0:
        inv_ratio = latest_bal.inventory / latest_bal.total_assets
    else:
        inv_ratio = 0
    inv_score = max(0, 1 - inv_ratio / 0.4)

    # 商誉/净资产
    if latest_bal.equity > 0:
        gw_ratio = latest_bal.goodwill / latest_bal.equity
    else:
        gw_ratio = 0
    gw_score = max(0, 1 - gw_ratio / 0.3)

    score = (ar_score + inv_score + gw_score) / 3 * 10
    return {"score": round(score, 1),
            "ar_ratio": round(ar_ratio, 4),
            "inv_ratio": round(inv_ratio, 4),
            "gw_ratio": round(gw_ratio, 4),
            "sub_scores": {"ar": round(ar_score * 10, 1),
                           "inv": round(inv_score * 10, 1),
                           "gw": round(gw_score * 10, 1)}}


# ═══════════════════════════════════════════════════════════
#  维度 IV：治理与安全边际 (Governance) — 15%
# ═══════════════════════════════════════════════════════════

def factor_shareholder_return(data: FinancialData) -> dict:
    """G1: 股东回报（8%）

    分红率 + 回购检测
    得分 = min(dividend_yield, 5%) / 5% × 7 + (3 if buyback else 0)
    """
    dy = data.dividend_yield
    if dy <= 0:
        return {"score": 0, "dividend_yield": dy, "no_dividend": True}

    div_score = min(dy, 0.05) / 0.05 * 7

    # 回购检测：equity 缩减 > 2%
    buyback = False
    if len(data.balance) >= 2:
        sorted_bal = sorted(data.balance, key=lambda x: x.report_year, reverse=True)
        if sorted_bal[1].equity > 0:
            if sorted_bal[0].equity < sorted_bal[1].equity * 0.98:
                buyback = True

    score = div_score + (3 if buyback else 0)
    return {"score": round(score, 1), "dividend_yield": round(dy, 4), "buyback": buyback}


def factor_audit_governance(data: FinancialData) -> dict:
    """G2: 审计与治理（7%）

    得分 = 审计意见 + 四大审计 + 内部人持股（简化：按审计意见给分）
    """
    score = 0
    details = {}

    if data.audit_opinion == "standard":
        score += 5
        details["audit"] = "标准无保留"
    elif data.audit_opinion:
        score += 2
        details["audit"] = data.audit_opinion
    else:
        score += 3  # 无数据，默认给及格
        details["audit"] = "未知"

    # 简化：无 big4 和内部人持股数据，给基础分
    score += 2  # 基础治理得分

    return {"score": min(round(score, 1), 10), "details": details}


# ═══════════════════════════════════════════════════════════
#  综合因子计算入口
# ═══════════════════════════════════════════════════════════

def compute_all_factors(data: FinancialData, industry_group: str = "其他") -> dict:
    """计算全部因子得分

    Returns:
        {
            "quality": {"roe": {...}, "roic": {...}, "cfo": {...}, "gm": {...}},
            "value": {"ev_oe": {...}, "fcf_yield": {...}, "pb_adj": {...}},
            "health": {"leverage": {...}, "ic": {...}, "asset_q": {...}},
            "gov": {"dividend": {...}, "audit": {...}},
            "weighted": {"quality": N, "value": N, "health": N, "gov": N, "total": N},
        }
    """
    from .industry import get_weights, get_special_rules

    weights = get_weights(industry_group)
    rules = get_special_rules(industry_group)
    is_financial = rules.get("skip_leverage", False)

    factors = {}

    # 维度 I：企业质量
    q_roe = factor_roe_stability(data.income, data.balance)
    q_roic = factor_roic(data.income, data.balance)
    q_cfo = factor_cfo_quality(data.income, data.cashflow)
    q_gm = factor_gross_margin_moat(data.income)
    factors["quality"] = {"roe": q_roe, "roic": q_roic, "cfo": q_cfo, "gm": q_gm}

    quality_raw = (
        q_roe.get("score", 0) * 0.12 +
        q_roic.get("score", 0) * 0.10 +
        q_cfo.get("score", 0) * 0.08 +
        q_gm.get("score", 0) * 0.05
    )

    # 维度 II：估值
    v_ev_oe = factor_ev_operating_earnings(data.quotes, data.balance, data.income, industry_group)
    v_fcf_y = factor_fcf_yield(data.quotes, data.cashflow)
    # Compute industry PB median from quotes if not cached
    industry_pb_median = _get_industry_pb_median(industry_group)
    v_pb = factor_pb_industry_adj(data.quotes, industry_group, industry_pb_median)
    factors["value"] = {"ev_oe": v_ev_oe, "fcf_yield": v_fcf_y, "pb_adj": v_pb}

    value_raw = (
        v_ev_oe.get("score", 0) * 0.15 +
        v_fcf_y.get("score", 0) * 0.10 +
        v_pb.get("score", 0) * 0.05
    )

    # 维度 III：财务健康
    h_lev = factor_leverage(data.balance, is_financial)
    h_ic = factor_interest_coverage(data.income)
    h_aq = factor_asset_quality(data.income, data.balance)
    factors["health"] = {"leverage": h_lev, "ic": h_ic, "asset_q": h_aq}

    health_raw = (
        h_lev.get("score", 0) * 0.08 +
        h_ic.get("score", 0) * 0.07 +
        h_aq.get("score", 0) * 0.05
    )

    # 维度 IV：治理
    g_div = factor_shareholder_return(data)
    g_audit = factor_audit_governance(data)
    factors["gov"] = {"dividend": g_div, "audit": g_audit}

    # ── P3: Beneish M-Score 财务造假检测 ──
    beneish = compute_beneish_m_score(data)
    factors["beneish"] = beneish

    gov_raw = (
        g_div.get("score", 0) * 0.08 +
        g_audit.get("score", 0) * 0.07
    )
    
    # M-Score penalty: cap governance if manipulation detected
    if beneish.get("likely_manipulator"):
        factors["beneish_penalty"] = "likely_manipulator"
        gov_raw = min(gov_raw, 0.3)  # cap at 0.3/1.5 = 20% of gov max
    elif beneish.get("possible_manipulator"):
        factors["beneish_penalty"] = "possible_manipulator"
        gov_raw = min(gov_raw, 0.9)  # cap at 0.9/1.5 = 60% of gov max

    # 加权汇总
    # 各维度满分为：quality=35, value=30, health=20, gov=15
    # 行业权重调整这4个维度的占比
    # formula: dim_score = dim_raw/max_raw * weight * 100
    q_max = 0.12 * 10 + 0.10 * 10 + 0.08 * 10 + 0.05 * 10  # = 3.5
    v_max = 0.15 * 10 + 0.10 * 10 + 0.05 * 10  # = 3.0
    h_max = 0.08 * 10 + 0.07 * 10 + 0.05 * 10  # = 2.0
    g_max = 0.08 * 10 + 0.07 * 10  # = 1.5

    q_norm = quality_raw / q_max if q_max > 0 else 0
    v_norm = value_raw / v_max if v_max > 0 else 0
    h_norm = health_raw / h_max if h_max > 0 else 0
    g_norm = gov_raw / g_max if g_max > 0 else 0

    w = weights
    quality_score = min(q_norm * w["quality"] * 100, 50)
    value_score = min(v_norm * w["value"] * 100, 50)
    health_score = min(h_norm * w["health"] * 100, 30)
    gov_score = min(g_norm * w["gov"] * 100, 25)

    total = quality_score + value_score + health_score + gov_score

    factors["weighted"] = {
        "quality": round(quality_score, 1),
        "value": round(value_score, 1),
        "health": round(health_score, 1),
        "gov": round(gov_score, 1),
        "total": round(total, 1),
    }

    return factors
